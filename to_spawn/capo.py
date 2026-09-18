"""Capo: der Regel-Prüfer des Bau-Wächters (duoplus-management#213).

Ein Tick liest GitHub (Sub-Issues der Spec), ``origin/<Hauptzweig>``, die
Ticket-Worktrees und die Bau-Logs — und prüft fünf Regeln:

1. ``commit_ohne_nummer`` — Ticket zu, aber kein Commit mit ``(#N)`` am Betreff-Ende
   oder in Scope-Form ``typ(#N): …`` (außer ``docs(#N):`` — das sind Nachträge).
2. ``beweis_fehlt`` — Ticket zu, aber keine Belegseite mit der Nummer im Namen
   (reine Doku-Tickets brauchen keine: die Doku ist der Beleg).
3. ``test_ersetzt`` — ein Ticket-Commit hat eine Testfunktion/Testdatei entfernt
   (nur echte Testdateien; erlaubt mit Commit-Trailer ``Test-entfernt: <Grund>``).
4. ``session_verwaist`` (kritisch) — Ticket offen + zugewiesen, aber seit Stunden keine Spur
   (nicht bei Checkpoint-Label oder wenn die jüngste Bau-Log-Zeile ``blockiert`` ist).
5. ``vps_ungleich_origin`` — Deploy-Ticket zu, aber sein Commit ist nicht auf dem VPS.

Verstöße 1, 2, 3 und 5 öffnen das Ticket höchstens EINMAL je Regel wieder; schließt
jemand erneut mit demselben Verstoß, gibt es nur noch einen Kommentar. Keine Regeln
für Tickets „nicht geplant“/„Duplikat“ oder mit Label ``waechter:ok``. Zeitgrenzen:
beim ersten Tick einer Spec sind alle geschlossenen Tickets Ausgangsstand (nur
melden), danach wird ein Schließen erst 15 min später geprüft (Karenz).
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

from . import bau_log, gh, melder

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
#: Sekunden, die ein Tick auf die Sperre eines anderen Laufs wartet.
SPERRE_S = 30.0
GIT_ZEIT_S = 30
FETCH_ZEIT_S = 60

_TEST_DATEI = re.compile(
    r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$|[^/]*\.(test|spec)\.[cm]?[jt]sx?$)"
)
#: Kopien von Tests (Belege, Archiv, Mutanten) sind keine Testdateien.
_KEINE_TESTDATEI = re.compile(r"(^|/)(docs|archive)/|(^|/)mutants/")
_TRAILER_TEST_ENTFERNT = re.compile(r"^Test-entfernt:[ \t]*\S", re.MULTILINE)
_PY_TEST = re.compile(r"^-[ \t]*(?:async[ \t]+)?def[ \t]+(test_\w+)", re.MULTILINE)
_JS_TEST = re.compile(r"""^-[ \t]*(?:it|test)\([ \t]*(["'`])(.+?)\1""", re.MULTILINE)


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
    """``origin/HEAD``, sonst ``origin/master``, sonst ``origin/main``."""
    code, kopf = _git(repo, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    kandidaten = ([kopf.strip()] if code == 0 and kopf.strip() else []) + [
        "origin/master",
        "origin/main",
    ]
    for ref in kandidaten:
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

    ``docs(#N): …`` zählt nicht: so heißen Nachträge (Wächter, Handoff), nicht der Bau.
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


def regel_beweis(
    repo: Path, ref: str, ticket: int, eigene: list[Commit], ordner: str
) -> Verstoss | None:
    ordner = ordner.strip("/") or "docs/verify-hard"
    code, text = _git(repo, "ls-tree", "-r", "--name-only", ref, "--", ordner)
    namen = text.splitlines() if code == 0 else []
    for commit in eigene:
        namen += [
            pfad
            for _, pfad in _dateien(repo, commit.sha)
            if pfad.startswith(ordner + "/")
        ]
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
    plus = "\n".join(
        z for z in diff.splitlines() if z.startswith("+") and not z.startswith("+++")
    )
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


def _test_verluste(repo: Path, sha: str) -> list[str]:
    code, diff = _git(repo, "show", "--format=", "-M", "--unified=0", sha)
    if code != 0:
        return []
    abschnitte = _test_abschnitte(diff)
    verluste = entfernte_tests("".join(abschnitte.values()))
    for status, pfad in _dateien(repo, sha):
        if status != "D" or not ist_testdatei(pfad):
            continue
        # Gelöschte Testdatei ohne erkennbare Testfälle: die Datei selbst zählt.
        abschnitt = abschnitte.get(pfad, "")
        if not _PY_TEST.search(abschnitt) and not _JS_TEST.search(abschnitt):
            verluste.append(f"Datei {pfad}")
    if verluste:
        code, text = _git(repo, "show", "-s", "--format=%B", sha)
        if code == 0 and _TRAILER_TEST_ENTFERNT.search(text):
            log.info("Commit %s: Test-Entfernung per Trailer erlaubt.", sha[:7])
            return []  # E5: bewusst entfernt, Grund steht im Commit
    return verluste


def regel_tests(repo: Path, ticket: int, eigene: list[Commit]) -> Verstoss | None:
    verluste: list[str] = []
    for commit in eigene:
        verluste += [
            f"{name} ({commit.sha[:7]})" for name in _test_verluste(repo, commit.sha)
        ]
    if not verluste:
        return None
    liste = ", ".join(verluste[:4]) + (
        f" (+{len(verluste) - 4})" if len(verluste) > 4 else ""
    )
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
        fertig = subprocess.run(
            befehl, capture_output=True, text=True, timeout=60, check=False
        )
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
    relevant = [
        c for c in eigene if any(_passt(p, muster) for _, p in _dateien(repo, c.sha))
    ]
    if not relevant and any(z.get("typ") == "deploy_phase" for z in log_zeilen):
        relevant = eigene[:1]  # jüngster Ticket-Commit
    for commit in relevant:
        code, _ = _git(repo, "merge-base", "--is-ancestor", commit.sha, kopf)
        if code == 1:
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
    wurzel = (
        "C:/dev" if sys.platform == "win32" else os.environ.get("BAU_WT_DIR") or "~/wt"
    )
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
    spuren: list[datetime] = [
        datetime.fromtimestamp(c.zeit, timezone.utc) for c in eigene
    ]
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
        inhalt = " ".join(
            str(z.get(k)) for k in ("phase", "status", "ergebnis", "grund") if z.get(k)
        )
    else:
        inhalt = next(
            (str(z[k]) for k in ("grund", "text", "umfang", "nach") if z.get(k)), ""
        )
    return _kurz(f"#{ticket} {_uhr(z.get('ts'))} {typ}: {inhalt}".rstrip(": "), 200)


def ist_gate_rot(z: dict[str, Any]) -> bool:
    if z.get("typ") != "deploy_phase":
        return False
    return any(
        str(z.get(k) or "").strip().lower() in ROT_WERTE
        for k in ("status", "phase", "ergebnis")
    )


# --- Übersicht ------------------------------------------------------------------


def _zelle(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


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
            zeilen.append(
                f"| #{ticket} | {wann} | {_zelle(frage)} | {_zelle(wahl)} | {_zelle(grund)} |"
            )
            anzahl += 1
    if not anzahl:
        zeilen += ["", "_Keine Entscheidungen im Bau-Log._"]
    datei = repo / "docs" / "agents" / f"entscheidungen_{spec}.md"
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return datei


# --- GitHub ---------------------------------------------------------------------


def kinder(gh_repo: str, spec: int) -> list[dict[str, Any]] | None:
    daten = gh.json_lauf(
        ["api", f"repos/{gh_repo}/issues/{spec}/sub_issues?per_page=100"]
    )
    return daten if isinstance(daten, list) else None


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


def tick(
    repo: Path,
    spec: int,
    gh_repo: str,
    konfig: dict[str, Any],
    *,
    wt_basis: str = "",
    dry_run: bool = False,
    jetzt: datetime | None = None,
) -> TickErgebnis:
    """Ein Wächter-Tick: Stand + Delta + Verstöße/Aktionen als Textzeilen.

    Ein echter Tick hält die Sperre der Zustandsdatei; ein Probelauf (``dry_run``)
    schreibt nichts und braucht keine Sperre.
    """
    if dry_run:
        return _tick(repo, spec, gh_repo, konfig, wt_basis, True, jetzt)
    datei = _zustand_datei(repo, gh_repo, spec)
    with sperre(datei, _sperr_wartezeit()) as frei:
        if not frei:
            erg = TickErgebnis(exit_code=1)
            erg.zeilen.append(
                f"FEHLER: ein anderer capo-Lauf für Spec #{spec} läuft schon "
                f"(Sperre {datei.name}.lock) — Tick übersprungen."
            )
            return erg
        return _tick(repo, spec, gh_repo, konfig, wt_basis, False, jetzt)


def _tick(
    repo: Path,
    spec: int,
    gh_repo: str,
    konfig: dict[str, Any],
    wt_basis: str,
    dry_run: bool,
    jetzt: datetime | None,
) -> TickErgebnis:
    jetzt = jetzt or datetime.now(timezone.utc)
    erg = TickErgebnis()
    kopfzeilen: list[str] = []
    regeln_aus = _git(repo, "fetch", "-q", "origin")[0] != 0
    if regeln_aus:
        log.warning("git fetch origin gescheitert — Regeln in diesem Tick ausgesetzt.")
        kopfzeilen.append(
            "FEHLER: fetch — Regeln ausgesetzt (kein Wieder-Öffnen, kein Kommentar in diesem Tick)."
        )
    ref = haupt_ref(repo)
    liste = kinder(gh_repo, spec)
    if liste is None:
        erg.zeilen += kopfzeilen
        erg.zeilen.append(
            f"FEHLER: Sub-Issues von #{spec} in {gh_repo} nicht lesbar (gh)."
        )
        erg.exit_code = 1
        return erg
    if ref is None:
        erg.zeilen += kopfzeilen
        erg.zeilen.append(
            "FEHLER: kein origin/master bzw. origin/main — git fetch prüfen."
        )
        erg.exit_code = 1
        return erg

    datei = _zustand_datei(repo, gh_repo, spec)
    zustand = melder.lade_json(datei)
    erledigt = set(zustand.get("erledigt") or [])
    gelesen: dict[str, int] = dict(zustand.get("log_zeilen") or {})
    wieder_geoeffnet = set(zustand.get("wieder_geoeffnet") or [])
    sofort = ist_sofort(konfig)
    erster_tick = _zeit(zustand.get("erster_tick"))
    if erster_tick is None:
        erster_tick = jetzt  # dieser Tick ist der erste: alles Geschlossene = Ausgangsstand
        zustand["erster_tick"] = jetzt.isoformat(timespec="seconds")
    waechter = konfig.get("waechter", {}) if isinstance(konfig.get("waechter"), dict) else {}
    stunden = float(waechter.get("verwaist_stunden") or 3)
    karenz = 0.0 if sofort else float(waechter.get("karenz_minuten") or KARENZ_MIN)
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
                "wieder_geoeffnet": sorted(wieder_geoeffnet),
            }
        )
        melder.speichere_json(datei, zustand)

    stand: list[str] = []
    delta: list[str] = []
    aktionen: list[str] = []
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
                            n, "gate_rot", _kurz(z.get("grund") or "Deploy-Gate rot"), True
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
                    erg.verstoesse.append(
                        Verstoss(n, "live_beweis_blockiert", _kurz(z.get("grund")), True)
                    )
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
            if grund in NICHT_GEPLANT:
                aktionen.append(f"#{n} zu als {grund} — keine Regeln")
                continue
            if OK_LABEL in label_namen(issue):
                aktionen.append(f"#{n} Label {OK_LABEL} — Regeln übersprungen")
                continue
            if regeln_aus:
                continue
            ausgangsstand = not sofort and (
                geschlossen_zeit is None or geschlossen_zeit < erster_tick
            )
            if (
                not ausgangsstand
                and geschlossen_zeit is not None
                and karenz > 0
                and jetzt - geschlossen_zeit < timedelta(minutes=karenz)
            ):
                minuten = (jetzt - geschlossen_zeit).total_seconds() / 60
                aktionen.append(
                    f"#{n} prüfe später (zu seit {minuten:.0f} min, Karenz {karenz:.0f} min)"
                )
                wartet += 1
                continue
            funde = [
                regel_commit(n, eigene),
                regel_beweis(repo, ref, n, eigene, belege),
                regel_tests(repo, n, eigene),
                regel_vps(repo, n, eigene, vps, kopf, alle_zeilen)
                if vps.get("ssh")
                else None,
            ]
            funde_ok = [f for f in funde if f]
            if ausgangsstand:
                erg.alt += funde_ok
                for f in funde_ok:
                    aktionen.append(
                        f"#{n} alt: {f.regel} — {f.text} (Ausgangsstand, nicht wieder geöffnet)"
                    )
                continue
            erg.verstoesse += funde_ok
            geschlossen = str(issue.get("closed_at") or "?")
            neu_funde = [
                f for f in funde_ok if f"{n}|{f.regel}|{geschlossen}" not in erledigt
            ]
            for f in funde_ok:
                aktionen.append(
                    f"#{n} VERSTOSS {f.regel}: {f.text}"
                    + ("" if f in neu_funde else " (schon gemeldet)")
                )
            if not neu_funde:
                continue
            erstmals_offen = [f for f in neu_funde if f"{n}|{f.regel}" not in wieder_geoeffnet]
            if erstmals_offen:
                aktionen += _wieder_oeffnen(n, gh_repo, neu_funde, dry_run)
                if aktionen[-1] == f"#{n} wieder geöffnet":
                    erledigt.update(f"{n}|{f.regel}|{geschlossen}" for f in neu_funde)
                    wieder_geoeffnet.update(f"{n}|{f.regel}" for f in neu_funde)
                    sichern()
            else:
                # E2: schon einmal wieder geöffnet — kein zweites Mal, nur Kommentar.
                aktionen.append(
                    f"#{n} schon einmal wieder geöffnet — diesmal nur Kommentar"
                )
                text = "\n".join(
                    f"Wächter: {f.regel} — {f.text} (schon einmal wieder geöffnet — "
                    f"diesmal nicht noch einmal; Label {OK_LABEL} gibt bewusst frei)"
                    for f in neu_funde
                )
                if dry_run:
                    aktionen.append(f"#{n} [Probe] würde kommentieren")
                elif _gh_ok(["issue", "comment", str(n), "--repo", gh_repo, "--body", text]):
                    erledigt.update(f"{n}|{f.regel}|{geschlossen}" for f in neu_funde)
                    sichern()
                    aktionen.append(f"#{n} kommentiert")
                else:
                    aktionen.append(f"#{n} FEHLER: kommentieren gescheitert")
        elif not regeln_aus:
            fund = regel_verwaist(
                n,
                issue,
                eigene,
                alle_zeilen,
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
                    aktionen.append(
                        f"#{n} [Probe] würde kommentieren + Mail session_tot"
                    )
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
                        f"Wächter: {fund.regel} — {fund.text}",
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

    erg.fertig = (
        bool(liste)
        and offen == 0
        and wartet == 0
        and not erg.verstoesse
        and not regeln_aus
        and not vps_fehlt
    )
    if erg.fertig:
        alt_hinweis = f" ({len(erg.alt)} alte aus dem Ausgangsstand)" if erg.alt else ""
        if dry_run:
            erg.zeilen.append(
                f"SPEC FERTIG — alle {len(liste)} Tickets zu, keine Verstöße{alt_hinweis}. [Probe]"
            )
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
                f"Alle {len(liste)} Tickets von #{spec} sind zu, der Wächter fand keine Verstöße.",
                f"spec_fertig|{spec}",
            )

    sichern()
    if any("FEHLER" in z for z in erg.zeilen):
        erg.exit_code = 1
    return erg


def _gh_ok(args: list[str]) -> bool:
    return gh.lauf(args)[0] == 0


def _wieder_oeffnen(
    n: int, gh_repo: str, funde: list[Verstoss], dry_run: bool
) -> list[str]:
    """Ticket einmal wieder öffnen; letzte Zeile ``#N wieder geöffnet`` = geklappt."""
    if dry_run:
        return [f"#{n} [Probe] würde wieder öffnen"]
    kommentar = "\n".join(f"Wächter: {f.regel} — {f.text}" for f in funde)
    if _gh_ok(["issue", "reopen", str(n), "--repo", gh_repo, "--comment", kommentar]):
        return [f"#{n} wieder geöffnet"]
    return [f"#{n} FEHLER: wieder öffnen gescheitert"]


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
    """Mail über den Melder; Fehlschlag = FEHLER-Zeile (der nächste Tick versucht es wieder)."""
    if dry_run:
        return [f"[Probe] Mail {art}: {betreff}"]
    if not melder.darf_raus(art, konfig):
        melder.melden(repo, art, betreff, text, schluessel, konfig=konfig, gh_repo=gh_repo)
        return []
    if melder.schon_gesendet(repo, schluessel, gh_repo):
        return []
    if melder.melden(
        repo, art, betreff, text, schluessel, konfig=konfig, gh_repo=gh_repo
    ):
        return [f"Mail {art} verschickt: {betreff}"]
    return [f"FEHLER: Mail {art} nicht verschickt: {betreff} (nächster Tick versucht es wieder)"]
