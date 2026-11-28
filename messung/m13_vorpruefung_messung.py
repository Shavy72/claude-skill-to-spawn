"""Mess- und Auswerteskript für Etappe 3b-6 (#106): Vorprüfung (Stufe 0–3), 2. Gate-Platz & M13.

Zweck:
1. Fährt einen reproduzierbaren 10-Ticket-Parallellauf im isolierten Temp-Probe-Repo durch
   ``to_spawn.pruef.vorpruefung.GatePool`` (Bremse aus #105: ``1,34× <= 1,5×`` → 2 Gate-Plätze)
   und misst Wandzeiten, Wartezeiten, ``MemAvailable``, Swap-Delta, 1-Minuten-Last sowie den
   Rot-Beweis (Syntax-Fehler-Tickets abgewiesen ohne Gate-Platz).
2. Wertet optional ein echtes Schlangen-Fahrtenbuch (``--schlange <ordner>``) über
   ``messung.schlange_sim`` mit der in #105 real gemessenen Bremse (``1,34×`` / ``1,29×`` statt
   der alten Annahme ``3,0×``) und realistischer Vorprüfungs-Dauer gegen die M13-Schwellen aus:
   Median ``<= 112 s``, P90 ``<= 1213 s``, 10 Tickets gleichzeitig ohne RAM-Riss, keins ``> 15 min``
   Warten, Last ``< 12``.
3. Liefert eine ehrliche M13-Bewertung: werden Schwellen gerissen oder fehlt der Nachweis im
   App-Staging-Betrieb, bleibt ``erfuellt=False`` mit explizitem Hinweis „Erreichbarkeit unbelegt“.

Sicherheit:
- Fasst niemals ein Produktiv- oder App-Repo schreibend an (nur Temp-Repo im Temp-Ordner,
  garantiert in ``try/finally`` gelöscht; Fahrtenbuch nur lesend).
- Prüft vor dem Last-Lauf die 1-Minuten-Last (``< 12,0``).
- Keine Personennamen, keine Benutzerpfade, keine Geheimnisse.
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_WURZEL = Path(__file__).resolve().parents[1]
if str(REPO_WURZEL) not in sys.path:
    sys.path.insert(0, str(REPO_WURZEL))

from messung import schlange_sim
from messung.pruefzeit_last_290 import (
    LastZuHochFehler,
    pruefe_last_grenze,
    speicher_stand,
)
from to_spawn import prozessbaum, speicher
from to_spawn.pruef import vorpruefung

log = logging.getLogger("messung.m13_vorpruefung")

#: M13-Zielschwellen (Hälfte der Vorher-Werte [VZ] 223 s / 2426 s, Vergleich 6.4).
M13_MEDIAN_MAX_S: float = 112.0
M13_P90_MAX_S: float = 1213.0
M13_WARTE_MAX_S: float = 900.0
M13_LAST_MAX: float = 12.0
#: Gemessene Bremse aus Etappe 3b-5 (#105, worksteal / loadfile).
BREMSE_3B5_WORKSTEAL: float = 1.34
BREMSE_3B5_LOADFILE: float = 1.29
#: Mindest-RAM-Reserve gegen RAM-Riss (MiB).
RAM_RISS_GRENZE_MIB: int = 512


@dataclass(frozen=True)
class M13Urteil:
    """Ehrliche Bewertung gegen alle M13-Kriterien."""

    erfuellt: bool
    median_ok: bool
    p90_ok: bool
    ram_ok: bool
    warte_ok: bool
    last_ok: bool
    erreichbarkeit_belegt: bool
    hinweis: str


@dataclass(frozen=True)
class TicketMessung:
    """Ergebnis eines einzelnen Tickets im 10-Ticket-Parallellauf."""

    name: str
    art: str
    zugelassen: bool
    gate_platz_belegt: bool
    platz_nr: int | None
    vorpruef_s: float
    warte_s: float
    gesamt_s: float
    urteil: str


@dataclass(frozen=True)
class ZehnTicketMessung:
    """Verdichtete Messwerte des realen 10-Ticket-Parallellaufs auf der Maschine."""

    tickets_gesamt: int
    bremse_faktor: float
    syntax_tickets: int
    syntax_abgewiesen: int
    syntax_ohne_gate_platz: bool
    doku_ohne_gate: int
    gate_belegungen: int
    max_gleichzeitig_gates: int
    median_gesamt_s: float
    p90_gesamt_s: float
    max_gesamt_s: float
    max_warte_s: float
    ram_frei_min_mib: int
    swap_delta_mib: int
    ram_riss: bool
    last_vorher: float
    last_spitze: float
    dauer_wand_s: float
    tickets: tuple[TicketMessung, ...]


@dataclass(frozen=True)
class SchlangeSzenario:
    """Ergebnis einer Fahrtenbuch-Simulation mit gemessener 3b-5-Bremse und Vorprüfung."""

    name: str
    bremse: float
    vor_s: float
    fangquote: float
    voll_faktor: float
    median_s: float
    p90_s: float
    max_s: float
    warte_ueber_15m_prozent: float
    gate_min: float
    spitze_parallel: int
    stress_max_warte_med_s: float
    stress_max_warte_p90_s: float
    stress_ueber_15m_prozent: float
    urteil: M13Urteil


def bewerte_m13(
    *,
    median_s: float,
    p90_s: float,
    ram_riss: bool,
    stress_ueber_15m_prozent: float,
    last_spitze: float,
    erreichbarkeit_belegt: bool,
) -> M13Urteil:
    """Bewertet Mess- oder Simulationszahlen ehrlich gegen alle M13-Zielschwellen."""
    median_ok = median_s <= M13_MEDIAN_MAX_S
    p90_ok = p90_s <= M13_P90_MAX_S
    ram_ok = not ram_riss
    warte_ok = stress_ueber_15m_prozent <= 0.0
    last_ok = last_spitze < M13_LAST_MAX
    erfuellt = median_ok and p90_ok and ram_ok and warte_ok and last_ok and erreichbarkeit_belegt

    offene: list[str] = []
    if not median_ok:
        offene.append(f"Median {median_s:.1f} s > {M13_MEDIAN_MAX_S:.0f} s")
    if not p90_ok:
        offene.append(f"P90 {p90_s:.1f} s > {M13_P90_MAX_S:.0f} s")
    if not ram_ok:
        offene.append("RAM-Riss erkannt")
    if not warte_ok:
        offene.append(f"10-Ticket-Probe: {stress_ueber_15m_prozent:.1f} % Runden > 15 min Warten")
    if not last_ok:
        offene.append(f"Last-Spitze {last_spitze:.2f} >= {M13_LAST_MAX:.0f}")
    if not erreichbarkeit_belegt:
        offene.append("Erreichbarkeit im App-Repo unbelegt (App-Staging/Voll-Lauf-Verkürzung offen)")

    hinweis = "Alle M13-Schwellen belegt erfüllt." if erfuellt else "Nicht erfüllt / offen: " + "; ".join(offene)
    return M13Urteil(
        erfuellt=erfuellt,
        median_ok=median_ok,
        p90_ok=p90_ok,
        ram_ok=ram_ok,
        warte_ok=warte_ok,
        last_ok=last_ok,
        erreichbarkeit_belegt=erreichbarkeit_belegt,
        hinweis=hinweis,
    )


def _git(ordner: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", "-c", "user.name=messung", "-c", "user.email=messung@lokal", *args],
        cwd=ordner,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        **prozessbaum.fensterlos(),
    )
    return erg.stdout.strip()


def _erstelle_probe_repo(ordner: Path) -> Path:
    """Legt ein isoliertes Python-Probe-Repo mit 6 Modulen, 6 Testdateien und Tag ``base`` an."""
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (ordner / "requirements.txt").write_text("pytest>=9.0\n", encoding="utf-8")
    (ordner / "README.md").write_text("# Probe M13\n", encoding="utf-8")
    (ordner / ".to-spawn").mkdir(exist_ok=True)
    (ordner / ".to-spawn" / "config.json").write_text(
        json.dumps({"pruefen": {"doku_pfade": ["README.md", "docs/**"]}}, indent=2) + "\n",
        encoding="utf-8",
    )
    (ordner / "paket").mkdir(exist_ok=True)
    (ordner / "paket" / "__init__.py").write_text("", encoding="utf-8")
    (ordner / "tests").mkdir(exist_ok=True)
    for idx in range(1, 7):
        (ordner / "paket" / f"mod_{idx}.py").write_text(
            f"def wert_{idx}(x: int) -> int:\n    return x + {idx}\n",
            encoding="utf-8",
        )
        (ordner / "tests" / f"test_mod_{idx}.py").write_text(
            textwrap.dedent(
                f"""\
                import pathlib
                import sys

                sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
                from paket.mod_{idx} import wert_{idx}


                def test_wert_{idx}_klein() -> None:
                    assert wert_{idx}(10) == {10 + idx}


                def test_wert_{idx}_null() -> None:
                    assert wert_{idx}(0) == {idx}
                """
            ),
            encoding="utf-8",
        )
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "base")
    _git(ordner, "tag", "base")
    return ordner


def _ticket_commit(ordner: Path, zweig: str, datei: str, inhalt: str, nachricht: str) -> str:
    _git(ordner, "checkout", "-q", "base")
    _git(ordner, "checkout", "-qb", zweig)
    ziel = ordner / datei
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(inhalt, encoding="utf-8")
    _git(ordner, "add", datei)
    _git(ordner, "commit", "-qm", nachricht)
    return _git(ordner, "rev-parse", "HEAD")


def messe_zehn_tickets_probe(
    *,
    bremse_faktor: float = BREMSE_3B5_WORKSTEAL,
    gate_arbeit_s: float = 0.25,
    max_start_last: float = M13_LAST_MAX,
) -> ZehnTicketMessung:
    """Fährt 10 gleichzeitige Tickets durch Vorprüfung + GatePool und misst Zeiten, RAM und Last."""
    last_vorher = pruefe_last_grenze(max_last=max_start_last)
    speicher_start = speicher_stand()

    with tempfile.TemporaryDirectory(prefix="to_spawn_m13_probe_") as huelle:
        basis_dir = Path(huelle)
        repo = _erstelle_probe_repo(basis_dir / "repo")
        sperren = basis_dir / "sperren"

        auftraege: list[tuple[str, str, str]] = [
            (
                "T01-syntax",
                "syntax",
                _ticket_commit(repo, "ticket/1-syntax", "paket/mod_1.py", "def kaputt(:\n    pass\n", "syntax 1"),
            ),
            (
                "T02-doku",
                "doku",
                _ticket_commit(repo, "ticket/2-doku", "README.md", "# Probe M13\n\nStand 1.\n", "doku 1"),
            ),
            (
                "T03-code",
                "code",
                _ticket_commit(
                    repo,
                    "ticket/3-code",
                    "paket/mod_2.py",
                    "def wert_2(x: int) -> int:\n    return x + 2 + 0\n",
                    "code 2",
                ),
            ),
            (
                "T04-code",
                "code",
                _ticket_commit(
                    repo,
                    "ticket/4-code",
                    "paket/mod_3.py",
                    "def wert_3(x: int) -> int:\n    return x + 3 + 0\n",
                    "code 3",
                ),
            ),
            (
                "T05-r3",
                "r3",
                _ticket_commit(repo, "ticket/5-r3", "requirements.txt", "pytest>=9.0\n# hinweis 1\n", "r3 1"),
            ),
            (
                "T06-syntax",
                "syntax",
                _ticket_commit(
                    repo, "ticket/6-syntax", "paket/mod_4.py", "def fehler(a, b\n    return a\n", "syntax 2"
                ),
            ),
            (
                "T07-code",
                "code",
                _ticket_commit(
                    repo,
                    "ticket/7-code",
                    "paket/mod_5.py",
                    "def wert_5(x: int) -> int:\n    return x + 5 + 0\n",
                    "code 5",
                ),
            ),
            (
                "T08-doku",
                "doku",
                _ticket_commit(repo, "ticket/8-doku", "README.md", "# Probe M13\n\nStand 2.\n", "doku 2"),
            ),
            (
                "T09-code",
                "code",
                _ticket_commit(
                    repo,
                    "ticket/9-code",
                    "paket/mod_6.py",
                    "def wert_6(x: int) -> int:\n    return x + 6 + 0\n",
                    "code 6",
                ),
            ),
            (
                "T10-r3",
                "r3",
                _ticket_commit(repo, "ticket/10-r3", "requirements.txt", "pytest>=9.0\n# hinweis 2\n", "r3 2"),
            ),
        ]
        _git(repo, "checkout", "-q", "base")

        pool = vorpruefung.GatePool(sperren, bremse_faktor=bremse_faktor)
        ergebnisse: list[TicketMessung] = []
        erg_lock = threading.Lock()
        stop_event = threading.Event()
        ram_min = speicher_start.frei_mib
        swap_max = speicher_start.swap_belegt_mib
        last_max = last_vorher

        def abtaster() -> None:
            nonlocal ram_min, swap_max, last_max
            while not stop_event.is_set():
                stand = speicher_stand()
                ram_min = min(ram_min, stand.frei_mib)
                swap_max = max(swap_max, stand.swap_belegt_mib)
                wert = speicher.last_1m()
                if wert is not None:
                    last_max = max(last_max, wert)
                stop_event.wait(0.1)

        def arbeite(name: str, art: str, sha: str) -> None:
            t0 = time.monotonic()
            with pool.einlass(repo, "base", sha, warten_s=120.0) as einlass:
                if einlass.gate_platz_belegt and gate_arbeit_s > 0:
                    time.sleep(gate_arbeit_s)
                gesamt = round(time.monotonic() - t0, 3)
                eintrag = TicketMessung(
                    name=name,
                    art=art,
                    zugelassen=einlass.zugelassen,
                    gate_platz_belegt=einlass.gate_platz_belegt,
                    platz_nr=einlass.platz_nr,
                    vorpruef_s=float(einlass.vorpruefung.zeile.get("gesamt_s", 0.0)),
                    warte_s=einlass.warte_s,
                    gesamt_s=gesamt,
                    urteil=str(einlass.vorpruefung.zeile.get("urteil", "")),
                )
            with erg_lock:
                ergebnisse.append(eintrag)

        sampler = threading.Thread(target=abtaster, daemon=True)
        sampler.start()
        wand_start = time.monotonic()
        threads = [threading.Thread(target=arbeite, args=(n, a, s)) for n, a, s in auftraege]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=300)
        dauer_wand = round(time.monotonic() - wand_start, 2)
        stop_event.set()
        sampler.join(timeout=2)

    sortiert = tuple(sorted(ergebnisse, key=lambda x: x.name))
    syntax_liste = [t for t in sortiert if t.art == "syntax"]
    zeiten = [t.gesamt_s for t in sortiert]
    wartezeiten = [t.warte_s for t in sortiert]
    swap_delta = max(0, swap_max - speicher_start.swap_belegt_mib)
    ram_riss = ram_min < RAM_RISS_GRENZE_MIB

    return ZehnTicketMessung(
        tickets_gesamt=len(sortiert),
        bremse_faktor=bremse_faktor,
        syntax_tickets=len(syntax_liste),
        syntax_abgewiesen=sum(1 for t in syntax_liste if not t.zugelassen),
        syntax_ohne_gate_platz=all(not t.gate_platz_belegt for t in syntax_liste),
        doku_ohne_gate=sum(1 for t in sortiert if t.art == "doku" and t.zugelassen and not t.gate_platz_belegt),
        gate_belegungen=pool.belegungen,
        max_gleichzeitig_gates=pool.max_gleichzeitig,
        median_gesamt_s=round(statistics.median(zeiten), 2) if zeiten else 0.0,
        p90_gesamt_s=round(schlange_sim.pct(zeiten, 90) or 0.0, 2),
        max_gesamt_s=round(max(zeiten), 2) if zeiten else 0.0,
        max_warte_s=round(max(wartezeiten), 3) if wartezeiten else 0.0,
        ram_frei_min_mib=ram_min,
        swap_delta_mib=swap_delta,
        ram_riss=ram_riss,
        last_vorher=round(last_vorher, 2),
        last_spitze=round(last_max, 2),
        dauer_wand_s=dauer_wand,
        tickets=sortiert,
    )


def _simuliere_szenario(
    jobs: Sequence[schlange_sim.Job],
    *,
    name: str,
    bau: Any,
    bremse: float,
    vor_s: float,
    fangquote: float,
    voll_faktor: float,
    seed: int,
    last_spitze: float,
    erreichbarkeit_belegt: bool,
) -> SchlangeSzenario:
    skaliert = [replace(j, vor=j.vor * voll_faktor, end=j.end * voll_faktor, ank_eff=j.ank) for j in jobs]
    alt_vor_s = schlange_sim.VOR_S
    schlange_sim.VOR_S = vor_s
    try:
        k = schlange_sim.lauf(skaliert, bau, bremse, fangquote, seed)
        sm, sp, su = schlange_sim.stress(skaliert, bau, bremse, fangquote, seed)
    finally:
        schlange_sim.VOR_S = alt_vor_s

    med = float(k["med"] or 0.0)
    p90 = float(k["p90"] or 0.0)
    urteil = bewerte_m13(
        median_s=med,
        p90_s=p90,
        ram_riss=False,
        stress_ueber_15m_prozent=su,
        last_spitze=last_spitze,
        erreichbarkeit_belegt=erreichbarkeit_belegt,
    )
    return SchlangeSzenario(
        name=name,
        bremse=bremse,
        vor_s=vor_s,
        fangquote=fangquote,
        voll_faktor=voll_faktor,
        median_s=round(med, 1),
        p90_s=round(p90, 1),
        max_s=round(float(k["max"] or 0.0), 1),
        warte_ueber_15m_prozent=round(float(k["m13"] or 0.0), 1),
        gate_min=round(float(k["masch_min"] or 0.0), 0),
        spitze_parallel=int(k["spitze"] or 0),
        stress_max_warte_med_s=round(sm, 1),
        stress_max_warte_p90_s=round(sp, 1),
        stress_ueber_15m_prozent=round(su, 1),
        urteil=urteil,
    )


def werte_schlange_mit_3b5_bremse(
    schlange_ordner: Path,
    *,
    bis: str | None = "2026-11-21T20:20:00+01:00",
    tage: int = 14,
    seed: int = 2026,
    last_spitze_3b5: float = 11.51,
) -> tuple[dict[str, Any], list[SchlangeSzenario]]:
    """Rechnet das reale Fahrtenbuch mit der gemessenen 3b-5-Bremse (1,34× / 1,29×) durch."""
    jobs, meta = schlange_sim.lade(
        schlange_ordner,
        datetime.fromisoformat(bis) if bis else None,
        tage,
    )
    for j in jobs:
        j.ank_eff = j.ank
    grenze = datetime.fromisoformat("2026-11-19T06:18:30+01:00").timestamp()
    rg, rs, nl = schlange_sim.ruestzeit(jobs, grenze)
    schlange_sim.RUEST.update(gate=rg, skip=rs)

    szenarien = [
        _simuliere_szenario(
            jobs,
            name="S0 seriell (Kontrolle)",
            bau=lambda: schlange_sim.S0(),
            bremse=1.0,
            vor_s=0.0,
            fangquote=0.0,
            voll_faktor=1.0,
            seed=seed,
            last_spitze=last_spitze_3b5,
            erreichbarkeit_belegt=False,
        ),
        _simuliere_szenario(
            jobs,
            name="S0 + Vorprüfung 5 s (Fangquote 50 %)",
            bau=lambda: schlange_sim.S0(),
            bremse=1.0,
            vor_s=5.0,
            fangquote=0.5,
            voll_faktor=1.0,
            seed=seed,
            last_spitze=last_spitze_3b5,
            erreichbarkeit_belegt=False,
        ),
        _simuliere_szenario(
            jobs,
            name="2 Gate-Plätze (S2 k=2), Bremse 1,34× (#105 worksteal)",
            bau=lambda: schlange_sim.S2(2),
            bremse=BREMSE_3B5_WORKSTEAL,
            vor_s=0.0,
            fangquote=0.0,
            voll_faktor=1.0,
            seed=seed,
            last_spitze=last_spitze_3b5,
            erreichbarkeit_belegt=False,
        ),
        _simuliere_szenario(
            jobs,
            name="2 Gate-Plätze, Bremse 1,34× + Vorprüfung 5 s (50 %)",
            bau=lambda: schlange_sim.S2(2),
            bremse=BREMSE_3B5_WORKSTEAL,
            vor_s=5.0,
            fangquote=0.5,
            voll_faktor=1.0,
            seed=seed,
            last_spitze=last_spitze_3b5,
            erreichbarkeit_belegt=False,
        ),
        _simuliere_szenario(
            jobs,
            name="2 Gate-Plätze, Bremse 1,34× + Vorprüfung 5 s (80 %)",
            bau=lambda: schlange_sim.S2(2),
            bremse=BREMSE_3B5_WORKSTEAL,
            vor_s=5.0,
            fangquote=0.8,
            voll_faktor=1.0,
            seed=seed,
            last_spitze=last_spitze_3b5,
            erreichbarkeit_belegt=False,
        ),
        _simuliere_szenario(
            jobs,
            name="2 Gate-Plätze, Bremse 1,29× (#105 loadfile) + V 5 s (80 %)",
            bau=lambda: schlange_sim.S2(2),
            bremse=BREMSE_3B5_LOADFILE,
            vor_s=5.0,
            fangquote=0.8,
            voll_faktor=1.0,
            seed=seed,
            last_spitze=last_spitze_3b5,
            erreichbarkeit_belegt=False,
        ),
        _simuliere_szenario(
            jobs,
            name="Sensitivität (unbelegt): 2 Plätze 1,34× + V 5 s (80 %) + Voll −45 %",
            bau=lambda: schlange_sim.S2(2),
            bremse=BREMSE_3B5_WORKSTEAL,
            vor_s=5.0,
            fangquote=0.8,
            voll_faktor=0.55,
            seed=seed,
            last_spitze=last_spitze_3b5,
            erreichbarkeit_belegt=False,
        ),
    ]
    info = {
        "tickets": len(jobs),
        "zeilen": meta["zeilen"],
        "t_voll_s": round(float(meta["t_voll"]), 1),
        "ruest_gate_s": round(rg, 1),
        "ruest_skip_s": round(rs, 1),
        "ruest_luecken": nl,
    }
    return info, szenarien


def bericht_markdown(
    probe: ZehnTicketMessung,
    schlange_info: dict[str, Any] | None = None,
    szenarien: Sequence[SchlangeSzenario] = (),
) -> str:
    """Formatiert Mess- und Simulationswerte als kompakten Markdown-Bericht."""
    zeilen = [
        "# Etappe 3b-6 (#106): Vorprüfung (Stufe 0–3), 2. Gate-Platz & M13-Messung",
        "",
        "## 1. Realer 10-Ticket-Parallellauf (Probe-Repo, isoliert, Last < 12)",
        "",
        f"- Tickets gesamt: **{probe.tickets_gesamt}** (Bremse-Vorgabe `{probe.bremse_faktor:.2f}× <= 1,5×` → 2 Gate-Plätze)",
        (
            f"- Rot-Beweis Syntax-Fehler: **{probe.syntax_abgewiesen}/{probe.syntax_tickets}** in Stufe 1 abgewiesen, "
            f"ohne Gate-Platz: **{'ja' if probe.syntax_ohne_gate_platz else 'NEIN'}**"
        ),
        f"- Nur-Doku ohne Gate-Belegung: **{probe.doku_ohne_gate}**",
        f"- Gate-Belegungen gesamt: **{probe.gate_belegungen}**, Spitze parallel: **{probe.max_gleichzeitig_gates}** (Deckel 2)",
        f"- Push → Urteil: Median **{probe.median_gesamt_s:.2f} s**, P90 **{probe.p90_gesamt_s:.2f} s**, Max **{probe.max_gesamt_s:.2f} s**",
        f"- Längste Wartezeit auf Gate-Platz: **{probe.max_warte_s:.3f} s** (Schwelle <= 900 s)",
        (
            f"- Speicher & Last: `MemAvailable min` **{probe.ram_frei_min_mib} MiB**, Swap-Delta **+{probe.swap_delta_mib} MiB** "
            f"(RAM-Riss: **{'ja' if probe.ram_riss else 'nein'}**), Last **{probe.last_vorher:.2f} → {probe.last_spitze:.2f}** (< 12)"
        ),
        "",
    ]
    if schlange_info and szenarien:
        zeilen.extend(
            [
                "## 2. App-Schlange Simulation mit gemessener 3b-5-Bremse (#105)",
                "",
                (
                    f"Datengrundlage: {schlange_info['tickets']} Tickets ({schlange_info['zeilen']} Zeilen), "
                    f"`T_voll` Median {schlange_info['t_voll_s']:.0f} s, Rüstzeit Gate {schlange_info['ruest_gate_s']:.0f} s."
                ),
                "",
                "| Szenario | Median s (<=112) | P90 s (<=1213) | Warte >15m % | 10-Ticket >15m % | Gate-Min | Urteil |",
                "|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for s in szenarien:
            marke = "erfüllt" if s.urteil.erfuellt else "nicht erreicht / unbelegt"
            zeilen.append(
                f"| {s.name} | {s.median_s:.0f} | {s.p90_s:.0f} | {s.warte_ueber_15m_prozent:.1f} | "
                f"{s.stress_ueber_15m_prozent:.0f} % | {s.gate_min:.0f} | {marke} |"
            )
        zeilen.append("")
        zeilen.append(
            "**Ehrliches Fazit:** Mit den realen Voll-Lauf-Zeiten (~1839 s) bleibt M13 im App-Repo "
            "(Median <= 112 s, P90 <= 1213 s, 0 % > 15 min Warten) auch mit Vorprüfung + 2 Gate-Plätzen "
            "(Bremse 1,29×–1,34×) **unbelegt / nicht erreicht**. Erst eine deutliche Verkürzung des "
            "Voll-Laufs selbst plus Staging-Messung im App-Repo kann P90 unter 1213 s drücken."
        )
    return "\n".join(zeilen)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Etappe 3b-6 (#106): Vorprüfung, 2. Gate-Platz & M13-Messung.")
    ap.add_argument("--schlange", type=Path, default=None, help="Ordner mit fahrtenbuch.jsonl und klon/ (nur lesend)")
    ap.add_argument("--bis", default="2026-11-21T20:20:00+01:00", help="Endzeitpunkt für Fahrtenbuch-Auswertung")
    ap.add_argument("--tage", type=int, default=14, help="Zeitraum in Tagen für Fahrtenbuch-Auswertung")
    ap.add_argument("--seed", type=int, default=2026, help="Zufalls-Seed für Simulation")
    ap.add_argument("--bremse", type=float, default=BREMSE_3B5_WORKSTEAL, help="Bremse-Faktor aus 3b-5 (Standard 1.34)")
    ap.add_argument("--json", dest="als_json", action="store_true", help="JSON statt Markdown ausgeben")
    args = ap.parse_args(argv)

    try:
        probe = messe_zehn_tickets_probe(bremse_faktor=args.bremse)
    except LastZuHochFehler as fehler:
        log.error("%s", fehler)
        return 2

    info: dict[str, Any] | None = None
    szenarien: list[SchlangeSzenario] = []
    if args.schlange is not None:
        info, szenarien = werte_schlange_mit_3b5_bremse(
            args.schlange,
            bis=args.bis,
            tage=args.tage,
            seed=args.seed,
        )

    if args.als_json:
        ausgabe = {
            "probe": asdict(probe),
            "schlange_info": info,
            "szenarien": [asdict(s) for s in szenarien],
        }
        sys.stdout.write(json.dumps(ausgabe, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(bericht_markdown(probe, info, szenarien) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
