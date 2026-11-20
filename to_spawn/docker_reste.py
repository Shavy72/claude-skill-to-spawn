"""Docker-Reste (#1372, E42): gibt einmal täglich unbenutzte Images und Build-Reste frei.

Teil des Hausmeisters — nur :func:`to_spawn.hausmeister.aufraeumen_alles` ruft
:func:`freigeben`; Aufrufer kennen keine der Regeln hier. Die Tür wirft nie.

Regeln (alle hier, nirgends sonst):
- Höchstens ein Lauf je :data:`ABSTAND_S` (24 h). Maßgeblich ist die letzte Zeile in
  ``docker_reste.jsonl`` neben dem Hausmeister-Log. Auch ein gescheiterter Lauf zählt,
  sonst stünde bei kaputtem Docker alle 15 min ein Fehler im Log.
- Nie während eines Staging-Neubaus: ``scripts/staging/staging_deploy.sh`` hält die
  ganze Zeit (Image-Bau bis Rauchtest) eine flock-Sperre auf
  :data:`STAGING_SPERRE_STANDARD`. Ist sie belegt, passiert nichts, der nächste Takt
  versucht es neu. Die Sperre wird nur geprüft und sofort wieder frei gegeben, damit ein
  gleichzeitig startender Staging-Deploy nicht an „anderer Deploy läuft“ scheitert.
  Zweite Sicherung dafür: :data:`MINDEST_ALTER` — Images und Cache jünger als 24 h
  bleiben immer, also auch alles, was ein gerade startender Neubau braucht.
- Nur die Befehle in :data:`BEFEHLE`: ``image prune`` und ``builder prune``. Nie
  ``volume``, nie ``system prune`` (``--volumes`` wäre dort ein Schalter weg) —
  Volumes wie ``werkstatt-live_werkstatt-arbeit`` bleiben unberührt. Der stündliche
  Cron ``docker builder prune -f --filter until=3h`` (crontab ``bau``) räumt nur
  verwaisten Cache; hier geht zusätzlich der unbenutzte, nicht verwaiste Cache (``-a``).
- Ohne fcntl (Windows/Laptop) läuft nichts: E42 gilt nur für den Bau-Server.

Test-Nähte: ``TO_SPAWN_HAUSMEISTER_DOCKER`` (Docker-Programm, Standard ``docker``),
``TO_SPAWN_STAGING_SPERRE`` (Pfad der Staging-Sperre).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

try:
    import fcntl
except ImportError:  # Windows: kein Bau-Server
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger("to_spawn.docker_reste")

ABSTAND_S = 24 * 3600
MINDEST_ALTER = "until=24h"
STAGING_SPERRE_STANDARD = "/home/bau/staging/.deploy.lock"
STAND_DATEI = "docker_reste.jsonl"
ZEITLIMIT_S = 15 * 60
#: Die einzigen Docker-Befehle dieses Moduls (Prüf-Test: nie „volume“).
BEFEHLE: tuple[tuple[str, ...], ...] = (
    ("image", "prune", "-a", "-f", "--filter", MINDEST_ALTER),
    ("builder", "prune", "-a", "-f", "--filter", MINDEST_ALTER),
)

Ausgang = Literal[
    "freigegeben", "fehler", "nicht_faellig", "staging_baut", "kein_server"
]
_FREI = re.compile(r"Total(?: reclaimed space)?:\s*(\S+)", re.IGNORECASE)


def freigeben(ordner: Path, *, jetzt: float | None = None) -> Ausgang:
    """Einmal täglich Docker-Reste freigeben (siehe Modul-Kommentar); wirft nie.

    ``ordner``: wohin ``docker_reste.jsonl`` geht (neben dem Hausmeister-Log).
    Rückgabe sagt, was passiert ist — nur für Log und Tests.
    """
    try:
        return _freigeben(ordner, time.time() if jetzt is None else jetzt)
    except Exception:  # Vertrag: wirft nie, Aufräumen darf den Takt nie aufhalten
        log.exception("Docker-Reste: unerwarteter Fehler")
        return "fehler"


def _freigeben(ordner: Path, jetzt: float) -> Ausgang:
    if fcntl is None:
        return "kein_server"
    stand = ordner / STAND_DATEI
    letzte = _letzter_lauf(stand)
    if letzte is not None and jetzt - letzte < ABSTAND_S:
        return "nicht_faellig"
    if _staging_baut():
        log.info("Docker-Reste: Staging-Neubau läuft — nächster Takt")
        return "staging_baut"
    zeile: dict[str, object] = {"zeit": jetzt, "uhrzeit": _uhrzeit(jetzt)}
    fehler: list[str] = []
    for befehl in BEFEHLE:
        name = " ".join(befehl[:2])
        ok, text = _docker(befehl)
        if ok:
            treffer = _FREI.search(text)
            zeile[name] = treffer.group(1) if treffer else "?"
        else:
            fehler.append(f"{name}: {text}")
    zeile["ausgang"] = "fehler" if fehler else "freigegeben"
    if fehler:
        zeile["fehler"] = fehler
    _anhaengen(stand, zeile)
    (log.warning if fehler else log.info)("Docker-Reste: %s", zeile)
    return "fehler" if fehler else "freigegeben"


def _docker(befehl: tuple[str, ...]) -> tuple[bool, str]:
    """(ok, Ausgabe bzw. Fehlertext) eines Befehls aus :data:`BEFEHLE`."""
    programm = os.environ.get("TO_SPAWN_HAUSMEISTER_DOCKER") or "docker"
    try:
        lauf = subprocess.run(
            [programm, *befehl],
            capture_output=True,
            text=True,
            timeout=ZEITLIMIT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        return False, str(fehler)
    if lauf.returncode != 0:
        return (
            False,
            f"Exit {lauf.returncode}: {(lauf.stderr or lauf.stdout).strip()[-300:]}",
        )
    return True, lauf.stdout


def _staging_baut() -> bool:
    """True, solange ``staging_deploy.sh`` seine Sperre hält. Keine Datei = kein Neubau."""
    pfad = Path(os.environ.get("TO_SPAWN_STAGING_SPERRE") or STAGING_SPERRE_STANDARD)
    try:
        datei = pfad.open("r")
    except FileNotFoundError:
        return False
    with datei:
        try:
            fcntl.flock(datei, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(datei, fcntl.LOCK_UN)
    return False


def _letzter_lauf(stand: Path) -> float | None:
    """Zeit der letzten Zeile; unlesbar/leer = nie gelaufen."""
    try:
        zeilen = stand.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    for roh in reversed(zeilen):
        try:
            return float(json.loads(roh)["zeit"])
        except (ValueError, KeyError, TypeError):
            log.warning("Docker-Reste: kaputte Zeile in %s übersprungen", stand)
    return None


def _anhaengen(stand: Path, zeile: dict[str, object]) -> None:
    stand.parent.mkdir(parents=True, exist_ok=True)
    with stand.open("a", encoding="utf-8") as f:
        f.write(json.dumps(zeile, ensure_ascii=False) + "\n")


def _uhrzeit(jetzt: float) -> str:
    return datetime.fromtimestamp(jetzt).astimezone().isoformat(timespec="seconds")
