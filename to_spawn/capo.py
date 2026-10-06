"""Capo: der Regel-Prüfer des Bau-Aufsehers (duoplus-management#213).

Ein Tick liest GitHub (Sub-Issues der Spec), ``origin/<Hauptzweig>``, die
Ticket-Worktrees und die Bau-Logs — und prüft fünf Regeln:

1. ``commit_ohne_nummer`` — Ticket zu, aber kein Commit mit ``(#N)`` am Betreff-Ende
   oder in Scope-Form ``typ(#N): …`` (außer ``docs(#N):`` — das sind Nachträge).
2. ``beweis_fehlt`` — Ticket zu, aber keine Belegseite mit der Nummer im Namen
   (reine Doku-Tickets brauchen keine: die Doku ist der Beleg).
3. ``test_ersetzt`` — ein Ticket-Commit hat eine Testfunktion/Testdatei entfernt
   (nur echte Testdateien; es gibt keine Ausnahme, Tests werden nie entfernt).
4. ``session_verwaist`` (kritisch) — Ticket offen + zugewiesen, aber seit Stunden keine Spur
   (nicht bei Checkpoint-Label oder wenn die jüngste Bau-Log-Zeile ``blockiert`` ist).
5. ``vps_ungleich_origin`` — Deploy-Ticket zu, aber sein Commit ist nicht auf dem VPS.

Verstöße 1, 2, 3 und 5 öffnen das Ticket jedes Mal wieder, wenn es erneut mit
demselben Verstoß geschlossen wird (Entscheidung 21.09.: Spec Zeile 13 kennt keine
Ausnahme; je Schließen bleibt es bei einer Wieder-Öffnung). Keine Regeln für Tickets
„nicht geplant“/„Duplikat“. Label ``waechter:ok`` hebt nur das Wieder-Öffnen auf —
der Verstoß wird trotzdem erkannt und im Tick-Bericht genannt, zählt aber wie der
Ausgangsstand als „alt“ und hält „Spec fertig“ nicht auf. Nach dem Wieder-Öffnen
startet capo die Folge-Runde (#284); ein Nacht-Checkpoint gilt nach der Frist mit
dem Vorschlag der Session als angenommen, David kann kippen (#285). Zeitgrenzen:
beim ersten Tick einer Spec sind alle geschlossenen Tickets Ausgangsstand (nur
melden; maßgeblich ist, ob das Ticket beim ersten Tick schon zu war — kein
Vergleich der GitHub-Uhr mit der lokalen Uhr), danach wird ein Schließen erst
15 min später geprüft (Karenz, ``waechter.karenz_minuten``; ``0`` = keine Karenz).
``TO_SPAWN_WAECHTER_SOFORT=1`` bzw. ``waechter.sofort`` schaltet beide Zeitgrenzen ab.
Regel 4 kommentiert nur und meldet per Mail (je Tag einmal). Dazu kommt das
Bau-Log-Delta (nur neue Zeilen seit dem letzten Tick, aus dem Hauptzweig und den
Laufdateien ``.to-spawn/bau_log/<N>.jsonl``) und die Entscheidungs-Übersicht.
Der Zustand liegt in ``~/.claude/to-spawn/waechter/<owner>_<name>_<S>.json``, eine
Sperre daneben (``….json.lock``) hält parallele Läufe fern.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import bau_log, befund, gh, melder, mensch_noetig, staging_schalter, vorfall

log = logging.getLogger("to_spawn.capo")

#: Bau-Log-Typen, die im Delta nur gezählt, nicht einzeln gezeigt werden (Hook-Rauschen).
LEISE_TYPEN = frozenset({"session_ende", "subagent_ende"})
#: Werte von ``status``/``phase``/``ergebnis`` einer ``deploy_phase``, die „Gate rot“ heißen.
ROT_WERTE = frozenset({"rot", "fail", "failed", "abbruch", "fehler"})
MAX_DELTA_ZEILEN = 8
#: ``state_reason`` geschlossener Tickets, für die keine Regel gilt.
NICHT_GEPLANT = frozenset({"not_planned", "duplicate"})
#: Label am Ticket: David hat bewusst freigegeben — Regeln überspringen (E2).
OK_LABEL = "waechter:ok"
#: Minuten nach ``closed_at``, bevor die Regeln greifen (Vorgabe, ``waechter.karenz_minuten``).
KARENZ_MIN = 15.0
#: Tick-Zeile, wenn ``mail.befehl`` fehlt: bewusste Wahl des Repos, kein Fehler.
MAIL_AUS = "INFO: Mail nicht eingerichtet (mail.befehl leer) — Meldungen stehen nur hier."
#: Sekunden, die ein Tick auf die Sperre eines anderen Laufs wartet.
SPERRE_S = 30.0
GIT_ZEIT_S = 30
FETCH_ZEIT_S = 60

_TEST_DATEI = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$|[^/]*\.(test|spec)\.[cm]?[jt]sx?$)")
#: Kopien von Tests (Belege, Archiv, Mutanten) sind keine Testdateien.
_KEINE_TESTDATEI = re.compile(r"(^|/)(docs|archive)/|(^|/)mutants/")
_PY_TEST = re.compile(r"^-[ \t]*(?:async[ \t]+)?def[ \t]+(test_\w+)", re.MULTILINE)
_JS_TEST = re.compile(r"""^-[ \t]*(?:it|test)\([ \t]*(["'`])(.+?)\1""", re.MULTILINE)
#: Vermerk im Commit-Text für eine bewusste Test-Umbenennung (#448), z. B.
#: ``test-umbenannt: tests/t.py::test_alt -> tests/t.py::test_neu``.
_UMBENANNT = re.compile(r"(?im)^[ \t]*test-umbenannt[ \t]*:(.*)$")
_PFEIL = re.compile(r"[ \t]*(?:→|->|=>)[ \t]*")
_PY_NAME = re.compile(r"test_\w+")


#: Regel → Vorfall für die Lernschleife (#286): Klasse, Symptom, Ursache, Lösung.
#: Die Worte sind je Regel fest — so steht eine Regel genau einmal im Katalog,
#: der konkrete Fall (Ticket, Text) landet in der Bau-Log-Zeile und im Beispiel.
REGEL_VORFALL: dict[str, tuple[str, str, str, str]] = {
    "commit_ohne_nummer": (
        "prozess",
        "Arbeit im Repo, aber kein Ticket-Bezug im Commit-Betreff",
        "Betreff endet nicht auf (#N) — der Aufseher ordnet den Commit keinem Ticket zu",
        "Betreff mit (#N) abschließen; capo öffnet das Ticket wieder",
    ),
    "beweis_fehlt": (
        "prozess",
        "Ticket zu, aber niemand kann den Beweis nachlesen",
        "Keine Belegseite unter dem Beleg-Ordner zum Ticket",
        "Belegseite anlegen (docs/verify-hard/<N>_*.md) und neu schließen",
    ),
    "test_ersetzt": (
        "prozess",
        "Nach dem Fix fehlen Tests, die vorher da waren",
        "Test entfernt statt ergänzt",
        "Test wieder aufnehmen oder die Umbenennung im Commit-Text ausweisen",
    ),
    "vps_ungleich_origin": (
        "infra",
        "Live-System zeigt einen anderen Stand als origin",
        "Fremder Deploy hat den eigenen Stand überschrieben",
        "VPS-HEAD vor dem Bundle prüfen, Rettung mit --to <origin-SHA>",
    ),
    "session_verwaist": (
        "skill",
        "Fenster steht, Ticket offen, nichts bewegt sich",
        "Session tot oder ohne Folge-Runde — keine Spur in Commit, Bau-Log, Worktree",
        "capo kommentiert das Ticket, der Aufpasser startet die Folge-Runde",
    ),
    "gate_rot": (
        "infra",
        "Deploy-Gate bricht rot ab",
        "Test oder Vorstufe im Gate scheitert",
        "Grund aus der Gate-Ausgabe beheben und das Gate neu starten",
    ),
    "live_beweis_blockiert": (
        "prozess",
        "Session meldet: Live-Beweis nicht möglich",
        "Ein fremder Lauf, ein Deploy oder ein fehlendes Gerät blockiert den Weg",
        "Blocker im Ticket benennen, Beweis nach dem Blocker nachholen",
    ),
    "folgerunden_grenze": (
        "mensch",
        "Ticket wieder offen, aber niemand baut weiter",
        "Folge-Runden-Grenze erreicht — capo startet keine neue Runde (#284)",
        "Ein Mensch prüft das Ticket und baut von Hand weiter",
    ),
    "checkpoint_offen": (
        "mensch",
        "Checkpoint wartet über die Frist, die Session hat keinen Vorschlag hinterlassen",
        "Frage ohne eigenen Vorschlag — der Aufseher rät nicht (#285)",
        "Session stellt jede Checkpoint-Frage mit „Vorschlag: …“",
    ),
    "checkpoint_annahme": (
        "mensch",
        "Ja/Nein-Frage mitten in der Kette, nachts antwortet niemand",
        "Checkpoint ohne Antwort über die Frist (#285)",
        "Aufseher nimmt den Vorschlag der Session nach Doktrin an; David kann kippen",
    ),
}

#: Vorfall-Worte, wenn eine Regel neu ist und noch nicht in REGEL_VORFALL steht.
VORFALL_UNBEKANNT = (
    "skill",
    "Aufseher meldet einen Verstoß ohne hinterlegte Lernschleife",
    "Regel ist neu und steht noch nicht in REGEL_VORFALL",
    "Regel in to_spawn/capo.py:REGEL_VORFALL mit Klasse/Symptom/Ursache/Lösung ergänzen",
)


@dataclass
class Verstoss:
    ticket: int
    regel: str
    text: str
    kritisch: bool = False


@dataclass
class Commit:
    sha: str
    betreff: str
    zeit: int


@dataclass
class TickErgebnis:
    zeilen: list[str] = field(default_factory=list)
    verstoesse: list[Verstoss] = field(default_factory=list)
    fertig: bool = False
    exit_code: int = 0
    #: Verstöße an Tickets aus dem Ausgangsstand (nur gemeldet, nie wieder geöffnet).
    alt: list[Verstoss] = field(default_factory=list)


# --- Git ------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> tuple[int, str]:
    """``git <args>`` mit Zeitgrenze (fetch 60 s, sonst 30 s); Zeitablauf = Fehler."""
    grenze = FETCH_ZEIT_S if args[:1] == ("fetch",) else GIT_ZEIT_S
    try:
        fertig = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=grenze,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("git %s nach %s s abgebrochen.", args[:2], grenze)
        return 124, ""
    except OSError as fehler:
        log.warning("git %s gescheitert: %s", args[:2], fehler)
        return 127, ""
    return fertig.returncode, fertig.stdout.rstrip("\n")


def haupt_ref(repo: Path) -> str | None:
    """``origin/<Hauptzweig>`` nach der Regel von ``gh.hauptzweig`` (#257); ``None`` ohne den Ref."""
    ref = f"origin/{gh.hauptzweig(repo)}"
    if _git(repo, "rev-parse", "--verify", "-q", ref)[0] == 0:
        return ref
    return None


def alle_commits(repo: Path, ref: str) -> list[Commit]:
    code, text = _git(repo, "log", ref, "--format=%H%x1f%s%x1f%ct")
    if code != 0:
        return []
    commits = []
    for zeile in text.splitlines():
        teile = zeile.split("\x1f")
        if len(teile) == 3 and teile[2].isdigit():
            commits.append(Commit(teile[0], teile[1], int(teile[2])))
    return commits


def betreff_hat_nummer(betreff: str, ticket: int) -> bool:
    """Betreff endet mit ``(#N)`` (optional `` [skip ci]``) oder beginnt mit ``typ(#N):`` (E1).

    ``docs(#N): …`` zählt nicht: so heißen Nachträge (Aufseher, Handoff), nicht der Bau.
    """
    if re.search(rf"\(#{ticket}\)(?: \[skip ci\])?\s*$", betreff):
        return True
    treffer = re.match(rf"([a-z]+)\(#{ticket}\)!?:", betreff)
    return treffer is not None and treffer.group(1) != "docs"


def ticket_commits(commits: list[Commit], ticket: int) -> list[Commit]:
    return [c for c in commits if betreff_hat_nummer(c.betreff, ticket)]


def _dateien(repo: Path, sha: str) -> list[tuple[str, str]]:
    """(Status, Pfad) je Datei eines Commits, Umbenennungen als Status ``R`` mit neuem Pfad."""
    code, text = _git(repo, "show", "--format=", "--name-status", "-M", sha)
    if code != 0:
        return []
    ergebnis = []
    for zeile in text.splitlines():
        teile = zeile.split("\t")
        if len(teile) >= 2:
            ergebnis.append((teile[0][:1], teile[-1]))
    return ergebnis


_DATUM = re.compile(r"\d{4}-\d{2}-\d{2}")


def beleg_passt(pfad: str, ticket: int) -> bool:
    """Nummer als eigenes Zahlwort im Pfad unterhalb des Beleg-Ordners (Datei oder Ordner).

    Belegseiten liegen als Datei (``213_gruen.txt``) oder als Ordner
    (``2026-09-18_203_staffel/BEWEIS.md``); ein Datum im Namen zählt nie als Nummer.
    """
    ohne_datum = _DATUM.sub("-", pfad)
    return re.search(rf"(^|\D){ticket}(\D|$)", ohne_datum) is not None


# --- Regeln --------------------------------------------------------------------


def regel_commit(ticket: int, eigene: list[Commit]) -> Verstoss | None:
    if eigene:
        return None
    return Verstoss(
        ticket,
        "commit_ohne_nummer",
        f"kein Commit auf dem Hauptzweig endet mit „(#{ticket})“",
    )


def regel_beweis(repo: Path, ref: str, ticket: int, eigene: list[Commit], ordner: str) -> Verstoss | None:
    ordner = ordner.strip("/") or "docs/verify-hard"
    code, text = _git(repo, "ls-tree", "-r", "--name-only", ref, "--", ordner)
    namen = text.splitlines() if code == 0 else []
    for commit in eigene:
        namen += [pfad for _, pfad in _dateien(repo, commit.sha) if pfad.startswith(ordner + "/")]
    if any(beleg_passt(pfad[len(ordner) + 1 :], ticket) for pfad in namen):
        return None
    if eigene and all(_nur_doku(repo, c.sha) for c in eigene):
        # Reines Doku-Ticket: die Doku ist der Beleg (E4) — aber nur Doku außerhalb
        # des Beleg-Ordners; eine fremde Belegseite (z. B. 9010 statt 901) zählt nicht.
        doku = [pfad for c in eigene for _, pfad in _dateien(repo, c.sha)]
        if any(not pfad.startswith(ordner + "/") for pfad in doku):
            return None
    return Verstoss(
        ticket,
        "beweis_fehlt",
        f"keine Belegseite (Datei oder Ordner) mit „{ticket}“ im Namen unter {ordner}/",
    )


def ist_doku(pfad: str) -> bool:
    return pfad.startswith("docs/") or pfad.lower().endswith(".md")


def _nur_doku(repo: Path, sha: str) -> bool:
    dateien = _dateien(repo, sha)
    return bool(dateien) and all(ist_doku(pfad) for _, pfad in dateien)


def ist_testdatei(pfad: str) -> bool:
    """Echte Testdatei — nicht deren Kopien unter ``docs/``, ``archive/`` oder ``mutants/``."""
    return bool(_TEST_DATEI.search(pfad)) and not _KEINE_TESTDATEI.search(pfad)


def _test_abschnitte(diff: str) -> dict[str, str]:
    """Diff je Datei (neuer Pfad → Abschnitt), nur echte Testdateien."""
    abschnitte: dict[str, str] = {}
    for teil in re.split(r"(?m)^(?=diff --git )", diff):
        kopf = re.match(r"diff --git a/(\S+) b/(\S+)", teil)
        if kopf and (ist_testdatei(kopf.group(1)) or ist_testdatei(kopf.group(2))):
            abschnitte[kopf.group(2)] = teil
    return abschnitte


def entfernte_tests(diff: str) -> list[str]:
    """Namen entfernter Testfälle, die im selben Diff nicht wieder auftauchen."""
    plus = "\n".join(z for z in diff.splitlines() if z.startswith("+") and not z.startswith("+++"))
    namen: list[str] = []
    for treffer in _PY_TEST.finditer(diff):
        name = treffer.group(1)
        if not re.search(rf"\b{re.escape(name)}\b", plus):
            namen.append(name)
    for treffer in _JS_TEST.finditer(diff):
        name = treffer.group(2)
        if not any(f"{q}{name}{q}" in plus for q in ("'", '"', "`")):
            namen.append(name)
    return list(dict.fromkeys(namen))


def umbenannte_tests(diff: str, commit_text: str) -> set[str]:
    """Alte Namen bewusst umbenannter Python-Tests (#448) — kein Verlust.

    Quelle ist der Vermerk ``test-umbenannt: alt -> neu`` (auch ``→``/``=>``, mehrere
    Paare mit ``,``/``;`` getrennt, Pfade wie ``tests/t.py::test_x`` erlaubt) im
    Commit-Text. Ein Paar zählt nur, wenn der neue Test als ``+def`` im selben
    Test-Abschnitt (:func:`_test_abschnitte`, also derselben Datei) steht, aus dem
    der alte als ``-def`` verschwindet. Ein Diff-Ausschnitt ohne ``diff --git``-Kopf
    gilt als ein einziger Abschnitt. Eine stille Umbenennung ohne Vermerk bleibt ein
    Ersatz (Test ``test_test_ersetzt_oeffnet_wieder`` aus #213).
    """
    mit_kopf = bool(re.search(r"(?m)^diff --git ", diff))
    teile = list(_test_abschnitte(diff).values()) if mit_kopf else [diff]
    alte: set[str] = set()
    for vermerk in _UMBENANNT.finditer(commit_text):
        for paar in re.split(r"[,;]", vermerk.group(1)):
            seiten = _PFEIL.split(paar.strip())
            if len(seiten) != 2:
                continue
            links, rechts = (_PY_NAME.findall(seite) for seite in seiten)
            if not links or not rechts:
                continue
            alt, neu = links[-1], rechts[-1]
            if any(
                _def_zeile("+", neu, teil) and (not mit_kopf or _def_zeile("-", alt, teil))
                for teil in teile
            ):
                alte.add(alt)
    return alte


def _def_zeile(zeichen: str, name: str, diff: str) -> bool:
    """Steht ``def <name>`` als ``+``- bzw. ``-``-Zeile im Diff?"""
    muster = rf"(?m)^{re.escape(zeichen)}[ \t]*(?:async[ \t]+)?def[ \t]+{re.escape(name)}\b"
    return re.search(muster, diff) is not None


def _test_verluste(repo: Path, sha: str) -> list[str]:
    code, diff = _git(repo, "show", "--format=", "-M", "--unified=0", sha)
    if code != 0:
        return []
    abschnitte = _test_abschnitte(diff)
    verluste = entfernte_tests("".join(abschnitte.values()))
    if verluste:
        code_text, commit_text = _git(repo, "show", "-s", "--format=%B", sha)
        umbenannt = umbenannte_tests(diff, commit_text if code_text == 0 else "")
        verluste = [name for name in verluste if name not in umbenannt]
    for status, pfad in _dateien(repo, sha):
        if status != "D" or not ist_testdatei(pfad):
            continue
        # Gelöschte Testdatei ohne erkennbare Testfälle: die Datei selbst zählt.
        abschnitt = abschnitte.get(pfad, "")
        if not _PY_TEST.search(abschnitt) and not _JS_TEST.search(abschnitt):
            verluste.append(f"Datei {pfad}")
    return verluste


def regel_tests(repo: Path, ticket: int, eigene: list[Commit]) -> Verstoss | None:
    verluste: list[str] = []
    for commit in eigene:
        verluste += [f"{name} ({commit.sha[:7]})" for name in _test_verluste(repo, commit.sha)]
    if not verluste:
        return None
    liste = ", ".join(verluste[:4]) + (f" (+{len(verluste) - 4})" if len(verluste) > 4 else "")
    return Verstoss(ticket, "test_ersetzt", f"Test entfernt statt ergänzt: {liste}")


def _passt(pfad: str, muster: list[str]) -> bool:
    for m in muster:
        if m.endswith("/") and pfad.startswith(m):
            return True
        if pfad == m or fnmatch.fnmatch(pfad, m):
            return True
    return False


def vps_kopf(vps: dict[str, Any]) -> str | None:
    """Voller SHA von ``HEAD`` auf dem VPS (``None`` = nicht lesbar)."""
    befehl = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        str(vps["ssh"]),
        f"cd {shlex.quote(str(vps.get('pfad') or '.'))} && git rev-parse HEAD",
    ]
    try:
        fertig = subprocess.run(befehl, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("VPS-HEAD nicht lesbar: %s", fehler)
        return None
    zeilen = fertig.stdout.strip().splitlines()
    sha = zeilen[-1].strip() if zeilen else ""
    if fertig.returncode != 0 or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        log.warning(
            "VPS-HEAD nicht lesbar (Exit %s): %s",
            fertig.returncode,
            fertig.stderr.strip()[:200],
        )
        return None
    return sha


STAGING_LOG_STANDARD = "/home/bau/staging/deploys.jsonl"
FREIGABE_STANDARD = "~/.config/to-spawn/live_freigabe"
FREIGABE_MAX_ALTER_S = 24 * 3600  # wie scripts/staging/deploy_ziel.py


def staging_kopf(vps: dict[str, Any]) -> str | None:
    """SHA des letzten grünen Staging-Deploys (``None`` = Datei fehlt/kein grüner Stand)."""
    pfad = Path(str(vps.get("staging_log") or STAGING_LOG_STANDARD)).expanduser()
    try:
        zeilen = pfad.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for zeile in reversed(zeilen):
        try:
            eintrag = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(eintrag, dict) and eintrag.get("ergebnis") == "gruen" and eintrag.get("sha"):
            return str(eintrag["sha"])
    return None


def live_freigabe(vps: dict[str, Any], ticket: int) -> bool:
    """True, wenn Davids Live-Zettel ``<ordner>/<ticket>`` existiert und jünger als 24 h ist."""
    zettel = Path(str(vps.get("freigabe_ordner") or FREIGABE_STANDARD)).expanduser() / str(ticket)
    try:
        return time.time() - zettel.stat().st_mtime < FREIGABE_MAX_ALTER_S
    except OSError:
        return False


def regel_vps(
    repo: Path,
    ticket: int,
    eigene: list[Commit],
    vps: dict[str, Any],
    kopf: str | None,
    log_zeilen: list[dict],
) -> Verstoss | None:
    if kopf is None or not eigene:
        return None
    muster = [str(m) for m in vps.get("deploy_pfade") or []]
    relevant = [c for c in eigene if any(_passt(p, muster) for _, p in _dateien(repo, c.sha))]
    if not relevant and any(z.get("typ") == "deploy_phase" for z in log_zeilen):
        relevant = eigene[:1]  # jüngster Ticket-Commit
    for commit in relevant:
        code, _ = _git(repo, "merge-base", "--is-ancestor", commit.sha, kopf)
        if code == 1:
            # Seit 22.09. deployen Bau-Sessions nur nach Staging; live nur mit Zettel.
            staging = staging_kopf(vps)
            if staging and not live_freigabe(vps, ticket):
                auf_staging, _ = _git(repo, "merge-base", "--is-ancestor", commit.sha, staging)
                if auf_staging == 0:
                    continue
                if auf_staging != 1:
                    # Staging-Stand lokal noch nicht geholt (#362): nicht als „fehlt“ werten.
                    log.warning(
                        "Staging-Stand %s lokal unbekannt — Regel vps für #%s übersprungen.",
                        staging[:7],
                        ticket,
                    )
                    return None
            return Verstoss(
                ticket,
                "vps_ungleich_origin",
                f"Commit {commit.sha[:7]} fehlt auf dem VPS (HEAD {kopf[:7]})",
            )
        if code != 0:
            log.warning(
                "VPS-HEAD %s lokal unbekannt — Regel vps für #%s übersprungen.",
                kopf[:7],
                ticket,
            )
            return None
    return None


def _zeit(text: Any) -> datetime | None:
    if not text:
        return None
    try:
        wert = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)


def worktree_ordner(ticket: int, basis: str) -> Path:
    """``<basis>/wt-N``; ohne Basis die Regel des Skills (``config.worktree_pfad``)."""
    if basis:
        return Path(basis).expanduser() / f"wt-{ticket}"
    from . import config

    regel = getattr(config, "worktree_pfad", None)
    if callable(regel):
        return Path(regel(ticket))
    wurzel = os.environ.get("BAU_WT_DIR") or ("C:/dev" if sys.platform == "win32" else "~/wt")
    return Path(wurzel).expanduser() / f"wt-{ticket}"


def worktree_spur(wt: Path, ref: str) -> tuple[float | None, str]:
    """(jüngste Änderung als Unix-Zeit, Kurzinfo) eines Worktrees."""
    if not wt.is_dir():
        return None, "-"
    code, status = _git(wt, "status", "--porcelain", "-uall")
    if code != 0:
        return None, "wt:?"
    zeiten: list[float] = []
    zeilen = [z for z in status.splitlines() if z.strip()]
    for zeile in zeilen:
        pfad = zeile[3:].split(" -> ")[-1].strip().strip('"')
        try:
            zeiten.append((wt / pfad).stat().st_mtime)
        except OSError:
            continue
    code, eigen = _git(wt, "log", f"{ref}..HEAD", "-1", "--format=%ct")
    if code == 0 and eigen.strip().isdigit():
        zeiten.append(float(eigen.strip()))
    return (max(zeiten) if zeiten else None), f"wt:{len(zeilen)}dirty"


def regel_verwaist(
    ticket: int,
    issue: dict[str, Any],
    eigene: list[Commit],
    log_zeilen: list[dict],
    wt_zeit: float | None,
    stunden: float,
    jetzt: datetime,
    *,
    checkpoint: str = "",
) -> Verstoss | None:
    if not issue.get("assignees"):
        return None
    if checkpoint and checkpoint in label_namen(issue):
        return None  # Checkpoint-Ticket wartet absichtlich auf einen Menschen
    datiert = [(t, z) for z in log_zeilen if (t := _zeit(z.get("ts")))]
    if datiert and max(datiert, key=lambda paar: paar[0])[1].get("typ") == "blockiert":
        return None  # Session hat „blockiert“ gemeldet — wartet, ist nicht tot
    spuren: list[datetime] = [datetime.fromtimestamp(c.zeit, timezone.utc) for c in eigene]
    spuren += [t for t in (_zeit(z.get("ts")) for z in log_zeilen) if t]
    if wt_zeit is not None:
        spuren.append(datetime.fromtimestamp(wt_zeit, timezone.utc))
    if not spuren:
        seit = _zeit(issue.get("updated_at"))
        spuren = [seit] if seit else []
    if not spuren:
        return None
    ruhe = jetzt - max(spuren)
    if ruhe <= timedelta(hours=stunden):
        return None
    std = ruhe.total_seconds() / 3600
    return Verstoss(
        ticket,
        "session_verwaist",
        f"seit {std:.1f} h keine Spur (Commit, Bau-Log, Worktree) — Session tot?",
        kritisch=True,
    )


# --- Bau-Log --------------------------------------------------------------------


def label_namen(issue: dict[str, Any]) -> set[str]:
    """Label-Namen eines Issues (GitHub liefert Objekte, zur Not auch reine Namen)."""
    namen: set[str] = set()
    for label in issue.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else label
        if name:
            namen.add(str(name))
    return namen


def _zeilen_aus(text: str, quelle: str) -> list[dict[str, Any]]:
    zeilen: list[dict[str, Any]] = []
    for nr, roh in enumerate(text.splitlines(), 1):
        roh = roh.strip()
        if not roh:
            continue
        try:
            eintrag = json.loads(roh)
        except ValueError:
            eintrag = None
        if not isinstance(eintrag, dict):
            log.warning("Kaputte Bau-Log-Zeile %s:%s — als „kaputt“ gezählt.", quelle, nr)
            eintrag = {"typ": "kaputt"}
        zeilen.append(eintrag)
    return zeilen


def log_vom_ref(repo: Path, ref: str, ticket: int) -> list[dict[str, Any]]:
    """Bau-Log-Zeilen eines Tickets, wie sie auf ``ref`` liegen."""
    pfad = f"{bau_log.LOG_ORDNER.as_posix()}/{ticket}.jsonl"
    code, text = _git(repo, "show", f"{ref}:{pfad}")
    if code != 0:
        return []
    return _zeilen_aus(text, f"{ref}:{pfad}")


def lauf_zeilen(datei: Path) -> list[dict[str, Any]] | None:
    """Zeilen einer unversionierten Laufdatei; ``None`` = Datei gibt es nicht."""
    if not datei.is_file():
        return None
    try:
        text = datei.read_text(encoding="utf-8", errors="replace")
    except OSError as fehler:
        log.warning("Laufdatei %s nicht lesbar: %s", datei, fehler)
        return None
    return _zeilen_aus(text, str(datei))


def laufdateien(repo: Path, wt: Path, ticket: int) -> list[tuple[str, Path]]:
    """(Quelle, Pfad) der Laufdateien: Ticket-Worktree und Repo-Ordner, ohne Doppel."""
    paare = [
        ("wt", wt / bau_log.LAUF_ORDNER / f"{ticket}.jsonl"),
        ("repo", repo / bau_log.LAUF_ORDNER / f"{ticket}.jsonl"),
    ]
    gesehen: set[str] = set()
    ergebnis = []
    for quelle, pfad in paare:
        schluessel = os.path.normcase(os.path.abspath(pfad))
        if schluessel not in gesehen:
            gesehen.add(schluessel)
            ergebnis.append((quelle, pfad))
    return ergebnis


def _roh(z: dict[str, Any]) -> str:
    return json.dumps(z, sort_keys=True, ensure_ascii=False)


def _uhr(ts: Any) -> str:
    zeit = _zeit(ts)
    return zeit.astimezone().strftime("%H:%M") if zeit else "--:--"


def _kurz(text: Any, grenze: int = 140) -> str:
    wert = " ".join(str(text or "").split())
    return wert if len(wert) <= grenze else wert[: grenze - 1] + "…"


def entscheidung_teile(z: dict[str, Any]) -> tuple[str, str, str]:
    """(Frage, Wahl, Grund) einer ``entscheidung``-Zeile (alte Felder als Rückfall)."""
    frage = z.get("frage") or z.get("text") or ""
    wahl = z.get("wahl") or z.get("entscheidungen") or ""
    return _kurz(frage), _kurz(wahl), _kurz(z.get("grund"))


def verdichte(ticket: int, z: dict[str, Any]) -> str:
    typ = str(z.get("typ") or "?")
    if typ == "entscheidung":
        inhalt = " · ".join(t for t in entscheidung_teile(z) if t)
    elif typ == "deploy_phase":
        inhalt = " ".join(str(z.get(k)) for k in ("phase", "status", "ergebnis", "grund") if z.get(k))
    else:
        inhalt = next((str(z[k]) for k in ("grund", "text", "umfang", "nach") if z.get(k)), "")
    return _kurz(f"#{ticket} {_uhr(z.get('ts'))} {typ}: {inhalt}".rstrip(": "), 200)


def ist_gate_rot(z: dict[str, Any]) -> bool:
    if z.get("typ") != "deploy_phase":
        return False
    return any(str(z.get(k) or "").strip().lower() in ROT_WERTE for k in ("status", "phase", "ergebnis"))


# --- Übersicht ------------------------------------------------------------------


def _zelle(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def entscheidungs_datei(repo: Path, spec: int) -> Path:
    return repo / "docs" / "agents" / f"entscheidungen_{spec}.md"


def _tabellen_kopf(spec: int, quelle: str) -> list[str]:
    return [
        f"# Entscheidungen Spec #{spec}",
        "",
        f"Stand {datetime.now().astimezone():%d.%m.%Y %H:%M} · Quelle: {quelle} (to-spawn capo).",
        "",
        "| Ticket | Zeit | Frage | Wahl | Grund |",
        "|---|---|---|---|---|",
    ]


def entscheidung_anhaengen(
    repo: Path,
    spec: int,
    ticket: int,
    frage: str,
    wahl: str,
    grund: str,
    wann: datetime,
) -> Path:
    """Eine einzelne Entscheidung an ``entscheidungen_<S>.md`` anhängen (#285).

    ponytail: die Zeile steht nur hier und in der unversionierten Laufdatei; beim
    Spec-Abschluss schreibt :func:`uebersicht` die Tabelle aus den versionierten
    Bau-Logs neu. Dauerhaft wäre sie erst, wenn die Session sie mit ``eintrag``
    in ihr Bau-Log übernimmt.
    """
    datei = entscheidungs_datei(repo, spec)
    zeile = f"| #{ticket} | {wann.astimezone():%d.%m. %H:%M} | {_zelle(frage)} | {_zelle(wahl)} | {_zelle(grund)} |"
    datei.parent.mkdir(parents=True, exist_ok=True)
    alt = datei.read_text(encoding="utf-8") if datei.is_file() else ""
    if "| Ticket | Zeit |" in alt:
        with datei.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(zeile + "\n")
    else:
        datei.write_text(
            "\n".join([*_tabellen_kopf(spec, "Aufseher-Annahmen"), zeile]) + "\n",
            encoding="utf-8",
        )
    return datei


def uebersicht(repo: Path, ref: str | None, spec: int, tickets: list[int]) -> Path:
    """Schreibt ``docs/agents/entscheidungen_<S>.md`` (alle Entscheidungen der Spec)."""
    zeilen = [
        f"# Entscheidungen Spec #{spec}",
        "",
        f"Stand {datetime.now().astimezone():%d.%m.%Y %H:%M} · Quelle: Bau-Logs auf {ref or '-'} (to-spawn capo).",
        "",
        "| Ticket | Zeit | Frage | Wahl | Grund |",
        "|---|---|---|---|---|",
    ]
    anzahl = 0
    for ticket in sorted(tickets):
        for z in log_vom_ref(repo, ref, ticket) if ref else []:
            if z.get("typ") != "entscheidung":
                continue
            zeit = _zeit(z.get("ts"))
            wann = zeit.astimezone().strftime("%d.%m. %H:%M") if zeit else "-"
            frage, wahl, grund = entscheidung_teile(z)
            zeilen.append(f"| #{ticket} | {wann} | {_zelle(frage)} | {_zelle(wahl)} | {_zelle(grund)} |")
            anzahl += 1
    if not anzahl:
        zeilen += ["", "_Keine Entscheidungen im Bau-Log._"]
    datei = repo / "docs" / "agents" / f"entscheidungen_{spec}.md"
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return datei


# --- GitHub ---------------------------------------------------------------------


def kinder(gh_repo: str, spec: int) -> list[dict[str, Any]] | None:
    daten = gh.json_lauf(["api", f"repos/{gh_repo}/issues/{spec}/sub_issues?per_page=100"])
    return daten if isinstance(daten, list) else None


# --- Folge-Runde nach dem Wiederöffnen (#284) -----------------------------------

#: Höchstzahl Folge-Runden je Ticket (Vorgabe, ``waechter.folgerunden_max``).
FOLGERUNDEN_MAX = 2
#: Minuten, in denen eine frische Worktree-Spur als „da baut noch jemand“ zählt.
#: ponytail: nur der Weg ohne tmux; genauer würde es mit der Prozessliste aus
#: ``skripte/sessions_stand.py`` — die kostet je Tick einen vollen Prozess-Scan.
LAEUFT_MIN = 20.0
TMUX_ZEIT_S = 15
#: Obergrenze für den ``--auftrag``-Text auf der Kommandozeile (#285).
AUFTRAG_MAX_ZEICHEN = 600


def folgerunden_max(waechter: dict[str, Any]) -> int:
    """``waechter.folgerunden_max``; fehlt der Wert, gilt :data:`FOLGERUNDEN_MAX`."""
    roh = waechter.get("folgerunden_max")
    if roh is None or (isinstance(roh, str) and not roh.strip()):
        return FOLGERUNDEN_MAX
    try:
        wert = int(roh)
    except (TypeError, ValueError):
        log.warning(
            "waechter.folgerunden_max=%r ist keine Zahl — nehme %s.",
            roh,
            FOLGERUNDEN_MAX,
        )
        return FOLGERUNDEN_MAX
    return max(wert, 0)


def _tmux_befehl() -> list[str]:
    """``tmux`` — oder der Ersatz aus ``TO_SPAWN_TMUX`` (JSON-Liste oder ein Pfad)."""
    roh = os.environ.get("TO_SPAWN_TMUX", "").strip()
    if not roh:
        return ["tmux"]
    if roh.startswith("["):
        try:
            teile = json.loads(roh)
        except ValueError:
            log.warning("TO_SPAWN_TMUX=%r ist keine JSON-Liste — nehme den Text als Pfad.", roh)
            return [roh]
        return [str(teil) for teil in teile]
    return [roh]


def tmux_fenster(spec: int) -> list[str] | None:
    """Fensternamen der tmux-Session ``spec-<S>``.

    ``None`` = kein tmux da (dann läuft die Folge-Runde lokal), ``[]`` = tmux da,
    aber (noch) keine Session für diese Spec.
    """
    befehl = [*_tmux_befehl(), "list-windows", "-t", f"=spec-{spec}", "-F", "#W"]
    try:
        fertig = subprocess.run(
            befehl,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=TMUX_ZEIT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as fehler:
        log.info("tmux nicht nutzbar (%s) — Folge-Runde liefe lokal.", fehler)
        return None
    if fertig.returncode != 0:
        log.info(
            "tmux list-windows für spec-%s: Exit %s (%s)",
            spec,
            fertig.returncode,
            (fertig.stderr or "").strip()[:120],
        )
        return []
    return [zeile.strip() for zeile in fertig.stdout.splitlines() if zeile.strip()]


def laeuft_noch(ticket: int, fenster: list[str] | None, wt_zeit: float | None, jetzt: datetime) -> str:
    """Kurzgrund, wenn an diesem Ticket noch jemand baut — sonst ``""``.

    Mit tmux entscheidet allein die Fensterliste (``bau <N>``). Ohne tmux zählt
    eine frische Worktree-Spur wie in :func:`regel_verwaist`, nur mit kurzer Frist.
    """
    if fenster is not None:
        return f"tmux-Fenster »bau {ticket}«" if f"bau {ticket}" in fenster else ""
    if wt_zeit is None:
        return ""
    ruhe = jetzt - datetime.fromtimestamp(wt_zeit, timezone.utc)
    if ruhe <= timedelta(minutes=LAEUFT_MIN):
        return f"frische Worktree-Spur ({ruhe.total_seconds() / 60:.0f} min alt)"
    return ""


def _bau_skript(repo: Path) -> Path:
    """``scripts/bau.py`` des Repos (Weiterleitung), sonst das Skript im Skill."""
    im_repo = repo / "scripts" / "bau.py"
    if im_repo.is_file():
        return im_repo
    return Path(__file__).resolve().parent.parent / "skripte" / "bau.py"


def bau_startzeile(repo: Path, ticket: int, auftrag: str = "", *schalter: str) -> str:
    """Shell-Zeile, mit der ein tmux-Fenster eine Bau-Session startet (``bash -lc``).

    Einziger Ort für den Vorspann (``REPO``/``TO_SPAWN_HOME`` wie ``skripte/spawn_srv.sh``,
    sonst startet ``bau`` in einem anderen Repo, #212). Die Konfiguration der Session
    (Settings, MCP, Session-ID, Staffel-Umgebung) liefert allein ``bau.py``. ``schalter``
    hängt weitere ``bau``-Schalter an (respawn #431: ``--ohne-prompt``).
    """
    skill = Path(__file__).resolve().parent.parent
    vorspann = f"REPO={shlex.quote(str(repo))} TO_SPAWN_HOME={shlex.quote(str(skill))}"
    if auftrag.strip():
        vorspann += f" BAU_AUFTRAG={shlex.quote(_kurz(auftrag, AUFTRAG_MAX_ZEICHEN))}"
    return f"export {vorspann}; {shlex.join(['bau', str(ticket), '--sofort', *schalter])}"


def folge_befehl(repo: Path, spec: int, ticket: int, fenster: list[str] | None, auftrag: str = "") -> list[str]:
    """Startbefehl der Folge-Runde: tmux-Fenster wie ``spawn_srv.sh``, ohne tmux lokal.

    Der Vorspann (``REPO``/``TO_SPAWN_HOME``) ist derselbe wie in
    ``skripte/spawn_srv.sh`` — sonst startet ``bau`` in einem anderen Repo (#212).
    ``auftrag`` (#285) reicht den Grund der Wiederöffnung über ``BAU_AUFTRAG``
    durch — als Umgebungsvariable, damit der Startbefehl selbst gleich bleibt.
    """
    if fenster is None:
        return [sys.executable, str(_bau_skript(repo)), str(ticket), "--sofort"]
    innen = bau_startzeile(repo, ticket, auftrag)
    kopf = ["new-window", "-t", f"=spec-{spec}"] if fenster else ["new-session", "-d", "-s", f"spec-{spec}"]
    return [
        *_tmux_befehl(),
        *kopf,
        "-n",
        f"bau {ticket}",
        "-c",
        str(repo),
        "bash",
        "-lc",
        innen,
    ]


def folge_umgebung(auftrag: str) -> dict[str, str]:
    """Umgebung der lokalen Folge-Runde: Auftrag als ``BAU_AUFTRAG`` (#285)."""
    if not auftrag.strip():
        return dict(os.environ)
    return {**os.environ, "BAU_AUFTRAG": _kurz(auftrag, AUFTRAG_MAX_ZEICHEN)}


def ereignis_vorfall(art: str, ticket: int, jetzt: datetime) -> vorfall.Vorfall:
    """Stillstand ohne Regel-Verstoß (#284/#285) als Vorfall für den Fehlerkatalog.

    Der Fall selbst steht schon am Ticket (Kommentar, Mail, Bau-Log-Entscheidung);
    hier entsteht nur die Katalog-Zeile mit den festen Worten aus REGEL_VORFALL,
    damit :func:`katalog_pflegen` jede Art genau einmal lernt.
    """
    klasse, symptom, ursache, loesung = REGEL_VORFALL.get(art, VORFALL_UNBEKANNT)
    return vorfall.Vorfall(
        klasse=klasse,
        symptom=symptom,
        ursache=ursache,
        loesung=loesung,
        beispiel=f"#{ticket} {jetzt.astimezone():%d.%m.%Y}",
        regel=art,
        ticket=str(ticket),
        quelle="capo",
    )


def _starte_folge_runde(repo: Path, ticket: int, befehl: list[str], ueber_tmux: bool, auftrag: str = "") -> str:
    """Folge-Runde starten; ``""`` = geklappt, sonst der Grund des Fehlschlags."""
    if ueber_tmux:
        fertig = subprocess.run(
            befehl,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=TMUX_ZEIT_S,
            check=False,
        )
        if fertig.returncode == 0:
            return ""
        return f"tmux Exit {fertig.returncode}: {(fertig.stderr or '').strip()[:120]}"
    ordner = repo / ".to-spawn"
    ordner.mkdir(parents=True, exist_ok=True)
    protokoll = ordner / f"folgerunde-{ticket}.log"
    with protokoll.open("a", encoding="utf-8") as fh:
        subprocess.Popen(
            befehl,
            cwd=str(repo),
            env=folge_umgebung(auftrag),
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return ""


def _folge_runde(
    repo: Path,
    konfig: dict[str, Any],
    gh_repo: str,
    spec: int,
    n: int,
    funde: list[Verstoss],
    folgerunden: dict[str, int],
    *,
    wt_zeit: float | None,
    jetzt: datetime,
    dry_run: bool,
    gelernt: list[vorfall.Vorfall],
) -> list[str]:
    """Nach dem Wiederöffnen weiterbauen lassen (#284).

    Baut noch jemand am Ticket, passiert nichts — diese Session sieht den
    Aufseher-Kommentar. Sonst startet hier die nächste Runde ``bau <N> --sofort``,
    höchstens ``waechter.folgerunden_max`` Mal je Ticket.
    """
    waechter = konfig.get("waechter", {}) if isinstance(konfig.get("waechter"), dict) else {}
    grenze = folgerunden_max(waechter)
    grund = "; ".join(f"{f.regel} — {f.text}" for f in funde) or "Aufseher-Verstoß"
    fenster = tmux_fenster(spec)
    laeuft = laeuft_noch(n, fenster, wt_zeit, jetzt)
    if laeuft:
        return [f"#{n} läuft bereits ({laeuft}) — keine Folge-Runde"]
    schon = int(folgerunden.get(str(n), 0) or 0)
    if schon >= grenze:
        zeilen = [f"#{n} Folge-Runden-Grenze erreicht ({schon}/{grenze}) — keine neue Runde, ein Mensch muss ran"]
        zeilen += _melde(
            repo,
            konfig,
            gh_repo,
            dry_run,
            "session_tot",
            f"Folge-Runden-Grenze #{n}",
            f"Ticket #{n} ist wieder offen, aber die Grenze von {grenze} Folge-Runden "
            f"ist erreicht — es baut niemand weiter. Grund der Wiederöffnung: {grund}",
            f"folgerunden_max|{n}|{schon}",
        )
        if not dry_run:
            gelernt.append(ereignis_vorfall("folgerunden_grenze", n, jetzt))
        return zeilen
    befehl = folge_befehl(repo, spec, n, fenster, grund)
    if dry_run:
        return [f"#{n} [Probe] würde Folge-Runde starten: {shlex.join(befehl)}"]
    try:
        fehler = _starte_folge_runde(repo, n, befehl, fenster is not None, grund)
    except (OSError, subprocess.SubprocessError) as ausnahme:
        fehler = str(ausnahme)
    if fehler:
        log.warning("Folge-Runde für #%s nicht gestartet: %s", n, fehler)
        return [f"#{n} FEHLER: Folge-Runde nicht gestartet ({fehler})"]
    folgerunden[str(n)] = schon + 1
    weg = "tmux-Fenster" if fenster is not None else "lokal"
    return [f"#{n} Folge-Runde gestartet ({weg}, {schon + 1}/{grenze}) — Auftrag: {_kurz(grund)}"]


# --- Checkpoint-Annahme nach Doktrin (#285) -------------------------------------

#: Minuten ohne Davids Antwort, nach denen der Vorschlag der Session gilt.
CHECKPOINT_FRIST_MIN = 60.0
#: „Vorschlag: …“ in einem Issue-Kommentar — alles danach ist die vorgeschlagene Wahl.
_VORSCHLAG = re.compile(r"Vorschlag\s*:\s*(.+)", re.IGNORECASE | re.DOTALL)
#: Anfang jedes Aufseher-Kommentars — eigene Kommentare sind nie „Davids Antwort“.
WAECHTER_KOPF = befund.KOPF
#: Frühere Marke vor der Umbenennung (#428) — alte Issue-Kommentare bleiben eigene Kommentare.
ALTE_AUFSEHER_KOEPFE: tuple[str, ...] = ("Wächter:",)  # #428-alt
#: Alle Marken des Aufsehers (neu + alt) — z. B. für den Leitstand.
AUFSEHER_KOEPFE: tuple[str, ...] = (WAECHTER_KOPF, *ALTE_AUFSEHER_KOEPFE)
#: Anfang der Issue-Kommentare des Aufpassers (to_spawn/aufpasser.py), gleiches gh-Konto.
AUFPASSER_KOPF = "Aufpasser:"
#: Anfang jedes Issue-Kommentars einer Bau-Session (#402). Session, Aufseher und David
#: kommentieren mit demselben gh-Konto — der Login trennt sie nicht, nur diese Marke.
#: Der Bau-Prompt bekommt sie über den Platzhalter ``{SESSION_KOPF}`` (skripte/bau.py).
SESSION_KOPF = "Bau-Session:"
#: Marken aller eigenen Kommentare ohne Bau-Session (die prüft ``_ist_session_kommentar``):
#: so beginnende Kommentare sind nie „Davids Antwort“.
EIGENE_KOEPFE: tuple[str, ...] = (*AUFSEHER_KOEPFE, AUFPASSER_KOPF)


@dataclass
class Vorschlag:
    """Was die Session selbst vorgeschlagen hat (Bau-Log oder Issue-Kommentar)."""

    frage: str
    wahl: str
    grund: str
    zeit: datetime
    autor: str = ""


def checkpoint_frist(waechter: dict[str, Any]) -> float:
    """``waechter.checkpoint_frist_min``; fehlt der Wert, gilt :data:`CHECKPOINT_FRIST_MIN`."""
    roh = waechter.get("checkpoint_frist_min")
    if roh is None or (isinstance(roh, str) and not str(roh).strip()):
        return CHECKPOINT_FRIST_MIN
    try:
        wert = float(roh)
    except (TypeError, ValueError):
        log.warning(
            "waechter.checkpoint_frist_min=%r ist keine Zahl — nehme %s min.",
            roh,
            CHECKPOINT_FRIST_MIN,
        )
        return CHECKPOINT_FRIST_MIN
    return max(wert, 0.0)


#: ``author_association``-Werte, deren Kommentare der Checkpoint überhaupt liest (#402).
#: Ein Dritter im öffentlichen Repo könnte sonst mit der Marke :data:`SESSION_KOPF`
#: einen Vorschlag setzen oder ``seit`` hinter Davids Antwort schieben.
BETEILIGTE_ROLLEN = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def issue_kommentare(gh_repo: str, ticket: int) -> list[dict[str, Any]] | None:
    """Kommentare eines Issues von Repo-Beteiligten (``None`` = Abfrage gescheitert).

    Nur Kommentare mit ``author_association`` aus :data:`BETEILIGTE_ROLLEN` kommen
    zurück — für Vorschlag, Fragezeit und Antwort gleichermaßen. Fehlt das Feld,
    zählt der Kommentar nicht (sichere Richtung: lieber keine Annahme).
    """
    daten = gh.json_lauf(["api", f"repos/{gh_repo}/issues/{ticket}/comments?per_page=100"])
    if not isinstance(daten, list):
        return None
    beteiligt = [e for e in daten if isinstance(e, dict) and e.get("author_association") in BETEILIGTE_ROLLEN]
    if len(beteiligt) < len(daten):
        log.info("#%s: %d Kommentar(e) von Nicht-Beteiligten ignoriert", ticket, len(daten) - len(beteiligt))
    return beteiligt


def _kommentar_teile(eintrag: dict[str, Any]) -> tuple[str, str, datetime | None]:
    """(Autor, Text, Zeit) eines Kommentars."""
    nutzer = eintrag.get("user")
    autor = str(nutzer.get("login") or "") if isinstance(nutzer, dict) else ""
    return autor, str(eintrag.get("body") or ""), _zeit(eintrag.get("created_at"))


def _ist_session_kommentar(text: str) -> bool:
    """Trägt der Kommentar die Marke :data:`SESSION_KOPF` am Anfang?"""
    return text.lstrip().startswith(SESSION_KOPF)


def checkpoint_vorschlag(zeilen: list[dict[str, Any]], kommentare: list[dict[str, Any]]) -> Vorschlag | None:
    """Jüngster eigener Vorschlag der Session — ``None`` heißt: nie raten.

    Zählt eine Bau-Log-Zeile ``entscheidung`` mit Wahl und ein Issue-Kommentar, der
    mit :data:`SESSION_KOPF` beginnt und „Vorschlag:“ enthält. Der Login zählt nicht:
    Session und David schreiben mit demselben gh-Konto (#402). Kommentare ohne Marke
    (auch Aufseher-Kommentare) sind nie ein Session-Vorschlag.
    """
    kandidaten: list[Vorschlag] = []
    for z in zeilen:
        if z.get("typ") != "entscheidung":
            continue
        frage, wahl, grund = entscheidung_teile(z)
        zeit = _zeit(z.get("ts"))
        if wahl and zeit:
            kandidaten.append(Vorschlag(frage, wahl, grund, zeit))
    for eintrag in kommentare:
        autor, text, zeit = _kommentar_teile(eintrag)
        if zeit is None or not _ist_session_kommentar(text):
            continue
        treffer = _VORSCHLAG.search(text)
        if treffer:
            kandidaten.append(
                Vorschlag(
                    frage=_kurz(text[: treffer.start()]),
                    wahl=_kurz(treffer.group(1), 400),
                    grund="",
                    zeit=zeit,
                    autor=autor,
                )
            )
    return max(kandidaten, key=lambda v: v.zeit) if kandidaten else None


def checkpoint_frage_zeit(zeilen: list[dict[str, Any]], kommentare: list[dict[str, Any]]) -> datetime | None:
    """Wann hat die Session zuletzt etwas gefragt/gemeldet? ``None`` = keine Spur.

    Spuren der Session sind nur Bau-Log-Zeilen ``blockiert``/``entscheidung`` und
    Kommentare mit :data:`SESSION_KOPF`. Davids Hinweise und Aufseher-Kommentare sind
    keine Frage (#451) — sonst meldet capo „Checkpoint wartet“ für ein Ticket, an dem
    nie eine Session gebaut hat. Ohne Spur wartet niemand, das Label allein löst nichts aus.
    """
    zeiten = [_zeit(z.get("ts")) for z in zeilen if z.get("typ") in ("blockiert", "entscheidung")]
    for eintrag in kommentare:
        _, text, zeit = _kommentar_teile(eintrag)
        if _ist_session_kommentar(text):
            zeiten.append(zeit)
    echte = [z for z in zeiten if z is not None]
    return max(echte) if echte else None


def davids_antwort(kommentare: list[dict[str, Any]], seit: datetime) -> str:
    """Login des ersten Kommentars nach ``seit`` ohne Session- oder Aufseher-Marke.

    ``""`` = keine Antwort. Der Login trennt nicht (gleiches gh-Konto, #402) — nur die
    Marken :data:`SESSION_KOPF` und :data:`EIGENE_KOEPFE` (Aufseher, Aufpasser).
    """
    for eintrag in sorted(
        kommentare, key=lambda e: _kommentar_teile(e)[2] or datetime.min.replace(tzinfo=timezone.utc)
    ):
        autor, text, zeit = _kommentar_teile(eintrag)
        if zeit is None or zeit <= seit:
            continue
        if text.lstrip().startswith(EIGENE_KOEPFE) or _ist_session_kommentar(text):
            continue
        return autor or "jemand"
    return ""


def mensch_noetig_was(eintrag: dict[str, Any]) -> str:
    """Kurztext eines „Mensch nötig“-Eintrags; alte Zeilen ohne ``grund`` gelten als beleg_rot.

    Fehlt ``fix_runde`` (alte Einträge), steht „?“ statt „None“ (#402).
    """
    if eintrag.get("grund") == "review_offen":
        return "Review angefordert, aber kein Beleg geschrieben"
    runden = eintrag.get("fix_runde")
    return f"Review-Beleg nach {'?' if runden is None else runden} Fixrunden noch rot"


def _checkpoint(
    repo: Path,
    konfig: dict[str, Any],
    gh_repo: str,
    spec: int,
    n: int,
    zeilen: list[dict[str, Any]],
    erledigt: set[str],
    *,
    checkpoint: str,
    jetzt: datetime,
    dry_run: bool,
    gelernt: list[vorfall.Vorfall],
) -> list[str]:
    """Nacht-Checkpoint: nach der Frist gilt der Vorschlag der Session (#285).

    David kann jede Annahme kippen — der Aufseher kommentiert sie am Ticket, schreibt
    sie ins Bau-Log und in ``entscheidungen_<S>.md`` und schickt eine Mail. Ohne
    erkennbaren eigenen Vorschlag der Session wird nichts angenommen, nur gemeldet.
    Solange ein Blocker offen ist, ruht der Checkpoint (keine Zeile); ist der
    Blocker-Stand nicht lesbar, kommt eine FEHLER-Zeile statt einer Annahme (#451).
    """
    waechter = konfig.get("waechter", {}) if isinstance(konfig.get("waechter"), dict) else {}
    frist = checkpoint_frist(waechter)
    kommentare = issue_kommentare(gh_repo, n)
    if kommentare is None:
        return [f"#{n} FEHLER: Kommentare nicht lesbar — Checkpoint ungeprüft"]
    vorschlag = checkpoint_vorschlag(zeilen, kommentare)
    frage_zeit = checkpoint_frage_zeit(zeilen, kommentare)
    if vorschlag is None and frage_zeit is None:
        return []  # Label gesetzt, aber noch keine Frage gestellt
    # Erst nach der Spur fragen: spart den gh-Aufruf und macht capo ohne Frage nie rot.
    blocker = gh.blocked_by(gh_repo, str(n))
    if blocker is None:
        return [f"#{n} FEHLER: Blocker nicht lesbar — Checkpoint ungeprüft"]
    if not all(isinstance(b, dict) and b.get("state") for b in blocker):
        return [f"#{n} FEHLER: Blocker-Format unbekannt — Checkpoint ungeprüft"]
    if any(str(b["state"]).lower() != "closed" for b in blocker):
        return []  # #451: noch blockiert — Checkpoint ruht, bis alle Blocker zu sind
    seit = vorschlag.zeit if vorschlag else frage_zeit
    if seit is None:
        return []
    wartet = (jetzt - seit).total_seconds() / 60
    if wartet <= frist:
        return [f"#{n} Checkpoint wartet ({wartet:.0f} von {frist:.0f} min)"]
    antwort = davids_antwort(kommentare, seit)
    if antwort:
        # Ohne Marke heißt nur: nicht von einer Session mit neuem Prompt — kann David
        # sein oder eine Session mit altem Prompt („Probesitz beendet:“). Sichere Richtung.
        return [
            f"#{n} Checkpoint: Kommentar ohne Session-Marke von {antwort} — als Antwort gewertet "
            f"({antwort} hat geantwortet oder Session mit altem Prompt), Vorschlag nicht übernommen"
        ]
    if vorschlag is None:
        zeilen_aus: list[str] = [
            f"#{n} Checkpoint {wartet:.0f} min offen, aber kein Vorschlag der Session "
            f"— keine Annahme (nie raten), gemeldet"
        ]
        zeilen_aus += _melde(
            repo,
            konfig,
            gh_repo,
            dry_run,
            "checkpoint_offen",
            f"Checkpoint ohne Vorschlag #{n}",
            f"Ticket #{n} wartet seit {wartet:.0f} min auf Davids Antwort, aber die "
            "Session hat keinen eigenen Vorschlag hinterlassen — der Aufseher rät nicht.",
            f"checkpoint_offen|{n}|{seit.isoformat()}",
        )
        if not dry_run:
            gelernt.append(ereignis_vorfall("checkpoint_offen", n, jetzt))
        return zeilen_aus
    schluessel = f"{n}|checkpoint_annahme|{seit.isoformat()}"
    if schluessel in erledigt:
        return [f"#{n} Checkpoint schon angenommen — nichts zu tun"]
    if dry_run:
        return [f"#{n} [Probe] würde Vorschlag annehmen: {_kurz(vorschlag.wahl)}"]
    text = f"{WAECHTER_KOPF} Annahme nach {frist:.0f} min nach Doktrin — David kann kippen. Vorschlag: {vorschlag.wahl}"
    if not _gh_ok(["issue", "comment", str(n), "--repo", gh_repo, "--body", text]):
        return [f"#{n} FEHLER: Checkpoint-Annahme nicht kommentiert"]
    erledigt.add(schluessel)
    grund = f"Aufseher-Annahme nach {frist:.0f} min ohne Davids Antwort (Doktrin, kippbar)" + (
        f" · {vorschlag.grund}" if vorschlag.grund else ""
    )
    frage = vorschlag.frage or f"Checkpoint #{n}"
    bau_log.schreibe(repo, n, "entscheidung", frage=frage, wahl=vorschlag.wahl, grund=grund)
    datei = entscheidung_anhaengen(repo, spec, n, frage, vorschlag.wahl, grund, jetzt)
    ausgabe = [f"#{n} Checkpoint-Annahme nach {wartet:.0f} min: {_kurz(vorschlag.wahl)} (Bau-Log + {datei.name})"]
    ausgabe += _melde(
        repo,
        konfig,
        gh_repo,
        dry_run,
        "checkpoint_annahme",
        f"Checkpoint angenommen #{n}",
        f"Ticket #{n}: {frage}\nAngenommen nach {frist:.0f} min ohne Antwort: "
        f"{vorschlag.wahl}\nDavid kann die Entscheidung jederzeit kippen.",
        schluessel,
    )
    gelernt.append(ereignis_vorfall("checkpoint_annahme", n, jetzt))
    if _gh_ok(["issue", "edit", str(n), "--repo", gh_repo, "--remove-label", checkpoint]):
        ausgabe.append(f"#{n} Label {checkpoint} entfernt — die Kette läuft weiter")
    else:
        ausgabe.append(f"#{n} FEHLER: Label {checkpoint} nicht entfernt")
    return ausgabe


# --- Tick -----------------------------------------------------------------------


def _zustand_datei(repo: Path, gh_repo: str, spec: int) -> Path:
    return melder.zustand_ordner() / f"{melder.repo_kennung(repo, gh_repo)}_{spec}.json"


def _sperr_versuch(fh: Any) -> bool | None:
    """Nicht blockierend sperren: ``True`` = gesperrt, ``False`` = belegt, ``None`` = kein Werkzeug."""
    try:
        import fcntl
    except ImportError:
        fcntl = None  # type: ignore[assignment]
    if fcntl is not None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    try:
        import msvcrt
    except ImportError:
        return None
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


def _sperre_frei(fh: Any) -> None:
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return
    except ImportError:
        pass
    except OSError as fehler:
        log.warning("Sperre nicht gelöst: %s", fehler)
        return
    try:
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
    except (ImportError, OSError) as fehler:
        log.warning("Sperre nicht gelöst: %s", fehler)


@contextlib.contextmanager
def sperre(datei: Path, warten_s: float = SPERRE_S) -> Iterator[bool]:
    """Sperre ``<datei>.lock`` für einen Tick; liefert ``False``, wenn ein anderer Lauf sie hält.

    fcntl (Linux/macOS) bzw. msvcrt (Windows); ohne beide eine O_EXCL-Lockdatei.
    """
    pfad = datei.with_name(datei.name + ".lock")
    pfad.parent.mkdir(parents=True, exist_ok=True)
    ende = time.monotonic() + max(warten_s, 0.0)
    with pfad.open("a+b") as fh:
        while True:
            ergebnis = _sperr_versuch(fh)
            if ergebnis is None:
                break  # kein Sperr-Werkzeug → O_EXCL unten
            if ergebnis:
                try:
                    yield True
                finally:
                    _sperre_frei(fh)
                return
            if time.monotonic() >= ende:
                yield False
                return
            time.sleep(0.2)
    exkl = pfad.with_name(pfad.name + ".excl")
    while True:
        try:
            os.close(os.open(str(exkl), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            if time.monotonic() >= ende:
                yield False
                return
            time.sleep(0.2)
    try:
        yield True
    finally:
        with contextlib.suppress(OSError):
            exkl.unlink()


def karenz_minuten(waechter: dict[str, Any]) -> float:
    """``waechter.karenz_minuten``; fehlt der Wert, gilt :data:`KARENZ_MIN`, ``0`` = keine Karenz."""
    roh = waechter.get("karenz_minuten")
    if roh is None or (isinstance(roh, str) and not roh.strip()):
        return KARENZ_MIN
    try:
        wert = float(roh)
    except (TypeError, ValueError):
        log.warning("waechter.karenz_minuten=%r ist keine Zahl — nehme %s min.", roh, KARENZ_MIN)
        return KARENZ_MIN
    if wert < 0:
        log.warning("waechter.karenz_minuten=%r ist negativ — nehme 0 (keine Karenz).", roh)
        return 0.0
    return wert


def schliess_marke(issue: dict[str, Any]) -> str:
    """Kennung eines Schließ-Ereignisses: ``closed_at`` (``?`` ohne Zeit), offen = ``""``."""
    if str(issue.get("state", "")).lower() != "closed":
        return ""
    return str(issue.get("closed_at") or "?")


def ist_ausgangsstand(
    n: int,
    issue: dict[str, Any],
    ausgang: dict[str, Any] | None,
    geschlossen_zeit: datetime | None,
    erster_tick: datetime,
) -> bool:
    """War dieses Schließen schon beim ersten Tick da?

    Maßgeblich ist der beim ersten Tick gemerkte Stand je Ticket (``ausgangsstand``
    in der Zustandsdatei) — kein Uhr-Vergleich: ``closed_at`` kommt von GitHub, der
    erste Tick von der lokalen Uhr, die vorgehen kann. Nur Tickets, die es beim
    ersten Tick noch nicht gab (oder Zustände aus älteren Versionen), fallen auf
    den Zeitvergleich zurück.
    """
    if ausgang is not None and str(n) in ausgang:
        marke = str(ausgang[str(n)] or "")
        return bool(marke) and marke == schliess_marke(issue)
    return geschlossen_zeit is None or geschlossen_zeit < erster_tick


def ist_sofort(konfig: dict[str, Any]) -> bool:
    """Sofort-Modus: keine Karenz, kein Ausgangsstand (Umgebung oder ``waechter.sofort``)."""
    if os.environ.get("TO_SPAWN_WAECHTER_SOFORT", "").strip() == "1":
        return True
    return bool(konfig.get("waechter", {}).get("sofort"))


def _sperr_wartezeit() -> float:
    roh = os.environ.get("TO_SPAWN_CAPO_SPERRE_S", "").strip()
    try:
        return float(roh) if roh else SPERRE_S
    except ValueError:
        log.warning("TO_SPAWN_CAPO_SPERRE_S=%r ist keine Zahl — nehme %s s.", roh, SPERRE_S)
        return SPERRE_S


def vorfall_aus_verstoss(repo: Path, fund: Verstoss, jetzt: datetime) -> vorfall.Vorfall | None:
    """Einen Verstoß als ``vorfall``-Zeile ins Bau-Log des Tickets schreiben.

    ``None`` heißt: derselbe Vorfall steht dort schon (zweiter Tick, gleiche Lage).
    Verstöße aus dem Ausgangsstand laufen hier bewusst nicht durch — sonst lernt
    der Katalog die Altlasten vor dem ersten Tick.
    """
    klasse, symptom, ursache, loesung = REGEL_VORFALL.get(fund.regel, VORFALL_UNBEKANNT)
    beispiel = f"#{fund.ticket} {jetzt.astimezone():%d.%m.%Y}"
    try:
        zeile = vorfall.schreibe(
            repo,
            fund.ticket,
            klasse=klasse,
            symptom=symptom,
            ursache=ursache,
            loesung=loesung,
            beispiel=beispiel,
            regel=fund.regel,
            quelle="capo",
        )
    except (OSError, ValueError) as fehler:  # ein Tick darf daran nie sterben
        log.warning("Vorfall zu #%s (%s) nicht notiert: %s", fund.ticket, fund.regel, fehler)
        return None
    if zeile is None:
        return None
    return vorfall.Vorfall(
        klasse=klasse,
        symptom=symptom,
        ursache=ursache,
        loesung=loesung,
        beispiel=beispiel,
        regel=fund.regel,
        ticket=str(fund.ticket),
        quelle="capo",
    )


def katalog_pflegen(repo: Path, konfig: dict[str, Any], vorfaelle: list[vorfall.Vorfall]) -> list[str]:
    """Neue Vorfälle in den Fehlerkatalog hängen; Rückgabe = Zeilen für den Tick.

    Der Katalog wird gegen parallele Aufseher gesperrt (zwei Specs, eine Datei).
    Ein Repo ganz ohne Katalog ist kein Fehler (Fremd-Repo, #257) — ein fehlender
    Abschnitt, eine kaputte Tabelle oder eine unschreibbare Datei schon: sonst
    meldet der Tick Erfolg, obwohl nichts gelernt wurde.
    """
    if not vorfaelle:
        return []
    pfad = vorfall.katalog_pfad(repo, konfig)
    with sperre(pfad, _sperr_wartezeit()) as frei:
        if not frei:
            return ["Katalog: ein anderer Lauf hält die Datei — dieser Tick lässt sie aus"]
        erg = vorfall.in_katalog(pfad, vorfaelle)
    zeilen: list[str] = []
    if erg.nummern:
        zeilen.append(f"Katalog: {len(erg.nummern)} neue Zeile(n) — {' '.join(erg.nummern)}")
    elif erg.ohne_katalog:
        # Kein Dateiname in der Zeile: der Tick wertet jede Zeile mit „FEHLER“ als
        # roten Lauf, und der Dateiname trägt das Wort schon im Namen.
        zeilen.append("Katalog: dieses Repo führt keinen Fehlerkatalog — nichts eingetragen")
    elif not erg.probleme:
        zeilen.append("Katalog: nichts Neues")
    zeilen += [f"FEHLER: Katalog — {grund}" for grund in erg.probleme]
    return zeilen


#: Regeln, nach denen capo ein geschlossenes Ticket wieder öffnet (#448: Grundlage der Rücknahme).
REOPEN_REGELN = frozenset({"commit_ohne_nummer", "beweis_fehlt", "test_ersetzt", "vps_ungleich_origin"})


def reopen_funde(
    repo: Path,
    ref: str,
    n: int,
    eigene: list[Commit],
    belege: str,
    vps: dict[str, Any],
    kopf: str | None,
    alle_zeilen: list[dict[str, Any]],
) -> list[Verstoss]:
    """Alle Verstöße, die ein geschlossenes Ticket wieder öffnen (regel_vps nur mit VPS-Konfig)."""
    funde = [
        regel_commit(n, eigene),
        regel_beweis(repo, ref, n, eigene, belege),
        regel_tests(repo, n, eigene),
        regel_vps(repo, n, eigene, vps, kopf, alle_zeilen) if vps.get("ssh") else None,
    ]
    return [f for f in funde if f]


def _gesehene_schliessungen(n: int, erledigt: set[str]) -> list[datetime]:
    """Schließ-Zeitpunkte von #n, die ein Tick gesehen hat (Schlüssel ``geschlossen|N|closed_at``)."""
    zeiten: list[datetime] = []
    for schluessel in erledigt:
        teile = schluessel.split("|")
        if len(teile) == 3 and teile[0] == "geschlossen" and teile[1] == str(n):
            zeit = _zeit(teile[2])
            if zeit is not None:
                zeiten.append(zeit)
    return zeiten


def merke_schliessen(n: int, closed_at: str, erledigt: set[str]) -> bool:
    """Gesehenes Schließen von #n festhalten, sobald capo #n schon einmal geöffnet hat (#448).

    Damit weiß :func:`fehl_reopen_kandidat`, dass ein späteres Offen nicht mehr aus
    capos Reopen stammt. Ohne eigenes Reopen bleibt der Zustand unberührt.
    ``True`` = neuer Schlüssel, der Aufrufer sichert den Zustand.
    """
    schluessel = f"geschlossen|{n}|{closed_at}"
    if not closed_at or schluessel in erledigt:
        return False
    eigenes_reopen = any(
        (teile := s.split("|"))[0] == str(n) and len(teile) == 3 and teile[1] in REOPEN_REGELN
        for s in erledigt
    )
    if not eigenes_reopen:
        return False
    erledigt.add(schluessel)
    return True


def ruecknahme_sperre(n: int, erledigt: set[str], laeuft: str) -> str:
    """Aktionszeile, falls eine an sich fällige Rücknahme jetzt nicht sein darf — sonst ``""``.

    Je Ticket höchstens eine automatische Rücknahme (Schlüssel ``ruecknahme|N``);
    solange an #n gebaut wird (``laeuft`` aus :func:`laeuft_noch`), wartet sie.
    """
    if f"ruecknahme|{n}" in erledigt:
        return f"#{n} Fehl-Reopen erneut — nicht zurückgenommen, Mensch prüfen"
    if laeuft:
        return f"#{n} Rücknahme wartet: Folge-Runde läuft ({laeuft})"
    return ""


def spaeter_geschlossen(gh_repo: str, n: int, stempel: str) -> bool | None:
    """Wurde #n nach dem Schließen ``stempel`` noch einmal geschlossen? (#448, Issue-Verlauf)

    Fängt, was :func:`merke_schliessen` nicht sehen kann: ein Mensch schließt und
    öffnet wieder, bevor ein Tick vorbeikommt. ``None`` = Verlauf nicht lesbar.
    """
    zeit = _zeit(stempel)
    code, ausgabe = gh.lauf(
        [
            "api",
            "--paginate",
            f"repos/{gh_repo}/issues/{n}/timeline?per_page=100",
            "--jq",
            '.[] | select(.event == "closed") | .created_at',
        ]
    )
    if code != 0 or zeit is None:
        log.warning("Issue-Verlauf von #%s nicht lesbar (gh Exit %s).", n, code)
        return None
    return any((spaeter := _zeit(z)) is not None and spaeter > zeit for z in ausgabe.split())


def fehl_reopen_kandidat(
    n: int, issue: dict[str, Any], eigene: list[Commit], erledigt: set[str], *, checkpoint: str
) -> tuple[str, list[str]] | None:
    """(closed_at, Regeln) des jüngsten capo-Reopens von #n, falls eine Rücknahme in Frage kommt (#448).

    Grundlage sind die Schlüssel ``N|Regel|closed_at`` in ``erledigt`` (= capo hat bei
    diesem Schließen wieder geöffnet). ``None``, wenn es keins gibt, es schon
    zurückgenommen ist, ein Ticket-Commit danach liegt (neue Arbeit, kein Prüferfehler),
    das Ticket danach nochmals geschlossen war (:func:`merke_schliessen` — dann stammt
    das jetzige Offen nicht aus capos Reopen) oder ein Label (``waechter:ok``,
    Checkpoint) den Fall ohnehin zum Sonderfall macht.
    Ob die Regeln jetzt grün sind, prüft der Aufrufer.
    """
    labels = label_namen(issue)
    if OK_LABEL in labels or checkpoint in labels:
        return None
    je_stempel: dict[str, list[str]] = {}
    for schluessel in erledigt:
        teile = schluessel.split("|")
        if len(teile) == 3 and teile[0] == str(n) and teile[1] in REOPEN_REGELN:
            je_stempel.setdefault(teile[2], []).append(teile[1])
    mit_zeit = [(zeit, stempel) for stempel in je_stempel if (zeit := _zeit(stempel)) is not None]
    if not mit_zeit:
        return None
    zeit, stempel = max(mit_zeit)
    if f"ruecknahme|{n}|{stempel}" in erledigt:
        return None
    if any(spaeter > zeit for spaeter in _gesehene_schliessungen(n, erledigt)):
        return None  # danach wieder zu (Mensch oder Session) — das jetzige Offen ist nicht capos Reopen
    if any(c.zeit > zeit.timestamp() for c in eigene):
        return None
    return stempel, sorted(je_stempel[stempel])


def tick(
    repo: Path,
    spec: int,
    gh_repo: str,
    konfig: dict[str, Any],
    *,
    wt_basis: str = "",
    dry_run: bool = False,
    katalog: bool = False,
    jetzt: datetime | None = None,
) -> TickErgebnis:
    """Ein Aufseher-Tick: Stand + Delta + Verstöße/Aktionen als Textzeilen.

    Ein echter Tick hält die Sperre der Zustandsdatei; ein Probelauf (``dry_run``)
    schreibt nichts und braucht keine Sperre.
    """
    if dry_run:
        return _tick(repo, spec, gh_repo, konfig, wt_basis, True, katalog, jetzt)
    datei = _zustand_datei(repo, gh_repo, spec)
    with sperre(datei, _sperr_wartezeit()) as frei:
        if not frei:
            erg = TickErgebnis(exit_code=1)
            erg.zeilen.append(
                f"FEHLER: ein anderer capo-Lauf für Spec #{spec} läuft schon "
                f"(Sperre {datei.name}.lock) — Tick übersprungen."
            )
            return erg
        return _tick(repo, spec, gh_repo, konfig, wt_basis, False, katalog, jetzt)


def _tick(
    repo: Path,
    spec: int,
    gh_repo: str,
    konfig: dict[str, Any],
    wt_basis: str,
    dry_run: bool,
    katalog: bool,
    jetzt: datetime | None,
) -> TickErgebnis:
    jetzt = jetzt or datetime.now(timezone.utc)
    erg = TickErgebnis()
    kopfzeilen: list[str] = []
    regeln_aus = _git(repo, "fetch", "-q", "origin")[0] != 0
    if regeln_aus:
        log.warning("git fetch origin gescheitert — Regeln in diesem Tick ausgesetzt.")
        kopfzeilen.append("FEHLER: fetch — Regeln ausgesetzt (kein Wieder-Öffnen, kein Kommentar in diesem Tick).")
    ref = haupt_ref(repo)
    liste = kinder(gh_repo, spec)
    if liste is None:
        erg.zeilen += kopfzeilen
        erg.zeilen.append(f"FEHLER: Sub-Issues von #{spec} in {gh_repo} nicht lesbar (gh).")
        erg.exit_code = 1
        return erg
    if ref is None:
        erg.zeilen += kopfzeilen
        erg.zeilen.append("FEHLER: kein origin/master bzw. origin/main — git fetch prüfen.")
        erg.exit_code = 1
        return erg

    datei = _zustand_datei(repo, gh_repo, spec)
    zustand = melder.lade_json(datei)
    erledigt = set(zustand.get("erledigt") or [])
    gelesen: dict[str, int] = dict(zustand.get("log_zeilen") or {})
    folgerunden: dict[str, int] = dict(zustand.get("folgerunden") or {})
    sofort = ist_sofort(konfig)
    erster_tick = _zeit(zustand.get("erster_tick"))
    if erster_tick is None:
        erster_tick = jetzt  # dieser Tick ist der erste: alles Geschlossene = Ausgangsstand
        zustand["erster_tick"] = jetzt.isoformat(timespec="seconds")
        zustand["ausgangsstand"] = {str(int(i["number"])): schliess_marke(i) for i in liste}
    ausgang = zustand.get("ausgangsstand")
    ausgang = ausgang if isinstance(ausgang, dict) else None
    waechter = konfig.get("waechter", {}) if isinstance(konfig.get("waechter"), dict) else {}
    stunden = float(waechter.get("verwaist_stunden") or 3)
    karenz = 0.0 if sofort else karenz_minuten(waechter)
    regularien = konfig.get("regularien", {}) if isinstance(konfig.get("regularien"), dict) else {}
    belege = str(regularien.get("belege_ordner") or "docs/verify-hard")
    checkpoint = str(regularien.get("checkpoint_label") or "checkpoint:human")
    vps = konfig.get("vps") if isinstance(konfig.get("vps"), dict) else {}
    kopf = vps_kopf(vps) if vps.get("ssh") else None
    vps_fehlt = bool(vps.get("ssh")) and kopf is None
    if vps_fehlt:
        kopfzeilen.append(
            f"FEHLER: VPS-HEAD nicht lesbar ({vps.get('ssh')}) — Regel vps ausgesetzt, Spec-Abschluss zurückgehalten."
        )
    commits = alle_commits(repo, ref)
    _, ref_kurz = _git(repo, "rev-parse", "--short", ref)

    def sichern() -> None:
        """Zustand sofort nach jeder Aktion festhalten (Absturz = keine Doppel-Aktion)."""
        if dry_run:
            return
        zustand.update(
            {
                "erledigt": sorted(erledigt),
                "log_zeilen": gelesen,
                "folgerunden": folgerunden,
            }
        )
        melder.speichere_json(datei, zustand)

    stand: list[str] = []
    delta: list[str] = []
    aktionen: list[str] = []
    gelernt: list[vorfall.Vorfall] = []  # Vorfälle für den Fehlerkatalog (#286)
    offen = 0
    wartet = 0
    for issue in sorted(liste, key=lambda x: int(x["number"])):
        n = int(issue["number"])
        zu = str(issue.get("state", "")).lower() == "closed"
        offen += 0 if zu else 1
        eigene = ticket_commits(commits, n)
        log_zeilen = log_vom_ref(repo, ref, n)
        wt = worktree_ordner(n, wt_basis)
        wt_zeit, wt_info = worktree_spur(wt, ref)
        lauf: list[tuple[str, list[dict[str, Any]]]] = []
        for quelle, pfad in laufdateien(repo, wt, n):
            zeilen = lauf_zeilen(pfad)
            if zeilen is not None:
                lauf.append((quelle, zeilen))
        alle_zeilen = log_zeilen + [z for _, zeilen in lauf for z in zeilen]
        gelernt += vorfall.aus_zeilen(alle_zeilen)  # was Sessions selbst meldeten
        wer = ",".join(a.get("login", "?") for a in issue.get("assignees") or []) or "-"
        commit = eigene[0].sha[:7] if eigene else "-"
        lauf_info = f" lauf:{sum(len(z) for _, z in lauf)}" if lauf else ""
        stand.append(
            f"#{n} {'zu ' if zu else 'OFF'} {wer:<10} commit:{commit} {wt_info:<10} log:{len(log_zeilen)}{lauf_info}"
        )

        # B: Delta der Bau-Log-Zeilen — je Quelle eigener Zähler
        im_lauf = {_roh(z) for _, zeilen in lauf for z in zeilen}
        neu_je_quelle: list[tuple[bool, list[dict[str, Any]]]] = []
        for quelle, zeilen in [("ref", log_zeilen), *lauf]:
            schluessel = str(n) if quelle == "ref" else f"{n}|{quelle}"
            schon = int(gelesen.get(schluessel, -1))
            neu = zeilen[max(schon, 0) :] if 0 <= schon <= len(zeilen) else zeilen
            if quelle == "ref":
                # Schon aus der Laufdatei bekannt (später versioniert) → nicht doppelt.
                neu = [z for z in neu if _roh(z) not in im_lauf]
            gelesen[schluessel] = len(zeilen)
            neu_je_quelle.append((schon < 0, neu))
        neu_alle = [z for _, neu in neu_je_quelle for z in neu]
        sichtbar = [z for z in neu_alle if z.get("typ") not in LEISE_TYPEN]
        for z in sichtbar[-MAX_DELTA_ZEILEN:]:
            delta.append(verdichte(n, z))
        if len(sichtbar) > MAX_DELTA_ZEILEN:
            delta.append(f"#{n} … (+{len(sichtbar) - MAX_DELTA_ZEILEN} ältere Zeilen)")
        if len(neu_alle) > len(sichtbar):
            delta.append(f"#{n} (+{len(neu_alle) - len(sichtbar)} Hook-Zeilen)")
        for erstmals, neu in neu_je_quelle:
            for z in neu:
                alt = _zeit(z.get("ts"))
                if erstmals and (alt is None or jetzt - alt > timedelta(hours=stunden)):
                    continue  # Altlast beim ersten Blick: zeigen ja, Alarm nein
                if ist_gate_rot(z):
                    erg.verstoesse.append(
                        Verstoss(
                            n,
                            "gate_rot",
                            _kurz(z.get("grund") or "Deploy-Gate rot"),
                            True,
                        )
                    )
                    aktionen += _melde(
                        repo,
                        konfig,
                        gh_repo,
                        dry_run,
                        "gate_rot",
                        f"Gate rot bei #{n}",
                        f"Ticket #{n}: {verdichte(n, z)}",
                        f"gate_rot|{n}|{z.get('ts')}|{z.get('lauf')}",
                    )
                elif z.get("typ") == "blockiert":
                    erg.verstoesse.append(Verstoss(n, "live_beweis_blockiert", _kurz(z.get("grund")), True))
                    aktionen += _melde(
                        repo,
                        konfig,
                        gh_repo,
                        dry_run,
                        "live_beweis_blockiert",
                        f"Live-Beweis blockiert bei #{n}",
                        f"Ticket #{n}: {_kurz(z.get('grund')) or 'ohne Grund'}",
                        f"blockiert|{n}|{z.get('ts')}",
                    )

        # A: Regeln
        if zu:
            grund = str(issue.get("state_reason") or "").lower()
            geschlossen_zeit = _zeit(issue.get("closed_at"))
            if not dry_run and merke_schliessen(n, str(issue.get("closed_at") or ""), erledigt):
                sichern()
            if grund in NICHT_GEPLANT:
                aktionen.append(f"#{n} zu als {grund} — keine Regeln")
                continue
            nur_melden = OK_LABEL in label_namen(issue)
            if regeln_aus:
                continue
            ausgangsstand = not sofort and ist_ausgangsstand(n, issue, ausgang, geschlossen_zeit, erster_tick)
            if (
                not ausgangsstand
                and geschlossen_zeit is not None
                and karenz > 0
                and jetzt - geschlossen_zeit < timedelta(minutes=karenz)
            ):
                minuten = (jetzt - geschlossen_zeit).total_seconds() / 60
                aktionen.append(f"#{n} prüfe später (zu seit {minuten:.0f} min, Karenz {karenz:.0f} min)")
                wartet += 1
                continue
            funde_ok = reopen_funde(repo, ref, n, eigene, belege, vps, kopf, alle_zeilen)
            if ausgangsstand:
                erg.alt += funde_ok
                for f in funde_ok:
                    aktionen.append(f"#{n} alt: {f.regel} — {f.text} (Ausgangsstand, nicht wieder geöffnet)")
                continue
            if nur_melden:
                erg.alt += funde_ok
                for f in funde_ok:
                    aktionen.append(
                        f"#{n} VERSTOSS {f.regel}: {f.text} "
                        f"(Label {OK_LABEL} — bewusst freigegeben, nicht wieder geöffnet)"
                    )
                continue
            erg.verstoesse += funde_ok
            geschlossen = str(issue.get("closed_at") or "?")
            neu_funde = [f for f in funde_ok if f"{n}|{f.regel}|{geschlossen}" not in erledigt]
            for f in funde_ok:
                aktionen.append(f"#{n} VERSTOSS {f.regel}: {f.text}" + ("" if f in neu_funde else " (schon gemeldet)"))
            if not neu_funde:
                continue
            aktionen += _wieder_oeffnen(n, gh_repo, neu_funde, dry_run)
            if aktionen[-1] == f"#{n} wieder geöffnet":
                erledigt.update(f"{n}|{f.regel}|{geschlossen}" for f in neu_funde)
                sichern()
            elif not (dry_run and aktionen[-1].endswith("würde wieder öffnen")):
                continue
            # #284: wieder offen bringt nichts, wenn niemand weiterbaut.
            aktionen += _folge_runde(
                repo,
                konfig,
                gh_repo,
                spec,
                n,
                neu_funde,
                folgerunden,
                wt_zeit=wt_zeit,
                jetzt=jetzt,
                dry_run=dry_run,
                gelernt=gelernt,
            )
            sichern()
        elif not regeln_aus:
            # #448: eigenes Fehl-Reopen zurücknehmen, wenn die Regeln jetzt grün sind.
            kandidat = None if vps_fehlt else fehl_reopen_kandidat(n, issue, eigene, erledigt, checkpoint=checkpoint)
            if kandidat is not None and reopen_funde(repo, ref, n, eigene, belege, vps, kopf, alle_zeilen):
                kandidat = None  # Regeln weiter rot — das Reopen war richtig
            if kandidat is not None:
                spaeter = spaeter_geschlossen(gh_repo, n, kandidat[0])
                if spaeter is None:
                    aktionen.append(f"#{n} Rücknahme ausgesetzt: Issue-Verlauf nicht lesbar (gh)")
                if spaeter is not False:
                    kandidat = None  # danach wieder zu (Mensch) — das jetzige Offen ist nicht capos Reopen
            if kandidat is not None:
                sperre = ruecknahme_sperre(n, erledigt, laeuft_noch(n, tmux_fenster(spec), wt_zeit, jetzt))
                if sperre:
                    aktionen.append(sperre)
                    kandidat = None
            if kandidat is not None:
                stempel, regeln = kandidat
                grund = (
                    f"{', '.join(regeln)} beim Schließen {stempel} trifft nicht mehr zu "
                    "(Regeln jetzt grün, kein neuer Ticket-Commit seitdem)."
                )
                rueck = befund.zuruecknehmen(n, gh_repo, grund, dry_run)
                aktionen += rueck.zeilen
                if rueck.ok:
                    erledigt.update({f"ruecknahme|{n}|{stempel}", f"ruecknahme|{n}"})
                    sichern()
                    bau_log.schreibe(repo, n, "ruecknahme", text=f"Fehl-Reopen zurückgenommen: {grund}")
                    continue
                if dry_run:
                    continue
            if checkpoint in label_namen(issue):
                # #285: nachts entscheidet der Aufseher nach Doktrin statt zu warten.
                vorher = len(erledigt)
                aktionen += _checkpoint(
                    repo,
                    konfig,
                    gh_repo,
                    spec,
                    n,
                    alle_zeilen,
                    erledigt,
                    checkpoint=checkpoint,
                    jetzt=jetzt,
                    dry_run=dry_run,
                    gelernt=gelernt,
                )
                if len(erledigt) != vorher:
                    sichern()
            fund = regel_verwaist(
                n,
                issue,
                eigene,
                # Eigene Vorfall-Zeilen sind keine Spur der Session (#286).
                vorfall.ohne_waechter_zeilen(alle_zeilen),
                wt_zeit,
                stunden,
                jetzt,
                checkpoint=checkpoint,
            )
            if fund:
                erg.verstoesse.append(fund)
                schluessel = f"{n}|{fund.regel}|{jetzt.astimezone():%Y-%m-%d}"
                aktionen.append(f"#{n} VERSTOSS {fund.regel}: {fund.text}")
                if dry_run:
                    aktionen.append(f"#{n} [Probe] würde kommentieren + Mail session_tot")
                    continue
                if schluessel in erledigt:
                    aktionen[-1] += " (heute schon kommentiert)"
                elif _gh_ok(
                    [
                        "issue",
                        "comment",
                        str(n),
                        "--repo",
                        gh_repo,
                        "--body",
                        f"{WAECHTER_KOPF} {fund.regel} — {fund.text}",
                    ]
                ):
                    erledigt.add(schluessel)
                    sichern()
                    aktionen.append(f"#{n} kommentiert")
                else:
                    aktionen.append(f"#{n} FEHLER: kommentieren gescheitert")
                # Mail unabhängig vom Kommentar; die Doppel-Sperre hält nur der Melder.
                aktionen += _melde(
                    repo,
                    konfig,
                    gh_repo,
                    dry_run,
                    "session_tot",
                    f"Session tot? #{n}",
                    f"Ticket #{n} ({wer}): {fund.text}",
                    schluessel,
                )

    # Der Aufpasser meldet Vorfälle eines Aufseher-Fensters auf die Spec-Nummer —
    # die steht nicht in der Kinderliste und käme sonst nie in den Katalog (#286).
    spec_wt = worktree_ordner(spec, wt_basis)
    spec_zeilen = log_vom_ref(repo, ref, spec)
    for _, pfad_lauf in laufdateien(repo, spec_wt, spec):
        spec_lauf = lauf_zeilen(pfad_lauf)
        if spec_lauf is not None:
            spec_zeilen += spec_lauf
    gelernt += vorfall.aus_zeilen(spec_zeilen)

    if not dry_run:
        for fund in erg.verstoesse:
            neu = vorfall_aus_verstoss(repo, fund, jetzt)
            if neu is not None:
                gelernt.append(neu)
                aktionen.append(f"#{fund.ticket} Vorfall notiert ({fund.regel})")
            else:
                aktionen.append(f"#{fund.ticket} Vorfall schon bekannt ({fund.regel})")
    # „Mensch nötig“ aus dem Review-Stop-Hook: je Eintrag einmal melden, Kette läuft weiter.
    mn_gesehen: list[str] = list(zustand.get("mensch_noetig_gesehen") or [])
    for eintrag in mensch_noetig.eintraege_fuer_spec(spec, set(mn_gesehen)):
        ziel = str(eintrag.get("ticket") or spec)
        fp = str(eintrag.get("fingerprint") or "")[:8]
        was = mensch_noetig_was(eintrag)
        aktionen.append(f"MENSCH NÖTIG: #{ziel} {was} (fp {fp}) — Kette läuft weiter, David prüft.")
        if dry_run or regeln_aus:
            continue
        text = (
            f"{WAECHTER_KOPF} Mensch nötig — {was} (fp {fp}).\n"
            f"{WAECHTER_KOPF} Die Kette läuft weiter; bitte den Review-Befund von Hand prüfen."
        )
        if not _gh_ok(["issue", "comment", ziel, "--repo", gh_repo, "--body", text]):
            aktionen.append(f"#{ziel} FEHLER: kommentieren gescheitert (Mensch nötig)")
            continue
        bau_log.schreibe(repo, ziel, "mensch_noetig", fingerprint=fp, fix_runde=eintrag.get("fix_runde"))
        mn_gesehen.append(mensch_noetig.schluessel(eintrag))
        zustand["mensch_noetig_gesehen"] = mn_gesehen
        sichern()
    if katalog and not dry_run:
        aktionen += katalog_pflegen(repo, konfig, gelernt)

    erg.zeilen += kopfzeilen
    erg.zeilen.append(
        f"Spec #{spec} · {ref} {ref_kurz} · VPS {kopf[:7] if kopf else '-'} · offen {offen}/{len(liste)} · "
        f"{jetzt.astimezone():%d.%m. %H:%M}"
    )
    erg.zeilen += stand
    if delta:
        erg.zeilen.append("Bau-Log neu:")
        erg.zeilen += delta
    erg.zeilen += aktionen

    erg.fertig = bool(liste) and offen == 0 and wartet == 0 and not erg.verstoesse and not regeln_aus and not vps_fehlt
    if erg.fertig:
        alt_hinweis = f" ({len(erg.alt)} alte aus dem Ausgangsstand)" if erg.alt else ""
        if dry_run:
            erg.zeilen.append(f"SPEC FERTIG — alle {len(liste)} Tickets zu, keine Verstöße{alt_hinweis}. [Probe]")
        else:
            pfad = uebersicht(repo, ref, spec, [int(i["number"]) for i in liste])
            erg.zeilen.append(
                f"SPEC FERTIG — alle {len(liste)} Tickets zu, keine Verstöße{alt_hinweis}. Übersicht: {pfad}"
            )
            erg.zeilen += _melde(
                repo,
                konfig,
                gh_repo,
                dry_run,
                "spec_fertig",
                f"Spec #{spec} fertig",
                f"Alle {len(liste)} Tickets von #{spec} sind zu, der Aufseher fand keine Verstöße.",
                f"spec_fertig|{spec}",
            )
        if not zustand.get("staging_schalter_erledigt"):
            # Staging-Hauptschalter der Spec AN (#563, Spec #548 E3) — Live nie automatisch.
            try:
                schalter = staging_schalter.schalte_an(repo, gh_repo, spec, dry_run)
            except Exception as fehler:  # ein Ausreißer darf den Tick (sichern) nicht abbrechen
                log.exception("Staging-Schalter Spec #%s: unerwarteter Fehler", spec)
                schalter = staging_schalter.Ergebnis(
                    [f"FEHLER: Staging-Schalter — unerwarteter Fehler ({type(fehler).__name__}: {fehler})."], False
                )
            erg.zeilen += schalter.zeilen
            if schalter.erledigt and not dry_run:
                zustand["staging_schalter_erledigt"] = jetzt.isoformat(timespec="seconds")

    if MAIL_AUS in erg.zeilen:  # je Tick nur einmal, nicht je Meldung
        erste = erg.zeilen.index(MAIL_AUS)
        erg.zeilen = [z for i, z in enumerate(erg.zeilen) if z != MAIL_AUS or i == erste]
    sichern()
    if any("FEHLER" in z for z in erg.zeilen):
        erg.exit_code = 1
    return erg


def _gh_ok(args: list[str]) -> bool:
    return gh.lauf(args)[0] == 0


def _wieder_oeffnen(n: int, gh_repo: str, funde: list[Verstoss], dry_run: bool) -> list[str]:
    """Ticket einmal wieder öffnen (Regel lebt in :mod:`befund`); Verstöße sind immer rot."""
    return befund.wieder_oeffnen(n, gh_repo, [(f.regel, f.text) for f in funde], dry_run)


def _melde(
    repo: Path,
    konfig: dict[str, Any],
    gh_repo: str,
    dry_run: bool,
    art: str,
    betreff: str,
    text: str,
    schluessel: str,
) -> list[str]:
    """Mail über den Melder; Fehlschlag = FEHLER-Zeile (der nächste Tick versucht es wieder).

    Ohne ``mail.befehl`` hat das Repo bewusst keine Mail: dann nur :data:`MAIL_AUS`
    (INFO, Exit 0). FEHLER heißt nur: ein eingerichteter Versand ist gescheitert.
    """
    if dry_run:
        return [f"[Probe] Mail {art}: {betreff}"]
    if not melder.darf_raus(art, konfig):
        melder.melden(repo, art, betreff, text, schluessel, konfig=konfig, gh_repo=gh_repo)
        return []
    if not melder.mail_eingerichtet(konfig):
        log.info("Mail nicht eingerichtet (mail.befehl leer) — %s: %s", art, betreff)
        return [MAIL_AUS]
    if melder.schon_gesendet(repo, schluessel, gh_repo):
        return []
    if melder.melden(repo, art, betreff, text, schluessel, konfig=konfig, gh_repo=gh_repo):
        return [f"Mail {art} verschickt: {betreff}"]
    return [f"FEHLER: Mail {art} nicht verschickt: {betreff} (nächster Tick versucht es wieder)"]
