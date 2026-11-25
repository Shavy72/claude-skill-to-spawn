"""Vorher-Zeiten der DuoPlus-Schlange.

Aufruf: python3 vorher_zeiten.py --schlange <schlangen-ordner> [JJJJ-MM-TT Stichtag] -> Markdown auf stdout.
Der Schlangen-Ordner enthält fahrtenbuch.jsonl, klon/ und laeufe/.
"""

import argparse
import collections
import json
import logging
import statistics
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger("vorher_zeiten")
ap = argparse.ArgumentParser(description="Vorher-Zeiten der Schlange als Markdown auf stdout.")
ap.add_argument("--schlange", type=Path, required=True, help="Ordner mit fahrtenbuch.jsonl, klon/, laeufe/")
ap.add_argument("stichtag", nargs="?", help="Stichtag JJJJ-MM-TT (Standard: jetzt)")
args = ap.parse_args()
H = args.schlange
KLON = H / "klon"
now = datetime.fromisoformat(args.stichtag + "T23:59:59+01:00") if args.stichtag else datetime.now(UTC)
rows = []
for l in (H / "fahrtenbuch.jsonl").read_text().splitlines():
    try:
        r = json.loads(l)
        r["_t"] = datetime.fromisoformat(r["zeit"])
        rows.append(r)
    except (ValueError, KeyError, TypeError):
        log.debug("Fahrtenbuch-Zeile übersprungen")


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, round(p / 100 * (len(v) - 1) + 0.4999))] if v else None


def stat(v):
    return (len(v), statistics.median(v), pct(v, 90), max(v)) if v else (0, None, None, None)


def f(x):
    return "–" if x is None else (f"{x:.0f}" if isinstance(x, float) else str(x))


_ct = {}


def ctime(sha):
    if sha not in _ct:
        o = subprocess.run(
            ["git", "-C", str(KLON), "show", "-s", "--format=%cI", sha], check=False, capture_output=True, text=True
        )
        try:
            _ct[sha] = datetime.fromisoformat(o.stdout.strip())
        except ValueError:
            _ct[sha] = None
    return _ct[sha]


URTEIL = {"ticket-gruen", "ticket-rot"}
out = []
for name, tage in (("letzte 14 Tage", 14), ("letzte 3 Tage", 3)):
    ab = now - timedelta(days=tage)
    R = [r for r in rows if r["_t"] >= ab]
    out.append(f"\n## {name} (ab {ab:%Y-%m-%d %H:%M}, {len(R)} Fahrtenbuch-Zeilen)\n")
    out.append(
        "### a) gate_s je Ergebnis-Art (Sekunden)\n\n| Ergebnis | Anzahl | Median | P90 | Max |\n|---|---|---|---|---|"
    )
    g = collections.defaultdict(list)
    for r in R:
        if isinstance(r.get("gate_s"), (int, float)):
            g[r["ergebnis"]].append(r["gate_s"])
    for k in sorted(g):
        n, m, p, x = stat(g[k])
        out.append(f"| {k} | {n} | {f(m)} | {f(p)} | {f(x)} |")
    # b/c
    seen, pu, ps, skip = set(), [], [], 0
    for i, r in enumerate(R):
        if r["ergebnis"] in URTEIL and r["sha"] not in seen:
            seen.add(r["sha"])
            ct = ctime(r["sha"])
            if not ct:
                skip += 1
                continue
            d = (r["_t"] - ct).total_seconds()
            if d >= 0:
                pu.append(d)
            if r["ergebnis"] == "ticket-gruen":
                st = next((x for x in R[i + 1 :] if x["ergebnis"] == "staging"), None)
                if st:
                    d2 = (st["_t"] - ct).total_seconds()
                    if d2 >= 0:
                        ps.append(d2)
    out.append(
        f"\n### b) Push bis Urteil (Näherung: Committer-Zeit bis erstes Urteil; {skip} sha nicht auffindbar übersprungen)\n\n| Anzahl | Median | P90 | Max |\n|---|---|---|---|"
    )
    n, m, p, x = stat(pu)
    out.append(f"| {n} | {f(m)} | {f(p)} | {f(x)} |")
    out.append(
        "\n### c) Push bis Staging (Näherung: Committer-Zeit bis nächste staging-Zeile nach ticket-gruen)\n\n| Anzahl | Median | P90 | Max |\n|---|---|---|---|"
    )
    n, m, p, x = stat(ps)
    out.append(f"| {n} | {f(m)} | {f(p)} | {f(x)} |")
    U = [
        r
        for r in R
        if r["ergebnis"] in ("ticket-gruen", "ticket-rot", "ticket-gate-fehler", "ticket-konflikt", "rot", "gruen")
    ]
    c = collections.Counter(r["ergebnis"] for r in U)
    out.append(f"\n### d) Urteile (Arten: {dict(c)}; Gesamt {len(U)})\n")
    for k in ("ticket-rot", "rot", "ticket-gate-fehler", "ticket-konflikt"):
        if len(U):
            out.append(f"- Anteil {k}: {c[k]}/{len(U)} = {100 * c[k] / len(U):.0f} %")
    pt = collections.Counter(f"{r['_t']:%Y-%m-%d}" for r in U)
    out.append("\nUrteile je Tag: " + ", ".join(f"{d}: {pt[d]}" for d in sorted(pt)))
# e)
out.append("\n## e) Langsamste Test-Dateien aus laeufe/laufzeit.jsonl\n")
L = []
for l in (H / "laeufe" / "laufzeit.jsonl").read_text().splitlines():
    try:
        L.append(json.loads(l))
    except ValueError:
        log.debug("laufzeit-Zeile übersprungen")
zs = [x["zeit"] for x in L]
out.append(
    f"{len(L)} Läufe-Teile ({min(zs)} bis {max(zs)}); je Datei die längste Zeit aus allen Läufen.\n\n| Sekunden | Datei |\n|---|---|"
)
best = {}
for x in L:
    for k, v in (x.get("dateien") or {}).items():
        best[k] = max(best.get(k, 0), v)
for k, v in sorted(best.items(), key=lambda kv: -kv[1])[:20]:
    out.append(f"| {v:.0f} | {k} |")
print("\n".join(out))
