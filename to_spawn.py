"""CLI des Skills ``to-spawn`` (Version 2, vorläufiger Kern).

Aufruf (im Repo-Wurzelordner):

    python ~/.claude/skills/to-spawn/to_spawn.py spawn <S> [--ziel local|srv]
    python ~/.claude/skills/to-spawn/to_spawn.py pruefen <S> [--tickets a,b] [--ohne-github]
    python ~/.claude/skills/to-spawn/to_spawn.py log <S>
    python ~/.claude/skills/to-spawn/to_spawn.py lernstoff [--letzte 30]
    python ~/.claude/skills/to-spawn/to_spawn.py setup [--zeigen|--standard|--terminal …]
    python ~/.claude/skills/to-spawn/to_spawn.py deploy-status [--datei P] [--ticket N] [--still-min 60]
    python ~/.claude/skills/to-spawn/to_spawn.py hook-stop          # JSON auf stdin
    python ~/.claude/skills/to-spawn/to_spawn.py hook-subagent-stop # JSON auf stdin
    python ~/.claude/skills/to-spawn/to_spawn.py eintrag --ticket <N> --typ zusammenfassung \
        --umfang "…" --schwierigkeiten "…" --entscheidungen "…" [--repo <pfad>]
    python ~/.claude/skills/to-spawn/to_spawn.py umzug <N> --handoff <pfad> [--dry-run]
    python ~/.claude/skills/to-spawn/to_spawn.py umzug-alle <S> [--ohne-wache] [--warte-max s] [--dry-run]
    python ~/.claude/skills/to-spawn/to_spawn.py hook-umzug         # JSON auf stdin (#212)
    python ~/.claude/skills/to-spawn/to_spawn.py nest onboarding|sandbox|secrets|werkzeuge|rechte|auswahl
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from to_spawn import (  # noqa: E402
    bau_log,
    config,
    deploy_status,
    hooks,
    inventur,
    manifest,
    nest,
    setup,
    umzug,
)
from to_spawn import spawn as spawn_modul  # noqa: E402

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


def _setup(repo: Path, args: argparse.Namespace) -> int:
    """Unterbefehl ``setup``: zeigen, Flags setzen oder Dialog über stdin."""
    datei = repo / config.KONFIG_PFAD
    werte = {
        "terminal": args.terminal,
        "modell_ticket": args.modell_ticket,
        "effort_ticket": args.effort_ticket,
        "modell_leicht": args.modell_leicht,
        "effort_leicht": args.effort_leicht,
        "effort_waechter": args.effort_waechter,
        "modell_waechter": args.modell_waechter,
    }
    hat_werte = any(wert is not None for wert in werte.values())
    if args.zeigen and (hat_werte or args.standard or args.dialog):
        print("--zeigen schreibt nichts — ohne Setz-Flags aufrufen.", file=sys.stderr)
        return 2
    if args.dialog and (hat_werte or args.standard):
        print(
            "--dialog fragt alles ab — ohne --standard und Wert-Flags aufrufen.",
            file=sys.stderr,
        )
        return 2
    # Lesbarkeit VOR allem anderen prüfen: nie fragen, um dann nicht speichern zu können.
    try:
        setup.lies_roh(datei)
    except setup.KonfigUnlesbar as fehler:
        print(
            f"{fehler} — nichts geschrieben. Datei reparieren oder löschen.",
            file=sys.stderr,
        )
        return 1
    konfig = config.lade(repo)
    if args.zeigen:
        print(setup.zeige(konfig, datei, args.plattform))
        return 0

    modelle = [mid for mid, _ in setup.MODELLE]
    erlaubt = {
        "terminal": setup.waehlbare_terminals(args.plattform),
        "modell_ticket": modelle,
        "modell_leicht": modelle,
        "modell_waechter": modelle,
        "effort_ticket": setup.EFFORTS,
        "effort_leicht": setup.EFFORTS,
        "effort_waechter": setup.EFFORTS,
    }
    for name, wert in werte.items():
        if wert is not None and wert not in erlaubt[name]:
            flag = "--" + name.replace("_", "-")
            print(
                f"Ungültig: {flag} {wert} — erlaubt: {', '.join(erlaubt[name])}",
                file=sys.stderr,
            )
            return 2
    if args.modell_waechter and args.modell_waechter != setup.FLAGGSCHIFF:
        print(
            f"Hinweis: Wächter läuft immer auf {setup.FLAGGSCHIFF} — Angabe ignoriert."
        )

    if args.standard or hat_werte:
        # --standard = Basis, gesetzte Flags gewinnen darüber.
        aenderungen: dict = (
            setup.standards(konfig, args.plattform)
            if args.standard
            else {"modelle": {}, "effort": {}}
        )
        if args.terminal:
            aenderungen["terminal"] = args.terminal
        for rolle, modell, effort in (
            ("ticket", args.modell_ticket, args.effort_ticket),
            ("ticket_leicht", args.modell_leicht, args.effort_leicht),
            ("waechter", None, args.effort_waechter),
        ):
            if modell:
                aenderungen["modelle"][rolle] = modell
            if effort:
                aenderungen["effort"][rolle] = effort
    elif args.dialog or sys.stdin.isatty():
        aenderungen = setup.fuehre_dialog(
            konfig, setup.stdin_eingabe(sys.stdout), sys.stdout, args.plattform
        )
    else:
        print(
            "Kein Terminal: `--standard` oder Werte als Flags angeben (Optionen: `--zeigen`).",
            file=sys.stderr,
        )
        return 2
    try:
        gespeichert = setup.speichere(repo, aenderungen, args.plattform)
    except setup.KonfigUnlesbar as fehler:
        print(
            f"{fehler} — nichts geschrieben. Datei reparieren oder löschen.",
            file=sys.stderr,
        )
        return 1
    stand = setup.standards(config.lade(repo), args.plattform)
    print(f"\nGespeichert: {gespeichert}")
    print(f"  terminal ({setup.plattform_von(args.plattform)}): {stand['terminal']}")
    for rolle in setup.ROLLEN:
        modell = stand["modelle"][rolle]
        print(
            f"  {rolle}: {setup.modell_name(modell)} ({modell}), "
            f"Effort {stand['effort'][rolle]}"
        )
    print(setup.SCHLUSS_SATZ)
    return 0


def _eintrag(args: argparse.Namespace) -> int:
    """``eintrag``: eine Klartext-Zeile (Zusammenfassung oder Entscheidung) anhängen."""
    repo = Path(args.repo).expanduser() if args.repo else bau_log.log_repo()
    if repo is None or not repo.is_dir():
        log.error("Kein Bau-Log-Ordner (TO_SPAWN_LOG_REPO fehlt auf der Platte?) — nichts geschrieben.")
        return 1
    ticket = args.ticket or bau_log.ticket_aus_umgebung(repo)
    if not ticket or not str(ticket).strip().isdigit():
        log.error("Ticket-Nummer fehlt — --ticket <N> angeben.")
        return 2
    # Einzige Stelle, die die versionierte Datei schreibt: überträgt fehlende
    # Laufdatei-Zeilen der Hooks gleich mit (Fixrunde #204).
    zeile = bau_log.eintrag_schreiben(
        repo,
        str(ticket).strip(),
        args.typ,
        umfang=args.umfang,
        schwierigkeiten=args.schwierigkeiten,
        entscheidungen=args.entscheidungen,
        text=args.text,
        frage=args.frage,
        wahl=args.wahl,
        grund=args.grund,
    )
    print(f"Bau-Log #{zeile['ticket']}: {args.typ} → {bau_log.log_pfad(repo, zeile['ticket'])}")
    return 0


def _hook_stop_mit_umzug() -> int:
    """Ein Stop-Befehl für Bau-Log (#204) und Umzug-Anfrage des Wächters (#212).

    stdin ist nur einmal lesbar. Liegt eine Umzug-Anfrage, gewinnt deren
    Blockier-Antwort; sonst gilt die Ausgabe des Bau-Log-Hooks. Endet immer mit 0.
    """
    log = logging.getLogger("to_spawn.hook_stop")
    roh = sys.stdin.read()
    bau_log_ausgabe = io.StringIO()
    code = hooks.hook_stop(io.StringIO(roh), bau_log_ausgabe)
    umzug_ausgabe = io.StringIO()
    try:
        umzug.hook_umzug_anfrage(roh, umzug_ausgabe)
    except Exception:  # noqa: BLE001 — Hook darf die Session nie stören
        log.exception("Umzug-Teil des Stop-Hooks fehlgeschlagen — Session läuft weiter.")
    sys.stdout.write(umzug_ausgabe.getvalue() or bau_log_ausgabe.getvalue())
    return code


def main(argv: list[str] | None = None) -> int:
    # Windows-Konsole ist cp1252: Umlaute und Pfeile sonst UnicodeEncodeError.
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        prog="to_spawn", description="Bau-Sessions einer Spec steuern."
    )
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
    p_pruefen.add_argument(
        "--dry-run", action="store_true", help="ohne Wirkung, nur Lesen"
    )
    p_pruefen.add_argument(
        "--ohne-github",
        action="store_true",
        help="GitHub-Teil auslassen (Kanten, Checkpoint, Belegung, Zustände)",
    )

    p_log = unter.add_parser("log", help="Gesamt-Tabelle aus dem Bau-Log")
    p_log.add_argument("spec")

    p_lern = unter.add_parser("lernstoff", help="Lernstoff-Zeilen für /to-tickets")
    p_lern.add_argument("--letzte", type=int, default=30)

    p_setup = unter.add_parser(
        "setup", help="Terminal, Modell und Effort je Rolle wählen"
    )
    p_setup.add_argument(
        "--zeigen",
        action="store_true",
        help="Optionen + Werte zeigen, nichts schreiben",
    )
    p_setup.add_argument(
        "--dialog", action="store_true", help="Dialog auch ohne Terminal (stdin gepipt)"
    )
    p_setup.add_argument(
        "--standard", action="store_true", help="alle Standards ohne Fragen"
    )
    p_setup.add_argument("--terminal")
    p_setup.add_argument("--modell-ticket")
    p_setup.add_argument("--effort-ticket")
    p_setup.add_argument("--modell-leicht")
    p_setup.add_argument("--effort-leicht")
    p_setup.add_argument("--effort-waechter")
    p_setup.add_argument(
        "--modell-waechter", help="wird ignoriert: Wächter = immer Flaggschiff"
    )
    p_setup.add_argument(
        "--plattform", choices=["win32", "linux", "darwin"], help="zum Testen"
    )
    p_inv = unter.add_parser("inventur", help="Werkzeug-Inventur (Setup-Wizard Teil 2)")
    p_inv.add_argument("--json", action="store_true", help="JSON statt Liste ausgeben")
    p_inv.add_argument("--abwahl", help="diese Werkzeuge abwählen (Komma)")
    p_inv.add_argument("--anwahl", help="Abwahl dieser Werkzeuge zurücknehmen (Komma)")
    p_inv.add_argument("--schreiben", action="store_true", help="Freigabeliste schreiben")
    p_inv.add_argument("--ausgabe", type=Path, help="anderer Zielpfad für die Freigabeliste")
    p_inv.add_argument(
        "--letzte",
        type=int,
        default=inventur.HISTORIE_VORGABE,
        help="so viele neueste Historie-Dateien (JSONL) auswerten "
        f"(Standard {inventur.HISTORIE_VORGABE}, 0 = keine)",
    )

    p_status = unter.add_parser(
        "deploy-status",
        help="Deploy-Statusdatei lesen, neue Phasen ins Bau-Log (Exit 0/1/2/3/4)",
    )
    p_status.add_argument("--datei", help="Statusdatei (Vorgabe: <Repo>/.deploy_status.jsonl)")
    p_status.add_argument("--ticket", help="Ticket fürs Bau-Log (sonst TO_SPAWN_TICKET/wt-<N>)")
    p_status.add_argument(
        "--still-min",
        type=float,
        default=deploy_status.STILL_MIN_VORGABE,
        help="ab so vielen Minuten ohne neue Phase gilt der Lauf als hängend (Exit 4)",
    )

    unter.add_parser("hook-stop", help="Stop-Hook (JSON auf stdin)")
    unter.add_parser("hook-subagent-stop", help="SubagentStop-Hook (JSON auf stdin)")
    # Umzug einer Bau-Session auf den Bau-Server (/to-spawn-of, #212).
    p_umzug = unter.add_parser("umzug", help="diese Bau-Session auf den Bau-Server umziehen")
    p_umzug.add_argument("ticket")
    p_umzug.add_argument("--handoff", required=True, help="Handoff mit Zeile „Umzug: server“")
    p_umzug.add_argument("--dry-run", action="store_true", help="nur prüfen und Befehle zeigen")
    p_alle = unter.add_parser("umzug-alle", help="alle Sessions einer Spec nacheinander umziehen")
    p_alle.add_argument("spec")
    p_alle.add_argument("--ohne-wache", action="store_true", help="Wächter bleibt lokal")
    p_alle.add_argument("--warte-max", type=float, default=1800, help="Sekunden je Session (1800)")
    p_alle.add_argument("--dry-run", action="store_true", help="nur den Plan zeigen")
    unter.add_parser("hook-umzug", help="Stop-Hook: Umzug-Anfrage des Wächters (JSON auf stdin)")

    p_eintrag = unter.add_parser("eintrag", help="Klartext-Zeile ins Bau-Log des Tickets")
    p_eintrag.add_argument("--ticket", help="Ticket-Nummer (sonst TO_SPAWN_TICKET/wt-<N>)")
    p_eintrag.add_argument(
        "--typ", required=True, choices=["zusammenfassung", "entscheidung", "blockiert"]
    )
    p_eintrag.add_argument("--umfang", help="was gebaut wurde")
    p_eintrag.add_argument("--schwierigkeiten", help="was schwer war")
    p_eintrag.add_argument("--entscheidungen", help="was entschieden wurde")
    p_eintrag.add_argument("--text", help="freier Text")
    # Wächter-Übersicht (#213): Entscheidung als Frage · Wahl · Grund; blockiert braucht --grund.
    p_eintrag.add_argument("--frage", help="Entscheidung: welche Frage")
    p_eintrag.add_argument("--wahl", help="Entscheidung: was gewählt wurde")
    p_eintrag.add_argument("--grund", help="Entscheidung: warum · blockiert: woran es hängt")
    p_eintrag.add_argument(
        "--repo", help="Ordner mit dem Bau-Log (sonst TO_SPAWN_LOG_REPO bzw. Git-Wurzel)"
    )

    nest.richte_parser_ein(unter)

    args = ap.parse_args(argv)
    # Hooks zuerst und ohne Vorarbeit: sie dürfen die Session nie stören (#204).
    if args.befehl == "hook-stop":
        return _hook_stop_mit_umzug()
    if args.befehl == "hook-subagent-stop":
        return hooks.hook_subagent_stop()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.befehl == "eintrag":
        return _eintrag(args)
    if args.befehl == "nest":
        return nest.lauf(args)

    repo = config.repo_wurzel()
    if args.befehl == "inventur":
        # Nur lesen: legt keine Konfig an (kein config.sicherstellen).
        return inventur.lauf(
            repo,
            als_json=args.json,
            abwahl=args.abwahl,
            anwahl=args.anwahl,
            schreiben=args.schreiben,
            ausgabe=args.ausgabe,
            letzte=args.letzte,
        )
    if args.befehl == "setup":
        return _setup(repo, args)
    if args.befehl == "spawn" and not args.dry_run:
        setup.erster_start(
            repo,
            ist_tty=sys.stdin.isatty(),
            eingabe=setup.stdin_eingabe(sys.stdout),
            ausgabe=sys.stdout,
        )
    if args.befehl == "pruefen" and not args.dry_run:
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
    if args.befehl == "deploy-status":
        return deploy_status.deploy_status(
            repo, datei=args.datei, ticket=args.ticket, still_min=args.still_min
        )
    if args.befehl == "hook-umzug":
        return umzug.hook_umzug_anfrage()
    if args.befehl == "umzug":
        return umzug.umzug_einzel(
            args.ticket,
            Path(args.handoff),
            worktree=repo,
            konfig=config.lade(umzug.haupt_repo(repo)),
            dry_run=args.dry_run,
        )
    if args.befehl == "umzug-alle":
        haupt = umzug.haupt_repo(repo)
        return umzug.umzug_alle(
            args.spec,
            repo=haupt,
            konfig=config.lade(haupt),
            warte_max=args.warte_max,
            ohne_wache=args.ohne_wache,
            dry_run=args.dry_run,
        )
    ap.error(f"Unbekannter Befehl: {args.befehl}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
