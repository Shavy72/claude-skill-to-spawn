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
import subprocess
import sys
import time
from pathlib import Path


# Prozess-Wissen (Session finden, beenden — Linux und Windows) lebt im Skill-Paket.
# Liegt diese Datei nicht im Skill (alte Repo-Kopie ``scripts/hooks/``), gilt der
# installierte Skill (``$TO_SPAWN_HOME``, Vorgabe ``~/.claude/skills/to-spawn``).
def _skill_wurzel() -> Path:
    neben = Path(__file__).resolve().parents[2]
    if (neben / "to_spawn" / "prozessbaum.py").is_file():
        return neben
    return Path(os.environ.get("TO_SPAWN_HOME") or Path.home() / ".claude" / "skills" / "to-spawn")


sys.path.insert(0, str(_skill_wurzel()))
from to_spawn import prozessbaum  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s staffel_stop: %(message)s")
log = logging.getLogger("staffel_stop")

# Ausdrücklicher Übergabe-Wunsch im Handoff — tolerant gegen Fettschrift.
STAFFEL_MARKER = re.compile(r"^\s*\**\s*staffel\s*\**\s*:\s*\**\s*weiter", re.IGNORECASE | re.MULTILINE)


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
    """``HANDOFF_<datum>_<ticket>.md``, optional mit Suffix (``_runde2``, ``_r2``) — sonst nichts.

    Ein reines Glob ``HANDOFF_*_<N>.md`` würde auch ``HANDOFF_2026-09-18_waechter_192.md``
    treffen: der Aufseher einer Spec 192 hätte die Bau-Session von Ticket 192 beendet.
    """
    return re.compile(rf"^HANDOFF_\d{{4}}-\d{{2}}-\d{{2}}_{re.escape(ticket)}(_[\w-]+)?\.md$")


def frischer_handoff(ordner: list[Path], ticket: str, seit: float) -> Path | None:
    """Jüngste Handoff-Datei dieses Tickets, die nach ``seit`` geschrieben wurde."""
    muster = handoff_muster(ticket)
    schon_uebergeben = os.environ.get("BAU_STAFFEL_FINGERABDRUCK") or ""
    treffer: list[tuple[float, Path]] = []
    for verzeichnis in ordner:
        for datei in verzeichnis.glob(f"HANDOFF_*_{ticket}*.md"):
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


def ist_session_zeile(zeile: str) -> bool:
    """Ist diese Kommandozeile die Claude-Session selbst? Regel: ``prozessbaum``."""
    return prozessbaum.ist_session_zeile(zeile)


def claude_vorfahr(pid: int) -> int | None:
    """Erster Vorfahr, der die Session ist — Linux über ``/proc``, Windows über
    ``Win32_Process`` (#501).

    Der Launcher selbst (``bau.py``) darf nie getroffen werden — er startet die
    Folge-Session. Oberhalb von ``BAU_LAUNCHER_PID`` wird nie gesucht.
    """
    grenze = os.environ.get("BAU_LAUNCHER_PID") or ""
    return prozessbaum.session_vorfahr(pid, grenze=int(grenze) if grenze.isdigit() else None)


def beenden(pid: int) -> bool:
    """Session beenden, ohne auf ihren Tod zu warten.

    Der Hook darf NICHT warten: Claude Code führt Stop-Hooks synchron aus und läuft
    erst nach dem Hook weiter (Weg-Test 18.09.2026: sonst endete jede Session mit
    SIGKILL). Den Rest erledigt ein abgelöster Nachläufer in ``prozessbaum``.
    Sein Ergebnis (Exit-Code von ``taskkill`` bzw. SIGKILL) steht in
    ``<BAU_STAFFEL_DATEI>.nachlauf.log``, ein Fehlschlag als ``WARNUNG``.
    """
    staffel_datei = os.environ.get("BAU_STAFFEL_DATEI")
    protokoll = Path(f"{staffel_datei}.nachlauf.log") if staffel_datei else None
    if not prozessbaum.session_beenden(pid, protokoll):
        log.warning("Session %d nicht beendbar — keine Staffel", pid)
        return False
    return True


def main() -> int:
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
    log.info(
        "Handoff %s frisch — Session %d wird beendet (Runde %s)",
        handoff.name,
        ziel,
        runde,
    )
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
