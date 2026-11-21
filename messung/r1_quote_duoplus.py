"""R1/R3-Quote je Ticket in DuoPlus (Etappe 3b-0).

Je Ticket (Nummer aus ticket-gruen/-rot im Fahrtenbuch seit --seit) die Summe der geänderten Dateien aller
Commits, deren Betreff "(#<Nr>)" trägt (``git log origin/master --grep``). Je Ticket gilt:
  R1  mindestens eine Nicht-Code-Datei (nicht ``*.py``) außerhalb von Doku (``docs/**``, ``*.md``, Bilder)
  R3  Prüf-Konfig/Abhängigkeiten (Lock, pytest.ini, conftest.py, requirements*, pyproject.toml, ...)
  R2  (nur mit --rueckblick) Rückblick-Lauf des Tickets: Code geändert, impacted wählt 0 Tests (SKIP/FEHLER)
Ausgabe: Markdown-Zusammenfassung nach stdout, Einzelzeilen optional als JSONL (--aus).

Aufruf:
  python3 r1_quote_duoplus.py --schlange <ordner> --repo <klon> [--seit 2026-11-07] [--rueckblick r.jsonl] [--aus q.jsonl]
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import subprocess
from pathlib import Path

LOG = logging.getLogger("r1_quote")
DOKU_ENDUNGEN = (".md", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".txt")
R3_MUSTER = (
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


def ist_doku(pfad: str) -> bool:
    return pfad.startswith("docs/") or pfad.lower().endswith(DOKU_ENDUNGEN)


def ist_r3(pfad: str) -> bool:
    name = pfad.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, m) for m in R3_MUSTER)


def dateien_ticket(repo: Path, nr: int) -> set[str]:
    r = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-C",
            str(repo),
            "log",
            "origin/master",
            "--grep",
            f"(#{nr})",
            "-F",
            "--format=",
            "--name-only",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return {z for z in r.stdout.splitlines() if z.strip()}


def lese_fahrtenbuch(schlange: Path) -> list[dict]:
    zeilen = []
    for zeile in (
        (schlange / "fahrtenbuch.jsonl").read_text(encoding="utf-8").splitlines()
    ):
        try:
            zeilen.append(json.loads(zeile))
        except json.JSONDecodeError:
            continue
    return zeilen


def lese_tickets(fahrten: list[dict], seit: str) -> dict[int, str]:
    tickets: dict[int, str] = {}
    for z in fahrten:
        if (
            z.get("ergebnis") not in ("ticket-gruen", "ticket-rot")
            or z.get("zeit", "") < seit
        ):
            continue
        for nr in z.get("tickets") or []:
            tickets.setdefault(int(nr), z["sha"])
    return tickets


def r2_tickets(rueckblick: Path, fahrten: list[dict]) -> dict[int, bool]:
    """Ticketnummer -> True, wenn ein Rückblick-Lauf des Tickets R2 auslöst (Code geändert, impacted 0 Tests)."""
    sha_ticket: dict[str, list[int]] = {}
    for z in fahrten:
        if z.get("sha"):
            sha_ticket.setdefault(z["sha"], []).extend(
                int(n) for n in z.get("tickets") or []
            )
    erg: dict[int, bool] = {}
    for zeile in rueckblick.read_text(encoding="utf-8").splitlines():
        d = json.loads(zeile)
        if "b" not in d:
            continue
        hit = d["b"]["urteil"] in ("SKIP", "FEHLER") and (d.get("geaendert_n") or 0) > 0
        for nr in sha_ticket.get(d["sha"], []):
            erg[nr] = erg.get(nr, False) or hit
    return erg


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--schlange", type=Path, required=True)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--seit", default="2026-11-07")
    p.add_argument("--rueckblick", type=Path)
    p.add_argument("--aus", type=Path)
    a = p.parse_args()
    fahrten = lese_fahrtenbuch(a.schlange)
    tickets = lese_tickets(fahrten, a.seit)
    r2 = r2_tickets(a.rueckblick, fahrten) if a.rueckblick else {}
    zeilen = []
    for nr in sorted(tickets):
        dateien = dateien_ticket(a.repo, nr)
        if not dateien:
            continue
        r1 = sorted(d for d in dateien if not d.endswith(".py") and not ist_doku(d))
        r3 = sorted(d for d in dateien if ist_r3(d))
        zeilen.append(
            {
                "ticket": nr,
                "dateien": len(dateien),
                "r1": bool(r1),
                "r3": bool(r3),
                "r2": r2.get(nr),
                "r1_beispiel": r1[:3],
            }
        )
    n = len(zeilen)
    if a.aus:
        a.aus.write_text(
            "".join(json.dumps(z, ensure_ascii=False) + "\n" for z in zeilen),
            encoding="utf-8",
        )
    r1n = sum(z["r1"] for z in zeilen)
    r3n = sum(z["r3"] for z in zeilen)
    r13 = sum(1 for z in zeilen if z["r1"] or z["r3"])
    print(
        f"Tickets seit {a.seit} mit Commits auf master: {n} (von {len(tickets)} im Fahrtenbuch)"
    )
    print(
        f"R1: {r1n} ({r1n / n:.1%}) · R3: {r3n} ({r3n / n:.1%}) · R1 oder R3 (voll): {r13} ({r13 / n:.1%})"
    )
    sub = [z for z in zeilen if z["r2"] is not None]
    if sub:
        voll = sum(1 for z in sub if z["r1"] or z["r3"] or z["r2"])
        print(
            f"Teilmenge mit Rückblick-Lauf: {len(sub)} Tickets, R1/R2/R3 voll: {voll} ({voll / len(sub):.1%})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
