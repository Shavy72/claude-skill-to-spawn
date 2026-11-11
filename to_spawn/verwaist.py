"""Verwaiste Bau-Sessions erkennen + Schließ-Protokoll (#1184).

Eine Bau-Session ist verwaist, wenn ihr ``claude``-Prozess noch läuft, aber in
keinem tmux-Fenster mehr hängt (bau.py oder Fenster weg, Prozess an PID 1
übergeben). Sie belegt Speicher und zählt bei jeder Zulassung mit, ohne zu
arbeiten — der Aufpasser beendet sie.

Nur Bau-Sessions zählen: Erkennungszeichen ist ``--settings …-bau/<N>-…/settings.json``
(von ``skripte/bau.py`` gesetzt). Von Hand gestartete Sessions (ssh, Remote
Control) tragen das nie und bleiben unberührt. Wartende ``bau``-Fenster (auf
Blocker) haben noch gar keinen ``claude``-Prozess — sie sind nie verwaist.

Reine Funktionen über ``ps -eo pid=,ppid=,etimes=,args=`` und
``tmux list-panes -a -F '#{session_name}\\t#{window_id}\\t#{window_name}\\t#{pane_pid}'``,
damit Tests gegen echte Aufzeichnungen laufen.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("to_spawn.verwaist")

#: Erkennungszeichen einer von bau.py gestarteten Session (``<out>/<repo>-bau/<N>-<zeit>-<id>/settings.json``).
_BAU_SETTINGS = re.compile(r"--settings\s+\S*-bau/(\d+)[-/]")

#: Jünger als das gilt nie als verwaist — ein Start kann kurz zwischen Fenster und Prozess hängen.
MIN_ALTER_S = 600


@dataclass(frozen=True)
class Waise:
    pid: int
    ticket: int
    alter_s: int
    befehl: str


def _ps_zeilen(ps_text: str) -> dict[int, tuple[int, int, str]]:
    prozesse: dict[int, tuple[int, int, str]] = {}
    for zeile in ps_text.splitlines():
        teile = zeile.split(None, 3)
        if len(teile) < 4 or not (
            teile[0].isdigit() and teile[1].isdigit() and teile[2].isdigit()
        ):
            continue
        prozesse[int(teile[0])] = (int(teile[1]), int(teile[2]), teile[3].rstrip())
    return prozesse


def _pane_pids(panes_text: str) -> set[int]:
    pids: set[int] = set()
    for zeile in panes_text.splitlines():
        letzte = zeile.rsplit("\t", 1)[-1].strip()
        if letzte.isdigit():
            pids.add(int(letzte))
    return pids


def verwaiste_sessions(
    ps_text: str, panes_text: str, *, min_alter_s: int = MIN_ALTER_S
) -> list[Waise]:
    """Bau-Sessions (``claude`` mit bau-Settings, kein ``-p``), deren Vorfahren kein Pane enthalten.

    Leere Pane-Liste ⇒ leeres Ergebnis: ohne tmux-Bild lässt sich „verwaist“ nicht
    beweisen (tmux-Server weg oder nicht lesbar) — lieber nichts beenden.
    """
    panes = _pane_pids(panes_text)
    if not panes:
        return []
    prozesse = _ps_zeilen(ps_text)
    waisen: list[Waise] = []
    for pid, (_, alter, befehl) in prozesse.items():
        woerter = befehl.split()
        if not woerter or Path(woerter[0]).name != "claude":
            continue
        if "-p" in woerter[1:] or "--print" in woerter[1:]:
            continue
        treffer = _BAU_SETTINGS.search(befehl)
        if treffer is None or alter < min_alter_s:
            continue
        aktuell, gesehen = pid, set()
        while (
            aktuell not in panes
            and aktuell in prozesse
            and aktuell not in gesehen
            and aktuell > 1
        ):
            gesehen.add(aktuell)
            aktuell = prozesse[aktuell][0]
        if aktuell in panes:
            continue
        waisen.append(
            Waise(
                pid=pid,
                ticket=int(treffer.group(1)),
                alter_s=alter,
                befehl=befehl[:160],
            )
        )
    return waisen


def protokoll_pfad(jetzt: float | None = None) -> Path:
    """``~/geschlossen-<JJJJ-MM-TT>.txt`` (Test-Tür ``TO_SPAWN_GESCHLOSSEN_ORDNER``)."""
    ordner = Path(os.environ.get("TO_SPAWN_GESCHLOSSEN_ORDNER") or Path.home())
    datum = time.strftime("%Y-%m-%d", time.localtime(jetzt))
    return ordner / f"geschlossen-{datum}.txt"


def protokollieren(
    grund: str, wer: str, detail: str = "", jetzt: float | None = None
) -> None:
    """Eine Zeile ``<Zeit>\\t<Grund>\\t<wer>\\t<Detail>`` anhängen; Schreibfehler stoppen nie den Aufpasser."""
    zeit = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(jetzt))
    try:
        with protokoll_pfad(jetzt).open("a", encoding="utf-8") as datei:
            datei.write(f"{zeit}\t{grund}\t{wer}\t{detail}\n")
    except OSError as fehler:
        log.warning(
            "Schließ-Protokoll nicht schreibbar (%s): %s %s", fehler, grund, wer
        )
