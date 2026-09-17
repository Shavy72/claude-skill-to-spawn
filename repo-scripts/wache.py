"""Bau-Wächter-Session für eine Spec starten (frische Claude-Session, Fable 5.1).

Aufruf: ``python scripts/wache.py <S> [--model <m>] [--takt <s>] [--dry-run] [--print-prompt]``

Der Wächter baut nichts und spricht keine Bau-Session an. Er liest je Tick nur
``scripts/spec_stand.py <S>`` (eine Zeile je Ticket), prüft Belegseiten nur bei
Zustandswechsel und schreibt seinen Stand in ``docs/HANDOFF_<datum>_waechter_<S>.md``.
Gegenstück zu ``bau <N>`` (eine Session je Ticket, Domino über native Blocker).
"""

from __future__ import annotations

import argparse
import logging
import re
import os
import shutil
import subprocess
import sys
from datetime import date

log = logging.getLogger("wache")
def repo_aus_origin(fallback: str) -> str:
    """``owner/name`` aus ``git remote get-url origin`` (GitHub, https oder ssh); sonst ``fallback``.

    Damit läuft dasselbe Skript in jedem Repo mit GitHub-Origin — nichts hart verdrahtet.
    """
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        return fallback
    m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", url)
    return m.group(1) if m else fallback


REPO = repo_aus_origin("Shavy72/duoplus-management")
MODELL = "claude-fable-5-1"

PROMPT = """/loop Bau-Wächter Spec #{S} ({REPO}). Ich baue NICHTS und spreche KEINE Bau-Session an (kein SendMessage; Kommentare auf Ticket-Issues nur bei echtem Zustandswechsel, max. 2 Zeilen). \
Jeder Tick: 1) `PYTHONIOENCODING=utf-8 python scripts/spec_stand.py {S}` — Ein-Zeilen-Stand je Ticket, mehr nicht lesen. \
2) Nur bei Änderung gegenüber dem letzten Tick genauer hinsehen: Ticket neu zu → Schließ-Kommentar + Belegseite unter docs/verify-hard/ auf origin/master prüfen (Akzeptanz erfüllt? Live-Klick-Weg-Beleg mit Rolle da? Tests nur ergänzt, nie ersetzt? Commit-Betreff endet mit „(#<Ticket>)“?), bei Mangel 2-Zeilen-Kommentar „Wächter: … fehlt“ + `gh issue reopen`; Assignee seit >3 h ohne Commit auf origin/master und Worktree C:/dev/wt-<N> ohne Änderung → verwaist-Verdacht notieren; VPS-HEAD ≠ origin/master nach einem Deploy-Ticket → notieren; zwei Sessions mit Commits in derselben Datei → Konflikt-Warnung notieren. \
3) Stand in `docs/HANDOFF_{DATUM}_waechter_{S}.md` fortschreiben (Stand + Nachträge mit Uhrzeit, Muster docs/HANDOFF_2026-09-15_waechter_107.md), Commit nur mit Pathspec + [skip ci], Rebase nur bei sauberem Baum (`git diff --quiet`), Push — nur wenn sich etwas geändert hat. \
4) ScheduleWakeup {TAKT} s solange Sessions bauen, 3600 s wenn alle Terminals nur warten; noop: true ohne Änderung. \
ENDE: alle Tickets zu + Belegseiten geprüft + VPS = origin/master → Abschlussbericht als Kommentar auf #{S} (max. 10 Zeilen) + stop: true. \
Eigener Kontext: Spec/Tickets nie voll laden, nur Stand-Zeilen; Belegseiten per Grep/limit. Erste Zeile jeder Antwort: 🧭 Fable · low · caveman · Wächter."""


def main() -> int:
    ap = argparse.ArgumentParser(description="Bau-Wächter-Session für eine Spec.")
    ap.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    ap.add_argument("--model", default=MODELL, help=f"Claude-Modell ({MODELL})")
    ap.add_argument("--takt", type=int, default=1800, help="Sekunden zwischen zwei Ticks (1800)")
    ap.add_argument("--dry-run", action="store_true", help="nur Befehl zeigen")
    ap.add_argument("--print-prompt", action="store_true", help="nur den Prompt ausgeben")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    prompt = PROMPT.format(S=a.spec, REPO=REPO, DATUM=date.today().isoformat(), TAKT=max(600, a.takt))
    if a.print_prompt:
        print(prompt)
        return 0
    claude = shutil.which("claude") or "claude"
    cmd = [claude, "--model", a.model, prompt]
    log.info("Wächter Spec #%s · Modell %s · Takt %ss", a.spec, a.model, a.takt)
    if a.dry_run:
        print(" ".join(cmd[:-1]), '"<prompt>"')
        return 0
    # Aus einer Claude-Session gestartet erben Kind-Sessions die Markierung
    # CLAUDE_CODE_CHILD_SESSION und speichern kein Transkript (kein Resume nach
    # Absturz, Beleg 17.09.2026). Persistenz deshalb ausdrücklich erzwingen.
    os.environ.pop("CLAUDE_CODE_CHILD_SESSION", None)
    os.environ["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] = "1"
    try:
        return subprocess.run(cmd, check=False).returncode
    except OSError:
        return subprocess.run(" ".join(f'"{c}"' for c in cmd), shell=True, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
