"""Hausmeister (#1368): räumt alte Arbeitsordner (Worktrees, Klone) sicher weg.

Eine Tür: :func:`aufraeumen` entscheidet für EINEN Ordner nach allen Lösch-Regeln
und löscht nur mit ``loeschen=True``. Alle Regeln leben hier — Aufrufer (CLI, Cron)
kennen keine davon. Grundsatz: im Zweifel bleibt der Ordner. Die Tür wirft nie;
jeder Fehler ergibt ``art="bleibt"`` mit dem Fehler als Grund und ``fehler=True``
(„bleibt“ aus einer Regel hat ``fehler=False``; die CLI endet nur bei Fehlern ≠ 0).

Reihenfolge (erste zutreffende Regel gewinnt), Vertrag ``docs/verify-hard/1368/API.md``:
Aufräum-Bereich (aufgelöster Pfad, Symlink bleibt) → Hauptrepo → eigenes Repo →
Ticket-Nummer → Ticket zu → 24 h → kein Prozess im Ordner (cwd, exe, offene
Dateien) → Prüfung des Ungesicherten → Sicherung → Gegenprobe → Log → löschen.

Ungesichert heißt: geänderte/neue Dateien oder Commits auf keinem Remote. Vorher
``git fetch --prune origin`` (veraltete Tracking-Refs täuschen „gepusht“ vor), dann
zählt nur ``--remotes=origin``. Beim Klon (``.git`` ist Ordner) zählen alle lokalen
Refs außer ``refs/remotes`` (Zweige, Tags, Notes) + HEAD, denn mit dem Klon sterben
sie alle; ein Stash bleibt. Beim verknüpften Worktree (``.git`` ist Datei) nur HEAD:
die übrigen Refs gehören dem Hauptrepo und überleben das ``worktree remove`` — sie
dort mitzusichern hieße fremde Arbeit als „Sicherung #N“ hochzuladen. Gesichert wird
über :func:`to_spawn.aufpasser.sichern` (ohne ``SICHERUNG_AUSSCHLUSS``).

Dateien, die die Sicherung NICHT mitnimmt, gehen beim Löschen verloren — darum:
- Geheimnisse (``SICHERUNG_AUSSCHLUSS``, z. B. ``.env*``, ``*.key``), geändert, neu
  oder ignoriert: dürfen nur weg, wenn das Hauptrepo des Worktrees dieselbe Datei mit
  gleichem Inhalt hat; sonst (und beim Klon immer) bleibt, ``fuer_david=True``.
  Geheimnisse in ungepushten Commits würde die Sicherung hochladen ⇒ bleibt, David.
- Ignorierte Dateien: nur bekannte Erzeugnisse (:data:`ERZEUGNISSE`) dürfen weg,
  jede andere ignorierte Datei ⇒ bleibt.
- Eingebettete Repos und Submodule (Sicherung sähe nur den Gitlink) ⇒ bleibt.

Prozesse kommen aus ``/proc``; ohne ``/proc`` (Windows) bleibt jeder Ordner —
Laptop-Teil ist Ticket #1373.

Jede Entscheidung schreibt eine JSON-Zeile nach ``TO_SPAWN_HAUSMEISTER_LOG``
(Standard ``~/.claude/to-spawn/hausmeister.jsonl``), beim Löschen VOR dem Löschen;
Log nicht schreibbar ⇒ bleibt. Scheitert danach das Löschen, folgt eine zweite Zeile
„bleibt“. :func:`liste_fuer_david` liest daraus die Ordner, über die David selbst
entscheiden muss.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from to_spawn import aufpasser, gh

log = logging.getLogger("to_spawn.hausmeister")

Art = Literal["gelöscht", "gesichert und gelöscht", "bleibt"]

#: Issues (Tickets) liegen immer hier, auch für Skill-Repo-Ordner.
TICKET_REPO = "Shavy72/duoplus-management"
EIGENE_REPOS = ("shavy72/duoplus-management", "shavy72/claude-skill-to-spawn")
#: Namen der Haupt-Arbeitskopien — nie aufräumen.
HAUPTREPO_NAMEN = ("duoplus-management", "claude-skill-to-spawn")
WURZELN_STANDARD = ("~/wt/*", "~/wt-*", "~/duoplus-[0-9]*", "C:/dev/wt-*")
MINDEST_ZU_SEKUNDEN = 24 * 3600
#: Ignorierte Dateien, die beim Löschen mit weg dürfen (Git-Glob-Pathspecs).
ERZEUGNISSE = (
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.pytest_cache/**",
    "**/.ruff_cache/**",
    "**/.mypy_cache/**",
    "**/.to-spawn/**",
    "**/node_modules/**",
    "**/*.log",
    "**/.coverage",
    "**/htmlcov/**",
)

_NAME_MUSTER = (
    re.compile(r"^wt-(\d+)(?:-.*)?$"),
    re.compile(r"^skill-(\d+)$"),
    re.compile(r"^duoplus-(\d+)$"),
    re.compile(r"^t(\d+)$"),
)
_ZWEIG_MUSTER = re.compile(r"^(?:ticket|fix|wip)/(\d+)(?:-.*)?$")
_GITHUB_SLUG = re.compile(r"github\.com[:/]+([^/\s]+/[^/\s]+?)(?:\.git)?/?$")


@dataclass(frozen=True)
class Ergebnis:
    ordner: Path
    art: Art
    grund: str
    ticket: int | None
    sicherung: str | None = None
    fuer_david: bool = False
    trocken: bool = True
    fehler: bool = False  # True = „bleibt“ wegen eines Fehlers, nicht wegen einer Regel


class _Bleibt(Exception):
    """Eine Regel (oder ein Fehler) hält den Ordner fest."""

    def __init__(
        self, grund: str, *, fuer_david: bool = False, fehler: bool = False
    ) -> None:
        super().__init__(grund)
        self.grund = grund
        self.fuer_david = fuer_david
        self.fehler = fehler


@dataclass
class _Stand:
    ticket: int | None = None
    sicherung: str | None = None


def aufraeumen(
    ordner: Path, *, loeschen: bool = False, jetzt: float | None = None
) -> Ergebnis:
    """Entscheidet (und löscht mit ``loeschen=True``) einen Arbeitsordner; wirft nie."""
    ordner = Path(os.path.abspath(Path(ordner).expanduser()))
    trocken = not loeschen
    stand = _Stand()
    ziel: tuple[Path, bool] | None = None
    try:
        erg, ziel = _regeln(ordner, trocken, jetzt, stand)
    except _Bleibt as b:
        erg = _bleibt(ordner, trocken, stand, b)
    except Exception as fehler:  # Vertrag: wirft nie, im Zweifel bleibt
        log.exception("Hausmeister: Fehler bei %s", ordner)
        erg = _bleibt(ordner, trocken, stand, _Bleibt(f"Fehler: {fehler}", fehler=True))
    if ziel is None:
        _log_schreiben(erg)
        return erg
    if not _log_schreiben(erg):
        erg = replace(
            erg,
            art="bleibt",
            grund="Hausmeister-Log nicht schreibbar — ohne Log kein Löschen",
            fehler=True,
        )
        return erg
    try:
        _loeschen(*ziel)
    except (RuntimeError, OSError) as fehler:
        erg = replace(
            erg, art="bleibt", grund=f"Löschen gescheitert: {fehler}", fehler=True
        )
        _log_schreiben(erg)
    return erg


def liste_fuer_david(log_datei: Path | None = None) -> list[Path]:
    """Ordner aller Log-Zeilen mit ``fuer_david=True``, jeder nur einmal (Reihenfolge des Logs)."""
    datei = log_datei or _log_pfad()
    try:
        zeilen = datei.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    gesehen: dict[Path, None] = {}
    for zeile in zeilen:
        try:
            daten = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(daten, dict) and daten.get("fuer_david") and daten.get("ordner"):
            gesehen.setdefault(Path(daten["ordner"]), None)
    return list(gesehen)


# --- Regeln ---------------------------------------------------------------------


def _bleibt(ordner: Path, trocken: bool, stand: _Stand, b: _Bleibt) -> Ergebnis:
    return Ergebnis(
        ordner,
        "bleibt",
        b.grund,
        stand.ticket,
        stand.sicherung,
        fuer_david=b.fuer_david,
        trocken=trocken,
        fehler=b.fehler,
    )


def _regeln(
    ordner: Path, trocken: bool, jetzt: float | None, stand: _Stand
) -> tuple[Ergebnis, tuple[Path, bool] | None]:
    """Alle Regeln; Rückgabe (Ergebnis, Lösch-Ziel oder None). „bleibt“ = ``_Bleibt``."""
    # 1 Aufräum-Bereich (nur der aufgelöste Pfad zählt), Symlink nie
    if not _im_bereich(ordner):
        raise _Bleibt("nicht im Aufräum-Bereich")
    if ordner.is_symlink():
        raise _Bleibt(f"Symlink (zeigt auf {os.readlink(ordner)})")
    if not ordner.is_dir():
        raise _Bleibt("Ordner fehlt")
    echt = ordner.resolve()

    # 2/3 Git-Ordner? Hauptrepo? Eigenes Repo?
    ist_worktree = _git_ordner_pruefen(echt)

    # 4 Ticket-Nummer aus Name und Zweig
    stand.ticket = ticket = _ticket_nummer(ordner, echt)

    # 5/6 Ticket zu, seit mindestens 24 h
    _ticket_lange_zu(ticket, jetzt)

    # 7 Prozess im Ordner
    _kein_prozess(echt)

    # 8 Ungesichertes prüfen (auch trocken: fetch, Geheimnisse, Ignoriertes)
    koepfe, zu_sichern, status_vorher = _ungesichertes(echt, ist_worktree)
    art: Art = "gesichert und gelöscht" if (koepfe or zu_sichern) else "gelöscht"
    if trocken:
        return Ergebnis(ordner, art, _grund(art, ticket), ticket, trocken=True), None
    if koepfe or zu_sichern:
        try:
            stand.sicherung = aufpasser.sichern(
                echt, ticket, jetzt, zusatz_koepfe=koepfe
            )
        except RuntimeError as fehler:
            raise _Bleibt(f"Sicherung gescheitert: {fehler}", fehler=True) from None
        if stand.sicherung is None:
            raise _Bleibt(
                "Sicherung lieferte nichts, obwohl Ungesichertes erkannt wurde",
                fehler=True,
            )

    # Gegenprobe direkt vor dem Löschen: nichts Neues, alles auf origin, kein Prozess
    _kein_prozess(echt)
    if _status(echt) != status_vorher:
        raise _Bleibt("Ordner hat sich während der Sicherung geändert")
    if _ungepushte_koepfe(echt, ist_worktree):
        raise _Bleibt("nach der Sicherung noch Commits auf keinem Remote", fehler=True)

    # 9 Löschen (erst nach der Log-Zeile, siehe aufraeumen)
    erg = Ergebnis(
        ordner, art, _grund(art, ticket), ticket, stand.sicherung, trocken=False
    )
    return erg, (echt, ist_worktree)


def _git_ordner_pruefen(echt: Path) -> bool:
    """Regeln 2/3; Rückgabe: verknüpfter Worktree (True) oder Klon (False)."""
    if _git_oder_none(echt, "rev-parse", "--is-inside-git-dir") == "true":
        raise _Bleibt("Git-Verwaltungsordner (Hauptrepo)")
    oben = _git_oder_none(echt, "rev-parse", "--show-toplevel")
    if oben is None or Path(oben).resolve() != echt:
        raise _Bleibt("kein eigener Git-Ordner", fuer_david=True)
    ist_worktree = (echt / ".git").is_file()
    if not ist_worktree:
        if echt.name in HAUPTREPO_NAMEN:
            raise _Bleibt("Hauptrepo")
        if _hat_verknuepfte_worktrees(echt):
            raise _Bleibt("Hauptrepo mit verknüpften Worktrees")
    url = _git_oder_none(echt, "config", "--get", "remote.origin.url") or ""
    if _slug(url).lower() not in EIGENE_REPOS:
        raise _Bleibt(f"fremdes Repo ({url or 'ohne origin'})", fuer_david=True)
    return ist_worktree


def _ticket_nummer(ordner: Path, echt: Path) -> int:
    zweig = _git_oder_none(echt, "symbolic-ref", "--short", "-q", "HEAD") or ""
    aus_name = _nummer(ordner.name, _NAME_MUSTER)
    aus_zweig = _nummer(zweig, (_ZWEIG_MUSTER,))
    if aus_name is not None and aus_zweig is not None and aus_name != aus_zweig:
        raise _Bleibt(
            f"Ordnername (#{aus_name}) und Zweig {zweig} (#{aus_zweig}) widersprechen sich",
            fuer_david=True,
        )
    ticket = aus_name if aus_name is not None else aus_zweig
    if ticket is None:
        raise _Bleibt("keine Ticket-Nummer in Ordnername oder Zweig", fuer_david=True)
    return ticket


def _ticket_lange_zu(ticket: int, jetzt: float | None) -> None:
    daten = gh.json_lauf(
        ["issue", "view", str(ticket), "-R", TICKET_REPO, "--json", "state,closedAt"]
    )
    if not isinstance(daten, dict) or "state" not in daten:
        raise _Bleibt(f"Ticket #{ticket} nicht abfragbar", fehler=True)
    if str(daten["state"]).upper() != "CLOSED":
        raise _Bleibt(f"Ticket #{ticket} offen")
    zu_seit = _zeitpunkt(daten.get("closedAt"))
    if zu_seit is None:
        raise _Bleibt(f"Ticket #{ticket}: Schließzeit unbekannt")
    jetzt_ts = jetzt if jetzt is not None else time.time()
    if jetzt_ts - zu_seit < MINDEST_ZU_SEKUNDEN:
        raise _Bleibt(f"Ticket #{ticket} erst seit weniger als 24 h zu")


def _grund(art: Art, ticket: int) -> str:
    if art == "gelöscht":
        return f"Ticket #{ticket} seit über 24 h zu, nichts Ungesichertes"
    return f"Ticket #{ticket} seit über 24 h zu, Ungesichertes auf sicherung/{ticket}"


def _wurzeln() -> list[str]:
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_WURZELN")
    muster = roh.split(os.pathsep) if roh else list(WURZELN_STANDARD)
    return [os.path.expanduser(m) for m in muster if m.strip()]


def _im_bereich(ordner: Path) -> bool:
    try:
        echt = ordner.resolve()
    except OSError:
        return False
    return any(echt.match(m) for m in _wurzeln())


def _nummer(text: str, muster: tuple[re.Pattern[str], ...]) -> int | None:
    for m in muster:
        treffer = m.match(text)
        if treffer:
            return int(treffer.group(1))
    return None


def _slug(url: str) -> str:
    treffer = _GITHUB_SLUG.search(url.strip())
    return treffer.group(1) if treffer else ""


def _zeitpunkt(text: object) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


# --- Git ------------------------------------------------------------------------


def _git_oder_none(cwd: Path, *args: str) -> str | None:
    try:
        return aufpasser.git_lauf(cwd, *args).strip()
    except RuntimeError:
        return None


def _hat_verknuepfte_worktrees(repo: Path) -> bool:
    for zeile in aufpasser.git_lauf(
        repo, "worktree", "list", "--porcelain"
    ).splitlines():
        if zeile.startswith("worktree "):
            pfad = Path(zeile[len("worktree ") :])
            if pfad.is_dir() and pfad.resolve() != repo:
                return True
    return False


def _hauptrepo(worktree: Path) -> Path:
    gemeinsam = Path(
        aufpasser.git_lauf(worktree, "rev-parse", "--git-common-dir").strip()
    )
    if not gemeinsam.is_absolute():
        gemeinsam = (worktree / gemeinsam).resolve()
    return gemeinsam.parent


def _glob(muster: tuple[str, ...]) -> list[str]:
    return [f":(glob){m}" for m in muster]


def _status(repo: Path, *pathspec: str) -> list[tuple[str, str]]:
    """``(XY, Pfad)`` aller geänderten, neuen und ignorierten Dateien (einzeln)."""
    roh = aufpasser.git_lauf(
        repo,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--ignored",
        "--",
        *pathspec,
    )
    teile = roh.split("\0")
    eintraege: list[tuple[str, str]] = []
    i = 0
    while i < len(teile):
        teil = teile[i]
        i += 1
        if not teil:
            continue
        xy = teil[:2]
        eintraege.append((xy, teil[3:]))
        if xy[0] in "RC":
            i += 1  # Herkunftspfad der Umbenennung überspringen
    return eintraege


def _gleiche_kopie(haupt: Path | None, repo: Path, rel: str) -> bool:
    if haupt is None:
        return False
    hier, dort = repo / rel, haupt / rel
    try:
        return (
            hier.is_file() and dort.is_file() and hier.read_bytes() == dort.read_bytes()
        )
    except OSError:
        return False


def _ungesichertes(
    repo: Path, ist_worktree: bool
) -> tuple[list[str], bool, list[tuple[str, str]]]:
    """Regel 8: (ungepushte Köpfe, zu sichernde Dateien da?, Status-Schnappschuss).

    Alles, was die Sicherung nicht retten kann, wirft ``_Bleibt``.
    """
    try:
        aufpasser.git_lauf(repo, "fetch", "--prune", "-q", "origin")
    except RuntimeError as fehler:
        raise _Bleibt(f"git fetch origin gescheitert: {fehler}", fehler=True) from None
    if not ist_worktree and _git_oder_none(
        repo, "rev-parse", "--verify", "-q", "refs/stash"
    ):
        raise _Bleibt("Stash vorhanden (git stash list)", fuer_david=True)
    for zeile in aufpasser.git_lauf(repo, "ls-files", "-s").splitlines():
        if zeile.startswith("160000 "):
            raise _Bleibt(f"Submodul {zeile.split(chr(9), 1)[-1]}", fuer_david=True)

    alle = _status(repo)
    for _, pfad in alle:
        if pfad.endswith("/"):
            raise _Bleibt(f"eingebettetes Repo {pfad}", fuer_david=True)
    geheim = {p for _, p in _status(repo, *_glob(aufpasser.SICHERUNG_AUSSCHLUSS))}
    haupt = _hauptrepo(repo) if ist_worktree else None
    for pfad in sorted(geheim):
        if not _gleiche_kopie(haupt, repo, pfad):
            raise _Bleibt(
                f"Geheimnis-Datei {pfad} nicht gleich im Hauptrepo", fuer_david=True
            )
    erzeugnis = {p for xy, p in _status(repo, *_glob(ERZEUGNISSE)) if xy == "!!"}
    for xy, pfad in alle:
        if xy == "!!" and pfad not in geheim and pfad not in erzeugnis:
            raise _Bleibt(f"ignorierte Datei {pfad} ist kein bekanntes Erzeugnis")
    zu_sichern = any(xy != "!!" and p not in geheim for xy, p in alle)

    koepfe = _ungepushte_koepfe(repo, ist_worktree)
    if koepfe:
        leck = aufpasser.git_lauf(
            repo,
            "log",
            "--format=",
            "--name-only",
            *koepfe,
            "--not",
            "--remotes=origin",
            "--",
            *_glob(aufpasser.SICHERUNG_AUSSCHLUSS),
        ).split()
        if leck:
            raise _Bleibt(
                f"Geheimnis-Datei {leck[0]} in ungepushtem Commit", fuer_david=True
            )
    return koepfe, zu_sichern, alle


def _ungepushte_koepfe(repo: Path, ist_worktree: bool) -> list[str]:
    """Refs mit Commits nicht auf origin (Klon: alle außer refs/remotes + HEAD; Worktree: HEAD)."""
    refs = ["HEAD"]
    if not ist_worktree:
        refs += [
            r
            for r in aufpasser.git_lauf(
                repo, "for-each-ref", "--format=%(refname)"
            ).split()
            if not r.startswith("refs/remotes/")
        ]
    return [
        ref
        for ref in refs
        if aufpasser.git_lauf(
            repo, "rev-list", "-n", "1", ref, "--not", "--remotes=origin"
        ).strip()
    ]


# --- Prozesse -------------------------------------------------------------------


def _kein_prozess(ordner: Path) -> None:
    """Regel 7: ``_Bleibt``, falls ein Prozess im Ordner arbeitet (cwd, exe, offene
    Datei) oder das nicht feststellbar ist. Ohne ``/proc`` (Windows) bleibt jeder
    Ordner — Laptop-Teil Ticket #1373."""
    proc = Path("/proc")
    try:
        pids = [int(p.name) for p in proc.iterdir() if p.name.isdigit()]
    except OSError:
        raise _Bleibt("Prozessliste nicht lesbar", fehler=True) from None

    def drin(ziel: str | Path) -> bool:
        pfad = Path(ziel)
        return pfad == ordner or pfad.is_relative_to(ordner)

    for pid in pids:
        basis = proc / str(pid)
        try:
            stat = (basis / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # Prozess schon weg
        if stat.rsplit(")", 1)[-1].split()[:1] == ["Z"]:
            continue  # Zombie: kein cwd, keine Dateien
        ziele: list[str] = []
        for teil in ("cwd", "exe"):
            ziel = _link_lesen(pid, basis / teil)
            if ziel is not None:
                ziele.append(ziel)
        try:
            fds = list((basis / "fd").iterdir())
        except OSError as fehler:
            fds = []
            _unlesbar_pruefen(pid, basis, fehler)
        for fd in fds:
            try:
                ziele.append(os.readlink(fd))
            except OSError:
                continue  # inzwischen geschlossen
        if any(drin(z) for z in ziele):
            raise _Bleibt(f"Prozess {pid} arbeitet im Ordner oder hat Dateien offen")


def _link_lesen(pid: int, link: Path) -> str | None:
    try:
        return os.readlink(link)
    except OSError as fehler:
        _unlesbar_pruefen(pid, link.parent, fehler)
        return None


def _unlesbar_pruefen(pid: int, basis: Path, fehler: OSError) -> None:
    """Unlesbares /proc-Feld: Prozess weg ⇒ egal; fremder oder nicht-dumpbarer
    Prozess (PermissionError) ⇒ übersprungen; jeder andere Fehler ⇒ bleibt."""
    # ponytail: PermissionError übersprungen (fremde Nutzer, systemd --user, setuid);
    # die arbeiten nicht in Ticket-Ordnern. Upgrade: Prüfung als root.
    if isinstance(fehler, (FileNotFoundError, ProcessLookupError, PermissionError)):
        return
    if basis.exists():
        raise _Bleibt(f"Prozess {pid}: /proc nicht lesbar ({fehler})")


# --- Löschen + Log --------------------------------------------------------------


def _loeschen(ordner: Path, ist_worktree: bool) -> None:
    if ist_worktree:
        hauptrepo = _hauptrepo(ordner)
        aufpasser.git_lauf(hauptrepo, "worktree", "remove", "--force", str(ordner))
        aufpasser.git_lauf(hauptrepo, "worktree", "prune")
    else:
        shutil.rmtree(ordner)


def _log_pfad() -> Path:
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_LOG")
    return (
        Path(roh).expanduser()
        if roh
        else Path.home() / ".claude" / "to-spawn" / "hausmeister.jsonl"
    )


def _log_schreiben(erg: Ergebnis) -> bool:
    """Eine JSON-Zeile anhängen; False = nicht schreibbar."""
    zeile = {
        "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ordner": str(erg.ordner),
        "art": erg.art,
        "grund": erg.grund,
        "ticket": erg.ticket,
        "sicherung": erg.sicherung,
        "trocken": erg.trocken,
        "fuer_david": erg.fuer_david,
        "fehler": erg.fehler,
    }
    log.info("Hausmeister: %s — %s (%s)", erg.ordner, erg.art, erg.grund)
    try:
        datei = _log_pfad()
        datei.parent.mkdir(parents=True, exist_ok=True)
        with datei.open("a", encoding="utf-8") as f:
            f.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    except OSError as fehler:
        log.warning("Hausmeister-Log nicht schreibbar: %s", fehler)
        return False
    return True
