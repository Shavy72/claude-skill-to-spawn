"""Spawn-Befehl: Regularien prüfen, Ziel erfragen, an die Terminal-Skripte geben.

Die Terminal-Arbeit machen die Skripte — hier wird nur delegiert. Windows:
``spawn_local.ps1`` (Windows Terminal) und ``spawn_srv.ps1`` (SSH zum Bau-Server).
Linux/macOS (#257, kein ``pwsh`` nötig): ``skripte/spawn_srv.sh`` (tmux) für ``local``,
``ssh <ssh_ziel> bash scripts/spawn_srv.sh`` für ``srv``.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence, TextIO

from . import capo, manifest, probesitz, stand

log = logging.getLogger("to_spawn.spawn")

SKILL_ORDNER = Path(__file__).resolve().parent.parent
SKRIPTE = {"local": "spawn_local.ps1", "srv": "spawn_srv.ps1"}
SPAWN_SH = SKILL_ORDNER / "skripte" / "spawn_srv.sh"


def frage_ziel(vorgabe: str, eingabe: TextIO | None = None) -> str:
    """„lokal (1) oder Server (2)?" — leere Antwort nimmt die Vorgabe."""
    vorbelegt = "1" if vorgabe == "local" else "2"
    print(f"lokal (1) oder Server (2)? [{vorbelegt}] ", end="", flush=True)
    strom = eingabe or sys.stdin
    antwort = (strom.readline() or "").strip()
    if not antwort:
        return vorgabe
    if antwort in ("1", "l", "lokal", "local"):
        return "local"
    if antwort in ("2", "s", "server", "srv"):
        return "srv"
    log.warning("Antwort %r nicht verstanden — Vorgabe %s gilt.", antwort, vorgabe)
    return vorgabe


def baue_befehl(
    ziel: str,
    spec: int | str,
    tickets: Sequence[str] | None,
    konfig: dict[str, Any],
    dry_run: bool,
) -> list[str]:
    """argv des Terminal-Skripts; auf Nicht-Windows ohne ``pwsh`` (#257).

    ``srv`` braucht dort ``server_repo`` aus der Konfig (Repo-Ordner auf dem Bau-Server) —
    fehlt er, ``ValueError`` mit klarer Meldung statt eines blinden SSH-Aufrufs.
    """
    if sys.platform != "win32":
        return _befehl_unix(ziel, spec, tickets, konfig, dry_run)
    skript = SKILL_ORDNER / SKRIPTE[ziel]
    pwsh = shutil.which("pwsh") or shutil.which("powershell") or "pwsh"
    befehl = [pwsh, "-File", str(skript), "-Spec", str(spec)]
    if tickets:
        befehl += ["-Tickets", ",".join(str(t) for t in tickets)]
    if ziel == "srv":
        befehl += ["-Zielserver", str(konfig.get("ssh_ziel", "bau-server"))]
    if dry_run:
        befehl.append("-DryRun")
    return befehl


def fern_ordner(pfad: str) -> str:
    """Ordner für ``cd`` in einem SSH-Befehl quoten — ``~/`` bleibt aufgelöst (``'~/x'`` würde es nicht)."""
    if pfad == "~":
        return "~"
    if pfad.startswith("~/"):
        return "~/" + shlex.quote(pfad[2:])
    return shlex.quote(pfad)


def _befehl_unix(
    ziel: str,
    spec: int | str,
    tickets: Sequence[str] | None,
    konfig: dict[str, Any],
    dry_run: bool,
) -> list[str]:
    argumente = [str(spec)]
    if tickets:
        argumente += ["--tickets", ",".join(str(t) for t in tickets)]
    if dry_run:
        argumente.append("--dry-run")
    if ziel == "local":
        return ["bash", str(SPAWN_SH), *argumente]
    server_repo = str(konfig.get("server_repo") or "").strip()
    if not server_repo:
        raise ValueError(
            "Ziel Server braucht `server_repo` in .to-spawn/config.json "
            "(Repo-Ordner auf dem Bau-Server) — oder lokal starten (Ziel 1)."
        )
    ssh_ziel = str(konfig.get("ssh_ziel") or "bau-server")
    fern = f"cd {fern_ordner(server_repo)} && bash scripts/spawn_srv.sh {shlex.join(argumente)}"
    return ["ssh", ssh_ziel, fern]


def spawn(
    repo: Path,
    spec: int | str,
    konfig: dict[str, Any],
    *,
    ziel: str | None = None,
    tickets: Sequence[str] | None = None,
    dry_run: bool = False,
    eingabe: TextIO | None = None,
) -> int:
    """Regularien prüfen, dann das passende Terminal-Skript starten."""
    bericht = manifest.pruefe(repo, spec, konfig, auswahl=tickets)
    print(bericht.text())
    if not bericht.sauber:
        return manifest.EXIT_WEIGERUNG

    gewaehlt = ziel or frage_ziel(str(konfig.get("ziel_default", "srv")), eingabe)
    if gewaehlt not in SKRIPTE:
        log.error("Unbekanntes Ziel: %s", gewaehlt)
        return 2
    try:
        befehl = baue_befehl(gewaehlt, spec, tickets, konfig, dry_run)
    except ValueError as fehler:
        log.error("%s", fehler)
        return 2
    # Lokales Skript (pwsh -File <Skript> bzw. bash <Skript>): muss auf der Platte liegen.
    if befehl[0] != "ssh":
        skript = Path(befehl[2] if befehl[0] != "bash" else befehl[1])
        if not skript.is_file():
            log.error("Terminal-Skript fehlt: %s", skript)
            return 2
    # Bau-Server muss den Skill-Stand des PCs haben, sonst selbst nachziehen (#325).
    if gewaehlt == "srv":
        stand_rc = stand.sichere_stand(repo, konfig, nur_pruefen=dry_run)
        if stand_rc != 0 and not (dry_run and stand_rc == stand.EXIT_ABWEICHEND):
            return manifest.EXIT_WEIGERUNG
    log.info("Ziel %s · %s", gewaehlt, " ".join(befehl))
    if dry_run:
        print(" ".join(befehl))
        return 0
    # Das Skript arbeitet im Repo des Aufrufs, nicht im Repo aus ~/.bashrc (#212/#257).
    umgebung = {**os.environ, "TO_SPAWN_REPO": str(repo)}
    return subprocess.run(befehl, cwd=str(repo), check=False, env=umgebung).returncode


# --- Einzelticket-Neustart (Wächter-Werkzeug) -------------------------------------------------------

#: Wartezeit, bis ein beendetes Ticket in ``sessions_stand`` als ``aus`` erscheint.
NEUSTART_WARTE_S = 60


def neustart_auftrag(handoff: str) -> str:
    """Auftrag der neuen Session: am Handoff weiterbauen (leer = normaler Start)."""
    if not handoff.strip():
        return ""
    return (
        f"Weiter ab Handoff {handoff.strip()} (Ticket-Worktree) — lies ihn zuerst "
        "und setze genau dort fort, wo die Vorsession aufgehört hat."
    )


def ticket_eintrag(spec: int | str, ticket: int | str) -> Any:
    """Zustand des Tickets aus ``skripte/sessions_stand.py`` (``aus``/``wartet``/``läuft …``/``VERWAIST …``)."""
    modul = probesitz._sessions_stand()
    eintraege = modul.manifeste_lesen(str(spec))
    modul.zuordnen(eintraege, modul.prozesse_lesen())
    return eintraege.get(str(ticket)) or modul.Eintrag(str(ticket), "ticket", "(nicht im Manifest)")


def _neustart_lokal_befehl(repo: Path, spec: int, ticket: int, auftrag: str) -> tuple[list[str], bool]:
    """(argv, über tmux?) — Linux: tmux-Fenster ``bau <N>`` wie capo; Windows: neuer wt-Tab."""
    if sys.platform != "win32":
        fenster = capo.tmux_fenster(spec)
        return capo.folge_befehl(repo, spec, ticket, fenster, auftrag), fenster is not None
    innen = f"python '{capo._bau_skript(repo)}' {ticket} --sofort"
    if auftrag:
        # wt trennt Tabs an ';' — der Auftrag darf keins enthalten; ' für pwsh verdoppeln.
        innen += " --auftrag '" + auftrag.replace(";", ",").replace("'", "''") + "'"
    return ["wt", "-w", "0", "new-tab", "--title", f"bau {ticket}", "-d", str(repo), "pwsh", "-NoExit", "-Command", innen], False


def neustart(
    repo: Path,
    spec: int,
    ticket: int,
    konfig: dict[str, Any],
    *,
    ziel: str = "local",
    handoff: str = "",
    beenden: bool = False,
    dry_run: bool = False,
) -> int:
    """Ein Ticket neu starten — Einzeiler für den Wächter.

    ``local`` = dieser Rechner (Bau-Server: tmux-Fenster in ``spec-<S>``, PC: wt-Tab),
    ``srv`` = per SSH derselbe Befehl im ``server_repo`` des Bau-Servers. Läuft das
    Ticket noch, bricht der Neustart ab (Exit 3) — außer mit ``beenden``: dann wird
    zuerst nur die Claude-Session beendet (ohne Session ``bau.py`` selbst, das dann
    nichts mehr zu tun hat), nie ein fremder Prozess.
    """
    if ziel == "srv":
        server_repo = str(konfig.get("server_repo") or "").strip()
        if not server_repo:
            log.error("Ziel Server braucht `server_repo` in .to-spawn/config.json.")
            return 2
        stand_rc = stand.sichere_stand(repo, konfig, nur_pruefen=dry_run)
        if stand_rc != 0 and not (dry_run and stand_rc == stand.EXIT_ABWEICHEND):
            return manifest.EXIT_WEIGERUNG
        argumente = ["neustart", str(spec), str(ticket), "--ziel", "local"]
        if handoff:
            argumente += ["--handoff", handoff]
        if beenden:
            argumente.append("--beenden")
        if dry_run:
            argumente.append("--dry-run")
        fern = (
            f"cd {fern_ordner(server_repo)} && "
            f"python3 ~/.claude/skills/to-spawn/to_spawn.py {shlex.join(argumente)}"
        )
        befehl = ["ssh", str(konfig.get("ssh_ziel") or "bau-server"), fern]
        print(" ".join(befehl[:2]), repr(fern))
        return subprocess.run(befehl, cwd=str(repo), check=False).returncode

    os.environ["TO_SPAWN_REPO"] = str(repo)
    eintrag = ticket_eintrag(spec, ticket)
    print(f"#{ticket}: {eintrag.zustand} (bau.py {eintrag.pid or '-'}, Session {eintrag.session_pid or '-'})")
    if eintrag.zustand != "aus":
        opfer = eintrag.session_pid or eintrag.pid
        if not beenden:
            log.error("#%s läuft noch (%s) — erst prüfen, dann mit --beenden neu starten.", ticket, eintrag.zustand)
            return 3
        if dry_run:
            print(f"würde beenden: PID {opfer} ({'Claude-Session' if eintrag.session_pid else 'bau.py ohne Session'})")
        else:
            try:
                os.kill(int(opfer), 15)
            except OSError as fehler:
                log.error("#%s: PID %s nicht beendbar (%s).", ticket, opfer, fehler)
                return 2
            ende = time.monotonic() + NEUSTART_WARTE_S
            while ticket_eintrag(spec, ticket).zustand != "aus":
                if time.monotonic() > ende:
                    log.error("#%s nach %ss noch nicht aus — kein Neustart.", ticket, NEUSTART_WARTE_S)
                    return 3
                time.sleep(2)
    auftrag = neustart_auftrag(handoff)
    befehl, ueber_tmux = _neustart_lokal_befehl(repo, spec, ticket, auftrag)
    print(("Trockenlauf: " if dry_run else "Start: ") + shlex.join(befehl))
    if dry_run:
        return 0
    if sys.platform == "win32":
        return subprocess.run(befehl, cwd=str(repo), check=False).returncode
    grund = capo._starte_folge_runde(repo, ticket, befehl, ueber_tmux, auftrag)
    if grund:
        log.error("#%s: Start fehlgeschlagen — %s", ticket, grund)
        return 2
    return 0
