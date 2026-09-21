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
    #: Repo-Ordner auf dem Bau-Server (leer = ``~/<Name des Repo-Ordners>``), für den Umzug (#212).
    "server_repo": "",
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
    #: Basis der Ticket-Worktrees für alle Starter (#257): leer = altes Verhalten (Windows
    #: ``C:/dev``, sonst ``$BAU_WT_DIR`` bzw. ``~/wt``); ``sicherstellen`` trägt beim ersten
    #: Anlegen ``~/wt/<Repo-Name>`` ein, damit zwei Repos sich nie ``wt-<N>`` teilen.
    "worktree_basis": "",
    #: Hauptzweig des Repos (leer = automatisch: origin/HEAD, sonst master/main) (#257).
    "hauptzweig": "",
    #: Befehl, der die Staging-Umgebung startet (leer = keine Staging-Stufe).
    "staging_start": "",
    #: Deploy-Befehl des Repos (leer = Repo deployt nicht über den Skill).
    "deploy_befehl": "",
    #: Staging-Nest (#211) für den Probesitz (#214): ``url`` = Adresse, ``zugang_datei`` = Datei mit
    #: ``nutzer=…``/``passwort=…`` (oder ``user:pass``); leer = Punkt 3 bleibt rot mit „fehlt noch“.
    "staging": {
        "url": "",
        "zugang_datei": "",
    },
    #: Sandbox je Worktree (#210), Opt-in je Repo: "an" = Session läuft in ``srt``.
    #: ``pflicht`` (bei "an"): fehlt srt/bwrap oder scheitert der Worktree → kein Start;
    #: false → Warnung, Session ohne Sandbox. ``netz_zusatz`` = weitere Ziele (``host[:port]``).
    "sandbox": {
        "modus": "aus",
        "pflicht": True,
        "netz_zusatz": [],
    },
    #: Nest-Bau (#210): ``bws_projekt`` = Projekt-ID im Bitwarden Secrets Manager (leer = alle).
    "nest": {
        "bws_projekt": "",
    },
    #: Speicher-Schutz (#257 Paket B): kein neuer Claude-Start, wenn weniger als
    #: ``min_frei_mib`` MiB frei sind oder schon ``max_sessions`` Claude-Prozesse laufen.
    #: Richtwert: 6 Sessions je 16 GB RAM (Bau-Server, OOM-Absturz 21.09.). ``staffel_s`` =
    #: Pause in Sekunden zwischen zwei Fenster-Starts, damit die Starts sich nicht stapeln.
    "speicher": {
        "min_frei_mib": 2048,
        "max_sessions": 6,
        "staffel_s": 20,
    },
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


def worktree_pfad(ticket: str | int, repo: Path | None = None) -> str:
    """Wohin der Worktree eines Tickets gehört — Windows ``C:/dev``, Linux unter ``$BAU_WT_DIR``.

    Steht ``worktree_basis`` in der Repo-Konfig (#257), gilt sie auf jeder Plattform
    (``~`` wird aufgelöst). Einzige Stelle dieser Regel: ``bau.py`` (Start + Bau-Log-Ziel),
    ``sessions_stand.py`` (Token-Spalte) und ``capo`` lesen sie von hier (#204).
    ``repo`` = Repo-Wurzel (sonst ``TO_SPAWN_REPO`` bzw. der aktuelle Ordner).
    """
    if repo is None and os.environ.get("TO_SPAWN_REPO"):
        repo = Path(os.environ["TO_SPAWN_REPO"]).expanduser()
    basis = str(lade(repo).get("worktree_basis") or "").strip()
    if basis:
        return f"{Path(basis).expanduser().as_posix().rstrip('/')}/wt-{ticket}"
    if sys.platform == "win32":
        return f"C:/dev/wt-{ticket}"
    basis = os.environ.get("BAU_WT_DIR") or "~/wt"
    return f"{Path(basis).expanduser().as_posix().rstrip('/')}/wt-{ticket}"


def worktree_basis_vorgabe(repo: Path) -> str:
    """Startwert für ``worktree_basis`` beim Anlegen der Konfig: ``~/wt/<Repo-Name>``
    (Windows ``C:/dev/<Repo-Name>``) — nur ein Vorschlag, die Datei darf ihn ändern."""
    name = repo_wurzel(repo).name
    return f"C:/dev/{name}" if sys.platform == "win32" else f"~/wt/{name}"


#: Zeilen, die ``sicherstellen`` in die Repo-``.gitignore`` schreibt: Laufdateien und
#: Marker unter ``.to-spawn/`` bleiben unversioniert, nur die Konfig wird eingecheckt (#257).
GITIGNORE_BLOCK = (
    "# to-spawn: Laufdateien/Marker unversioniert, nur die Konfig eingecheckt",
    ".to-spawn/*",
    "!.to-spawn/config.json",
)


def gitignore_ergaenzen(wurzel: Path) -> bool:
    """Block aus ``GITIGNORE_BLOCK`` idempotent an ``.gitignore`` anhängen (``True`` = geschrieben)."""
    datei = wurzel / ".gitignore"
    try:
        vorhanden = datei.read_text(encoding="utf-8") if datei.is_file() else ""
    except OSError as fehler:
        log.warning(".gitignore unlesbar (%s) — nicht ergänzt: %s", datei, fehler)
        return False
    zeilen = {zeile.strip() for zeile in vorhanden.splitlines()}
    fehlend = [zeile for zeile in GITIGNORE_BLOCK[1:] if zeile not in zeilen]
    if not fehlend:
        return False
    block = "\n".join(GITIGNORE_BLOCK) + "\n"
    trenner = "" if not vorhanden or vorhanden.endswith("\n") else "\n"
    try:
        with datei.open("a", encoding="utf-8") as strom:
            strom.write(f"{trenner}{block}")
    except OSError as fehler:
        log.warning(".gitignore nicht schreibbar (%s): %s", datei, fehler)
        return False
    log.info(".gitignore ergänzt: %s", datei)
    return True


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

    Beim Anlegen bekommt ``worktree_basis`` den Vorschlag ``~/wt/<Repo-Name>`` (#257).
    Eine vorhandene Datei bleibt byte-gleich (Plan B8, #257): fehlende Felder liefert
    ``lade`` beim Lesen aus ``DEFAULTS``, nichts wird nachgetragen. Dazu der
    ``.gitignore``-Block für ``.to-spawn/`` (idempotent). Rückgabe: Pfad der Konfig-Datei.
    """
    wurzel = repo_wurzel(repo)
    datei = wurzel / KONFIG_PFAD
    gitignore_ergaenzen(wurzel)
    if datei.exists():
        return datei
    datei.parent.mkdir(parents=True, exist_ok=True)
    inhalt = dict(DEFAULTS)
    inhalt["worktree_basis"] = worktree_basis_vorgabe(wurzel)
    try:
        # Modus "x": zwei gleichzeitige Starts überschreiben sich nie gegenseitig.
        with datei.open("x", encoding="utf-8") as strom:
            strom.write(json.dumps(inhalt, indent=2, ensure_ascii=False) + "\n")
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
