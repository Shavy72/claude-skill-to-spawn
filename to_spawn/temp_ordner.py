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
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
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
#: Aktiver Ticket-Temp-Ordner eines zusammengehörigen Laufs (:func:`lauf`, z. B. ``pruef.pruefen``).
_AKTIVER_ORDNER: ContextVar[tuple[Path, int, str] | None] = ContextVar("_AKTIVER_ORDNER", default=None)


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


def _umg_dict(ordner: Path) -> dict[str, str]:
    wert = str(ordner)
    return {"TMPDIR": wert, "TEMP": wert, "TMP": wert}


def _repo_aufloesen(repo: str | None, arbeitsordner: Path | None = None) -> str:
    """Ticket-Repo bestimmen: explizites ``repo`` über die Maschinen-Liste auf ``ticket_repo``
    abbilden, sonst aus ``origin`` von ``arbeitsordner``, sonst das eindeutige Repo der Maschine."""
    from . import hausmeister as hm  # spät: hausmeister ruft dieses Modul

    if repo is not None:
        roh = repo.strip()
        if not roh:
            return ""
        return (hm._ticket_repo_fuer(roh) or roh).strip()
    if arbeitsordner is not None:
        slug = hm._origin_slug(arbeitsordner)
        if slug:
            return (hm._ticket_repo_fuer(slug) or slug).strip()
    return (hm._ticket_repo() or "").strip()


def _ticket_aus_ordner(ordner: Path) -> int | None:
    """Ticket-Nummer aus Ordnername oder ausgechecktem Git-Zweig (Hausmeister-Regeln);
    bei Widerspruch oder Unklarheit ``None``."""
    from . import hausmeister as hm

    aus_name = hm._nummer(ordner.name, hm._name_muster())
    aus_zweig: int | None = None
    if (ordner / ".git").exists():
        zweig = hm._git_oder_none(ordner, "symbolic-ref", "--short", "-q", "HEAD") or ""
        aus_zweig = hm._nummer(zweig, hm._ZWEIG_FORMEN)
    if aus_name is not None and aus_zweig is not None and aus_name != aus_zweig:
        return None
    return aus_name if aus_name is not None else aus_zweig


def ticket_erkennen(
    ordner: Path | None = None, env: Mapping[str, str] | None = None
) -> tuple[int | None, str | None]:
    """Ermittelt ``(ticket, repo)`` aus ``ordner`` und Umgebung (``None`` = unklar).

    Ticket-Nummer: zuerst aus ``ordner`` (Name/Zweig über Hausmeister-Regeln), sonst
    ``TO_SPAWN_TICKET`` oder ``TO_SPAWN_WACHE_SPEC``. Repo: aus ``origin`` von ``ordner``
    (auf ``ticket_repo`` aufgelöst), sonst ``TO_SPAWN_GH_REPO``, sonst eindeutiges Ticket-Repo.
    """
    umg = os.environ if env is None else env
    nr: int | None = _ticket_aus_ordner(ordner) if ordner is not None else None
    if nr is None:
        for name in ("TO_SPAWN_TICKET", "TO_SPAWN_WACHE_SPEC"):
            roh = (umg.get(name) or "").strip()
            if roh.isdigit() and int(roh) > 0:
                nr = int(roh)
                break
    if nr is None:
        return None, None
    ziel_repo = _repo_aufloesen(None, ordner)
    if not ziel_repo:
        gh_repo = (umg.get("TO_SPAWN_GH_REPO") or "").strip()
        if gh_repo:
            ziel_repo = _repo_aufloesen(gh_repo)
    return nr, ziel_repo or None


def _bestehender_ordner(wurzel: Path, ticket: int, ziel_repo: str) -> Path | None:
    """Gibt das aktuelle ``TMPDIR`` zurück, wenn es schon ein gültiger, an sich gebundener
    Temp-Ordner dieses Tickets direkt unter ``wurzel`` ist; sonst ``None``."""
    roh = os.environ.get("TMPDIR", "").strip()
    if not roh:
        return None
    kandidat = Path(roh)
    try:
        info = os.lstat(kandidat)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return None
        if Path(os.path.realpath(kandidat)).parent != wurzel:
            return None
        marker = kandidat / MARKER
        m_vor = os.lstat(marker)
        if stat.S_ISLNK(m_vor.st_mode) or not stat.S_ISREG(m_vor.st_mode):
            return None
        fd = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as datei:
            m = os.fstat(datei.fileno())
            if (
                not stat.S_ISREG(m.st_mode)
                or m.st_dev != m_vor.st_dev
                or m.st_ino != m_vor.st_ino
                or m.st_nlink != 1
            ):
                return None
            daten_roh = datei.read(_MARKER_MAX + 1)
        if len(daten_roh) > _MARKER_MAX:
            return None
        daten = json.loads(daten_roh)
    except (OSError, ValueError):
        return None
    if not isinstance(daten, dict) or daten.get("ticket") != ticket:
        return None
    if (
        daten.get("ordner") != kandidat.name
        or daten.get("dev") != info.st_dev
        or daten.get("ino") != info.st_ino
    ):
        return None
    if ziel_repo:
        m_repo = daten.get("repo")
        if not isinstance(m_repo, str) or m_repo.strip().lower() != ziel_repo.lower():
            return None
    return kandidat


def _ticket_und_repo(
    ticket: int | None, repo: str | None, ordner: Path | None
) -> tuple[int | None, str]:
    """Bestimmt ``(ticket, ticket_repo)`` aus expliziten Argumenten, ``ordner`` und Umgebung."""
    nr = ticket
    ziel_repo = _repo_aufloesen(repo, ordner) if repo is not None else ""
    if nr is None and ordner is not None:
        nr, erkannt_repo = ticket_erkennen(ordner)
        if not ziel_repo and erkannt_repo:
            ziel_repo = erkannt_repo
    elif nr is not None and not ziel_repo:
        ziel_repo = _repo_aufloesen(None, ordner)
        if not ziel_repo:
            gh_repo = os.environ.get("TO_SPAWN_GH_REPO", "").strip()
            if gh_repo:
                ziel_repo = _repo_aufloesen(gh_repo)
    return nr, ziel_repo


def _ziel_fuer_lauf(
    ticket: int | None,
    repo: str | None,
    von: str,
    ordner: Path | None,
    wiederverwenden: bool,
) -> Path:
    basis = pfad()
    if ticket is None and ordner is None:
        return basis
    nr, ziel_repo = _ticket_und_repo(ticket, repo, ordner)
    if nr is None or not ziel_repo:
        return basis
    wurzel = Path(os.path.realpath(basis))
    if wiederverwenden:
        aktiv = _AKTIVER_ORDNER.get()
        if aktiv is not None:
            a_ordner, a_ticket, a_repo = aktiv
            if a_ticket == nr and a_repo.lower() == ziel_repo.lower() and a_ordner.is_dir():
                return a_ordner
        bestehend = _bestehender_ordner(wurzel, nr, ziel_repo)
        if bestehend is not None:
            return bestehend
    return anlegen(nr, repo=ziel_repo, von=von)


def umgebung(
    ticket: int | None = None,
    *,
    repo: str | None = None,
    von: str = "to_spawn",
    ordner: Path | None = None,
    wiederverwenden: bool = False,
) -> dict[str, str]:
    """``TMPDIR``/``TEMP``/``TMP`` für eine Sitzung oder einen Lauf; ``{}``, wenn nicht anlegbar.

    Ohne ``ticket``/``ordner``: Temp-Wurzel (:func:`pfad`). Mit ``ticket`` (Bau, Aufseher)
    oder erkanntem Ticket aus ``ordner``/Umgebung (Prüf-Tür): markierter Unterordner
    ``t<ticket>-…`` über :func:`anlegen` (#177). Mit ``wiederverwenden=True`` gilt ein
    laufender :func:`lauf`-Kontext oder ein schon passend markiertes ``TMPDIR`` weiter.
    """
    try:
        ziel = _ziel_fuer_lauf(ticket, repo, von, ordner, wiederverwenden)
    except ValueError as fehler:
        log.warning("Ticket-Temp-Ordner nicht anlegbar (%s) — Temp-Wurzel bleibt", fehler)
        try:
            return _umg_dict(pfad())
        except OSError as os_fehler:
            log.warning("Temp-Ordner nicht anlegbar (%s) — System-Temp bleibt", os_fehler)
            return {}
    except OSError as fehler:
        log.warning("Temp-Ordner nicht anlegbar (%s) — System-Temp bleibt", fehler)
        return {}
    return _umg_dict(ziel)


@contextmanager
def lauf(
    ordner: Path | None = None,
    *,
    ticket: int | None = None,
    repo: str | None = None,
    von: str = "pruef",
) -> Iterator[Path | None]:
    """Hält einen markierten Ticket-Temp-Ordner für mehrere Unterprozesse eines Laufs fest (#177).

    Nutzt ein schon passend markiertes ``TMPDIR`` weiter, sonst legt genau einen Ordner über
    :func:`anlegen` an, den alle Aufrufe von ``umgebung(..., wiederverwenden=True)`` im Block
    teilen. Scheitert das Anlegen oder ist kein Ticket erkennbar, liefert der Block ``None``.
    """
    token = None
    ziel: Path | None = None
    try:
        basis = pfad()
        nr, ziel_repo = _ticket_und_repo(ticket, repo, ordner)
        if nr is not None and ziel_repo:
            wurzel = Path(os.path.realpath(basis))
            ziel = _bestehender_ordner(wurzel, nr, ziel_repo) or anlegen(nr, repo=ziel_repo, von=von)
            token = _AKTIVER_ORDNER.set((ziel, nr, ziel_repo))
    except (OSError, ValueError) as fehler:
        log.warning("Temp-Lauf-Ordner nicht anlegbar (%s) — Rückfall je Unterprozess", fehler)
    try:
        yield ziel
    finally:
        if token is not None:
            _AKTIVER_ORDNER.reset(token)


def anlegen(ticket: int, *, repo: str | None = None, von: str = "to_spawn") -> Path:
    """Neuer Ordner ``t<ticket>-…`` unter :func:`pfad` mit gebundenem Marker; nur ihn räumt der Hausmeister.

    ``repo`` = Ticket-Repo oder Repo-Slug (wird über die Maschinen-Liste auf sein ``ticket_repo``
    abgebildet; Vorgabe: das eindeutige Ticket-Repo der Maschine), ``von`` = Erzeuger.
    Bindet den Marker an Ordnername, ``st_dev`` und ``st_ino`` dieses Ordners. ``ValueError`` bei
    ungültiger Nummer oder fehlendem Ticket-Repo, ``OSError`` geht an den Aufrufer.
    """
    if isinstance(ticket, bool) or not isinstance(ticket, int) or ticket <= 0:
        raise ValueError(f"Ticket-Nummer ungültig: {ticket!r}")
    ziel_repo = _repo_aufloesen(repo)
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


def _marker_pruefen_voll(ordner: Path, wurzel: Path) -> tuple[int, str, tuple[int, int]]:
    """Regeln 1+2: ``(ticket, repo, (st_dev, st_ino))`` aus dem gültigen, an ``ordner`` gebundenen Marker; sonst ``_Bleibt``."""
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
    return ticket, bekannt[schluessel], (info.st_dev, info.st_ino)


def _marker_pruefen(ordner: Path, wurzel: Path) -> tuple[int, str]:
    """Regeln 1+2: ``(ticket, repo)`` aus dem gültigen, an ``ordner`` gebundenen Marker; sonst ``_Bleibt``."""
    ticket, repo, _ = _marker_pruefen_voll(ordner, wurzel)
    return ticket, repo


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
    from . import aufpasser
    from . import hausmeister as hm  # spät: beide ziehen mittelbar temp_ordner an

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
        _, _, ordner_id = _marker_pruefen_voll(ordner, wurzel)
        hm._kein_prozess(ordner, probe=True)
        _baum_pruefen(ordner, jetzt)
    except hm._Bleibt as b:
        return f"bleibt: {b.grund}"
    except (OSError, RuntimeError, ValueError) as fehler:
        return f"bleibt: Fehler {type(fehler).__name__}: {fehler}"
    if not hm._log_schreiben(hm.Ergebnis(ordner, "gelöscht", grund, ticket, trocken=False)):
        return "bleibt: Hausmeister-Log nicht schreibbar — ohne Log kein Löschen"
    try:
        hm.temp_ordner_loeschen(ordner, erwartet=ordner_id)
    except (RuntimeError, OSError) as fehler:
        log.warning("Temp-Rest %s: Löschen gescheitert (%s)", ordner, fehler)
        meldung = f"Löschen gescheitert: {fehler}"
        hm._log_schreiben(hm.Ergebnis(ordner, "bleibt", meldung, ticket, trocken=False, fehler=True))
        return f"bleibt: {meldung}"
    log.info("Temp-Rest gelöscht: %s (#%s)", ordner, ticket)
    return grund
