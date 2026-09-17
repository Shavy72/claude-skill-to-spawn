"""Manifest lesen und die Regularien prüfen, bevor Sessions gespawnt werden.

Pflichtfelder je Ticket: ``schaetzung_k`` (Zahl, Tausend Token) und ``umfang``
(Klartext). Fehlt eines oder liegt die Schätzung über der Smart-Zone-Grenze,
weigert sich der Skill (Exit 3) — dann muss ``/to-tickets`` neu schneiden.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gh

log = logging.getLogger("to_spawn.manifest")

#: Exit-Code der Weigerung (Regularien nicht erfüllt).
EXIT_WEIGERUNG = 3


def manifest_pfad(repo: Path, spec: int | str) -> Path:
    return repo / "docs" / "agents" / "manifests" / f"spec-{spec}.json"


def lade_manifest(repo: Path, spec: int | str) -> dict[str, Any]:
    """Manifest der Spec lesen; ``FileNotFoundError``/``ValueError`` bei Problemen."""
    datei = manifest_pfad(repo, spec)
    if not datei.is_file():
        raise FileNotFoundError(str(datei))
    daten = json.loads(datei.read_text(encoding="utf-8"))
    if not isinstance(daten, dict) or not isinstance(daten.get("tickets"), dict):
        raise ValueError(f"Manifest ohne Ticket-Tabelle: {datei}")
    return daten


@dataclass
class Bericht:
    """Ergebnis der Regularien-Prüfung."""

    spec: str
    tickets: list[str] = field(default_factory=list)
    fehler: list[str] = field(default_factory=list)
    warnungen: list[str] = field(default_factory=list)

    @property
    def sauber(self) -> bool:
        return not self.fehler

    def text(self) -> str:
        zeilen = [f"Regularien Spec #{self.spec} · {len(self.tickets)} Tickets"]
        for eintrag in self.fehler:
            zeilen.append(f"  FEHLER  {eintrag}")
        for eintrag in self.warnungen:
            zeilen.append(f"  Warnung {eintrag}")
        if self.sauber and not self.warnungen:
            zeilen.append("  alles erfüllt")
        elif self.fehler:
            zeilen.append("  → erst /to-tickets: Tickets neu schneiden bzw. Felder ergänzen.")
        return "\n".join(zeilen)


def pruefe(
    repo: Path,
    spec: int | str,
    konfig: dict[str, Any],
    *,
    mit_github: bool = True,
) -> Bericht:
    """Manifest gegen die Regularien prüfen (Pflichtfelder, Grenze, Blocker-Kanten)."""
    bericht = Bericht(spec=str(spec))
    try:
        daten = lade_manifest(repo, spec)
    except FileNotFoundError as fehler:
        bericht.fehler.append(f"Manifest fehlt: {fehler}")
        return bericht
    except ValueError as fehler:
        bericht.fehler.append(str(fehler))
        return bericht

    grenze = float(konfig.get("staffel", {}).get("grenze_k", 200))
    tickets: dict[str, Any] = daten["tickets"]
    bericht.tickets = sorted(tickets, key=lambda n: int(n) if str(n).isdigit() else 0)

    if not bericht.tickets:
        bericht.fehler.append("Manifest führt kein einziges Ticket.")
        return bericht

    for nummer in bericht.tickets:
        eintrag = tickets[nummer] if isinstance(tickets[nummer], dict) else {}
        schaetzung = eintrag.get("schaetzung_k")
        umfang = eintrag.get("umfang")
        if not isinstance(schaetzung, (int, float)) or isinstance(schaetzung, bool):
            bericht.fehler.append(f"#{nummer}: Feld schaetzung_k fehlt oder ist keine Zahl.")
        elif schaetzung > grenze:
            bericht.fehler.append(
                f"#{nummer}: Schätzung {schaetzung:g}k über der Grenze {grenze:g}k — Ticket ist zu groß geschnitten."
            )
        if not isinstance(umfang, str) or not umfang.strip():
            bericht.fehler.append(f"#{nummer}: Feld umfang (Klartext) fehlt.")

    if mit_github:
        _pruefe_blocker(repo, bericht)
    return bericht


def _pruefe_blocker(repo: Path, bericht: Bericht) -> None:
    """Warnen, wenn kein Ticket der Spec eine native ``blocked_by``-Kante hat."""
    if gh.gh_befehl() is None:
        bericht.warnungen.append("gh fehlt — Blocker-Kanten nicht geprüft.")
        return
    slug = gh.repo_aus_origin(repo)
    if not slug:
        bericht.warnungen.append("origin nicht lesbar — Blocker-Kanten nicht geprüft.")
        return
    mit_kante = 0
    unklar = 0
    for nummer in bericht.tickets:
        kanten = gh.blocked_by(slug, nummer, cwd=repo)
        if kanten is None:
            unklar += 1
            continue
        if kanten:
            mit_kante += 1
    if unklar == len(bericht.tickets):
        bericht.warnungen.append("Blocker-Abfrage fehlgeschlagen — Kanten ungeprüft.")
    elif mit_kante == 0:
        bericht.warnungen.append(
            "Kein Ticket hat eine native blocked_by-Kante — alle Sessions starten "
            "gleichzeitig. Reihenfolge gewollt? Sonst /to-tickets nachziehen."
        )
