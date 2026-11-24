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
        [--schaetzung K] [--umfang TEXT] [--datei PFAD:a-b …] [--trocken]  # Ticket nachtragen (E2-2)
    python ~/.claude/skills/to-spawn/to_spawn.py befund --spec <S> --ticket <N> --stufe gelb --text "…"  # Folge-Ticket
    python ~/.claude/skills/to-spawn/to_spawn.py spec-schliessen <S>  # Spec schließen mit Sperre (E2-4, Exit 0/1)
    python ~/.claude/skills/to-spawn/to_spawn.py hook-spec-sperre    # PreToolUse-Hook (Bash), JSON auf stdin
    python ~/.claude/skills/to-spawn/to_spawn.py log <S>
    python ~/.claude/skills/to-spawn/to_spawn.py lernstoff [--letzte 30]
    python ~/.claude/skills/to-spawn/to_spawn.py setup [--zeigen|--standard|--terminal …]
    python ~/.claude/skills/to-spawn/to_spawn.py einrichten [--pruefen]  # Repo einrichten (Exit 0/1, --pruefen 0/3)
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
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from to_spawn import (  # noqa: E402
    arbeitsstand,
    aufpasser,
    aufseher_stand,
    bau_log,
    befund,
    briefkasten,
    briefkasten_cli,
    config,
    deploy_status,
    einrichten,
    einrichtungs_ticket,
    gh,
    hausmeister,
    hausmeister_aufgabe,
    hooks,
    inventur,
    komplett_lauf,
    leiter,
    leitstand,
    manifest,
    nest,
    probesitz,
    pruef,
    respawn,
    setup,
    spec_rand,
    speicher,
    startklar,
    ticket_zustand,
    ticketstand,
    uebergabe_sichern,
    umzug,
    vorfall,
    waechter_takt,
)
from to_spawn import einreichen as einreichen_modul  # noqa: E402
from to_spawn import spawn as spawn_modul  # noqa: E402
from to_spawn import stand as stand_modul  # noqa: E402
from to_spawn.pruefplan import Moment

log = logging.getLogger("to_spawn")


#: Unterbefehl ``komplett --moment``: Text der Kommandozeile → Moment (anderes lehnt argparse mit Exit 2 ab).
_KOMPLETT_MOMENTE = {"vor-live": Moment.VOR_LIVE, "alle-auf-staging": Moment.ALLE_AUF_STAGING}


def _tickets(wert: str | None) -> list[str] | None:
    if not wert:
        return None
    return [teil.strip() for teil in wert.split(",") if teil.strip()]


def _spec_tickets(repo: Path, spec: str, *, mit_briefkasten: bool = False) -> list[str]:
    """Tickets der Spec aus dem Manifest; ohne Manifest alle aus dem Bau-Log.

    ``mit_briefkasten`` (nur die Entscheidungs-Liste): ohne Manifest zählen auch Tickets, die nur im
    Briefkasten stehen (#278 P3b) — sonst fehlten Aufseher-Antworten ohne Bau-Log-Zeile.
    """
    try:
        daten = manifest.lade_manifest(repo, spec)
    except (FileNotFoundError, ValueError):
        tickets = set(bau_log.alle_tickets(repo))
        if mit_briefkasten:
            tickets.update(briefkasten.alle_tickets())
        return sorted(tickets, key=manifest.ticket_schluessel)
    return sorted(daten["tickets"], key=manifest.ticket_schluessel)


def _regularien_optionen(p: argparse.ArgumentParser) -> None:
    p.add_argument("--tickets", help="gewählte Tickets (Komma), jedes muss im Manifest stehen")
    p.add_argument("--dry-run", action="store_true", help="ohne Wirkung, nur Lesen")
    p.add_argument(
        "--ohne-einrichtung",
        action="store_true",
        help="Teil-Start ohne Ticket-Fenster (--nur-wache): Einrichtungs-Ticket nur melden",
    )
    p.add_argument(
        "--ohne-github",
        action="store_true",
        help="GitHub-Teil auslassen (Kanten, Checkpoint, Belegung, Zustände)",
    )


def _pruefen(ap: argparse.ArgumentParser, args: argparse.Namespace) -> int | None:
    """``pruefen <basis> <ziel>`` = Tür des Prüf-Werkzeugkastens (#99).

    ``pruefen <S>`` (ein Argument) ist der alte Name von ``regularien``: Warnung auf stderr,
    ``args`` wird umgeschrieben, Rückgabe ``None`` = weiter wie ``regularien``.
    """
    if args.ziel is None:
        print(
            f"WARNUNG: „to_spawn.py pruefen {args.basis}“ heißt jetzt "
            f"„to_spawn.py regularien {args.basis}“ — bitte umstellen; "
            "„pruefen <basis> <ziel>“ ist der Prüf-Werkzeugkasten.",
            file=sys.stderr,
        )
        args.befehl, args.spec = "regularien", args.basis
        return None
    if args.tickets or args.dry_run or args.ohne_github or args.ohne_einrichtung:
        ap.error(
            "--tickets/--dry-run/--ohne-einrichtung/--ohne-github gelten nur für „regularien <S>“"
        )
    repo = config.repo_wurzel(args.repo.expanduser() if args.repo else _repo_aus_umgebung())
    urteil = pruef.pruefen(repo, args.basis, args.ziel)
    print(pruef.protokoll.text(urteil.zeile))
    return urteil.exit


def _repo_aus_umgebung() -> Path | None:
    """``TO_SPAWN_REPO`` (setzt die Weiterleitung im Repo, #205) oder ``None`` = aktueller Ordner."""
    wert = os.environ.get("TO_SPAWN_REPO", "").strip()
    return Path(wert).expanduser() if wert else None


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
        print(setup.zeige(konfig, datei, args.plattform, repo=repo))
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
            f"Hinweis: Aufseher läuft immer auf {setup.FLAGGSCHIFF} — Angabe ignoriert."
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
    # Checkpoint-Label im GitHub-Repo anlegen (#257) — Weigerung „Kein Checkpoint-Ticket“ vorbeugen.
    label = manifest.checkpoint_label(config.lade(repo))
    if gh.label_sicherstellen(repo, label):
        print(f"  Label {label} im GitHub-Repo vorhanden.")
    else:
        print(f"  Label {label} nicht angelegt — von Hand: gh label create {label}")
    print()
    print(setup.probesitz_block(repo))
    return 0


def _aufraeumen(args: argparse.Namespace, ap: argparse.ArgumentParser) -> int:
    """Hausmeister-CLI (#1368/#1373); Exit 1 nur bei Fehlern, „bleibt“ ist keiner."""
    if args.aufgabe_einrichten:
        try:
            print(hausmeister_aufgabe.aufgabe_einrichten(trocken=args.trocken))
        except RuntimeError as fehler:
            print(f"Hausmeister-Aufgabe nicht eingerichtet: {fehler}", file=sys.stderr)
            return 1
        return 0
    if args.altbestand_liste:
        try:
            print(f"Altbestand-Liste: {hausmeister_aufgabe.loesch_liste(args.altbestand_liste)}")
        except (RuntimeError, OSError) as fehler:
            print(f"Altbestand-Liste nicht geschrieben: {fehler}", file=sys.stderr)
            return 1
        return 0
    if args.zweige_probelauf:
        try:
            print(hausmeister.zweige_probelauf(args.zweige_probelauf))
        except OSError as fehler:
            print(f"Probelauf-Liste nicht geschrieben: {fehler}", file=sys.stderr)
            return 1
        return 0
    if args.zweig:  # Zweig-Tür (#1439), z. B. aus der Deploy-Schlange
        if not args.repo:
            ap.error("aufraeumen --zweig braucht --repo")
        erg = hausmeister.zweig_aufraeumen(
            args.repo, args.zweig, uebernommen_tip=args.uebernommen_tip, loeschen=args.loeschen
        )
        print(f"{erg.zweig}: {erg.art} ({erg.grund}) [{'trocken' if erg.trocken else 'echt'}]")
        return 1 if erg.fehler else 0
    if args.alles:
        ergebnisse = hausmeister.aufraeumen_alles(loeschen=args.loeschen)
    elif args.ordner:
        ergebnisse = [hausmeister.aufraeumen(args.ordner, loeschen=args.loeschen)]
    else:
        ap.error("aufraeumen: Ordner, --alles, --aufgabe-einrichten oder --altbestand-liste angeben")
    for erg in ergebnisse:
        modus = "trocken" if erg.trocken else "echt"
        print(f"{erg.ordner}: {erg.art} ({erg.grund}) [{modus}]")
    return 1 if any(e.fehler for e in ergebnisse) else 0


def _befund(args: argparse.Namespace) -> int:
    """Unterbefehl ``befund`` (#438): Aufseher meldet gelb oder rot, das Modul handelt."""
    repo = config.repo_wurzel(_repo_aus_umgebung())
    gh_repo = gh.repo_aus_origin(repo)
    if not gh_repo:
        print("FEHLER: kein GitHub-Repo erkannt (origin fehlt oder zeigt nicht auf github.com)")
        return 2
    ergebnis = befund.melde(
        repo, gh_repo, args.spec, args.ticket, args.stufe, args.text, dry_run=args.dry_run
    )
    for zeile in ergebnis.zeilen:
        print(zeile)
    return 0 if ergebnis.ok else 1


def _spec_schliessen(args: argparse.Namespace) -> int:
    """Unterbefehl ``spec-schliessen`` (E2-4): schließt die Spec nur ohne offene Folge-Tickets."""
    repo = config.repo_wurzel(_repo_aus_umgebung())
    gh_repo = gh.repo_aus_origin(repo)
    if not gh_repo:
        print("FEHLER: kein GitHub-Repo erkannt (origin fehlt oder zeigt nicht auf github.com)")
        return 2
    ergebnis = befund.spec_schliessen(repo, gh_repo, args.spec)
    for zeile in ergebnis.zeilen:
        print(zeile)
    return 0 if ergebnis.ok else 1


def _blocker(args: argparse.Namespace) -> int:
    """Unterbefehl ``blocker`` (#267): Warte-Prüfung der Bau-Session, Regel nur in ``gh.blocker_erledigt``.

    Genau eine Zeile: ``FREI`` (Exit 0) oder ``WARTET: <Gründe>`` (Exit 1). Gründe aus
    ``gh.blocker_gruende`` (erledigt + Commit auf dem Hauptzweig), wie die Start-Schranke
    ohne ``--sofort``. Scheitert die Abfrage, gilt „wartet“ — nie „frei“.
    """
    repo = config.repo_wurzel(_repo_aus_umgebung())
    gh_repo = args.gh_repo or gh.repo_aus_origin(repo)
    deps = gh.blocked_by(gh_repo, str(args.ticket)) if gh_repo else None
    if deps is None:
        print("WARTET: Blocker-Abfrage fehlgeschlagen")
        return 1
    gruende = gh.blocker_gruende(repo, deps)
    if gruende:
        print("WARTET: " + "; ".join(gruende))
        return 1
    print("FREI")
    return 0


def _einreichen(args: argparse.Namespace) -> int:
    """Unterbefehl ``einreichen`` (#411): fertigen Stand auf den Hauptzweig, Regel nur in ``einreichen``."""
    erg = einreichen_modul.einreichen(Path.cwd(), str(args.nummer))
    print(("EINGEREICHT: " if erg.exit == 0 else "NICHT EINGEREICHT: ") + erg.text)
    return erg.exit


def _zu_bauen(args: argparse.Namespace) -> int:
    """Unterbefehl ``zu-bauen`` (#279): Tickets der Spec, an denen noch jemand bauen muss.

    Eine Nummer je Zeile, Regel nur in :func:`ticket_zustand.fertig_gebaut`. Unbekannter
    Stand (gh-Fehler) → Nummer fehlt, Zeile auf stderr, Exit 1 — fail-closed, nie „zu bauen“.
    """
    repo = config.repo_wurzel(_repo_aus_umgebung())
    code = 0
    for ticket in _spec_tickets(repo, args.spec):
        daten = ticket_zustand.lesen(ticket, repo)
        if daten is None:
            print(f"#{ticket}: Ticket-Stand nicht bestimmbar — nicht gestartet", file=sys.stderr)
            code = 1
        elif not ticket_zustand.fertig_gebaut(daten):
            print(ticket)
    return code


def _ticket_lage(args: argparse.Namespace) -> int:
    """Unterbefehl ``ticket-lage`` (#282): Lage beim erneuten Start, Regel nur in :mod:`to_spawn.wiederanlauf`.

    Genau eine Zeile: Art (``neu``/``fortsetzen``/``abschluss``/``nichts``), bei ``abschluss``
    dazu was fehlt — Exit 0. Stand nicht bestimmbar → ``unbekannt: …``, Exit 2 (fail-closed).
    """
    from to_spawn import wiederanlauf

    repo = config.repo_wurzel(_repo_aus_umgebung())
    lage = wiederanlauf.feststellen(args.ticket, repo)
    if lage is None:
        print(f"unbekannt: Stand von #{args.ticket} nicht bestimmbar — nichts starten")
        return 2
    print(lage.zeile())
    return 0


def _anhaengen(args: argparse.Namespace) -> int:
    """Unterbefehl ``anhaengen`` (E2-2): Issue an Spec hängen — Kanten, Manifest, Unter-Issue."""
    repo = config.repo_wurzel(_repo_aus_umgebung())
    ergebnis = spec_rand.anhaengen(
        repo,
        args.spec,
        args.ticket,
        config.lade(repo),
        blockiert_von=_tickets(args.blockiert_von) or [],
        schaetzung_k=args.schaetzung,
        umfang=args.umfang,
        dateien=args.datei,
        trocken=args.trocken,
    )
    for zeile in ergebnis.zeilen:
        print(zeile)
    return ergebnis.exit


def _eintrag(args: argparse.Namespace) -> int:
    """``eintrag``: eine Klartext-Zeile (Zusammenfassung oder Entscheidung) anhängen."""
    # Versionierte Datei: nie in den Hauptbaum ausweichen (#257, ``versioniert=True``).
    repo = Path(args.repo).expanduser() if args.repo else bau_log.log_repo(versioniert=True)
    if repo is None or not repo.is_dir():
        log.error("Kein Bau-Log-Ordner (TO_SPAWN_LOG_REPO fehlt auf der Platte?) — nichts geschrieben.")
        return 1
    ticket = args.ticket or bau_log.ticket_aus_umgebung(repo)
    if not ticket or not str(ticket).strip().isdigit():
        log.error("Ticket-Nummer fehlt — --ticket <N> angeben.")
        return 2
    zusatz: dict[str, str] = {}
    if args.typ == "vorfall":
        try:
            zusatz = vorfall.eintrag_felder(
                args.klasse or "",
                args.symptom or "",
                args.ursache or "",
                args.loesung or "",
                args.schutz or "",
                args.beispiel or "",
            )
            zusatz["quelle"] = vorfall.SESSION_QUELLE
        except ValueError as fehler:
            log.error("%s", fehler)
            return 2
    # Einzige Stelle, die die versionierte Datei schreibt: überträgt fehlende
    # Laufdatei-Zeilen der Hooks gleich mit (Fixrunde #204) — auch die aus dem
    # Hauptbaum, die vor dem Anlegen des Worktrees dort landeten (#257).
    zeile = bau_log.eintrag_schreiben(
        repo,
        str(ticket).strip(),
        args.typ,
        hauptbaum=bau_log.log_rueckfall(),
        umfang=args.umfang,
        schwierigkeiten=args.schwierigkeiten,
        entscheidungen=args.entscheidungen,
        text=args.text,
        frage=args.frage,
        wahl=args.wahl,
        grund=args.grund,
        **zusatz,
    )
    if args.typ == "vorfall":
        # Damit der Aufseher den Vorfall schon vor Commit + Push sieht (#286).
        bau_log.spiegel_in_laufdatei(repo, zeile["ticket"], zeile)
    print(f"Bau-Log #{zeile['ticket']}: {args.typ} → {bau_log.log_pfad(repo, zeile['ticket'])}")
    return 0


def _entscheidungen(args: argparse.Namespace) -> int:
    """``entscheidungen S``: Liste aller Entscheidungen einer Spec (#278, Tür B)."""
    repo = config.repo_wurzel(_repo_aus_umgebung())
    for z in briefkasten.liste(_spec_tickets(repo, args.spec, mit_briefkasten=True), log_repo=repo):
        print(f"#{z['ticket']} · {z['wer']} · {z['was']} · {z['warum']} · {z['beleg']}")
    return 0


def _umrechnen(args: argparse.Namespace) -> int:
    """``umrechnen``: alten Bau-Log-Zeilen den Spitzen-Kontext nachtragen (#238)."""
    repo = Path(args.repo).expanduser() if args.repo else bau_log.log_repo()
    if repo is None or not repo.is_dir():
        log.error("Kein Bau-Log-Ordner — --repo <Ordner> angeben.")
        return 1
    ticket = str(args.ticket or bau_log.ticket_aus_umgebung(repo) or "").strip()
    if not ticket.isdigit():
        log.error("Ticket-Nummer fehlt — --ticket <N> angeben.")
        return 2
    transkripte = Path(args.transkripte).expanduser()
    if not transkripte.is_dir():
        log.error("Transkript-Ordner %s fehlt.", transkripte)
        return 1
    umgerechnet, ohne = hooks.umrechnen(repo, ticket, transkripte)
    z = bau_log.zusammenfassung(repo, ticket)
    ist = f"{z['ist_k']:g}k" if z["ist_k"] else "—"
    print(
        f"Bau-Log #{ticket}: {umgerechnet} Zeile(n) umgerechnet, {ohne} ohne Transkript "
        f"· Kontext jetzt {ist}"
    )
    return 0


def _hook_stop_mit_umzug() -> int:
    """Ein Stop-Befehl für Bau-Log (#204) und Umzug-Anfrage des Aufsehers (#212).

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

    p_neu = unter.add_parser("neustart", help="ein Ticket neu starten (Aufseher-Einzeiler)")
    p_neu.add_argument("spec", type=int)
    p_neu.add_argument("ticket", type=int)
    p_neu.add_argument("--ziel", choices=["local", "srv"], default="local", help="local = dieser Rechner")
    p_neu.add_argument("--handoff", default="", help="Handoff-Datei: neue Session setzt dort fort")
    p_neu.add_argument("--beenden", action="store_true", help="laufende Claude-Session vorher beenden")
    p_neu.add_argument("--dry-run", action="store_true", help="nur zeigen, nichts starten/beenden")

    p_resp = unter.add_parser(
        "respawn", help="Bau-Session nach SOP a–e ablösen (#431), stdout genau eine Zeile"
    )
    p_resp.add_argument("spec", type=int)
    p_resp.add_argument("ticket", type=int)
    p_resp.add_argument(
        "--warte-max",
        type=float,
        default=respawn.WARTE_MAX_VORGABE,
        help="max. Sekunden auf Handoff + Start-Prompt (1800)",
    )
    p_resp.add_argument("--dry-run", action="store_true", help="nur Duplikat-Prüfung + geplanter Befehl")

    p_leiter = unter.add_parser(
        "leiter", help="Eingriffs-Leiter je Ticket (#432), stdout genau eine Zeile"
    )
    p_leiter.add_argument("spec", type=int)
    p_leiter.add_argument("ticket", type=int)
    p_leiter.add_argument("--gh-repo", default="", help="owner/name (sonst aus origin)")
    p_leiter.add_argument("--dry-run", action="store_true", help="nur entscheiden, nichts tippen/merken")

    unter.add_parser(
        "stand", help="Skill-Stand des Bau-Servers prüfen, bei Abweichung pushen (#325)"
    )

    p_regularien = unter.add_parser("regularien", help="Regularien des Manifests prüfen")
    p_regularien.add_argument("spec")
    _regularien_optionen(p_regularien)
    # Prüf-Werkzeugkasten (#99); ``pruefen <S>`` (nur ein Argument) leitet auf ``regularien``.
    p_pruefen = unter.add_parser(
        "pruefen", help="Stand prüfen: Exit 0 grün, 1 rot, 2 Gate-Fehler, JSON-Zeile auf stdout"
    )
    p_pruefen.add_argument("basis", help="Ausgangsstand (git-Ref)")
    p_pruefen.add_argument("ziel", nargs="?", help="zu prüfender Stand (git-Ref, ausgecheckt)")
    p_pruefen.add_argument("--repo", type=Path, help="Repo-Ordner (Vorgabe: aktueller Ordner)")
    _regularien_optionen(p_pruefen)  # nur für die Weiterleitung ``pruefen <S>``

    # Spec-Rand (E2-2): ein Issue nachträglich an eine Spec hängen (= „Ticket nachtragen“).
    p_anh = unter.add_parser(
        "anhaengen", help="Issue an Spec hängen: Blocker-Kanten, Abnahme-Kante, Manifest, Unter-Issue"
    )
    p_anh.add_argument("spec", type=int, help="Spec-Nummer (Eltern-Issue + Manifest)")
    p_anh.add_argument("ticket", type=int, help="anzuhängendes Issue")
    p_anh.add_argument("--blockiert-von", default="", metavar="B,…", help="Blocker-Tickets (Komma)")
    p_anh.add_argument("--schaetzung", type=float, metavar="K", help="schaetzung_k (Tausend Token)")
    p_anh.add_argument("--umfang", metavar="TEXT", help="umfang (ein Satz Klartext)")
    p_anh.add_argument(
        "--datei", action="append", default=[], metavar="PFAD:a-b", help="files-Eintrag (mehrfach)"
    )
    p_anh.add_argument("--trocken", action="store_true", help="Schritte zeigen, nichts schreiben")
    p_log = unter.add_parser("log", help="Gesamt-Tabelle aus dem Bau-Log")
    p_log.add_argument("spec")

    p_lern = unter.add_parser("lernstoff", help="Lernstoff-Zeilen für /to-tickets")
    p_lern.add_argument("--letzte", type=int, default=30)

    p_einr = unter.add_parser("einrichten", help="Repo für to-spawn einrichten (Soll-Liste)")
    p_einr.add_argument(
        "--pruefen",
        action="store_true",
        help="nur prüfen, nichts schreiben (Exit 0 = alles da und aktuell, 3 = Abweichung)",
    )
    p_setup = unter.add_parser(
        "setup", help="Terminal, Modell und Effort je Rolle wählen"
    )
    p_setup.add_argument(
        "--zeigen",
        action="store_true",
        help="Optionen + Werte zeigen, nichts schreiben",
    )
    p_probe = unter.add_parser(
        "probesitz", help="7-Punkte-Abnahme des Setups selbst fahren (#214)"
    )
    p_probe.add_argument(
        "--punkt",
        action="append",
        type=int,
        choices=range(1, 8),
        help="nur diesen Punkt (1–7), mehrfach möglich",
    )
    p_probe.add_argument(
        "--zeigen", action="store_true", help="nur den gemerkten Stand zeigen, nichts prüfen"
    )
    p_probe.add_argument("--modell", help="Modell der Wegwerf-Session (Vorgabe claude-sonnet-5)")
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
        "--modell-waechter", help="wird ignoriert: Aufseher = immer Flaggschiff"
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

    unter.add_parser(
        "hauptzweig", help="Name des Hauptzweigs ausgeben (Konfig, origin/HEAD, master/main) (#257)"
    )
    p_arbeit = unter.add_parser(
        "arbeitsstand",
        help="Erst-Start: Arbeitsordner auf origin/<Hauptzweig> nachziehen (nur fast-forward) oder Abbruch; "
        "fetch-Fehler/anderer Zweig nur Hinweis (#283)",
    )
    p_arbeit.add_argument("--pruefen", action="store_true", help="nur prüfen, nichts nachziehen")
    # Warte-Prüfung der Bau-Session (#267): dieselbe Regel wie die Start-Schranke.
    p_blocker = unter.add_parser(
        "blocker", help="Vorgänger erledigt + Commit auf Hauptzweig? Exit 0 = FREI, 1 = WARTET + Gründe (#267)"
    )
    p_blocker.add_argument("ticket", type=int)
    p_blocker.add_argument("--gh-repo", default="", help="owner/name (sonst aus origin)")
    # Fertig einreichen (#411): der eine Weg auf den Hauptzweig, ohne Frage und ohne Takt.
    p_einreichen = unter.add_parser(
        "einreichen",
        help="fertigen, geprüften Stand auf den Hauptzweig bringen (im eigenen Worktree; Exit 0/1/5)",
    )
    p_einreichen.add_argument("nummer", help="Ticket-Nummer (Bau-Session) bzw. Spec-Nummer (Aufseher)")
    p_zu_bauen = unter.add_parser(
        "zu-bauen", help="Tickets der Spec, die noch nicht fertig gebaut sind (eine Nummer je Zeile, #279)"
    )
    p_zu_bauen.add_argument("spec")
    p_lage = unter.add_parser(
        "ticket-lage", help="Lage beim erneuten Start: neu/fortsetzen/abschluss/nichts + fehlt (Exit 0, unbekannt 2, #282)"
    )
    p_lage.add_argument("ticket", type=int)
    unter.add_parser("hook-stop", help="Stop-Hook (JSON auf stdin)")
    unter.add_parser("hook-subagent-stop", help="SubagentStop-Hook (JSON auf stdin)")
    unter.add_parser(
        "hook-spec-sperre", help="PreToolUse-Hook: rohes `gh issue close <Spec>` sperren (E2-4)"
    )
    p_schliessen = unter.add_parser(
        "spec-schliessen", help="Spec schließen, nur wenn alle Folge-Tickets erledigt sind (E2-4)"
    )
    p_schliessen.add_argument("spec", type=int)
    # Umzug einer Bau-Session auf den Bau-Server (/to-spawn-of, #212).
    p_umzug = unter.add_parser("umzug", help="diese Bau-Session auf den Bau-Server umziehen")
    p_umzug.add_argument("ticket")
    p_umzug.add_argument("--handoff", required=True, help="Handoff mit Zeile „Umzug: server“")
    p_umzug.add_argument("--dry-run", action="store_true", help="nur prüfen und Befehle zeigen")
    p_alle = unter.add_parser("umzug-alle", help="alle Sessions einer Spec nacheinander umziehen")
    p_alle.add_argument("spec")
    p_alle.add_argument("--ohne-wache", action="store_true", help="Aufseher bleibt lokal")
    p_alle.add_argument("--warte-max", type=float, default=1800, help="Sekunden je Session (1800)")
    p_alle.add_argument("--dry-run", action="store_true", help="nur den Plan zeigen")
    unter.add_parser("hook-umzug", help="Stop-Hook: Umzug-Anfrage des Aufsehers (JSON auf stdin)")

    p_eintrag = unter.add_parser("eintrag", help="Klartext-Zeile ins Bau-Log des Tickets")
    p_eintrag.add_argument("--ticket", help="Ticket-Nummer (sonst TO_SPAWN_TICKET/wt-<N>)")
    p_eintrag.add_argument(
        "--typ",
        required=True,
        choices=["zusammenfassung", "entscheidung", "blockiert", "frage", "vorfall"],
    )
    p_eintrag.add_argument("--umfang", help="was gebaut wurde")
    p_eintrag.add_argument("--schwierigkeiten", help="was schwer war")
    p_eintrag.add_argument("--entscheidungen", help="was entschieden wurde")
    p_eintrag.add_argument("--text", help="freier Text")
    # Aufseher-Übersicht (#213): Entscheidung als Frage · Wahl · Grund; blockiert braucht --grund.
    p_eintrag.add_argument("--frage", help="Entscheidung: welche Frage")
    p_eintrag.add_argument("--wahl", help="Entscheidung: was gewählt wurde")
    p_eintrag.add_argument("--grund", help="Entscheidung: warum · blockiert: woran es hängt")
    # Lernschleife (#286): ein Vorfall braucht Klasse, Symptom, Ursache, Lösung.
    p_eintrag.add_argument(
        "--klasse", choices=sorted(vorfall.KLASSEN), help="Vorfall: welche Klasse"
    )
    p_eintrag.add_argument("--symptom", help="Vorfall: was man sieht")
    p_eintrag.add_argument("--ursache", help="Vorfall: woran es lag")
    p_eintrag.add_argument("--loesung", help="Vorfall: was hilft")
    p_eintrag.add_argument("--schutz", help="Vorfall: Schutz-Spalte im Katalog")
    p_eintrag.add_argument("--beispiel", help="Vorfall: Beleg (Ticket, Datum)")
    p_eintrag.add_argument(
        "--repo", help="Ordner mit dem Bau-Log (sonst TO_SPAWN_LOG_REPO bzw. Git-Wurzel)"
    )

    # Tür B (#278 P2): Befugnis prüfen, Frage einwerfen, Antwort lesen/schreiben, Liste je Spec.
    briefkasten_cli.richte_parser_ein(unter)
    p_ents = unter.add_parser("entscheidungen", help="Liste aller Entscheidungen einer Spec")
    p_ents.add_argument("spec")

    p_um = unter.add_parser(
        "umrechnen", help="alte Bau-Log-Zeilen: Spitzen-Kontext aus dem Transkript nachtragen"
    )
    p_um.add_argument("--ticket", help="Ticket-Nummer (sonst TO_SPAWN_TICKET/wt-<N>)")
    p_um.add_argument("--repo", help="Ordner mit dem Bau-Log (sonst TO_SPAWN_LOG_REPO)")
    p_um.add_argument(
        "--transkripte",
        default=str(Path.home() / ".claude" / "projects"),
        help="Claude-Projektordner mit den Transkripten",
    )

    # Speicher-Schutz (#257 Paket B): Stand drucken, Exit 0 = frei, 5 = voll,
    # 7 = Platte voll (#1369); ``--trocken`` misst nur (Probelauf, Status);
    # ``--staffel`` gibt nur die Pause in Sekunden aus (für spawn_srv.sh).
    p_speicher = unter.add_parser(
        "speicher",
        help="darf noch eine Claude-Session starten? (Exit 0 frei / 5 voll / 7 Platte voll)",
    )
    p_speicher.add_argument(
        "--staffel", action="store_true", help="nur die Staffel-Pause in Sekunden ausgeben"
    )
    p_speicher.add_argument(
        "--trocken", action="store_true", help="nur messen, nie aufräumen (Probelauf, Status)"
    )
    p_speicher.add_argument(
        "--sitzung", default=None, help="Obergrenze nur für diese tmux-Sitzung zählen, z. B. spec-12 (#409)"
    )

    # Startklar-Prüfung (#450): venv, Schlüssel, Aufseher-Werkzeuge — Exit 0 ok / 1 rot.
    p_startklar = unter.add_parser("startklar", help="Aufseher-Ordner vor dem Spec-Start prüfen (Exit 0/1)")
    p_startklar.add_argument("spec", type=int)
    p_startklar.add_argument("--ordner", type=Path, default=None, help="Aufseher-Ordner (Vorgabe: Repo-Wurzel)")

    # Komplett-Prüfung einmal je Spec (#425 C2): einzige Tür vor dem Live-Schritt — Exit 0 grün/nicht nötig, 1 sonst.
    p_komplett = unter.add_parser("komplett", help="Komplett-Prüfung der Spec, höchstens einmal (Exit 0/1)")
    p_komplett.add_argument("spec", type=int)
    p_komplett.add_argument("--moment", choices=sorted(_KOMPLETT_MOMENTE), default="vor-live")
    # Nur für den Hintergrund-Lauf aus dem capo-Takt (#455): Rot je Kopf gilt dort als geprüft.
    p_komplett.add_argument("--aus-takt", action="store_true", help=argparse.SUPPRESS)

    nest.richte_parser_ein(unter)
    leitstand.richte_parser_ein(unter)
    waechter_takt.richte_parser_ein(unter)
    aufseher_stand.richte_parser_ein(unter)
    ticketstand.richte_parser_ein(unter)
    uebergabe_sichern.richte_parser_ein(unter)
    # Gelb-Liste (#438): Befund des Aufsehers — gelb → Folge-Ticket, rot → wieder öffnen.
    # E2-4: einzige Tür für Folge-Tickets (auch Bau-Sessions) — nie roh `gh issue create`.
    p_befund = unter.add_parser(
        "befund", help="Befund melden: gelb → Folge-Ticket an der Spec, rot → wieder öffnen"
    )
    p_befund.add_argument(
        "--spec", required=True, type=int, help="Herkunfts-Spec (Manifest, Unter-Issue)"
    )
    p_befund.add_argument("--ticket", required=True, type=int, help="Ticket mit dem Befund")
    p_befund.add_argument("--stufe", required=True, choices=list(befund.STUFEN))
    p_befund.add_argument("--text", required=True, help="was fehlt / der Hinweis")
    p_befund.add_argument("--dry-run", action="store_true", help="nur zeigen, nichts tun")
    # Hausmeister (#1368): alten Arbeitsordner prüfen (Trockenlauf) bzw. aufräumen.
    p_aufr = unter.add_parser("aufraeumen", help="alten Arbeitsordner aufräumen (Vorgabe: Trockenlauf)")
    p_aufr.add_argument("ordner", type=Path, nargs="?")
    p_aufr.add_argument("--loeschen", action="store_true", help="wirklich sichern und löschen")
    # Hausmeister am Laptop (#1373): alle Ordner, tägliche Windows-Aufgabe, Altbestand-Liste.
    p_aufr.add_argument("--alles", action="store_true", help="alle Ordner unter den Wurzeln")
    p_aufr.add_argument(
        "--aufgabe-einrichten", action="store_true", help="tägliche Windows-Aufgabe einrichten"
    )
    p_aufr.add_argument("--trocken", action="store_true", help="mit --aufgabe-einrichten: nur zeigen")
    p_aufr.add_argument(
        "--altbestand-liste", type=Path, metavar="DATEI.md", help="Lösch-Liste für den Menschen schreiben"
    )
    # Ticket-Zweige (#1439): ein Zweig (Schlange) bzw. Probelauf-Liste aller Zweige.
    p_aufr.add_argument("--zweig", help="Zweig ticket/<N>-… aufräumen (statt eines Ordners)")
    p_aufr.add_argument("--repo", help="mit --zweig: Repo-Pfad (sein origin zählt)")
    p_aufr.add_argument("--uebernommen-tip", help="mit --zweig: Stand, den die Schlange übernahm")
    p_aufr.add_argument(
        "--zweige-probelauf", type=Path, metavar="DATEI", help="Probelauf-Liste aller Ticket-Zweige"
    )
    # Aufpasser (#236): Cron-Hausmeister für die tmux-Fenster; gleiche Argumente wie
    # ``skripte/aufpasser.py``.
    aufpasser.parser_fuellen(
        unter.add_parser("aufpasser", help="tmux-Fenster bau/wache beaufsichtigen (Cron)")
    )

    args = ap.parse_args(argv)
    if args.befehl == "pruefen":
        code = _pruefen(ap, args)
        if code is not None:
            return code
    # Hooks zuerst und ohne Vorarbeit: sie dürfen die Session nie stören (#204).
    if args.befehl == "hook-stop":
        return _hook_stop_mit_umzug()
    if args.befehl == "hook-subagent-stop":
        return hooks.hook_subagent_stop()
    if args.befehl == "hook-spec-sperre":
        return hooks.hook_spec_sperre()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.befehl == "eintrag":
        return _eintrag(args)
    if args.befehl == "befund":
        return _befund(args)
    if args.befehl == "spec-schliessen":
        return _spec_schliessen(args)
    if args.befehl == "anhaengen":
        return _anhaengen(args)
    if args.befehl == "umrechnen":
        return _umrechnen(args)
    if args.befehl in briefkasten_cli.BEFEHLE:
        return briefkasten_cli.lauf(args)
    if args.befehl == "entscheidungen":
        return _entscheidungen(args)
    if args.befehl == "nest":
        return nest.lauf(args)
    if args.befehl == "leitstand":
        return leitstand.lauf(args)
    if args.befehl in ("takt", "takt-einrichten"):
        return waechter_takt.lauf(args)
    if args.befehl == "aufpasser":
        return aufpasser.lauf_mit_args(args)
    if args.befehl == "aufraeumen":
        return _aufraeumen(args, ap)
    if args.befehl == "sichern":
        return uebergabe_sichern.lauf(args.dateien, trocken=args.trocken)
    if args.befehl == "ticketstand":
        # Nur lesen: Tabelle „X von Y fertig“ für jeden Aufseher-Tick.
        return ticketstand.lauf(args, config.repo_wurzel(_repo_aus_umgebung()))
    if args.befehl == "aufseher-stand":
        # Nur lesen + eigene Stand-Datei: legt keine Konfig an (#430).
        return aufseher_stand.lauf(args, config.repo_wurzel(_repo_aus_umgebung()))
    if args.befehl == "leiter":
        # Nur lesen + Leitstand: legt keine Konfig an; stdout genau eine Zeile (E16).
        erg = leiter.cli(
            config.repo_wurzel(_repo_aus_umgebung()),
            args.spec,
            args.ticket,
            args.gh_repo,
            args.dry_run,
        )
        sys.stdout.write(erg.zeile + "\n")
        return erg.exit
    if args.befehl == "arbeitsstand":
        # Exit 0 = aktuell/nachgezogen/hinweis, 1 = veraltet/Abbruch (Zeile nennt Zahl und was zu tun ist).
        erg = arbeitsstand.sicherstellen(config.repo_wurzel(_repo_aus_umgebung()), nachziehen=not args.pruefen)
        print(f"Arbeitsstand: {erg.zustand} — {erg.text}", file=sys.stdout if erg.ok else sys.stderr)
        return 0 if erg.ok else 1
    if args.befehl == "hauptzweig":
        # Nur der Name auf stdout — spawn_srv.sh liest ihn ein (#257).
        print(gh.hauptzweig(config.repo_wurzel(_repo_aus_umgebung())))
        return 0
    if args.befehl == "blocker":
        return _blocker(args)
    if args.befehl == "einreichen":
        return _einreichen(args)
    if args.befehl == "zu-bauen":
        return _zu_bauen(args)
    if args.befehl == "ticket-lage":
        return _ticket_lage(args)
    if args.befehl == "komplett":
        repo = config.repo_wurzel(_repo_aus_umgebung())
        tuer = komplett_lauf.takt_kind if args.aus_takt else komplett_lauf.komplett_einmal
        erg = tuer(repo, args.spec, _KOMPLETT_MOMENTE[args.moment], config.lade(repo))
        # „läuft schon“ ist kein grüner Stand: vor Live erst weiter, wenn der andere Lauf grün ist.
        frei = erg.ok and not erg.laeuft
        print(erg.grund)
        return 0 if frei else 1
    if args.befehl == "startklar":
        ordner = args.ordner.resolve() if args.ordner else config.repo_wurzel(_repo_aus_umgebung())
        code, text = startklar.ausgabe(startklar.pruefe(ordner, args.spec))
        print(text)
        return code
    if args.befehl == "speicher":
        # Nur lesen: legt keine Konfig an (kein config.sicherstellen).
        return speicher.cli(
            config.lade(_repo_aus_umgebung()), nur_staffel=args.staffel, trocken=args.trocken, sitzung=args.sitzung
        )

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
    if args.befehl == "einrichten":
        return einrichten.einrichten(repo, pruefen=args.pruefen, ausgabe=sys.stdout)
    if args.befehl == "probesitz":
        return probesitz.befehl(
            repo, punkte=args.punkt, nur_zeigen=args.zeigen, modell=args.modell
        )
    if args.befehl == "spawn" and args.dry_run:
        # Probelauf (L1): Einrichter nur prüfend, schreibt nichts, Abweichung hält nicht auf.
        einrichten.einrichten(repo, pruefen=True, ausgabe=sys.stdout)
    if args.befehl == "spawn" and not args.dry_run:
        # Einrichter vor dem Setup (L1): große Lücken nur gemeldet, nur ein Anleger-Fehler hält auf.
        code = einrichten.einrichten(repo, ausgabe=sys.stdout)
        if code != 0:
            return code
        setup.erster_start(
            repo,
            ist_tty=sys.stdin.isatty(),
            eingabe=setup.stdin_eingabe(sys.stdout),
            ausgabe=sys.stdout,
        )
    if args.befehl == "regularien" and not args.dry_run:
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
    if args.befehl == "respawn":
        erg = respawn.abloesen(
            repo,
            args.spec,
            args.ticket,
            werkzeug=respawn.werkzeug_fuer_rechner(),
            warte_max=args.warte_max,
            dry_run=args.dry_run,
        )
        sys.stdout.write(erg.zeile + "\n")
        return erg.exit
    if args.befehl == "neustart":
        return spawn_modul.neustart(
            repo,
            args.spec,
            args.ticket,
            konfig,
            ziel=args.ziel,
            handoff=args.handoff,
            beenden=args.beenden,
            dry_run=args.dry_run,
        )
    if args.befehl == "stand":
        return stand_modul.sichere_stand(repo, konfig)
    if args.befehl == "regularien":
        # Einrichtungs-Ticket (#410) vor der Prüfung: es soll in ihr schon mitzählen.
        # Probelauf (--dry-run), --tickets, --nur-wache (--ohne-einrichtung) und
        # „Spec läuft schon“: nur Hinweis. Anlegen scheitert oder Stand unklar: Exit 3 (unten).
        einrichtung = einrichtungs_ticket.sicherstellen(
            repo,
            args.spec,
            konfig,
            mit_github=not args.ohne_github,
            trocken=args.dry_run,
            auswahl=bool(_tickets(args.tickets)) or args.ohne_einrichtung,
        )
        for zeile in einrichtung.zeilen:
            print(zeile)
        bericht = manifest.pruefe(
            repo,
            args.spec,
            konfig,
            mit_github=not args.ohne_github,
            auswahl=_tickets(args.tickets),
        )
        print(bericht.text())
        if not einrichtung.ok:
            # Ohne Einrichtungs-Ticket bauten die Tickets ohne Prüfung/Staging los (#410):
            # Weigerung, Schlusszeile wiederholt den Grund.
            print(einrichtung.zeilen[-1])
            return manifest.EXIT_WEIGERUNG
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
