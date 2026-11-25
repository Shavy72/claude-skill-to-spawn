"""Stufe 3 Diff-Mutationstest mit ``mutmut`` – Linux-Teil (#799, Teil von #581, Beschlüsse E2/E16/E19/E26).

Linux-Baustein für den Mutationstest-Beweis ausschließlich auf geändertem Produktivcode:
- Ermittelt geänderte Produktiv-Python-Dateien aus einem git-Diff über :func:`sofort.geaendert_zwischen`
  und schließt Testdateien, Test-Helfer, Doku-Dateien (auch ``.py`` unter ``docs/``), Werkzeug-Ordner
  und gelöschte Dateien strikt aus.
- Begrenzt den ``mutmut``-Lauf strikt auf genau diese geänderten Dateien (unberührte Dateien werden
  weder übergeben noch mutiert) und nutzt den AST-Import-Graph (:mod:`to_spawn.pruef.import_graph`),
  um den Test-Runner gezielt auf betroffene Tests einzugrenzen.
- Liefert ein strukturiertes Ergebnis (:class:`Ergebnis`) mit Zählern (getötete, überlebende,
  abgelaufene, verdächtige, übersprungene Mutanten), geprüften Dateien, Einzel-Liste überlebender
  Mutanten, JSON-Ausgabe und Markdown-Zusammenfassung.
- Klar als Linux-Teil getrennt: ``mutmut`` setzt POSIX/Linux voraus; unter Nicht-Linux erfolgt ein
  sauberer, eindeutiger Hinweis mit Exit-Code 2 (fail-closed) statt Absturz.

CLI: ``python -m to_spawn.pruef.mutation --repo <pfad> --basis <ref> [--ziel <ref>] [--json]``
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import re
import shlex
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from to_spawn import config, konsole
from to_spawn.pruef import adapter_py, ausfuehren, import_graph, leiter, regeln, sofort, test_schutz

log = logging.getLogger("to_spawn.pruef.mutation")

EXIT_GRUEN = leiter.GRUEN
EXIT_ROT = leiter.ROT
EXIT_FEHLER = leiter.GATE_FEHLER

#: Ordner, die keine Produktiv-Python-Dateien für Mutationstests enthalten.
_AUSGESCHLOSSENE_ORDNER: frozenset[str] = frozenset(
    {
        "tests",
        "test",
        "docs",
        "doc",
        "venv",
        ".venv",
        "env",
        ".env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "node_modules",
        "build",
        "dist",
        ".eggs",
        "deps",
        ".git",
        ".hg",
        ".svn",
        "mutants",
    }
)
#: Sondernamen für Test-Konfiguration, die auch außerhalb von ``tests/`` kein Produktivcode sind.
_TEST_SONDERNAMEN: frozenset[str] = frozenset({"conftest.py"})

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)*)")
_MUTMUT2_EMOJI_RE = re.compile(
    r"(?:(?P<aktuell>\d+)/(?P<gesamt>\d+)\s+)?"
    r"🎉\s*(?P<getoetet>\d+)"
    r"(?:\s+⏰\s*(?P<zeitgrenze>\d+))?"
    r"(?:\s+🤔\s*(?P<verdaechtig>\d+))?"
    r"(?:\s+🙁\s*(?P<ueberlebt>\d+))?"
    r"(?:\s+🔇\s*(?P<uebersprungen>\d+))?"
)
_MUTMUT2_TEXT_RE = re.compile(
    r"(?:(?P<aktuell>\d+)/(?P<gesamt>\d+)\s+)?"
    r"KILLED\s+(?P<getoetet>\d+)"
    r"(?:\s+TIMEOUT\s+(?P<zeitgrenze>\d+))?"
    r"(?:\s+SUSPICIOUS\s+(?P<verdaechtig>\d+))?"
    r"(?:\s+SURVIVED\s+(?P<ueberlebt>\d+))?"
    r"(?:\s+SKIPPED\s+(?P<uebersprungen>\d+))?",
    re.IGNORECASE,
)
_MUTMUT3_ZEILE_RE = re.compile(
    r"^\s*(?P<kennung>[A-Za-z_][A-Za-z0-9_.]*__mutmut_\d+)\s*:\s*(?P<status>[a-z][a-z _-]*)\s*$",
    re.IGNORECASE,
)
_RESULTS_ABSCHNITT_RE = re.compile(
    r"^(?P<art>Survived|Timed out|Suspicious|Skipped)\b",
    re.IGNORECASE,
)
_RESULTS_DATEI_RE = re.compile(r"^-{3,}\s+(?P<pfad>.+?)\s+\(\d+\)\s+-{3,}$")
_ID_BEREICH_RE = re.compile(r"^(\d+)-(\d+)$")

_ABSCHNITT_ZU_STATUS: dict[str, str] = {
    "survived": "ueberlebt",
    "timed out": "zeitgrenze",
    "suspicious": "verdaechtig",
    "skipped": "uebersprungen",
}
_MUTMUT3_STATUS_ZU_FELD: dict[str, str] = {
    "killed": "getoetet",
    "survived": "ueberlebt",
    "timeout": "zeitgrenze",
    "timed out": "zeitgrenze",
    "suspicious": "verdaechtig",
    "skipped": "uebersprungen",
    "not checked": "ungeprueft",
    "untested": "ungeprueft",
    "segfault": "getoetet",
}
_MELDE_STATUS: frozenset[str] = frozenset({"ueberlebt", "zeitgrenze", "verdaechtig"})


@dataclass(frozen=True)
class Zaehler:
    """Mutanten-Zählwerte eines Diff-Mutationstests."""

    gesamt: int = 0
    getoetet: int = 0
    ueberlebt: int = 0
    zeitgrenze: int = 0
    verdaechtig: int = 0
    uebersprungen: int = 0
    ungeprueft: int = 0

    @property
    def toetungsrate(self) -> float | None:
        """Anteil getöteter Mutanten an den ausgewerteten Mutanten (``None`` bei 0)."""
        wirksam = self.getoetet + self.ueberlebt + self.zeitgrenze + self.verdaechtig
        if wirksam <= 0:
            return None
        return round(self.getoetet / wirksam, 4)

    def als_dict(self) -> dict[str, Any]:
        return {
            "gesamt": self.gesamt,
            "getoetet": self.getoetet,
            "ueberlebt": self.ueberlebt,
            "zeitgrenze": self.zeitgrenze,
            "verdaechtig": self.verdaechtig,
            "uebersprungen": self.uebersprungen,
            "ungeprueft": self.ungeprueft,
            "toetungsrate": self.toetungsrate,
        }


@dataclass(frozen=True)
class UeberlebenderMutant:
    """Einzelner nicht getöteter Mutant (überlebt, Zeitgrenze oder verdächtig)."""

    datei: str
    kennung: str
    status: str = "ueberlebt"

    def als_dict(self) -> dict[str, str]:
        return {
            "datei": self.datei,
            "kennung": self.kennung,
            "status": self.status,
        }

    def kurz(self) -> str:
        ort = f"{self.datei}#{self.kennung}" if self.datei else self.kennung
        return f"- [{self.status}] {ort}"


@dataclass(frozen=True)
class Ergebnis:
    """Strukturiertes Gesamtergebnis von :func:`pruefe_diff`."""

    ok: bool
    exit_code: int
    plattform: str
    unterstuetzt: bool
    lief: bool
    basis: str
    ziel: str | None
    geaenderte_dateien: tuple[str, ...]
    gepruefte_dateien: tuple[str, ...]
    ausgeschlossene_dateien: tuple[str, ...]
    zaehler: Zaehler
    ueberlebende: tuple[UeberlebenderMutant, ...]
    befehl: tuple[str, ...]
    werkzeug: str
    dauer_s: float
    hinweis: str = ""
    ausgabe: str = ""

    def als_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "plattform": self.plattform,
            "unterstuetzt": self.unterstuetzt,
            "lief": self.lief,
            "basis": self.basis,
            "ziel": self.ziel,
            "geaenderte_dateien": list(self.geaenderte_dateien),
            "gepruefte_dateien": list(self.gepruefte_dateien),
            "ausgeschlossene_dateien": list(self.ausgeschlossene_dateien),
            "zaehler": self.zaehler.als_dict(),
            "ueberlebende": [m.als_dict() for m in self.ueberlebende],
            "befehl": list(self.befehl),
            "werkzeug": self.werkzeug,
            "dauer_s": self.dauer_s,
            "hinweis": self.hinweis,
        }

    def bericht(self) -> str:
        if not self.unterstuetzt:
            return f"Mutationstest nicht unterstützt: {self.hinweis}"
        if self.exit_code == EXIT_FEHLER:
            return f"Mutationstest Fehler: {self.hinweis}"
        if not self.lief:
            return f"Mutationstest grün (übersprungen): {self.hinweis}"
        status = "grün" if self.ok else "rot"
        zeilen = [
            (
                f"Mutationstest {status} ({self.dauer_s:.2f} s, {len(self.gepruefte_dateien)} Datei(en)): "
                f"getötet={self.zaehler.getoetet}, überlebt={self.zaehler.ueberlebt}, "
                f"zeitgrenze={self.zaehler.zeitgrenze}, verdächtig={self.zaehler.verdaechtig}, "
                f"übersprungen={self.zaehler.uebersprungen} (gesamt={self.zaehler.gesamt})."
            )
        ]
        if self.gepruefte_dateien:
            zeilen.append("Geprüfte Dateien: " + ", ".join(self.gepruefte_dateien))
        if self.hinweis:
            zeilen.append(f"Hinweis: {self.hinweis}")
        if self.ueberlebende:
            zeilen.extend(m.kurz() for m in self.ueberlebende)
        return "\n".join(zeilen)

    def zusammenfassung(self, titel: str = "Stufe 3 (Diff-Mutationstest)") -> str:
        """Kurze Markdown-Zusammenfassung für ``GITHUB_STEP_SUMMARY`` oder Aufräum-Berichte."""
        if not self.unterstuetzt:
            status = "nicht unterstützt (nur Linux)"
        elif self.exit_code == EXIT_FEHLER:
            status = "gate-fehler"
        elif self.ok:
            status = "grün"
        else:
            status = "rot"
        zeilen = [
            f"### {titel}",
            "",
            (
                f"Status: **{status}** · System: `{self.plattform}` · "
                f"geprüfte Dateien: {len(self.gepruefte_dateien)} · "
                f"getötet: {self.zaehler.getoetet} · überlebend: {self.zaehler.ueberlebt} · "
                f"Zeitgrenze: {self.zaehler.zeitgrenze} · Dauer: {self.dauer_s:.2f} s"
            ),
            "",
        ]
        if self.hinweis:
            zeilen.extend([f"Hinweis: {self.hinweis}", ""])
        if self.gepruefte_dateien:
            zeilen.extend(["<details><summary>Geprüfte Produktiv-Dateien</summary>", ""])
            zeilen.extend(f"- `{pfad}`" for pfad in self.gepruefte_dateien)
            zeilen.extend(["", "</details>", ""])
        if self.ueberlebende:
            zeilen.extend([f"<details><summary>Überlebende/auffällige Mutanten ({len(self.ueberlebende)})</summary>", ""])
            zeilen.extend(m.kurz() for m in self.ueberlebende)
            zeilen.extend(["", "</details>", ""])
        return "\n".join(zeilen)


def ist_linux(plattform: str | None = None) -> bool:
    """``True``, wenn das Zielsystem Linux ist (``mutmut`` setzt POSIX/Linux voraus)."""
    ziel = sys.platform if plattform is None else plattform
    return ziel.lower().startswith("linux")


def ist_produktiv_python(pfad: str) -> bool:
    """``True``, wenn ``pfad`` eine mutierbare Produktiv-Python-Datei ist (ohne Tests/Doku/Werkzeug)."""
    norm = regeln.normal(pfad)
    if not norm:
        return False
    p = PurePosixPath(norm)
    if p.suffix.lower() != ".py":
        return False
    werkzeug_orte = frozenset(ort.lower() for ort in leiter.WERKZEUG_ORTE)
    for teil in p.parts[:-1]:
        klein = teil.lower()
        if (
            klein in _AUSGESCHLOSSENE_ORDNER
            or klein in werkzeug_orte
            or teil.startswith(".")
            or klein.endswith(".egg-info")
        ):
            return False
    if p.name.lower() in _TEST_SONDERNAMEN:
        return False
    return not test_schutz.ist_testdatei(norm)


def _passt_pfad_filter(pfad: str, pfad_filter: Sequence[str]) -> bool:
    if not pfad_filter:
        return True
    for roh in pfad_filter:
        muster = regeln.normal(roh).rstrip("/")
        if not muster:
            continue
        if pfad == muster or pfad.startswith(f"{muster}/") or fnmatch.fnmatchcase(pfad, muster):
            return True
    return False


def _git_wurzel(repo: Path) -> tuple[Path | None, str]:
    code, raus = sofort._git(repo, "rev-parse", "--show-toplevel")
    if code != 0:
        kurz = (raus.strip().splitlines() or ["kein Git-Repo"])[0]
        return None, f"kein Git-Repo ({repo}): {kurz}"
    return Path(raus.strip()), ""


def _diff_roh_pfade(
    repo: Path,
    basis: str,
    ziel: str | None,
    pfade: Sequence[str] | None,
) -> tuple[Path | None, tuple[str, ...] | None, str]:
    if pfade is None:
        wurzel, fehler = _git_wurzel(repo)
        if wurzel is None:
            return None, None, fehler
        diff = sofort.geaendert_zwischen(wurzel, basis, ziel)
        if diff is None:
            spanne = f"{basis}..{ziel}" if ziel else f"{basis}..Arbeitsbaum"
            return wurzel, None, f"geänderte Dateien für {spanne} unbekannt (git diff gescheitert)"
        roh = diff
    else:
        wurzel = config.repo_wurzel(repo)
        roh = [p for p in pfade if p and p.strip()]
    normalisiert = tuple(sorted({regeln.normal(p) for p in roh if p and p.strip()}))
    return wurzel, normalisiert, ""


def geaenderte_produktiv_dateien(
    repo: Path,
    basis: str = "HEAD",
    ziel: str | None = None,
    *,
    pfade: Sequence[str] | None = None,
    pfad_filter: Sequence[str] | None = None,
    nur_vorhandene: bool = True,
) -> tuple[tuple[str, ...] | None, str]:
    """Bestimmt geänderte Produktiv-Python-Dateien aus dem git-Diff (ohne Tests, Doku, Gelöschtes)."""
    wurzel, alle, fehler = _diff_roh_pfade(repo, basis, ziel, pfade)
    if fehler or wurzel is None or alle is None:
        return None, fehler
    filter_liste = tuple(pfad_filter or ())
    treffer = [
        pfad
        for pfad in alle
        if ist_produktiv_python(pfad)
        and _passt_pfad_filter(pfad, filter_liste)
        and (not nur_vorhandene or (wurzel / pfad).is_file())
    ]
    return tuple(treffer), ""


def _version_aus_text(text: str) -> str:
    treffer = _VERSION_RE.search(text)
    return treffer.group(1) if treffer else "?"


def _hauptversion(werkzeug: str) -> int:
    _, _, version = werkzeug.partition("@")
    erster = version.split(".", 1)[0]
    return int(erster) if erster.isdigit() else 2


def _finde_mutmut(repo: Path) -> tuple[list[str] | None, str]:
    """Sucht ``mutmut`` zuerst im Repo-Interpreter (``python -m mutmut``), sonst im PATH."""
    python = adapter_py._python(repo)
    probe = ausfuehren.lauf([python, "-m", "mutmut", "--version"], repo)
    if probe.code == 0:
        return [python, "-m", "mutmut"], f"mutmut@{_version_aus_text(probe.ausgabe)}"
    programm = sofort._finde_werkzeug(repo, "mutmut")
    if programm is not None:
        probe_bin = ausfuehren.lauf([programm, "--version"], repo)
        if probe_bin.code == 0:
            return [programm], f"mutmut@{_version_aus_text(probe_bin.ausgabe)}"
    return None, ""


def _modul_filter(pfad: str) -> str:
    """Leitet aus einem Python-Relativpfad einen ``mutmut``-3-Modulfilter (``pkg.mod.*``) ab."""
    p = PurePosixPath(regeln.normal(pfad)).with_suffix("")
    teile = list(p.parts)
    if len(teile) >= 2 and teile[0] == "src":
        teile = teile[1:]
    if teile and teile[-1] == "__init__":
        teile = teile[:-1]
    name = ".".join(teile)
    return f"{name}.*" if name else ""


def _standard_runner(repo: Path, dateien: Sequence[str]) -> str:
    """Baut den pytest-Runner für ``mutmut``; grenzt Tests nach Möglichkeit per Import-Graph ein."""
    python = adapter_py._python(repo)
    basis_cmd = [python, "-m", "pytest", "-q", "-x", "--assert=plain"]
    try:
        wahl = import_graph.auswaehlen(repo, geaendert=dateien)
    except (OSError, RuntimeError, ValueError) as fehler:
        log.debug("Import-Graph für Mutationstest-Runner übersprungen: %s", fehler)
        return shlex.join(basis_cmd)
    if wahl.tests:
        return shlex.join([*basis_cmd, *wahl.tests])
    return shlex.join(basis_cmd)


def baue_mutmut_befehl(
    mutmut_basis: Sequence[str],
    dateien: Sequence[str],
    *,
    runner: str | None = None,
    hauptversion: int = 2,
) -> list[str]:
    """Erzeugt den strikt auf ``dateien`` begrenzten ``mutmut run``-Befehl."""
    saubere = [regeln.normal(p) for p in dateien if p and p.strip()]
    if hauptversion >= 3:
        muster = [m for m in (_modul_filter(p) for p in saubere) if m]
        return [*mutmut_basis, "run", *muster]
    befehl = [*mutmut_basis, "run", "--paths-to-mutate", ",".join(saubere)]
    if runner:
        befehl.extend(["--runner", runner])
    return befehl


def _bereinige_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def parse_mutmut_ausgabe(ausgabe: str) -> Zaehler | None:
    """Extrahiert Mutanten-Zähler aus ``mutmut run``- oder ``mutmut results``-Ausgaben (2.x + 3.x)."""
    sauber = _bereinige_ansi(ausgabe)
    letzter_treffer: re.Match[str] | None = None
    for zeile in re.split(r"[\r\n]+", sauber):
        treffer = _MUTMUT2_EMOJI_RE.search(zeile) or _MUTMUT2_TEXT_RE.search(zeile)
        if treffer is not None:
            letzter_treffer = treffer
    if letzter_treffer is not None:
        getoetet = int(letzter_treffer.group("getoetet") or 0)
        zeitgrenze = int(letzter_treffer.group("zeitgrenze") or 0)
        verdaechtig = int(letzter_treffer.group("verdaechtig") or 0)
        ueberlebt = int(letzter_treffer.group("ueberlebt") or 0)
        uebersprungen = int(letzter_treffer.group("uebersprungen") or 0)
        summe = getoetet + zeitgrenze + verdaechtig + ueberlebt + uebersprungen
        gesamt_roh = letzter_treffer.group("gesamt")
        gesamt = max(int(gesamt_roh), summe) if gesamt_roh is not None else summe
        ungeprueft = max(0, gesamt - summe)
        return Zaehler(
            gesamt=gesamt,
            getoetet=getoetet,
            ueberlebt=ueberlebt,
            zeitgrenze=zeitgrenze,
            verdaechtig=verdaechtig,
            uebersprungen=uebersprungen,
            ungeprueft=ungeprueft,
        )

    # Rückfall: mutmut-3-Einzelzeilen (``modul.fn__mutmut_1: survived``)
    zaehlwerte = {
        "getoetet": 0,
        "ueberlebt": 0,
        "zeitgrenze": 0,
        "verdaechtig": 0,
        "uebersprungen": 0,
        "ungeprueft": 0,
    }
    gefunden = 0
    for zeile in sauber.splitlines():
        treffer = _MUTMUT3_ZEILE_RE.match(zeile)
        if treffer is None:
            continue
        feld = _MUTMUT3_STATUS_ZU_FELD.get(treffer.group("status").strip().lower())
        if feld is not None:
            zaehlwerte[feld] += 1
            gefunden += 1
    if gefunden > 0:
        return Zaehler(gesamt=gefunden, **zaehlwerte)
    return None


def _entpacke_id_liste(zeile: str) -> list[str]:
    """Entpackt ID-Listen wie ``2, 5-7`` zu ``['2', '5', '6', '7']``."""
    ids: list[str] = []
    for teil in zeile.split(","):
        eintrag = teil.strip()
        if not eintrag:
            continue
        bereich = _ID_BEREICH_RE.match(eintrag)
        if bereich is not None:
            von, bis = int(bereich.group(1)), int(bereich.group(2))
            if von <= bis and (bis - von) <= 10_000:
                ids.extend(str(nr) for nr in range(von, bis + 1))
                continue
        if eintrag.isdigit():
            ids.append(eintrag)
    return ids


def parse_mutmut_results(ausgabe: str) -> tuple[UeberlebenderMutant, ...]:
    """Parst überlebende/auffällige Mutanten aus ``mutmut results`` (2.x-Blöcke und 3.x-Zeilen)."""
    sauber = _bereinige_ansi(ausgabe)
    funde: list[UeberlebenderMutant] = []
    gesehen: set[tuple[str, str, str]] = set()

    def _merke(datei: str, kennung: str, status: str) -> None:
        if status not in _MELDE_STATUS:
            return
        schluessel = (datei, kennung, status)
        if schluessel not in gesehen:
            gesehen.add(schluessel)
            funde.append(UeberlebenderMutant(datei=datei, kennung=kennung, status=status))

    aktueller_status = ""
    aktuelle_datei = ""
    for roh_zeile in sauber.splitlines():
        zeile = roh_zeile.strip()
        if not zeile:
            continue
        treffer3 = _MUTMUT3_ZEILE_RE.match(zeile)
        if treffer3 is not None:
            feld = _MUTMUT3_STATUS_ZU_FELD.get(treffer3.group("status").strip().lower(), "")
            if feld in _MELDE_STATUS:
                _merke("", treffer3.group("kennung"), feld)
            continue
        abschnitt = _RESULTS_ABSCHNITT_RE.match(zeile)
        if abschnitt is not None:
            aktueller_status = _ABSCHNITT_ZU_STATUS.get(abschnitt.group("art").lower(), "")
            aktuelle_datei = ""
            continue
        datei_kopf = _RESULTS_DATEI_RE.match(zeile)
        if datei_kopf is not None:
            aktuelle_datei = regeln.normal(datei_kopf.group("pfad").strip())
            continue
        if aktueller_status in _MELDE_STATUS:
            for kennung in _entpacke_id_liste(zeile):
                _merke(aktuelle_datei, kennung, aktueller_status)
    return tuple(funde)


def _fehler_ergebnis(
    *,
    plattform: str,
    unterstuetzt: bool,
    basis: str,
    ziel: str | None,
    hinweis: str,
    dauer_s: float,
    geaendert: tuple[str, ...] = (),
    geprueft: tuple[str, ...] = (),
    ausgeschlossen: tuple[str, ...] = (),
    befehl: tuple[str, ...] = (),
    werkzeug: str = "",
    ausgabe: str = "",
) -> Ergebnis:
    return Ergebnis(
        ok=False,
        exit_code=EXIT_FEHLER,
        plattform=plattform,
        unterstuetzt=unterstuetzt,
        lief=False,
        basis=basis,
        ziel=ziel,
        geaenderte_dateien=geaendert,
        gepruefte_dateien=geprueft,
        ausgeschlossene_dateien=ausgeschlossen,
        zaehler=Zaehler(),
        ueberlebende=(),
        befehl=befehl,
        werkzeug=werkzeug,
        dauer_s=round(dauer_s, 2),
        hinweis=hinweis,
        ausgabe=ausgabe,
    )


def pruefe_diff(
    repo: Path,
    basis: str = "HEAD",
    ziel: str | None = None,
    *,
    pfade: Sequence[str] | None = None,
    pfad_filter: Sequence[str] | None = None,
    runner: str | None = None,
    frischer_cache: bool = True,
    plattform: str | None = None,
) -> Ergebnis:
    """Führt den Diff-Mutationstest mit ``mutmut`` ausschließlich auf geänderten Produktiv-Dateien aus.

    Unter Nicht-Linux wird kein ``mutmut`` gestartet; stattdessen gibt die Funktion sauber
    ``unterstuetzt=False`` und ``exit_code=2`` mit eindeutigem Hinweis zurück.
    """
    start = time.monotonic()
    aktuelle_plattform = sys.platform if plattform is None else plattform
    if not ist_linux(aktuelle_plattform):
        hinweis = (
            f"Mutationstest (mutmut) ist nur unter Linux verfügbar "
            f"(aktuelles System: {aktuelle_plattform}); auf Nicht-Linux bitte den Linux-Runner nutzen."
        )
        log.info("%s", hinweis)
        return _fehler_ergebnis(
            plattform=aktuelle_plattform,
            unterstuetzt=False,
            basis=basis,
            ziel=ziel,
            hinweis=hinweis,
            dauer_s=time.monotonic() - start,
        )

    wurzel, alle_geaendert, diff_fehler = _diff_roh_pfade(repo, basis, ziel, pfade)
    if diff_fehler or wurzel is None or alle_geaendert is None:
        return _fehler_ergebnis(
            plattform=aktuelle_plattform,
            unterstuetzt=True,
            basis=basis,
            ziel=ziel,
            hinweis=diff_fehler or "git diff gescheitert",
            dauer_s=time.monotonic() - start,
        )

    filter_liste = tuple(pfad_filter or ())
    gepruefte = tuple(
        pfad
        for pfad in alle_geaendert
        if ist_produktiv_python(pfad)
        and _passt_pfad_filter(pfad, filter_liste)
        and (wurzel / pfad).is_file()
    )
    gepruefte_menge = frozenset(gepruefte)
    ausgeschlossene = tuple(pfad for pfad in alle_geaendert if pfad not in gepruefte_menge)

    if not gepruefte:
        return Ergebnis(
            ok=True,
            exit_code=EXIT_GRUEN,
            plattform=aktuelle_plattform,
            unterstuetzt=True,
            lief=False,
            basis=basis,
            ziel=ziel,
            geaenderte_dateien=alle_geaendert,
            gepruefte_dateien=(),
            ausgeschlossene_dateien=ausgeschlossene,
            zaehler=Zaehler(),
            ueberlebende=(),
            befehl=(),
            werkzeug="",
            dauer_s=round(time.monotonic() - start, 2),
            hinweis="Keine geänderten Produktiv-Python-Dateien im Diff — Mutationstest übersprungen.",
        )

    mutmut_basis, werkzeug = _finde_mutmut(wurzel)
    if mutmut_basis is None:
        return _fehler_ergebnis(
            plattform=aktuelle_plattform,
            unterstuetzt=True,
            basis=basis,
            ziel=ziel,
            hinweis="mutmut fehlt (nur Linux; installieren z. B. mit 'python -m pip install mutmut').",
            dauer_s=time.monotonic() - start,
            geaendert=alle_geaendert,
            geprueft=gepruefte,
            ausgeschlossen=ausgeschlossene,
        )

    if frischer_cache:
        cache_datei = wurzel / ".mutmut-cache"
        if cache_datei.is_file():
            try:
                cache_datei.unlink()
            except OSError as fehler:
                log.debug("Alter .mutmut-cache nicht entfernbar (%s): %s", cache_datei, fehler)

    haupt = _hauptversion(werkzeug)
    test_runner = runner if runner is not None else (_standard_runner(wurzel, gepruefte) if haupt < 3 else None)
    befehl = baue_mutmut_befehl(mutmut_basis, gepruefte, runner=test_runner, hauptversion=haupt)

    log.info("Starte Diff-Mutationstest (%s) für %d Datei(en): %s", werkzeug, len(gepruefte), ", ".join(gepruefte))
    lauf_erg = ausfuehren.lauf(befehl, wurzel)
    if lauf_erg.code == ausfuehren.NICHT_GESTARTET:
        return _fehler_ergebnis(
            plattform=aktuelle_plattform,
            unterstuetzt=True,
            basis=basis,
            ziel=ziel,
            hinweis=f"mutmut konnte nicht gestartet werden: {lauf_erg.ausgabe.strip()}",
            dauer_s=time.monotonic() - start,
            geaendert=alle_geaendert,
            geprueft=gepruefte,
            ausgeschlossen=ausgeschlossene,
            befehl=tuple(befehl),
            werkzeug=werkzeug,
            ausgabe=lauf_erg.ausgabe,
        )

    zaehler = parse_mutmut_ausgabe(lauf_erg.ausgabe)
    ueberlebende: tuple[UeberlebenderMutant, ...] = ()
    braucht_results = zaehler is None or zaehler.ueberlebt > 0 or zaehler.zeitgrenze > 0 or zaehler.verdaechtig > 0
    if braucht_results and lauf_erg.code != 1:
        res_erg = ausfuehren.lauf([*mutmut_basis, "results"], wurzel)
        if res_erg.code == 0:
            ueberlebende = parse_mutmut_results(res_erg.ausgabe)
            if zaehler is None:
                zaehler = parse_mutmut_ausgabe(res_erg.ausgabe)

    if zaehler is None:
        letzte = (lauf_erg.ausgabe.strip().splitlines() or ["keine Ausgabe"])[-1]
        return _fehler_ergebnis(
            plattform=aktuelle_plattform,
            unterstuetzt=True,
            basis=basis,
            ziel=ziel,
            hinweis=f"mutmut-Lauf gescheitert (Exit {lauf_erg.code}): {letzte}",
            dauer_s=time.monotonic() - start,
            geaendert=alle_geaendert,
            geprueft=gepruefte,
            ausgeschlossen=ausgeschlossene,
            befehl=tuple(befehl),
            werkzeug=werkzeug,
            ausgabe=lauf_erg.ausgabe,
        )

    if zaehler.ueberlebt > 0:
        return Ergebnis(
            ok=False,
            exit_code=EXIT_ROT,
            plattform=aktuelle_plattform,
            unterstuetzt=True,
            lief=True,
            basis=basis,
            ziel=ziel,
            geaenderte_dateien=alle_geaendert,
            gepruefte_dateien=gepruefte,
            ausgeschlossene_dateien=ausgeschlossene,
            zaehler=zaehler,
            ueberlebende=ueberlebende,
            befehl=tuple(befehl),
            werkzeug=werkzeug,
            dauer_s=round(time.monotonic() - start, 2),
            hinweis=f"{zaehler.ueberlebt} überlebende(r) Mutant(en) in {len(gepruefte)} Datei(en).",
            ausgabe=lauf_erg.ausgabe,
        )

    if lauf_erg.code not in (0, 4, 8):
        letzte = (lauf_erg.ausgabe.strip().splitlines() or ["unerwarteter Exit-Code"])[-1]
        return _fehler_ergebnis(
            plattform=aktuelle_plattform,
            unterstuetzt=True,
            basis=basis,
            ziel=ziel,
            hinweis=f"mutmut endete unerwartet (Exit {lauf_erg.code}): {letzte}",
            dauer_s=time.monotonic() - start,
            geaendert=alle_geaendert,
            geprueft=gepruefte,
            ausgeschlossen=ausgeschlossene,
            befehl=tuple(befehl),
            werkzeug=werkzeug,
            ausgabe=lauf_erg.ausgabe,
        )

    hinweis = (
        f"Alle {zaehler.getoetet} geprüften Mutanten getötet."
        if zaehler.gesamt > 0
        else "0 Mutanten in den geänderten Dateien erzeugt."
    )
    return Ergebnis(
        ok=True,
        exit_code=EXIT_GRUEN,
        plattform=aktuelle_plattform,
        unterstuetzt=True,
        lief=True,
        basis=basis,
        ziel=ziel,
        geaenderte_dateien=alle_geaendert,
        gepruefte_dateien=gepruefte,
        ausgeschlossene_dateien=ausgeschlossene,
        zaehler=zaehler,
        ueberlebende=ueberlebende,
        befehl=tuple(befehl),
        werkzeug=werkzeug,
        dauer_s=round(time.monotonic() - start, 2),
        hinweis=hinweis,
        ausgabe=lauf_erg.ausgabe,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m to_spawn.pruef.mutation",
        description="Stufe 3 Diff-Mutationstest (mutmut, nur Linux) ausschließlich auf geänderten Produktiv-Dateien.",
    )
    parser.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner (Vorgabe: aktueller Ordner)")
    parser.add_argument(
        "--basis", "--base", dest="basis", default="HEAD", help="Ausgangsstand (git-Ref, Vorgabe: HEAD)"
    )
    parser.add_argument(
        "--ziel",
        default=None,
        help="Zielstand (git-Ref, z. B. HEAD in CI; weglassen = Arbeitsbaum gegen Basis prüfen)",
    )
    parser.add_argument(
        "--geaendert",
        nargs="+",
        default=None,
        help="Geänderte Relativpfade explizit vorgeben statt git diff",
    )
    parser.add_argument(
        "--pfad-filter",
        nargs="+",
        default=None,
        help="Optionale Pfad-Filter (Ordner-Präfixe oder fnmatch-Muster)",
    )
    parser.add_argument(
        "--runner",
        default=None,
        help="Eigener Test-Runner-Befehl für mutmut (Vorgabe: pytest mit Import-Graph-Auswahl)",
    )
    parser.add_argument(
        "--cache-behalten",
        action="store_true",
        help="Vorhandenen .mutmut-cache vor dem Lauf nicht löschen",
    )
    parser.add_argument(
        "--zusammenfassung",
        type=Path,
        default=None,
        help="Schreibt eine Markdown-Zusammenfassung in diese Datei (z. B. GITHUB_STEP_SUMMARY)",
    )
    parser.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON auf stdout ausgeben")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    konsole.utf8_ausgabe()
    args = _parser().parse_args(argv)
    erg = pruefe_diff(
        args.repo,
        args.basis,
        args.ziel,
        pfade=args.geaendert,
        pfad_filter=args.pfad_filter,
        runner=args.runner,
        frischer_cache=not args.cache_behalten,
    )
    if args.zusammenfassung is not None:
        args.zusammenfassung.parent.mkdir(parents=True, exist_ok=True)
        args.zusammenfassung.write_text(erg.zusammenfassung() + "\n", encoding="utf-8")
    if args.als_json:
        sys.stdout.write(json.dumps(erg.als_dict(), ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(erg.bericht() + "\n")
    return erg.exit_code


if __name__ == "__main__":
    sys.exit(main())
