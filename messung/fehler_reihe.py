"""Etappe 3a: wiederholbare Mess-Reihe Test-Auswahl (Python + Node).

Aufruf (<probe-ordner> enthält py/, node/, .venv/, seed_testmon/):
  <probe-ordner>/.venv/bin/python fehler_reihe.py --ort <probe-ordner> faelle   # M11: Kaltstart + Doku/Blatt/Konfig
  <probe-ordner>/.venv/bin/python fehler_reihe.py --ort <probe-ordner> py       # M12: Fehler-Einbau Python (VOLL, A, A+R, B, B+R)
  <probe-ordner>/.venv/bin/python fehler_reihe.py --ort <probe-ordner> node     # M12: Fehler-Einbau Node (VOLL, A, A+R)
Weitere Argumente nach dem Modus gehen an pytest-xdist, z. B. ``py -n 2 --dist worksteal``.
Ergebnisse: <probe-ordner>/ergebnis_<modus>.json + Tabelle auf stdout.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

import psutil

log = logging.getLogger("fehler_reihe")
_ap = argparse.ArgumentParser(description="Mess-Reihe Test-Auswahl; weitere Argumente gehen an pytest-xdist.")
_ap.add_argument("--ort", type=Path, required=True, help="Probe-Ordner mit py/, node/, .venv/, seed_testmon/")
_ap.add_argument("modus", choices=("faelle", "py", "node"))
ARGS, XDIST_ARGS = _ap.parse_known_args()
ROOT = ARGS.ort.resolve()
PY = ROOT / "py"
NODE = ROOT / "node"
VENV = ROOT / ".venv" / "bin"
SEED = ROOT / "seed_testmon"
NICE = ["nice", "-n", "10"]
XDIST: list[str] = []  # z. B. ["-n", "2", "--dist", "worksteal"] über Umgebungsvariable

PY_FULL = [str(VENV / "pytest"), "-q", "-p", "no:cacheprovider"]
PY_A = PY_FULL + [
    "--impacted",
    "--impacted-module=app",
    "--impacted-git-mode=branch",
    "--impacted-base-branch=base",
    "--impacted-tests-dir=tests",
]
PY_B = PY_FULL + ["--testmon"]
NODE_FULL = [str(NODE / "node_modules/.bin/vitest"), "run", "--passWithNoTests"]
NODE_A = NODE_FULL + ["--changed", "base"]
CODE_EXT = (".py", ".js")


def run(cmd: list[str], cwd: Path) -> dict:
    """Startet cmd, misst Wandzeit und RAM-Spitze (Summe RSS inkl. Kinder)."""
    t0 = time.perf_counter()
    p = subprocess.Popen(NICE + cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    peak = 0
    proc = psutil.Process(p.pid)
    while p.poll() is None:
        try:
            rss = sum(c.memory_info().rss for c in [proc, *proc.children(recursive=True)])
            peak = max(peak, rss)
        except psutil.Error:
            pass
        time.sleep(0.02)
    out = p.stdout.read() if p.stdout else ""
    return {
        "rc": p.returncode,
        "s": round(time.perf_counter() - t0, 2),
        "mb": round(peak / 2**20),
        "n": count(out),
        "out": out[-1500:],
    }


def count(out: str) -> int:
    """Zahl gelaufener Tests aus pytest- oder vitest-Zusammenfassung."""
    m = re.search(r"Tests\s+(?:.*?)\((\d+)\)", out)
    if m:
        return int(m.group(1))
    n = 0
    for k, w in re.findall(r"(\d+) (passed|failed|errors?)\b", out):
        n += int(k)
    return n


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def fresh(cwd: Path) -> None:
    git(cwd, "checkout", "-qf", "--detach", "base")
    git(cwd, "clean", "-fdq")


def apply(cwd: Path, path: str, old: str, new: str) -> None:
    p = cwd / path
    txt = p.read_text()
    if old not in txt:
        raise SystemExit(f"Patch passt nicht: {path}: {old!r}")
    p.write_text(txt.replace(old, new))
    git(cwd, "commit", "-qam", f"fehler {path}")


def py_env(req_changed: bool) -> None:
    if req_changed:
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "-q",
                "-p",
                str(VENV / "python"),
                "--reinstall-package",
                "greetlib",
                "-r",
                "requirements.txt",
            ],
            cwd=PY,
            check=True,
        )


def seed_restore() -> None:
    for f in PY.glob(".testmondata*"):
        f.unlink()
    for f in SEED.glob(".testmondata*"):
        shutil.copy2(f, PY / f.name)


# (id, Lücken-Kategorie, Datei, alt, neu)
PY_FEHLER = [
    ("P01", "Code Blatt", "app/tax.py", '"DE": 0.19', '"DE": 0.2'),
    ("P02", "Code Blatt", "app/util/text.py", "s.strip().lower()", "s.strip()"),
    ("P03", "Code", "app/pricing.py", "(100 - pct)", "(101 - pct)"),
    ("P04", "Code", "app/validators.py", "{2,}", "{1,}"),
    ("P05", "Code", "app/services/orders.py", '> config.get("max_items")', '>= config.get("max_items")'),
    ("P06", "Code Blatt", "app/util/dates.py", ">= 5", "> 5"),
    ("P07", "conftest-Fixture", "tests/conftest.py", '"anna@example.org", "DE"', '"anna@example.org", "AT"'),
    (
        "P08",
        "conftest-Fixture",
        "tests/conftest.py",
        "    mailer.outbox.clear()\n    yield\n    mailer.outbox.clear()",
        "    yield",
    ),
    ("P09", "Daten-Datei", "app/data/catalog.json", '"A1": 10.0', '"A1": 11.0'),
    ("P10", "Daten-Datei", "app/data/catalog.json", ', "C3": 4.5', ""),
    ("P11", "Template", "app/templates/welcome.txt", "Willkommen bei", "Willkommen im"),
    ("P12", "Template", "app/templates/welcome.txt", "{name}", "{nme}"),
    ("P13", "dyn. Import", "app/plugins/csv_export.py", '"\\n".join', '"\\r\\n".join'),
    ("P14", "dyn. Import", "app/plugins/json_export.py", "sort_keys=True", "sort_keys=False"),
    ("P15", "mock.patch-String", "app/plugins/csv_export.py", "DELIM", "SEP"),
    ("P16", "mock.patch-String", "app/plugins/json_export.py", "INDENT", "INDENT_"),
    ("P17", "Prüf-Konfig", "pytest.ini", "testpaths = tests\n", "testpaths = tests\nfilterwarnings = error\n"),
    ("P18", "Konfig-Datei", "app/settings.toml", "max_items = 5", "max_items = 4"),
    ("P19", "Abhängigkeit", "requirements.txt", "greetlib-1", "greetlib-2"),
    ("P20", "Re-Export", "app/__init__.py", "total_price as total", "discount as total"),
    (
        "P21",
        "Code (dyn. Lader)",
        "app/plugins/__init__.py",
        'f"app.plugins.{name}"',
        'f"app.plugins.{name}_x"',
    ),
    ("P22", "Code (mock-Ziel)", "app/mailer.py", '"subject": subject', '"subject": subject.upper()'),
]
NODE_FEHLER = [
    ("N01", "Code Blatt", "src/math.js", "a + b", "a - b"),
    ("N02", "Code Blatt", "src/format.js", "toFixed(2)", "toFixed(1)"),
    ("N03", "Code", "src/cart.js", "reduce(add, 0)", "reduce(add, 1)"),
    ("N04", "Daten-Datei (import)", "src/data/prices.json", '"A1": 10', '"A1": 11'),
    ("N05", "Daten-Datei (fs)", "src/data/stock.json", '"B2": 0', '"B2": 1'),
    ("N06", "dyn. import()", "src/plugins/upper.js", "toUpperCase()", "toLowerCase()"),
    ("N07", "dyn. import()", "src/plugins/reverse.js", ".reverse()", ""),
    ("N08", "Prüf-Konfig", "vitest.config.js", '["./test/setup.js"]', "[]"),
    ("N09", "Setup-Datei", "test/setup.js", "'Probe-Shop'", "'Shop'"),
    ("N10", "Abhängigkeit", "package.json", "greetlib-1", "greetlib-2"),
    (
        "N11",
        "Re-Export",
        "src/index.js",
        'export { total } from "./cart.js";',
        'export { priceOf as total } from "./cart.js";',
    ),
    ("N12", "Code (dyn. Lader)", "src/plugins/index.js", "`./${name}.js`", "`./${name}.mjs`"),
]


def nur_code_ohne_auswahl(path: str, a: dict) -> bool:
    return path.endswith(CODE_EXT) and a["n"] == 0


def reihe_py() -> list[dict]:
    rows = []
    for fid, kat, path, old, new in PY_FEHLER:
        fresh(PY)
        py_env(True)  # Basis-Abhängigkeiten sicher herstellen
        apply(PY, path, old, new)
        py_env(path == "requirements.txt")
        voll = run(PY_FULL + XDIST, PY)
        a = run(PY_A + XDIST, PY)
        seed_restore()
        b = run(PY_B + XDIST, PY)
        nicht_code = not path.endswith(".py")
        # Rückfall-Regeln (Vorschlag): R1 Nicht-Code-Datei geändert -> voll; R2 Code geändert, Auswahl 0 -> voll
        ar = voll if (nicht_code or nur_code_ohne_auswahl(path, a)) else a
        br = voll if (nicht_code or nur_code_ohne_auswahl(path, b)) else b
        row = {"id": fid, "kat": kat, "datei": path, "voll": voll, "A": a, "A+R": ar, "B": b, "B+R": br}
        rows.append(row)
        print_row(row, ["voll", "A", "A+R", "B", "B+R"])
    fresh(PY)
    py_env(True)
    return rows


def node_env() -> None:
    subprocess.run(["npm", "install", "-q", "--no-audit", "--no-fund"], cwd=NODE, check=True, capture_output=True)


def reihe_node() -> list[dict]:
    rows = []
    for fid, kat, path, old, new in NODE_FEHLER:
        fresh(NODE)
        apply(NODE, path, old, new)
        if path == "package.json":
            node_env()
        voll = run(NODE_FULL, NODE)
        a = run(NODE_A, NODE)
        nicht_code = not path.endswith(".js") or path.startswith("test/setup") or "config" in path
        ar = voll if (nicht_code or nur_code_ohne_auswahl(path, a)) else a
        row = {"id": fid, "kat": kat, "datei": path, "voll": voll, "A": a, "A+R": ar}
        rows.append(row)
        print_row(row, ["voll", "A", "A+R"])
        if path == "package.json":
            fresh(NODE)
            node_env()
    fresh(NODE)
    return rows


def rot(r: dict) -> bool:
    return r["rc"] != 0


def print_row(row: dict, keys: list[str]) -> None:
    cells = [f"{k}:{'ROT' if rot(row[k]) else 'grün'} n={row[k]['n']} {row[k]['s']}s" for k in keys]
    print(row["id"], row["kat"], "|", " | ".join(cells), flush=True)


def faelle() -> dict:
    """M11: Kaltstart + drei Fälle je Ansatz (Python und Node)."""
    res: dict = {}
    fresh(PY)
    py_env(True)
    for f in PY.glob(".testmondata*"):
        f.unlink()
    res["py_voll_basis"] = run(PY_FULL, PY)
    res["py_B_kaltstart"] = run(PY_B, PY)
    SEED.mkdir(exist_ok=True)
    for f in SEED.glob("*"):
        f.unlink()
    for f in PY.glob(".testmondata*"):
        shutil.copy2(f, SEED / f.name)
    res["py_B_dbgroesse_kb"] = sum(f.stat().st_size for f in SEED.glob("*")) // 1024
    res["py_B_ohne_aenderung"] = run(PY_B, PY)
    py_faelle = {
        "doku": ("docs/README.md", "Kleine", "Winzige"),
        "blatt": ("app/util/dates.py", "d.weekday() >= 5", "d.weekday() in (5, 6)"),
        "konfig": ("requirements.txt", "pytest\n", "pytest>=8\n"),
        "pruefkonfig": ("pytest.ini", "testpaths = tests\n", "testpaths = tests\nxfail_strict = true\n"),
    }
    for name, (path, old, new) in py_faelle.items():
        fresh(PY)
        apply(PY, path, old, new)
        res[f"py_{name}_voll"] = run(PY_FULL, PY)
        res[f"py_{name}_A"] = run(PY_A, PY)
        seed_restore()
        res[f"py_{name}_B"] = run(PY_B, PY)
    node_faelle = {
        "doku": ("docs/README.md", "Probe", "Probe2"),
        "blatt": ("src/format.js", 'toFixed(2) + " €"', 'toFixed(2) + " " + "€"'),
        "konfig": ("package.json", '"private": true', '"private": true,\n  "description": "x"'),
        "pruefkonfig": ("vitest.config.js", "setupFiles", "testTimeout: 5000, setupFiles"),
    }
    fresh(NODE)
    res["node_voll_basis"] = run(NODE_FULL, NODE)
    for name, (path, old, new) in node_faelle.items():
        fresh(NODE)
        apply(NODE, path, old, new)
        res[f"node_{name}_voll"] = run(NODE_FULL, NODE)
        res[f"node_{name}_A"] = run(NODE_A, NODE)
    fresh(PY)
    fresh(NODE)
    seed_restore()
    for k, v in res.items():
        print(k, v if not isinstance(v, dict) else f"rc={v['rc']} n={v['n']} {v['s']}s {v['mb']}MB")
    return res


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    modus = ARGS.modus
    XDIST.extend(XDIST_ARGS)
    res = {"faelle": faelle, "py": reihe_py, "node": reihe_node}[modus]()
    suffix = "_xdist" if XDIST else ""
    (ROOT / f"ergebnis_{modus}{suffix}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
    if modus in ("py", "node"):
        keys = [k for k in ("A", "A+R", "B", "B+R") if k in res[0]]
        for k in keys:
            miss = [r["id"] for r in res if rot(r["voll"]) and not rot(r[k])]
            print(f"übersehen {k}: {len(miss)} {miss}")
        print("voll rot:", sum(rot(r["voll"]) for r in res), "von", len(res))


if __name__ == "__main__":
    main()
