"""Wackler: rote Tests einmal wiederholen, Grün nur mit zweitem vollem Lauf (L3 3b-4, #103).

Ein Test, der erst im zweiten Anlauf grün wird, ist ein Wackler — er wird mit Namen gemeldet, nie
still grün. Zweimal rot bleibt rot. Ablauf im Rot-Fall:

1. Einzel-Wiederholung nur der roten Tests (billig). Grün zählt nur, wenn genau so viele Tests
   bestanden (``passed``) wie vorher rot waren — skip/xfail/abgewählt heißt „nie grün gelaufen“.
2. Danach ein zweiter voller Lauf. Nur ist auch er grün — jeder vorher rote Test darin mit Namen
   als bestanden belegt (``Namentlich``; sonst ausgezählt), nicht nur Exit 0 —, gibt es Exit 0
   mit Urteil „wackler“.
   Ist er wieder rot (z. B. reihenfolge-abhängiger Test, allein grün), bleibt es Rot.

Die Kosten fallen nur im seltenen Rot-Fall an und stehen getrennt im Prüf-Protokoll.
Wie ein Werkzeug „nur die roten“ startet und ihre Namen ausgibt, weiß der Adapter; kann er das
nicht (``Wiederholbar`` fehlt), gibt es keine Wiederholung und Rot bleibt Rot. Ohne ``Zaehlbar``
ist nichts belegt, das erste Rot bleibt stehen (fail-closed).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from to_spawn.pruef import ausfuehren


@runtime_checkable
class Wiederholbar(Protocol):
    def nochmal_befehl(self, befehl: list[str], code: int) -> list[str] | None:
        """Befehl für „nur die roten Tests“; ``None``, wenn dieses Rot nicht wackeln kann."""
        ...

    def voll_nochmal_befehl(self, befehl: list[str]) -> list[str]:
        """Befehl für den zweiten vollen Lauf, unabhängig von den Läufen davor."""
        ...

    def rote(self, ausgabe: str) -> set[str]:
        """Namen der roten Tests aus der Ausgabe eines Laufs."""
        ...


@runtime_checkable
class Zaehlbar(Protocol):
    def gelaufen(self, ausgabe: str) -> int | None:
        """Zahl der gelaufenen Tests, wenn alle ausgewählten liefen; sonst ``None`` (unbelegt)."""
        ...

    def bestanden(self, ausgabe: str) -> int | None:
        """Zahl der bestandenen Tests (ohne skip/xfail); ``None``, wenn nicht ablesbar."""
        ...


@runtime_checkable
class Namentlich(Protocol):
    def mit_beleg(self, befehl: list[str], beleg: Path) -> list[str]:
        """``befehl`` so erweitert, dass der Lauf sein Ergebnis je Test nach ``beleg`` schreibt."""
        ...

    def alle_bestanden(self, beleg: Path, namen: set[str]) -> bool:
        """Steht jeder Test aus ``namen`` (Kennung wie aus ``rote``) im Beleg als bestanden?

        Fehlt der Beleg, ist er unlesbar oder fehlt ein Name darin: ``False`` (fail-closed).
        """
        ...


@dataclass(frozen=True)
class Wiederholung:
    gruen: bool
    wackler: tuple[str, ...]
    befehl: list[str]
    dauer_s: float
    #: Zweiter voller Lauf (nur, wenn die Einzel-Wiederholung grün war): eigene Zeit.
    voll_s: float = 0.0


def nochmal(adapter: object, repo: Path, befehl: list[str], erst: ausfuehren.Lauf) -> Wiederholung | None:
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
    # „nicht mehr rot“ nur „nicht gelaufen“. Bestanden == nicht mehr rot: skip/xfail ist kein
    # Grün. Ohne ``Zaehlbar`` ist beides nie belegt.
    if (
        not zaehlt
        or adapter.gelaufen(zweit.ausgabe) != len(erst_rot)
        or adapter.bestanden(zweit.ausgabe) != len(erst_rot - noch_rot)
    ):
        return Wiederholung(False, (), zweit_befehl, dauer)
    wackler = tuple(sorted(erst_rot - noch_rot))
    if zweit.code:
        return Wiederholung(False, wackler, zweit_befehl, dauer)
    # Einzeln grün reicht nicht (reihenfolge-abhängig): erst ein zweiter voller Lauf entscheidet.
    voll_befehl = adapter.voll_nochmal_befehl(befehl)
    with tempfile.TemporaryDirectory(prefix="to_spawn_beleg_") as ordner:
        beleg = Path(ordner) / "voll.xml"
        namentlich = isinstance(adapter, Namentlich)
        voll = ausfuehren.lauf(adapter.mit_beleg(voll_befehl, beleg) if namentlich else voll_befehl, repo)
        # Mit Namens-Beleg zählt nur er: jeder vorher rote Test ist im vollen Lauf bestanden
        # (#149 Fix 1). Zahlen ließen sich durch einen anderen Test ausgleichen und hingen an der
        # Schlusszeile des ersten Laufs.
        belegt = voll.code == 0 and (
            adapter.alle_bestanden(beleg, erst_rot)
            if namentlich
            else _voll_belegt(adapter, erst.ausgabe, len(erst_rot), voll.ausgabe)
        )
    voll_s = round(voll.dauer_s, 2)
    if not belegt:
        return Wiederholung(False, (), zweit_befehl, dauer, voll_s)
    return Wiederholung(True, wackler, zweit_befehl, dauer, voll_s)


def _voll_belegt(adapter: Zaehlbar, erst: str, rot: int, voll: str) -> bool:
    """Nur für Adapter ohne ``Namentlich``: ist der zweite volle Lauf ausgezählt grün (#149)?

    Gelaufen == wie im ersten Lauf, bestanden == im ersten Lauf bestanden plus die vorher roten:
    ein Skip, eine Abwahl oder ein Ausstieg mit Exit 0 ist kein Grün. Ohne ablesbare Zählung des
    ersten Laufs gilt nur ein Lauf, in dem jeder gelaufene Test bestand.
    """
    gelaufen, bestanden = adapter.gelaufen(voll), adapter.bestanden(voll)
    if gelaufen is None or bestanden is None:
        return False
    vorher = adapter.bestanden(erst)
    if vorher is None:
        return bestanden == gelaufen
    return gelaufen == adapter.gelaufen(erst) and bestanden == vorher + rot
