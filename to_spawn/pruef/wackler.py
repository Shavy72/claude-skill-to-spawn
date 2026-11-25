"""Wackler: rote Tests einmal wiederholen, Grün nur mit zweitem vollem Lauf (L3 3b-4, #103).

Ein Test, der erst im zweiten Anlauf grün wird, ist ein Wackler — er wird mit Namen gemeldet, nie
still grün. Zweimal rot bleibt rot. Ablauf im Rot-Fall:

1. Einzel-Wiederholung nur der roten Tests (billig). Grün zählt nur, wenn genau so viele Tests
   bestanden (``passed``) wie vorher rot waren — skip/xfail/abgewählt heißt „nie grün gelaufen“.
2. Danach ein zweiter voller Lauf. Nur ist auch er grün, gibt es Exit 0 mit Urteil „wackler“.
   Ist er wieder rot (z. B. reihenfolge-abhängiger Test, allein grün), bleibt es Rot.

Die Kosten fallen nur im seltenen Rot-Fall an und stehen getrennt im Prüf-Protokoll.
Wie ein Werkzeug „nur die roten“ startet und ihre Namen ausgibt, weiß der Adapter; kann er das
nicht (``Wiederholbar`` fehlt), gibt es keine Wiederholung und Rot bleibt Rot. Ohne ``Zaehlbar``
ist nichts belegt, das erste Rot bleibt stehen (fail-closed).
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


@dataclass(frozen=True)
class Bericht:
    """Maschinenlesbares Ergebnis eines Testlaufs (z. B. aus JUnit-XML, #149)."""

    #: Zahl der gelaufenen Tests, wenn alle ausgewählten liefen; ``None`` = unbelegt/abgebrochen.
    gelaufen: int | None
    #: Zahl der bestandenen Tests ohne skip/xfail/error/failure; ``None`` = unbelegt.
    bestanden: int | None
    #: Zahl der im Bericht eingetragenen Testfälle (0 = nichts gelaufen, z. B. ``--co``).
    im_bericht: int = 0
    #: Vorzeitiger Abbruch (z. B. ``pytest.exit(..., returncode=0)``).
    abgebrochen: bool = False


@runtime_checkable
class Berichtbar(Protocol):
    def bericht(self, befehl: list[str], ausgabe: str) -> Bericht | None:
        """Maschinenlesbares Ergebnis zum gelaufenen ``befehl``; ``None`` = Bericht fehlt."""
        ...


@dataclass(frozen=True)
class Wiederholung:
    gruen: bool
    wackler: tuple[str, ...]
    befehl: list[str]
    dauer_s: float
    #: Zweiter voller Lauf (nur, wenn die Einzel-Wiederholung grün war): eigene Zeit.
    voll_s: float = 0.0


def _zaehle(adapter: object, befehl: list[str], ausgabe: str) -> tuple[int | None, int | None]:
    """(gelaufen, bestanden) bevorzugt aus dem maschinenlesbaren Bericht, sonst aus ``Zaehlbar``."""
    if isinstance(adapter, Berichtbar):
        b = adapter.bericht(befehl, ausgabe)
        if b is None or b.abgebrochen:
            return None, None
        return b.gelaufen, b.bestanden
    if isinstance(adapter, Zaehlbar):
        return adapter.gelaufen(ausgabe), adapter.bestanden(ausgabe)
    return None, None


def nochmal(adapter: object, repo: Path, befehl: list[str], erst: ausfuehren.Lauf) -> Wiederholung | None:
    """Wiederholt die roten Tests aus ``erst`` einmal; ``None`` = nicht wiederholt, Rot bleibt."""
    if not isinstance(adapter, Wiederholbar):
        return None
    zweit_befehl = adapter.nochmal_befehl(befehl, erst.code)
    erst_rot = adapter.rote(erst.ausgabe)
    if zweit_befehl is None or not erst_rot:
        return None
    zaehlt = isinstance(adapter, (Berichtbar, Zaehlbar))
    erst_gelaufen, erst_bestanden = _zaehle(adapter, befehl, erst.ausgabe) if zaehlt else (None, None)
    if zaehlt and (erst_gelaufen is None or erst_bestanden is None):
        # Erster Lauf nicht belegt ganz (abgebrochen, Bericht/Zählung fehlt): ungelaufene Tests
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
    # Grün. Ohne ``Berichtbar``/``Zaehlbar`` ist beides nie belegt.
    zweit_gelaufen, zweit_bestanden = _zaehle(adapter, zweit_befehl, zweit.ausgabe)
    if not zaehlt or zweit_gelaufen != len(erst_rot) or zweit_bestanden != len(erst_rot - noch_rot):
        return Wiederholung(False, (), zweit_befehl, dauer)
    wackler = tuple(sorted(erst_rot - noch_rot))
    if zweit.code:
        return Wiederholung(False, wackler, zweit_befehl, dauer)
    # Einzeln grün reicht nicht (reihenfolge-abhängig): erst ein zweiter voller Lauf entscheidet.
    # Auch er wird ausgezählt: Skip, Abwahl oder Ausstieg mit Exit 0 ergeben nie „wackler“ (#149).
    voll_befehl = adapter.voll_nochmal_befehl(befehl)
    voll = ausfuehren.lauf(voll_befehl, repo)
    voll_s = round(voll.dauer_s, 2)
    if voll.code != 0:
        return Wiederholung(False, (), zweit_befehl, dauer, voll_s)
    voll_gelaufen, voll_bestanden = _zaehle(adapter, voll_befehl, voll.ausgabe)
    if (
        erst_gelaufen is None
        or erst_bestanden is None
        or voll_gelaufen != erst_gelaufen
        or voll_bestanden is None
        or voll_bestanden < erst_bestanden + len(erst_rot)
    ):
        return Wiederholung(False, (), zweit_befehl, dauer, voll_s)
    return Wiederholung(True, wackler, zweit_befehl, dauer, voll_s)
