"""Temp-Ordner des Bau-Systems (#136): wo Temp liegt, was ein Rest ist, wann er weg darf.

Warum: der System-Temp ist auf dem Bau-Server eine RAM-Platte (tmpfs). Testläufe und alte
Ticket-Kopien füllten sie am 21.11.2026 bis „No space left on device“. Darum bekommen
Bau-Sessions und die Prüf-Tür ``TMPDIR``/``TEMP``/``TMP`` aus genau einer Stelle
(:func:`umgebung`); pytest legt seinen ``--basetemp`` von selbst unter ``TMPDIR`` an.

Schnittstelle:

- :func:`pfad` — der Temp-Ordner auf der Platte (``TO_SPAWN_TEMP_ORDNER``, sonst
  Maschinen-Liste ``temp_ordner``,
  Rückfall :data:`vorgaben.TEMP_ORDNER`), wird angelegt.
- :func:`umgebung` — die drei Variablen für ``os.environ``/``subprocess``; ``{}``, wenn
  der Ordner nicht anlegbar ist (dann bleibt der System-Temp, nie ein Start-Abbruch).
- :func:`aufraeumen` — ein Hausmeister-Schritt: Temp-Reste geschlossener Tickets im
  Temp-Ordner und im Altbestand (``TO_SPAWN_TEMP_RESTE``, sonst Maschinen-Liste
  ``temp_reste``, Rückfall
  :data:`vorgaben.TEMP_RESTE`). Prüfen, Log und Löschen nimmt es vom Hausmeister
  (:mod:`to_spawn.hausmeister`), damit es nur eine Lösch-Stelle gibt.

Ein Rest ist wegwerfbar, wenn alles gilt: echter Ordner (kein Symlink), Name wie
``t1335``/``mut1499``/``tmp_136`` (Buchstaben, Ticket-Nummer, optional ``-``/``_``-Rest),
Ticket zu, nichts darin jünger als :data:`ALTER_S`, kein Prozess darin. Ordner, unter
denen andere Muster suchen (z. B. ``claude-1000`` für ``claude-*/*``), sind nie
selbst ein Rest.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import time
from pathlib import Path

from . import config, vorgaben

log = logging.getLogger("to_spawn.temp_ordner")

#: Nichts Frisches: jüngste Änderung (Ordner + oberste Ebene) mindestens so alt.
ALTER_S = 24 * 3600
#: Name eines Ticket-Rests: Buchstaben, Nummer (≥ 2 Ziffern), optional ``-``/``_`` und Rest.
_REST_NAME = re.compile(r"^[A-Za-z]*[-_]?(\d{2,})(?:[-_].*)?$")


def pfad() -> Path:
    """Temp-Ordner auf der Platte (angelegt); ``OSError`` geht an den Aufrufer."""
    roh = os.environ.get("TO_SPAWN_TEMP_ORDNER") or config.maschine().get("temp_ordner")
    wert = roh.strip() if isinstance(roh, str) and roh.strip() else vorgaben.TEMP_ORDNER
    ordner = Path(os.path.abspath(Path(wert).expanduser()))
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner


def umgebung() -> dict[str, str]:
    """``TMPDIR``/``TEMP``/``TMP`` auf :func:`pfad`; ``{}``, wenn nicht anlegbar."""
    try:
        ordner = str(pfad())
    except OSError as fehler:
        log.warning("Temp-Ordner nicht anlegbar (%s) — System-Temp bleibt", fehler)
        return {}
    return {"TMPDIR": ordner, "TEMP": ordner, "TMP": ordner}


def _muster() -> list[str]:
    umg = os.environ.get("TO_SPAWN_TEMP_RESTE")  # Test-Tür; "" = kein Altbestand
    roh = umg.split(os.pathsep) if umg is not None else config.maschine().get("temp_reste")
    reste = roh if isinstance(roh, list) else vorgaben.TEMP_RESTE
    muster = [os.path.join(str(pfad()), "*")]
    muster += [os.path.expanduser(m) for m in reste if isinstance(m, str) and m.strip()]
    return list(dict.fromkeys(muster))


def reste() -> list[tuple[Path, int]]:
    """Kandidaten (Ordner, Ticket-Nummer) unter allen Mustern, ohne Such-Eltern, sortiert."""
    muster = _muster()
    eltern = {os.path.abspath(t) for m in muster for t in glob.glob(os.path.dirname(m))}
    gefunden: dict[Path, int] = {}
    for m in muster:
        for treffer in sorted(glob.glob(m)):
            if os.path.abspath(treffer) in eltern:
                continue  # Such-Eltern (``claude-1000``) sind nie selbst ein Rest
            p = Path(os.path.abspath(treffer))
            treffer_name = _REST_NAME.match(p.name)
            if treffer_name and p.is_dir() and not p.is_symlink():
                gefunden.setdefault(p, int(treffer_name.group(1)))
    return sorted(gefunden.items())


def _juengste_aenderung(ordner: Path) -> float:
    # ponytail: nur Ordner + oberste Ebene (2-GB-Bäume nicht ganz ablaufen); laufende
    # Schreiber tiefer unten fängt die Prozess-Regel.
    zeiten = [ordner.stat().st_mtime]
    with os.scandir(ordner) as eintraege:
        for e in eintraege:
            try:
                zeiten.append(e.stat(follow_symlinks=False).st_mtime)
            except OSError:
                continue
    return max(zeiten)


def aufraeumen(*, loeschen: bool, jetzt: float | None = None) -> list[tuple[Path, str]]:
    """Ein Hausmeister-Schritt über alle Temp-Reste; Rückgabe (Ordner, Grund) je Rest.

    ``loeschen=False``: nur entscheiden. Gelöscht wird nur, was wegwerfbar ist (Regeln
    im Modul-Kommentar), mit Log-Zeile im Hausmeister-Log vorher („ohne Log kein
    Löschen“). Wirft nie.
    """
    jetzt = time.time() if jetzt is None else jetzt
    try:
        kandidaten = reste()
    except OSError as fehler:
        log.warning("Temp-Reste: Suche gescheitert (%s)", fehler)
        return []
    ergebnis: list[tuple[Path, str]] = []
    zu: dict[int, bool] = {}  # ein gh-Aufruf je Ticket, nicht je Rest (Hunderte je Ticket)
    for ordner, ticket in kandidaten:
        grund = _entscheiden(ordner, ticket, jetzt, zu)
        if grund is None:
            grund = f"Temp-Rest von Ticket #{ticket} (zu, alt, kein Prozess)"
            if loeschen:
                grund = _weg(ordner, ticket, grund)
        ergebnis.append((ordner, grund))
    return ergebnis


def _entscheiden(ordner: Path, ticket: int, jetzt: float, zu: dict[int, bool]) -> str | None:
    """``None`` = wegwerfbar, sonst der Grund, warum der Rest bleibt."""
    from . import hausmeister as hm  # spät: hausmeister ruft dieses Modul

    try:
        if jetzt - _juengste_aenderung(ordner) < ALTER_S:
            return "bleibt: frisch"
        if ticket not in zu:
            daten = hm._ticket_daten(ticket)
            zu[ticket] = isinstance(daten, dict) and str(daten.get("state", "")).upper() == "CLOSED"
        # ponytail: Nummer gilt im Ticket-Repo der Maschine; Reste aus anderen Repos
        # schützen Alter + Prozess-Regel. Upgrade: Repo-Kürzel im Rest-Namen.
        if not zu[ticket]:
            return "bleibt: Ticket offen oder unbekannt"
        hm._kein_prozess(ordner)
    except hm._Bleibt as b:
        return f"bleibt: {b.grund}"
    except (OSError, RuntimeError) as fehler:
        return f"bleibt: Fehler {type(fehler).__name__}: {fehler}"
    return None


def _weg(ordner: Path, ticket: int, grund: str) -> str:
    """Gegenprobe, Log-Zeile, dann der Lösch-Weg des Hausmeisters (``shutil.rmtree``)."""
    from . import hausmeister as hm

    try:
        hm._kein_prozess(ordner, probe=True)
    except hm._Bleibt as b:
        return f"bleibt: {b.grund}"
    if not hm._log_schreiben(hm.Ergebnis(ordner, "gelöscht", grund, ticket, trocken=False)):
        return "bleibt: Hausmeister-Log nicht schreibbar — ohne Log kein Löschen"
    try:
        hm._loeschen(ordner, False)
    except (RuntimeError, OSError) as fehler:
        log.warning("Temp-Rest %s: Löschen gescheitert (%s)", ordner, fehler)
        return f"bleibt: Löschen gescheitert: {fehler}"
    log.info("Temp-Rest gelöscht: %s (#%s)", ordner, ticket)
    return grund
