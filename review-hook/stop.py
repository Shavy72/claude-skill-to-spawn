"""Stop-Hook (Moment sitzung): gemerkte Dateien routen, bei fälligem Review blocken (VERTRAG.md §8)."""

from __future__ import annotations

import json
import os
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
    """„Mensch nötig“ zusätzlich als JSON-Zeile festhalten (nachts liest die Wache das
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
    max_runden = int(lade_config().get("max_fix_runden", 2))
    anweisungen: list[str] = []
    mensch: list[str] = []
    hinweis_grenze: list[str] = []
    start_shas: dict[str, str] = sitzung.get("start_sha") or {}
    for wurzel, rels in nach_repo(dateien, sitzungsstart, start_shas).items():
        base = basis_sha(wurzel, start_shas)
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
            if not scharf or beleg_status(beleg.get("befunde", []), lade_config()) != "rot":
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
