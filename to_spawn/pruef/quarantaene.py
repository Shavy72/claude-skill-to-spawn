"""Stufe 3: Wackler-Quarantäne-Ledger und Meldung langsamster Tests (#798, Teil von #581).

Baut auf der bestehenden Wackler-Spur (:mod:`to_spawn.pruef.wackler`,
``.to-spawn/pruef/wackler.jsonl``) und JUnit-XML-Laufzeitberichten auf
(plattformübergreifend für Linux und Windows):

- :func:`erkenne_wiederkehrende_wackler` wertet Wackler-Funde mit konfigurierbarer Schwelle
  und optionalem Zeitfenster aus und erzeugt strukturierte Quarantäne-Vorschläge.
- :func:`lade_ledger`, :func:`schreibe_ledger` und :func:`aktualisiere_ledger` führen das
  Quarantäne-Ledger unter ``.to-spawn/pruef/quarantaene.json`` (Status
  ``vorgeschlagen``, ``quarantaene``, ``aufgehoben``). Testdateien im Repo werden niemals
  verändert oder gelöscht.
- :func:`langsamste_tests` liest eine oder mehrere JUnit-XML-Dateien (auch ganze Ordner aus
  verteilten Läufen) und liefert einen Top-N-Bericht (maschinenlesbar + Markdown-Kurzfassung).
- :func:`erstelle_bericht` und die CLI ``python -m to_spawn.pruef.quarantaene`` bündeln beide
  Stufe-3-Berichte für lokale Nutzung und GitHub Actions.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from to_spawn import konsole
from to_spawn.pruef import protokoll, wackler

log = logging.getLogger("to_spawn.pruef.quarantaene")

LEDGER_VERSION = 1
STANDARD_SCHWELLE = 2
STANDARD_FENSTER_TAGE = 14
STANDARD_TOP_N = 10

STATUS_VORGESCHLAGEN = "vorgeschlagen"
STATUS_QUARANTAENE = "quarantaene"
STATUS_AUFGEHOBEN = "aufgehoben"
GUELTIGE_STATI = frozenset({STATUS_VORGESCHLAGEN, STATUS_QUARANTAENE, STATUS_AUFGEHOBEN})


def _jetzt_utc(jetzt: datetime | None = None) -> datetime:
    if jetzt is None:
        return datetime.now(UTC)
    return jetzt.astimezone(UTC) if jetzt.tzinfo is not None else jetzt.replace(tzinfo=UTC)


def _iso_utc(zeitpunkt: datetime) -> str:
    return _jetzt_utc(zeitpunkt).isoformat(timespec="seconds")


def _frueher(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    za, zb = wackler.parse_zeit(a), wackler.parse_zeit(b)
    if za is not None and zb is not None:
        return a if za <= zb else b
    return min(a, b)


def _spaeter(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    za, zb = wackler.parse_zeit(a), wackler.parse_zeit(b)
    if za is not None and zb is not None:
        return a if za >= zb else b
    return max(a, b)


def _ist_neuer_als(neu: str, referenz: str) -> bool:
    if not neu:
        return False
    if not referenz:
        return True
    zn, zr = wackler.parse_zeit(neu), wackler.parse_zeit(referenz)
    if zn is not None and zr is not None:
        return zn > zr
    return neu > referenz


@dataclass(frozen=True)
class WacklerVorschlag:
    """Quarantäne-Vorschlag für einen wiederkehrend wackelnden Test (nie Löschung)."""

    test: str
    adapter: str
    treffer: int
    schwelle: int
    erster_fund: str
    letzter_fund: str
    staende: tuple[str, ...]
    status: str = STATUS_VORGESCHLAGEN
    aktion: str = "quarantaene_vorschlagen"
    grund: str = ""

    def als_dict(self) -> dict[str, Any]:
        return {
            "test": self.test,
            "adapter": self.adapter,
            "treffer": self.treffer,
            "schwelle": self.schwelle,
            "erster_fund": self.erster_fund,
            "letzter_fund": self.letzter_fund,
            "staende": list(self.staende),
            "status": self.status,
            "aktion": self.aktion,
            "grund": self.grund,
        }


@dataclass(frozen=True)
class QuarantaeneEintrag:
    """Ein Eintrag im Quarantäne-Ledger (``.to-spawn/pruef/quarantaene.json``)."""

    test: str
    adapter: str
    status: str
    treffer: int
    erster_fund: str
    letzter_fund: str
    aktualisiert: str
    grund: str
    quelle: str = protokoll.WACKLER_DATEI
    staende: tuple[str, ...] = ()

    def als_dict(self) -> dict[str, Any]:
        return {
            "test": self.test,
            "adapter": self.adapter,
            "status": self.status,
            "treffer": self.treffer,
            "erster_fund": self.erster_fund,
            "letzter_fund": self.letzter_fund,
            "aktualisiert": self.aktualisiert,
            "grund": self.grund,
            "quelle": self.quelle,
            "staende": list(self.staende),
        }

    @classmethod
    def aus_dict(cls, daten: Mapping[str, Any]) -> QuarantaeneEintrag | None:
        test = wackler.normalisiere_test_id(str(daten.get("test", "")))
        if not test:
            return None
        status = str(daten.get("status", STATUS_VORGESCHLAGEN)).strip()
        if status not in GUELTIGE_STATI:
            status = STATUS_VORGESCHLAGEN
        roh_treffer = daten.get("treffer", 0)
        try:
            treffer = max(0, int(roh_treffer))
        except (TypeError, ValueError):
            treffer = 0
        roh_staende = daten.get("staende", ())
        staende = (
            tuple(str(s).strip() for s in roh_staende if str(s).strip())
            if isinstance(roh_staende, (list, tuple))
            else ()
        )
        return cls(
            test=test,
            adapter=str(daten.get("adapter", "")).strip(),
            status=status,
            treffer=treffer,
            erster_fund=str(daten.get("erster_fund", "")).strip(),
            letzter_fund=str(daten.get("letzter_fund", "")).strip(),
            aktualisiert=str(daten.get("aktualisiert", "")).strip(),
            grund=str(daten.get("grund", "")).strip(),
            quelle=str(daten.get("quelle", protokoll.WACKLER_DATEI)).strip() or protokoll.WACKLER_DATEI,
            staende=staende,
        )


@dataclass(frozen=True)
class QuarantaeneLedger:
    """Persistenter Zustand des Quarantäne-Ledgers."""

    version: int = LEDGER_VERSION
    aktualisiert: str = ""
    schwelle: int = STANDARD_SCHWELLE
    fenster_tage: int | None = STANDARD_FENSTER_TAGE
    eintraege: tuple[QuarantaeneEintrag, ...] = ()

    def als_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "aktualisiert": self.aktualisiert,
            "schwelle": self.schwelle,
            "fenster_tage": self.fenster_tage,
            "nur_vorschlaege": True,
            "eintraege": [e.als_dict() for e in self.eintraege],
        }

    def aktive_tests(self, *, inklusive_vorschlaege: bool = False) -> tuple[str, ...]:
        """Namen der Tests in Quarantäne (optional inklusive noch offener Vorschläge)."""
        erlaubt = {STATUS_QUARANTAENE, STATUS_VORGESCHLAGEN} if inklusive_vorschlaege else {STATUS_QUARANTAENE}
        return tuple(e.test for e in self.eintraege if e.status in erlaubt)

    def deselect_argumente(self, *, inklusive_vorschlaege: bool = False) -> tuple[str, ...]:
        """Liefert ``--deselect``-Argumente für pytest, ohne Testdateien anzutasten."""
        args: list[str] = []
        for test_id in self.aktive_tests(inklusive_vorschlaege=inklusive_vorschlaege):
            args.extend(["--deselect", test_id])
        return tuple(args)


@dataclass(frozen=True)
class QuarantaeneAktualisierung:
    """Ergebnis einer Auswertung der Wackler-Spur gegen das Quarantäne-Ledger."""

    ledger: QuarantaeneLedger
    vorschlaege: tuple[WacklerVorschlag, ...]
    neu: tuple[str, ...]
    bestehend: tuple[str, ...]
    gesamte_funde: int
    ledger_pfad: Path
    geschrieben: bool

    def als_dict(self) -> dict[str, Any]:
        return {
            "nur_vorschlaege": True,
            "geschrieben": self.geschrieben,
            "ledger_pfad": self.ledger_pfad.as_posix(),
            "gesamte_funde": self.gesamte_funde,
            "neu": list(self.neu),
            "bestehend": list(self.bestehend),
            "vorschlaege": [v.als_dict() for v in self.vorschlaege],
            "ledger": self.ledger.als_dict(),
        }

    def kurzfassung(self, titel: str = "Wackler-Quarantäne") -> str:
        aktiv = len(self.ledger.aktive_tests(inklusive_vorschlaege=False))
        vorgeschlagen = sum(1 for e in self.ledger.eintraege if e.status == STATUS_VORGESCHLAGEN)
        zeilen = [
            f"### {titel}",
            "",
            (
                f"Wackler-Funde: {self.gesamte_funde} · Vorschläge aktuell: {len(self.vorschlaege)} · "
                f"neu: {len(self.neu)} · im Ledger vorgeschlagen: {vorgeschlagen} · "
                f"in Quarantäne: {aktiv}"
            ),
            "",
        ]
        if self.vorschlaege:
            zeilen.append("Wiederkehrende Wackler (nur Vorschläge, keine Tests gelöscht):")
            zeilen.append("")
            for v in self.vorschlaege:
                zeilen.append(
                    f"- `{v.test}` — {v.treffer}× (Schwelle {v.schwelle}, zuletzt {v.letzter_fund or 'unbekannt'})"
                )
            zeilen.append("")
        else:
            zeilen.append("Keine wiederkehrenden Wackler über der Schwelle.")
            zeilen.append("")
        return "\n".join(zeilen)


@dataclass(frozen=True)
class TestLaufzeit:
    """Einzelner Testfall mit Laufzeit aus einem JUnit-XML-Bericht."""

    test: str
    dauer_s: float
    status: str
    quelle: str
    datei: str = ""
    classname: str = ""
    name: str = ""

    def als_dict(self) -> dict[str, Any]:
        return {
            "test": self.test,
            "dauer_s": self.dauer_s,
            "status": self.status,
            "quelle": self.quelle,
            "datei": self.datei,
            "classname": self.classname,
            "name": self.name,
        }


@dataclass(frozen=True)
class LangsamsteBericht:
    """Top-N-Auswertung der langsamsten Tests aus JUnit-XML-Berichten."""

    top_n: int
    eintraege: tuple[TestLaufzeit, ...]
    gesamt_tests: int
    gesamt_dauer_s: float
    dateien: tuple[str, ...]
    fehler: tuple[str, ...] = ()
    mindest_s: float = 0.0

    def als_dict(self) -> dict[str, Any]:
        return {
            "top_n": self.top_n,
            "mindest_s": self.mindest_s,
            "gesamt_tests": self.gesamt_tests,
            "gesamt_dauer_s": self.gesamt_dauer_s,
            "dateien": list(self.dateien),
            "fehler": list(self.fehler),
            "eintraege": [e.als_dict() for e in self.eintraege],
        }

    def kurzfassung(self, titel: str = "Langsamste Tests") -> str:
        zeilen = [
            f"### {titel}",
            "",
            (
                f"Top {self.top_n} aus {self.gesamt_tests} Tests ({len(self.dateien)} JUnit-XML) · "
                f"Gesamtzeit {self.gesamt_dauer_s:.2f} s"
            ),
            "",
        ]
        if self.eintraege:
            for rang, eintrag in enumerate(self.eintraege, start=1):
                anteil = (eintrag.dauer_s / self.gesamt_dauer_s * 100.0) if self.gesamt_dauer_s > 0 else 0.0
                zeilen.append(f"{rang}. `{eintrag.test}` — {eintrag.dauer_s:.2f} s ({anteil:.1f} %, {eintrag.status})")
            zeilen.append("")
        else:
            zeilen.append("Keine Test-Laufzeiten gefunden.")
            zeilen.append("")
        if self.fehler:
            zeilen.append("Hinweise zu unlesbaren Berichten:")
            zeilen.append("")
            for meldung in self.fehler:
                zeilen.append(f"- {meldung}")
            zeilen.append("")
        return "\n".join(zeilen)


@dataclass(frozen=True)
class Stufe3Bericht:
    """Gebündelter Stufe-3-Bericht: Wackler-Quarantäne und langsamste Tests (nur Vorschläge)."""

    quarantaene: QuarantaeneAktualisierung
    langsamste: LangsamsteBericht
    nur_vorschlaege: bool = True
    geloeschte_dateien: tuple[str, ...] = ()

    def als_dict(self) -> dict[str, Any]:
        return {
            "nur_vorschlaege": self.nur_vorschlaege,
            "geloeschte_dateien": list(self.geloeschte_dateien),
            "quarantaene": self.quarantaene.als_dict(),
            "langsamste": self.langsamste.als_dict(),
        }

    def kurzfassung(self, titel: str = "Stufe 3: Wackler-Quarantäne & langsamste Tests") -> str:
        zeilen = [
            f"## {titel}",
            "",
            "Nur Vorschläge/Berichte — keine Tests gelöscht.",
            "",
            self.quarantaene.kurzfassung().rstrip(),
            "",
            self.langsamste.kurzfassung().rstrip(),
            "",
        ]
        return "\n".join(zeilen)


def ledger_pfad_fuer(repo: Path, pfad: Path | None = None) -> Path:
    """Standard-Speicherort des Quarantäne-Ledgers (oder expliziter Zielpfad)."""
    return pfad if pfad is not None else repo / protokoll.ORDNER / protokoll.QUARANTAENE_DATEI


def erkenne_wiederkehrende_wackler(
    funde: Sequence[wackler.WacklerFund],
    *,
    schwelle: int = STANDARD_SCHWELLE,
    fenster_tage: int | None = STANDARD_FENSTER_TAGE,
    jetzt: datetime | None = None,
) -> tuple[WacklerVorschlag, ...]:
    """Erkennt wiederkehrende Wackler ab ``schwelle`` Treffern im gewählten Zeitfenster."""
    if schwelle < 1:
        raise ValueError(f"schwelle muss >= 1 sein, erhalten: {schwelle}")
    if fenster_tage is not None and fenster_tage < 1:
        raise ValueError(f"fenster_tage muss >= 1 oder None sein, erhalten: {fenster_tage}")

    grenze = _jetzt_utc(jetzt) - timedelta(days=fenster_tage) if fenster_tage is not None else None
    gruppen: dict[str, list[wackler.WacklerFund]] = defaultdict(list)

    for fund in funde:
        test_id = wackler.normalisiere_test_id(fund.test)
        if not test_id:
            continue
        if grenze is not None:
            zeitpunkt = fund.zeitpunkt()
            if zeitpunkt is None or zeitpunkt < grenze:
                continue
        gruppen[test_id].append(fund)

    fenster_text = f"im {fenster_tage}-Tage-Fenster" if fenster_tage is not None else "über den gesamten Verlauf"
    vorschlaege: list[WacklerVorschlag] = []
    for test_id, liste in gruppen.items():
        if len(liste) < schwelle:
            continue
        adapter = next((f.adapter for f in reversed(liste) if f.adapter), "")
        erster = ""
        letzter = ""
        staende: list[str] = []
        gesehen_staende: set[str] = set()
        for eintrag in liste:
            if eintrag.zeit:
                erster = _frueher(erster, eintrag.zeit)
                letzter = _spaeter(letzter, eintrag.zeit)
            if eintrag.ziel and eintrag.ziel not in gesehen_staende:
                gesehen_staende.add(eintrag.ziel)
                staende.append(eintrag.ziel)
        grund = (
            f"Wiederkehrender Wackler ({len(liste)} Treffer >= Schwelle {schwelle} {fenster_text}) "
            "— Quarantäne vorgeschlagen, Test wird nicht gelöscht."
        )
        vorschlaege.append(
            WacklerVorschlag(
                test=test_id,
                adapter=adapter,
                treffer=len(liste),
                schwelle=schwelle,
                erster_fund=erster,
                letzter_fund=letzter,
                staende=tuple(staende),
                status=STATUS_VORGESCHLAGEN,
                aktion="quarantaene_vorschlagen",
                grund=grund,
            )
        )

    vorschlaege.sort(key=lambda eintrag: (-eintrag.treffer, eintrag.test))
    return tuple(vorschlaege)


def lade_ledger(repo: Path, *, pfad: Path | None = None) -> QuarantaeneLedger:
    """Liest das Quarantäne-Ledger; fehlende oder kaputte Datei liefert ein leeres Ledger."""
    ziel = ledger_pfad_fuer(repo, pfad)
    if not ziel.is_file():
        return QuarantaeneLedger()
    try:
        daten = json.loads(ziel.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        log.warning("Quarantäne-Ledger unlesbar (%s) — starte leer: %s", ziel, fehler)
        return QuarantaeneLedger()
    if not isinstance(daten, dict):
        log.warning("Quarantäne-Ledger ungültig (%s) — kein JSON-Objekt", ziel)
        return QuarantaeneLedger()
    roh_eintraege = daten.get("eintraege", [])
    eintraege: list[QuarantaeneEintrag] = []
    if isinstance(roh_eintraege, list):
        for roh in roh_eintraege:
            if not isinstance(roh, Mapping):
                continue
            geparst = QuarantaeneEintrag.aus_dict(roh)
            if geparst is not None:
                eintraege.append(geparst)
    eintraege.sort(key=lambda e: e.test)
    roh_schwelle = daten.get("schwelle", STANDARD_SCHWELLE)
    try:
        schwelle = max(1, int(roh_schwelle))
    except (TypeError, ValueError):
        schwelle = STANDARD_SCHWELLE
    roh_fenster = daten.get("fenster_tage", STANDARD_FENSTER_TAGE)
    if roh_fenster is None:
        fenster_tage: int | None = None
    else:
        try:
            fenster_tage = max(1, int(roh_fenster))
        except (TypeError, ValueError):
            fenster_tage = STANDARD_FENSTER_TAGE
    roh_version = daten.get("version", LEDGER_VERSION)
    try:
        version = int(roh_version)
    except (TypeError, ValueError):
        version = LEDGER_VERSION
    return QuarantaeneLedger(
        version=version,
        aktualisiert=str(daten.get("aktualisiert", "")).strip(),
        schwelle=schwelle,
        fenster_tage=fenster_tage,
        eintraege=tuple(eintraege),
    )


def schreibe_ledger(repo: Path, ledger: QuarantaeneLedger, *, pfad: Path | None = None) -> Path:
    """Schreibt das Quarantäne-Ledger atomar (UTF-8, LF) und schützt den Prüf-Ordner per ``.gitignore``."""
    ziel = ledger_pfad_fuer(repo, pfad)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    if pfad is None:
        ignorieren = (repo / protokoll.ORDNER) / ".gitignore"
        if not ignorieren.exists():
            ignorieren.write_text("*\n", encoding="utf-8", newline="\n")
    inhalt = json.dumps(ledger.als_dict(), ensure_ascii=False, indent=2) + "\n"
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=ziel.parent,
            prefix=f".{ziel.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.write(inhalt)
        os.replace(tmp_name, ziel)
        tmp_name = None
    finally:
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass
    return ziel


def aktualisiere_ledger(
    repo: Path,
    *,
    schwelle: int = STANDARD_SCHWELLE,
    fenster_tage: int | None = STANDARD_FENSTER_TAGE,
    jetzt: datetime | None = None,
    wackler_pfad: Path | None = None,
    ledger_pfad: Path | None = None,
    schreiben: bool = True,
) -> QuarantaeneAktualisierung:
    """Liest ``wackler.jsonl``, erkennt wiederkehrende Wackler und schreibt das Quarantäne-Ledger fort.

    Erzeugt ausschließlich Vorschläge bzw. Ledger-Einträge und verändert niemals Testdateien.
    """
    stempel = _iso_utc(_jetzt_utc(jetzt))
    ziel = ledger_pfad_fuer(repo, ledger_pfad)
    funde = wackler.lese_spur(repo, pfad=wackler_pfad)
    vorschlaege = erkenne_wiederkehrende_wackler(
        funde,
        schwelle=schwelle,
        fenster_tage=fenster_tage,
        jetzt=jetzt,
    )
    alt = lade_ledger(repo, pfad=ledger_pfad)
    bestand: dict[str, QuarantaeneEintrag] = {e.test: e for e in alt.eintraege}

    neu: list[str] = []
    bestehend: list[str] = []
    quelle = wackler_pfad.name if wackler_pfad is not None else protokoll.WACKLER_DATEI

    for vorschlag in vorschlaege:
        vorher = bestand.get(vorschlag.test)
        if vorher is None:
            neu.append(vorschlag.test)
            bestand[vorschlag.test] = QuarantaeneEintrag(
                test=vorschlag.test,
                adapter=vorschlag.adapter,
                status=STATUS_VORGESCHLAGEN,
                treffer=vorschlag.treffer,
                erster_fund=vorschlag.erster_fund,
                letzter_fund=vorschlag.letzter_fund,
                aktualisiert=stempel,
                grund=vorschlag.grund,
                quelle=quelle,
                staende=vorschlag.staende,
            )
            continue

        bestehend.append(vorschlag.test)
        if vorher.status == STATUS_AUFGEHOBEN and not _ist_neuer_als(vorschlag.letzter_fund, vorher.aktualisiert):
            # Bereits nach dem letzten Fund aufgehoben — nicht erneut vorschlagen.
            continue

        neuer_status = STATUS_QUARANTAENE if vorher.status == STATUS_QUARANTAENE else STATUS_VORGESCHLAGEN
        vereinte_staende = tuple(dict.fromkeys([*vorher.staende, *vorschlag.staende]))
        bestand[vorschlag.test] = QuarantaeneEintrag(
            test=vorschlag.test,
            adapter=vorschlag.adapter or vorher.adapter,
            status=neuer_status,
            treffer=vorschlag.treffer,
            erster_fund=_frueher(vorher.erster_fund, vorschlag.erster_fund),
            letzter_fund=_spaeter(vorher.letzter_fund, vorschlag.letzter_fund),
            aktualisiert=stempel,
            grund=vorher.grund if neuer_status == STATUS_QUARANTAENE and vorher.grund else vorschlag.grund,
            quelle=quelle,
            staende=vereinte_staende,
        )

    geordnet = tuple(sorted(bestand.values(), key=lambda e: e.test))
    neues_ledger = QuarantaeneLedger(
        version=LEDGER_VERSION,
        aktualisiert=stempel,
        schwelle=schwelle,
        fenster_tage=fenster_tage,
        eintraege=geordnet,
    )
    geschrieben = False
    if schreiben:
        schreibe_ledger(repo, neues_ledger, pfad=ledger_pfad)
        geschrieben = True

    return QuarantaeneAktualisierung(
        ledger=neues_ledger,
        vorschlaege=vorschlaege,
        neu=tuple(neu),
        bestehend=tuple(bestehend),
        gesamte_funde=len(funde),
        ledger_pfad=ziel,
        geschrieben=geschrieben,
    )


def _sammle_junit_dateien(quellen: Sequence[Path | str] | Path | str) -> tuple[list[Path], list[str]]:
    roh_liste: list[Path]
    if isinstance(quellen, (Path, str)):
        roh_liste = [Path(quellen)]
    else:
        roh_liste = [Path(q) for q in quellen]

    dateien: list[Path] = []
    fehler: list[str] = []
    gesehen: set[Path] = set()
    for quelle in roh_liste:
        if quelle.is_dir():
            for xml_datei in sorted(p for p in quelle.rglob("*.xml") if p.is_file()):
                aufgeloest = xml_datei.resolve()
                if aufgeloest not in gesehen:
                    gesehen.add(aufgeloest)
                    dateien.append(xml_datei)
        elif quelle.is_file():
            aufgeloest = quelle.resolve()
            if aufgeloest not in gesehen:
                gesehen.add(aufgeloest)
                dateien.append(quelle)
        else:
            meldung = f"JUnit-XML fehlt: {quelle.as_posix()}"
            log.warning("%s", meldung)
            fehler.append(meldung)
    return dateien, fehler


def _parse_dauer(roh: str | None) -> float:
    if not roh:
        return 0.0
    try:
        wert = float(roh)
    except ValueError:
        return 0.0
    if not math.isfinite(wert) or wert < 0.0:
        return 0.0
    return round(wert, 4)


def _testcase_status(fall: ET.Element) -> str:
    if fall.find("failure") is not None or fall.find("error") is not None:
        return "rot"
    if fall.find("skipped") is not None:
        return "uebersprungen"
    return "gruen"


def langsamste_tests(
    quellen: Sequence[Path | str] | Path | str,
    *,
    top_n: int = STANDARD_TOP_N,
    mindest_s: float = 0.0,
    ohne_uebersprungene: bool = False,
) -> LangsamsteBericht:
    """Ermittelt die Top-N langsamsten Tests aus einer oder mehreren JUnit-XML-Dateien bzw. Ordnern."""
    if top_n < 1:
        raise ValueError(f"top_n muss >= 1 sein, erhalten: {top_n}")
    if mindest_s < 0.0 or not math.isfinite(mindest_s):
        raise ValueError(f"mindest_s muss >= 0 sein, erhalten: {mindest_s}")

    xml_dateien, fehler = _sammle_junit_dateien(quellen)
    gelesen: list[str] = []
    alle: list[TestLaufzeit] = []
    gesamt_tests = 0
    gesamt_dauer = 0.0

    for pfad in xml_dateien:
        try:
            wurzel = ET.parse(pfad).getroot()
        except (OSError, ET.ParseError) as exc:
            meldung = f"JUnit-XML unlesbar ({pfad.as_posix()}): {exc}"
            log.warning("%s", meldung)
            fehler.append(meldung)
            continue
        if wurzel.tag not in ("testsuite", "testsuites"):
            meldung = f"Unbekanntes JUnit-Wurzelelement in {pfad.as_posix()}: {wurzel.tag}"
            log.warning("%s", meldung)
            fehler.append(meldung)
            continue

        quelle_text = pfad.as_posix()
        gelesen.append(quelle_text)
        for fall in wurzel.iter("testcase"):
            name = (fall.get("name") or "").strip()
            classname = (fall.get("classname") or "").strip()
            if not name and not classname:
                continue
            test_id = f"{classname}::{name}" if classname and name else (name or classname)
            dauer = _parse_dauer(fall.get("time"))
            status = _testcase_status(fall)
            datei = (fall.get("file") or "").strip().replace("\\", "/")
            gesamt_tests += 1
            gesamt_dauer += dauer
            if ohne_uebersprungene and status == "uebersprungen":
                continue
            if dauer < mindest_s:
                continue
            alle.append(
                TestLaufzeit(
                    test=test_id,
                    dauer_s=dauer,
                    status=status,
                    quelle=quelle_text,
                    datei=datei,
                    classname=classname,
                    name=name,
                )
            )

    alle.sort(key=lambda eintrag: (-eintrag.dauer_s, eintrag.test, eintrag.quelle))
    return LangsamsteBericht(
        top_n=top_n,
        eintraege=tuple(alle[:top_n]),
        gesamt_tests=gesamt_tests,
        gesamt_dauer_s=round(gesamt_dauer, 4),
        dateien=tuple(gelesen),
        fehler=tuple(fehler),
        mindest_s=mindest_s,
    )


def erstelle_bericht(
    repo: Path,
    *,
    junit_pfade: Sequence[Path | str] = (),
    schwelle: int = STANDARD_SCHWELLE,
    fenster_tage: int | None = STANDARD_FENSTER_TAGE,
    top_n: int = STANDARD_TOP_N,
    mindest_s: float = 0.0,
    ohne_uebersprungene: bool = False,
    wackler_pfad: Path | None = None,
    ledger_pfad: Path | None = None,
    schreiben: bool = True,
    jetzt: datetime | None = None,
) -> Stufe3Bericht:
    """Erstellt den gebündelten Stufe-3-Bericht (Quarantäne-Ledger + langsamste Tests)."""
    quarantaene_erg = aktualisiere_ledger(
        repo,
        schwelle=schwelle,
        fenster_tage=fenster_tage,
        jetzt=jetzt,
        wackler_pfad=wackler_pfad,
        ledger_pfad=ledger_pfad,
        schreiben=schreiben,
    )
    langsamste_erg = langsamste_tests(
        junit_pfade,
        top_n=top_n,
        mindest_s=mindest_s,
        ohne_uebersprungene=ohne_uebersprungene,
    )
    return Stufe3Bericht(
        quarantaene=quarantaene_erg,
        langsamste=langsamste_erg,
        nur_vorschlaege=True,
        geloeschte_dateien=(),
    )


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


def _schreibe_json_datei(ziel: Path | None, daten: dict[str, Any]) -> None:
    if ziel is None:
        return
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m to_spawn.pruef.quarantaene",
        description="Stufe 3: Wackler-Quarantäne-Ledger und Meldung langsamster Tests (erzeugt nur Vorschläge).",
    )
    unter = parser.add_subparsers(dest="befehl", required=True)

    pq = unter.add_parser("quarantaene", help="Wackler-Spur auswerten und Quarantäne-Ledger fortschreiben.")
    pq.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner")
    pq.add_argument("--wackler", type=Path, default=None, help="Abweichender Pfad zu wackler.jsonl")
    pq.add_argument("--ledger", type=Path, default=None, help="Abweichender Pfad zu quarantaene.json")
    pq.add_argument("--schwelle", type=int, default=STANDARD_SCHWELLE, help="Mindestzahl Wackler-Treffer")
    pq.add_argument(
        "--fenster-tage",
        type=int,
        default=STANDARD_FENSTER_TAGE,
        help="Zeitfenster in Tagen (0 = gesamter Verlauf)",
    )
    pq.add_argument("--nur-pruefen", action="store_true", help="Ledger nicht schreiben (Dry-Run)")
    pq.add_argument("--summary-pfad", default="", help="Pfad für Markdown-Zusammenfassung")
    pq.add_argument("--ausgabe-json", type=Path, default=None, help="Optionaler Dateipfad für JSON-Ausgabe")
    pq.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON auf stdout")

    pl = unter.add_parser("langsamste", help="Langsamste Tests (Top-N) aus JUnit-XML-Berichten melden.")
    pl.add_argument("--junit", type=Path, action="append", required=True, help="JUnit-XML-Datei oder Ordner")
    pl.add_argument("--top", type=int, default=STANDARD_TOP_N, help="Anzahl langsamster Tests")
    pl.add_argument("--mindest-s", type=float, default=0.0, help="Mindestlaufzeit in Sekunden")
    pl.add_argument("--ohne-uebersprungene", action="store_true", help="Übersprungene Tests ausblenden")
    pl.add_argument("--summary-pfad", default="", help="Pfad für Markdown-Zusammenfassung")
    pl.add_argument("--ausgabe-json", type=Path, default=None, help="Optionaler Dateipfad für JSON-Ausgabe")
    pl.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON auf stdout")

    pb = unter.add_parser("bericht", help="Gebündelten Stufe-3-Bericht (Quarantäne + langsamste Tests) erstellen.")
    pb.add_argument("--repo", type=Path, default=Path("."), help="Repo-Ordner")
    pb.add_argument("--junit", type=Path, action="append", default=[], help="JUnit-XML-Datei oder Ordner")
    pb.add_argument("--wackler", type=Path, default=None, help="Abweichender Pfad zu wackler.jsonl")
    pb.add_argument("--ledger", type=Path, default=None, help="Abweichender Pfad zu quarantaene.json")
    pb.add_argument("--schwelle", type=int, default=STANDARD_SCHWELLE, help="Mindestzahl Wackler-Treffer")
    pb.add_argument(
        "--fenster-tage",
        type=int,
        default=STANDARD_FENSTER_TAGE,
        help="Zeitfenster in Tagen (0 = gesamter Verlauf)",
    )
    pb.add_argument("--top", type=int, default=STANDARD_TOP_N, help="Anzahl langsamster Tests")
    pb.add_argument("--mindest-s", type=float, default=0.0, help="Mindestlaufzeit in Sekunden")
    pb.add_argument("--ohne-uebersprungene", action="store_true", help="Übersprungene Tests ausblenden")
    pb.add_argument("--nur-pruefen", action="store_true", help="Ledger nicht schreiben (Dry-Run)")
    pb.add_argument("--summary-pfad", default="", help="Pfad für Markdown-Zusammenfassung")
    pb.add_argument("--ausgabe-json", type=Path, default=None, help="Optionaler Dateipfad für JSON-Ausgabe")
    pb.add_argument("--json", dest="als_json", action="store_true", help="Ergebnis als JSON auf stdout")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-Einstiegspunkt für lokale Nutzung und GitHub Actions (Linux + Windows)."""
    konsole.utf8_ausgabe()
    args = _parser().parse_args(argv)

    try:
        if args.befehl == "quarantaene":
            fenster = None if args.fenster_tage == 0 else args.fenster_tage
            erg_q = aktualisiere_ledger(
                args.repo,
                schwelle=args.schwelle,
                fenster_tage=fenster,
                wackler_pfad=args.wackler,
                ledger_pfad=args.ledger,
                schreiben=not args.nur_pruefen,
            )
            daten_q = erg_q.als_dict()
            _schreibe_json_datei(args.ausgabe_json, daten_q)
            _schreibe_summary(args.summary_pfad, erg_q.kurzfassung())
            if args.als_json:
                sys.stdout.write(json.dumps(daten_q, ensure_ascii=False) + "\n")
            else:
                sys.stdout.write(erg_q.kurzfassung() + "\n")
            return 0

        if args.befehl == "langsamste":
            erg_l = langsamste_tests(
                args.junit,
                top_n=args.top,
                mindest_s=args.mindest_s,
                ohne_uebersprungene=args.ohne_uebersprungene,
            )
            daten_l = erg_l.als_dict()
            _schreibe_json_datei(args.ausgabe_json, daten_l)
            _schreibe_summary(args.summary_pfad, erg_l.kurzfassung())
            if args.als_json:
                sys.stdout.write(json.dumps(daten_l, ensure_ascii=False) + "\n")
            else:
                sys.stdout.write(erg_l.kurzfassung() + "\n")
            return 2 if erg_l.fehler and not erg_l.dateien else 0

        fenster = None if args.fenster_tage == 0 else args.fenster_tage
        erg_b = erstelle_bericht(
            args.repo,
            junit_pfade=args.junit,
            schwelle=args.schwelle,
            fenster_tage=fenster,
            top_n=args.top,
            mindest_s=args.mindest_s,
            ohne_uebersprungene=args.ohne_uebersprungene,
            wackler_pfad=args.wackler,
            ledger_pfad=args.ledger,
            schreiben=not args.nur_pruefen,
        )
        daten_b = erg_b.als_dict()
        _schreibe_json_datei(args.ausgabe_json, daten_b)
        _schreibe_summary(args.summary_pfad, erg_b.kurzfassung())
        if args.als_json:
            sys.stdout.write(json.dumps(daten_b, ensure_ascii=False) + "\n")
        else:
            sys.stdout.write(erg_b.kurzfassung() + "\n")
        return 2 if args.junit and erg_b.langsamste.fehler and not erg_b.langsamste.dateien else 0
    except ValueError as fehler:
        sys.stderr.write(f"Ungültige Eingabe: {fehler}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
