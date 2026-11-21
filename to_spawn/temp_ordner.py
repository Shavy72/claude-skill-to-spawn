"""Temp-Ordner des Bau-Systems (#136): wo Temp liegt, was uns gehört, wann es weg darf.

Warum: der System-Temp ist auf dem Bau-Server eine RAM-Platte (tmpfs). Testläufe und alte
Ticket-Kopien füllten sie am 21.11.2026 bis „No space left on device“. Darum bekommen
Bau-Sessions, Aufseher und die Prüf-Tür ``TMPDIR``/``TEMP``/``TMP`` aus genau einer Stelle
(:func:`umgebung`); pytest legt seinen ``--basetemp`` von selbst unter ``TMPDIR`` an.

Schnittstelle:

- :func:`pfad` — die Temp-Wurzel auf der Platte (``TO_SPAWN_TEMP_ORDNER``, sonst
  Maschinen-Liste ``temp_ordner``, Rückfall ``config.MASCHINE_DEFAULTS``), wird angelegt. Nie
  ``/``, das Home oder ein Ordner darüber, auch nicht über einen Symlink (sonst Rückfall mit
  Warnung). Länger als :data:`MAX_LAENGE` Zeichen gibt eine Warnung: tmux-/Unix-Sockets unter
  pytest-``tmp_path`` brauchen < 108 Zeichen, lange Temp-Pfade lassen solche Tests scheitern.
- :func:`umgebung` — die drei Variablen für ``os.environ``/``subprocess``; ``{}``, wenn
  der Ordner nicht anlegbar ist (dann bleibt der System-Temp, nie ein Start-Abbruch).
- :func:`anlegen` — ein neuer Temp-Ordner für ein Ticket unter der Wurzel, mit Marker-Datei
  :data:`MARKER` (Repo, Ticket, Anlege-Zeit, erzeugt von). Nur so entsteht ein Ordner, den
  :func:`aufraeumen` je anfasst.
- :func:`aufraeumen` — ein Hausmeister-Schritt über die markierten Ordner. Prüfen, Log und
  Löschen nimmt es vom Hausmeister (:mod:`to_spawn.hausmeister`), damit es nur eine
  Lösch-Stelle gibt.

**Geräumt wird nur, was der Skill selbst angelegt und gekennzeichnet hat.** Der Marker ist der
einzige Beweis für „gehört uns“; Ordnernamen zählen nie. Ein Ordner ist wegwerfbar, wenn alles
gilt (sonst bleibt er, Grund im Ergebnis; im Zweifel bleibt er immer):

1. direkter Unterordner der Temp-Wurzel, echter Ordner (kein Symlink), eigener Besitzer (UID),
   aufgelöster Pfad liegt direkt unter der aufgelösten Wurzel;
2. gültiger Marker: echte Datei (kein Symlink), eigener Besitzer, JSON mit Ticket-Nummer und
   Repo = Ticket-Repo der Maschine (fremdes Repo, kaputter oder fehlender Marker ⇒ bleibt);
3. Ticket nachweislich zu (jeder Fehler ⇒ bleibt); kein Prozess darin;
4. **nichts Frisches oder Ungesichertes im Baum** (:func:`_baum_pruefen`, die EINE Stelle
   dieser Regel, ein Lauf über den ganzen Baum): nichts jünger als :data:`ALTER_S` (jede
   Ebene, ``.git``-Inneres ausgenommen), kein Socket/FIFO (lebender Server), in jedem
   Git-Repo keine Commits auf keinem Remote (Zweige, HEAD, Stash und Reflog), keine
   geänderten oder neuen nicht ignorierten Dateien, kein Repo ohne Arbeitsbaum.

ponytail: Reste ohne Marker (alter Bestand im System-Temp, ``claude-*``) räumt dieses Modul
nie; Upgrade: Liste mit Davids Freigabe, wie ``altbestand_festhalten``.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
import time
from pathlib import Path

from . import aufpasser, config

log = logging.getLogger("to_spawn.temp_ordner")

#: Nichts Frisches: jüngste Änderung im ganzen Baum (ohne ``.git``-Inneres) mindestens so alt.
ALTER_S = 24 * 3600
#: Längster Temp-Ordner ohne Warnung (Vorgabe auf dem Bau-Server: 17 Zeichen).
MAX_LAENGE = 24
#: Marker-Datei in jedem von :func:`anlegen` erzeugten Ordner — der Beweis „gehört uns“.
MARKER = ".to-spawn-temp.json"
#: Größter Marker, der gelesen wird (mehr ist kein Marker von uns).
_MARKER_MAX = 4096


def _gefaehrlich(ordner: Path) -> bool:
    """``/``, das Home oder ein Ordner darüber (aufgelöst): dort wäre jeder Unterordner Rest."""
    echt = Path(os.path.realpath(ordner))
    heim = Path(os.path.realpath(Path("~").expanduser()))
    return echt == Path(echt.anchor) or echt == heim or echt in heim.parents


def pfad() -> Path:
    """Temp-Wurzel auf der Platte (angelegt); ``OSError`` geht an den Aufrufer."""
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


def anlegen(ticket: int, *, repo: str | None = None, von: str = "to_spawn") -> Path:
    """Neuer Ordner ``t<ticket>-…`` unter :func:`pfad` mit Marker; nur ihn räumt der Hausmeister.

    ``repo`` = Ticket-Repo (Vorgabe: das Ticket-Repo der Maschine), ``von`` = Erzeuger (für
    den Menschen, der den Ordner findet). ``ValueError`` bei ungültiger Nummer oder ohne
    Ticket-Repo, ``OSError`` geht an den Aufrufer.
    """
    from . import hausmeister as hm  # spät: hausmeister ruft dieses Modul

    if isinstance(ticket, bool) or not isinstance(ticket, int) or ticket <= 0:
        raise ValueError(f"Ticket-Nummer ungültig: {ticket!r}")
    repo = (repo or hm._ticket_repo() or "").strip()
    if not repo:
        raise ValueError(
            "kein eindeutiges Ticket-Repo — Temp-Ordner ohne Marker gäbe es nie"
        )
    ordner = Path(tempfile.mkdtemp(prefix=f"t{ticket}-", dir=pfad()))
    inhalt = {"repo": repo, "ticket": ticket, "angelegt": time.time(), "von": von}
    fd = os.open(
        ordner / MARKER,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as datei:
        json.dump(inhalt, datei)
    return ordner


def _markierte(wurzel: Path) -> list[Path]:
    """Direkte Unterordner der Wurzel, in denen ein Marker liegt (gültig oder nicht), sortiert."""
    with os.scandir(wurzel) as it:
        return sorted(
            Path(e.path) for e in it if os.path.lexists(os.path.join(e.path, MARKER))
        )


def _marker_ticket(ordner: Path, wurzel: Path) -> int:
    """Regeln 1+2: Ticket-Nummer aus dem gültigen Marker, sonst ``_Bleibt`` mit Grund."""
    from . import hausmeister as hm

    uid = os.getuid() if hasattr(os, "getuid") else None
    info = os.lstat(ordner)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise hm._Bleibt("kein echter Ordner (Symlink)")
    if uid is not None and info.st_uid != uid:
        raise hm._Bleibt("fremder Besitzer")
    if Path(os.path.realpath(ordner)).parent != wurzel:
        raise hm._Bleibt("liegt aufgelöst nicht unter der Temp-Wurzel")
    marker = ordner / MARKER
    if os.path.islink(marker):
        raise hm._Bleibt("Marker ist ein Symlink")
    fd = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as datei:
        m = os.fstat(datei.fileno())
        if not stat.S_ISREG(m.st_mode) or (uid is not None and m.st_uid != uid):
            raise hm._Bleibt("Marker keine eigene Datei")
        roh = datei.read(_MARKER_MAX + 1)
    try:
        daten = json.loads(roh) if len(roh) <= _MARKER_MAX else None
    except ValueError:
        daten = None
    ticket = daten.get("ticket") if isinstance(daten, dict) else None
    if isinstance(ticket, bool) or not isinstance(ticket, int) or ticket <= 0:
        raise hm._Bleibt("Marker ungültig")
    repo = daten.get("repo")
    eigen = (hm._ticket_repo() or "").strip().lower()
    if not isinstance(repo, str) or not eigen or repo.strip().lower() != eigen:
        raise hm._Bleibt(f"fremdes Repo im Marker ({repo!r})")
    return ticket


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
        eigen = {MARKER} if rel == "." else set()  # der Marker selbst ist keine Arbeit
        if [p for xy, p in hm._status(repo) if xy != "!!" and p not in eigen]:
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
    """Ein Hausmeister-Schritt über alle markierten Ordner; Rückgabe (Ordner, Grund) je Ordner.

    ``loeschen=False``: nur entscheiden. Gelöscht wird nur, was wegwerfbar ist (Regeln
    im Modul-Kommentar), mit Log-Zeile im Hausmeister-Log vorher („ohne Log kein
    Löschen“). Ordner ohne Marker tauchen nicht auf. Wirft nie.
    """
    jetzt = time.time() if jetzt is None else jetzt
    try:
        wurzel = Path(os.path.realpath(pfad()))
        kandidaten = _markierte(wurzel)
    except OSError as fehler:
        log.warning("Temp-Reste: Suche gescheitert (%s)", fehler)
        return []
    ergebnis: list[tuple[Path, str]] = []
    zu: dict[int, bool] = {}  # ein gh-Aufruf je Ticket, nicht je Ordner
    for ordner in kandidaten:
        ticket, grund = _entscheiden(ordner, wurzel, jetzt, zu)
        if grund is None:
            grund = f"Temp-Rest von Ticket #{ticket} (zu, alt, kein Prozess, nichts Ungesichertes)"
            if loeschen:
                grund = _weg(ordner, ticket, grund, jetzt)
        ergebnis.append((ordner, grund))
    return ergebnis


def _entscheiden(
    ordner: Path, wurzel: Path, jetzt: float, zu: dict[int, bool]
) -> tuple[int, str | None]:
    """(Ticket, ``None``) = wegwerfbar, sonst (Ticket oder 0, Grund, warum er bleibt)."""
    from . import hausmeister as hm  # spät: hausmeister ruft dieses Modul

    ticket = 0
    try:
        ticket = _marker_ticket(ordner, wurzel)
        if ticket not in zu:  # Marker-Repo = Ticket-Repo der Maschine (geprüft)
            daten = hm._ticket_daten(ticket)
            zu[ticket] = (
                isinstance(daten, dict)
                and str(daten.get("state", "")).upper() == "CLOSED"
            )
        if not zu[ticket]:
            return ticket, "bleibt: Ticket offen oder unbekannt"
        hm._kein_prozess(ordner)
        _baum_pruefen(ordner, jetzt)
    except hm._Bleibt as b:
        return ticket, f"bleibt: {b.grund}"
    except (OSError, RuntimeError, ValueError) as fehler:
        return ticket, f"bleibt: Fehler {type(fehler).__name__}: {fehler}"
    return ticket, None


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
