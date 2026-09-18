"""CLI des Skills ``to-spawn`` (Version 2, vorläufiger Kern).

Aufruf (im Repo-Wurzelordner):

    python ~/.claude/skills/to-spawn/to_spawn.py spawn <S> [--ziel local|srv]
    python ~/.claude/skills/to-spawn/to_spawn.py pruefen <S> [--tickets a,b] [--ohne-github]
    python ~/.claude/skills/to-spawn/to_spawn.py log <S>
    python ~/.claude/skills/to-spawn/to_spawn.py lernstoff [--letzte 30]
    python ~/.claude/skills/to-spawn/to_spawn.py hook-stop          # JSON auf stdin
    python ~/.claude/skills/to-spawn/to_spawn.py hook-subagent-stop # JSON auf stdin
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from to_spawn import bau_log, config, hooks, manifest, spawn as spawn_modul  # noqa: E402

log = logging.getLogger("to_spawn")


def _tickets(wert: str | None) -> list[str] | None:
    if not wert:
        return None
    return [teil.strip() for teil in wert.split(",") if teil.strip()]


def _spec_tickets(repo: Path, spec: str) -> list[str]:
    try:
        daten = manifest.lade_manifest(repo, spec)
    except (FileNotFoundError, ValueError):
        return bau_log.alle_tickets(repo)
    return sorted(daten["tickets"], key=manifest.ticket_schluessel)


def main(argv: list[str] | None = None) -> int:
    # Windows-Konsole ist cp1252: Umlaute und Pfeile sonst UnicodeEncodeError.
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="to_spawn", description="Bau-Sessions einer Spec steuern.")
    unter = ap.add_subparsers(dest="befehl", required=True)

    p_spawn = unter.add_parser("spawn", help="alle Ticket-Sessions einer Spec starten")
    p_spawn.add_argument("spec")
    p_spawn.add_argument("--ziel", choices=["local", "srv"], help="Frage überspringen")
    p_spawn.add_argument("--tickets", help="nur diese Tickets, mit Komma getrennt")
    p_spawn.add_argument("--dry-run", action="store_true", help="nur den Befehl zeigen")

    p_pruefen = unter.add_parser("pruefen", help="Regularien des Manifests prüfen")
    p_pruefen.add_argument("spec")
    p_pruefen.add_argument(
        "--tickets", help="gewählte Tickets (Komma), jedes muss im Manifest stehen"
    )
    p_pruefen.add_argument("--dry-run", action="store_true", help="ohne Wirkung, nur Lesen")
    p_pruefen.add_argument(
        "--ohne-github",
        action="store_true",
        help="GitHub-Teil auslassen (Kanten, Checkpoint, Belegung, Zustände)",
    )

    p_log = unter.add_parser("log", help="Gesamt-Tabelle aus dem Bau-Log")
    p_log.add_argument("spec")

    p_lern = unter.add_parser("lernstoff", help="Lernstoff-Zeilen für /to-tickets")
    p_lern.add_argument("--letzte", type=int, default=30)

    unter.add_parser("hook-stop", help="Stop-Hook (JSON auf stdin)")
    unter.add_parser("hook-subagent-stop", help="SubagentStop-Hook (JSON auf stdin)")

    args = ap.parse_args(argv)
    if not args.befehl.startswith("hook-"):
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    repo = config.repo_wurzel()
    if args.befehl in ("spawn", "pruefen") and not args.dry_run:
        config.sicherstellen(repo)
    konfig = config.lade(repo)

    if args.befehl == "spawn":
        return spawn_modul.spawn(
            repo,
            args.spec,
            konfig,
            ziel=args.ziel,
            tickets=_tickets(args.tickets),
            dry_run=args.dry_run,
        )
    if args.befehl == "pruefen":
        bericht = manifest.pruefe(
            repo,
            args.spec,
            konfig,
            mit_github=not args.ohne_github,
            auswahl=_tickets(args.tickets),
        )
        print(bericht.text())
        return 0 if bericht.sauber else manifest.EXIT_WEIGERUNG
    if args.befehl == "log":
        print(bau_log.tabelle(repo, _spec_tickets(repo, args.spec)))
        return 0
    if args.befehl == "lernstoff":
        print(bau_log.lernstoff(repo, args.letzte))
        return 0
    if args.befehl == "hook-stop":
        return hooks.hook_stop()
    if args.befehl == "hook-subagent-stop":
        return hooks.hook_subagent_stop()
    ap.error(f"Unbekannter Befehl: {args.befehl}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
