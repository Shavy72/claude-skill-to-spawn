#!/usr/bin/env python3
"""Stop-Hook der Staffel: frischer Handoff + offenes Ticket → Session beenden.

Läuft nur in Sessions, die ``scripts/bau.py`` gestartet hat — der Launcher legt
den Hook in die Session-eigene ``settings.json`` und setzt die Umgebung:

* ``BAU_TICKET``        — Ticket-Nummer der Session
* ``BAU_STAFFEL_DATEI`` — Datei, an der ``bau.py`` die Übergabe erkennt
* ``BAU_SESSION_START`` — Unix-Zeit des Rundenstarts (Handoff muss jünger sein)
* ``BAU_HANDOFF_DIRS``  — Verzeichnisse mit ``HANDOFF_*_<N>.md`` (os.pathsep)
* ``BAU_STAFFEL_RUNDE`` — laufende Runde (nur fürs Protokoll)
* ``BAU_STAFFEL_FINGERABDRUCK`` — Inhalt des schon übergebenen Handoffs (verhindert Dauer-Staffel)
* ``BAU_LAUNCHER_PID`` — Obergrenze der Vorfahren-Suche (nie darüber hinaus töten)

Ein Handoff allein ist kein Auftrag: die Session muss die Übergabe im Handoff
ausdrücklich verlangen (Zeile ``Staffel: weiter``). Sonst würde jeder Vorsorge-
oder Zwischenstand-Handoff die Session am Ende ihres nächsten Zuges abschießen —
Stop-Hooks laufen nach JEDEM Zug, nicht nur am Sitzungsende.

Fehlt eine dieser Angaben, hält sich der Hook vollständig heraus. Jede
Unklarheit (kein Handoff, Ticket zu, gh unerreichbar, kein claude-Vorfahr)
endet ohne Kill und ohne Staffel-Datei: eine Session zu lange laufen zu lassen
kostet Token, eine falsch abgeschossene kostet Arbeit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s staffel_stop: %(message)s")
log = logging.getLogger("staffel_stop")

MAX_VORFAHREN = 12
# Frist, die eine Session zum geordneten Beenden bekommt, bevor SIGKILL folgt.
NACHLAUF_SEKUNDEN = 20.0
# Ausdrücklicher Übergabe-Wunsch im Handoff — tolerant gegen Fettschrift.
STAFFEL_MARKER = re.compile(r"^\s*\**\s*staffel\s*\**\s*:\s*\**\s*weiter", re.IGNORECASE | re.MULTILINE)
# Prozesse, die nie die Session sind (Hook-Wrapper, Launcher, Terminal-Multiplexer).
WRAPPER_NAMEN = ("sh", "bash", "dash", "zsh", "env")
NIE_SESSION = ("bau.py", "staffel_stop.py")


def handoff_dirs_aus_umgebung() -> list[Path]:
    rohwert = os.environ.get("BAU_HANDOFF_DIRS") or ""
    return [Path(teil) for teil in rohwert.split(os.pathsep) if teil.strip()]


def fingerabdruck(datei: Path) -> str:
    """SHA-256 des Handoff-Inhalts — Wiedererkennung ohne Verlass auf die Uhr."""
    try:
        return hashlib.sha256(datei.read_bytes()).hexdigest()
    except OSError:
        return ""


def handoff_muster(ticket: str) -> re.Pattern[str]:
    """``HANDOFF_<datum>_<ticket>.md`` — und sonst nichts.

    Ein reines Glob ``HANDOFF_*_<N>.md`` würde auch ``HANDOFF_2026-09-18_waechter_192.md``
    treffen: der Wächter einer Spec 192 hätte die Bau-Session von Ticket 192 beendet.
    """
    return re.compile(rf"^HANDOFF_\d{{4}}-\d{{2}}-\d{{2}}_{re.escape(ticket)}\.md$")


def frischer_handoff(ordner: list[Path], ticket: str, seit: float) -> Path | None:
    """Jüngste Handoff-Datei dieses Tickets, die nach ``seit`` geschrieben wurde."""
    muster = handoff_muster(ticket)
    schon_uebergeben = os.environ.get("BAU_STAFFEL_FINGERABDRUCK") or ""
    treffer: list[tuple[float, Path]] = []
    for verzeichnis in ordner:
        for datei in verzeichnis.glob(f"HANDOFF_*_{ticket}.md"):
            if not muster.match(datei.name):
                continue
            try:
                stand = datei.stat().st_mtime
                inhalt = datei.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if stand <= seit:
                continue
            if not STAFFEL_MARKER.search(inhalt):
                continue
            if schon_uebergeben and fingerabdruck(datei) == schon_uebergeben:
                # Dieselbe Datei wie in der Vorrunde: ein fremdes git checkout im
                # Baum setzt die mtime auf jetzt, ohne dass jemand übergeben will.
                continue
            treffer.append((stand, datei))
    if not treffer:
        return None
    return max(treffer, key=lambda paar: paar[0])[1]


def ticket_offen(ticket: str) -> bool:
    """``True`` nur bei belegt offenem Issue — jeder Fehler gilt als „nicht offen"."""
    gh = shutil.which("gh")
    if gh is None:
        log.warning("gh fehlt — Ticket-Zustand unbekannt, keine Staffel")
        return False
    proc = subprocess.run(
        [gh, "issue", "view", ticket, "--json", "state"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        log.warning("gh issue view %s: %s", ticket, proc.stderr.strip()[:200])
        return False
    try:
        zustand = (json.loads(proc.stdout or "{}") or {}).get("state")
    except json.JSONDecodeError:
        log.warning("gh-Antwort unlesbar — keine Staffel")
        return False
    return str(zustand).upper() == "OPEN"


def _cmdline(pid: int) -> str:
    try:
        rohwert = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return rohwert.replace(b"\x00", b" ").decode("utf-8", "replace")


def _ppid(pid: int) -> int | None:
    try:
        zeilen = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for zeile in zeilen.splitlines():
        if zeile.startswith("PPid:"):
            try:
                return int(zeile.split()[1])
            except (IndexError, ValueError):
                return None
    return None


def ist_session_zeile(zeile: str) -> bool:
    """Ist diese Kommandozeile die Claude-Session selbst?

    Ein Teilstring-Treffer auf „claude" genügt nicht: der direkte Vorfahr des
    Hooks ist ``/bin/sh -c <hook-befehl>``, und dieser Befehl kann „claude" im
    Pfad tragen (``~/.claude/…``). Getroffen wird nur ein Programm, das
    ``claude`` heißt oder im Paket ``claude-code`` liegt.
    """
    teile = zeile.split()
    if not teile:
        return False
    if any(marke in zeile for marke in NIE_SESSION):
        return False
    if Path(teile[0]).name in WRAPPER_NAMEN and "-c" in teile[1:3]:
        return False
    for teil in teile:
        if teil.startswith("-"):
            continue
        pfad = Path(teil)
        if pfad.name.startswith("claude"):
            return True
        if "claude-code" in pfad.parts:
            return True
    return False


def claude_vorfahr(pid: int) -> int | None:
    """Erster Vorfahr, dessen Kommandozeile die Session ist (enthält ``claude``).

    Der Launcher selbst (``bau.py``) darf nie getroffen werden — er startet die
    Folge-Session. Deshalb wird die Kette abgebrochen, sobald ``bau.py``
    auftaucht, und ohne Treffer ``None`` zurückgegeben.
    """
    grenze = os.environ.get("BAU_LAUNCHER_PID") or ""
    aktuell = _ppid(pid)
    for _ in range(MAX_VORFAHREN):
        if aktuell is None or aktuell <= 1:
            return None
        if grenze and str(aktuell) == grenze:
            # Oberhalb des Launchers liegen fremde Sessions (z. B. die Session,
            # aus der bau.py gestartet wurde). Dort wird nie gesucht.
            return None
        zeile = _cmdline(aktuell)
        if "bau.py" in zeile:
            return None
        if ist_session_zeile(zeile):
            return aktuell
        aktuell = _ppid(aktuell)
    return None


def beenden(pid: int) -> bool:
    """SIGTERM an die Session — und einen abgekoppelten Nachläufer für den Notnagel.

    Der Hook darf hier NICHT auf den Tod der Session warten: Claude Code führt
    Stop-Hooks synchron aus und läuft erst nach dem Hook weiter, die Session
    käme also gar nicht dazu, das Signal zu verarbeiten (im Weg-Test
    18.09.2026 endete jede Session dadurch mit SIGKILL/Exit -9 statt
    kontrolliert). Die Eskalation läuft deshalb in einem eigenen Prozess mit
    eigener Sitzung, damit sie das Sterben der Session überlebt.
    """
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        log.warning("SIGTERM an PID %d fehlgeschlagen: %s — keine Staffel", pid, exc)
        return False
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--nachlauf", str(pid)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.warning("Nachläufer für PID %d nicht gestartet: %s", pid, exc)
    return True


def nachlauf(pid: int, frist: float = NACHLAUF_SEKUNDEN) -> int:
    """Wartet ``frist`` Sekunden und schickt SIGKILL, falls die Session noch lebt."""
    ende = time.monotonic() + frist
    while time.monotonic() < ende:
        if not Path(f"/proc/{pid}").exists():
            return 0
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return 0
    return 0


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--nachlauf":
        return nachlauf(int(sys.argv[2]))
    try:
        eingabe = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        eingabe = {}
    if eingabe.get("stop_hook_active"):
        return 0

    ticket = os.environ.get("BAU_TICKET")
    staffel_datei = os.environ.get("BAU_STAFFEL_DATEI")
    start = os.environ.get("BAU_SESSION_START")
    ordner = handoff_dirs_aus_umgebung()
    if not (ticket and staffel_datei and start and ordner):
        return 0
    try:
        seit = float(start)
    except ValueError:
        return 0

    handoff = frischer_handoff(ordner, ticket, seit)
    if handoff is None:
        return 0
    if not ticket_offen(ticket):
        log.info("Ticket #%s nicht mehr offen — Session läuft normal aus", ticket)
        return 0

    ziel = claude_vorfahr(os.getpid())
    if ziel is None:
        log.warning("Kein Session-Prozess in der Vorfahren-Kette — kein Kill, keine Staffel")
        return 0

    runde = os.environ.get("BAU_STAFFEL_RUNDE") or "1"
    log.info("Handoff %s frisch — Session %d wird beendet (Runde %s)", handoff.name, ziel, runde)
    if not beenden(ziel):
        return 0
    Path(staffel_datei).parent.mkdir(parents=True, exist_ok=True)
    Path(staffel_datei).write_text(
        json.dumps(
            {
                "ticket": ticket,
                "handoff": str(handoff),
                "runde": runde,
                "erkannt_um": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "session_pid": ziel,
                "fingerabdruck": fingerabdruck(handoff),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
