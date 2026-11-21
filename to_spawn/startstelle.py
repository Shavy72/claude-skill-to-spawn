"""Eine Start-Stelle für Ticket-Sessions (#97, L1 E2-13, App #1390).

Jeder Weg, der eine Bau-Session startet — ``skripte/spawn_srv.sh``, ``spawn_local.ps1``,
Aufpasser (``fenster_starten``), respawn, capo-Folgerunde, ``spawn.neustart`` — öffnet
ein Fenster mit ``skripte/bau.py <N>``. Dort ruft ``main()`` vor dem ersten Claude-Start
genau einmal :func:`betreten`. Darum gibt es keine zweite Stelle, an der geprüft wird
(der Prüf-Test ``tests/test_startstelle_97.py`` wird rot, wenn ``claude`` an ihr vorbei
startet).

:func:`betreten` entscheidet unter einer Sperre je Ticket (Maschine, über
``to_spawn.sperre``):

1. Läuft für das Ticket schon eine Session auf dieser Maschine? → kein Start (Exit 0,
   eine Zeile, kein Fehler-Lärm). Die Sperre hält der laufende ``bau.py`` bis zu seinem
   Ende; stirbt er, gibt das Betriebssystem sie frei (tote Sperre = keine Sperre).
2. Ticket geschlossen? → kein Start (Exit 0, eine Zeile).
3. Ticket-Stand nicht bestimmbar (gh fehlt, Netz weg, kaputte Antwort) → fail-closed:
   kein Start, laute Meldung, Exit 2.

Ablösung (respawn startet ``bau.py <N> --sofort --ohne-prompt``): die neue Session darf neben der alten starten,
aber nur eine Ablösung je Ticket gleichzeitig. Sie übernimmt die Ticket-Sperre, sobald
die alte Session endet — danach ist sie wieder „die eine“ Session.

:func:`weiter_erlaubt` prüft vor jeder Staffel-Runde erneut, ob das Ticket noch offen ist.

Test-Naht: unter Test (``starter.unter_test()``) mit ``TO_SPAWN_TEST_TICKET_OFFEN=1``
gilt jedes Ticket als offen; ``TO_SPAWN_START_SPERREN`` lenkt den Sperr-Ordner um.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from to_spawn import gh, sperre, starter

log = logging.getLogger(__name__)

#: Umleitung des Sperr-Ordners (Tests, Sonderfälle).
SPERR_ORDNER_ENV = "TO_SPAWN_START_SPERREN"
#: Nur unter Test wirksam: jedes Ticket gilt als offen (Tests ohne gh-Attrappe).
TEST_OFFEN_ENV = "TO_SPAWN_TEST_TICKET_OFFEN"
#: Exit-Code, wenn der Ticket-Stand nicht bestimmbar ist (laut, fail-closed).
EXIT_UNKLAR = 2

#: Liefert ``True`` (offen), ``False`` (zu) oder ``None`` (unbekannt).
TicketPruefer = Callable[[str, "Path | None"], "bool | None"]

# Gehaltene Sperren bis Prozess-Ende (sonst schlösse der Müllsammler sie).
_GEHALTEN: list[contextlib.ExitStack] = []


@dataclass(frozen=True)
class Entscheidung:
    """Ergebnis von :func:`betreten`: ``erlaubt`` + eine Zeile Grund + Exit-Code bei Nein."""

    erlaubt: bool
    grund: str
    exit_code: int = 0


def sperr_datei(ticket: str, gh_repo: str) -> Path:
    """Sperr-Datei je Repo und Ticket auf dieser Maschine (alle Klone teilen sie)."""
    ordner = os.environ.get(SPERR_ORDNER_ENV) or str(Path.home() / ".to-spawn" / "start-sperren")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", gh_repo or "ohne-repo")
    return Path(ordner) / f"{slug}-{ticket}.lock"


def _zustand(ticket: str, cwd: Path | None, pruefer: TicketPruefer | None) -> bool | None:
    if pruefer is not None:
        return pruefer(ticket, cwd)
    if os.environ.get(TEST_OFFEN_ENV) == "1" and starter.unter_test():
        return True
    return gh.ticket_offen(ticket, cwd=cwd)


def _pruefen(ticket: str, cwd: Path | None, pruefer: TicketPruefer | None) -> Entscheidung:
    offen = _zustand(ticket, cwd, pruefer)
    if offen is None:
        grund = (
            f"bau {ticket}: Ticket-Stand nicht bestimmbar (gh fehlt/Fehler) — KEIN Start "
            "(fail-closed). gh prüfen, dann neu starten."
        )
        log.error("%s", grund)
        return Entscheidung(False, grund, EXIT_UNKLAR)
    if not offen:
        grund = f"bau {ticket}: Ticket ist geschlossen — kein Start."
        log.info("%s", grund)
        return Entscheidung(False, grund)
    return Entscheidung(True, f"bau {ticket}: offen, Sperre gehalten — Start.")


def betreten(
    ticket: str,
    gh_repo: str,
    cwd: Path | None = None,
    *,
    abloesung: bool = False,
    pruefer: TicketPruefer | None = None,
) -> Entscheidung:
    """Die eine Tür vor jedem Session-Start; bei ``erlaubt`` bleibt die Sperre bis Prozess-Ende.

    ``abloesung``: respawn — Start neben der laufenden Session erlaubt (höchstens eine
    Ablösung gleichzeitig), die Ticket-Sperre geht nach deren Ende auf diesen Prozess über.
    ``pruefer``: Test-Naht statt ``gh.ticket_offen``.
    """
    ticket = str(ticket)
    haupt = sperr_datei(ticket, gh_repo)
    stapel = contextlib.ExitStack()
    try:
        if stapel.enter_context(sperre.halte(haupt)):
            entscheidung = _pruefen(ticket, cwd, pruefer)
        elif not abloesung:
            entscheidung = Entscheidung(False, f"bau {ticket}: läuft schon auf dieser Maschine — kein zweiter Start.")
            log.info("%s", entscheidung.grund)
        elif not stapel.enter_context(sperre.halte(haupt.with_name(haupt.name + ".abloesung"))):
            entscheidung = Entscheidung(False, f"bau {ticket}: eine Ablösung läuft schon — kein zweiter Start.")
            log.info("%s", entscheidung.grund)
        else:
            entscheidung = _pruefen(ticket, cwd, pruefer)
            if entscheidung.erlaubt:
                _uebernehmen_wenn_frei(haupt, stapel)
                return entscheidung
    except OSError as fehler:
        stapel.close()
        grund = f"bau {ticket}: Start-Sperre {haupt} nicht nutzbar ({fehler}) — KEIN Start."
        log.error("%s", grund)
        return Entscheidung(False, grund, EXIT_UNKLAR)
    if entscheidung.erlaubt:
        _GEHALTEN.append(stapel)
    else:
        stapel.close()
    return entscheidung


def _uebernehmen_wenn_frei(haupt: Path, abloese_stapel: contextlib.ExitStack) -> None:
    """Ablösung: im Hintergrund auf die Ticket-Sperre warten, halten, dann Ablöse-Sperre frei."""

    def warten() -> None:
        with contextlib.ExitStack() as stapel:
            try:
                if not stapel.enter_context(sperre.halte(haupt, warten=float(10**9))):
                    return
            except OSError as fehler:
                log.error("Ablösung: Ticket-Sperre %s nicht übernehmbar: %s", haupt, fehler)
                return
            abloese_stapel.close()
            threading.Event().wait()  # bis Prozess-Ende halten

    _GEHALTEN.append(abloese_stapel)
    threading.Thread(target=warten, name="start-sperre-uebernahme", daemon=True).start()


def weiter_erlaubt(ticket: str, cwd: Path | None = None, *, pruefer: TicketPruefer | None = None) -> bool:
    """Vor jeder Staffel-Runde: Ticket noch offen? Unbekannt zählt als Nein (fail-closed)."""
    return _pruefen(str(ticket), cwd, pruefer).erlaubt
