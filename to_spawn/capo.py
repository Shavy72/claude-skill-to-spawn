"""Capo: der Regel-Prüfer des Bau-Wächters (duoplus-management#213).

Ein Tick liest GitHub (Sub-Issues der Spec), ``origin/<Hauptzweig>``, die
Ticket-Worktrees und die Bau-Logs — und prüft fünf Regeln:

1. ``commit_ohne_nummer`` — Ticket zu, aber kein Commit, dessen Betreff mit ``(#N)`` endet.
2. ``beweis_fehlt`` — Ticket zu, aber keine Belegseite mit der Nummer im Namen.
3. ``test_ersetzt`` — ein Ticket-Commit hat eine Testfunktion/Testdatei entfernt.
4. ``session_verwaist`` (kritisch) — Ticket offen + zugewiesen, aber seit Stunden keine Spur.
5. ``vps_ungleich_origin`` — Deploy-Ticket zu, aber sein Commit ist nicht auf dem VPS.

Verstöße 1, 2, 3 und 5 öffnen das Ticket wieder (je Schließ-Ereignis einmal),
Regel 4 kommentiert nur und meldet per Mail (je Tag einmal). Dazu kommt das
Bau-Log-Delta (nur neue Zeilen seit dem letzten Tick) und die Entscheidungs-Übersicht.
Der Zustand liegt in ``~/.claude/to-spawn/waechter/<owner>_<name>_<S>.json``.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shlex
import subprocess
import sys
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

_TEST_DATEI = re.compile(
    r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$|[^/]*\.(test|spec)\.[cm]?[jt]sx?$)"
)
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


# --- Git ------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> tuple[int, str]:
    try:
        fertig = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
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
    """Betreff endet mit ``(#N)``, optional gefolgt von `` [skip ci]``."""
    return re.search(rf"\(#{ticket}\)(?: \[skip ci\])?\s*$", betreff) is not None


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
    return Verstoss(
        ticket,
        "beweis_fehlt",
        f"keine Belegseite (Datei oder Ordner) mit „{ticket}“ im Namen unter {ordner}/",
    )


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
    verluste = entfernte_tests(diff)
    for status, pfad in _dateien(repo, sha):
        if status != "D" or not _TEST_DATEI.search(pfad):
            continue
        # Gelöschte Testdatei ohne erkennbare Testfälle: die Datei selbst zählt.
        abschnitt = diff.split(f"diff --git a/{pfad} ", 1)[-1].split(
            "\ndiff --git ", 1
        )[0]
        if not _PY_TEST.search(abschnitt) and not _JS_TEST.search(abschnitt):
            verluste.append(f"Datei {pfad}")
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
) -> Verstoss | None:
    if not issue.get("assignees"):
        return None
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


def log_vom_ref(repo: Path, ref: str, ticket: int) -> list[dict[str, Any]]:
    """Bau-Log-Zeilen eines Tickets, wie sie auf ``ref`` liegen."""
    code, text = _git(
        repo, "show", f"{ref}:{bau_log.LOG_ORDNER.as_posix()}/{ticket}.jsonl"
    )
    if code != 0:
        return []
    zeilen = []
    for roh in text.splitlines():
        roh = roh.strip()
        if not roh:
            continue
        try:
            eintrag = json.loads(roh)
        except ValueError:
            eintrag = {"typ": "kaputt"}
        zeilen.append(eintrag if isinstance(eintrag, dict) else {"typ": "kaputt"})
    return zeilen


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
    """Ein Wächter-Tick: Stand + Delta + Verstöße/Aktionen als Textzeilen."""
    jetzt = jetzt or datetime.now(timezone.utc)
    erg = TickErgebnis()
    if _git(repo, "fetch", "-q", "origin")[0] != 0:
        log.warning("git fetch origin gescheitert — Stand von vorher.")
    ref = haupt_ref(repo)
    liste = kinder(gh_repo, spec)
    if liste is None:
        erg.zeilen.append(
            f"FEHLER: Sub-Issues von #{spec} in {gh_repo} nicht lesbar (gh)."
        )
        erg.exit_code = 1
        return erg
    if ref is None:
        erg.zeilen.append(
            "FEHLER: kein origin/master bzw. origin/main — git fetch prüfen."
        )
        erg.exit_code = 1
        return erg

    datei = _zustand_datei(repo, gh_repo, spec)
    zustand = melder.lade_json(datei)
    erledigt = set(zustand.get("erledigt") or [])
    gelesen: dict[str, int] = dict(zustand.get("log_zeilen") or {})
    stunden = float(konfig.get("waechter", {}).get("verwaist_stunden") or 3)
    belege = str(
        konfig.get("regularien", {}).get("belege_ordner") or "docs/verify-hard"
    )
    vps = konfig.get("vps") if isinstance(konfig.get("vps"), dict) else {}
    kopf = vps_kopf(vps) if vps.get("ssh") else None
    commits = alle_commits(repo, ref)
    _, ref_kurz = _git(repo, "rev-parse", "--short", ref)

    stand: list[str] = []
    delta: list[str] = []
    aktionen: list[str] = []
    offen = 0
    for issue in sorted(liste, key=lambda x: int(x["number"])):
        n = int(issue["number"])
        zu = str(issue.get("state", "")).lower() == "closed"
        offen += 0 if zu else 1
        eigene = ticket_commits(commits, n)
        log_zeilen = log_vom_ref(repo, ref, n)
        wt = worktree_ordner(n, wt_basis)
        wt_zeit, wt_info = worktree_spur(wt, ref)
        wer = ",".join(a.get("login", "?") for a in issue.get("assignees") or []) or "-"
        commit = eigene[0].sha[:7] if eigene else "-"
        stand.append(
            f"#{n} {'zu ' if zu else 'OFF'} {wer:<10} commit:{commit} {wt_info:<10} log:{len(log_zeilen)}"
        )

        # B: Delta der Bau-Log-Zeilen
        schon = int(gelesen.get(str(n), -1))
        erstmals = schon < 0
        neu = (
            log_zeilen[max(schon, 0) :] if 0 <= schon <= len(log_zeilen) else log_zeilen
        )
        sichtbar = [z for z in neu if z.get("typ") not in LEISE_TYPEN]
        for z in sichtbar[-MAX_DELTA_ZEILEN:]:
            delta.append(verdichte(n, z))
        if len(sichtbar) > MAX_DELTA_ZEILEN:
            delta.append(f"#{n} … (+{len(sichtbar) - MAX_DELTA_ZEILEN} ältere Zeilen)")
        if len(neu) > len(sichtbar):
            delta.append(f"#{n} (+{len(neu) - len(sichtbar)} Hook-Zeilen)")
        gelesen[str(n)] = len(log_zeilen)
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
            funde = [
                regel_commit(n, eigene),
                regel_beweis(repo, ref, n, eigene, belege),
                regel_tests(repo, n, eigene),
                regel_vps(repo, n, eigene, vps, kopf, log_zeilen)
                if vps.get("ssh")
                else None,
            ]
            funde_ok = [f for f in funde if f]
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
            if neu_funde:
                kommentar = "\n".join(
                    f"Wächter: {f.regel} — {f.text}" for f in neu_funde
                )
                if dry_run:
                    aktionen.append(f"#{n} [Probe] würde wieder öffnen")
                elif (
                    gh.lauf(
                        [
                            "issue",
                            "reopen",
                            str(n),
                            "--repo",
                            gh_repo,
                            "--comment",
                            kommentar,
                        ]
                    )[0]
                    == 0
                ):
                    erledigt.update(f"{n}|{f.regel}|{geschlossen}" for f in neu_funde)
                    aktionen.append(f"#{n} wieder geöffnet")
                else:
                    aktionen.append(f"#{n} FEHLER: wieder öffnen gescheitert")
        else:
            fund = regel_verwaist(n, issue, eigene, log_zeilen, wt_zeit, stunden, jetzt)
            if fund:
                erg.verstoesse.append(fund)
                schluessel = f"{n}|{fund.regel}|{jetzt.astimezone():%Y-%m-%d}"
                aktionen.append(f"#{n} VERSTOSS {fund.regel}: {fund.text}")
                if schluessel in erledigt:
                    aktionen[-1] += " (heute schon gemeldet)"
                elif dry_run:
                    aktionen.append(
                        f"#{n} [Probe] würde kommentieren + Mail session_tot"
                    )
                elif (
                    gh.lauf(
                        [
                            "issue",
                            "comment",
                            str(n),
                            "--repo",
                            gh_repo,
                            "--body",
                            f"Wächter: {fund.regel} — {fund.text}",
                        ]
                    )[0]
                    == 0
                ):
                    erledigt.add(schluessel)
                    aktionen.append(f"#{n} kommentiert")
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

    erg.zeilen.append(
        f"Spec #{spec} · {ref} {ref_kurz} · VPS {kopf[:7] if kopf else '-'} · offen {offen}/{len(liste)} · "
        f"{jetzt.astimezone():%d.%m. %H:%M}"
    )
    erg.zeilen += stand
    if delta:
        erg.zeilen.append("Bau-Log neu:")
        erg.zeilen += delta
    erg.zeilen += aktionen

    erg.fertig = bool(liste) and offen == 0 and not erg.verstoesse
    if erg.fertig:
        if dry_run:
            erg.zeilen.append(
                f"SPEC FERTIG — alle {len(liste)} Tickets zu, keine Verstöße. [Probe]"
            )
        else:
            pfad = uebersicht(repo, ref, spec, [int(i["number"]) for i in liste])
            erg.zeilen.append(
                f"SPEC FERTIG — alle {len(liste)} Tickets zu, keine Verstöße. Übersicht: {pfad}"
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

    if not dry_run:
        zustand.update({"erledigt": sorted(erledigt), "log_zeilen": gelesen})
        melder.speichere_json(datei, zustand)
    return erg


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
    if dry_run:
        return [f"[Probe] Mail {art}: {betreff}"]
    if melder.melden(
        repo, art, betreff, text, schluessel, konfig=konfig, gh_repo=gh_repo
    ):
        return [f"Mail {art} verschickt: {betreff}"]
    return []
