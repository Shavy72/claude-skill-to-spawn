"""Welche Ticket-Sessions sind an? Prozessliste → Ticket-Zuordnung, eine Zeile je Ticket.

Aufruf: ``python scripts/sessions_stand.py [<S>] [--alle]``
  <S>     Spec-Nummer (Manifest ``docs/agents/manifests/spec-<S>.json``); ohne Angabe
          werden alle Manifeste gelesen.
  --alle  auch Tickets ohne laufenden Prozess zeigen (Standard: nur mit Prozess
          plus die des angegebenen Manifests).

Zustände je Ticket:
  aus              kein ``bau``/``wache``-Prozess
  wartet           ``bau.py`` pollt GitHub (0 Token), noch keine Claude-Session
  läuft seit HH:MM ``bau.py``/``wache.py`` hat eine Claude-Session gestartet (Kindprozess)
  VERWAIST seit    Claude-Session lebt, aber ``bau.py`` ist weg (nie ``bau.py`` killen, ohne die Kinder zu prüfen)

Windows liest die Prozessliste per WMI (PowerShell), Linux per ``ps``.
Kostet keine Token, läuft in jedem Terminal.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Skill-Wurzel in sys.path, damit ``to_spawn.config`` (Repo-Wurzel, Konfig) importierbar ist (#205).
_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import auswahl, bau_log, config, konsole, manifest, prozessbaum, werkzeug  # noqa: E402

log = logging.getLogger("sessions_stand")
#: Repo, in dem gearbeitet wird: ``TO_SPAWN_REPO`` (setzt die Weiterleitung im Repo), sonst
#: Git-Wurzel des aktuellen Ordners — nie der Ort dieses Skripts (liegt im Skill, #205).
REPO = Path(os.environ["TO_SPAWN_REPO"]).resolve() if os.environ.get("TO_SPAWN_REPO") else config.repo_wurzel()
MANIFESTE = manifest.manifest_ordner(REPO)
#: ``scripts/`` = Weiterleitung im Repo, ``skripte/`` = Direktstart aus dem Skill (#205).
MUSTER = re.compile(r"(?:scripts|skripte)[\\/](bau|wache)\.py\"?\s+(\d+)")
#: Claude-Session aus ``bau``: der Settings-Pfad trägt die Ticket-Nummer (``<repo>-bau\\<N>-<zeit>[-<anhang>]``,
#: Anhang seit ``mkdtemp`` in ``bau.py``).
VERWAIST = re.compile(r"[\w.-]+-bau[\\/](\d+)-\d{8}-\d{6}(?:-\w+)?[\\/]settings\.json")
#: Prozessnamen einer laufenden Bau-Session (``claude``/``node``/``agy``, Windows + Linux, #660).
SESSION_NAMEN = werkzeug.SESSION_NAMEN_ALLE
#: Namen, unter denen eine verwaiste Claude-Session auftaucht (``node.exe`` bleibt draußen — auf Windows zu unscharf).
VERWAIST_NAMEN = frozenset({"claude.exe", "claude", "node"})


@dataclass
class Prozess:
    pid: int
    ppid: int
    name: str
    cmd: str
    start: datetime | None
    #: Arbeitsordner, gelesen beim Erstellen der echten Liste (``prozesse_linux`` / ``prozesse_lesen``, #588);
    #: ``None`` = unbekannt (aufgezeichnete Liste, unlesbar) → zählt als eigenes Repo.
    ordner: Path | None = None


@dataclass
class Eintrag:
    nummer: str
    art: str  # "ticket" | "spec"
    titel: str
    zustand: str = "aus"
    pid: int | None = None
    session_pid: int | None = None
    kinder: list[str] = field(default_factory=list)
    token: str = "—"
    ctx: str = "—"  # context-mode-Server unter der Session: ✓ / ✗ (#237)


def ps_zeilen_parsen(text: str) -> list[Prozess]:
    """``ps -eo pid,ppid,lstart,comm,args --no-headers`` auswerten (``lstart`` = 5 Felder, Locale C)."""
    ergebnis: list[Prozess] = []
    for zeile in text.splitlines():
        teile = zeile.split(None, 8)
        if len(teile) < 8 or not teile[0].isdigit():
            continue
        try:
            start = datetime.strptime(" ".join(teile[2:7]), "%a %b %d %H:%M:%S %Y")
        except ValueError:
            start = None
        name = teile[7]
        ergebnis.append(Prozess(int(teile[0]), int(teile[1]), name, teile[8] if len(teile) > 8 else name, start))
    return ergebnis


def prozesse_linux() -> list[Prozess]:
    """Prozessliste über ``ps`` (Startzeit-Format nur mit ``LC_ALL=C`` verlässlich).

    ``-ww``: volle Kommandozeile — sonst kürzt ``ps`` bei gesetztem ``COLUMNS`` (z. B. 80)
    ``args`` ab und ``bau.py <N>`` am Zeilenende fällt weg (#1411).
    """
    out = subprocess.run(
        ["ps", "-ww", "-eo", "pid,ppid,lstart,comm,args", "--no-headers"],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        env={**os.environ, "LC_ALL": "C"},
        **prozessbaum.fensterlos(),
    ).stdout
    if not out.strip():
        log.error("Prozessliste leer — ist ``ps`` (procps) installiert?")
        return []
    # Lebens-Prüfung (#494) und Arbeitsordner (#212) gehören zum Lesen der echten Liste, nicht
    # in ``zuordnen`` (#564, #588): das urteilt nur aus der übergebenen Liste, auch über
    # aufgezeichnete mit erfundenen Nummern. Ordner zuerst, dann Lebens-Prüfung: stirbt ein
    # Prozess dazwischen, fällt er raus, statt mit unlesbarem Ordner als eigener zu zählen.
    lebende: list[Prozess] = []
    for p in ps_zeilen_parsen(out):
        p.ordner = arbeitsordner(p.pid)
        if not beendet(p.pid):
            lebende.append(p)
    return lebende


def prozesse_lesen() -> list[Prozess]:
    """Alle Prozesse mit Kommandozeile und Startzeit (Windows: PowerShell/WMI, sonst ``ps``)."""
    if sys.platform != "win32":
        return prozesse_linux()
    ps = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CommandLine, "
        "@{n='Start';e={ if ($_.CreationDate) { $_.CreationDate.ToString('o') } else { '' } }} | ConvertTo-Json -Compress"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        # Aus Takt/Leitstand ohne Konsole gestartet: sonst öffnet PowerShell je Aufruf ein Fenster.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    if not out.strip():
        log.error("Prozessliste leer — läuft das auf Windows mit PowerShell?")
        return []
    rohe = json.loads(out)
    if isinstance(rohe, dict):
        rohe = [rohe]
    ergebnis: list[Prozess] = []
    for r in rohe:
        start = None
        if r.get("Start"):
            try:
                start = datetime.fromisoformat(r["Start"])
            except ValueError:
                start = None
        pid = int(r["ProcessId"])
        # Arbeitsordner (#212) und Lebens-Prüfung (#494) wie in prozesse_linux:
        # Ordner zuerst, dann Lebens-Prüfung — ein zwischen WMI und Blick beendeter
        # Prozess fällt raus, statt mit unlesbarem Ordner als eigener zu zählen.
        ordner = arbeitsordner(pid)
        if beendet(pid):
            continue
        ergebnis.append(
            Prozess(
                pid,
                int(r.get("ParentProcessId") or 0),
                str(r.get("Name") or ""),
                str(r.get("CommandLine") or ""),
                start,
                ordner,
            )
        )
    return ergebnis


def nachkommen(pid: int, alle: list[Prozess]) -> list[Prozess]:
    """Alle Nachfahren von ``pid``; Windows recycelt PIDs, darum Zyklen-Schutz."""
    gesehen = {pid}
    kinder: list[Prozess] = []
    offen = [pid]
    while offen:
        eltern = offen.pop()
        for p in alle:
            if p.ppid == eltern and p.pid not in gesehen:
                gesehen.add(p.pid)
                kinder.append(p)
                offen.append(p.pid)
    return kinder


#: MCP-Server des Plugins = ``…/start.mjs`` als eigene Datei; die Hooks heißen ``hooks/sessionstart.mjs``.
CONTEXT_MODE_SERVER = re.compile(r"[\\/]start\.mjs(?:\s|\"|$)")


def context_mode_zustand(session_pid: int, alle: list[Prozess]) -> str:
    """``✓`` wenn unter der Claude-Session ein context-mode-Server (``start.mjs``) läuft (#237)."""
    return "✓" if any(CONTEXT_MODE_SERVER.search(k.cmd) for k in nachkommen(session_pid, alle)) else "✗"


def manifeste_lesen(spec: str | None) -> dict[str, Eintrag]:
    eintraege: dict[str, Eintrag] = {}
    dateien = [MANIFESTE / f"spec-{spec}.json"] if spec else sorted(MANIFESTE.glob("spec-*.json"))
    for d in dateien:
        if not d.exists():
            log.warning("Manifest fehlt: %s", d)
            continue
        try:
            m = json.loads(d.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            log.warning("Manifest unlesbar %s: %s", d.name, e)
            continue
        s = str(m.get("spec") or "")
        auswahl_stand = auswahl.lesen(REPO, s) if s else None
        if s and s not in eintraege and auswahl.wache_erlaubt(REPO, s, auswahl=auswahl_stand, gelesen=True):
            eintraege[s] = Eintrag(s, "spec", f"Aufseher Spec #{s} ({m.get('feature', '')})")
        for n, t in (m.get("tickets") or {}).items():
            if s and not auswahl.ticket_erlaubt(REPO, s, str(n), auswahl=auswahl_stand, gelesen=True):
                continue
            eintraege[str(n)] = Eintrag(str(n), "ticket", str(t.get("title") or ""))
    return eintraege


def arbeitsordner(pid: int) -> Path | None:
    """Arbeitsordner aus ``/proc/<pid>/cwd`` (Linux) bzw. PEB (Windows); ``None`` = unlesbar."""
    if sys.platform == "win32":
        try:
            pfad = prozessbaum.arbeitsordner_windows(pid)
            return Path(pfad).resolve() if pfad else None
        except OSError:
            return None
    try:
        return Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
    except OSError:
        return None


def ordner_fremd(ordner: Path | None) -> bool:
    """Liegt ``ordner`` außerhalb von ``REPO``? (#212)

    Außerhalb zählt der Prozess nicht (zwei Repos mit derselben Ticket-Nummer auf einem
    Rechner, oder „lokal“ und „Server“ auf demselben Rechner). Unbekannter Ordner
    (``None``) zählt wie bisher als eigener.
    """
    if ordner is None:
        return False
    wurzel = REPO.resolve()
    return ordner != wurzel and wurzel not in ordner.parents


def beendet(pid: int) -> bool:
    """Ist der Prozess seit ``ps``/WMI beendet? Linux: ``/proc/<pid>`` weg/Zombie; Windows: Kernel.

    Nur beim Lesen der echten Liste (``prozesse_linux`` / ``prozesse_lesen``) gefragt, nie in ``zuordnen`` (#564).

    Ein beendeter Prozess läuft nicht mehr, auch wenn ``ordner_fremd`` ihn mangels lesbarem
    Ordner mitzählt (#494): sonst gälte etwa die eben beendete Aufseher-Attrappe eines
    Nachbar-Tests als lebender Aufseher, und ``spawn_srv.sh`` meldete bei vollem Rechner
    „Aufseher startet nach“ (Exit 0), obwohl niemand nachstartet.
    """
    if sys.platform == "win32":
        try:
            return prozessbaum.ist_weg_windows(pid)
        except OSError:
            return False
    if not Path("/proc/self").is_dir():
        return False
    if not Path(f"/proc/{pid}").exists():
        return True
    try:  # Zombie (beendet, nicht abgeholt): /proc/<pid> da, cwd aber ENOENT
        os.readlink(f"/proc/{pid}/cwd")
    except FileNotFoundError:
        return True
    except OSError:
        pass
    return False


def zuordnen(eintraege: dict[str, Eintrag], alle: list[Prozess]) -> None:
    for p in alle:
        if not p.name.lower().startswith("python"):
            continue
        m = MUSTER.search(p.cmd)
        if not m:
            continue
        if ordner_fremd(p.ordner):
            continue
        art, nummer = m.group(1), m.group(2)
        e = eintraege.get(nummer)
        if e is None:
            e = Eintrag(nummer, "spec" if art == "wache" else "ticket", "(nicht im Manifest)")
            eintraege[nummer] = e
        e.pid = p.pid
        kinder = nachkommen(p.pid, alle)
        session = next((k for k in kinder if k.name.lower() in SESSION_NAMEN), None)
        if session is not None:
            e.session_pid = session.pid
            seit = session.start.strftime("%H:%M") if session.start else "?"
            e.zustand = f"läuft seit {seit}"
        else:
            e.zustand = "wartet" if art == "bau" else "startet"
        e.kinder = [k.name for k in kinder][:4]
        if session is not None:
            e.ctx = context_mode_zustand(session.pid, alle)
    # Verwaiste Sessions: ``claude.exe`` lebt, aber der ``bau.py``-Elternprozess ist weg
    # (17.09.2026: bau.py beendet, Claude-Kind lief unsichtbar weiter, zweimal für #187).
    bekannte = {e.session_pid for e in eintraege.values() if e.session_pid}
    for p in alle:
        if p.name.lower() not in VERWAIST_NAMEN or p.pid in bekannte:
            continue
        if ordner_fremd(p.ordner):  # Claude-Session eines anderen Repos ist hier keine Waise (#212)
            continue
        m = VERWAIST.search(p.cmd)
        if not m:
            continue
        nummer = m.group(1)
        e = eintraege.get(nummer) or Eintrag(nummer, "ticket", "(nicht im Manifest)")
        eintraege[nummer] = e
        seit = p.start.strftime("%H:%M") if p.start else "?"
        if e.session_pid:
            e.zustand = f"{e.zustand} + VERWAIST {p.pid} seit {seit}"
        else:
            e.session_pid = p.pid
            e.zustand = f"VERWAIST seit {seit}"
            e.ctx = context_mode_zustand(p.pid, alle)


def token_text(ticket: str, repo: Path = REPO) -> str:
    """Spitzen-Kontext eines Tickets aus dem Bau-Log, z. B. „180,2k“ (``—`` ohne Wert).

    Die Smart-Zone-Zahl: größter Kontext einer Session (#238), nie die Summe des
    Cache-Lesens. Zuerst das Log im Ticket-Worktree (dort schreiben die Hooks),
    sonst das im Repo.
    """
    # Bewusst ohne ``repo``: ``worktree_pfad`` liest das Repo selbst aus ``TO_SPAWN_REPO``
    # bzw. dem aktuellen Ordner — genau die Quelle von ``REPO`` oben. Die Alt-Tests (#238)
    # stubben die Funktion mit einem Argument, ein zweites bricht sie (Fixrunde #257).
    ort = bau_log.log_ort(ticket, Path(config.worktree_pfad(ticket)).expanduser(), repo)
    if ort is None:
        return bau_log.kontext_text(None)
    # Rückfall-Laufdatei im Hauptbaum kommt dazu (Worktree fehlte beim Schreiben, #257).
    spitze_k = bau_log.zusammenfassung(ort, ticket, hauptbaum=repo)["spitze_k"]
    return bau_log.kontext_text(spitze_k)


def token_eintragen(eintraege: dict[str, Eintrag], repo: Path = REPO) -> None:
    for e in eintraege.values():
        if e.art == "ticket":
            try:
                e.token = token_text(e.nummer, repo)
            except OSError as fehler:
                log.warning("Bau-Log #%s unlesbar: %s", e.nummer, fehler)


def tabelle(eintraege: dict[str, Eintrag], alle_zeigen: bool) -> str:
    zeilen = []
    for n in sorted(eintraege, key=lambda x: int(x)):
        e = eintraege[n]
        if not alle_zeigen and e.zustand == "aus":
            continue
        kopf = f"Spec #{n}" if e.art == "spec" else f"#{n}"
        pid = f"pid {e.pid}" if e.pid else "—"
        sess = f"session {e.session_pid}" if e.session_pid else ""
        zeilen.append(f"{kopf:<10} {e.zustand:<18} {pid:<10} {sess:<14} {e.token:<8} {e.ctx:<4} {e.titel[:60]}")
    if not zeilen:
        return "(keine Ticket-Sessions gefunden)"
    kopfzeile = f"{'Ticket':<10} {'Zustand':<18} {'Prozess':<10} {'Claude':<14} {'Token':<8} {'ctx':<4} Titel"
    return "\n".join([kopfzeile, "-" * 119, *zeilen])


def main(argv: list[str] | None = None) -> int:
    konsole.utf8_ausgabe()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?", help="Spec-Nummer (Manifest)")
    ap.add_argument("--alle", action="store_true", help="auch Tickets ohne Prozess zeigen")
    a = ap.parse_args(argv)
    eintraege = manifeste_lesen(a.spec)
    alle = prozesse_lesen()
    zuordnen(eintraege, alle)
    token_eintragen(eintraege)
    print(tabelle(eintraege, alle_zeigen=a.alle or bool(a.spec)))
    an = sum(1 for e in eintraege.values() if e.zustand != "aus")
    laufen = sum(1 for e in eintraege.values() if e.zustand.startswith("läuft"))
    print(f"\n{an} Prozesse an · {laufen} Claude-Sessions laufen · {datetime.now().strftime('%H:%M')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
