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
     [--merges 40] [--rot 15] [--max-s 1020] [--ohne archive]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

LOG = logging.getLogger("rueckblick")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()


def _last_1m() -> float | None:
    if hasattr(os, "getloadavg"):
        return round(os.getloadavg()[0], 2)
    return None


def _polle_ram(
    proc: subprocess.Popen[str],
    timeout: float,
    t0: float,
) -> tuple[int, str, str, float, float | None]:
    """Wartet auf ``proc`` und misst Wandzeit sowie RAM-Spitze (Summe RSS des Prozessbaums, MB)."""
    try:
        import psutil
    except ImportError:
        psutil = None  # type: ignore[assignment]

    spitze: int | None = 0 if psutil is not None else None
    ps = None
    if psutil is not None:
        try:
            ps = psutil.Process(proc.pid)
        except psutil.Error:
            ps = None

    out, err = "", ""
    while True:
        try:
            out, err = proc.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() - t0 > timeout:
                proc.kill()
                out, err = proc.communicate()
                break
            if ps is not None and psutil is not None:
                try:
                    rss = ps.memory_info().rss + sum(c.memory_info().rss for c in ps.children(recursive=True))
                    spitze = max(spitze or 0, rss)
                except psutil.Error:
                    pass
    ram_mb = round(spitze / 1048576, 1) if spitze is not None else None
    return proc.returncode, out, err, time.monotonic() - t0, ram_mb


def lese_fahrtenbuch(schlange: Path) -> list[dict[str, Any]]:
    zeilen: list[dict[str, Any]] = []
    pfad = schlange / "fahrtenbuch.jsonl"
    for nr, zeile in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1):
        if not zeile.strip():
            continue
        try:
            obj = json.loads(zeile)
        except json.JSONDecodeError as fehler:
            LOG.warning("kaputte JSON-Zeile in %s:%d übersprungen: %s", pfad.name, nr, fehler)
            continue
        if isinstance(obj, dict) and obj.get("ergebnis") in ("ticket-gruen", "ticket-rot"):
            zeilen.append(obj)
    return zeilen


def waehle_laeufe(laeufe: Sequence[dict[str, Any]], merges: int, rot: int) -> list[dict[str, Any]]:
    gesehen: set[tuple[str, str]] = set()
    auswahl: list[dict[str, Any]] = []
    for z in reversed(laeufe):
        k = (str(z["sha"]), str(z["ergebnis"]))
        if k not in gesehen:
            gesehen.add(k)
            auswahl.append(z | {"quelle": "merge"})
        if len(auswahl) >= merges:
            break
    rote = [z for z in reversed(laeufe) if z.get("ergebnis") == "ticket-rot" and z.get("kennungen")]
    for z in rote:
        k = (str(z["sha"]), str(z["ergebnis"]))
        if sum(1 for a in auswahl if a["quelle"] == "rot") >= rot:
            break
        if k in gesehen:
            continue
        gesehen.add(k)
        auswahl.append(z | {"quelle": "rot"})
    return auswahl


def basis_fuer(repo: Path, eintrag: dict[str, Any], laeufe: Sequence[dict[str, Any]]) -> str | None:
    """Vorheriger grüner Stand (nicht sha selbst, nicht Nachfahre) → merge-base mit sha."""
    for z in reversed(laeufe):
        if z["zeit"] >= eintrag["zeit"] or z["ergebnis"] != "ticket-gruen" or z["sha"] == eintrag["sha"]:
            continue
        r = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", eintrag["sha"], z["sha"]],
            check=False,
        )
        if r.returncode == 0:
            continue
        try:
            mb = git(repo, "merge-base", eintrag["sha"], z["sha"])
        except subprocess.CalledProcessError:
            continue
        if mb and mb != eintrag["sha"]:
            return mb
    return None


def zaehle_tests(collect: Path) -> dict[str, int]:
    zahl: dict[str, int] = {}
    for zeile in collect.read_text(encoding="utf-8", errors="replace").splitlines():
        if "::" in zeile and zeile.startswith("tests/"):
            datei = zeile.split("::", 1)[0]
            zahl[datei] = zahl.get(datei, 0) + 1
    return zahl


def gate_auswahl(
    werkzeug: Path,
    venv: Path,
    repo: Path,
    basis: str,
    ziel: str,
) -> tuple[str, list[str], dict[str, Any]]:
    last = _last_1m()
    t0 = time.monotonic()
    proc = subprocess.Popen(
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
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    rc, out, err, dauer, ram = _polle_ram(proc, timeout=300.0, t0=t0)
    zeilen = [z for z in out.splitlines() if z.strip()]
    if rc != 0 or not zeilen:
        raise RuntimeError(f"gate_auswahl ohne Antwort: {err[-300:]}")
    mess: dict[str, Any] = {"dauer_s": round(dauer, 2), "ram_mb": ram, "last": last}
    return zeilen[0], zeilen[1:], mess


def impacted(venv: Path, repo: Path, basis: str) -> tuple[str, list[str], dict[str, Any], str]:
    last = _last_1m()
    t0 = time.monotonic()
    proc = subprocess.Popen(
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
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    rc, out, err, dauer, ram = _polle_ram(proc, timeout=600.0, t0=t0)
    pfade: list[str] = []
    praefix = str(repo) + "/"
    for z in out.splitlines():
        z = z.strip()
        if z.startswith(praefix):
            rel = z[len(praefix) :]
            name = rel.rsplit("/", 1)[-1]
            if rel.startswith("tests/") and name.startswith("test_") and name.endswith(".py"):
                pfade.append(rel)
    urteil = "AUSWAHL" if pfade else ("SKIP" if "No impacted tests" in out else "FEHLER")
    fehler = "" if rc == 0 else (err or out)[-400:]
    mess: dict[str, Any] = {"dauer_s": round(dauer, 2), "ram_mb": ram, "last": last}
    return urteil, sorted(set(pfade)), mess, fehler


def ohne_setzen(repo: Path, ohne: Sequence[str]) -> None:
    """Blendet Top-Level-Ordner per git sparse-checkout aus (impacted 0.38.1 kennt keinen Ausschluss)."""
    if not ohne:
        subprocess.run(["git", "-C", str(repo), "sparse-checkout", "disable"], check=False, capture_output=True)
        return
    muster = ["/*", *(f"!/{o.strip('/')}/" for o in ohne)]
    subprocess.run(["git", "-C", str(repo), "sparse-checkout", "set", "--no-cone", *muster], check=True)


@contextmanager
def gefilterter_klon(repo: Path, ohne: Sequence[str], ref_ende: str = "origin/master") -> Iterator[None]:
    """Setzt Sparse-Checkout und stellt im ``finally`` immer ``ref_ende`` und vollen Baum wieder her."""
    ohne_setzen(repo, ohne)
    try:
        yield
    finally:
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-q", "--detach", "-f", ref_ende],
            check=False,
            capture_output=True,
        )
        ohne_setzen(repo, [])


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--schlange", type=Path, required=True, help="Ordner mit fahrtenbuch.jsonl")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--venv", type=Path, required=True)
    p.add_argument("--collect", type=Path, required=True)
    p.add_argument("--aus", type=Path, required=True)
    p.add_argument("--merges", type=int, default=40)
    p.add_argument("--rot", type=int, default=15)
    p.add_argument(
        "--ohne",
        action="append",
        default=[],
        help="Ordner im Klon ausblenden (z. B. archive), wiederholbar",
    )
    p.add_argument("--max-s", type=float, default=1020.0, help="Zeitdeckel je Aufruf, danach Abbruch (fortsetzbar)")
    a = p.parse_args(argv)

    repo = a.repo.resolve()
    werkzeug = a.aus.resolve().parent / "werkzeug_gate_auswahl"
    werkzeug.mkdir(exist_ok=True)
    for name in ("gate_auswahl.py", "schlange_spur.py"):
        inhalt = git(repo, "show", f"origin/master:scripts/{name}")
        (werkzeug / name).write_text(inhalt + "\n", encoding="utf-8")
    zahl = zaehle_tests(a.collect)
    gesamt = sum(zahl.values())
    laeufe = lese_fahrtenbuch(a.schlange)
    fertig: set[tuple[str, str, str]] = set()
    if a.aus.exists():
        for z in a.aus.read_text(encoding="utf-8").splitlines():
            if not z.strip():
                continue
            try:
                d = json.loads(z)
                fertig.add((str(d["sha"]), str(d["ergebnis"]), str(d["quelle"])))
            except (json.JSONDecodeError, KeyError) as fehler:
                LOG.warning("kaputte Zeile in %s übersprungen: %s", a.aus.name, fehler)

    start = time.monotonic()
    with gefilterter_klon(repo, a.ohne):
        for fall in waehle_laeufe(laeufe, a.merges, a.rot):
            if (str(fall["sha"]), str(fall["ergebnis"]), str(fall["quelle"])) in fertig:
                continue
            if time.monotonic() - start > a.max_s:
                LOG.info("Zeitdeckel erreicht — später fortsetzen")
                break
            basis = basis_fuer(repo, fall, laeufe)
            erg: dict[str, Any] = {
                k: fall.get(k) for k in ("zeit", "sha", "ergebnis", "gate_art", "gate_s", "quelle", "kennungen")
            }
            erg["basis"] = basis
            if not basis:
                erg["fehler"] = "keine Basis"
                with a.aus.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(erg) + "\n")
                continue
            subprocess.run(
                ["git", "-C", str(repo), "checkout", "-q", "--detach", "-f", fall["sha"]],
                check=True,
            )
            geaendert = git(repo, "diff", "--name-only", basis, fall["sha"]).splitlines()
            erg["geaendert_n"] = len(geaendert)
            erg["ohne"] = list(a.ohne)
            ua, la, ma = gate_auswahl(werkzeug, a.venv, repo, basis, fall["sha"])
            ub, lb, mb, fb = impacted(a.venv, repo, basis)
            for key, urteil, liste, mess in (("a", ua, la, ma), ("b", ub, lb, mb)):
                n = gesamt if urteil == "VOLL" else sum(zahl.get(d, 0) for d in liste)
                erg[key] = {
                    "urteil": urteil,
                    "dateien": len(liste),
                    "tests": n,
                    "anteil": round(n / gesamt, 4) if gesamt else 0.0,
                } | mess
                if fall.get("kennungen"):
                    rote = sorted({str(k).split("::", 1)[0] for k in fall["kennungen"]})
                    drin = rote if urteil == "VOLL" else [r for r in rote if r in liste]
                    erg[key]["rot_dateien"] = len(rote)
                    erg[key]["rot_getroffen"] = len(drin)
                    erg[key]["rot_verpasst"] = [r for r in rote if r not in drin]
            erg["b"]["fehler"] = fb
            LOG.info(
                "%s %s a=%s/%d b=%s/%d (%.0fs)",
                str(fall["sha"])[:7],
                fall["ergebnis"],
                ua,
                erg["a"]["tests"],
                ub,
                erg["b"]["tests"],
                mb["dauer_s"],
            )
            with a.aus.open("a", encoding="utf-8") as f:
                f.write(json.dumps(erg) + "\n")

    LOG.info("Tests gesamt laut collect: %d in %d Dateien", gesamt, len(zahl))
    shutil.rmtree(werkzeug / "__pycache__", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
