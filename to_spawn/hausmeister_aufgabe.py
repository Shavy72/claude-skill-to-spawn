"""Hausmeister am Laptop (#1373): tägliche Windows-Aufgabe, Ticket-Ende, Altbestand-Liste.

Auf dem Bau-Server stößt der Aufpasser-Cron den Hausmeister an; am Windows-Laptop
gibt es keinen Aufpasser. Darum hier drei Türen, die alle nur
:func:`to_spawn.hausmeister.aufraeumen_alles` auslösen — die Lösch-Regeln kennt
allein ``hausmeister``:

- :func:`aufgabe_einrichten` legt die tägliche Aufgabe in der Windows-Aufgabenplanung
  an (idempotent, ``pythonw.exe`` ohne Fenster). Vorher hält sie den Altbestand fest,
  damit der erste Lauf keine alten Ordner ohne das Ja des Menschen löscht.
- :func:`ticket_ende_anstossen` startet nach einer lokalen Bau-Session einen Lauf im
  Hintergrund (nur Windows; auf dem Server läuft der Aufpasser).
- :func:`loesch_liste` schreibt die Altbestand-Liste für den Menschen (Trockenlauf, löscht
  nichts) und hält genau diese Ordner fest.

Kein Mail-Versand, nur Log.
"""

from __future__ import annotations

import html
import logging
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from to_spawn import hausmeister, konsole, starter

log = logging.getLogger(__name__)

#: Name der Aufgabe in der Windows-Aufgabenplanung.
AUFGABE_NAME = "to-spawn Hausmeister"
#: Zeitlimit je schtasks-Aufruf in Sekunden.
SCHTASKS_ZEITLIMIT_S = 30
#: Werte der Windows-Konstanten; getattr-Fallback, weil ``subprocess`` sie nur unter Windows kennt.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

_CLI = Path(__file__).resolve().parent.parent / "to_spawn.py"
_LAUF_ARGS = ("aufraeumen", "--alles", "--loeschen")
_GRUPPEN = (
    "Löschbar",
    "Sichern + löschen",
    "Bleibt (mit Grund)",
    "Ohne Ticket-Nummer",
    "Für den Menschen (selbst prüfen)",
)


def _ist_windows() -> bool:
    return sys.platform == "win32"


def aufgabe_einrichten(*, trocken: bool = False, python: str = sys.executable, uhrzeit: str = "03:30") -> str:
    """Tägliche Aufgabe anlegen oder ersetzen (idempotent); gibt einen Satz für Menschen.

    Gleiche Aufgabe (Befehl + Uhrzeit) schon da → nichts tun. Fehlt sie oder weicht
    sie ab → ``schtasks /Create /F``. Vor dem Anlegen wird der Altbestand festgehalten.
    Wirft ``RuntimeError``, wenn ``pythonw.exe`` oder schtasks fehlt bzw. scheitert.
    """
    if not _ist_windows():
        return "Hausmeister-Aufgabe gibt es nur unter Windows — hier nichts eingerichtet (Server: Aufpasser-Cron)."
    pythonw = Path(python).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise RuntimeError(f"pythonw.exe fehlt neben {python} — ohne sie öffnet die Aufgabe jede Nacht ein Fenster.")
    argumente = f'"{_CLI}" ' + " ".join(_LAUF_ARGS)
    if _aufgabe_gleich(_abfrage(), str(pythonw), argumente, uhrzeit):
        return f"Hausmeister-Aufgabe schon eingerichtet (täglich {uhrzeit})."
    befehl = f'"{pythonw}" {argumente}'
    if trocken:
        return f"[trocken] Hausmeister-Aufgabe würde eingerichtet: täglich {uhrzeit} {befehl}"
    pfad = hausmeister.altbestand_festhalten(hausmeister._kandidaten())
    log.info("Hausmeister: Altbestand festgehalten in %s", pfad)
    lauf = _schtasks(
        "/Create",
        "/F",
        "/SC",
        "DAILY",
        "/ST",
        uhrzeit,
        "/TN",
        AUFGABE_NAME,
        "/TR",
        befehl,
    )
    if lauf.returncode != 0:
        raise RuntimeError(f"schtasks /Create: {(lauf.stderr or lauf.stdout).strip()[:300]}")
    return f"Hausmeister-Aufgabe eingerichtet: täglich {uhrzeit} {befehl}"


def _abfrage() -> str | None:
    """XML der vorhandenen Aufgabe; ``None`` = gibt es nicht."""
    lauf = _schtasks("/Query", "/TN", AUFGABE_NAME, "/XML")
    return lauf.stdout if lauf.returncode == 0 else None


def _aufgabe_gleich(xml: str | None, pythonw: str, argumente: str, uhrzeit: str) -> bool:
    # Regex statt XML-Parser: schtasks kündigt UTF-16 an, liefert über die Pipe aber Text.
    if not xml:
        return False

    def feld(name: str) -> str:
        treffer = re.search(rf"<{name}>(.*?)</{name}>", xml, re.DOTALL)
        return html.unescape(treffer.group(1)).strip() if treffer else ""

    start = feld("StartBoundary")
    return (
        feld("Command").strip('"').lower() == pythonw.lower()
        and feld("Arguments") == argumente
        and start[11:16] == uhrzeit
    )


def _schtasks(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["schtasks", *args],
            capture_output=True,
            **konsole.UNTERPROZESS_TEXT,
            check=False,
            timeout=SCHTASKS_ZEITLIMIT_S,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError as fehler:
        raise RuntimeError("schtasks nicht gefunden — Windows-Aufgabenplanung fehlt") from fehler
    except subprocess.TimeoutExpired as fehler:
        raise RuntimeError(f"schtasks {args[0]} hängt (über {SCHTASKS_ZEITLIMIT_S} s)") from fehler


def ticket_ende_anstossen() -> None:
    """Nach einer lokalen Bau-Session einen Hausmeister-Lauf im Hintergrund starten; wirft nie.

    Nur Windows: auf dem Server stößt der Aufpasser-Cron den Hausmeister an. Ohne
    Fenster, eigene Prozessgruppe (überlebt das Ende von bau.py). Ergebnisse stehen im
    Hausmeister-Log; stderr (z. B. Absturz beim Start) hängt an ``hausmeister-start.err``
    daneben.
    """
    if not _ist_windows():
        return
    fehlerdatei = hausmeister._log_pfad().parent / "hausmeister-start.err"
    try:
        fehlerdatei.parent.mkdir(parents=True, exist_ok=True)
        with fehlerdatei.open("ab") as stderr:  # Kind erbt den Handle, Eltern schließen
            starter.starte(  # einzige Start-Tür (#1406)
                [sys.executable, str(_CLI), *_LAUF_ARGS],
                notnagel=None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP,
            )
    except (OSError, starter.StartGesperrt) as fehler:
        log.warning("Hausmeister am Ticket-Ende nicht gestartet: %s", fehler)


def loesch_liste(ziel: Path) -> Path:
    """Altbestand-Liste für den Menschen schreiben (Trockenlauf, löscht nichts); gibt ``ziel``.

    Danach sind alle gelisteten Ordner festgehalten — der Hausmeister lässt sie
    stehen, bis der Mensch sie freigibt. ``RuntimeError``, wenn der Lauf scheitert oder
    ein anderer Lauf die Sperre hält.
    """
    ergebnisse = hausmeister.aufraeumen_alles(loeschen=False)
    # aufraeumen_alles liefert bei belegter Sperre [] — leer trotz Kandidaten heißt: kein Lauf.
    if ergebnisse is None or (not ergebnisse and hausmeister._kandidaten()):
        raise RuntimeError("Hausmeister-Lauf gescheitert oder Sperre belegt (anderer Lauf) — Liste nicht geschrieben")
    gruppen: dict[str, list[hausmeister.Ergebnis]] = {g: [] for g in _GRUPPEN}
    for erg in ergebnisse:
        gruppen[_gruppe(erg)].append(erg)
    pfad = hausmeister.altbestand_festhalten([e.ordner for e in ergebnisse])
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(_markdown(gruppen, pfad), encoding="utf-8")
    log.info("Hausmeister: Altbestand-Liste %s (%d Ordner)", ziel, len(ergebnisse))
    return ziel


def _gruppe(erg: hausmeister.Ergebnis) -> str:
    if erg.art == "gelöscht":
        return _GRUPPEN[0]
    if erg.art == "gesichert und gelöscht":
        return _GRUPPEN[1]
    if erg.ticket is None:
        return _GRUPPEN[3]
    if erg.fuer_david:
        return _GRUPPEN[4]
    return _GRUPPEN[2]


def _markdown(gruppen: dict[str, list[hausmeister.Ergebnis]], altbestand: Path) -> str:
    stand = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    zeilen = [
        "# Altbestand: alte Arbeitsordner",
        "",
        f"Stand: {stand}",
        "",
        (
            "**Gelöscht wird erst nach dem Ja des Menschen.** Bis dahin bleiben alle Ordner stehen "
            f'(Freigabe: `"freigegeben": true` in `{altbestand}`).'
        ),
        "",
        " · ".join(f"{g}: {len(e)}" for g, e in gruppen.items()),
    ]
    for gruppe, eintraege in gruppen.items():
        zeilen += ["", f"## {gruppe} ({len(eintraege)})", ""]
        zeilen += [_zeile(e) for e in eintraege] or ["(keine)"]
    return "\n".join(zeilen) + "\n"


def _zeile(erg: hausmeister.Ergebnis) -> str:
    ticket = f"#{erg.ticket}" if erg.ticket is not None else "ohne Nummer"
    return f"- `{erg.ordner}` · {ticket} · {erg.grund}"
