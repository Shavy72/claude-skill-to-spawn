"""Stufe 3: Aufräum-Job, Doppelte-Vorschläge, Schutzliste und Zweitprüfer (#800, Teil von #581).

Bündelt die wöchentliche Stufe-3-Aufräum-Runde (Aufräumen ohne Feature-Bau) plattformübergreifend
für Linux und Windows an genau einer Stelle:

- **Doppelte-/Überdeckt-Erkennung** (:func:`sammle_testfaelle`, :func:`finde_doppelte`):
  analysiert Python-Testdateien per ``ast`` und meldet strukturell doppelte sowie durch stärkere
  Tests überdeckte Test-Kandidaten ausschließlich als Vorschläge (niemals direktes Löschen).
- **Unverbrüchliche Schutzliste** (:class:`Schutzliste`, :func:`lade_schutzliste`, :func:`pruefe_schutz`):
  Nutzer-Ablauf-Tests (``nutzer_ablauf`` / ``e2e`` / ``user_flow`` / ``smoke`` / …) und Tests mit
  echtem Fund (``echter_fund`` / ``regression`` / ``bugfix`` / ``vorfall`` / …) bleiben immer
  geschützt. Repo-Konfiguration (``.to-spawn/config.json`` unter ``aufraeumen.schutzliste``) kann
  nur zusätzliche Muster/Marker/Tests ergänzen, die Pflicht-Sperre aber niemals aushebeln.
- **Zweitprüfer-Sperre (fail-closed)** (:class:`ZweitprueferNachweis`, :func:`pruefe_loeschfreigabe`):
  Löschen ist technisch erst nach bestätigtem Prüf-Nachweis eines zweiten unabhängigen Agenten
  zulässig. Fehlender Nachweis, Selbst-Freigabe (gleicher Agent oder gleiche Session), fehlende
  Bestätigung/Begründung, ungültiger Zeitstempel, falsche Prüfsumme oder ein Eintrag auf der
  Schutzliste führen zwingend zur Ablehnung.
- **Gebündelte Aufräum-Runde** (:func:`aufraeum_runde`) und **wöchentliche ``workflow_call``-Vorlage**
  (:func:`vorlage_stufe3_yaml`, :func:`eigenanschluss_stufe3_yaml`, :func:`pruefe_stufe3_vorlage_text`):
  bindet Doppelte-Vorschläge, Wackler-Quarantäne/langsamste Tests (:mod:`to_spawn.pruef.quarantaene`,
  #798) und Diff-Mutationstest (:mod:`to_spawn.pruef.mutation`, #799) zusammen; ``permissions``
  bleiben strikt ``contents: read`` (ausschließlich Vorschläge).

CLI: ``python -m to_spawn.pruef.aufraeumen {doppelte,runde,freigabe-pruefen,vorlage}``
"""

from __future__ import annotations

import argparse
import ast
import copy
import fnmatch
import hashlib
import io
import json
import logging
import os
import re
import sys
import tempfile
import tokenize
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from to_spawn import config, konsole
from to_spawn.pruef import (
    import_graph,
    leiter,
    mutation,
    pipeline_vorlage,
    protokoll,
    quarantaene,
    regeln,
    test_schutz,
    wackler,
)

log = logging.getLogger("to_spawn.pruef.aufraeumen")

BERICHT_VERSION = 1
STANDARD_ERSTELLER = "stufe3-aufraeum-agent"
STANDARD_CRON = "0 3 * * 1"

SCHUTZ_NUTZER_ABLAUF = "nutzer_ablauf"
SCHUTZ_ECHTER_FUND = "echter_fund"
SCHUTZ_KONFIG = "konfig"

AKTION_LOESCHEN_VORSCHLAGEN = "loeschen_vorschlagen"
AKTION_GESCHUETZT_BEHALTEN = "geschuetzt_behalten"

DoppelteArt = Literal["duplikat", "ueberdeckt"]

#: Unverbrüchliche Pflicht-Marker für Nutzer-Ablauf-Tests (nie abschaltbar).
PFLICHT_MARKER_NUTZER_ABLAUF: frozenset[str] = frozenset(
    {
        "nutzer_ablauf",
        "nutzerablauf",
        "e2e",
        "end_to_end",
        "user_flow",
        "user_journey",
        "smoke",
        "ablauf",
        "journey",
    }
)
#: Unverbrüchliche Pflicht-Marker für Tests mit echtem Fund / Regressionsschutz (nie abschaltbar).
PFLICHT_MARKER_ECHTER_FUND: frozenset[str] = frozenset(
    {
        "echter_fund",
        "regression",
        "regressionsschutz",
        "bugfix",
        "bug",
        "vorfall",
        "incident",
    }
)
#: Unverbrüchliche Pfad-/Namens-Muster für Nutzer-Ablauf-Tests (Groß-/Kleinschreibung egal).
PFLICHT_MUSTER_NUTZER_ABLAUF: tuple[str, ...] = (
    "*nutzer_ablauf*",
    "*nutzerablauf*",
    "*e2e*",
    "*end_to_end*",
    "*user_flow*",
    "*user_journey*",
    "*probesitz*",
)
#: Unverbrüchliche Ordnernamen für Nutzer-Ablauf-Tests.
PFLICHT_ORDNER_NUTZER_ABLAUF: frozenset[str] = frozenset(
    {"e2e", "end_to_end", "ablauf", "user_flow", "user_flows", "user_journey"}
)
#: Unverbrüchliche Pfad-/Namens-Muster für Tests mit echtem Fund (Groß-/Kleinschreibung egal).
PFLICHT_MUSTER_ECHTER_FUND: tuple[str, ...] = (
    "*echter_fund*",
    "*regression*",
    "*bugfix*",
    "*vorfall*",
    "*incident*",
)
#: Unverbrüchliche Ordnernamen für Regressions-/Fund-Tests.
PFLICHT_ORDNER_ECHTER_FUND: frozenset[str] = frozenset({"regression", "regressions", "vorfaelle", "bugfixes"})

_TEXT_NUTZER_ABLAUF_RE = re.compile(
    r"\b(?:nutzer[-_\s]?ablauf|end[-_\s]?to[-_\s]?end|user[-_\s]?(?:flow|journey)|e2e[-_\s]?ablauf)\b",
    re.IGNORECASE,
)
_TEXT_ECHTER_FUND_RE = re.compile(
    r"\b(?:echter[-_\s]+fund|regression(?:sschutz)?|vorfall|bugfix|bug\s*#\d+|repro\s*#\d+|fund\s*#\d+)\b",
    re.IGNORECASE,
)
_NAME_BUG_NR_RE = re.compile(r"(?:^|_)(?:bug|vorfall|fund|repro)_\d+(?:_|$)", re.IGNORECASE)
_RAISES_NAMEN = frozenset({"raises", "warns", "assertRaises", "assertRaisesRegex", "assertWarns", "assertWarnsRegex"})

#: Alle dokumentierten Eingaben für ``.github/workflows/pipeline-stufe-3.yml``.
EINGABEN_STUFE3: tuple[pipeline_vorlage.Eingabe, ...] = (
    pipeline_vorlage.Eingabe(
        name="python-version",
        typ="string",
        vorgabe="3.13",
        beschreibung="Python-Version für die wöchentlichen Stufe-3-Runner.",
    ),
    pipeline_vorlage.Eingabe(
        name="test-pfade",
        typ="string",
        vorgabe="tests",
        beschreibung="Test-Wurzelordner oder Test-Pfade für Doppelte-Vorschläge (leerzeichengetrennt).",
    ),
    pipeline_vorlage.Eingabe(
        name="anforderungen",
        typ="string",
        vorgabe="tests/requirements.txt",
        beschreibung="Pfad zu pip-Abhängigkeiten für Tests (optional für Diff-Mutationstest).",
    ),
    pipeline_vorlage.Eingabe(
        name="basis-ref",
        typ="string",
        vorgabe="",
        beschreibung="Git-Basis-Ref für den Diff-Mutationstest (leer = automatisch letzter Merge/HEAD~1).",
    ),
    pipeline_vorlage.Eingabe(
        name="junit-pfad",
        typ="string",
        vorgabe="",
        beschreibung="Optionaler Pfad zu JUnit-XML-Dateien oder einem Ordner für die Meldung langsamster Tests.",
    ),
    pipeline_vorlage.Eingabe(
        name="wackler-schwelle",
        typ="number",
        vorgabe=quarantaene.STANDARD_SCHWELLE,
        beschreibung="Mindestanzahl Wackler-Treffer für einen Quarantäne-Vorschlag.",
    ),
    pipeline_vorlage.Eingabe(
        name="wackler-fenster-tage",
        typ="number",
        vorgabe=quarantaene.STANDARD_FENSTER_TAGE,
        beschreibung="Zeitfenster in Tagen für die Wackler-Auswertung (0 = gesamter Verlauf).",
    ),
    pipeline_vorlage.Eingabe(
        name="langsamste-top",
        typ="number",
        vorgabe=quarantaene.STANDARD_TOP_N,
        beschreibung="Anzahl der gemeldeten langsamsten Tests (Top-N).",
    ),
    pipeline_vorlage.Eingabe(
        name="mutation-aktiv",
        typ="boolean",
        vorgabe=True,
        beschreibung="Diff-Mutationstest (mutmut, nur Linux) in der Stufe-3-Runde ausführen.",
    ),
    pipeline_vorlage.Eingabe(
        name="pfad-filter",
        typ="string",
        vorgabe="",
        beschreibung="Optionale Pfad-Filter für den Diff-Mutationstest (leerzeichengetrennt).",
    ),
    pipeline_vorlage.Eingabe(
        name="ersteller-agent",
        typ="string",
        vorgabe=STANDARD_ERSTELLER,
        beschreibung="Kennung des Aufräum-Agenten (für die Zweitprüfer-Sperre gegen Selbst-Freigabe).",
    ),
    pipeline_vorlage.Eingabe(
        name="skill-pfad",
        typ="string",
        vorgabe=".",
        beschreibung="Pfad zum Skill-Repo im Runner (Eigenanschluss '.', Fremdrepo z. B. '.to-spawn-skill').",
    ),
)


def _jetzt_iso(jetzt: datetime | None = None) -> str:
    wert = (
        datetime.now(UTC) if jetzt is None else (jetzt.astimezone(UTC) if jetzt.tzinfo else jetzt.replace(tzinfo=UTC))
    )
    return wert.isoformat(timespec="seconds")


def _normalisiere_agent(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip()).casefold()


def _text_tupel(wert: Any) -> tuple[str, ...]:
    if not isinstance(wert, (list, tuple, set, frozenset)):
        return ()
    gesehen: set[str] = set()
    ergebnis: list[str] = []
    for eintrag in wert:
        if not isinstance(eintrag, str):
            continue
        sauber = eintrag.strip()
        if sauber and sauber not in gesehen:
            gesehen.add(sauber)
            ergebnis.append(sauber)
    return tuple(ergebnis)


def _schreibe_json_atomar(ziel: Path, daten: dict[str, Any]) -> None:
    ziel.parent.mkdir(parents=True, exist_ok=True)
    if ziel.parent.name == protokoll.ORDNER.name and ziel.parent.parent.name == protokoll.ORDNER.parent.name:
        ign = ziel.parent / ".gitignore"
        if not ign.exists():
            ign.write_text("*\n", encoding="utf-8")
    inhalt = json.dumps(daten, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".aufraeumen-", suffix=".tmp", dir=str(ziel.parent))
    tmp_pfad = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(inhalt)
        os.replace(tmp_pfad, ziel)
    except OSError:
        try:
            tmp_pfad.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _schreibe_summary(ziel: Path | str | None, text: str) -> None:
    if not ziel:
        return
    pfad = Path(ziel)
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        with pfad.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(text.rstrip() + "\n\n")
    except OSError as fehler:
        log.warning("Zusammenfassung konnte nicht nach %s geschrieben werden: %s", pfad, fehler)


# --- Unverbrüchliche Schutzliste ---------------------------------------------------------------


@dataclass(frozen=True)
class Schutzliste:
    """Konfigurierbare Zusatz-Einträge zur unverbrüchlichen Schutzliste.

    Die Pflicht-Kategorien (``nutzer_ablauf`` und ``echter_fund``) sind fest im Prüf-Kern
    verankert und werden durch diese Zusatz-Einträge ausschließlich erweitert, nie ersetzt.
    """

    zusatz_marker: tuple[str, ...] = ()
    zusatz_pfad_muster: tuple[str, ...] = ()
    zusatz_namen_muster: tuple[str, ...] = ()
    zusatz_tests: tuple[str, ...] = ()

    def als_dict(self) -> dict[str, Any]:
        return {
            "pflicht_marker_nutzer_ablauf": sorted(PFLICHT_MARKER_NUTZER_ABLAUF),
            "pflicht_marker_echter_fund": sorted(PFLICHT_MARKER_ECHTER_FUND),
            "pflicht_muster_nutzer_ablauf": list(PFLICHT_MUSTER_NUTZER_ABLAUF),
            "pflicht_muster_echter_fund": list(PFLICHT_MUSTER_ECHTER_FUND),
            "zusatz_marker": list(self.zusatz_marker),
            "zusatz_pfad_muster": list(self.zusatz_pfad_muster),
            "zusatz_namen_muster": list(self.zusatz_namen_muster),
            "zusatz_tests": list(self.zusatz_tests),
            "unumgehbar": True,
        }


@dataclass(frozen=True)
class SchutzBefund:
    """Ergebnis der Schutzlisten-Prüfung für einen Testfall."""

    geschuetzt: bool
    kategorien: tuple[str, ...] = ()
    gruende: tuple[str, ...] = ()

    def als_dict(self) -> dict[str, Any]:
        return {
            "geschuetzt": self.geschuetzt,
            "kategorien": list(self.kategorien),
            "gruende": list(self.gruende),
        }


def lade_schutzliste(quelle: Path | Mapping[str, Any] | None = None) -> Schutzliste:
    """Liest optionale Zusatz-Einträge aus ``.to-spawn/config.json`` (Abschnitt ``aufraeumen.schutzliste``).

    Versucht eine Konfiguration, die Pflicht-Sperre abzuschalten (z. B. ``deaktiviert: true``),
    wird das protokolliert und strikt ignoriert — Pflicht-Schutz bleibt immer aktiv.
    """
    if quelle is None:
        return Schutzliste()
    konfig: Mapping[str, Any] = config.lade(config.repo_wurzel(quelle)) if isinstance(quelle, Path) else quelle
    gruppe = konfig.get("aufraeumen")
    if not isinstance(gruppe, Mapping):
        pruef_gruppe = konfig.get("pruefung")
        gruppe = pruef_gruppe.get("aufraeumen") if isinstance(pruef_gruppe, Mapping) else None
    if not isinstance(gruppe, Mapping):
        return Schutzliste()

    roh = gruppe.get("schutzliste")
    if not isinstance(roh, Mapping):
        return Schutzliste()

    for schluessel in ("deaktiviert", "pflicht_deaktivieren", "ohne_pflicht", "ausschliessen"):
        if roh.get(schluessel):
            log.warning(
                "Schutzliste: Konfigurationsversuch '%s' ignoriert — Pflicht-Schutz (Nutzer-Ablauf, echter Fund) ist unumgehbar.",
                schluessel,
            )

    return Schutzliste(
        zusatz_marker=tuple(m.lower() for m in _text_tupel(roh.get("marker"))),
        zusatz_pfad_muster=_text_tupel(roh.get("pfad_muster")),
        zusatz_namen_muster=_text_tupel(roh.get("namen_muster")),
        zusatz_tests=tuple(wackler.normalisiere_test_id(t) for t in _text_tupel(roh.get("tests"))),
    )


def _ergaenze_schutz(
    kategorien: list[str],
    gruende: list[str],
    kategorie: str,
    grund: str,
) -> None:
    if kategorie not in kategorien:
        kategorien.append(kategorie)
    if grund not in gruende:
        gruende.append(grund)


def pruefe_schutz_metadaten(
    *,
    test_id: str,
    datei: str,
    name: str,
    marker: Sequence[str] = (),
    docstring: str = "",
    kommentare: Sequence[str] = (),
    schutzliste: Schutzliste | None = None,
) -> SchutzBefund:
    """Prüft die unverbrüchliche Schutzliste gegen Test-Metadaten (Pfad, Name, Marker, Texte, Zusatzliste)."""
    norm_id = wackler.normalisiere_test_id(test_id)
    norm_datei = regeln.normal(datei)
    datei_klein = norm_datei.lower()
    name_klein = name.lower()
    ordner_klein = {teil.lower() for teil in PurePosixPath(norm_datei).parts[:-1]} if norm_datei else set()
    marker_klein = tuple(m.strip().lower() for m in marker if m.strip())
    alle_texte = "\n".join([docstring, *kommentare]).strip()

    kategorien: list[str] = []
    gruende: list[str] = []

    # 1. Pflicht-Sperre: Nutzer-Ablauf-Tests (unumgehbar)
    for m in marker_klein:
        if m in PFLICHT_MARKER_NUTZER_ABLAUF:
            _ergaenze_schutz(
                kategorien, gruende, SCHUTZ_NUTZER_ABLAUF, f"Pflicht-Marker '@pytest.mark.{m}' (Nutzer-Ablauf)"
            )
    for ordner in sorted(ordner_klein & PFLICHT_ORDNER_NUTZER_ABLAUF):
        _ergaenze_schutz(kategorien, gruende, SCHUTZ_NUTZER_ABLAUF, f"Pflicht-Ordner '{ordner}/' (Nutzer-Ablauf)")
    for muster in PFLICHT_MUSTER_NUTZER_ABLAUF:
        if fnmatch.fnmatchcase(datei_klein, muster) or fnmatch.fnmatchcase(name_klein, muster):
            _ergaenze_schutz(kategorien, gruende, SCHUTZ_NUTZER_ABLAUF, f"Pflicht-Muster '{muster}' (Nutzer-Ablauf)")
    if alle_texte and _TEXT_NUTZER_ABLAUF_RE.search(alle_texte):
        _ergaenze_schutz(kategorien, gruende, SCHUTZ_NUTZER_ABLAUF, "Docstring/Kommentar kennzeichnet Nutzer-Ablauf")

    # 2. Pflicht-Sperre: Tests mit echtem Fund / Regressionsschutz (unumgehbar)
    for m in marker_klein:
        if m in PFLICHT_MARKER_ECHTER_FUND:
            _ergaenze_schutz(
                kategorien, gruende, SCHUTZ_ECHTER_FUND, f"Pflicht-Marker '@pytest.mark.{m}' (echter Fund)"
            )
    for ordner in sorted(ordner_klein & PFLICHT_ORDNER_ECHTER_FUND):
        _ergaenze_schutz(kategorien, gruende, SCHUTZ_ECHTER_FUND, f"Pflicht-Ordner '{ordner}/' (echter Fund)")
    for muster in PFLICHT_MUSTER_ECHTER_FUND:
        if fnmatch.fnmatchcase(datei_klein, muster) or fnmatch.fnmatchcase(name_klein, muster):
            _ergaenze_schutz(kategorien, gruende, SCHUTZ_ECHTER_FUND, f"Pflicht-Muster '{muster}' (echter Fund)")
    if _NAME_BUG_NR_RE.search(name_klein):
        _ergaenze_schutz(kategorien, gruende, SCHUTZ_ECHTER_FUND, "Testname verweist auf Vorfall/Bug (echter Fund)")
    if alle_texte and _TEXT_ECHTER_FUND_RE.search(alle_texte):
        _ergaenze_schutz(
            kategorien, gruende, SCHUTZ_ECHTER_FUND, "Docstring/Kommentar verweist auf echten Fund/Regression"
        )

    # 3. Additive Repo-Schutzliste (nur ergänzend, nie ersetzend)
    zusatz = schutzliste or Schutzliste()
    zusatz_marker_menge = {m.lower() for m in zusatz.zusatz_marker}
    for m in marker_klein:
        if m in zusatz_marker_menge:
            _ergaenze_schutz(kategorien, gruende, SCHUTZ_KONFIG, f"Konfig-Marker '@pytest.mark.{m}'")
    for muster in zusatz.zusatz_pfad_muster:
        norm_muster = regeln.normal(muster).lower()
        if fnmatch.fnmatchcase(datei_klein, norm_muster):
            _ergaenze_schutz(kategorien, gruende, SCHUTZ_KONFIG, f"Konfig-Pfadmuster '{muster}'")
    for muster in zusatz.zusatz_namen_muster:
        if fnmatch.fnmatchcase(name_klein, muster.lower()):
            _ergaenze_schutz(kategorien, gruende, SCHUTZ_KONFIG, f"Konfig-Namensmuster '{muster}'")
    if norm_id and norm_id in {wackler.normalisiere_test_id(t) for t in zusatz.zusatz_tests}:
        _ergaenze_schutz(kategorien, gruende, SCHUTZ_KONFIG, f"Konfig-Schutzliste enthält '{norm_id}'")

    return SchutzBefund(
        geschuetzt=bool(kategorien),
        kategorien=tuple(kategorien),
        gruende=tuple(gruende),
    )


# --- AST-Extraktion und Doppelte-/Überdeckt-Erkennung ------------------------------------------


@dataclass(frozen=True)
class TestFall:
    """Analysierter Python-Testfall inklusive AST-Signatur und Schutzlisten-Status."""

    test_id: str
    datei: str
    name: str
    zeile: int
    marker: tuple[str, ...]
    docstring: str
    kommentare: tuple[str, ...]
    koerper_hash: str
    inhalts_hash: str
    pruefungen: tuple[str, ...]
    aufrufe: tuple[str, ...]
    hat_parametrize: bool
    schutz: SchutzBefund


def _marker_namen_aus_ausdruck(ausdruck: ast.AST) -> list[str]:
    """Extrahiert pytest-Markernamen aus einem Decorator- oder ``pytestmark``-AST-Knoten."""
    if isinstance(ausdruck, (ast.List, ast.Tuple, ast.Set)):
        namen: list[str] = []
        for elem in ausdruck.elts:
            namen.extend(_marker_namen_aus_ausdruck(elem))
        return namen
    ziel = ausdruck.func if isinstance(ausdruck, ast.Call) else ausdruck
    if isinstance(ziel, ast.Attribute):
        # Muster: pytest.mark.<name> oder mark.<name>
        if isinstance(ziel.value, ast.Attribute) and ziel.value.attr == "mark":
            return [ziel.attr.lower()]
        if isinstance(ziel.value, ast.Name) and ziel.value.id == "mark":
            return [ziel.attr.lower()]
        # Auch direkte unittest-Skip-Dekoratoren erfassen
        if ziel.attr in ("skip", "skipIf", "skipUnless", "expectedFailure"):
            return [ziel.attr.lower()]
    return []


def _modul_pytestmark(baum: ast.Module) -> list[str]:
    namen: list[str] = []
    for knoten in baum.body:
        if isinstance(knoten, ast.Assign):
            for target in knoten.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    namen.extend(_marker_namen_aus_ausdruck(knoten.value))
        elif (
            isinstance(knoten, ast.AnnAssign)
            and isinstance(knoten.target, ast.Name)
            and knoten.target.id == "pytestmark"
            and knoten.value is not None
        ):
            namen.extend(_marker_namen_aus_ausdruck(knoten.value))
    return namen


def _kommentare_nach_zeile(quelltext: str) -> dict[int, str]:
    ergebnis: dict[int, str] = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(quelltext).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                text = tok.string.lstrip("#").strip()
                if text:
                    ergebnis[tok.start[0]] = text
    except (tokenize.TokenError, IndentationError):
        return {}
    return ergebnis


def _entferne_docstring(anweisungen: list[ast.stmt]) -> list[ast.stmt]:
    if (
        anweisungen
        and isinstance(anweisungen[0], ast.Expr)
        and isinstance(anweisungen[0].value, ast.Constant)
        and isinstance(anweisungen[0].value.value, str)
    ):
        return anweisungen[1:]
    return list(anweisungen)


def _sammle_zielnamen(ziel: ast.AST, ausgabe: list[str]) -> None:
    if isinstance(ziel, ast.Name):
        if ziel.id not in ausgabe:
            ausgabe.append(ziel.id)
    elif isinstance(ziel, (ast.Tuple, ast.List)):
        for elem in ziel.elts:
            _sammle_zielnamen(elem, ausgabe)
    elif isinstance(ziel, ast.Starred):
        _sammle_zielnamen(ziel.value, ausgabe)


def _lokale_namen(fn: ast.FunctionDef | ast.AsyncFunctionDef, koerper: list[ast.stmt]) -> dict[str, str]:
    """Ordnet Parametern und lokal zugewiesenen Variablen deterministische Platzhalter zu."""
    namen: list[str] = []
    alle_args = [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]
    if fn.args.vararg is not None:
        alle_args.append(fn.args.vararg)
    if fn.args.kwarg is not None:
        alle_args.append(fn.args.kwarg)
    for arg in alle_args:
        if arg.arg != "self" and arg.arg not in namen:
            namen.append(arg.arg)

    for anw in koerper:
        for knoten in ast.walk(anw):
            if isinstance(knoten, ast.Assign):
                for target in knoten.targets:
                    _sammle_zielnamen(target, namen)
            elif isinstance(knoten, (ast.AnnAssign, ast.For, ast.AsyncFor)):
                _sammle_zielnamen(knoten.target, namen)
            elif isinstance(knoten, (ast.With, ast.AsyncWith)):
                for item in knoten.items:
                    if item.optional_vars is not None:
                        _sammle_zielnamen(item.optional_vars, namen)
            elif isinstance(knoten, ast.ExceptHandler) and knoten.name:
                if knoten.name not in namen:
                    namen.append(knoten.name)
            elif isinstance(knoten, ast.comprehension):
                _sammle_zielnamen(knoten.target, namen)

    return {alt: f"__lokal_{idx}__" for idx, alt in enumerate(namen)}


class _LokalNormalisierer(ast.NodeTransformer):
    """Ersetzt lokal gebundene Variablennamen durch kanonische Platzhalter."""

    def __init__(self, abbildung: Mapping[str, str]) -> None:
        self._abbildung = abbildung

    def visit_Name(self, node: ast.Name) -> ast.AST:
        ersatz = self._abbildung.get(node.id)
        if ersatz is not None:
            return ast.copy_location(ast.Name(id=ersatz, ctx=node.ctx), node)
        return node


def _normalisiere_koerper(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    ohne_doc = _entferne_docstring(list(fn.body))
    if not ohne_doc:
        return []
    kopie = copy.deepcopy(ohne_doc)
    abbildung = _lokale_namen(fn, kopie)
    if not abbildung:
        return kopie
    normalisierer = _LokalNormalisierer(abbildung)
    return [normalisierer.visit(anw) for anw in kopie]


def _zerlege_and(ausdruck: ast.expr) -> list[ast.expr]:
    if isinstance(ausdruck, ast.BoolOp) and isinstance(ausdruck.op, ast.And):
        teile: list[ast.expr] = []
        for wert in ausdruck.values:
            teile.extend(_zerlege_and(wert))
        return teile
    return [ausdruck]


def _aufruf_kurzname(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _extrahiere_pruefungen_und_aufrufe(koerper: Sequence[ast.stmt]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    pruefungen: list[str] = []
    aufrufe: list[str] = []
    gesehen_p: set[str] = set()
    gesehen_a: set[str] = set()

    for anw in koerper:
        for knoten in ast.walk(anw):
            if isinstance(knoten, ast.Assert):
                for teil in _zerlege_and(knoten.test):
                    dump = ast.dump(teil, include_attributes=False)
                    if dump not in gesehen_p:
                        gesehen_p.add(dump)
                        pruefungen.append(dump)
            elif isinstance(knoten, ast.Call):
                kurz = _aufruf_kurzname(knoten.func)
                dump = ast.dump(knoten, include_attributes=False)
                if kurz in _RAISES_NAMEN or kurz.startswith("assert"):
                    if dump not in gesehen_p:
                        gesehen_p.add(dump)
                        pruefungen.append(dump)
                else:
                    if dump not in gesehen_a:
                        gesehen_a.add(dump)
                        aufrufe.append(dump)

    return tuple(pruefungen), tuple(aufrufe)


def _test_quelltext_block(zeilen: Sequence[str], fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    start = min([fn.lineno, *(d.lineno for d in fn.decorator_list)]) - 1
    ende = getattr(fn, "end_lineno", fn.lineno) or fn.lineno
    return "\n".join(zeilen[max(0, start) : ende])


def _hat_test_pfad_filter(pfad: str, test_pfade: Sequence[str]) -> bool:
    if not test_pfade:
        return True
    norm = regeln.normal(pfad)
    for filter_pfad in test_pfade:
        sauber = regeln.normal(filter_pfad).rstrip("/")
        if not sauber or sauber == ".":
            return True
        if norm == sauber or norm.startswith(f"{sauber}/") or fnmatch.fnmatchcase(norm, sauber):
            return True
    return False


def _analysiere_testfunktion(
    *,
    datei: str,
    klasse: str,
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    geerbte_marker: Sequence[str],
    zeilen: Sequence[str],
    kommentare_karte: Mapping[int, str],
    schutzliste: Schutzliste,
) -> TestFall:
    name = f"{klasse}.{fn.name}" if klasse else fn.name
    test_id = f"{datei}::{klasse}::{fn.name}" if klasse else f"{datei}::{fn.name}"
    eigene_marker: list[str] = list(geerbte_marker)
    for dec in fn.decorator_list:
        eigene_marker.extend(_marker_namen_aus_ausdruck(dec))
    marker = tuple(dict.fromkeys(m for m in eigene_marker if m))
    hat_parametrize = "parametrize" in marker

    docstring = ast.get_docstring(fn) or ""
    start_zeile = min([fn.lineno, *(d.lineno for d in fn.decorator_list)])
    end_zeile = getattr(fn, "end_lineno", fn.lineno) or fn.lineno
    relevante_kommentare = tuple(
        text for nr, text in sorted(kommentare_karte.items()) if max(1, start_zeile - 2) <= nr <= end_zeile
    )

    normal_koerper = _normalisiere_koerper(fn)
    if normal_koerper:
        koerper_text = "\n".join(ast.dump(anw, include_attributes=False) for anw in normal_koerper)
        koerper_hash = hashlib.sha256(koerper_text.encode("utf-8")).hexdigest()
    else:
        koerper_hash = ""
    quell_block = _test_quelltext_block(zeilen, fn)
    inhalts_hash = hashlib.sha256(quell_block.encode("utf-8")).hexdigest()
    pruefungen, aufrufe = _extrahiere_pruefungen_und_aufrufe(normal_koerper)

    schutz = pruefe_schutz_metadaten(
        test_id=test_id,
        datei=datei,
        name=name,
        marker=marker,
        docstring=docstring,
        kommentare=relevante_kommentare,
        schutzliste=schutzliste,
    )
    return TestFall(
        test_id=test_id,
        datei=datei,
        name=name,
        zeile=fn.lineno,
        marker=marker,
        docstring=docstring,
        kommentare=relevante_kommentare,
        koerper_hash=koerper_hash,
        inhalts_hash=inhalts_hash,
        pruefungen=pruefungen,
        aufrufe=aufrufe,
        hat_parametrize=hat_parametrize,
        schutz=schutz,
    )


def pruefe_schutz(fall: TestFall, schutzliste: Schutzliste | None = None) -> SchutzBefund:
    """Prüft einen :class:`TestFall` erneut gegen die unverbrüchliche Schutzliste."""
    return pruefe_schutz_metadaten(
        test_id=fall.test_id,
        datei=fall.datei,
        name=fall.name,
        marker=fall.marker,
        docstring=fall.docstring,
        kommentare=fall.kommentare,
        schutzliste=schutzliste,
    )


def sammle_testfaelle(
    repo: Path,
    *,
    test_pfade: Sequence[str] | str | None = None,
    schutzliste: Schutzliste | None = None,
) -> tuple[TestFall, ...]:
    """Scannt alle Python-Testdateien unter ``repo`` und extrahiert Testfälle samt Schutzstatus."""
    wurzel = config.repo_wurzel(repo)
    aktive_schutzliste = lade_schutzliste(wurzel) if schutzliste is None else schutzliste
    if isinstance(test_pfade, str):
        filter_liste = tuple(t for t in test_pfade.split() if t.strip())
    else:
        filter_liste = tuple(t.strip() for t in (test_pfade or ()) if t.strip())

    faelle: list[TestFall] = []
    for rel_pfad in import_graph._py_dateien(wurzel):
        if not _hat_test_pfad_filter(rel_pfad, filter_liste):
            continue
        voller_pfad = wurzel / rel_pfad
        try:
            quelltext = voller_pfad.read_text(encoding="utf-8", errors="replace")
        except OSError as fehler:
            log.warning("Testdatei nicht lesbar (%s): %s", rel_pfad, fehler)
            continue
        if not test_schutz.ist_testdatei(rel_pfad, quelltext):
            continue
        try:
            baum = ast.parse(quelltext, filename=rel_pfad)
        except SyntaxError as fehler:
            log.warning("Testdatei nicht parsbar (%s): %s", rel_pfad, fehler)
            continue

        zeilen = quelltext.splitlines()
        kommentare = _kommentare_nach_zeile(quelltext)
        modul_marker = _modul_pytestmark(baum)

        for knoten in baum.body:
            if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)) and knoten.name.startswith("test_"):
                faelle.append(
                    _analysiere_testfunktion(
                        datei=rel_pfad,
                        klasse="",
                        fn=knoten,
                        geerbte_marker=modul_marker,
                        zeilen=zeilen,
                        kommentare_karte=kommentare,
                        schutzliste=aktive_schutzliste,
                    )
                )
            elif isinstance(knoten, ast.ClassDef) and knoten.name.startswith("Test"):
                klassen_marker = list(modul_marker)
                for dec in knoten.decorator_list:
                    klassen_marker.extend(_marker_namen_aus_ausdruck(dec))
                for mitglied in knoten.body:
                    if isinstance(mitglied, (ast.FunctionDef, ast.AsyncFunctionDef)) and mitglied.name.startswith(
                        "test_"
                    ):
                        faelle.append(
                            _analysiere_testfunktion(
                                datei=rel_pfad,
                                klasse=knoten.name,
                                fn=mitglied,
                                geerbte_marker=klassen_marker,
                                zeilen=zeilen,
                                kommentare_karte=kommentare,
                                schutzliste=aktive_schutzliste,
                            )
                        )
    return tuple(faelle)


@dataclass(frozen=True)
class DoppelteVorschlag:
    """Einzelner Aufräum-Vorschlag für einen doppelten oder überdeckten Test (nie direkte Löschung)."""

    vorschlag_id: str
    kandidat: str
    abgedeckt_durch: str
    art: DoppelteArt
    datei: str
    zeile: int
    aehnlichkeit: float
    begruendung: str
    geschuetzt: bool
    schutz_kategorien: tuple[str, ...]
    schutz_gruende: tuple[str, ...]
    aktion: str
    ersteller_agent: str
    inhalts_hash: str
    erfordert_zweitpruefer: bool = True

    def als_dict(self) -> dict[str, Any]:
        return {
            "vorschlag_id": self.vorschlag_id,
            "kandidat": self.kandidat,
            "abgedeckt_durch": self.abgedeckt_durch,
            "art": self.art,
            "datei": self.datei,
            "zeile": self.zeile,
            "aehnlichkeit": self.aehnlichkeit,
            "begruendung": self.begruendung,
            "geschuetzt": self.geschuetzt,
            "schutz_kategorien": list(self.schutz_kategorien),
            "schutz_gruende": list(self.schutz_gruende),
            "aktion": self.aktion,
            "ersteller_agent": self.ersteller_agent,
            "inhalts_hash": self.inhalts_hash,
            "erfordert_zweitpruefer": self.erfordert_zweitpruefer,
        }

    @classmethod
    def aus_dict(cls, daten: Mapping[str, Any]) -> DoppelteVorschlag | None:
        vorschlag_id = str(daten.get("vorschlag_id", "")).strip()
        kandidat = wackler.normalisiere_test_id(str(daten.get("kandidat", "")))
        abgedeckt = wackler.normalisiere_test_id(str(daten.get("abgedeckt_durch", "")))
        if not vorschlag_id or not kandidat:
            return None
        art_roh = str(daten.get("art", "duplikat")).strip()
        art: DoppelteArt = "ueberdeckt" if art_roh == "ueberdeckt" else "duplikat"
        try:
            zeile = max(0, int(daten.get("zeile", 0)))
        except (TypeError, ValueError):
            zeile = 0
        try:
            aehnlichkeit = float(daten.get("aehnlichkeit", 1.0))
        except (TypeError, ValueError):
            aehnlichkeit = 1.0
        geschuetzt = bool(daten.get("geschuetzt", False))
        kategorien = _text_tupel(daten.get("schutz_kategorien"))
        gruende = _text_tupel(daten.get("schutz_gruende"))
        aktion = str(daten.get("aktion", "")).strip() or (
            AKTION_GESCHUETZT_BEHALTEN if geschuetzt else AKTION_LOESCHEN_VORSCHLAGEN
        )
        return cls(
            vorschlag_id=vorschlag_id,
            kandidat=kandidat,
            abgedeckt_durch=abgedeckt,
            art=art,
            datei=regeln.normal(str(daten.get("datei", ""))),
            zeile=zeile,
            aehnlichkeit=round(aehnlichkeit, 4),
            begruendung=str(daten.get("begruendung", "")).strip(),
            geschuetzt=geschuetzt,
            schutz_kategorien=kategorien,
            schutz_gruende=gruende,
            aktion=aktion,
            ersteller_agent=str(daten.get("ersteller_agent", "")).strip(),
            inhalts_hash=str(daten.get("inhalts_hash", "")).strip(),
            erfordert_zweitpruefer=bool(daten.get("erfordert_zweitpruefer", True)),
        )


@dataclass(frozen=True)
class DoppelteBericht:
    """Ergebnis der Doppelte-/Überdeckt-Analyse mit strikter Schutzlisten-Trennung."""

    gepruefte_dateien: int
    gepruefte_tests: int
    vorschlaege: tuple[DoppelteVorschlag, ...]
    geschuetzte_treffer: tuple[DoppelteVorschlag, ...]
    ersteller_agent: str
    nur_vorschlaege: bool = True
    geloeschte_dateien: tuple[str, ...] = ()

    def als_dict(self) -> dict[str, Any]:
        return {
            "version": BERICHT_VERSION,
            "nur_vorschlaege": self.nur_vorschlaege,
            "geloeschte_dateien": list(self.geloeschte_dateien),
            "erfordert_zweitpruefer": True,
            "ersteller_agent": self.ersteller_agent,
            "gepruefte_dateien": self.gepruefte_dateien,
            "gepruefte_tests": self.gepruefte_tests,
            "vorschlaege": [v.als_dict() for v in self.vorschlaege],
            "geschuetzte_treffer": [t.als_dict() for t in self.geschuetzte_treffer],
        }

    def kurzfassung(self, titel: str = "Doppelte-Vorschläge (Stufe 3)") -> str:
        zeilen = [
            f"### {titel}",
            "",
            (
                f"Geprüft: {self.gepruefte_tests} Test(s) in {self.gepruefte_dateien} Datei(en) · "
                f"Vorschläge (nur Vorschläge, Zweitprüfer Pflicht): {len(self.vorschlaege)} · "
                f"durch Schutzliste behalten: {len(self.geschuetzte_treffer)} · gelöscht: 0"
            ),
            "",
        ]
        if self.vorschlaege:
            zeilen.append("Offene Vorschläge (Löschen erst nach Zweitprüfer-Nachweis):")
            zeilen.append("")
            for v in self.vorschlaege:
                zeilen.append(
                    f"- `{v.kandidat}` ({v.art}, abgedeckt durch `{v.abgedeckt_durch}`, ID `{v.vorschlag_id}`)"
                )
            zeilen.append("")
        else:
            zeilen.append("Keine offenen Löschvorschläge gefunden.")
            zeilen.append("")
        if self.geschuetzte_treffer:
            zeilen.append("Geschützte Treffer (unverbrüchliche Schutzliste, nie zum Löschen vorgeschlagen):")
            zeilen.append("")
            for t in self.geschuetzte_treffer:
                kat = ", ".join(t.schutz_kategorien)
                zeilen.append(f"- `{t.kandidat}` — geschützt ({kat})")
            zeilen.append("")
        return "\n".join(zeilen)


def _vorschlag_id(kandidat: str, abgedeckt_durch: str, art: DoppelteArt) -> str:
    roh = f"{art}|{kandidat}|{abgedeckt_durch}".encode()
    return "auf-" + hashlib.sha256(roh).hexdigest()[:12]


def _baue_vorschlag(
    *,
    kandidat: TestFall,
    abgedeckt_durch: TestFall,
    art: DoppelteArt,
    aehnlichkeit: float,
    begruendung: str,
    ersteller_agent: str,
) -> DoppelteVorschlag:
    geschuetzt = kandidat.schutz.geschuetzt
    return DoppelteVorschlag(
        vorschlag_id=_vorschlag_id(kandidat.test_id, abgedeckt_durch.test_id, art),
        kandidat=kandidat.test_id,
        abgedeckt_durch=abgedeckt_durch.test_id,
        art=art,
        datei=kandidat.datei,
        zeile=kandidat.zeile,
        aehnlichkeit=round(aehnlichkeit, 4),
        begruendung=begruendung,
        geschuetzt=geschuetzt,
        schutz_kategorien=kandidat.schutz.kategorien,
        schutz_gruende=kandidat.schutz.gruende,
        aktion=AKTION_GESCHUETZT_BEHALTEN if geschuetzt else AKTION_LOESCHEN_VORSCHLAGEN,
        ersteller_agent=ersteller_agent,
        inhalts_hash=kandidat.inhalts_hash,
        erfordert_zweitpruefer=True,
    )


def finde_doppelte(
    repo: Path,
    *,
    test_pfade: Sequence[str] | str | None = None,
    schutzliste: Schutzliste | None = None,
    ersteller_agent: str = STANDARD_ERSTELLER,
) -> DoppelteBericht:
    """Erkennt doppelte und überdeckte Test-Kandidaten und erzeugt ausschließlich Vorschläge.

    Geschützte Tests (Nutzer-Ablauf, echter Fund, konfigurierte Schutzliste) werden niemals
    zum Löschen vorgeschlagen; sie werden als Referenz bevorzugt behalten und bei Dopplung
    höchstens getrennt in ``geschuetzte_treffer`` (``aktion='geschuetzt_behalten'``) vermerkt.
    """
    agent_sauber = ersteller_agent.strip() or STANDARD_ERSTELLER
    faelle = sammle_testfaelle(repo, test_pfade=test_pfade, schutzliste=schutzliste)
    dateien_menge = {f.datei for f in faelle}

    vorschlaege: list[DoppelteVorschlag] = []
    geschuetzte_treffer: list[DoppelteVorschlag] = []
    als_kandidat_erfasst: set[str] = set()

    # 1. Strukturelle Duplikate über normalisierten AST-Körper-Hash
    gruppen: dict[str, list[TestFall]] = defaultdict(list)
    for fall in faelle:
        if fall.koerper_hash:
            gruppen[fall.koerper_hash].append(fall)

    for koerper_hash in sorted(gruppen):
        gruppe = gruppen[koerper_hash]
        if len(gruppe) < 2:
            continue
        # Geschützte Tests haben Vorrang als behaltene Referenz; bei Gleichstand deterministisch nach Pfad/Zeile.
        sortiert = sorted(
            gruppe,
            key=lambda f: (not f.schutz.geschuetzt, not bool(f.marker), f.datei, f.zeile, f.test_id),
        )
        referenz = sortiert[0]
        for kandidat in sortiert[1:]:
            if kandidat.hat_parametrize and not referenz.hat_parametrize:
                continue
            eintrag = _baue_vorschlag(
                kandidat=kandidat,
                abgedeckt_durch=referenz,
                art="duplikat",
                aehnlichkeit=1.0,
                begruendung=f"Identischer normalisierter AST-Körper zu {referenz.test_id}.",
                ersteller_agent=agent_sauber,
            )
            als_kandidat_erfasst.add(kandidat.test_id)
            if eintrag.geschuetzt:
                geschuetzte_treffer.append(eintrag)
            else:
                vorschlaege.append(eintrag)

    # 2. Überdeckte Tests (Prüfungen und Aufrufe von A sind Teilmenge eines stärkeren Tests B)
    pruef_mengen = {f.test_id: frozenset(f.pruefungen) for f in faelle}
    aufruf_mengen = {f.test_id: frozenset(f.aufrufe) for f in faelle}

    for kandidat in faelle:
        if kandidat.test_id in als_kandidat_erfasst or kandidat.hat_parametrize:
            continue
        p_a = pruef_mengen[kandidat.test_id]
        a_a = aufruf_mengen[kandidat.test_id]
        if not p_a:
            continue

        bester: tuple[tuple[int, int, int, str], TestFall] | None = None
        for ander in faelle:
            if ander.test_id == kandidat.test_id or ander.test_id in als_kandidat_erfasst:
                continue
            p_b = pruef_mengen[ander.test_id]
            a_b = aufruf_mengen[ander.test_id]
            if not (p_a <= p_b and a_a <= a_b):
                continue
            if len(p_b) <= len(p_a) and len(a_b) <= len(a_a):
                continue
            rang = (
                1 if ander.schutz.geschuetzt else 0,
                len(p_b) - len(p_a),
                len(a_b) - len(a_a),
                ander.test_id,
            )
            if bester is None or rang > bester[0]:
                bester = (rang, ander)

        if bester is not None:
            referenz = bester[1]
            p_b = pruef_mengen[referenz.test_id]
            aehnlichkeit = len(p_a) / max(1, len(p_b))
            eintrag = _baue_vorschlag(
                kandidat=kandidat,
                abgedeckt_durch=referenz,
                art="ueberdeckt",
                aehnlichkeit=aehnlichkeit,
                begruendung=(
                    f"Alle {len(p_a)} Prüfung(en) und {len(a_a)} Aufruf(e) sind vollständig in "
                    f"{referenz.test_id} ({len(p_b)} Prüfung(en)) enthalten."
                ),
                ersteller_agent=agent_sauber,
            )
            als_kandidat_erfasst.add(kandidat.test_id)
            if eintrag.geschuetzt:
                geschuetzte_treffer.append(eintrag)
            else:
                vorschlaege.append(eintrag)

    vorschlaege.sort(key=lambda v: (v.datei, v.zeile, v.kandidat))
    geschuetzte_treffer.sort(key=lambda v: (v.datei, v.zeile, v.kandidat))
    return DoppelteBericht(
        gepruefte_dateien=len(dateien_menge),
        gepruefte_tests=len(faelle),
        vorschlaege=tuple(vorschlaege),
        geschuetzte_treffer=tuple(geschuetzte_treffer),
        ersteller_agent=agent_sauber,
        nur_vorschlaege=True,
        geloeschte_dateien=(),
    )


# --- Zweitprüfer-Sperre (fail-closed) ----------------------------------------------------------


@dataclass(frozen=True)
class ZweitprueferNachweis:
    """Prüf-Nachweis eines zweiten unabhängigen Agenten vor jeder Lösch-Freigabe."""

    vorschlag_id: str
    test_id: str
    pruefer_agent: str
    bestaetigt: bool
    begruendung: str
    geprueft_am: str
    pruefsumme: str = ""
    session_id: str = ""

    def als_dict(self) -> dict[str, Any]:
        return {
            "vorschlag_id": self.vorschlag_id,
            "test_id": self.test_id,
            "pruefer_agent": self.pruefer_agent,
            "bestaetigt": self.bestaetigt,
            "begruendung": self.begruendung,
            "geprueft_am": self.geprueft_am,
            "pruefsumme": self.pruefsumme,
            "session_id": self.session_id,
        }

    @classmethod
    def aus_dict(cls, daten: Mapping[str, Any]) -> ZweitprueferNachweis:
        roh_best = daten.get("bestaetigt")
        bestaetigt = roh_best if isinstance(roh_best, bool) else False
        return cls(
            vorschlag_id=str(daten.get("vorschlag_id", "")).strip(),
            test_id=wackler.normalisiere_test_id(str(daten.get("test_id", ""))),
            pruefer_agent=str(daten.get("pruefer_agent", "")).strip(),
            bestaetigt=bestaetigt,
            begruendung=str(daten.get("begruendung", "")).strip(),
            geprueft_am=str(daten.get("geprueft_am", "")).strip(),
            pruefsumme=str(daten.get("pruefsumme", "")).strip(),
            session_id=str(daten.get("session_id", "")).strip(),
        )


@dataclass(frozen=True)
class LoeschFreigabe:
    """Ergebnis der technischen Zweitprüfer- und Schutzlisten-Sperre (fail-closed)."""

    erlaubt: bool
    vorschlag_id: str
    test_id: str
    ersteller_agent: str
    pruefer_agent: str
    gruende: tuple[str, ...]
    schutz: SchutzBefund

    def als_dict(self) -> dict[str, Any]:
        return {
            "erlaubt": self.erlaubt,
            "vorschlag_id": self.vorschlag_id,
            "test_id": self.test_id,
            "ersteller_agent": self.ersteller_agent,
            "pruefer_agent": self.pruefer_agent,
            "gruende": list(self.gruende),
            "schutz": self.schutz.als_dict(),
        }


def _name_aus_test_id(test_id: str) -> str:
    teile = test_id.split("::")
    return ".".join(teile[1:]) if len(teile) > 1 else test_id


def pruefe_loeschfreigabe(
    vorschlag: DoppelteVorschlag,
    nachweis: ZweitprueferNachweis | None,
    *,
    test_fall: TestFall | None = None,
    schutzliste: Schutzliste | None = None,
    ersteller_session_id: str = "",
    pruefsumme_pflicht: bool = False,
) -> LoeschFreigabe:
    """Prüft fail-closed, ob ein Löschvorschlag technisch freigegeben werden darf.

    Löscht niemals selbst Dateien. Lehnt zwingend ab bei:
    - Schutzlisten-Treffer (Nutzer-Ablauf, echter Fund oder Zusatzliste — unumgehbar),
    - fehlendem oder unbestätigtem Zweitprüfer-Nachweis,
    - Selbst-Freigabe (gleicher Agenten-Name oder gleiche Session-ID),
    - fehlender Begründung, ungültigem Zeitstempel, falscher ID oder abweichender Prüfsumme.
    """
    gruende: list[str] = []

    # 1. Schutzliste immer erneut prüfen (unumgehbare Sperre, auch gegen manipulierte Vorschläge)
    if test_fall is not None:
        frischer_schutz = pruefe_schutz(test_fall, schutzliste)
    else:
        datei = vorschlag.datei or vorschlag.kandidat.split("::", 1)[0]
        frischer_schutz = pruefe_schutz_metadaten(
            test_id=vorschlag.kandidat,
            datei=datei,
            name=_name_aus_test_id(vorschlag.kandidat),
            schutzliste=schutzliste,
        )

    kategorien = tuple(dict.fromkeys([*vorschlag.schutz_kategorien, *frischer_schutz.kategorien]))
    schutz_gruende = tuple(dict.fromkeys([*vorschlag.schutz_gruende, *frischer_schutz.gruende]))
    ist_geschuetzt = vorschlag.geschuetzt or frischer_schutz.geschuetzt or bool(kategorien)
    gesamt_schutz = SchutzBefund(geschuetzt=ist_geschuetzt, kategorien=kategorien, gruende=schutz_gruende)
    if ist_geschuetzt:
        detail = ", ".join(kategorien) if kategorien else "Schutzliste"
        gruende.append(f"Unumgehbare Schutzliste blockt Löschung ({detail}).")

    # 2. Ersteller-Kennung prüfen
    ersteller = vorschlag.ersteller_agent.strip()
    if not ersteller:
        gruende.append("Ersteller-Agent im Vorschlag fehlt (Selbst-Freigabe nicht ausschließbar).")

    # 3. Zweitprüfer-Nachweis prüfen (fail-closed)
    pruefer = ""
    if nachweis is None:
        gruende.append("Kein Zweitprüfer-Nachweis vorhanden (fail-closed).")
    else:
        pruefer = nachweis.pruefer_agent.strip()
        if not pruefer:
            gruende.append("Zweitprüfer-Agent im Nachweis fehlt.")
        elif ersteller and _normalisiere_agent(pruefer) == _normalisiere_agent(ersteller):
            gruende.append(f"Selbst-Freigabe abgelehnt: Zweitprüfer '{pruefer}' entspricht Ersteller '{ersteller}'.")

        if (
            ersteller_session_id.strip()
            and nachweis.session_id.strip()
            and ersteller_session_id.strip().casefold() == nachweis.session_id.strip().casefold()
        ):
            gruende.append("Selbst-Freigabe abgelehnt: Zweitprüfer nutzt dieselbe Session-ID wie der Ersteller.")

        if nachweis.bestaetigt is not True:
            gruende.append("Zweitprüfer hat den Vorschlag nicht ausdrücklich bestätigt (bestaetigt != True).")

        if not nachweis.begruendung.strip():
            gruende.append("Zweitprüfer-Begründung fehlt.")

        if not nachweis.geprueft_am.strip() or wackler.parse_zeit(nachweis.geprueft_am) is None:
            gruende.append("Zweitprüfer-Zeitstempel 'geprueft_am' fehlt oder ist kein gültiges ISO-Datum.")

        if nachweis.vorschlag_id.strip() != vorschlag.vorschlag_id.strip():
            gruende.append(
                f"Vorschlags-ID im Nachweis ('{nachweis.vorschlag_id}') passt nicht zu '{vorschlag.vorschlag_id}'."
            )

        if wackler.normalisiere_test_id(nachweis.test_id) != wackler.normalisiere_test_id(vorschlag.kandidat):
            gruende.append(f"Test-ID im Nachweis ('{nachweis.test_id}') passt nicht zu '{vorschlag.kandidat}'.")

        erwartete_summe = test_fall.inhalts_hash if test_fall is not None else vorschlag.inhalts_hash
        if erwartete_summe:
            if nachweis.pruefsumme.strip():
                if nachweis.pruefsumme.strip() != erwartete_summe:
                    gruende.append("Prüfsumme im Nachweis weicht vom aktuellen Test-Stand ab.")
            elif pruefsumme_pflicht:
                gruende.append("Prüfsumme im Zweitprüfer-Nachweis fehlt.")

    return LoeschFreigabe(
        erlaubt=not gruende,
        vorschlag_id=vorschlag.vorschlag_id,
        test_id=vorschlag.kandidat,
        ersteller_agent=ersteller,
        pruefer_agent=pruefer,
        gruende=tuple(gruende),
        schutz=gesamt_schutz,
    )


# --- Gebündelte Stufe-3-Aufräum-Runde ----------------------------------------------------------


@dataclass(frozen=True)
class AufraeumRundeErgebnis:
    """Gebündeltes Ergebnis einer vollständigen Stufe-3-Aufräum-Runde (nur Vorschläge)."""

    erstellt_am: str
    ersteller_agent: str
    doppelte: DoppelteBericht
    quarantaene: quarantaene.Stufe3Bericht
    mutation: mutation.Ergebnis | None
    schutzliste: Schutzliste
    bericht_pfad: Path
    geschrieben: bool
    nur_vorschlaege: bool = True
    geloeschte_dateien: tuple[str, ...] = ()

    def als_dict(self) -> dict[str, Any]:
        return {
            "version": BERICHT_VERSION,
            "erstellt_am": self.erstellt_am,
            "ersteller_agent": self.ersteller_agent,
            "nur_vorschlaege": self.nur_vorschlaege,
            "geloeschte_dateien": list(self.geloeschte_dateien),
            "erfordert_zweitpruefer": True,
            "bericht_pfad": self.bericht_pfad.as_posix(),
            "geschrieben": self.geschrieben,
            "schutzliste": self.schutzliste.als_dict(),
            "doppelte": self.doppelte.als_dict(),
            "quarantaene": self.quarantaene.als_dict(),
            "mutation": self.mutation.als_dict() if self.mutation is not None else None,
        }

    def zusammenfassung(self, titel: str = "Stufe 3 — Wöchentliche Aufräum-Runde") -> str:
        mut_text = (
            "deaktiviert"
            if self.mutation is None
            else (
                "grün"
                if self.mutation.ok
                else ("nicht unterstützt (nur Linux)" if not self.mutation.unterstuetzt else "Befunde/Hinweis")
            )
        )
        zeilen = [
            f"## {titel}",
            "",
            (
                f"Modus: **nur Vorschläge** (Zweitprüfer vor jedem Löschen Pflicht, gelöscht: 0) · "
                f"Agent: `{self.ersteller_agent}` · Doppelte-Vorschläge: {len(self.doppelte.vorschlaege)} · "
                f"geschützte Treffer: {len(self.doppelte.geschuetzte_treffer)} · "
                f"Wackler-Vorschläge: {len(self.quarantaene.quarantaene.vorschlaege)} · "
                f"Diff-Mutationstest: {mut_text}"
            ),
            "",
            self.doppelte.kurzfassung(),
            self.quarantaene.kurzfassung(),
        ]
        if self.mutation is not None:
            zeilen.append(self.mutation.zusammenfassung())
        return "\n".join(zeilen).rstrip() + "\n"


def aufraeum_runde(
    repo: Path,
    *,
    test_pfade: Sequence[str] | str | None = None,
    junit_pfade: Sequence[Path | str] = (),
    wackler_schwelle: int = quarantaene.STANDARD_SCHWELLE,
    wackler_fenster_tage: int | None = quarantaene.STANDARD_FENSTER_TAGE,
    langsamste_top: int = quarantaene.STANDARD_TOP_N,
    mutation_aktiv: bool = False,
    basis: str = "HEAD~1",
    ziel: str | None = "HEAD",
    pfad_filter: Sequence[str] | None = None,
    ersteller_agent: str = STANDARD_ERSTELLER,
    schreiben: bool = True,
    bericht_pfad: Path | None = None,
    summary_pfad: Path | str | None = None,
    jetzt: datetime | None = None,
) -> AufraeumRundeErgebnis:
    """Führt eine vollständige Stufe-3-Aufräum-Runde aus (erzeugt ausschließlich Vorschläge)."""
    wurzel = config.repo_wurzel(repo)
    agent_sauber = ersteller_agent.strip() or STANDARD_ERSTELLER
    schutz = lade_schutzliste(wurzel)

    doppelte_erg = finde_doppelte(
        wurzel,
        test_pfade=test_pfade,
        schutzliste=schutz,
        ersteller_agent=agent_sauber,
    )
    quarantaene_erg = quarantaene.erstelle_bericht(
        wurzel,
        junit_pfade=junit_pfade,
        schwelle=wackler_schwelle,
        fenster_tage=wackler_fenster_tage,
        top_n=langsamste_top,
        schreiben=schreiben,
        jetzt=jetzt,
    )
    mutation_erg: mutation.Ergebnis | None = None
    if mutation_aktiv:
        mutation_erg = mutation.pruefe_diff(
            wurzel,
            basis,
            ziel,
            pfad_filter=pfad_filter,
        )

    ziel_pfad = bericht_pfad if bericht_pfad is not None else (wurzel / protokoll.ORDNER / protokoll.AUFRAEUMEN_DATEI)
    ergebnis = AufraeumRundeErgebnis(
        erstellt_am=_jetzt_iso(jetzt),
        ersteller_agent=agent_sauber,
        doppelte=doppelte_erg,
        quarantaene=quarantaene_erg,
        mutation=mutation_erg,
        schutzliste=schutz,
        bericht_pfad=ziel_pfad,
        geschrieben=schreiben,
        nur_vorschlaege=True,
        geloeschte_dateien=(),
    )
    if schreiben:
        _schreibe_json_atomar(ziel_pfad, ergebnis.als_dict())
    _schreibe_summary(summary_pfad, ergebnis.zusammenfassung())
    return ergebnis


# --- Wiederverwendbare workflow_call-Vorlage (.github/workflows/pipeline-stufe-3.yml) ----------


def vorlage_stufe3_yaml() -> str:
    """Erzeugt die wiederverwendbare ``workflow_call``-Vorlage für Stufe 3 (wöchentliches Aufräumen)."""
    eingabe_zeilen: list[str] = []
    for e in EINGABEN_STUFE3:
        eingabe_zeilen.extend(
            [
                f"      {e.name}:",
                f"        description: {pipeline_vorlage._yaml_skalar(e.beschreibung)}",
                "        required: false",
                f"        type: {e.typ}",
                f"        default: {pipeline_vorlage._yaml_skalar(e.vorgabe)}",
            ]
        )
    eingaben_block = "\n".join(eingabe_zeilen)
    return (
        "# Wiederverwendbare Pipeline-Vorlage Stufe 3 (#581, #800).\n"
        f"{pipeline_vorlage.kopf_doku_kommentar()}"
        "# Stufe 3 (wöchentliches Aufräumen ohne Feature-Bau — erzeugt ausschließlich Vorschläge):\n"
        "# - Doppelte-/Überdeckt-Vorschläge mit unverbrüchlicher Schutzliste (Nutzer-Ablauf, echter Fund)\n"
        "#   und technischer Zweitprüfer-Sperre (Löschen erst nach Nachweis eines zweiten unabhängigen Agenten).\n"
        "# - Wackler-Quarantäne-Ledger und Meldung langsamster Tests (Teil 2, #798).\n"
        "# - Diff-Mutationstest mit mutmut ausschließlich auf geändertem Produktivcode (Teil 3, #799, nur Linux).\n"
        "name: Pipeline Stufe 3 (Wöchentliches Aufräumen — nur Vorschläge)\n"
        "\n"
        "on:\n"
        "  workflow_call:\n"
        "    inputs:\n"
        f"{eingaben_block}\n"
        "\n"
        "permissions:\n"
        "  contents: read\n"
        "\n"
        "jobs:\n"
        "  doppelte-vorschlaege:\n"
        "    name: Stufe 3 Doppelte-Vorschläge & Schutzliste (nur Vorschläge)\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 20\n"
        "    defaults:\n"
        "      run:\n"
        "        shell: bash\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "${{ inputs.python-version }}"\n'
        "\n"
        "      - name: Doppelte und überdeckte Test-Kandidaten melden (nur Vorschläge)\n"
        "        env:\n"
        "          SKILL_PFAD: ${{ inputs.skill-pfad }}\n"
        "          TEST_PFADE: ${{ inputs.test-pfade }}\n"
        "          ERSTELLER_AGENT: ${{ inputs.ersteller-agent }}\n"
        "        run: |\n"
        '          args=(--repo . --ersteller-agent "$ERSTELLER_AGENT" --ausgabe-json "$RUNNER_TEMP/stufe3-doppelte.json" --summary-pfad "$GITHUB_STEP_SUMMARY")\n'
        '          [ -n "$TEST_PFADE" ] && args+=(--test-pfade "$TEST_PFADE")\n'
        '          PYTHONPATH="${SKILL_PFAD}:${PYTHONPATH:-}" python -m to_spawn.pruef.aufraeumen doppelte "${args[@]}"\n'
        "\n"
        "      - name: Vorschlags-Artefakt (Doppelte) hochladen\n"
        "        if: always()\n"
        "        uses: actions/upload-artifact@v4\n"
        "        with:\n"
        "          name: stufe3-doppelte-vorschlaege\n"
        "          path: ${{ runner.temp }}/stufe3-doppelte.json\n"
        "          if-no-files-found: ignore\n"
        "\n"
        "  quarantaene-langsamste:\n"
        "    name: Stufe 3 Wackler-Quarantäne & langsamste Tests (nur Vorschläge)\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 20\n"
        "    defaults:\n"
        "      run:\n"
        "        shell: bash\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "${{ inputs.python-version }}"\n'
        "\n"
        "      - name: Quarantäne-Vorschläge und langsamste Tests erzeugen\n"
        "        env:\n"
        "          SKILL_PFAD: ${{ inputs.skill-pfad }}\n"
        "          JUNIT_PFAD: ${{ inputs.junit-pfad }}\n"
        "          WACKLER_SCHWELLE: ${{ inputs.wackler-schwelle }}\n"
        "          WACKLER_FENSTER: ${{ inputs.wackler-fenster-tage }}\n"
        "          LANGSAMSTE_TOP: ${{ inputs.langsamste-top }}\n"
        "        run: |\n"
        '          args=(--repo . --schwelle "$WACKLER_SCHWELLE" --fenster-tage "$WACKLER_FENSTER" --top "$LANGSAMSTE_TOP" --nur-pruefen --ausgabe-json "$RUNNER_TEMP/stufe3-quarantaene.json" --summary-pfad "$GITHUB_STEP_SUMMARY")\n'
        '          if [ -n "$JUNIT_PFAD" ] && [ -e "$JUNIT_PFAD" ]; then\n'
        '            args+=(--junit "$JUNIT_PFAD")\n'
        "          fi\n"
        '          PYTHONPATH="${SKILL_PFAD}:${PYTHONPATH:-}" python -m to_spawn.pruef.quarantaene bericht "${args[@]}"\n'
        "\n"
        "      - name: Vorschlags-Artefakt (Quarantäne & Langsamste) hochladen\n"
        "        if: always()\n"
        "        uses: actions/upload-artifact@v4\n"
        "        with:\n"
        "          name: stufe3-quarantaene-langsamste\n"
        "          path: ${{ runner.temp }}/stufe3-quarantaene.json\n"
        "          if-no-files-found: ignore\n"
        "\n"
        "  diff-mutation:\n"
        "    name: Stufe 3 Diff-Mutationstest (mutmut, nur Linux)\n"
        "    if: ${{ inputs.mutation-aktiv }}\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 45\n"
        "    defaults:\n"
        "      run:\n"
        "        shell: bash\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "        with:\n"
        "          fetch-depth: 0\n"
        "\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "${{ inputs.python-version }}"\n'
        "\n"
        "      - name: Werkzeuge für Diff-Mutationstest installieren\n"
        "        env:\n"
        "          ANFORDERUNGEN: ${{ inputs.anforderungen }}\n"
        "        run: |\n"
        "          python -m pip install -q pytest mutmut\n"
        '          if [ -n "$ANFORDERUNGEN" ] && [ -f "$ANFORDERUNGEN" ]; then\n'
        '            python -m pip install -q -r "$ANFORDERUNGEN"\n'
        "          fi\n"
        "\n"
        f'{pipeline_vorlage.basis_ref_schritt_yaml("Basis-Ref für Diff-Mutationstest bestimmen")}'
        "\n"
        "      - name: Diff-Mutationstest ausführen (nur Bericht/Vorschläge)\n"
        "        continue-on-error: true\n"
        "        env:\n"
        "          SKILL_PFAD: ${{ inputs.skill-pfad }}\n"
        "          PFAD_FILTER: ${{ inputs.pfad-filter }}\n"
        "        run: |\n"
        '          args=(--repo . --basis "$BASIS_REF" --ziel HEAD --zusammenfassung "$GITHUB_STEP_SUMMARY" --json)\n'
        '          if [ -n "$PFAD_FILTER" ]; then\n'
        "            # shellcheck disable=SC2206\n"
        "            filter_arr=($PFAD_FILTER)\n"
        '            args+=(--pfad-filter "${filter_arr[@]}")\n'
        "          fi\n"
        '          PYTHONPATH="${SKILL_PFAD}:${PYTHONPATH:-}" python -m to_spawn.pruef.mutation "${args[@]}" > "$RUNNER_TEMP/stufe3-mutation.json"\n'
        "\n"
        "      - name: Vorschlags-Artefakt (Diff-Mutationstest) hochladen\n"
        "        if: always()\n"
        "        uses: actions/upload-artifact@v4\n"
        "        with:\n"
        "          name: stufe3-diff-mutation\n"
        "          path: ${{ runner.temp }}/stufe3-mutation.json\n"
        "          if-no-files-found: ignore\n"
    )


def eigenanschluss_stufe3_yaml(
    workflow_ref: str = "./.github/workflows/pipeline-stufe-3.yml",
    *,
    cron: str = STANDARD_CRON,
    mutation_aktiv: bool = True,
) -> str:
    """Erzeugt den wöchentlichen Eigenanschluss-Workflow für das Skill-Repo (Stufe 3)."""
    if not cron or len(cron.split()) != 5:
        raise ValueError(f"Ungültiger 5-Feld-Cron-Ausdruck für Stufe 3: {cron!r}")
    mut_bool = "true" if mutation_aktiv else "false"
    return (
        "# Wöchentlicher Eigenanschluss des Skill-Repos an Pipeline-Vorlage Stufe 3 (#581, #800).\n"
        f"{pipeline_vorlage.kopf_doku_kommentar()}"
        "name: Pipeline Stufe 3 (Wöchentliches Aufräumen)\n"
        "\n"
        "on:\n"
        "  schedule:\n"
        f'    - cron: "{cron}"\n'
        "  workflow_dispatch:\n"
        "\n"
        "permissions:\n"
        "  contents: read\n"
        "\n"
        "concurrency:\n"
        "  group: pipeline-stufe-3-${{ github.ref }}\n"
        "  cancel-in-progress: false\n"
        "\n"
        "jobs:\n"
        "  pipeline-stufe-3:\n"
        f"    uses: {workflow_ref}\n"
        "    with:\n"
        '      python-version: "3.13"\n'
        '      test-pfade: "tests"\n'
        '      anforderungen: "tests/requirements.txt"\n'
        f"      wackler-schwelle: {quarantaene.STANDARD_SCHWELLE}\n"
        f"      wackler-fenster-tage: {quarantaene.STANDARD_FENSTER_TAGE}\n"
        f"      langsamste-top: {quarantaene.STANDARD_TOP_N}\n"
        f"      mutation-aktiv: {mut_bool}\n"
        f'      ersteller-agent: "{STANDARD_ERSTELLER}"\n'
        '      skill-pfad: "."\n'
    )


def pruefe_stufe3_vorlage_text(text: str) -> list[str]:
    """Prüft den YAML-Text einer Stufe-3-Vorlage rein mit der Standardbibliothek."""
    befunde: list[str] = []
    if pipeline_vorlage._VERBOTENES_PLUGIN in text.lower():
        befunde.append(f"verbotenes Plugin {pipeline_vorlage._VERBOTENES_PLUGIN!r} gefunden")
    if not re.search(r"(?m)^  workflow_call:\s*$", text):
        befunde.append("Abschnitt 'workflow_call:' fehlt")
    if not re.search(r"(?m)^permissions:\s*$", text) or "contents: read" not in text:
        befunde.append("minimale Berechtigung 'permissions: contents: read' fehlt")
    if "contents: write" in text:
        befunde.append("verbotene Schreibberechtigung 'contents: write' gefunden (Stufe 3 erzeugt nur Vorschläge)")
    if re.search(r"\bgit\s+(?:rm|push|commit)\b", text):
        befunde.append("direkter Schreib-/Löschbefehl (git rm/commit/push) in Stufe-3-Vorlage verboten")

    for stichwort, beschreibung in (
        ("Triage", "Zielbild-Kopf-Doku (Triage)"),
        ("Audits", "Zielbild-Kopf-Doku (nächtliche Audits)"),
        ("Claude-Sessions", "Bau-Server-Rolle (Claude-Sessions)"),
        ("braucht Server", "Bau-Server-Rolle ('braucht Server'-Tests)"),
    ):
        if stichwort not in text:
            befunde.append(f"{beschreibung} fehlt in der Kopf-Doku")

    zeilen = text.splitlines()
    for eingabe in EINGABEN_STUFE3:
        muster = re.compile(rf"^      {re.escape(eingabe.name)}:\s*$")
        treffer = [idx for idx, z in enumerate(zeilen) if muster.match(z)]
        if not treffer:
            befunde.append(f"Eingabe '{eingabe.name}' fehlt")
            continue
        block = "\n".join(zeilen[treffer[0] + 1 : treffer[0] + 6])
        if "description:" not in block:
            befunde.append(f"Eingabe '{eingabe.name}' ohne description")
        if f"type: {eingabe.typ}" not in block:
            befunde.append(f"Eingabe '{eingabe.name}' ohne type: {eingabe.typ}")

    for job in ("doppelte-vorschlaege", "quarantaene-langsamste", "diff-mutation"):
        if not re.search(rf"(?m)^  {re.escape(job)}:\s*$", text):
            befunde.append(f"Job '{job}:' fehlt")

    for modul in ("to_spawn.pruef.aufraeumen", "to_spawn.pruef.quarantaene", "to_spawn.pruef.mutation"):
        if modul not in text:
            befunde.append(f"Einbindung von '{modul}' fehlt")

    if "inputs.mutation-aktiv" not in text:
        befunde.append("Bedingung 'inputs.mutation-aktiv' für Job 'diff-mutation' fehlt")
    return befunde


def pruefe_eigenanschluss_stufe3_text(text: str) -> list[str]:
    """Prüft den wöchentlichen Eigenanschluss-Workflow für Stufe 3."""
    befunde: list[str] = []
    if not re.search(r"(?m)^  schedule:\s*$", text) or "cron:" not in text:
        befunde.append("wöchentlicher Zeitplan ('schedule:' + 'cron:') fehlt")
    if not re.search(r"(?m)^  workflow_dispatch:\s*$", text):
        befunde.append("manueller Auslöser 'workflow_dispatch:' fehlt")
    if "pipeline-stufe-3.yml" not in text or "uses:" not in text:
        befunde.append("Verweis auf Vorlage 'pipeline-stufe-3.yml' per 'uses:' fehlt")
    if "contents: read" not in text:
        befunde.append("minimale Berechtigung 'contents: read' fehlt")
    if "contents: write" in text:
        befunde.append("verbotene Schreibberechtigung 'contents: write' gefunden")
    return befunde


# --- CLI ---------------------------------------------------------------------------------------


def _lade_vorschlag_aus_bericht(pfad: Path, vorschlag_id: str) -> DoppelteVorschlag | None:
    daten = json.loads(pfad.read_text(encoding="utf-8"))
    if not isinstance(daten, Mapping):
        return None
    listen: list[Any] = []
    if isinstance(daten.get("vorschlaege"), list):
        listen.extend(daten["vorschlaege"])
    if isinstance(daten.get("geschuetzte_treffer"), list):
        listen.extend(daten["geschuetzte_treffer"])
    doppelte = daten.get("doppelte")
    if isinstance(doppelte, Mapping):
        if isinstance(doppelte.get("vorschlaege"), list):
            listen.extend(doppelte["vorschlaege"])
        if isinstance(doppelte.get("geschuetzte_treffer"), list):
            listen.extend(doppelte["geschuetzte_treffer"])
    for roh in listen:
        if not isinstance(roh, Mapping):
            continue
        kandidat = DoppelteVorschlag.aus_dict(roh)
        if kandidat is not None and kandidat.vorschlag_id == vorschlag_id:
            return kandidat
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m to_spawn.pruef.aufraeumen",
        description="Stufe 3: Aufräum-Job, Doppelte-Vorschläge, unverbrüchliche Schutzliste und Zweitprüfer.",
    )
    unter = parser.add_subparsers(dest="befehl", required=True)

    pd = unter.add_parser("doppelte", help="Doppelte und überdeckte Tests finden (ausschließlich Vorschläge).")
    pd.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner")
    pd.add_argument("--test-pfade", default="", help="Optionale Test-Pfade (leerzeichengetrennt)")
    pd.add_argument("--ersteller-agent", default=STANDARD_ERSTELLER, help="Kennung des Aufräum-Agenten")
    pd.add_argument("--ausgabe-json", type=Path, default=None, help="Optionaler Dateipfad für JSON-Bericht")
    pd.add_argument("--summary-pfad", default="", help="Pfad für Markdown-Zusammenfassung")
    pd.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON auf stdout")

    pr = unter.add_parser("runde", help="Gebündelte Stufe-3-Aufräum-Runde (Doppelte + Quarantäne + Mutation).")
    pr.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner")
    pr.add_argument("--test-pfade", default="", help="Optionale Test-Pfade (leerzeichengetrennt)")
    pr.add_argument("--junit", type=Path, action="append", default=[], help="JUnit-XML-Datei oder Ordner")
    pr.add_argument("--wackler-schwelle", type=int, default=quarantaene.STANDARD_SCHWELLE)
    pr.add_argument("--wackler-fenster-tage", type=int, default=quarantaene.STANDARD_FENSTER_TAGE)
    pr.add_argument("--langsamste-top", type=int, default=quarantaene.STANDARD_TOP_N)
    pr.add_argument("--mit-mutation", action="store_true", help="Diff-Mutationstest (nur Linux) einbinden")
    pr.add_argument("--basis", default="HEAD~1", help="Git-Basis-Ref für Diff-Mutationstest")
    pr.add_argument("--ziel", default="HEAD", help="Git-Ziel-Ref für Diff-Mutationstest")
    pr.add_argument("--pfad-filter", nargs="+", default=None, help="Optionale Pfad-Filter für Diff-Mutationstest")
    pr.add_argument("--ersteller-agent", default=STANDARD_ERSTELLER, help="Kennung des Aufräum-Agenten")
    pr.add_argument("--nur-pruefen", action="store_true", help="Kein Schreiben in .to-spawn/pruef/")
    pr.add_argument("--ausgabe-json", type=Path, default=None, help="Optionaler Dateipfad für JSON-Bericht")
    pr.add_argument("--summary-pfad", default="", help="Pfad für Markdown-Zusammenfassung")
    pr.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON auf stdout")

    pf = unter.add_parser("freigabe-pruefen", help="Zweitprüfer-Nachweis und Schutzliste fail-closed prüfen.")
    pf.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner")
    pf.add_argument("--bericht", type=Path, required=True, help="JSON-Bericht mit Aufräum-Vorschlägen")
    pf.add_argument("--vorschlag-id", required=True, help="ID des zu prüfenden Vorschlags")
    pf.add_argument("--nachweis", type=Path, default=None, help="JSON-Datei mit Zweitprüfer-Nachweis")
    pf.add_argument("--ersteller-session", default="", help="Optionale Session-ID des Erstellers")
    pf.add_argument("--pruefsumme-pflicht", action="store_true", help="Prüfsumme im Nachweis zwingend verlangen")
    pf.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON auf stdout")

    pv = unter.add_parser(
        "vorlage", help="Stufe-3-workflow_call-Vorlage oder wöchentlichen Eigenanschluss ausgeben/prüfen."
    )
    pv.add_argument("--eigenanschluss", action="store_true", help="Wöchentlichen Eigenanschluss statt Vorlage ausgeben")
    pv.add_argument("--cron", default=STANDARD_CRON, help="5-Feld-Cron-Ausdruck für den wöchentlichen Eigenanschluss")
    pv.add_argument("--ausgabe", type=Path, default=None, help="Zieldatei zum Schreiben der Vorlage")
    pv.add_argument("--pruefen", type=Path, default=None, help="Bestehende YAML-Vorlage gegen Vertrag prüfen")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-Einstiegspunkt für lokale Nutzung und GitHub Actions (Linux + Windows)."""
    konsole.utf8_ausgabe()
    args = _parser().parse_args(argv)

    if args.befehl == "vorlage":
        if args.pruefen is not None:
            if not args.pruefen.is_file():
                sys.stderr.write(f"Vorlage fehlt: {args.pruefen}\n")
                return leiter.GATE_FEHLER
            inhalt = args.pruefen.read_text(encoding="utf-8")
            befunde = (
                pruefe_eigenanschluss_stufe3_text(inhalt) if args.eigenanschluss else pruefe_stufe3_vorlage_text(inhalt)
            )
            if befunde:
                for befund in befunde:
                    sys.stderr.write(f"- {befund}\n")
                return leiter.ROT
            sys.stdout.write(f"Vorlage sauber: {args.pruefen}\n")
            return leiter.GRUEN
        try:
            text = eigenanschluss_stufe3_yaml(cron=args.cron) if args.eigenanschluss else vorlage_stufe3_yaml()
        except ValueError as fehler:
            sys.stderr.write(f"{fehler}\n")
            return leiter.GATE_FEHLER
        if args.ausgabe is not None:
            args.ausgabe.parent.mkdir(parents=True, exist_ok=True)
            args.ausgabe.write_text(text, encoding="utf-8", newline="\n")
            sys.stdout.write(f"Geschrieben: {args.ausgabe}\n")
        else:
            sys.stdout.write(text)
        return leiter.GRUEN

    if args.befehl == "doppelte":
        bericht = finde_doppelte(
            args.repo,
            test_pfade=args.test_pfade,
            ersteller_agent=args.ersteller_agent,
        )
        daten = bericht.als_dict()
        if args.ausgabe_json is not None:
            _schreibe_json_atomar(args.ausgabe_json, daten)
        _schreibe_summary(args.summary_pfad, bericht.kurzfassung())
        if args.als_json:
            sys.stdout.write(json.dumps(daten, ensure_ascii=False) + "\n")
        else:
            sys.stdout.write(bericht.kurzfassung() + "\n")
        return leiter.GRUEN

    if args.befehl == "freigabe-pruefen":
        if not args.bericht.is_file():
            sys.stderr.write(f"Bericht fehlt: {args.bericht}\n")
            return leiter.GATE_FEHLER
        try:
            vorschlag = _lade_vorschlag_aus_bericht(args.bericht, args.vorschlag_id)
        except (OSError, ValueError) as fehler:
            sys.stderr.write(f"Bericht unlesbar: {fehler}\n")
            return leiter.GATE_FEHLER
        if vorschlag is None:
            sys.stderr.write(f"Vorschlag nicht gefunden: {args.vorschlag_id}\n")
            return leiter.GATE_FEHLER

        nachweis: ZweitprueferNachweis | None = None
        if args.nachweis is not None:
            if not args.nachweis.is_file():
                sys.stderr.write(f"Nachweis fehlt: {args.nachweis}\n")
                return leiter.ROT
            try:
                roh_nachweis = json.loads(args.nachweis.read_text(encoding="utf-8"))
            except (OSError, ValueError) as fehler:
                sys.stderr.write(f"Nachweis unlesbar: {fehler}\n")
                return leiter.GATE_FEHLER
            if not isinstance(roh_nachweis, Mapping):
                sys.stderr.write("Nachweis ist kein JSON-Objekt.\n")
                return leiter.GATE_FEHLER
            nachweis = ZweitprueferNachweis.aus_dict(roh_nachweis)

        schutz = lade_schutzliste(args.repo)
        faelle = {f.test_id: f for f in sammle_testfaelle(args.repo, schutzliste=schutz)}
        freigabe = pruefe_loeschfreigabe(
            vorschlag,
            nachweis,
            test_fall=faelle.get(vorschlag.kandidat),
            schutzliste=schutz,
            ersteller_session_id=args.ersteller_session,
            pruefsumme_pflicht=args.pruefsumme_pflicht,
        )
        if args.als_json:
            sys.stdout.write(json.dumps(freigabe.als_dict(), ensure_ascii=False) + "\n")
        elif freigabe.erlaubt:
            sys.stdout.write(
                f"Freigabe erteilt für {freigabe.test_id} (Ersteller: {freigabe.ersteller_agent}, "
                f"Zweitprüfer: {freigabe.pruefer_agent}).\n"
            )
        else:
            sys.stdout.write(f"Freigabe abgelehnt für {freigabe.test_id}:\n")
            for grund in freigabe.gruende:
                sys.stdout.write(f"- {grund}\n")
        return leiter.GRUEN if freigabe.erlaubt else leiter.ROT

    # Befehl: runde
    try:
        fenster = None if args.wackler_fenster_tage == 0 else args.wackler_fenster_tage
        erg = aufraeum_runde(
            args.repo,
            test_pfade=args.test_pfade,
            junit_pfade=args.junit,
            wackler_schwelle=args.wackler_schwelle,
            wackler_fenster_tage=fenster,
            langsamste_top=args.langsamste_top,
            mutation_aktiv=args.mit_mutation,
            basis=args.basis,
            ziel=args.ziel,
            pfad_filter=args.pfad_filter,
            ersteller_agent=args.ersteller_agent,
            schreiben=not args.nur_pruefen,
            summary_pfad=args.summary_pfad or None,
        )
    except ValueError as fehler:
        sys.stderr.write(f"Ungültige Eingabe: {fehler}\n")
        return leiter.GATE_FEHLER

    daten_r = erg.als_dict()
    if args.ausgabe_json is not None:
        _schreibe_json_atomar(args.ausgabe_json, daten_r)
    if args.als_json:
        sys.stdout.write(json.dumps(daten_r, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(erg.zusammenfassung() + "\n")
    return leiter.GRUEN


if __name__ == "__main__":
    raise SystemExit(main())
