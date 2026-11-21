"""Rückblick Test-Auswahl DuoPlus (Etappe 3a): heutige gate_auswahl vs. pytest-impacted.

Liest <schlangen-ordner>/fahrtenbuch.jsonl (nur lesen), nimmt die letzten N ticket-gruen/-rot-Läufe
(je sha+ergebnis einmal) plus die letzten M ticket-rot-Läufe mit ``kennungen`` (rote Test-IDs).
Je Lauf: Klon auf sha auschecken, Basis = merge-base(sha, vorheriger grüner Stand) (Näherung,
das Gate nahm master zur Gate-Zeit), dann
  (a) heutige scripts/gate_auswahl.py (Stand master, Kopie in --werkzeug-dir) --basis/--ziel
  (b) impacted-tests --git-mode branch --base-branch <basis> --module . --tests-dir tests
Ergebnis je Lauf als JSON-Zeile nach --aus (wird fortgesetzt, fertige Läufe übersprungen).

Aufruf:
  nice -n 15 python3 rueckblick_auswahl_duoplus.py --schlange <schlangen-ordner> --repo <klon-app-repo> \
     --venv <venv-ordner> --collect collect_b5b2308.txt --aus rueckblick.jsonl \
     [--merges 40] [--rot 30] [--max-s 480]
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

LOG = logging.getLogger("rueckblick")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def lese_fahrtenbuch(schlange: Path) -> list[dict]:
    zeilen = []
    for zeile in (schlange / "fahrtenbuch.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            zeilen.append(json.loads(zeile))
        except json.JSONDecodeError:
            continue
    return [z for z in zeilen if z.get("ergebnis") in ("ticket-gruen", "ticket-rot")]


def waehle_laeufe(laeufe: list[dict], merges: int, rot: int) -> list[dict]:
    gesehen: set[tuple[str, str]] = set()
    auswahl: list[dict] = []
    for z in reversed(laeufe):
        k = (z["sha"], z["ergebnis"])
        if k not in gesehen:
            gesehen.add(k)
            auswahl.append(z | {"quelle": "merge"})
        if len(auswahl) >= merges:
            break
    rote = [z for z in reversed(laeufe) if z["ergebnis"] == "ticket-rot" and z.get("kennungen")]
    for z in rote:
        k = (z["sha"], z["ergebnis"])
        if sum(1 for a in auswahl if a["quelle"] == "rot") >= rot:
            break
        if k in gesehen:
            continue
        gesehen.add(k)
        auswahl.append(z | {"quelle": "rot"})
    return auswahl


def basis_fuer(repo: Path, lauf: dict, laeufe: list[dict]) -> str | None:
    """Vorheriger grüner Stand (nicht sha selbst, nicht Nachfahre) → merge-base mit sha."""
    for z in reversed(laeufe):
        if z["zeit"] >= lauf["zeit"] or z["ergebnis"] != "ticket-gruen" or z["sha"] == lauf["sha"]:
            continue
        r = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", lauf["sha"], z["sha"]], check=False)
        if r.returncode == 0:
            continue
        try:
            mb = git(repo, "merge-base", lauf["sha"], z["sha"])
        except subprocess.CalledProcessError:
            continue
        if mb and mb != lauf["sha"]:
            return mb
    return None


def zaehle_tests(collect: Path) -> dict[str, int]:
    zahl: dict[str, int] = {}
    for zeile in collect.read_text(encoding="utf-8", errors="replace").splitlines():
        if "::" in zeile and zeile.startswith("tests/"):
            datei = zeile.split("::", 1)[0]
            zahl[datei] = zahl.get(datei, 0) + 1
    return zahl


def gate_auswahl(werkzeug: Path, venv: Path, repo: Path, basis: str, ziel: str) -> tuple[str, list[str], float]:
    t0 = time.monotonic()
    r = subprocess.run(
        [
            str(venv / "bin" / "python"),
            str(werkzeug / "gate_auswahl.py"),
            "--basis",
            basis,
            "--ziel",
            ziel,
            "--repo",
            str(repo),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=repo,
        timeout=300,
    )
    dauer = time.monotonic() - t0
    zeilen = [z for z in r.stdout.splitlines() if z.strip()]
    if r.returncode != 0 or not zeilen:
        raise RuntimeError(f"gate_auswahl ohne Antwort: {r.stderr[-300:]}")
    kopf = zeilen[0]
    return kopf, zeilen[1:], dauer


def impacted(venv: Path, repo: Path, basis: str) -> tuple[str, list[str], float, str]:
    t0 = time.monotonic()
    r = subprocess.run(
        [
            str(venv / "bin" / "impacted-tests"),
            "--git-mode",
            "branch",
            "--base-branch",
            basis,
            "--module",
            ".",
            "--tests-dir",
            "tests",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=repo,
        timeout=600,
    )
    dauer = time.monotonic() - t0
    pfade = []
    for z in r.stdout.splitlines():
        z = z.strip()
        if z.startswith(str(repo) + "/"):
            rel = z[len(str(repo)) + 1 :]
            name = rel.rsplit("/", 1)[-1]
            if rel.startswith("tests/") and name.startswith("test_") and name.endswith(".py"):
                pfade.append(rel)
    urteil = "AUSWAHL" if pfade else ("SKIP" if "No impacted tests" in r.stdout else "FEHLER")
    fehler = "" if r.returncode == 0 else (r.stderr or r.stdout)[-400:]
    return urteil, sorted(set(pfade)), dauer, fehler


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--schlange", type=Path, required=True, help="Ordner mit fahrtenbuch.jsonl")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--venv", type=Path, required=True)
    p.add_argument("--collect", type=Path, required=True)
    p.add_argument("--aus", type=Path, required=True)
    p.add_argument("--merges", type=int, default=40)
    p.add_argument("--rot", type=int, default=30)
    p.add_argument("--max-s", type=float, default=480.0, help="Zeitdeckel je Aufruf, danach Abbruch (fortsetzbar)")
    a = p.parse_args()

    repo = a.repo.resolve()
    werkzeug = a.aus.resolve().parent / "werkzeug_gate_auswahl"
    werkzeug.mkdir(exist_ok=True)
    for name in ("gate_auswahl.py", "schlange_spur.py"):
        inhalt = git(repo, "show", f"origin/master:scripts/{name}")
        (werkzeug / name).write_text(inhalt + "\n", encoding="utf-8")
    zahl = zaehle_tests(a.collect)
    gesamt = sum(zahl.values())
    laeufe = lese_fahrtenbuch(a.schlange)
    fertig = set()
    if a.aus.exists():
        for z in a.aus.read_text(encoding="utf-8").splitlines():
            d = json.loads(z)
            fertig.add((d["sha"], d["ergebnis"], d["quelle"]))
    start = time.monotonic()
    for lauf in waehle_laeufe(laeufe, a.merges, a.rot):
        if (lauf["sha"], lauf["ergebnis"], lauf["quelle"]) in fertig:
            continue
        if time.monotonic() - start > a.max_s:
            LOG.info("Zeitdeckel erreicht — später fortsetzen")
            break
        basis = basis_fuer(repo, lauf, laeufe)
        erg: dict = {k: lauf.get(k) for k in ("zeit", "sha", "ergebnis", "gate_art", "gate_s", "quelle", "kennungen")}
        erg["basis"] = basis
        if not basis:
            erg["fehler"] = "keine Basis"
            a.aus.open("a", encoding="utf-8").write(json.dumps(erg) + "\n")
            continue
        subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--detach", "-f", lauf["sha"]], check=True)
        geaendert = git(repo, "diff", "--name-only", basis, lauf["sha"]).splitlines()
        erg["geaendert_n"] = len(geaendert)
        ua, la, ta = gate_auswahl(werkzeug, a.venv, repo, basis, lauf["sha"])
        ub, lb, tb, fb = impacted(a.venv, repo, basis)
        for key, urteil, liste, dauer in (("a", ua, la, ta), ("b", ub, lb, tb)):
            if urteil == "VOLL":
                n = gesamt
            else:
                n = sum(zahl.get(d, 0) for d in liste)
            erg[key] = {
                "urteil": urteil,
                "dateien": len(liste),
                "tests": n,
                "anteil": round(n / gesamt, 4),
                "dauer_s": round(dauer, 2),
            }
            if lauf.get("kennungen"):
                rote = sorted({k.split("::", 1)[0] for k in lauf["kennungen"]})
                if urteil == "VOLL":
                    drin = rote
                else:
                    drin = [r for r in rote if r in liste]
                erg[key]["rot_dateien"] = len(rote)
                erg[key]["rot_getroffen"] = len(drin)
                erg[key]["rot_verpasst"] = [r for r in rote if r not in drin]
        erg["b"]["fehler"] = fb
        LOG.info(
            "%s %s a=%s/%d b=%s/%d (%.0fs)",
            lauf["sha"][:7],
            lauf["ergebnis"],
            ua,
            erg["a"]["tests"],
            ub,
            erg["b"]["tests"],
            tb,
        )
        with a.aus.open("a", encoding="utf-8") as f:
            f.write(json.dumps(erg) + "\n")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--detach", "-f", "origin/master"], check=True)
    LOG.info("Tests gesamt laut collect: %d in %d Dateien", gesamt, len(zahl))
    shutil.rmtree(werkzeug / "__pycache__", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
