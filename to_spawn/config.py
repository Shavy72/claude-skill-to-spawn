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
import re
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import prozessbaum, skill_stand, vorgaben

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
        #: Nur der Ticket-Schnitt (#417): Planungs-Grenze je Ticket in Tausend Token, gelesen von
        #: ``manifest.pruefe_eintrag`` und ``bau_log.lernstoff``. Keine Ablöse-Grenze: die Ablösung
        #: einer laufenden Session hängt allein an ``~/.claude/smart-zone.json`` (Test #417).
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
        #: Unbeaufsichtigter Lauf (Dauerauftrag, #405): ``/to-tickets`` wertet die Zustimmung
        #: als gegeben und meldet fehlende ``Quelle:``-Zeile / ``(E<n>)`` nur als Warnung.
        "unbeaufsichtigt": False,
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
    #: Prüf-Befehle des Repos (#425), relativ zur Repo-Wurzel; leer = nicht eingerichtet.
    #: ``schnell`` = je Ticket (Stil, betroffene Tests, Rauchtest, 1–3 min); ``komplett`` = genau
    #: einmal je Spec. Welcher wann dran ist, entscheidet nur ``to_spawn.pruefplan``.
    "pruefung": {
        "schnell": "",
        "komplett": "",
    },
    #: Live-Schritt des Repos (#425); leer = nicht eingerichtet. Lesen über :func:`live_schritt`.
    "live": {
        "befehl": "",
        "url": "",
        "aufraeumen": "",
        #: ``True`` = Repo arbeitet bewusst ohne Live-Schritt: kein Einrichtungs-Ticket dafür (#452).
        "ohne": False,
    },
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


#: Konkreter ``Path``-Typ des laufenden Systems — bleibt auch bei umgebogenem ``os.name`` gültig (#232).
_SYSTEM_PFAD: type[Path] = type(Path())


class KeinHeimOrdner(RuntimeError):
    """Heimat-Ordner nicht bestimmbar (weder ``HOME`` noch ``USERPROFILE`` gesetzt, #212)."""


_KEIN_HEIM_MELDUNG = (
    "Heimat-Ordner nicht bestimmbar: weder HOME noch USERPROFILE in der Umgebung gesetzt."
)


def _ist_path_home_umgebogen() -> bool:
    """True, wenn ``Path.home`` ersetzt wurde (z. B. ``monkeypatch.setattr(Path, 'home', …)``, #212)."""
    fn = getattr(Path, "home", None)
    modul = getattr(fn, "__module__", "") or ""
    return not (modul == "pathlib" or modul.startswith("pathlib."))


def heim_ordner() -> Path:
    """Heimat-Ordner erst bei Gebrauch bestimmen — eine Tür für Windows und Linux (#212).

    Liest die Umgebung (Windows: ``USERPROFILE``, ``HOMEDRIVE``+``HOMEPATH``, ``HOME``;
    Linux/macOS: ``HOME``, ``USERPROFILE``). Fehlt alles, wirft :class:`KeinHeimOrdner`
    mit verständlicher Meldung — Windows und Linux gleich (kein Absturz beim Import und
    kein stiller Rückfall auf ``/etc/passwd``). Wurde ``Path.home`` in einem Test
    umgebogen, respektiert die Tür das und übersetzt ``RuntimeError`` in
    :class:`KeinHeimOrdner`. Nutzt :data:`_SYSTEM_PFAD`, damit auch ein umgebogenes
    ``os.name`` keinen ``UnsupportedOperation``-Fehler auslöst (#232).
    """
    if _ist_path_home_umgebogen():
        try:
            return Path.home()
        except RuntimeError as fehler:
            raise KeinHeimOrdner(_KEIN_HEIM_MELDUNG) from fehler
    if sys.platform == "win32":
        laufwerk_pfad = (
            os.environ.get("HOMEDRIVE", "").strip() + os.environ.get("HOMEPATH", "").strip()
            if os.environ.get("HOMEPATH", "").strip()
            else ""
        )
        kandidaten = (
            os.environ.get("USERPROFILE", "").strip(),
            laufwerk_pfad,
            os.environ.get("HOME", "").strip(),
        )
    else:
        kandidaten = (
            os.environ.get("HOME", "").strip(),
            os.environ.get("USERPROFILE", "").strip(),
        )
    for wert in kandidaten:
        if wert:
            return _SYSTEM_PFAD(wert)
    raise KeinHeimOrdner(_KEIN_HEIM_MELDUNG)


def expand_heim(pfad: str | Path) -> Path:
    """Löst führendes ``~`` / ``~/…`` über die eine Tür :func:`heim_ordner` auf (#212)."""
    text = os.fspath(pfad)
    if text == "~":
        return heim_ordner()
    if text.startswith(("~/", "~\\")):
        return heim_ordner() / text[2:]
    if text.startswith("~"):
        return _SYSTEM_PFAD(text).expanduser()
    return _SYSTEM_PFAD(text)


def notiz_tresor() -> Path:
    """Ort des privaten Notiz-Tresors (Obsidian-Vault): ``$OBSIDIAN_VAULT``, sonst ``~/obsidian`` (#365).

    Einzige Quelle für :mod:`to_spawn.uebergabe_sichern` und :mod:`to_spawn.befugnis`.
    """
    roh = os.environ.get("OBSIDIAN_VAULT", "").strip()
    if roh:
        return expand_heim(roh)
    return heim_ordner() / "obsidian"


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
    if os.environ.get("BAU_WT_DIR"):
        return f"{Path(os.environ['BAU_WT_DIR']).expanduser().as_posix().rstrip('/')}/wt-{ticket}"
    # Plattform-Vorgabe (#641): ``<basis>/wt-<N>``, außer der Ordner gehört einem anderen Repo —
    # dann ``<basis>/<Repo-Name>/wt-<N>`` (= ``worktree_basis_vorgabe``). Ein einmal angelegter
    # Ausweich-Ordner gewinnt immer, damit Leser ihn auch nach dem Aufräumen des anderen finden.
    # ponytail: Weiche hängt am Zustand beim ersten Aufruf; fest wäre ``worktree_basis`` je Repo.
    eigen = repo or Path.cwd()
    flach = Path("C:/dev" if sys.platform == "win32" else "~/wt").expanduser() / f"wt-{ticket}"
    ausweich = Path(worktree_basis_vorgabe(eigen)).expanduser() / f"wt-{ticket}"
    if ausweich.is_dir() or _fremder_besitzer(flach, eigen):
        return ausweich.as_posix()
    return flach.as_posix()


def _adresse_normal(url: str) -> str:
    """``host/pfad``: Host immer klein, Pfad nur bei ``github.com`` klein (dort ohne Groß-/Kleinschreibung);
    ssh-/https-Schreibweise, ``.git``, End-Schrägstrich egal. Lokaler Pfad (``C:/…``, ``/…``, UNC) wird
    wie das Betriebssystem vergleicht (``os.path.normcase``: unter Windows klein), Trenner ``/`` (#499, #508)."""
    url = url.strip().replace("\\", "/")
    if re.match(r"^([A-Za-z]:)?/", url):  # lokaler Pfad, kein ``://``/scp
        return os.path.normcase(url.rstrip("/").removesuffix(".git").rstrip("/")).replace("\\", "/")
    if "://" in url:
        url = url.split("://", 1)[1]
    elif re.match(r"^[^/@]*@?[^/:]+:(?!/)", url):  # scp-Form ``git@host:pfad``
        url = url.replace(":", "/", 1)
    url = url.split("@", 1)[-1] if "@" in url.split("/", 1)[0] else url
    host, _, pfad = url.rstrip("/").removesuffix(".git").rstrip("/").partition("/")
    host = host.lower()
    return f"{host}/{pfad.lower() if host == 'github.com' else pfad}"


def repo_kennung(ordner: Path) -> str | None:
    """Wem gehört ``ordner``? Einzige Stelle der Repo-Kennung (#457, #499, #508).

    Normalisierte ``origin``-Adresse (``https``/``ssh``/``.git``/Groß-Kleinschreibung bei
    ``github.com`` egal), ohne ``origin`` der echte gemeinsame Git-Ordner; ``None`` = kein
    Git-Ordner. Ein anderer Klon desselben Repos hat dieselbe Kennung.
    """
    return _repo_kennung(ordner)


def repo_kurzname(repo: Path) -> str:
    """Eindeutiger, tmux-tauglicher Kurzname des Repos (Kleinbuchstaben, ``[a-z0-9_-]``, #457).

    Bevorzugt den Repo-Namen aus der normalisierten ``origin``-Adresse, damit zwei Klone
    desselben Repos (z. B. Hauptklon und Spec-Klon ``~/<repo>-<S>`` oder HTTPS vs. SSH)
    denselben Session-Namen bilden. Ohne ``origin`` gilt :func:`_hauptrepo_name`.
    """
    wurzel = repo_wurzel(repo)
    roh = ""
    try:
        lauf = subprocess.run(
            ["git", "-C", str(wurzel), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **prozessbaum.fensterlos(),
        )
    except OSError as fehler:
        log.warning("git in %s nicht startbar: %s", wurzel, fehler)
    else:
        url = lauf.stdout.strip() if lauf.returncode == 0 else ""
        if url:
            normal = _adresse_normal(url)
            teile = [t for t in normal.split("/") if t]
            if teile:
                roh = teile[-1]
    if not roh:
        roh = _hauptrepo_name(wurzel)
    sauber = re.sub(r"[^a-z0-9_-]+", "-", roh.lower()).strip("-_")
    sauber = re.sub(r"-{2,}", "-", sauber)[:40].rstrip("-_")
    return sauber or "repo"


def _repo_kennung(ordner: Path) -> str | None:
    """Wem gehört ``ordner``? ``origin``-Adresse (ohne ``.git``), ohne ``origin`` der echte
    gemeinsame Git-Ordner; ``None`` = kein Git-Ordner. Ein anderer Klon desselben Repos hat
    dieselbe Kennung (dieselben Tickets) — gleiche Regel wie ``spawn_srv.sh`` für ``spec-<S>`` (#457).
    """

    def git(*args: str) -> str | None:
        try:
            lauf = subprocess.run(
                ["git", "-C", str(ordner), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                **prozessbaum.fensterlos(),
            )
        except OSError as fehler:
            log.warning("git in %s nicht startbar: %s", ordner, fehler)
            return None
        return lauf.stdout.strip() if lauf.returncode == 0 else None

    url = git("remote", "get-url", "origin")
    if url:
        return _adresse_normal(url)
    gemeinsam = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    return os.path.normcase(os.path.realpath(gemeinsam)) if gemeinsam else None


def worktree_fremd(ticket: str | int, repo: Path | None = None) -> str | None:
    """Gehört der Ordner aus :func:`worktree_pfad` schon einem anderen Repo? (#499)

    Ohne ``worktree_basis`` teilen sich zwei Repos ``~/wt/wt-<N>``, und jedes frische Repo
    beginnt bei Ticket #1. Rückgabe: eine Zeile Grund, wenn der Ordner ein Git-Worktree
    eines anderen Repos ist; sonst ``None`` (Ordner fehlt, eigenes Repo, anderer Klon
    desselben Repos, oder kein Git-Ordner — dann entscheidet der Start wie bisher).
    Laufende ``wt-<N>`` des eigenen Repos werden so weiter gefunden (kein neuer Pfad).
    Ohne ``worktree_basis``/``BAU_WT_DIR`` weicht :func:`worktree_pfad` selbst aus (#641); dann
    greift die Weigerung nur noch bei gleichem Repo-Namen. Mit gesetzter Basis verweigert sie.
    """
    if repo is None and os.environ.get("TO_SPAWN_REPO"):
        repo = Path(os.environ["TO_SPAWN_REPO"]).expanduser()
    wt = Path(worktree_pfad(ticket, repo)).expanduser()
    fremd = _fremder_besitzer(wt, repo or Path.cwd())
    if fremd is None:
        return None
    besitzer, eigen = fremd
    return (
        f"Ordner {wt} ist schon Worktree eines anderen Repos ({besitzer}), nicht {eigen} — "
        "gleiche Ticket-Nummer, gleicher Ordner. Erst das andere Ticket beenden oder in "
        "der Repo-Konfig .to-spawn/config.json ``worktree_basis`` setzen (z. B. "
        f"{worktree_basis_vorgabe(repo or Path.cwd())})."
    )


def worktree_basis_vorgabe(repo: Path) -> str:
    """Startwert für ``worktree_basis`` beim Anlegen der Konfig: ``~/wt/<Repo-Name>``
    (Windows ``C:/dev/<Repo-Name>``) — nur ein Vorschlag, die Datei darf ihn ändern."""
    name = _hauptrepo_name(repo_wurzel(repo))
    return f"C:/dev/{name}" if sys.platform == "win32" else f"~/wt/{name}"


def _hauptrepo_name(wurzel: Path) -> str:
    """Name des Hauptrepos — auch, wenn ``wurzel`` ein Worktree ist (``.git``-Datei mit
    ``gitdir: <haupt>/.git/worktrees/<x>``); sonst der Ordnername (#641)."""
    git = wurzel / ".git"
    if git.is_file():
        try:
            zeile = git.read_text(encoding="utf-8").strip()
        except OSError as fehler:
            log.warning(".git in %s nicht lesbar: %s", wurzel, fehler)
            return wurzel.name
        if zeile.startswith("gitdir:"):
            gitdir = Path(zeile.removeprefix("gitdir:").strip())
            gitdir = gitdir if gitdir.is_absolute() else wurzel / gitdir
            if gitdir.parent.name == "worktrees":
                return gitdir.parent.parent.parent.name
    return wurzel.name


def _fremder_besitzer(wt: Path, repo: Path) -> tuple[str, str] | None:
    """(Besitzer, eigene Kennung), wenn ``wt`` ein Git-Ordner eines anderen Repos ist (#499);
    sonst ``None`` (Ordner fehlt, eigenes Repo, anderer Klon, kein Git-Ordner)."""
    if not wt.is_dir():
        return None
    besitzer = _repo_kennung(wt)
    eigen = _repo_kennung(repo)
    if besitzer is None or eigen is None or besitzer == eigen:
        return None
    return besitzer, eigen


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


def _gruppe(konfig: dict[str, Any], name: str) -> dict[str, Any]:
    """Verschachtelte Gruppe ``name`` der Konfig; fehlt sie oder ist kaputt → ``{}``."""
    gruppe = konfig.get(name)
    return gruppe if isinstance(gruppe, dict) else {}


def _text(gruppe: dict[str, Any], schluessel: str) -> str:
    """Textwert ohne Rand-Leerzeichen; ``""`` = nicht eingerichtet.

    Nur echter Text zählt (#448): fehlt der Wert, ist er ``None`` oder kein Text (Liste,
    Zahl, Wahrheitswert), gilt er als nicht eingerichtet und erscheint so als Lücke im
    Einrichtungs-Ticket — statt still als ``"['pytest', '-q']"`` durchzugehen.
    """
    wert = gruppe.get(schluessel)
    if wert is None:
        return ""
    if not isinstance(wert, str):
        log.warning(
            "Konfig-Wert %r ist kein Text (%s) — gilt als nicht eingerichtet",
            schluessel,
            type(wert).__name__,
        )
        return ""
    return wert.strip()


def _staging_gruppe(konfig: dict[str, Any]) -> dict[str, Any]:
    return _gruppe(konfig, "staging")


def pruef_befehl(konfig: dict[str, Any], art: str) -> str:
    """Befehl ``pruefung.<art>`` (``schnell``/``komplett``, #425); ``""`` = nicht eingerichtet.

    Für :mod:`to_spawn.pruefplan` und :mod:`to_spawn.einrichtungs_ticket` (#452) — andere
    Aufrufer fragen :func:`to_spawn.pruefplan.befehl`.
    """
    return _text(_gruppe(konfig, "pruefung"), art)


#: Vorgabe für ``pruefung.komplett_zeitgrenze_min`` (#455): so lange darf die Komplett-Prüfung
#: höchstens laufen, dann bricht der Skill sie samt Unterprozessen ab (rot, Grund „Zeitgrenze“).
#: Bewusst nicht in ``DEFAULTS["pruefung"]`` — dort stehen nur die Befehle, die eine Einrichtung ausmachen.
KOMPLETT_ZEITGRENZE_MIN = 60


def komplett_zeitgrenze_s(konfig: dict[str, Any]) -> float:
    """Zeitgrenze der Komplett-Prüfung in Sekunden aus ``pruefung.komplett_zeitgrenze_min`` (#455).

    Fehlt der Wert oder ist er keine positive Zahl, gilt :data:`KOMPLETT_ZEITGRENZE_MIN`.
    """
    wert = _gruppe(konfig, "pruefung").get("komplett_zeitgrenze_min")
    if isinstance(wert, (int, float)) and not isinstance(wert, bool) and wert > 0:
        return float(wert) * 60
    if wert is not None:
        log.warning(
            "Konfig-Wert pruefung.komplett_zeitgrenze_min=%r ungültig — Vorgabe %s min",
            wert,
            KOMPLETT_ZEITGRENZE_MIN,
        )
    return float(KOMPLETT_ZEITGRENZE_MIN * 60)


#: Text-Schlüssel der Gruppe ``live`` (#425); ``ohne`` ist ein Schalter (:func:`live_bewusst_ohne`).
_LIVE_TEXT_FELDER: tuple[str, ...] = ("befehl", "url", "aufraeumen")


def live_schritt(konfig: dict[str, Any]) -> dict[str, str]:
    """Text-Felder der Gruppe ``live`` (#425: ``befehl``, ``url``, ``aufraeumen``); ``""`` = nicht eingerichtet.

    Für den Schalter ``live.ohne`` gibt es :func:`live_bewusst_ohne`.
    """
    gruppe = _gruppe(konfig, "live")
    return {schluessel: _text(gruppe, schluessel) for schluessel in _LIVE_TEXT_FELDER}


def live_bewusst_ohne(konfig: dict[str, Any]) -> bool:
    """``live.ohne: true`` — das Repo arbeitet bewusst ohne Live-Schritt (#452)."""
    return _gruppe(konfig, "live").get("ohne") is True


def staging_bewusst_ohne(konfig: dict[str, Any]) -> bool:
    """``staging.ohne: true`` — das Repo arbeitet bewusst ohne Staging-App (#410)."""
    return _staging_gruppe(konfig).get("ohne") is True


def hat_staging(konfig: dict[str, Any]) -> bool:
    """Hat dieses Repo eine Staging-App? Einzige Stelle dieser Regel (#410, #425).

    Ja = ``staging.url`` oder ``staging_start`` ist gesetzt und das Repo arbeitet nicht
    bewusst ohne Staging (:func:`staging_bewusst_ohne` gewinnt).
    """
    if staging_bewusst_ohne(konfig):
        return False
    url = str(_staging_gruppe(konfig).get("url") or "").strip()
    return bool(url or str(konfig.get("staging_start") or "").strip())


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


def ist_skill_slug(slug: str) -> bool:
    """``True``, wenn ``slug`` zu den eigenen Skill-Slugs (:data:`OEFFENTLICHE_SLUGS`) zählt."""
    return slug.strip().lower() in {s.lower() for s in OEFFENTLICHE_SLUGS}


def repo_klon_pfad(eintrag: dict[str, str], *, skill: Path | None = None) -> Path | None:
    """Aufgelöster Klon-Ordner für einen ``repos[]``-Eintrag der Maschinen-Liste (#707).

    Einzige Stelle für die Reihenfolge „Maschinen-Liste vor ``STAND.json``“:
    1. Existiert ``eintrag["pfad"]`` als Ordner, gilt er (Maschinen-Liste gewinnt).
    2. Fehlt er bei einem Skill-Eintrag (:data:`OEFFENTLICHE_SLUGS`), liefert
       :func:`skill_stand.klon_ordner` den echten Klon aus ``STAND.json`` (``klon``).
    3. Existiert keiner der beiden, bleibt der konfigurierte Pfad (falls gesetzt)
       erhalten, damit Aufrufer ihn klar loggen können; leer ergibt ``None``.
    """
    roh = (eintrag.get("pfad") or "").strip()
    pfad = expand_heim(roh) if roh else None
    if pfad is not None and pfad.is_dir():
        return pfad
    if ist_skill_slug(eintrag.get("slug") or ""):
        aus_stempel = skill_stand.klon_ordner(skill)
        if aus_stempel is not None:
            return aus_stempel
    return pfad


def maschine_datei() -> Path:
    """Ort der Maschinen-Liste: ``TO_SPAWN_MASCHINE``, sonst ``~/.config/to-spawn/maschine.json``."""
    roh = os.environ.get("TO_SPAWN_MASCHINE")
    if roh:
        return expand_heim(roh)
    return heim_ordner() / ".config" / "to-spawn" / "maschine.json"


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


def _schreibe_maschine(datei: Path, daten: dict[str, Any]) -> None:
    """Schreibt die Maschinen-Liste atomar (temporäre Datei + ``os.replace``)."""
    datei.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".maschine-", suffix=".tmp", dir=datei.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as strom:
            strom.write(json.dumps(daten, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, datei)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise


#: Gültige Form eines GitHub-Ticket-Repos ``<besitzer>/<name>`` (#261).
_TICKET_REPO_MUSTER = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class TicketRepoAenderung:
    """Ergebnis einer ``ticket_repo``-Änderung an einem ``repos[]``-Eintrag (#261)."""

    slug: str
    pfad: str
    vorher: str
    nachher: str

    @property
    def geaendert(self) -> bool:
        return self.vorher != self.nachher

    def zeile(self) -> str:
        alt = self.vorher or "(leer)"
        neu = self.nachher or "(leer)"
        if not self.geaendert:
            return f"{self.slug} ({self.pfad}): ticket_repo unverändert ({alt})"
        return f"{self.slug} ({self.pfad}): ticket_repo {alt} → {neu}"


def _veraltete_ticket_repo_ziele(daten: dict[str, Any]) -> list[tuple[dict[str, str], TicketRepoAenderung]]:
    """Findet Einträge in ``daten["repos"]`` mit bekannter Alt-Vorgabe (``vorgaben.VERALTETE_TICKET_REPOS``)."""
    soll_je_slug = {
        e["slug"].lower(): e["ticket_repo"]
        for e in vorgaben.REPOS
        if e.get("slug") and e.get("ticket_repo")
    }
    alt_je_slug = {
        slug.lower(): {wert.lower() for wert in werte}
        for slug, werte in vorgaben.VERALTETE_TICKET_REPOS.items()
    }
    treffer: list[tuple[dict[str, str], TicketRepoAenderung]] = []
    for eintrag in daten.get("repos", []):
        if not isinstance(eintrag, dict):
            continue
        slug = (eintrag.get("slug") or "").strip()
        schluessel = slug.lower()
        soll = soll_je_slug.get(schluessel, "")
        alte = alt_je_slug.get(schluessel, set())
        ist = (eintrag.get("ticket_repo") or "").strip()
        if soll and ist.lower() in alte and ist != soll:
            treffer.append(
                (
                    eintrag,
                    TicketRepoAenderung(
                        slug=slug,
                        pfad=str(eintrag.get("pfad") or ""),
                        vorher=ist,
                        nachher=soll,
                    ),
                )
            )
    return treffer


def veraltete_ticket_repos(daten: dict[str, Any] | None = None) -> list[TicketRepoAenderung]:
    """Liest veraltete Vorgabe-Werte von ``ticket_repo`` in der Maschinen-Liste (nur lesend, #261).

    Fehlt die Datei oder ist sie unlesbar, liefert sie leer (keine stillschweigende Migration).
    """
    if daten is None:
        datei = maschine_datei()
        if not datei.is_file() or _lies(datei)[0] is None:
            return []
        daten = maschine()
    return [aenderung for _, aenderung in _veraltete_ticket_repo_ziele(daten)]


def maschine_ticket_repos_nachziehen() -> list[TicketRepoAenderung]:
    """Zieht bekannte veraltete Vorgabe-Werte von ``ticket_repo`` in der Maschinen-Liste nach (#261).

    Wiederholbar: fehlt die Datei oder ist alles aktuell, wird nichts geschrieben. Eine kaputte
    Datei wird nie überschrieben (Warnung im Log). Bewusst abweichend gesetzte Werte bleiben (#542).
    """
    datei = maschine_datei()
    if not datei.is_file():
        return []
    if _lies(datei)[0] is None:
        log.warning("Maschinen-Liste unlesbar (%s) — nicht überschrieben", datei)
        return []
    daten = maschine()
    treffer = _veraltete_ticket_repo_ziele(daten)
    if not treffer:
        return []
    aenderungen: list[TicketRepoAenderung] = []
    for eintrag, aenderung in treffer:
        eintrag["ticket_repo"] = aenderung.nachher
        aenderungen.append(aenderung)
    _schreibe_maschine(datei, daten)
    for aenderung in aenderungen:
        log.info("Maschinen-Liste nachgezogen: %s (%s)", aenderung.zeile(), datei)
    return aenderungen


def maschine_ticket_repo_setzen(ziel: str | Path, ticket_repo: str) -> list[TicketRepoAenderung]:
    """Setzt ``ticket_repo`` für passende Einträge in der Maschinen-Liste ausdrücklich (#261).

    ``ziel`` trifft per ``slug`` (Groß-/Kleinschreibung egal) oder per Ordner-``pfad``.
    ``ticket_repo`` muss die Form ``<besitzer>/<name>`` haben. Wiederholbar: stimmt der Wert
    schon (und die Datei existiert), wird nicht neu geschrieben.
    """
    wert = ticket_repo.strip()
    if not _TICKET_REPO_MUSTER.fullmatch(wert):
        raise ValueError(
            f"ticket_repo ungültig ({ticket_repo!r}) — erwartet wird <besitzer>/<name>"
        )
    datei = maschine_datei()
    if datei.is_file() and _lies(datei)[0] is None:
        raise ValueError(f"Maschinen-Liste unlesbar ({datei}) — nicht überschrieben")
    daten = maschine()
    ziel_text = str(ziel).strip()
    ziel_klein = ziel_text.lower()
    try:
        ziel_pfad: Path | None = Path(ziel_text).expanduser().resolve()
    except (OSError, ValueError):
        ziel_pfad = None

    treffer: list[dict[str, str]] = []
    for eintrag in daten["repos"]:
        if (eintrag.get("slug") or "").strip().lower() == ziel_klein or (
            ziel_pfad is not None and _gleicher_ort(eintrag.get("pfad") or "", ziel_pfad)
        ):
            treffer.append(eintrag)
    if not treffer:
        raise LookupError(f"Kein Eintrag in {datei} für {ziel_text!r}")

    aenderungen: list[TicketRepoAenderung] = []
    geaendert = False
    for eintrag in treffer:
        vorher = (eintrag.get("ticket_repo") or "").strip()
        aenderung = TicketRepoAenderung(
            slug=eintrag.get("slug") or "",
            pfad=eintrag.get("pfad") or "",
            vorher=vorher,
            nachher=wert,
        )
        if aenderung.geaendert:
            eintrag["ticket_repo"] = wert
            geaendert = True
        aenderungen.append(aenderung)

    if geaendert or not datei.is_file():
        _schreibe_maschine(datei, daten)
        for aenderung in aenderungen:
            log.info("Maschinen-Liste geändert: %s (%s)", aenderung.zeile(), datei)
    return aenderungen


def maschine_eintragen(repo: Path | None = None, ticket_repo: str = "") -> dict[str, str]:
    """Trägt das Repo in ``repos[]`` der Maschinen-Liste ein und gibt seinen Eintrag zurück.

    ``ticket_repo`` (Standard ``""``) setzt nur den Wert eines neuen bzw. noch leeren Eintrags;
    ein vorhandener Wert wird nie überschrieben. Der Einrichter übergibt ihn nur mit Beleg (#542).

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
        "ticket_repo": ticket_repo,  # nur mit Beleg (Einrichter, #542); Standard bleibt leer
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
    _schreibe_maschine(datei, daten)
    log.info("Maschinen-Liste: %s eingetragen (%s)", wurzel, datei)
    return eintrag


#: Erlaubte Werte für ``rolle`` in der Maschinen-Liste (``maschine.json``, #408).
_SERVER_ROLLEN = frozenset({"server", "bau-server", "bau_server"})
_PC_ROLLEN = frozenset({"pc", "laptop", "lokal", "local"})
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def ssh_ziel(konfig: dict[str, Any] | None = None) -> str:
    """SSH-Ziel des Bau-Servers: ``TO_SPAWN_SSH_ZIEL`` gewinnt, sonst Konfig ``ssh_ziel`` oder Vorgabe."""
    umg = os.environ.get("TO_SPAWN_SSH_ZIEL", "").strip()
    if umg:
        return umg
    wert = str((konfig or {}).get("ssh_ziel") or "").strip()
    return wert or str(DEFAULTS["ssh_ziel"])


def ist_bau_server(konfig: dict[str, Any] | None = None) -> bool:
    """Läuft der Skill auf dem Bau-Server selbst? Einzige Stelle dieser Prüfung (#408).

    Reihenfolge:
    1. Maschinen-Liste (``maschine.json``): ``rolle`` (``"server"``/``"bau-server"`` → True,
       ``"pc"``/``"laptop"``/``"lokal"`` → False) oder ``bau_server`` (Bool) gewinnt immer.
    2. Hostname (``socket.gethostname()``) passt zu ``ssh_ziel`` (klein, ohne ``nutzer@``,
       bei blankem Namen ohne Punkt auch der Kurzname vor dem ersten Punkt).
    """
    daten = maschine()
    rolle = daten.get("rolle")
    if isinstance(rolle, str) and rolle.strip():
        normal = rolle.strip().lower()
        if normal in _SERVER_ROLLEN:
            return True
        if normal in _PC_ROLLEN:
            return False
        log.warning("Maschinen-Liste: unbekannte rolle %r — Hostname entscheidet", rolle)
    schalter = daten.get("bau_server")
    if isinstance(schalter, bool):
        return schalter
    try:
        host = socket.gethostname().strip().lower()
    except OSError:
        return False
    if not host:
        return False
    ziel_host = ssh_ziel(konfig).strip().lower().rsplit("@", 1)[-1]
    if not ziel_host or ziel_host in _LOOPBACK_HOSTS:
        return False
    return host == ziel_host or ("." not in ziel_host and host.split(".", 1)[0] == ziel_host)
