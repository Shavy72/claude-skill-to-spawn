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
from to_spawn import bau_log, config  # noqa: E402

log = logging.getLogger("sessions_stand")
#: Repo, in dem gearbeitet wird: ``TO_SPAWN_REPO`` (setzt die Weiterleitung im Repo), sonst
#: Git-Wurzel des aktuellen Ordners — nie der Ort dieses Skripts (liegt im Skill, #205).
REPO = Path(os.environ["TO_SPAWN_REPO"]).resolve() if os.environ.get("TO_SPAWN_REPO") else config.repo_wurzel()
MANIFESTE = REPO / "docs" / "agents" / "manifests"
#: ``scripts/`` = Weiterleitung im Repo, ``skripte/`` = Direktstart aus dem Skill (#205).
MUSTER = re.compile(r"(?:scripts|skripte)[\\/](bau|wache)\.py\"?\s+(\d+)")
#: Claude-Session aus ``bau``: der Settings-Pfad trägt die Ticket-Nummer (``<repo>-bau\\<N>-<zeit>``).
VERWAIST = re.compile(r"[\w.-]+-bau[\\/](\d+)-\d{8}-\d{6}[\\/]settings\.json")
#: Prozessnamen einer laufenden Claude-Session (Windows ``claude.exe``/``node.exe``, Linux ``claude``/``node``).
SESSION_NAMEN = frozenset({"claude.exe", "node.exe", "claude", "node"})
#: Namen, unter denen eine verwaiste Claude-Session auftaucht (``node.exe`` bleibt draußen — auf Windows zu unscharf).
VERWAIST_NAMEN = frozenset({"claude.exe", "claude", "node"})


@dataclass
class Prozess:
    pid: int
    ppid: int
    name: str
    cmd: str
    start: datetime | None


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
    """Prozessliste über ``ps`` (Startzeit-Format nur mit ``LC_ALL=C`` verlässlich)."""
    out = subprocess.run(
        ["ps", "-eo", "pid,ppid,lstart,comm,args", "--no-headers"],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        env={**os.environ, "LC_ALL": "C"},
    ).stdout
    if not out.strip():
        log.error("Prozessliste leer — ist ``ps`` (procps) installiert?")
        return []
    return ps_zeilen_parsen(out)


def prozesse_lesen() -> list[Prozess]:
    """Alle Prozesse mit Kommandozeile und Startzeit (Windows: PowerShell/WMI, sonst ``ps``)."""
    if sys.platform != "win32":
        return prozesse_linux()
    ps = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CommandLine, "
        "@{n='Start';e={ if ($_.CreationDate) { $_.CreationDate.ToString('o') } else { '' } }} | ConvertTo-Json -Compress"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    ).stdout
    if not out.strip():
        log.error("Prozessliste leer — läuft das auf Windows mit PowerShell?")
        return []
    rohe = json.loads(out)
    ergebnis: list[Prozess] = []
    for r in rohe:
        start = None
        if r.get("Start"):
            try:
                start = datetime.fromisoformat(r["Start"])
            except ValueError:
                start = None
        ergebnis.append(
            Prozess(
                int(r["ProcessId"]),
                int(r.get("ParentProcessId") or 0),
                str(r.get("Name") or ""),
                str(r.get("CommandLine") or ""),
                start,
            )
        )
    return ergebnis


def nachkommen(pid: int, alle: list[Prozess]) -> list[Prozess]:
    kinder = [p for p in alle if p.ppid == pid]
    for k in list(kinder):
        kinder.extend(nachkommen(k.pid, alle))
    return kinder


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
        if s and s not in eintraege:
            eintraege[s] = Eintrag(s, "spec", f"Wächter Spec #{s} ({m.get('feature', '')})")
        for n, t in (m.get("tickets") or {}).items():
            eintraege[str(n)] = Eintrag(str(n), "ticket", str(t.get("title") or ""))
    return eintraege


def fremdes_repo(pid: int) -> bool:
    """Läuft dieser ``bau.py``/``wache.py`` in einem anderen Repo als ``REPO``? (#212)

    Linux: Arbeitsordner aus ``/proc/<pid>/cwd`` — außerhalb von ``REPO`` zählt der
    Prozess nicht (zwei Repos mit derselben Ticket-Nummer auf einem Rechner, oder
    „lokal“ und „Server“ auf demselben Rechner). Ohne ``/proc`` (Windows) oder bei
    unlesbarem Ordner zählt er wie bisher.
    """
    try:
        ordner = Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
    except OSError:
        return False
    wurzel = REPO.resolve()
    return ordner != wurzel and wurzel not in ordner.parents


def zuordnen(eintraege: dict[str, Eintrag], alle: list[Prozess]) -> None:
    for p in alle:
        if not p.name.lower().startswith("python"):
            continue
        m = MUSTER.search(p.cmd)
        if not m:
            continue
        if fremdes_repo(p.pid):
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
    # Verwaiste Sessions: ``claude.exe`` lebt, aber der ``bau.py``-Elternprozess ist weg
    # (17.09.2026: bau.py beendet, Claude-Kind lief unsichtbar weiter, zweimal für #187).
    bekannte = {e.session_pid for e in eintraege.values() if e.session_pid}
    for p in alle:
        if p.name.lower() not in VERWAIST_NAMEN or p.pid in bekannte:
            continue
        if fremdes_repo(p.pid):  # Claude-Session eines anderen Repos ist hier keine Waise (#212)
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


def token_text(ticket: str, repo: Path = REPO) -> str:
    """Spitzen-Kontext eines Tickets aus dem Bau-Log, z. B. „180,2k“ (``—`` ohne Wert).

    Die Smart-Zone-Zahl: größter Kontext einer Session (#238), nie die Summe des
    Cache-Lesens. Zuerst das Log im Ticket-Worktree (dort schreiben die Hooks),
    sonst das im Repo.
    """
    for ort in (Path(config.worktree_pfad(ticket)).expanduser(), repo):
        if bau_log.hat_log(ort, ticket):  # versioniert oder Laufdatei (Fixrunde #204)
            spitze_k = bau_log.zusammenfassung(ort, ticket)["spitze_k"]
            return f"{spitze_k:.1f}".replace(".", ",") + "k" if spitze_k else "—"
    return "—"


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
        zeilen.append(f"{kopf:<10} {e.zustand:<18} {pid:<10} {sess:<14} {e.token:<8} {e.titel[:60]}")
    if not zeilen:
        return "(keine Ticket-Sessions gefunden)"
    kopfzeile = f"{'Ticket':<10} {'Zustand':<18} {'Prozess':<10} {'Claude':<14} {'Token':<8} Titel"
    return "\n".join([kopfzeile, "-" * 119, *zeilen])


def main(argv: list[str] | None = None) -> int:
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
