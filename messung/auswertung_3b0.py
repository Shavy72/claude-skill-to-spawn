"""Reproduzierbare Auswertung und Entscheidung für Mess-Vorstufe 3b-0 (#98).

Liest die Rohdaten aus Teil (a) (Rückblick-JSONL, R1/R3-Quote-JSONL) sowie optional
die Voll- und Auswahl-Laufzeiten aus Teil (b) (JSON) und berechnet:
  - Median, P90, Max der Auswahl-Dauer und RAM-Spitze sowie Last-Spanne,
  - Fehlgriffe bei roten Läufen inkl. Gegenprobe ``ce8a95f`` (``test_gate_bash_240.py``),
  - R1/R3-Quote je Ticket,
  - Entscheidung des Abbruch-Kriteriums 3b-0 (a)/(b)/(c) laut ``docs/PRUEFTOR_VERGLEICH.md`` 6.4.

Rein analytisch (nur Standardbibliothek, Linux + Windows).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import statistics
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

LOG = logging.getLogger("auswertung_3b0")

SCHWELLE_A = 0.50
SCHWELLE_B = 0.50
GEGENPROBE_SHA = "ce8a95f"
GEGENPROBE_TEST = "tests/test_gate_bash_240.py"

DOKU_ENDUNGEN: tuple[str, ...] = (".md", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".txt")
R3_MUSTER: tuple[str, ...] = (
    "pytest.ini",
    "conftest.py",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "requirements*.txt",
    "*.lock",
    "package-lock.json",
    "Dockerfile*",
    "docker-compose*.yml",
)


def normalisiere_pfad(pfad: str) -> str:
    """Vereinheitlicht Pfadtrenner (Windows ``\\`` → ``/``) und entfernt führendes ``./``."""
    return pfad.replace("\\", "/").removeprefix("./")


def ist_doku(pfad: str) -> bool:
    """Prüft, ob ``pfad`` nach 3b-0-Regel als reine Doku gilt."""
    norm = normalisiere_pfad(pfad)
    return norm.startswith("docs/") or norm.lower().endswith(DOKU_ENDUNGEN)


def ist_r3(pfad: str) -> bool:
    """Prüft, ob ``pfad`` als Prüf-Konfig oder Abhängigkeit (R3) gilt."""
    norm = normalisiere_pfad(pfad)
    name = norm.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, m) for m in R3_MUSTER)


def klassifiziere_dateien(dateien: Iterable[str]) -> tuple[list[str], list[str]]:
    """Liefert ``(r1_dateien, r3_dateien)`` sortiert für eine Dateiliste."""
    normiert = {normalisiere_pfad(d) for d in dateien if d.strip()}
    r1 = sorted(d for d in normiert if not d.endswith(".py") and not ist_doku(d))
    r3 = sorted(d for d in normiert if ist_r3(d))
    return r1, r3


def _git_diff_dateien(repo: Path, basis: str, sha: str) -> set[str]:
    r = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), "diff", "--name-only", basis, sha],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if r.returncode != 0:
        meldung = (r.stderr or r.stdout).strip()[:300]
        raise RuntimeError(f"git diff fehlgeschlagen für {basis[:7]}..{sha[:7]} (rc={r.returncode}): {meldung}")
    return {normalisiere_pfad(z.strip()) for z in r.stdout.splitlines() if z.strip()}


def pruefe_rueckblick_r1_r3(zeilen: Sequence[dict[str, Any]], repo: Path) -> dict[str, Any]:
    """Prüft R1/R2/R3 je gerechnetem Rückblick-Lauf reproduzierbar gegen ``git diff basis..sha``."""
    gerechnet = [
        r
        for r in zeilen
        if isinstance(r.get("a"), dict) and isinstance(r.get("b"), dict) and r.get("basis") and r.get("sha")
    ]
    r1_laeufe = 0
    r3_laeufe = 0
    r2_laeufe = 0
    voll_laeufe = 0
    fehlgriffe_detail: list[dict[str, Any]] = []
    ungefangen_shas: list[str] = []

    for r in gerechnet:
        basis = str(r["basis"])
        sha = str(r["sha"])
        dateien = _git_diff_dateien(repo, basis, sha)
        r1_treffer, r3_treffer = klassifiziere_dateien(dateien)
        ist_r2 = r["b"].get("urteil") in ("SKIP", "FEHLER") and int(r.get("geaendert_n") or 0) > 0
        if r1_treffer:
            r1_laeufe += 1
        if r3_treffer:
            r3_laeufe += 1
        if ist_r2:
            r2_laeufe += 1
        if r1_treffer or r3_treffer or ist_r2:
            voll_laeufe += 1
        if r.get("kennungen") and r["b"].get("rot_verpasst"):
            gruende: list[str] = []
            if r1_treffer:
                gruende.append("R1")
            if r3_treffer:
                gruende.append("R3")
            if ist_r2:
                gruende.append("R2")
            kurz = sha[:7]
            if not gruende and kurz not in ungefangen_shas:
                ungefangen_shas.append(kurz)
            fehlgriffe_detail.append(
                {
                    "sha": kurz,
                    "basis": basis[:7],
                    "gefangen_durch": gruende,
                    "r1_beispiel": r1_treffer[:3],
                    "rot_verpasst": list(r["b"].get("rot_verpasst") or []),
                }
            )

    n = len(gerechnet)
    return {
        "laeufe_n": n,
        "r1_laeufe": r1_laeufe,
        "r3_laeufe": r3_laeufe,
        "r2_laeufe": r2_laeufe,
        "voll_laeufe": voll_laeufe,
        "voll_anteil": (voll_laeufe / n) if n else 0.0,
        "fehlgriffe_detail": fehlgriffe_detail,
        "ungefangen_shas": ungefangen_shas,
    }


def _p90(werte: Sequence[float]) -> float:
    if not werte:
        return 0.0
    sortiert = sorted(werte)
    return float(sortiert[int(0.9 * (len(sortiert) - 1))])


def _lese_jsonl(pfad: Path) -> list[dict[str, Any]]:
    zeilen: list[dict[str, Any]] = []
    for nr, text in enumerate(pfad.read_text(encoding="utf-8").splitlines(), 1):
        if not text.strip():
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as fehler:
            LOG.warning("kaputte JSON-Zeile in %s:%d übersprungen: %s", pfad.name, nr, fehler)
            continue
        if isinstance(obj, dict):
            zeilen.append(obj)
    return zeilen


def _auswerten_seite(gerechnet: Sequence[dict[str, Any]], schluessel: str) -> dict[str, Any]:
    dauern = [float(r[schluessel]["dauer_s"]) for r in gerechnet if "dauer_s" in r[schluessel]]
    ram = [float(r[schluessel]["ram_mb"]) for r in gerechnet if r[schluessel].get("ram_mb") is not None]
    anteile = [float(r[schluessel]["anteil"]) for r in gerechnet if "anteil" in r[schluessel]]
    urteile: dict[str, int] = {}
    for r in gerechnet:
        u = str(r[schluessel].get("urteil") or "UNBEKANNT")
        urteile[u] = urteile.get(u, 0) + 1
    rote = [r for r in gerechnet if r.get("kennungen")]
    fehlgriffe = [str(r.get("sha", ""))[:7] for r in rote if r[schluessel].get("rot_verpasst")]
    return {
        "dauer_median_s": round(statistics.median(dauern), 2) if dauern else 0.0,
        "dauer_p90_s": round(_p90(dauern), 2) if dauern else 0.0,
        "dauer_max_s": round(max(dauern), 2) if dauern else 0.0,
        "ram_median_mb": round(statistics.median(ram), 1) if ram else None,
        "ram_max_mb": round(max(ram), 1) if ram else None,
        "ram_n": len(ram),
        "anteil_mittel": round(statistics.mean(anteile), 4) if anteile else 0.0,
        "anteil_median": round(statistics.median(anteile), 4) if anteile else 0.0,
        "urteile": urteile,
        "fehlgriffe_n": len(fehlgriffe),
        "fehlgriffe_shas": fehlgriffe,
    }


def auswerten_rueckblick(zeilen: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Berechnet alle Kennzahlen und die Gegenprobe ``ce8a95f`` aus Rückblick-Zeilen."""
    gerechnet = [r for r in zeilen if isinstance(r.get("a"), dict) and isinstance(r.get("b"), dict)]
    ohne_basis = len(zeilen) - len(gerechnet)
    lasten = [float(r[k]["last"]) for r in gerechnet for k in ("a", "b") if r[k].get("last") is not None]
    rote = [r for r in gerechnet if r.get("kennungen")]
    gegenprobe = any(
        str(r.get("sha", "")).startswith(GEGENPROBE_SHA) and GEGENPROBE_TEST in (r["b"].get("rot_verpasst") or [])
        for r in rote
    )
    return {
        "gesamt": len(zeilen),
        "gerechnet": len(gerechnet),
        "ohne_basis": ohne_basis,
        "rote_n": len(rote),
        "last_min": round(min(lasten), 2) if lasten else None,
        "last_max": round(max(lasten), 2) if lasten else None,
        "gegenprobe_ce8a95f": gegenprobe,
        "a": _auswerten_seite(gerechnet, "a"),
        "b": _auswerten_seite(gerechnet, "b"),
    }


def auswerten_r1_quote(zeilen: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Berechnet die R1/R3/R2-Quoten je Ticket (ohne Division durch null)."""
    n = len(zeilen)
    r1 = sum(1 for z in zeilen if z.get("r1"))
    r3 = sum(1 for z in zeilen if z.get("r3"))
    r13 = sum(1 for z in zeilen if z.get("r1") or z.get("r3"))
    sub = [z for z in zeilen if z.get("r2") is not None]
    sub_voll = sum(1 for z in sub if z.get("r1") or z.get("r3") or z.get("r2"))
    return {
        "n": n,
        "r1": r1,
        "r1_anteil": (r1 / n) if n else 0.0,
        "r3": r3,
        "r3_anteil": (r3 / n) if n else 0.0,
        "r1_oder_r3": r13,
        "r1_oder_r3_anteil": (r13 / n) if n else 0.0,
        "r2_teilmenge_n": len(sub),
        "r2_teilmenge_voll": sub_voll,
    }


def entscheide_3b0(
    rb_stats: dict[str, Any],
    r1_stats: dict[str, Any],
    teil_b: dict[str, Any] | None,
    ungefangen_shas: Sequence[str],
) -> dict[str, Any]:
    """Wendet das Abbruch-Kriterium 3b-0 (a)/(b)/(c) aus PRUEFTOR_VERGLEICH 6.4 an."""
    # Kriterium (a): Median (Auswahl-Dauer + Lauf der Auswahl) / Voll-Lauf am selben Stand >= 50 %
    krit_a: dict[str, Any] = {"status": "offen", "ausgeloest": None, "anteil": None, "median_summe_s": None}
    if teil_b:
        voll = teil_b.get("voll_worksteal") or teil_b.get("voll_loadfile")
        auswahl_laeufe = teil_b.get("auswahl_laeufe") or []
        if isinstance(voll, dict) and voll.get("dauer_s") and auswahl_laeufe:
            voll_s = float(voll["dauer_s"])
            summen = [float(x["auswahl_s"]) + float(x["lauf_s"]) for x in auswahl_laeufe]
            med_summe = float(statistics.median(summen))
            anteil_a = med_summe / voll_s
            krit_a = {
                "status": "gemessen",
                "ausgeloest": anteil_a >= SCHWELLE_A,
                "anteil": anteil_a,
                "median_summe_s": round(med_summe, 2),
                "voll_s": round(voll_s, 2),
            }

    # Kriterium (b): R1–R3 schicken >= 50 % der Tickets voll
    anteil_b = float(r1_stats.get("r1_oder_r3_anteil", 0.0))
    krit_b = {
        "status": "gemessen",
        "ausgeloest": anteil_b >= SCHWELLE_B,
        "anteil": anteil_b,
        "voll": int(r1_stats.get("r1_oder_r3", 0)),
        "gesamt": int(r1_stats.get("n", 0)),
    }

    # Kriterium (c): mindestens ein Fehlgriff im Rückblick, den R1–R3 nicht fangen
    ungefangen = [s[:7] for s in ungefangen_shas]
    krit_c = {
        "status": "gemessen",
        "ausgeloest": len(ungefangen) > 0,
        "anzahl": len(ungefangen),
        "shas": ungefangen,
        "gegenprobe_ce8a95f": bool(rb_stats.get("gegenprobe_ce8a95f")),
    }

    ausgeloest = bool(krit_a["ausgeloest"]) or bool(krit_b["ausgeloest"]) or bool(krit_c["ausgeloest"])
    return {
        "kriterium_a": krit_a,
        "kriterium_b": krit_b,
        "kriterium_c": krit_c,
        "entscheidung": "variante_3" if ausgeloest else "kombination",
    }


def formatiere_markdown(
    rb_stats: dict[str, Any],
    r1_stats: dict[str, Any],
    teil_b: dict[str, Any] | None,
    urteil: dict[str, Any],
) -> str:
    """Erzeugt eine deterministische Markdown-Auswertung für Doku und Beleg."""
    a = rb_stats["a"]
    b = rb_stats["b"]
    ka = urteil["kriterium_a"]
    kb = urteil["kriterium_b"]
    kc = urteil["kriterium_c"]
    entscheidung_text = (
        "Variante 3 allein (schnellerer Voll-Lauf, keine Auswahl)"
        if urteil["entscheidung"] == "variante_3"
        else "Kombination (Variante 1 + Variante 3)"
    )

    zeilen = [
        "# Auswertung Mess-Vorstufe 3b-0 (#98)",
        "",
        (
            f"- Rückblick: {rb_stats['gerechnet']} gerechnete Läufe "
            f"({rb_stats['gesamt']} Zeilen, {rb_stats['ohne_basis']} ohne Basis, {rb_stats['rote_n']} rote mit Kennungen)."
        ),
        f"- Last-Spanne (1 min): {rb_stats['last_min']}–{rb_stats['last_max']}.",
        (
            f"- Gegenprobe `ce8a95f` (`{GEGENPROBE_TEST}`): "
            f"{'bestätigt (Fehlgriff von impacted roh)' if rb_stats['gegenprobe_ce8a95f'] else 'NICHT bestätigt'}."
        ),
        "",
        "## Teil (a): Auswahl-Dauer, RAM-Spitze, Fehlgriffe",
        "| Kennzahl | Eigenbau | impacted |",
        "|---|---|---|",
        f"| Auswahl-Dauer Median | {a['dauer_median_s']:.2f} s | {b['dauer_median_s']:.2f} s |",
        (
            f"| Auswahl-Dauer P90 / Max | {a['dauer_p90_s']:.2f} s / {a['dauer_max_s']:.2f} s | "
            f"{b['dauer_p90_s']:.2f} s / {b['dauer_max_s']:.2f} s |"
        ),
        (
            f"| RAM-Spitze Median / Max | {a['ram_median_mb']} / {a['ram_max_mb']} MB | "
            f"{b['ram_median_mb']} / {b['ram_max_mb']} MB |"
        ),
        (
            f"| Anteil Tests Mittel / Median | {a['anteil_mittel']:.1%} / {a['anteil_median']:.1%} | "
            f"{b['anteil_mittel']:.1%} / {b['anteil_median']:.1%} |"
        ),
        (
            f"| Fehlgriffe (rote Läufe) | {a['fehlgriffe_n']}/{rb_stats['rote_n']} ({', '.join(a['fehlgriffe_shas'])}) | "
            f"{b['fehlgriffe_n']}/{rb_stats['rote_n']} ({', '.join(b['fehlgriffe_shas'])}) |"
        ),
        "",
        "## Quote R1–R3 je Ticket",
        (
            f"- Tickets ausgewertet: {r1_stats['n']} · R1: {r1_stats['r1']} ({r1_stats['r1_anteil']:.1%}) · "
            f"R3: {r1_stats['r3']} ({r1_stats['r3_anteil']:.1%}) · "
            f"R1 oder R3 (voll): {r1_stats['r1_oder_r3']} ({r1_stats['r1_oder_r3_anteil']:.1%})."
        ),
    ]

    if teil_b:
        zeilen.extend(["", "## Teil (b): Voll-Lauf und Auswahl-Lauf am selben Stand"])
        for schluessel, titel in (
            ("voll_loadfile", "Voll-Lauf `-n 4 --dist loadfile`"),
            ("voll_worksteal", "Voll-Lauf `-n 4 --dist worksteal`"),
        ):
            lauf = teil_b.get(schluessel)
            if isinstance(lauf, dict):
                zeilen.append(
                    f"- {titel}: {lauf.get('dauer_s')} s, RAM-Spitze {lauf.get('ram_mb')} MB, Exit {lauf.get('rc')}."
                )

    a_text = (
        f"{'JA' if ka['ausgeloest'] else 'NEIN'} ({ka['median_summe_s']} s / {ka['voll_s']} s = {ka['anteil']:.1%})"
        if ka["status"] == "gemessen"
        else "offen (Teil b fehlt)"
    )
    zeilen.extend(
        [
            "",
            "## Abbruch-Kriterium 3b-0",
            f"- (a) Median (Auswahl + Lauf) >= 50 % Voll-Lauf: **{a_text}**",
            (
                f"- (b) R1–R3 schicken >= 50 % der Tickets voll: "
                f"**{'JA' if kb['ausgeloest'] else 'NEIN'}** ({kb['voll']}/{kb['gesamt']} = {kb['anteil']:.1%})"
            ),
            (
                f"- (c) Fehlgriff, den R1–R3 nicht fangen: "
                f"**{'JA' if kc['ausgeloest'] else 'NEIN'}** ({kc['anzahl']}: {', '.join(kc['shas'])})"
            ),
            f"- **Entscheidung:** {entscheidung_text}",
        ]
    )
    return "\n".join(zeilen) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser(description="Wertet 3b-0 Rohdaten reproduzierbar aus.")
    p.add_argument("--rueckblick", type=Path, required=True, help="JSONL aus Rückblick Teil (a)")
    p.add_argument("--r1-quote", type=Path, required=True, help="JSONL aus R1/R3-Quote Teil (a)")
    p.add_argument("--teil-b", type=Path, help="Optional: JSON mit Messwerten aus Teil (b)")
    p.add_argument(
        "--repo",
        type=Path,
        help="Optional: Git-Klon für automatische R1/R2/R3-Prüfung je Rückblick-Lauf (statt --ungefangen)",
    )
    p.add_argument(
        "--ungefangen",
        action="append",
        default=[],
        help="Commit-SHA eines von R1–R3 nicht gefangenen Fehlgriffs (wiederholbar)",
    )
    p.add_argument("--json", action="store_true", dest="als_json", help="Ausgabe als JSON statt Markdown")
    p.add_argument("--ausgabe", type=Path, help="Optional: Ergebnis in Datei schreiben")
    a = p.parse_args(argv)

    rb_zeilen = _lese_jsonl(a.rueckblick)
    rb_stats = auswerten_rueckblick(rb_zeilen)
    r1_stats = auswerten_r1_quote(_lese_jsonl(a.r1_quote))
    teil_b = json.loads(a.teil_b.read_text(encoding="utf-8")) if a.teil_b else None
    repo_pruefung = pruefe_rueckblick_r1_r3(rb_zeilen, a.repo) if a.repo else None
    ungefangen = list(a.ungefangen) or (repo_pruefung["ungefangen_shas"] if repo_pruefung else [])
    urteil = entscheide_3b0(rb_stats, r1_stats, teil_b, ungefangen)

    if a.als_json:
        text = (
            json.dumps(
                {
                    "rueckblick": rb_stats,
                    "r1_quote": r1_stats,
                    "repo_pruefung": repo_pruefung,
                    "teil_b": teil_b,
                    "urteil": urteil,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    else:
        text = formatiere_markdown(rb_stats, r1_stats, teil_b, urteil)

    if a.ausgabe:
        a.ausgabe.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
