"""Ort-Wahl für alle Testläufe (#760/#761/#802, Beschlüsse E33/E34/E35): lokal oder GitHub.

Tiefes Modul mit schmaler Schnittstelle (ein Befehl, ein Ergebnis):

- **Jeder Testlauf, Linux und Windows** (#802, E35, :func:`entscheiden`): kleine Ticket-Prüfung
  (:mod:`to_spawn.pruef`) wie großer Spec-/Nacht-Lauf (:mod:`to_spawn.komplett_lauf`,
  :func:`ausfuehren`) fragen dieselbe Ort-Wahl. Ergebnis ``lokal`` oder ``github``.
- **Im Zweifel GitHub**: zuerst wird der Speicher über :func:`messen_system` /
  :func:`speicher.system_speicher_mib` (Linux ``/proc/meminfo`` und Windows ``GlobalMemoryStatusEx``
  hinter derselben Schnittstelle) gemessen. Ist der Speicher auch nur knapp oder die Messung
  unsicher (fehlt, kaputt, schwankt, Fehler), heißt das Ergebnis ``github`` mit Grund im Log.
- **Dynamische Schwelle an genau einer Stelle** (:func:`berechne_schwelle`): aus Gesamt-Speicher,
  CPU-Kernen und Betriebssystem berechnet. Die Betriebssystem-Grundlast wird vorher abgezogen
  (Windows mit Desktop-Programmen braucht mehr Reserve als ein Linux-Server).
- **Repo ohne GitHub-Lauf**: hat das Repo keinen GitHub-Workflow und keinen ``github_befehl``,
  läuft der Test mit Hinweis-Zeile im Log lokal, ohne Mail.
- **Große Bau-Server-Runde mit Ruhefenster** (#760/#761, E33/E34, :func:`waehlen` /
  :func:`ausfuehren`): misst den Linux-Bau-Server über ``fenster_min`` (30 min, optional +30 min),
  überwacht während des Laufs, übergibt bei Kipp-Lage an GitHub und lässt angehaltene/gebremste
  Bau-Tickets nach Rundenende sofort weiterlaufen.
- **Erkennung im Repo** (#761, E34): :func:`pruefe_repo_runde` prüft rein lesend für die
  Bereitschafts-Prüfung (:mod:`to_spawn.inventur`), ob ein Repo eine große Runde hat und ob
  sie über die Ort-Wahl läuft — ohne jemals etwas im Repo umzubauen.
- **Grenzwerte an genau einer Stelle**: alle Schwellen, Zeitfenster und Rechenregel-Parameter
  stammen allein aus ``config.DEFAULTS["ort_wahl"]`` (überschreibbar in ``.to-spawn/config.json``).
- **Keine Mail, nur Log**: jede Entscheidung und jede Übergabe landet ausschließlich im Log.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from . import config, prozessbaum, speicher, sperre, starter

log = logging.getLogger("to_spawn.ort_wahl")

ORT_LOKAL: Literal["lokal"] = "lokal"
ORT_SERVER: Literal["server"] = "server"
ORT_GITHUB: Literal["github"] = "github"
Ort = Literal["lokal", "server", "github"]
LaufArt = Literal["klein", "gross"]

BEFEHLE: tuple[str, ...] = ("ort-wahl",)

#: Vorgabe aus der einen Konfigurationsquelle ``config.DEFAULTS["ort_wahl"]``.
VORGABE: dict[str, Any] = dict(config.DEFAULTS["ort_wahl"])

#: Test-Tür für die Datei der laufenden großen Runde (#761, analog ``TO_SPAWN_SPEICHER_PLATZ``).
RUNDE_ENV = "TO_SPAWN_ORT_WAHL_RUNDE"
#: Wartezeit (Sekunden) auf die Sperre der Runden-Datei.
_RUNDE_SPERRE_S = 5.0

EXIT_OK = 0
EXIT_FEHLER = 1
EXIT_AUFRUF = 2

_ORT_WAHL_AUFRUF_MUSTER = re.compile(
    r"(?:\bort-wahl\b|\bto_spawn\.ort_wahl\b|\bimport\s+ort_wahl\b|\bort_wahl\.(?:waehlen|ausfuehren|entscheiden)\b)",
    re.IGNORECASE,
)
_RUNDE_SKRIPT_MUSTER = re.compile(
    r"(?:grosse[_-]?runde|große[_-]?runde|nachtrunde|nightly|komplett[_-]?runde|voll[_-]?test)",
    re.IGNORECASE,
)
_SKRIPT_ENDUNGEN = frozenset({".sh", ".bash", ".py", ".mjs", ".js", ""})
_SCHEDULE_MUSTER = re.compile(r"^\s*schedule\s*:", re.IGNORECASE)
_SELF_HOSTED_MUSTER = re.compile(r"runs-on\s*:[^\n]*self-hosted", re.IGNORECASE)


@dataclass(frozen=True)
class Grenzen:
    """Alle Schwellen, Fenster und Rechenregel-Parameter der Ort-Wahl aus ``config.DEFAULTS['ort_wahl']``."""

    fenster_min: int
    verlaengerung_min: int
    takt_s: int
    lauf_takt_s: int
    min_frei_mib: int
    max_last: float
    max_bau_sessions: int
    os_grundlast_linux_mib: int = 2048
    os_grundlast_windows_mib: int = 6144
    os_grundlast_sonst_mib: int = 4096
    je_kern_mib: int = 256
    max_kerne_rechnung: int = 16
    puffer_anteil: float = 0.20
    max_last_je_kern_linux: float = 0.5
    max_last_je_kern_windows: float = 0.35
    max_schwankung_anteil: float = 0.10
    stichproben: int = 2
    stichproben_abstand_s: float = 0.05
    github_befehl: str = ""
    github_befehl_schnell: str = ""
    github_befehl_komplett: str = ""


@dataclass(frozen=True)
class Schwelle:
    """Dynamisch aus Hardware und Betriebssystem berechnete Schwelle (#802, E35)."""

    plattform: str
    gesamt_mib: int
    kerne: int
    os_grundlast_mib: int
    nutzbar_gesamt_mib: int
    kern_bedarf_mib: int
    puffer_mib: int
    min_frei_mib: int
    max_last: float
    max_schwankung_mib: int

    def als_dict(self) -> dict[str, Any]:
        return {
            "plattform": self.plattform,
            "gesamt_mib": self.gesamt_mib,
            "kerne": self.kerne,
            "os_grundlast_mib": self.os_grundlast_mib,
            "nutzbar_gesamt_mib": self.nutzbar_gesamt_mib,
            "kern_bedarf_mib": self.kern_bedarf_mib,
            "puffer_mib": self.puffer_mib,
            "min_frei_mib": self.min_frei_mib,
            "max_last": self.max_last,
            "max_schwankung_mib": self.max_schwankung_mib,
        }


@dataclass(frozen=True)
class Messpunkt:
    """Einzelne Momentaufnahme der Auslastung (Linux und Windows, #760/#802)."""

    frei_mib: int | None
    last_1m: float | None = None
    bau_sessions: int | None = None
    zeit_s: float | None = None
    gesamt_mib: int | None = None
    kerne: int | None = None
    plattform: str | None = None

    def als_dict(self) -> dict[str, Any]:
        daten: dict[str, Any] = {
            "frei_mib": self.frei_mib,
            "last_1m": self.last_1m,
            "bau_sessions": self.bau_sessions,
        }
        if self.zeit_s is not None:
            daten["zeit_s"] = self.zeit_s
        if self.gesamt_mib is not None:
            daten["gesamt_mib"] = self.gesamt_mib
        if self.kerne is not None:
            daten["kerne"] = self.kerne
        if self.plattform is not None:
            daten["plattform"] = self.plattform
        return daten


@dataclass(frozen=True)
class WahlErgebnis:
    """Ergebnis der Vorab-Entscheidung (:func:`entscheiden` / :func:`waehlen`)."""

    ort: Ort
    grund: str
    messungen: tuple[Messpunkt, ...] = ()
    verlaengert: bool = False
    schwelle: Schwelle | None = None
    fallback_lokal: bool = False
    empfohlen: Ort | None = None

    def als_dict(self) -> dict[str, Any]:
        daten: dict[str, Any] = {
            "ort": self.ort,
            "grund": self.grund,
            "verlaengert": self.verlaengert,
            "messungen": len(self.messungen),
            "fallback_lokal": self.fallback_lokal,
        }
        if self.empfohlen is not None:
            daten["empfohlen"] = self.empfohlen
        if self.schwelle is not None:
            daten["schwelle"] = self.schwelle.als_dict()
        return daten


@dataclass(frozen=True)
class LaufErgebnis:
    """Ergebnis eines überwachten Testlaufs (:func:`ausfuehren`)."""

    ort: Ort
    grund: str
    exit_code: int
    wahl: WahlErgebnis
    abgebrochen: bool = False
    uebergeben: bool = False
    lauf_messungen: tuple[Messpunkt, ...] = ()
    fortgesetzt_pids: tuple[int, ...] = ()

    def als_dict(self) -> dict[str, Any]:
        return {
            "ort": self.ort,
            "grund": self.grund,
            "exit_code": self.exit_code,
            "abgebrochen": self.abgebrochen,
            "uebergeben": self.uebergeben,
            "wahl": self.wahl.als_dict(),
            "lauf_messungen": len(self.lauf_messungen),
            "fortgesetzt_pids": list(self.fortgesetzt_pids),
        }


@dataclass(frozen=True)
class RepoRundeBefund:
    """Ergebnis der Bereitschafts-Prüfung für die große Testrunde eines Repos (#761, E34)."""

    status: Literal["mit_ort_wahl", "github", "ohne_ort_wahl", "fehlt"]
    quelle: str = ""
    hinweis: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("mit_ort_wahl", "github")

    def als_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "quelle": self.quelle,
            "hinweis": self.hinweis,
        }


def _zahl_int(block: dict[str, Any], name: str, *, min_wert: int) -> int:
    vorgabe = int(config.DEFAULTS["ort_wahl"][name])
    wert = block.get(name)
    if isinstance(wert, (int, float)) and not isinstance(wert, bool) and math.isfinite(wert) and wert >= min_wert:
        return int(wert)
    return vorgabe


def _zahl_float(block: dict[str, Any], name: str, *, min_wert: float) -> float:
    vorgabe = float(config.DEFAULTS["ort_wahl"][name])
    wert = block.get(name)
    if isinstance(wert, (int, float)) and not isinstance(wert, bool) and math.isfinite(wert) and wert >= min_wert:
        return float(wert)
    return vorgabe


def _text(block: dict[str, Any], name: str) -> str:
    vorgabe = str(config.DEFAULTS["ort_wahl"].get(name, ""))
    wert = block.get(name)
    return wert.strip() if isinstance(wert, str) else vorgabe


def grenzen(konfig: dict[str, Any] | None) -> Grenzen:
    """Liest den Block ``ort_wahl`` aus ``konfig``; fehlende/ungültige Felder kommen aus ``config.DEFAULTS``."""
    block = (konfig or {}).get("ort_wahl") if isinstance(konfig, dict) else None
    daten = block if isinstance(block, dict) else {}
    return Grenzen(
        fenster_min=_zahl_int(daten, "fenster_min", min_wert=1),
        verlaengerung_min=_zahl_int(daten, "verlaengerung_min", min_wert=0),
        takt_s=_zahl_int(daten, "takt_s", min_wert=1),
        lauf_takt_s=_zahl_int(daten, "lauf_takt_s", min_wert=1),
        min_frei_mib=_zahl_int(daten, "min_frei_mib", min_wert=0),
        max_last=_zahl_float(daten, "max_last", min_wert=0.0),
        max_bau_sessions=_zahl_int(daten, "max_bau_sessions", min_wert=0),
        os_grundlast_linux_mib=_zahl_int(daten, "os_grundlast_linux_mib", min_wert=0),
        os_grundlast_windows_mib=_zahl_int(daten, "os_grundlast_windows_mib", min_wert=0),
        os_grundlast_sonst_mib=_zahl_int(daten, "os_grundlast_sonst_mib", min_wert=0),
        je_kern_mib=_zahl_int(daten, "je_kern_mib", min_wert=0),
        max_kerne_rechnung=_zahl_int(daten, "max_kerne_rechnung", min_wert=1),
        puffer_anteil=_zahl_float(daten, "puffer_anteil", min_wert=0.0),
        max_last_je_kern_linux=_zahl_float(daten, "max_last_je_kern_linux", min_wert=0.05),
        max_last_je_kern_windows=_zahl_float(daten, "max_last_je_kern_windows", min_wert=0.05),
        max_schwankung_anteil=_zahl_float(daten, "max_schwankung_anteil", min_wert=0.0),
        stichproben=_zahl_int(daten, "stichproben", min_wert=1),
        stichproben_abstand_s=_zahl_float(daten, "stichproben_abstand_s", min_wert=0.0),
        github_befehl=_text(daten, "github_befehl"),
        github_befehl_schnell=_text(daten, "github_befehl_schnell"),
        github_befehl_komplett=_text(daten, "github_befehl_komplett"),
    )


def _normalisiere_plattform(plattform: str) -> str:
    wert = plattform.strip().lower()
    if wert in ("linux", "linux2"):
        return "linux"
    if wert in ("win32", "windows", "cygwin", "msys"):
        return "win32"
    return wert


def _normalisiere_art(art: str) -> LaufArt:
    wert = art.strip().lower()
    if wert in ("klein", "schnell"):
        return "klein"
    if wert in ("gross", "komplett"):
        return "gross"
    raise ValueError(f"Unbekannte Lauf-Art für Ort-Wahl: {art!r} (erlaubt: klein, schnell, gross, komplett)")


def berechne_schwelle(
    gesamt_mib: int,
    kerne: int,
    plattform: str,
    konfig: dict[str, Any] | None = None,
) -> Schwelle:
    """Einzige Rechenregel für die dynamische Schwelle nach Hardware und Betriebssystem (#802, E35).

    Zieht zuerst die Grundlast des Betriebssystems ab (Windows mit Desktop-Programmen braucht
    mehr Reserve als ein Linux-Server) und berechnet daraus Mindest-Freispeicher, Last-Obergrenze
    und zulässige Mess-Schwankung. Kleine Maschinen kippen dadurch anteilig früh zu GitHub,
    größere Maschinen dürfen mehr lokal laufen lassen.
    """
    if isinstance(gesamt_mib, bool) or not isinstance(gesamt_mib, int) or gesamt_mib <= 0:
        raise ValueError(f"Gesamtspeicher muss eine positive ganze Zahl sein, erhalten: {gesamt_mib!r}")
    if isinstance(kerne, bool) or not isinstance(kerne, int) or kerne <= 0:
        raise ValueError(f"CPU-Kerne müssen eine positive ganze Zahl sein, erhalten: {kerne!r}")
    if not isinstance(plattform, str) or not plattform.strip():
        raise ValueError(f"Betriebssystem fehlt oder ist leer: {plattform!r}")

    g = grenzen(konfig)
    os_name = _normalisiere_plattform(plattform)
    if os_name == "linux":
        os_grundlast_mib = g.os_grundlast_linux_mib
        last_je_kern = g.max_last_je_kern_linux
    elif os_name == "win32":
        os_grundlast_mib = g.os_grundlast_windows_mib
        last_je_kern = g.max_last_je_kern_windows
    else:
        os_grundlast_mib = g.os_grundlast_sonst_mib
        last_je_kern = g.max_last_je_kern_windows

    nutzbar_gesamt_mib = max(0, gesamt_mib - os_grundlast_mib)
    kern_bedarf_mib = min(kerne, g.max_kerne_rechnung) * g.je_kern_mib
    puffer_mib = round(nutzbar_gesamt_mib * g.puffer_anteil)
    min_frei_mib = os_grundlast_mib + kern_bedarf_mib + puffer_mib
    max_last = round(max(0.5, kerne * last_je_kern), 2)
    max_schwankung_mib = max(512, round(nutzbar_gesamt_mib * g.max_schwankung_anteil))
    return Schwelle(
        plattform=os_name,
        gesamt_mib=gesamt_mib,
        kerne=kerne,
        os_grundlast_mib=os_grundlast_mib,
        nutzbar_gesamt_mib=nutzbar_gesamt_mib,
        kern_bedarf_mib=kern_bedarf_mib,
        puffer_mib=puffer_mib,
        min_frei_mib=min_frei_mib,
        max_last=max_last,
        max_schwankung_mib=max_schwankung_mib,
    )


def _punkt_dynamisch_pruefen(
    punkt: Messpunkt,
    konfig: dict[str, Any] | None,
    *,
    art: LaufArt,
    standard_plattform: str,
) -> tuple[bool, str, Schwelle | None]:
    """Prüft einen :class:`Messpunkt` gegen die eine dynamische Rechenregel (fail-closed, #802)."""
    os_wert = standard_plattform if punkt.plattform is None else punkt.plattform
    if not isinstance(os_wert, str) or not os_wert.strip():
        return False, "Messung ungültig: Betriebssystem fehlt", None
    if punkt.gesamt_mib is None:
        return False, "Messung unvollständig: Gesamtspeicher fehlt", None
    if isinstance(punkt.gesamt_mib, bool) or not isinstance(punkt.gesamt_mib, int) or punkt.gesamt_mib <= 0:
        return False, f"Messung ungültig: Gesamtspeicher kaputt ({punkt.gesamt_mib!r})", None
    if punkt.kerne is None:
        return False, "Messung unvollständig: CPU-Kerne fehlen", None
    if isinstance(punkt.kerne, bool) or not isinstance(punkt.kerne, int) or punkt.kerne <= 0:
        return False, f"Messung ungültig: CPU-Kerne kaputt ({punkt.kerne!r})", None
    if punkt.frei_mib is None:
        return False, "Messung unvollständig: freier Speicher nicht lesbar", None
    if isinstance(punkt.frei_mib, bool) or not isinstance(punkt.frei_mib, int) or punkt.frei_mib < 0:
        return False, f"Messung ungültig: freier Speicher negativ oder kaputt ({punkt.frei_mib!r})", None
    if punkt.frei_mib > punkt.gesamt_mib:
        return (
            False,
            f"Messung unplausibel: freier Speicher ({punkt.frei_mib} MiB) übersteigt Gesamtspeicher ({punkt.gesamt_mib} MiB)",
            None,
        )

    schwelle = berechne_schwelle(punkt.gesamt_mib, punkt.kerne, os_wert, konfig)
    if punkt.frei_mib < schwelle.min_frei_mib:
        return (
            False,
            (
                f"Speicher knapp ({schwelle.plattform}, {speicher._gb_text(punkt.gesamt_mib)}, {punkt.kerne} Kerne): "
                f"{speicher._gb_text(punkt.frei_mib)} frei, Mindestmaß {speicher._gb_text(schwelle.min_frei_mib)}"
            ),
            schwelle,
        )
    if punkt.last_1m is not None:
        if (
            isinstance(punkt.last_1m, bool)
            or not isinstance(punkt.last_1m, (int, float))
            or not math.isfinite(punkt.last_1m)
            or punkt.last_1m < 0
        ):
            return False, f"Messung ungültig: Systemlast kaputt ({punkt.last_1m!r})", schwelle
        if punkt.last_1m > schwelle.max_last:
            return (
                False,
                f"Systemlast zu hoch: {punkt.last_1m:.2f}, Obergrenze {schwelle.max_last:.2f} ({punkt.kerne} Kerne)",
                schwelle,
            )
    if punkt.bau_sessions is not None:
        if isinstance(punkt.bau_sessions, bool) or not isinstance(punkt.bau_sessions, int) or punkt.bau_sessions < 0:
            return False, f"Messung ungültig: Bau-Sessions kaputt ({punkt.bau_sessions!r})", schwelle
        g = grenzen(konfig)
        if art == "gross" and punkt.bau_sessions > g.max_bau_sessions:
            return (
                False,
                f"{punkt.bau_sessions} laufende Bau-Session(s), Obergrenze {g.max_bau_sessions}",
                schwelle,
            )
    return (
        True,
        (
            f"genug Speicher ({schwelle.plattform}, {speicher._gb_text(punkt.gesamt_mib)}, {punkt.kerne} Kerne): "
            f"{speicher._gb_text(punkt.frei_mib)} frei, Mindestmaß {speicher._gb_text(schwelle.min_frei_mib)}"
        ),
        schwelle,
    )


def _schwankung_pruefen(punkte: Sequence[Messpunkt], schwelle: Schwelle) -> str | None:
    """Meldet einen Grund, wenn der freie Speicher über die Messreihe zu stark schwankt (#802)."""
    if len(punkte) < 2:
        return None
    freie = [p.frei_mib for p in punkte if isinstance(p.frei_mib, int) and not isinstance(p.frei_mib, bool)]
    if len(freie) < 2:
        return None
    spanne = max(freie) - min(freie)
    if spanne > schwelle.max_schwankung_mib:
        return (
            f"Messung unsicher: freier Speicher schwankt um {speicher._gb_text(spanne)} "
            f"(Grenze {speicher._gb_text(schwelle.max_schwankung_mib)})"
        )
    return None


def punkt_pruefen(punkt: Messpunkt, schwellen: Grenzen, *, konfig: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Prüft einen einzelnen :class:`Messpunkt` gegen ``schwellen`` (fail-closed bei fehlenden Werten)."""
    min_frei = schwellen.min_frei_mib
    max_last = schwellen.max_last
    if (
        punkt.gesamt_mib is not None
        and punkt.kerne is not None
        and isinstance(punkt.gesamt_mib, int)
        and not isinstance(punkt.gesamt_mib, bool)
        and punkt.gesamt_mib > 0
        and isinstance(punkt.kerne, int)
        and not isinstance(punkt.kerne, bool)
        and punkt.kerne > 0
    ):
        os_wert = punkt.plattform if punkt.plattform is not None else sys.platform
        if isinstance(os_wert, str) and os_wert.strip():
            dyn = berechne_schwelle(punkt.gesamt_mib, punkt.kerne, os_wert, konfig)
            min_frei = dyn.min_frei_mib
            max_last = dyn.max_last

    if punkt.frei_mib is None:
        return False, "freier Speicher nicht lesbar"
    if punkt.frei_mib < min_frei:
        return (
            False,
            (f"Speicher knapp: {speicher._gb_text(punkt.frei_mib)} frei, Mindestmaß {speicher._gb_text(min_frei)}"),
        )
    if punkt.last_1m is None or not math.isfinite(punkt.last_1m):
        return False, "Systemlast nicht lesbar"
    if punkt.last_1m > max_last:
        return False, f"Systemlast zu hoch: {punkt.last_1m:.2f}, Obergrenze {max_last:.2f}"
    if punkt.bau_sessions is None:
        return False, "laufende Bau-Sessions nicht lesbar"
    if punkt.bau_sessions > schwellen.max_bau_sessions:
        return (
            False,
            f"{punkt.bau_sessions} laufende Bau-Session(s), Obergrenze {schwellen.max_bau_sessions}",
        )
    return (
        True,
        f"ruhig: {speicher._gb_text(punkt.frei_mib)} frei, Last {punkt.last_1m:.2f}, {punkt.bau_sessions} Bau-Sessions",
    )


def messen_system(plattform: str | None = None) -> Messpunkt:
    """Misst Speicher, CPU-Kerne und (unter Linux) Last/Bau-Sessions über eine Schnittstelle (#802)."""
    ziel = sys.platform if plattform is None else plattform
    frei, gesamt = speicher.system_speicher_mib(plattform=ziel)
    kerne = speicher.cpu_kerne()
    if _normalisiere_plattform(ziel) == "linux":
        last = speicher.last_1m()
        sessions = speicher.laufende_sessions()
    else:
        last = None
        sessions = None
    return Messpunkt(
        frei_mib=frei,
        last_1m=last,
        bau_sessions=sessions,
        gesamt_mib=gesamt,
        kerne=kerne,
        plattform=ziel,
    )


def messen_linux() -> Messpunkt:
    """Liest Speicher, 1-Minuten-Last und arbeitende Bau-Sessions über :mod:`to_spawn.speicher`."""
    return messen_system(plattform="linux")


def github_befehl_fuer(konfig: dict[str, Any] | None, art: str = "schnell") -> str:
    """Konfigurierter GitHub-Befehl aus ``ort_wahl`` (art-spezifisch vor allgemein, #802)."""
    g = grenzen(konfig)
    art_sauber = art.strip().lower()
    if art_sauber in ("klein", "schnell") and g.github_befehl_schnell:
        return g.github_befehl_schnell
    if art_sauber in ("gross", "komplett") and g.github_befehl_komplett:
        return g.github_befehl_komplett
    return g.github_befehl


def hat_github_lauf(repo: Path, konfig: dict[str, Any] | None = None, *, art: str = "schnell") -> bool:
    """Hat ``repo`` einen GitHub-Testlauf (konfigurierter ``github_befehl`` oder Workflow in ``.github/workflows``)?"""
    if github_befehl_fuer(konfig, art):
        return True
    ordner = repo / ".github" / "workflows"
    if not ordner.is_dir():
        return False
    try:
        return any(p.is_file() and p.suffix.lower() in (".yml", ".yaml") for p in ordner.iterdir())
    except OSError as fehler:
        log.warning("Ort-Wahl: Workflow-Ordner %s nicht lesbar: %s", ordner, fehler)
        return False


def _abschluss_ergebnis(
    empfohlen: Ort,
    grund: str,
    punkte: Sequence[Messpunkt],
    schwelle: Schwelle | None,
    *,
    repo: Path | None,
    konfig: dict[str, Any] | None,
    art: LaufArt,
) -> WahlErgebnis:
    """Wendet bei ``github`` den Rückfall auf ``lokal`` an, falls das Repo keinen GitHub-Lauf hat (#802 Punkt 5)."""
    if empfohlen == ORT_GITHUB and repo is not None and not hat_github_lauf(repo, konfig, art=art):
        hinweis = f"Repo hat keinen GitHub-Lauf — laufe lokal (eigentlich github: {grund})"
        log.info("Ort-Wahl → lokal (Hinweis: %s)", hinweis)
        return WahlErgebnis(
            ort=ORT_LOKAL,
            grund=hinweis,
            messungen=tuple(punkte),
            schwelle=schwelle,
            fallback_lokal=True,
            empfohlen=ORT_GITHUB,
        )
    log.info("Ort-Wahl → %s: %s", empfohlen, grund)
    return WahlErgebnis(
        ort=empfohlen,
        grund=grund,
        messungen=tuple(punkte),
        schwelle=schwelle,
        fallback_lokal=False,
        empfohlen=empfohlen,
    )


def entscheiden(
    konfig: dict[str, Any] | None = None,
    *,
    art: str = "klein",
    messung: Messpunkt | None = None,
    messreihe: Sequence[Messpunkt] | None = None,
    messen: Callable[[], Messpunkt] | None = None,
    plattform: str | None = None,
    repo: Path | None = None,
    schlafen: Callable[[float], object] = time.sleep,
) -> WahlErgebnis:
    """Die eine Ort-Wahl für jeden Testlauf (kleine Ticket-Prüfung wie großer Lauf, Linux wie Windows, #802).

    Misst zuerst den Speicher (oder prüft aufgezeichnete Messwerte). Im Zweifel — knapper Speicher,
    hohe Last, fehlende/kaputte/schwankende Messung oder Messfehler — lautet das Ergebnis ``github``
    mit Grund im Log. Hat ``repo`` keinen GitHub-Lauf, fällt der Lauf mit Hinweis im Log (ohne Mail)
    auf ``lokal`` zurück.
    """
    lauf_art = _normalisiere_art(art)
    ziel_plattform = sys.platform if plattform is None else plattform
    g = grenzen(konfig)

    punkte: list[Messpunkt] = []
    letzte_schwelle: Schwelle | None = None
    letzter_satz = ""

    if messung is not None:
        kandidaten: Sequence[Messpunkt] = (messung,)
    elif messreihe is not None:
        kandidaten = messreihe
    else:
        quelle = messen or (lambda: messen_system(plattform=ziel_plattform))
        anzahl = max(1, g.stichproben)
        for i in range(anzahl):
            try:
                punkt = quelle()
            except Exception as fehler:  # noqa: BLE001 — jeder Messfehler gilt als unsicher → github
                grund = f"Messung fehlgeschlagen ({type(fehler).__name__}: {fehler})"
                log.warning("Ort-Wahl: %s", grund)
                return _abschluss_ergebnis(
                    ORT_GITHUB, grund, punkte, letzte_schwelle, repo=repo, konfig=konfig, art=lauf_art
                )
            punkte.append(punkt)
            ok, satz, schwelle = _punkt_dynamisch_pruefen(
                punkt, konfig, art=lauf_art, standard_plattform=ziel_plattform
            )
            if schwelle is not None:
                letzte_schwelle = schwelle
            if not ok:
                return _abschluss_ergebnis(
                    ORT_GITHUB, satz, punkte, letzte_schwelle, repo=repo, konfig=konfig, art=lauf_art
                )
            letzter_satz = satz
            if i + 1 < anzahl and g.stichproben_abstand_s > 0:
                schlafen(g.stichproben_abstand_s)
        if letzte_schwelle is not None:
            schwankung = _schwankung_pruefen(punkte, letzte_schwelle)
            if schwankung is not None:
                return _abschluss_ergebnis(
                    ORT_GITHUB, schwankung, punkte, letzte_schwelle, repo=repo, konfig=konfig, art=lauf_art
                )
        return _abschluss_ergebnis(
            ORT_LOKAL, letzter_satz, punkte, letzte_schwelle, repo=repo, konfig=konfig, art=lauf_art
        )

    if not kandidaten:
        return _abschluss_ergebnis(
            ORT_GITHUB,
            "Messreihe leer — kein Messpunkt vorhanden",
            (),
            None,
            repo=repo,
            konfig=konfig,
            art=lauf_art,
        )

    for idx, punkt in enumerate(kandidaten):
        punkte.append(punkt)
        ok, satz, schwelle = _punkt_dynamisch_pruefen(punkt, konfig, art=lauf_art, standard_plattform=ziel_plattform)
        if schwelle is not None:
            letzte_schwelle = schwelle
        if not ok:
            grund = satz if len(kandidaten) == 1 else f"Messpunkt {idx + 1}: {satz}"
            return _abschluss_ergebnis(
                ORT_GITHUB, grund, punkte, letzte_schwelle, repo=repo, konfig=konfig, art=lauf_art
            )
        letzter_satz = satz

    if letzte_schwelle is not None:
        schwankung = _schwankung_pruefen(punkte, letzte_schwelle)
        if schwankung is not None:
            return _abschluss_ergebnis(
                ORT_GITHUB, schwankung, punkte, letzte_schwelle, repo=repo, konfig=konfig, art=lauf_art
            )

    return _abschluss_ergebnis(ORT_LOKAL, letzter_satz, punkte, letzte_schwelle, repo=repo, konfig=konfig, art=lauf_art)


def _punkt_aus_dict(roh: Any, index: int) -> Messpunkt:
    if not isinstance(roh, dict):
        raise TypeError(f"Messpunkt #{index}: Objekt erwartet, erhalten: {type(roh).__name__}")

    def _opt_int(schluessel: str) -> int | None:
        wert = roh.get(schluessel)
        if wert is None:
            return None
        if isinstance(wert, bool) or not isinstance(wert, int):
            raise TypeError(f"Messpunkt #{index}: {schluessel} muss eine ganze Zahl oder null sein")
        return wert

    def _opt_float(schluessel: str) -> float | None:
        wert = roh.get(schluessel)
        if wert is None:
            return None
        if isinstance(wert, bool) or not isinstance(wert, (int, float)) or not math.isfinite(wert):
            raise ValueError(f"Messpunkt #{index}: {schluessel} muss eine Zahl oder null sein")
        return float(wert)

    def _opt_str(schluessel: str) -> str | None:
        wert = roh.get(schluessel)
        if wert is None:
            return None
        if not isinstance(wert, str):
            raise TypeError(f"Messpunkt #{index}: {schluessel} muss Text oder null sein")
        return wert

    return Messpunkt(
        frei_mib=_opt_int("frei_mib"),
        last_1m=_opt_float("last_1m"),
        bau_sessions=_opt_int("bau_sessions"),
        zeit_s=_opt_float("zeit_s"),
        gesamt_mib=_opt_int("gesamt_mib"),
        kerne=_opt_int("kerne"),
        plattform=_opt_str("plattform"),
    )


def messreihe_laden(pfad: Path) -> tuple[Messpunkt, ...]:
    """Lädt eine aufgezeichnete Messreihe aus JSON (Liste oder ``{"messreihe": [...]}``) oder JSONL."""
    text = pfad.read_text(encoding="utf-8").strip()
    if not text:
        return ()
    if text.startswith(("[", "{")):
        try:
            geladen = json.loads(text)
        except ValueError:
            geladen = None
        if isinstance(geladen, list):
            return tuple(_punkt_aus_dict(e, i) for i, e in enumerate(geladen, 1))
        if isinstance(geladen, dict) and isinstance(geladen.get("messreihe"), list):
            return tuple(_punkt_aus_dict(e, i) for i, e in enumerate(geladen["messreihe"], 1))
        if isinstance(geladen, dict) and any(
            k in geladen for k in ("frei_mib", "last_1m", "bau_sessions", "gesamt_mib", "kerne", "plattform")
        ):
            return (_punkt_aus_dict(geladen, 1),)
    zeilen = [z.strip() for z in text.splitlines() if z.strip()]
    return tuple(_punkt_aus_dict(json.loads(z), i) for i, z in enumerate(zeilen, 1))


def _waehle_aus_reihe(
    messreihe: Sequence[Messpunkt],
    schwellen: Grenzen,
    *,
    verlaengern: bool,
    braucht_verlaengerung: Callable[[tuple[Messpunkt, ...], Grenzen], bool] | None,
) -> WahlErgebnis:
    if not messreihe:
        grund = "Messreihe leer — kein Messpunkt vorhanden"
        log.info("Ort-Wahl → github: %s", grund)
        return WahlErgebnis(ort=ORT_GITHUB, grund=grund)

    ziel_s = float(schwellen.fenster_min * 60)
    verlaengerung_s = float(schwellen.verlaengerung_min * 60)
    verlaengert = False
    punkte: list[Messpunkt] = []
    start_zeit = messreihe[0].zeit_s
    letzte_zeit = start_zeit
    dauer_s = 0.0

    for idx, punkt in enumerate(messreihe):
        punkte.append(punkt)
        ruhig, satz = punkt_pruefen(punkt, schwellen)
        if not ruhig:
            grund = f"Messpunkt {idx + 1}: {satz}"
            log.info("Ort-Wahl → github: %s", grund)
            return WahlErgebnis(ort=ORT_GITHUB, grund=grund, messungen=tuple(punkte), verlaengert=verlaengert)

        if start_zeit is not None and punkt.zeit_s is not None:
            if letzte_zeit is not None and punkt.zeit_s < letzte_zeit:
                grund = f"Messpunkt {idx + 1}: rückläufiger Zeitstempel in Messreihe"
                log.info("Ort-Wahl → github: %s", grund)
                return WahlErgebnis(ort=ORT_GITHUB, grund=grund, messungen=tuple(punkte), verlaengert=verlaengert)
            letzte_zeit = punkt.zeit_s
            dauer_s = punkt.zeit_s - start_zeit
        else:
            dauer_s = float(idx * schwellen.takt_s)

        if dauer_s >= ziel_s:
            soll_verlaengern = (
                not verlaengert
                and verlaengerung_s > 0
                and (
                    verlaengern
                    or (braucht_verlaengerung is not None and braucht_verlaengerung(tuple(punkte), schwellen))
                )
            )
            if soll_verlaengern:
                verlaengert = True
                ziel_s = dauer_s + verlaengerung_s
                log.info(
                    "Ort-Wahl: erstes Fenster (%d min) ruhig — verlängere bei Bedarf um weitere %d min",
                    schwellen.fenster_min,
                    schwellen.verlaengerung_min,
                )
                continue
            gesamt_min = schwellen.fenster_min + (schwellen.verlaengerung_min if verlaengert else 0)
            grund = f"Bau-Server durchgehend ruhig über {gesamt_min} min ({len(punkte)} Messpunkte)"
            log.info("Ort-Wahl → server: %s", grund)
            return WahlErgebnis(ort=ORT_SERVER, grund=grund, messungen=tuple(punkte), verlaengert=verlaengert)

    grund = f"Messreihe unvollständig: {dauer_s / 60:.1f} min statt {ziel_s / 60:.0f} min"
    log.info("Ort-Wahl → github: %s", grund)
    return WahlErgebnis(ort=ORT_GITHUB, grund=grund, messungen=tuple(punkte), verlaengert=verlaengert)


def waehlen(
    konfig: dict[str, Any] | None = None,
    *,
    messreihe: Sequence[Messpunkt] | None = None,
    messen: Callable[[], Messpunkt] | None = None,
    schlafen: Callable[[float], object] = time.sleep,
    uhr: Callable[[], float] = time.monotonic,
    verlaengern: bool = False,
    braucht_verlaengerung: Callable[[tuple[Messpunkt, ...], Grenzen], bool] | None = None,
    plattform: str | None = None,
) -> WahlErgebnis:
    """Wählt vor dem Start den Lauf-Ort (``server`` oder ``github``).

    Läuft nur unter Linux; unter Windows (und jedem anderen Nicht-Linux) wird sofort
    ``github`` mit Grund gemeldet — ohne Messung und ohne Schlaf.
    """
    ziel_plattform = sys.platform if plattform is None else plattform
    if ziel_plattform != "linux":
        grund = f"kein Linux ({ziel_plattform}) — Ort-Wahl läuft nur auf dem Linux-Bau-Server"
        log.info("Ort-Wahl → github: %s", grund)
        return WahlErgebnis(ort=ORT_GITHUB, grund=grund)

    schwellen = grenzen(konfig)
    if messreihe is not None:
        return _waehle_aus_reihe(
            messreihe,
            schwellen,
            verlaengern=verlaengern,
            braucht_verlaengerung=braucht_verlaengerung,
        )

    quelle = messen or messen_linux
    start = uhr()
    ziel = start + float(schwellen.fenster_min * 60)
    verlaengerung_s = float(schwellen.verlaengerung_min * 60)
    verlaengert = False
    punkte: list[Messpunkt] = []

    while True:
        punkt = quelle()
        punkte.append(punkt)
        ruhig, satz = punkt_pruefen(punkt, schwellen)
        if not ruhig:
            grund = f"Messpunkt {len(punkte)}: {satz}"
            log.info("Ort-Wahl → github: %s", grund)
            return WahlErgebnis(ort=ORT_GITHUB, grund=grund, messungen=tuple(punkte), verlaengert=verlaengert)

        jetzt = uhr()
        if jetzt >= ziel:
            soll_verlaengern = (
                not verlaengert
                and verlaengerung_s > 0
                and (
                    verlaengern
                    or (braucht_verlaengerung is not None and braucht_verlaengerung(tuple(punkte), schwellen))
                )
            )
            if soll_verlaengern:
                verlaengert = True
                ziel = jetzt + verlaengerung_s
                log.info(
                    "Ort-Wahl: erstes Fenster (%d min) ruhig — verlängere bei Bedarf um weitere %d min",
                    schwellen.fenster_min,
                    schwellen.verlaengerung_min,
                )
            else:
                gesamt_min = schwellen.fenster_min + (schwellen.verlaengerung_min if verlaengert else 0)
                grund = f"Bau-Server durchgehend ruhig über {gesamt_min} min ({len(punkte)} Messpunkte)"
                log.info("Ort-Wahl → server: %s", grund)
                return WahlErgebnis(ort=ORT_SERVER, grund=grund, messungen=tuple(punkte), verlaengert=verlaengert)

        warte = min(float(schwellen.takt_s), max(0.0, ziel - jetzt))
        if warte > 0:
            schlafen(warte)


def _server_abbrechen(proc: subprocess.Popen[Any], job: Any, frist_s: float) -> None:
    """Beendet den laufenden Server-Testlauf samt Kindprozessen sauber (SIGTERM → harter Abbruch)."""
    if proc.poll() is not None:
        return
    if sys.platform != "win32":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError as fehler:
            log.warning("SIGTERM an Prozessgruppe %d gescheitert: %s", proc.pid, fehler)
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=max(0.0, frist_s))
            return
        except subprocess.TimeoutExpired:
            pass
    prozessbaum.gruppe_beenden(proc, job=job)
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        log.warning("Server-Prozess %d endete nicht rechtzeitig nach Abbruch", proc.pid)


def _github_starten(befehl: Sequence[str], cwd: Path | None) -> int:
    lauf = starter.starte(
        list(befehl),
        cwd=cwd,
        warten=True,
        notnagel=None,
        check=False,
    )
    return int(lauf.returncode)


def runde_pfad() -> Path:
    """Pfad der Statusdatei für eine gerade auf dem Linux-Bau-Server laufende große Runde (#761)."""
    eigen = os.environ.get(RUNDE_ENV)
    if eigen:
        return Path(eigen)
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "to-spawn" / "ort-wahl-runde.json"


def _pid_eintrag(pid: int) -> dict[str, Any]:
    eintrag: dict[str, Any] = {"pid": int(pid)}
    start = prozessbaum.startzeit(int(pid))
    if start is not None:
        eintrag["proc_start"] = start
    return eintrag


def _runde_lesen(pfad: Path) -> dict[str, Any] | None:
    try:
        roh = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return roh if isinstance(roh, dict) else None


def _runde_schreiben(pfad: Path, daten: dict[str, Any]) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    neu = pfad.with_name(f"{pfad.name}.{os.getpid()}.neu")
    neu.write_text(json.dumps(daten, ensure_ascii=False) + "\n", encoding="utf-8")
    neu.replace(pfad)


@contextlib.contextmanager
def _runde_sperre(pfad: Path) -> Iterator[None]:
    try:
        with sperre.halte(pfad.with_suffix(".sperre"), warten=_RUNDE_SPERRE_S):
            yield
    except OSError as fehler:
        log.warning("Runden-Sperre %s nicht nutzbar (%s) — arbeite ohne Sperre", pfad, fehler)
        yield


def _pids_fortsetzen(eintraege: Sequence[Any], *, grund: str = "") -> tuple[int, ...]:
    """Setzt angehaltene Bau-Session-PIDs unter Linux per ``SIGCONT`` fort (#761)."""
    if sys.platform != "linux":
        return ()
    sigcont = getattr(signal, "SIGCONT", None)
    if sigcont is None:
        return ()
    fortgesetzt: list[int] = []
    gesehen: set[int] = set()
    for roh in eintraege:
        if isinstance(roh, int) and not isinstance(roh, bool):
            pid, erwartet_start = roh, None
        elif isinstance(roh, dict) and isinstance(roh.get("pid"), int) and not isinstance(roh.get("pid"), bool):
            pid = int(roh["pid"])
            start_roh = roh.get("proc_start")
            erwartet_start = int(start_roh) if isinstance(start_roh, int) and not isinstance(start_roh, bool) else None
        else:
            continue
        if pid <= 0 or pid in gesehen:
            continue
        gesehen.add(pid)
        if not prozessbaum.aktiv(pid, erwartet_start=erwartet_start):
            log.info("Ort-Wahl: angehaltene PID %d lebt nicht mehr (oder neu vergeben) — kein SIGCONT", pid)
            continue
        try:
            os.kill(pid, sigcont)
        except ProcessLookupError:
            continue
        except OSError as fehler:
            log.warning("Ort-Wahl: SIGCONT an PID %d gescheitert: %s", pid, fehler)
            continue
        fortgesetzt.append(pid)
        zusatz = f" ({grund})" if grund else ""
        log.info("Ort-Wahl: angehaltene Session PID %d nach Rundenende fortgesetzt%s", pid, zusatz)
    return tuple(fortgesetzt)


def runde_beginnen(
    *,
    pid: int | None = None,
    server_pid: int | None = None,
    grund: str = "",
    angehaltene_pids: Sequence[int] = (),
    pfad: Path | None = None,
) -> Path:
    """Vermerkt eine auf dem Linux-Bau-Server laufende große Testrunde (#761, E34)."""
    ziel = pfad or runde_pfad()
    inhaber = os.getpid() if pid is None else int(pid)
    daten: dict[str, Any] = {
        "pid": inhaber,
        "seit": time.time(),
        "grund": grund,
        "angehaltene_pids": [_pid_eintrag(p) for p in angehaltene_pids if isinstance(p, int) and p > 0],
    }
    start = prozessbaum.startzeit(inhaber)
    if start is not None:
        daten["proc_start"] = start
    if server_pid is not None:
        daten["server_pid"] = int(server_pid)
    with _runde_sperre(ziel):
        _runde_schreiben(ziel, daten)
    log.info("Ort-Wahl: große Runde auf dem Bau-Server aktiv (PID %d)", inhaber)
    return ziel


def session_anhalten(pid: int, *, pfad: Path | None = None) -> bool:
    """Hält eine laufende Bau-Session unter Linux per ``SIGSTOP`` für die aktive Runde an (#761).

    Hält nur an, wenn tatsächlich eine große Runde aktiv vermerkt ist, und trägt die PID samt
    Startzeit atomar ein — scheitert das Vermerken, wird sofort per ``SIGCONT`` fortgesetzt,
    damit keine Session stehen bleibt. Unter Windows/Nicht-Linux keine Wirkung (``False``).
    """
    if sys.platform != "linux" or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    sigstop = getattr(signal, "SIGSTOP", None)
    sigcont = getattr(signal, "SIGCONT", None)
    if sigstop is None or sigcont is None:
        return False
    ziel = pfad or runde_pfad()
    with _runde_sperre(ziel):
        daten = _runde_lesen(ziel)
        if daten is None:
            return False
        eintrag = _pid_eintrag(pid)
        try:
            os.kill(pid, sigstop)
        except OSError as fehler:
            log.warning("Ort-Wahl: SIGSTOP an PID %d gescheitert: %s", pid, fehler)
            return False
        liste = list(daten.get("angehaltene_pids") or [])
        if not any(isinstance(e, dict) and e.get("pid") == pid for e in liste):
            liste.append(eintrag)
        daten["angehaltene_pids"] = liste
        try:
            _runde_schreiben(ziel, daten)
        except OSError as fehler:
            log.warning(
                "Ort-Wahl: Runden-Datei nach SIGSTOP nicht schreibbar (%s) — setze PID %d sofort fort", fehler, pid
            )
            with contextlib.suppress(OSError):
                os.kill(pid, sigcont)
            return False
    log.info("Ort-Wahl: Bau-Session PID %d für laufende große Runde angehalten", pid)
    return True


def runde_beenden(
    *,
    grund: str = "",
    pfad: Path | None = None,
    zusaetzliche_pids: Sequence[int] = (),
) -> tuple[int, ...]:
    """Beendet den Runden-Vermerk und setzt alle angehaltenen Sessions automatisch fort (#761)."""
    ziel = pfad or runde_pfad()
    eintraege: list[Any] = []
    war_aktiv = False
    with _runde_sperre(ziel):
        daten = _runde_lesen(ziel)
        if daten is not None:
            war_aktiv = True
            roh = daten.get("angehaltene_pids")
            if isinstance(roh, list):
                eintraege.extend(roh)
        try:
            ziel.unlink()
            war_aktiv = True
        except FileNotFoundError:
            pass
        except OSError as fehler:
            log.warning("Ort-Wahl: Runden-Datei %s nicht löschbar: %s", ziel, fehler)
    for pid in zusaetzliche_pids:
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            eintraege.append(pid)
    fortgesetzt = _pids_fortsetzen(eintraege, grund=grund)
    if war_aktiv or fortgesetzt:
        zusatz = f" ({grund})" if grund else ""
        log.info("Ort-Wahl: große Runde beendet%s — Bau-Tickets laufen weiter", zusatz)
    return fortgesetzt


def runde_aktiv(pfad: Path | None = None) -> dict[str, Any] | None:
    """Liefert den Stand der laufenden großen Runde auf Linux, sonst ``None`` (#761).

    Lebt der vermerkte Runden-Prozess nicht mehr (z. B. harter Absturz), räumt die Funktion
    die verwaiste Datei sofort weg und setzt alle vermerkten angehaltenen Sessions per
    ``SIGCONT`` fort, damit Bau-Tickets nie hängen bleiben.
    """
    if sys.platform != "linux":
        return None
    ziel = pfad or runde_pfad()
    daten = _runde_lesen(ziel)
    if daten is None:
        return None
    pid = daten.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        runde_beenden(grund="ungültige Runden-Datei entfernt", pfad=ziel)
        return None
    start_roh = daten.get("proc_start")
    erwartet_start = int(start_roh) if isinstance(start_roh, int) and not isinstance(start_roh, bool) else None
    if not prozessbaum.aktiv(pid, erwartet_start=erwartet_start):
        runde_beenden(grund=f"Runden-Prozess PID {pid} beendet (Nachlauf-Räumung)", pfad=ziel)
        return None
    return daten


def ausfuehren(
    server_befehl: Sequence[str],
    github_befehl: Sequence[str],
    konfig: dict[str, Any] | None = None,
    *,
    cwd: Path | None = None,
    messreihe: Sequence[Messpunkt] | None = None,
    lauf_messreihe: Sequence[Messpunkt] | None = None,
    messen: Callable[[], Messpunkt] | None = None,
    lauf_messen: Callable[[], Messpunkt] | None = None,
    schlafen: Callable[[float], object] = time.sleep,
    uhr: Callable[[], float] = time.monotonic,
    verlaengern: bool = False,
    braucht_verlaengerung: Callable[[tuple[Messpunkt, ...], Grenzen], bool] | None = None,
    plattform: str | None = None,
    abbruch_frist_s: float = 5.0,
    angehaltene_pids: Sequence[int] = (),
    runde_datei: Path | None = None,
) -> LaufErgebnis:
    """Entscheidet den Ort, führt den Lauf aus, übergibt bei Kipp-Lage an GitHub und lässt Bau-Tickets weiterlaufen."""
    wahl = waehlen(
        konfig,
        messreihe=messreihe,
        messen=messen,
        schlafen=schlafen,
        uhr=uhr,
        verlaengern=verlaengern,
        braucht_verlaengerung=braucht_verlaengerung,
        plattform=plattform,
    )
    if wahl.ort == ORT_GITHUB:
        fortgesetzt = _pids_fortsetzen(angehaltene_pids, grund="direkt auf GitHub")
        log.info("Ort-Wahl: starte direkt auf GitHub (%s)", wahl.grund)
        code = _github_starten(github_befehl, cwd)
        return LaufErgebnis(
            ort=ORT_GITHUB,
            grund=wahl.grund,
            exit_code=code,
            wahl=wahl,
            abgebrochen=False,
            uebergeben=False,
            fortgesetzt_pids=fortgesetzt,
        )

    schwellen = grenzen(konfig)
    log.info("Ort-Wahl: starte großen Testlauf auf dem Bau-Server (%s)", wahl.grund)

    extra: dict[str, Any] = {"start_new_session": True} if sys.platform != "win32" else {}
    try:
        proc = starter.starte(list(server_befehl), cwd=cwd, notnagel=None, **extra)
    except BaseException:
        _pids_fortsetzen(angehaltene_pids, grund="Server-Start gescheitert")
        raise

    lauf_punkte: list[Messpunkt] = []
    reihe_iter: Iterator[Messpunkt] | None = iter(lauf_messreihe) if lauf_messreihe is not None else None
    quelle = lauf_messen or messen or messen_linux
    gekippt_grund: str | None = None
    ziel_plattform = sys.platform if plattform is None else plattform
    runde_gestartet = False
    fortgesetzt: tuple[int, ...] = ()

    try:
        if ziel_plattform == "linux":
            runde_beginnen(
                pid=os.getpid(),
                server_pid=proc.pid,
                grund=wahl.grund,
                angehaltene_pids=angehaltene_pids,
                pfad=runde_datei,
            )
            runde_gestartet = True

        with prozessbaum.gruppe_gebunden(proc) as job:
            while proc.poll() is None:
                if reihe_iter is not None:
                    try:
                        punkt = next(reihe_iter)
                    except StopIteration:
                        break
                else:
                    punkt = quelle()
                lauf_punkte.append(punkt)
                ruhig, satz = punkt_pruefen(punkt, schwellen)
                if not ruhig:
                    gekippt_grund = satz
                    log.warning(
                        "Ort-Wahl: Lage während Server-Lauf gekippt (%s) — breche Server-Lauf ab und übergebe an GitHub",
                        satz,
                    )
                    _server_abbrechen(proc, job, abbruch_frist_s)
                    break
                if proc.poll() is not None:
                    break
                if schlafen is time.sleep:
                    try:
                        proc.wait(timeout=float(schwellen.lauf_takt_s))
                        break
                    except subprocess.TimeoutExpired:
                        continue
                schlafen(float(schwellen.lauf_takt_s))

            if gekippt_grund is None:
                server_code = int(proc.wait())
                grund = f"Lauf auf dem Bau-Server abgeschlossen (Exit {server_code})"
                log.info("Ort-Wahl: %s", grund)
                if runde_gestartet:
                    fortgesetzt = runde_beenden(grund=grund, pfad=runde_datei)
                    runde_gestartet = False
                return LaufErgebnis(
                    ort=ORT_SERVER,
                    grund=grund,
                    exit_code=server_code,
                    wahl=wahl,
                    abgebrochen=False,
                    uebergeben=False,
                    lauf_messungen=tuple(lauf_punkte),
                    fortgesetzt_pids=fortgesetzt,
                )
    finally:
        # Freigabe SOFORT bei Ende, Abbruch oder vor dem GitHub-Lauf — Bau-Tickets laufen sofort weiter (#761).
        if runde_gestartet:
            ende_grund = f"Übergabe an GitHub ({gekippt_grund})" if gekippt_grund else "Server-Lauf abgebrochen/beendet"
            fortgesetzt = runde_beenden(grund=ende_grund, pfad=runde_datei)
            runde_gestartet = False

    gh_code = _github_starten(github_befehl, cwd)
    grund = f"Lage während Server-Lauf gekippt ({gekippt_grund}) — an GitHub übergeben"
    log.info("Ort-Wahl: Übergabe an GitHub abgeschlossen (Exit %d)", gh_code)
    return LaufErgebnis(
        ort=ORT_GITHUB,
        grund=grund,
        exit_code=gh_code,
        wahl=wahl,
        abgebrochen=True,
        uebergeben=True,
        lauf_messungen=tuple(lauf_punkte),
        fortgesetzt_pids=fortgesetzt,
    )


# --- Bereitschafts-Prüfung im Repo (E31/E34, #761): rein lesend -----------------


def nutzt_ort_wahl(text: str) -> bool:
    """Erkennt, ob ein Befehl oder Skript die Ort-Wahl aufruft (reine Kommentarzeilen zählen nicht)."""
    for zeile in text.splitlines():
        sauber = zeile.lstrip()
        if not sauber or sauber.startswith("#"):
            continue
        if _ORT_WAHL_AUFRUF_MUSTER.search(sauber):
            return True
    return False


def _lies_text_leise(datei: Path) -> str:
    try:
        return datei.read_text(encoding="utf-8", errors="replace")
    except OSError as fehler:
        log.warning("Ort-Wahl: Datei %s nicht lesbar: %s", datei, fehler)
        return ""


def _befehl_nutzt_ort_wahl(befehl: str, repo: Path) -> bool:
    """Prüft den Befehlstext selbst sowie darin genannte lokale Repo-Skripte (rein lesend)."""
    if nutzt_ort_wahl(befehl):
        return True
    try:
        teile = shlex.split(befehl, posix=True)
    except ValueError:
        teile = befehl.split()
    wurzel = repo.resolve()
    for teil in teile:
        kandidat = teil.strip("\"'")
        if not kandidat or kandidat.startswith("-"):
            continue
        pfad = (wurzel / kandidat).resolve()
        try:
            if not pfad.is_relative_to(wurzel) or not pfad.is_file():
                continue
        except OSError:
            continue
        if nutzt_ort_wahl(_lies_text_leise(pfad)):
            return True
    return False


def _runde_skripte_im_repo(repo: Path) -> list[Path]:
    """Findet Skripte für eine große Runde unter ``scripts/`` und ``skripte/`` (maximal eine Ebene tief)."""
    treffer: list[Path] = []
    for ordner_name in ("scripts", "skripte"):
        ordner = repo / ordner_name
        if not ordner.is_dir():
            continue
        kandidaten: list[Path] = []
        try:
            for eintrag in sorted(ordner.iterdir()):
                if eintrag.is_file():
                    kandidaten.append(eintrag)
                elif eintrag.is_dir() and not eintrag.name.startswith((".", "_")):
                    kandidaten.extend(sorted(p for p in eintrag.iterdir() if p.is_file()))
        except OSError as fehler:
            log.warning("Ort-Wahl: Skript-Ordner %s nicht lesbar: %s", ordner, fehler)
            continue
        for datei in kandidaten:
            if datei.suffix.lower() not in _SKRIPT_ENDUNGEN:
                continue
            if _RUNDE_SKRIPT_MUSTER.search(datei.stem):
                treffer.append(datei)
    return treffer


def _geplante_workflows_im_repo(repo: Path) -> list[tuple[Path, bool, bool]]:
    """Geplante GitHub-Workflows (``schedule:``): Liste aus ``(pfad, self_hosted, mit_ort_wahl)``."""
    ordner = repo / ".github" / "workflows"
    if not ordner.is_dir():
        return []
    ergebnis: list[tuple[Path, bool, bool]] = []
    try:
        dateien = sorted(p for p in ordner.iterdir() if p.is_file() and p.suffix.lower() in (".yml", ".yaml"))
    except OSError as fehler:
        log.warning("Ort-Wahl: Workflow-Ordner %s nicht lesbar: %s", ordner, fehler)
        return []
    for datei in dateien:
        inhalt = _lies_text_leise(datei)
        aktive_zeilen = [z for z in inhalt.splitlines() if z.strip() and not z.lstrip().startswith("#")]
        if not any(_SCHEDULE_MUSTER.search(z) for z in aktive_zeilen):
            continue
        aktiver_text = "\n".join(aktive_zeilen)
        ergebnis.append((datei, bool(_SELF_HOSTED_MUSTER.search(aktiver_text)), nutzt_ort_wahl(aktiver_text)))
    return ergebnis


def _hinweis_ohne_ort_wahl(quelle: str) -> str:
    return (
        f"Große Testrunde: läuft ohne Ort-Wahl ({quelle}) — über `to_spawn.py ort-wahl` "
        "einbinden, damit sie bei Last auf GitHub ausweicht (Skill baut nichts selbst um)"
    )


def pruefe_repo_runde(repo: Path, konfig: dict[str, Any] | None = None) -> RepoRundeBefund:
    """Prüft rein lesend, ob ``repo`` eine große Runde hat und ob sie die Ort-Wahl nutzt (#761, E34).

    Ändert niemals Dateien im Repo. Erkennt:
    1. ``pruefung.grosse_runde`` in ``.to-spawn/config.json``
    2. Große-Runde-Skripte in ``scripts/`` bzw. ``skripte/``
    3. ``pruefung.komplett`` in ``.to-spawn/config.json``
    4. Geplante GitHub-Workflows (``schedule:``) in ``.github/workflows/``
    """
    wurzel = repo.resolve()
    daten = konfig if isinstance(konfig, dict) else config.lade(wurzel)
    pruefung = daten.get("pruefung") if isinstance(daten, dict) else None
    pruef_block = pruefung if isinstance(pruefung, dict) else {}

    grosse_runde_cmd = str(pruef_block.get("grosse_runde") or "").strip()
    if grosse_runde_cmd:
        if _befehl_nutzt_ort_wahl(grosse_runde_cmd, wurzel):
            return RepoRundeBefund(status="mit_ort_wahl", quelle="pruefung.grosse_runde")
        return RepoRundeBefund(
            status="ohne_ort_wahl",
            quelle="pruefung.grosse_runde",
            hinweis=_hinweis_ohne_ort_wahl("pruefung.grosse_runde"),
        )

    skripte = _runde_skripte_im_repo(wurzel)
    if skripte:
        for skript in skripte:
            if nutzt_ort_wahl(_lies_text_leise(skript)):
                return RepoRundeBefund(status="mit_ort_wahl", quelle=skript.relative_to(wurzel).as_posix())
        erstes = skripte[0].relative_to(wurzel).as_posix()
        return RepoRundeBefund(
            status="ohne_ort_wahl",
            quelle=erstes,
            hinweis=_hinweis_ohne_ort_wahl(erstes),
        )

    komplett_cmd = str(pruef_block.get("komplett") or "").strip()
    if komplett_cmd:
        if _befehl_nutzt_ort_wahl(komplett_cmd, wurzel):
            return RepoRundeBefund(status="mit_ort_wahl", quelle="pruefung.komplett")
        return RepoRundeBefund(
            status="ohne_ort_wahl",
            quelle="pruefung.komplett",
            hinweis=_hinweis_ohne_ort_wahl("pruefung.komplett"),
        )

    workflows = _geplante_workflows_im_repo(wurzel)
    if workflows:
        for wf, self_hosted, mit_ort in workflows:
            rel = wf.relative_to(wurzel).as_posix()
            if not self_hosted:
                return RepoRundeBefund(status="github", quelle=rel)
            if mit_ort:
                return RepoRundeBefund(status="mit_ort_wahl", quelle=rel)
        erstes_wf = workflows[0][0].relative_to(wurzel).as_posix()
        return RepoRundeBefund(
            status="ohne_ort_wahl",
            quelle=erstes_wf,
            hinweis=_hinweis_ohne_ort_wahl(erstes_wf),
        )

    return RepoRundeBefund(
        status="fehlt",
        hinweis=(
            "Große Testrunde: im Repo nicht gefunden — große Runde anlegen und über "
            "`to_spawn.py ort-wahl` starten (Skill baut nichts selbst um)"
        ),
    )


def richte_parser_ein(unter: Any) -> None:
    """Registriert den Unterbefehl ``to_spawn.py ort-wahl`` (#760/#802)."""
    p = unter.add_parser(
        "ort-wahl",
        help="Ort-Wahl für Testläufe: lokal wenn Speicher reicht, sonst GitHub (#760/#802)",
    )
    p.add_argument("--repo", type=Path, default=None, help="Repo-Ordner für .to-spawn/config.json")
    p.add_argument(
        "--art",
        choices=("klein", "schnell", "gross", "komplett"),
        default=None,
        help="Testlauf-Art (klein/schnell oder gross/komplett): liefert lokal oder github (#802)",
    )
    p.add_argument(
        "--plattform",
        default=None,
        help="Betriebssystem für die Schwellen-Berechnung (Vorgabe: aktuelles System)",
    )
    p.add_argument(
        "--verlaengern",
        action="store_true",
        help="bei ruhigem ersten Fenster um die konfigurierte Verlängerung weiter messen",
    )
    p.add_argument(
        "--messreihe",
        type=Path,
        default=None,
        help="aufgezeichnete Messreihe (JSON/JSONL) statt Live-Messung vor dem Start",
    )
    p.add_argument(
        "--lauf-messreihe",
        type=Path,
        default=None,
        help="aufgezeichnete Messreihe (JSON/JSONL) für die Überwachung während des Server-Laufs",
    )
    p.add_argument(
        "--server-befehl",
        default=None,
        help="Befehl für den Testlauf auf dem Bau-Server (erfordert --github-befehl)",
    )
    p.add_argument(
        "--github-befehl",
        default=None,
        help="Befehl für den Testlauf bzw. die Übergabe an GitHub (erfordert --server-befehl)",
    )
    p.add_argument("--json", action="store_true", help="Ergebnis als JSON auf stdout ausgeben")


def lauf(args: argparse.Namespace, repo: Path) -> int:
    """Führt ``to_spawn.py ort-wahl`` aus — ein Befehl, ein Ergebnis auf stdout."""
    if bool(args.server_befehl) != bool(args.github_befehl):
        print("FEHLER: --server-befehl und --github-befehl müssen zusammen angegeben werden.", file=sys.stderr)
        return EXIT_AUFRUF

    ziel_repo = config.repo_wurzel(args.repo.expanduser() if args.repo else repo)
    konfig = config.lade(ziel_repo)

    try:
        vorlauf = messreihe_laden(args.messreihe.expanduser()) if args.messreihe else None
        lauf_reihe = messreihe_laden(args.lauf_messreihe.expanduser()) if args.lauf_messreihe else None
    except (OSError, TypeError, ValueError) as fehler:
        print(f"FEHLER: Messreihe nicht lesbar: {fehler}", file=sys.stderr)
        return EXIT_AUFRUF

    if args.server_befehl and args.github_befehl:
        posix = sys.platform != "win32"
        server_cmd = shlex.split(args.server_befehl, posix=posix)
        github_cmd = shlex.split(args.github_befehl, posix=posix)
        if not server_cmd or not github_cmd:
            print("FEHLER: --server-befehl und --github-befehl dürfen nicht leer sein.", file=sys.stderr)
            return EXIT_AUFRUF
        ergebnis = ausfuehren(
            server_cmd,
            github_cmd,
            konfig,
            cwd=ziel_repo,
            messreihe=vorlauf,
            lauf_messreihe=lauf_reihe,
            verlaengern=args.verlaengern,
        )
        print(json.dumps(ergebnis.als_dict(), ensure_ascii=False) if args.json else ergebnis.ort)
        return ergebnis.exit_code

    if args.art is not None:
        wahl = entscheiden(
            konfig,
            art=args.art,
            messreihe=vorlauf,
            plattform=args.plattform,
            repo=ziel_repo if args.repo is not None else None,
        )
        print(json.dumps(wahl.als_dict(), ensure_ascii=False) if args.json else wahl.ort)
        return EXIT_OK

    wahl = waehlen(konfig, messreihe=vorlauf, verlaengern=args.verlaengern, plattform=args.plattform)
    print(json.dumps(wahl.als_dict(), ensure_ascii=False) if args.json else wahl.ort)
    return EXIT_OK
