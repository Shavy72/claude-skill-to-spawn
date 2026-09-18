"""Repo-Konfiguration ``.to-spawn/config.json`` mit Vorgabewerten.

Fehlt die Datei, gelten die Vorgaben unten — der Skill läuft also in jedem Repo
ohne Setup. Vorhandene Felder überschreiben die Vorgaben (verschachtelt).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("to_spawn.config")

#: Terminal-Adapter (V1) je Plattform; der erste ist der Standard. Die Konfig
#: ist eingecheckt und wird zwischen Windows und Server geteilt, darum steht
#: ``terminal`` je Plattform in einem Objekt.
TERMINAL_ADAPTER: dict[str, tuple[str, ...]] = {
    "win32": ("wt",),
    "linux": ("tmux",),
    "darwin": ("tmux",),
}

#: Vorgabewerte. ``staffel.modus`` = "eltern", weil ein Stop-Hook den
#: Claude-Prozess laut Doku nicht beenden kann (siehe README, Abschnitt Staffel).
DEFAULTS: dict[str, Any] = {
    "ziel_default": "srv",
    "terminal": {
        plattform: adapter[0] for plattform, adapter in TERMINAL_ADAPTER.items()
    },
    "ssh_ziel": "bau-server",
    "runner": "claude",
    "modelle": {
        "ticket": "claude-opus-5",
        "ticket_leicht": "claude-sonnet-5",
        "waechter": "claude-fable-5-1",
        #: Modell, auf das der Wächter beim Nutzungs-Limit wechselt (#213).
        "waechter_ausweich": "claude-opus-5",
    },
    "effort": {
        "ticket": "medium",
        "ticket_leicht": "low",
        "waechter": "low",
    },
    "staffel": {
        "modus": "eltern",
        "grenze_k": 200,
        "max_staffeln": 3,
    },
    "mail": {
        "ziel": "",
        "nur_kritisch": True,
        #: Versand-Befehl als argv-Liste, JSON auf stdin (leer = kein Versand, nur Log) (#213).
        "befehl": [],
    },
    "regularien": {
        "checkpoint_label": "checkpoint:human",
        #: Ordner der Belegseiten, den der Wächter je Ticket prüft (#213).
        "belege_ordner": "docs/verify-hard",
    },
    #: Wächter (#213): Remote Control an, verwaist ab so vielen Stunden ohne Spur.
    "waechter": {
        "remote_control": True,
        "verwaist_stunden": 3,
    },
    #: Ordner mit den Ticket-Worktrees ``wt-<N>`` für die Wächter-Regel verwaist
    #: (leer = Regel von ``worktree_pfad``: Windows ``C:/dev``, sonst ``$BAU_WT_DIR`` bzw. ``~/wt``) (#213).
    "wt_basis": "",
    #: Befehl, der die Staging-Umgebung startet (leer = keine Staging-Stufe).
    "staging_start": "",
    #: Deploy-Befehl des Repos (leer = Repo deployt nicht über den Skill).
    "deploy_befehl": "",
}

KONFIG_PFAD = Path(".to-spawn") / "config.json"


def _mische(vorgabe: dict[str, Any], eigen: dict[str, Any]) -> dict[str, Any]:
    """Verschachtelte Vereinigung: Werte aus ``eigen`` gewinnen."""
    ergebnis = dict(vorgabe)
    for schluessel, wert in eigen.items():
        alt = ergebnis.get(schluessel)
        if isinstance(alt, dict) and isinstance(wert, dict):
            ergebnis[schluessel] = _mische(alt, wert)
        else:
            ergebnis[schluessel] = wert
    return ergebnis


def repo_wurzel(start: Path | None = None) -> Path:
    """Nächstgelegener Ordner mit ``.git`` ab ``start`` (sonst ``start`` selbst)."""
    pfad = (start or Path.cwd()).resolve()
    for kandidat in [pfad, *pfad.parents]:
        if (kandidat / ".git").exists():
            return kandidat
    return pfad


def worktree_pfad(ticket: str | int) -> str:
    """Wohin der Worktree eines Tickets gehört — Windows ``C:/dev``, Linux unter ``$BAU_WT_DIR``.

    Einzige Stelle dieser Regel: ``bau.py`` (Start + Bau-Log-Ziel) und
    ``sessions_stand.py`` (Token-Spalte) lesen sie von hier (#204).
    """
    if sys.platform == "win32":
        return f"C:/dev/wt-{ticket}"
    basis = os.environ.get("BAU_WT_DIR") or "~/wt"
    return f"{Path(basis).expanduser().as_posix().rstrip('/')}/wt-{ticket}"


def lade(repo: Path | None = None) -> dict[str, Any]:
    """Konfiguration des Repos, mit Vorgaben aufgefüllt."""
    wurzel = repo_wurzel(repo)
    datei = wurzel / KONFIG_PFAD
    if not datei.is_file():
        return dict(DEFAULTS)
    try:
        eigen = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        log.warning("Konfig unlesbar (%s) — Vorgaben gelten: %s", datei, fehler)
        return dict(DEFAULTS)
    if not isinstance(eigen, dict):
        log.warning("Konfig ist kein Objekt (%s) — Vorgaben gelten.", datei)
        return dict(DEFAULTS)
    return _mische(DEFAULTS, eigen)


def sicherstellen(repo: Path | None = None) -> Path:
    """Legt ``.to-spawn/config.json`` mit den Vorgaben an, falls sie fehlt.

    Eine vorhandene Datei bleibt unangetastet (auch wenn sie unlesbar ist).
    Rückgabe: Pfad der Konfig-Datei.
    """
    datei = repo_wurzel(repo) / KONFIG_PFAD
    if datei.exists():
        return datei
    datei.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Modus "x": zwei gleichzeitige Starts überschreiben sich nie gegenseitig.
        with datei.open("x", encoding="utf-8") as strom:
            strom.write(json.dumps(DEFAULTS, indent=2, ensure_ascii=False) + "\n")
    except FileExistsError:
        return datei
    log.info("Konfig angelegt: %s (Vorgaben, bitte anpassen)", datei)
    return datei


def staffel_modus(konfig: dict[str, Any]) -> str:
    """ "hook" oder "eltern" — Umgebungsvariable ``TO_SPAWN_STAFFEL_MODUS`` gewinnt."""
    modus = os.environ.get("TO_SPAWN_STAFFEL_MODUS") or str(
        konfig.get("staffel", {}).get("modus", "eltern")
    )
    return modus if modus in ("hook", "eltern") else "eltern"


def plattform_von(plattform: str | None = None) -> str:
    """win32 / linux / darwin — unbekannte Systeme zählen als linux."""
    wert = plattform or sys.platform
    if wert.startswith("win"):
        return "win32"
    if wert == "darwin":
        return "darwin"
    return "linux"


def terminal_fuer(konfig: dict[str, Any], plattform: str | None = None) -> str:
    """Terminal dieser Plattform aus ``terminal`` (Objekt je Plattform oder alter Text).

    Alter Text gilt nur auf der Plattform, auf der er ein Adapter ist; sonst und
    bei fehlendem/kaputtem Wert gilt der Plattform-Standard.
    """
    system = plattform_von(plattform)
    standard = TERMINAL_ADAPTER[system][0]
    wert = konfig.get("terminal")
    if isinstance(wert, dict):
        eintrag = wert.get(system)
        return eintrag if isinstance(eintrag, str) and eintrag else standard
    if isinstance(wert, str) and wert in TERMINAL_ADAPTER[system]:
        return wert
    return standard
