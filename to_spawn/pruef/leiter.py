"""Leiter des Prüf-Werkzeugkastens: welche Stufen laufen und in welcher Reihenfolge (Paket-intern, #99).

Sprachfrei — kennt nur die Adapter-Schnittstelle. Zuerst Stufe 1 sofort + Stufe 2 doku (``sofort``,
#100), dann je erkanntem Adapter Stufe 3 (nur betroffene Tests, #101) oder Stufe 4 (voll). Wann
die Auswahl nicht reicht, entscheidet nur ``regeln`` (R1–R3, LF); der Grund steht in der Stufe.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from to_spawn.pruef import ausfuehren, protokoll, regeln, sofort, wackler

GRUEN, ROT, GATE_FEHLER = 0, 1, 2
#: Stufe lief nicht (``lief=False`` + Grund) — zählt nie als Grün, ändert das Urteil nicht.
AUSGELASSEN = -1
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

    def auswahl(self, repo: Path, basis: str) -> Plan:
        """Befehl „nur betroffene Tests seit ``basis``“; ``fehler`` ≠ "" = Auswahl nicht möglich."""
        ...

    def zaehle(self, code: int, ausgabe: str) -> tuple[int | None, int | None]:
        """(gelaufen, gesamt) eines Auswahl-Laufs; ``None`` = unbelegt."""
        ...


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
    #: Zweiter voller Lauf nach grüner Einzel-Wiederholung: eigene Zeit (0 = nicht gelaufen).
    voll_nochmal_s: float = 0.0
    wackler: tuple[str, ...] = ()
    #: Entscheidung Auswahl/voll fürs Protokoll: {adapter, n, gesamt, sicher, grund}.
    auswahl: dict[str, Any] | None = None


def _kurz(text: str) -> str:
    zeilen = [zeile for zeile in text.splitlines() if zeile.strip()]
    return "\n".join(zeilen[-FEHLER_ZEILEN:])


def _wahl(adapter: Adapter, grund: str, n: int | None = None, gesamt: int | None = None) -> dict[str, Any]:
    return {"adapter": adapter.name, "n": n, "gesamt": gesamt, "sicher": not grund, "grund": grund}


def _voll(repo: Path, adapter: Adapter, grund: str) -> Stufe:
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
            auswahl=_wahl(adapter, grund),
        )
    erg = ausfuehren.lauf(plan.befehl, repo)
    return _gedeutet(
        repo,
        adapter,
        plan,
        erg,
        Stufe("voll", adapter.name, True, grund, 0.0, "", "", GRUEN, "", auswahl=_wahl(adapter, grund)),
    )


def _pruefe(repo: Path, adapter: Adapter, basis: str, pfade: list[str] | None) -> Stufe:
    """Stufe 3 (Auswahl), wenn ``regeln`` sie erlaubt und sie ≥ 1 Test wählt, sonst Stufe 4 (voll)."""
    doku = sofort.erklaerte_doku(repo, basis, pfade or [])
    grund = regeln.vorab(
        pfade,
        adapter.name,
        regeln.risiko_pfade(repo),
        protokoll.letztes_urteil(repo),
        doku=doku,
        offen=sofort.ungespeichert(repo),
    )
    if grund:
        return _voll(repo, adapter, grund)
    plan = adapter.auswahl(repo, basis)
    if plan.fehler:
        return _voll(repo, adapter, f"Auswahl nicht möglich: {plan.fehler}")
    erg = ausfuehren.lauf(plan.befehl, repo)
    n, gesamt = adapter.zaehle(erg.code, erg.ausgabe)
    grund = regeln.nach_auswahl(n, pfade or [], adapter.name, doku)
    if grund:
        return _voll(repo, adapter, grund)
    wahl = _wahl(adapter, "", n, gesamt)
    if n == 0:
        # Kein Test betroffen (nur erklärte Doku, regeln.nach_auswahl): pytest meldet Exit 5, das ist
        # hier Grün, kein Gate-Fehler.
        return Stufe(
            "auswahl",
            adapter.name,
            True,
            "0 betroffene Tests",
            round(erg.dauer_s, 2),
            " ".join(plan.befehl),
            plan.werkzeug,
            GRUEN,
            "",
            auswahl=wahl,
        )
    return _gedeutet(
        repo,
        adapter,
        plan,
        erg,
        Stufe("auswahl", adapter.name, True, f"{n} betroffene Tests", 0.0, "", "", GRUEN, "", auswahl=wahl),
    )


def _gedeutet(repo: Path, adapter: Adapter, plan: Plan, erg: ausfuehren.Lauf, roh: Stufe) -> Stufe:
    """Ergebnis eines Laufs in ``roh`` eintragen; rot → einmalige Wiederholung (3b-4)."""
    ergebnis = adapter.deute(erg.code)
    stufe = replace(
        roh,
        dauer_s=round(erg.dauer_s, 2),
        befehl=" ".join(plan.befehl),
        werkzeug=plan.werkzeug,
        ergebnis=ergebnis,
        ausgabe=erg.ausgabe if ergebnis != GRUEN else "",
    )
    zweit = wackler.nochmal(adapter, repo, plan.befehl, erg) if ergebnis == ROT else None
    if zweit is None:
        return stufe
    return replace(
        stufe,
        ergebnis=GRUEN if zweit.gruen else ROT,
        ausgabe="" if zweit.gruen else stufe.ausgabe,
        wiederholung=" ".join(zweit.befehl),
        wiederholung_s=zweit.dauer_s,
        voll_nochmal_s=zweit.voll_s,
        wackler=zweit.wackler,
    )


def stufen(repo: Path, adapter_liste: tuple[Adapter, ...], basis: str) -> list[Stufe]:
    """Alle Stufen für ``repo`` gegen ``basis``; leer, wenn kein Adapter das Repo erkennt."""
    erkannt = [adapter for adapter in adapter_liste if adapter.erkennt(repo)]
    if not erkannt:
        return []
    vorn, halt = sofort.stufen(repo, basis)
    if halt:
        return vorn + [Stufe("voll", a.name, False, halt, 0.0, "", "", AUSGELASSEN, "") for a in erkannt]
    pfade = sofort.geaendert(repo, basis)
    return vorn + [_pruefe(repo, adapter, basis, pfade) for adapter in erkannt]


def ergebnis(liste: list[Stufe]) -> tuple[int, str]:
    """Gesamt-Exit und ``fehler_kurz``: Rot schlägt Gate-Fehler (Rot ist sicher ein Code-Fehler)."""
    for wert in (ROT, GATE_FEHLER):
        treffer = [stufe for stufe in liste if stufe.ergebnis == wert]
        if treffer:
            return wert, _kurz("\n".join(stufe.ausgabe for stufe in treffer))
    return GRUEN, ""
