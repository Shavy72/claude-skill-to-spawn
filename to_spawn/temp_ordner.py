"""Temp-Ordner des Bau-Systems (#136): wo Temp liegt, was uns gehört, wann es weg darf.

Warum: der System-Temp ist auf dem Bau-Server eine RAM-Platte (tmpfs). Testläufe und alte
Ticket-Kopien füllten sie am 21.11.2026 bis „No space left on device“. Darum bekommen
Bau-Sessions, Aufseher und die Prüf-Tür ``TMPDIR``/``TEMP``/``TMP`` aus genau einer Stelle
(:func:`umgebung`); pytest legt seinen ``--basetemp`` von selbst unter ``TMPDIR`` an.
Aufrufer kennen keinen Pfad.

Schnittstelle:

- :func:`pfad` — die Temp-Wurzel auf der Platte (Test-Tür ``TO_SPAWN_TEMP_ORDNER``, sonst
  Maschinen-Liste ``temp_ordner``, Rückfall ``config.MASCHINE_DEFAULTS``), wird angelegt.
  Vorgabe ``~/tmp-bau`` gilt unter Linux und Windows (Home liegt auf der Platte). Nie ``/``,
  das Home oder ein Ordner darüber, auch nicht über einen Symlink (sonst Vorgabe mit Warnung).
  Länger als :data:`MAX_LAENGE` Zeichen gibt eine Warnung: tmux-/Unix-Sockets unter
  pytest-``tmp_path`` brauchen < 108 Zeichen.
- :func:`umgebung` — die drei Variablen für ``os.environ``/``subprocess``; ``{}``, wenn
  der Ordner nicht anlegbar ist (dann bleibt der System-Temp, nie ein Start-Abbruch).
- :func:`anlegen` — ein neuer Temp-Ordner ``t<ticket>-…`` unter :func:`pfad`, mit
  Marker-Datei :data:`MARKER` (Repo, Ticket, Anlege-Zeit, Erzeuger sowie Ordnername,
  ``st_dev`` und ``st_ino`` zur Bindung an genau diesen Ordner).
- :func:`aufraeumen` — ein Hausmeister-Schritt über die markierten Ordner. Prüfen, Log und
  Löschen kommen vom Hausmeister (:mod:`to_spawn.hausmeister`), damit es nur eine
  Lösch-Stelle gibt.

**Geräumt wird nur, was der Skill selbst angelegt und gekennzeichnet hat.** Der Marker ist der
einzige Beweis für „gehört uns“; Ordnernamen zählen nie (Altbestand ohne Marker läuft per
``tmpfs``-Neustart aus, #178). Ein Ordner ist wegwerfbar, wenn alles gilt (sonst bleibt er,
Grund im Ergebnis; im Zweifel bleibt er immer):

1. direkter Unterordner der Temp-Wurzel, echter Ordner (kein Symlink), eigener Besitzer (UID),
   aufgelöster Pfad liegt direkt unter der aufgelösten Wurzel;
2. gültiger Marker: echte reguläre Datei (kein Symlink, ``st_nlink == 1``), eigener Besitzer,
   höchstens :data:`_MARKER_MAX` Bytes, JSON mit gültiger Ticket-Nummer, bekanntem Ticket-Repo
   der Maschine sowie genau passendem Ordnernamen, ``st_dev`` und ``st_ino`` (kopierte Ordner,
   kopierte Marker und Hardlinks werden abgelehnt);
3. Ticket nachweislich geschlossen (:func:`to_spawn.ticket_zustand.endgueltig_zu`); kein
   Prozess arbeitet darin;
4. nichts Frisches oder Ungesichertes im Baum (:func:`_baum_pruefen`): nichts jünger als
   :data:`ALTER_S` (``.git``-Inneres ausgenommen), kein Socket/FIFO, in jedem Git-Repo
   (außer Wegwerf-Testordnern ``pytest-of-*`` und markierten ``pt-*``) keine geänderten/neuen
   Dateien, kein Stash, erreichbares Remote ``origin`` außerhalb des Ordners und jeder lokale
   Kopf sowie Reflog-Stand auf dem Server (``ls-remote``).
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
import time
from pathlib import Path

from . import config, ticket_zustand

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
    """``/``, das Home oder ein Ordner darüber (aufgelöst): kein Temp-Ordner für fremde Dateien."""
    echt = Path(os.path.realpath(ordner))
    heim = Path(os.path.realpath(Path("~").expanduser()))
    return echt == Path(echt.anchor) or echt == heim or echt in heim.parents


def _pfad_ohne_anlegen() -> Path:
    """Temp-Ordner auf der Platte ohne ihn anzulegen (für :func:`pfad` und :func:`aufraeumen`)."""
    vorgabe = str(config.MASCHINE_DEFAULTS["temp_ordner"])
    umg = os.environ.get("TO_SPAWN_TEMP_ORDNER", "").strip()  # Test-Tür
    roh = umg or str(config.maschine().get("temp_ordner") or "").strip() or vorgabe
    ordner = Path(os.path.abspath(Path(roh).expanduser()))
    if _gefaehrlich(ordner):
        log.warning("Temp-Ordner %s abgelehnt (Wurzel oder Home) — Vorgabe gilt", ordner)
        ordner = Path(os.path.abspath(Path(vorgabe).expanduser()))
    elif not umg and len(str(ordner)) > MAX_LAENGE:
        log.warning(
            "Temp-Ordner %s ist länger als %s Zeichen — Unix-Sockets darunter scheitern",
            ordner,
            MAX_LAENGE,
        )
    return ordner


def pfad() -> Path:
    """Temp-Ordner auf der Platte (angelegt); ``OSError`` geht an den Aufrufer."""
    ordner = _pfad_ohne_anlegen()
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
    """Neuer Ordner ``t<ticket>-…`` unter :func:`pfad` mit gebundenem Marker; nur ihn räumt der Hausmeister.

    ``repo`` = Ticket-Repo (Vorgabe: das eindeutige Ticket-Repo der Maschine), ``von`` = Erzeuger.
    Bindet den Marker an Ordnername, ``st_dev`` und ``st_ino`` dieses Ordners. ``ValueError`` bei
    ungültiger Nummer oder fehlendem Ticket-Repo, ``OSError`` geht an den Aufrufer.
    """
    from . import hausmeister as hm  # spät: hausmeister ruft dieses Modul

    if isinstance(ticket, bool) or not isinstance(ticket, int) or ticket <= 0:
        raise ValueError(f"Ticket-Nummer ungültig: {ticket!r}")
    ziel_repo = (repo if repo is not None else (hm._ticket_repo() or "")).strip()
    if not ziel_repo:
        raise ValueError("kein eindeutiges Ticket-Repo — Temp-Ordner ohne Marker gäbe es nie")
    ordner = Path(tempfile.mkdtemp(prefix=f"t{ticket}-", dir=pfad()))
    info = os.lstat(ordner)
    inhalt = {
        "repo": ziel_repo,
        "ticket": ticket,
        "angelegt": time.time(),
        "von": von,
        "ordner": ordner.name,
        "dev": info.st_dev,
        "ino": info.st_ino,
    }
    fd = os.open(
        ordner / MARKER,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as datei:
        json.dump(inhalt, datei)
    return ordner


def _eigene_ticket_repos() -> dict[str, str]:
    """Bekannte Ticket-Repos der Maschine (klein → Originalschreibweise)."""
    from . import hausmeister as hm

    return {wert.lower(): wert for wert in hm._alle_ticket_repos()}


def _markierte(wurzel: Path) -> list[Path]:
    """Direkte Unterordner der Wurzel, in denen ein Marker liegt (gültig oder nicht), sortiert."""
    with os.scandir(wurzel) as it:
        return sorted(Path(e.path) for e in it if os.path.lexists(os.path.join(e.path, MARKER)))


def _marker_pruefen(ordner: Path, wurzel: Path) -> tuple[int, str]:
    """Regeln 1+2: ``(ticket, repo)`` aus dem gültigen, an ``ordner`` gebundenen Marker; sonst ``_Bleibt``."""
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
    m_vor = os.lstat(marker)
    if stat.S_ISLNK(m_vor.st_mode):
        raise hm._Bleibt("Marker ist ein Symlink")
    if not stat.S_ISREG(m_vor.st_mode) or (uid is not None and m_vor.st_uid != uid):
        raise hm._Bleibt("Marker keine eigene Datei")
    fd = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as datei:
        m = os.fstat(datei.fileno())
        if not stat.S_ISREG(m.st_mode) or (uid is not None and m.st_uid != uid):
            raise hm._Bleibt("Marker keine eigene Datei")
        if m.st_dev != m_vor.st_dev or m.st_ino != m_vor.st_ino:
            raise hm._Bleibt("Marker während der Prüfung ersetzt")
        if m.st_nlink != 1:
            raise hm._Bleibt("Marker hat mehrere Hardlinks")
        roh = datei.read(_MARKER_MAX + 1)
    if len(roh) > _MARKER_MAX:
        raise hm._Bleibt("Marker zu groß")
    try:
        daten = json.loads(roh)
    except ValueError:
        raise hm._Bleibt("Marker ungültig") from None
    if not isinstance(daten, dict):
        raise hm._Bleibt("Marker ungültig")
    ticket = daten.get("ticket")
    if isinstance(ticket, bool) or not isinstance(ticket, int) or ticket <= 0:
        raise hm._Bleibt("Marker ungültig")
    ordner_name = daten.get("ordner")
    dev = daten.get("dev")
    ino = daten.get("ino")
    if (
        not isinstance(ordner_name, str)
        or ordner_name != ordner.name
        or isinstance(dev, bool)
        or not isinstance(dev, int)
        or isinstance(ino, bool)
        or not isinstance(ino, int)
        or dev != info.st_dev
        or ino != info.st_ino
    ):
        raise hm._Bleibt("Marker passt nicht zu diesem Ordner (kopiert oder verschoben)")
    repo = daten.get("repo")
    bekannt = _eigene_ticket_repos()
    schluessel = repo.strip().lower() if isinstance(repo, str) else ""
    if not schluessel or schluessel not in bekannt:
        raise hm._Bleibt(f"fremdes Repo im Marker ({repo!r})")
    return ticket, bekannt[schluessel]


def _baum_pruefen(ordner: Path, jetzt: float) -> None:
    """Regel 4: ``_Bleibt``, wenn im Baum etwas Frisches, Ungesichertes oder Lebendes liegt.

    Ein Lauf über den ganzen Baum, damit keine Ebene ungeprüft bleibt. Symlinks werden nie
    verfolgt, ``.git`` nie betreten (seine Zeit zählt nicht: ``git status`` schreibt dort den
    Index; frische Commits fängt die Repo-Prüfung). In Wegwerf-Testordnern (``pytest-of-*`` und
    markierte ``pt-*`` mit ``.pt-lauf``) zählen nur Alter und Sockets/FIFOs, keine Test-Repos.
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
            _repo_pruefen(d, rel, (d / ".git").is_file(), ordner)
        elif not in_pytest and {"HEAD", "objects", "refs"} <= namen:
            raise hm._Bleibt(f"ungesichert: Git-Repo ohne Arbeitsbaum in {rel}")
        for e in eintraege:
            info = e.stat(follow_symlinks=False)
            art = info.st_mode
            if e.name != ".git" and jetzt - info.st_mtime < ALTER_S:
                raise hm._Bleibt(f"frisch ({rel}/{e.name})")
            if stat.S_ISSOCK(art) or stat.S_ISFIFO(art):
                raise hm._Bleibt(f"Socket/FIFO im Baum ({rel}/{e.name}) — ein Server lebt evtl.")
            if stat.S_ISDIR(art) and e.name != ".git":
                ist_pytest = (
                    in_pytest
                    or e.name.startswith("pytest-of-")
                    or (e.name.startswith("pt-") and (Path(e.path) / ".pt-lauf").is_file())
                )
                stapel.append((Path(e.path), ist_pytest))


def _repo_pruefen(repo: Path, rel: str, ist_worktree: bool, wurzel_ordner: Path) -> None:
    """Prüft ein Git-Repo im Temp-Ordner gegen das erreichbare Remote ``origin`` (fail-closed)."""
    from . import hausmeister as hm

    try:
        eigen = {MARKER} if rel == "." else set()  # der Marker selbst ist keine Arbeit
        if [p for xy, p in hm._status(repo) if xy != "!!" and p not in eigen]:
            raise hm._Bleibt(f"ungesichert: geänderte/neue Dateien in {rel}")
        if not ist_worktree and hm._git_oder_none(repo, "rev-parse", "--verify", "-q", "refs/stash"):
            raise hm._Bleibt(f"ungesichert: Stash vorhanden in {rel}")
        url = hm._git_oder_none(repo, "remote", "get-url", "origin")
        if not url:
            raise hm._Bleibt(f"ungesichert: kein Remote origin in {rel}")
        if hm._ist_transport_helfer(url):
            raise hm._Bleibt(f"ungesichert: Transport-Helfer als Remote in {rel}")
        if hm._ziel_im_ordner(wurzel_ordner, url):
            raise hm._Bleibt(f"ungesichert: Remote liegt im Temp-Ordner in {rel}")
        auf_server = hm._server_koepfe(repo)
        if hm._ungepushte_koepfe(repo, ist_worktree, auf_server):
            raise hm._Bleibt(f"ungesichert: Commits auf keinem erreichbaren Remote in {rel}")
        if hm._nur_im_reflog(repo, ist_worktree, auf_server):
            raise hm._Bleibt(f"ungesichert: Commits nur im Reflog in {rel}")
    except RuntimeError as fehler:  # leeres/kaputtes Repo: nicht prüfbar ⇒ bleibt
        raise hm._Bleibt(f"ungesichert: Repo {rel} nicht prüfbar ({fehler})") from None


def aufraeumen(*, loeschen: bool, jetzt: float | None = None) -> list[tuple[Path, str]]:
    """Ein Hausmeister-Schritt über alle markierten Ordner; Rückgabe ``(Ordner, Grund)`` je Ordner.

    ``loeschen=False``: nur entscheiden. Gelöscht wird nur, was wegwerfbar ist (Regeln im
    Modul-Kommentar), mit Log-Zeile im Hausmeister-Log vorher („ohne Log kein Löschen“).
    Ordner ohne Marker tauchen nicht auf. Wirft nie.
    """
    jetzt = time.time() if jetzt is None else jetzt
    try:
        ziel = _pfad_ohne_anlegen()
        if not ziel.is_dir():
            return []
        wurzel = Path(os.path.realpath(ziel))
        kandidaten = _markierte(wurzel)
    except OSError as fehler:
        log.warning("Temp-Reste: Suche gescheitert (%s)", fehler)
        return []
    ergebnis: list[tuple[Path, str]] = []
    zu: dict[tuple[str, int], bool] = {}  # ein gh-Aufruf je (Repo, Ticket), nicht je Ordner
    for ordner in kandidaten:
        ticket, grund = _entscheiden(ordner, wurzel, jetzt, zu)
        if grund is None:
            grund = f"Temp-Rest von Ticket #{ticket} (zu, alt, kein Prozess, nichts Ungesichertes)"
            if loeschen:
                grund = _weg(ordner, wurzel, ticket, grund, jetzt)
        ergebnis.append((ordner, grund))
    return ergebnis


def _entscheiden(
    ordner: Path, wurzel: Path, jetzt: float, zu: dict[tuple[str, int], bool]
) -> tuple[int, str | None]:
    """``(Ticket, None)`` = wegwerfbar, sonst ``(Ticket oder 0, Grund)``."""
    from . import hausmeister as hm  # spät: hausmeister ruft dieses Modul

    ticket = 0
    try:
        ticket, repo = _marker_pruefen(ordner, wurzel)
        schluessel = (repo, ticket)
        if schluessel not in zu:
            daten = hm._ticket_daten(ticket, (repo, ticket))
            zu[schluessel] = isinstance(daten, dict) and ticket_zustand.endgueltig_zu(daten)
        if not zu[schluessel]:
            return ticket, "bleibt: Ticket offen oder unbekannt"
        hm._kein_prozess(ordner)
        _baum_pruefen(ordner, jetzt)
    except hm._Bleibt as b:
        return ticket, f"bleibt: {b.grund}"
    except (OSError, RuntimeError, ValueError) as fehler:
        return ticket, f"bleibt: Fehler {type(fehler).__name__}: {fehler}"
    return ticket, None


def _weg(ordner: Path, wurzel: Path, ticket: int, grund: str, jetzt: float) -> str:
    """Gegenprobe (Marker + Prozess + Baum), Log-Zeile, dann der Lösch-Weg des Hausmeisters."""
    from . import hausmeister as hm

    try:
        _marker_pruefen(ordner, wurzel)
        hm._kein_prozess(ordner, probe=True)
        _baum_pruefen(ordner, jetzt)
    except hm._Bleibt as b:
        return f"bleibt: {b.grund}"
    except (OSError, RuntimeError, ValueError) as fehler:
        return f"bleibt: Fehler {type(fehler).__name__}: {fehler}"
    if not hm._log_schreiben(hm.Ergebnis(ordner, "gelöscht", grund, ticket, trocken=False)):
        return "bleibt: Hausmeister-Log nicht schreibbar — ohne Log kein Löschen"
    try:
        hm.temp_ordner_loeschen(ordner)
    except (RuntimeError, OSError) as fehler:
        log.warning("Temp-Rest %s: Löschen gescheitert (%s)", ordner, fehler)
        meldung = f"Löschen gescheitert: {fehler}"
        hm._log_schreiben(hm.Ergebnis(ordner, "bleibt", meldung, ticket, trocken=False, fehler=True))
        return f"bleibt: {meldung}"
    log.info("Temp-Rest gelöscht: %s (#%s)", ordner, ticket)
    return grund
