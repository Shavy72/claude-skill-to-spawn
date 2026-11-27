"""Lesende Zweig- und Issue-Prüfung (#888, aus App #1208).

Prüft ein Git-Repository (Remote-Zweige ``origin/ticket/*``), GitHub-Issue-Stände
und optional ein Schlangen-Journal (JSONL) auf drei Auffälligkeiten und schreibt
Befunde ins Log, ohne jemals selbst Zweige oder Worktrees zu löschen:

1. ``haengend_rot_konflikt``: Ticket-Branches auf ``origin``, die älter als
   :data:`ALTER_TAGE_VORGABE` (2 Tage) sind und im Schlangen-Journal als rot/Konflikt
   stehen oder beim lesenden ``git merge-tree`` gegen den Hauptzweig im Konflikt liegen.
2. ``geschlossen_ohne_code``: Geschlossene Issues (Stufe ``zu``, nicht ``verworfen``)
   ohne ``(#N)``-Commit auf dem Hauptzweig und — sofern ein Remote-Zweig existiert —
   ohne ausreichende Inhalts-Deckung (:func:`inhalts_probe`) auf dem Hauptzweig.
3. ``offen_alter_zweig``: Offene Issues mit Remote-Branch älter als
   :data:`ALTER_TAGE_VORGABE` ohne neuen Push.

Läuft plattformgleich unter Linux und Windows (``pathlib``, UTF-8, fensterlose
Kindprozesse über :mod:`to_spawn.gh`).

Aufruf:
  ``python to_spawn.py zweige-pruefen [--repo PFAD] [--journal DATEI] [--log DATEI] [--json]``
  oder ``python -m to_spawn.zweig_pruefung …``
Exit-Codes: 0 = sauber · 1 = Befunde oder Prüffehler · 2 = Eingabefehler.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from . import config, gh, hausmeister, konsole, ticket_zustand

log = logging.getLogger(__name__)

FallArt = Literal["haengend_rot_konflikt", "geschlossen_ohne_code", "offen_alter_zweig"]

#: Ab so vielen Tagen ohne neuen Push gilt ein Branch als hängend/alt.
ALTER_TAGE_VORGABE = 2.0
#: Mindestanteil (0.0–1.0) hinzugefügter Branch-Zeilen auf dem Hauptzweig für Inhalts-Deckung.
INHALTS_SCHWELLE_VORGABE = 0.7
#: Wie viele zuletzt geschlossene Issues standardmäßig ohne Branch mitgeprüft werden.
GESCHLOSSENE_LIMIT_VORGABE = 50
#: Dateiname des Standard-JSONL-Logs neben dem Hausmeister-Log.
LOG_DATEINAME = "zweig_pruefung.jsonl"

#: Journal-Statuswerte, die einen hängenden roten oder im Konflikt stehenden Branch markieren.
ROT_KONFLIKT_STATI: frozenset[str] = frozenset(
    {
        "rot",
        "red",
        "konflikt",
        "conflict",
        "merge_conflict",
        "merge-conflict",
        "failed",
        "fehler",
        "abgelehnt",
        "error",
    }
)


@dataclass(frozen=True)
class InhaltsProbe:
    """Ergebnis der Inhalts-Probe: wie viele hinzugefügte Branch-Zeilen auf dem Hauptzweig liegen."""

    hinzugefuegt: int
    gefunden: int
    anteil: float


@dataclass(frozen=True)
class Befund:
    """Ein gemeldeter Fall der Zweig-/Issue-Prüfung."""

    fall: FallArt
    ticket: int
    zweig: str
    grund: str
    alter_tage: float | None = None
    inhalts_deckung: float | None = None

    def als_dict(self, *, repo: str = "", zeit: str = "") -> dict[str, Any]:
        daten: dict[str, Any] = {
            "fall": self.fall,
            "ticket": self.ticket,
            "zweig": self.zweig,
            "alter_tage": self.alter_tage,
            "inhalts_deckung": self.inhalts_deckung,
            "grund": self.grund,
        }
        if repo:
            daten["repo"] = repo
        if zeit:
            daten["zeit"] = zeit
        return daten

    def zeile_text(self) -> str:
        teile = [f"[{self.fall}] #{self.ticket}"]
        if self.zweig:
            teile.append(self.zweig)
        if self.alter_tage is not None:
            teile.append(f"{self.alter_tage:.1f} d")
        if self.inhalts_deckung is not None:
            teile.append(f"Deckung {self.inhalts_deckung * 100:.0f}%")
        return f"{' · '.join(teile)} — {self.grund}"


@dataclass(frozen=True)
class Ergebnis:
    """Gesamtergebnis eines Prüflaufs."""

    ok: bool
    repo: str
    hauptzweig: str
    gepruefte_zweige: int
    befunde: tuple[Befund, ...]
    fehler: tuple[str, ...] = ()

    def als_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "repo": self.repo,
            "hauptzweig": self.hauptzweig,
            "gepruefte_zweige": self.gepruefte_zweige,
            "befunde": [b.als_dict() for b in self.befunde],
            "fehler": list(self.fehler),
        }

    def text(self) -> str:
        if self.ok:
            return f"Zweig-Prüfung sauber ({self.gepruefte_zweige} Zweige auf {self.hauptzweig} geprüft)."
        zeilen = [
            (
                f"Zweig-Prüfung: {len(self.befunde)} Befund(e), {len(self.fehler)} Fehler "
                f"({self.gepruefte_zweige} Zweige auf {self.hauptzweig}):"
            )
        ]
        for befund in self.befunde:
            zeilen.append(f"  - {befund.zeile_text()}")
        for err in self.fehler:
            zeilen.append(f"  ! {err}")
        return "\n".join(zeilen)


@dataclass(frozen=True)
class _RemoteZweig:
    zweig: str
    ref: str
    ticket: int
    commit_zeit: float
    sha: str


def lade_journal(pfad: Path | None) -> tuple[dict[str, str], dict[int, str]]:
    """Liest ein optionales Schlangen-Journal (JSONL) als ``(status_je_zweig, status_je_ticket)``.

    Spätere Zeilen überschreiben frühere Einträge für denselben Zweig bzw. dasselbe Ticket.
    Fehlende Datei ergibt leere Zuordnungen; fehlerhafte JSON-Zeilen werden übersprungen.
    """
    nach_zweig: dict[str, str] = {}
    nach_ticket: dict[int, str] = {}
    if pfad is None or not pfad.is_file():
        return nach_zweig, nach_ticket
    try:
        roh = pfad.read_text(encoding="utf-8", errors="replace")
    except OSError as fehler:
        log.warning("Schlangen-Journal %s nicht lesbar: %s", pfad, fehler)
        return nach_zweig, nach_ticket
    for nr, zeile in enumerate(roh.splitlines(), 1):
        text = zeile.strip()
        if not text:
            continue
        try:
            eintrag = json.loads(text)
        except ValueError:
            log.warning("Schlangen-Journal %s:%s ist kein gültiges JSON — übersprungen", pfad, nr)
            continue
        if not isinstance(eintrag, dict):
            continue
        status = str(
            eintrag.get("status")
            or eintrag.get("ergebnis")
            or eintrag.get("zustand")
            or eintrag.get("state")
            or ""
        ).strip().lower()
        if not status:
            continue
        zweig = str(eintrag.get("zweig") or eintrag.get("branch") or "").strip()
        if zweig:
            nach_zweig[zweig] = status
        ticket_roh = eintrag.get("ticket") if "ticket" in eintrag else eintrag.get("nummer")
        if ticket_roh is not None and str(ticket_roh).strip().isdigit():
            nach_ticket[int(str(ticket_roh).strip())] = status
    return nach_zweig, nach_ticket


def inhalts_probe(repo: Path, haupt_ref: str, zweig_ref: str) -> InhaltsProbe:
    """Misst den Anteil hinzugefügter nicht-leerer Zeilen von ``zweig_ref`` auf ``haupt_ref``.

    Ermittelt über ``git diff --unified=0 <haupt_ref>...<zweig_ref>`` alle hinzugefügten
    Zeilen je Datei und prüft, wie viele davon in der jeweiligen Datei auf ``haupt_ref``
    vorkommen. Rein lesend, ändert weder Arbeitsbaum noch Index.
    """
    code, diff_text, _stderr = gh._git_lauf(
        ["diff", "--no-color", "--unified=0", f"{haupt_ref}...{zweig_ref}"],
        repo,
        timeout=gh.GIT_ZEIT_S,
    )
    if code != 0:
        return InhaltsProbe(hinzugefuegt=0, gefunden=0, anteil=0.0)

    hinzugefuegt_je_datei: dict[str, list[str]] = {}
    aktuelle_datei: str | None = None
    for zeile in diff_text.splitlines():
        if zeile.startswith("+++ "):
            pfad_teil = zeile[4:].strip()
            if pfad_teil == "/dev/null":
                aktuelle_datei = None
            elif pfad_teil.startswith("b/"):
                aktuelle_datei = pfad_teil[2:]
            else:
                aktuelle_datei = pfad_teil
            continue
        if zeile.startswith(("--- ", "@@ ")):
            continue
        if aktuelle_datei is not None and zeile.startswith("+"):
            inhalt = zeile[1:].strip()
            if inhalt:
                hinzugefuegt_je_datei.setdefault(aktuelle_datei, []).append(inhalt)

    gesamt = sum(len(zeilen) for zeilen in hinzugefuegt_je_datei.values())
    if gesamt == 0:
        return InhaltsProbe(hinzugefuegt=0, gefunden=0, anteil=1.0)

    gefunden = 0
    for datei, zeilen in hinzugefuegt_je_datei.items():
        c_show, inhalt_haupt, _err = gh._git_lauf(
            ["show", f"{haupt_ref}:{datei}"],
            repo,
            timeout=gh.GIT_ZEIT_S,
        )
        if c_show != 0:
            continue
        haupt_zeilen = {z.strip() for z in inhalt_haupt.splitlines() if z.strip()}
        gefunden += sum(1 for z in zeilen if z in haupt_zeilen)

    return InhaltsProbe(hinzugefuegt=gesamt, gefunden=gefunden, anteil=gefunden / gesamt)


def _hat_konflikt(repo: Path, haupt_ref: str, zweig_ref: str) -> bool:
    """Prüft rein lesend per ``git merge-tree --write-tree``, ob ein Merge-Konflikt vorliegt."""
    code, _stdout, _stderr = gh._git_lauf(
        ["merge-tree", "--write-tree", haupt_ref, zweig_ref],
        repo,
        timeout=gh.GIT_ZEIT_S,
    )
    return code == 1


def _ist_vorfahre(repo: Path, sha: str, haupt_ref: str) -> bool:
    """Ist ``sha`` bereits über ``haupt_ref`` erreichbar?"""
    code, _stdout, _stderr = gh._git_lauf(
        ["merge-base", "--is-ancestor", sha, haupt_ref],
        repo,
        timeout=gh.GIT_ZEIT_S,
    )
    return code == 0


def _remote_ticket_zweige(repo: Path) -> list[_RemoteZweig]:
    """Liest alle ``refs/remotes/origin/ticket/*``-Zweige mit Ticket-Nummer und Commit-Zeit."""
    code, ausgabe = gh._git_ausgabe(
        [
            "for-each-ref",
            "--format=%(refname:short)\t%(committerdate:unix)\t%(objectname)",
            "refs/remotes/origin/ticket/",
        ],
        repo,
        timeout=gh.GIT_ZEIT_S,
    )
    if code != 0 or not ausgabe:
        return []
    ergebnis: list[_RemoteZweig] = []
    for zeile in ausgabe.splitlines():
        teile = zeile.split("\t")
        if len(teile) != 3:
            continue
        ref, zeit_roh, sha = teile
        zweig = ref.removeprefix("origin/")
        ticket_nr = hausmeister._zweig_ticket(zweig)
        if ticket_nr is None:
            continue
        try:
            commit_zeit = float(zeit_roh)
        except ValueError:
            continue
        ergebnis.append(_RemoteZweig(zweig=zweig, ref=ref, ticket=ticket_nr, commit_zeit=commit_zeit, sha=sha))
    return sorted(ergebnis, key=lambda eintrag: (eintrag.ticket, eintrag.zweig))


def _lade_geschlossene_issues(repo: Path, gh_repo: str, limit: int) -> dict[int, dict[str, Any]]:
    """Lädt die letzten ``limit`` geschlossenen Issues über ``gh issue list``."""
    if limit <= 0:
        return {}
    befehl = [
        "issue",
        "list",
        "--state",
        "closed",
        "--limit",
        str(limit),
        "--json",
        "number,state,stateReason,labels,title",
    ]
    if gh_repo:
        befehl.extend(["--repo", gh_repo])
    daten = gh.json_lauf(befehl, cwd=repo)
    if not isinstance(daten, list):
        return {}
    ergebnis: dict[int, dict[str, Any]] = {}
    for eintrag in daten:
        if isinstance(eintrag, dict) and isinstance(eintrag.get("number"), int):
            ergebnis[int(eintrag["number"])] = eintrag
    return ergebnis


def _lade_issue(repo: Path, gh_repo: str, nummer: int) -> dict[str, Any] | None:
    """Lädt ein einzelnes Issue über ``gh issue view``."""
    befehl = [
        "issue",
        "view",
        str(nummer),
        "--json",
        "number,state,stateReason,labels,title",
    ]
    if gh_repo:
        befehl.extend(["--repo", gh_repo])
    daten = gh.json_lauf(befehl, cwd=repo)
    return daten if isinstance(daten, dict) and "state" in daten else None


def _schreibe_befunde_log(befunde: Sequence[Befund], *, repo: Path, log_datei: Path | None) -> None:
    """Schreibt jeden Befund über ``logging`` und als JSONL-Zeile ins Prüf-Log."""
    zeit = datetime.now().astimezone().isoformat(timespec="seconds")
    ziel = log_datei or hausmeister._log_pfad().with_name(LOG_DATEINAME)
    for befund in befunde:
        log.warning("Zweig-Prüfung: %s", befund.zeile_text())
        hausmeister._log_zeile(befund.als_dict(repo=str(repo), zeit=zeit), datei=ziel)


def pruefe_repo(
    repo: Path,
    *,
    gh_repo: str | None = None,
    hauptzweig: str | None = None,
    journal_pfad: Path | None = None,
    log_datei: Path | None = None,
    alter_tage: float = ALTER_TAGE_VORGABE,
    inhalts_schwelle: float = INHALTS_SCHWELLE_VORGABE,
    geschlossene_limit: int = GESCHLOSSENE_LIMIT_VORGABE,
    jetzt: float | None = None,
    mit_fetch: bool = True,
    schreibe_log: bool = True,
) -> Ergebnis:
    """Prüft Remote-Ticket-Branches und GitHub-Issues eines Repos rein lesend.

    Löscht niemals Zweige oder Worktrees. Schreibt Befunde ins Log (abschaltbar
    über ``schreibe_log=False``).
    """
    repo_pfad = repo.resolve()
    fehler: list[str] = []

    if mit_fetch:
        c_fetch, _s_fetch, e_fetch = gh._git_lauf(
            ["fetch", "-q", "--prune", "--no-tags", "origin"],
            repo_pfad,
            timeout=gh.FETCH_ZEIT_S,
        )
        if c_fetch != 0:
            fehler.append(f"git fetch origin fehlgeschlagen (Exit {c_fetch}): {e_fetch.strip()[:160]}")

    haupt = (hauptzweig or gh.hauptzweig(repo_pfad)).strip()
    haupt_ref = f"origin/{haupt}"
    slug = (gh_repo if gh_repo is not None else gh.repo_aus_origin(repo_pfad)).strip()
    jetzt_ts = time.time() if jetzt is None else float(jetzt)

    journal_zweig, journal_ticket = lade_journal(journal_pfad)
    zweige = _remote_ticket_zweige(repo_pfad)
    issues = _lade_geschlossene_issues(repo_pfad, slug, geschlossene_limit)

    befunde: list[Befund] = []
    gepruefte_tickets: set[int] = set()

    for eintrag in zweige:
        gepruefte_tickets.add(eintrag.ticket)
        alter = max(0.0, (jetzt_ts - eintrag.commit_zeit) / 86400.0)
        issue = issues.get(eintrag.ticket)
        if issue is None:
            issue = _lade_issue(repo_pfad, slug, eintrag.ticket)
            if issue is not None:
                issues[eintrag.ticket] = issue

        stufe = ticket_zustand.stufe(issue) if issue is not None else "offen"
        vorfahre = _ist_vorfahre(repo_pfad, eintrag.sha, haupt_ref)

        if stufe == "zu":
            if not vorfahre:
                commits = gh.commit_betreffe(repo_pfad, eintrag.ticket, haupt_ref)
                if commits == []:
                    probe = inhalts_probe(repo_pfad, haupt_ref, eintrag.ref)
                    if probe.anteil < inhalts_schwelle:
                        befunde.append(
                            Befund(
                                fall="geschlossen_ohne_code",
                                ticket=eintrag.ticket,
                                zweig=eintrag.zweig,
                                alter_tage=round(alter, 2),
                                inhalts_deckung=round(probe.anteil, 4),
                                grund=(
                                    f"Issue geschlossen, kein (#{eintrag.ticket})-Commit auf {haupt_ref} "
                                    f"und Inhalts-Deckung {probe.gefunden}/{probe.hinzugefuegt} "
                                    f"({probe.anteil * 100:.0f}% < {inhalts_schwelle * 100:.0f}%)"
                                ),
                            )
                        )
            continue

        if stufe in ("verworfen", "fertig_gebaut"):
            continue

        # Offenes Issue (oder Status nicht ermittelbar) mit altem Branch
        if alter > alter_tage:
            journal_status = journal_zweig.get(eintrag.zweig) or journal_ticket.get(eintrag.ticket) or ""
            ist_journal_rot = journal_status in ROT_KONFLIKT_STATI
            ist_konflikt = False if ist_journal_rot else _hat_konflikt(repo_pfad, haupt_ref, eintrag.ref)
            if ist_journal_rot or ist_konflikt:
                ursache = f"Journal-Status „{journal_status}“" if ist_journal_rot else f"Merge-Konflikt mit {haupt_ref}"
                befunde.append(
                    Befund(
                        fall="haengend_rot_konflikt",
                        ticket=eintrag.ticket,
                        zweig=eintrag.zweig,
                        alter_tage=round(alter, 2),
                        grund=f"hängender Branch ({alter:.1f} Tage > {alter_tage:g} d): {ursache}",
                    )
                )
            elif not vorfahre:
                befunde.append(
                    Befund(
                        fall="offen_alter_zweig",
                        ticket=eintrag.ticket,
                        zweig=eintrag.zweig,
                        alter_tage=round(alter, 2),
                        grund=f"offenes Issue mit altem Branch ohne neuen Push ({alter:.1f} Tage > {alter_tage:g} d)",
                    )
                )

    # Fall 2 auch für geschlossene Issues ohne noch vorhandenen Remote-Branch
    for nr in sorted(issues):
        if nr in gepruefte_tickets:
            continue
        if ticket_zustand.stufe(issues[nr]) != "zu":
            continue
        commits = gh.commit_betreffe(repo_pfad, nr, haupt_ref)
        if commits == []:
            befunde.append(
                Befund(
                    fall="geschlossen_ohne_code",
                    ticket=nr,
                    zweig="",
                    grund=f"Issue geschlossen, aber kein (#{nr})-Commit auf {haupt_ref}",
                )
            )

    if schreibe_log and befunde:
        _schreibe_befunde_log(befunde, repo=repo_pfad, log_datei=log_datei)
    elif not befunde:
        log.info("Zweig-Prüfung %s: sauber (%d Zweige geprüft)", repo_pfad, len(zweige))

    return Ergebnis(
        ok=not befunde and not fehler,
        repo=str(repo_pfad),
        hauptzweig=haupt,
        gepruefte_zweige=len(zweige),
        befunde=tuple(befunde),
        fehler=tuple(fehler),
    )


def _parser_optionen(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", type=Path, default=None, help="Git-Repo-Ordner (Vorgabe: aktuelles Repo)")
    parser.add_argument("--gh-repo", default=None, help="GitHub-Slug owner/name (Vorgabe: aus origin)")
    parser.add_argument("--hauptzweig", default=None, help="Hauptzweig-Name (Vorgabe: automatisch)")
    parser.add_argument("--journal", type=Path, default=None, help="optionales Schlangen-Journal (JSONL)")
    parser.add_argument("--log", type=Path, default=None, help="Zielpfad für das Befund-Log (JSONL)")
    parser.add_argument(
        "--alter-tage",
        type=float,
        default=ALTER_TAGE_VORGABE,
        help=f"Altersschwelle in Tagen für hängende Branches (Vorgabe: {ALTER_TAGE_VORGABE:g})",
    )
    parser.add_argument(
        "--inhalts-schwelle",
        type=float,
        default=INHALTS_SCHWELLE_VORGABE,
        help=f"Mindestanteil hinzugefügter Zeilen auf dem Hauptzweig (Vorgabe: {INHALTS_SCHWELLE_VORGABE:g})",
    )
    parser.add_argument(
        "--geschlossene-limit",
        type=int,
        default=GESCHLOSSENE_LIMIT_VORGABE,
        help=f"Anzahl zuletzt geschlossener Issues ohne Branch (Vorgabe: {GESCHLOSSENE_LIMIT_VORGABE}, 0 = aus)",
    )
    parser.add_argument("--ohne-fetch", action="store_true", help="kein git fetch vor der Prüfung ausführen")
    parser.add_argument("--json", action="store_true", help="Ergebnis als JSON auf stdout ausgeben")


def richte_parser_ein(unter: argparse._SubParsersAction[Any]) -> None:
    """Registriert den Unterbefehl ``zweige-pruefen`` in ``to_spawn.py`` (#888)."""
    parser = unter.add_parser(
        "zweige-pruefen",
        help="hängende Ticket-Branches und geschlossene Issues ohne Code rein lesend prüfen (Exit 0/1/2)",
    )
    _parser_optionen(parser)


def lauf(args: argparse.Namespace, standard_repo: Path | None = None) -> int:
    """Führt den CLI-Befehl ``zweige-pruefen`` aus; Exit 0 sauber, 1 Befunde/Fehler, 2 Eingabefehler."""
    ziel_repo = args.repo.expanduser() if args.repo is not None else (standard_repo or config.repo_wurzel())
    if not ziel_repo.is_dir():
        log.error("Repo-Ordner existiert nicht: %s", ziel_repo)
        return 2
    if args.alter_tage < 0 or not (0.0 <= args.inhalts_schwelle <= 1.0):
        log.error("Ungültige Schwellenwerte (--alter-tage >= 0, --inhalts-schwelle zwischen 0 und 1).")
        return 2

    ergebnis = pruefe_repo(
        ziel_repo,
        gh_repo=args.gh_repo,
        hauptzweig=args.hauptzweig,
        journal_pfad=args.journal,
        log_datei=args.log,
        alter_tage=args.alter_tage,
        inhalts_schwelle=args.inhalts_schwelle,
        geschlossene_limit=args.geschlossene_limit,
        mit_fetch=not args.ohne_fetch,
    )
    if args.json:
        sys.stdout.write(json.dumps(ergebnis.als_dict(), ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(ergebnis.text() + "\n")
    return 0 if ergebnis.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    konsole.utf8_ausgabe()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        prog="to_spawn.zweig_pruefung",
        description="Hängende Ticket-Branches und geschlossene Issues ohne Code rein lesend prüfen (#888)",
    )
    _parser_optionen(parser)
    return lauf(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
