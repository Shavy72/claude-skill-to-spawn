"""Aussetzerfeste SSH-Beobachtungen zum Bau-Server (#603).

Stehende Verbindungen (Warten auf ein Urteil, Status lesen) brechen bei einem kurzen
Netz-Aussetzer am PC mit SSH-Exit ``255`` (z. B. ``Broken pipe``, Namensauflösung weg)
oder einer Zeitüberschreitung ab. Dieses Modul verbindet automatisch neu statt die
Beobachtung zu beenden und schreibt bei jedem Aussetzer sowie nach geglückter
Wiederaufnahme eine klare Log-Zeile:

- :func:`ssh_befehl` ergänzt nicht-interaktive Keepalive-Optionen
  (``BatchMode=yes``, ``ConnectTimeout``, ``ServerAliveInterval``, ``ServerAliveCountMax``),
  ohne ausdrücklich übergebene Optionen doppelt zu setzen.
- :func:`beobachten` führt einen stehenden Fern-Befehl oder eine Takt-Schleife
  (``takt_s > 0``, optional bis ``bis_muster`` oder ``max_runden``) über
  :func:`to_spawn.starter.starte` aus — unter Windows fensterlos
  (:func:`to_spawn.prozessbaum.ohne_fenster`) und UTF-8-sicher (:data:`to_spawn.konsole.UNTERPROZESS_TEXT`).
- Reguläre Exit-Codes des Fern-Befehls (1–254, z. B. Urteil „rot“) werden sofort
  zurückgegeben und nie blind wiederholt; nur echte SSH-Verbindungsabbrüche
  (Exit ``255`` oder ``TimeoutExpired``) lösen das Neuverbinden aus.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config, konsole, prozessbaum, starter

log = logging.getLogger("to_spawn.ssh_beobachten")

#: OpenSSH-Exit-Code bei Verbindungs-/Transportfehler (Netz weg, DNS weg, Broken pipe).
SSH_EXIT_ABBRUCH = 255
#: Vorgabe-Verbindungsaufbau-Zeitlimit (Sekunden).
STANDARD_CONNECT_TIMEOUT_S = 10
#: Vorgabe-Keepalive-Abstand (Sekunden), damit tote Verbindungen zügig erkannt werden.
STANDARD_ALIVE_INTERVAL_S = 15
#: Vorgabe-Keepalive-Fehlversuche bis zum Trennen (3 × 15 s = 45 s).
STANDARD_ALIVE_COUNT_MAX = 3
#: Vorgabe-Pause (Sekunden) vor einer automatischen Neuverbindung.
STANDARD_PAUSE_S = 15.0

EXIT_AUFRUF = 2


@dataclass(frozen=True)
class BeobachtungsErgebnis:
    """Ergebnis einer SSH-Beobachtung (stehend oder im Takt)."""

    exit_code: int
    stdout: str
    stderr: str
    neuverbindungen: int = 0
    abbruch_gruende: tuple[str, ...] = ()
    runden: int = 1


def ist_ssh_befehl(befehl: Sequence[str] | None, *, plattform: str | None = None) -> bool:
    """``True``, wenn das erste Argument ein ``ssh``-Aufruf ist (Linux/Windows, auch mit Pfad)."""
    if not befehl:
        return False
    ist_windows = (sys.platform if plattform is None else plattform) == "win32"
    return prozessbaum.programm_name(str(befehl[0]), windows=ist_windows) == "ssh"


def _gesetzte_o_schluessel(optionen: Sequence[str]) -> set[str]:
    """Alle bereits per ``-o Schlüssel=Wert`` bzw. ``-oSchlüssel=Wert`` gesetzten Namen (klein)."""
    namen: set[str] = set()
    i = 0
    while i < len(optionen):
        teil = str(optionen[i])
        if teil == "-o" and i + 1 < len(optionen):
            wert = str(optionen[i + 1])
            namen.add(wert.split("=", 1)[0].strip().lower())
            i += 2
            continue
        if teil.startswith("-o") and len(teil) > 2:
            wert = teil[2:]
            namen.add(wert.split("=", 1)[0].strip().lower())
        i += 1
    return namen


def ssh_befehl(
    ziel: str,
    fern_befehl: str,
    *,
    connect_timeout_s: int = STANDARD_CONNECT_TIMEOUT_S,
    alive_interval_s: int = STANDARD_ALIVE_INTERVAL_S,
    alive_count_max: int = STANDARD_ALIVE_COUNT_MAX,
    zusatz_optionen: Sequence[str] = (),
) -> list[str]:
    """Baut den ``ssh``-Aufruf mit Keepalive-Optionen, ohne gesetzte ``-o``-Werte zu doppeln."""
    extra = [str(teil) for teil in zusatz_optionen]
    vorhanden = _gesetzte_o_schluessel(extra)
    vorgaben = (
        ("BatchMode", "yes"),
        ("ConnectTimeout", str(max(1, int(connect_timeout_s)))),
        ("ServerAliveInterval", str(max(1, int(alive_interval_s)))),
        ("ServerAliveCountMax", str(max(1, int(alive_count_max)))),
    )
    cmd: list[str] = ["ssh"]
    for name, wert in vorgaben:
        if name.lower() not in vorhanden:
            cmd.extend(["-o", f"{name}={wert}"])
    cmd.extend(extra)
    cmd.extend([ziel, fern_befehl])
    return cmd


def _letzte_fehlerzeile(text: str, ersatz: str) -> str:
    zeilen = [z.strip() for z in text.splitlines() if z.strip()]
    return zeilen[-1][:200] if zeilen else ersatz


def _zaehler_text(anzahl: int, maximum: int | None) -> str:
    return f"{anzahl}/{maximum}" if maximum is not None else str(anzahl)


def _ausgabe_weitergeben(text: str, ausgabe_zeile: Callable[[str], object] | None) -> None:
    if ausgabe_zeile is None or not text:
        return
    for zeile in text.splitlines():
        ausgabe_zeile(zeile)


def beobachten(
    ziel: str,
    fern_befehl: str,
    *,
    timeout_s: float | None = None,
    pause_s: float = STANDARD_PAUSE_S,
    takt_s: float = 0.0,
    max_neuverbindungen: int | None = None,
    max_frist_s: float | None = None,
    max_runden: int | None = None,
    bis_muster: str | None = None,
    connect_timeout_s: int = STANDARD_CONNECT_TIMEOUT_S,
    alive_interval_s: int = STANDARD_ALIVE_INTERVAL_S,
    alive_count_max: int = STANDARD_ALIVE_COUNT_MAX,
    zusatz_optionen: Sequence[str] = (),
    schlafen: Callable[[float], object] = time.sleep,
    uhr: Callable[[], float] = time.monotonic,
    ausgabe_zeile: Callable[[str], object] | None = None,
) -> BeobachtungsErgebnis:
    """Führt eine SSH-Beobachtung aus und verbindet bei Aussetzern (Exit 255 / Timeout) neu.

    - ``takt_s == 0``: stehende Verbindung (z. B. Warten auf Urteil); endet beim ersten
      regulären Exit (0–254).
    - ``takt_s > 0``: Takt-Schleife (Status lesen / Warten auf Urteil im Takt); verbindet
      je Runde frisch, übersteht Aussetzer zwischendrin mit Log-Zeile und endet bei
      Treffer auf ``bis_muster``, nach ``max_runden`` oder bei regulärem Fern-Fehler (1–254).
    """
    cmd = ssh_befehl(
        ziel,
        fern_befehl,
        connect_timeout_s=connect_timeout_s,
        alive_interval_s=alive_interval_s,
        alive_count_max=alive_count_max,
        zusatz_optionen=zusatz_optionen,
    )
    muster = re.compile(bis_muster) if bis_muster else None
    pause = max(0.0, float(pause_s))
    takt = max(0.0, float(takt_s))
    ende_frist = (uhr() + float(max_frist_s)) if max_frist_s is not None else None

    neuverbindungen = 0
    aussetzer_seit_ok = 0
    runden = 0
    gruende: list[str] = []
    letzte_stdout = ""
    letzte_stderr = ""

    while True:
        runden += 1
        abbruch_grund: str | None = None
        try:
            lauf = starter.starte(
                cmd,
                warten=True,
                notnagel=None,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout_s,
                check=False,
                **konsole.UNTERPROZESS_TEXT,
                **prozessbaum.ohne_fenster(),
            )
        except FileNotFoundError as fehler:
            grund = f"ssh nicht aufrufbar: {fehler}"
            log.error("SSH-Beobachtung %s: %s", ziel, grund)
            return BeobachtungsErgebnis(
                exit_code=SSH_EXIT_ABBRUCH,
                stdout=letzte_stdout,
                stderr=grund,
                neuverbindungen=neuverbindungen,
                abbruch_gruende=tuple(gruende),
                runden=runden,
            )
        except subprocess.TimeoutExpired:
            abbruch_grund = (
                f"Zeitüberschreitung nach {timeout_s:g} s" if timeout_s is not None else "Zeitüberschreitung"
            )
        except OSError as fehler:
            abbruch_grund = f"Verbindungsfehler ({fehler})"
        else:
            letzte_stdout = lauf.stdout or ""
            letzte_stderr = lauf.stderr or ""
            if lauf.returncode == SSH_EXIT_ABBRUCH:
                abbruch_grund = _letzte_fehlerzeile(letzte_stderr, f"SSH Exit {SSH_EXIT_ABBRUCH}")
            else:
                if aussetzer_seit_ok > 0:
                    log.info(
                        "SSH-Beobachtung %s: Verbindung nach %d Aussetzer(n) wiederhergestellt.",
                        ziel,
                        aussetzer_seit_ok,
                    )
                    aussetzer_seit_ok = 0
                _ausgabe_weitergeben(letzte_stdout, ausgabe_zeile)
                if lauf.returncode != 0:
                    return BeobachtungsErgebnis(
                        exit_code=int(lauf.returncode),
                        stdout=letzte_stdout,
                        stderr=letzte_stderr,
                        neuverbindungen=neuverbindungen,
                        abbruch_gruende=tuple(gruende),
                        runden=runden,
                    )
                if takt <= 0.0:
                    return BeobachtungsErgebnis(
                        exit_code=0,
                        stdout=letzte_stdout,
                        stderr=letzte_stderr,
                        neuverbindungen=neuverbindungen,
                        abbruch_gruende=tuple(gruende),
                        runden=runden,
                    )
                if muster is not None and muster.search(letzte_stdout):
                    return BeobachtungsErgebnis(
                        exit_code=0,
                        stdout=letzte_stdout,
                        stderr=letzte_stderr,
                        neuverbindungen=neuverbindungen,
                        abbruch_gruende=tuple(gruende),
                        runden=runden,
                    )
                if max_runden is not None and runden >= max_runden:
                    return BeobachtungsErgebnis(
                        exit_code=0,
                        stdout=letzte_stdout,
                        stderr=letzte_stderr,
                        neuverbindungen=neuverbindungen,
                        abbruch_gruende=tuple(gruende),
                        runden=runden,
                    )
                if ende_frist is not None and uhr() >= ende_frist:
                    return BeobachtungsErgebnis(
                        exit_code=0,
                        stdout=letzte_stdout,
                        stderr=letzte_stderr,
                        neuverbindungen=neuverbindungen,
                        abbruch_gruende=tuple(gruende),
                        runden=runden,
                    )
                schlafen(takt)
                continue

        # Verbindungsabbruch (Exit 255, Timeout oder transienter Verbindungsfehler).
        assert abbruch_grund is not None
        gruende.append(abbruch_grund)
        if max_neuverbindungen is not None and neuverbindungen >= max_neuverbindungen:
            log.error(
                "SSH-Beobachtung %s: Verbindung abgebrochen (%s) — Höchstzahl von %d Neuverbindung(en) erreicht.",
                ziel,
                abbruch_grund,
                max_neuverbindungen,
            )
            return BeobachtungsErgebnis(
                exit_code=SSH_EXIT_ABBRUCH,
                stdout=letzte_stdout,
                stderr=letzte_stderr or abbruch_grund,
                neuverbindungen=neuverbindungen,
                abbruch_gruende=tuple(gruende),
                runden=runden,
            )
        if ende_frist is not None and uhr() >= ende_frist:
            log.error(
                "SSH-Beobachtung %s: Verbindung abgebrochen (%s) — Zeitfrist erreicht.",
                ziel,
                abbruch_grund,
            )
            return BeobachtungsErgebnis(
                exit_code=SSH_EXIT_ABBRUCH,
                stdout=letzte_stdout,
                stderr=letzte_stderr or abbruch_grund,
                neuverbindungen=neuverbindungen,
                abbruch_gruende=tuple(gruende),
                runden=runden,
            )
        neuverbindungen += 1
        aussetzer_seit_ok += 1
        log.warning(
            "SSH-Beobachtung %s: Verbindung abgebrochen (%s) — verbinde in %.1f s neu (Neuverbindung %s).",
            ziel,
            abbruch_grund,
            pause,
            _zaehler_text(neuverbindungen, max_neuverbindungen),
        )
        schlafen(pause)


# --- CLI ------------------------------------------------------------------------------


def richte_parser_ein(unter: Any) -> None:
    """Registriert den Unterbefehl ``to_spawn.py ssh-beobachten`` (#603)."""
    p = unter.add_parser(
        "ssh-beobachten",
        help="aussetzerfeste SSH-Beobachtung zum Bau-Server: bei Abbruch neu verbinden mit Log-Zeile (#603)",
    )
    p.add_argument("--ziel", default="", help="SSH-Ziel (Vorgabe: ssh_ziel aus .to-spawn/config.json)")
    p.add_argument(
        "--pause",
        type=float,
        default=STANDARD_PAUSE_S,
        help=f"Pause in Sekunden vor jeder Neuverbindung ({int(STANDARD_PAUSE_S)})",
    )
    p.add_argument(
        "--takt",
        type=float,
        default=0.0,
        help="Takt in Sekunden für Status-Schleife (0 = stehende Verbindung bis Exit != 255)",
    )
    p.add_argument("--timeout", type=float, default=None, help="Zeitlimit je einzelner SSH-Verbindung (Sekunden)")
    p.add_argument(
        "--max-neuverbindungen",
        type=int,
        default=None,
        help="Höchstzahl Neuverbindungen nach SSH-Aussetzern (ohne Angabe: unbegrenzt)",
    )
    p.add_argument("--max-frist", type=float, default=None, help="Gesamtfrist in Sekunden (optional)")
    p.add_argument("--max-runden", type=int, default=None, help="Höchstzahl Runden im Takt-Modus (optional)")
    p.add_argument("--bis-muster", default=None, help="Regex im Takt-Modus: bei Treffer sauber mit Exit 0 enden")
    p.add_argument("kommando", nargs="*", help="Fern-Befehl hinter `--`")


def _befehl_bereinigen(teile: Sequence[str]) -> str:
    liste = list(teile)
    if liste and liste[0] == "--":
        liste = liste[1:]
    return " ".join(str(t) for t in liste).strip()


def lauf(args: argparse.Namespace, repo: Path) -> int:
    """Ausführung des Unterbefehls ``to_spawn.py ssh-beobachten``."""
    fern = _befehl_bereinigen(args.kommando)
    if not fern:
        print("FEHLER: Fern-Befehl fehlt — hinter `--` angeben.", file=sys.stderr)
        return EXIT_AUFRUF
    ziel = (args.ziel or "").strip() or config.ssh_ziel(config.lade(repo))
    erg = beobachten(
        ziel,
        fern,
        timeout_s=args.timeout,
        pause_s=args.pause,
        takt_s=args.takt,
        max_neuverbindungen=args.max_neuverbindungen,
        max_frist_s=args.max_frist,
        max_runden=args.max_runden,
        bis_muster=args.bis_muster,
        ausgabe_zeile=lambda z: print(z, flush=True),
    )
    if erg.exit_code != 0 and erg.stderr:
        print(erg.stderr.rstrip(), file=sys.stderr)
    return erg.exit_code
