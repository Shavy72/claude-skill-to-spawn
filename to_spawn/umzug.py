"""Umzug einer Bau-Session vom lokalen PC auf den Bau-Server — ``/to-spawn-of`` (#212).

Drei Teile:

* :func:`umzug_einzel` — aus einer laufenden Bau-Session: Handoff prüfen, committen,
  pushen, Session auf dem Server mit dem Handoff als Startkontext neu starten und
  erst nach bewiesenem Server-Lauf das lokale Ende anstoßen (``BAU_UMZUG_DATEI``,
  ``bau.py`` beendet daraufhin die lokale Session — kein Doppel-Lauf).
* :func:`umzug_alle` — Wächter-Variante: alle laufenden Ticket-Sessions einer Spec
  streng nacheinander umziehen, nie zwei halb.
* :func:`hook_umzug_anfrage` — Stop-Hook in jeder Bau-Session: liegt eine Anfrage des
  Wächters, bekommt die Session die Anweisung, sich selbst umzuziehen.

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
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO

log = logging.getLogger("to_spawn.umzug")

EXIT_OK = 0
EXIT_FEHLER = 1
EXIT_WEIGERUNG = 3
#: ``spawn_srv.sh --umzug``: das Ticket läuft auf dem Server schon — Umzug abgebrochen.
EXIT_LAEUFT_SCHON = 4
#: ``ssh`` selbst scheiterte (Verbindung weg) — der Befehl kann trotzdem gelaufen sein.
SSH_EXIT_EIGEN = 255

#: Server-Beweis: Takt und Höchstdauer (Sekunden) des Pollens nach dem Start.
BEWEIS_TAKT = 5.0
BEWEIS_MAX = 90.0
#: Höchstdauer des Server-Starts per SSH (``spawn_srv.sh`` wartet selbst 10 s).
SSH_START_TIMEOUT = 300
SSH_KURZ_TIMEOUT = 30
#: Frist zwischen ``terminate()`` und ``kill()`` für das Claude-Kind.
KILL_FRIST = 20.0

HANDOFF_NAME = re.compile(r"^HANDOFF_\d{4}-\d{2}-\d{2}_(\d+)\.md$")
#: Pflichtzeile im Umzug-Handoff — Fettschrift/Groß-Klein egal (wie ``STAFFEL_MARKER``),
#: auch als Markdown-Überschrift (``## Umzug: server``).
UMZUG_MARKER = re.compile(
    r"^\s*(?:#+\s*)?\**\s*umzug\s*\**\s*:\s*\**\s*server", re.IGNORECASE | re.MULTILINE
)
#: Gleiches Muster wie ``scripts/hooks/staffel_stop.py`` — diese Zeile startet die lokale Staffel neu.
STAFFEL_MARKER = re.compile(
    r"^\s*\**\s*staffel\s*\**\s*:\s*\**\s*weiter", re.IGNORECASE | re.MULTILINE
)
GESCHUETZTE_BRANCHES = frozenset({"master", "main"})
SKILL = Path(__file__).resolve().parent.parent


def _ist_windows() -> bool:
    return sys.platform == "win32"


def _jetzt() -> datetime:
    """Ortszeit mit Zeitzone (Handoff-Datum und Zeitstempel wie auf dem PC)."""
    return datetime.now(timezone.utc).astimezone()


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
    )


def haupt_repo(ort: Path) -> Path:
    """Haupt-Repo eines Worktrees (bei einem normalen Klon: der Klon selbst)."""
    ergebnis = _git(ort, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if ergebnis.returncode != 0 or not ergebnis.stdout.strip():
        return ort
    return Path(ergebnis.stdout.strip()).parent


def ssh_ziel(konfig: dict[str, Any]) -> str:
    """SSH-Ziel: Umgebung ``TO_SPAWN_SSH_ZIEL`` gewinnt, sonst Konfig ``ssh_ziel``."""
    return os.environ.get("TO_SPAWN_SSH_ZIEL") or str(konfig.get("ssh_ziel") or "bau-server")


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


def _ssh(ziel: str, befehl: str, timeout: float) -> subprocess.CompletedProcess[str] | None:
    """Ein Befehl per SSH; ``None`` bei Zeitüberschreitung oder fehlendem ``ssh``."""
    try:
        return subprocess.run(
            ["ssh", "-o", "BatchMode=yes", ziel, befehl],
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
    """Server-Befehl: ``spawn_srv.sh`` im Server-Repo."""
    teile = ["bash", "scripts/spawn_srv.sh", spec]
    if nur_wache:
        teile += ["--nur-wache", "--ohne-regularien"]
    else:
        teile += ["--tickets", str(ticket), "--ohne-wache"]
        if umzug_ref:
            teile += ["--umzug", umzug_ref]
        else:
            teile.append("--ohne-regularien")
    return f"{_cd(ordner)} && " + " ".join(shlex.quote(t) for t in teile)


def server_starten(
    spec: str,
    ticket: str | None,
    *,
    ziel: str,
    ordner: str,
    umzug_ref: str | None = None,
    nur_wache: bool = False,
) -> bool:
    """Startet ``bau <N>`` (oder nur ``wache <S>``) per SSH auf dem Server.

    Bricht die SSH-Verbindung selbst ab (Zeitüberschreitung, Exit 255), kann der Start
    trotzdem durchgelaufen sein — dann einmal den Server-Beweis prüfen: läuft die
    Session, gilt der Start (laut geloggt), sonst bleibt es ein Fehler. Exit 4 von
    ``spawn_srv.sh --umzug`` = das Ticket läuft dort schon → Umzug abgebrochen.
    """
    befehl = spawn_befehl(spec, ticket, ordner, umzug_ref=umzug_ref, nur_wache=nur_wache)
    log.info("Server-Start: ssh %s %s", ziel, befehl)
    ergebnis = _ssh(ziel, befehl, SSH_START_TIMEOUT)
    if ergebnis is not None and ergebnis.returncode == 0:
        return True
    was = f"#{ticket}" if ticket else f"Wächter Spec #{spec}"
    if ergebnis is not None and ergebnis.returncode == EXIT_LAEUFT_SCHON:
        log.error(
            "%s läuft auf dem Server schon — Umzug abgebrochen, lokale Session läuft weiter "
            "(nie zwei Sessions für ein Ticket): %s",
            was,
            (ergebnis.stderr or ergebnis.stdout).strip()[-400:],
        )
        return False
    if ergebnis is None or ergebnis.returncode == SSH_EXIT_EIGEN:
        grund = (
            "keine Antwort"
            if ergebnis is None
            else (ergebnis.stderr or ergebnis.stdout).strip()[-200:]
        )
        log.warning(
            "SSH brach beim Server-Start ab (%s) — prüfe einmal, ob %s trotzdem läuft.",
            grund,
            was,
        )
        if server_laeuft(spec, ticket, ziel=ziel, ordner=ordner):
            log.warning("%s läuft trotzdem auf %s — Start gilt als gelungen.", was, ziel)
            return True
        log.error("Server-Start scheiterte: SSH abgebrochen und %s läuft nicht auf %s.", was, ziel)
        return False
    log.error(
        "Server-Start scheiterte (Exit %s): %s",
        ergebnis.returncode,
        (ergebnis.stderr or ergebnis.stdout).strip()[-400:],
    )
    return False


def server_fenster_schliessen(spec: str, ticket: str, *, ziel: str) -> bool:
    """tmux-Fenster ``bau <N>`` auf dem Server schließen (Doppelstart zurücknehmen)."""
    befehl = f"tmux kill-window -t {shlex.quote(f'=spec-{spec}:bau {ticket}')}"
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


def server_laeuft(spec: str, ticket: str | None, *, ziel: str, ordner: str) -> bool:
    """Einmal prüfen: tmux-Fenster da und (bei Tickets) Session nicht ``aus``."""
    fenster = "bau " + ticket if ticket else f"wache {spec}"
    liste = _ssh(ziel, f"tmux list-windows -t ={shlex.quote('spec-' + spec)} -F '#W'", SSH_KURZ_TIMEOUT)
    if liste is None or liste.returncode != 0:
        return False
    if fenster not in [z.strip() for z in liste.stdout.splitlines()]:
        return False
    if ticket is None:
        return True
    stand = _ssh(
        ziel,
        f"{_cd(ordner)} && python3 scripts/sessions_stand.py {shlex.quote(spec)}",
        SSH_KURZ_TIMEOUT,
    )
    if stand is None or stand.returncode != 0:
        return False
    zustand = _stand_zeile(stand.stdout, ticket)
    return zustand is not None and zustand != "aus"


def _warte_auf_server(spec: str, ticket: str | None, *, ziel: str, ordner: str) -> bool:
    ende = time.monotonic() + BEWEIS_MAX
    while True:
        if server_laeuft(spec, ticket, ziel=ziel, ordner=ordner):
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
    """Spec-Nummer aus dem ersten ``docs/agents/manifests/*.json``, das das Ticket führt."""
    for ort in orte:
        for datei in sorted((ort / "docs" / "agents" / "manifests").glob("*.json")):
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
        raise Abbruch(
            EXIT_WEIGERUNG, f"Handoff {handoff} liegt nicht im Worktree {worktree}."
        ) from fehler


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


def stand_sichern(
    worktree: Path, handoff: Path, ticket: str, *, dry_run: bool
) -> tuple[str, str, str]:
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
            "Ungesicherte Änderungen: " + ", ".join(fremde[:5])
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
    """Ergebnis an den Wächter: ``<BAU_UMZUG_ANFRAGE>.laeuft`` (nur wenn sie existiert).

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
        log.warning("Ergebnis für den Wächter nicht schreibbar (%s): %s", laeuft, fehler)


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
                f"Kein Manifest führt Ticket #{ticket} — Abhilfe: docs/agents/manifests/spec-<S>.json prüfen.",
            )
        branch, rel, sha = stand_sichern(worktree, handoff, ticket, dry_run=dry_run)
        # Commit-SHA in der Referenz: der Server liest genau diesen Stand des Handoffs,
        # auch wenn der Branch inzwischen weiterläuft.
        ref = f"{branch}@{sha}:{rel}"
        if dry_run:
            print(f"Probelauf: ssh -o BatchMode=yes {ziel} {shlex.quote(spawn_befehl(spec, ticket, ordner, umzug_ref=ref))}")
            print("Probelauf — nichts committet, gepusht oder gestartet.")
            return EXIT_OK
        if not server_starten(spec, ticket, ziel=ziel, ordner=ordner, umzug_ref=ref):
            raise Abbruch(EXIT_FEHLER, "Server-Start scheiterte — lokale Session läuft weiter.")
        if not _warte_auf_server(spec, ticket, ziel=ziel, ordner=ordner):
            raise Abbruch(
                EXIT_FEHLER,
                f"Kein Beweis, dass #{ticket} auf {ziel} läuft (tmux-Fenster/sessions) — "
                "lokale Session läuft weiter, auf dem Server nachsehen: sessions " + spec,
            )
    except Abbruch as abbruch:
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


# --- Wächter-Variante -------------------------------------------------------------


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
    modul.MANIFESTE = repo / "docs" / "agents" / "manifests"
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
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
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
            lambda: server_laeuft(spec, ticket, ziel=ziel, ordner=ordner),
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
            return frisch(ticket)[0] == "aus" and server_laeuft(
                spec, ticket, ziel=ziel, ordner=ordner
            )

        try:
            anfrage_schreiben(repo, ticket)
            bestaetigt = _warte(fertig, warte_max, takt)
        finally:
            anfrage_loeschen(repo, ticket)
        if ergebnis:
            return (
                f"Session meldet Fehlschlag (Exit {ergebnis.get('exit')}): "
                f"{ergebnis.get('grund') or '?'}"
            )
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
                weg = (
                    "neu auf dem Server, dann bau.py beenden"
                    if zustand == "wartet"
                    else "Anfrage an die Session"
                )
                print(f"#{ticket} Probelauf: {zustand} → {weg}")
                continue
            if zustand == "wartet":
                if pid is None:
                    return stopp(ticket, "wartet, aber keine Prozess-ID")
                # Erst der Server — scheitert er, bleibt lokal alles unangetastet.
                if not server_starten(spec, ticket, ziel=ziel, ordner=ordner):
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
                        server_fenster_schliessen(spec, ticket, ziel=ziel)
                        return stopp(
                            ticket,
                            f"bau.py (pid {ziel_pid}) lässt sich nicht beenden — "
                            "Server-Fenster wieder geschlossen",
                        )
                elif zustand != "aus":
                    # Lokal ist inzwischen Claude gestartet: Server-Start zurücknehmen,
                    # die Session zieht über den Anfrage-Weg um (mit Handoff).
                    log.warning(
                        "#%s startete lokal, während der Server hochfuhr (%s) — "
                        "Server-Fenster zu, Anfrage-Weg.",
                        ticket,
                        zustand,
                    )
                    if not server_fenster_schliessen(spec, ticket, ziel=ziel):
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
    if not server_starten(spec, None, ziel=ziel, ordner=ordner, nur_wache=True):
        print(f"Wächter Spec #{spec}: Server-Start scheiterte — Wächter bleibt lokal.")
        return EXIT_FEHLER
    if not _warte(lambda: server_laeuft(spec, None, ziel=ziel, ordner=ordner), BEWEIS_MAX, BEWEIS_TAKT):
        print(f"Wächter Spec #{spec}: kein tmux-Fenster auf {ziel} — Wächter bleibt lokal.")
        return EXIT_FEHLER
    print(f"Wächter Spec #{spec} umgezogen → {ziel}")
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
    """Stop-Hook: Anfrage des Wächters → Session bekommt die Umzug-Anweisung (einmal).

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
        "Der Wächter verlangt den Umzug dieser Session auf den Bau-Server. "
        f"1) Handoff {handoff} im Worktree schreiben (Stand, nächste Schritte, offene Punkte) "
        "mit der Zeile „Umzug: server“, ohne die Zeile „Staffel: weiter“. "
        "2) Eigene Arbeit mit Pathspec committen. "
        f"3) {shlex.quote(Path(sys.executable).as_posix())} "
        f"{shlex.quote((SKILL / 'to_spawn.py').as_posix())} umzug {shlex.quote(ticket)} "
        f"--handoff {shlex.quote(handoff)} ausführen. "
        "Sonst nichts tun."
    )
    (ausgabe or sys.stdout).write(
        json.dumps({"decision": "block", "reason": grund}, ensure_ascii=False) + "\n"
    )
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
                "taskkill /T meldete Exit %s (%s) — Unterprozesse der Session können noch "
                "laufen, Doppel-Lauf prüfen.",
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


def _umzug_waechter(
    datei: Path,
    prozess: subprocess.Popen[Any],
    stopp: threading.Event,
    stand: dict[str, bool],
    takt: float,
    frist: float,
) -> None:
    while not stopp.wait(takt):
        if not datei.exists():
            continue
        if prozess.poll() is None:
            log.info("Umzug-Datei %s gesehen — lokale Session wird beendet.", datei.name)
            stand["lokal_beendet"] = _baum_beenden(prozess, frist)
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


def starte_mit_umzug_wache(
    cmd: Sequence[str], datei: Path, *, takt: float = 1.0, frist: float = KILL_FRIST
) -> tuple[int, dict[str, Any] | None]:
    """Startet ``cmd`` interaktiv; taucht ``datei`` auf, wird das Kind beendet.

    Rückgabe ``(exit_code, umzug_daten)`` — ``umzug_daten`` ist ``None`` ohne Umzug,
    sonst mit ``lokal_beendet`` (``False`` = die lokale Session ist nicht nachweislich
    weg, Doppel-Lauf möglich). Auf Windows bleibt der ``shell=True``-Notnagel für
    ``.cmd``-Shims — nur, wenn das Programm ohne Shell nicht gefunden wird.
    """
    datei.unlink(missing_ok=True)
    try:
        prozess: subprocess.Popen[Any] = subprocess.Popen(list(cmd))
    except FileNotFoundError:
        prozess = subprocess.Popen(subprocess.list2cmdline(list(cmd)), shell=True)
    except OSError as fehler:
        log.error("Start von %s scheiterte: %s", cmd[0] if cmd else "?", fehler)
        raise
    stopp = threading.Event()
    stand: dict[str, bool] = {}
    faden = threading.Thread(
        target=_umzug_waechter,
        args=(datei, prozess, stopp, stand, takt, frist),
        daemon=True,
    )
    faden.start()
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
