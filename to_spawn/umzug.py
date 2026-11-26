"""Umzug einer Bau-Session vom lokalen PC auf den Bau-Server — ``/to-spawn-of`` (#212).

Drei Teile:

* :func:`umzug_einzel` — aus einer laufenden Bau-Session: Handoff prüfen, committen,
  pushen, Session auf dem Server mit dem Handoff als Startkontext neu starten und
  erst nach bewiesenem Server-Lauf das lokale Ende anstoßen (``BAU_UMZUG_DATEI``,
  ``bau.py`` beendet daraufhin die lokale Session — kein Doppel-Lauf).
* :func:`umzug_alle` — Aufseher-Variante: alle laufenden Ticket-Sessions einer Spec
  streng nacheinander umziehen, nie zwei halb.
* :func:`hook_umzug_anfrage` — Stop-Hook in jeder Bau-Session: liegt eine Anfrage des
  Aufsehers, bekommt die Session die Anweisung, sich selbst umzuziehen.

Außerdem :func:`starte_mit_umzug_wache` für ``bau.py``/``wache.py``: startet das
Claude-Kind und beendet es, sobald die Umzug-Datei auftaucht.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO

from . import config, manifest, prozessbaum, sitzungsjob, spawn_exit, starter, tmux_aufruf

log = logging.getLogger("to_spawn.umzug")

EXIT_OK = 0
EXIT_FEHLER = 1
EXIT_WEIGERUNG = 3
#: ``spawn_srv.sh --umzug``: das Ticket läuft auf dem Server schon — Umzug abgebrochen.
EXIT_LAEUFT_SCHON = spawn_exit.EXIT_UMZUG_LAEUFT
#: ``spawn_srv.sh``: Start-Sperre belegt — später erneut, kein Fehler (#604).
EXIT_SPERRE = spawn_exit.EXIT_SPERRE
#: ``ssh`` selbst scheiterte (Verbindung weg) — der Befehl kann trotzdem gelaufen sein.
SSH_EXIT_EIGEN = 255

#: Server-Beweis: Takt und Höchstdauer (Sekunden) des Pollens nach dem Start.
BEWEIS_TAKT = 5.0
BEWEIS_MAX = 90.0
#: Wiederholungen bei belegter Start-Sperre (Exit 10, #604).
SPERRE_VERSUCHE = 3
SPERRE_PAUSE_S = 5.0
#: Höchstdauer des Server-Starts per SSH (``spawn_srv.sh`` wartet bis zu 10 s auf Fenster).
SSH_START_TIMEOUT = 300
SSH_KURZ_TIMEOUT = 30
#: Frist zwischen ``terminate()`` und ``kill()`` für das Claude-Kind.
KILL_FRIST = 20.0
#: Ruhefrist (s) nach einer Fertig-Meldung des Stop-Hooks, bevor ``bau.py`` die stille
#: Session beendet (nur Linux, #288) — gibt parallelen Stop-Hooks (z. B. ``review/stop.py``)
#: Zeit, die Session weiterlaufen zu lassen. Per ``TO_SPAWN_FERTIG_RUHE_S`` kürzbar (Tests).
FERTIG_RUHE_S = 5.0
FERTIG_RUHE_ENV = "TO_SPAWN_FERTIG_RUHE_S"
#: Nachprüf-Fenster (s), wenn nach einer Fertig-Meldung kein neuer Transkript-Zug erschien,
#: ``session.json`` oder Pane aber noch kurz ``busy``/aktiv meldet (nur Linux, #288).
FERTIG_NACHPRUEF_S = 30.0
FERTIG_NACHPRUEF_ENV = "TO_SPAWN_FERTIG_NACHPRUEF_S"
#: Prüf-Takt (s) für Werkzeuge ohne Claude-Stop-Hook (z. B. ``agy``, nur Linux, #288).
FERTIG_POLL_S = 30.0
FERTIG_POLL_ENV = "TO_SPAWN_FERTIG_POLL_S"
#: Transkript-Einträge, die echte Weiterarbeit nach einem Stop-Hook belegen (#288).
_AKTIVE_TRANSKRIPT_TYPEN = frozenset({"assistant", "user", "api-request"})
#: Aktiver Subagent-Hinweis in der Statuszeile von ``agy`` (z. B. ``1 subagent(s)``, #288).
_AGY_SUBAGENT_AKTIV = re.compile(r"\b[1-9]\d*\s+subagent\(s\)")

HANDOFF_NAME = re.compile(r"^HANDOFF_\d{4}-\d{2}-\d{2}_(\d+)\.md$")
#: Pflichtzeile im Umzug-Handoff — Fettschrift/Groß-Klein egal (wie ``STAFFEL_MARKER``),
#: auch als Markdown-Überschrift (``## Umzug: server``).
UMZUG_MARKER = re.compile(r"^\s*(?:#+\s*)?\**\s*umzug\s*\**\s*:\s*\**\s*server", re.IGNORECASE | re.MULTILINE)
#: Gleiches Muster wie ``scripts/hooks/staffel_stop.py`` — diese Zeile startet die lokale Staffel neu.
STAFFEL_MARKER = re.compile(r"^\s*\**\s*staffel\s*\**\s*:\s*\**\s*weiter", re.IGNORECASE | re.MULTILINE)
GESCHUETZTE_BRANCHES = frozenset({"master", "main"})
SKILL = Path(__file__).resolve().parent.parent


def _ist_windows() -> bool:
    return sys.platform == "win32"


def _jetzt() -> datetime:
    """Ortszeit mit Zeitzone (Handoff-Datum und Zeitstempel wie auf dem PC)."""
    return datetime.now(UTC).astimezone()


class Abbruch(Exception):
    """Umzug hält an; ``code`` ist der Exit-Code (3 = Weigerung, 1 = Fehler)."""

    def __init__(self, code: int, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


# --- Kleine Helfer ------------------------------------------------------------


def _git(ort: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(ort),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **prozessbaum.fensterlos(),
    )


def haupt_repo(ort: Path) -> Path:
    """Haupt-Repo eines Worktrees (bei einem normalen Klon: der Klon selbst)."""
    ergebnis = _git(ort, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if ergebnis.returncode != 0 or not ergebnis.stdout.strip():
        return ort
    return Path(ergebnis.stdout.strip()).parent


def ssh_ziel(konfig: dict[str, Any]) -> str:
    """SSH-Ziel: Umgebung ``TO_SPAWN_SSH_ZIEL`` gewinnt, sonst Konfig ``ssh_ziel``."""
    return config.ssh_ziel(konfig)


def server_repo(konfig: dict[str, Any], repo: Path) -> str:
    """Repo-Ordner auf dem Server: Konfig ``server_repo``, sonst ``~/<Name des Repo-Ordners>``."""
    return str(konfig.get("server_repo") or f"~/{repo.name}")


def _cd(ordner: str) -> str:
    """``cd`` mit Quoting, das die Tilde am Anfang trotzdem expandieren lässt."""
    if ordner == "~":
        return "cd ~"
    if ordner.startswith("~/"):
        return f"cd ~/{shlex.quote(ordner[2:])}"
    return f"cd {shlex.quote(ordner)}"


def fern_skill_befehl(ordner: str, aufruf: str, skript: str, argumente: Sequence[str]) -> str:
    """Fern-Befehl: Skill-Skript ``skripte/<skript>`` für das Server-Repo ``ordner`` (Etappe 1, P4a; #786).

    Hat das Repo die Weiterleitung ``scripts/<skript>`` (DuoPlus), läuft der Befehl unverändert über
    sie. Sonst ruft er direkt ``<Skill-Ort>/skripte/<skript>`` (über :func:`config.fern_skill_pfad`)
    mit ``TO_SPAWN_REPO`` = Repo auf, wie ``skripte/spawn_srv.sh`` die Skill-Skripte startet.
    ``aufruf`` = ``bash``/``python3``.
    """
    ort = _cd(ordner)[3:]
    weiter = f"scripts/{skript}"
    args = shlex.join(argumente)
    skill_skript = config.fern_skill_pfad(f"skripte/{skript}")
    fern_env = (
        f"{config.SKILL_HOME_ENV}={config.fern_skill_pfad()} "
        if (os.environ.get(config.SKILL_HOME_ENV) or "").strip()
        else ""
    )
    return (
        f"if [ -f {ort}/{weiter} ]; then cd {ort} && {fern_env}{aufruf} {weiter} {args}; "
        f'else cd {ort} && {fern_env}TO_SPAWN_REPO="$PWD" {aufruf} {skill_skript} {args}; fi'
    )


def _ssh(ziel: str, befehl: str, timeout: float) -> subprocess.CompletedProcess[str] | None:
    """Ein Befehl per SSH; ``None`` bei Zeitüberschreitung oder fehlendem ``ssh``.

    Läuft über die Start-Tür (#1406): ``spawn_srv.sh`` startet echte Server-Sessions; unter
    Test nur die ``ssh``-Attrappe aus ``TO_SPAWN_TEST_BIN``, sonst ``StartGesperrt``.
    """
    try:
        return starter.starte(
            ["ssh", "-o", "BatchMode=yes", ziel, befehl],
            warten=True,
            notnagel=None,  # fehlendes ssh → OSError unten, nie über die Shell
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.error("SSH %s: keine Antwort nach %s s (%s)", ziel, timeout, befehl[:80])
    except OSError as fehler:
        log.error("SSH nicht aufrufbar: %s", fehler)
    return None


def spawn_befehl(
    spec: str,
    ticket: str | None,
    ordner: str,
    *,
    umzug_ref: str | None = None,
    nur_wache: bool = False,
) -> str:
    """Server-Befehl: ``spawn_srv.sh`` für das Server-Repo (Weiterleitung, sonst Skill-Skript)."""
    teile = [spec]
    if nur_wache:
        teile += ["--nur-wache", "--ohne-regularien"]
    else:
        teile += ["--tickets", str(ticket), "--ohne-wache"]
        if umzug_ref:
            teile += ["--umzug", umzug_ref]
        else:
            teile.append("--ohne-regularien")
    return fern_skill_befehl(ordner, "bash", "spawn_srv.sh", teile)


def _server_start_ergebnis(
    spec: str,
    ticket: str | None,
    *,
    ziel: str,
    ordner: str,
    umzug_ref: str | None = None,
    nur_wache: bool = False,
    repo: Path | None = None,
) -> int:
    """Wie :func:`server_starten`, liefert aber den Ergebnis-Code (0 ok, 10 Sperre, 1 Fehler)."""
    befehl = spawn_befehl(spec, ticket, ordner, umzug_ref=umzug_ref, nur_wache=nur_wache)
    was = f"#{ticket}" if ticket else f"Aufseher Spec #{spec}"
    versuche = max(1, SPERRE_VERSUCHE)
    for versuch in range(1, versuche + 1):
        log.info("Server-Start: ssh %s %s", ziel, befehl)
        ergebnis = _ssh(ziel, befehl, SSH_START_TIMEOUT)
        if ergebnis is not None and ergebnis.returncode == 0:
            return EXIT_OK
        if ergebnis is not None and spawn_exit.ist_spaeter_erneut(ergebnis.returncode):
            if versuch < versuche:
                log.info(
                    "%s: %s (Exit %s) — warte %s s, später erneut (Versuch %d/%d).",
                    was,
                    spawn_exit.beschreibung(ergebnis.returncode),
                    ergebnis.returncode,
                    SPERRE_PAUSE_S,
                    versuch,
                    versuche,
                )
                if SPERRE_PAUSE_S > 0:
                    time.sleep(SPERRE_PAUSE_S)
                continue
            log.warning(
                "%s: %s (Exit %s) — später erneut versuchen: %s",
                was,
                spawn_exit.beschreibung(ergebnis.returncode),
                ergebnis.returncode,
                (ergebnis.stderr or ergebnis.stdout).strip()[-400:],
            )
            return EXIT_SPERRE
        if ergebnis is not None and ergebnis.returncode == EXIT_LAEUFT_SCHON:
            log.error(
                "%s läuft auf dem Server schon — Umzug abgebrochen, lokale Session läuft weiter "
                "(nie zwei Sessions für ein Ticket): %s",
                was,
                (ergebnis.stderr or ergebnis.stdout).strip()[-400:],
            )
            return EXIT_FEHLER
        if ergebnis is None or ergebnis.returncode == SSH_EXIT_EIGEN:
            grund = "keine Antwort" if ergebnis is None else (ergebnis.stderr or ergebnis.stdout).strip()[-200:]
            log.warning(
                "SSH brach beim Server-Start ab (%s) — prüfe einmal, ob %s trotzdem läuft.",
                grund,
                was,
            )
            if server_laeuft(spec, ticket, ziel=ziel, ordner=ordner, repo=repo):
                log.warning("%s läuft trotzdem auf %s — Start gilt als gelungen.", was, ziel)
                return EXIT_OK
            log.error("Server-Start scheiterte: SSH abgebrochen und %s läuft nicht auf %s.", was, ziel)
            return EXIT_FEHLER
        log.error(
            "Server-Start scheiterte (Exit %s, %s): %s",
            ergebnis.returncode,
            spawn_exit.beschreibung(ergebnis.returncode),
            (ergebnis.stderr or ergebnis.stdout).strip()[-400:],
        )
        return EXIT_FEHLER
    return EXIT_SPERRE


def server_starten(
    spec: str,
    ticket: str | None,
    *,
    ziel: str,
    ordner: str,
    umzug_ref: str | None = None,
    nur_wache: bool = False,
    repo: Path | None = None,
) -> bool:
    """Startet ``bau <N>`` (oder nur ``wache <S>``) per SSH auf dem Server.

    Bricht die SSH-Verbindung selbst ab (Zeitüberschreitung, Exit 255), kann der Start
    trotzdem durchgelaufen sein — dann einmal den Server-Beweis prüfen: läuft die
    Session, gilt der Start (laut geloggt), sonst bleibt es ein Fehler. Exit 4 von
    ``spawn_srv.sh --umzug`` = das Ticket läuft dort schon → Umzug abgebrochen.
    Exit 10 = Start-Sperre belegt → wird kurz wiederholt, sonst als „später erneut“ geloggt (#604).
    """
    return (
        _server_start_ergebnis(
            spec,
            ticket,
            ziel=ziel,
            ordner=ordner,
            umzug_ref=umzug_ref,
            nur_wache=nur_wache,
            repo=repo,
        )
        == EXIT_OK
    )


def _sitzungen_fern(spec: str, repo: Path | None = None) -> tuple[str, ...]:
    """Kandidaten für den tmux-Session-Namen auf dem Server: neu (falls ``repo`` bekannt) + alt."""
    alt = tmux_aufruf.sitzung_alt(spec)
    if repo is None:
        return (alt,)
    neu = tmux_aufruf.sitzung_name(repo, spec)
    return (neu, alt) if neu != alt else (alt,)


def server_fenster_schliessen(spec: str, ticket: str, *, ziel: str, repo: Path | None = None) -> bool:
    """tmux-Fenster ``bau <N>`` auf dem Server schließen (Doppelstart zurücknehmen)."""
    teile = [f"tmux kill-window -t {shlex.quote(f'={sitz}:bau {ticket}')}" for sitz in _sitzungen_fern(spec, repo)]
    befehl = " 2>/dev/null || ".join(teile)
    log.info("Server-Fenster schließen: ssh %s %s", ziel, befehl)
    ergebnis = _ssh(ziel, befehl, SSH_KURZ_TIMEOUT)
    if ergebnis is None or ergebnis.returncode != 0:
        log.error(
            "Server-Fenster bau %s ließ sich nicht schließen — auf %s nachsehen: sessions %s",
            ticket,
            ziel,
            spec,
        )
        return False
    return True


def _stand_zeile(text: str, ticket: str) -> str | None:
    """Zustand von ``#<ticket>`` aus der ``sessions_stand.py``-Tabelle."""
    for zeile in text.splitlines():
        teile = zeile.split()
        if len(teile) >= 2 and teile[0] == f"#{ticket}":
            return teile[1]
    return None


def server_laeuft(
    spec: str,
    ticket: str | None,
    *,
    ziel: str,
    ordner: str,
    repo: Path | None = None,
) -> bool:
    """Einmal prüfen: tmux-Fenster da und (bei Tickets) Session nicht ``aus``."""
    fenster = "bau " + ticket if ticket else f"wache {spec}"
    teile = [f"tmux list-windows -t ={shlex.quote(sitz)} -F '#W'" for sitz in _sitzungen_fern(spec, repo)]
    liste = _ssh(ziel, " 2>/dev/null || ".join(teile), SSH_KURZ_TIMEOUT)
    if liste is None or liste.returncode == SSH_EXIT_EIGEN:
        grund = "keine Antwort" if liste is None else (liste.stderr or liste.stdout).strip()[:200] or "Exit 255"
        log.warning(
            "SSH-Beobachtung %s: Verbindung beim Status-Lesen abgebrochen (%s) — verbinde im nächsten Takt neu.",
            ziel,
            grund,
        )
        return False
    if liste.returncode != 0:
        return False
    if fenster not in [z.strip() for z in liste.stdout.splitlines()]:
        return False
    if ticket is None:
        return True
    stand = _ssh(
        ziel,
        fern_skill_befehl(ordner, "python3", "sessions_stand.py", [spec]),
        SSH_KURZ_TIMEOUT,
    )
    if stand is None or stand.returncode == SSH_EXIT_EIGEN:
        grund = "keine Antwort" if stand is None else (stand.stderr or stand.stdout).strip()[:200] or "Exit 255"
        log.warning(
            "SSH-Beobachtung %s: Verbindung beim Status-Lesen abgebrochen (%s) — verbinde im nächsten Takt neu.",
            ziel,
            grund,
        )
        return False
    if stand.returncode != 0:
        return False
    zustand = _stand_zeile(stand.stdout, ticket)
    return zustand is not None and zustand != "aus"


def _warte_auf_server(
    spec: str,
    ticket: str | None,
    *,
    ziel: str,
    ordner: str,
    repo: Path | None = None,
) -> bool:
    ende = time.monotonic() + BEWEIS_MAX
    while True:
        if server_laeuft(spec, ticket, ziel=ziel, ordner=ordner, repo=repo):
            return True
        if time.monotonic() >= ende:
            return False
        time.sleep(BEWEIS_TAKT)


def lokales_ende_anstossen(daten: dict[str, Any]) -> None:
    """Schreibt die Umzug-Datei für ``bau.py``/``wache.py`` (die beenden dann die Session)."""
    datei = os.environ.get("BAU_UMZUG_DATEI")
    if not datei:
        log.warning(
            "Umzug bestätigt, aber diese Session läuft nicht über bau.py/wache.py — "
            "Session von Hand beenden (/exit), sonst Doppel-Lauf."
        )
        return
    pfad = Path(datei)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    log.info("Umzug-Datei geschrieben: %s — die lokale Session wird beendet.", pfad)


# --- Manifest -----------------------------------------------------------------


def spec_von_ticket(ticket: str, orte: Sequence[Path]) -> str | None:
    """Spec-Nummer aus dem ersten Manifest (``pfade.manifeste``), das das Ticket führt."""
    for ort in orte:
        for datei in sorted(manifest.manifest_ordner(ort).glob("*.json")):
            if datei.name == "_default.json":
                continue
            try:
                daten = json.loads(datei.read_text(encoding="utf-8"))
            except (OSError, ValueError) as fehler:
                log.warning("Manifest unlesbar %s: %s", datei, fehler)
                continue
            if isinstance(daten, dict) and str(ticket) in (daten.get("tickets") or {}):
                spec = daten.get("spec")
                if spec is not None:
                    return str(spec)
    return None


# --- Einzel-Umzug ---------------------------------------------------------------


def pruefe_handoff(handoff: Path, ticket: str) -> None:
    """Weigert sich (``Abbruch`` 3), wenn der Handoff nicht für einen Umzug taugt."""
    m = HANDOFF_NAME.match(handoff.name)
    if not m or m.group(1) != str(ticket):
        raise Abbruch(
            EXIT_WEIGERUNG,
            f"Handoff-Name {handoff.name!r} passt nicht — Abhilfe: "
            f"docs/handoffs/HANDOFF_{_jetzt().date().isoformat()}_{ticket}.md schreiben.",
        )
    if not handoff.is_file():
        raise Abbruch(EXIT_WEIGERUNG, f"Handoff fehlt: {handoff} — Abhilfe: erst schreiben.")
    inhalt = handoff.read_text(encoding="utf-8", errors="replace")
    if not UMZUG_MARKER.search(inhalt):
        raise Abbruch(
            EXIT_WEIGERUNG,
            "Handoff ohne Zeile „Umzug: server“ — Abhilfe: Zeile ergänzen, dann erneut.",
        )
    if STAFFEL_MARKER.search(inhalt):
        raise Abbruch(
            EXIT_WEIGERUNG,
            "Handoff enthält „Staffel: weiter“ — die lokale Staffel würde neu starten. "
            "Abhilfe: Zeile entfernen, dann erneut.",
        )


def _branch(worktree: Path) -> str:
    ergebnis = _git(worktree, "symbolic-ref", "--short", "-q", "HEAD")
    branch = ergebnis.stdout.strip()
    if ergebnis.returncode != 0 or not branch:
        raise Abbruch(
            EXIT_WEIGERUNG,
            "Kein Branch (detached HEAD) — Abhilfe: Arbeit auf den Ticket-Branch legen.",
        )
    if branch in GESCHUETZTE_BRANCHES:
        raise Abbruch(
            EXIT_WEIGERUNG,
            f"Branch {branch} — Umzug nur von einem Ticket-Branch im Worktree, nie vom Hauptzweig.",
        )
    return branch


def _relativ(handoff: Path, worktree: Path) -> str:
    try:
        return handoff.resolve().relative_to(worktree.resolve()).as_posix()
    except ValueError as fehler:
        raise Abbruch(EXIT_WEIGERUNG, f"Handoff {handoff} liegt nicht im Worktree {worktree}.") from fehler


def _fremde_aenderungen(worktree: Path, rel: str) -> list[str]:
    """Geänderte und ungetrackte Dateien außer dem Handoff (git-ignorierte zählen nicht).

    Ungetrackte Arbeitsdateien kämen nicht mit auf den Server — die Session dort liefe
    ohne sie weiter. Deshalb zählen sie wie ungesicherte Änderungen.
    """
    status = _git(worktree, "status", "--porcelain", "--untracked-files=all")
    if status.returncode != 0:
        raise Abbruch(EXIT_FEHLER, f"git status scheiterte: {status.stderr.strip()}")
    fremde: list[str] = []
    for zeile in status.stdout.splitlines():
        pfad = zeile[3:].strip().strip('"')
        if " -> " in pfad:
            pfad = pfad.split(" -> ", 1)[1]
        if pfad != rel:
            fremde.append(pfad)
    return fremde


def stand_sichern(worktree: Path, handoff: Path, ticket: str, *, dry_run: bool) -> tuple[str, str, str]:
    """Handoff committen (nur er, mit Pathspec) und pushen.

    Rückgabe ``(branch, relpfad, sha)`` — ``sha`` ist der gepushte Commit (im Probelauf
    der aktuelle ``HEAD``).
    """
    branch = _branch(worktree)
    rel = _relativ(handoff, worktree)
    fremde = _fremde_aenderungen(worktree, rel)
    if fremde:
        raise Abbruch(
            EXIT_WEIGERUNG,
            "Ungesicherte Änderungen: "
            + ", ".join(fremde[:5])
            + " — Abhilfe: erst eigene Arbeit mit Pathspec committen (neue Dateien mit "
            "git add), dann erneut.",
        )
    handoff_offen = bool(_git(worktree, "status", "--porcelain", "--", rel).stdout.strip())
    nachricht = f"docs(#{ticket}): Umzug-Handoff auf den Bau-Server (#{ticket}) [skip ci]"
    if dry_run:
        if handoff_offen:
            print(f"Probelauf: git add {rel} && git commit -m {shlex.quote(nachricht)} -- {rel}")
        print(f"Probelauf: git push -u origin HEAD:refs/heads/{branch}")
        return branch, rel, _git(worktree, "rev-parse", "HEAD").stdout.strip()
    if handoff_offen:
        for args in (("add", "--", rel), ("commit", "-q", "-m", nachricht, "--", rel)):
            ergebnis = _git(worktree, *args)
            if ergebnis.returncode != 0:
                raise Abbruch(
                    EXIT_FEHLER,
                    f"git {args[0]} scheiterte: {(ergebnis.stderr or ergebnis.stdout).strip()}",
                )
        log.info("Handoff committet: %s", rel)
    push = _git(worktree, "push", "-q", "-u", "origin", f"HEAD:refs/heads/{branch}")
    if push.returncode != 0:
        raise Abbruch(EXIT_FEHLER, f"git push scheiterte: {push.stderr.strip()}")
    kopf = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    ls_remote = _git(worktree, "ls-remote", "origin", f"refs/heads/{branch}")
    if ls_remote.returncode != 0:
        raise Abbruch(
            EXIT_FEHLER,
            f"git ls-remote scheiterte (Exit {ls_remote.returncode}): "
            f"{(ls_remote.stderr or ls_remote.stdout).strip()[:300]} — Push nicht bewiesen.",
        )
    fern = ls_remote.stdout.split()
    if not fern or fern[0] != kopf:
        raise Abbruch(
            EXIT_FEHLER,
            f"Push nicht bewiesen: origin/{branch} = {fern[0] if fern else '—'}, HEAD = {kopf}.",
        )
    log.info("Gepusht und bewiesen: origin/%s = %s", branch, kopf[:10])
    return branch, rel, kopf


def ergebnis_melden(code: int, grund: str) -> None:
    """Ergebnis an den Aufseher: ``<BAU_UMZUG_ANFRAGE>.laeuft`` (nur wenn sie existiert).

    Die Datei legt der Stop-Hook beim Übernehmen der Anfrage an; ``umzug_alle`` liest
    sie und stoppt bei einem Fehlschlag sofort, statt bis zur Frist zu warten.
    """
    anfrage = os.environ.get("BAU_UMZUG_ANFRAGE")
    if not anfrage:
        return
    laeuft = Path(f"{anfrage}.laeuft")
    if not laeuft.is_file():
        return
    try:
        laeuft.write_text(
            json.dumps(
                {
                    "exit": code,
                    "grund": grund,
                    "zeit": _jetzt().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as fehler:
        log.warning("Ergebnis für den Aufseher nicht schreibbar (%s): %s", laeuft, fehler)


def umzug_einzel(
    ticket: str,
    handoff: Path,
    *,
    worktree: Path | None = None,
    konfig: dict[str, Any],
    dry_run: bool = False,
) -> int:
    """Zieht die laufende Bau-Session ``ticket`` auf den Server um (Exit 0/1/3)."""
    ticket = str(ticket)
    worktree = (worktree or Path.cwd()).resolve()
    handoff = handoff if handoff.is_absolute() else (Path.cwd() / handoff)
    haupt = haupt_repo(worktree)
    ziel = ssh_ziel(konfig)
    ordner = server_repo(konfig, haupt)
    try:
        pruefe_handoff(handoff, ticket)
        spec = spec_von_ticket(ticket, [haupt, worktree])
        if spec is None:
            raise Abbruch(
                EXIT_WEIGERUNG,
                f"Kein Manifest führt Ticket #{ticket} — Abhilfe: {manifest.manifest_im_repo(haupt, '<S>')} prüfen.",
            )
        branch, rel, sha = stand_sichern(worktree, handoff, ticket, dry_run=dry_run)
        # Commit-SHA in der Referenz: der Server liest genau diesen Stand des Handoffs,
        # auch wenn der Branch inzwischen weiterläuft.
        ref = f"{branch}@{sha}:{rel}"
        if dry_run:
            print(
                f"Probelauf: ssh -o BatchMode=yes {ziel} {shlex.quote(spawn_befehl(spec, ticket, ordner, umzug_ref=ref))}"
            )
            print("Probelauf — nichts committet, gepusht oder gestartet.")
            return EXIT_OK
        start_rc = _server_start_ergebnis(spec, ticket, ziel=ziel, ordner=ordner, umzug_ref=ref, repo=haupt)
        if spawn_exit.ist_spaeter_erneut(start_rc):
            raise Abbruch(
                EXIT_SPERRE,
                f"{spawn_exit.beschreibung(start_rc)} — lokale Session läuft weiter, später erneut.",
            )
        if start_rc != EXIT_OK:
            raise Abbruch(EXIT_FEHLER, "Server-Start scheiterte — lokale Session läuft weiter.")
        if not _warte_auf_server(spec, ticket, ziel=ziel, ordner=ordner, repo=haupt):
            raise Abbruch(
                EXIT_FEHLER,
                f"Kein Beweis, dass #{ticket} auf {ziel} läuft (tmux-Fenster/sessions) — "
                "lokale Session läuft weiter, auf dem Server nachsehen: sessions " + spec,
            )
    except Abbruch as abbruch:
        if spawn_exit.ist_spaeter_erneut(abbruch.code):
            log.warning("SPÄTER ERNEUT: %s", abbruch.text)
        else:
            stufe = "WEIGERUNG" if abbruch.code == EXIT_WEIGERUNG else "FEHLER"
            log.error("%s: %s", stufe, abbruch.text)
        if not dry_run:
            ergebnis_melden(abbruch.code, abbruch.text)
        return abbruch.code
    log.info("#%s läuft auf %s (Spec #%s, Branch %s).", ticket, ziel, spec, branch)
    ergebnis_melden(EXIT_OK, f"#{ticket} läuft auf {ziel}")
    lokales_ende_anstossen(
        {
            "ticket": ticket,
            "handoff": rel,
            "branch": branch,
            "ziel": ziel,
            "zeit": _jetzt().isoformat(timespec="seconds"),
        }
    )
    return EXIT_OK


# --- Aufseher-Variante -------------------------------------------------------------


def _sessions_stand(repo: Path) -> ModuleType:
    """``skripte/sessions_stand.py`` laden (importieren, nicht kopieren) — auf ``repo`` gebogen."""
    datei = SKILL / "skripte" / "sessions_stand.py"
    spec = importlib.util.spec_from_file_location("_to_spawn_sessions_stand_umzug", datei)
    if spec is None or spec.loader is None:
        raise ImportError(f"sessions_stand.py nicht ladbar: {datei}")
    modul = importlib.util.module_from_spec(spec)
    # Dataclasses mit ``from __future__ import annotations`` brauchen das Modul in sys.modules.
    sys.modules[spec.name] = modul
    spec.loader.exec_module(modul)
    modul.REPO = repo
    modul.MANIFESTE = manifest.manifest_ordner(repo)
    return modul


def lokale_zustaende(repo: Path, spec: str) -> dict[str, tuple[str, int | None]]:
    """Zustand je Ticket der Spec (nur Manifest-Tickets): ``(zustand, pid von bau.py)``."""
    stand = _sessions_stand(repo)
    eintraege = stand.manifeste_lesen(str(spec))
    tickets = {n for n, e in eintraege.items() if e.art == "ticket"}
    stand.zuordnen(eintraege, stand.prozesse_lesen())
    return {n: (eintraege[n].zustand, eintraege[n].pid) for n in tickets}


def prozess_beenden(pid: int) -> None:
    """Lokales ``bau.py`` beenden (nur im Zustand ``wartet`` — kein Claude-Kind)."""
    if _ist_windows():
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False, **prozessbaum.fensterlos()
        )
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        log.info("Prozess %s war schon beendet.", pid)


def anfrage_pfad(repo: Path, ticket: str) -> Path:
    return repo / ".to-spawn" / f"umzug-anfrage-{ticket}"


def anfrage_schreiben(repo: Path, ticket: str) -> Path:
    pfad = anfrage_pfad(repo, ticket)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(_jetzt().isoformat(timespec="seconds"), encoding="utf-8")
    return pfad


def anfrage_loeschen(repo: Path, ticket: str) -> None:
    pfad = anfrage_pfad(repo, ticket)
    pfad.unlink(missing_ok=True)
    Path(f"{pfad}.laeuft").unlink(missing_ok=True)


def _ticket_schluessel(nummer: str) -> tuple[int, str]:
    return (int(nummer), nummer) if nummer.isdigit() else (sys.maxsize, nummer)


def _warte(bedingung: Callable[[], bool], max_s: float, takt: float) -> bool:
    ende = time.monotonic() + max_s
    while True:
        if bedingung():
            return True
        if time.monotonic() >= ende:
            return False
        time.sleep(takt)


def _session_ergebnis(anfrage: Path) -> dict[str, Any] | None:
    """Ergebnis, das ``umzug_einzel`` in ``<anfrage>.laeuft`` geschrieben hat (sonst ``None``).

    Solange dort nur der Zeitstempel des Stop-Hooks steht, gibt es noch kein Ergebnis.
    """
    laeuft = Path(f"{anfrage}.laeuft")
    try:
        daten = json.loads(laeuft.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return daten if isinstance(daten, dict) and "exit" in daten else None


@contextmanager
def _sigterm_als_ausnahme() -> Iterator[None]:
    """SIGTERM wird zu ``SystemExit`` — so laufen ``finally``-Blöcke (Anfrage aufräumen).

    Nur im Haupt-Thread möglich; der vorige Handler kommt danach zurück.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    vorher = signal.getsignal(signal.SIGTERM)

    def beenden(nummer: int, _rahmen: object) -> None:
        log.warning("SIGTERM — Umzug bricht ab, offene Anfrage wird aufgeräumt.")
        raise SystemExit(128 + nummer)

    signal.signal(signal.SIGTERM, beenden)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, vorher if vorher is not None else signal.SIG_DFL)


def umzug_alle(
    spec: str,
    *,
    repo: Path,
    konfig: dict[str, Any],
    warte_max: float = 1800,
    ohne_wache: bool = False,
    dry_run: bool = False,
    takt: float = 10.0,
) -> int:
    """Alle Ticket-Sessions der Spec nacheinander auf den Server umziehen (Exit 0/1).

    Der Zustand jedes Tickets wird direkt vor der Aktion neu gelesen (eine wartende
    Session kann inzwischen laufen). ``wartet``: erst Server starten und beweisen, dann
    lokal neu lesen — noch ``wartet`` → lokales ``bau.py`` beenden; inzwischen ``läuft``
    → Server-Fenster schließen und Anfrage-Weg. Jeder Fehlschlag stoppt sofort, der Rest
    bleibt lokal. Die Anfrage-Datei wird immer aufgeräumt (auch bei Strg+C/SIGTERM).
    """
    spec = str(spec)
    ziel = ssh_ziel(konfig)
    ordner = server_repo(konfig, repo)

    def frisch(ticket: str) -> tuple[str, int | None]:
        return lokale_zustaende(repo, spec).get(ticket, ("aus", None))

    def stopp(ticket: str, grund: str) -> int:
        print(f"#{ticket} Stopp: {grund}")
        print(f"Stopp bei #{ticket} — Rest bleibt lokal.")
        return EXIT_FEHLER

    def server_bewiesen(ticket: str) -> bool:
        return _warte(
            lambda: server_laeuft(spec, ticket, ziel=ziel, ordner=ordner, repo=repo),
            BEWEIS_MAX,
            BEWEIS_TAKT,
        )

    def per_anfrage(ticket: str) -> str | None:
        """Anfrage an die laufende Session; Rückgabe: Stopp-Grund oder ``None``."""
        anfrage = anfrage_pfad(repo, ticket)
        ergebnis: dict[str, Any] = {}

        def fertig() -> bool:
            daten = _session_ergebnis(anfrage)
            if daten is not None and daten.get("exit") != EXIT_OK:
                ergebnis.update(daten)
                return True
            return frisch(ticket)[0] == "aus" and server_laeuft(spec, ticket, ziel=ziel, ordner=ordner, repo=repo)

        try:
            anfrage_schreiben(repo, ticket)
            bestaetigt = _warte(fertig, warte_max, takt)
        finally:
            anfrage_loeschen(repo, ticket)
        if ergebnis:
            return f"Session meldet Fehlschlag (Exit {ergebnis.get('exit')}): {ergebnis.get('grund') or '?'}"
        if not bestaetigt:
            return f"Umzug nicht binnen {int(warte_max)} s bestätigt"
        return None

    tickets = sorted(lokale_zustaende(repo, spec), key=_ticket_schluessel)
    with _sigterm_als_ausnahme():
        for ticket in tickets:
            zustand, pid = frisch(ticket)
            if "VERWAIST" in zustand:
                return stopp(ticket, f"{zustand} — Claude läuft ohne bau.py, Mensch nötig")
            if zustand == "aus":
                print(f"#{ticket} übersprungen (lokal aus)")
                continue
            if dry_run:
                weg = "neu auf dem Server, dann bau.py beenden" if zustand == "wartet" else "Anfrage an die Session"
                print(f"#{ticket} Probelauf: {zustand} → {weg}")
                continue
            if zustand == "wartet":
                if pid is None:
                    return stopp(ticket, "wartet, aber keine Prozess-ID")
                # Erst der Server — scheitert er, bleibt lokal alles unangetastet.
                if not server_starten(spec, ticket, ziel=ziel, ordner=ordner, repo=repo):
                    return stopp(ticket, "Server-Start scheiterte — lokal unangetastet")
                if not server_bewiesen(ticket):
                    return stopp(
                        ticket,
                        "kein Beweis, dass die Session auf dem Server läuft — lokal "
                        f"unangetastet, auf dem Server nachsehen: sessions {spec}",
                    )
                zustand, neue_pid = frisch(ticket)
                if zustand == "wartet":
                    ziel_pid = neue_pid or pid
                    prozess_beenden(ziel_pid)
                    if not _warte(lambda: frisch(ticket)[0] == "aus", 30.0, min(takt, 1.0)):
                        server_fenster_schliessen(spec, ticket, ziel=ziel, repo=repo)
                        return stopp(
                            ticket,
                            f"bau.py (pid {ziel_pid}) lässt sich nicht beenden — Server-Fenster wieder geschlossen",
                        )
                elif zustand != "aus":
                    # Lokal ist inzwischen Claude gestartet: Server-Start zurücknehmen,
                    # die Session zieht über den Anfrage-Weg um (mit Handoff).
                    log.warning(
                        "#%s startete lokal, während der Server hochfuhr (%s) — Server-Fenster zu, Anfrage-Weg.",
                        ticket,
                        zustand,
                    )
                    if not server_fenster_schliessen(spec, ticket, ziel=ziel, repo=repo):
                        return stopp(
                            ticket,
                            "läuft jetzt lokal und auf dem Server — Server-Fenster ließ "
                            "sich nicht schließen, Mensch nötig",
                        )
                    if "VERWAIST" in zustand:
                        return stopp(ticket, f"{zustand} — Claude läuft ohne bau.py, Mensch nötig")
                    grund = per_anfrage(ticket)
                    if grund:
                        return stopp(ticket, grund)
            else:
                grund = per_anfrage(ticket)
                if grund:
                    return stopp(ticket, grund)
            print(f"#{ticket} umgezogen → {ziel}")

    if dry_run:
        print("Probelauf — nichts beendet, nichts gestartet.")
        return EXIT_OK
    if ohne_wache:
        return EXIT_OK
    if not server_starten(spec, None, ziel=ziel, ordner=ordner, nur_wache=True, repo=repo):
        print(f"Aufseher Spec #{spec}: Server-Start scheiterte — Aufseher bleibt lokal.")
        return EXIT_FEHLER
    if not _warte(lambda: server_laeuft(spec, None, ziel=ziel, ordner=ordner, repo=repo), BEWEIS_MAX, BEWEIS_TAKT):
        print(f"Aufseher Spec #{spec}: kein tmux-Fenster auf {ziel} — Aufseher bleibt lokal.")
        return EXIT_FEHLER
    print(f"Aufseher Spec #{spec} umgezogen → {ziel}")
    lokales_ende_anstossen(
        {
            "spec": spec,
            "ziel": ziel,
            "zeit": _jetzt().isoformat(timespec="seconds"),
        }
    )
    return EXIT_OK


# --- Stop-Hook ------------------------------------------------------------------


def hook_umzug_anfrage(stdin_json: str | None = None, ausgabe: TextIO | None = None) -> int:
    """Stop-Hook: Anfrage des Aufsehers → Session bekommt die Umzug-Anweisung (einmal).

    Läuft in jeder Bau-Session über ``to_spawn.py hook-stop`` mit (ein Stop-Befehl für
    Bau-Log und Umzug) und einzeln als ``to_spawn.py hook-umzug``.
    """
    roh = sys.stdin.read() if stdin_json is None else stdin_json
    try:
        daten = json.loads(roh or "{}")
    except ValueError:
        daten = {}
    if not isinstance(daten, dict):
        daten = {}
    pfad_text = os.environ.get("BAU_UMZUG_ANFRAGE")
    ticket = os.environ.get("BAU_TICKET")
    if not pfad_text or not ticket or daten.get("stop_hook_active"):
        return 0
    anfrage = Path(pfad_text)
    if not anfrage.is_file():
        return 0
    try:
        anfrage.rename(f"{anfrage}.laeuft")
    except OSError as fehler:
        log.warning("Umzug-Anfrage nicht übernehmbar (%s): %s", anfrage, fehler)
        return 0
    handoff = f"docs/handoffs/HANDOFF_{_jetzt().date().isoformat()}_{ticket}.md"
    grund = (
        "Der Aufseher verlangt den Umzug dieser Session auf den Bau-Server. "
        f"1) Handoff {handoff} im Worktree schreiben (Stand, nächste Schritte, offene Punkte) "
        "mit der Zeile „Umzug: server“, ohne die Zeile „Staffel: weiter“. "
        "2) Eigene Arbeit mit Pathspec committen. "
        f"3) {shlex.quote(Path(sys.executable).as_posix())} "
        f"{shlex.quote((SKILL / 'to_spawn.py').as_posix())} umzug {shlex.quote(ticket)} "
        f"--handoff {shlex.quote(handoff)} ausführen. "
        "Sonst nichts tun."
    )
    (ausgabe or sys.stdout).write(json.dumps({"decision": "block", "reason": grund}, ensure_ascii=False) + "\n")
    return 0


# --- Kind-Prozess mit Umzug-Wache (bau.py / wache.py) ------------------------------


def _baum_beenden(prozess: subprocess.Popen[Any], frist: float) -> bool:
    """Kind samt Unterprozessen beenden; ``True`` nur, wenn es nachweislich weg ist.

    Windows: ``terminate()`` träfe nur ``claude.cmd``/``cmd.exe``, das eigentliche
    Claude liefe weiter → ``taskkill /PID <pid> /T /F`` für den ganzen Baum.
    """
    if _ist_windows():
        tk = subprocess.run(
            ["taskkill", "/PID", str(prozess.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **prozessbaum.fensterlos(),
        )
        try:
            prozess.wait(timeout=frist)
        except subprocess.TimeoutExpired:
            log.error(
                "Lokale Session (pid %s) läuft nach taskkill /T weiter (%s) — Doppel-Lauf "
                "droht! Claude-Fenster von Hand schließen.",
                prozess.pid,
                (tk.stderr or tk.stdout).strip()[:200] or f"Exit {tk.returncode}",
            )
            prozess.kill()  # Notnagel: wenigstens das direkte Kind, damit bau.py nicht hängt
            try:
                prozess.wait(timeout=frist)
            except subprocess.TimeoutExpired:
                log.error("Auch kill wirkte nicht auf pid %s.", prozess.pid)
            return False
        if tk.returncode != 0:
            log.error(
                "taskkill /T meldete Exit %s (%s) — Unterprozesse der Session können noch laufen, Doppel-Lauf prüfen.",
                tk.returncode,
                (tk.stderr or tk.stdout).strip()[:200],
            )
            return False
        return True
    prozess.terminate()
    try:
        prozess.wait(timeout=frist)
        return True
    except subprocess.TimeoutExpired:
        log.warning("Session reagiert nicht auf terminate — kill.")
    prozess.kill()
    try:
        prozess.wait(timeout=frist)
        return True
    except subprocess.TimeoutExpired:
        log.error("Lokale Session (pid %s) lässt sich nicht beenden — Doppel-Lauf droht!", prozess.pid)
        return False


def _fertig_ruhe_s() -> float:
    """Ruhefrist nach Fertig-Meldung: ``TO_SPAWN_FERTIG_RUHE_S`` oder :data:`FERTIG_RUHE_S`."""
    roh = os.environ.get(FERTIG_RUHE_ENV, "").strip()
    if not roh:
        return FERTIG_RUHE_S
    try:
        wert = float(roh)
    except ValueError:
        return FERTIG_RUHE_S
    return max(0.0, wert)


def _fertig_nachpruef_s() -> float:
    """Nachprüf-Fenster bei kurzzeitigem ``busy`` ohne neuen Zug (:data:`FERTIG_NACHPRUEF_S`)."""
    roh = os.environ.get(FERTIG_NACHPRUEF_ENV, "").strip()
    if not roh:
        return FERTIG_NACHPRUEF_S
    try:
        wert = float(roh)
    except ValueError:
        return FERTIG_NACHPRUEF_S
    return max(0.0, wert)


def _fertig_poll_s(standard: float) -> float:
    """Prüf-Takt für Werkzeuge ohne Claude-Stop-Hook (``0`` = aus; :data:`FERTIG_POLL_ENV`)."""
    if standard <= 0:
        return 0.0
    roh = os.environ.get(FERTIG_POLL_ENV, "").strip()
    if not roh:
        return standard
    try:
        wert = float(roh)
    except ValueError:
        return standard
    return max(0.0, wert)


def _iso_zeitstempel(roh: Any) -> float | None:
    """ISO-8601-Zeitstempel in Unix-Sekunden wandeln; ``None`` bei fehlendem/ungültigem Wert."""
    if not isinstance(roh, str):
        return None
    text = roh.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        wert = datetime.fromisoformat(text)
    except ValueError:
        return None
    if wert.tzinfo is None:
        wert = wert.replace(tzinfo=UTC)
    return wert.timestamp()


def _schreibe_fertig_beendet(datei: Path, daten: dict[str, Any]) -> None:
    """Vermerkt atomar ``beendet: True`` in ``datei`` (nur Linux, #288)."""
    kopie = {**daten, "beendet": True}
    tmp = datei.with_name(f".{datei.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(kopie, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, datei)
    except OSError as fehler:
        log.warning("Fertig-Datei %s nicht aktualisierbar: %s", datei, fehler)


def _transkript_aktiv_nach_stop(daten: dict[str, Any]) -> str | None:
    """Prüft das Transkript ab ``transcript_size`` auf echte Weiterarbeit nach dem Stop-Hook (#288).

    Claude Code flusht Transkript-Zeilen teils asynchron: der letzte ``assistant``-Zug vor dem
    Stop-Hook kann erst nach ``stat().st_size`` auf der Platte landen. Enthält der Ausschnitt
    ein ``system/stop_hook_summary``, zählen daher nur Einträge NACH dem letzten
    ``stop_hook_summary`` (oder ``preventedContinuation: True``) als echte Weiterarbeit.
    Ohne ``stop_hook_summary`` werden verspätete Einträge mit Zeitstempel deutlich vor
    ``fertig.json["zeit"]`` ignoriert.
    """
    transkript_roh = str(daten.get("transcript_path") or "").strip()
    groesse = daten.get("transcript_size")
    if not (transkript_roh and isinstance(groesse, int) and not isinstance(groesse, bool) and groesse >= 0):
        return None
    pfad = Path(transkript_roh)
    try:
        if not pfad.is_file():
            return None
        roh = pfad.read_bytes()
    except OSError:
        return None
    ausschnitt = roh[groesse:] if groesse <= len(roh) else roh
    eintraege: list[dict[str, Any]] = []
    for zeile in ausschnitt.decode("utf-8", errors="replace").splitlines():
        zeile = zeile.strip()
        if not zeile:
            continue
        try:
            eintrag = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(eintrag, dict):
            eintraege.append(eintrag)

    letzter_stop_idx = -1
    for idx, eintrag in enumerate(eintraege):
        if eintrag.get("type") == "system" and eintrag.get("subtype") == "stop_hook_summary":
            if eintrag.get("preventedContinuation") is True:
                return "transkript:stop_hook_blockiert"
            letzter_stop_idx = idx

    if letzter_stop_idx >= 0:
        for eintrag in eintraege[letzter_stop_idx + 1 :]:
            typ = eintrag.get("type")
            if typ in _AKTIVE_TRANSKRIPT_TYPEN:
                return f"transkript:{typ}"
        return None

    fertig_zeit = daten.get("zeit")
    zeit_grenze = (
        float(fertig_zeit) - 0.5
        if isinstance(fertig_zeit, (int, float)) and not isinstance(fertig_zeit, bool)
        else None
    )
    for eintrag in eintraege:
        typ = eintrag.get("type")
        if typ not in _AKTIVE_TRANSKRIPT_TYPEN:
            continue
        ts = _iso_zeitstempel(eintrag.get("timestamp"))
        if ts is not None and zeit_grenze is not None and ts < zeit_grenze:
            continue
        return f"transkript:{typ}"
    return None


def _pane_text_eigen() -> str | None:
    """Liest den Text des eigenen tmux-Panes (``TMUX_PANE``); ``None`` ohne Pane oder bei Fehler."""
    pane = os.environ.get("TMUX_PANE", "").strip()
    if not pane:
        return None
    try:
        erg = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", pane],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            **prozessbaum.fensterlos(),
        )
        if erg.returncode == 0:
            return erg.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _lauf_aktiv_nach_stop(prozess_pid: int, *, pane_text: str | None = None) -> str | None:
    """Prüft Live-Signale (Session-JSON ``busy`` oder laufende Pane-Marker ohne Alt-Limit-Text)."""
    from . import speicher

    sessions_ordner = config.heim_ordner() / ".claude" / "sessions"
    try:
        baum = prozessbaum.baum(prozess_pid)
    except OSError:
        baum = [prozess_pid]
    for pid in baum:
        info = speicher.session_json_lesen(pid, sessions_ordner, Path("/proc"))
        if info is not None and info.get("status") == "busy":
            return "session_json:busy"

    text = pane_text if pane_text is not None else _pane_text_eigen()
    if text:
        from . import aufpasser

        status_text = aufpasser.statuszeile(text)
        if any(m in status_text for m in aufpasser.LAUFEND_MARKER):
            return "pane:esc_to_interrupt"
        if any(m in status_text for m in aufpasser.STATUSZEILEN_ARBEITS_MARKER):
            return "pane:background_agent"
        if _AGY_SUBAGENT_AKTIV.search(text):
            return "pane:agy_subagent"
    return None


def _session_aktiv_nach_stop(prozess_pid: int, daten: dict[str, Any]) -> bool:
    """Rein lokale Gegenprobe (nur Linux, #288): arbeitet die Session nach dem Stop-Hook weiter?

    Erkennt drei Fälle:
    1. Neuer Zug im Transkript NACH ``stop_hook_summary`` (oder ``preventedContinuation: True``,
       z. B. wenn ``review/stop.py`` blockierte oder ein Hintergrund-Agent meldete).
    2. Session-JSON unter ``~/.claude/sessions/<pid>.json`` meldet ``status == "busy"``.
    3. Im eigenen tmux-Pane (``TMUX_PANE``) steht ein laufender Arbeits- oder Subagent-Marker
       (ohne alte Limit-Hinweise im Scrollback).
    """
    return (_transkript_aktiv_nach_stop(daten) or _lauf_aktiv_nach_stop(prozess_pid)) is not None


def _agy_transkript_aktiv(ruhe_s: float) -> bool:
    """Wurde das jüngste ``agy``-Transkript des aktuellen Worktrees/Repos gerade beschrieben?"""
    from . import werkzeug

    kandidaten: list[Path] = []
    for var in ("TO_SPAWN_LOG_REPO", "TO_SPAWN_LOG_RUECKFALL"):
        roh = os.environ.get(var, "").strip()
        if roh:
            pfad = Path(roh)
            if pfad.is_dir() and pfad not in kandidaten:
                kandidaten.append(pfad)
    for ordner in kandidaten:
        cid = werkzeug.agy_gespraech_fuer_ordner(ordner)
        if not cid:
            continue
        transkript = werkzeug.agy_transkript_pfad(cid)
        try:
            if transkript.is_file() and (time.time() - transkript.stat().st_mtime) < ruhe_s:
                return True
        except OSError:
            continue
    return False


def _baum_mit_kindern_beenden(prozess: subprocess.Popen[Any], frist: float) -> bool:
    """Beendet ``prozess`` und räumt überlebende Nachkommen (z. B. MCP-Server) auf Linux ab (#288)."""
    try:
        teilbaum = prozessbaum.baum(prozess.pid)
    except OSError:
        teilbaum = []
    ok = _baum_beenden(prozess, frist)
    reste = [pid for pid in teilbaum if pid != prozess.pid and prozessbaum.lebt(pid)]
    if reste:
        stufen = tuple((getattr(signal, name), 2.0) for name in ("SIGTERM", "SIGKILL") if hasattr(signal, name))
        prozessbaum.beenden(list(reversed(reste)), stufen, takt_s=0.1)
    return ok


def _warte_mit_abbruch(
    stopp: threading.Event,
    dauer: float,
    takt: float,
    datei: Path,
    prozess: subprocess.Popen[Any],
) -> bool:
    """Wartet bis ``dauer`` s; bricht bei ``stopp``, ``datei`` oder Prozessende sofort ab.

    Rückgabe ``True`` = sauber durchgelaufen, ``False`` = vorzeitig abgebrochen.
    """
    ende = time.monotonic() + max(0.0, dauer)
    while time.monotonic() < ende:
        if stopp.wait(min(takt, max(0.02, ende - time.monotonic()))):
            return False
        if datei.exists() or prozess.poll() is not None:
            return False
    return not stopp.is_set() and prozess.poll() is None and not datei.exists()


def _umzug_waechter(
    datei: Path,
    prozess: subprocess.Popen[Any],
    stopp: threading.Event,
    stand: dict[str, bool],
    takt: float,
    frist: float,
    fertig_datei: Path | None = None,
    fertig_poll_s: float = 0.0,
) -> None:
    poll_intervall = _fertig_poll_s(fertig_poll_s) if (fertig_datei is not None and not _ist_windows()) else 0.0
    naechster_poll = time.monotonic() + poll_intervall if poll_intervall > 0 else 0.0
    letzter_pane_text: str | None = None

    while not stopp.wait(takt):
        if datei.exists():
            if prozess.poll() is None:
                log.info("Umzug-Datei %s gesehen — lokale Session wird beendet.", datei.name)
                stand["lokal_beendet"] = _baum_beenden(prozess, frist)
            return
        if fertig_datei is None or _ist_windows():
            continue
        staffel_roh = os.environ.get("BAU_STAFFEL_DATEI", "").strip()
        if staffel_roh and Path(staffel_roh).is_file():
            continue
        if not fertig_datei.is_file():
            if poll_intervall > 0 and time.monotonic() >= naechster_poll and prozess.poll() is None:
                naechster_poll = time.monotonic() + poll_intervall
                pane_text = _pane_text_eigen()
                if _lauf_aktiv_nach_stop(prozess.pid, pane_text=pane_text) is not None:
                    letzter_pane_text = pane_text
                    continue
                if pane_text is not None and pane_text != letzter_pane_text:
                    letzter_pane_text = pane_text
                    continue
                if _agy_transkript_aktiv(_fertig_ruhe_s()):
                    continue
                from . import hooks

                try:
                    hooks.fertig_pruefen_und_melden("")
                except Exception as fehler:  # noqa: BLE001 — Prüf-Fehler darf Session nie kippen
                    log.warning("Fertig-Prüfung ohne Stop-Hook fehlgeschlagen: %s", fehler)
            if not fertig_datei.is_file():
                continue
        fertig_daten = lies_fertig(fertig_datei)
        if fertig_daten is None or fertig_daten.get("beendet") is True:
            continue
        ruhe = _fertig_ruhe_s()
        _warte_mit_abbruch(stopp, ruhe, takt, datei, prozess)
        if stopp.is_set() or prozess.poll() is not None:
            return
        if datei.exists():
            log.info("Umzug-Datei %s gesehen — lokale Session wird beendet.", datei.name)
            stand["lokal_beendet"] = _baum_beenden(prozess, frist)
            return
        if staffel_roh and Path(staffel_roh).is_file():
            continue
        aktuell = lies_fertig(fertig_datei)
        if aktuell is None or aktuell.get("zeit") != fertig_daten.get("zeit"):
            continue
        transkript_grund = _transkript_aktiv_nach_stop(aktuell)
        if transkript_grund is not None:
            log.info("Fertig-Meldung verworfen (%s) — Session arbeitet nach Stop-Hook weiter.", transkript_grund)
            fertig_datei.unlink(missing_ok=True)
            continue
        lauf_grund = _lauf_aktiv_nach_stop(prozess.pid)
        if lauf_grund is not None:
            # Kein neuer Zug im Transkript, aber kurzzeitig noch busy/aktiv (z. B. ai-title):
            # nicht sofort für immer verwerfen, sondern innerhalb des Nachprüf-Fensters abwarten (#288).
            nachpruef_ende = time.monotonic() + _fertig_nachpruef_s()
            verworfen = False
            while time.monotonic() < nachpruef_ende:
                schritt = min(takt, 0.5, max(0.02, nachpruef_ende - time.monotonic()))
                if stopp.wait(schritt) or prozess.poll() is not None:
                    return
                if datei.exists():
                    log.info("Umzug-Datei %s gesehen — lokale Session wird beendet.", datei.name)
                    stand["lokal_beendet"] = _baum_beenden(prozess, frist)
                    return
                if staffel_roh and Path(staffel_roh).is_file():
                    verworfen = True
                    break
                transkript_grund = _transkript_aktiv_nach_stop(aktuell)
                if transkript_grund is not None:
                    log.info(
                        "Fertig-Meldung verworfen (%s) — Session arbeitet nach Stop-Hook weiter.",
                        transkript_grund,
                    )
                    fertig_datei.unlink(missing_ok=True)
                    verworfen = True
                    break
                lauf_grund = _lauf_aktiv_nach_stop(prozess.pid)
                if lauf_grund is None:
                    if not _warte_mit_abbruch(stopp, min(ruhe, 1.0), takt, datei, prozess):
                        if datei.exists():
                            log.info("Umzug-Datei %s gesehen — lokale Session wird beendet.", datei.name)
                            stand["lokal_beendet"] = _baum_beenden(prozess, frist)
                        return
                    transkript_grund = _transkript_aktiv_nach_stop(aktuell)
                    lauf_grund = _lauf_aktiv_nach_stop(prozess.pid)
                    if transkript_grund is not None:
                        log.info(
                            "Fertig-Meldung verworfen (%s) — Session arbeitet nach Stop-Hook weiter.",
                            transkript_grund,
                        )
                        fertig_datei.unlink(missing_ok=True)
                        verworfen = True
                    break
            if verworfen:
                continue
            if lauf_grund is not None:
                log.info("Fertig-Meldung verworfen (%s) — Session arbeitet nach Stop-Hook weiter.", lauf_grund)
                fertig_datei.unlink(missing_ok=True)
                continue
        log.info(
            "Fertig-Datei %s gesehen (%s) — stille Session wird beendet.",
            fertig_datei.name,
            aktuell.get("grund") or "fertig",
        )
        _baum_mit_kindern_beenden(prozess, frist)
        _schreibe_fertig_beendet(fertig_datei, aktuell)
        return


def lies_umzug(datei: Path) -> dict[str, Any] | None:
    """Umzug-Datei lesen (``None`` = kein Umzug). Leere/kaputte Datei zählt trotzdem als Umzug."""
    if not datei.is_file():
        return None
    try:
        daten = json.loads(datei.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError) as fehler:
        log.warning("Umzug-Datei unlesbar (%s): %s", datei, fehler)
        daten = {}
    return daten if isinstance(daten, dict) and daten else {"ziel": "?"}


def lies_fertig(datei: Path) -> dict[str, Any] | None:
    """Fertig-Datei lesen (``None`` = fehlt oder unlesbar; nur Linux, #288)."""
    if not datei.is_file():
        return None
    try:
        daten = json.loads(datei.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError) as fehler:
        log.warning("Fertig-Datei unlesbar (%s): %s", datei, fehler)
        return None
    return daten if isinstance(daten, dict) and daten else None


def starte_mit_umzug_wache(
    cmd: Sequence[str],
    datei: Path,
    *,
    takt: float = 1.0,
    frist: float = KILL_FRIST,
    fertig_datei: Path | None = None,
    fertig_poll_s: float = 0.0,
) -> tuple[int, dict[str, Any] | None]:
    """Startet ``cmd`` interaktiv; taucht ``datei`` (oder auf Linux ``fertig_datei``) auf, wird das Kind beendet.

    Rückgabe ``(exit_code, umzug_daten)`` — ``umzug_daten`` ist ``None`` ohne Umzug,
    sonst mit ``lokal_beendet`` (``False`` = die lokale Session ist nicht nachweislich
    weg, Doppel-Lauf möglich). Auf Windows bleibt der ``shell=True``-Notnagel für
    ``.cmd``-Shims — nur, wenn das Programm ohne Shell nicht gefunden wird; ``fertig_datei``
    und ``fertig_poll_s`` wirken nur unter Linux (#288).
    """
    datei.unlink(missing_ok=True)
    if fertig_datei is not None and not _ist_windows():
        fertig_datei.unlink(missing_ok=True)
    try:
        prozess: subprocess.Popen[Any] = starter.starte(list(cmd), notnagel=FileNotFoundError)
    except OSError as fehler:
        log.error("Start von %s scheiterte: %s", cmd[0] if cmd else "?", fehler)
        raise
    stopp = threading.Event()
    stand: dict[str, bool] = {}
    faden = threading.Thread(
        target=_umzug_waechter,
        args=(datei, prozess, stopp, stand, takt, frist, fertig_datei, fertig_poll_s),
        daemon=True,
    )
    faden.start()
    try:
        # Job hält den ganzen Baum, auch Git-Bash-Kinder; Blockende beendet den Rest (#122).
        with sitzungsjob.gebunden(prozess):
            try:
                code = prozess.wait()
            except KeyboardInterrupt:
                _baum_beenden(prozess, frist)
                code = prozess.wait()
    finally:
        stopp.set()
        faden.join(timeout=frist * 2 + takt * 3)
    daten = lies_umzug(datei)
    if daten is not None:
        daten["lokal_beendet"] = stand.get("lokal_beendet", True)
    return code, daten
