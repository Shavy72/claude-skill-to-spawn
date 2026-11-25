"""Stop-Hook (Moment sitzung): gemerkte Dateien routen, bei fälligem Review blocken (VERTRAG.md §8)."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from _hookbasis import (
    LOG,
    REVIEW,
    arg,
    ausfuehren,
    befehl,
    git_wurzel,
    lade_dateien_log,
    lade_sitzung,
    route,
    speichere_sitzung,
)

sys.path.insert(0, str(REVIEW))
from _gemeinsam import beleg_status, lade_config  # noqa: E402


def sitzungsstart_sichern(sitzung: dict[str, Any]) -> float:
    """Sitzungsstart aus dem State lesen, sonst jetzt setzen (Beleg 11: Merker kann bei
    parallelen Subagent-Edits Dateien verlieren — ohne Startzeit im State gäbe es keinen
    Anker für die Nachmeldung aus git status)."""
    start = sitzung.get("start")
    if isinstance(start, (int, float)):
        return float(start)
    jetzt = time.time()
    sitzung["start"] = jetzt
    return jetzt


def mensch_noetig_pfad() -> Path:
    return Path(os.environ.get("REVIEW_MENSCH_NOETIG") or Path.home() / ".claude" / ".review" / "mensch_noetig.jsonl")


def mensch_noetig_merken(
    session_id: str, wurzel: Path, fp: str, fix_runde: int, grund: str = "review_offen"
) -> None:
    """„Mensch nötig“ zusätzlich als JSON-Zeile festhalten (nachts liest der Aufseher das
    statt der flüchtigen systemMessage). O_APPEND + ein os.write je Zeile."""
    zeile = {
        "zeit": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session_id": session_id,
        "repo": wurzel.as_posix(),
        "fingerprint": fp,
        "fix_runde": fix_runde,
        # beleg_rot = geprüft, aber rot; review_offen = Review angefordert, nie belegt (Runde 5)
        "grund": grund,
        "spec": os.environ.get("TO_SPAWN_SPEC"),
        "ticket": os.environ.get("TO_SPAWN_TICKET"),
    }
    try:
        pfad = mensch_noetig_pfad()
        pfad.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(pfad, os.O_APPEND | os.O_CREAT | os.O_WRONLY)
        try:
            os.write(fd, (json.dumps(zeile, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as fehler:  # Chat-Meldung bleibt, Ausfall sichtbar im Hook-Log
        LOG.error("mensch_noetig.jsonl nicht geschrieben: %s", fehler)


def basis_sha(wurzel: Path, start_shas: dict[str, str]) -> str | None:
    """Start-SHA der Sitzung, falls seitdem committet wurde (HEAD ≠ Start), sonst None."""
    start = start_shas.get(wurzel.as_posix())
    if not start:
        return None
    head = befehl(["git", "-C", str(wurzel), "rev-parse", "HEAD"])
    if head.returncode != 0:
        raise RuntimeError(f"git rev-parse HEAD {wurzel}: {head.stderr.strip()}")
    return start if head.stdout.strip() != start else None


# ------------------------------------------------------------------ Merge-Umfang (#214, Variante A)

_HAUPTZWEIG_REFS = (
    "refs/remotes/origin/HEAD",
    "refs/remotes/origin/main",
    "refs/remotes/origin/master",
    "refs/heads/main",
    "refs/heads/master",
)


def _ist_auf_hauptzweig(wurzel: Path) -> bool:
    """Steht HEAD direkt auf ``main``, ``master`` oder dem Ziel von ``origin/HEAD``?"""
    zweig = befehl(["git", "-C", str(wurzel), "symbolic-ref", "-q", "--short", "HEAD"])
    if zweig.returncode != 0:
        return False
    aktuell = zweig.stdout.strip()
    namen = {"main", "master"}
    origin_head = befehl(["git", "-C", str(wurzel), "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"])
    if origin_head.returncode == 0:
        ziel = origin_head.stdout.strip().removeprefix("origin/")
        if ziel:
            namen.add(ziel)
    return aktuell in namen


def _hauptzweig_shas(wurzel: Path) -> list[str]:
    """Alle vorhandenen Commit-SHAs von ``main``/``master``/``origin/main``/``origin/HEAD``."""
    shas: list[str] = []
    for ref in _HAUPTZWEIG_REFS:
        res = befehl(["git", "-C", str(wurzel), "rev-parse", "--verify", "-q", f"{ref}^{{commit}}"])
        if res.returncode == 0:
            sha = res.stdout.strip()
            if sha and sha not in shas:
                shas.append(sha)
    return shas


def _ist_hauptzweig_commit(wurzel: Path, commit: str, haupt_shas: list[str]) -> bool:
    """Liegt ``commit`` auf (oder als Vorfahre auf) dem Hauptzweig?"""
    for ref_sha in haupt_shas:
        res = befehl(["git", "-C", str(wurzel), "merge-base", "--is-ancestor", commit, ref_sha])
        if res.returncode == 0:
            return True
    return False


def _merge_tree_konflikte(wurzel: Path, p1: str, p2: str) -> tuple[str, set[str]] | None:
    """Simuliert den 3-Wege-Merge im Speicher (``git merge-tree --write-tree``, git ≥ 2.38).
    Liefert ``(auto_tree, konflikt_dateien)`` oder ``None`` bei älterem git / Fehler."""
    res = befehl(["git", "-C", str(wurzel), "merge-tree", "--write-tree", "--no-messages", "--name-only", "-z", p1, p2])
    if res.returncode not in (0, 1):
        LOG.warning("git merge-tree --write-tree fehlgeschlagen (Exit %d): %s", res.returncode, res.stderr.strip())
        return None
    teile = res.stdout.split("\0")
    auto_tree = teile[0].strip() if teile else ""
    if not auto_tree:
        return None
    konflikte = {p for p in teile[1:] if p}
    return auto_tree, konflikte


def _merge_commit_dateien(wurzel: Path, p1: str, p2: str, commit: str) -> set[str] | None:
    """Konflikt-Dateien plus im Merge-Commit von Hand geänderte Dateien (Differenz zu ``auto_tree``)."""
    sim = _merge_tree_konflikte(wurzel, p1, p2)
    if sim is None:
        return None
    auto_tree, konflikte = sim
    diff = befehl(
        ["git", "-C", str(wurzel), "diff-tree", "-r", "--no-commit-id", "--name-only", "-z", "--no-renames", auto_tree, commit]
    )
    if diff.returncode != 0:
        return None
    return konflikte | {p for p in diff.stdout.split("\0") if p}


def _offener_merge_dateien(wurzel: Path, merge_head: str) -> tuple[set[str], set[str]] | None:
    """Offener Merge (``MERGE_HEAD``): ``(konflikte, abweichend_vom_auto_merge)``."""
    sim = _merge_tree_konflikte(wurzel, "HEAD", merge_head)
    if sim is None:
        return None
    auto_tree, konflikte = sim
    diff = befehl(["git", "-C", str(wurzel), "diff", "--name-only", "-z", "--no-renames", auto_tree])
    untracked = befehl(["git", "-C", str(wurzel), "ls-files", "--others", "--exclude-standard", "-z"])
    if diff.returncode != 0 or untracked.returncode != 0:
        return None
    abweichend = {p for p in diff.stdout.split("\0") if p} | {p for p in untracked.stdout.split("\0") if p}
    return konflikte, abweichend


def _status_pfade(wurzel: Path, pathspec: list[str] | None = None) -> set[str]:
    """Geänderte/unversionierte Pfade aus ``git status --porcelain -z`` (Renames sauber übersprungen)."""
    cmd = ["git", "-C", str(wurzel), "status", "--porcelain", "-z", "--untracked-files=all"]
    if pathspec:
        cmd += ["--", *pathspec]
    res = befehl(cmd)
    if res.returncode != 0:
        return set()
    pfade: set[str] = set()
    tokens = res.stdout.split("\0")
    i = 0
    while i < len(tokens):
        eintrag = tokens[i]
        i += 1
        if len(eintrag) <= 3:
            continue
        pfade.add(eintrag[3:])
        if eintrag[0] in ("R", "C") or eintrag[1] in ("R", "C"):
            i += 1  # Quellpfad der Umbenennung überspringen
    return pfade


def merge_umfang(wurzel: Path, start: str | None, kandidaten: list[str]) -> tuple[str | None, list[str]]:
    """Review-Umfang nach einem Merge von ``main`` eingrenzen (#214, Variante A).

    Stammen Änderungen seit Sitzungsstart (``start..HEAD`` oder offener ``MERGE_HEAD``) aus
    einem Merge des Hauptzweigs, zählen nur:
    - eigene Commits vor/zwischen/nach dem Merge (in ``kandidaten``),
    - Konflikt-Dateien und im Merge von Hand geänderte Dateien,
    - eigene uncommittete Änderungen danach.
    Alle sauber übernommenen fremden Dateien von ``main`` fallen heraus; ``basis`` wird der
    ``main``-Elternteil des letzten Merges. Bei Nicht-main-Merges, auf ``main`` selbst oder
    bei unklarer Historie bleibt ``(start, kandidaten)`` unverändert.
    """
    if _ist_auf_hauptzweig(wurzel):
        return start, kandidaten
    haupt_shas = _hauptzweig_shas(wurzel)
    if not haupt_shas:
        return start, kandidaten

    mh = befehl(["git", "-C", str(wurzel), "rev-parse", "--verify", "-q", "MERGE_HEAD"])
    merge_head = mh.stdout.strip() if mh.returncode == 0 and mh.stdout.strip() else None
    if merge_head and not _ist_hauptzweig_commit(wurzel, merge_head, haupt_shas):
        return start, kandidaten  # offener Merge eines Nicht-main-Zweigs → voller Umfang

    hat_main_merge = False
    letzte_main_basis: str | None = None
    eigene_commits: set[str] = set()
    konflikte_gesamt: set[str] = set()

    if start:
        rev = befehl(
            ["git", "-C", str(wurzel), "rev-list", "--first-parent", "--reverse", "--parents", f"{start}..HEAD"]
        )
        if rev.returncode != 0:
            return start, kandidaten
        erwartet = start
        for zeile in rev.stdout.splitlines():
            teile = zeile.split()
            if len(teile) < 2 or teile[1] != erwartet:
                return start, kandidaten  # start nicht auf der first-parent-Kette
            sha, eltern = teile[0], teile[1:]
            if len(eltern) == 1:
                dt = befehl(
                    ["git", "-C", str(wurzel), "diff-tree", "-r", "--no-commit-id", "--name-only", "-z", "--no-renames", eltern[0], sha]
                )
                if dt.returncode != 0:
                    return start, kandidaten
                eigene_commits.update(p for p in dt.stdout.split("\0") if p)
            elif len(eltern) == 2 and _ist_hauptzweig_commit(wurzel, eltern[1], haupt_shas):
                hat_main_merge = True
                letzte_main_basis = eltern[1]
                m_dateien = _merge_commit_dateien(wurzel, eltern[0], eltern[1], sha)
                if m_dateien is None:
                    return start, kandidaten
                konflikte_gesamt.update(m_dateien)
            else:
                return start, kandidaten  # fremder Merge oder Octopus → voller Umfang
            erwartet = sha

    arbeitsbaum: set[str] = set()
    if merge_head:
        hat_main_merge = True
        letzte_main_basis = merge_head
        offen = _offener_merge_dateien(wurzel, merge_head)
        if offen is None:
            return start, kandidaten
        offen_konflikte, offen_abweichend = offen
        konflikte_gesamt.update(offen_konflikte)
        arbeitsbaum.update(offen_abweichend)

    if not hat_main_merge:
        return start, kandidaten

    if not merge_head:
        arbeitsbaum.update(_status_pfade(wurzel))

    erlaubt = eigene_commits | konflikte_gesamt | arbeitsbaum
    gefiltert = [f for f in kandidaten if f in erlaubt]
    bereits = set(gefiltert)
    for pfad in sorted(konflikte_gesamt):
        if pfad not in bereits:
            gefiltert.append(pfad)
            bereits.add(pfad)
    return letzte_main_basis, gefiltert


# ------------------------------------------------------------------ Hand-Belege (*-hand.json, #214)

_GUELTIGE_HAND_STATUS = frozenset({"gruen", "gelb", "rot"})


def _beleg_mtime(pfad: Path) -> float:
    try:
        return pfad.stat().st_mtime
    except OSError:
        return 0.0


def hand_beleg_finden(wurzel: Path, sha: str, dateien: list[str]) -> tuple[Path, dict[str, Any]] | None:
    """Erkennt handgeführte Prüf-Belege (``<repo>/.review/*-hand.json``) für den aktuellen Stand.

    Gültig, wenn:
    - der Beleg ein JSON-Objekt mit gültigem Status (``gruen``/``gelb``/``rot`` oder ``befunde``-Liste) ist,
    - seine ``sha`` (oder der Dateiname ``beleg-<sha7>-…-hand.json``) zu ``HEAD`` passt,
    - eine angegebene ``dateien``-Liste alle zu prüfenden Dateien abdeckt,
    - keine der zu prüfenden Dateien nach dem Schreiben des Hand-Belegs im Arbeitsbaum geändert wurde.
    """
    ordner = wurzel / ".review"
    if not ordner.is_dir() or not dateien:
        return None
    kandidaten = sorted(ordner.glob("*-hand.json"), key=_beleg_mtime, reverse=True)
    if not kandidaten:
        return None
    head_res = befehl(["git", "-C", str(wurzel), "rev-parse", "HEAD"])
    voll_sha = head_res.stdout.strip().lower() if head_res.returncode == 0 else sha.lower()
    kurz = (sha or voll_sha)[:7].lower()
    if not kurz:
        return None
    schmutzig = _status_pfade(wurzel, dateien)
    gesucht = {Path(d).as_posix() for d in dateien}

    for pfad in kandidaten:
        try:
            beleg = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, ValueError) as fehler:
            LOG.warning("Hand-Beleg %s unlesbar: %s", pfad, fehler)
            continue
        if not isinstance(beleg, dict):
            continue
        status = beleg.get("status")
        if status is not None and status not in _GUELTIGE_HAND_STATUS:
            continue
        if status is None and not isinstance(beleg.get("befunde"), list):
            continue
        beleg_sha = beleg.get("sha")
        if isinstance(beleg_sha, str) and len(beleg_sha.strip()) >= 7:
            s = beleg_sha.strip().lower()
            if not (voll_sha.startswith(s) or s.startswith(kurz)):
                continue
        elif f"-{kurz}-" not in pfad.name.lower():
            continue
        beleg_dateien = beleg.get("dateien")
        if isinstance(beleg_dateien, list) and beleg_dateien:
            abgedeckt = {Path(d).as_posix() for d in beleg_dateien if isinstance(d, str)}
            if not (gesucht <= abgedeckt):
                continue
        beleg_zeit = _beleg_mtime(pfad)
        veraltet = False
        for rel in schmutzig:
            if rel not in gesucht:
                continue
            try:
                if (wurzel / rel).stat().st_mtime > beleg_zeit:
                    veraltet = True
                    break
            except OSError:
                veraltet = True
                break
        if veraltet:
            continue
        return pfad, beleg
    return None


# ------------------------------------------------------------------ Aktiver Helfer im Klon (#214)

_AGENT_ID_MUSTER = re.compile(r"""(?:agentId|agent_id|task_id)["']?\s*[:=]\s*["']?([A-Za-z0-9_-]+)""")
_TASK_ID_MUSTER = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")
_ENDE_STATUS = frozenset({"completed", "failed", "stopped", "killed", "cancelled"})


def _inhalt_text(inhalt: Any) -> str:
    if isinstance(inhalt, str):
        return inhalt
    if isinstance(inhalt, list):
        teile: list[str] = []
        for block in inhalt:
            if isinstance(block, str):
                teile.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                teile.append(block["text"])
        return "\n".join(teile)
    return ""


def _offene_hintergrund_helfer(transkript: Path) -> dict[str, str]:
    """Laufende Hintergrund-Helfer aus dem Sitzungs-Transkript: ``{agent_id: auftrags_text}``."""
    if not transkript.is_file():
        return {}
    gestartet: dict[str, tuple[bool, str]] = {}
    unbeantwortet: set[str] = set()
    offen: dict[str, str] = {}
    try:
        zeilen = transkript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as fehler:
        LOG.warning("Transkript %s nicht lesbar: %s", transkript, fehler)
        return {}
    for roh in zeilen:
        roh = roh.strip()
        if not roh:
            continue
        try:
            eintrag = json.loads(roh)
        except ValueError:
            continue
        if not isinstance(eintrag, dict):
            continue
        nachricht = eintrag.get("message")
        inhalt = nachricht.get("content") if isinstance(nachricht, dict) else None
        if isinstance(inhalt, list):
            for block in inhalt:
                if not isinstance(block, dict):
                    continue
                typ = block.get("type")
                if typ == "tool_use" and block.get("name") in ("Agent", "Task"):
                    tid = str(block.get("id") or "")
                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                    if tid:
                        gestartet[tid] = (bool(inp.get("run_in_background")), json.dumps(inp, ensure_ascii=False))
                        unbeantwortet.add(tid)
                elif typ == "tool_result":
                    tid = str(block.get("tool_use_id") or "")
                    if tid in gestartet:
                        unbeantwortet.discard(tid)
                        ist_bg, text = gestartet[tid]
                        tur = eintrag.get("toolUseResult")
                        res_text = _inhalt_text(block.get("content"))
                        ist_async = ist_bg or "async agent launched" in res_text.lower()
                        aid = ""
                        if isinstance(tur, dict):
                            if tur.get("isAsync") is True or tur.get("status") == "async_launched":
                                ist_async = True
                            aid = str(tur.get("agentId") or tur.get("taskId") or "")
                        if not aid:
                            treffer = _AGENT_ID_MUSTER.search(res_text)
                            aid = treffer.group(1) if treffer else tid
                        if ist_async:
                            offen[aid] = text
        gesamt_text = _inhalt_text(inhalt)
        if "<task-notification>" in gesamt_text:
            for tid in _TASK_ID_MUSTER.findall(gesamt_text):
                offen.pop(tid, None)
        if str(eintrag.get("status") or "").lower() in _ENDE_STATUS:
            for feld in ("task_id", "agent_id", "agentId", "taskId"):
                wert = eintrag.get(feld)
                if isinstance(wert, str):
                    offen.pop(wert, None)
    for tid in unbeantwortet:
        ist_bg, text = gestartet[tid]
        if ist_bg:
            offen[tid] = text
    return offen


def _pfad_im_text(pfad_norm: str, text_norm: str) -> bool:
    """Kommt ``pfad_norm`` (Kleinbuchstaben, ``/``) mit sauberer Pfadgrenze in ``text_norm`` vor?"""
    pos = text_norm.find(pfad_norm)
    while pos != -1:
        ende = pos + len(pfad_norm)
        if ende == len(text_norm) or not (text_norm[ende].isalnum() or text_norm[ende] in ("_", "-")):
            return True
        pos = text_norm.find(pfad_norm, pos + 1)
    return False


def helfer_baut_noch(wurzel: Path, eingabe: dict[str, Any], sitzung: dict[str, Any]) -> bool:
    """Läuft laut Transkript noch ein Hintergrund-Helfer in diesem Klon (Stand halbfertig)?"""
    roh_pfad = eingabe.get("transcript_path")
    if not isinstance(roh_pfad, str) or not roh_pfad.strip():
        return False
    offen = _offene_hintergrund_helfer(Path(roh_pfad))
    if not offen:
        return False
    ziel = wurzel.resolve()
    ziel_norm = ziel.as_posix().casefold()
    suchformen = [ziel_norm]
    try:
        rel_heim = ziel.relative_to(Path.home().resolve()).as_posix().casefold()
        suchformen.append(f"~/{rel_heim}")
    except ValueError:
        pass
    helfer_repos = sitzung.get("helfer_repos")
    if not isinstance(helfer_repos, dict):
        helfer_repos = {}
    for aid, auftrag in offen.items():
        repos = helfer_repos.get(aid)
        if isinstance(repos, list) and any(
            isinstance(r, str) and Path(r).as_posix().casefold() == ziel_norm for r in repos
        ):
            return True
        text_norm = auftrag.replace("\\", "/").casefold()
        if any(_pfad_im_text(form, text_norm) for form in suchformen):
            return True
    return False


def nach_repo(
    dateien: list[str], sitzungsstart: float, start_shas: dict[str, str] | None = None
) -> dict[Path, list[str]]:
    """Gemerkte Dateien je Repo, ergänzt um weitere laut `git status` seit Sitzungsstart
    geänderte Dateien (Beleg 11: Merker sah bei Subagent-Edits nur einen Teil der Dateien,
    stop.py meldet den Rest aus dem Repo-Status nach). `git status` wird per Pathspec auf
    die Elternordner der bekannten Dateien eingeschränkt, damit fremde Ordner nie
    nachgemeldet werden. Eine bekannte, gelöschte Datei bleibt im Block-Text (kein Stat
    nötig, um in `geaendert` zu bleiben)."""
    gruppen: dict[Path, list[str]] = {}
    for text in dateien:
        pfad = Path(text)
        ordner = pfad.parent
        while not ordner.is_dir() and ordner != ordner.parent:
            ordner = ordner.parent
        wurzel = git_wurzel(ordner)
        if wurzel is None:
            continue
        try:
            rel = pfad.resolve().relative_to(wurzel).as_posix()
        except ValueError:
            continue
        gruppen.setdefault(wurzel, []).append(rel)
    ergebnis: dict[Path, list[str]] = {}
    for wurzel, rels in gruppen.items():
        bekannt = dict.fromkeys(rels)
        # Beleg (Runde 3): Elternordner "." meint bei git status die ganze Repo-Wurzel
        # rekursiv, nicht nur die Wurzel-Ebene — sonst melden Wurzel-Dateien fremde
        # Unterordner nach. ":(glob)*" beschränkt git status auf die Wurzel-Ebene selbst.
        elternordner = sorted(
            {":(glob)*" if Path(r).parent == Path(".") else Path(r).parent.as_posix() for r in bekannt}
        )
        status = befehl(
            [
                "git",
                "-C",
                str(wurzel),
                "status",
                "--porcelain",
                "-z",
                "--untracked-files=all",
                "--",
                *elternordner,
            ]
        )
        if status.returncode != 0:
            raise RuntimeError(f"git status {wurzel}: {status.stderr.strip()}")
        geaendert: set[str] = set()
        for eintrag in status.stdout.split("\0"):
            if len(eintrag) <= 3:
                continue
            geaendert.add(eintrag[3:])
        nur_committet: set[str] = set()
        base = basis_sha(wurzel, start_shas or {})
        if base:  # seit Sitzungsstart committete Dateien zählen mit
            commits = befehl(
                ["git", "-C", str(wurzel), "diff", "--name-only", "-z", "--no-renames"]
                + [base, "HEAD", "--", *elternordner]
            )
            if commits.returncode != 0:
                raise RuntimeError(f"git diff {base} {wurzel}: {commits.stderr.strip()}")
            # Runde 5: Nur-committete Dateien zählen nur, wenn der Merker sie kennt — sonst
            # fielen Commits paralleler Sessions im selben Ordner mit rein (mtime nach Start).
            nur_committet = {x for x in commits.stdout.split("\0") if x} - geaendert
            geaendert.update(nur_committet)
        behalten = [r for r in bekannt if r in geaendert]
        for rel in geaendert:
            if rel in bekannt or rel in nur_committet:
                continue
            try:
                mtime = (wurzel / rel).stat().st_mtime
            except OSError:
                continue
            if mtime >= sitzungsstart:
                behalten.append(rel)
        if behalten:
            ergebnis[wurzel] = behalten
    return ergebnis


def haupt(eingabe: dict[str, Any]) -> dict[str, Any] | None:
    session_id = str(eingabe["session_id"])
    sitzung = lade_sitzung(session_id)
    # Beleg 11: dedup aus JSON + race-freiem Anhänge-Log, falls das JSON bei parallelen
    # Subagent-Edits eine Datei verloren hat.
    dateien = list(dict.fromkeys([*sitzung["dateien"], *lade_dateien_log(session_id)]))
    if not dateien:
        return None
    sitzungsstart = sitzungsstart_sichern(sitzung)
    cfg = lade_config()
    max_runden = int(cfg.get("max_fix_runden", 2))
    anweisungen: list[str] = []
    mensch: list[str] = []
    hinweis_grenze: list[str] = []
    start_shas: dict[str, str] = sitzung.get("start_sha") or {}
    for wurzel, rels in nach_repo(dateien, sitzungsstart, start_shas).items():
        if helfer_baut_noch(wurzel, eingabe, sitzung):
            continue
        base, rels = merge_umfang(wurzel, basis_sha(wurzel, start_shas), rels)
        if not rels:
            continue
        r = route(
            "--moment",
            "sitzung",
            "--repo",
            str(wurzel),
            *(["--base", base] if base else []),
            "--files",
            *rels,
            "--log-ausloeser",
        )
        if r["stufe"] == "keine":
            continue
        scharf = r["modus"] == "scharf"
        if r["beleg_gueltig"]:
            beleg = json.loads(Path(r["beleg"]).read_text(encoding="utf-8"))
            if not scharf or beleg_status(beleg.get("befunde", []), cfg) != "rot":
                continue
            praefix = "Beleg rot, Befunde fixen und erneut prüfen. "
        elif (hand := hand_beleg_finden(wurzel, str(r.get("sha") or ""), list(r.get("dateien") or rels))) is not None:
            _, beleg = hand
            ist_rot = beleg.get("status") == "rot" or beleg_status(beleg.get("befunde", []), cfg) == "rot"
            if not scharf or not ist_rot:
                continue
            praefix = "Beleg rot, Befunde fixen und erneut prüfen. "
        else:
            if not scharf and eingabe.get("stop_hook_active"):
                continue
            praefix = ""
        fp = r["fingerprint"]
        zaehler = int(sitzung["bloecke"].get(fp, 0))
        grenze = max_runden if scharf else 1
        if zaehler >= grenze or (scharf and int(r.get("fix_runde", 0)) >= max_runden):
            marke = f"{wurzel.as_posix()} ({fp})"
            fix_runde = int(r.get("fix_runde", 0))
            grund = "beleg_rot" if praefix else "review_offen"
            if scharf:
                mensch.append(marke)
                mensch_noetig_merken(session_id, wurzel, fp, fix_runde, grund)
            else:
                vermerkt: list[str] = sitzung.setdefault("hinweis_mensch", [])
                if marke not in vermerkt:  # einmal je Stand melden, sonst Dauerrauschen
                    vermerkt.append(marke)
                    hinweis_grenze.append(marke)
                    mensch_noetig_merken(session_id, wurzel, fp, fix_runde, grund)
            continue
        sitzung["bloecke"][fp] = zaehler + 1
        anweisungen.append(
            f"{praefix}Review fällig (sitzung, Stufe {r['stufe']}): Skill review-dirigent "
            f"ausführen mit moment=sitzung und --repo {arg(wurzel.as_posix())} "
            f"{f'--base {base} ' if base else ''}--files {' '.join(arg(x) for x in rels)}"
        )
    speichere_sitzung(session_id, sitzung)
    ausgabe: dict[str, Any] = {}
    if anweisungen:
        ausgabe.update(decision="block", reason="\n".join(anweisungen))
    meldungen: list[str] = []
    if mensch:
        meldungen.append(f"Review: Mensch nötig (max_fix_runden={max_runden} erreicht): {', '.join(mensch)}")
    if hinweis_grenze:
        meldungen.append(f"Review fällig – Mensch nötig: {', '.join(hinweis_grenze)}")
    if meldungen:
        ausgabe["systemMessage"] = "\n".join(meldungen)
    return ausgabe or None


if __name__ == "__main__":
    ausfuehren("stop", haupt)
