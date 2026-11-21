"""Temp-Ordner des Bau-Systems (#136): wo Temp liegt, was ein Rest ist, wann er weg darf.

Warum: der System-Temp ist auf dem Bau-Server eine RAM-Platte (tmpfs). Testläufe und alte
Ticket-Kopien füllten sie am 21.11.2026 bis „No space left on device“. Darum bekommen
Bau-Sessions, Aufseher und die Prüf-Tür ``TMPDIR``/``TEMP``/``TMP`` aus genau einer Stelle
(:func:`umgebung`); pytest legt seinen ``--basetemp`` von selbst unter ``TMPDIR`` an.

Schnittstelle:

- :func:`pfad` — der Temp-Ordner auf der Platte (``TO_SPAWN_TEMP_ORDNER``, sonst
  Maschinen-Liste ``temp_ordner``, Rückfall ``config.MASCHINE_DEFAULTS``), wird angelegt. Nie
  ``/``, das Home oder ein Ordner darüber (sonst Rückfall mit Warnung). Länger als
  :data:`MAX_LAENGE` Zeichen gibt eine Warnung: tmux-/Unix-Sockets unter pytest-``tmp_path``
  brauchen < 108 Zeichen, lange Temp-Pfade lassen solche Tests scheitern.
- :func:`umgebung` — die drei Variablen für ``os.environ``/``subprocess``; ``{}``, wenn
  der Ordner nicht anlegbar ist (dann bleibt der System-Temp, nie ein Start-Abbruch).
- :func:`aufraeumen` — ein Hausmeister-Schritt: Temp-Reste geschlossener Tickets im
  Temp-Ordner und im Altbestand (``TO_SPAWN_TEMP_RESTE``, sonst Maschinen-Liste
  ``temp_reste``). Prüfen, Log und Löschen nimmt es vom Hausmeister
  (:mod:`to_spawn.hausmeister`), damit es nur eine Lösch-Stelle gibt.

Ein Rest ist wegwerfbar, wenn alles gilt (sonst bleibt er, Grund im Ergebnis; im Zweifel
bleibt er immer):

1. echter Ordner (kein Symlink), eigener Besitzer (UID), Name aus :data:`PRAEFIXE` +
   Ticket-Nummer (``t1335``, ``mut1499``, ``tmp_136``; kein Datum wie ``tmp_2026-11-21``,
   kein ``tmux-1000``/``systemd-…``);
2. Ticket zu; kein Prozess darin;
3. **nichts Frisches oder Ungesichertes im Baum** (:func:`_baum_pruefen`, die EINE Stelle
   dieser Regel, ein Lauf über den ganzen Baum): nichts jünger als :data:`ALTER_S` (jede
   Ebene, ``.git``-Inneres ausgenommen), kein Socket/FIFO (lebender Server), in jedem
   Git-Repo keine Commits auf keinem Remote (Zweige, HEAD, Stash und Reflog), keine
   geänderten oder neuen nicht ignorierten Dateien, kein Repo ohne Arbeitsbaum.

Ordner, unter denen andere Muster suchen (z. B. ``claude-1000`` für ``claude-*/*``), sind
nie selbst ein Rest. Muster mit ``..`` oder mit ``/``/Home als Such-Ordner fallen weg.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import stat
import time
from pathlib import Path

from . import aufpasser, config

log = logging.getLogger("to_spawn.temp_ordner")

#: Nichts Frisches: jüngste Änderung im ganzen Baum (ohne ``.git``-Inneres) mindestens so alt.
ALTER_S = 24 * 3600
#: Längster Temp-Ordner ohne Warnung (Vorgabe auf dem Bau-Server: 17 Zeichen).
MAX_LAENGE = 24
#: Bekannte Präfixe von Ticket-Resten (Text, kein Regex). Alles andere ist kein Rest.
PRAEFIXE: tuple[str, ...] = ("t", "mut", "tmp", "beweis", "dp", "pt-fix")
_REST_NAME = re.compile(
    rf"^(?:{'|'.join(map(re.escape, PRAEFIXE))})[-_]?"
    r"(?!20\d\d(?:[-_]\d|$))([1-9]\d{1,3})(?:[-_].*)?$"
)


def _gefaehrlich(ordner: Path) -> bool:
    """``/``, das Home oder ein Ordner darüber: dort wäre jeder Unterordner ein Rest."""
    heim = Path(os.path.abspath(Path("~").expanduser()))
    return ordner == Path(ordner.anchor) or ordner == heim or ordner in heim.parents


def pfad() -> Path:
    """Temp-Ordner auf der Platte (angelegt); ``OSError`` geht an den Aufrufer."""
    vorgabe = config.MASCHINE_DEFAULTS["temp_ordner"]
    umg = os.environ.get("TO_SPAWN_TEMP_ORDNER", "").strip()  # Test-Tür
    roh = umg or str(config.maschine()["temp_ordner"]).strip() or vorgabe
    ordner = Path(os.path.abspath(Path(roh).expanduser()))
    if _gefaehrlich(ordner):
        log.warning(
            "Temp-Ordner %s abgelehnt (Wurzel oder Home) — Vorgabe gilt", ordner
        )
        ordner = Path(os.path.abspath(Path(vorgabe).expanduser()))
    elif not umg and len(str(ordner)) > MAX_LAENGE:
        log.warning(
            "Temp-Ordner %s ist länger als %s Zeichen — Unix-Sockets darunter scheitern",
            ordner,
            MAX_LAENGE,
        )
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
    reste = (
        umg.split(os.pathsep) if umg is not None else config.maschine()["temp_reste"]
    )
    muster = [os.path.join(str(pfad()), "*")]
    for m in reste:
        if not isinstance(m, str) or not m.strip():
            continue
        roh = os.path.expanduser(m.strip())
        if ".." in Path(roh).parts or _gefaehrlich(
            Path(os.path.abspath(os.path.dirname(roh)))
        ):
            log.warning("Temp-Rest-Muster %s abgelehnt (.. oder Wurzel/Home)", m)
            continue
        muster.append(roh)
    return list(dict.fromkeys(muster))


def reste() -> list[tuple[Path, int]]:
    """Kandidaten (Ordner, Ticket-Nummer) unter allen Mustern, ohne Such-Eltern, sortiert."""
    muster = _muster()
    eltern = {os.path.abspath(t) for m in muster for t in glob.glob(os.path.dirname(m))}
    uid = os.getuid() if hasattr(os, "getuid") else None
    gefunden: dict[Path, int] = {}
    for m in muster:
        for treffer in sorted(glob.glob(m)):
            if os.path.abspath(treffer) in eltern:
                continue  # Such-Eltern (``claude-1000``) sind nie selbst ein Rest
            p = Path(os.path.abspath(treffer))
            treffer_name = _REST_NAME.match(p.name)
            if not treffer_name or p.is_symlink() or not p.is_dir():
                continue
            if uid is not None and p.stat().st_uid != uid:
                continue  # fremder Besitzer: nie unser Rest
            gefunden.setdefault(p, int(treffer_name.group(1)))
    return sorted(gefunden.items())


def _baum_pruefen(ordner: Path, jetzt: float) -> None:
    """Regel 3: ``_Bleibt``, wenn im Baum etwas Frisches, Ungesichertes oder Lebendes liegt.

    Ein Lauf über den ganzen Baum, damit keine Ebene ungeprüft bleibt. Symlinks werden nie
    verfolgt, ``.git`` nie betreten (seine Zeit zählt nicht: ``git status`` schreibt dort den
    Index; frische Commits fängt die Repo-Prüfung). ponytail: in ``pytest-of-*``-Bäumen
    (Wegwerf-Ordner von pytest, pytest löscht sie selbst nach drei Läufen) zählen nur
    Sockets/FIFOs, keine Test-Repos; Upgrade: auch dort Repos prüfen, wenn dort je echte
    Arbeit liegt.
    """
    from . import hausmeister as hm

    if jetzt - ordner.stat(follow_symlinks=False).st_mtime < ALTER_S:
        raise hm._Bleibt("frisch")
    stapel = [(ordner, False)]
    while stapel:
        d, in_pytest = stapel.pop()
        with os.scandir(d) as it:
            eintraege = list(it)
        namen = {e.name for e in eintraege}
        rel = d.relative_to(ordner).as_posix()
        if not in_pytest and ".git" in namen:
            _repo_pruefen(d, rel, (d / ".git").is_file())
        elif not in_pytest and {"HEAD", "objects", "refs"} <= namen:
            raise hm._Bleibt(f"ungesichert: Git-Repo ohne Arbeitsbaum in {rel}")
        for e in eintraege:
            info = e.stat(follow_symlinks=False)
            art = info.st_mode
            if e.name != ".git" and jetzt - info.st_mtime < ALTER_S:
                raise hm._Bleibt(f"frisch ({rel}/{e.name})")
            if stat.S_ISSOCK(art) or stat.S_ISFIFO(art):
                raise hm._Bleibt(
                    f"Socket/FIFO im Baum ({rel}/{e.name}) — ein Server lebt evtl."
                )
            if stat.S_ISDIR(art) and e.name != ".git":
                stapel.append(
                    (Path(e.path), in_pytest or e.name.startswith("pytest-of-"))
                )


def _repo_pruefen(repo: Path, rel: str, ist_worktree: bool) -> None:
    from . import hausmeister as hm

    try:
        if [p for xy, p in hm._status(repo) if xy != "!!"]:
            raise hm._Bleibt(f"ungesichert: geänderte/neue Dateien in {rel}")
        if hm._ungepushte_koepfe(repo, ist_worktree):
            raise hm._Bleibt(f"ungesichert: Commits auf keinem Remote in {rel}")
        # Reflog: verlassene Commits (abgehängter HEAD, alte Stashes) hängen an keinem Ref mehr.
        if aufpasser.git_lauf(
            repo, "rev-list", "-n", "1", "--reflog", "--not", "--remotes=origin"
        ).strip():
            raise hm._Bleibt(f"ungesichert: Commits nur im Reflog in {rel}")
    except RuntimeError as fehler:  # leeres/kaputtes Repo: nicht prüfbar ⇒ bleibt
        raise hm._Bleibt(f"ungesichert: Repo {rel} nicht prüfbar ({fehler})") from None


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
    zu: dict[tuple[int, str], bool] = {}  # ein gh-Aufruf je Ticket, nicht je Rest
    for ordner, ticket in kandidaten:
        grund = _entscheiden(ordner, ticket, jetzt, zu)
        if grund is None:
            grund = f"Temp-Rest von Ticket #{ticket} (zu, alt, kein Prozess, nichts Ungesichertes)"
            if loeschen:
                grund = _weg(ordner, ticket, grund, jetzt)
        ergebnis.append((ordner, grund))
    return ergebnis


def _entscheiden(
    ordner: Path, ticket: int, jetzt: float, zu: dict[tuple[int, str], bool]
) -> str | None:
    """``None`` = wegwerfbar, sonst der Grund, warum der Rest bleibt."""
    from . import hausmeister as hm  # spät: hausmeister ruft dieses Modul

    try:
        # Ist der Rest selbst ein Klon, gilt die Nummer in seinem Repo; sonst im Ticket-Repo
        # der Maschine. ponytail: Reste ohne Git aus anderen Repos schützen Alter, Prozess-
        # und Baum-Regel; Upgrade: Repo-Kürzel im Rest-Namen.
        slug = hm._origin_slug(ordner) if (ordner / ".git").exists() else ""
        if (ticket, slug) not in zu:
            daten = hm._ticket_daten(ticket, slug or None)
            zu[(ticket, slug)] = (
                isinstance(daten, dict)
                and str(daten.get("state", "")).upper() == "CLOSED"
            )
        if not zu[(ticket, slug)]:
            return "bleibt: Ticket offen oder unbekannt"
        hm._kein_prozess(ordner)
        _baum_pruefen(ordner, jetzt)
    except hm._Bleibt as b:
        return f"bleibt: {b.grund}"
    except (OSError, RuntimeError, ValueError) as fehler:
        return f"bleibt: Fehler {type(fehler).__name__}: {fehler}"
    return None


def _weg(ordner: Path, ticket: int, grund: str, jetzt: float) -> str:
    """Gegenprobe (Prozess + Baum), Log-Zeile, dann der Lösch-Weg des Hausmeisters."""
    from . import hausmeister as hm

    try:
        hm._kein_prozess(ordner, probe=True)
        _baum_pruefen(ordner, jetzt)
    except hm._Bleibt as b:
        return f"bleibt: {b.grund}"
    except (OSError, RuntimeError, ValueError) as fehler:
        return f"bleibt: Fehler {type(fehler).__name__}: {fehler}"
    if not hm._log_schreiben(
        hm.Ergebnis(ordner, "gelöscht", grund, ticket, trocken=False)
    ):
        return "bleibt: Hausmeister-Log nicht schreibbar — ohne Log kein Löschen"
    try:
        hm._loeschen(ordner, False)
    except (RuntimeError, OSError) as fehler:
        log.warning("Temp-Rest %s: Löschen gescheitert (%s)", ordner, fehler)
        return f"bleibt: Löschen gescheitert: {fehler}"
    log.info("Temp-Rest gelöscht: %s (#%s)", ordner, ticket)
    return grund
