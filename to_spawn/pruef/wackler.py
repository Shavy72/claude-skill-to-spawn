"""Wackler: rote Tests genau einmal wiederholen (Paket-intern, L3 3b-4, #103).

Ein Test, der erst im zweiten Anlauf grün wird, ist ein Wackler — er wird mit Namen gemeldet, nie
still grün. Zweimal rot bleibt rot. Wiederholt werden nur die roten Tests, nicht der ganze Lauf.
Wie ein Werkzeug „nur die roten“ startet und ihre Namen ausgibt, weiß der Adapter; kann er das
nicht (``Wiederholbar`` fehlt), gibt es keine Wiederholung und Rot bleibt Rot.

Grün nach Wiederholung nur, wenn beide Läufe belegt vollständig waren (``Zaehlbar``: alle
ausgewählten Tests gelaufen). Was nicht belegbar ist, lässt das erste Rot stehen (fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from to_spawn.pruef import ausfuehren


@runtime_checkable
class Wiederholbar(Protocol):
    def nochmal_befehl(self, befehl: list[str], code: int) -> list[str] | None:
        """Befehl für „nur die roten Tests“; ``None``, wenn dieses Rot nicht wackeln kann."""
        ...

    def rote(self, ausgabe: str) -> set[str]:
        """Namen der roten Tests aus der Ausgabe eines Laufs."""
        ...


@runtime_checkable
class Zaehlbar(Protocol):
    def gelaufen(self, ausgabe: str) -> int | None:
        """Zahl der gelaufenen Tests, wenn alle ausgewählten liefen; sonst ``None`` (unbelegt)."""
        ...


@dataclass(frozen=True)
class Wiederholung:
    gruen: bool
    wackler: tuple[str, ...]
    befehl: list[str]
    dauer_s: float


def nochmal(
    adapter: object, repo: Path, befehl: list[str], erst: ausfuehren.Lauf
) -> Wiederholung | None:
    """Wiederholt die roten Tests aus ``erst`` einmal; ``None`` = nicht wiederholt, Rot bleibt."""
    if not isinstance(adapter, Wiederholbar):
        return None
    zweit_befehl = adapter.nochmal_befehl(befehl, erst.code)
    erst_rot = adapter.rote(erst.ausgabe)
    if zweit_befehl is None or not erst_rot:
        return None
    zaehlt = isinstance(adapter, Zaehlbar)
    if zaehlt and adapter.gelaufen(erst.ausgabe) is None:
        # Erster Lauf nicht belegt ganz (abgebrochen, Ausgabe ohne Zählung): ungelaufene Tests
        # könnten rot sein, also kann keine Wiederholung daraus Grün machen.
        return None
    zweit = ausfuehren.lauf(zweit_befehl, repo)
    dauer = round(zweit.dauer_s, 2)
    # Nur 0 (grün) und 1 (rot) sagen etwas über die Tests; alles andere (nichts gelaufen,
    # Werkzeug kaputt) macht aus dem ersten Rot weder Grün noch Gate-Fehler.
    if zweit.code not in (0, 1):
        return Wiederholung(False, (), zweit_befehl, dauer)
    noch_rot = adapter.rote(zweit.ausgabe) if zweit.code else set()
    if zweit.code and not noch_rot:
        return Wiederholung(False, (), zweit_befehl, dauer)
    # Gelaufen == gesammelt: genau die roten Tests müssen nochmal gelaufen sein, sonst ist
    # „nicht mehr rot“ nur „nicht gelaufen“.
    # Ohne ``Zaehlbar`` ist das nie belegt.
    if not zaehlt or adapter.gelaufen(zweit.ausgabe) != len(erst_rot):
        return Wiederholung(False, (), zweit_befehl, dauer)
    return Wiederholung(
        zweit.code == 0, tuple(sorted(erst_rot - noch_rot)), zweit_befehl, dauer
    )
