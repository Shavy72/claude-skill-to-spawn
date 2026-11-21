"""Leiter des Prüf-Werkzeugkastens: welche Stufen laufen und in welcher Reihenfolge (Paket-intern, #99).

Sprachfrei — kennt nur die Adapter-Schnittstelle. In 3b-1a gibt es nur Stufe 4 (voll) je
erkanntem Adapter; Sofort-Prüfung, Auswahl und die Regeln R1–R3 kommen in 3b-1b/3b-2a.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from to_spawn.pruef import ausfuehren, wackler

GRUEN, ROT, GATE_FEHLER = 0, 1, 2
#: Höchstens so viele Zeilen gehen als ``fehler_kurz`` ins Protokoll.
FEHLER_ZEILEN = 20


@dataclass(frozen=True)
class Plan:
    """Was ein Adapter für eine Stufe vorhat: ``fehler`` ≠ "" heißt, sie kann nicht laufen."""

    werkzeug: str
    befehl: list[str]
    fehler: str


class Adapter(Protocol):
    name: str

    def erkennt(self, repo: Path) -> bool: ...

    def voll(self, repo: Path) -> Plan: ...

    def deute(self, code: int) -> int: ...


@dataclass(frozen=True)
class Stufe:
    name: str
    adapter: str
    lief: bool
    grund: str
    dauer_s: float
    befehl: str
    werkzeug: str
    ergebnis: int
    ausgabe: str
    #: Einmalige Wiederholung der roten Tests (3b-4): Befehl, eigene Zeit, erst rot → dann grün.
    wiederholung: str = ""
    wiederholung_s: float = 0.0
    wackler: tuple[str, ...] = ()


def _kurz(text: str) -> str:
    zeilen = [zeile for zeile in text.splitlines() if zeile.strip()]
    return "\n".join(zeilen[-FEHLER_ZEILEN:])


def _voll(repo: Path, adapter: Adapter) -> Stufe:
    plan = adapter.voll(repo)
    if plan.fehler:
        return Stufe(
            "voll",
            adapter.name,
            False,
            plan.fehler,
            0.0,
            "",
            plan.werkzeug,
            GATE_FEHLER,
            plan.fehler,
        )
    erg = ausfuehren.lauf(plan.befehl, repo)
    ergebnis = adapter.deute(erg.code)
    stufe = Stufe(
        "voll",
        adapter.name,
        True,
        "3b-1a: nur Stufe voll",
        round(erg.dauer_s, 2),
        " ".join(plan.befehl),
        plan.werkzeug,
        ergebnis,
        erg.ausgabe if ergebnis != GRUEN else "",
    )
    zweit = (
        wackler.nochmal(adapter, repo, plan.befehl, erg) if ergebnis == ROT else None
    )
    if zweit is None:
        return stufe
    return replace(
        stufe,
        ergebnis=GRUEN if zweit.gruen else ROT,
        ausgabe="" if zweit.gruen else stufe.ausgabe,
        wiederholung=" ".join(zweit.befehl),
        wiederholung_s=zweit.dauer_s,
        wackler=zweit.wackler,
    )


def stufen(repo: Path, adapter_liste: tuple[Adapter, ...]) -> list[Stufe]:
    """Alle Stufen für ``repo``; leer, wenn kein Adapter das Repo erkennt."""
    return [_voll(repo, adapter) for adapter in adapter_liste if adapter.erkennt(repo)]


def ergebnis(liste: list[Stufe]) -> tuple[int, str]:
    """Gesamt-Exit und ``fehler_kurz``: Rot schlägt Gate-Fehler (Rot ist sicher ein Code-Fehler)."""
    for wert in (ROT, GATE_FEHLER):
        treffer = [stufe for stufe in liste if stufe.ergebnis == wert]
        if treffer:
            return wert, _kurz("\n".join(stufe.ausgabe for stufe in treffer))
    return GRUEN, ""
