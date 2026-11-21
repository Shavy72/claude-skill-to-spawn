"""Speicher-Schutz (#257 Paket B): darf jetzt noch eine Claude-Session starten?

Hintergrund: Am 21.09. starb der Bau-Server im OOM-Kill (16 GB RAM, kein Swap,
12 Claude-Sessions + Gate). Seitdem prüft jeder Starter (``bau``, ``wache``,
``spawn_srv.sh``, Aufpasser) vor dem Claude-Start diese zwei Grenzen aus der
Repo-Konfig (``DEFAULTS["speicher"]``):

* ``min_frei_mib`` — so viel MiB müssen laut ``/proc/meminfo`` (``MemAvailable``)
  noch frei sein;
* ``max_sessions`` — höchstens so viele Prozesse mit ``argv[0]``-Basename ``claude``
  dürfen schon laufen (Richtwert 6 je 16 GB).

Nur Standardbibliothek (plus ``to_spawn.config``), nur Linux: auf anderen Systemen gibt es kein Urteil,
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

from to_spawn import config

log = logging.getLogger("to_spawn.speicher")

#: Vorgabe, falls die Konfig den Block ``speicher`` nicht kennt — eine Quelle: ``config.DEFAULTS``.
VORGABE: dict[str, int] = dict(config.DEFAULTS["speicher"])

#: Exit-Code der CLI (``to_spawn.py speicher``) und der Starter, wenn kein Platz ist.
EXIT_VOLL = 5
#: Exit-Code der CLI, wenn der Hausmeister die Platte zu voll meldet (#1369).
EXIT_PLATTE = 7
#: Takt von :func:`auf_platte_warten` — der Hausmeister-Lauf fragt gh, also selten.
PLATTE_TAKT_S = 900

#: Kernstück der Wartezeilen von :func:`auf_platz_warten` (``<wer> wartet auf Speicher …``)
#: und :func:`auf_platte_warten` (``<wer> wartet auf Speicher auf der Platte …``).
#: respawn erkennt daran auf dem Bildschirm, dass bau.py noch vor dem Claude-Start wartet.
WARTE_TEXT = "wartet auf Speicher"


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

    ``frei``/``sessions`` = Messwerte für Tests; ohne Angabe wird gemessen. Vorgegebene
    Werte (Parameter oder ``TO_SPAWN_SPEICHER_MEMINFO``/``TO_SPAWN_SPEICHER_PROC``)
    gelten auf jeder Plattform; selbst gemessen wird nur unter Linux. Kein Wert →
    kein Urteil, also frei (lieber starten als grundlos blockieren). Der Satz ist für
    Menschen: „Speicher knapp: 1,2 GB frei, Mindestmaß 2 GB“ oder „schon 6
    Claude-Sessions (Obergrenze 6)“.
    """
    linux = sys.platform == "linux"
    werte = grenzen(konfig)
    if frei is None and (linux or os.environ.get("TO_SPAWN_SPEICHER_MEMINFO")):
        frei = frei_mib()
    if sessions is None and (linux or os.environ.get("TO_SPAWN_SPEICHER_PROC")):
        sessions = claude_sessions()
    if frei is None and sessions is None:
        grund = "/proc nicht lesbar" if linux else "kein Linux"
        return True, f"Speicher: kein Urteil ({grund}) — Start erlaubt"
    if frei is not None and frei < werte["min_frei_mib"]:
        return False, (f"Speicher knapp: {_gb_text(frei)} frei, Mindestmaß {_gb_text(werte['min_frei_mib'])}")
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

    Jeder Zyklus prüft mit den Grenzen, die gerade in ``.to-spawn/config.json`` stehen
    (:func:`config.frisch`): wer ``max_sessions``/``min_frei_mib`` ändert, erreicht auch
    schon wartende Fenster — ohne Neustart (Vorfall 05.10.). Kaputte Datei mitten im
    Warten → die letzten gültigen Werte gelten weiter.
    """
    zyklen = 0
    while True:
        konfig = config.frisch(konfig)
        frei, grund = pruefen(konfig)
        if frei:
            if zyklen:
                text = f"{wer}: Speicher wieder frei nach {zyklen} Wartezyklen — {grund}"
                print(text, file=sys.stderr)
                log.info("%s", text)
            return zyklen
        zyklen += 1
        if zyklen == 1 or zyklen % meldung_alle == 0:
            text = f"{wer} {WARTE_TEXT} (Zyklus {zyklen}, Takt {takt_s} s): {grund}"
            print(text, file=sys.stderr)
            log.info("%s", text)
        schlafen(takt_s)


def _platte_pruefen(loeschen: bool = True) -> tuple[bool, str]:
    """Start-Check der Platte — die Entscheidung trifft der Hausmeister (#1369).

    ``loeschen=False``: nur messen (Probelauf, Status), nie aufräumen.
    """
    from to_spawn import hausmeister  # spät: hausmeister → aufpasser → speicher

    return hausmeister.start_erlaubt(loeschen=loeschen)


def _platte_knapp() -> bool:
    """Nur messen: herrscht Platznot (der Start-Check räumt dann lange auf)?

    Messung unmöglich oder Fehler ⇒ ``True`` (lieber eine Warte-Zeile zu viel)."""
    from to_spawn import hausmeister  # spät: hausmeister → aufpasser → speicher

    try:
        anteil = hausmeister._frei_anteil()
    except Exception:  # Messfehler: wie Platznot behandeln, Start-Check entscheidet
        log.exception("Platte: Vorab-Messung gescheitert")
        return True
    return anteil is None or hausmeister._ist_platznot(anteil)


def auf_platte_warten(
    *,
    wer: str = "Session",
    pruefen: Callable[[], tuple[bool, str]] = _platte_pruefen,
    schlafen: Callable[[float], object] = time.sleep,
    takt_s: int = PLATTE_TAKT_S,
    knapp: Callable[[], bool] | None = None,
) -> int:
    """Wartet, bis der Hausmeister die Platte für einen Start frei gibt; Rückgabe = Wartezyklen.

    Wie :func:`auf_platz_warten`: das Fenster bleibt offen, der Grund steht auf stderr
    und im Log. ``pruefen``/``schlafen``/``knapp`` sind Test-Türen. Bei genug Platz
    bleibt alles still — „wartet“ erscheint nur, wenn wirklich gewartet wird.
    ``knapp=None``: mit dem echten Start-Check misst :func:`_platte_knapp`; mit einer
    eingesetzten Prüf-Tür gilt Platznot (die Tür spielt den langen Hausmeister-Lauf).
    """
    if knapp is None:
        knapp = _platte_knapp if pruefen is _platte_pruefen else (lambda: True)
    # Bei Platznot schon vor der ersten Prüfung eine Zeile mit WARTE_TEXT: der
    # Hausmeister räumt darin synchron auf (gh/git, Minuten) — respawn erkennt daran
    # das Warten. Ohne Platznot ist die Prüfung kurz, eine Vorab-Zeile wäre falsch.
    if knapp():
        vorab = f"{wer} {WARTE_TEXT} auf der Platte: Hausmeister prüft"
        print(vorab, file=sys.stderr)
        log.info("%s", vorab)
    zyklen = 0
    while True:
        frei, grund = pruefen()
        if frei:
            if zyklen:
                text = f"{wer}: Speicher auf der Platte wieder frei nach {zyklen} Wartezyklen — {grund}"
                print(text, file=sys.stderr)
                log.info("%s", text)
            return zyklen
        zyklen += 1
        text = f"{wer} {WARTE_TEXT} auf der Platte (Zyklus {zyklen}, Takt {takt_s} s): {grund}"
        print(text, file=sys.stderr)
        log.info("%s", text)
        schlafen(takt_s)


def cli(konfig: dict[str, Any] | None, nur_staffel: bool = False, trocken: bool = False) -> int:
    """Unterbefehl ``to_spawn.py speicher``: Stand drucken, Exit 0 = frei, 5 = voll,
    7 = Platte voll (Start-Check des Hausmeisters, vor der RAM-Prüfung).

    ``--staffel`` druckt nur die Sekundenzahl der Staffel (für Shell-Skripte).
    ``--trocken`` (Probelauf, Status): nur messen, der Hausmeister löscht nie.
    """
    if nur_staffel:
        print(staffel_s(konfig))
        return 0
    platte_ok, platte_grund = _platte_pruefen(loeschen=not trocken)
    if not platte_ok:
        print(platte_grund)
        return EXIT_PLATTE
    ok, grund = platz_frei(konfig)
    print(grund)
    return 0 if ok else EXIT_VOLL
