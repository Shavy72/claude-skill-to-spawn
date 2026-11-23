"""Belegseiten eines Tickets lesen: die eine Stelle, die weiß, was ein Beleg aussagt (#282 P1).

Belege liegen unter dem Beleg-Ordner des Repos (Vorgabe ``docs/verify-hard/``) als Datei
``<N>…`` oder Ordner ``[<datum>_]<N>…/`` mit ``.md``/``.txt``. :func:`auswerten` liefert je
Ticket, was darin steht:

- **Rot-Beweis** — eine Datei, deren Name ``rot`` enthält (gewolltes Scheitern vor dem Fix);
  sie zählt nie als Testlauf.
- **Testlauf** — größte ``N passed``/``N failed``-Zählung aus den übrigen Dateien
  (Fallback: saubere Markdown-Zeilen bzw. ``X/Y``-Zählung).
- **Klickweg** — eine Markdown-Zeile mit „Klickweg“/„Live-Klick“/„Live-Beweis“, die nicht
  verneint ist („kein Klickweg“, „entfällt“ zählen nicht).
- **Status** ``gruen``/``rot``/``offen`` aus Testlauf und ``ABNAHME:``-Zeile.

Nutzer: ``skripte/test_uebersicht.py`` (Anzeige) und :mod:`to_spawn.wiederanlauf` (was fehlt
beim erneuten Start). Neue Erkennungsregeln gehören nur hierher.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("belege")

MAX_BYTES = 2_000_000
_ROT_DATEI = re.compile(r"(?:^|[_\-/.])rot(?:[_\-/.]|$)", re.I)
_PASSED = re.compile(r"\b(\d+) passed\b")
_FAILED = re.compile(r"\b(\d+) (?:failed|errors?)\b")
_BEFEHL = re.compile(r"(?<![\w./])(?:python3? -m pytest|pytest|node --test|npm (?:run )?test) [^\n`|]*")
_ABNAHME = re.compile(r"ABNAHME[^:\n]*:\s*\**\s*([^\n*|]+)")
# Abnahme-Stichwörter: Wortanfang fest, Endung frei (grün/grüne/grünen, belegt/belegte), aber
# "unbelegt" trifft nicht. ROT nicht vor Bindestrich ("Rot-Beweis" ist Beleg, kein Urteil).
# OFFEN gilt als schlecht, außer verneint ("0 offen", "nichts mehr offen", "keine Punkte offen":
# Verneinung direkt vor OFFEN, höchstens 1 Wort, keine Satzzeichen) - die Verneinung wird vor der Suche entfernt.
_ABNAHME_GUT = re.compile(r"\b(?:ABGENOMMEN|BESTANDEN|GRÜN|BELEGT|FREIGEGEBEN)(?:e|en|er|es|em)?\b", re.I)
_ABNAHME_SCHLECHT = re.compile(r"\b(?:ABGELEHNT|ROT\b(?!-)|NICHT|FEHLT(?:EN|T)?)\b|\bOFFEN\b", re.I)
_OFFEN_VERNEINT = re.compile(r"\b(?:0|null|nichts(?: mehr)?|keine(?: \w+)?|nix)\s+OFFEN\b", re.I)
_XY = re.compile(r"\b(\d+)\s*/\s*(\d+)\s+(?:Tests?|grün|bestanden|belegt|Kriterien|Abnahme)", re.I)
_KLICKWEG = re.compile(r"klick.?weg|live-klick|live-beweis", re.I)
_VERNEINT = re.compile(r"\b(?:kein|keine|keiner|nicht|entfällt|n/a)\b", re.I)


@dataclass
class Lauf:
    """Ein Testlauf: Zählung + Beleg (Befehl, Ausgabezeile, Datei)."""

    passed: int
    failed: int
    zeile: str
    befehl: str
    datei: str


@dataclass
class Belege:
    """Was die Belegseiten eines Tickets aussagen (Felder s. Modul-Doku)."""

    status: str = "offen"  # "gruen" | "rot" | "offen"
    lauf: Lauf | None = None
    abnahme: str = ""
    klickweg: bool = False
    rot_beweis: bool = False
    dateien: list[str] = field(default_factory=list)


def _lies(datei: Path) -> str:
    try:
        if datei.stat().st_size > MAX_BYTES:
            return ""
        return datei.read_text(encoding="utf-8", errors="replace")
    except OSError as fehler:
        log.warning("Belegdatei nicht lesbar %s: %s", datei, fehler)
        return ""


def finde_dateien(verify_hard: Path, nummer: str) -> list[Path]:
    """Alle .md/.txt-Belegdateien eines Tickets (Datei ``<N>…`` oder Ordner ``[<datum>_]<N>…``)."""
    muster = re.compile(rf"^(?:\d{{4}}-\d{{2}}-\d{{2}}_)?{re.escape(nummer)}(?=$|[_.\-])")
    treffer: list[Path] = []
    if not verify_hard.is_dir():
        return treffer
    for eintrag in sorted(verify_hard.iterdir()):
        if not muster.match(eintrag.name):
            continue
        kandidaten = sorted(eintrag.rglob("*")) if eintrag.is_dir() else [eintrag]
        treffer += [k for k in kandidaten if k.is_file() and k.suffix.lower() in (".md", ".txt")]
    return treffer


def lauf_aus_text(text: str, datei: str, nur_saubere_zeilen: bool = False) -> Lauf | None:
    """Letzte Zusammenfassungs-Zeile (``N passed``/``N failed``) eines Textes als Lauf.

    ``nur_saubere_zeilen`` (Markdown): nur Zeilen ohne ``failed``/``rot``/``→`` — dort stehen
    oft Vorher-Nachher-Übergänge, die nichts über den Endstand sagen; dann zählt der größte Lauf.
    """
    befehl_m = _BEFEHL.search(text)
    befehl = befehl_m.group(0).strip()[:200] if befehl_m else ""
    beste: Lauf | None = None
    for zeile in text.splitlines():
        p = _PASSED.search(zeile)
        f = _FAILED.search(zeile)
        if not p and not f:
            continue
        if nur_saubere_zeilen and (f or "→" in zeile or re.search(r"\brot\b", zeile, re.I)):
            continue
        lauf = Lauf(
            passed=int(p.group(1)) if p else 0,
            failed=sum(int(x) for x in _FAILED.findall(zeile)),
            zeile=zeile.strip(" =|\t")[:200],
            befehl=befehl,
            datei=datei,
        )
        if not nur_saubere_zeilen or beste is None or lauf.passed >= beste.passed:
            beste = lauf
    return beste


def auswerten(verify_hard: Path, nummer: str) -> Belege:
    """Belege eines Tickets aus seinen Belegdateien; ohne Dateien alles leer, Status ``offen``."""
    belege = Belege()
    laeufe: list[Lauf] = []
    for datei in finde_dateien(verify_hard, nummer):
        rel = datei.relative_to(verify_hard).as_posix()
        belege.dateien.append(rel)
        text = _lies(datei)
        if _ROT_DATEI.search(rel):
            belege.rot_beweis = True
            continue
        if datei.suffix.lower() == ".txt":
            lauf = lauf_aus_text(text, rel)
        else:
            lauf = None
            if not belege.abnahme and (m := _ABNAHME.search(text)):
                belege.abnahme = m.group(1).strip()
            for zeile in text.splitlines():
                if _KLICKWEG.search(zeile) and not _VERNEINT.search(zeile):
                    belege.klickweg = True
                    break
        if lauf:
            laeufe.append(lauf)
    if not laeufe:  # Fallback: Zählung aus den Markdown-Belegseiten
        for rel in belege.dateien:
            if rel.lower().endswith(".md") and not _ROT_DATEI.search(rel):
                text = _lies(verify_hard / rel)
                lauf = lauf_aus_text(text, rel, nur_saubere_zeilen=True)
                if not lauf and (xy := _XY.search(text)):
                    x, y = int(xy.group(1)), int(xy.group(2))
                    if 0 < y and x <= y:
                        lauf = Lauf(x, y - x, xy.group(0), "", rel)
                if lauf:
                    laeufe.append(lauf)
    if laeufe:
        belege.lauf = max(laeufe, key=lambda l: (l.passed + l.failed, l.datei))
    abnahme_schlecht = bool(belege.abnahme) and bool(_ABNAHME_SCHLECHT.search(_OFFEN_VERNEINT.sub("", belege.abnahme)))
    abnahme_gut = bool(belege.abnahme) and bool(_ABNAHME_GUT.search(belege.abnahme))
    if (belege.lauf and belege.lauf.failed > 0) or abnahme_schlecht:
        belege.status = "rot"
    elif belege.lauf or abnahme_gut:
        belege.status = "gruen"
    return belege
