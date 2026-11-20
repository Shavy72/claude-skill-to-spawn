"""Hindernis-Log (#1369): eine JSON-Zeile je Hindernis, das einen Start aufhält.

Beispiel: Platte fast voll, der Start-Check lässt keine neue Session zu. Die Zeilen
zeigt ``/stand`` über :func:`kurzzeile` (#1433) — nie Mail, nie
Benachrichtigung. Eigene Datei statt Repo-Doku (eine im Ticket genannte
``docs/GRILL_…_hindernis-log.md`` gibt es nicht, und Cron darf keine Repo-Doku
committen). Datei: ``TO_SPAWN_HINDERNIS_LOG``, Standard
``~/.claude/to-spawn/hindernisse.jsonl``. Beide Türen werfen nie.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import socket
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger("to_spawn.hindernis")


def _pfad() -> Path:
    roh = os.environ.get("TO_SPAWN_HINDERNIS_LOG")
    return (
        Path(roh).expanduser()
        if roh
        else Path.home() / ".claude" / "to-spawn" / "hindernisse.jsonl"
    )


def _wer() -> str:
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"
    except (OSError, KeyError):
        return "unbekannt"


def melden(art: str, text: str, **felder: Any) -> bool:
    """Hängt eine Zeile ``{zeit, art, text, wer, **felder}`` an; False = nicht schreibbar."""
    zeile = {
        "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
        "art": art,
        "text": text,
        "wer": _wer(),
        **felder,
    }
    try:
        datei = _pfad()
        datei.parent.mkdir(parents=True, exist_ok=True)
        with datei.open("a", encoding="utf-8") as f:
            f.write(json.dumps(zeile, ensure_ascii=False, default=str) + "\n")
    except OSError as fehler:
        log.warning("Hindernis-Log nicht schreibbar: %s", fehler)
        return False
    return True


def lesen(datei: Path | None = None) -> list[dict[str, Any]]:
    """Alle lesbaren Zeilen (älteste zuerst); fehlende Datei oder kaputte Zeile → übersprungen."""
    try:
        zeilen = (datei or _pfad()).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    ergebnis: list[dict[str, Any]] = []
    for zeile in zeilen:
        try:
            daten = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(daten, dict):
            ergebnis.append(daten)
    return ergebnis


#: Wie weit ``/stand`` zurückschaut (#1433).
FENSTER = timedelta(hours=24)


def _zeitpunkt(roh: object) -> datetime | None:
    """ISO-Zeit der Zeile; ohne Zone = lokale Zeit; unlesbar → ``None``."""
    try:
        zeit = datetime.fromisoformat(str(roh))
    except ValueError:
        return None
    return zeit if zeit.tzinfo else zeit.astimezone()


def offene(
    *, jetzt: datetime | None = None, datei: Path | None = None
) -> list[dict[str, Any]]:
    """Offene Hindernisse der letzten 24 h, je ``art`` nur die jüngste Zeile (neueste zuerst).

    Eine Zeile mit ``erledigt`` genau ``True`` (Ende-Zeile, siehe :func:`erledigt`)
    schließt alle früheren Zeilen ihrer Art; eine spätere Meldung öffnet sie wieder.
    Jede Ergebnis-Zeile trägt ``anzahl`` (wie oft die Art seit dem letzten Ende im
    Fenster auftrat) und ``zeitpunkt`` (datetime). Zeilen ohne lesbare Zeit zählen
    nicht. Fehlende Datei → leer, wirft nie.
    """
    grenze = (jetzt or datetime.now().astimezone()) - FENSTER
    je_art: dict[str, dict[str, Any]] = {}
    gelesen: list[tuple[datetime, dict[str, Any]]] = []
    for zeile in lesen(datei):
        zeit = _zeitpunkt(zeile.get("zeit"))
        if zeit is not None and zeit >= grenze:
            gelesen.append((zeit, zeile))
    for zeit, zeile in sorted(gelesen, key=lambda paar: paar[0]):
        art = str(zeile.get("art", "?"))
        if zeile.get("erledigt") is True:
            je_art.pop(art, None)
            continue
        alt = je_art.get(art)
        anzahl = (alt["anzahl"] if alt else 0) + 1
        if alt is None or zeit >= alt["zeitpunkt"]:
            je_art[art] = {**zeile, "zeitpunkt": zeit}
        je_art[art]["anzahl"] = anzahl
    return sorted(je_art.values(), key=lambda z: z["zeitpunkt"], reverse=True)


def erledigt(*arten: str) -> list[str]:
    """Meldet das Ende: je genannte Art, die gerade offen ist, eine Ende-Zeile; wirft nie.

    Ruft die Stelle, die bei gesundem Zustand vorbeikommt (z. B. Start-Check mit
    messbarer, freier Platte). Nicht offene Arten schreiben nichts — so wächst das Log
    bei jedem gesunden Lauf nicht. Rückgabe: die geschlossenen Arten.
    Lesen und Schreiben sind nicht atomar (das Modul hat keine Sperre): schlimmstenfalls
    schließt eine Ende-Zeile eine Meldung, die ein paralleler Lauf Sekunden zuvor
    schrieb — harmlos, weil derselbe Zustand beim nächsten Start-Check neu gemeldet wird.
    """
    offen = {str(z.get("art")) for z in offene()}
    zu = [art for art in arten if art in offen]
    for art in zu:
        melden(art, "behoben", erledigt=True)
    return zu


def kurzzeile(
    *, jetzt: datetime | None = None, datei: Path | None = None
) -> str | None:
    """Eine Zeile für ``/stand``: ``Hindernisse (24 h): <text> (2×, zuletzt 14:05) · …``.

    ``None``, wenn nichts offen ist — dann druckt ``/stand`` nichts.
    """
    teile = []
    for z in offene(jetzt=jetzt, datei=datei):
        uhr = z["zeitpunkt"].astimezone().strftime("%H:%M")
        wann = f"{z['anzahl']}×, zuletzt {uhr}" if z["anzahl"] > 1 else uhr
        text = " ".join(str(z.get("text") or z.get("art", "?")).split())
        teile.append(f"{text} ({wann})")
    return "Hindernisse (24 h): " + " · ".join(teile) if teile else None
