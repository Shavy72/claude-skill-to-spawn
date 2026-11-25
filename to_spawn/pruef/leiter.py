"""Leiter des Prüf-Werkzeugkastens: welche Stufen laufen und in welcher Reihenfolge (Paket-intern, #99).

Sprachfrei — kennt nur die Adapter-Schnittstelle. Zuerst Stufe 1 sofort + Stufe 2 doku (``sofort``,
#100), dann je erkanntem Adapter Stufe 3 (nur betroffene Tests, #101) oder Stufe 4 (voll). Wann
die Auswahl nicht reicht, entscheidet nur ``regeln`` (R1–R3, LF); der Grund steht in der Stufe.

Art der Prüfung (``to_spawn.pruefplan``, #425): ``schnell`` endet vor Stufe 4 — wo heute voll liefe,
steht eine ausgelassene Stufe mit Grund im Protokoll. ``komplett`` läuft gleich Stufe 4.
``None`` = Leiter wie bisher (Auswahl, sonst voll). Ein eingerichteter Befehl (``pruefung.<art>``)
läuft über :func:`eigener_befehl`.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from to_spawn import bash_wahl, temp_ordner
from to_spawn.pruef import ausfuehren, protokoll, regeln, sofort, wackler

GRUEN, ROT, GATE_FEHLER = 0, 1, 2
#: Anfang von ``Stufe.grund``, wenn :func:`eigener_befehl` an der Zeitgrenze abbrach (#455).
ZEITGRENZE = "Zeitgrenze"
#: Stufe lief nicht (``lief=False`` + Grund) — zählt nie als Grün, ändert das Urteil nicht.
AUSGELASSEN = -1
#: Höchstens so viele Zeilen gehen als ``fehler_kurz`` ins Protokoll.
FEHLER_ZEILEN = 20
#: Orte des Bau-Werkzeugs im App-Repo: deren Tests sammelt die App-Prüfung nie ein,
#: auch wenn die App ihre eigene Ausschluss-Liste setzt (#425 Abnahme 4, #415, #446).
WERKZEUG_ORTE: tuple[str, ...] = (".to-spawn", ".claude")


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


def _ohne_voll(adapter: Adapter, grund: str, pfade: list[str] | None) -> Stufe:
    """Schnelle Prüfung: statt Stufe 4 eine Stufe, die sagt, warum nichts lief.

    Mit geänderter Code-Datei oder unbekannter Änderung (``pfade is None``) ein Gate-Fehler
    (``regeln.schnell_ohne_test`` entscheidet, nie still Grün), sonst ausgelassen.
    """
    fehler = regeln.schnell_ohne_test(pfade, adapter.name, grund)
    if fehler:
        wahl = _wahl(adapter, grund)
        return Stufe("voll", adapter.name, False, fehler, 0.0, "", "", GATE_FEHLER, fehler, auswahl=wahl)
    text = f"schnell: kein voller Lauf je Ticket, Komplett-Prüfung einmal je Spec — {grund}"
    return Stufe("voll", adapter.name, False, text, 0.0, "", "", AUSGELASSEN, "", auswahl=_wahl(adapter, grund))


def _pruefe(repo: Path, adapter: Adapter, basis: str, pfade: list[str] | None, art: str | None) -> Stufe:
    """Stufe 3 (Auswahl), wenn ``regeln`` sie erlaubt und sie ≥ 1 Test wählt, sonst Stufe 4 (voll).

    ``art="schnell"``: nie Stufe 4 (:func:`_ohne_voll`, mit Code-Datei Gate-Fehler); ``art="komplett"``:
    gleich Stufe 4.
    """
    if art == "komplett":
        return _voll(repo, adapter, "komplett: voller Lauf")

    def rueckfall(a: Adapter, grund: str) -> Stufe:
        return _ohne_voll(a, grund, pfade) if art == "schnell" else _voll(repo, a, grund)

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
        return rueckfall(adapter, grund)
    plan = adapter.auswahl(repo, basis)
    if plan.fehler:
        return rueckfall(adapter, f"Auswahl nicht möglich: {plan.fehler}")
    erg = ausfuehren.lauf(plan.befehl, repo)
    n, gesamt = adapter.zaehle(erg.code, erg.ausgabe)
    grund = regeln.nach_auswahl(n, pfade or [], adapter.name, doku)
    if grund:
        return rueckfall(adapter, grund)
    wahl = _wahl(adapter, grund, n, gesamt)
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
    grund = roh.grund
    if ergebnis == GRUEN and isinstance(adapter, wackler.Berichtbar):
        bericht = adapter.bericht(plan.befehl, erg.ausgabe)
        if bericht is None:
            ergebnis = GATE_FEHLER
            grund = "Prüf-Bericht fehlt oder unlesbar (kein Grün ohne Nachweis)"
        elif bericht.abgebrochen:
            ergebnis = ROT
            grund = "Prüflauf vorzeitig abgebrochen (kein Grün trotz Exit 0)"
        elif bericht.im_bericht == 0:
            ergebnis = GATE_FEHLER
            grund = "0 Tests im Prüf-Bericht gelaufen (kein Grün trotz Exit 0)"
    stufe = replace(
        roh,
        grund=grund,
        dauer_s=round(erg.dauer_s, 2),
        befehl=" ".join(plan.befehl),
        werkzeug=plan.werkzeug,
        ergebnis=ergebnis,
        ausgabe=(erg.ausgabe or grund) if ergebnis != GRUEN else "",
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


def stufen(repo: Path, adapter_liste: tuple[Adapter, ...], basis: str, art: str | None = None) -> list[Stufe]:
    """Alle Stufen für ``repo`` gegen ``basis``; leer, wenn kein Adapter das Repo erkennt.

    ``art`` siehe Modul-Kopf (``schnell`` ohne Stufe 4, ``komplett`` gleich Stufe 4).
    """
    erkannt = [adapter for adapter in adapter_liste if adapter.erkennt(repo)]
    if not erkannt:
        return []
    vorn, halt = sofort.stufen(repo, basis)
    if halt:
        return vorn + [Stufe("voll", a.name, False, halt, 0.0, "", "", AUSGELASSEN, "") for a in erkannt]
    pfade = sofort.geaendert(repo, basis)
    return vorn + [_pruefe(repo, adapter, basis, pfade, art) for adapter in erkannt]


def eigener_befehl(
    repo: Path, befehl: str, art: str, zeitgrenze_s: float | None = None, *, ticket: int | None = None
) -> Stufe:
    """Eingerichteten Prüf-Befehl ``pruefung.<art>`` in bash laufen lassen: Exit 0 grün, sonst rot.

    Fehlt bash, ist das ein Gate-Fehler (Werkzeug), kein roter Code. ``zeitgrenze_s`` (#455):
    danach endet der Befehl samt Unterprozessen; das Ergebnis ist rot, ``grund`` und ``ausgabe``
    nennen „Zeitgrenze“.
    """
    start = time.monotonic()
    try:
        erg = bash_wahl.lauf(
            ["-c", befehl],
            zeitgrenze_s=zeitgrenze_s,
            cwd=repo,
            env={**os.environ, **temp_ordner.umgebung(ticket, ordner=repo, von="pruef", wiederverwenden=True)},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as fehler:
        text = f"bash für pruefung.{art} fehlt: {fehler}"
        return Stufe(art, "", False, text, 0.0, befehl, "bash", GATE_FEHLER, text)
    except subprocess.TimeoutExpired as abbruch:
        text = f"{ZEITGRENZE} {zeitgrenze_s:.0f} s überschritten — pruefung.{art} samt Unterprozessen beendet"
        bisher = abbruch.output if isinstance(abbruch.output, str) else ""
        return Stufe(
            art, "", True, text, round(time.monotonic() - start, 2), befehl, "bash", ROT, f"{bisher}\n{text}".lstrip()
        )
    ergebnis = GRUEN if erg.returncode == 0 else ROT
    return Stufe(
        art,
        "",
        True,
        f"pruefung.{art} aus .to-spawn/config.json (Exit {erg.returncode})",
        round(time.monotonic() - start, 2),
        befehl,
        "bash",
        ergebnis,
        (erg.stdout or "") if ergebnis != GRUEN else "",
    )


def ergebnis(liste: list[Stufe]) -> tuple[int, str]:
    """Gesamt-Exit und ``fehler_kurz``: Rot schlägt Gate-Fehler (Rot ist sicher ein Code-Fehler)."""
    for wert in (ROT, GATE_FEHLER):
        treffer = [stufe for stufe in liste if stufe.ergebnis == wert]
        if treffer:
            return wert, _kurz("\n".join(stufe.ausgabe for stufe in treffer))
    return GRUEN, ""
