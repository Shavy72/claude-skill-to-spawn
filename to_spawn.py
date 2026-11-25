"""CLI des Skills ``to-spawn`` (Version 2, vorläufiger Kern).

Aufruf (im Repo-Wurzelordner):

    python ~/.claude/skills/to-spawn/to_spawn.py spawn <S> [--ziel local|srv]
    python ~/.claude/skills/to-spawn/to_spawn.py stand            # Skill-Stand Bau-Server sichern (#325)
    python ~/.claude/skills/to-spawn/to_spawn.py regularien <S> [--tickets a,b] [--dry-run] \
        [--ohne-einrichtung] [--ohne-github]  # vorher Einrichtungs-Ticket (#410): Anlegen
        # scheitert oder Stand unklar (gh api) = Exit 3 + Schlusszeile „EINRICHTUNG FEHLT“;
        # --tickets, --dry-run, --ohne-einrichtung (= --nur-wache), Spec läuft schon: nur Hinweis
    python ~/.claude/skills/to-spawn/to_spawn.py pruefen <basis> <ziel> [--repo <pfad>]  # Exit 0/1/2 + JSON (#99)
    python ~/.claude/skills/to-spawn/to_spawn.py anhaengen <S> <N> [--blockiert-von B,…] \
        [--abnahme A,…|keine] [--schaetzung K] [--umfang TEXT] [--datei PFAD:a-b …] [--trocken]  # Ticket nachtragen (E2-2)
    python ~/.claude/skills/to-spawn/to_spawn.py befund --spec <S> --ticket <N> --stufe gelb --text "…"  # Folge-Ticket
    python ~/.claude/skills/to-spawn/to_spawn.py spec-schliessen <S>  # Spec schließen mit Sperre (E2-4, Exit 0/1)
    python ~/.claude/skills/to-spawn/to_spawn.py hook-spec-sperre    # PreToolUse-Hook (Bash), JSON auf stdin
    python ~/.claude/skills/to-spawn/to_spawn.py log <S>
    python ~/.claude/skills/to-spawn/to_spawn.py lernstoff [--letzte 30]
    python ~/.claude/skills/to-spawn/to_spawn.py setup [--zeigen|--standard|--terminal …]
    python ~/.claude/skills/to-spawn/to_spawn.py einrichten [--pruefen]  # Repo einrichten (Exit 0/1, --pruefen 0/3)
    python ~/.claude/skills/to-spawn/to_spawn.py ticket-repo [<eintrag> <ticket_repo>|--nachziehen]  # Maschinen-Liste (#261)
    python ~/.claude/skills/to-spawn/to_spawn.py probesitz [--punkt N …] [--zeigen] [--modell M]  # 7-Punkte-Abnahme (#214)
    python ~/.claude/skills/to-spawn/to_spawn.py deploy-status [--datei P] [--ticket N] [--still-min 60]
    python ~/.claude/skills/to-spawn/to_spawn.py hook-stop          # JSON auf stdin
    python ~/.claude/skills/to-spawn/to_spawn.py hook-subagent-stop # JSON auf stdin
    python ~/.claude/skills/to-spawn/to_spawn.py eintrag --ticket <N> --typ zusammenfassung \
        --umfang "…" --schwierigkeiten "…" --entscheidungen "…" [--repo <pfad>]
    python ~/.claude/skills/to-spawn/to_spawn.py umzug <N> --handoff <pfad> [--dry-run]
    python ~/.claude/skills/to-spawn/to_spawn.py umzug-alle <S> [--ohne-wache] [--warte-max s] [--dry-run]
    python ~/.claude/skills/to-spawn/to_spawn.py hook-umzug         # JSON auf stdin (#212)
    python ~/.claude/skills/to-spawn/to_spawn.py nest onboarding|sandbox|secrets|werkzeuge|rechte|auswahl

Parser-Aufbau und Verteilung der Unterbefehle liegen in :mod:`to_spawn.cli_parser`
und :mod:`to_spawn.cli_befehle` (#387).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from to_spawn import cli_befehle, cli_parser, config

ARGUMENTFEHLER_EXIT = cli_parser.ARGUMENTFEHLER_EXIT


def main(argv: list[str] | None = None) -> int:
    # Windows-Konsole ist cp1252: Umlaute und Pfeile sonst UnicodeEncodeError.
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(encoding="utf-8", errors="replace")
    ap = cli_parser.baue_parser()
    args = ap.parse_args(argv)
    try:
        return cli_befehle.verteilen(ap, args)
    except config.KeinHeimOrdner as fehler:
        print(f"FEHLER: {fehler}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
