"""Schmale GitHub-Anbindung über die ``gh``-CLI.

Im Test zeigt ``TO_SPAWN_GH_STUB`` auf ein Ersatz-Skript — GitHub ist ein
externer Dienst und die einzige zweite Attrappe im Weg-Test (dokumentiert im
README). Ohne die Variable läuft echtes ``gh``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from to_spawn import starter, ticket_zustand

log = logging.getLogger("to_spawn.gh")

#: Test-Tür: Pfad eines Ersatz-Skripts für ``gh``; wirkt nur unter Test (:func:`starter.unter_test`).
GH_STUB_ENV = "TO_SPAWN_GH_STUB"
_stub_gemeldet = False


def gh_befehl() -> list[str] | None:
    """Aufruf-Vorspann für ``gh`` (oder das Stub-Skript); ``None`` wenn nichts da.

    ``TO_SPAWN_GH_STUB`` gilt nur unter Test (#320) — derselbe Merker wie die Start-Tür
    (:func:`starter.unter_test`, setzt ``tests/conftest.py``, erbt sich in Kind-Prozesse).
    Im Betrieb wird die Variable ignoriert und einmal je Prozess gemeldet.
    """
    global _stub_gemeldet
    stub = os.environ.get(GH_STUB_ENV)
    if stub and starter.unter_test():
        return [sys.executable, stub] if stub.endswith(".py") else [stub]
    if stub and not _stub_gemeldet:
        _stub_gemeldet = True
        log.warning("%s ist gesetzt, gilt aber nur im Test — ignoriert, echtes gh wird benutzt", GH_STUB_ENV)
    pfad = shutil.which("gh")
    return [pfad] if pfad else None


def lauf(
    args: list[str],
    cwd: Path | None = None,
    timeout: float | None = None,
    *,
    fehlertext: bool = False,
) -> tuple[int, str]:
    """``gh <args>`` ausführen; gibt (Exit-Code, stdout) zurück.

    ``timeout`` in Sekunden (Standard: keins); überschritten → Exit 124, leeres stdout.
    ``fehlertext=True``: bei Exit ≠ 0 kommt stderr statt stdout zurück (Fehlermeldung für den Nutzer).
    """
    vorspann = gh_befehl()
    if vorspann is None:
        return 127, ""
    try:
        fertig = subprocess.run(
            [*vorspann, *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.warning("gh %s: Zeitlimit %s s überschritten", " ".join(args[:3]), timeout)
        return 124, ""
    except OSError as fehler:
        log.warning("gh-Aufruf fehlgeschlagen: %s", fehler)
        return 127, ""
    if fehlertext and fertig.returncode != 0:
        return fertig.returncode, (fertig.stderr or "").strip()
    return fertig.returncode, (fertig.stdout or "").strip()


def json_lauf(args: list[str], cwd: Path | None = None, timeout: float | None = None) -> Any:
    """Wie :func:`lauf`, aber stdout als JSON (``None`` bei Fehler)."""
    code, ausgabe = lauf(args, cwd=cwd, timeout=timeout)
    if code != 0 or not ausgabe:
        return None
    try:
        return json.loads(ausgabe)
    except ValueError:
        log.warning("gh-Ausgabe ist kein JSON: %s", ausgabe[:200])
        return None


def _origin_url(cwd: Path | None) -> str | None:
    """``git remote get-url origin`` mit ``/`` statt ``\\``; ``None`` = git nicht startbar."""
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        ).stdout.strip()
    except OSError:
        return None
    return url.replace("\\", "/")  # Windows-Pfad als Origin (C:\…\org\fern.git), #68


def _github_slug(url: str) -> str:
    treffer = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", url)
    return treffer.group(1) if treffer else ""


def github_slug_aus_origin(cwd: Path | None = None) -> str:
    """``owner/name`` nur, wenn ``origin`` auf github.com zeigt; sonst ``""`` (#112).

    Für alles, was den Slug dauerhaft festhält (Maschinen-Liste): ein lokaler Pfad als
    origin (Wegwerf-Klon) ergibt nie einen geratenen Slug.
    """
    return _github_slug(_origin_url(cwd) or "")


def repo_aus_origin(cwd: Path | None = None, fallback: str = "") -> str:
    """``owner/name`` aus ``git remote get-url origin`` (https oder ssh); bei einem Pfad
    als origin die letzten zwei Glieder (Test-Bare-Repos, #68)."""
    url = _origin_url(cwd)
    if url is None:
        return fallback
    if slug := _github_slug(url):
        return slug
    treffer = re.search(r"([^/\\]+/[^/\\]+?)(?:\.git)?$", url)
    return treffer.group(1) if treffer else fallback


def blocked_by(repo_slug: str, ticket: str, cwd: Path | None = None) -> list[dict] | None:
    """Native Blocker-Kanten eines Tickets (``None`` = Abfrage fehlgeschlagen)."""
    daten = json_lauf(["api", f"repos/{repo_slug}/issues/{ticket}/dependencies/blocked_by"], cwd=cwd)
    return daten if isinstance(daten, list) else None


def blocker_erledigt(dep: dict) -> bool:
    """Zählt dieser Blocker für Folge-Tickets als erledigt? Frage ``fertig_gebaut`` (#1527, #279).

    Kein Ticket wartet darauf, dass ein anderes live ist.
    """
    return ticket_zustand.fertig_gebaut(dep)


def offene_blocker(deps: list[dict]) -> list[int]:
    """Nummern der noch nicht erledigten Blocker (sortiert), Regel: :func:`blocker_erledigt`."""
    return sorted(int(d["number"]) for d in deps if not blocker_erledigt(d))


def blocker_gruende(repo: Path, deps: list[dict], *, mit_commit: bool = True) -> list[str]:
    """Warum ein Ticket noch nicht starten darf — leer = frei (#267: die eine Stelle).

    Je Blocker: nicht erledigt (:func:`blocker_erledigt`) → ``#N offen``. ``mit_commit``:
    jeder erledigte Blocker — zu oder ``staging:fertig``, keine Sonderregel — braucht
    zusätzlich seinen Commit ``(#N)`` auf ``origin/<Hauptzweig>``; sonst baut die Session
    auf einem Stand ohne die Blocker-Arbeit. git-Fehler zählen als „kein Commit“.
    Fragen: ``bau.py`` (Start-Schranke) und ``to_spawn.py blocker`` (Warte-Prüfung der Session).
    """
    gruende: list[str] = []
    erledigt: list[object] = []
    for d in deps:
        if blocker_erledigt(d):
            erledigt.append(d.get("number"))
        else:
            gruende.append(f"#{d.get('number')} offen")
    if not mit_commit or not erledigt:
        return gruende
    haupt = f"origin/{hauptzweig(repo)}"  # master/main/… je Repo (#257)
    code, _ = _git_ausgabe(["fetch", "-q", "origin"], repo, timeout=FETCH_ZEIT_S)
    if code == 124:  # Zeitablauf: Stand von origin unbekannt → fail-closed (#320)
        return [*gruende, *(f"#{nr} zu, aber git fetch nach {FETCH_ZEIT_S:g} s abgebrochen" for nr in erledigt)]
    for nr in erledigt:
        if not commit_betreffe(repo, str(nr), haupt):  # None (git-Fehler) zählt als „kein Commit“
            gruende.append(f"#{nr} zu, aber kein Commit „(#{nr})“ auf {haupt}")
    return gruende


#: Zeitgrenze (s) der Ticket-Stand-Abfrage (#191): hängt gh, wird daraus „unbekannt“.
TICKET_STAND_TIMEOUT_S = 30.0
#: Überschreibt :data:`TICKET_STAND_TIMEOUT_S` (Tests kürzen damit).
TICKET_STAND_TIMEOUT_ENV = "TO_SPAWN_GH_TIMEOUT"


def ticket_stand_timeout() -> float:
    """Zeitgrenze für :func:`ticket_zustand.lesen`; ungültiger Env-Wert → Standard."""
    try:
        wert = float(os.environ.get(TICKET_STAND_TIMEOUT_ENV) or TICKET_STAND_TIMEOUT_S)
    except ValueError:
        return TICKET_STAND_TIMEOUT_S
    return wert if wert > 0 else TICKET_STAND_TIMEOUT_S


def ticket_daten(ticket: str, cwd: Path | None = None) -> dict[str, Any] | None:
    """Zustand, Labels, Text und Zuständige eines Issues (``None`` bei Fehler)."""
    daten = json_lauf(["issue", "view", str(ticket), "--json", "state,labels,body,assignees"], cwd=cwd)
    if not isinstance(daten, dict) or "state" not in daten:
        return None
    return daten


#: Zeitgrenzen (s) aller git-Aufrufe des Skills — die eine Stelle (#320), ``capo`` liest sie hier.
#: Lokale Abfragen (log, rev-parse …):
GIT_ZEIT_S = 30.0
#: Abfragen übers Netz (fetch, ls-remote):
FETCH_ZEIT_S = 60.0


def _git_ausgabe(args: list[str], cwd: Path, timeout: float | None = None) -> tuple[int, str]:
    """``git <args>`` im Ordner ``cwd``; (Exit-Code, stdout) — 127 wenn git fehlt, 124 bei Zeitablauf."""
    try:
        fertig = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.warning("git %s: Zeitgrenze %s s überschritten", args[:1], timeout)
        return 124, ""
    except OSError as fehler:
        log.warning("git-Aufruf fehlgeschlagen: %s", fehler)
        return 127, ""
    return fertig.returncode, (fertig.stdout or "").strip()


def commit_betreffe(repo: Path, nummer: int | str, *refs: str) -> list[str] | None:
    """Betreffe der Commits auf ``refs`` (z. B. ``origin/main`` oder ``--all``), deren Text ``(#<nummer>)`` enthält.

    Die eine Commit-Suche nach Ticketnummer (#267, #282): Blocker-Prüfung und Wiederanlauf
    fragen beide hier. Grobfilter — ob der Betreff wirklich das Ticket meint, entscheidet
    der Aufrufer (z. B. :func:`capo.betreff_hat_nummer`). ``None`` = git gescheitert; was das
    heißt („kein Commit“ oder „unbekannt“), entscheidet ebenfalls der Aufrufer.
    """
    return _commit_suche(repo, nummer, "%s", *refs)


def letzter_commit(repo: Path, nummer: int | str, ref: str) -> str | None:
    """Kurz-Hash des jüngsten Commits auf ``ref`` mit ``(#<nummer>)``; ``None`` = keiner oder git gescheitert.

    Für Anzeigen (``skripte/spec_stand.py``) — gleiche Suche wie :func:`commit_betreffe`.
    """
    zeilen = _commit_suche(repo, nummer, "%h", "-1", ref)
    return zeilen[0] if zeilen else None


def _commit_suche(repo: Path, nummer: int | str, format_: str, *refs: str) -> list[str] | None:
    """Die eine ``git log --grep=(#<nummer>)``-Suche; je Treffer eine Zeile im ``format_``."""
    code, text = _git_ausgabe(
        ["log", *refs, f"--format={format_}", "--fixed-strings", f"--grep=(#{nummer})"], repo, timeout=GIT_ZEIT_S
    )
    if code != 0:
        log.warning("Commit-Suche (#%s) gescheitert (git Exit %s)", nummer, code)
        return None
    return text.splitlines()


def origin_zweige(repo: Path, muster: str) -> list[str] | None:
    """Zweige auf origin, die das git-Muster ``muster`` treffen (z. B. ``ticket/7-*``), live per ``ls-remote``.

    Namen ohne ``refs/heads/``. ``None`` = kein origin, origin unerreichbar oder Zeitablauf —
    der Aufrufer darf das nicht als „kein Zweig“ lesen.
    """
    code, text = _git_ausgabe(["ls-remote", "--heads", "origin", muster], repo, timeout=FETCH_ZEIT_S)
    if code != 0:
        log.warning("git ls-remote origin %s gescheitert (Exit %s)", muster, code)
        return None
    praefix = "refs/heads/"
    refs = (zeile.split("\t")[-1].strip() for zeile in text.splitlines())
    return [ref[len(praefix) :] for ref in refs if ref.startswith(praefix)]


def hauptzweig(repo: Path | None = None) -> str:
    """Name des Hauptzweigs (``master``/``main``/…) — nie hart verdrahtet (#257).

    Reihenfolge: 1. ``hauptzweig`` in ``.to-spawn/config.json`` · 2. ``origin/HEAD``
    · 3. erster vorhandener von ``origin/main``, ``origin/master`` (``main`` zuerst —
    Fremd-Repos heißen heute meist so; DuoPlus hat kein ``origin/main``) · 4. ``master``.
    """
    from . import config

    wurzel = config.repo_wurzel(repo)
    eigen = str(config.lade(wurzel).get("hauptzweig") or "").strip()
    if eigen:
        return eigen
    code, kopf = _git_ausgabe(["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], wurzel)
    if code == 0 and kopf.startswith("origin/") and len(kopf) > len("origin/"):
        return kopf[len("origin/") :]
    for name in ("main", "master"):
        if _git_ausgabe(["rev-parse", "--verify", "-q", f"origin/{name}"], wurzel)[0] == 0:
            return name
    return "master"


def label_sicherstellen(
    repo_pfad: Path,
    label: str,
    farbe: str = "d93f0b",
    beschreibung: str = "Ticket wartet auf menschliche Abnahme (to-spawn)",
    timeout: float | None = None,
) -> bool:
    """Sorgt dafür, dass das Label im GitHub-Repo existiert — erst prüfen, dann anlegen (#257).

    Reihenfolge: ``gh api repos/{owner}/{repo}/labels/<label>`` (``gh`` löst die
    Platzhalter aus dem origin des ``repo_pfad``) → Exit 0 = vorhanden, nichts wird
    geschrieben (Farbe/Beschreibung bleiben, wie sie sind). Nur wenn das Label fehlt,
    ``gh label create`` ohne ``--force``.

    ``timeout`` (Sekunden) gilt je ``gh``-Aufruf; überschritten → ``False``.
    ``False`` mit Warnung, wenn ``gh`` fehlt oder das Anlegen scheitert (kein origin,
    nicht eingeloggt) — der Aufrufer läuft weiter, das Label holt man von Hand nach.
    """
    pfad = "repos/{owner}/{repo}/labels/" + urllib.parse.quote(label, safe="")
    code, _ausgabe = lauf(["api", pfad], cwd=repo_pfad, timeout=timeout)
    if code == 124:
        return False
    if code == 127:
        log.warning("gh fehlt — Label %s nicht angelegt (gh label create %s).", label, label)
        return False
    if code == 0:
        return True
    code, _ausgabe = lauf(
        ["label", "create", label, "--color", farbe, "--description", beschreibung],
        cwd=repo_pfad,
        timeout=timeout,
    )
    if code == 127:
        log.warning("gh fehlt — Label %s nicht angelegt (gh label create %s).", label, label)
        return False
    if code != 0:
        log.warning("Label %s nicht angelegt (gh Exit %s) — von Hand: gh label create %s", label, code, label)
        return False
    return True
