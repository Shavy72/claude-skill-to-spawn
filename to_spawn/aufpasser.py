"""Aufpasser (Hausmeister) für Bau-Sessions im tmux — Ebene 3 über Wächter und Bau-Sessions.

Läuft per Cron alle 15 Minuten (0 Token, reines Skript) und sieht je tmux-Sitzung
``spec-<S>`` nach den Fenstern ``bau <N>`` und ``wache <S>``:

0. **Deploy-Wache:** läuft ein Deploy oder Gate (``--deploy-muster`` plus die Skripte
   aus ``.to-spawn/config.json`` gegen ``pgrep -af``), wird in diesem Lauf nichts
   angefasst — nur die Hashes werden fortgeschrieben. Prozesse älter als 6 h zählen
   nicht (verwaiste Läufe), sie werden als „Waise ignoriert“ geloggt.
1. **Fehlendes Fenster:** offenes Unter-Ticket ohne ``bau <N>`` (oder fehlendes
   ``wache <S>`` bei offenen Tickets) → Fenster anlegen. Liegt
   ``<repo>/.to-spawn/sessions/<N>.json`` (``wache-<S>.json``) und das Transkript
   dazu, geht es mit ``--resume <id>`` weiter statt frisch (R2). Ein Start gilt
   erst, wenn Fenster und Prozess 5 s lang bleiben (R6). Nie bei Label
   ``ready-for-human``/``needs-info``/``wontfix``; verschwindet das Fenster nach
   dem Start gleich wieder, gibt es höchstens 2 Starts in 6 h, dann einmal
   „braucht David“.
2. **Ticket zu:** ``bau <N>`` mit geschlossenem Ticket, Pane-Text seit ≥ 15 min
   unverändert und keine laufende Arbeit → Worktree sichern (falls schmutzig),
   Fenster schließen.
3. **Stilles Fenster** (Pane-Text ≥ ``hang_min`` Minuten unverändert und Session
   nicht ``busy``), Sicherheitskette mit Zähler ``stufe`` je Fenster:
   - Stufe 0 → anstupsen (Text ins Fenster tippen).
   - Stufe 1 → Gesprächs-ID aus ``~/.claude/sessions/<pid>.json`` lesen, Worktree
     auf ``sicherung/<N>`` sichern und pushen, Fenster per tmux-Respawn mit
     ``--resume <id>`` neu starten (Fenster, Index und Sitzung bleiben — die Sitzung
     stirbt nie, auch als letztes Fenster). Danach bis 120 s nachweisen, dass die
     Session mit derselben ID wieder läuft. Scheitert Sicherung oder Nachweis,
     bleibt alles stehen und David wird gerufen.
   - Stufe 2 → nichts mehr ändern, einmal „braucht David“ melden (Stufe 3).
   Die Stufe steigt nur bei einem Eingriff. Ändert sich der Pane-Text danach, hat
   der Eingriff gewirkt: Stufe 0, Uhr neu. Kill/Resume gibt es also nur, wenn nach
   dem Anstupsen ``hang_min`` lang GAR NICHTS passiert ist; Stufe 3 bleibt, bis der
   Text sich ändert — kein Zeit-Reset. Direkt vor jedem Eingriff wird der Pane-Text
   neu gelesen: anders als zu Laufbeginn, arbeitend oder Deploy/Gate inzwischen
   gestartet ⇒ Kette abbrechen (R4). Mehr als 3 Anstupser je Fenster in 24 h ⇒
   Stufe 3, einmal „braucht David“ (R5). Das Wächter-Fenster geht denselben Weg,
   nur ohne Sicherung (kein Worktree): ``wache.py --resume <id>`` (R1).
   Lebt im Pane nur noch die Shell (Session beendet), wird nie angestupst — der
   Text liefe als Befehl —, sondern nach ``hang_min`` Stille gleich bei Stufe 1
   begonnen, mit der ID aus ``.to-spawn/sessions/`` (R2).

**Gesprächs-ID und Arbeitszustand** kommen aus ``~/.claude/sessions/<pid>.json``
(Claude Code schreibt sie je laufender Session: ``sessionId``, ``cwd``, ``status``
``busy``/``idle``, ``tmux`` = ``sitzung:@fenster.%pane``). Gültig nur, wenn der
Prozess lebt und das ``tmux``-Feld zu diesem Fenster passt. Ein Transkript-Fallback
über die neueste ``.jsonl`` gibt es nicht mehr: alle Bau-Fenster eines Repos teilen
sich das cwd, die neueste Datei gehört nachweislich oft einer fremden Session.

**Wartende Loop-Sessions** (ScheduleWakeup) zeigen nur ``❯`` + „done HH:MM“ — kein
Marker. Sicher sind sie durch ``hang_min``: jeder Wakeup ≤ 60 min ändert den
Pane-Text vorher, deshalb ist ``hang_min`` nie kleiner als 61 (``HANG_MIN_UNTERGRENZE``).

Es wird nie gestasht, nie der Branch gewechselt, nie der Index angefasst — die
Sicherung ist ein Commit aus einem temporären Index, ohne ``.env``/Zugangsdateien,
mit der vorigen Sicherung als zweitem Elternteil (Kette bleibt, Push ohne Zwang).
Das Hauptrepo wird nie gesichert (geteilter Baum mehrerer Sessions, fremde Arbeit).
Genau eine Stelle schließt ein Fenster (``_fenster_schliessen``), genau eine setzt
es fort (``_fenster_fortsetzen``); beide brauchen eine ``Freigabe``, die nur nach
„Ticket zu“ oder „gesichert + ID bekannt“ entsteht. Ein Lauf hält ``<zustand>/lock``
(flock); läuft schon einer, endet der zweite sofort.

Nur Linux mit tmux und ``/proc`` (Bau-Server). Windows wird nicht unterstützt.
Jeder Eingriff: Logzeile + eigener Kommentar im Spec-Issue; nur Meldungen ohne
Eingriff kommen je Fenster/Ereignis einmal am Tag (R3). Trockenlauf: ``--trocken``
(legt keinen Zustandsordner an, Log auf stderr).
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from to_spawn import bau_log, config, sessions_datei, speicher, vorfall
from to_spawn.waechter_lauf import _LIMIT_TEXT as LIMIT_TEXT
from to_spawn.waechter_lauf import transkript_ordner

log = logging.getLogger("aufpasser")

HANG_MIN = 90
#: Wakeups wartender Loop-Sessions kommen spätestens alle 60 min — darunter wäre
#: eine wartende Session „still“.
HANG_MIN_UNTERGRENZE = 61
#: Offene Ja/Nein-Rückfrage: nach so vielen Minuten einmal melden.
RUECKFRAGE_MIN = 15
#: „Ticket zu“: Fenster erst schließen, wenn der Text so lange unverändert ist.
KARENZ_MIN = 15
#: Nach dem Fortsetzen so lange auf die Session mit derselben ID warten.
# Echter Weg bash -lc → bau.py (Manifest, gh, MCP-Prüfung) → claude → Session-JSON
# dauert deutlich länger als die Attrappe; Fehlalarm wäre Stufe 3 ohne Not.
NACHWEIS_S = 120
#: Höchstens so viele Starts je Ticket im Zeitfenster.
START_MAX = 2
START_FENSTER_S = 6 * 3600
#: Nach einem Fenster-Start so lange prüfen, dass Fenster und Prozess bleiben (R6).
START_NACHWEIS_S = 5.0
#: Mehr als so viele Anstupser je Fenster im Zeitfenster ⇒ „braucht David“ (R5).
STUPSER_MAX = 3
STUPSER_FENSTER_S = 24 * 3600
#: Meldungen ohne Eingriff kommen je Fenster/Ereignis nur einmal am Tag ins Issue;
#: jeder Eingriff (gestartet, angestupst, fortgesetzt, geschlossen) immer (R3).
MELDUNG_EINMAL_PRO_TAG = frozenset(
    {
        "rueckfrage",
        "braucht_david",
        "sicherung_fehlgeschlagen",
        "fortsetzen_fehlgeschlagen",
        "startet_nicht",
        "start_fehlgeschlagen",
        "stupser_erschoepft",
        "session_beendet",
    }
)
#: Ereignis → Vorfall (Klasse, Symptom, Ursache, Lösung) für die Lernschleife (#286).
#: Jeder erkannte Stillstand steht hier, auch der erste Anstupser. Nur die Züge, bei
#: denen nichts stockte — ``gestartet``, ``fortgesetzt``, ``geschlossen`` — fehlen:
#: sie sind der Normalbetrieb und lernen nichts.
EREIGNIS_VORFALL: dict[str, tuple[str, str, str, str]] = {
    "startet_nicht": (
        "skill",
        "Fenster steht leer, die Bau-Session läuft nicht an",
        "Startbefehl im Fenster lief nicht an (Vorlage, Pfad oder Repo stimmt nicht)",
        "Aufpasser startet neu; Startbefehl und Repo-Pfad des Fensters prüfen",
    ),
    "start_fehlgeschlagen": (
        "skill",
        "Neustart des Fensters schlägt fehl",
        "tmux-Befehl oder Startvorlage scheitert",
        "Fehlertext im Aufpasser-Log lesen, Vorlage (--bau-vorlage) richtigstellen",
    ),
    "fortsetzen_fehlgeschlagen": (
        "skill",
        "Angehaltene Session lässt sich nicht fortsetzen",
        "Resume-ID fehlt oder das Transkript ist weg",
        "Session neu starten statt fortsetzen (bau <N> --sofort)",
    ),
    "sicherung_fehlgeschlagen": (
        "skill",
        "Arbeit eines Fensters konnte nicht gesichert werden",
        "Commit/Push vor dem Schließen scheiterte",
        "Worktree von Hand sichern, erst dann das Fenster schließen",
    ),
    "stupser_erschoepft": (
        "skill",
        "Session reagiert auf keinen Anstupser mehr",
        "Session hängt oder wartet auf etwas, das nie kommt",
        "Fenster ansehen, Session beenden und die Runde neu starten",
    ),
    "braucht_david": (
        "mensch",
        "Bau-Kette wartet auf Davids Entscheidung",
        "Die Frage wurde nicht vor dem Spawn im Grill entschieden",
        "Entscheidung in den Grill vorziehen; sonst greift die 60-min-Annahme (#285)",
    ),
    "session_beendet": (
        "skill",
        "Session beendet, aber das Ticket ist offen — niemand baut weiter",
        "Keine Gesprächs-ID gemerkt, der Aufpasser kann nicht fortsetzen",
        "Neue Runde starten (bau <N> --sofort); Sitzungs-Datei der Session prüfen",
    ),
    "angestupst": (
        "skill",
        "Session steht still und muss angestupst werden",
        "Session wartet auf nichts, meldet aber nichts — Stufe 1 des Aufpassers",
        "Aufpasser stupst an; wiederholt es sich am selben Ticket, Auftrag schärfen",
    ),
    "rueckfrage": (
        "mensch",
        "Session stellt eine Rückfrage und wartet",
        "Der Auftrag ließ eine Entscheidung offen",
        "Auftrag vorab schärfen (Bleibt-gleich-Liste, Umfang beziffern)",
    ),
}

#: Ticket-Nummer aus einem Fenster-Schlüssel wie ``spec-282/bau 286``.
_FENSTER_TICKET = re.compile(r"bau[ _-]?(\d+)")

#: Nur noch das im Pane ⇒ die Session ist beendet, ein Anstupser liefe als Befehl.
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "fish", "ksh"})
#: Deploy-Prozesse älter als das sind Waisen.
DEPLOY_ALTER_MAX_S = 6 * 3600
DEPLOY_MUSTER = r"safe_deploy_vps\.sh|deploy_schlange|staging_deploy\.sh|pytest"
STOPP_LABELS = frozenset({"ready-for-human", "needs-info", "wontfix"})
CRON_MARKE = "# to-spawn aufpasser"
CRON_TAKT = "*/15 * * * *"
SKRIPT_STARTER = Path(__file__).resolve().parent.parent / "skripte" / "aufpasser.py"
ARBEITS_MARKER = (
    "esc to interrupt",
    "background agent",
    "usage limit",
    "limit reset",
)
# Warte-Zeile von bau.py (Blocker offen). Nur diese Form zählt — „nächste Prüfung“
# allein stand am 21.09. als Claude-Prosa im Scrollback eines idle Fensters.
BLOCKER_WARTEN = re.compile(r"Ticket #\d+ wartet \(.*\) — nächste Prüfung in \d+ min")
RUECKFRAGE_MARKER = (
    "Do you want to proceed?",
    "Do you want to",
    "Yes, and don't ask again",
    "(y/n)",
    "Trust",
    "Esc to cancel",
)
ANSTUPS_TEXT = (
    "Aufpasser: Du stehst seit {min} min still. Setz deinen Loop/Auftrag genau dort "
    "fort, wo du warst (Ticket offen? weiterbauen; nichts zu tun? ScheduleWakeup)."
)
VORLAGE_REPO = "cd {repo} && {py} scripts/bau.py {n}{resume}"
VORLAGE_SKILL = "cd {repo} && TO_SPAWN_REPO={repo} {py} ~/.claude/skills/to-spawn/skripte/bau.py {n}{resume}"
VORLAGE_WACHE_REPO = "cd {repo} && {py} scripts/wache.py {s}{resume}"
VORLAGE_WACHE_SKILL = "cd {repo} && TO_SPAWN_REPO={repo} {py} ~/.claude/skills/to-spawn/skripte/wache.py {s}{resume}"
#: Nie in eine Sicherung: Geheimnisse und Zugangsdateien.
SICHERUNG_AUSSCHLUSS = (
    ".env",
    ".env.*",
    "*.env",
    "*zugang*",
    "*.pem",
    "*.key",
    ".credentials.json",
)
GIT_IDENTITAET = {
    "GIT_AUTHOR_NAME": "Aufpasser",
    "GIT_AUTHOR_EMAIL": "aufpasser@to-spawn.invalid",
    "GIT_COMMITTER_NAME": "Aufpasser",
    "GIT_COMMITTER_EMAIL": "aufpasser@to-spawn.invalid",
}


def zustand_standard() -> Path:
    return Path.home() / ".local" / "state" / "to-spawn" / "aufpasser"


# --- Bausteine ohne Seiteneffekte -------------------------------------------


@dataclass
class Einstellungen:
    """Alles, was der Aufpasser von außen bekommt (Tests spritzen es ein)."""

    zustand: Path
    tmux_socket: str | None = None
    hang_min: float = HANG_MIN
    bau_vorlage: str | None = None
    #: Startbefehl des Wächter-Fensters mit {repo} {py} {s} {resume} (R1).
    wache_vorlage: str | None = None
    deploy_muster: str = DEPLOY_MUSTER
    repo: Path | None = None
    trocken: bool = False
    jetzt: Callable[[], float] = time.time
    heim: Path | None = None
    #: Künstliche Pause zwischen Lesen und Eingriff (nur Tests, Fix F6).
    verzoegerung_s: float = 0.0

    def __post_init__(self) -> None:
        if self.hang_min < HANG_MIN_UNTERGRENZE:
            raise ValueError(
                f"hang_min {self.hang_min} liegt unter der Untergrenze "
                f"{HANG_MIN_UNTERGRENZE} (Wakeups wartender Sessions kommen alle ≤ 60 min)"
            )


@dataclass(frozen=True)
class Freigabe:
    """Erlaubnis, ein Fenster zu schließen/fortzusetzen — entsteht nur in den zwei
    Fabriken unten."""

    grund: str
    sicherung: str | None
    session_id: str | None = None


def freigabe_ticket_zu(ticket: int, sicherung: str | None) -> Freigabe:
    """Ticket ist CLOSED; ein schmutziger Worktree wurde vorher gesichert."""
    return Freigabe(grund=f"Ticket #{ticket} zu", sicherung=sicherung)


def freigabe_gesichert(session_id: str, sicherung: str | None) -> Freigabe:
    """Sicherung erledigt (oder nichts zu sichern) und die Gesprächs-ID ist bekannt."""
    if not session_id:
        raise ValueError("Freigabe ohne Gesprächs-ID")
    return Freigabe(grund="gesichert", sicherung=sicherung, session_id=session_id)


@dataclass
class Fenster:
    sitzung: str
    spec: str
    index: str
    name: str
    pfad: str
    pane_pid: int
    window_id: str = ""
    pane_id: str = ""

    @property
    def ziel(self) -> str:
        return f"={self.sitzung}:{self.index}"

    @property
    def ticket(self) -> int | None:
        treffer = re.fullmatch(r"bau (\d+)", self.name)
        return int(treffer.group(1)) if treffer else None

    @property
    def ist_wache(self) -> bool:
        return self.name == f"wache {self.spec}"

    @property
    def sessions_name(self) -> str:
        """Name der Datei ``.to-spawn/sessions/<name>.json`` (R2)."""
        return f"wache-{self.spec}" if self.ist_wache else str(self.ticket or "")


@dataclass(frozen=True)
class SessionInfo:
    """Was Claude Code über eine laufende Session verrät (``~/.claude/sessions/``)."""

    session_id: str
    cwd: Path | None
    status: str | None
    pid: int
    #: True: aus der Session-JSON (dann sind ``cwd``/``status`` verlässlich).
    aus_json: bool = True


def arbeitet(text: str, status: str | None = None) -> bool:
    """Fenster arbeitet oder wartet auf Antwort → nie anfassen.

    Marker im Pane-Text (auch die Limit-Meldung des Wächters) oder ``status == busy``
    aus der Session-JSON.
    """
    if status == "busy":
        return True
    return (
        any(m in text for m in ARBEITS_MARKER + RUECKFRAGE_MARKER)
        or bool(BLOCKER_WARTEN.search(text))
        or bool(LIMIT_TEXT.search(text))
    )


def rueckfrage(text: str) -> bool:
    return any(m in text for m in RUECKFRAGE_MARKER)


def pane_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def session_id_aus_argv(argv: list[str]) -> str | None:
    """``--session-id <id>`` oder ``--resume <id>`` aus einem ``claude``-Aufruf."""
    if not any(Path(a).name == "claude" for a in argv):
        return None
    for i, a in enumerate(argv):
        if a in ("--session-id", "--resume") and i + 1 < len(argv):
            return argv[i + 1]
        for flag in ("--session-id=", "--resume="):
            if a.startswith(flag):
                return a[len(flag) :]
    return None


def _argv(pid: int) -> list[str]:
    try:
        roh = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [t.decode("utf-8", "replace") for t in roh.split(b"\0") if t]


def prozess_argv_text(pid: int) -> str:
    """Argumente eines Prozesses als Text (für Prüfungen)."""
    return " ".join(_argv(pid))


def _prozess_kinder() -> dict[int, list[int]]:
    kinder: dict[int, list[int]] = {}
    for eintrag in Path("/proc").iterdir():
        if not eintrag.name.isdigit():
            continue
        try:
            stat = (eintrag / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # ``pid (comm) zustand ppid …`` — comm darf Leerzeichen und Klammern enthalten.
        rest = stat[stat.rfind(")") + 2 :].split()
        if len(rest) < 2:
            continue
        kinder.setdefault(int(rest[1]), []).append(int(eintrag.name))
    return kinder


def _vorfahren(pid: int) -> set[int]:
    """``pid`` und seine Eltern bis init — die eigene Aufrufkette ist nie ein Deploy."""
    kette: set[int] = set()
    while pid > 1 and pid not in kette:
        kette.add(pid)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(
                encoding="utf-8", errors="replace"
            )
            pid = int(stat[stat.rfind(")") + 2 :].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return kette


def prozess_baum(pid: int) -> list[int]:
    """``pid`` selbst und alle Nachfahren (Breite zuerst)."""
    kinder = _prozess_kinder()
    baum, warteschlange = [], [pid]
    while warteschlange:
        p = warteschlange.pop(0)
        baum.append(p)
        warteschlange.extend(kinder.get(p, []))
    return baum


def session_beendet(pane_pid: int) -> bool:
    """Im Pane lebt nur noch eine Shell (oder gar nichts): die Session ist zu Ende.

    Ein Anstupser würde dort als Shell-Befehl laufen und als „Erfolg“ zählen —
    deshalb wird so ein Fenster nie angestupst, sondern gleich fortgesetzt (R2).
    ``cat``/``sleep`` und alles andere gelten nicht als beendet.
    """
    if not pane_pid:
        return True
    baum = prozess_baum(pane_pid)
    lebend = [pid for pid in baum if _prozess_lebt(pid)]
    if not lebend:
        return True
    for pid in lebend:
        argv = _argv(pid)
        if not argv:
            continue
        if Path(argv[0]).name.lstrip("-") not in SHELLS:
            return False
    return True


def _prozess_lebt(pid: int) -> bool:
    return Path(f"/proc/{pid}").exists()


def session_laeuft_schon(sid: str, heim: Path | None = None) -> int | None:
    """PID eines lebenden Claude-Prozesses, der laut ``~/.claude/sessions/*.json``
    dieses Gespräch schon führt — sonst None. Schutz vor zwei ``--resume`` auf
    dasselbe Transkript (anderes Fenster, andere tmux-Sitzung, ohne tmux)."""
    ordner = (heim or Path.home()) / ".claude" / "sessions"
    try:
        dateien = sorted(ordner.glob("*.json"))
    except OSError:
        return None
    for datei in dateien:
        try:
            roh = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(roh, dict) or roh.get("sessionId") != sid:
            continue
        try:
            pid = int(roh.get("pid") or datei.stem)
        except ValueError:
            continue
        if _prozess_lebt(pid):
            return pid
    return None


def _session_json_lesen(
    pid: int, window_id: str, pane_id: str, heim: Path | None = None
) -> SessionInfo | None:
    """``<heim>/.claude/sessions/<pid>.json`` lesen — nur gültig, wenn der Prozess lebt
    und das ``tmux``-Feld (``sitzung:@fenster.%pane``) zu diesem Pane passt oder fehlt."""
    datei = (heim or Path.home()) / ".claude" / "sessions" / f"{pid}.json"
    try:
        roh = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(roh, dict) or not _prozess_lebt(pid):
        return None
    if int(roh.get("pid") or pid) != pid:
        return None
    sid = roh.get("sessionId")
    if not isinstance(sid, str) or not sid:
        return None
    tmux_feld = roh.get("tmux")
    if (
        isinstance(tmux_feld, str)
        and tmux_feld
        and not tmux_feld.endswith(f"{window_id}.{pane_id}")
    ):
        log.info(
            "Session-JSON %s gehört zu %s, nicht zu %s.%s — ignoriert",
            datei.name,
            tmux_feld,
            window_id,
            pane_id,
        )
        return None
    cwd = roh.get("cwd")
    status = roh.get("status")
    return SessionInfo(
        session_id=sid,
        cwd=Path(cwd) if isinstance(cwd, str) and cwd else None,
        status=status if isinstance(status, str) else None,
        pid=pid,
        aus_json=True,
    )


def session_finden(
    pane_pid: int, window_id: str, pane_id: str, heim: Path | None = None
) -> SessionInfo | None:
    """Gesprächs-ID und Zustand des Claude-Prozesses unter ``pane_pid``.

    Reihenfolge: ``--session-id``/``--resume`` aus argv (muss zur Session-JSON
    passen, falls es eine gibt) → Session-JSON → None. Kein Transkript-Fallback.
    """
    for pid in prozess_baum(pane_pid):
        argv = _argv(pid)
        if not any(Path(a).name == "claude" for a in argv):
            continue
        argv_sid = session_id_aus_argv(argv)
        info = _session_json_lesen(pid, window_id, pane_id, heim)
        if info is not None:
            if argv_sid and argv_sid != info.session_id:
                log.warning(
                    "PID %s: argv nennt %s…, Session-JSON %s… — Widerspruch, keine ID",
                    pid,
                    argv_sid[:8],
                    info.session_id[:8],
                )
                return None
            return info
        if argv_sid:
            try:
                cwd: Path | None = Path(os.readlink(f"/proc/{pid}/cwd"))
            except OSError:
                cwd = None
            return SessionInfo(argv_sid, cwd, None, pid, aus_json=False)
    return None


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    try:
        ergebnis = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args[:3])}: Zeitgrenze 300 s") from None
    except FileNotFoundError as fehler:
        raise RuntimeError(f"git {' '.join(args[:3])}: {fehler}") from None
    if ergebnis.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}: {ergebnis.stderr.strip()[:300]}")
    return ergebnis.stdout


def worktree_schmutzig(worktree: Path) -> bool:
    return bool(_git(worktree, "status", "--porcelain").strip())


def worktree_finden(repo: Path, ticket: int, cwd: Path | None = None) -> Path | None:
    """Worktree eines Tickets aus ``git worktree list`` — Basename ``wt-<N>``, sonst
    ``cwd`` der Session, falls es ein registrierter Worktree ist.

    Das Hauptrepo zählt nie: es ist der geteilte Baum aller Sessions, dort liegt
    fremde Arbeit, die niemand als „Sicherung #N“ wegpushen darf. Kein Treffer ⇒
    None („nichts zu sichern“), ohne Umweg über Umgebungsvariablen.
    """
    haupt = Path(_git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    kandidaten: list[Path] = []
    for zeile in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if zeile.startswith("worktree "):
            pfad = Path(zeile[len("worktree ") :]).resolve()
            if pfad != haupt:
                kandidaten.append(pfad)
    for pfad in kandidaten:
        if pfad.name == f"wt-{ticket}":
            return pfad
    if cwd is not None:
        try:
            ziel = cwd.resolve()
        except OSError:
            return None
        if ziel in kandidaten:
            return ziel
    return None


def _ref_lesen(worktree: Path, ref: str) -> str | None:
    try:
        return _git(worktree, "rev-parse", "--verify", "-q", ref).strip() or None
    except RuntimeError:
        return None


def sichern(worktree: Path, ticket: int, jetzt: float | None = None) -> str | None:
    """Arbeitsstand als Commit auf ``sicherung/<N>`` sichern und pushen.

    Kein stash, kein Branch-Wechsel, kein Index-Zugriff: temporärer ``GIT_INDEX_FILE``
    ab ``HEAD`` → ``add -A`` ohne Geheimnisse (``SICHERUNG_AUSSCHLUSS``) →
    ``write-tree`` → ``commit-tree`` mit Eltern ``HEAD`` und (falls vorhanden) der
    vorigen ``sicherung/<N>`` → ``update-ref`` → ``push`` (kein ``+``, die Kette ist
    fast-forward). Rückgabe = Commit-SHA; sauberer Baum → None. Fehler ⇒ RuntimeError.
    """
    if not worktree_schmutzig(worktree):
        return None
    handle, index = tempfile.mkstemp(prefix="aufpasser-index-")
    os.close(handle)
    os.unlink(index)
    env = {**os.environ, **GIT_IDENTITAET, "GIT_INDEX_FILE": index}
    ref = f"refs/heads/sicherung/{ticket}"
    try:
        kopf = _git(worktree, "rev-parse", "HEAD").strip()
        _git(worktree, "read-tree", kopf, env=env)
        ausschluss = [f":(exclude){m}" for m in SICHERUNG_AUSSCHLUSS]
        _git(worktree, "add", "-A", "--", ".", *ausschluss, env=env)
        baum = _git(worktree, "write-tree", env=env).strip()
        eltern = ["-p", kopf]
        vorige = _ref_lesen(worktree, ref)
        if vorige and vorige != kopf:
            eltern += ["-p", vorige]
        zeit = datetime.fromtimestamp(
            jetzt or time.time(), tz=timezone.utc
        ).astimezone()
        commit = _git(
            worktree,
            "commit-tree",
            baum,
            *eltern,
            "-m",
            f"Aufpasser: Sicherung #{ticket} {zeit.isoformat(timespec='seconds')} [skip ci]",
            env=env,
        ).strip()
    finally:
        Path(index).unlink(missing_ok=True)
    _git(worktree, "update-ref", ref, commit)
    _git(worktree, "push", "-q", "origin", f"{ref}:{ref}")
    return commit


def _crontab(
    *args: str, eingabe: str | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["crontab", *args],
            input=eingabe,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("crontab antwortet nicht (30 s)") from None
    except FileNotFoundError:
        raise RuntimeError("crontab nicht installiert") from None


def cron_einrichten(
    zustand: Path,
    skript: Path = SKRIPT_STARTER,
    python: str = sys.executable,
    jetzt: float | None = None,
    trocken: bool = False,
) -> str:
    """Cron-Zeile anlegen oder ersetzen (idempotent); alte crontab wird vorher gesichert.

    Ersetzt wird jede Zeile mit der Marke UND jede Alt-Zeile, die ``aufpasser.py``
    aufruft (z. B. der alte Pfad ``~/aufpasser/aufpasser.py`` ohne Marke) — danach
    gibt es genau eine Aufpasser-Zeile. ``trocken``: nur zeigen, nichts schreiben.
    """
    alt = _crontab("-l")
    zeilen = alt.stdout.splitlines() if alt.returncode == 0 else []
    zeile = f"{CRON_TAKT} {python} {skript} >> {zustand}/cron.out 2>&1  {CRON_MARKE}"

    def ist_aufpasser(z: str) -> bool:
        return CRON_MARKE in z or (
            "aufpasser.py" in z and not z.lstrip().startswith("#")
        )

    neu = [z for z in zeilen if not ist_aufpasser(z)]
    stellen = [i for i, z in enumerate(zeilen) if ist_aufpasser(z)]
    neu.insert(stellen[0] if stellen else len(neu), zeile)
    if neu == zeilen:
        return f"Cron unverändert: {zeile}"
    if trocken:
        return f"[trocken] Cron würde eingerichtet: {zeile}"
    zustand.mkdir(parents=True, exist_ok=True)
    stempel = datetime.fromtimestamp(jetzt or time.time(), tz=timezone.utc).astimezone()
    stempel = stempel.strftime("%Y%m%d-%H%M%S")
    (zustand / f"crontab.vorher.{stempel}").write_text(alt.stdout, encoding="utf-8")
    ergebnis = _crontab("-", eingabe="\n".join(neu) + "\n")
    if ergebnis.returncode != 0:
        raise RuntimeError(f"crontab schreiben: {ergebnis.stderr.strip()[:300]}")
    return f"Cron eingerichtet: {zeile}"


# --- Der Aufpasser -----------------------------------------------------------


@dataclass
class Stand:
    fenster: dict[str, dict] = field(default_factory=dict)
    meldungen: dict[str, float] = field(default_factory=dict)
    #: Start-Gedächtnis je Ticket: Zeitstempel der Fenster-Starts (Regel 1).
    starts: dict[str, list[float]] = field(default_factory=dict)


class Aufpasser:
    def __init__(self, e: Einstellungen) -> None:
        self.e = e
        self.jetzt = e.jetzt()
        #: Deploy-Muster dieses Laufs (mit Repo-Konfig) — Wache vor jedem Eingriff (R4).
        self.muster = e.deploy_muster
        self.datum = (
            datetime.fromtimestamp(self.jetzt, tz=timezone.utc)
            .astimezone()
            .strftime("%Y-%m-%d")
        )
        self.stand = self._stand_laden()
        #: Echte Fenster-Starts in diesem Tick (Speicher-Staffel, #257 Paket B):
        #: ab dem zweiten Start wartet der Tick ``staffel_s`` Sekunden, damit die
        #: Claude-Starts sich nicht stapeln. Am Tick-Anfang wieder 0.
        self._starts_in_tick = 0

    # -- Werkzeuge ---------------------------------------------------------------

    def sh(self, *args: str, cwd: Path | None = None, timeout: float = 120) -> str:
        try:
            ergebnis = subprocess.run(
                args,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"{' '.join(args[:4])}: Zeitgrenze {timeout:g} s überschritten"
            ) from None
        except FileNotFoundError as fehler:
            raise RuntimeError(f"{' '.join(args[:4])}: {fehler}") from None
        if ergebnis.returncode != 0:
            raise RuntimeError(f"{' '.join(args[:4])}: {ergebnis.stderr.strip()[:300]}")
        return ergebnis.stdout

    def tmux(self, *args: str) -> str:
        socket = ["-L", self.e.tmux_socket] if self.e.tmux_socket else []
        return self.sh("tmux", *socket, *args)

    def _stand_laden(self) -> Stand:
        datei = self.e.zustand / "stand.json"
        try:
            roh = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return Stand()
        return Stand(
            fenster=dict(roh.get("fenster") or {}),
            meldungen=dict(roh.get("meldungen") or {}),
            starts={
                k: [float(t) for t in v] for k, v in (roh.get("starts") or {}).items()
            },
        )

    def _stand_schreiben(self) -> None:
        if self.e.trocken:
            return
        grenze = self.jetzt - 2 * 86400
        meldungen = {k: t for k, t in self.stand.meldungen.items() if t >= grenze}
        starts = {
            k: [t for t in v if self.jetzt - t < START_FENSTER_S]
            for k, v in self.stand.starts.items()
        }
        starts = {k: v for k, v in starts.items() if v}
        self.e.zustand.mkdir(parents=True, exist_ok=True)
        (self.e.zustand / "stand.json").write_text(
            json.dumps(
                {
                    "fenster": self.stand.fenster,
                    "meldungen": meldungen,
                    "starts": starts,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

    # -- Lesen -------------------------------------------------------------------

    def deploy_muster_fuer(self, repos: Iterable[Path]) -> str:
        """``--deploy-muster`` plus die Skripte aus ``.to-spawn/config.json`` der Repos
        (``deploy_befehl``, ``staging_start`` — jeweils der Basename), ODER-verknüpft."""
        teile = [self.e.deploy_muster]
        for repo in repos:
            try:
                konfig = config.lade(repo)
            except (OSError, ValueError) as fehler:
                log.warning("%s: Konfig nicht lesbar: %s", repo, fehler)
                continue
            for schluessel in ("deploy_befehl", "staging_start"):
                wert = konfig.get(schluessel)
                if not isinstance(wert, str) or not wert.strip():
                    continue
                for wort in shlex.split(wert):
                    name = Path(wort).name
                    if name.endswith((".sh", ".py")):
                        teile.append(re.escape(name))
        return "|".join(dict.fromkeys(teile))

    def _prozess_alter_s(self, pid: int) -> float | None:
        try:
            raus = self.sh("ps", "-o", "etimes=", "-p", str(pid), timeout=30).strip()
            return float(raus) if raus else None
        except (RuntimeError, ValueError):
            return None

    def deploy_laeuft(self, muster_text: str | None = None) -> list[str]:
        """Prozesse, die auf das Deploy-Muster passen (ohne uns selbst, ohne Waisen).

        ``pgrep -af`` liefert die Kandidaten; gezählt wird nur ein Treffer im Programm,
        Skript oder ersten Argument (``argv[:3]``) — der Prompt einer Claude-Session
        nennt ``safe_deploy_vps.sh`` und ``pytest`` als Text, ist aber kein Deploy.
        Die eigene Aufrufkette (Cron, Shell) zählt nie; Prozesse älter als 6 h sind
        verwaist („Waise ignoriert“).
        """
        muster_text = muster_text or self.e.deploy_muster
        try:
            raus = self.sh("pgrep", "-af", muster_text, timeout=30)
        except RuntimeError as fehler:
            # pgrep: Exit 1 = kein Treffer; alles andere ist eine echte Störung —
            # dann lieber nichts anfassen (fail-closed) als ein Deploy übersehen.
            if "Zeitgrenze" in str(fehler) or "No such file" in str(fehler):
                raise RuntimeError(f"Deploy-Wache: pgrep nicht nutzbar ({fehler})")
            return []
        muster = re.compile(muster_text)
        eigene = _vorfahren(os.getpid())
        treffer = []
        for zeile in raus.splitlines():
            pid, _, args = zeile.partition(" ")
            if not pid.isdigit() or int(pid) in eigene:
                continue
            kopf = " ".join(_argv(int(pid))[:3]) or args
            if not muster.search(kopf):
                continue
            alter = self._prozess_alter_s(int(pid))
            if alter is not None and alter > DEPLOY_ALTER_MAX_S:
                log.info(
                    "Waise ignoriert: PID %s seit %.1f h (%s)",
                    pid,
                    alter / 3600,
                    kopf[:120],
                )
                continue
            treffer.append(kopf[:200])
        return treffer

    def sitzungen(self) -> list[tuple[str, str]]:
        try:
            namen = self.tmux("list-sessions", "-F", "#{session_name}").split()
        except RuntimeError as fehler:
            log.info("kein tmux-Server erreichbar: %s", fehler)
            return []
        gefunden = []
        for name in namen:
            treffer = re.fullmatch(r"spec-(\d+)", name)
            if treffer:
                gefunden.append((name, treffer.group(1)))
        return gefunden

    def fenster(self, sitzung: str, spec: str) -> list[Fenster]:
        liste = []
        for zeile in self.tmux(
            "list-windows",
            "-t",
            f"={sitzung}",
            "-F",
            "#{window_index}\t#{window_name}\t#{pane_current_path}\t#{pane_pid}\t#{window_id}\t#{pane_id}",
        ).splitlines():
            teile = zeile.split("\t")
            if len(teile) != 6:
                continue
            liste.append(
                Fenster(
                    sitzung,
                    spec,
                    teile[0],
                    teile[1],
                    teile[2],
                    int(teile[3] or 0),
                    teile[4],
                    teile[5],
                )
            )
        return liste

    def repo_ordner(self, fenster: list[Fenster]) -> Path | None:
        for f in fenster:
            pfad = Path(f.pfad)
            if (pfad / "scripts" / "bau.py").is_file() or (
                pfad / ".to-spawn" / "config.json"
            ).is_file():
                return pfad
        if self.e.repo:
            return self.e.repo
        roh = os.environ.get("TO_SPAWN_REPO")
        return Path(roh) if roh else None

    def offene_tickets(self, spec: str, repo: Path) -> set[int]:
        raus = self.sh(
            "gh",
            "api",
            f"repos/{{owner}}/{{repo}}/issues/{spec}/sub_issues",
            "--paginate",
            "--jq",
            '.[] | select(.state=="open") | .number',
            cwd=repo,
        )
        return {int(x) for x in raus.split()}

    def ticket_lesen(self, ticket: int, repo: Path) -> dict:
        """``state`` und ``labels`` (Namen) eines Tickets."""
        raus = self.sh(
            "gh", "issue", "view", str(ticket), "--json", "state,labels", cwd=repo
        )
        try:
            roh = json.loads(raus)
            labels = {
                str(x.get("name", ""))
                for x in roh.get("labels") or []
                if isinstance(x, dict)
            }
            return {"state": roh.get("state"), "labels": labels}
        except (json.JSONDecodeError, AttributeError) as fehler:
            raise RuntimeError(f"gh issue view {ticket}: unlesbar ({fehler})") from None

    def ticket_zu(self, ticket: int, repo: Path) -> bool:
        return self.ticket_lesen(ticket, repo).get("state") == "CLOSED"

    def pane_text(self, ziel: str) -> str:
        return self.tmux("capture-pane", "-p", "-t", ziel)

    def session(self, f: Fenster) -> SessionInfo | None:
        if not f.pane_pid:
            return None
        return session_finden(f.pane_pid, f.window_id, f.pane_id, self.e.heim)

    # -- Melden ------------------------------------------------------------------

    def melden(
        self, spec: str, repo: Path, fenster: str, ereignis: str, text: str
    ) -> None:
        """Logzeile + Kommentar im Spec-Issue. Jeder Eingriff bekommt seine Zeile;
        Meldungen ohne Eingriff (``MELDUNG_EINMAL_PRO_TAG``) je Fenster/Ereignis/Tag
        nur einmal — der Doppelschutz-Schlüssel wird erst nach erfolgreichem
        Kommentar gesetzt (R3)."""
        log.info("spec %s: %s", spec, text)
        schluessel = f"{fenster}|{ereignis}|{self.datum}"
        if self.e.trocken:
            print(f"[trocken] spec {spec}: {text}")
            return
        # Vorfall VOR der Tages-Sperre: die Lernschleife hat ihren eigenen
        # Doppelschutz, sonst verlöre ein gescheiterter Schreibversuch den Tag.
        self.vorfall_notieren(spec, repo, fenster, ereignis, text)
        if ereignis in MELDUNG_EINMAL_PRO_TAG and schluessel in self.stand.meldungen:
            log.info("spec %s: Meldung heute schon abgesetzt (%s)", spec, ereignis)
            return
        try:
            self.sh(
                "gh", "issue", "comment", spec, "--body", f"Aufpasser: {text}", cwd=repo
            )
        except RuntimeError as fehler:
            log.error("spec %s: Kommentar fehlgeschlagen: %s", spec, fehler)
            return
        self.stand.meldungen[schluessel] = self.jetzt

    def log_ordner(self, repo: Path, ticket: str) -> Path:
        """Wohin die Vorfall-Zeile gehört: Worktree des Tickets, sonst Hauptbaum.

        capo liest Laufdateien nur im Repo selbst und in ``wt-<Ticket>``. Ein
        Vorfall zu #291, der im Worktree von #286 landet, sieht niemand wieder.
        """
        if repo.name == f"wt-{ticket}":
            return repo
        haupt = bau_log.log_rueckfall()
        if haupt is not None and haupt.is_dir():
            return haupt
        return repo

    def vorfall_notieren(
        self, spec: str, repo: Path, fenster: str, ereignis: str, text: str
    ) -> None:
        """Stillstand als ``vorfall``-Zeile ins Bau-Log — die Lernschleife (#286).

        Ticket aus dem Fenster-Namen (``bau <N>``), sonst die Spec. ``capo …
        --katalog`` hängt die Zeile später an den Fehlerkatalog. Ein Fehler beim
        Schreiben darf den Aufpasser-Lauf nie kippen.
        """
        worte = EREIGNIS_VORFALL.get(ereignis)
        if worte is None:
            return
        treffer = _FENSTER_TICKET.search(fenster)
        ticket = treffer.group(1) if treffer else spec
        # Wächter-Fenster arbeiten in einem Worktree; dessen ``.to-spawn`` liest
        # niemand für ein fremdes Ticket. Darum in den Hauptbaum schreiben, wenn
        # das Fenster nicht der Worktree dieses Tickets ist (#286).
        ziel = self.log_ordner(repo, ticket)
        klasse, symptom, ursache, loesung = worte
        try:
            vorfall.schreibe(
                ziel,
                ticket,
                klasse=klasse,
                symptom=symptom,
                ursache=ursache,
                loesung=loesung,
                beispiel=f"#{ticket} {self.datum}",
                regel=ereignis,
                quelle="aufpasser",
            )
        except (OSError, ValueError) as fehler:
            log.warning("Vorfall zu %s nicht notiert: %s", ereignis, fehler)

    # -- Eingreifen --------------------------------------------------------------

    def bau_befehl(self, repo: Path, ticket: int, resume: str | None) -> str:
        venv = repo / ".venv" / "bin" / "python"
        py = str(venv) if venv.is_file() else "python3"
        if self.e.bau_vorlage:
            vorlage = self.e.bau_vorlage
        elif (repo / "scripts" / "bau.py").is_file():
            vorlage = VORLAGE_REPO
        else:
            vorlage = VORLAGE_SKILL
        return vorlage.format(
            repo=shlex.quote(str(repo)),
            py=shlex.quote(py),
            n=ticket,
            resume=f" --resume {resume}" if resume else "",
        )

    def wache_befehl(self, repo: Path, spec: str, resume: str | None) -> str:
        """Startbefehl des Wächter-Fensters (R1), analog ``bau_befehl``."""
        venv = repo / ".venv" / "bin" / "python"
        py = str(venv) if venv.is_file() else "python3"
        if self.e.wache_vorlage:
            vorlage = self.e.wache_vorlage
        elif (repo / "scripts" / "wache.py").is_file():
            vorlage = VORLAGE_WACHE_REPO
        else:
            vorlage = VORLAGE_WACHE_SKILL
        return vorlage.format(
            repo=shlex.quote(str(repo)),
            py=shlex.quote(py),
            s=spec,
            resume=f" --resume {resume}" if resume else "",
        )

    def resume_aus_datei(self, repo: Path, name: str) -> tuple[str, Path] | None:
        """Gesprächs-ID + cwd aus ``.to-spawn/sessions/<name>.json`` — nur wenn das
        Transkript ``~/.claude/projects/<cwd-slug>/<id>.jsonl`` noch da ist (R2)."""
        daten = sessions_datei.lesen(repo, name)
        if not daten:
            return None
        sid = str(daten["session_id"])
        cwd = Path(str(daten.get("cwd") or repo))
        if not cwd.is_dir():
            cwd = repo
        transkript = transkript_ordner(cwd, self.e.heim) / f"{sid}.jsonl"
        if not transkript.is_file():
            log.info(
                "%s: Gesprächs-ID %s… gemerkt, aber Transkript %s fehlt — frischer Start",
                name,
                sid[:8],
                transkript,
            )
            return None
        pid = session_laeuft_schon(sid, self.e.heim)
        if pid is not None:
            log.warning(
                "%s: Gespräch %s… läuft schon (PID %d) — kein zweites --resume",
                name,
                sid[:8],
                pid,
            )
            return None
        return sid, cwd

    def fenster_starten(self, sitzung: str, name: str, cwd: Path, befehl: str) -> None:
        """Regel 1: neues Fenster ``bau <N>`` bzw. ``wache <S>`` anlegen."""
        log.info("%s: Fenster „%s“ starten: %s", sitzung, name, befehl)
        if self.e.trocken:
            print(f"[trocken] {sitzung}: Fenster „{name}“ starten: {befehl}")
            return
        self.tmux(
            "new-window",
            "-d",
            "-t",
            f"={sitzung}",
            "-n",
            name,
            "-c",
            str(cwd),
            f"bash -lc {shlex.quote(befehl)}",
        )

    def _start_nachweisen(self, sitzung: str, name: str) -> bool:
        """Bis ``START_NACHWEIS_S`` prüfen, dass das Fenster bleibt und sein
        Pane-Prozess lebt — erst dann gilt der Start (R6)."""
        frist = time.monotonic() + START_NACHWEIS_S
        while True:
            lebt = False
            try:
                for zeile in self.tmux(
                    "list-windows",
                    "-t",
                    f"={sitzung}",
                    "-F",
                    "#{window_name}\t#{pane_pid}\t#{pane_dead}",
                ).splitlines():
                    teile = zeile.split("\t")
                    if len(teile) == 3 and teile[0] == name:
                        lebt = teile[2] != "1" and _prozess_lebt(int(teile[1] or 0))
            except (RuntimeError, ValueError):
                lebt = False
            if not lebt:
                return False
            if time.monotonic() >= frist:
                return True
            time.sleep(0.5)

    def _prozesse_beenden(self, pane_pid: int) -> None:
        """Alten Prozessbaum des Panes beenden: SIGHUP, bis 5 s warten, SIGTERM, nach
        weiteren 5 s SIGKILL — sonst liefe dieselbe Session doppelt."""
        baum = prozess_baum(pane_pid) if pane_pid else []
        for signal in (1, 15, 9):
            baum = [pid for pid in baum if _prozess_lebt(pid)]
            if not baum:
                return
            if signal != 1:
                log.warning(
                    "Pane %s: %d Prozesse überlebt, Signal %d",
                    pane_pid,
                    len(baum),
                    signal,
                )
            for pid in reversed(baum):
                try:
                    os.kill(pid, signal)
                except ProcessLookupError:
                    continue
            for _ in range(50):
                if not any(_prozess_lebt(pid) for pid in baum):
                    return
                time.sleep(0.1)

    def _fenster_schliessen(
        self, ziel: str, freigabe: Freigabe, pane_pid: int = 0
    ) -> None:
        """Einzige Stelle, die ein Fenster schließt — nur mit gültiger Freigabe."""
        if not isinstance(freigabe, Freigabe):
            raise TypeError("Fenster schließen braucht eine Freigabe")
        log.info(
            "Fenster %s schließen (%s, Sicherung %s)",
            ziel,
            freigabe.grund,
            freigabe.sicherung,
        )
        if self.e.trocken:
            print(f"[trocken] Fenster {ziel} schließen ({freigabe.grund})")
            return
        self.tmux("kill-window", "-t", ziel)
        self._prozesse_beenden(pane_pid)

    def _fenster_fortsetzen(
        self,
        ziel: str,
        freigabe: Freigabe,
        cwd: Path,
        befehl: str,
        pane_pid: int = 0,
    ) -> None:
        """Einzige Stelle, die ein Fenster neu bestückt — nur mit gültiger Freigabe.

        tmux-Respawn (``-k``) behält Fenster, Index und Name; die Sitzung stirbt nie,
        auch wenn es das letzte Fenster ist. Vorher werden die Prozesse des alten
        Baums beendet (``_prozesse_beenden``).
        """
        if not isinstance(freigabe, Freigabe) or not freigabe.session_id:
            raise TypeError("Fenster fortsetzen braucht eine Freigabe mit Gesprächs-ID")
        log.info("Fenster %s fortsetzen (%s): %s", ziel, freigabe.grund, befehl)
        if self.e.trocken:
            print(f"[trocken] Fenster {ziel} fortsetzen: {befehl}")
            return
        # Das Fenster muss den Tod seines Prozesses überleben, sonst ist es weg,
        # bevor der neue Befehl hineinkommt.
        self.tmux("set-option", "-w", "-t", ziel, "remain-on-exit", "on")
        try:
            self._prozesse_beenden(pane_pid)
            self.tmux(
                "respawn-window",
                "-k",
                "-t",
                ziel,
                "-c",
                str(cwd),
                f"bash -lc {shlex.quote(befehl)}",
            )
        finally:
            self.tmux("set-option", "-w", "-t", ziel, "-u", "remain-on-exit")

    def _fortsetzen_nachweisen(self, f: Fenster, sid: str) -> bool:
        """Bis ``NACHWEIS_S`` prüfen, ob im Fenster wieder eine Session mit
        ``sessions/<pid>.json`` und derselben ID läuft."""
        frist = time.monotonic() + NACHWEIS_S
        while True:
            try:
                pane_pid = int(
                    self.tmux(
                        "display-message", "-p", "-t", f.ziel, "#{pane_pid}"
                    ).strip()
                    or 0
                )
            except (RuntimeError, ValueError):
                pane_pid = 0
            if pane_pid:
                info = session_finden(pane_pid, f.window_id, f.pane_id, self.e.heim)
                if info and info.aus_json and info.session_id == sid:
                    return True
            if time.monotonic() >= frist:
                return False
            time.sleep(1)

    def anstupsen(self, ziel: str, still_min: float) -> None:
        text = ANSTUPS_TEXT.format(min=int(still_min))
        if self.e.trocken:
            print(f"[trocken] {ziel}: anstupsen")
            return
        self.tmux("send-keys", "-t", ziel, "-l", text)
        time.sleep(1)
        self.tmux("send-keys", "-t", ziel, "Enter")
        time.sleep(1)

    def sichern_worktree(
        self, repo: Path, ticket: int, cwd: Path | None = None
    ) -> str | None:
        """Sicherung des Ticket-Worktrees; gibt es keinen oder ist er sauber → None."""
        worktree = worktree_finden(repo, ticket, cwd)
        if worktree is None:
            log.info("#%s: kein Worktree im Repo %s — nichts zu sichern", ticket, repo)
            return None
        if self.e.trocken:
            print(f"[trocken] #{ticket}: Worktree {worktree} würde gesichert")
            return None
        return sichern(worktree, ticket, self.jetzt)

    def vor_eingriff(self, f: Fenster, hash_start: str) -> bool:
        """Direkt vor jedem Eingriff neu lesen: Text anders als zu Laufbeginn oder
        arbeitend ⇒ Kette abbrechen (False)."""
        if self.e.verzoegerung_s:
            time.sleep(self.e.verzoegerung_s)
        deploy = self.deploy_laeuft(self.muster)
        if deploy:
            log.info(
                "%s: Deploy/Gate inzwischen gestartet (%s) — kein Eingriff",
                f.name,
                deploy[0],
            )
            return False
        text = self.pane_text(f.ziel)
        info = self.session(f)
        if pane_hash(text) != hash_start or arbeitet(
            text, info.status if info else None
        ):
            log.info("%s: inzwischen aktiv — kein Eingriff", f.name)
            e = self.stand.fenster.get(f"{f.sitzung}/{f.name}")
            if e is not None and pane_hash(text) != hash_start:
                e["hash"], e["seit"] = pane_hash(text), self.jetzt
                if e.get("stufe", 0):
                    e["stufe"] = 0
            return False
        return True

    # -- Regeln je Fenster -------------------------------------------------------

    def eintrag(self, schluessel: str, text: str) -> dict:
        """Hash/Zeit fortschreiben. Ändert sich der Text nach einem Eingriff
        (``stufe`` > 0), hat der Eingriff gewirkt: Stufe 0 — kein Zeit-Reset."""
        h = pane_hash(text)
        e = self.stand.fenster.get(schluessel) or {
            "hash": h,
            "seit": self.jetzt,
            "stufe": 0,
        }
        e.setdefault("stufe", 0)
        if e.get("hash") != h:
            e["hash"], e["seit"] = h, self.jetzt
            if e["stufe"]:
                log.info(
                    "%s: Text geändert nach Eingriff (Stufe %s) — Kette zurück auf 0",
                    schluessel,
                    e["stufe"],
                )
                e["stufe"] = 0
        self.stand.fenster[schluessel] = e
        return e

    def hashes_fortschreiben(self, sitzung: str, fenster: list[Fenster]) -> None:
        for f in fenster:
            self.eintrag(f"{sitzung}/{f.name}", self.pane_text(f.ziel))

    def _braucht_david(
        self,
        e: dict,
        f: Fenster,
        repo: Path,
        schluessel: str,
        ereignis: str,
        text: str,
    ) -> None:
        """Kette beenden: Stufe 3 (nichts mehr anfassen) + einmalige Meldung (R7)."""
        e.update(stufe=3, eingriff=self.jetzt)
        self.melden(f.spec, repo, schluessel, ereignis, text)

    def _stupser_zaehlen(self, e: dict) -> list[float]:
        """Anstupser der letzten 24 h (R5) — ältere fallen heraus."""
        stupser = [
            float(t)
            for t in e.get("stupser") or []
            if self.jetzt - float(t) < STUPSER_FENSTER_S
        ]
        e["stupser"] = stupser
        return stupser

    def fenster_pruefen(self, f: Fenster, repo: Path, offen: set[int]) -> None:
        schluessel = f"{f.sitzung}/{f.name}"
        text = self.pane_text(f.ziel)
        info = self.session(f)
        status = info.status if info else None
        e = self.eintrag(schluessel, text)
        hash_start = e["hash"]
        still_min = (self.jetzt - e["seit"]) / 60
        ticket = f.ticket
        arbeitend = arbeitet(text, status)

        if (
            ticket is not None
            and ticket not in offen
            and not arbeitend
            and still_min >= KARENZ_MIN
        ):
            try:
                zu = self.ticket_zu(ticket, repo)
            except RuntimeError as fehler:
                log.error("%s: Ticket-Zustand nicht lesbar: %s", f.name, fehler)
                zu = False
            if zu:
                self.ticket_zu_schliessen(f, ticket, repo, schluessel, hash_start, info)
                return

        if rueckfrage(text):
            if still_min >= RUECKFRAGE_MIN:
                self.melden(
                    f.spec,
                    repo,
                    schluessel,
                    "rueckfrage",
                    f"„{f.name}“ wartet seit {int(still_min)} min auf eine Ja/Nein-Bestätigung — braucht David.",
                )
            return
        if arbeitend or still_min < self.e.hang_min:
            return
        if ticket is None and not f.ist_wache:
            return

        stufe = int(e.get("stufe", 0))
        # Session beendet, nur noch die Shell im Pane (R2): nie anstupsen — der
        # Text liefe als Befehl. Kette gleich bei Stufe 1 beginnen, ID aus der Datei.
        if stufe < 2 and info is None and session_beendet(f.pane_pid):
            gemerkt = self.resume_aus_datei(repo, f.sessions_name)
            if gemerkt is None:
                self._braucht_david(
                    e,
                    f,
                    repo,
                    schluessel,
                    "session_beendet",
                    f"„{f.name}“: Session beendet, keine Gesprächs-ID — braucht David.",
                )
                return
            sid, cwd = gemerkt
            self.sichern_und_fortsetzen(
                f,
                ticket,
                repo,
                schluessel,
                e,
                hash_start,
                SessionInfo(sid, cwd, None, 0, aus_json=False),
            )
            return
        if stufe == 0:
            if len(self._stupser_zaehlen(e)) >= STUPSER_MAX:
                self._braucht_david(
                    e,
                    f,
                    repo,
                    schluessel,
                    "stupser_erschoepft",
                    f"„{f.name}“ wurde {STUPSER_MAX}× angestupst und wird immer wieder still — braucht David.",
                )
                return
            if not self.vor_eingriff(f, hash_start):
                return
            self.anstupsen(f.ziel, still_min)
            if not self.e.trocken:
                nachher = self.pane_text(f.ziel)
                e.update(hash=pane_hash(nachher))
                e["stupser"].append(self.jetzt)
            e.update(stufe=1, eingriff=self.jetzt, seit=self.jetzt)
            self.melden(
                f.spec,
                repo,
                schluessel,
                "angestupst",
                f"„{f.name}“ stand {int(still_min)} min still, angestupst.",
            )
        elif stufe == 1:
            self.sichern_und_fortsetzen(
                f, ticket, repo, schluessel, e, hash_start, info
            )
        elif stufe == 2:
            self._braucht_david(
                e,
                f,
                repo,
                schluessel,
                "braucht_david",
                f"„{f.name}“ reagiert nach Anstupsen und Fortsetzen nicht — braucht David.",
            )

    def ticket_zu_schliessen(
        self,
        f: Fenster,
        ticket: int,
        repo: Path,
        schluessel: str,
        hash_start: str,
        info: SessionInfo | None,
    ) -> None:
        try:
            sha = self.sichern_worktree(repo, ticket, info.cwd if info else None)
        except RuntimeError as fehler:
            log.error(
                "%s: Sicherung vor dem Schließen fehlgeschlagen: %s", f.name, fehler
            )
            self.melden(
                f.spec,
                repo,
                schluessel,
                "sicherung_fehlgeschlagen",
                f"„{f.name}“: Ticket zu, aber Sicherung fehlgeschlagen — braucht David.",
            )
            return
        if not self.vor_eingriff(f, hash_start):
            return
        self._fenster_schliessen(f.ziel, freigabe_ticket_zu(ticket, sha), f.pane_pid)
        if not self.e.trocken:
            self.stand.fenster.pop(schluessel, None)
        zusatz = (
            f" Änderungen auf sicherung/{ticket} gesichert ({sha[:7]})." if sha else ""
        )
        self.melden(
            f.spec,
            repo,
            schluessel,
            "geschlossen",
            f"„{f.name}“ geschlossen (Ticket zu).{zusatz}",
        )

    def sichern_und_fortsetzen(
        self,
        f: Fenster,
        ticket: int | None,
        repo: Path,
        schluessel: str,
        e: dict,
        hash_start: str,
        info: SessionInfo | None,
    ) -> None:
        """Stufe 1 → 2: Worktree sichern (nur ``bau <N>``; der Wächter hat keinen),
        Fenster mit ``--resume <id>`` fortsetzen, Nachweis (R1: auch ``wache <S>``)."""
        if info is None:
            self._braucht_david(
                e,
                f,
                repo,
                schluessel,
                "braucht_david",
                f"„{f.name}“ weiter still, aber keine Gesprächs-ID gefunden — braucht David.",
            )
            return
        sid = info.session_id
        sha: str | None = None
        if ticket is not None:
            try:
                sha = self.sichern_worktree(repo, ticket, info.cwd)
            except RuntimeError as fehler:
                log.error(
                    "%s: Sicherung fehlgeschlagen, Fenster bleibt: %s", f.name, fehler
                )
                self._braucht_david(
                    e,
                    f,
                    repo,
                    schluessel,
                    "sicherung_fehlgeschlagen",
                    f"„{f.name}“ weiter still — Sicherung fehlgeschlagen — braucht David.",
                )
                return
        if not self.vor_eingriff(f, hash_start):
            return
        cwd = info.cwd if info.cwd and info.cwd.is_dir() else repo
        if ticket is not None:
            befehl = self.bau_befehl(repo, ticket, resume=sid)
        else:
            befehl = self.wache_befehl(repo, f.spec, resume=sid)
        try:
            self._fenster_fortsetzen(
                f.ziel, freigabe_gesichert(sid, sha), cwd, befehl, f.pane_pid
            )
        except RuntimeError as fehler:
            # Prozess ist womöglich schon beendet, das Fenster aber leer: nicht
            # noch einmal versuchen, sondern David rufen (Stufe 3).
            log.error("Fenster %s fortsetzen fehlgeschlagen: %s", f.ziel, fehler)
            self._braucht_david(
                e,
                f,
                repo,
                schluessel,
                "fortsetzen_fehlgeschlagen",
                f"„{f.name}“: Fortsetzen fehlgeschlagen (tmux: {fehler}) — braucht David.",
            )
            return
        if ticket is None:
            gesichert = "nichts zu sichern (Wächter ohne Worktree)"
        elif sha:
            gesichert = f"Änderungen auf sicherung/{ticket} gesichert ({sha[:7]})"
        else:
            gesichert = "nichts zu sichern"
        if not self.e.trocken and not self._fortsetzen_nachweisen(f, sid):
            self._braucht_david(
                e,
                f,
                repo,
                schluessel,
                "fortsetzen_fehlgeschlagen",
                f"„{f.name}“: {gesichert}, aber Fortsetzen fehlgeschlagen "
                f"(Session {sid[:8]} nicht wieder da) — braucht David.",
            )
            return
        if not self.e.trocken:
            e.update(hash=pane_hash(self.pane_text(f.ziel)))
        e.update(stufe=2, eingriff=self.jetzt, seit=self.jetzt)
        self.melden(
            f.spec,
            repo,
            schluessel,
            "fortgesetzt",
            f"„{f.name}“ nach Anstupsen weiter still — {gesichert}, Session {sid[:8]}… fortgesetzt.",
        )

    # -- Ein Lauf ----------------------------------------------------------------

    def start_erlaubt(self, spec: str, repo: Path, ticket: int | None) -> bool:
        """Regel 1: kein Start bei Stopp-Label oder wenn das Fenster in 6 h schon
        ``START_MAX``-mal gestartet wurde (es verschwindet offenbar wieder).
        ``ticket`` None = Wächter-Fenster (kein Label, Start-Gedächtnis ``wache-<S>``)."""
        name = f"bau {ticket}" if ticket is not None else f"wache {spec}"
        if ticket is not None:
            try:
                daten = self.ticket_lesen(ticket, repo)
            except RuntimeError as fehler:
                log.error("#%s: Ticket nicht lesbar, kein Start: %s", ticket, fehler)
                return False
            gesperrt = daten["labels"] & STOPP_LABELS
            if gesperrt:
                log.info(
                    "#%s: Label %s — kein Start", ticket, ", ".join(sorted(gesperrt))
                )
                return False
        starts = [
            t
            for t in self.stand.starts.get(self._start_schluessel(spec, ticket), [])
            if self.jetzt - t < START_FENSTER_S
        ]
        if len(starts) >= START_MAX:
            wer = f"#{ticket}" if ticket is not None else f"„{name}“"
            self.melden(
                spec,
                repo,
                f"spec-{spec}/{name}",
                "startet_nicht",
                f"{wer} startet nicht (Fenster verschwindet wieder) — braucht David.",
            )
            return False
        return True

    @staticmethod
    def _start_schluessel(spec: str, ticket: int | None) -> str:
        return str(ticket) if ticket is not None else f"wache-{spec}"

    def fehlendes_fenster(
        self, sitzung: str, spec: str, repo: Path, ticket: int | None
    ) -> None:
        """Regel 1 für ein Fenster: liegt ``.to-spawn/sessions/<name>.json`` samt
        Transkript, geht es mit ``--resume`` weiter (R2), sonst frisch. Erst nach
        dem Start-Nachweis (R6) gilt es als gestartet."""
        name = f"bau {ticket}" if ticket is not None else f"wache {spec}"
        datei_name = str(ticket) if ticket else f"wache-{spec}"
        gemerkt_roh = sessions_datei.lesen(repo, datei_name)
        if gemerkt_roh and session_laeuft_schon(
            str(gemerkt_roh.get("session_id") or ""), self.e.heim
        ):
            # Gespräch lebt woanders (andere tmux-Sitzung, von Hand fortgesetzt):
            # kein zweites Fenster, sonst zwei Sessions auf demselben Ticket.
            log.info(
                "%s: „%s“ läuft außerhalb dieser Sitzung — kein Start", sitzung, name
            )
            return
        gemerkt = self.resume_aus_datei(repo, datei_name)
        sid, cwd = gemerkt if gemerkt else (None, repo)
        if ticket is not None:
            befehl = self.bau_befehl(repo, ticket, resume=sid)
        else:
            befehl = self.wache_befehl(repo, spec, resume=sid)
        # Speicher-Schutz (#257 Paket B): kein Start, wenn RAM knapp oder die Obergrenze
        # an Claude-Sessions erreicht ist — der nächste Tick prüft neu. Zwischen zwei
        # echten Starts in einem Tick liegt die Staffel-Pause (nicht im Trockenlauf).
        konfig = config.lade(repo)
        frei, grund = speicher.platz_frei(konfig)
        if not frei:
            log.warning("%s: Fenster „%s“ nicht gestartet — %s", sitzung, name, grund)
            if self.e.trocken:
                print(f"[trocken] {sitzung}: Fenster „{name}“ nicht gestartet — {grund}")
            return
        if self._starts_in_tick > 0 and not self.e.trocken:
            pause = speicher.staffel_s(konfig)
            log.info("%s: Staffel — %d s Pause vor Fenster „%s“", sitzung, pause, name)
            time.sleep(pause)
        try:
            self.fenster_starten(sitzung, name, cwd, befehl)
        except RuntimeError as fehler:
            log.error("%s: Fenster „%s“ nicht startbar: %s", sitzung, name, fehler)
            return
        if not self.e.trocken:
            self._starts_in_tick += 1
        wer = f"#{ticket}" if ticket is not None else "Wächter-Fenster"
        was = "hatte keine Session" if ticket is not None else "fehlte"
        if not self.e.trocken:
            self.stand.starts.setdefault(
                self._start_schluessel(spec, ticket), []
            ).append(self.jetzt)
        if not self.e.trocken and not self._start_nachweisen(sitzung, name):
            self.melden(
                spec,
                repo,
                f"{sitzung}/{name}",
                "start_fehlgeschlagen",
                f"{wer}: Start fehlgeschlagen (Fenster gleich wieder weg).",
            )
            return
        wie = f"mit Gespräch {sid[:8]}… fortgesetzt." if sid else "gestartet."
        self.melden(spec, repo, f"{sitzung}/{name}", "gestartet", f"{wer} {was}, {wie}")

    def sitzung_pruefen(
        self, sitzung: str, spec: str, fenster: list[Fenster], repo: Path
    ) -> None:
        try:
            offen = self.offene_tickets(spec, repo)
        except (RuntimeError, ValueError) as fehler:
            log.error("%s: GitHub nicht lesbar: %s", sitzung, fehler)
            return
        vorhanden = {f.name for f in fenster}
        log.info(
            "%s: %d Fenster (%s), offene Tickets %s",
            sitzung,
            len(fenster),
            ", ".join(sorted(vorhanden)),
            sorted(offen) or "-",
        )
        for ticket in sorted(offen):
            if f"bau {ticket}" in vorhanden:
                continue
            if not self.start_erlaubt(spec, repo, ticket):
                continue
            self.fehlendes_fenster(sitzung, spec, repo, ticket)
        # Wächter-Fenster fehlt (R2): nachstarten, solange die Spec offene Tickets
        # hat — ist sie fertig, hat der Wächter sich regulär beendet.
        if (
            offen
            and f"wache {spec}" not in vorhanden
            and self.start_erlaubt(spec, repo, None)
        ):
            self.fehlendes_fenster(sitzung, spec, repo, None)
        for f in fenster:
            try:
                self.fenster_pruefen(f, repo, offen)
            except RuntimeError as fehler:
                log.error("%s: Fenster „%s“ übersprungen: %s", sitzung, f.name, fehler)

    def _sperren(self) -> object | None:
        """Lauf-Sperre ``<zustand>/lock`` (flock, nicht blockierend). Belegt ⇒ None."""
        if self.e.trocken:
            return object()
        self.e.zustand.mkdir(parents=True, exist_ok=True)
        datei = open(self.e.zustand / "lock", "w", encoding="utf-8")  # noqa: SIM115
        try:
            fcntl.flock(datei, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            datei.close()
            return None
        return datei

    def lauf(self) -> int:
        sperre = self._sperren()
        if sperre is None:
            log.info("Aufpasser läuft schon (Sperre belegt) — dieser Lauf endet")
            return 0
        code = 0
        try:
            self._lauf()
        except RuntimeError as fehler:
            # Werkzeug-Störung (z. B. pgrep der Deploy-Wache): Lauf endet ohne
            # Eingriff — lieber einmal nichts tun als blind eingreifen.
            log.error("Lauf abgebrochen, nichts angefasst: %s", fehler)
            code = 1
        finally:
            self._stand_schreiben()
            if hasattr(sperre, "close"):
                sperre.close()
        return code

    def _lauf(self) -> None:
        self._starts_in_tick = 0
        gesehen: list[tuple[str, str, list[Fenster], Path]] = []
        for sitzung, spec in self.sitzungen():
            try:
                fenster = self.fenster(sitzung, spec)
            except RuntimeError as fehler:
                log.error("%s: Fenster nicht lesbar: %s", sitzung, fehler)
                continue
            repo = self.repo_ordner(fenster)
            if repo is None:
                log.warning("%s: kein Repo-Ordner gefunden — übersprungen", sitzung)
                continue
            gesehen.append((sitzung, spec, fenster, repo))
        self.muster = self.deploy_muster_fuer({repo for _, _, _, repo in gesehen})
        deploy = self.deploy_laeuft(self.muster)
        if deploy:
            log.info("Deploy/Gate läuft (%s): nichts angefasst", "; ".join(deploy[:3]))
            if self.e.trocken:
                print(f"[trocken] Deploy/Gate läuft ({deploy[0]}): nichts angefasst")
            for sitzung, _, fenster, _ in gesehen:
                try:
                    self.hashes_fortschreiben(sitzung, fenster)
                except RuntimeError as fehler:
                    log.error("%s: Fenster nicht lesbar: %s", sitzung, fehler)
            return
        for sitzung, spec, fenster, repo in gesehen:
            try:
                self.sitzung_pruefen(sitzung, spec, fenster, repo)
            except RuntimeError as fehler:
                log.error("%s: Sitzung übersprungen: %s", sitzung, fehler)


# --- Kommandozeile -------------------------------------------------------------


def parser_fuellen(ap: argparse.ArgumentParser) -> argparse.ArgumentParser:
    ap.add_argument(
        "--trocken", action="store_true", help="nur zeigen, nichts ausführen"
    )
    ap.add_argument(
        "--tmux-socket", help="tmux-Socket-Name (-L); Vorgabe: Standard-Server"
    )
    ap.add_argument(
        "--zustand",
        type=Path,
        default=None,
        help=f"Zustandsordner ({zustand_standard()})",
    )
    ap.add_argument(
        "--hang-min",
        type=float,
        default=HANG_MIN,
        help=f"Minuten Stille ({HANG_MIN}; nie unter {HANG_MIN_UNTERGRENZE})",
    )
    ap.add_argument(
        "--bau-vorlage",
        help="Startbefehl mit {repo} {py} {n} {resume} (Vorgabe: scripts/bau.py bzw. Skill)",
    )
    ap.add_argument(
        "--wache-vorlage",
        help="Startbefehl des Wächter-Fensters mit {repo} {py} {s} {resume} (Vorgabe: scripts/wache.py bzw. Skill)",
    )
    ap.add_argument(
        "--deploy-muster", default=DEPLOY_MUSTER, help="Regex für pgrep -af"
    )
    ap.add_argument(
        "--repo", type=Path, help="Repo-Ordner, falls kein Fenster ihn verrät"
    )
    ap.add_argument(
        "--cron-einrichten",
        action="store_true",
        help="Cron-Zeile (alle 15 min) anlegen/ersetzen",
    )
    return ap


def _logging_einrichten(zustand: Path, trocken: bool) -> logging.Handler:
    """Log in ``<zustand>/aufpasser.log`` — im Trockenlauf auf stderr (kein Ordner)."""
    handler: logging.Handler
    if trocken:
        handler = logging.StreamHandler(sys.stderr)
    else:
        zustand.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(zustand / "aufpasser.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return handler


def lauf_mit_einstellungen(e: Einstellungen) -> int:
    handler = _logging_einrichten(e.zustand, e.trocken)
    try:
        return Aufpasser(e).lauf()
    except Exception:  # Cron darf nie still sterben — jede Überraschung ins Log.
        log.exception("Aufpasser-Lauf abgebrochen")
        return 1
    finally:
        log.removeHandler(handler)
        handler.close()


def lauf_mit_args(args: argparse.Namespace) -> int:
    zustand = args.zustand or zustand_standard()
    if args.cron_einrichten:
        handler = _logging_einrichten(zustand, args.trocken)
        try:
            print(cron_einrichten(zustand, trocken=args.trocken))
            return 0
        except RuntimeError as fehler:
            log.error("Cron einrichten: %s", fehler)
            return 1
        finally:
            log.removeHandler(handler)
            handler.close()
    try:
        einstellungen = Einstellungen(
            zustand=zustand,
            tmux_socket=args.tmux_socket,
            hang_min=args.hang_min,
            bau_vorlage=args.bau_vorlage,
            wache_vorlage=args.wache_vorlage,
            deploy_muster=args.deploy_muster,
            repo=args.repo.resolve() if args.repo else None,
            trocken=args.trocken,
        )
    except ValueError as fehler:  # z. B. --hang-min unter 61 über to_spawn.py
        print(f"aufpasser: {fehler}", file=sys.stderr)
        return 2
    return lauf_mit_einstellungen(einstellungen)


def main(argv: list[str] | None = None) -> int:
    ap = parser_fuellen(
        argparse.ArgumentParser(description="Aufpasser für Bau-Sessions im tmux.")
    )
    args = ap.parse_args(argv)
    if args.hang_min < HANG_MIN_UNTERGRENZE:
        ap.error(
            f"--hang-min {args.hang_min:g} liegt unter {HANG_MIN_UNTERGRENZE} "
            "(Wakeups wartender Sessions kommen alle ≤ 60 min)"
        )
    return lauf_mit_args(args)


if __name__ == "__main__":
    sys.exit(main())
