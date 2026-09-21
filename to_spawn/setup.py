"""Setup-Wizard (#208): Terminal, Modell und Effort je Rolle wählen.

Ändert NUR ``.to-spawn/config.json`` — startet und beendet keine Prozesse.
Geschrieben wird atomar (tmp-Datei im selben Ordner + ``os.replace``), damit
eine laufende Session nie eine halbe Datei liest. Alle übrigen Felder der
vorhandenen Datei bleiben erhalten, auch unbekannte.

Die Wächter-Rolle läuft immer auf dem Flaggschiff-Modell; der Wizard fragt dort
nur den Effort.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from . import config, context_mode

log = logging.getLogger("to_spawn.setup")

FLAGGSCHIFF = "claude-fable-5-1"

#: (Modell-ID, Klartext-Name) in Anzeige-Reihenfolge.
MODELLE: list[tuple[str, str]] = [
    ("claude-fable-5-1", "Fable 5.1"),
    ("claude-opus-5", "Opus 5"),
    ("claude-sonnet-5", "Sonnet 5"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5"),
]
EFFORTS: list[str] = ["low", "medium", "high", "xhigh", "max"]

#: Rolle → Klartext für die Fragen.
ROLLEN: dict[str, str] = {
    "ticket": "Ticket-Session",
    "ticket_leicht": "leichtes Ticket",
    "waechter": "Wächter",
}

SCHLUSS_SATZ = "Laufende Sessions bleiben unberührt; neue Starts lesen die Werte."
ERSTES_MAL = (
    "Erstes Mal in diesem Repo: `to_spawn.py setup` passt Terminal/Modell/Effort an."
)

Finde = Callable[[str], "str | None"]
PruefeStart = Callable[[str, str], "str | None"]
Eingabe = Callable[[str], str]


class KonfigUnlesbar(ValueError):
    """Vorhandene Konfig-Datei ist kein lesbares JSON-Objekt — nicht überschreiben."""


@dataclass(frozen=True)
class Option:
    """Eine Terminal-Option, wie der Wizard sie zeigt."""

    schluessel: str
    name: str
    vorteil: str
    status: str  # "bereit" | "nicht installiert" | "geplant"
    grund: str = ""

    @property
    def waehlbar(self) -> bool:
        return self.status != "geplant"


@dataclass(frozen=True)
class _Terminal:
    schluessel: str
    name: str
    vorteil: str
    plattformen: tuple[str, ...]
    programm: str | None  # None = geplant, kein Adapter
    version_arg: str | None  # None = Fund reicht


_ALLE = ("win32", "linux", "darwin")
_TERMINALS: list[_Terminal] = [
    _Terminal(
        "wt",
        "Windows Terminal",
        "Tabs je Ticket in einem Fenster, auf Windows schon da",
        ("win32",),
        "wt",
        None,
    ),
    _Terminal(
        "tmux",
        "tmux",
        "läuft auf dem Server weiter, auch wenn der Laptop zu ist",
        ("linux", "darwin"),
        "tmux",
        "-V",
    ),
    _Terminal(
        "herdr",
        "Herdr",
        "Übersicht über viele Agenten-Sitzungen im Terminal",
        ("linux",),
        None,
        None,
    ),
    _Terminal(
        "cmux",
        "cmux",
        "Mac-Terminal, das meldet, wenn ein Agent fertig ist",
        ("darwin",),
        None,
        None,
    ),
    _Terminal(
        "pane", "Pane", "gleiche Bedienung auf allen Systemen", _ALLE, None, None
    ),
    _Terminal(
        "wezterm",
        "WezTerm",
        "ein Terminal für Windows, Linux und Mac mit eingebauten Tabs",
        _ALLE,
        None,
        None,
    ),
]


plattform_von = config.plattform_von


def remote_hinweis(konfig: dict[str, Any]) -> str:
    """Remote-Control-Zeile so, wie ``wache`` startet (Konfig ``waechter.remote_control``, #213)."""
    an = bool(konfig.get("waechter", {}).get("remote_control", True))
    return f"Remote Control (nur Wächter, per Claude-App erreichbar): {'an' if an else 'aus'}"


def pruefe_start(programm: str, pfad: str) -> str | None:
    """Kurzer Versionsaufruf. Rückgabe: Grund, falls das Programm nicht startet."""
    arg = next((t.version_arg for t in _TERMINALS if t.programm == programm), None)
    if arg is None:
        return None
    try:
        ergebnis = subprocess.run(
            [pfad, arg], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError) as fehler:
        return f"{programm} {arg} startet nicht: {fehler}"
    if ergebnis.returncode != 0:
        return f"{programm} {arg} endet mit Exit {ergebnis.returncode}"
    return None


def terminal_optionen(
    plattform: str | None = None,
    finde: Finde = shutil.which,
    pruefe_start: PruefeStart = pruefe_start,
) -> list[Option]:
    """Terminal-Optionen dieser Plattform, V1-Adapter zuerst, mit Live-Prüfung."""
    system = plattform_von(plattform)
    optionen: list[Option] = []
    for terminal in _TERMINALS:
        if system not in terminal.plattformen:
            continue
        if terminal.programm is None:
            optionen.append(
                Option(terminal.schluessel, terminal.name, terminal.vorteil, "geplant")
            )
            continue
        pfad = finde(terminal.programm)
        if not pfad:
            status, grund = "nicht installiert", f"{terminal.programm} nicht gefunden"
        else:
            fehler = pruefe_start(terminal.programm, pfad)
            status, grund = ("nicht installiert", fehler) if fehler else ("bereit", "")
        if grund:
            log.info("Terminal %s: %s", terminal.schluessel, grund)
        optionen.append(
            Option(terminal.schluessel, terminal.name, terminal.vorteil, status, grund)
        )
    return optionen


def waehlbare_terminals(plattform: str | None = None) -> list[str]:
    """Schlüssel der V1-Adapter dieser Plattform (ohne Live-Prüfung)."""
    system = plattform_von(plattform)
    return [
        t.schluessel
        for t in _TERMINALS
        if system in t.plattformen and t.programm is not None
    ]


def standard_terminal(konfig: dict[str, Any], plattform: str | None = None) -> str:
    """Aktueller Konfig-Wert dieser Plattform, falls hier wählbar, sonst der erste V1-Adapter."""
    moeglich = waehlbare_terminals(plattform)
    aktuell = config.terminal_fuer(konfig, plattform)
    return aktuell if aktuell in moeglich else moeglich[0]


def _rollen_wert(konfig: dict[str, Any], feld: str, rolle: str) -> str:
    wert = konfig.get(feld, {})
    if isinstance(wert, dict) and isinstance(wert.get(rolle), str) and wert[rolle]:
        return str(wert[rolle])
    return str(config.DEFAULTS[feld][rolle])


def modell_name(modell: str) -> str:
    return next((name for mid, name in MODELLE if mid == modell), modell)


def standards(konfig: dict[str, Any], plattform: str | None = None) -> dict[str, Any]:
    """Alle Standard-Antworten (= aktuelle Werte der Konfig), Wächter = Flaggschiff."""
    modelle = {r: _rollen_wert(konfig, "modelle", r) for r in ROLLEN}
    modelle["waechter"] = FLAGGSCHIFF
    return {
        "terminal": standard_terminal(konfig, plattform),
        "modelle": modelle,
        "effort": {r: _rollen_wert(konfig, "effort", r) for r in ROLLEN},
    }


class _Eingabeende(Exception):
    """stdin ist zu — ab hier gelten die Standards."""


def _frage(
    titel: str,
    eintraege: list[tuple[str, str]],
    standard: str,
    eingabe: Eingabe,
    ausgabe: TextIO,
    zusatz: list[str] | None = None,
) -> str:
    """Nummerierte Liste, Enter = Standard, 3 Fehlversuche → Standard.

    ``eintraege`` = (Wert, Anzeige). Eingabe als Nummer oder Wert selbst.
    """
    if standard not in [wert for wert, _ in eintraege]:
        eintraege = [*eintraege, (standard, f"{standard} (eigener Wert)")]
    ausgabe.write(f"\n{titel}\n")
    for nummer, (wert, anzeige) in enumerate(eintraege, start=1):
        marke = " [Standard]" if wert == standard else ""
        ausgabe.write(f"  {nummer}) {anzeige}{marke}\n")
    ausgabe.writelines(f"  {zeile}\n" for zeile in zusatz or [])
    ausgabe.flush()
    for _ in range(3):
        try:
            antwort = eingabe("Auswahl (Enter = Standard): ").strip()
        except EOFError as ende:
            raise _Eingabeende from ende
        if not antwort:
            return standard
        if antwort.isdigit() and 1 <= int(antwort) <= len(eintraege):
            return eintraege[int(antwort) - 1][0]
        for wert, _anzeige in eintraege:
            if antwort.lower() == wert.lower():
                return wert
        ausgabe.write(f"  Ungültig: {antwort!r} — bitte eine Nummer aus der Liste.\n")
        ausgabe.flush()
    ausgabe.write(f"  Drei ungültige Antworten — Standard {standard} gilt.\n")
    return standard


def _terminal_zeile(option: Option) -> str:
    zeile = f"{option.name} — {option.status} ({option.vorteil})"
    if option.grund:
        zeile += f" · {option.grund}"
    return zeile


def fuehre_dialog(
    konfig: dict[str, Any],
    eingabe: Eingabe,
    ausgabe: TextIO,
    plattform: str | None = None,
    finde: Finde = shutil.which,
    pruefe_start: PruefeStart = pruefe_start,
) -> dict[str, Any]:
    """Fragt Terminal → Ticket → leichtes Ticket → Wächter (nur Effort).

    Rückgabe: ``{"terminal", "modelle", "effort"}`` für :func:`speichere`.
    EOF auf der Eingabe = ab dort alle Standards.
    """
    vorgabe = standards(konfig, plattform)
    ergebnis = copy.deepcopy(vorgabe)
    optionen = terminal_optionen(plattform, finde=finde, pruefe_start=pruefe_start)
    waehlbar = [o for o in optionen if o.waehlbar]
    geplant = [f"–  {_terminal_zeile(o)}" for o in optionen if not o.waehlbar]
    std_option = next(o for o in waehlbar if o.schluessel == vorgabe["terminal"])
    if std_option.status != "bereit":
        geplant.append(
            f"Achtung: Standard {std_option.name} ist hier {std_option.status}"
            f"{' (' + std_option.grund + ')' if std_option.grund else ''}."
        )
    geplant.append(remote_hinweis(konfig))
    geplant.append(context_mode.hinweis())
    modell_liste = [(mid, f"{name} ({mid})") for mid, name in MODELLE]
    effort_liste = [(e, e) for e in EFFORTS]

    ausgabe.write(f"Setup to-spawn — Plattform {plattform_von(plattform)}\n")
    try:
        ergebnis["terminal"] = _frage(
            "Terminal:",
            [(o.schluessel, _terminal_zeile(o)) for o in waehlbar],
            vorgabe["terminal"],
            eingabe,
            ausgabe,
            geplant,
        )
        for rolle in ("ticket", "ticket_leicht"):
            ergebnis["modelle"][rolle] = _frage(
                f"Modell für {ROLLEN[rolle]}:",
                modell_liste,
                vorgabe["modelle"][rolle],
                eingabe,
                ausgabe,
            )
            ergebnis["effort"][rolle] = _frage(
                f"Effort für {ROLLEN[rolle]}:",
                effort_liste,
                vorgabe["effort"][rolle],
                eingabe,
                ausgabe,
            )
        ausgabe.write(
            f"\nWächter läuft immer auf {modell_name(FLAGGSCHIFF)} ({FLAGGSCHIFF}), "
            "gefragt wird nur der Effort.\n"
        )
        ergebnis["effort"]["waechter"] = _frage(
            "Effort für Wächter:",
            effort_liste,
            vorgabe["effort"]["waechter"],
            eingabe,
            ausgabe,
        )
    except _Eingabeende:
        ausgabe.write("\nEingabe beendet — für den Rest gelten die Standards.\n")
    ausgabe.flush()
    ergebnis["modelle"]["waechter"] = FLAGGSCHIFF
    return ergebnis


def lies_roh(datei: Path) -> dict[str, Any] | None:
    """Vorhandene Datei roh lesen (ohne Vorgaben). ``None`` = Datei fehlt."""
    if not datei.exists():
        return None
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        raise KonfigUnlesbar(f"Konfig unlesbar: {datei} ({fehler})") from fehler
    if not isinstance(daten, dict):
        raise KonfigUnlesbar(f"Konfig unlesbar: {datei} ist kein JSON-Objekt")
    return daten


def _schreibe_atomar(datei: Path, daten: dict[str, Any]) -> None:
    datei.parent.mkdir(parents=True, exist_ok=True)
    # Rechte der alten Datei übernehmen (mkstemp legt 0600 an), neu = 0644.
    try:
        modus = stat.S_IMODE(datei.stat().st_mode)
    except FileNotFoundError:
        modus = 0o644
    griff, tmp = tempfile.mkstemp(
        prefix=".config-", suffix=".tmp", dir=str(datei.parent)
    )
    try:
        with os.fdopen(griff, "w", encoding="utf-8") as strom:
            strom.write(json.dumps(daten, indent=2, ensure_ascii=False) + "\n")
            strom.flush()
            os.fsync(strom.fileno())
        try:
            os.chmod(tmp, modus)
        except OSError as fehler:
            log.warning("Rechte %o nicht gesetzt (%s): %s", modus, tmp, fehler)
        os.replace(tmp, datei)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _terminal_objekt(alt: Any) -> dict[str, Any]:
    """``terminal`` als Objekt je Plattform; alter Text bleibt, wo er Adapter ist."""
    if isinstance(alt, dict):
        return dict(alt)
    objekt: dict[str, Any] = dict(config.DEFAULTS["terminal"])
    if isinstance(alt, str):
        for system, adapter in config.TERMINAL_ADAPTER.items():
            if alt in adapter:
                objekt[system] = alt
    elif alt is not None:
        log.warning(
            "Feld terminal ist weder Text noch Objekt — Standards je Plattform."
        )
    return objekt


def speichere(
    repo: Path | None, aenderungen: dict[str, Any], plattform: str | None = None
) -> Path:
    """Setzt nur ``terminal.<plattform>``, ``modelle.*``, ``effort.*`` — der Rest bleibt.

    Fehlt die Datei, sind die Vorgaben die Basis. Eine unlesbare Datei wird nie
    überschrieben (:class:`KonfigUnlesbar`). Wächter-Modell = immer Flaggschiff.
    ``modelle``/``effort``, die kein Objekt sind, werden (mit Warnung) neu angelegt.
    """
    datei = config.repo_wurzel(repo) / config.KONFIG_PFAD
    roh = lies_roh(datei)
    daten = copy.deepcopy(config.DEFAULTS) if roh is None else roh
    if "terminal" in aenderungen:
        objekt = _terminal_objekt(daten.get("terminal"))
        objekt[plattform_von(plattform)] = aenderungen["terminal"]
        daten["terminal"] = objekt
    for feld in ("modelle", "effort"):
        neu = aenderungen.get(feld) or {}
        ziel = daten.get(feld)
        if ziel is not None and not isinstance(ziel, dict):
            log.warning("Feld %s ist kein Objekt — wird neu angelegt.", feld)
            ziel = {}
            daten[feld] = ziel
        if not neu and feld != "modelle":
            continue
        if ziel is None:
            ziel = {}
        ziel.update({rolle: wert for rolle, wert in neu.items() if rolle in ROLLEN})
        daten[feld] = ziel
    if daten["modelle"].get("waechter") != FLAGGSCHIFF:
        log.info("Wächter-Modell auf Flaggschiff %s gesetzt.", FLAGGSCHIFF)
    daten["modelle"]["waechter"] = FLAGGSCHIFF
    _schreibe_atomar(datei, daten)
    log.info("Konfig gespeichert: %s", datei)
    return datei


def zeige(
    konfig: dict[str, Any], datei: Path, plattform: str | None = None, **pruef: Any
) -> str:
    """Optionen + Werte als Text (für den Skill, der dann nachfragt).

    „In der Datei“ = Rohwerte, „Standard“ = was Enter im Dialog nimmt.
    Unlesbare Datei → :class:`KonfigUnlesbar`.
    """
    roh = lies_roh(datei)
    vorgabe = standards(konfig, plattform)
    zeilen = [
        f"Setup to-spawn — Plattform {plattform_von(plattform)}",
        f"Konfig: {datei}{'' if datei.exists() else ' (fehlt noch)'}",
        "",
        "Terminal (--terminal):",
    ]
    for option in terminal_optionen(plattform, **pruef):
        marke = " [Standard]" if option.schluessel == vorgabe["terminal"] else ""
        wahl = option.schluessel if option.waehlbar else "nicht wählbar"
        zeilen.append(f"  {wahl}: {_terminal_zeile(option)}{marke}")
    zeilen.append(f"  {remote_hinweis(konfig)}")
    zeilen.append(f"  {context_mode.hinweis()}")
    zeilen += ["", "Modelle (--modell-ticket, --modell-leicht):"]
    zeilen += [f"  {mid}: {name}" for mid, name in MODELLE]
    zeilen += [
        "",
        f"Effort (--effort-ticket, --effort-leicht, --effort-waechter): {', '.join(EFFORTS)}",
    ]
    zeilen += ["", "In der Datei:"]
    if roh is None:
        zeilen.append("  fehlt (Datei gibt es noch nicht)")
    else:
        terminal = roh.get("terminal")
        zeilen.append(
            "  terminal: "
            + (
                "fehlt"
                if terminal is None
                else json.dumps(terminal, ensure_ascii=False)
            )
        )
        for rolle, klartext in ROLLEN.items():
            teile = []
            for feld in ("modelle", "effort"):
                wert = roh.get(feld)
                eintrag = wert.get(rolle) if isinstance(wert, dict) else None
                teile.append(f"{feld} {'fehlt' if eintrag is None else eintrag}")
            zeilen.append(f"  {rolle} ({klartext}): {', '.join(teile)}")
    zeilen += ["", "Standard (Enter im Dialog):", f"  terminal: {vorgabe['terminal']}"]
    for rolle, klartext in ROLLEN.items():
        modell = vorgabe["modelle"][rolle]
        fest = ", fest" if rolle == "waechter" else ""
        zeilen.append(
            f"  {rolle} ({klartext}): {modell_name(modell)} ({modell}{fest}), "
            f"Effort {vorgabe['effort'][rolle]}"
        )
    return "\n".join(zeilen)


def erster_start(
    repo: Path,
    *,
    ist_tty: bool,
    eingabe: Eingabe,
    ausgabe: TextIO,
    plattform: str | None = None,
    finde: Finde = shutil.which,
    pruefe_start: PruefeStart = pruefe_start,
) -> Path:
    """Vor ``spawn``: fehlt die Konfig, im Terminal den Dialog fahren, sonst Vorgaben.

    Ist die Datei schon da, passiert nichts.
    """
    datei = config.repo_wurzel(repo) / config.KONFIG_PFAD
    if datei.exists():
        return datei
    if ist_tty:
        aenderungen = fuehre_dialog(
            config.lade(repo), eingabe, ausgabe, plattform, finde, pruefe_start
        )
        datei = speichere(repo, aenderungen, plattform)
        ausgabe.write(f"Gespeichert: {datei}\n")
        ausgabe.flush()
        return datei
    datei = config.sicherstellen(repo)
    ausgabe.write(ERSTES_MAL + "\n")
    ausgabe.flush()
    return datei


def stdin_eingabe(ausgabe: TextIO) -> Eingabe:
    """Eingabe-Funktion über ``sys.stdin``: leere Datei-Ende → EOFError."""

    def eingabe(frage: str) -> str:
        ausgabe.write(frage)
        ausgabe.flush()
        zeile = sys.stdin.readline()
        if zeile == "":
            ausgabe.write("\n")
            raise EOFError
        return zeile.rstrip("\r\n")

    return eingabe
