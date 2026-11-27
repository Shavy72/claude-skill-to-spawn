"""Spawn-Befehl: Regularien prüfen, Ziel erfragen, an die Terminal-Skripte geben.

Die Terminal-Arbeit machen die Skripte — hier wird nur delegiert. Windows:
``spawn_local.ps1`` (Windows Terminal) und ``spawn_srv.ps1`` (SSH zum Bau-Server).
Linux/macOS (#257, kein ``pwsh`` nötig): ``skripte/spawn_srv.sh`` (tmux) für ``local``,
``ssh <ssh_ziel> … spawn_srv.sh`` für ``srv`` (Weiterleitung im Repo, sonst Skill-Skript).
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Sequence, TextIO

from . import auswahl, capo, config, manifest, probesitz, sitzung_env, spawn_exit, stand, starter, tmux_aufruf, umzug

log = logging.getLogger("to_spawn.spawn")

SKILL_ORDNER = Path(__file__).resolve().parent.parent
SKRIPTE = {"local": "spawn_local.ps1", "srv": "spawn_srv.ps1"}
SPAWN_SH = SKILL_ORDNER / "skripte" / "spawn_srv.sh"
#: Exit-Codes von ``spawn_srv.sh`` — einzige Quelle ist :mod:`to_spawn.spawn_exit` (#604).
EXIT_SPERRE = spawn_exit.EXIT_SPERRE
SPAWN_SRV_EXITS = spawn_exit.BESCHREIBUNG
_FEHLER_SERVER_REPO = (
    "Ziel Server braucht `server_repo` in .to-spawn/config.json "
    "(Repo-Ordner auf dem Bau-Server) — auf dem Server: --ziel local."
)


def ziel_aufloesen(ziel: str, konfig: dict[str, Any]) -> str:
    """Auf dem Bau-Server selbst gilt ``srv`` wie ``local`` — kein SSH auf sich selbst (#408)."""
    if ziel == "srv" and config.ist_bau_server(konfig):
        log.info("Läuft auf dem Bau-Server — Ziel srv wird wie local behandelt.")
        return "local"
    return ziel


def _server_repo_pflicht(konfig: dict[str, Any]) -> str:
    """``server_repo`` für Fern-Start per SSH; fehlt es, ``ValueError`` mit Hinweis (#408)."""
    wert = str(konfig.get("server_repo") or "").strip()
    if not wert:
        raise ValueError(_FEHLER_SERVER_REPO)
    return wert


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
    *,
    ohne_wache: bool = False,
) -> list[str]:
    """argv des Terminal-Skripts; auf Nicht-Windows ohne ``pwsh`` (#257).

    Auf dem Bau-Server selbst wird ``srv`` wie ``local`` behandelt (#408). Sonst braucht
    ``srv`` ``server_repo`` aus der Konfig (Repo-Ordner auf dem Bau-Server) — fehlt er,
    ``ValueError`` mit klarer Meldung statt eines blinden SSH-Aufrufs.
    """
    ziel = ziel_aufloesen(ziel, konfig)
    if sys.platform != "win32":
        return _befehl_unix(ziel, spec, tickets, konfig, dry_run, ohne_wache=ohne_wache)
    if ziel == "srv":
        _server_repo_pflicht(konfig)
    skript = SKILL_ORDNER / SKRIPTE[ziel]
    pwsh = shutil.which("pwsh") or shutil.which("powershell") or "pwsh"
    befehl = [pwsh, "-File", str(skript), "-Spec", str(spec)]
    if tickets:
        befehl += ["-Tickets", ",".join(str(t) for t in tickets)]
    if ohne_wache:
        befehl.append("-OhneWache")
    if ziel == "srv":
        befehl += ["-Zielserver", config.ssh_ziel(konfig)]
    if dry_run:
        befehl.append("-DryRun")
    return befehl


def fern_ordner(pfad: str) -> str:
    """Ordner für ``cd`` in einem SSH-Befehl quoten — ``~/`` bleibt aufgelöst (``'~/x'`` würde es nicht)."""
    return config.fern_ordner(pfad)


def _befehl_unix(
    ziel: str,
    spec: int | str,
    tickets: Sequence[str] | None,
    konfig: dict[str, Any],
    dry_run: bool,
    *,
    ohne_wache: bool = False,
) -> list[str]:
    argumente = [str(spec)]
    if tickets:
        argumente += ["--tickets", ",".join(str(t) for t in tickets)]
    if ohne_wache:
        argumente.append("--ohne-wache")
    if dry_run:
        argumente.append("--dry-run")
    if ziel == "local":
        return ["bash", str(SPAWN_SH), *argumente]
    server_repo = _server_repo_pflicht(konfig)
    return ["ssh", config.ssh_ziel(konfig), umzug.fern_skill_befehl(server_repo, "bash", "spawn_srv.sh", argumente)]


def spawn(
    repo: Path,
    spec: int | str,
    konfig: dict[str, Any],
    *,
    ziel: str | None = None,
    tickets: Sequence[str] | None = None,
    ohne_wache: bool = False,
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
    gewaehlt = ziel_aufloesen(gewaehlt, konfig)
    try:
        befehl = baue_befehl(gewaehlt, spec, tickets, konfig, dry_run, ohne_wache=ohne_wache)
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
    rc = starter.starte(befehl, cwd=repo, check=False, env=umgebung, warten=True).returncode
    if spawn_exit.ist_spaeter_erneut(rc):
        log.info(
            "Terminal-Skript endete mit Exit %s (%s) — später erneut versuchen.",
            rc,
            spawn_exit.beschreibung(rc),
        )
        return rc
    if rc != 0:
        log.error(
            "Terminal-Skript endete mit Exit %s (%s) — es wurde nichts (vollständig) gestartet; Ausgabe siehe oben.",
            rc,
            spawn_exit.beschreibung(rc),
        )
    return rc


# --- Einzelticket-Neustart (Aufseher-Werkzeug) -------------------------------------------------------

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
        sitzung, _ = tmux_aufruf.sitzung_aufloesen(repo, spec)
        fenster = capo.tmux_fenster(spec, sitzung=sitzung)
        return capo.folge_befehl(repo, spec, ticket, fenster, auftrag, sitzung=sitzung), fenster is not None
    return wt_tab_befehl(repo, ticket, auftrag), False


def wt_tab_befehl(repo: Path, ticket: int, auftrag: str, *schalter: str) -> list[str]:
    """argv für einen Windows-Terminal-Tab ``bau <N>`` (``bau.py <N> --sofort``) — einzige Stelle.

    ``schalter`` hängt weitere ``bau.py``-Schalter an (respawn am PC: ``--remote-control``).
    Bei Exit 0 schließt der Tab von selbst; bei Fehler ≠ 0 hält ``-NoExit`` ihn offen (#782).
    """
    innen = f"python '{capo._bau_skript(repo)}' {ticket} --sofort"
    if auftrag:
        # wt trennt Tabs an ';' — der Auftrag darf keins enthalten; ' für pwsh verdoppeln.
        innen += " --auftrag '" + auftrag.replace(";", ",").replace("'", "''") + "'"
    if schalter:
        innen += " " + " ".join(schalter)
    # Kein ';' im -Command-Text (wt trennt Tabs an ';'); wie spawn_local.ps1 (#282/#782).
    innen += " && & { if ($LASTEXITCODE -eq 0) { exit 0 } }"
    vorspann = sitzung_env.pwsh_vorspann(repo)
    if vorspann:
        innen = f"{vorspann} && {innen}"
    return [
        "wt",
        "-w",
        "0",
        "new-tab",
        "--title",
        f"bau {ticket}",
        "-d",
        str(repo),
        "pwsh",
        "-NoExit",
        "-Command",
        innen,
    ]


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
    """Ein Ticket neu starten — Einzeiler für den Aufseher.

    ``local`` = dieser Rechner (Bau-Server: tmux-Fenster in ``spec-<S>``, PC: wt-Tab),
    ``srv`` = per SSH derselbe Befehl im ``server_repo`` des Bau-Servers. Läuft das
    Ticket noch, bricht der Neustart ab (Exit 3) — außer mit ``beenden``: dann wird
    zuerst nur die Claude-Session beendet (ohne Session ``bau.py`` selbst, das dann
    nichts mehr zu tun hat), nie ein fremder Prozess.
    """
    ziel = ziel_aufloesen(ziel, konfig)
    if ziel == "srv":
        try:
            server_repo = _server_repo_pflicht(konfig)
        except ValueError as fehler:
            log.error("%s", fehler)
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
        fern_cli = config.fern_skill_pfad("to_spawn.py")
        fern_env = (
            f"{config.SKILL_HOME_ENV}={config.fern_skill_pfad()} "
            if (os.environ.get(config.SKILL_HOME_ENV) or "").strip()
            else ""
        )
        fern = f"cd {fern_ordner(server_repo)} && {fern_env}python3 {fern_cli} {shlex.join(argumente)}"
        befehl = ["ssh", config.ssh_ziel(konfig), fern]
        print(" ".join(befehl[:2]), repr(fern))
        return starter.starte(befehl, cwd=repo, check=False, warten=True).returncode

    stand_auswahl = auswahl.lesen(repo, spec)
    if not auswahl.ticket_erlaubt(repo, spec, ticket, auswahl=stand_auswahl, gelesen=True):
        meldung = (
            f"WEIGERUNG: Ticket #{ticket} liegt außerhalb der gespeicherten Auswahl "
            f"für Spec #{spec} ({auswahl.beschreibung(stand_auswahl)}) — kein Neustart."
        )
        log.error("%s", meldung)
        print(meldung, file=sys.stderr)
        return manifest.EXIT_WEIGERUNG

    os.environ["TO_SPAWN_REPO"] = str(repo)
    eintrag = ticket_eintrag(spec, ticket)
    print(f"#{ticket}: {eintrag.zustand} (bau.py {eintrag.pid or '-'}, Session {eintrag.session_pid or '-'})")
    if eintrag.zustand != "aus":
        if not beenden:
            log.error("#%s läuft noch (%s) — erst prüfen, dann mit --beenden neu starten.", ticket, eintrag.zustand)
            return 3
        if sys.platform == "win32":
            from . import pc_fenster  # spät: pc_fenster importiert spawn

            tab = pc_fenster.tab_pid(ticket, int(eintrag.pid)) if eintrag.pid else None
            if tab is not None:
                opfer, rolle = tab, "Tab-pwsh samt bau.py"
            elif eintrag.pid:
                opfer = int(eintrag.pid)
                rolle = "bau.py samt Session" if eintrag.session_pid else "bau.py ohne Session"
            else:
                opfer, rolle = eintrag.session_pid, "Claude-Session"
            if dry_run:
                print(f"würde beenden: PID {opfer} ({rolle})")
            else:
                if opfer is None or not pc_fenster.baum_beenden(int(opfer)):
                    log.error("#%s: PID %s (%s) lebt nach taskkill noch — kein Neustart.", ticket, opfer, rolle)
                    return 3
        else:
            opfer = eintrag.session_pid or eintrag.pid
            if dry_run:
                print(
                    f"würde beenden: PID {opfer} ({'Claude-Session' if eintrag.session_pid else 'bau.py ohne Session'})"
                )
            else:
                try:
                    os.kill(int(opfer), 15)
                except OSError as fehler:
                    log.error("#%s: PID %s nicht beendbar (%s).", ticket, opfer, fehler)
                    return 2
        if not dry_run:
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
        return starter.starte(befehl, cwd=repo, check=False, warten=True).returncode
    grund = capo._starte_folge_runde(repo, ticket, befehl, ueber_tmux, auftrag)
    if grund:
        log.error("#%s: Start fehlgeschlagen — %s", ticket, grund)
        return 2
    return 0
