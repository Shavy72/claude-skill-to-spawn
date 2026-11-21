"""Schlangen-Simulator (Etappe 3a, Teil Schlange): spielt die echten Ankünfte der letzten 14 Tage
aus <schlangen-ordner>/fahrtenbuch.jsonl mit S0 (seriell), S1 (Batch + Bisect), S2 (spekulativ
parallel, Zuul-Art), S3 (Spuren nach ``ordner``) und Vorprüfung V nach. Nur lesend, nur Standardbibliothek.

Aufruf: python3 schlange_sim.py --schlange <schlangen-ordner> [--bis 2026-11-21T19:21:00+01:00] [--tage 14] [--seed 2026]
Ausgabe: Markdown auf stdout. Deterministisch (fester Seed).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import statistics
import subprocess
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path

log = logging.getLogger("schlange_sim")
ROT_ARTIG = {"rot", "fehler"}
VOR_S = 120.0  # Annahme: Vorprüfung dauert 2 min
RUEST = {"gate": 0.0, "skip": 0.0}  # Rüstzeit je Lauf (gemessen in main(), siehe ruestzeit())
M13_S = 900.0
INF = float("inf")


@dataclass
class Job:
    sha: str
    ank: float  # Ankunft (Committer-Zeit, auf echten Start gekappt), Epoch-Sekunden
    ordner: frozenset[str]
    art: str  # gruen | rot | fehler | konflikt | offen
    vor: float  # Gate-Sekunden vor dem End-Urteil (Gate-Fehler, Wartet, Wackler-Rot)
    end: float  # gate_s des End-Urteils
    ist_urteil: float | None  # erstes echtes gruen/rot-Urteil (wie vorher_zeiten.py)
    ist_start: float  # erster echter Gate-Start
    ank_eff: float = 0.0

    @property
    def gesamt(self) -> float:
        return self.vor + self.end


@dataclass
class Lauf:
    jobs: list[Job]
    rest: float
    wand_start: float = 0.0


# ---------------------------------------------------------------- Daten
def pct(v: list[float], p: float) -> float | None:  # gleiche Formel wie vorher_zeiten.py
    v = sorted(v)
    return v[min(len(v) - 1, round(p / 100 * (len(v) - 1) + 0.4999))] if v else None


def ctime(klon: Path, sha: str) -> datetime | None:  # Push-Zeit-Näherung wie vorher_zeiten.py
    o = subprocess.run(["git", "-C", str(klon), "show", "-s", "--format=%cI", sha], check=False, capture_output=True, text=True)
    try:
        return datetime.fromisoformat(o.stdout.strip())
    except ValueError:
        return None


def lade(schlange: Path, bis: datetime | None, tage: int) -> tuple[list[Job], dict[str, object]]:
    rows: list[dict] = []
    for zeile in (schlange / "fahrtenbuch.jsonl").read_text().splitlines():
        try:
            r = json.loads(zeile)
            r["_t"] = datetime.fromisoformat(r["zeit"])
            rows.append(r)
        except (ValueError, KeyError):
            continue
    ende = bis or max(r["_t"] for r in rows)
    ab = ende - timedelta(days=tage)
    R = [r for r in rows if ab <= r["_t"] <= ende and str(r.get("ergebnis", "")).startswith("ticket-")]
    je: dict[str, list[dict]] = {}
    for r in R:
        je.setdefault(r["sha"], []).append(r)
    jobs, ohne = [], 0
    for sha, rs in je.items():
        rs.sort(key=lambda r: r["_t"])
        g = lambda r: float(r["gate_s"]) if isinstance(r.get("gate_s"), (int, float)) else 0.0
        idx = next((i for i, r in enumerate(rs) if r["ergebnis"] == "ticket-gruen"), None)
        art = "gruen"
        if idx is None:
            for a, e in (("rot", "ticket-rot"), ("fehler", "ticket-gate-fehler"), ("konflikt", "ticket-konflikt")):
                kand = [i for i, r in enumerate(rs) if r["ergebnis"] == e]
                if kand:
                    idx, art = (kand[-1] if a == "fehler" else kand[0]), a
                    break
        if idx is None:
            idx, art = len(rs) - 1, "offen"
        vor = sum(g(r) for r in rs[:idx])
        start = min(r["_t"].timestamp() - g(r) for r in rs)
        ct = ctime(schlange / "klon", sha)
        if ct is None:
            ohne += 1
            continue
        uu = next((r["_t"].timestamp() for r in rs if r["ergebnis"] in ("ticket-gruen", "ticket-rot")), None)
        ordner = frozenset(rs[idx].get("ordner") or [])
        jobs.append(Job(sha, min(ct.timestamp(), start), ordner, art, vor, g(rs[idx]), uu, start))
    jobs.sort(key=lambda j: j.ank)
    voll = [g for r in R if r["ergebnis"] == "ticket-gruen" and r.get("gate_art") == "VOLL" and (g := r.get("gate_s"))]
    meta = {
        "ab": ab,
        "ende": ende,
        "zeilen": len(R),
        "ohne_sha": ohne,
        "t_voll": statistics.median(voll),
        "n_voll": len(voll),
    }
    return jobs, meta


def ruestzeit(jobs: list[Job], grenze: float) -> tuple[float, float, int]:
    """Gemessene Rüstzeit im seriellen Abschnitt: Lücke zwischen Ende eines Gates und Start des nächsten,
    wenn das nächste Ticket schon wartete (Median); SKIP-Tickets: Median Push→Urteil ohne Gate."""
    iv = sorted((j.ist_start, j.ist_start + j.gesamt, j.ank) for j in jobs if j.ank < grenze and j.art != "konflikt")
    luecken = [a1 - e0 for (_, e0, _), (a1, _, k1) in pairwise(iv) if k1 < e0 and a1 >= e0]
    skip = [j.ist_urteil - j.ank for j in jobs if j.gesamt == 0 and j.ist_urteil]
    return statistics.median(luecken), statistics.median(skip), len(luecken)


# ---------------------------------------------------------------- Strategien
class Politik:
    """Eine Schlangen-Strategie. Motor ruft rein(), fertig(), fuelle(); läuft = laufende Gates."""

    def __init__(self) -> None:
        self.laeuft: list[Lauf] = []
        self.start: dict[str, float] = {}
        self.urteil: dict[str, float] = {}

    def starte(self, t: float, jobs: list[Job], dauer: float) -> None:
        for j in jobs:
            self.start.setdefault(j.sha, t)
        dauer += RUEST["gate"] if dauer > 0 else RUEST["skip"]
        self.laeuft.append(Lauf(jobs, dauer, t))

    def sag(self, t: float, jobs: list[Job]) -> None:
        for j in jobs:
            self.start.setdefault(j.sha, t)
            self.urteil[j.sha] = t

    def rein(self, t: float, j: Job) -> None: ...
    def fertig(self, t: float, lauf: Lauf) -> None: ...
    def fuelle(self, t: float) -> None: ...


class S0(Politik):
    def __init__(self) -> None:
        super().__init__()
        self.w: deque[Job] = deque()

    def rein(self, t: float, j: Job) -> None:
        self.w.append(j)

    def fertig(self, t: float, lauf: Lauf) -> None:
        self.sag(t, lauf.jobs)

    def fuelle(self, t: float) -> None:
        while not self.laeuft and self.w:
            j = self.w.popleft()
            if j.art == "konflikt":
                self.sag(t, [j])
            else:
                self.starte(t, [j], j.gesamt)


@dataclass
class Teil:
    """Bisect-Hälfte. gewiss = Schwester-Hälfte war grün, also steckt der Schuldige sicher hier (bors-Regel)."""

    jobs: list[Job]
    gewiss: bool = False
    schwester: Teil | None = None


class S1(Politik):
    """Batch + Bisect; Größe clamp(ceil(wartend/freie_Slots), 1, max_b); Batch-Dauer Variante A/B.
    A: max(gate_s der Mitglieder). B: wie A, aber Batch ≥ 2 mindestens T_voll (voller Lauf)."""

    def __init__(self, max_b: int, var: str, t_voll: float, slots: int = 1) -> None:
        super().__init__()
        self.max_b, self.var, self.t_voll, self.slots = max_b, var, t_voll, slots
        self.w: deque[Job] = deque()
        self.bisect: list[Teil] = []
        self.vor_bezahlt: set[str] = set()
        self.teil_von: dict[int, Teil] = {}

    def rein(self, t: float, j: Job) -> None:
        self.w.append(j)

    def dauer(self, g: list[Job]) -> float:
        vor = max((j.vor for j in g if j.sha not in self.vor_bezahlt), default=0.0)
        self.vor_bezahlt.update(j.sha for j in g)
        d = vor + max(j.end for j in g)
        return max(d, self.t_voll) if (self.var == "B" and len(g) > 1) else d

    def teile(self, g: list[Job]) -> None:
        h = len(g) // 2
        a, b = Teil(g[:h]), Teil(g[h:])
        a.schwester = b
        self.bisect[:0] = [a, b]

    def fertig(self, t: float, lauf: Lauf) -> None:
        g, teil = lauf.jobs, self.teil_von.pop(id(lauf), None)
        if any(j.art in ROT_ARTIG for j in g):
            if len(g) > 1:
                self.teile(g)
            else:
                self.sag(t, g)
        else:
            self.sag(t, g)
            if teil and teil.schwester:
                teil.schwester.gewiss = True

    def fuelle(self, t: float) -> None:
        while len(self.laeuft) < self.slots and (self.bisect or self.w):
            teil = None
            if self.bisect:
                teil = self.bisect.pop(0)
                g = teil.jobs
                if teil.gewiss:  # Schuldiger sicher hier: ohne Lauf weiter teilen bzw. Urteil
                    if len(g) == 1:
                        self.sag(t, g)
                    else:
                        self.teile(g)
                    continue
            else:
                frei = self.slots - len(self.laeuft)
                n = max(1, min(self.max_b, math.ceil(len(self.w) / frei)))
                g = [self.w.popleft() for _ in range(min(n, len(self.w)))]
                self.sag(t, [j for j in g if j.art == "konflikt"])
                g = [j for j in g if j.art != "konflikt"]
                if not g:
                    continue
            self.starte(t, g, self.dauer(g))
            if teil:
                self.teil_von[id(self.laeuft[-1])] = teil


class S2(Politik):
    """Spekulativ parallel (Zuul-Art): k vordere Tickets testen master+Vorgänger gleichzeitig.
    Rot → sofort Urteil, alle dahinter im Fenster starten neu. Grün zählt erst, wenn vorn."""

    def __init__(self, k: int) -> None:
        super().__init__()
        self.k = k
        self.q: list[Job] = []
        self.fertig_gruen: set[str] = set()

    def rein(self, t: float, j: Job) -> None:
        self.q.append(j)

    def fertig(self, t: float, lauf: Lauf) -> None:
        j = lauf.jobs[0]
        if j.art in ROT_ARTIG:
            self.sag(t, [j])
            i = self.q.index(j)
            self.q.pop(i)
            dahinter = {x.sha for x in self.q[i:]}
            self.laeuft = [r for r in self.laeuft if r.jobs[0].sha not in dahinter]
            self.fertig_gruen -= dahinter
        else:
            self.fertig_gruen.add(j.sha)
        while self.q and self.q[0].sha in self.fertig_gruen:
            self.sag(t, [self.q.pop(0)])

    def fuelle(self, t: float) -> None:
        while self.q and self.q[0].art == "konflikt":
            self.sag(t, [self.q.pop(0)])
        aktiv = {r.jobs[0].sha for r in self.laeuft}
        for j in list(self.q[: self.k]):
            if j.art == "konflikt":
                self.sag(t, [j])
                self.q.remove(j)
            elif j.sha not in aktiv and j.sha not in self.fertig_gruen:
                self.starte(t, [j], j.gesamt)


class S3(Politik):
    """Spuren: Ticket startet, wenn seine Ordner weder mit laufenden noch mit früher Wartenden überlappen."""

    def __init__(self, deckel: int, ohne: frozenset[str]) -> None:
        super().__init__()
        self.deckel, self.ohne = deckel, ohne
        self.w: list[Job] = []

    def rein(self, t: float, j: Job) -> None:
        self.w.append(j)

    def fertig(self, t: float, lauf: Lauf) -> None:
        self.sag(t, lauf.jobs)

    def fuelle(self, t: float) -> None:
        belegt: set[str] = set()
        for r in self.laeuft:
            belegt |= r.jobs[0].ordner - self.ohne
        gesperrt: set[str] = set()
        for j in list(self.w):
            if j.art == "konflikt":
                self.sag(t, [j])
                self.w.remove(j)
                continue
            if len(self.laeuft) >= self.deckel:
                break
            o = j.ordner - self.ohne
            if (o & belegt) or (o & gesperrt):
                gesperrt |= o
                continue
            self.w.remove(j)
            self.starte(t, [j], j.gesamt)
            belegt |= o


# ---------------------------------------------------------------- Motor
@dataclass
class Ergebnis:
    pol: Politik
    maschine_s: float = 0.0
    spitze: int = 0


def motor(jobs: list[Job], pol: Politik, bremse: float) -> Ergebnis:
    """Ereignis-Simulation. bremse = Laufzeit-Faktor je Gate, sobald ≥ 2 Gates gleichzeitig laufen (Annahme)."""
    ank = sorted(jobs, key=lambda j: j.ank_eff)
    i, t, e = 0, ank[0].ank_eff if ank else 0.0, Ergebnis(pol)
    while True:
        n = len(pol.laeuft)
        f = bremse if n >= 2 else 1.0
        t_fertig = t + min(r.rest for r in pol.laeuft) * f if n else INF
        t_ank = ank[i].ank_eff if i < len(ank) else INF
        if t_fertig == INF and t_ank == INF:
            return e
        tn = min(t_fertig, t_ank)
        for r in pol.laeuft:
            r.rest -= (tn - t) / f
        e.maschine_s += (tn - t) * n
        t = tn
        if t_ank <= t_fertig:
            while i < len(ank) and ank[i].ank_eff <= t:
                pol.rein(t, ank[i])
                i += 1
        else:
            for r in [r for r in pol.laeuft if r.rest <= 1e-6]:
                pol.laeuft.remove(r)
                pol.fertig(t, r)
        pol.fuelle(t)
        e.spitze = max(e.spitze, len(pol.laeuft))


def mit_vorpruefung(jobs: list[Job], quote: float, seed: int) -> tuple[list[Job], dict[str, float]]:
    """Fängt Anteil `quote` der roten Tickets nach VOR_S ab; alle anderen kommen VOR_S später in die Schlange."""
    rote = sorted(j.sha for j in jobs if j.art == "rot")
    gefangen = set(random.Random(seed).sample(rote, round(quote * len(rote))))
    rest = [replace(j, ank_eff=j.ank + (VOR_S if quote > 0 else 0.0)) for j in jobs if j.sha not in gefangen]
    return rest, {j.sha: j.ank + VOR_S for j in jobs if j.sha in gefangen}


def kennzahlen(
    jobs: list[Job],
    urteil: dict[str, float],
    start: dict[str, float],
    ank_eff: dict[str, float],
    maschine_s: float,
    spitze: int,
) -> dict[str, float | None]:
    pu = [urteil[j.sha] - j.ank for j in jobs if j.art in ("gruen", "rot") and j.sha in urteil]
    w = [start[s] - ank_eff[s] for s in start if s in ank_eff]
    fehlt = sum(1 for j in jobs if j.sha not in urteil)
    return {
        "n": len(pu),
        "med": statistics.median(pu),
        "p90": pct(pu, 90),
        "max": max(pu),
        "w_max": max(w),
        "m13": 100 * sum(1 for x in w if x > M13_S) / len(w),
        "masch_min": maschine_s / 60,
        "spitze": spitze,
        "fehlt": fehlt,
    }


def lauf(jobs: list[Job], bau, bremse: float, quote: float, seed: int) -> dict[str, float | None]:
    rest, gefangen = mit_vorpruefung(jobs, quote, seed)
    e = motor(rest, bau(), bremse)
    urteil = gefangen | e.pol.urteil
    return kennzahlen(jobs, urteil, e.pol.start, {j.sha: j.ank_eff for j in rest}, e.maschine_s, e.spitze)


def stress(jobs: list[Job], bau, bremse: float, quote: float, seed: int, runden: int = 200) -> tuple[float, float, float]:
    """M13: 10 Tickets gleichzeitig auf leerer Maschine (Ergebnis-Mix aus den Daten), `runden` Ziehungen.
    Mit Vorprüfung: gefangene Rote fallen raus, der Rest kommt VOR_S später. Rückgabe: Max-Warte Median, P90, % > 15 min."""
    rng = random.Random(seed)
    maxw, ueber = [], 0
    for r in range(runden):
        zehn = [replace(j, ank=0.0, ank_eff=0.0) for j in rng.sample(jobs, 10)]
        rest, _ = mit_vorpruefung(zehn, quote, seed + r)
        if not rest:
            maxw.append(0.0)
            continue
        e = motor(rest, bau(), bremse)
        w = max(e.pol.start[j.sha] - j.ank_eff for j in rest)
        maxw.append(w)
        ueber += w > M13_S
    return statistics.median(maxw), pct(maxw, 90) or 0.0, 100 * ueber / runden


# ---------------------------------------------------------------- Ausgabe
def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--schlange", type=Path, required=True, help="Ordner mit fahrtenbuch.jsonl, klon/, laeufe/")
    ap.add_argument("--bis", default=None)
    ap.add_argument("--tage", type=int, default=14)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--ohne-ruest", action="store_true", help="Rüstzeit 0 (rohes Modell)")
    a = ap.parse_args()
    jobs, meta = lade(a.schlange, datetime.fromisoformat(a.bis) if a.bis else None, a.tage)
    for j in jobs:
        j.ank_eff = j.ank
    tv = float(meta["t_voll"])
    arten: dict[str, int] = {}
    for j in jobs:
        arten[j.art] = arten.get(j.art, 0) + 1
    print(
        f"Zeitraum {meta['ab']:%Y-%m-%d %H:%M} bis {meta['ende']:%Y-%m-%d %H:%M}, {meta['zeilen']} Ticket-Zeilen, "
        f"{len(jobs)} Tickets (sha), {meta['ohne_sha']} ohne Committer-Zeit übersprungen. Arten: {arten}. "
        f"T_voll (Median gate_s VOLL grün, n={meta['n_voll']}) = {tv:.0f} s.\n"
    )

    # Ist
    ist_u = {j.sha: j.ist_urteil for j in jobs if j.ist_urteil}
    ist_s = {j.sha: j.ist_start for j in jobs}
    k_ist = kennzahlen(jobs, ist_u, ist_s, {j.sha: j.ank for j in jobs}, sum(j.gesamt for j in jobs), 0)
    grenze = datetime.fromisoformat("2026-11-19T06:18:30+01:00").timestamp()  # erstes Bündel im Fahrtenbuch
    rg, rs, nl = ruestzeit(jobs, grenze)
    if not a.ohne_ruest:
        RUEST.update(gate=rg, skip=rs)
    print(f"Rüstzeit je Lauf (gemessen, serieller Abschnitt, n={nl} Lücken): Gate {RUEST['gate']:.0f} s, "
          f"SKIP {RUEST['skip']:.0f} s.\n")
    vor_b = [j for j in jobs if j.ank < grenze]

    S = a.seed
    zeilen: list[tuple[str, object, float, float]] = [
        ("S0 seriell (Kontrolle)", lambda: S0(), 1.0, 0.0),
        ("S0 + V 50 %", lambda: S0(), 1.0, 0.5),
        ("S0 + V 80 %", lambda: S0(), 1.0, 0.8),
    ]
    for mb in (4, 8):
        for var in ("A", "B"):
            zeilen.append((f"S1 Batch max {mb}, Dauer {var}", lambda mb=mb, var=var: S1(mb, var, tv), 1.0, 0.0))
    for var in ("A", "B"):
        for q in (0.5, 0.8):
            zeilen.append((f"S1 max 4 {var} + V {q:.0%}", lambda var=var: S1(4, var, tv), 1.0, q))
    zeilen.append(("S1 max 4 A, 2 Slots, Bremse 1,5", lambda: S1(4, "A", tv, 2), 1.5, 0.0))
    for k in (2, 3):
        for b in (1.0, 1.5) + ((3.0,) if k == 2 else ()):
            zeilen.append((f"S2 k={k}, Bremse {b:.1f}".replace(".", ","), lambda k=k: S2(k), b, 0.0))
    alle = frozenset(o for j in jobs for o in j.ordner)
    zeilen.append(("Untergrenze: unbegrenzt parallel, Bremse 1,0", lambda: S3(10**6, alle), 1.0, 0.0))
    for k, b in ((2, 1.5), (3, 1.5)):
        zeilen.append((f"S2 k={k}, Bremse 1,5 + V 80 %", lambda k=k: S2(k), b, 0.8))
    for name, ohne in (("roh", frozenset()), ("ohne docs", frozenset({"docs"}))):
        for b in (1.0, 1.5):
            zeilen.append(
                (f"S3 Spuren {name}, Deckel 3, Bremse {b:.1f}".replace(".", ","), lambda ohne=ohne: S3(3, ohne), b, 0.0)
            )

    def zf(x: float | None) -> str:
        return "–" if x is None else f"{x:.0f}"

    print(
        "| Strategie | Push→Urteil Median s | P90 s | Max s | längste Wartezeit s | Warte > 15 min % | "
        "Gate-Min gesamt | Spitze parallel | M13-Probe: Max-Warte Median / P90 s, Runden > 15 min % |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    k = k_ist
    print(
        f"| **Ist (Fahrtenbuch)** | {zf(k['med'])} | {zf(k['p90'])} | {zf(k['max'])} | {zf(k['w_max'])} | "
        f"{k['m13']:.1f} | {zf(k['masch_min'])}¹ | – | – |"
    )
    for name, bau, b, q in zeilen:
        k = lauf(jobs, bau, b, q, S)
        sm, sp, su = stress(jobs, bau, b, q, S)
        print(
            f"| {name} | {zf(k['med'])} | {zf(k['p90'])} | {zf(k['max'])} | {zf(k['w_max'])} | {k['m13']:.1f} | "
            f"{zf(k['masch_min'])} | {k['spitze']} | {sm:.0f} / {sp:.0f}, {su:.0f} % |"
        )
    print("\n¹ Ist-Gate-Minuten = Summe gate_s aller Ticket-Zeilen je sha (ohne verdeckte Bündel-Gesamtläufe).\n")

    # Kontrolle nach Abschnitt (vor Bündel = rein seriell)
    print("Kontrolle S0 gegen Ist, getrennt nach Abschnitt (Ankunft vor/nach erstem Bündel 2026-11-19 06:18):\n")
    print("| Abschnitt | Tickets | Ist Median / P90 | S0 Median / P90 |\n|---|---|---|---|")
    e = motor(jobs, S0(), 1.0)
    for nm, sel in (("vor Bündel (seriell)", lambda j: j.ank < grenze), ("ab Bündel", lambda j: j.ank >= grenze)):
        js = [j for j in jobs if sel(j) and j.art in ("gruen", "rot")]
        iv = [j.ist_urteil - j.ank for j in js if j.ist_urteil]
        sv = [e.pol.urteil[j.sha] - j.ank for j in js]
        print(
            f"| {nm} | {len(js)} | {statistics.median(iv):.0f} / {pct(iv, 90):.0f} | "
            f"{statistics.median(sv):.0f} / {pct(sv, 90):.0f} |"
        )
    _ = vor_b
    # Überlappung der Ordner (für S3)
    gr = [j for j in jobs if j.art in ("gruen", "rot")]
    paare = ueb = ueb2 = 0
    for x in range(len(gr) - 1):
        for y in range(x + 1, min(len(gr), x + 6)):  # nur zeitlich benachbarte (5 Nachfolger)
            paare += 1
            ueb += bool(gr[x].ordner & gr[y].ordner)
            ueb2 += bool((gr[x].ordner - {"docs"}) & (gr[y].ordner - {"docs"}))
    print(
        f"\nOrdner-Überlappung benachbarter Tickets (je 5 Nachfolger, {paare} Paare): roh {100 * ueb / paare:.0f} %, "
        f"ohne docs {100 * ueb2 / paare:.0f} %."
    )


if __name__ == "__main__":
    main()
