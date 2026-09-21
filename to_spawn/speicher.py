"""Speicher-Schutz (#257 Paket B): darf jetzt noch eine Claude-Session starten?

Hintergrund: Am 21.09. starb der Bau-Server im OOM-Kill (16 GB RAM, kein Swap,
12 Claude-Sessions + Gate). Seitdem prüft jeder Starter (``bau``, ``wache``,
``spawn_srv.sh``, Aufpasser) vor dem Claude-Start diese zwei Grenzen aus der
Repo-Konfig (``DEFAULTS["speicher"]``):

* ``min_frei_mib`` — so viel MiB müssen laut ``/proc/meminfo`` (``MemAvailable``)
  noch frei sein;
* ``max_sessions`` — höchstens so viele Prozesse mit ``argv[0]``-Basename ``claude``
  dürfen schon laufen (Richtwert 6 je 16 GB).

Nur Standardbibliothek, nur Linux: auf anderen Systemen gibt es kein Urteil,
also „frei“. Test-Tür: die Umgebungsvariablen ``TO_SPAWN_SPEICHER_MEMINFO``
(Pfad einer meminfo-Datei) und ``TO_SPAWN_SPEICHER_PROC`` (Ordner im Aufbau von
``/proc``) ersetzen den echten ``/proc`` — so bleibt das Ergebnis in Tests
unabhängig von den Sessions, die gerade auf der Testmaschine laufen.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger("to_spawn.speicher")

#: Vorgabe, falls die Konfig den Block ``speicher`` nicht kennt (gleich ``config.DEFAULTS``).
VORGABE: dict[str, int] = {"min_frei_mib": 2048, "max_sessions": 6, "staffel_s": 20}

#: Exit-Code der CLI (``to_spawn.py speicher``) und der Starter, wenn kein Platz ist.
EXIT_VOLL = 5


def _meminfo_pfad() -> Path:
    return Path(os.environ.get("TO_SPAWN_SPEICHER_MEMINFO") or "/proc/meminfo")


def _proc_pfad() -> Path:
    return Path(os.environ.get("TO_SPAWN_SPEICHER_PROC") or "/proc")


def frei_mib(meminfo: Path | None = None) -> int | None:
    """Freier Speicher in MiB (``MemAvailable`` aus ``/proc/meminfo``), ``None`` = unbekannt.

    ``meminfo`` = Datei im Aufbau von ``/proc/meminfo``; ohne Angabe gilt
    ``TO_SPAWN_SPEICHER_MEMINFO`` bzw. ``/proc/meminfo``.
    """
    datei = meminfo if meminfo is not None else _meminfo_pfad()
    try:
        text = datei.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for zeile in text.splitlines():
        if not zeile.startswith("MemAvailable:"):
            continue
        teile = zeile.split()
        if len(teile) < 2 or not teile[1].isdigit():
            return None
        return int(teile[1]) // 1024  # kB → MiB
    return None


def claude_sessions(proc: Path | None = None) -> int | None:
    """Zahl der laufenden Claude-Prozesse (``argv[0]``-Basename ``claude``), ``None`` = unbekannt.

    ``proc`` = Ordner im Aufbau von ``/proc`` (Unterordner je PID mit ``cmdline``);
    ohne Angabe gilt ``TO_SPAWN_SPEICHER_PROC`` bzw. ``/proc``.
    """
    ordner = proc if proc is not None else _proc_pfad()
    try:
        eintraege = list(ordner.iterdir())
    except OSError:
        return None
    anzahl = 0
    for eintrag in eintraege:
        if not eintrag.name.isdigit():
            continue
        try:
            roh = (eintrag / "cmdline").read_bytes()
        except OSError:
            continue  # Prozess schon weg oder nicht lesbar — zählt nicht
        teile = roh.split(b"\0")
        argv0 = teile[0].decode("utf-8", errors="replace")
        if Path(argv0).name != "claude":
            continue
        if b"-p" in teile[1:] or b"--print" in teile[1:]:
            continue  # Einmal-Helfer (``claude -p``) einer Session — keine eigene Sitzung
        anzahl += 1
    return anzahl


def _gb_text(mib: int) -> str:
    """1200 MiB → „1,2 GB“, 2048 MiB → „2 GB“ (eine Nachkommastelle, deutsches Komma)."""
    gb = round(mib / 1024, 1)
    if gb == int(gb):
        return f"{int(gb)} GB"
    return f"{gb:.1f}".replace(".", ",") + " GB"


def grenzen(konfig: dict[str, Any] | None) -> dict[str, int]:
    """Die drei Zahlen aus ``konfig["speicher"]``, fehlende aus der Vorgabe."""
    block = (konfig or {}).get("speicher") if isinstance(konfig, dict) else None
    werte = dict(VORGABE)
    if isinstance(block, dict):
        for name in VORGABE:
            wert = block.get(name)
            if isinstance(wert, (int, float)) and not isinstance(wert, bool) and wert >= 0:
                werte[name] = int(wert)
    return werte


def staffel_s(konfig: dict[str, Any] | None) -> int:
    """Pause in Sekunden zwischen zwei Fenster-Starts."""
    return grenzen(konfig)["staffel_s"]


def platz_frei(
    konfig: dict[str, Any] | None,
    frei: int | None = None,
    sessions: int | None = None,
) -> tuple[bool, str]:
    """(darf starten?, Satz dazu) — die Entscheidung aller Starter.

    ``frei``/``sessions`` = Messwerte für Tests; ohne Angabe wird gemessen. Kein
    Linux oder Messung unmöglich → kein Urteil, also frei (lieber starten als
    grundlos blockieren). Der Satz ist für Menschen: „Speicher knapp: 1,2 GB frei,
    Mindestmaß 2 GB“ oder „schon 6 Claude-Sessions (Obergrenze 6)“.
    """
    if sys.platform != "linux":
        return True, "Speicher: kein Urteil (kein Linux) — Start erlaubt"
    werte = grenzen(konfig)
    if frei is None:
        frei = frei_mib()
    if sessions is None:
        sessions = claude_sessions()
    if frei is None and sessions is None:
        return True, "Speicher: kein Urteil (/proc nicht lesbar) — Start erlaubt"
    if frei is not None and frei < werte["min_frei_mib"]:
        return False, (
            f"Speicher knapp: {_gb_text(frei)} frei, Mindestmaß {_gb_text(werte['min_frei_mib'])}"
        )
    if sessions is not None and sessions >= werte["max_sessions"]:
        return False, f"schon {sessions} Claude-Sessions (Obergrenze {werte['max_sessions']})"
    frei_text = _gb_text(frei) if frei is not None else "unbekannt"
    sessions_text = str(sessions) if sessions is not None else "unbekannt"
    return True, (
        f"Speicher frei: {frei_text} frei, {sessions_text} Claude-Sessions "
        f"(Obergrenze {werte['max_sessions']}, Mindestmaß {_gb_text(werte['min_frei_mib'])})"
    )


def auf_platz_warten(
    konfig: dict[str, Any] | None,
    *,
    wer: str = "Session",
    pruefen: Callable[[dict[str, Any] | None], tuple[bool, str]] = platz_frei,
    schlafen: Callable[[float], object] = time.sleep,
    takt_s: int = 60,
    meldung_alle: int = 5,
) -> int:
    """Wartet, bis :func:`platz_frei` „frei“ sagt; gibt die Zahl der Wartezyklen zurück (#257 F1).

    Früher brachen ``bau``/``wache`` bei vollem Speicher mit Exit 5 ab — das tmux-Fenster
    ging zu und der Grund war weg. Jetzt prüft die Schleife alle ``takt_s`` Sekunden neu
    und meldet den Grund im ersten und danach jedem ``meldung_alle``-ten Durchlauf auf
    stderr und im Log. ``pruefen``/``schlafen`` sind Test-Türen. Aufpasser und
    ``spawn_srv.sh`` behalten ihr Exit/Auslassen (sie starten viele Fenster).
    """
    zyklen = 0
    while True:
        frei, grund = pruefen(konfig)
        if frei:
            if zyklen:
                text = f"{wer}: Speicher wieder frei nach {zyklen} Wartezyklen — {grund}"
                print(text, file=sys.stderr)
                log.info("%s", text)
            return zyklen
        zyklen += 1
        if zyklen == 1 or zyklen % meldung_alle == 0:
            text = f"{wer} wartet auf Speicher (Zyklus {zyklen}, Takt {takt_s} s): {grund}"
            print(text, file=sys.stderr)
            log.info("%s", text)
        schlafen(takt_s)


def cli(konfig: dict[str, Any] | None, nur_staffel: bool = False) -> int:
    """Unterbefehl ``to_spawn.py speicher``: Stand drucken, Exit 0 = frei, 5 = voll.

    ``--staffel`` druckt nur die Sekundenzahl der Staffel (für Shell-Skripte).
    """
    if nur_staffel:
        print(staffel_s(konfig))
        return 0
    ok, grund = platz_frei(konfig)
    print(grund)
    return 0 if ok else EXIT_VOLL
