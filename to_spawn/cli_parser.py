"""CLI-Parser von ``to_spawn.py`` (#387): Parser-Aufbau und Argumentfehler-Exit-Codes.

Alle Unterbefehle und Schalter der ``to-spawn``-CLI werden an genau dieser Stelle
registriert; die Verteilung auf die Zielmodule liegt in :mod:`to_spawn.cli_befehle`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from to_spawn import (
    abgleich,
    aufpasser,
    aufseher_stand,
    befund,
    briefkasten_cli,
    deploy_status,
    helfer_temp,
    inventur,
    leitstand,
    nest,
    respawn,
    start_last,
    ticketstand,
    uebergabe_sichern,
    vorfall,
    waechter_takt,
)
from to_spawn import einreichen as einreichen_modul
from to_spawn.pruefplan import Moment

#: Unterbefehl ``komplett --moment``: Text der Kommandozeile → Moment (anderes lehnt argparse mit Exit 2 ab).
KOMPLETT_MOMENTE: dict[str, Moment] = {
    "vor-live": Moment.VOR_LIVE,
    "alle-auf-staging": Moment.ALLE_AUF_STAGING,
}

#: Schlüssel für ``set_defaults``: Exit-Code bei falschen Argumenten dieses Unterbefehls (#505).
ARGUMENTFEHLER_EXIT = "argumentfehler_exit"


class _Parser(argparse.ArgumentParser):
    """argparse, dessen Exit-Code bei falschen Argumenten der Unterbefehl festlegt (#505).

    Standard bleibt argparse-üblich 2. Ein Unterbefehl, dessen Exit 2 schon etwas anderes heißt
    (``einreichen``: „Stand unbekannt – erneut rufen“), setzt ``set_defaults(argumentfehler_exit=N)``;
    das gilt für fehlende und überzählige Argumente gleichermaßen.
    """

    def error(self, message: str) -> NoReturn:
        self._abbruch(self._defaults.get(ARGUMENTFEHLER_EXIT, 2), message)

    def parse_args(  # type: ignore[override]
        self, args: Sequence[str] | None = None, namespace: argparse.Namespace | None = None
    ) -> argparse.Namespace:
        ergebnis, rest = self.parse_known_args(args, namespace)
        if rest:
            self._abbruch(
                getattr(ergebnis, ARGUMENTFEHLER_EXIT, 2),
                f"unrecognized arguments: {' '.join(rest)}",
            )
        return ergebnis

    def _abbruch(self, code: int, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(code, f"{self.prog}: error: {message}\n")


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


def baue_parser() -> _Parser:
    """Baut den vollständigen CLI-Parser mit allen Unterbefehlen."""
    ap = _Parser(prog="to_spawn", description="Bau-Sessions einer Spec steuern.")
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
    p_anh.add_argument(
        "--abnahme",
        default="",
        metavar="A,…|keine",
        help="Abnahme-Ticket(s) dieser Kette (Komma; oder 'keine' für eine neue Kette)",
    )
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
        help="fertigen, geprüften Stand auf den Hauptzweig bringen (im eigenen Worktree; Exit 0/1/2/5)",
    )
    # #505: Exit 2 heißt hier „Stand unbekannt – erneut rufen“; Tippfehler im Befehl → Aufruf falsch.
    p_einreichen.set_defaults(**{ARGUMENTFEHLER_EXIT: einreichen_modul.AUFRUF})
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
    unter.add_parser("hook-ja-sperre", help="PreToolUse-Hook: Ja-Befehle aus Werkzeug-Aufrufen sperren (#531)")
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
    helfer_temp.richte_parser_ein(unter)  # #551
    abgleich.richte_parser_ein(unter)  # #561
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
    p_komplett.add_argument("--moment", choices=sorted(KOMPLETT_MOMENTE), default="vor-live")
    # Nur für den Hintergrund-Lauf aus dem capo-Takt (#455): Rot je Kopf gilt dort als geprüft.
    p_komplett.add_argument("--aus-takt", action="store_true", help=argparse.SUPPRESS)
    # Live-Tür (#456): live.befehl startet nur nach grüner Komplett-Prüfung — Exit 0/1, 2 = live.befehl fehlt.
    p_live = unter.add_parser("live", help="Live-Schritt der Spec, nur nach grüner Komplett-Prüfung (Exit 0/1/2)")
    p_live.add_argument("spec", type=int)

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
    # Start-Last (#412): Kontext nach Start je Fenstertyp (bau, aufseher) + Obergrenzen-Prüfung.
    start_last.parser_fuellen(
        unter.add_parser(
            "start-last",
            help="Start-Last (Kontext nach Start) je Fenstertyp messen und gegen Obergrenzen prüfen",
        )
    )
    return ap
