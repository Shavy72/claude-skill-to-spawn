"""Repo-Konfiguration ``.to-spawn/config.json`` und Maschinen-Liste — die eine Tür (#46).

Fehlt die Datei, gelten die Vorgaben unten — der Skill läuft also in jedem Repo
ohne Setup. Vorhandene Felder überschreiben die Vorgaben (verschachtelt).

Schnittstelle für die Aufrufer (L1 Etappe 1, Pakete P1–P5):

- ``lade(repo) -> dict``: Repo-Konfig mit Vorgaben aufgefüllt. Neue Gruppen:
  ``pfade.{manifeste,bau_log,agenten}``, ``deploy.skripte`` (Liste ``name``/``art``/
  ``pruef_schalter``/``fern``), ``staging.schalter``, ``befehle.wirkungskreis``,
  ``vps.staging_log``.
- ``pfad(konfig, name) -> Path``: ein ``pfade.<name>``-Ordner als Pfad unter der
  Repo-Wurzel (absolute Werte und ``~`` gelten wie eingetragen).
- ``maschine() -> dict``: Maschinen-Liste ``~/.config/to-spawn/maschine.json``
  (Env ``TO_SPAWN_MASCHINE`` lenkt um) mit ``repos[]`` (``slug``, ``pfad``,
  ``ticket_repo``, ``worktree_basis``), ``staging_sperren[]`` und
  ``hausmeister.{wurzeln,ordner_praefixe}``, ``temp_ordner`` — Dinge, die je Maschine gelten, nicht je Repo.
  ``hausmeister.geschuetzte_ordner`` (Liste von Ordnernamen) schützt nur zusätzlich zu
  ``HAUSMEISTER_GESCHUETZTE_ORDNER`` — nie Vorgabe-Ersatz, darum nicht in ``MASCHINE_DEFAULTS``.
- ``maschine_eintragen(repo) -> dict``: trägt das Repo in die Maschinen-Liste ein
  (wiederholbar ohne Schaden, atomar geschrieben), Rückgabe = sein Eintrag.

Regeln: fehlender Schlüssel = Vorgabe = heutiges Verhalten, nie Abbruch; fehlende oder
kaputte Datei = Vorgaben; leerer Wert = „ableiten“ (wie ``hauptzweig``). Die heutigen
DuoPlus-Werte stehen nur in ``to_spawn/vorgaben.py`` — kein anderes Modul importiert sie.
Env-Nähte der Aufrufer (``TO_SPAWN_HAUSMEISTER_*``, ``TO_SPAWN_STAGING_SPERRE``) behalten
Vorrang vor der Maschinen-Liste; das prüft jeder Aufrufer selbst.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import vorgaben

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
    "terminal": {plattform: adapter[0] for plattform, adapter in TERMINAL_ADAPTER.items()},
    "ssh_ziel": "bau-server",
    #: Repo-Ordner auf dem Bau-Server (leer = ``~/<Name des Repo-Ordners>``), für den Umzug (#212).
    "server_repo": "",
    "runner": "claude",
    "modelle": {
        "ticket": "claude-opus-5-5",
        "ticket_leicht": "claude-sonnet-5",
        "waechter": "claude-opus-5-5",
        #: Modell, auf das der Aufseher beim Nutzungs-Limit wechselt (#213).
        "waechter_ausweich": "claude-sonnet-5",
    },
    "effort": {
        "ticket": "medium",
        "ticket_leicht": "low",
        "waechter": "medium",
    },
    "staffel": {
        "modus": "eltern",
        "grenze_k": 240,
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
        #: Ordner der Belegseiten, den der Aufseher je Ticket prüft (#213).
        "belege_ordner": "docs/verify-hard",
    },
    #: Aufseher (#213): Remote Control an, verwaist ab so vielen Stunden ohne Spur.
    "waechter": {
        "remote_control": True,
        "verwaist_stunden": 3,
    },
    #: Ordner mit den Ticket-Worktrees ``wt-<N>`` für die Aufseher-Regel verwaist
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
        #: ``True`` = Repo arbeitet bewusst ohne Staging: kein Einrichtungs-Ticket dafür (#410).
        "ohne": False,
        #: Schalter-Skript im App-Repo, relativ zur Repo-Wurzel (L1 #46).
        "schalter": vorgaben.STAGING_SCHALTER,
    },
    #: Ordner im App-Repo, relativ zur Repo-Wurzel; Zugriff über :func:`pfad` (L1 #46).
    "pfade": dict(vorgaben.PFADE),
    #: Deploy-Skripte für den Aufpasser: ``name``, ``art`` ("live"/"pruef"),
    #: ``pruef_schalter`` (macht "live" zu "pruef", "" = keiner), ``fern`` (nur per ssh) (L1 #46).
    "deploy": {
        "skripte": copy.deepcopy(vorgaben.DEPLOY_SKRIPTE),
    },
    #: App-Befehle, die der Skill aufruft, relativ zur Repo-Wurzel (L1 #46).
    "befehle": {
        "wirkungskreis": vorgaben.BEFEHL_WIRKUNGSKREIS,
    },
    #: VPS-Regel des Aufsehers; ``ssh`` fehlt in der Vorgabe → Regel aus (L1 #46: nur das Staging-Log).
    "vps": {
        "staging_log": vorgaben.STAGING_LOG,
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
    #: Startklar-Prüfung (#450): Schlüssel-NAMEN, die jede Spec dieses Repos braucht
    #: (zusätzlich zu Manifest-Feld ``schluessel`` und Treffern aus ``.env.example``).
    "startklar": {
        "schluessel": [],
    },
    #: Start-Text (#274 P1): ``zusatz_datei`` = Repo-Datei (relativ zur Wurzel), deren Text als
    #: Abschnitt „Regeln dieses Repos“ in den Skill-Start-Text kommt. Leer = kein Zusatz.
    "start_text": {
        "zusatz_datei": "",
    },
    "speicher": {
        "min_frei_mib": 2048,
        "max_sessions": 6,
        "staffel_s": 20,
    },
}

KONFIG_PFAD = Path(".to-spawn") / "config.json"

#: Vorgaben der Maschinen-Liste (L1 #46): je Maschine, nicht je Repo.
MASCHINE_DEFAULTS: dict[str, Any] = {
    "repos": copy.deepcopy(vorgaben.REPOS),
    "staging_sperren": list(vorgaben.STAGING_SPERREN),
    "hausmeister": {
        "wurzeln": list(vorgaben.HAUSMEISTER_WURZELN),
        "ordner_praefixe": list(vorgaben.HAUSMEISTER_ORDNER_PRAEFIXE),
    },
    # #136: Temp-Ordner auf der Platte (to_spawn/temp_ordner.py)
    "temp_ordner": vorgaben.TEMP_ORDNER,
}
#: Ordnernamen, die der Hausmeister immer stehen lässt; ``hausmeister.geschuetzte_ordner`` ergänzt nur.
HAUSMEISTER_GESCHUETZTE_ORDNER: tuple[str, ...] = tuple(vorgaben.HAUSMEISTER_GESCHUETZTE_ORDNER)
#: Öffentliche Repos (Slugs), in die die Übergabe-Sicherung nie schreibt.
OEFFENTLICHE_SLUGS: tuple[str, ...] = tuple(vorgaben.OEFFENTLICHE_SLUGS)
#: Felder eines Eintrags in ``repos[]``; fehlende Felder gelten als "" (= ableiten).
REPO_FELDER = ("slug", "pfad", "ticket_repo", "worktree_basis")


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
    """Wohin der Worktree eines Tickets gehört — unter ``$BAU_WT_DIR``, sonst Windows ``C:/dev``, Linux ``~/wt``.

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
    # Gesetztes BAU_WT_DIR gilt auf jeder Plattform (Tests/Leiter/Aufpasser auf dem PC
    # sähen sonst C:/dev statt des vorgegebenen Ordners); ohne es die Plattform-Vorgabe.
    basis = os.environ.get("BAU_WT_DIR") or ("C:/dev" if sys.platform == "win32" else "~/wt")
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


class Konfig(dict):  # type: ignore[type-arg]
    """Geladene Konfiguration: ein normales ``dict``, das zusätzlich seine Datei kennt.

    ``datei`` = die ``.to-spawn/config.json``, aus der :func:`lade` gelesen hat (auch
    wenn sie fehlte und nur Vorgaben gelten). Über sie liest :func:`frisch` neu ein —
    lange Warteschleifen sehen so Änderungen, ohne dass ein Aufrufer einen Pfad kennt.
    Gleichheit, JSON und ``dict(…)`` verhalten sich wie beim normalen ``dict``. Werte, die
    ein Aufrufer im Speicher ändert, ersetzt :func:`frisch` wieder durch die Datei.
    """

    def __init__(self, werte: dict[str, Any], datei: Path | None = None) -> None:
        super().__init__(werte)
        self.datei = datei
        #: Warnung „Datei kaputt“ schon geschrieben — einmal je kaputter Strecke, nicht je Zyklus.
        self.kaputt_gemeldet = False


def _lies(datei: Path) -> tuple[dict[str, Any] | None, str]:
    """(Inhalt, Fehler) der Konfig-Datei; Inhalt ``None`` = unlesbar, kaputt oder kein Objekt."""
    try:
        eigen = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        return None, str(fehler)
    if not isinstance(eigen, dict):
        return None, "kein Objekt"
    return eigen, ""


def lade(repo: Path | None = None) -> dict[str, Any]:
    """Konfiguration des Repos, mit Vorgaben aufgefüllt (eine :class:`Konfig`)."""
    wurzel = repo_wurzel(repo)
    datei = wurzel / KONFIG_PFAD
    if not datei.is_file():
        return Konfig(DEFAULTS, datei)
    eigen, fehler = _lies(datei)
    if eigen is None:
        log.warning("Konfig unlesbar (%s) — Vorgaben gelten: %s", datei, fehler)
        return Konfig(DEFAULTS, datei)
    return Konfig(_mische(DEFAULTS, eigen), datei)


def frisch(konfig: dict[str, Any] | None) -> dict[str, Any] | None:
    """Dieselbe Konfiguration, jetzt neu von der Platte gelesen — für lange Warteschleifen.

    Nur eine :class:`Konfig` aus :func:`lade` kennt ihre Datei; jedes andere ``dict``
    (auch ``None``) kommt unverändert zurück. Sonst gilt dasselbe wie bei :func:`lade`
    (fehlende Datei oder fehlende Felder → Vorgaben) — mit einer Ausnahme: ist die Datei
    unlesbar oder kein gültiges JSON-Objekt (z. B. halb gespeichert), gelten die bisherigen
    Werte weiter, mit einer Warnung im Log je kaputter Strecke. Eine Warteschleife stürzt so
    nie an der Konfig ab und springt bei einem Tippfehler nicht auf die Vorgaben.
    """
    datei = konfig.datei if isinstance(konfig, Konfig) else None
    if datei is None:
        return konfig
    if not datei.is_file():
        return Konfig(DEFAULTS, datei)
    eigen, fehler = _lies(datei)
    if eigen is None:
        if not konfig.kaputt_gemeldet:
            log.warning("Konfig unlesbar (%s) — bisherige Werte gelten weiter: %s", datei, fehler)
            konfig.kaputt_gemeldet = True
        return konfig
    return Konfig(_mische(DEFAULTS, eigen), datei)


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
    modus = os.environ.get("TO_SPAWN_STAFFEL_MODUS") or str(konfig.get("staffel", {}).get("modus", "eltern"))
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


def _wurzel_von(konfig: dict[str, Any]) -> Path:
    """Repo-Wurzel einer Konfig: aus ihrer Datei, sonst ``TO_SPAWN_REPO``, sonst der aktuelle Ordner."""
    datei = konfig.datei if isinstance(konfig, Konfig) else None
    if datei is not None:
        return datei.parent.parent
    roh = os.environ.get("TO_SPAWN_REPO")
    return repo_wurzel(Path(roh).expanduser() if roh else None)


def pfad(konfig: dict[str, Any], name: str) -> Path:
    """Ordner ``pfade.<name>`` als Pfad unter der Repo-Wurzel (L1 #46).

    ``name`` ist einer aus ``DEFAULTS["pfade"]`` (``manifeste``, ``bau_log``, ``agenten``);
    ein anderer Name ist ein Programmierfehler (``KeyError``). Fehlt der Wert oder ist er
    leer bzw. kein Text, gilt die Vorgabe. ``~`` und absolute Werte gelten wie eingetragen.
    """
    vorgabe = DEFAULTS["pfade"][name]
    gruppe = konfig.get("pfade")
    wert = gruppe.get(name) if isinstance(gruppe, dict) else None
    text = wert.strip() if isinstance(wert, str) and wert.strip() else vorgabe
    return _wurzel_von(konfig) / Path(text).expanduser()


def maschine_datei() -> Path:
    """Ort der Maschinen-Liste: ``TO_SPAWN_MASCHINE``, sonst ``~/.config/to-spawn/maschine.json``."""
    roh = os.environ.get("TO_SPAWN_MASCHINE")
    if roh:
        return Path(roh).expanduser()
    return Path.home() / ".config" / "to-spawn" / "maschine.json"


def _repo_eintrag(roh: Any) -> dict[str, str] | None:
    """Ein ``repos[]``-Eintrag mit allen Feldern als Text, ``None`` = unbrauchbar (kein Objekt)."""
    if not isinstance(roh, dict):
        return None
    return {feld: str(roh.get(feld) or "") for feld in REPO_FELDER}


def maschine() -> dict[str, Any]:
    """Maschinen-Liste, mit Vorgaben aufgefüllt (L1 #46) — nie Abbruch.

    Fehlt die Datei oder ist sie kaputt, gelten ``MASCHINE_DEFAULTS`` (Warnung im Log bei
    kaputt). Jeder fehlende Schlüssel gilt einzeln als Vorgabe; eine vorhandene Liste
    ersetzt die Vorgabe-Liste ganz (keine Vereinigung). ``repos[]``-Einträge haben immer
    alle Felder aus ``REPO_FELDER``; Einträge, die kein Objekt sind, fallen weg.
    """
    datei = maschine_datei()
    eigen: dict[str, Any] = {}
    if datei.is_file():
        gelesen, fehler = _lies(datei)
        if gelesen is None:
            log.warning("Maschinen-Liste unlesbar (%s) — Vorgaben gelten: %s", datei, fehler)
        else:
            eigen = gelesen
    ergebnis = _mische(copy.deepcopy(MASCHINE_DEFAULTS), eigen)
    for schluessel, vorgabe in MASCHINE_DEFAULTS.items():
        if not isinstance(ergebnis.get(schluessel), type(vorgabe)):
            ergebnis[schluessel] = copy.deepcopy(vorgabe)
    for schluessel, vorgabe in MASCHINE_DEFAULTS["hausmeister"].items():
        if not isinstance(ergebnis["hausmeister"].get(schluessel), list):
            ergebnis["hausmeister"][schluessel] = list(vorgabe)
    ergebnis["repos"] = [e for e in map(_repo_eintrag, ergebnis["repos"]) if e is not None]
    return ergebnis


def _gleicher_ort(a: str, b: Path) -> bool:
    """``a`` (Text aus der Liste, ``~`` erlaubt) zeigt auf denselben Ordner wie ``b``."""
    try:
        return Path(a).expanduser().resolve() == b
    except (OSError, ValueError):
        return False


def maschine_eintragen(repo: Path | None = None) -> dict[str, str]:
    """Trägt das Repo in ``repos[]`` der Maschinen-Liste ein und gibt seinen Eintrag zurück.

    Schlüssel ist der Ordner (``pfad``). Neu: ``slug`` aus ``origin``, ``worktree_basis`` aus
    der Repo-Konfig, ``ticket_repo`` leer (= ableiten). Zeigt ``origin`` nicht auf ein
    GitHub-Repo (Wegwerf-Klon mit lokalem Pfad, #112), bleibt die Liste unberührt: Warnung,
    Rückgabe des Eintrags ohne Schreiben. Schon da: nur leere Felder werden
    ergänzt, Eingetragenes bleibt. Fehlt die Datei, sind die Vorgaben die Basis — die
    Vorgabe-Repos bleiben also erhalten. Geschrieben wird nur bei Änderung, atomar
    (temporäre Datei + ``os.replace``). Eine kaputte Datei wird nie überschrieben: dann
    Warnung und Rückgabe des Eintrags ohne Schreiben. Ein Schreibfehler (``OSError``)
    geht an den Aufrufer (Einrichter), die alte Datei bleibt dabei unberührt.
    """
    from . import gh  # spät: gh importiert config

    wurzel = repo_wurzel(repo)
    datei = maschine_datei()
    neu = {
        "slug": gh.github_slug_aus_origin(wurzel),
        "pfad": wurzel.as_posix(),
        "ticket_repo": "",
        "worktree_basis": str(lade(wurzel).get("worktree_basis") or ""),
    }
    if not neu["slug"]:
        log.warning(
            "Maschinen-Liste: %s nicht eingetragen — origin ist kein GitHub-Repo <besitzer>/<name>",
            wurzel,
        )
        return neu
    if datei.is_file() and _lies(datei)[0] is None:
        log.warning("Maschinen-Liste unlesbar (%s) — nicht überschrieben", datei)
        return neu
    daten = maschine()
    for eintrag in daten["repos"]:
        if _gleicher_ort(eintrag["pfad"], wurzel):
            fehlend = {k: v for k, v in neu.items() if v and not eintrag[k]}
            if not fehlend and datei.is_file():
                return eintrag
            eintrag.update(fehlend)
            break
    else:
        eintrag = neu
        daten["repos"].append(eintrag)
    datei.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".maschine-", suffix=".tmp", dir=datei.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as strom:
            strom.write(json.dumps(daten, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, datei)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise
    log.info("Maschinen-Liste: %s eingetragen (%s)", wurzel, datei)
    return eintrag
