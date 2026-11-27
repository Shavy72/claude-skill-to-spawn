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
import threading
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from to_spawn import prozessbaum, starter, ticket_zustand

log = logging.getLogger("to_spawn.gh")
_COMMIT_SUCHE_GEMELDET: set[tuple[str, str]] = set()  # #618: schon gewarnte (Repo, Ticket)

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
    """``gh <args>`` ausführen; gibt (Exit-Code, stdout) zurück — die eine Stelle, die ``gh`` startet.

    ``timeout`` in Sekunden; ohne Angabe gilt :func:`standard_zeit` (#601: hängt GitHub, hängt
    kein Aufrufer mit). Überschritten → Kind wird beendet, Exit 124, leeres stdout — derselbe
    Fehlerweg wie ein ``gh``-Fehler, Aufrufer brauchen keinen eigenen Code.
    ``fehlertext=True``: bei Exit ≠ 0 kommt stderr statt stdout zurück (Fehlermeldung für den Nutzer).
    """
    vorspann = gh_befehl()
    if vorspann is None:
        return 127, ""
    if timeout is None:
        timeout = standard_zeit()
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
            **prozessbaum.fensterlos(),
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


#: Standard-Zeitgrenze (s) je ``gh``-Aufruf ohne eigene Grenze (#601). 120 s = größte schon
#: ausdrücklich gesetzte Grenze im Skill (``einreichen.GH_MAX_S``); deckt ``--paginate``-Abfragen.
GH_ZEIT_S = 120.0
#: Überschreibt :data:`GH_ZEIT_S` (Tests kürzen damit) — dieselbe Variable wie die Ticket-Stand-Grenze.
GH_ZEIT_ENV = "TO_SPAWN_GH_TIMEOUT"


def standard_zeit() -> float:
    """Zeitgrenze für :func:`lauf` ohne eigene Angabe; ungültiger Env-Wert → :data:`GH_ZEIT_S`."""
    try:
        wert = float(os.environ.get(GH_ZEIT_ENV) or GH_ZEIT_S)
    except ValueError:
        return GH_ZEIT_S
    return wert if wert > 0 else GH_ZEIT_S


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
            **prozessbaum.fensterlos(),
        ).stdout.strip()
    except OSError:
        return None
    return url.replace("\\", "/")  # Windows-Pfad als Origin (C:\…\org\fern.git), #68


#: #235 H1 / #129: nur diese drei Anfänge sind GitHub; ein fremder Host (``notgithub.com``)
#: oder ein lokaler Pfad mit ``github.com/…`` im Namen ist kein GitHub-Repo (nur mit ``.match``).
_GITHUB_SLUG = re.compile(
    r"(?:https://github\.com/|ssh://git@github\.com/|git@github\.com:)([^/\s]+/[^/\s]+?)(?:\.git)?/?$"
)


def github_slug(url: str) -> str:
    """``owner/name`` einer GitHub-URL; alles andere (fremder Host, Pfad, ``http://``) ⇒ ``""``.

    Einzige Stelle für die GitHub-Slug-Regel (#129, #235 H1) — ``gh`` und ``hausmeister``
    teilen dieselbe Prüfung.
    """
    treffer = _GITHUB_SLUG.match(url.strip())
    return treffer.group(1) if treffer else ""


_github_slug = github_slug


def github_slug_aus_origin(cwd: Path | None = None) -> str:
    """``owner/name`` nur, wenn ``origin`` auf github.com zeigt; sonst ``""`` (#112).

    Für alles, was den Slug dauerhaft festhält (Maschinen-Liste): ein lokaler Pfad als
    origin (Wegwerf-Klon) ergibt nie einen geratenen Slug.
    """
    return github_slug(_origin_url(cwd) or "")


def repo_aus_origin(cwd: Path | None = None, fallback: str = "") -> str:
    """``owner/name`` aus ``git remote get-url origin`` (https oder ssh); bei einem Pfad
    als origin die letzten zwei Glieder (Test-Bare-Repos, #68)."""
    url = _origin_url(cwd)
    if url is None:
        return fallback
    if slug := github_slug(url):
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
    erledigt: list[tuple[object, str]] = []  # (Nummer, Zustandswort für die Meldung, #269)
    for d in deps:
        if blocker_erledigt(d):
            wort = ticket_zustand.ANZEIGE[ticket_zustand.stufe(d)]  # „zu“ / „fertig gebaut“
            erledigt.append((d.get("number"), wort))
        else:
            gruende.append(f"#{d.get('number')} offen")
    if not mit_commit or not erledigt or gruende:  # fetch erst, wenn alle Blocker zu sind (#432)
        return gruende
    haupt = f"origin/{hauptzweig(repo)}"  # master/main/… je Repo (#257)
    code, _ = _git_ausgabe(["fetch", "-q", "origin"], repo, timeout=FETCH_ZEIT_S)
    if code == 124:  # Zeitablauf: Stand von origin unbekannt → fail-closed (#320)
        return [*gruende, *(f"#{nr} {w}, aber git fetch nach {FETCH_ZEIT_S:g} s abgebrochen" for nr, w in erledigt)]
    for nr, wort in erledigt:
        if not commit_betreffe(repo, str(nr), haupt):  # None (git-Fehler) zählt als „kein Commit“
            gruende.append(f"#{nr} {wort}, aber kein Commit „(#{nr})“ auf {haupt}")
    return gruende


#: Zeitgrenze (s) der Ticket-Stand-Abfrage (#191): hängt gh, wird daraus „unbekannt“.
TICKET_STAND_TIMEOUT_S = 30.0
#: Überschreibt :data:`TICKET_STAND_TIMEOUT_S` (Tests kürzen damit).
TICKET_STAND_TIMEOUT_ENV = GH_ZEIT_ENV


def ticket_stand_timeout() -> float:
    """Zeitgrenze für :func:`ticket_zustand.lesen`; ungültiger Env-Wert → Standard."""
    try:
        wert = float(os.environ.get(TICKET_STAND_TIMEOUT_ENV) or TICKET_STAND_TIMEOUT_S)
    except ValueError:
        return TICKET_STAND_TIMEOUT_S
    return wert if wert > 0 else TICKET_STAND_TIMEOUT_S


def ticket_existiert(ticket: str, cwd: Path | None = None) -> bool | None:
    """``True``/``False``; ``None``, wenn GitHub nicht sicher antwortet (Netz, Auth, gh fehlt).

    ``False`` nur bei der eindeutigen gh-Meldung „Could not resolve to an issue“ — ein
    Netzfehler darf nie als „gibt es nicht“ gelten (#1444).
    """
    code, ausgabe = lauf(["issue", "view", str(ticket), "--json", "state"], cwd=cwd, timeout=60, fehlertext=True)
    if code == 0:
        return True
    return False if "Could not resolve to an issue" in ausgabe else None


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
#: Gültige Git-Objekt-ID (SHA-1 oder SHA-256).
_GIT_SHA = re.compile(r"^[0-9a-fA-F]{40,64}$")
#: Fehlermeldungen eines abgelehnten atomaren ``push --force-with-lease``-Versuchs (#191).
_CAS_KONFLIKT = re.compile(
    r"stale info|fetch first|rejected|cannot lock ref|reference already exists|non-fast-forward",
    re.IGNORECASE,
)
#: Neutrale Identität für interne Git-Objekte ohne Nutzer-Konfig (z. B. Remote-Lease-Commits, #191).
_INTERN_GIT_ENV = {
    "GIT_AUTHOR_NAME": "to-spawn",
    "GIT_AUTHOR_EMAIL": "to-spawn@localhost",
    "GIT_COMMITTER_NAME": "to-spawn",
    "GIT_COMMITTER_EMAIL": "to-spawn@localhost",
}


def _git_lauf(
    args: list[str],
    cwd: Path,
    *,
    timeout: float | None = None,
    eingabe: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """``git <args>`` im Ordner ``cwd`` → ``(Exit, stdout, stderr)``; 127 = git fehlt, 124 = Zeit."""
    if timeout is None:
        timeout = GIT_ZEIT_S
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if extra_env:
        env.update(extra_env)
    try:
        fertig = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=env,
            input=eingabe,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            **prozessbaum.fensterlos(),
        )
    except subprocess.TimeoutExpired:
        log.warning("git %s: Zeitgrenze %s s überschritten", args[:1], timeout)
        return 124, "", f"Zeitgrenze {timeout:g} s überschritten"
    except OSError as fehler:
        log.warning("git-Aufruf fehlgeschlagen: %s", fehler)
        return 127, "", str(fehler)
    return fertig.returncode, fertig.stdout or "", fertig.stderr or ""


def _git_fehler_kurz(code: int, stdout: str, stderr: str) -> str:
    text = (stderr or stdout).strip()
    zeilen = [z.strip() for z in text.splitlines() if z.strip()]
    return (zeilen[-1] if zeilen else f"git Exit {code}")[:200]


def _git_ausgabe(args: list[str], cwd: Path, timeout: float | None = None) -> tuple[int, str]:
    """``git <args>`` im Ordner ``cwd``; (Exit-Code, stdout) — 127 wenn git fehlt, 124 bei Zeitablauf."""
    code, stdout, _stderr = _git_lauf(args, cwd, timeout=timeout)
    return code, stdout.strip()


def hat_origin(cwd: Path | None) -> bool:
    """Hat ``cwd`` einen konfigurierten Git-Remote ``origin``?"""
    return bool(cwd is not None and _origin_url(cwd))


def remote_ref_sha(repo: Path, ref: str, *, timeout: float | None = None) -> tuple[bool, str, str]:
    """Liest den aktuellen Commit-SHA von ``ref`` auf ``origin`` per ``ls-remote`` (#191).

    Rückgabe ``(ok, sha, fehler)``:
    - ``(True, "", "")``: Remote erreichbar, Ref existiert nicht.
    - ``(True, "<sha>", "")``: Remote erreichbar, Ref steht auf ``<sha>``.
    - ``(False, "", "<grund>")``: Remote nicht erreichbar, Zeitgrenze oder unlesbare Antwort.
    """
    code, stdout, stderr = _git_lauf(
        ["ls-remote", "origin", ref],
        repo,
        timeout=FETCH_ZEIT_S if timeout is None else timeout,
    )
    if code != 0:
        return False, "", _git_fehler_kurz(code, stdout, stderr)
    gefunden = ""
    for zeile in stdout.splitlines():
        if not zeile.strip():
            continue
        sha, trenner, name = zeile.partition("\t")
        sha = sha.strip()
        name = name.strip()
        if not trenner or not _GIT_SHA.fullmatch(sha):
            return False, "", f"unlesbare ls-remote-Zeile: {zeile[:120]!r}"
        if name == ref:
            gefunden = sha
    return True, gefunden, ""


def remote_ref_nachricht(repo: Path, ref: str, *, timeout: float | None = None) -> tuple[bool, str, str, str]:
    """Holt ``ref`` von ``origin`` in eine flüchtige Ref und liest ``(ok, sha, nachricht, fehler)`` (#191).

    Arbeitsbaum, Index und ``refs/heads/*`` bleiben unberührt; die flüchtige Ref wird immer entfernt.
    """
    temp_ref = f"refs/to-spawn/tmp/{os.getpid()}-{threading.get_ident()}-{uuid.uuid4().hex[:8]}"
    code, stdout, stderr = _git_lauf(
        ["fetch", "-q", "--no-tags", "origin", f"+{ref}:{temp_ref}"],
        repo,
        timeout=FETCH_ZEIT_S if timeout is None else timeout,
    )
    if code != 0:
        return False, "", "", _git_fehler_kurz(code, stdout, stderr)
    try:
        c_sha, s_sha, e_sha = _git_lauf(["rev-parse", "--verify", f"{temp_ref}^{{commit}}"], repo, timeout=GIT_ZEIT_S)
        sha = s_sha.strip()
        if c_sha != 0 or not _GIT_SHA.fullmatch(sha):
            return False, "", "", _git_fehler_kurz(c_sha, s_sha, e_sha)
        c_msg, s_msg, e_msg = _git_lauf(["log", "-1", "--format=%B", temp_ref], repo, timeout=GIT_ZEIT_S)
        if c_msg != 0:
            return False, "", "", _git_fehler_kurz(c_msg, s_msg, e_msg)
        return True, sha, s_msg.strip(), ""
    finally:
        _git_lauf(["update-ref", "-d", temp_ref], repo, timeout=GIT_ZEIT_S)


def commit_mit_nachricht(repo: Path, nachricht: str, *, timeout: float | None = None) -> tuple[bool, str, str]:
    """Erzeugt einen leeren verwaisten Commit mit ``nachricht`` im Objekt-Speicher von ``repo`` (#191).

    Kein Arbeitsbaum-, Index- oder Zweig-Eingriff; läuft ohne ``user.name``/``user.email`` und
    plattformgleich unter Linux und Windows (UTF-8 über stdin).
    """
    frist = GIT_ZEIT_S if timeout is None else timeout
    c_baum, s_baum, e_baum = _git_lauf(["mktree"], repo, timeout=frist, eingabe="")
    baum_sha = s_baum.strip()
    if c_baum != 0 or not _GIT_SHA.fullmatch(baum_sha):
        return False, "", _git_fehler_kurz(c_baum, s_baum, e_baum)
    c_com, s_com, e_com = _git_lauf(
        ["-c", "commit.gpgsign=false", "commit-tree", baum_sha, "-F", "-"],
        repo,
        timeout=frist,
        eingabe=nachricht,
        extra_env=_INTERN_GIT_ENV,
    )
    commit_sha = s_com.strip()
    if c_com != 0 or not _GIT_SHA.fullmatch(commit_sha):
        return False, "", _git_fehler_kurz(c_com, s_com, e_com)
    return True, commit_sha, ""


def remote_ref_cas_setzen(
    repo: Path,
    ref: str,
    neu_sha: str,
    erwartet_sha: str = "",
    *,
    timeout: float | None = None,
) -> tuple[bool, bool, str]:
    """Setzt ``ref`` auf ``origin`` atomar auf ``neu_sha``, wenn es auf ``erwartet_sha`` steht (#191).

    Leeres ``erwartet_sha`` verlangt, dass ``ref`` auf ``origin`` noch nicht existiert.
    Rückgabe ``(ok, konflikt, fehler)``.
    """
    code, stdout, stderr = _git_lauf(
        [
            "-c",
            "push.useForceIfIncludes=false",
            "push",
            "-q",
            "--no-verify",
            f"--force-with-lease={ref}:{erwartet_sha}",
            "origin",
            f"{neu_sha}:{ref}",
        ],
        repo,
        timeout=FETCH_ZEIT_S if timeout is None else timeout,
    )
    if code == 0:
        return True, False, ""
    konflikt = bool(_CAS_KONFLIKT.search(f"{stderr}\n{stdout}"))
    return False, konflikt, _git_fehler_kurz(code, stdout, stderr)


def remote_ref_cas_loeschen(
    repo: Path,
    ref: str,
    erwartet_sha: str,
    *,
    timeout: float | None = None,
) -> tuple[bool, bool, str]:
    """Löscht ``ref`` auf ``origin`` atomar nur, wenn es noch auf ``erwartet_sha`` steht (#191).

    Rückgabe ``(ok, konflikt, fehler)``.
    """
    code, stdout, stderr = _git_lauf(
        [
            "-c",
            "push.useForceIfIncludes=false",
            "push",
            "-q",
            "--no-verify",
            f"--force-with-lease={ref}:{erwartet_sha}",
            "origin",
            f":{ref}",
        ],
        repo,
        timeout=FETCH_ZEIT_S if timeout is None else timeout,
    )
    if code == 0:
        return True, False, ""
    konflikt = bool(_CAS_KONFLIKT.search(f"{stderr}\n{stdout}"))
    return False, konflikt, _git_fehler_kurz(code, stdout, stderr)


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
    schluessel = (str(repo), str(nummer))
    if code != 0:
        # #618: je Repo + Ticket nur einmal warnen (Wecker fragt je 60-s-Takt), erst nach einem Erfolg wieder
        melden = log.debug if schluessel in _COMMIT_SUCHE_GEMELDET else log.warning
        _COMMIT_SUCHE_GEMELDET.add(schluessel)
        melden("Commit-Suche (#%s) gescheitert (git Exit %s)", nummer, code)
        return None
    _COMMIT_SUCHE_GEMELDET.discard(schluessel)
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


def lokale_zweige(repo: Path, muster: str) -> list[str] | None:
    """Lokale Zweige in ``repo``, die das git-Muster ``muster`` treffen (z. B. ``ticket/7-*``).

    Namen ohne ``refs/heads/``. ``None`` = git gescheitert.
    """
    code, text = _git_ausgabe(
        ["for-each-ref", "--format=%(refname:short)", f"refs/heads/{muster}"], repo, timeout=GIT_ZEIT_S
    )
    if code != 0:
        log.warning("git for-each-ref %s gescheitert (Exit %s)", muster, code)
        return None
    return [zeile.strip() for zeile in text.splitlines() if zeile.strip()]


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
    code, kopf = _git_ausgabe(["symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"], wurzel, timeout=GIT_ZEIT_S)
    if code == 0 and kopf.startswith("origin/") and len(kopf) > len("origin/"):
        return kopf[len("origin/") :]
    for name in ("main", "master"):
        if _git_ausgabe(["rev-parse", "--verify", "-q", f"origin/{name}"], wurzel, timeout=GIT_ZEIT_S)[0] == 0:
            return name
    return "master"


def label_sicherstellen(
    repo_pfad: Path | None,
    label: str,
    farbe: str = "d93f0b",
    beschreibung: str = "Ticket wartet auf menschliche Abnahme (to-spawn)",
    timeout: float | None = None,
    *,
    repo_slug: str = "",
) -> bool:
    """Sorgt dafür, dass das Label im GitHub-Repo existiert — erst prüfen, dann anlegen (#257).

    Reihenfolge: ``gh api repos/<owner>/<repo>/labels/<label>`` (mit ``repo_slug`` genau dieses
    Repo, sonst löst ``gh`` ``{owner}/{repo}`` aus dem origin des ``repo_pfad``) → Exit 0 =
    vorhanden, nichts wird geschrieben (Farbe/Beschreibung bleiben, wie sie sind). Nur wenn das
    Label fehlt, ``gh label create`` ohne ``--force``.

    ``timeout`` (Sekunden) gilt je ``gh``-Aufruf; überschritten → ``False``.
    ``False`` mit Warnung, wenn ``gh`` fehlt oder das Anlegen scheitert (kein origin,
    nicht eingeloggt) — der Aufrufer läuft weiter, das Label holt man von Hand nach.
    """
    slug = repo_slug.strip()
    ziel_teil = slug if slug else "{owner}/{repo}"
    pfad = f"repos/{ziel_teil}/labels/" + urllib.parse.quote(label, safe="")
    code, _ausgabe = lauf(["api", pfad], cwd=repo_pfad, timeout=timeout)
    if code == 124:
        return False
    if code == 127:
        log.warning("gh fehlt — Label %s nicht angelegt (gh label create %s).", label, label)
        return False
    if code == 0:
        return True
    erstellen = ["label", "create", label]
    if slug:
        erstellen += ["--repo", slug]
    erstellen += ["--color", farbe, "--description", beschreibung]
    code, _ausgabe = lauf(erstellen, cwd=repo_pfad, timeout=timeout)
    if code == 127:
        log.warning("gh fehlt — Label %s nicht angelegt (gh label create %s).", label, label)
        return False
    if code != 0:
        log.warning("Label %s nicht angelegt (gh Exit %s) — von Hand: gh label create %s", label, code, label)
        return False
    return True
