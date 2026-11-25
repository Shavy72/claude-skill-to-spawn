"""Verteilung der CLI-Unterbefehle von ``to_spawn.py`` (#387).

Nimmt die von :func:`to_spawn.cli_parser.baue_parser` geparsten Argumente entgegen
und ruft die jeweils zuständigen Fachmodule unter ``to_spawn/`` auf.
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
from pathlib import Path
from typing import Any

from to_spawn import (
    abgleich,
    arbeitsstand,
    aufpasser,
    aufseher_stand,
    auswahl,
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
    helfer_temp,
    hooks,
    inventur,
    komplett_lauf,
    konsole,
    leiter,
    leitstand,
    live_tuer,
    manifest,
    nest,
    ort_wahl,
    probesitz,
    pruef,
    respawn,
    setup,
    spec_rand,
    speicher,
    start_last,
    startklar,
    ticket_zustand,
    ticketstand,
    tmux_aufruf,
    uebergabe_sichern,
    umzug,
    vorfall,
    waechter_takt,
    warten,
    wecker,
)
from to_spawn import einreichen as einreichen_modul
from to_spawn import spawn as spawn_modul
from to_spawn import stand as stand_modul
from to_spawn.cli_parser import KOMPLETT_MOMENTE

log = logging.getLogger("to_spawn")


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


def _repo_aus_umgebung() -> Path | None:
    """``TO_SPAWN_REPO`` (setzt die Weiterleitung im Repo, #205) oder ``None`` = aktueller Ordner."""
    wert = os.environ.get("TO_SPAWN_REPO", "").strip()
    return Path(wert).expanduser() if wert else None


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
        ap.error("--tickets/--dry-run/--ohne-einrichtung/--ohne-github gelten nur für „regularien <S>“")
    repo = config.repo_wurzel(args.repo.expanduser() if args.repo else _repo_aus_umgebung())
    urteil = pruef.pruefen(repo, args.basis, args.ziel)
    print(pruef.protokoll.text(urteil.zeile))
    return urteil.exit


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
        print(f"Hinweis: Aufseher läuft immer auf {setup.FLAGGSCHIFF} — Angabe ignoriert.")

    if args.standard or hat_werte:
        # --standard = Basis, gesetzte Flags gewinnen darüber.
        aenderungen: dict[str, Any] = (
            setup.standards(konfig, args.plattform) if args.standard else {"modelle": {}, "effort": {}}
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
    elif args.dialog or konsole.stdin_ist_terminal():
        aenderungen = setup.fuehre_dialog(konfig, setup.stdin_eingabe(sys.stdout), sys.stdout, args.plattform)
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
        print(f"  {rolle}: {setup.modell_name(modell)} ({modell}), Effort {stand['effort'][rolle]}")
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


def _ticket_repo_befehl(args: argparse.Namespace, ap: argparse.ArgumentParser) -> int:
    """Unterbefehl ``ticket-repo``: zeigt, setzt oder zieht ``ticket_repo``-Einträge nach (#261)."""
    if args.nachziehen:
        if args.eintrag or args.wert:
            ap.error("ticket-repo --nachziehen akzeptiert keine weiteren Argumente")
        datei = config.maschine_datei()
        if datei.is_file() and config._lies(datei)[0] is None:
            print(f"FEHLER: Maschinen-Liste unlesbar ({datei}) — nicht überschrieben", file=sys.stderr)
            return 1
        aenderungen = config.maschine_ticket_repos_nachziehen()
        if not aenderungen:
            print("Maschinen-Liste: alle ticket_repo aktuell")
            return 0
        for aenderung in aenderungen:
            print(aenderung.zeile())
        return 0
    if args.eintrag is None and args.wert is None:
        datei = config.maschine_datei()
        if datei.is_file() and config._lies(datei)[0] is None:
            print(f"FEHLER: Maschinen-Liste unlesbar ({datei})", file=sys.stderr)
            return 1
        for eintrag in config.maschine()["repos"]:
            wert = (eintrag.get("ticket_repo") or "").strip() or "(leer)"
            print(f"{eintrag.get('slug', '')} ({eintrag.get('pfad', '')}): ticket_repo = {wert}")
        return 0
    if not args.eintrag or not args.wert:
        ap.error("ticket-repo braucht <eintrag> <ticket_repo> oder --nachziehen")
    try:
        aenderungen = config.maschine_ticket_repo_setzen(args.eintrag, args.wert)
    except (ValueError, LookupError, OSError) as fehler:
        print(f"FEHLER: {fehler}", file=sys.stderr)
        return 1
    for aenderung in aenderungen:
        print(aenderung.zeile())
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
    if args.aelter_als_tage is not None and not args.helfer_altbestand:
        ap.error("--aelter-als-tage gilt nur mit --helfer-altbestand")
    if args.helfer_altbestand:
        tage = args.aelter_als_tage if args.aelter_als_tage is not None else hausmeister.HELFER_ALTBESTAND_TAGE
        ergebnisse = hausmeister.helfer_altbestand_aufraeumen(
            loeschen=args.loeschen,
            aelter_als_tage=tage,
        )
    elif args.alles:
        ergebnisse = hausmeister.aufraeumen_alles(loeschen=args.loeschen)
    elif args.ordner:
        ergebnisse = [hausmeister.aufraeumen(args.ordner, loeschen=args.loeschen)]
    else:
        ap.error(
            "aufraeumen: Ordner, --alles, --helfer-altbestand, --aufgabe-einrichten oder --altbestand-liste angeben"
        )
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
    ergebnis = befund.melde(repo, gh_repo, args.spec, args.ticket, args.stufe, args.text, dry_run=args.dry_run)
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


def _spec_pruefen(args: argparse.Namespace) -> int:
    """Unterbefehl ``spec-pruefen`` (#405): prüft Spec auf Quelle-Zeile/(E<n>) je Modus."""
    repo = config.repo_wurzel(_repo_aus_umgebung())
    try:
        text = manifest.lies_spec_text(repo, args.spec)
    except ValueError as fehler:
        print(f"FEHLER: {fehler}", file=sys.stderr)
        return 1
    ergebnis = manifest.pruefe_spec(text, config.lade(repo))
    print(ergebnis.text())
    return 0 if ergebnis.darf_schneiden else manifest.EXIT_WEIGERUNG


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
    print(erg.zeile)
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
        abnahme=_tickets(args.abnahme) or [],
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
    transkripte = (
        config.expand_heim(args.transkripte) if args.transkripte else config.heim_ordner() / ".claude" / "projects"
    )
    if not transkripte.is_dir():
        log.error("Transkript-Ordner %s fehlt.", transkripte)
        return 1
    umgerechnet, ohne = hooks.umrechnen(repo, ticket, transkripte)
    z = bau_log.zusammenfassung(repo, ticket)
    ist = f"{z['ist_k']:g}k" if z["ist_k"] else "—"
    print(f"Bau-Log #{ticket}: {umgerechnet} Zeile(n) umgerechnet, {ohne} ohne Transkript · Kontext jetzt {ist}")
    return 0


def _hook_stop_mit_umzug() -> int:
    """Ein Stop-Befehl für Bau-Log (#204) und Umzug-Anfrage des Aufsehers (#212).

    stdin ist nur einmal lesbar. Liegt eine Umzug-Anfrage, gewinnt deren
    Blockier-Antwort; sonst gilt die Ausgabe des Bau-Log-Hooks. Endet immer mit 0.
    """
    hook_log = logging.getLogger("to_spawn.hook_stop")
    roh = sys.stdin.read()
    bau_log_ausgabe = io.StringIO()
    code = hooks.hook_stop(io.StringIO(roh), bau_log_ausgabe)
    umzug_ausgabe = io.StringIO()
    try:
        umzug.hook_umzug_anfrage(roh, umzug_ausgabe)
    except Exception:  # noqa: BLE001 — Hook darf die Session nie stören
        hook_log.exception("Umzug-Teil des Stop-Hooks fehlgeschlagen — Session läuft weiter.")
    sys.stdout.write(umzug_ausgabe.getvalue() or bau_log_ausgabe.getvalue())
    return code


def verteilen(ap: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """Verteilt einen geparsten CLI-Aufruf auf den zuständigen Unterbefehl."""
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
    if args.befehl == "hook-ja-sperre":
        return hooks.hook_ja_sperre()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.befehl == "eintrag":
        return _eintrag(args)
    if args.befehl == "befund":
        return _befund(args)
    if args.befehl == "spec-schliessen":
        return _spec_schliessen(args)
    if args.befehl == "spec-pruefen":
        return _spec_pruefen(args)
    if args.befehl == "anhaengen":
        return _anhaengen(args)
    if args.befehl == "umrechnen":
        return _umrechnen(args)
    if args.befehl in briefkasten_cli.BEFEHLE:
        return briefkasten_cli.lauf(args)
    if args.befehl in helfer_temp.BEFEHLE:
        return helfer_temp.lauf(args)
    if args.befehl in abgleich.BEFEHLE:
        return abgleich.lauf(args)
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
    if args.befehl == "wecker":
        return wecker.lauf(args)
    if args.befehl == "start-last":
        return start_last.lauf_mit_args(args)
    if args.befehl == "warten":
        return warten.lauf(args, config.repo_wurzel(_repo_aus_umgebung()))
    if args.befehl in ort_wahl.BEFEHLE:
        return ort_wahl.lauf(args, config.repo_wurzel(_repo_aus_umgebung()))
    if args.befehl == "aufraeumen":
        return _aufraeumen(args, ap)
    if args.befehl == "sichern":
        return uebergabe_sichern.lauf(args.dateien, trocken=args.trocken)
    if args.befehl == "ticket-repo":
        return _ticket_repo_befehl(args, ap)
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
    if args.befehl == "sitzung":
        # Einzige Quelle für tmux-Session-Name + Besitzer-Prüfung (#457).
        repo = config.repo_wurzel(_repo_aus_umgebung())
        if args.nur_name:
            print(tmux_aufruf.sitzung_name(repo, args.spec))
            return 0
        name, fremd = tmux_aufruf.sitzung_aufloesen(repo, args.spec)
        if fremd:
            print(fremd, file=sys.stderr)
            return tmux_aufruf.EXIT_FREMD
        print(name)
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
        erg = tuer(repo, args.spec, KOMPLETT_MOMENTE[args.moment], config.lade(repo))
        # „läuft schon“ ist kein grüner Stand: vor Live erst weiter, wenn der andere Lauf grün ist.
        print(erg.grund)
        return 0 if erg.frei else 1
    if args.befehl == "live":
        repo = config.repo_wurzel(_repo_aus_umgebung())
        code, zeilen = live_tuer.starte(repo, args.spec, config.lade(repo))
        print("\n".join(zeilen))
        return code
    if args.befehl == "startklar":
        ordner = args.ordner.resolve() if args.ordner else config.repo_wurzel(_repo_aus_umgebung())
        code, text = startklar.ausgabe(startklar.pruefe(ordner, args.spec))
        print(text)
        return code
    if args.befehl == "speicher":
        # Nur lesen: legt keine Konfig an (kein config.sicherstellen).
        return speicher.cli(
            config.lade(_repo_aus_umgebung()),
            nur_staffel=args.staffel,
            trocken=args.trocken,
            sitzung=args.sitzung,
        )

    if args.befehl == "auswahl":
        return auswahl.lauf(args, _repo_aus_umgebung() or config.repo_wurzel())

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
        return probesitz.befehl(repo, punkte=args.punkt, nur_zeigen=args.zeigen, modell=args.modell)
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
            ist_tty=konsole.stdin_ist_terminal(),
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
            ohne_wache=args.ohne_wache,
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
        return deploy_status.deploy_status(repo, datei=args.datei, ticket=args.ticket, still_min=args.still_min)
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
