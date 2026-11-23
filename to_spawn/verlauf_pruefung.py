"""Was hat seit der letzten weitergegebenen Frage kein Mensch geprüft? (#372, PR 374 Runde 7)

Eine Fenster-Frage ist Klasse 2, wenn sie selbst ein Klasse-2-Wort trägt
(:func:`befugnis.ist_klasse_2`) oder die Session seit der Stelle, die der Leiter zuletzt
weitergegeben hat, eines geschrieben hat. Quelle ist das Transkript der Session, nicht der
Fenster-Text: wer etwas ins Fenster getippt hat (Mensch, Aufpasser, Aufseher, Entscheider),
spielt keine Rolle — Eingaben sind nie eine Grenze und werden nicht durchsucht.

Durchsucht werden nur Einträge ``type=assistant`` ohne ``isSidechain``/``isApiErrorMessage``,
je Block ``text`` und die Eingaben von ``tool_use``; nie ``user`` (Eingaben, Anstupser,
``tool_result``) und nie ``thinking``. Die Stelle (``<sessionId>:<uuid>``) gehört dem Leiter;
fremde sessionId (Respawn, ``/clear``) oder unbekannte uuid ⇒ ab Dateianfang (sichere Richtung).

Rückfall, wenn das Transkript nicht lesbar ist (keine Gesprächs-ID, Datei fehlt, keine Zeile
lesbar, letzte Zeile halb geschrieben): Fenster mit Verlauf (``capture-pane -J -S -2000``),
jede Klasse-2-Zeile darin zählt, ohne Grenze. Im Zweifel geht die Frage zum Menschen.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from to_spawn import aufpasser, befugnis, waechter_lauf

if TYPE_CHECKING:
    from to_spawn.aufseher_stand import Quellen

log = logging.getLogger("to_spawn.verlauf_pruefung")

#: ``bis`` im Rückfall: keine Stelle im Transkript, der nächste Lauf prüft wieder alles.
RUECKFALL = "fenster"
#: So weit liest der Rückfall zurück (tmux ``history-limit`` steht auf dem Bau-Server höher).
VERLAUF_ZEILEN = 2000
#: Grund, wenn weder Transkript noch Verlauf lesbar sind — trägt selbst ein Klasse-2-Wort.
UNGEPRUEFT = "Verlauf nicht lesbar — Live-Bezug ungeprüft"


@dataclass(frozen=True)
class Pruefung:
    """``grund``: erste Klasse-2-Zeile seit der Stelle (``None`` = keine); ``bis``: neue Stelle."""

    grund: str | None
    bis: str


def pruefen(ziel: str, q: Quellen, stelle: str | None) -> Pruefung:
    """Klasse-2-Grund der Session im Fenster ``ziel`` seit ``stelle`` (siehe Modul-Doc)."""
    gefunden = _transkript(ziel, q)
    eintraege = _lies(gefunden[1]) if gefunden else None
    if gefunden is None or eintraege is None:
        return _rueckfall(ziel, q)
    sid = gefunden[0]
    sid_alt, _, uuid_alt = (stelle or "").partition(":")
    start = 0
    if sid_alt == sid and uuid_alt:
        start = next(
            (i + 1 for i, e in enumerate(eintraege) if e.get("uuid") == uuid_alt), 0
        )
    eigene = [e for e in eintraege if _eigene_antwort(e)]
    bis = f"{sid}:{eigene[-1].get('uuid') or ''}" if eigene else f"{sid}:"
    for e in eintraege[start:]:
        if _eigene_antwort(e) and (
            grund := _erste_zeile(waechter_lauf._texte(e, werkzeug=True))
        ):
            return Pruefung(grund, bis)
    return Pruefung(None, bis)


def _transkript(ziel: str, q: Quellen) -> tuple[str, Path] | None:
    """(sessionId, Transkript-Datei) der Session im Pane ``ziel``; ``None`` = nicht zu finden."""
    raus = q.tmux(
        ["display-message", "-p", "-t", ziel, "#{pane_pid} #{window_id} #{pane_id}"]
    )
    teile = (raus or "").split()
    if len(teile) != 3 or not teile[0].isdigit():
        return None
    try:
        info = aufpasser.session_finden(int(teile[0]), teile[1], teile[2])
    except (OSError, ValueError) as fehler:
        log.warning("Session in %s nicht lesbar: %s", ziel, fehler)
        return None
    if info is None or info.cwd is None:
        return None
    return info.session_id, waechter_lauf.transkript_ordner(
        info.cwd
    ) / f"{info.session_id}.jsonl"


def _lies(datei: Path) -> list[dict[str, Any]] | None:
    """Einträge der JSONL; kaputte Zeilen fallen weg. ``None``: Datei fehlt, keine Zeile lesbar
    oder die letzte Zeile ist halb geschrieben (dann könnte gerade ihr Text fehlen)."""
    try:
        roh = datei.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    eintraege: list[dict[str, Any]] = []
    zeilen = [z for z in roh.splitlines() if z.strip()]
    for nr, zeile in enumerate(zeilen):
        try:
            e = json.loads(zeile)
        except json.JSONDecodeError:
            if nr == len(zeilen) - 1:
                return None
            continue
        if isinstance(e, dict):
            eintraege.append(e)
    return eintraege or None


def _eigene_antwort(e: dict[str, Any]) -> bool:
    return (
        e.get("type") == "assistant"
        and not e.get("isSidechain")
        and not e.get("isApiErrorMessage")
    )


def _erste_zeile(text: str) -> str | None:
    """Erste Klasse-2-Zeile; steht das Wort über Zeilen verteilt, der ganze Text."""
    if not befugnis.ist_klasse_2(text):
        return None
    zeile = next((z for z in text.splitlines() if befugnis.ist_klasse_2(z)), text)
    return " ".join(zeile.split())


def _rueckfall(ziel: str, q: Quellen) -> Pruefung:
    log.warning(
        "Transkript von %s nicht lesbar — prüfe den ganzen Fenster-Verlauf", ziel
    )
    text = q.tmux(["capture-pane", "-p", "-J", "-S", f"-{VERLAUF_ZEILEN}", "-t", ziel])
    if text is None:
        return Pruefung(UNGEPRUEFT, RUECKFALL)
    return Pruefung(_erste_zeile(aufpasser.ohne_statuszeile(text)), RUECKFALL)


assert befugnis.ist_klasse_2(UNGEPRUEFT), "UNGEPRUEFT muss Klasse 2 tragen"
