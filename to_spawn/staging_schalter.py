"""Staging-Schalter einer fertigen Spec automatisch AN schalten (#563, Spec #548 E3/E12).

Sobald der Aufseher „SPEC FERTIG“ meldet, soll David morgens die ganze Spec auf
Staging testen können. Dafür schaltet dieser Baustein den Hauptschalter der Spec
auf Staging AN. Live schaltet er nie: das Ziel ist fest :data:`ZIEL`, es gibt
keinen Parameter dafür (Live-Freigabe gibt nur David).

Welcher Schalter zur Spec gehört, steht im Spec-Text im Abschnitt
``## Hauptschalter``, Zeile „Name des Hauptschalters“ (Vorlage
``.claude/skills/to-spec/SKILL.md`` im App-Repo). Geschaltet wird über das
Schalter-Skript des App-Repos (``scripts/schalter.py``, Ticket #560).

Schnittstelle für capo: :func:`schalte_an` — liefert Zeilen für die Aufseher-Ausgabe
und ob der Fall erledigt ist (dann merkt capo sich das und fragt nie wieder).
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import gh

log = logging.getLogger(__name__)

#: Einziges Ziel dieses Bausteins. Live schaltet nur David von Hand.
ZIEL = "staging"
#: Schalter-Skript im App-Repo (Ticket #560).
SKRIPT = "scripts/schalter.py"
#: Wer im Schalter-Log als Umschalter steht.
WER = "to-spawn"
#: Obergrenze für einen Schalt-Aufruf (ssh/docker exec), danach FEHLER.
ZEITLIMIT_S = 60

#: Gültiger Schalter-Name, gleiche Regel wie ``web/services/feature_schalter.py``.
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9_-]+)*")
#: Erstes Backtick-Paar einer Zeile.
_BACKTICK = re.compile(r"`([^`]*)`")


class NameUnlesbar(ValueError):
    """Die Spec nennt einen Hauptschalter, der Name ist aber nicht gültig (z. B. Vorlagen-Platzhalter)."""


@dataclass
class Ergebnis:
    """Zeilen für die Aufseher-Ausgabe; ``erledigt`` = nie wieder versuchen."""

    zeilen: list[str]
    erledigt: bool


def hauptschalter(spec_text: str) -> str | None:
    """Name des Hauptschalters aus dem Spec-Text.

    ``None`` = Abschnitt fehlt oder „Kein Schalter nötig“. Steht die Zeile „Name des
    Hauptschalters“ da, zählt nur ihr erstes Backtick-Paar; ist es kein gültiger Name
    (Platzhalter ``<spec-kurzname>.an``), kommt :class:`NameUnlesbar` — nie ein
    anderer Name aus derselben Zeile (Vorbild ``story_macher.an``).
    """
    abschnitt = re.search(
        r"^##\s+Hauptschalter\s*$(.*?)(?=^##\s|\Z)", spec_text, re.MULTILINE | re.DOTALL
    )
    if not abschnitt:
        return None
    for zeile in abschnitt.group(1).splitlines():
        if "Name des Hauptschalters" in zeile:
            treffer = _BACKTICK.search(zeile)
            if treffer and _NAME.fullmatch(treffer.group(1)):
                return treffer.group(1)
            raise NameUnlesbar(zeile.strip()[:160])
    return None


def _spec_text(gh_repo: str, spec: int) -> str | None:
    """Text des Spec-Issues; ``None`` (mit Warnung im Log), wenn gh scheitert."""
    code, ausgabe = gh.lauf(["api", f"repos/{gh_repo}/issues/{spec}"])
    if code != 0:
        log.warning("Staging-Schalter: gh api issues/%s Exit %s", spec, code)
        return None
    try:
        daten = json.loads(ausgabe or "null")
    except json.JSONDecodeError as fehler:
        log.warning("Staging-Schalter: Antwort zu #%s kein JSON: %s", spec, fehler)
        return None
    if not isinstance(daten, dict):
        log.warning("Staging-Schalter: Antwort zu #%s ist kein Objekt", spec)
        return None
    return str(daten.get("body") or "")


def befehl(name: str, spec: int) -> list[str]:
    """Kommandozeile für das Schalter-Skript — Ziel immer :data:`ZIEL`."""
    grund = f"Spec #{spec} fertig — letztes Ticket grün, Staging automatisch AN (to-spawn #563)"
    return [
        sys.executable,
        SKRIPT,
        "an",
        name,
        "--grund",
        grund,
        "--ziel",
        ZIEL,
        "--wer",
        WER,
    ]


def schalte_an(repo: Path, gh_repo: str, spec: int, dry_run: bool) -> Ergebnis:
    """Staging-Hauptschalter der Spec AN; nichts tun (und das melden), wenn es keinen gibt.

    Fehlschlag (Spec-Text oder Name nicht lesbar, Schalter-Skript fehlt oder rot) =
    FEHLER-Zeile und ``erledigt=False``: der nächste Tick versucht es wieder.
    """
    text = _spec_text(gh_repo, spec)
    if text is None:
        return Ergebnis(
            [f"FEHLER: Staging-Schalter — Spec-Text #{spec} nicht lesbar (gh)."], False
        )
    try:
        name = hauptschalter(text)
    except NameUnlesbar as fehler:
        log.warning("Staging-Schalter: Name in Spec #%s unlesbar: %s", spec, fehler)
        return Ergebnis(
            [
                f"FEHLER: Staging-Schalter — Name des Hauptschalters in Spec #{spec} unlesbar: {fehler}"
            ],
            False,
        )
    if name is None:
        meldung = f"Staging-Schalter: Spec #{spec} hat kein Hauptschalter-Feld — nichts geschaltet (kein Hauptschalter)."
        log.info("%s", meldung)
        return Ergebnis([meldung], True)
    if not (repo / SKRIPT).is_file():
        log.warning("Staging-Schalter: %s fehlt in %s", SKRIPT, repo)
        return Ergebnis(
            [
                f"FEHLER: Staging-Schalter {name} — {SKRIPT} fehlt im Repo (Stand veraltet?), nächster Tick versucht es wieder."
            ],
            False,
        )
    argv = befehl(name, spec)
    if dry_run:
        return Ergebnis(
            [f"[Probe] Staging-Schalter {name} AN: {shlex.join(argv[1:])}"],
            False,
        )
    try:
        lauf = subprocess.run(
            argv,
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=ZEITLIMIT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("Staging-Schalter %s: Aufruf gescheitert: %s", name, fehler)
        return Ergebnis(
            [
                f"FEHLER: Staging-Schalter {name} nicht geschaltet ({fehler}) — nächster Tick versucht es wieder."
            ],
            False,
        )
    if lauf.returncode != 0:
        zeilen = (lauf.stderr or lauf.stdout).strip().splitlines()
        fehlzeile = zeilen[-1] if zeilen else "ohne Ausgabe"
        log.warning(
            "Staging-Schalter %s: Exit %s:\n%s",
            name,
            lauf.returncode,
            "\n".join(zeilen[-20:]),
        )
        return Ergebnis(
            [
                f"FEHLER: Staging-Schalter {name} nicht geschaltet (Exit {lauf.returncode}: {fehlzeile[:160]}) — nächster Tick versucht es wieder."
            ],
            False,
        )
    log.info("Staging-Schalter %s AN (Spec #%s fertig).", name, spec)
    return Ergebnis(
        [f"Staging-Schalter {name} AN (Spec #{spec} fertig, Live bleibt unverändert)."],
        True,
    )
