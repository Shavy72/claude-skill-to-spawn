"""Hausmeister (#1368): räumt alte Arbeitsordner (Worktrees, Klone) sicher weg.

Eine Tür: :func:`aufraeumen` entscheidet für EINEN Ordner nach allen Lösch-Regeln
und löscht nur mit ``loeschen=True``. Alle Regeln leben hier — Aufrufer (CLI, Cron)
kennen keine davon. Grundsatz: im Zweifel bleibt der Ordner. Die Tür wirft nie;
jeder Fehler ergibt ``art="bleibt"`` mit dem Fehler als Grund.

Reihenfolge (erste zutreffende Regel gewinnt), Vertrag ``docs/verify-hard/1368/API.md``:
Aufräum-Bereich → Hauptrepo → eigenes Repo → Ticket-Nummer → Ticket zu → 24 h →
kein Prozess im Ordner → Sicherung des Ungesicherten → löschen.

Ungesichert heißt: geänderte/neue Dateien oder Commits auf keinem Remote. Beim Klon
(``.git`` ist Ordner) zählen alle lokalen Zweige + HEAD, denn mit dem Klon sterben
alle seine Zweige. Beim verknüpften Worktree (``.git`` ist Datei) nur HEAD bzw. der
ausgecheckte Zweig: die übrigen Zweige gehören dem Hauptrepo und überleben das
``worktree remove`` — sie dort mitzusichern hieße fremde Arbeit als „Sicherung #N“
hochzuladen. Gesichert wird über :func:`to_spawn.aufpasser.sichern` (ohne ``.env*``).

Jede Entscheidung schreibt genau eine JSON-Zeile nach ``TO_SPAWN_HAUSMEISTER_LOG``
(Standard ``~/.claude/to-spawn/hausmeister.jsonl``); :func:`liste_fuer_david` liest
daraus die Ordner, über die David selbst entscheiden muss.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from to_spawn import aufpasser, gh

log = logging.getLogger("to_spawn.hausmeister")

#: Issues (Tickets) liegen immer hier, auch für Skill-Repo-Ordner.
TICKET_REPO = "Shavy72/duoplus-management"
EIGENE_REPOS = ("shavy72/duoplus-management", "shavy72/claude-skill-to-spawn")
#: Namen der Haupt-Arbeitskopien — nie aufräumen.
HAUPTREPO_NAMEN = ("duoplus-management", "claude-skill-to-spawn")
WURZELN_STANDARD = ("~/wt/*", "~/wt-*", "~/duoplus-[0-9]*", "C:/dev/wt-*")
MINDEST_ZU_SEKUNDEN = 24 * 3600

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
    art: str  # "gelöscht" | "gesichert und gelöscht" | "bleibt"
    grund: str
    ticket: int | None
    sicherung: str | None = None
    fuer_david: bool = False
    trocken: bool = True


def aufraeumen(
    ordner: Path, *, loeschen: bool = False, jetzt: float | None = None
) -> Ergebnis:
    """Entscheidet (und löscht mit ``loeschen=True``) einen Arbeitsordner; wirft nie."""
    ordner = Path(os.path.abspath(Path(ordner).expanduser()))
    trocken = not loeschen
    try:
        erg = _entscheiden(ordner, trocken, jetzt)
    except Exception as fehler:  # Vertrag: wirft nie, im Zweifel bleibt
        log.exception("Hausmeister: Fehler bei %s", ordner)
        erg = Ergebnis(ordner, "bleibt", f"Fehler: {fehler}", None, trocken=trocken)
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


def _entscheiden(ordner: Path, trocken: bool, jetzt: float | None) -> Ergebnis:
    def bleibt(
        grund: str, ticket: int | None = None, fuer_david: bool = False
    ) -> Ergebnis:
        return Ergebnis(
            ordner, "bleibt", grund, ticket, fuer_david=fuer_david, trocken=trocken
        )

    # 1 Aufräum-Bereich
    if not _im_bereich(ordner):
        return bleibt("nicht im Aufräum-Bereich")
    if not ordner.is_dir():
        return bleibt("Ordner fehlt")
    echt = ordner.resolve()

    # 2/3 Git-Ordner? Hauptrepo?
    if _git_oder_none(echt, "rev-parse", "--is-inside-git-dir") == "true":
        return bleibt("Git-Verwaltungsordner (Hauptrepo)")
    oben = _git_oder_none(echt, "rev-parse", "--show-toplevel")
    if oben is None or Path(oben).resolve() != echt:
        return bleibt("kein eigener Git-Ordner", fuer_david=True)
    ist_worktree = (echt / ".git").is_file()
    if not ist_worktree:
        if echt.name in HAUPTREPO_NAMEN:
            return bleibt("Hauptrepo")
        if _hat_verknuepfte_worktrees(echt):
            return bleibt("Hauptrepo mit verknüpften Worktrees")
    url = _git_oder_none(echt, "config", "--get", "remote.origin.url") or ""
    slug = _slug(url)
    if slug.lower() not in EIGENE_REPOS:
        return bleibt(f"fremdes Repo ({url or 'ohne origin'})", fuer_david=True)

    # 4 Ticket-Nummer aus Name und Zweig
    zweig = _git_oder_none(echt, "symbolic-ref", "--short", "-q", "HEAD") or ""
    aus_name = _nummer(ordner.name, _NAME_MUSTER)
    aus_zweig = _nummer(zweig, (_ZWEIG_MUSTER,))
    if aus_name is not None and aus_zweig is not None and aus_name != aus_zweig:
        return bleibt(
            f"Ordnername (#{aus_name}) und Zweig {zweig} (#{aus_zweig}) widersprechen sich",
            fuer_david=True,
        )
    ticket = aus_name if aus_name is not None else aus_zweig
    if ticket is None:
        return bleibt("keine Ticket-Nummer in Ordnername oder Zweig", fuer_david=True)

    # 5/6 Ticket zu, seit mindestens 24 h
    daten = gh.json_lauf(
        ["issue", "view", str(ticket), "-R", TICKET_REPO, "--json", "state,closedAt"]
    )
    if not isinstance(daten, dict) or "state" not in daten:
        return bleibt(f"Ticket #{ticket} nicht abfragbar", ticket)
    if str(daten["state"]).upper() != "CLOSED":
        return bleibt(f"Ticket #{ticket} offen", ticket)
    zu_seit = _zeitpunkt(daten.get("closedAt"))
    if zu_seit is None:
        return bleibt(f"Ticket #{ticket}: Schließzeit unbekannt", ticket)
    jetzt_ts = jetzt if jetzt is not None else time.time()
    if jetzt_ts - zu_seit < MINDEST_ZU_SEKUNDEN:
        return bleibt(f"Ticket #{ticket} erst seit weniger als 24 h zu", ticket)

    # 7 Prozess im Ordner
    prozess = _prozess_im_ordner(echt)
    if prozess:
        return bleibt(prozess, ticket)

    # 8 Sicherung
    koepfe = _ungepushte_koepfe(echt, ist_worktree)
    schmutzig = aufpasser.worktree_schmutzig(echt)
    sicherung: str | None = None
    art = "gesichert und gelöscht" if (koepfe or schmutzig) else "gelöscht"
    if trocken:
        return Ergebnis(ordner, art, _grund(art, ticket), ticket, trocken=True)
    if koepfe or schmutzig:
        try:
            sicherung = aufpasser.sichern(echt, ticket, jetzt, zusatz_koepfe=koepfe)
        except RuntimeError as fehler:
            return bleibt(f"Sicherung gescheitert: {fehler}", ticket)
        if sicherung is None:
            art = "gelöscht"

    # 9 Löschen
    try:
        _loeschen(echt, ist_worktree)
    except (RuntimeError, OSError) as fehler:
        return Ergebnis(
            ordner,
            "bleibt",
            f"Löschen gescheitert: {fehler}",
            ticket,
            sicherung,
            trocken=False,
        )
    return Ergebnis(ordner, art, _grund(art, ticket), ticket, sicherung, trocken=False)


def _grund(art: str, ticket: int) -> str:
    if art == "gelöscht":
        return f"Ticket #{ticket} seit über 24 h zu, nichts Ungesichertes"
    return f"Ticket #{ticket} seit über 24 h zu, Ungesichertes auf sicherung/{ticket}"


def _wurzeln() -> list[str]:
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_WURZELN")
    muster = roh.split(os.pathsep) if roh else list(WURZELN_STANDARD)
    return [os.path.expanduser(m) for m in muster if m.strip()]


def _im_bereich(ordner: Path) -> bool:
    kandidaten = {ordner}
    try:
        kandidaten.add(ordner.resolve())
    except OSError:
        pass
    return any(k.match(m) for k in kandidaten for m in _wurzeln())


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


def _git_oder_none(cwd: Path, *args: str) -> str | None:
    try:
        return aufpasser._git(cwd, *args).strip()
    except RuntimeError:
        return None


def _hat_verknuepfte_worktrees(repo: Path) -> bool:
    for zeile in aufpasser._git(repo, "worktree", "list", "--porcelain").splitlines():
        if zeile.startswith("worktree "):
            pfad = Path(zeile[len("worktree ") :])
            if pfad.is_dir() and pfad.resolve() != repo:
                return True
    return False


def _ungepushte_koepfe(repo: Path, ist_worktree: bool) -> list[str]:
    """Refs mit Commits auf keinem Remote (Klon: alle Zweige + HEAD; Worktree: nur HEAD)."""
    refs = ["HEAD"]
    if not ist_worktree:
        refs += aufpasser._git(
            repo, "for-each-ref", "--format=%(refname)", "refs/heads"
        ).split()
    return [
        ref
        for ref in refs
        if aufpasser._git(
            repo, "rev-list", "-n", "1", ref, "--not", "--remotes"
        ).strip()
    ]


def _prozess_im_ordner(ordner: Path) -> str | None:
    """Grund, falls ein Prozess im Ordner arbeitet oder die Prozessliste fehlt; sonst None."""
    proc = Path("/proc")
    try:
        pids = [int(p.name) for p in proc.iterdir() if p.name.isdigit()]
    except OSError:
        return "Prozessliste nicht lesbar"
    for pid in pids:
        cwd = aufpasser._prozess_cwd(pid)
        if cwd is not None and (cwd == ordner or cwd.is_relative_to(ordner)):
            return f"Prozess {pid} arbeitet im Ordner"
    return None


def _loeschen(ordner: Path, ist_worktree: bool) -> None:
    if ist_worktree:
        gemeinsam = Path(
            aufpasser._git(ordner, "rev-parse", "--git-common-dir").strip()
        )
        if not gemeinsam.is_absolute():
            gemeinsam = (ordner / gemeinsam).resolve()
        hauptrepo = gemeinsam.parent
        aufpasser._git(hauptrepo, "worktree", "remove", "--force", str(ordner))
        aufpasser._git(hauptrepo, "worktree", "prune")
    else:
        shutil.rmtree(ordner)


# --- Log ------------------------------------------------------------------------


def _log_pfad() -> Path:
    roh = os.environ.get("TO_SPAWN_HAUSMEISTER_LOG")
    return (
        Path(roh).expanduser()
        if roh
        else Path.home() / ".claude" / "to-spawn" / "hausmeister.jsonl"
    )


def _log_schreiben(erg: Ergebnis) -> None:
    zeile = {
        "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ordner": str(erg.ordner),
        "art": erg.art,
        "grund": erg.grund,
        "ticket": erg.ticket,
        "sicherung": erg.sicherung,
        "trocken": erg.trocken,
        "fuer_david": erg.fuer_david,
    }
    log.info("Hausmeister: %s — %s (%s)", erg.ordner, erg.art, erg.grund)
    try:
        datei = _log_pfad()
        datei.parent.mkdir(parents=True, exist_ok=True)
        with datei.open("a", encoding="utf-8") as f:
            f.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    except OSError as fehler:
        log.warning("Hausmeister-Log nicht schreibbar: %s", fehler)
