"""Platte hält sich selbst sauber (#1184): fertige Worktrees/Klone weg, Bau-Müll weg.

Eine Prüfung für PC (Windows) und Bau-Server (Linux). Ein Ordner darf nur weg, wenn
nichts verloren gehen kann: Ticket geschlossen, ``git status`` leer, jeder lokale
Commit liegt auf ``origin``, und kein Prozess/tmux-Fenster arbeitet darin. Fehlt eine
Messung, bleibt der Ordner — lieber eine Zeile in ``~/braucht-blick.txt`` als ein
verlorener Stand.

* :func:`darf_weg` — nur prüfen, (Urteil, deutscher Grund).
* :func:`nach_ende` — nach dem Ende einer Bau-Session (Aufpasser): prüfen, dann
  ``git worktree remove`` (ohne ``--force``) bzw. bei eigenständigem Klon ``rmtree``
  mit Pfad-Absicherung. Gelöschtes steht in ``~/geloescht-<JJJJ-MM-TT>.txt``.
* :func:`taeglich` — Timer ``nest/bau-aufraeumen.timer`` und Platten-Sperre in
  ``speicher.zulassen``: alle Kandidaten durchgehen, Ticket-Zustand per ``gh``; auf
  Linux zusätzlich Docker-Reste (nie Volumes, nie laufende Container), alte
  Test-Ordner unter ``bau_schutz.tmpdir`` und das User-Journal.

Keine Mail, keine Benachrichtigung — nur die zwei Log-Dateien und ``logging``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from to_spawn import config, gh

log = logging.getLogger("to_spawn.aufraeumen")

#: Test-Ordner unter ``bau_schutz.tmpdir`` älter als das fliegen raus (Linux).
TMP_ALTER_S = 2 * 24 * 3600

#: Obergrenze des User-Journals (``journalctl --user --vacuum-size``).
JOURNAL_GRENZE = "200M"

#: Windows-Wurzel der Worktrees (``config.worktree_pfad``).
WINDOWS_WT_WURZEL = Path("C:/dev")

#: Nummer aus dem Branch: ``ticket/123-name``, ``fix/1184-zulassung``.
_NR_BRANCH = re.compile(r"^[\w.-]+/(\d+)(?:-|$)")
#: Nummer aus dem Ordnernamen: ``wt-123``, ``wt-ts1184``, ``duoplus-832``.
_NR_ORDNER = re.compile(r"^(?:wt|duoplus)-[a-z]*?(\d+)(?:-|$)")


def _git(args: list[str], pfad: Path) -> tuple[int, str]:
    return gh._git_ausgabe(args, pfad)


def _unter(kind: Path, wurzel: Path) -> bool:
    try:
        kind.relative_to(wurzel)
    except ValueError:
        return False
    return True


def _pfad_text(text: str) -> Path | None:
    text = text.strip()
    if not text:
        return None
    try:
        return Path(text).resolve()
    except (OSError, RuntimeError):
        return None


def _arbeitet_darin(pfad: Path) -> tuple[bool | None, str]:
    """(arbeitet ein Prozess/Fenster in ``pfad``?, Detail). ``None`` = nicht prüfbar."""
    ziel = pfad.resolve()
    if sys.platform == "linux":
        for eintrag in Path("/proc").glob("[0-9]*"):
            try:
                cwd = Path(os.readlink(eintrag / "cwd"))
            except OSError:
                continue  # fremder Nutzer oder Prozess schon weg
            if _unter(cwd, ziel):
                return True, f"Prozess {eintrag.name} arbeitet darin"
        try:
            fertig = subprocess.run(
                ["tmux", "list-panes", "-a", "-F", "#{pane_current_path}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except OSError:
            return False, "kein Prozess darin (tmux fehlt)"
        for zeile in (fertig.stdout or "").splitlines():
            ort = _pfad_text(zeile)
            if ort is not None and _unter(ort, ziel):
                return True, "tmux-Fenster steht darin"
        return False, "kein Prozess/tmux-Fenster darin"
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return None, "Prozess-Prüfung nicht möglich (kein psutil), nur git geprüft"
    for proc in psutil.process_iter():
        try:
            ort = _pfad_text(proc.cwd())
        except (psutil.Error, OSError):
            continue
        if ort is not None and _unter(ort, ziel):
            return True, f"Prozess {proc.pid} arbeitet darin"
    return False, "kein Prozess darin (psutil)"


def _ist_worktree(pfad: Path) -> bool | None:
    """True = verknüpfter Worktree, False = eigenständiger Klon, None = kein Repo-Kopf."""
    code, oben = _git(["rev-parse", "--show-toplevel"], pfad)
    if code != 0 or _pfad_text(oben) != pfad.resolve():
        return None
    c1, git_dir = _git(["rev-parse", "--path-format=absolute", "--git-dir"], pfad)
    c2, common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], pfad)
    if c1 != 0 or c2 != 0:
        return None
    return _pfad_text(git_dir) != _pfad_text(common)


def darf_weg(pfad: Path, *, geschlossen: bool) -> tuple[bool, str]:
    """(darf ``pfad`` gelöscht werden?, Grund) — ohne irgendetwas zu ändern.

    Ja nur, wenn alles stimmt: Ticket ``geschlossen``, ``git status --porcelain`` leer,
    kein Commit fehlt auf ``origin`` (Worktree: ``HEAD``; Klon: alle Branches, ``HEAD``
    und Stash — sie gehen mit dem Ordner verloren) und kein Prozess/tmux-Fenster
    arbeitet darin. Jeder git-Fehler heißt nein.
    """
    if not geschlossen:
        return False, "Ticket noch offen"
    if not pfad.is_dir():
        return False, "Ordner fehlt"
    art = _ist_worktree(pfad)
    if art is None:
        return False, "kein git-Repo-Kopf (git rev-parse scheitert oder Unterordner)"
    code, aenderungen = _git(["status", "--porcelain"], pfad)
    if code != 0:
        return False, "git status scheitert"
    if aenderungen:
        return False, f"ungespeicherte Änderungen: {aenderungen.splitlines()[0].strip()}"
    pruefungen = [["rev-list", "HEAD", "--not", "--remotes"]]
    if not art:
        pruefungen.append(["log", "--branches", "--not", "--remotes", "--format=%h"])
    for args in pruefungen:
        code, fehlt = _git(args, pfad)
        if code != 0:
            return False, f"git {args[0]} scheitert (kein origin?)"
        if fehlt:
            return False, f"{len(fehlt.splitlines())} Commit(s) nicht auf origin"
    if not art:
        code, stash = _git(["stash", "list"], pfad)
        if code != 0 or stash:
            return False, "Stash vorhanden oder nicht lesbar"
        code, liste = _git(["worktree", "list", "--porcelain"], pfad)
        if code != 0 or liste.count("worktree ") > 1:
            return False, "Klon hat noch verknüpfte Worktrees"
    besetzt, detail = _arbeitet_darin(pfad)
    if besetzt:
        return False, detail
    return True, f"geschlossen, sauber, alles auf origin; {detail}"


def _zeile(datei: Path, text: str) -> None:
    zeit = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with datei.open("a", encoding="utf-8") as griff:
            griff.write(f"{zeit}\t{text}\n")
    except OSError as fehler:
        log.warning("Log %s nicht schreibbar (%s): %s", datei, fehler, text)


def _geloescht(home: Path, pfad: Path, grund: str) -> None:
    _zeile(home / f"geloescht-{time.strftime('%Y-%m-%d')}.txt", f"{pfad}\t{grund}")
    log.info("aufgeräumt: %s (%s)", pfad, grund)


def _blick(home: Path, pfad: Path, grund: str) -> None:
    # ponytail: Zeile je Lauf, auch doppelt; Entdoppeln erst, wenn die Datei stört.
    _zeile(home / "braucht-blick.txt", f"{pfad}\t{grund}")
    log.info("bleibt: %s (%s)", pfad, grund)


def _erlaubte_wurzeln(home: Path) -> list[Path]:
    wurzeln = [home.resolve()]
    if sys.platform == "win32":
        wurzeln.append(WINDOWS_WT_WURZEL.resolve())
    return wurzeln


def _loesch_sperre(pfad: Path, home: Path) -> str | None:
    """Grund, warum ``rmtree(pfad)`` verboten ist — ``None`` = erlaubt."""
    if not pfad.is_absolute():
        return "Pfad nicht absolut"
    if pfad.is_symlink():
        return "Pfad ist ein Link"
    echt = pfad.resolve()
    heim = home.resolve()
    if not echt.is_dir():
        return "Ordner fehlt"
    if echt in (heim, Path(echt.anchor), heim / ".claude") or _unter(echt, heim / ".claude"):
        return "geschützter Ordner (Home, Wurzel oder ~/.claude)"
    if not any(echt != w and _unter(echt, w) for w in _erlaubte_wurzeln(home)):
        return "liegt nicht unter Home bzw. C:/dev"
    return None


def _schreibbar_machen(funktion: Callable[..., Any], ort: str, _fehler: Any) -> None:
    os.chmod(ort, stat.S_IWRITE)  # git-Packdateien sind unter Windows schreibgeschützt
    funktion(ort)


def _rmtree(pfad: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(pfad, onexc=_schreibbar_machen)
    else:  # pragma: no cover - Server-Python älter
        shutil.rmtree(pfad, onerror=_schreibbar_machen)


def _entfernen(pfad: Path, home: Path) -> str | None:
    """Ordner wirklich entfernen; Rückgabe = Fehlergrund oder ``None``."""
    if _ist_worktree(pfad):
        code, common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], pfad)
        haupt = _pfad_text(common) if code == 0 else None
        if haupt is None:
            return "Haupt-Repo nicht gefunden"
        code, _ = _git(["worktree", "remove", str(pfad)], haupt)
        if code != 0 or pfad.exists():
            return "git worktree remove scheitert"
        _git(["worktree", "prune"], haupt)
        return None
    sperre = _loesch_sperre(pfad, home)
    if sperre:
        return sperre
    try:
        _rmtree(pfad.resolve())
    except OSError as fehler:
        return f"Löschen scheitert: {fehler}"
    return None


def nach_ende(pfad: Path, *, geschlossen: bool, home: Path | None = None, trocken: bool = False) -> bool:
    """Nach dem Ende einer Bau-Session: ``pfad`` entfernen, wenn :func:`darf_weg` ja sagt.

    True = entfernt (bzw. bei ``trocken`` = würde entfernt). Nein → Zeile in
    ``~/braucht-blick.txt``. ``home`` = Ordner der Log-Dateien und Lösch-Wurzel (Tests).
    """
    heim = home or Path.home()
    ok, grund = darf_weg(pfad, geschlossen=geschlossen)
    if not ok:
        _blick(heim, pfad, grund)
        return False
    if trocken:
        log.info("trocken: würde entfernen %s (%s)", pfad, grund)
        return True
    fehler = _entfernen(pfad, heim)
    if fehler:
        _blick(heim, pfad, fehler)
        return False
    _geloescht(heim, pfad, grund)
    return True


def nummer(pfad: Path) -> int | None:
    """Ticket-/Spec-Nummer aus Branch (``ticket/<nr>-…``) oder Ordner (``wt-<nr>``, ``duoplus-<nr>``)."""
    code, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], pfad)
    treffer = _NR_BRANCH.match(branch) if code == 0 else None
    treffer = treffer or _NR_ORDNER.match(pfad.name)
    return int(treffer.group(1)) if treffer else None


def kandidaten(home: Path) -> list[Path]:
    """Server: ``$BAU_WT_DIR``/``~/wt`` (eine Ebene tiefer bei ``worktree_basis``) + ``~/duoplus-<nr>``;
    PC: ``C:/dev/wt-*``."""
    if sys.platform == "win32":
        return sorted(p for p in WINDOWS_WT_WURZEL.glob("wt-*") if p.is_dir())
    wurzel = Path(os.environ.get("BAU_WT_DIR") or home / "wt").expanduser()
    gefunden: list[Path] = []
    for kind in sorted(wurzel.glob("*")) if wurzel.is_dir() else []:
        if (kind / ".git").exists():
            gefunden.append(kind)
        elif kind.is_dir():
            gefunden.extend(p for p in sorted(kind.glob("wt-*")) if (p / ".git").exists())
    gefunden.extend(p for p in sorted(home.glob("duoplus-*")) if re.fullmatch(r"duoplus-\d+", p.name) and p.is_dir())
    return gefunden


def _geschlossen(pfad: Path, nr: int) -> bool | None:
    repo = gh.repo_aus_origin(pfad)
    if not repo:
        return None
    daten = gh.json_lauf(["issue", "view", str(nr), "--repo", repo, "--json", "state"], cwd=pfad)
    if not isinstance(daten, dict) or "state" not in daten:
        return None
    return str(daten["state"]).upper() == "CLOSED"


def _befehl(args: list[str], trocken: bool) -> None:
    if trocken:
        log.info("trocken: würde ausführen %s", " ".join(args))
        return
    try:
        fertig = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as fehler:
        log.info("%s nicht möglich: %s", args[0], fehler)
        return
    letzte = ((fertig.stdout or "") + (fertig.stderr or "")).strip().splitlines()[-1:] or [""]
    log.info("%s → Exit %s %s", " ".join(args), fertig.returncode, letzte[0])


def _tmp_alt(trocken: bool, jetzt: float) -> int:
    wurzel = Path(str(config.DEFAULTS["bau_schutz"]["tmpdir"]))
    if not wurzel.is_dir() or wurzel.is_symlink():
        return 0
    echt = wurzel.resolve()
    weg = 0
    for kind in echt.iterdir():
        try:
            if kind.is_symlink() or kind.parent != echt or jetzt - kind.lstat().st_mtime < TMP_ALTER_S:
                continue
            if trocken:
                log.info("trocken: würde entfernen %s", kind)
            elif kind.is_dir():
                _rmtree(kind)
            else:
                kind.unlink()
            weg += 1
        except OSError as fehler:
            log.warning("Test-Ordner %s nicht entfernt: %s", kind, fehler)
    return weg


def taeglich(*, trocken: bool = False, home: Path | None = None) -> int:
    """Täglicher Lauf: alle Kandidaten prüfen + entfernen; Rückgabe = Zahl entfernter Ordner.

    Keine Nummer oder ``gh`` unklar → ``~/braucht-blick.txt``. ``trocken`` = nur Log.
    """
    heim = home or Path.home()
    weg = 0
    for pfad in kandidaten(heim):
        nr = nummer(pfad)
        if nr is None:
            _blick(heim, pfad, "keine Ticket-/Spec-Nummer erkennbar")
            continue
        zu = _geschlossen(pfad, nr)
        if zu is None:
            _blick(heim, pfad, f"Zustand von #{nr} nicht abfragbar")
            continue
        weg += nach_ende(pfad, geschlossen=zu, home=heim, trocken=trocken)
    if sys.platform == "linux":
        _befehl(["docker", "image", "prune", "-f"], trocken)
        _befehl(["docker", "builder", "prune", "-f"], trocken)
        log.info("alte Test-Ordner: %s", _tmp_alt(trocken, time.time()))
        _befehl(["journalctl", "--user", f"--vacuum-size={JOURNAL_GRENZE}"], trocken)
    log.info("Aufräumen fertig: %s Ordner %s", weg, "würden weg" if trocken else "entfernt")
    return weg


def cli(trocken: bool) -> int:
    """Unterbefehl ``to_spawn.py aufraeumen [--trocken]``; Exit 0."""
    taeglich(trocken=trocken)
    return 0
