"""Lernschleife: erkannte Stillstände als ``vorfall``-Zeile und Katalog-Eintrag (#286).

Ein Vorfall hat vier Pflichtangaben — **Klasse, Symptom, Ursache, Lösung**. Wer einen
Stillstand erkennt (capo, Aufpasser, eine Session über ``to_spawn.py eintrag``),
schreibt ihn über :func:`schreibe` ins Bau-Log. ``capo … --katalog`` hängt die neuen
Vorfälle über :func:`in_katalog` als Zeile an den passenden Abschnitt von
``docs/agents/FEHLERKATALOG_spawn.md`` — bis dahin von Hand gepflegt und darum
sofort veraltet.

Nur anhängen, nie umschreiben: eine Vorfall-Zeile steht je Bau-Log genau einmal
(gleiche Klasse + Symptom + Ursache), und eine Katalog-Zeile kommt nur dazu, wenn
der Abschnitt Symptom und Ursache noch nicht kennt. So darf jeder Tick den Weg
wiederholen, ohne dass Log oder Katalog wachsen.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import bau_log

log = logging.getLogger("to_spawn.vorfall")

#: Zeilen-Typ im Bau-Log.
TYP = "vorfall"

#: Klasse → (Nummern-Buchstabe, Abschnitts-Präfix im Fehlerkatalog).
KLASSEN: dict[str, tuple[str, str]] = {
    "extern": ("E", "### 3.1"),
    "infra": ("I", "### 3.2"),
    "skill": ("S", "### 3.3"),
    "prozess": ("P", "### 3.4"),
    "mensch": ("M", "### 3.5"),
}

#: Vorgabe-Ort des Katalogs im Repo (Konfig: ``regularien.fehlerkatalog``).
KATALOG_PFAD = Path("docs") / "agents" / "FEHLERKATALOG_spawn.md"

#: Schutz-Spalte für alles, was diese Lernschleife selbst erkannt hat.
SCHUTZ_AUTOMAT = "🟢 capo (#286)"

#: Wer die Zeile geschrieben hat. Wichtig für capo: eine Vorfall-Zeile des Wächters
#: ist KEIN Lebenszeichen der Session — sonst tarnt der eigene Fund die tote Session
#: als lebendig (Regression beim Bau von #286).
QUELLEN = ("capo", "aufpasser", "session")
SESSION_QUELLE = "session"

#: Nummer einer Katalog-Zeile, auch mit Auszeichnung wie ``| **S1** |``.
_NUMMER = re.compile(r"^\|[\s*_`]*([A-Z])(\d+)[\s*_`]*\|")
_ZELLE_RAND = re.compile(r"\s+")


@dataclass
class KatalogErgebnis:
    """Was ein Katalog-Lauf getan hat — und warum er etwas NICHT getan hat.

    ``probleme`` ist der Unterschied zwischen „alles stand schon drin" und „die
    Datei fehlt / der Abschnitt fehlt / die Tabelle passt nicht". Ohne diese
    Unterscheidung sieht stilles Nichtstun aus wie erfolgreiches Lernen.
    """

    nummern: list[str] = field(default_factory=list)
    probleme: list[str] = field(default_factory=list)
    #: Datei fehlt ganz — kein Fehler, nur ein Repo ohne Katalog (Fremd-Repo, #257).
    ohne_katalog: bool = False


@dataclass(frozen=True)
class Vorfall:
    """Ein erkannter Stillstand — vier Pflichtangaben plus Herkunft."""

    klasse: str
    symptom: str
    ursache: str
    loesung: str
    schutz: str = SCHUTZ_AUTOMAT
    beispiel: str = ""
    regel: str = ""
    ticket: str = ""
    quelle: str = SESSION_QUELLE

    def schluessel(self) -> tuple[str, str, str]:
        """Was einen Vorfall doppelt macht: Klasse, Symptom, Ursache."""
        return (self.klasse, _norm(self.symptom), _norm(self.ursache))


def _norm(text: Any) -> str:
    return _ZELLE_RAND.sub(" ", str(text or "")).strip().lower()


def _zelle(text: Any) -> str:
    """Freitext tabellentauglich: eine Zeile, Pipes entschärft."""
    eine_zeile = _ZELLE_RAND.sub(" ", str(text or "").replace("|", "\\|")).strip()
    return eine_zeile or "—"


def pruefe(klasse: str, symptom: str, ursache: str, loesung: str) -> None:
    """Vier Pflichtangaben — ein halber Vorfall hilft niemandem."""
    if klasse not in KLASSEN:
        raise ValueError(
            f"Unbekannte Klasse {klasse!r} — erlaubt: {', '.join(sorted(KLASSEN))}"
        )
    fehlend = [
        name
        for name, wert in (
            ("symptom", symptom),
            ("ursache", ursache),
            ("loesung", loesung),
        )
        if not str(wert or "").strip()
    ]
    if fehlend:
        raise ValueError(f"Vorfall ohne {', '.join(fehlend)} — Angabe fehlt.")


def aus_zeilen(zeilen: list[dict[str, Any]]) -> list[Vorfall]:
    """Alle ``vorfall``-Zeilen eines Bau-Logs als :class:`Vorfall`."""
    funde: list[Vorfall] = []
    for z in zeilen:
        if z.get("typ") != TYP:
            continue
        if z.get("klasse") not in KLASSEN:
            log.warning(
                "Vorfall-Zeile mit unbekannter Klasse %r übersprungen (#%s).",
                z.get("klasse"),
                z.get("ticket"),
            )
            continue
        if not all(str(z.get(feld) or "").strip() for feld in ("symptom", "ursache", "loesung")):
            log.warning(
                "Vorfall-Zeile ohne Symptom/Ursache/Lösung übersprungen (#%s).",
                z.get("ticket"),
            )
            continue
        funde.append(
            Vorfall(
                klasse=str(z.get("klasse")),
                symptom=str(z.get("symptom") or ""),
                ursache=str(z.get("ursache") or ""),
                loesung=str(z.get("loesung") or ""),
                schutz=str(z.get("schutz") or SCHUTZ_AUTOMAT),
                beispiel=str(z.get("beispiel") or ""),
                regel=str(z.get("regel") or ""),
                ticket=str(z.get("ticket") or ""),
                quelle=str(z.get("quelle") or SESSION_QUELLE),
            )
        )
    return funde


def ist_waechter_zeile(zeile: dict[str, Any]) -> bool:
    """Vorfall-Zeile, die capo oder der Aufpasser geschrieben hat — kein Lebenszeichen.

    Ohne diese Unterscheidung hält der Wächter seinen eigenen Fund für eine frische
    Spur der Session und meldet die tote Session ab dem zweiten Tick nicht mehr.
    """
    return zeile.get("typ") == TYP and str(
        zeile.get("quelle") or SESSION_QUELLE
    ) in ("capo", "aufpasser")


def ohne_waechter_zeilen(zeilen: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nur Zeilen, die als Spur der Session zählen."""
    return [z for z in zeilen if not ist_waechter_zeile(z)]


def schon_im_log(repo: Path, ticket: str | int, vor: Vorfall) -> bool:
    """Steht derselbe Vorfall (Klasse/Symptom/Ursache) schon im Bau-Log des Tickets?"""
    try:
        zeilen = bau_log.lese(repo, ticket)
    except OSError as fehler:  # Log unlesbar: lieber einmal zu viel schreiben
        log.warning("Bau-Log #%s nicht lesbar (%s) — Vorfall wird geschrieben.", ticket, fehler)
        return False
    return any(alt.schluessel() == vor.schluessel() for alt in aus_zeilen(zeilen))


def schreibe(
    repo: Path,
    ticket: str | int,
    *,
    klasse: str,
    symptom: str,
    ursache: str,
    loesung: str,
    schutz: str = SCHUTZ_AUTOMAT,
    beispiel: str = "",
    regel: str = "",
    quelle: str = SESSION_QUELLE,
) -> dict[str, Any] | None:
    """Vorfall in die Laufdatei des Bau-Logs schreiben (``None`` = stand schon drin).

    Die Laufdatei ist unversioniert; der nächste ``eintrag``-Aufruf der Session
    überträgt die Zeile in die versionierte Datei (#204).
    """
    pruefe(klasse, symptom, ursache, loesung)
    vor = Vorfall(
        klasse=klasse,
        symptom=symptom.strip(),
        ursache=ursache.strip(),
        loesung=loesung.strip(),
        schutz=schutz,
        beispiel=beispiel,
        regel=regel,
        ticket=str(ticket),
        quelle=quelle,
    )
    if schon_im_log(repo, ticket, vor):
        log.info("Vorfall steht schon im Bau-Log #%s — nicht doppelt.", ticket)
        return None
    return bau_log.schreibe(
        repo,
        ticket,
        TYP,
        klasse=vor.klasse,
        symptom=vor.symptom,
        ursache=vor.ursache,
        loesung=vor.loesung,
        schutz=vor.schutz or None,
        beispiel=vor.beispiel or None,
        regel=vor.regel or None,
        quelle=vor.quelle,
    )


def eintrag_felder(
    klasse: str,
    symptom: str,
    ursache: str,
    loesung: str,
    schutz: str = "",
    beispiel: str = "",
) -> dict[str, str]:
    """Felder für den CLI-Befehl ``eintrag --typ vorfall`` (prüft die Pflichtangaben)."""
    pruefe(klasse, symptom, ursache, loesung)
    felder = {
        "klasse": klasse,
        "symptom": symptom.strip(),
        "ursache": ursache.strip(),
        "loesung": loesung.strip(),
    }
    if schutz.strip():
        felder["schutz"] = schutz.strip()
    if beispiel.strip():
        felder["beispiel"] = beispiel.strip()
    return felder


def katalog_pfad(repo: Path, konfig: dict[str, Any] | None = None) -> Path:
    """Ort des Fehlerkatalogs: Konfig ``regularien.fehlerkatalog``, sonst Vorgabe."""
    regularien = (konfig or {}).get("regularien")
    ort = str((regularien or {}).get("fehlerkatalog") or "") if isinstance(regularien, dict) else ""
    return repo / (ort or KATALOG_PFAD)


def _abschnitt_grenzen(zeilen: list[str], praefix: str) -> tuple[int, int] | None:
    """Zeilenbereich eines ``### 3.x``-Abschnitts (Kopf ausgenommen, Folge-Kopf exklusiv)."""
    start = next((i for i, z in enumerate(zeilen) if z.startswith(praefix)), None)
    if start is None:
        return None
    ende = len(zeilen)
    for i in range(start + 1, len(zeilen)):
        if zeilen[i].startswith("### ") or zeilen[i].startswith("## "):
            ende = i
            break
    return start + 1, ende


def _letzte_tabellenzeile(zeilen: list[str], von: int, bis: int) -> int:
    """Index hinter der letzten Tabellenzeile des Bereichs (dorthin kommt die neue)."""
    letzte = von
    for i in range(von, bis):
        if zeilen[i].lstrip().startswith("|"):
            letzte = i + 1
    return letzte


def _bekannt(zeilen: list[str], von: int, bis: int) -> set[tuple[str, str]]:
    """Symptom/Ursache-Paare, die im Abschnitt schon stehen."""
    paare: set[tuple[str, str]] = set()
    for i in range(von, bis):
        spalten = _spalten(zeilen[i])
        if len(spalten) >= 3 and _NUMMER.match(zeilen[i].strip()):
            # Vergleich gegen den Klartext: im Katalog steht ``\|``, im Vorfall ``|``.
            paare.add((_norm(spalten[1].replace("\\|", "|")), _norm(spalten[2].replace("\\|", "|"))))
    return paare


#: Platzhalter, damit ein entschärftes ``\|`` im Text die Spalten nicht verschiebt.
_PIPE_MARKE = "\x00pipe\x00"


def _spalten(zeile: str) -> list[str]:
    r"""Zellen einer Tabellenzeile — ``\|`` im Text bleibt Text, kein Trenner."""
    roh = zeile.strip()
    if not roh.startswith("|"):
        return []
    geschuetzt = roh.replace("\\|", _PIPE_MARKE)
    return [
        teil.strip().replace(_PIPE_MARKE, "\\|")
        for teil in geschuetzt.strip("|").split("|")
    ]


def _hoechste_nummer(zeilen: list[str], von: int, bis: int, buchstabe: str) -> int:
    hoch = 0
    for i in range(von, bis):
        treffer = _NUMMER.match(zeilen[i].strip())
        if treffer and treffer.group(1) == buchstabe:
            hoch = max(hoch, int(treffer.group(2)))
    return hoch


def _tabellenzeile(nummer: str, vor: Vorfall) -> str:
    felder = [
        nummer,
        _zelle(vor.symptom),
        _zelle(vor.ursache),
        _zelle(vor.loesung),
        _zelle(vor.schutz),
        _zelle(vor.beispiel),
    ]
    return "| " + " | ".join(felder) + " |"


def _hat_tabelle(zeilen: list[str], von: int, bis: int) -> bool:
    """Steht im Abschnitt überhaupt eine Tabelle? Sonst darf hier nichts eingefügt werden."""
    return any(zeilen[i].lstrip().startswith("|") for i in range(von, bis))


def _schreibe_atomar(pfad: Path, text: str) -> None:
    """Erst daneben schreiben, dann umbenennen — ein Absturz kürzt die Datei nie."""
    neben = pfad.with_name(pfad.name + ".neu")
    neben.write_text(text, encoding="utf-8")
    os.replace(neben, pfad)


def in_katalog(pfad: Path, vorfaelle: list[Vorfall]) -> KatalogErgebnis:
    """Neue Vorfälle als Tabellenzeile an ihren Abschnitt hängen.

    ``nummern`` = die vergebenen Nummern (``["S15", "P8"]``). ``probleme`` nennt
    jeden Vorfall, der NICHT abgelegt werden konnte (Abschnitt fehlt, Tabelle fehlt,
    Datei nicht schreibbar) — „alles stand schon drin" ist kein Problem, sondern der
    Normalfall. ``ohne_katalog`` heißt: das Repo führt gar keinen Katalog.
    """
    erg = KatalogErgebnis()
    if not vorfaelle:
        return erg
    if not pfad.is_file():
        erg.ohne_katalog = True
        log.info("Kein Fehlerkatalog unter %s — nichts eingetragen.", pfad)
        return erg
    try:
        text = pfad.read_text(encoding="utf-8")
    except OSError as fehler:
        erg.probleme.append(f"Datei nicht lesbar ({fehler})")
        return erg
    zeilen = text.splitlines()
    schluss = "\n" if text.endswith("\n") else ""
    for vor in vorfaelle:
        eintrag = KLASSEN.get(vor.klasse)
        if eintrag is None:
            erg.probleme.append(f"Klasse {vor.klasse!r} unbekannt — {vor.symptom}")
            continue
        buchstabe, praefix = eintrag
        grenzen = _abschnitt_grenzen(zeilen, praefix)
        if grenzen is None:
            erg.probleme.append(
                f"Abschnitt {praefix} fehlt im Katalog (Klasse {vor.klasse}) — {vor.symptom}"
            )
            continue
        von, bis = grenzen
        if not _hat_tabelle(zeilen, von, bis):
            erg.probleme.append(
                f"Abschnitt {praefix} hat keine Tabelle — {vor.symptom} nicht abgelegt"
            )
            continue
        if (_norm(vor.symptom), _norm(vor.ursache)) in _bekannt(zeilen, von, bis):
            log.info("Vorfall steht schon im Katalog-Abschnitt %s.", praefix)
            continue
        nummer = f"{buchstabe}{_hoechste_nummer(zeilen, von, bis, buchstabe) + 1}"
        zeilen.insert(_letzte_tabellenzeile(zeilen, von, bis), _tabellenzeile(nummer, vor))
        erg.nummern.append(nummer)
    if not erg.nummern:
        return erg
    try:
        _schreibe_atomar(pfad, "\n".join(zeilen) + schluss)
    except OSError as fehler:
        erg.probleme.append(f"Datei nicht schreibbar ({fehler})")
        erg.nummern.clear()
    return erg
