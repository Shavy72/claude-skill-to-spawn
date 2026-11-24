"""Docker-Reste (#1372, E42): gibt einmal täglich unbenutzte Images und Build-Reste frei.

Teil des Hausmeisters — nur :func:`to_spawn.hausmeister.aufraeumen_alles` ruft
:func:`freigeben`; Aufrufer kennen keine der Regeln hier. Die Tür wirft nie.

Regeln (alle hier, nirgends sonst):
- Höchstens ein Lauf je :data:`ABSTAND_S` (24 h). Maßgeblich ist die letzte Zeile in
  ``docker_reste.jsonl`` neben dem Hausmeister-Log. Auch ein gescheiterter Lauf zählt,
  sonst stünde bei kaputtem Docker alle 15 min ein Fehler im Log.
- Nie während eines Staging-Neubaus: ``scripts/staging/staging_deploy.sh`` hält die
  ganze Zeit (Image-Bau bis Rauchtest) eine flock-Sperre auf jedem Pfad aus
  ``staging_sperren`` der Maschinen-Liste (Vorgabe :data:`STAGING_SPERRE_STANDARD`).
  Ist eine belegt, passiert nichts, der nächste Takt
  versucht es neu. Sind alle frei, hält dieser Lauf sie selbst, bis beide Befehle fertig
  sind — ``image prune -a`` sieht nur das Bau-Datum, ein Grund-Image wie
  ``python:3.12-slim`` fiele sonst mitten in einen gerade startenden Neubau. Ein
  Staging-Deploy wartet so lange (``flock -w`` in ``staging_deploy.sh``, länger als
  2 × :data:`ZEITLIMIT_S`). Sperre nicht prüfbar (Rechte, Ordner) ⇒ im Zweifel nicht
  räumen. :data:`MINDEST_ALTER`: Images und Cache jünger als 24 h bleiben immer.
- Nur die Befehle in :data:`BEFEHLE`: ``image prune`` und ``builder prune``. Nie
  ``volume``, nie ``system prune`` (``--volumes`` wäre dort ein Schalter weg) —
  Volumes wie ``werkstatt-live_werkstatt-arbeit`` bleiben unberührt. Der stündliche
  Cron ``docker builder prune -f --filter until=3h`` (crontab ``bau``) räumt nur
  verwaisten Cache; hier geht zusätzlich der unbenutzte, nicht verwaiste Cache (``-a``).
- Ohne flock (Windows/Laptop, :func:`to_spawn.sperre.flock_vorhanden`) läuft nichts:
  E42 gilt nur für den Bau-Server.

Test-Nähte: ``TO_SPAWN_HAUSMEISTER_DOCKER`` (Docker-Programm, Standard ``docker``),
``TO_SPAWN_STAGING_SPERRE`` (Pfad der Staging-Sperre, hat Vorrang vor der Maschinen-Liste).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from typing import Literal

from . import config, konsole
from . import sperre as _sperre

#: Nur noch Alt-Naht für ältere Tests (``skipif``/Gegen-Sperre); gesperrt wird hier allein
#: über :func:`to_spawn.sperre.halte`.
fcntl = _sperre._fcntl

log = logging.getLogger("to_spawn.docker_reste")

ABSTAND_S = 24 * 3600
MINDEST_ALTER = "until=24h"
#: Vorgabe von ``staging_sperren`` (L1 #46); Name bleibt für Aufrufer.
STAGING_SPERRE_STANDARD: str = config.MASCHINE_DEFAULTS["staging_sperren"][0]
STAND_DATEI = "docker_reste.jsonl"
ZEITLIMIT_S = 5 * 60  # je Befehl; Staging-Deploy wartet bis 15 min
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
    if not _sperre.flock_vorhanden():
        return "kein_server"
    stand = ordner / STAND_DATEI
    letzte = _letzter_lauf(stand)
    if letzte is not None and 0 <= jetzt - letzte < ABSTAND_S:
        return "nicht_faellig"
    sperre = _staging_sperre_nehmen()
    if sperre is None:
        log.info("Docker-Reste: Staging-Neubau läuft — nächster Takt")
        return "staging_baut"
    with sperre:  # Schließen gibt die flock-Sperre frei
        return _laufen(stand, jetzt)


def _laufen(stand: Path, jetzt: float) -> Ausgang:
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
    if fehler:
        log.warning("Docker-Reste: %s", zeile)
        return "fehler"
    log.info("Docker-Reste: %s", zeile)
    return "freigegeben"


def _docker(befehl: tuple[str, ...]) -> tuple[bool, str]:
    """(ok, Ausgabe bzw. Fehlertext) eines Befehls aus :data:`BEFEHLE`."""
    programm = os.environ.get("TO_SPAWN_HAUSMEISTER_DOCKER") or "docker"
    try:
        lauf = konsole.lauf_daten([programm, *befehl], timeout=ZEITLIMIT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as fehler:
        return False, str(fehler)
    if lauf is None:
        return False, "Docker nicht lesbar (Ausgabe nicht UTF-8)"
    if lauf.returncode != 0:
        return (
            False,
            f"Exit {lauf.returncode}: {(lauf.stderr or lauf.stdout).strip()[-300:]}",
        )
    return True, lauf.stdout


def _sperr_pfade() -> list[Path]:
    """Pfade der Staging-Sperren: ``TO_SPAWN_STAGING_SPERRE``, sonst ``staging_sperren`` der Maschinen-Liste."""
    roh = os.environ.get("TO_SPAWN_STAGING_SPERRE")
    if roh:
        return [Path(roh)]
    return [Path(str(p)).expanduser() for p in config.maschine()["staging_sperren"] if str(p).strip()]


def _staging_sperre_nehmen() -> ExitStack | None:
    """Alle Staging-Sperren nicht blockierend nehmen; ``None`` = ein Neubau läuft oder unklar.

    Keine Datei = Staging nie eingerichtet, also kein Neubau (die Datei legt nur
    ``staging_deploy.sh`` an, nie dieser Lauf). Schließen des Stapels gibt alle frei.
    """
    stapel = ExitStack()
    for pfad in _sperr_pfade():
        if not os.path.lexists(pfad):  # nie angelegt; halte() legte sie sonst an
            continue
        try:
            gehalten = stapel.enter_context(_sperre.halte(pfad))
        except OSError as fehler:
            log.warning("Docker-Reste: Staging-Sperre %s nicht prüfbar (%s)", pfad, fehler)
            stapel.close()
            return None
        if not gehalten:  # Deploy hält sie
            stapel.close()
            return None
    return stapel


def _letzter_lauf(stand: Path) -> float | None:
    """Zeit der letzten Zeile; unlesbar/leer = nie gelaufen."""
    try:
        zeilen = stand.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as fehler:
        log.warning(
            "Docker-Reste: %s unlesbar (%s) — gilt als nie gelaufen", stand, fehler
        )
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
