"""Capo: der Regel-Prüfer des Bau-Aufsehers (duoplus-management#213).

Ein Tick liest GitHub (Sub-Issues der Spec), ``origin/<Hauptzweig>``, die
Ticket-Worktrees und die Bau-Logs — und prüft fünf Regeln:

1. ``commit_ohne_nummer`` — Ticket zu, aber kein Commit mit ``(#N)`` am Betreff-Ende
   oder in Scope-Form ``typ(#N): …`` (außer ``docs(#N):`` — das sind Nachträge).
2. ``beweis_fehlt`` — Ticket zu, aber keine Belegseite mit der Nummer im Namen
   (reine Doku-Tickets brauchen keine: die Doku ist der Beleg).
3. ``test_ersetzt`` — ein Ticket-Commit hat eine Testfunktion/Testdatei entfernt
   (nur echte Testdateien; es gibt keine Ausnahme, Tests werden nie entfernt).
4. ``session_verwaist`` (kritisch) — Ticket offen + zugewiesen, aber seit Stunden keine Spur
   (nicht bei Checkpoint-Label oder wenn die jüngste Bau-Log-Zeile ``blockiert`` ist).
5. ``vps_ungleich_origin`` — Deploy-Ticket zu, aber sein Commit ist nicht auf dem VPS.

Verstöße 1, 2, 3 und 5 öffnen das Ticket jedes Mal wieder, wenn es erneut mit
demselben Verstoß geschlossen wird (Entscheidung 21.09.: Spec Zeile 13 kennt keine
Ausnahme; je Schließen bleibt es bei einer Wieder-Öffnung). Keine Regeln für Tickets
„nicht geplant“/„Duplikat“. Label ``waechter:ok`` hebt nur das Wieder-Öffnen auf —
der Verstoß wird trotzdem erkannt und im Tick-Bericht genannt, zählt aber wie der
Ausgangsstand als „alt“ und hält „Spec fertig“ nicht auf. Nach dem Wieder-Öffnen
startet capo die Folge-Runde (#284); ein Nacht-Checkpoint gilt nach der Frist mit
dem Vorschlag der Session als angenommen, der Mensch kann kippen (#285). Zeitgrenzen:
beim ersten Tick einer Spec sind alle geschlossenen Tickets Ausgangsstand (nur
melden; maßgeblich ist, ob das Ticket beim ersten Tick schon zu war — kein
Vergleich der GitHub-Uhr mit der lokalen Uhr), danach wird ein Schließen erst
15 min später geprüft (Karenz, ``waechter.karenz_minuten``; ``0`` = keine Karenz).
``TO_SPAWN_WAECHTER_SOFORT=1`` bzw. ``waechter.sofort`` schaltet beide Zeitgrenzen ab.
Regel 4 kommentiert nur und meldet per Mail (je Tag einmal). Dazu kommt das
Bau-Log-Delta (nur neue Zeilen seit dem letzten Tick, aus dem Hauptzweig und den
Laufdateien ``.to-spawn/bau_log/<N>.jsonl``) und die Entscheidungs-Übersicht.
Der Zustand liegt in ``~/.claude/to-spawn/waechter/<owner>_<name>_<S>.json``, eine
Sperre daneben (``….json.lock``) hält parallele Läufe fern.
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import io
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import (
    auswahl,
    bau_log,
    befund,
    briefkasten,
    config,
    fehlermeldung,
    gh,
    komplett_lauf,
    konsole,
    melder,
    mensch_noetig,
    schliess_pruefer,
    sitzung_env,
    staging_schalter,
    starter,
    ticket_zustand,
    vorfall,
)
from . import sperre as _sperre
from .pruefplan import Moment
from . import prozessbaum

log = logging.getLogger("to_spawn.capo")

#: Bau-Log-Typen, die im Delta nur gezählt, nicht einzeln gezeigt werden (Hook-Rauschen).
LEISE_TYPEN = frozenset({"session_ende", "subagent_ende"})
#: Werte von ``status``/``phase``/``ergebnis`` einer ``deploy_phase``, die „Gate rot“ heißen.
ROT_WERTE = frozenset({"rot", "fail", "failed", "abbruch", "fehler"})
MAX_DELTA_ZEILEN = 8
#: Label am Ticket: der Mensch hat bewusst freigegeben — Regeln überspringen (E2).
OK_LABEL = "waechter:ok"
#: Minuten nach ``closed_at``, bevor die Regeln greifen (Vorgabe, ``waechter.karenz_minuten``).
KARENZ_MIN = 15.0
#: Tick-Zeile, wenn ``mail.befehl`` fehlt: bewusste Wahl des Repos, kein Fehler.
MAIL_AUS = "INFO: Mail nicht eingerichtet (mail.befehl leer) — Meldungen stehen nur hier."
#: Sekunden, die ein Tick auf die Sperre eines anderen Laufs wartet.
SPERRE_S = 30.0


#: Regel → Vorfall für die Lernschleife (#286): Klasse, Symptom, Ursache, Lösung.
#: Die Worte sind je Regel fest — so steht eine Regel genau einmal im Katalog,
#: der konkrete Fall (Ticket, Text) landet in der Bau-Log-Zeile und im Beispiel.
REGEL_VORFALL: dict[str, tuple[str, str, str, str]] = {
    "commit_ohne_nummer": (
        "prozess",
        "Arbeit im Repo, aber kein Ticket-Bezug im Commit-Betreff",
        "Betreff endet nicht auf (#N) — der Aufseher ordnet den Commit keinem Ticket zu",
        "Betreff mit (#N) abschließen; capo öffnet das Ticket wieder",
    ),
    "beweis_fehlt": (
        "prozess",
        "Ticket zu, aber niemand kann den Beweis nachlesen",
        "Keine Belegseite unter dem Beleg-Ordner zum Ticket",
        "Belegseite anlegen (docs/verify-hard/<N>_*.md) und neu schließen",
    ),
    "beleg_form": (
        "prozess",
        "Belegseite vorhanden, erfüllt aber die feste Form nicht",
        "Pflichtfelder, Klickweg-Abschnitt, Platzhalter oder Bilder fehlen/ungültig",
        "Belegseite nach fester Form vervollständigen und auf dem Hauptzweig ablegen",
    ),
    "test_ersetzt": (
        "prozess",
        "Nach dem Fix fehlen Tests, die vorher da waren",
        "Test entfernt statt ergänzt",
        "Test wieder aufnehmen oder die Umbenennung im Commit-Text ausweisen",
    ),
    "vps_ungleich_origin": (
        "infra",
        "Live-System zeigt einen anderen Stand als origin",
        "Fremder Deploy hat den eigenen Stand überschrieben",
        "VPS-HEAD vor dem Bundle prüfen, Rettung mit --to <origin-SHA>",
    ),
    "session_verwaist": (
        "skill",
        "Fenster steht, Ticket offen, nichts bewegt sich",
        "Session tot oder ohne Folge-Runde — keine Spur in Commit, Bau-Log, Worktree",
        "capo kommentiert das Ticket, der Aufpasser startet die Folge-Runde",
    ),
    "gate_rot": (
        "infra",
        "Deploy-Gate bricht rot ab",
        "Test oder Vorstufe im Gate scheitert",
        "Grund aus der Gate-Ausgabe beheben und das Gate neu starten",
    ),
    "live_beweis_blockiert": (
        "prozess",
        "Session meldet: Live-Beweis nicht möglich",
        "Ein fremder Lauf, ein Deploy oder ein fehlendes Gerät blockiert den Weg",
        "Blocker im Ticket benennen, Beweis nach dem Blocker nachholen",
    ),
    "folgerunden_grenze": (
        "mensch",
        "Ticket wieder offen, aber niemand baut weiter",
        "Folge-Runden-Grenze erreicht — capo startet keine neue Runde (#284)",
        "Ein Mensch prüft das Ticket und baut von Hand weiter",
    ),
    "checkpoint_offen": (
        "mensch",
        "Checkpoint wartet über die Frist, die Session hat keinen Vorschlag hinterlassen",
        "Frage ohne eigenen Vorschlag — der Aufseher rät nicht (#285)",
        "Session stellt jede Checkpoint-Frage mit „Vorschlag: …“",
    ),
    "checkpoint_annahme": (
        "mensch",
        "Ja/Nein-Frage mitten in der Kette, nachts antwortet niemand",
        "Checkpoint ohne Antwort über die Frist (#285)",
        "Aufseher nimmt den Vorschlag der Session nach Doktrin an; der Mensch kann kippen",
    ),
}

#: Vorfall-Worte, wenn eine Regel neu ist und noch nicht in REGEL_VORFALL steht.
VORFALL_UNBEKANNT = (
    "skill",
    "Aufseher meldet einen Verstoß ohne hinterlegte Lernschleife",
    "Regel ist neu und steht noch nicht in REGEL_VORFALL",
    "Regel in to_spawn/capo.py:REGEL_VORFALL mit Klasse/Symptom/Ursache/Lösung ergänzen",
)


@dataclass
class Verstoss:
    ticket: int
    regel: str
    text: str
    kritisch: bool = False
    #: Was den Verstoß ausmacht, falls der Text auch Wanderndes nennt (z. B. VPS-HEAD);
    #: leer = der Text selbst. Grundlage von :func:`verstoss_kennung` (#275).
    kern: str = ""


Commit = schliess_pruefer.Commit
BELEGE_ORDNER = schliess_pruefer.BELEGE_ORDNER
_NichtPruefbar = schliess_pruefer._NichtPruefbar


@dataclass
class TickErgebnis:
    zeilen: list[str] = field(default_factory=list)
    verstoesse: list[Verstoss] = field(default_factory=list)
    fertig: bool = False
    exit_code: int = 0
    #: Verstöße an Tickets aus dem Ausgangsstand (nur gemeldet, nie wieder geöffnet).
    alt: list[Verstoss] = field(default_factory=list)


# --- Git ------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> tuple[int, str]:
    """``git <args>`` mit Zeitgrenze (Grenzen: gh.FETCH_ZEIT_S bzw. gh.GIT_ZEIT_S); Zeitablauf = Fehler."""
    grenze = gh.FETCH_ZEIT_S if args[:1] == ("fetch",) else gh.GIT_ZEIT_S  # eine Stelle: gh (#320)
    try:
        fertig = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=grenze,
            check=False,
            **prozessbaum.fensterlos(),
        )
    except subprocess.TimeoutExpired:
        log.warning("git %s nach %s s abgebrochen.", args[:2], grenze)
        return 124, ""
    except OSError as fehler:
        log.warning("git %s gescheitert: %s", args[:2], fehler)
        return 127, ""
    return fertig.returncode, fertig.stdout.rstrip("\n")


def haupt_ref(repo: Path) -> str | None:
    """Weiterleitung auf :func:`bau_log.haupt_ref` (#375)."""
    return bau_log.haupt_ref(repo)


# --- Schließ-Regeln: eine Quelle der Wahrheit in ``to_spawn.schliess_pruefer`` (#864) ---

betreff_hat_nummer = schliess_pruefer.betreff_hat_nummer
ticket_commits = schliess_pruefer.ticket_commits
belege_ordner = schliess_pruefer.belege_ordner
beleg_passt = schliess_pruefer.beleg_passt
ist_doku = schliess_pruefer.ist_doku
ist_testdatei = schliess_pruefer.ist_testdatei
entfernte_tests = schliess_pruefer.entfernte_tests
umbenannte_tests = schliess_pruefer.umbenannte_tests


def _verstoss_aus_grund(ticket: int, grund: schliess_pruefer.Grund | None) -> Verstoss | None:
    if grund is None:
        return None
    return Verstoss(ticket, grund.regel, grund.text, kern=grund.kern)


def alle_commits(repo: Path, ref: str) -> list[Commit]:
    """Weiterleitung auf :func:`schliess_pruefer.alle_commits`."""
    return schliess_pruefer.alle_commits(repo, ref, git_lauf=_git)


def _dateien(repo: Path, sha: str, *, streng: bool = False) -> list[tuple[str, str]]:
    """Weiterleitung auf :func:`schliess_pruefer._dateien`."""
    return schliess_pruefer._dateien(repo, sha, streng=streng, git_lauf=_git)


def belegseiten(repo: Path, ticket: int, *, ref: str = "HEAD", ordner: str | None = None) -> list[str]:
    """Weiterleitung auf :func:`schliess_pruefer.belegseiten`."""
    return schliess_pruefer.belegseiten(repo, ticket, ref=ref, ordner=ordner, git_lauf=_git)


def _datei_an_spitze(repo: Path, spitze: str, pfad: str) -> str | None:
    """Weiterleitung auf :func:`schliess_pruefer._datei_an_spitze`."""
    return schliess_pruefer._datei_an_spitze(repo, spitze, pfad, git_lauf=_git)


def regel_commit(ticket: int, eigene: list[Commit]) -> Verstoss | None:
    """Weiterleitung auf :func:`schliess_pruefer.regel_commit`."""
    return _verstoss_aus_grund(ticket, schliess_pruefer.regel_commit(ticket, eigene))


def regel_beweis(repo: Path, ref: str, ticket: int, eigene: list[Commit], ordner: str) -> Verstoss | None:
    """Weiterleitung auf :func:`schliess_pruefer.regel_beweis`."""
    return _verstoss_aus_grund(
        ticket,
        schliess_pruefer.regel_beweis(
            repo,
            ref,
            ticket,
            eigene,
            ordner,
            form_pruefen=False,
            beleg_auf_hauptzweig=False,
            git_lauf=_git,
        ),
    )


def regel_tests(repo: Path, ticket: int, eigene: list[Commit]) -> Verstoss | None:
    """Weiterleitung auf :func:`schliess_pruefer.regel_tests`."""
    return _verstoss_aus_grund(ticket, schliess_pruefer.regel_tests(repo, ticket, eigene, git_lauf=_git))


def _passt(pfad: str, muster: list[str]) -> bool:
    for m in muster:
        if m.endswith("/") and pfad.startswith(m):
            return True
        if pfad == m or fnmatch.fnmatch(pfad, m):
            return True
    return False


def _ssh_programm() -> str:
    """ssh wie die Shell auflösen (unter Windows auch ``ssh.cmd``/``.bat`` über PATHEXT)."""
    return shutil.which("ssh") or "ssh"


def _server_pfad(pfad: str) -> str:
    """Pfad für die Server-Shell quoten; ein führendes ``~/`` bleibt offen, damit der Server es auflöst."""
    if pfad == "~":
        return "~"
    if pfad.startswith("~/"):
        return "~/" + shlex.quote(pfad[2:])
    return shlex.quote(pfad)


def vps_kopf(vps: dict[str, Any]) -> str | None:
    """Voller SHA von ``HEAD`` auf dem VPS (``None`` = nicht lesbar)."""
    befehl = [
        _ssh_programm(),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        str(vps["ssh"]),
        f"cd {_server_pfad(str(vps.get('pfad') or '.'))} && git rev-parse HEAD",
    ]
    try:
        fertig = konsole.lauf_daten(befehl, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("VPS-HEAD nicht lesbar: %s", fehler)
        return None
    if fertig is None:
        return None
    zeilen = fertig.stdout.strip().splitlines()
    sha = zeilen[-1].strip() if zeilen else ""
    if fertig.returncode != 0 or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        log.warning(
            "VPS-HEAD nicht lesbar (Exit %s): %s",
            fertig.returncode,
            fertig.stderr.strip()[:200],
        )
        return None
    return sha


#: Vorgabe von ``vps.staging_log`` (L1 #46); Name bleibt für Aufrufer.
STAGING_LOG_STANDARD: str = config.DEFAULTS["vps"]["staging_log"]
FREIGABE_STANDARD = "~/.config/to-spawn/live_freigabe"
FREIGABE_MAX_ALTER_S = 24 * 3600  # wie scripts/staging/deploy_ziel.py


class _StagingUnlesbar(Exception):
    """Staging-Log per ssh nicht lesbar — das ist kein „Commit fehlt auf Staging“ (#1407)."""


def _staging_zeilen(vps: dict[str, Any]) -> list[str] | None:
    """Zeilen des Staging-Deploy-Logs; lokal, sonst per ``vps.staging_ssh`` vom Bau-Server.

    ``None`` = kein Staging-Stand: Datei fehlt lokal ohne ``staging_ssh``, oder die Verbindung
    steht, aber ``tail`` scheitert auf dem Server (Log fehlt, z. B. Exit 1). Nur ein
    Verbindungsfehler (ssh Exit 255, Zeitüberschreitung, ssh nicht startbar) gibt
    ``_StagingUnlesbar`` (Warnung ist dann schon geloggt).

    Am PC liegt das Log nur auf dem Bau-Server. Ohne diesen Weg sah capo dort nie
    einen Staging-Stand und öffnete fertige Nur-Staging-Tickets immer wieder (#1332).
    """
    pfad_text = str(vps.get("staging_log") or STAGING_LOG_STANDARD)
    try:
        return Path(pfad_text).expanduser().read_text(encoding="utf-8").splitlines()
    except OSError:
        pass
    host = vps.get("staging_ssh")
    if not host:
        return None
    befehl = [
        _ssh_programm(),
        "-o",
        "ConnectTimeout=8",
        "-o",
        "BatchMode=yes",
        str(host),
        f"tail -n 200 {_server_pfad(pfad_text)}",
    ]
    try:
        lauf = subprocess.run(
            befehl,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
            **prozessbaum.fensterlos(),
        )
    except (OSError, subprocess.SubprocessError) as fehler:
        log.warning("Staging-Log über %s nicht lesbar: %s", host, fehler)
        raise _StagingUnlesbar from fehler
    if lauf.returncode == 255:  # ssh selbst gescheitert (Verbindung/Anmeldung)
        log.warning("Staging-Log über %s nicht lesbar (Exit %s).", host, lauf.returncode)
        raise _StagingUnlesbar
    if lauf.returncode != 0:  # Verbindung stand, Log fehlt auf dem Server = kein Staging-Stand
        log.warning("Staging-Log auf %s fehlt (Exit %s): %s", host, lauf.returncode, lauf.stderr.strip()[:200])
        return None
    return lauf.stdout.splitlines()


def staging_kopf(vps: dict[str, Any]) -> str | None:
    """SHA des letzten grünen Staging-Deploys (``None`` = Datei fehlt/nicht lesbar/kein grüner Stand)."""
    try:
        return _gruener_sha(_staging_zeilen(vps))
    except _StagingUnlesbar:
        return None


def _gruener_sha(zeilen: list[str] | None) -> str | None:
    for zeile in reversed(zeilen or []):
        try:
            eintrag = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(eintrag, dict) and eintrag.get("ergebnis") == "gruen" and eintrag.get("sha"):
            return str(eintrag["sha"])
    return None


class StagingStand:
    """Staging-Kopf eines Takts: höchstens ein Abruf, das Ergebnis (auch ``None``) gilt für den ganzen Takt.

    Aufrufer fragen nur ``kopf()``. Wie (lokal oder ssh) und wie oft gelesen wird, steht
    allein hier: ein hängender Bau-Server kostet so je Takt einmal die Zeitgrenze, nicht
    einmal je Commit und Ticket (#1407).
    """

    def __init__(self, vps: dict[str, Any]) -> None:
        self._vps = vps
        self._geholt = False
        self._kopf: str | None = None
        self.unlesbar = False  # ssh-Aussetzer: Staging-Prüfung für diesen Takt aussetzen

    def kopf(self) -> str | None:
        if not self._geholt:
            self._geholt = True
            try:
                self._kopf = _gruener_sha(_staging_zeilen(self._vps))
            except _StagingUnlesbar:
                self.unlesbar = True
                log.warning("Staging-Stand nicht lesbar — Staging-Prüfung in diesem Takt ausgesetzt.")
        return self._kopf


def live_freigabe(vps: dict[str, Any], ticket: int) -> bool:
    """True, wenn der Live-Zettel des Menschen ``<ordner>/<ticket>`` existiert und jünger als 24 h ist."""
    zettel = Path(str(vps.get("freigabe_ordner") or FREIGABE_STANDARD)).expanduser() / str(ticket)
    try:
        return time.time() - zettel.stat().st_mtime < FREIGABE_MAX_ALTER_S
    except OSError:
        return False


class _StandUnlesbar(Exception):
    """Der Deckungs-Stand (origin/<Hauptzweig>) ließ sich nicht lesen."""


# Was das Deckungs-Skript zum Laufen braucht: es selbst, sein Import ``schlange_spur`` und
# ``staging/deploy_ziel.py`` (Unterprozess). Erstes Element = das Skript.
_DECKUNG_DATEIEN = ("scripts/schlange_deckung.py", "scripts/schlange_spur.py", "scripts/staging/deploy_ziel.py")
_MANIFESTE_VORGABE: str = str(config.DEFAULTS["pfade"]["manifeste"])


def _manifest_ordner(repo: Path) -> str:
    """Relativer Manifest-Ordner des Repos für ``git ls-tree``/``git archive`` (Konfig ``pfade.manifeste``)."""
    pfade = config.lade(repo).get("pfade")
    wert = pfade.get("manifeste") if isinstance(pfade, dict) else None
    text = wert.strip() if isinstance(wert, str) and wert.strip() else _MANIFESTE_VORGABE
    if Path(text).is_absolute() or text.startswith("~"):
        return _MANIFESTE_VORGABE
    return text.replace("\\", "/").strip("/") or _MANIFESTE_VORGABE


@dataclass(frozen=True)
class _Stand:
    """Entpackter Deckungs-Stand: Pfad zum Skript und zum Manifest-Ordner."""

    skript: Path
    manifeste: Path


@contextlib.contextmanager
def _deckungs_stand(repo: Path) -> Iterator[_Stand | None]:
    """Der Stand, aus dem die Deploy-Schlange deckt — einzige Stelle mit diesem Wissen (#1504/#860).

    Die Schlange rechnet in ihrem Klon auf ``origin/master``; ein ungepushtes Manifest oder Skript
    im Arbeitsbaum darf capos Urteil deshalb nicht ändern. Entpackt nur :data:`_DECKUNG_DATEIEN`
    und die Manifeste aus ``origin/<Hauptzweig>`` (``git archive``, Stand des letzten ``git fetch``
    in :func:`tick`) in einen Wegwerf-Ordner. ``None``: Deckungs-Skript gibt es dort nachweislich
    nicht (Fremd-Repo, alter Stand). Wirft :class:`_StandUnlesbar`, wenn Ref, Baum-Abfrage
    (Zeitablauf, OSError, …) oder Archiv scheitern — der Aufrufer wertet das als „nicht gedeckt“
    (fail-closed); eine gescheiterte Abfrage heißt nie „fehlt“.
    """
    ref = haupt_ref(repo)
    if ref is None:
        raise _StandUnlesbar("kein origin/<Hauptzweig>")
    manifeste = _manifest_ordner(repo)
    code, text = _git(repo, "ls-tree", "-r", "-z", "--name-only", ref, "--", *_DECKUNG_DATEIEN, manifeste)
    if code != 0:
        raise _StandUnlesbar(f"git ls-tree {ref} Exit {code}")
    vorhanden = set(filter(None, text.split("\0")))
    if _DECKUNG_DATEIEN[0] not in vorhanden:
        yield None
        return
    pfade = [d for d in _DECKUNG_DATEIEN if d in vorhanden]
    if any(n.startswith(manifeste + "/") for n in vorhanden):
        pfade.append(manifeste)
    with tempfile.TemporaryDirectory(prefix="capo-deckung-") as ordner:
        ziel = Path(ordner)
        try:
            archiv = subprocess.run(
                ["git", "archive", "--format=tar", ref, "--", *pfade],
                cwd=str(repo),
                capture_output=True,
                timeout=gh.GIT_ZEIT_S,
                check=False,
                **prozessbaum.fensterlos(),
            )
            if archiv.returncode != 0:
                raise _StandUnlesbar(f"git archive {ref} Exit {archiv.returncode}")
            with tarfile.open(fileobj=io.BytesIO(archiv.stdout)) as tar:
                tar.extractall(ziel, filter="data")
        except (OSError, subprocess.SubprocessError, tarfile.TarError) as fehler:
            raise _StandUnlesbar(str(fehler)) from fehler
        (ziel / manifeste).mkdir(parents=True, exist_ok=True)
        yield _Stand(skript=ziel / _DECKUNG_DATEIEN[0], manifeste=ziel / manifeste)


def live_gedeckt(repo: Path, vps: dict[str, Any], ticket: int, kopf: str, sha: str) -> bool:
    """Nimmt der Deploy-Dienst ``sha`` beim nächsten Lauf live? Urteil allein bei der SSOT im Repo.

    ``scripts/schlange_deckung.py --bis-spitze`` wählt den Bereich wie die Deploy-Schlange
    (``kopf..origin/master``) und rechnet Schlüssel und Deckung mit derselben Funktion (#395, #1207/#859):
    ein Ticket-Zettel allein deckt kein fremdes Ticket, ein SHA-Zettel auf der Spitze deckt alles davor.
    Antwort: Exit 0 = gedeckt, 1 = nicht gedeckt, JSON ``{"gedeckt", "offen"}``.
    Fehlt das Skript ganz (Fremd-Repo, alter Stand), zählt wie früher nur der eigene Zettel.
    Jede andere unbrauchbare Antwort (Exit ≠ 0/1, Zeitlimit, kein JSON, Widerspruch) heißt
    „nicht gedeckt“ mit Warnzeile — nie Rückfall auf den eigenen Zettel (sonst Fehlalarm).
    Skript und Manifeste kommen aus :func:`_deckungs_stand` (origin/master, #1504/#860); ist der Stand
    unlesbar, heißt das ebenfalls „nicht gedeckt“ mit Warnzeile.
    """
    try:
        with _deckungs_stand(repo) as stand:
            if stand is None:
                return live_freigabe(vps, ticket)
            befehl = [
                sys.executable,
                str(stand.skript),
                kopf,
                sha,
                "--bis-spitze",
                "--repo",
                str(repo),
                "--manifeste",
                str(stand.manifeste),
                "--freigabe-ordner",
                str(Path(str(vps.get("freigabe_ordner") or FREIGABE_STANDARD)).expanduser()),
            ]
            lauf = subprocess.run(
                befehl,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
                check=False,
                **prozessbaum.fensterlos(),
            )
            daten = json.loads(lauf.stdout or "null") if lauf.returncode in (0, 1) else None
    except (OSError, subprocess.SubprocessError, ValueError, _StandUnlesbar) as fehler:
        log.warning("Deckung für #%s nicht prüfbar (%s) — gilt als nicht gedeckt.", ticket, fehler)
        return False
    if not isinstance(daten, dict) or daten.get("gedeckt") is not (lauf.returncode == 0):
        log.warning("Deckung für #%s nicht prüfbar (Exit %s) — gilt als nicht gedeckt.", ticket, lauf.returncode)
        return False
    return daten["gedeckt"]


def regel_vps(
    repo: Path,
    ticket: int,
    eigene: list[Commit],
    vps: dict[str, Any],
    kopf: str | None,
    log_zeilen: list[dict],
    staging_stand: StagingStand | None = None,
    issue: dict[str, Any] | None = None,
) -> Verstoss | None:
    if kopf is None or not eigene:
        return None
    if issue is not None and ticket_zustand.wartet_auf_live(issue, live_freigegeben=live_freigabe(vps, ticket)):
        return None  # #275: grün auf Staging, Live erst nach Freigabe — kein Verstoß
    staging_stand = staging_stand or StagingStand(vps)
    muster = [str(m) for m in vps.get("deploy_pfade") or []]
    relevant = [c for c in eigene if any(_passt(p, muster) for _, p in _dateien(repo, c.sha))]
    if not relevant and any(z.get("typ") == "deploy_phase" for z in log_zeilen):
        relevant = eigene[:1]  # jüngster Ticket-Commit
    for commit in relevant:
        code, _ = _git(repo, "merge-base", "--is-ancestor", commit.sha, kopf)
        if code == 1:
            # Seit 22.09. deployen Bau-Sessions nur nach Staging; live nur mit Zettel.
            staging = staging_stand.kopf()
            # Live erwartet nur, wenn der Deploy-Dienst den Stand live nimmt (#1207/#859), nicht schon beim
            # Ticket-Zettel. Ohne Staging-Stand zählt das Urteil nicht — dann kein Unterprozess.
            live = (staging_stand.unlesbar or bool(staging)) and live_gedeckt(repo, vps, ticket, kopf, commit.sha)
            if staging_stand.unlesbar and not live:
                return None  # nicht lesbar ≠ fehlt auf Staging (#1407), Log-Zeile kommt aus StagingStand
            if staging and not live:
                auf_staging, _ = _git(repo, "merge-base", "--is-ancestor", commit.sha, staging)
                if auf_staging == 0:
                    continue
                if auf_staging != 1:
                    # Staging-Stand lokal noch nicht geholt (#362): nicht als „fehlt“ werten.
                    log.warning(
                        "Staging-Stand %s lokal unbekannt — Regel vps für #%s übersprungen.",
                        staging[:7],
                        ticket,
                    )
                    return None
            return Verstoss(
                ticket,
                "vps_ungleich_origin",
                f"Commit {commit.sha[:7]} fehlt auf dem VPS (HEAD {kopf[:7]})",
                kern=commit.sha,
            )
        if code != 0:
            log.warning(
                "VPS-HEAD %s lokal unbekannt — Regel vps für #%s übersprungen.",
                kopf[:7],
                ticket,
            )
            return None
    return None


def _zeit(text: Any) -> datetime | None:
    if not text:
        return None
    try:
        wert = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)


def worktree_ordner(ticket: int, basis: str, repo: Path | None = None) -> Path:
    """``<basis>/wt-N``; ohne Basis die Regel des Skills (``config.worktree_pfad`` für ``repo``,
    samt Ausweich-Ordner bei gleicher Nummer in zwei Repos, #641)."""
    if basis:
        return Path(basis).expanduser() / f"wt-{ticket}"
    from . import config

    return Path(config.worktree_pfad(ticket, repo)).expanduser()


def worktree_spur(wt: Path, ref: str) -> tuple[float | None, str]:
    """(jüngste Änderung als Unix-Zeit, Kurzinfo) eines Worktrees."""
    if not wt.is_dir():
        return None, "-"
    code, status = _git(wt, "status", "--porcelain", "-uall")
    if code != 0:
        return None, "wt:?"
    zeiten: list[float] = []
    zeilen = [z for z in status.splitlines() if z.strip()]
    for zeile in zeilen:
        pfad = zeile[3:].split(" -> ")[-1].strip().strip('"')
        try:
            zeiten.append((wt / pfad).stat().st_mtime)
        except OSError:
            continue
    code, eigen = _git(wt, "log", f"{ref}..HEAD", "-1", "--format=%ct")
    if code == 0 and eigen.strip().isdigit():
        zeiten.append(float(eigen.strip()))
    return (max(zeiten) if zeiten else None), f"wt:{len(zeilen)}dirty"


def regel_verwaist(
    ticket: int,
    issue: dict[str, Any],
    eigene: list[Commit],
    log_zeilen: list[dict],
    wt_zeit: float | None,
    stunden: float,
    jetzt: datetime,
    *,
    checkpoint: str = "",
) -> Verstoss | None:
    if not issue.get("assignees"):
        return None
    if ticket_zustand.fertig_gebaut(issue):
        return None  # #279: fertig gebaut hat zu Recht keine Spur mehr
    if checkpoint and checkpoint in label_namen(issue):
        return None  # Checkpoint-Ticket wartet absichtlich auf einen Menschen
    datiert = [(t, z) for z in log_zeilen if (t := _zeit(z.get("ts")))]
    if datiert and max(datiert, key=lambda paar: paar[0])[1].get("typ") == "blockiert":
        return None  # Session hat „blockiert“ gemeldet — wartet, ist nicht tot
    spuren: list[datetime] = [datetime.fromtimestamp(c.zeit, timezone.utc) for c in eigene]
    spuren += [t for t in (_zeit(z.get("ts")) for z in log_zeilen) if t]
    if wt_zeit is not None:
        spuren.append(datetime.fromtimestamp(wt_zeit, timezone.utc))
    if not spuren:
        seit = _zeit(issue.get("updated_at"))
        spuren = [seit] if seit else []
    if not spuren:
        return None
    ruhe = jetzt - max(spuren)
    if ruhe <= timedelta(hours=stunden):
        return None
    std = ruhe.total_seconds() / 3600
    return Verstoss(
        ticket,
        "session_verwaist",
        f"seit {std:.1f} h keine Spur (Commit, Bau-Log, Worktree) — Session tot?",
        kritisch=True,
    )


# --- Bau-Log --------------------------------------------------------------------


def label_namen(issue: dict[str, Any]) -> set[str]:
    """Label-Namen eines Issues (GitHub liefert Objekte, zur Not auch reine Namen)."""
    namen: set[str] = set()
    for label in issue.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else label
        if name:
            namen.add(str(name))
    return namen


def log_vom_ref(repo: Path, ref: str, ticket: int) -> list[dict[str, Any]]:
    """Weiterleitung auf :func:`bau_log.log_vom_ref` (#375)."""
    return bau_log.log_vom_ref(repo, ref, ticket)


def lauf_zeilen(datei: Path) -> list[dict[str, Any]] | None:
    """Zeilen einer unversionierten Laufdatei; ``None`` = Datei gibt es nicht."""
    if not datei.is_file():
        return None
    try:
        text = datei.read_text(encoding="utf-8", errors="replace")
    except OSError as fehler:
        log.warning("Laufdatei %s nicht lesbar: %s", datei, fehler)
        return None
    return bau_log.zeilen_aus(text, str(datei))


def laufdateien(repo: Path, wt: Path, ticket: int) -> list[tuple[str, Path]]:
    """(Quelle, Pfad) der Laufdateien: Ticket-Worktree und Repo-Ordner, ohne Doppel."""
    paare = [
        ("wt", wt / bau_log.LAUF_ORDNER / f"{ticket}.jsonl"),
        ("repo", repo / bau_log.LAUF_ORDNER / f"{ticket}.jsonl"),
    ]
    gesehen: set[str] = set()
    ergebnis = []
    for quelle, pfad in paare:
        schluessel = os.path.normcase(os.path.abspath(pfad))
        if schluessel not in gesehen:
            gesehen.add(schluessel)
            ergebnis.append((quelle, pfad))
    return ergebnis


def _roh(z: dict[str, Any]) -> str:
    return json.dumps(z, sort_keys=True, ensure_ascii=False)


def _uhr(ts: Any) -> str:
    zeit = _zeit(ts)
    return zeit.astimezone().strftime("%H:%M") if zeit else "--:--"


def _kurz(text: Any, grenze: int = 140) -> str:
    wert = " ".join(str(text or "").split())
    return wert if len(wert) <= grenze else wert[: grenze - 1] + "…"


def entscheidung_teile(z: dict[str, Any]) -> tuple[str, str, str]:
    """(Frage, Wahl, Grund) einer ``entscheidung``-Zeile, gekürzt (Rückfall in :func:`bau_log.entscheidung_felder`)."""
    frage, wahl, grund = bau_log.entscheidung_felder(z)
    return _kurz(frage), _kurz(wahl), _kurz(grund)


def verdichte(ticket: int, z: dict[str, Any]) -> str:
    typ = str(z.get("typ") or "?")
    if typ == "entscheidung":
        inhalt = " · ".join(t for t in entscheidung_teile(z) if t)
    elif typ == "deploy_phase":
        inhalt = " ".join(str(z.get(k)) for k in ("phase", "status", "ergebnis", "grund") if z.get(k))
    else:
        inhalt = next((str(z[k]) for k in ("grund", "text", "umfang", "nach") if z.get(k)), "")
    return _kurz(f"#{ticket} {_uhr(z.get('ts'))} {typ}: {inhalt}".rstrip(": "), 200)


def ist_gate_rot(z: dict[str, Any]) -> bool:
    if z.get("typ") != "deploy_phase":
        return False
    return any(str(z.get(k) or "").strip().lower() in ROT_WERTE for k in ("status", "phase", "ergebnis"))


# --- Übersicht ------------------------------------------------------------------


def _zelle(text: str, grenze: int | None = 140) -> str:
    """Tabellenzelle: eine Zeile, ``|`` entschärft, ab ``grenze`` gekürzt (``None`` = nie)."""
    wert = _kurz(text, grenze) if grenze else " ".join(str(text or "").split())
    return wert.replace("|", "/")


def entscheidungs_datei(repo: Path, spec: int) -> Path:
    """``<pfade.agenten>/entscheidungen_<S>.md`` (Vorgabe ``docs/agents``, L1 #46)."""
    return config.pfad(config.lade(repo), "agenten") / f"entscheidungen_{spec}.md"


#: Spaltenkopf der Entscheidungs-Tabelle (#364); :func:`_entscheidungs_zeile` passt dazu.
_SPALTEN = "| Ticket | Was | Warum | Quelle |"


def _tabellen_kopf(spec: int, quelle: str) -> list[str]:
    return [
        f"# Entscheidungen Spec #{spec}",
        "",
        f"Stand {datetime.now().astimezone():%d.%m.%Y %H:%M} · Quelle: {quelle} (to-spawn capo).",
        "",
        _SPALTEN,
        "|---|---|---|---|",
    ]


def _entscheidungs_zeile(e: dict[str, str]) -> str:
    """Eine Tabellenzeile aus einem Eintrag von ``briefkasten.liste`` — das eine Zeilenformat.

    ``was`` bleibt ungekürzt: die Frage ist dort schon gekürzt, die Wahl muss ganz bleiben.
    """
    return f"| #{e['ticket']} | {_zelle(e['was'], None)} | {_zelle(e['warum'])} | {_zelle(e['beleg'])} |"


def entscheidung_anhaengen(
    repo: Path,
    spec: int,
    ticket: int,
    frage: str,
    wahl: str,
    grund: str,
    wann: datetime,
) -> Path:
    """Eine einzelne Entscheidung an ``entscheidungen_<S>.md`` anhängen (#285).

    Gleiches Zeilenformat wie :func:`uebersicht` (#364). Die Zeile steht auch in der
    Laufdatei des Bau-Logs (Aufrufer schreibt sie); :func:`uebersicht` liest die Laufdatei
    über ``briefkasten.liste`` mit, die Annahme bleibt beim Abschluss also erhalten.
    """
    datei = entscheidungs_datei(repo, spec)
    eintrag = briefkasten.eintrag_aus_bau_log(
        ticket,
        {"frage": frage, "wahl": wahl, "grund": grund, "ts": wann.astimezone().isoformat(timespec="seconds")},
    )
    zeile = _entscheidungs_zeile(eintrag)
    datei.parent.mkdir(parents=True, exist_ok=True)
    alt = datei.read_text(encoding="utf-8") if datei.is_file() else ""
    if _SPALTEN in alt:
        with datei.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(zeile + "\n")
    else:
        datei.write_text(
            "\n".join([*_tabellen_kopf(spec, "Aufseher-Annahmen"), zeile]) + "\n",
            encoding="utf-8",
        )
    return datei


def uebersicht(repo: Path, spec: int, tickets: list[int], *, hinweis: str = "") -> Path:
    """Schreibt :func:`entscheidungs_datei` neu: alle Entscheidungen der Spec (#364).

    Woher sie kommen (Bau-Log im Hauptbaum und auf ``origin/<Hauptzweig>``, Briefkasten-
    Antworten), weiß nur ``briefkasten.liste``; hier steht nur die Darstellung (eine
    Tabellenzeile je Eintrag). Die Kopfzeile nennt ``haupt_ref`` — den Stand, den
    ``briefkasten.liste`` liest. Optionaler ``hinweis`` markiert unvollständige Listen
    (z. B. fehlendes Manifest, #445).
    """
    eintraege = briefkasten.liste(sorted(tickets), log_repo=repo, hauptbaum=repo)
    zeilen = _tabellen_kopf(spec, f"Bau-Logs auf {haupt_ref(repo) or '-'} + Briefkasten")
    if hinweis.strip():
        zeilen = [*zeilen[:3], "", f"> **Hinweis:** {hinweis.strip()}", *zeilen[3:]]
    zeilen += [_entscheidungs_zeile(e) for e in eintraege]
    if not eintraege:
        zeilen += ["", "_Keine Entscheidungen im Bau-Log oder Briefkasten._"]
    datei = entscheidungs_datei(repo, spec)
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return datei


#: Höchstzahl Punkte je Abschnitt im Morgen-Bericht — er soll in 5 Minuten lesbar sein (#885).
BERICHT_MAX = 12
BERICHT_ZEILE = 220


def morgen_bericht_datei(repo: Path, spec: int) -> Path:
    """``.to-spawn/morgen_bericht_<S>.md`` — Laufdatei neben ``abschluss_<S>_paket.json`` (#885)."""
    return repo / config.KONFIG_PFAD.parent / f"morgen_bericht_{spec}.md"


def _umweg(z: dict[str, Any]) -> str | None:
    """Kurztext einer Bau-Log-Zeile, die einen Umweg zeigt; ``None`` = kein Umweg."""
    typ = z.get("typ")
    if typ == "vorfall":
        return " → ".join(t for t in (_kurz(z.get("symptom")), _kurz(z.get("loesung"))) if t) or None
    if ist_gate_rot(z):
        return f"Gate rot: {_kurz(z.get('grund') or z.get('ergebnis') or z.get('phase'))}"
    if typ == "blockiert":
        return f"blockiert: {_kurz(z.get('grund'))}"
    if typ == "ruecknahme":
        return f"Fehl-Reopen zurückgenommen: {_kurz(z.get('text'))}"
    return None


def _abschnitt(titel: str, punkte: list[str], leer: str) -> list[str]:
    zeilen = ["", f"## {titel}", ""]
    if not punkte:
        return [*zeilen, f"_{leer}_"]
    zeilen += [f"- {_kurz(p, BERICHT_ZEILE)}" for p in punkte[:BERICHT_MAX]]
    if len(punkte) > BERICHT_MAX:
        zeilen.append(f"- … und {len(punkte) - BERICHT_MAX} weitere (siehe Bau-Log)")
    return zeilen


def morgen_bericht(
    repo: Path,
    ref: str | None,
    spec: int,
    liste: list[dict[str, Any]],
    *,
    checkpoint: str = "checkpoint:human",
) -> tuple[Path, str]:
    """Schreibt ``morgen_bericht_<S>.md`` mit 4 Teilen und Deckel (#885) und gibt ``(Pfad, Text)``.

    Fertig · Entscheidungen zum Kippen · Umwege · Wartet auf den Nutzer — aus den Unter-Tickets
    und den Bau-Logs auf ``ref``. Je Teil höchstens :data:`BERICHT_MAX` Punkte; am Ende steht
    einmal die Liste der Kritisch-Ausnahmen (:func:`melder.kritisch_liste`).
    """
    fertig: list[str] = []
    kippen: list[str] = []
    umwege: list[str] = []
    wartet: list[str] = []
    for issue in sorted(liste, key=lambda i: int(i["number"])):
        n = int(issue["number"])
        titel = _kurz(issue.get("title"), 90)
        stufe = ticket_zustand.stufe(issue)
        if stufe in ("zu", "verworfen"):
            fertig.append(f"#{n} {titel}" + (" (nicht geplant)" if stufe == "verworfen" else ""))
        else:
            art = "Checkpoint" if checkpoint in label_namen(issue) else "noch offen"
            wartet.append(f"#{n} {art}: {titel}")
        for z in log_vom_ref(repo, ref, n) if ref else []:
            zeit = _zeit(z.get("ts"))
            wann = zeit.astimezone().strftime("%d.%m. %H:%M") if zeit else "-"
            if z.get("typ") == "entscheidung":
                frage, wahl, grund = entscheidung_teile(z)
                kippen.append(f"#{n} {wann}: {frage} → {wahl or '?'}" + (f" — {grund}" if grund else ""))
            elif (umweg := _umweg(z)) is not None:
                umwege.append(f"#{n} {wann}: {umweg}")
    for eintrag in mensch_noetig.eintraege_fuer_spec(spec, set()):
        wartet.append(f"#{eintrag.get('ticket') or spec} Mensch nötig: {mensch_noetig_was(eintrag)}")
    wartet.append("Spec abnehmen und Live freigeben — Live-Deploy wartet auf Freigabe des Nutzers.")
    kritisch_text = ", ".join(melder.kritisch_liste())
    ent_pfad = entscheidungs_datei(repo, spec)
    try:
        ent_rel = ent_pfad.relative_to(repo).as_posix()
    except ValueError:
        ent_rel = ent_pfad.as_posix()
    zeilen = [
        f"# Morgen-Bericht Spec #{spec}",
        "",
        f"Stand {datetime.now().astimezone():%d.%m.%Y %H:%M} · Quelle: Unter-Tickets (GitHub) und "
        f"Bau-Logs auf {ref or '-'} (to-spawn capo). Alle Entscheidungen: {ent_rel}",
        *_abschnitt("Fertig", fertig, "Noch kein Ticket zu."),
        *_abschnitt("Entscheidungen zum Kippen", kippen, "Keine Entscheidungen im Bau-Log."),
        *_abschnitt("Umwege", umwege, "Keine Umwege im Bau-Log."),
        *_abschnitt("Wartet auf den Nutzer", wartet, "Nichts."),
        "",
        f"Sofort-Mails (Kritisch-Ausnahmen tagsüber; nachts nur server_tot): {kritisch_text}",
    ]
    text = "\n".join(zeilen) + "\n"
    datei = morgen_bericht_datei(repo, spec)
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(text, encoding="utf-8")
    return datei, text


def morgen_bericht_verschicken(
    repo: Path,
    spec: int,
    text: str,
    *,
    konfig: dict[str, Any] | None = None,
    gh_repo: str = "",
    schluessel: str | None = None,
    jetzt: datetime | None = None,
) -> bool:
    """Genau 1 Morgen-Bericht als Mail (Art ``morgen_bericht``, nachts stumm, Doppel-Schutz, #885)."""
    return melder.melden(
        repo,
        "morgen_bericht",
        f"Morgen-Bericht Spec #{spec}",
        text,
        schluessel or f"morgen_bericht|{spec}",
        konfig=konfig,
        gh_repo=gh_repo,
        jetzt=jetzt,
    )


# --- GitHub ---------------------------------------------------------------------


def kinder(gh_repo: str, spec: int) -> list[dict[str, Any]] | None:
    """Sub-Issues der Spec; ``None`` bei Fehler oder kaputter Antwort (#279 P3).

    Ein einziger unlesbarer Eintrag macht die ganze Liste unbrauchbar: eine halbe Liste
    ließe die Spec fertig aussehen. Aufrufer setzen den Takt dann aus.
    """
    daten = gh.json_lauf(["api", f"repos/{gh_repo}/issues/{spec}/sub_issues?per_page=100"])
    if not isinstance(daten, list):
        return None
    kaputt = [
        f"Eintrag {stelle}"
        for stelle, i in enumerate(daten, start=1)
        if not (isinstance(i, dict) and "state" in i and str(i.get("number", "")).isdigit())
    ]
    if kaputt:
        log.warning(
            "Sub-Issues von #%s in %s: %s ohne Nummer/Zustand — Takt ausgesetzt.",
            spec,
            gh_repo,
            ", ".join(kaputt),
        )
        return None
    return daten


# --- Folge-Runde nach dem Wiederöffnen (#284) -----------------------------------

#: Höchstzahl Folge-Runden je Ticket (Vorgabe, ``waechter.folgerunden_max``).
FOLGERUNDEN_MAX = 2
#: Minuten, in denen eine frische Worktree-Spur als „da baut noch jemand“ zählt.
#: ponytail: nur der Weg ohne tmux; genauer würde es mit der Prozessliste aus
#: ``skripte/sessions_stand.py`` — die kostet je Tick einen vollen Prozess-Scan.
LAEUFT_MIN = 20.0
TMUX_ZEIT_S = 15
#: Obergrenze für den ``--auftrag``-Text auf der Kommandozeile (#285).
AUFTRAG_MAX_ZEICHEN = 600


def folgerunden_max(waechter: dict[str, Any]) -> int:
    """``waechter.folgerunden_max``; fehlt der Wert, gilt :data:`FOLGERUNDEN_MAX`."""
    roh = waechter.get("folgerunden_max")
    if roh is None or (isinstance(roh, str) and not roh.strip()):
        return FOLGERUNDEN_MAX
    try:
        wert = int(roh)
    except (TypeError, ValueError):
        log.warning(
            "waechter.folgerunden_max=%r ist keine Zahl — nehme %s.",
            roh,
            FOLGERUNDEN_MAX,
        )
        return FOLGERUNDEN_MAX
    return max(wert, 0)


def _tmux_befehl() -> list[str]:
    """``tmux`` — oder der Ersatz aus ``TO_SPAWN_TMUX`` (JSON-Liste oder ein Pfad)."""
    roh = os.environ.get("TO_SPAWN_TMUX", "").strip()
    if not roh:
        return ["tmux"]
    if roh.startswith("["):
        try:
            teile = json.loads(roh)
        except ValueError:
            log.warning("TO_SPAWN_TMUX=%r ist keine JSON-Liste — nehme den Text als Pfad.", roh)
            return [roh]
        return [str(teil) for teil in teile]
    return [roh]


def tmux_fenster(spec: int, *, sitzung: str | None = None) -> list[str] | None:
    """Fensternamen der tmux-Session einer Spec (``sitzung`` oder alt ``spec-<S>``).

    ``None`` = kein tmux da (dann läuft die Folge-Runde lokal), ``[]`` = tmux da,
    aber (noch) keine Session für diese Spec.
    """
    from . import tmux_aufruf  # spät: tmux_aufruf ruft _tmux_befehl aus capo

    ziel = sitzung or tmux_aufruf.sitzung_alt(spec)
    befehl = [*_tmux_befehl(), "list-windows", "-t", f"={ziel}", "-F", "#W"]
    try:
        fertig = subprocess.run(
            befehl,
            capture_output=True,
            **konsole.UNTERPROZESS_TEXT,
            **prozessbaum.fensterlos(),
            timeout=TMUX_ZEIT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as fehler:
        log.info("tmux nicht nutzbar (%s) — Folge-Runde liefe lokal.", fehler)
        return None
    if fertig.returncode != 0:
        log.info(
            "tmux list-windows für %s: Exit %s (%s)",
            ziel,
            fertig.returncode,
            (fertig.stderr or "").strip()[:120],
        )
        return []
    return [zeile.strip() for zeile in fertig.stdout.splitlines() if zeile.strip()]


_TMUX_FENSTER_ORIG = tmux_fenster


def _fenster_fuer_sitzung(spec: int, sitzung: str) -> list[str] | None:
    """Ruft :func:`tmux_fenster` für ``sitzung``; respektiert 1-Arg-Test-Nähte."""
    if tmux_fenster is _TMUX_FENSTER_ORIG:
        return tmux_fenster(spec, sitzung=sitzung)
    return tmux_fenster(spec)


def laeuft_noch(ticket: int, fenster: list[str] | None, wt_zeit: float | None, jetzt: datetime) -> str:
    """Kurzgrund, wenn an diesem Ticket noch jemand baut — sonst ``""``.

    Mit tmux entscheidet allein die Fensterliste (``bau <N>``). Ohne tmux zählt
    eine frische Worktree-Spur wie in :func:`regel_verwaist`, nur mit kurzer Frist.
    """
    if fenster is not None:
        return f"tmux-Fenster »bau {ticket}«" if f"bau {ticket}" in fenster else ""
    if wt_zeit is None:
        return ""
    ruhe = jetzt - datetime.fromtimestamp(wt_zeit, timezone.utc)
    if ruhe <= timedelta(minutes=LAEUFT_MIN):
        return f"frische Worktree-Spur ({ruhe.total_seconds() / 60:.0f} min alt)"
    return ""


#: Wurzel des Skills; ``skripte/bau.py`` darin ist der eigentliche Launcher.
_SKILL_DEFAULT = Path(__file__).resolve().parent.parent
_SKILL = _SKILL_DEFAULT


def _skill_ort() -> Path:
    """Aktiver Skill-Ordner für Startzeilen: ``TO_SPAWN_HOME`` (falls gesetzt), sonst ``_SKILL``.

    Lenkt ein Test ``capo._SKILL`` um, gewinnt diese Test-Naht (#277/#786/#846).
    """
    if _SKILL != _SKILL_DEFAULT:
        return _SKILL
    return config.skill_ordner()


def _bau_skript(repo: Path) -> Path:
    """Welche ``bau.py`` ein Start OHNE Vorspann ruft (lokal, Windows-Tab): ``scripts/bau.py``
    des Repos (Weiterleitung, setzt ``TO_SPAWN_REPO``/``TO_SPAWN_HOME``), sonst die des Skills.

    Die tmux-Zeile :func:`bau_startzeile` nimmt dagegen immer die des Skills: sie setzt den
    Vorspann selbst und darf weder von der Weiterleitung noch von deren ``TO_SPAWN_HOME``-
    Vorgabe abhängen (#277). Der Unterschied ist Absicht, festgehalten in
    ``tests/test_startzeile_277.py``.
    """
    im_repo = repo / "scripts" / "bau.py"
    if im_repo.is_file():
        return im_repo
    return _skill_ort() / "skripte" / "bau.py"


def fenster_log(repo: Path, ticket: int) -> Path:
    """Log eines tmux-Bau-Fensters: stderr von ``bau.py`` und Exit-Code bei Fehler (#277)."""
    return repo / ".to-spawn" / f"fenster_{ticket}.log"


def bau_startzeile(repo: Path, ticket: int, auftrag: str = "", *schalter: str) -> str:
    """Shell-Zeile, mit der ein tmux-Fenster eine Bau-Session startet (``bash -lc``).

    Einziger Ort für Ablösung (respawn), Folge-Runde und Aufpasser (#419). Ruft ``bau.py`` des Skills direkt
    mit diesem Python auf, wie ``skripte/spawn_srv.sh`` (#257) — nie die Shell-Funktion
    ``bau`` aus ``~/.bashrc`` oder die ``.venv`` des Repos, die ein Spec-Klon nicht hat
    (#277). Vorspann (``REPO``/``TO_SPAWN_HOME``/``TO_SPAWN_REPO``/``TO_SPAWN_MASCHINE``)
    kommt gebündelt aus :func:`sitzung_env.shell_vorspann` (#212/#846). Die
    Konfiguration der Session liefert allein ``bau.py``. ``schalter`` hängt weitere
    ``bau.py``-Schalter an (respawn #431: ``--ohne-prompt``). stderr von ``bau.py`` geht
    weiter ins Fenster und zusätzlich in :func:`fenster_log`; endet ``bau.py`` mit Fehler,
    schließt sich das Fenster — Meldung und Exit-Code bleiben dort lesbar.
    """
    skill = _skill_ort()
    auftrag_kurz = _kurz(auftrag, AUFTRAG_MAX_ZEICHEN) if auftrag.strip() else ""
    vorspann = sitzung_env.shell_vorspann(repo, skill_home=skill, auftrag=auftrag_kurz)
    bau = [sys.executable or "python3", str(skill / "skripte" / "bau.py"), str(ticket)]
    log_pfad = fenster_log(repo, ticket)
    log = shlex.quote(str(log_pfad))
    # stderr über tee ins Log, Original bleibt auf fd 3 fürs Fenster (Wartezeilen, #431).
    # Bei Fehler wartet das Fenster höchstens 3 s auf tee, damit die Meldung von bau.py
    # vor dem Exit-Code steht. Hält ein Rest-Kind von bau.py stderr offen, endet das
    # Fenster trotzdem; dessen späte Ausgabe landet dann hinter der Exit-Zeile (PR 293).
    tee = f"exec 3>&2 2> >(tee -a {log} >&3); tee_pid=$!"
    spur = (
        f'rc=$?; exec 2>&3 3>&-; [ "$rc" -eq 0 ] || {{ '
        f'timeout 3 tail -s 0.1 --pid="$tee_pid" -f /dev/null 2>/dev/null; '
        f'echo "$(date -Is) bau.py #{ticket} Exit $rc" >> {log}; }}; exit "$rc"'
    )
    ordner = shlex.quote(str(log_pfad.parent))
    lauf = shlex.join([*bau, "--sofort", *schalter])
    return f"{vorspann} mkdir -p {ordner}; {tee}; {lauf}; {spur}"


def folge_befehl(
    repo: Path,
    spec: int,
    ticket: int,
    fenster: list[str] | None,
    auftrag: str = "",
    *,
    sitzung: str | None = None,
) -> list[str]:
    """Startbefehl der Folge-Runde: tmux-Fenster wie ``spawn_srv.sh``, ohne tmux lokal.

    Der Vorspann (``REPO``/``TO_SPAWN_HOME``) ist derselbe wie in
    ``skripte/spawn_srv.sh`` — sonst startet ``bau`` in einem anderen Repo (#212).
    ``auftrag`` (#285) reicht den Grund der Wiederöffnung über ``BAU_AUFTRAG``
    durch — als Umgebungsvariable, damit der Startbefehl selbst gleich bleibt.
    """
    if fenster is None:
        return [sys.executable, str(_bau_skript(repo)), str(ticket), "--sofort"]
    from . import tmux_aufruf

    ziel = sitzung or tmux_aufruf.sitzung_aufloesen(repo, spec)[0]
    innen = bau_startzeile(repo, ticket, auftrag)
    kopf = ["new-window", "-t", f"={ziel}"] if fenster else ["new-session", "-d", "-s", ziel]
    return [
        *_tmux_befehl(),
        *kopf,
        "-n",
        f"bau {ticket}",
        "-c",
        str(repo),
        "bash",
        "-lc",
        innen,
    ]


def folge_umgebung(auftrag: str) -> dict[str, str]:
    """Umgebung der lokalen Folge-Runde: Auftrag als ``BAU_AUFTRAG`` (#285)."""
    if not auftrag.strip():
        return dict(os.environ)
    return {**os.environ, "BAU_AUFTRAG": _kurz(auftrag, AUFTRAG_MAX_ZEICHEN)}


def ereignis_vorfall(art: str, ticket: int, jetzt: datetime) -> vorfall.Vorfall:
    """Stillstand ohne Regel-Verstoß (#284/#285) als Vorfall für den Fehlerkatalog.

    Der Fall selbst steht schon am Ticket (Kommentar, Mail, Bau-Log-Entscheidung);
    hier entsteht nur die Katalog-Zeile mit den festen Worten aus REGEL_VORFALL,
    damit :func:`katalog_pflegen` jede Art genau einmal lernt.
    """
    klasse, symptom, ursache, loesung = REGEL_VORFALL.get(art, VORFALL_UNBEKANNT)
    return vorfall.Vorfall(
        klasse=klasse,
        symptom=symptom,
        ursache=ursache,
        loesung=loesung,
        beispiel=f"#{ticket} {jetzt.astimezone():%d.%m.%Y}",
        regel=art,
        ticket=str(ticket),
        quelle="capo",
    )


def _starte_folge_runde(repo: Path, ticket: int, befehl: list[str], ueber_tmux: bool, auftrag: str = "") -> str:
    """Folge-Runde starten; ``""`` = geklappt, sonst der Grund des Fehlschlags.

    Vor einem tmux-Start prüft :func:`tmux_aufruf.sitzung_fremd` den Besitzer der
    Ziel-Session (#457), damit nie ein Fenster in ein fremdes Repo gelegt wird.
    """
    if ueber_tmux:
        from . import tmux_aufruf

        ziel = tmux_aufruf.sitzung_aus_befehl(befehl)
        if ziel:
            fremd = tmux_aufruf.sitzung_fremd(ziel, repo)
            if fremd:
                return fremd
        fertig = starter.starte(
            befehl,
            warten=True,
            notnagel=None,
            capture_output=True,
            **konsole.UNTERPROZESS_TEXT,
            timeout=TMUX_ZEIT_S,
            check=False,
        )
        if fertig.returncode == 0:
            return ""
        return f"tmux Exit {fertig.returncode}: {(fertig.stderr or '').strip()[:120]}"
    ordner = repo / ".to-spawn"
    ordner.mkdir(parents=True, exist_ok=True)
    protokoll = ordner / f"folgerunde-{ticket}.log"
    with protokoll.open("a", encoding="utf-8") as fh:
        starter.starte(
            befehl,
            cwd=repo,
            notnagel=None,
            env=folge_umgebung(auftrag),
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    return ""


def _folge_runde(
    repo: Path,
    konfig: dict[str, Any],
    gh_repo: str,
    spec: int,
    n: int,
    funde: list[Verstoss],
    folgerunden: dict[str, int],
    *,
    wt_zeit: float | None,
    jetzt: datetime,
    dry_run: bool,
    gelernt: list[vorfall.Vorfall],
) -> list[str]:
    """Nach dem Wiederöffnen weiterbauen lassen (#284).

    Baut noch jemand am Ticket, passiert nichts — diese Session sieht den
    Aufseher-Kommentar. Sonst startet hier die nächste Runde ``bau <N> --sofort``,
    höchstens ``waechter.folgerunden_max`` Mal je Ticket.
    """
    waechter = konfig.get("waechter", {}) if isinstance(konfig.get("waechter"), dict) else {}
    grenze = folgerunden_max(waechter)
    grund = "; ".join(f"{f.regel} — {f.text}" for f in funde) or "Aufseher-Verstoß"
    from . import tmux_aufruf

    sitzung, fremd = tmux_aufruf.sitzung_aufloesen(repo, spec)
    if fremd:
        log.warning("Folge-Runde für #%s verweigert: %s", n, fremd)
        zeilen = [f"#{n} {fremd}"]
        zeilen += _melde(
            repo,
            konfig,
            gh_repo,
            dry_run,
            "session_tot",
            f"Folge-Runde blockiert #{n}",
            f"Ticket #{n} ist wieder offen, aber die Folge-Runde wurde verweigert ({fremd}) — "
            f"es baut niemand weiter. Grund der Wiederöffnung: {grund}",
            f"folgerunde_fehler|{n}|{jetzt.astimezone():%Y-%m-%d}",
        )
        return zeilen
    fenster = _fenster_fuer_sitzung(spec, sitzung)
    laeuft = laeuft_noch(n, fenster, wt_zeit, jetzt)
    if laeuft:
        return [f"#{n} läuft bereits ({laeuft}) — keine Folge-Runde"]
    schon = int(folgerunden.get(str(n), 0) or 0)
    if schon >= grenze:
        zeilen = [f"#{n} Folge-Runden-Grenze erreicht ({schon}/{grenze}) — keine neue Runde, ein Mensch muss ran"]
        zeilen += _melde(
            repo,
            konfig,
            gh_repo,
            dry_run,
            "session_tot",
            f"Folge-Runden-Grenze #{n}",
            f"Ticket #{n} ist wieder offen, aber die Grenze von {grenze} Folge-Runden "
            f"ist erreicht — es baut niemand weiter. Grund der Wiederöffnung: {grund}",
            f"folgerunden_max|{n}|{schon}",
        )
        if not dry_run:
            gelernt.append(ereignis_vorfall("folgerunden_grenze", n, jetzt))
        return zeilen
    befehl = folge_befehl(repo, spec, n, fenster, grund, sitzung=sitzung)
    if dry_run:
        return [f"#{n} [Probe] würde Folge-Runde starten: {shlex.join(befehl)}"]
    try:
        fehler = _starte_folge_runde(repo, n, befehl, fenster is not None, grund)
    except (OSError, subprocess.SubprocessError) as ausnahme:
        fehler = str(ausnahme)
    if fehler:
        log.warning("Folge-Runde für #%s nicht gestartet: %s", n, fehler)
        zeilen = [f"#{n} FEHLER: Folge-Runde nicht gestartet ({fehler})"]
        zeilen += _melde(
            repo,
            konfig,
            gh_repo,
            dry_run,
            "session_tot",
            f"Folge-Runde gescheitert #{n}",
            f"Ticket #{n} ist wieder offen, aber der Start der Folge-Runde ist gescheitert "
            f"({fehler}) — es baut niemand weiter. Grund der Wiederöffnung: {grund}",
            f"folgerunde_fehler|{n}|{schon}|{jetzt.astimezone():%Y-%m-%d}",
        )
        return zeilen
    folgerunden[str(n)] = schon + 1
    weg = "tmux-Fenster" if fenster is not None else "lokal"
    return [f"#{n} Folge-Runde gestartet ({weg}, {schon + 1}/{grenze}) — Auftrag: {_kurz(grund)}"]


# --- Checkpoint-Annahme nach Doktrin (#285) -------------------------------------

#: Minuten ohne Antwort des Menschen, nach denen der Vorschlag der Session gilt.
CHECKPOINT_FRIST_MIN = 60.0


def annahme_grenze(n: str | int, befehl: str = "to_spawn.py", frage_id: str = "") -> str:
    """Was die Annahme nach der Frist deckt — die eine Stelle der Regel (#490).

    Der Annahme-Kommentar des Aufsehers (:func:`_checkpoint`) und der Start-Text der
    Bau-Session (Platzhalter ``{ANNAHME_GRENZE}`` in ``skripte/bau.py``) lesen nur diesen
    Text. ``befehl`` = Aufruf von ``to_spawn.py``; nennt den einen Weg für das Live-Ja.
    ``befehl``/``frage_id`` bleiben für die Aufrufer; der Kurzbefehl (#531) braucht nur die Ticket-Nummer.
    """
    befehl_ja = briefkasten.ja_aufforderung(n)
    return (
        "Die Annahme nach der Frist deckt nur die Entscheidungs-Liste (Abnahme „passt“), "
        "nie den Live-Schritt: Live braucht immer ein eigenes Ja des Menschen, nur vom "
        f"Menschen selbst im Eingabefeld: {befehl_ja}."
    )


#: „Vorschlag: …“ in einem Issue-Kommentar — alles danach ist die vorgeschlagene Wahl.
_VORSCHLAG = re.compile(r"Vorschlag\s*:\s*(.+)", re.IGNORECASE | re.DOTALL)
#: Anfang jedes Aufseher-Kommentars — eigene Kommentare sind nie „Antwort des Menschen“.
WAECHTER_KOPF = befund.KOPF
#: Frühere Marke vor der Umbenennung (#428) — alte Issue-Kommentare bleiben eigene Kommentare.
ALTE_AUFSEHER_KOEPFE: tuple[str, ...] = ("Wächter:",)  # #428-alt
#: Alle Marken des Aufsehers (neu + alt) — z. B. für den Leitstand.
AUFSEHER_KOEPFE: tuple[str, ...] = (WAECHTER_KOPF, *ALTE_AUFSEHER_KOEPFE)
#: Anfang der Issue-Kommentare des Aufpassers (to_spawn/aufpasser.py), gleiches gh-Konto.
AUFPASSER_KOPF = "Aufpasser:"
#: Anfang jedes Issue-Kommentars einer Bau-Session (#402). Session, Aufseher und der Mensch
#: kommentieren mit demselben gh-Konto — der Login trennt sie nicht, nur diese Marke.
#: Der Bau-Prompt bekommt sie über den Platzhalter ``{SESSION_KOPF}`` (skripte/bau.py).
SESSION_KOPF = "Bau-Session:"
#: Marken aller eigenen Kommentare ohne Bau-Session (die prüft ``_ist_session_kommentar``):
#: so beginnende Kommentare sind nie „Antwort des Menschen“.
EIGENE_KOEPFE: tuple[str, ...] = (*AUFSEHER_KOEPFE, AUFPASSER_KOPF)
#: Anfang des Annahme-Kommentars nach der Marke — schreibt :func:`_checkpoint`, liest
#: :func:`annahme_schon_gesetzt` (#507).
ANNAHME_ANFANG = "Annahme nach"
#: Anfang von ``grund`` der Bau-Log-Zeile ``entscheidung``, die die Annahme selbst schreibt
#: (:func:`_checkpoint`). Diese Zeile ist keine Spur der Session (#511), siehe
#: :func:`_ist_session_zeile`.
ANNAHME_GRUND = "Aufseher-Annahme nach"


@dataclass
class Vorschlag:
    """Was die Session selbst vorgeschlagen hat (Bau-Log oder Issue-Kommentar)."""

    frage: str
    wahl: str
    grund: str
    zeit: datetime
    autor: str = ""


def checkpoint_frist(waechter: dict[str, Any]) -> float:
    """``waechter.checkpoint_frist_min``; fehlt der Wert, gilt :data:`CHECKPOINT_FRIST_MIN`."""
    roh = waechter.get("checkpoint_frist_min")
    if roh is None or (isinstance(roh, str) and not str(roh).strip()):
        return CHECKPOINT_FRIST_MIN
    try:
        wert = float(roh)
    except (TypeError, ValueError):
        log.warning(
            "waechter.checkpoint_frist_min=%r ist keine Zahl — nehme %s min.",
            roh,
            CHECKPOINT_FRIST_MIN,
        )
        return CHECKPOINT_FRIST_MIN
    return max(wert, 0.0)


#: ``author_association``-Werte, deren Kommentare der Checkpoint überhaupt liest (#402).
#: Ein Dritter im öffentlichen Repo könnte sonst mit der Marke :data:`SESSION_KOPF`
#: einen Vorschlag setzen oder ``seit`` hinter die Antwort des Menschen schieben.
BETEILIGTE_ROLLEN = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def issue_kommentare(gh_repo: str, ticket: int) -> list[dict[str, Any]] | None:
    """Kommentare eines Issues von Repo-Beteiligten (``None`` = Abfrage gescheitert).

    Nur Kommentare mit ``author_association`` aus :data:`BETEILIGTE_ROLLEN` kommen
    zurück — für Vorschlag, Fragezeit und Antwort gleichermaßen. Fehlt das Feld,
    zählt der Kommentar nicht (sichere Richtung: lieber keine Annahme). Alle Seiten
    (``--paginate --slurp`` liefert eine Liste je Seite), sonst fehlt alles hinter dem
    100. Kommentar (#339).
    """
    daten = gh.json_lauf(
        [
            "api",
            f"repos/{gh_repo}/issues/{ticket}/comments?per_page=100",
            "--paginate",
            "--slurp",
        ]
    )
    if not isinstance(daten, list):
        return None
    if all(isinstance(seite, list) for seite in daten):
        daten = [e for seite in daten for e in seite]
    beteiligt = [e for e in daten if isinstance(e, dict) and e.get("author_association") in BETEILIGTE_ROLLEN]
    if len(beteiligt) < len(daten):
        log.info(
            "#%s: %d Kommentar(e) von Nicht-Beteiligten ignoriert",
            ticket,
            len(daten) - len(beteiligt),
        )
    return beteiligt


def _kommentar_teile(eintrag: dict[str, Any]) -> tuple[str, str, datetime | None]:
    """(Autor, Text, Zeit) eines Kommentars."""
    nutzer = eintrag.get("user")
    autor = str(nutzer.get("login") or "") if isinstance(nutzer, dict) else ""
    return autor, str(eintrag.get("body") or ""), _zeit(eintrag.get("created_at"))


def _ist_session_kommentar(text: str) -> bool:
    """Trägt der Kommentar die Marke :data:`SESSION_KOPF` am Anfang?"""
    return text.lstrip().startswith(SESSION_KOPF)


def _ist_session_zeile(z: dict[str, Any], typen: tuple[str, ...]) -> bool:
    """Bau-Log-Zeile eines der ``typen``, die von der Session stammt (#511).

    Die eigene ``entscheidung``-Zeile der Annahme (``grund`` beginnt mit
    :data:`ANNAHME_GRUND`) zählt nie — sonst gilt sie nach der Frist als neuer Vorschlag
    mit neuer Frist (Probe-Lauf 2, #15). Erkannt am Text, damit auch Zeilen von vor #511
    nicht zählen.
    """
    eigene_annahme = str(z.get("grund") or "").startswith(ANNAHME_GRUND)
    return z.get("typ") in typen and not eigene_annahme


def checkpoint_vorschlag(zeilen: list[dict[str, Any]], kommentare: list[dict[str, Any]]) -> Vorschlag | None:
    """Jüngster eigener Vorschlag der Session — ``None`` heißt: nie raten.

    Zählt eine Bau-Log-Zeile ``entscheidung`` mit Wahl und ein Issue-Kommentar, der
    mit :data:`SESSION_KOPF` beginnt und „Vorschlag:“ enthält. Der Login zählt nicht:
    Session und der Mensch schreiben mit demselben gh-Konto (#402). Kommentare ohne Marke
    (auch Aufseher-Kommentare) sind nie ein Session-Vorschlag.
    """
    kandidaten: list[Vorschlag] = []
    for z in zeilen:
        if not _ist_session_zeile(z, ("entscheidung",)):
            continue
        frage, wahl, grund = entscheidung_teile(z)
        zeit = _zeit(z.get("ts"))
        if wahl and zeit:
            kandidaten.append(Vorschlag(frage, wahl, grund, zeit))
    for eintrag in kommentare:
        autor, text, zeit = _kommentar_teile(eintrag)
        if zeit is None or not _ist_session_kommentar(text):
            continue
        treffer = _VORSCHLAG.search(text)
        if treffer:
            kandidaten.append(
                Vorschlag(
                    frage=_kurz(text[: treffer.start()]),
                    wahl=_kurz(treffer.group(1), 400),
                    grund="",
                    zeit=zeit,
                    autor=autor,
                )
            )
    return max(kandidaten, key=lambda v: v.zeit) if kandidaten else None


def checkpoint_frage_zeit(zeilen: list[dict[str, Any]], kommentare: list[dict[str, Any]]) -> datetime | None:
    """Wann hat die Session zuletzt etwas gefragt/gemeldet? ``None`` = keine Spur.

    Spuren der Session sind nur Bau-Log-Zeilen ``blockiert``/``entscheidung`` und
    Kommentare mit :data:`SESSION_KOPF`. Hinweise des Menschen und Aufseher-Kommentare sind
    keine Frage (#451) — sonst meldet capo „Checkpoint wartet“ für ein Ticket, an dem
    nie eine Session gebaut hat. Ohne Spur wartet niemand, das Label allein löst nichts aus.
    """
    zeiten = [_zeit(z.get("ts")) for z in zeilen if _ist_session_zeile(z, ("blockiert", "entscheidung"))]
    for eintrag in kommentare:
        _, text, zeit = _kommentar_teile(eintrag)
        if _ist_session_kommentar(text):
            zeiten.append(zeit)
    echte = [z for z in zeiten if z is not None]
    return max(echte) if echte else None


def mensch_antwort(kommentare: list[dict[str, Any]], seit: datetime) -> str:
    """Login des ersten Kommentars nach ``seit`` ohne Session- oder Aufseher-Marke.

    ``""`` = keine Antwort. Der Login trennt nicht (gleiches gh-Konto, #402) — nur die
    Marken :data:`SESSION_KOPF` und :data:`EIGENE_KOEPFE` (Aufseher, Aufpasser).
    """
    for eintrag in sorted(
        kommentare,
        key=lambda e: _kommentar_teile(e)[2] or datetime.min.replace(tzinfo=timezone.utc),
    ):
        autor, text, zeit = _kommentar_teile(eintrag)
        if zeit is None or zeit <= seit:
            continue
        if text.lstrip().startswith(EIGENE_KOEPFE) or _ist_session_kommentar(text):
            continue
        return autor or "jemand"
    return ""


davids_antwort = mensch_antwort  # Übergangs-Alias (#325)


def annahme_schon_gesetzt(kommentare: list[dict[str, Any]]) -> bool:
    """Steht am Ticket schon eine Annahme des Aufsehers? Dann nie eine zweite (#507).

    Die eine Stelle der Regel „höchstens einmal je Ticket, nie nach eigener Rücknahme“:
    eine Rücknahme setzt eine frühere Annahme voraus, also reicht die Annahme selbst als
    Merkmal. Gelesen wird das Ticket, nicht der Zustand des Aufsehers — der geht bei einem
    Neustart verloren (Probe-Lauf 2, #15: 11:34 Annahme, 12:08 Rücknahme, 12:51 erneut).
    Nur Kommentare, die mit einer Aufseher-Marke beginnen — ein Zitat (``> Aufseher: …``)
    stammt vom Menschen.

    ponytail: liest nur Kommentare aus :func:`issue_kommentare`; schreibt der Aufseher unter
    einem Konto, das nicht in :data:`BETEILIGTE_ROLLEN` fällt, sieht er seine Annahme nicht
    (heute geteiltes Konto) — dann eigenes Konto als beteiligt zulassen (#511).
    """
    for eintrag in kommentare:
        text = _kommentar_teile(eintrag)[1].lstrip()
        for kopf in AUFSEHER_KOEPFE:
            if text.startswith(kopf) and text[len(kopf) :].lstrip().startswith(ANNAHME_ANFANG):
                return True
    return False


def mensch_noetig_was(eintrag: dict[str, Any]) -> str:
    """Kurztext eines „Mensch nötig“-Eintrags; alte Zeilen ohne ``grund`` gelten als beleg_rot.

    Fehlt ``fix_runde`` (alte Einträge), steht „?“ statt „None“ (#402).
    """
    if eintrag.get("grund") == "review_offen":
        return "Review angefordert, aber kein Beleg geschrieben"
    runden = eintrag.get("fix_runde")
    return f"Review-Beleg nach {'?' if runden is None else runden} Fixrunden noch rot"


def _checkpoint(
    repo: Path,
    konfig: dict[str, Any],
    gh_repo: str,
    spec: int,
    n: int,
    zeilen: list[dict[str, Any]],
    erledigt: set[str],
    *,
    checkpoint: str,
    jetzt: datetime,
    dry_run: bool,
    gelernt: list[vorfall.Vorfall],
) -> list[str]:
    """Nacht-Checkpoint: nach der Frist gilt der Vorschlag der Session (#285).

    Der Mensch kann jede Annahme kippen — der Aufseher kommentiert sie am Ticket, schreibt
    sie ins Bau-Log und in ``entscheidungen_<S>.md`` und schickt eine Mail. Ohne
    erkennbaren eigenen Vorschlag der Session wird nichts angenommen, nur gemeldet.
    Solange ein Blocker offen ist, ruht der Checkpoint (keine Zeile); ist der
    Blocker-Stand nicht lesbar, kommt eine FEHLER-Zeile statt einer Annahme (#451).
    """
    waechter = konfig.get("waechter", {}) if isinstance(konfig.get("waechter"), dict) else {}
    frist = checkpoint_frist(waechter)
    kommentare = issue_kommentare(gh_repo, n)
    if kommentare is None:
        return [f"#{n} FEHLER: Kommentare nicht lesbar — Checkpoint ungeprüft"]
    vorschlag = checkpoint_vorschlag(zeilen, kommentare)
    frage_zeit = checkpoint_frage_zeit(zeilen, kommentare)
    if vorschlag is None and frage_zeit is None:
        return []  # Label gesetzt, aber noch keine Frage gestellt
    if annahme_schon_gesetzt(kommentare):
        gemeldet = f"{n}|annahme_steht"  # #511: Meldezeile einmal, nicht bei jedem Lauf
        if gemeldet in erledigt:
            return []
        erledigt.add(gemeldet)
        steht = "Annahme steht schon am Ticket — keine zweite, der Mensch antwortet selbst"
        return [f"#{n} Checkpoint: {steht}"]
    # Erst nach der Spur fragen: spart den gh-Aufruf und macht capo ohne Frage nie rot.
    blocker = gh.blocked_by(gh_repo, str(n))
    if blocker is None:
        return [f"#{n} FEHLER: Blocker nicht lesbar — Checkpoint ungeprüft"]
    if not all(isinstance(b, dict) and b.get("state") for b in blocker):
        return [f"#{n} FEHLER: Blocker-Format unbekannt — Checkpoint ungeprüft"]
    if any(not ticket_zustand.fertig_gebaut(b) for b in blocker):  # #1527, #279
        return []  # #451: noch blockiert — Checkpoint ruht, bis alle Blocker zu sind
    seit = vorschlag.zeit if vorschlag else frage_zeit
    if seit is None:
        return []
    wartet = (jetzt - seit).total_seconds() / 60
    if wartet <= frist:
        return [f"#{n} Checkpoint wartet ({wartet:.0f} von {frist:.0f} min)"]
    antwort = mensch_antwort(kommentare, seit)
    if antwort:
        # Ohne Marke heißt nur: nicht von einer Session mit neuem Prompt — kann der Mensch
        # sein oder eine Session mit altem Prompt („Probesitz beendet:“). Sichere Richtung.
        return [
            f"#{n} Checkpoint: Kommentar ohne Session-Marke von {antwort} — als Antwort gewertet "
            f"({antwort} hat geantwortet oder Session mit altem Prompt), Vorschlag nicht übernommen"
        ]
    if vorschlag is None:
        zeilen_aus: list[str] = [
            f"#{n} Checkpoint {wartet:.0f} min offen, aber kein Vorschlag der Session "
            f"— keine Annahme (nie raten), gemeldet"
        ]
        zeilen_aus += _melde(
            repo,
            konfig,
            gh_repo,
            dry_run,
            "checkpoint_offen",
            f"Checkpoint ohne Vorschlag #{n}",
            f"Ticket #{n} wartet seit {wartet:.0f} min auf die Antwort des Menschen, aber die "
            "Session hat keinen eigenen Vorschlag hinterlassen — der Aufseher rät nicht.",
            f"checkpoint_offen|{n}|{seit.isoformat()}",
        )
        if not dry_run:
            gelernt.append(ereignis_vorfall("checkpoint_offen", n, jetzt))
        return zeilen_aus
    schluessel = f"{n}|checkpoint_annahme|{seit.isoformat()}"
    if schluessel in erledigt:
        return [f"#{n} Checkpoint schon angenommen — nichts zu tun"]
    if dry_run:
        return [f"#{n} [Probe] würde Vorschlag annehmen: {_kurz(vorschlag.wahl)}"]
    text = f"{WAECHTER_KOPF} {ANNAHME_ANFANG} {frist:.0f} min nach Doktrin — der Mensch kann kippen. Vorschlag: {vorschlag.wahl}"
    befehl = f"python {(_SKILL / 'to_spawn.py').as_posix()}"
    # #501: echte Kennung nur, wenn genau EINE Klasse-2-Frage offen ist (sonst könnte es die falsche sein).
    klasse_2 = [o for o in briefkasten.offene([n], hauptbaum=repo) if o["klasse"] == "2"]
    live_id = klasse_2[0]["id"] if len(klasse_2) == 1 else ""
    text += f"\n\n{annahme_grenze(n, befehl, live_id)}"  # #490: Live-Grenze
    if not befund.kommentiere(n, gh_repo, text):
        return [f"#{n} FEHLER: Checkpoint-Annahme nicht kommentiert"]
    erledigt.add(schluessel)
    grund = f"{ANNAHME_GRUND} {frist:.0f} min ohne Antwort des Menschen (Doktrin, kippbar)" + (
        f" · {vorschlag.grund}" if vorschlag.grund else ""
    )
    frage = vorschlag.frage or f"Checkpoint #{n}"
    bau_log.schreibe(repo, n, "entscheidung", frage=frage, wahl=vorschlag.wahl, grund=grund)
    datei = entscheidung_anhaengen(repo, spec, n, frage, vorschlag.wahl, grund, jetzt)
    ausgabe = [f"#{n} Checkpoint-Annahme nach {wartet:.0f} min: {_kurz(vorschlag.wahl)} (Bau-Log + {datei.name})"]
    ausgabe += _melde(
        repo,
        konfig,
        gh_repo,
        dry_run,
        "checkpoint_annahme",
        f"Checkpoint angenommen #{n}",
        f"Ticket #{n}: {frage}\nAngenommen nach {frist:.0f} min ohne Antwort: "
        f"{vorschlag.wahl}\nDer Mensch kann die Entscheidung jederzeit kippen.",
        schluessel,
    )
    gelernt.append(ereignis_vorfall("checkpoint_annahme", n, jetzt))
    if _gh_ok(["issue", "edit", str(n), "--repo", gh_repo, "--remove-label", checkpoint]):
        ausgabe.append(f"#{n} Label {checkpoint} entfernt — die Kette läuft weiter")
    else:
        ausgabe.append(f"#{n} FEHLER: Label {checkpoint} nicht entfernt")
    return ausgabe


# --- Tick -----------------------------------------------------------------------


def _zustand_name(kennung: str, spec: int) -> str:
    """Dateiname der capo-Zustandsdatei; ``kennung="*"`` ergibt das Glob-Muster aller Repos."""
    return f"{kennung}_{spec}.json"


def _zustand_datei(repo: Path, gh_repo: str, spec: int) -> Path:
    return melder.zustand_ordner() / _zustand_name(melder.repo_kennung(repo, gh_repo), spec)


def _repo_vermerk(repo: Path) -> str:
    """Wofür capo eine Spec führt: das gemeinsame Git-Verzeichnis (#871).

    Haupt-Repo und alle seine Worktrees ergeben denselben Wert. Kein Git-Repo bzw. git fehlt →
    Rückfall auf den aufgelösten Pfad. Vermerken (:func:`_tick`) und Finden nutzen nur diese Funktion.
    """
    code, aus = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if code == 0 and aus.strip():
        return str(Path(aus.strip()).resolve())
    return str(repo.resolve())


def _gefuehrte_zustand_datei(repo: Path, gh_repo: str, spec: int) -> Path | None:
    """Zustandsdatei der Spec, auch wenn capo mit abweichendem ``--gh-repo`` oder im Haupt-Repo lief (#871)."""
    datei = _zustand_datei(repo, gh_repo, spec)
    if datei.is_file():
        return datei
    if not datei.parent.is_dir():
        return None
    ziel = _repo_vermerk(repo)
    treffer: list[tuple[float, Path]] = []
    for kandidat in datei.parent.glob(_zustand_name("*", spec)):
        try:
            daten = json.loads(kandidat.read_text(encoding="utf-8"))
            geaendert = kandidat.stat().st_mtime
        except (OSError, ValueError):
            continue  # fremde kaputte Datei: Zugehörigkeit unklar, nicht sperren
        if isinstance(daten, dict) and daten.get("repo") == ziel:
            treffer.append((geaendert, kandidat))
    return max(treffer)[1] if treffer else None  # mehrere Vermerke: die jüngste Datei zählt


def fertig_urteil(repo: Path, spec: int, gh_repo: str = "") -> str | None:
    """capos Urteil „Spec fertig“ für Aufrufer außerhalb des Ticks (#889).

    ``None`` = capo führt die Spec nicht (keine Zustandsdatei, z. B. Handlauf) · ``""`` = capo führt
    sie, meldet aber (noch) nicht fertig · sonst der Zeitpunkt ``spec_fertig_seit``. Das Urteil
    selbst fällt nur :func:`tick` (``erg.fertig``); hier wird es nur gelesen.
    """
    datei = _gefuehrte_zustand_datei(repo, gh_repo, spec)
    if datei is None:
        _fremder_zustand_melden(_zustand_datei(repo, gh_repo, spec), spec)
        return None
    return str(melder.lade_json(datei).get("spec_fertig_seit") or "")


def fertig_luecke(repo: Path, spec: int, gh_repo: str = "") -> tuple[Path, str] | None:
    """Warum capo die Spec (noch) nicht fertig meldet — für die Sperre in ``abschluss_paket.py`` (#889).

    ``None`` = keine Sperre (capo führt die Spec nicht oder meldet fertig) · sonst
    ``(Zustandsdatei, Grund)``. Kaputt oder unklar zählt als „nicht fertig“ (sichere Seite):
    Grund „nicht lesbar …“ bei kaputtem JSON, „Feld spec_fertig_seit fehlt“ sonst.
    """
    urteil = fertig_urteil(repo, spec, gh_repo)
    if urteil is None or urteil:
        return None
    datei = _gefuehrte_zustand_datei(repo, gh_repo, spec)
    if datei is None:
        return None
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        return datei, f"nicht lesbar ({type(fehler).__name__})"
    if not isinstance(daten, dict):
        return datei, "nicht lesbar (kein JSON-Objekt)"
    return datei, "Feld spec_fertig_seit fehlt"


def _fremder_zustand_melden(datei: Path, spec: int) -> None:
    """Liegt capos Zustand der Spec unter anderem Repo-Namen (``--gh-repo`` ≠ origin), Log-Zeile (#889).

    Ohne Meldung fiele die Sperre still weg; gesperrt wird nicht, weil die Nummer auch zu einem
    anderen Repo gehören kann.
    """
    if not datei.parent.is_dir():
        return
    fremde = sorted(p.name for p in datei.parent.glob(f"*_{spec}.json"))
    if fremde:
        log.warning(
            "capo-Zustand %s fehlt, aber %s liegt da — Repo-Name weicht ab (--gh-repo ≠ origin?); "
            "keine Fertig-Sperre (#889).",
            datei,
            ", ".join(fremde),
        )


@contextlib.contextmanager
def sperre(datei: Path, warten_s: float = SPERRE_S) -> Iterator[bool]:
    """Sperre ``<datei>.lock`` für einen Tick; liefert ``False``, wenn ein anderer Lauf sie hält.

    Werkzeug (fcntl/msvcrt/O_EXCL) versteckt :func:`to_spawn.sperre.halte` (#95). Ein
    Sperrfehler außer „belegt“ (z. B. ENOLCK) gilt wie bisher als belegt: kein Tick.
    Sperr-Datei nicht öffenbar ⇒ :class:`to_spawn.sperre.SperreNichtOeffenbar` fliegt
    durch (#165), der Aufrufer meldet ``FEHLER`` statt „anderer Lauf“.
    """
    pfad = datei.with_name(datei.name + ".lock")
    with contextlib.ExitStack() as stapel:
        try:
            gehalten = stapel.enter_context(_sperre.halte(pfad, warten=warten_s))
        except _sperre.SperreNichtOeffenbar:
            raise
        except OSError as fehler:
            log.warning("Sperre %s nicht nehmbar (%s) — gilt als belegt.", pfad, fehler)
            gehalten = False
        yield gehalten


def karenz_minuten(waechter: dict[str, Any]) -> float:
    """``waechter.karenz_minuten``; fehlt der Wert, gilt :data:`KARENZ_MIN`, ``0`` = keine Karenz."""
    roh = waechter.get("karenz_minuten")
    if roh is None or (isinstance(roh, str) and not roh.strip()):
        return KARENZ_MIN
    try:
        wert = float(roh)
    except (TypeError, ValueError):
        log.warning("waechter.karenz_minuten=%r ist keine Zahl — nehme %s min.", roh, KARENZ_MIN)
        return KARENZ_MIN
    if wert < 0:
        log.warning("waechter.karenz_minuten=%r ist negativ — nehme 0 (keine Karenz).", roh)
        return 0.0
    return wert


def schliess_marke(issue: dict[str, Any]) -> str:
    """Kennung eines Schließ-Ereignisses: ``closed_at`` (``?`` ohne Zeit), offen = ``""``."""
    if not ticket_zustand.endgueltig_zu(issue):
        return ""
    return str(issue.get("closed_at") or "?")


def ist_ausgangsstand(
    n: int,
    issue: dict[str, Any],
    ausgang: dict[str, Any] | None,
    geschlossen_zeit: datetime | None,
    erster_tick: datetime,
) -> bool:
    """War dieses Schließen schon beim ersten Tick da?

    Maßgeblich ist der beim ersten Tick gemerkte Stand je Ticket (``ausgangsstand``
    in der Zustandsdatei) — kein Uhr-Vergleich: ``closed_at`` kommt von GitHub, der
    erste Tick von der lokalen Uhr, die vorgehen kann. Nur Tickets, die es beim
    ersten Tick noch nicht gab (oder Zustände aus älteren Versionen), fallen auf
    den Zeitvergleich zurück.
    """
    if ausgang is not None and str(n) in ausgang:
        marke = str(ausgang[str(n)] or "")
        return bool(marke) and marke == schliess_marke(issue)
    return geschlossen_zeit is None or geschlossen_zeit < erster_tick


def ist_sofort(konfig: dict[str, Any]) -> bool:
    """Sofort-Modus: keine Karenz, kein Ausgangsstand (Umgebung oder ``waechter.sofort``)."""
    if os.environ.get("TO_SPAWN_WAECHTER_SOFORT", "").strip() == "1":
        return True
    return bool(konfig.get("waechter", {}).get("sofort"))


def _sperr_wartezeit() -> float:
    roh = os.environ.get("TO_SPAWN_CAPO_SPERRE_S", "").strip()
    try:
        return float(roh) if roh else SPERRE_S
    except ValueError:
        log.warning("TO_SPAWN_CAPO_SPERRE_S=%r ist keine Zahl — nehme %s s.", roh, SPERRE_S)
        return SPERRE_S


def vorfall_aus_verstoss(repo: Path, fund: Verstoss, jetzt: datetime) -> vorfall.Vorfall | None:
    """Einen Verstoß als ``vorfall``-Zeile ins Bau-Log des Tickets schreiben.

    ``None`` heißt: derselbe Vorfall steht dort schon (zweiter Tick, gleiche Lage).
    Verstöße aus dem Ausgangsstand laufen hier bewusst nicht durch — sonst lernt
    der Katalog die Altlasten vor dem ersten Tick.
    """
    klasse, symptom, ursache, loesung = REGEL_VORFALL.get(fund.regel, VORFALL_UNBEKANNT)
    beispiel = f"#{fund.ticket} {jetzt.astimezone():%d.%m.%Y}"
    try:
        zeile = vorfall.schreibe(
            repo,
            fund.ticket,
            klasse=klasse,
            symptom=symptom,
            ursache=ursache,
            loesung=loesung,
            beispiel=beispiel,
            regel=fund.regel,
            quelle="capo",
        )
    except (OSError, ValueError) as fehler:  # ein Tick darf daran nie sterben
        log.warning("Vorfall zu #%s (%s) nicht notiert: %s", fund.ticket, fund.regel, fehler)
        return None
    if zeile is None:
        return None
    return vorfall.Vorfall(
        klasse=klasse,
        symptom=symptom,
        ursache=ursache,
        loesung=loesung,
        beispiel=beispiel,
        regel=fund.regel,
        ticket=str(fund.ticket),
        quelle="capo",
    )


def katalog_pflegen(repo: Path, konfig: dict[str, Any], vorfaelle: list[vorfall.Vorfall]) -> list[str]:
    """Neue Vorfälle in den Fehlerkatalog hängen; Rückgabe = Zeilen für den Tick.

    Der Katalog wird gegen parallele Aufseher gesperrt (zwei Specs, eine Datei).
    Ein Repo ganz ohne Katalog ist kein Fehler (Fremd-Repo, #257) — ein fehlender
    Abschnitt, eine kaputte Tabelle oder eine unschreibbare Datei schon: sonst
    meldet der Tick Erfolg, obwohl nichts gelernt wurde.
    """
    if not vorfaelle:
        return []
    pfad = vorfall.katalog_pfad(repo, konfig)
    with contextlib.ExitStack() as stapel:
        try:
            frei = stapel.enter_context(sperre(pfad, _sperr_wartezeit()))
        except _sperre.SperreNichtOeffenbar as fehler:
            return [f"FEHLER: Katalog — {fehler}"]
        if not frei:
            return ["Katalog: ein anderer Lauf hält die Datei — dieser Tick lässt sie aus"]
        erg = vorfall.in_katalog(pfad, vorfaelle)
    zeilen: list[str] = []
    if erg.nummern:
        zeilen.append(f"Katalog: {len(erg.nummern)} neue Zeile(n) — {' '.join(erg.nummern)}")
    elif erg.ohne_katalog:
        # Kein Dateiname in der Zeile: der Tick wertet jede Zeile mit „FEHLER“ als
        # roten Lauf, und der Dateiname trägt das Wort schon im Namen.
        zeilen.append("Katalog: dieses Repo führt keinen Fehlerkatalog — nichts eingetragen")
    elif not erg.probleme:
        zeilen.append("Katalog: nichts Neues")
    zeilen += [f"FEHLER: Katalog — {grund}" for grund in erg.probleme]
    return zeilen


#: Regeln, nach denen capo ein geschlossenes Ticket wieder öffnet (#448, #864).
REOPEN_REGELN = schliess_pruefer.CAPO_REGELN | frozenset({"vps_ungleich_origin"})


def reopen_funde(
    repo: Path,
    ref: str,
    n: int,
    eigene: list[Commit],
    belege: str,
    vps: dict[str, Any],
    kopf: str | None,
    alle_zeilen: list[dict[str, Any]],
    staging_stand: StagingStand | None = None,
    issue: dict[str, Any] | None = None,
) -> list[Verstoss]:
    """Alle Verstöße, die ein geschlossenes Ticket wieder öffnen.

    Die Schließ-Regeln laufen ausschließlich über :func:`schliess_pruefer.pruefe` (#864);
    ``regel_vps`` bleibt capos eigene Live-Drift-Prüfung (nur mit VPS-Konfig).
    """
    ergebnis = schliess_pruefer.pruefe(
        schliess_pruefer.Stand(
            repo=repo,
            ticket=n,
            ref=ref,
            belege_ordner=belege,
            eigene=eigene,
            form_pruefen=False,
            gruen_pruefen=False,
            beleg_auf_hauptzweig=False,
            git_lauf=_git,
        )
    )
    funde = [Verstoss(n, g.regel, g.text, kern=g.kern) for g in ergebnis.gruende]
    if vps.get("ssh"):
        vps_fund = regel_vps(repo, n, eigene, vps, kopf, alle_zeilen, staging_stand, issue)
        if vps_fund is not None:
            funde.append(vps_fund)
    return funde


def _gesehene_schliessungen(n: int, erledigt: set[str]) -> list[datetime]:
    """Schließ-Zeitpunkte von #n, die ein Tick gesehen hat (Schlüssel ``geschlossen|N|closed_at``)."""
    zeiten: list[datetime] = []
    for schluessel in erledigt:
        teile = schluessel.split("|")
        if len(teile) == 3 and teile[0] == "geschlossen" and teile[1] == str(n):
            zeit = _zeit(teile[2])
            if zeit is not None:
                zeiten.append(zeit)
    return zeiten


def verstoss_kennung(n: int, f: Verstoss) -> str:
    """Stabile Kennung eines Verstoßes: ``N|Regel|Hash(Kern)`` — einzige Stelle dieser Regel (#275).

    Kern ist :attr:`Verstoss.kern` oder sonst der Text (z. B. fehlender Commit-SHA,
    entfernte Tests); er ändert sich nur mit neuer Arbeit. Zeitstempel wie ``closed_at``
    oder der VPS-HEAD gehören nie hinein, sonst öffnet jedes Schließen/Deploy neu.
    """
    kern = f.kern or f.text
    return f"{n}|{f.regel}|{hashlib.sha1(kern.encode('utf-8')).hexdigest()[:12]}"


def schon_geoeffnet(n: int, f: Verstoss, erledigt: set[str]) -> bool:
    """Hat capo #n wegen genau dieses Verstoßes schon einmal wieder geöffnet? (#275)

    Merker ``geoeffnet|<Kennung>`` aus :func:`verstoss_kennung`; Altzustände macht
    :func:`altmerker_ueberfuehren` vorher daraus.
    """
    return f"geoeffnet|{verstoss_kennung(n, f)}" in erledigt


def altmerker_ueberfuehren(n: int, funde: list[Verstoss], erledigt: set[str]) -> bool:
    """Merker von vor der Kennung in Kennungs-Merker für die aktuellen Funde überführen (#275).

    Alt hieß „geöffnet“ ``N|Regel|closed_at`` und „gemeldet“ ``erneut|N|Regel``. Hat
    Ticket und Regel noch keinen Kennungs-Merker dieser Art, gilt der aktuelle Verstoß
    als der von damals — nach dem Update lieber einmal nur melden als eine Welle
    Wiederöffnungen. Danach zählt nur die Kennung: ein späterer neuer Verstoß öffnet
    einmal. ``True`` = neue Merker, der Aufrufer sichert den Zustand.
    """
    neu: set[str] = set()
    for f in funde:
        kennung = verstoss_kennung(n, f)
        alt_geoeffnet = any(s.split("|")[:2] == [str(n), f.regel] for s in erledigt)
        alt_gemeldet = f"erneut|{n}|{f.regel}" in erledigt
        for art, alt in (("geoeffnet", alt_geoeffnet), ("erneut", alt_gemeldet)):
            if alt and not any(s.startswith(f"{art}|{n}|{f.regel}|") for s in erledigt):
                neu.add(f"{art}|{kennung}")
    erledigt.update(neu)
    return bool(neu)


def merke_schliessen(n: int, closed_at: str, erledigt: set[str]) -> bool:
    """Gesehenes Schließen von #n festhalten, sobald capo #n schon einmal geöffnet hat (#448).

    Damit weiß :func:`fehl_reopen_kandidat`, dass ein späteres Offen nicht mehr aus
    capos Reopen stammt. Ohne eigenes Reopen bleibt der Zustand unberührt.
    ``True`` = neuer Schlüssel, der Aufrufer sichert den Zustand.
    """
    schluessel = f"geschlossen|{n}|{closed_at}"
    if not closed_at or schluessel in erledigt:
        return False
    eigenes_reopen = any(
        (teile := s.split("|"))[0] == str(n) and len(teile) == 3 and teile[1] in REOPEN_REGELN for s in erledigt
    )
    if not eigenes_reopen:
        return False
    erledigt.add(schluessel)
    return True


def ruecknahme_sperre(n: int, erledigt: set[str], laeuft: str) -> str:
    """Aktionszeile, falls eine an sich fällige Rücknahme jetzt nicht sein darf — sonst ``""``.

    Je Ticket höchstens eine automatische Rücknahme (Schlüssel ``ruecknahme|N``);
    solange an #n gebaut wird (``laeuft`` aus :func:`laeuft_noch`), wartet sie.
    """
    if f"ruecknahme|{n}" in erledigt:
        return f"#{n} Fehl-Reopen erneut — nicht zurückgenommen, Mensch prüfen"
    if laeuft:
        return f"#{n} Rücknahme wartet: Folge-Runde läuft ({laeuft})"
    return ""


def spaeter_geschlossen(gh_repo: str, n: int, stempel: str) -> bool | None:
    """Wurde #n nach dem Schließen ``stempel`` noch einmal geschlossen? (#448, Issue-Verlauf)

    Fängt, was :func:`merke_schliessen` nicht sehen kann: ein Mensch schließt und
    öffnet wieder, bevor ein Tick vorbeikommt. ``None`` = Verlauf nicht lesbar.
    """
    zeit = _zeit(stempel)
    code, ausgabe = gh.lauf(
        [
            "api",
            "--paginate",
            f"repos/{gh_repo}/issues/{n}/timeline?per_page=100",
            "--jq",
            '.[] | select(.event == "closed") | .created_at',
        ]
    )
    if code != 0 or zeit is None:
        log.warning("Issue-Verlauf von #%s nicht lesbar (gh Exit %s).", n, code)
        return None
    return any((spaeter := _zeit(z)) is not None and spaeter > zeit for z in ausgabe.split())


def fehl_reopen_kandidat(
    n: int,
    issue: dict[str, Any],
    eigene: list[Commit],
    erledigt: set[str],
    *,
    checkpoint: str,
) -> tuple[str, list[str]] | None:
    """(closed_at, Regeln) des jüngsten capo-Reopens von #n, falls eine Rücknahme in Frage kommt (#448).

    Grundlage sind die Schlüssel ``N|Regel|closed_at`` in ``erledigt`` (= capo hat bei
    diesem Schließen wieder geöffnet). ``None``, wenn es keins gibt, es schon
    zurückgenommen ist, ein Ticket-Commit danach liegt (neue Arbeit, kein Prüferfehler),
    das Ticket danach nochmals geschlossen war (:func:`merke_schliessen` — dann stammt
    das jetzige Offen nicht aus capos Reopen) oder ein Label (``waechter:ok``,
    Checkpoint) den Fall ohnehin zum Sonderfall macht.
    Ob die Regeln jetzt grün sind, prüft der Aufrufer.
    """
    labels = label_namen(issue)
    if OK_LABEL in labels or checkpoint in labels:
        return None
    je_stempel: dict[str, list[str]] = {}
    for schluessel in erledigt:
        teile = schluessel.split("|")
        if len(teile) == 3 and teile[0] == str(n) and teile[1] in REOPEN_REGELN:
            je_stempel.setdefault(teile[2], []).append(teile[1])
    mit_zeit = [(zeit, stempel) for stempel in je_stempel if (zeit := _zeit(stempel)) is not None]
    if not mit_zeit:
        return None
    zeit, stempel = max(mit_zeit)
    if f"ruecknahme|{n}|{stempel}" in erledigt:
        return None
    if any(spaeter > zeit for spaeter in _gesehene_schliessungen(n, erledigt)):
        return None  # danach wieder zu (Mensch oder Session) — das jetzige Offen ist nicht capos Reopen
    if any(c.zeit > zeit.timestamp() for c in eigene):
        return None
    return stempel, sorted(je_stempel[stempel])


def _wiedereroeffnete_regeln(n: int, erledigt: set[str]) -> list[str]:
    """Regeln des jüngsten noch offenen capo-Reopens von #n — leer ohne aktives Reopen (#862).

    Liest die beim Wiederöffnen geschriebenen Schlüssel ``N|Regel|closed_at`` (Regel in
    :data:`REOPEN_REGELN`). Wurde das jüngste Reopen zurückgenommen (``ruecknahme|N|<stempel>``)
    oder danach erneut geschlossen (:func:`_gesehene_schliessungen`), liefert die Funktion ``[]``.
    """
    je_stempel: dict[str, list[str]] = {}
    for schluessel in erledigt:
        teile = schluessel.split("|")
        if len(teile) == 3 and teile[0] == str(n) and teile[1] in REOPEN_REGELN:
            je_stempel.setdefault(teile[2], []).append(teile[1])
    if je_stempel:
        mit_zeit = [(zeit, stempel) for stempel in je_stempel if (zeit := _zeit(stempel)) is not None]
        if mit_zeit:
            zeit, stempel = max(mit_zeit)
            if f"ruecknahme|{n}|{stempel}" in erledigt:
                return []
            if any(spaeter > zeit for spaeter in _gesehene_schliessungen(n, erledigt)):
                return []
            return sorted(set(je_stempel[stempel]))
        if f"ruecknahme|{n}" in erledigt or _gesehene_schliessungen(n, erledigt):
            return []
        return sorted({r for regeln in je_stempel.values() for r in regeln})
    if f"ruecknahme|{n}" in erledigt or _gesehene_schliessungen(n, erledigt):
        return []
    return sorted(
        {
            teile[2]
            for s in erledigt
            if len(teile := s.split("|")) == 4
            and teile[0] == "geoeffnet"
            and teile[1] == str(n)
            and teile[2] in REOPEN_REGELN
        }
    )


def _folge_runde_offen(
    repo: Path,
    konfig: dict[str, Any],
    gh_repo: str,
    spec: int,
    n: int,
    issue: dict[str, Any],
    erledigt: set[str],
    folgerunden: dict[str, int],
    *,
    checkpoint: str,
    aktuelle_funde: list[Verstoss],
    wt_zeit: float | None,
    jetzt: datetime,
    dry_run: bool,
    gelernt: list[vorfall.Vorfall],
) -> tuple[list[str], bool]:
    """Stehen gebliebenes, wiedereröffnetes Ticket anstoßen oder Stillstand melden (#862).

    Löst die Folge-Runde vom einmaligen Reopen-Tick: ist #n offen und bereits als capo-Reopen
    in ``erledigt`` vermerkt, nicht fertig gebaut, ohne Checkpoint-/Freigabe-Label, ohne
    laufende Session und ohne offene Blocker, startet :func:`_folge_runde` über ``skripte/bau.py``
    (→ ``to_spawn/startstelle.py``) die Folge-Runde bzw. meldet den Stillstand über :func:`_melde`.
    Rückgabe: ``(aktionszeilen, behandelt)``.
    """
    reopen_regeln = _wiedereroeffnete_regeln(n, erledigt)
    if not reopen_regeln:
        return [], False
    labels = label_namen(issue)
    if checkpoint in labels or OK_LABEL in labels or ticket_zustand.fertig_gebaut(issue):
        return [], False
    from . import tmux_aufruf

    sitzung, fremd = tmux_aufruf.sitzung_aufloesen(repo, spec)
    if not fremd:
        fenster = _fenster_fuer_sitzung(spec, sitzung)
        if laeuft_noch(n, fenster, wt_zeit, jetzt):
            return [], False
    blocker = gh.blocked_by(gh_repo, str(n))
    if blocker is None:
        return [f"#{n} FEHLER: Blocker nicht lesbar — Folge-Runde zurückgehalten"], False
    if not all(isinstance(b, dict) and b.get("state") for b in blocker):
        return [f"#{n} FEHLER: Blocker-Format unbekannt — Folge-Runde zurückgehalten"], False
    if any(not ticket_zustand.fertig_gebaut(b) for b in blocker):
        return [], False
    funde = aktuelle_funde or [Verstoss(n, r, "wiedereröffnet ohne laufende Session") for r in reopen_regeln]
    zeilen = _folge_runde(
        repo,
        konfig,
        gh_repo,
        spec,
        n,
        funde,
        folgerunden,
        wt_zeit=wt_zeit,
        jetzt=jetzt,
        dry_run=dry_run,
        gelernt=gelernt,
    )
    return zeilen, True


def tick(
    repo: Path,
    spec: int,
    gh_repo: str,
    konfig: dict[str, Any],
    *,
    wt_basis: str = "",
    dry_run: bool = False,
    katalog: bool = False,
    jetzt: datetime | None = None,
) -> TickErgebnis:
    """Ein Aufseher-Tick: Stand + Delta + Verstöße/Aktionen als Textzeilen.

    Ein echter Tick hält die Sperre der Zustandsdatei; ein Probelauf (``dry_run``)
    schreibt nichts und braucht keine Sperre.
    """
    if dry_run:
        return _tick(repo, spec, gh_repo, konfig, wt_basis, True, katalog, jetzt)
    datei = _zustand_datei(repo, gh_repo, spec)
    with contextlib.ExitStack() as stapel:
        try:
            frei = stapel.enter_context(sperre(datei, _sperr_wartezeit()))
        except _sperre.SperreNichtOeffenbar as fehler:
            erg = TickErgebnis(exit_code=1)
            erg.zeilen.append(f"FEHLER: {fehler} — Tick übersprungen.")
            return erg
        if not frei:
            erg = TickErgebnis(exit_code=1)
            erg.zeilen.append(
                f"FEHLER: ein anderer capo-Lauf für Spec #{spec} läuft schon "
                f"(Sperre {datei.name}.lock) — Tick übersprungen."
            )
            return erg
        return _tick(repo, spec, gh_repo, konfig, wt_basis, False, katalog, jetzt)


def _tick(
    repo: Path,
    spec: int,
    gh_repo: str,
    konfig: dict[str, Any],
    wt_basis: str,
    dry_run: bool,
    katalog: bool,
    jetzt: datetime | None,
) -> TickErgebnis:
    jetzt = jetzt or datetime.now(timezone.utc)
    erg = TickErgebnis()
    kopfzeilen: list[str] = []
    regeln_aus = _git(repo, "fetch", "-q", "origin")[0] != 0
    if regeln_aus:
        log.warning("git fetch origin gescheitert — Regeln in diesem Tick ausgesetzt.")
        kopfzeilen.append("FEHLER: fetch — Regeln ausgesetzt (kein Wieder-Öffnen, kein Kommentar in diesem Tick).")
    ref = haupt_ref(repo)
    alle_kinder = kinder(gh_repo, spec)
    if alle_kinder is None:
        erg.zeilen += kopfzeilen
        erg.zeilen.append(f"FEHLER: Sub-Issues von #{spec} in {gh_repo} nicht lesbar (gh).")
        erg.exit_code = 1
        return erg
    auswahl_stand = auswahl.lesen(repo, spec)
    teilauswahl = auswahl_stand is not None and auswahl_stand.ist_teilauswahl()
    liste = auswahl.filtere_issues(alle_kinder, auswahl_stand) if auswahl_stand is not None else alle_kinder
    if ref is None:
        erg.zeilen += kopfzeilen
        erg.zeilen.append("FEHLER: kein origin/master bzw. origin/main — git fetch prüfen.")
        erg.exit_code = 1
        return erg

    datei = _zustand_datei(repo, gh_repo, spec)
    zustand = melder.lade_json(datei)
    zustand["repo"] = _repo_vermerk(repo)  # Sperre in abschluss_paket.py findet die Datei darüber (#871)
    erledigt = set(zustand.get("erledigt") or [])
    gelesen: dict[str, int] = dict(zustand.get("log_zeilen") or {})
    folgerunden: dict[str, int] = dict(zustand.get("folgerunden") or {})
    sofort = ist_sofort(konfig)
    erster_tick = _zeit(zustand.get("erster_tick"))
    if erster_tick is None:
        erster_tick = jetzt  # dieser Tick ist der erste: alles Geschlossene = Ausgangsstand
        zustand["erster_tick"] = jetzt.isoformat(timespec="seconds")
        zustand["ausgangsstand"] = {str(int(i["number"])): schliess_marke(i) for i in alle_kinder}
    ausgang = zustand.get("ausgangsstand")
    ausgang = ausgang if isinstance(ausgang, dict) else None
    waechter = konfig.get("waechter", {}) if isinstance(konfig.get("waechter"), dict) else {}
    stunden = float(waechter.get("verwaist_stunden") or 3)
    karenz = 0.0 if sofort else karenz_minuten(waechter)
    regularien = konfig.get("regularien", {}) if isinstance(konfig.get("regularien"), dict) else {}
    belege = belege_ordner(konfig)
    checkpoint = str(regularien.get("checkpoint_label") or "checkpoint:human")
    vps = konfig.get("vps") if isinstance(konfig.get("vps"), dict) else {}
    kopf = vps_kopf(vps) if vps.get("ssh") else None
    staging_stand = StagingStand(vps)  # je Takt höchstens ein Abruf (#1407)
    vps_fehlt = bool(vps.get("ssh")) and kopf is None
    if vps_fehlt:
        kopfzeilen.append(
            f"FEHLER: VPS-HEAD nicht lesbar ({vps.get('ssh')}) — Regel vps ausgesetzt, Spec-Abschluss zurückgehalten."
        )
    commits = alle_commits(repo, ref)
    _, ref_kurz = _git(repo, "rev-parse", "--short", ref)

    def sichern() -> None:
        """Zustand sofort nach jeder Aktion festhalten (Absturz = keine Doppel-Aktion)."""
        if dry_run:
            return
        zustand.update(
            {
                "erledigt": sorted(erledigt),
                "log_zeilen": gelesen,
                "folgerunden": folgerunden,
            }
        )
        melder.speichere_json(datei, zustand)

    stand: list[str] = []
    delta: list[str] = []
    aktionen: list[str] = []
    gelernt: list[vorfall.Vorfall] = []  # Vorfälle für den Fehlerkatalog (#286)
    offen = 0
    wartet = 0
    for issue in sorted(liste, key=lambda x: int(x["number"])):
        n = int(issue["number"])
        zu = ticket_zustand.endgueltig_zu(issue)
        offen += 0 if zu else 1
        eigene = ticket_commits(commits, n)
        log_zeilen = log_vom_ref(repo, ref, n)
        wt = worktree_ordner(n, wt_basis, repo)
        wt_zeit, wt_info = worktree_spur(wt, ref)
        lauf: list[tuple[str, list[dict[str, Any]]]] = []
        for quelle, pfad in laufdateien(repo, wt, n):
            zeilen = lauf_zeilen(pfad)
            if zeilen is not None:
                lauf.append((quelle, zeilen))
        alle_zeilen = log_zeilen + [z for _, zeilen in lauf for z in zeilen]
        gelernt += vorfall.aus_zeilen(alle_zeilen)  # was Sessions selbst meldeten
        wer = ",".join(a.get("login", "?") for a in issue.get("assignees") or []) or "-"
        commit = eigene[0].sha[:7] if eigene else "-"
        lauf_info = f" lauf:{sum(len(z) for _, z in lauf)}" if lauf else ""
        stand.append(
            f"#{n} {'zu ' if zu else 'OFF'} {wer:<10} commit:{commit} {wt_info:<10} log:{len(log_zeilen)}{lauf_info}"
        )

        # B: Delta der Bau-Log-Zeilen — je Quelle eigener Zähler
        im_lauf = {_roh(z) for _, zeilen in lauf for z in zeilen}
        neu_je_quelle: list[tuple[bool, list[dict[str, Any]]]] = []
        for quelle, zeilen in [("ref", log_zeilen), *lauf]:
            schluessel = str(n) if quelle == "ref" else f"{n}|{quelle}"
            schon = int(gelesen.get(schluessel, -1))
            neu = zeilen[max(schon, 0) :] if 0 <= schon <= len(zeilen) else zeilen
            if quelle == "ref":
                # Schon aus der Laufdatei bekannt (später versioniert) → nicht doppelt.
                neu = [z for z in neu if _roh(z) not in im_lauf]
            gelesen[schluessel] = len(zeilen)
            neu_je_quelle.append((schon < 0, neu))
        neu_alle = [z for _, neu in neu_je_quelle for z in neu]
        sichtbar = [z for z in neu_alle if z.get("typ") not in LEISE_TYPEN]
        for z in sichtbar[-MAX_DELTA_ZEILEN:]:
            delta.append(verdichte(n, z))
        if len(sichtbar) > MAX_DELTA_ZEILEN:
            delta.append(f"#{n} … (+{len(sichtbar) - MAX_DELTA_ZEILEN} ältere Zeilen)")
        if len(neu_alle) > len(sichtbar):
            delta.append(f"#{n} (+{len(neu_alle) - len(sichtbar)} Hook-Zeilen)")
        for erstmals, neu in neu_je_quelle:
            for z in neu:
                alt = _zeit(z.get("ts"))
                if erstmals and (alt is None or jetzt - alt > timedelta(hours=stunden)):
                    continue  # Altlast beim ersten Blick: zeigen ja, Alarm nein
                if ist_gate_rot(z):
                    erg.verstoesse.append(
                        Verstoss(
                            n,
                            "gate_rot",
                            _kurz(z.get("grund") or "Deploy-Gate rot"),
                            True,
                        )
                    )
                    aktionen += _melde(
                        repo,
                        konfig,
                        gh_repo,
                        dry_run,
                        "gate_rot",
                        f"Gate rot bei #{n}",
                        f"Ticket #{n}: {verdichte(n, z)}",
                        f"gate_rot|{n}|{z.get('ts')}|{z.get('lauf')}",
                    )
                elif z.get("typ") == "blockiert":
                    erg.verstoesse.append(Verstoss(n, "live_beweis_blockiert", _kurz(z.get("grund")), True))
                    aktionen += _melde(
                        repo,
                        konfig,
                        gh_repo,
                        dry_run,
                        "live_beweis_blockiert",
                        f"Live-Beweis blockiert bei #{n}",
                        f"Ticket #{n}: {_kurz(z.get('grund')) or 'ohne Grund'}",
                        f"blockiert|{n}|{z.get('ts')}",
                    )

        # A: Regeln
        if zu:
            grund = ticket_zustand.schliessgrund(issue)
            geschlossen_zeit = _zeit(issue.get("closed_at"))
            if not dry_run and merke_schliessen(n, str(issue.get("closed_at") or ""), erledigt):
                sichern()
            if ticket_zustand.stufe(issue) == "verworfen":
                aktionen.append(f"#{n} zu als {grund} — keine Regeln")
                continue
            nur_melden = OK_LABEL in label_namen(issue)
            if regeln_aus:
                continue
            ausgangsstand = not sofort and ist_ausgangsstand(n, issue, ausgang, geschlossen_zeit, erster_tick)
            if (
                not ausgangsstand
                and geschlossen_zeit is not None
                and karenz > 0
                and jetzt - geschlossen_zeit < timedelta(minutes=karenz)
            ):
                minuten = (jetzt - geschlossen_zeit).total_seconds() / 60
                aktionen.append(f"#{n} prüfe später (zu seit {minuten:.0f} min, Karenz {karenz:.0f} min)")
                wartet += 1
                continue
            funde_ok = reopen_funde(repo, ref, n, eigene, belege, vps, kopf, alle_zeilen, staging_stand, issue)
            if ausgangsstand:
                erg.alt += funde_ok
                for f in funde_ok:
                    aktionen.append(f"#{n} alt: {f.regel} — {f.text} (Ausgangsstand, nicht wieder geöffnet)")
                continue
            if nur_melden:
                erg.alt += funde_ok
                for f in funde_ok:
                    aktionen.append(
                        f"#{n} VERSTOSS {f.regel}: {f.text} "
                        f"(Label {OK_LABEL} — bewusst freigegeben, nicht wieder geöffnet)"
                    )
                continue
            erg.verstoesse += funde_ok
            geschlossen = str(issue.get("closed_at") or "?")
            if altmerker_ueberfuehren(n, funde_ok, erledigt):
                sichern()
            ungesehen = [f for f in funde_ok if f"{n}|{f.regel}|{geschlossen}" not in erledigt]
            # #275: je Verstoß höchstens ein Wiederöffnen — danach nur einmal melden.
            erneut = [f for f in ungesehen if schon_geoeffnet(n, f, erledigt)]
            neu_funde = [f for f in ungesehen if f not in erneut]
            for f in funde_ok:
                zusatz = (
                    "" if f in neu_funde else " (erneut, nicht wieder geöffnet)" if f in erneut else " (schon gemeldet)"
                )
                aktionen.append(f"#{n} VERSTOSS {f.regel}: {f.text}{zusatz}")
            zu_melden = [f for f in erneut if f"erneut|{verstoss_kennung(n, f)}" not in erledigt]
            if zu_melden:
                aktionen += befund.erneut_melden(n, gh_repo, [(f.regel, f.text) for f in zu_melden], dry_run)
                if aktionen[-1] == f"#{n} erneut gemeldet":
                    erledigt.update(f"erneut|{verstoss_kennung(n, f)}" for f in zu_melden)
                    sichern()
            if not neu_funde:
                continue
            aktionen += _wieder_oeffnen(n, gh_repo, neu_funde, dry_run)
            if aktionen[-1] == f"#{n} wieder geöffnet":
                erledigt.update(f"{n}|{f.regel}|{geschlossen}" for f in neu_funde)
                erledigt.update(f"geoeffnet|{verstoss_kennung(n, f)}" for f in neu_funde)
                sichern()
            elif not (dry_run and aktionen[-1].endswith("würde wieder öffnen")):
                continue
            # #284: wieder offen bringt nichts, wenn niemand weiterbaut.
            # ``issue`` ist der Stand vor dem Wieder-Öffnen; gefragt wird der Stand danach.
            jetzt_offen = {**issue, "state": "open"}
            if ticket_zustand.fertig_gebaut(jetzt_offen):
                # #275: grün auf Staging — ein zweiter Bau-Start wäre Doppelbau.
                aktionen.append(f"#{n} {ticket_zustand.wortlaut(jetzt_offen)} — keine Folge-Runde")
                continue
            aktionen += _folge_runde(
                repo,
                konfig,
                gh_repo,
                spec,
                n,
                neu_funde,
                folgerunden,
                wt_zeit=wt_zeit,
                jetzt=jetzt,
                dry_run=dry_run,
                gelernt=gelernt,
            )
            sichern()
        elif not regeln_aus:
            # #448: eigenes Fehl-Reopen zurücknehmen, wenn die Regeln jetzt grün sind.
            kandidat = None if vps_fehlt else fehl_reopen_kandidat(n, issue, eigene, erledigt, checkpoint=checkpoint)
            aktuelle_funde: list[Verstoss] = []
            folge_runde_gesperrt = vps_fehlt
            if kandidat is not None:
                aktuelle_funde = reopen_funde(
                    repo, ref, n, eigene, belege, vps, kopf, alle_zeilen, staging_stand, issue
                )
                if aktuelle_funde:
                    kandidat = None  # Regeln weiter rot — das Reopen war richtig
                else:
                    # Regeln jetzt grün (#448): keine Folge-Runde starten, auch wenn die Rücknahme
                    # ausgesetzt, blockiert oder gescheitert ist (z. B. Mensch öffnete danach neu).
                    folge_runde_gesperrt = True
            if kandidat is not None:
                spaeter = spaeter_geschlossen(gh_repo, n, kandidat[0])
                if spaeter is None:
                    aktionen.append(f"#{n} Rücknahme ausgesetzt: Issue-Verlauf nicht lesbar (gh)")
                if spaeter is not False:
                    kandidat = None  # danach wieder zu (Mensch) — das jetzige Offen ist nicht capos Reopen
            if kandidat is not None:
                from . import tmux_aufruf

                sitzung, fremd = tmux_aufruf.sitzung_aufloesen(repo, spec)
                fenster_stand = None if fremd else _fenster_fuer_sitzung(spec, sitzung)
                sperre = ruecknahme_sperre(n, erledigt, laeuft_noch(n, fenster_stand, wt_zeit, jetzt))
                if sperre:
                    aktionen.append(sperre)
                    kandidat = None
            if kandidat is not None:
                stempel, regeln = kandidat
                grund = (
                    f"{', '.join(regeln)} beim Schließen {stempel} trifft nicht mehr zu "
                    "(Regeln jetzt grün, kein neuer Ticket-Commit seitdem)."
                )
                rueck = befund.zuruecknehmen(n, gh_repo, grund, dry_run)
                aktionen += rueck.zeilen
                if rueck.ok:
                    erledigt.update({f"ruecknahme|{n}|{stempel}", f"ruecknahme|{n}"})
                    sichern()
                    bau_log.schreibe(
                        repo,
                        n,
                        "ruecknahme",
                        text=f"Fehl-Reopen zurückgenommen: {grund}",
                    )
                    continue
                if dry_run:
                    continue
            if checkpoint in label_namen(issue):
                # #285: nachts entscheidet der Aufseher nach Doktrin statt zu warten.
                vorher = len(erledigt)
                aktionen += _checkpoint(
                    repo,
                    konfig,
                    gh_repo,
                    spec,
                    n,
                    alle_zeilen,
                    erledigt,
                    checkpoint=checkpoint,
                    jetzt=jetzt,
                    dry_run=dry_run,
                    gelernt=gelernt,
                )
                if len(erledigt) != vorher:
                    sichern()
            elif not folge_runde_gesperrt:
                # #862: wiedereröffnetes Ticket ohne laufende Session nachstarten oder Stillstand melden.
                folge_zeilen, folge_behandelt = _folge_runde_offen(
                    repo,
                    konfig,
                    gh_repo,
                    spec,
                    n,
                    issue,
                    erledigt,
                    folgerunden,
                    checkpoint=checkpoint,
                    aktuelle_funde=aktuelle_funde,
                    wt_zeit=wt_zeit,
                    jetzt=jetzt,
                    dry_run=dry_run,
                    gelernt=gelernt,
                )
                if folge_zeilen:
                    aktionen += folge_zeilen
                if folge_behandelt:
                    sichern()
                    continue
            fund = regel_verwaist(
                n,
                issue,
                eigene,
                # Eigene Vorfall-Zeilen sind keine Spur der Session (#286).
                vorfall.ohne_waechter_zeilen(alle_zeilen),
                wt_zeit,
                stunden,
                jetzt,
                checkpoint=checkpoint,
            )
            if fund:
                erg.verstoesse.append(fund)
                schluessel = f"{n}|{fund.regel}|{jetzt.astimezone():%Y-%m-%d}"
                aktionen.append(f"#{n} VERSTOSS {fund.regel}: {fund.text}")
                if dry_run:
                    aktionen.append(f"#{n} [Probe] würde kommentieren + Mail session_tot")
                    continue
                if schluessel in erledigt:
                    aktionen[-1] += " (heute schon kommentiert)"
                elif befund.kommentiere(n, gh_repo, f"{WAECHTER_KOPF} {fund.regel} — {fund.text}"):
                    erledigt.add(schluessel)
                    sichern()
                    aktionen.append(f"#{n} kommentiert")
                else:
                    aktionen.append(f"#{n} FEHLER: kommentieren gescheitert")
                # Mail unabhängig vom Kommentar; die Doppel-Sperre hält nur der Melder.
                aktionen += _melde(
                    repo,
                    konfig,
                    gh_repo,
                    dry_run,
                    "session_tot",
                    f"Session tot? #{n}",
                    f"Ticket #{n} ({wer}): {fund.text}",
                    schluessel,
                )

    # Der Aufpasser meldet Vorfälle eines Aufseher-Fensters auf die Spec-Nummer —
    # die steht nicht in der Kinderliste und käme sonst nie in den Katalog (#286).
    spec_wt = worktree_ordner(spec, wt_basis, repo)
    spec_zeilen = log_vom_ref(repo, ref, spec)
    for _, pfad_lauf in laufdateien(repo, spec_wt, spec):
        spec_lauf = lauf_zeilen(pfad_lauf)
        if spec_lauf is not None:
            spec_zeilen += spec_lauf
    gelernt += vorfall.aus_zeilen(spec_zeilen)

    if not dry_run:
        for fund in erg.verstoesse:
            neu = vorfall_aus_verstoss(repo, fund, jetzt)
            if neu is not None:
                gelernt.append(neu)
                aktionen.append(f"#{fund.ticket} Vorfall notiert ({fund.regel})")
            else:
                aktionen.append(f"#{fund.ticket} Vorfall schon bekannt ({fund.regel})")
    # „Mensch nötig“ aus dem Review-Stop-Hook: je Eintrag einmal melden, Kette läuft weiter.
    mn_gesehen: list[str] = list(zustand.get("mensch_noetig_gesehen") or [])
    eigene = mensch_noetig.eintraege_mit_zustand(
        spec, zustand, None if dry_run else sichern, repo, lambda n: worktree_ordner(n, wt_basis, repo)
    )
    # #530: Ruf zurücknehmen, wenn der Beleg nach dem Ruf doch noch kam (ein Satz am Ticket).
    rufe = [r for r in zustand.get("mensch_noetig_rufe") or [] if isinstance(r, dict)]
    for ruf in list(rufe):
        ziel_ruf = str(ruf.get("ticket") or "")
        if auswahl_stand is not None and ziel_ruf.isdigit() and not auswahl_stand.enthaelt(int(ziel_ruf)):
            continue
        if not mensch_noetig.beleg_da(ruf):
            continue
        ziel, fp = str(ruf.get("ticket")), str(ruf.get("fingerprint") or "")[:8]
        aktionen.append(f"#{ziel} Review-Beleg nachgereicht (fp {fp}) — Ruf nach dem Menschen zurückgenommen.")
        if dry_run or regeln_aus:
            continue
        text = f"{WAECHTER_KOPF} Ruf zurückgenommen — der Review-Beleg liegt jetzt vor (fp {fp}), kein Mensch nötig."
        if befund.kommentiere(ziel, gh_repo, text):
            rufe.remove(ruf)
            zustand["mensch_noetig_rufe"] = rufe
            sichern()
    for eintrag in eigene:  # #465/#498: fremde Repos filtert und loggt mensch_noetig
        ziel = str(eintrag.get("ticket") or spec)
        if auswahl_stand is not None and ziel.isdigit() and int(ziel) != spec and not auswahl_stand.enthaelt(int(ziel)):
            continue
        fp = str(eintrag.get("fingerprint") or "")[:8]
        stand = mensch_noetig.review_stand(eintrag, jetzt)
        if stand == "laeuft":  # nicht gemerkt: nächster Takt prüft erneut
            aktionen.append(
                f"#{ziel} Review läuft noch (fp {fp}) — Mensch nötig erst nach "
                f"{mensch_noetig.REVIEW_WARTEZEIT_MIN} min ohne Beleg."
            )
            continue
        if stand == "belegt":
            if not dry_run:
                mn_gesehen.append(mensch_noetig.schluessel(eintrag))
                zustand["mensch_noetig_gesehen"] = mn_gesehen
                sichern()
            continue
        was = mensch_noetig_was(eintrag)
        aktionen.append(f"MENSCH NÖTIG: #{ziel} {was} (fp {fp}) — Kette läuft weiter, der Mensch prüft.")
        if dry_run or regeln_aus:
            continue
        text = (
            f"{WAECHTER_KOPF} Mensch nötig — {was} (fp {fp}).\n"
            f"{WAECHTER_KOPF} Die Kette läuft weiter; bitte den Review-Befund von Hand prüfen."
        )
        if not befund.kommentiere(ziel, gh_repo, text):
            aktionen.append(f"#{ziel} FEHLER: kommentieren gescheitert (Mensch nötig)")
            continue
        bau_log.schreibe(
            repo,
            ziel,
            "mensch_noetig",
            fingerprint=fp,
            fix_runde=eintrag.get("fix_runde"),
        )
        mn_gesehen.append(mensch_noetig.schluessel(eintrag))
        zustand["mensch_noetig_gesehen"] = mn_gesehen
        if eintrag.get("grund") == "review_offen":
            # ponytail: Rufe ohne späteren Beleg bleiben stehen, gekappt auf 50; Upgrade = mit Ticket-Zu räumen.
            rufe.append({"ticket": ziel, "repo": eintrag.get("repo"), "fingerprint": eintrag.get("fingerprint")})
            zustand["mensch_noetig_rufe"] = rufe[-50:]
        sichern()
    if katalog and not dry_run:
        aktionen += katalog_pflegen(repo, konfig, gelernt)

    # #853: Abnahme-Tickets (checkpoint:human) warten auf den Menschen und zählen für
    # Moment.ALLE_AUF_STAGING nicht als Bau-Tickets mit.
    bau_tickets = [i for i in liste if checkpoint not in label_namen(i)]
    abnahme_tickets = [i for i in liste if checkpoint in label_namen(i)]
    komplett_haelt = False
    if not teilauswahl and bau_tickets and all(ticket_zustand.fertig_gebaut(i) for i in bau_tickets):
        komplett_haelt = _komplett_alle_auf_staging(repo, spec, konfig, aktionen, dry_run)
        if (
            abnahme_tickets
            and not erg.verstoesse
            and not regeln_aus
            and not vps_fehlt
            and not staging_stand.unlesbar
            and not komplett_haelt
            and _live_nach_abnahme_passt(
                repo,
                spec,
                gh_repo,
                konfig,
                abnahme_tickets,
                erledigt,
                aktionen,
                dry_run=dry_run,
                spec_tickets=[int(i["number"]) for i in liste],
            )
        ):
            sichern()

    # #1407: unlesbares Staging ist kein „fehlt“, aber auch kein Beleg „liegt drauf“ — Abschluss zurückhalten.
    unlesbar_takte = int(zustand.get("staging_unlesbar_takte") or 0) + 1 if staging_stand.unlesbar else 0
    zustand["staging_unlesbar_takte"] = unlesbar_takte
    if unlesbar_takte >= STAGING_UNLESBAR_LAUT:
        kopfzeilen.append(
            f"FEHLER: Staging seit {unlesbar_takte} Takten nicht lesbar — Regel vps für Tickets ohne Zettel aus, "
            "Spec-Abschluss zurückgehalten."
        )
    elif staging_stand.unlesbar:
        kopfzeilen.append("FEHLER: Staging nicht lesbar — Regel vps teils ausgesetzt, Spec-Abschluss zurückgehalten.")
    erg.zeilen += kopfzeilen
    auswahl_zusatz = f" · Auswahl {len(liste)}/{len(alle_kinder)}" if teilauswahl else ""
    erg.zeilen.append(
        f"Spec #{spec} · {ref} {ref_kurz} · VPS {kopf[:7] if kopf else '-'} · offen {offen}/{len(liste)}{auswahl_zusatz} · "
        f"{jetzt.astimezone():%d.%m. %H:%M}"
    )
    erg.zeilen += stand
    if delta:
        erg.zeilen.append("Bau-Log neu:")
        erg.zeilen += delta
    erg.zeilen += aktionen

    erg.fertig = (
        not teilauswahl
        and bool(liste)
        and offen == 0
        and wartet == 0
        and not erg.verstoesse
        and not regeln_aus
        and not vps_fehlt
        and not staging_stand.unlesbar
        and not komplett_haelt
    )
    if erg.fertig:
        alt_hinweis = f" ({len(erg.alt)} alte aus dem Ausgangsstand)" if erg.alt else ""
        if dry_run:
            erg.zeilen.append(f"SPEC FERTIG — alle {len(liste)} Tickets zu, keine Verstöße{alt_hinweis}. [Probe]")
        else:
            pfad = uebersicht(repo, spec, [int(i["number"]) for i in liste])
            bericht_pfad, _bericht = morgen_bericht(repo, ref, spec, liste, checkpoint=checkpoint)
            erg.zeilen.append(
                f"SPEC FERTIG — alle {len(liste)} Tickets zu, keine Verstöße{alt_hinweis}. Übersicht: {pfad}"
            )
            erg.zeilen.append(f"Morgen-Bericht: {bericht_pfad}")
        if not dry_run and not zustand.get("spec_fertig_seit"):
            zustand["spec_fertig_seit"] = jetzt.astimezone().isoformat(timespec="seconds")
            sichern()  # abschluss_paket.py liest das Urteil aus der Datei (#889) — noch in diesem Tick
        if not zustand.get("staging_schalter_erledigt"):
            # Staging-Hauptschalter der Spec AN (#563, Spec #548 E3) — Live nie automatisch.
            try:
                schalter = staging_schalter.schalte_an(repo, gh_repo, spec, dry_run)
            except Exception as fehler:  # ein Ausreißer darf den Tick (sichern) nicht abbrechen
                log.exception("Staging-Schalter Spec #%s: unerwarteter Fehler", spec)
                _werkzeug_fehler(
                    "capo.staging_schalter", "Staging-Schalter: unerwarteter Fehler", fehler, spec=spec, dry_run=dry_run
                )
                schalter = staging_schalter.Ergebnis(
                    [f"FEHLER: Staging-Schalter — unerwarteter Fehler ({type(fehler).__name__}: {fehler})."], False
                )
            erg.zeilen += schalter.zeilen
            if schalter.erledigt and not dry_run:
                zustand["staging_schalter_erledigt"] = jetzt.isoformat(timespec="seconds")
    # Eine Mail je Spec (#588): nachsehen verschickt das abgelegte Paket, sobald Thermo + Rückblick da
    # sind (oder 120 min um). Hängt an „SPEC FERTIG einmal gesehen“ — ein später wieder geöffnetes
    # Ticket hält die Mail nicht auf.
    if (zustand.get("spec_fertig_seit") or (erg.fertig and dry_run)) and not zustand.get("abschluss_mail_erledigt"):
        try:
            erg.zeilen += abschluss_anstossen(repo, spec, gh_repo, konfig, zustand, dry_run)
        except Exception as fehler:  # ein Ausreißer darf den Tick (sichern) nicht abbrechen
            log.exception("Abschluss-Mail Spec #%s: unerwarteter Fehler", spec)
            _werkzeug_fehler(
                "capo.abschluss_mail", "Abschluss-Mail: unerwarteter Fehler", fehler, spec=spec, dry_run=dry_run
            )
            erg.zeilen.append(f"FEHLER: Abschluss-Mail — unerwarteter Fehler ({type(fehler).__name__}: {fehler}).")
    # Aufräumen erst nach der Abschluss-Mail (#1370): dann hat der Rückblick die Laufdateien
    # der Worktrees schon gesammelt.
    if (
        (zustand.get("spec_fertig_seit") or (erg.fertig and dry_run))
        and (zustand.get("abschluss_mail_erledigt") or dry_run)
        and not zustand.get("aufraeumen_erledigt")
    ):
        erg.zeilen += _hausmeister_schluss(spec, zustand, jetzt, dry_run)
    # Letzter Schritt der Kette (#543): Spec-Issue schließen, dort hängt das Aufseher-Fenster.
    # Nur bei positivem Beleg: ``staging.ohne: true`` (Vorgabe False, kaputte Datei → False) oder
    # Staging-Repo mit Merker „Live ist gelaufen“ (#546, :func:`live_gelaufen`). Ein Repo ganz ohne
    # Staging-Eintrag wird bewusst nicht von selbst geschlossen.
    war_zu = bool(zustand.get("spec_geschlossen"))
    if (
        zustand.get("aufraeumen_erledigt")
        and not war_zu
        and (config.staging_bewusst_ohne(konfig) or (config.hat_staging(konfig) and live_gelaufen(repo, spec, gh_repo)))
    ):
        erg.zeilen += _spec_schliessen_schritt(repo, spec, gh_repo, zustand, jetzt, dry_run)

    if MAIL_AUS in erg.zeilen:  # je Tick nur einmal, nicht je Meldung
        erste = erg.zeilen.index(MAIL_AUS)
        erg.zeilen = [z for i, z in enumerate(erg.zeilen) if z != MAIL_AUS or i == erste]
    sichern()
    if any("FEHLER" in z for z in erg.zeilen):
        erg.exit_code = 1
    # Allerletzter Schritt (#546): erst nach ``sichern()``, denn der Takt kann im Fenster laufen,
    # das hier endet. Nur im Takt, der ``spec_geschlossen`` gerade gesetzt hat → nie wiederholt.
    if not war_zu and zustand.get("spec_geschlossen") and not dry_run:
        _spec_fenster_wegraeumen(repo, spec)
    return erg


def _live_merker_datei(repo: Path, spec: int, gh_repo: str = "") -> Path:
    """Eigene Merker-Datei neben dem Zustand der Spec; ohne Takt-Sperre beschreibbar (#546)."""
    datei = _zustand_datei(repo, gh_repo or gh.repo_aus_origin(repo), spec)
    return datei.with_name(f"{datei.stem}.live")


def live_gelaufen(repo: Path, spec: int, gh_repo: str = "") -> bool:
    """Hat der Live-Schritt dieser Spec erfolgreich gelaufen? Einzige Lese-Stelle des Merkers (#546).

    Nur ein gültiger Zeitstempel in der Merker-Datei zählt; fehlt sie, ist sie leer oder
    unlesbar, gilt „nicht gelaufen“.
    """
    try:
        text = _live_merker_datei(repo, spec, gh_repo).read_text(encoding="utf-8").strip()
        datetime.fromisoformat(text)
    except (OSError, ValueError):
        return False
    return True


def live_merken(repo: Path, spec: int, gh_repo: str = "") -> bool:
    """Merker „Live ist gelaufen“ atomar schreiben (Live-Schritt, nur nach Erfolg); ``True`` = gesetzt.

    Bewusst ohne Takt-Sperre: ein langer Takt darf den Merker nicht verhindern.
    """
    datei = _live_merker_datei(repo, spec, gh_repo)
    zwischen = datei.with_name(datei.name + ".neu")
    try:
        datei.parent.mkdir(parents=True, exist_ok=True)
        zwischen.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        os.replace(zwischen, datei)
    except OSError as fehler:
        log.error("Live-Merker Spec #%s nicht gesetzt: %s", spec, fehler)
        return False
    return True


def _werkzeug_fehler(quelle: str, titel: str, fehler: BaseException, *, spec: int, dry_run: bool = False) -> None:
    """Unerwartete Werkzeug-Ausnahme ins eigene Log + Skill-Repo melden (#578, nie Mail)."""
    if dry_run:
        return
    fehlermeldung.melde(quelle, titel, ausnahme=fehler, kontext={"spec": spec})


def _spec_fenster_wegraeumen(repo: Path, spec: int) -> None:
    """Fenster der geschlossenen Spec wegräumen; Fehler nur loggen (Takt bleibt grün)."""
    from to_spawn import tmux_aufruf  # spät: tmux_aufruf importiert capo

    try:
        tmux_aufruf.spec_session_schliessen(repo, spec)
    except Exception as fehler:  # der Takt endet nie rot am Aufräumen
        log.exception("Fenster der Spec #%s nicht geschlossen", spec)
        _werkzeug_fehler("capo.spec_fenster", "Spec-Fenster schließen: unerwarteter Fehler", fehler, spec=spec)


#: Ganzes Wort „passt“ (Groß/klein egal) — Abnahme-Freigabe des Menschen (#924).
_ABNAHME_PASST_WORT = re.compile(r"\bpasst\b", re.IGNORECASE)
#: Verneinung, Einschränkung oder Nachbesserung: dann ist ein Kommentar mit „passt“ keine Freigabe (#924).
_ABNAHME_EINWAND = re.compile(
    r"\b(?:nicht|kein\w*|aber|jedoch|allerdings|sonst|nachbesser\w*|kritik|"
    r"änder\w*|aender\w*|korrigier\w*|fehl\w*|mangel|mängel|falsch|anders|statt|noch)\b",
    re.IGNORECASE,
)


def ist_abnahme_passt(text: str) -> bool:
    """True, wenn ein Mensch-Kommentar am Abnahme-Ticket eine klare „passt“-Freigabe ist (#924).

    Zitierte Zeilen (``> …``) werden ausgeblendet. Verneinungen oder Nachbesserungs-Wünsche
    (z. B. „passt nicht“, „Sonst passt alles. Bitte nachbessern…“) gelten nie als Freigabe.
    """
    ohne_zitat = "\n".join(zeile for zeile in text.splitlines() if not zeile.lstrip().startswith(">")).strip()
    if not ohne_zitat:
        return False
    return bool(_ABNAHME_PASST_WORT.search(ohne_zitat)) and not _ABNAHME_EINWAND.search(ohne_zitat)


def abnahme_passt_kommentar(kommentare: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Jüngster Mensch-Kommentar am Abnahme-Ticket, wenn er eine „passt“-Freigabe ist (#924, #935).

    ``kommentare`` stammt aus :func:`issue_kommentare` (nur Repo-Beteiligte). Kommentare mit
    Aufseher-, Aufpasser- oder Session-Marke zählen nie als Freigabe des Menschen — auch eine
    automatische Aufseher-Annahme nach Frist löst kein Live aus — und verdecken ein vorheriges
    „passt“ des Menschen nicht (#935: z. B. Aufseher-Quittung nach der Abnahme). Folgt auf ein
    „passt“ später noch ein anderer Mensch-Kommentar (z. B. Stopp/Kritik), gilt nur der jüngste
    Mensch-Kommentar.
    """
    sortiert = sorted(
        kommentare,
        key=lambda e: (_kommentar_teile(e)[2] or datetime.min.replace(tzinfo=timezone.utc), int(e.get("id") or 0)),
    )
    mensch_kommentare = [
        eintrag
        for eintrag in sortiert
        if not (
            _kommentar_teile(eintrag)[1].lstrip().startswith(EIGENE_KOEPFE)
            or _ist_session_kommentar(_kommentar_teile(eintrag)[1])
        )
    ]
    if not mensch_kommentare:
        return None
    juengster = mensch_kommentare[-1]
    return juengster if ist_abnahme_passt(_kommentar_teile(juengster)[1]) else None


def _live_nach_abnahme_passt(
    repo: Path,
    spec: int,
    gh_repo: str,
    konfig: dict[str, Any],
    abnahme_tickets: list[dict[str, Any]],
    erledigt: set[str],
    zeilen: list[str],
    *,
    dry_run: bool = False,
    spec_tickets: Iterable[str | int] | None = None,
) -> bool:
    """Abnahme-Kommentar „passt“ gilt als Live-Freigabe und startet Komplett-Prüfung + Live (#924).

    Die eine Stelle für alle Wege (capo-Takt und Wege ohne capo-Takt wie ``leitstand.py`` über
    :func:`abnahme_passt_live_pruefen`). Läuft nur, wenn alle Bau-Tickets fertig sind und das Repo
    nicht bewusst ohne Live arbeitet (``live.ohne: true``). Offene Briefkasten-Live-Fragen der Spec
    werden nach „passt“ als beantwortet (``ja``) eingetragen; danach startet :func:`live_tuer.starte`
    (Komplett-Prüfung genau einmal, dann Live), sofern noch kein Live-Merker liegt. Rückgabe ``True``,
    wenn ``erledigt`` ergänzt wurde (Aufrufer sichert).
    """
    if config.live_bewusst_ohne(konfig):
        return False
    alle_tickets: list[int] = list(
        dict.fromkeys(
            [
                spec,
                *(int(issue["number"]) for issue in abnahme_tickets),
                *(int(t) for t in (spec_tickets or ()) if str(t).strip().isdigit()),
            ]
        )
    )
    bereits_live = live_gelaufen(repo, spec, gh_repo)
    live_konfiguriert = bool(config.live_schritt(konfig)["befehl"] or config.pruef_befehl(konfig, "komplett"))
    hat_live_fragen = briefkasten.hat_offene_live_fragen(alle_tickets, hauptbaum=repo)
    if (bereits_live or not live_konfiguriert) and not hat_live_fragen:
        return False
    treffer: tuple[int, dict[str, Any]] | None = None
    for issue in abnahme_tickets:
        n = int(issue["number"])
        kommentare = issue_kommentare(gh_repo, n)
        if kommentare is None:
            continue
        kommentar = abnahme_passt_kommentar(kommentare)
        if kommentar is not None:
            treffer = (n, kommentar)
            break
    if treffer is None:
        return False
    n, kommentar = treffer
    if dry_run:
        if hat_live_fragen:
            zeilen.append(f"#{n} [Probe] Abnahme „passt“ — würde offene Briefkasten-Live-Fragen mit „ja“ beantworten.")
        if not bereits_live and live_konfiguriert:
            zeilen.append(f"#{n} [Probe] Abnahme „passt“ — würde Live-Schritt starten (to_spawn.py live {spec}).")
        return False
    if hat_live_fragen:
        try:
            beantwortet = briefkasten.live_fragen_freigeben(
                alle_tickets, n, kommentar.get("id", ""), hauptbaum=repo
            )
        except (briefkasten.Verweigert, OSError, TimeoutError) as fehler:
            log.warning("Briefkasten-Live-Fragen Spec #%s nach Abnahme #%s nicht freigegeben: %s", spec, n, fehler)
        else:
            for t_nr, fid in beantwortet:
                zeilen.append(
                    f"#{t_nr} Briefkasten-Live-Frage {fid} durch Abnahme „passt“ an #{n} mit „ja“ beantwortet."
                )
    if bereits_live or not live_konfiguriert:
        return False
    from to_spawn import live_tuer  # spät: live_tuer importiert capo

    sperre_pfad = _live_merker_datei(repo, spec, gh_repo).with_suffix(".live.lock")
    with _sperre.halte(sperre_pfad, warten=0) as gehalten:
        if not gehalten or live_gelaufen(repo, spec, gh_repo):
            return False
        try:
            code, live_zeilen = live_tuer.starte(repo, spec, konfig, gh_repo=gh_repo)
        except Exception as fehler:  # der Tick (sichern) bricht nie ab
            log.exception("Live-Schritt nach Abnahme #%s (Spec #%s): unerwarteter Fehler", n, spec)
            _werkzeug_fehler("capo.abnahme_live", "Live-Schritt nach Abnahme: unerwarteter Fehler", fehler, spec=spec)
            zeilen.append(
                f"FEHLER: #{n} Live-Schritt nach Abnahme — unerwarteter Fehler ({type(fehler).__name__}: {fehler})."
            )
            return False
    if code == 0:
        zeilen.append(f"#{n} Abnahme „passt“ — Live-Schritt gestartet ({'; '.join(live_zeilen)}).")
        return False
    if code == 2:
        schluessel = f"abnahme_live_hand|{spec}|{n}|{kommentar.get('id', '')}"
        if schluessel not in erledigt:
            erledigt.add(schluessel)
            zeilen.append(f"#{n} Abnahme „passt“ — {'; '.join(live_zeilen)}")
            return True
        return False
    zeilen.append(f"FEHLER: #{n} Abnahme „passt“ — {'; '.join(live_zeilen)}")
    return False


def abnahme_passt_live_pruefen(
    repo: Path,
    spec: int,
    issues: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
    *,
    gh_repo: str = "",
    konfig: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Gemeinsame Regel für Wege ohne capo-Takt (z. B. ``skripte/leitstand.py``) (#924).

    Prüft dieselbe Vorbedingung wie :func:`tick` (alle Bau-Tickets fertig gebaut, mindestens ein
    Abnahme-Ticket) und delegiert direkt an :func:`_live_nach_abnahme_passt` — keine zweite Kopie.
    """
    konfig_aktiv = konfig if konfig is not None else config.lade(repo)
    slug = gh_repo or gh.repo_aus_origin(repo) or os.environ.get("TO_SPAWN_GH_REPO", "").strip()
    if not slug:
        return []
    liste: list[dict[str, Any]] = []
    if isinstance(issues, Mapping):
        for nr, daten in issues.items():
            if not str(nr).strip().isdigit() or int(nr) == spec or not isinstance(daten, Mapping):
                continue
            liste.append({"number": int(nr), **daten})
    else:
        for daten in issues:
            nr_roh = daten.get("number")
            if not str(nr_roh or "").strip().isdigit() or int(nr_roh) == spec:
                continue
            liste.append({**daten, "number": int(nr_roh)})
    checkpoint = str(konfig_aktiv.get("regularien", {}).get("checkpoint_label") or ticket_zustand.CHECKPOINT_LABEL)
    bau_tickets = [i for i in liste if checkpoint not in ticket_zustand.label_namen(i)]
    abnahme_tickets = [i for i in liste if checkpoint in ticket_zustand.label_namen(i)]
    if not bau_tickets or not abnahme_tickets or not all(ticket_zustand.fertig_gebaut(i) for i in bau_tickets):
        return []
    zeilen: list[str] = []
    _live_nach_abnahme_passt(
        repo,
        spec,
        slug,
        konfig_aktiv,
        abnahme_tickets,
        set(),
        zeilen,
        dry_run=dry_run,
        spec_tickets=[int(i["number"]) for i in liste],
    )
    return zeilen


def _komplett_alle_auf_staging(
    repo: Path, spec: int, konfig: dict[str, Any], zeilen: list[str], dry_run: bool = False
) -> bool:
    """Komplett-Prüfung „alle Tickets auf Staging“ (#425 C2); ``True`` = Spec in diesem Takt nicht fertig.

    Einmal-Regel (rot je Kopf), Hintergrund-Lauf und Sperre versteckt
    :mod:`to_spawn.komplett_lauf` (#455: der Takt wartet nicht auf lange Prüfungen). Eine Zeile
    nur, wenn etwas passiert ist (Lauf, Fehler, läuft schon) — sonst bleibt der Takt wie vor #425.
    Probelauf (#456): nur den Merker lesen (:func:`komplett_lauf.nur_lesen`), nichts starten;
    rot oder fehlend hält „SPEC FERTIG“ auch dort zurück (Zeile mit ``[Probe]``, kein FEHLER).
    """
    try:
        if dry_run:
            erg = komplett_lauf.nur_lesen(repo, spec, Moment.ALLE_AUF_STAGING, konfig)
        else:
            erg = komplett_lauf.komplett_einmal(repo, spec, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    except Exception as fehler:  # ein Ausreißer darf den Tick (sichern) nicht abbrechen
        log.exception("Komplett-Prüfung Spec #%s: unerwarteter Fehler", spec)
        _werkzeug_fehler(
            "capo.komplett_pruefung", "Komplett-Prüfung: unerwarteter Fehler", fehler, spec=spec, dry_run=dry_run
        )
        zeilen.append(f"FEHLER: Komplett-Prüfung — unerwarteter Fehler ({type(fehler).__name__}: {fehler}).")
        return True
    if dry_run:
        if not erg.frei:
            zeilen.append(f"[Probe] {erg.grund}")
    elif not erg.ok:
        zeilen.append(f"FEHLER: {erg.grund} Spec bleibt bis grün offen.")
    elif erg.gelaufen or erg.laeuft:
        zeilen.append(erg.grund)
    return not erg.frei


def _spec_schliessen_schritt(
    repo: Path, spec: int, gh_repo: str, zustand: dict[str, Any], jetzt: datetime, dry_run: bool
) -> list[str]:
    """Spec nach SPEC FERTIG über den vorhandenen Weg schließen (#543, ``befund.spec_schliessen``).

    Die Regel „darf zu?“ (alle Folge-/Spec-Tickets erledigt) steht allein dort; Der Tick ruft nur bei
    ``staging.ohne: true`` auf, weil ``spec_offen`` Live nicht kennt. Abbruch dort ist kein Fehler: Grund ins Log, der nächste Takt versucht es erneut.
    """
    if dry_run:
        return [f"[Probe] Spec #{spec} schließen: spec-schliessen {spec}"]
    try:
        ergebnis = befund.spec_schliessen(repo, gh_repo, spec)
    except Exception as fehler:  # der Tick (sichern) bricht nie ab
        log.exception("Spec #%s schließen: unerwarteter Fehler", spec)
        _werkzeug_fehler("capo.spec_schliessen", "Spec schließen: unerwarteter Fehler", fehler, spec=spec)
        return []
    if not ergebnis.ok:
        log.warning("Spec #%s bleibt offen: %s", spec, " | ".join(ergebnis.zeilen))
        return []
    zustand["spec_geschlossen"] = jetzt.isoformat(timespec="seconds")
    return ergebnis.zeilen


def _hausmeister_schluss(spec: int, zustand: dict[str, Any], jetzt: datetime, dry_run: bool) -> list[str]:
    """Fester Schlussschritt nach der Abschluss-Mail (#1370): einmal alles Fertige aufräumen.

    Ruft nur die Tür des Hausmeisters — welche Ordner wann weg dürfen, weiß allein er.
    ``aufraeumen_erledigt`` nur, wenn der Rundgang wirklich lief und kein Ordner mit Fehler
    blieb (nicht im Probelauf). Belegte Sperre → Wartezeile, nächster Tick erneut. Sonst
    FEHLER-Zeile und ``aufraeumen_versuche`` +1; ab :data:`HAUSMEISTER_VERSUCHE` in Folge
    trotzdem erledigt (kein Dauer-Rot), das Netz bleibt der Aufpasser-Takt.
    """
    from to_spawn import hausmeister  # spät: hausmeister importiert aufpasser

    try:
        rund = hausmeister.rundgang(loeschen=not dry_run)
    except Exception as fehler:  # Vertrag sagt „wirft nie“; der Tick (sichern) bricht nie ab
        log.exception("Hausmeister Spec #%s: unerwarteter Fehler", spec)
        _werkzeug_fehler("capo.hausmeister", "Hausmeister: unerwarteter Fehler", fehler, spec=spec, dry_run=dry_run)
        rund = hausmeister.Rundgang(False, f"unerwarteter Fehler ({type(fehler).__name__}: {fehler})")
    if dry_run:
        if not rund.gelaufen:
            return [f"Hausmeister (Probe): nicht gelaufen — {rund.grund}."]
        return [f"Hausmeister (Probe): {rund.geloescht} würde löschen, {rund.bleibt} bleibt, {rund.fehler} Fehler."]
    if rund.wartet:
        return [f"Hausmeister wartet: {rund.grund}."]
    if rund.gelaufen and not rund.fehler:
        zustand.pop("aufraeumen_versuche", None)
        zustand["aufraeumen_erledigt"] = jetzt.isoformat(timespec="seconds")
        return [f"Hausmeister: {rund.geloescht} gelöscht, {rund.bleibt} bleibt, 0 Fehler."]
    if rund.gelaufen:
        namen = ", ".join(e.ordner.name for e in rund.ergebnisse if e.fehler)
        zeile = (
            f"FEHLER: Hausmeister — {rund.fehler} Ordner: {namen} ({rund.geloescht} gelöscht, {rund.bleibt} bleibt)."
        )
        zeilen = [zeile]
    else:
        zeilen = [f"FEHLER: Hausmeister — {rund.grund}."]
    versuche = int(zustand.get("aufraeumen_versuche") or 0) + 1
    zustand["aufraeumen_versuche"] = versuche
    if versuche >= HAUSMEISTER_VERSUCHE:
        zustand["aufraeumen_erledigt"] = jetzt.isoformat(timespec="seconds")
        zeilen.append(f"FEHLER: Hausmeister — nach {versuche} Versuchen aufgegeben, Netz ist der Aufpasser-Takt")
    return zeilen


def _gh_ok(args: list[str]) -> bool:
    return gh.lauf(args)[0] == 0


def _wieder_oeffnen(n: int, gh_repo: str, funde: list[Verstoss], dry_run: bool) -> list[str]:
    """Ticket einmal wieder öffnen (Regel lebt in :mod:`befund`); Verstöße sind immer rot."""
    return befund.wieder_oeffnen(n, gh_repo, [(f.regel, f.text) for f in funde], dry_run)


#: ``nachsehen`` ruft den Wirkungskreis (bis 600 s) — Luft darüber für Mail und Start.
ABSCHLUSS_ZEITLIMIT_S = 720
#: ``ablegen`` schreibt nur eine Datei.
ABLEGEN_ZEITLIMIT_S = 60
#: Ab so vielen Fehlversuchen in Folge einmal ein Kommentar auf dem Spec-Issue.
ABSCHLUSS_ALARM_AB = 3
#: Nach so vielen gescheiterten Hausmeister-Schlussschritten in Folge gilt er als erledigt (#1370).
HAUSMEISTER_VERSUCHE = 3
#: Ab so vielen unlesbaren Staging-Takten in Folge eine eigene FEHLER-Kopfzeile (#1407), keine Mail.
STAGING_UNLESBAR_LAUT = 3


def _abschluss_lauf(repo: Path, befehl: str, spec: int, *args: str) -> tuple[int, str, str]:
    """``skripte/abschluss_paket.py <befehl> <S> --repo <repo> …``; Rückgabe (Exit, stdout, stderr)."""
    skript = Path(__file__).resolve().parent.parent / "skripte" / "abschluss_paket.py"
    lauf = subprocess.run(
        [sys.executable, "-X", "utf8", str(skript), befehl, str(spec), "--repo", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=ABLEGEN_ZEITLIMIT_S if befehl == "ablegen" else ABSCHLUSS_ZEITLIMIT_S,
        check=False,
        **prozessbaum.fensterlos(),
    )
    return lauf.returncode, lauf.stdout.strip(), lauf.stderr.strip()


def _abschluss_ausgabe(befehl: str, spec: int, code: int, out: str, err: str) -> str:
    """stderr + stdout (stderr zuerst) gekürzt für die FEHLER-Zeile; voll ins Log."""
    voll = " | ".join(t for t in (err, out) if t) or "keine Ausgabe"
    log.warning("Abschluss-Mail Spec #%s: %s Exit %s — %s", spec, befehl, code, voll)
    return voll if len(voll) <= 500 else voll[:500] + " …"


def _stage_ohne_zugang(url: str) -> str:
    """staging.url ohne ``nutzer:pass@``; kein http(s)-Link → „fehlt“."""
    teile = urllib.parse.urlsplit(url.strip())
    if teile.scheme not in ("http", "https") or not teile.hostname:
        return "fehlt"
    netloc = teile.netloc.rsplit("@", 1)[-1]
    return urllib.parse.urlunsplit(teile._replace(netloc=netloc))


def _abschluss_versuch(repo: Path, spec: int, konfig: dict[str, Any], zustand: dict[str, Any]) -> tuple[list[str], str]:
    """Ein Anstoß; Rückgabe (Zeilen, Fehlergrund — leer = kein Fehlversuch)."""
    try:
        code, out, err = _abschluss_lauf(repo, "nachsehen", spec)
        if code == 2:
            stage = _stage_ohne_zugang(str((konfig.get("staging") or {}).get("url") or ""))
            grund = "fehlt (Aufseher hat kein Paket abgelegt)"
            fertig = str(zustand.get("spec_fertig_seit") or datetime.now().astimezone().isoformat(timespec="seconds"))
            args = [
                "--stage",
                stage,
                "--rundschau",
                grund,
                "--tests",
                grund,
                "--ohne-rundschau",
                "--spec-fertig",
                fertig,
            ]
            code, out, err = _abschluss_lauf(repo, "ablegen", spec, *args)
            if code != 0:
                text = _abschluss_ausgabe("ablegen", spec, code, out, err)
                return [f"FEHLER: Abschluss-Mail — Notfall-Ablage gescheitert (Exit {code}: {text})."], text
            code, out, err = _abschluss_lauf(repo, "nachsehen", spec)
            if code == 2:
                text = _abschluss_ausgabe("nachsehen", spec, code, out, err)
                zeile = f"FEHLER: Abschluss-Mail — nach der Notfall-Ablage findet nachsehen kein Paket ({text})."
                return [zeile], text
    except (OSError, subprocess.TimeoutExpired) as fehler:
        zeile = f"FEHLER: Abschluss-Mail — nachsehen nicht gelaufen ({fehler}); nächster Tick versucht es wieder."
        return [zeile], str(fehler)
    if code == 0:
        zustand["abschluss_mail_erledigt"] = datetime.now().astimezone().isoformat(timespec="seconds")
        if not melder.mail_eingerichtet(konfig):
            return ["Abschluss-Mail: erledigt ohne Versand", MAIL_AUS], ""
        return ["Abschluss-Mail: verschickt/erledigt"], ""
    if code == 3:
        return [f"Abschluss-Mail wartet: {out[-300:]}"], ""
    text = _abschluss_ausgabe("nachsehen", spec, code, out, err)
    return [f"FEHLER: Abschluss-Mail — nachsehen Exit {code}: {text} (nächster Tick versucht es wieder)."], text


def abschluss_anstossen(
    repo: Path, spec: int, gh_repo: str, konfig: dict[str, Any], zustand: dict[str, Any], dry_run: bool
) -> list[str]:
    """Abschluss-Mail nach SPEC FERTIG anstoßen (#588): ``abschluss_paket.py nachsehen``.

    Exit 0 → erledigt (``zustand["abschluss_mail_erledigt"]``; ohne ``mail.befehl`` zusätzlich
    :data:`MAIL_AUS`), 3 → wartet auf Thermo/Rückblick, 2 → keine Ablage: capo legt einmal selbst ab
    (Links „fehlt“, Stage-URL ohne Zugangsdaten, ``--spec-fertig`` = ``spec_fertig_seit``) und sieht
    erneut nach — eine spätere Ablage des Aufsehers überschreibt das Paket, die SPEC-FERTIG-Zeit bleibt.
    Fehler → FEHLER-Zeile, der nächste Tick versucht es wieder; Fehlversuche in Folge zählen
    (``abschluss_fehlversuche``), ab :data:`ABSCHLUSS_ALARM_AB` einmal ein Kommentar auf #S
    (``abschluss_alarm``). Jeder Lauf ohne Fehler setzt den Zähler zurück.
    """
    if dry_run:
        return [f"[Probe] Abschluss-Mail: abschluss_paket.py nachsehen {spec}"]
    zeilen, grund = _abschluss_versuch(repo, spec, konfig, zustand)
    if not grund:
        zustand.pop("abschluss_fehlversuche", None)
        return zeilen
    fehl = int(zustand.get("abschluss_fehlversuche") or 0) + 1
    zustand["abschluss_fehlversuche"] = fehl
    if fehl >= ABSCHLUSS_ALARM_AB and not zustand.get("abschluss_alarm"):
        text = f"{WAECHTER_KOPF} Abschluss-Mail scheitert seit {fehl} Ticks: {_kurz(grund, 300)} — bitte prüfen"
        if befund.kommentiere(spec, gh_repo, text):
            zustand["abschluss_alarm"] = datetime.now().astimezone().isoformat(timespec="seconds")
        else:
            zeilen.append(f"FEHLER: Abschluss-Mail — Kommentar auf #{spec} nicht geschrieben.")
    return zeilen


def _melde(
    repo: Path,
    konfig: dict[str, Any],
    gh_repo: str,
    dry_run: bool,
    art: str,
    betreff: str,
    text: str,
    schluessel: str,
) -> list[str]:
    """Mail über den Melder; Fehlschlag = FEHLER-Zeile (der nächste Tick versucht es wieder).

    Ohne ``mail.befehl`` hat das Repo bewusst keine Mail: dann nur :data:`MAIL_AUS`
    (INFO, Exit 0). FEHLER heißt nur: ein eingerichteter Versand ist gescheitert.
    """
    if dry_run:
        return [f"[Probe] Mail {art}: {betreff}"]
    if not melder.darf_raus(art, konfig):
        melder.melden(repo, art, betreff, text, schluessel, konfig=konfig, gh_repo=gh_repo)
        return []
    if not melder.mail_eingerichtet(konfig):
        log.info("Mail nicht eingerichtet (mail.befehl leer) — %s: %s", art, betreff)
        return [MAIL_AUS]
    if melder.schon_gesendet(repo, schluessel, gh_repo):
        return []
    if melder.melden(repo, art, betreff, text, schluessel, konfig=konfig, gh_repo=gh_repo):
        return [f"Mail {art} verschickt: {betreff}"]
    return [f"FEHLER: Mail {art} nicht verschickt: {betreff} (nächster Tick versucht es wieder)"]
