"""R1/R3-Quote je Ticket im App-Repo (Etappe 3b-0, #98).

Je Ticket (Nummer aus ticket-gruen/-rot im Fahrtenbuch seit --seit) wird die Summe der
geänderten Dateien aller Commits ermittelt, deren Betreff ``(#<Nr>)`` trägt
(``git log origin/master --grep``). Je Ticket gilt:
  R1  mindestens eine Nicht-Code-Datei (nicht ``*.py``) außerhalb von Doku
      (``docs/**``, ``*.md``, Bilder, ``*.txt``)
  R3  Prüf-Konfig/Abhängigkeiten (Lock, pytest.ini, conftest.py, requirements*,
      pyproject.toml, Dockerfile*, docker-compose*.yml)
  R2  (nur mit --rueckblick) Rückblick-Lauf des Tickets: Code geändert,
      impacted wählt 0 Tests (SKIP/FEHLER)

Ausgabe: Zusammenfassung nach stdout, Einzelzeilen optional als JSONL (--aus).
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

try:
    from messung.auswertung_3b0 import (
        DOKU_ENDUNGEN,
        R3_MUSTER,
        ist_doku,
        ist_r3,
        klassifiziere_dateien,
    )
    from messung.auswertung_3b0 import (
        normalisiere_pfad as _normal,
    )
except ImportError:  # Direktaufruf als Skript aus messung/
    from auswertung_3b0 import (  # type: ignore[no-redef]
        DOKU_ENDUNGEN,
        R3_MUSTER,
        ist_doku,
        ist_r3,
        klassifiziere_dateien,
    )
    from auswertung_3b0 import (
        normalisiere_pfad as _normal,
    )

LOG = logging.getLogger("r1_quote")
__all__ = ["DOKU_ENDUNGEN", "R3_MUSTER", "ist_doku", "ist_r3", "klassifiziere_ticket", "werte_quote_aus"]


def dateien_ticket(repo: Path, nr: int, ref: str = "origin/master") -> set[str]:
    """Alle geänderten Dateipfade der Commits mit ``(#<nr>)`` auf ``ref``."""
    r = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-C",
            str(repo),
            "log",
            ref,
            "--grep",
            f"(#{nr})",
            "-F",
            "--format=",
            "--name-only",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if r.returncode != 0:
        meldung = (r.stderr or r.stdout).strip()[:300]
        raise RuntimeError(f"git log fehlgeschlagen für Ticket #{nr} (rc={r.returncode}): {meldung}")
    return {_normal(z.strip()) for z in r.stdout.splitlines() if z.strip()}


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
        if isinstance(obj, dict):
            zeilen.append(obj)
    return zeilen


def lese_tickets(fahrten: Sequence[dict[str, Any]], seit: str) -> dict[int, str]:
    tickets: dict[int, str] = {}
    for z in fahrten:
        if z.get("ergebnis") not in ("ticket-gruen", "ticket-rot") or str(z.get("zeit", "")) < seit:
            continue
        sha = str(z.get("sha") or "")
        for nr in z.get("tickets") or []:
            try:
                tickets.setdefault(int(nr), sha)
            except (TypeError, ValueError):
                LOG.warning("ungültige Ticketnummer im Fahrtenbuch übersprungen: %r", nr)
    return tickets


def r2_tickets(rueckblick: Path, fahrten: Sequence[dict[str, Any]]) -> dict[int, bool]:
    """Ticketnummer → True, wenn ein Rückblick-Lauf des Tickets R2 auslöst."""
    sha_ticket: dict[str, list[int]] = {}
    for z in fahrten:
        sha = z.get("sha")
        if sha:
            nummern = []
            for n in z.get("tickets") or []:
                try:
                    nummern.append(int(n))
                except (TypeError, ValueError):
                    continue
            sha_ticket.setdefault(str(sha), []).extend(nummern)

    erg: dict[int, bool] = {}
    for nr_zeile, zeile in enumerate(rueckblick.read_text(encoding="utf-8").splitlines(), 1):
        if not zeile.strip():
            continue
        try:
            d = json.loads(zeile)
        except json.JSONDecodeError as fehler:
            LOG.warning("kaputte JSON-Zeile in %s:%d übersprungen: %s", rueckblick.name, nr_zeile, fehler)
            continue
        if not isinstance(d, dict) or not isinstance(d.get("b"), dict):
            continue
        sha = d.get("sha")
        if not sha:
            continue
        urteil = d["b"].get("urteil")
        hit = urteil in ("SKIP", "FEHLER") and int(d.get("geaendert_n") or 0) > 0
        for ticket_nr in sha_ticket.get(str(sha), []):
            erg[ticket_nr] = erg.get(ticket_nr, False) or hit
    return erg


def klassifiziere_ticket(
    nr: int,
    dateien: set[str],
    r2_wert: bool | None = None,
) -> dict[str, Any]:
    """Klassifiziert die geänderten Dateien eines Tickets nach R1 und R3."""
    r1, r3 = klassifiziere_dateien(dateien)
    return {
        "ticket": nr,
        "dateien": len(dateien),
        "r1": bool(r1),
        "r3": bool(r3),
        "r2": r2_wert,
        "r1_beispiel": r1[:3],
    }


def werte_quote_aus(zeilen: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregiert R1/R3/R2-Quoten über Ticket-Zeilen (sicher auch bei ``n == 0``)."""
    n = len(zeilen)
    r1n = sum(1 for z in zeilen if z.get("r1"))
    r3n = sum(1 for z in zeilen if z.get("r3"))
    r13 = sum(1 for z in zeilen if z.get("r1") or z.get("r3"))
    sub = [z for z in zeilen if z.get("r2") is not None]
    sub_voll = sum(1 for z in sub if z.get("r1") or z.get("r3") or z.get("r2"))
    return {
        "n": n,
        "r1": r1n,
        "r1_anteil": (r1n / n) if n else 0.0,
        "r3": r3n,
        "r3_anteil": (r3n / n) if n else 0.0,
        "voll": r13,
        "voll_anteil": (r13 / n) if n else 0.0,
        "r2_teilmenge_n": len(sub),
        "r2_teilmenge_voll": sub_voll,
        "r2_teilmenge_anteil": (sub_voll / len(sub)) if sub else 0.0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    p = argparse.ArgumentParser(description="R1/R3/R2-Quote je Ticket aus Fahrtenbuch und Git-Verlauf.")
    p.add_argument("--schlange", type=Path, required=True, help="Ordner mit fahrtenbuch.jsonl")
    p.add_argument("--repo", type=Path, required=True, help="Klon des App-Repos")
    p.add_argument("--seit", default="2026-11-07", help="Frühester Zeitstempel (ISO-Präfix)")
    p.add_argument("--ref", default="origin/master", help="Git-Referenz für Commit-Suche")
    p.add_argument("--rueckblick", type=Path, help="Optional: JSONL aus Rückblick für R2")
    p.add_argument("--aus", type=Path, help="Optional: Einzelzeilen als JSONL schreiben")
    a = p.parse_args(argv)

    fahrten = lese_fahrtenbuch(a.schlange)
    tickets = lese_tickets(fahrten, a.seit)
    r2 = r2_tickets(a.rueckblick, fahrten) if a.rueckblick else {}
    zeilen: list[dict[str, Any]] = []
    for nr in sorted(tickets):
        dateien = dateien_ticket(a.repo, nr, ref=a.ref)
        if not dateien:
            continue
        zeilen.append(klassifiziere_ticket(nr, dateien, r2_wert=r2.get(nr)))

    if a.aus:
        a.aus.write_text(
            "".join(json.dumps(z, ensure_ascii=False) + "\n" for z in zeilen),
            encoding="utf-8",
        )

    bericht = werte_quote_aus(zeilen)
    n = bericht["n"]
    ausgabe = [
        f"Tickets seit {a.seit} mit Commits auf {a.ref}: {n} (von {len(tickets)} im Fahrtenbuch)",
    ]
    if n:
        ausgabe.append(
            f"R1: {bericht['r1']} ({bericht['r1_anteil']:.1%}) · "
            f"R3: {bericht['r3']} ({bericht['r3_anteil']:.1%}) · "
            f"R1 oder R3 (voll): {bericht['voll']} ({bericht['voll_anteil']:.1%})"
        )
    else:
        ausgabe.append("Keine Tickets mit passenden Commits gefunden.")
    if bericht["r2_teilmenge_n"]:
        ausgabe.append(
            f"Teilmenge mit Rückblick-Lauf: {bericht['r2_teilmenge_n']} Tickets, "
            f"R1/R2/R3 voll: {bericht['r2_teilmenge_voll']} ({bericht['r2_teilmenge_anteil']:.1%})"
        )
    sys.stdout.write("\n".join(ausgabe) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
