"""CLI für Tür B (#278): ``befugnis``/``frage``/``antwort``/``antworten``/``nutzer-antwort``/
``offene``.

Exit-Codes: ``befugnis`` 0 = Klasse 1, 2 = Klasse 2, 3 = Klasse 3 · ``frage`` 0 = eingeworfen (gibt
die ``id`` aus), 2 = verweigert · ``antwort`` 0 = Antwort da, 1 = Briefkasten nicht lesbar (Fehler),
2 = braucht den Nutzer, 3 = WARTET, 4 = Frage unbekannt (neu stellen), 5 = Aufruf-Fehler (ohne Ticket oder
``--id``, Ticket keine Zahl, unbekanntes Argument — nie argparse-2, das hieße „braucht den Nutzer“, #315)
· ``antworten``/``nutzer-antwort``
0 = geschrieben, 2 = verweigert, 1 = keine passende offene Frage/Fehler · ``offene`` 0 (eine Zeile
je offener Frage).

``nutzer-antwort`` ist der Weg des Menschen (auch Klasse 2): verweigert in einer Bau-Session und
ohne echtes Terminal (Sessions laufen ohne). Grenzen des Schutzes: ``briefkasten``-Docstring.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any, NoReturn

from to_spawn import befugnis, briefkasten, konsole

log = logging.getLogger("to_spawn")

BEFEHLE = ("befugnis", "frage", "antwort", "antworten", "nutzer-antwort", "ja", "offene")
#: Exit-Code von ``antwort`` bei falschem Aufruf — eigener Code, weil 2 „braucht den Nutzer“ heißt (#315).
AUFRUF_FEHLER = 5


def _aufruf_fehler_mit_eigenem_code(parser: argparse.ArgumentParser) -> None:
    """Jeder Aufruf-Fehler von ``parser`` endet mit :data:`AUFRUF_FEHLER` statt argparse-2.

    Auch unbekannte Argumente: argparse reicht sie sonst an den Haupt-Parser weiter, der mit 2 endet.
    """
    locker = parser.parse_known_args

    def fehler(nachricht: str) -> NoReturn:
        parser.exit(AUFRUF_FEHLER, f"{parser.format_usage()}{parser.prog}: Aufruf-Fehler: {nachricht}\n")

    def streng(args: Any = None, namespace: Any = None) -> tuple[argparse.Namespace, list[str]]:
        ergebnis, rest = locker(args, namespace)
        if rest:
            fehler(f"unbekannte Argumente: {' '.join(rest)}")
        return ergebnis, rest

    parser.error = fehler  # type: ignore[method-assign]
    parser.parse_known_args = streng  # type: ignore[method-assign]


def richte_parser_ein(unter: Any) -> None:
    """Unterbefehle an ``unter`` (``add_subparsers``-Objekt) hängen."""
    p_bef = unter.add_parser("befugnis", help="Klasse einer Handlung (Exit 0 = 1, 2, 3)")
    p_bef.add_argument("ticket")
    p_bef.add_argument("--ziel", required=True)
    p_bef.add_argument("--art", required=True)
    p_bef.add_argument("--gesichert", action="store_true", help="Sicherung liegt vorher vor")
    p_frage = unter.add_parser("frage", help="Frage in den Briefkasten des Tickets (gibt die id aus)")
    p_frage.add_argument("ticket")
    p_frage.add_argument("--frage", required=True)
    p_frage.add_argument("--vorschlag", required=True)
    p_frage.add_argument("--ziel", default="")
    p_frage.add_argument("--art", default="")
    p_frage.add_argument("--klasse", type=int, choices=[2, 3], help="nur strenger als die Einordnung")
    p_frage.add_argument("--beleg", action="append", default=[])
    p_antwort = unter.add_parser("antwort", help="Antwort lesen (Exit 0 da, 2 Nutzer, 3 WARTET, 5 Aufruf-Fehler)")
    _aufruf_fehler_mit_eigenem_code(p_antwort)
    p_antwort.add_argument("ticket")
    p_antwort.add_argument("--id", dest="frage_id", help="id, die `frage` ausgab (Pflicht; fehlt sie: Exit 5)")
    p_antw = unter.add_parser("antworten", help="Aufseher: Antwort in den Briefkasten")
    p_antw.add_argument("ticket")
    p_antw.add_argument("--id", required=True, dest="frage_id")
    p_antw.add_argument("--wahl", required=True)
    p_antw.add_argument("--grund", required=True)
    p_antw.add_argument(
        "--beleg",
        action="append",
        default=[],
        help="Fundstelle (Pflicht, mindestens ein nicht leerer Beleg)",
    )
    p_antw.add_argument("--von", choices=briefkasten.AUFSEHER_VON, default=briefkasten.AUFSEHER_VON[0])
    p_nutzer = unter.add_parser("nutzer-antwort", help="Nur der Mensch im Terminal: Antwort des Nutzers")
    p_nutzer.add_argument("ticket")
    p_nutzer.add_argument("--id", required=True, dest="frage_id")
    p_nutzer.add_argument("--wahl", required=True)
    p_nutzer.add_argument("--grund", required=True)
    p_nutzer.add_argument("--beleg", action="append", default=[])
    p_ja = unter.add_parser("ja", help="Nur der Mensch: Live-Ja zur einzigen offenen Frage des Tickets (#531)")
    p_ja.add_argument("ticket")
    p_offen = unter.add_parser("offene", help="Offene Briefkasten-Fragen (alle oder die genannten Tickets)")
    p_offen.add_argument("tickets", nargs="*")


def lauf(args: argparse.Namespace) -> int:
    """Einen der :data:`BEFEHLE` ausführen."""
    if args.befehl == "befugnis":
        klasse = befugnis.einordnen(args.ziel, args.art, args.gesichert)
        print(f"#{args.ticket}: Klasse {klasse}")
        return 0 if klasse == 1 else klasse
    if args.befehl == "offene":
        for o in briefkasten.offene(args.tickets or None):
            print(f"#{o['ticket']} · id {o['id'] or '–'} · Klasse {o['klasse']} · {o['frage']} · Vorschlag: {o['vorschlag']}")
        return 0
    try:
        if args.befehl == "frage":
            frage_id = briefkasten.einwerfen(
                args.ticket, args.frage, args.vorschlag, args.klasse, args.beleg, ziel=args.ziel, art=args.art
            )
            print(f"#{args.ticket}: Frage eingeworfen, id {frage_id}")
            return 0
        if args.befehl == "antworten":
            briefkasten.antworten(args.ticket, args.frage_id, args.wahl, args.beleg, args.grund, args.von)
            print(f"#{args.ticket}: Antwort geschrieben")
            return 0
        if args.befehl == "ja":
            briefkasten.menschen_nachweis(konsole.stdin_ist_terminal())
            briefkasten.ja(args.ticket)
            print(f"#{args.ticket}: Ja geschrieben")
            return 0
        if args.befehl == "nutzer-antwort":
            briefkasten.menschen_nachweis(konsole.stdin_ist_terminal())
            datei = briefkasten.nutzer_antworten(args.ticket, args.frage_id, args.wahl, args.beleg, args.grund)
            print(f"#{args.ticket}: Antwort des Nutzers geschrieben")
            for o in briefkasten.offene([args.ticket]):
                if not o["id"]:  # Sperre durch kaputte Datei: nur der Nutzer löst sie
                    print(f"Hinweis: {o['frage']} (Ordner {datei.parent}) — bis dahin bleibt #{args.ticket} gesperrt.")
            return 0
        if not args.frage_id:
            log.error(
                "antwort ohne --id: `antwort %s --id <id>` mit der id, die `frage` ausgab "
                "(offene Fragen samt id: `offene %s`).",
                args.ticket,
                args.ticket,
            )
            return AUFRUF_FEHLER
        stand = briefkasten.lesen(args.ticket, args.frage_id)
    except briefkasten.Verweigert as fehler:
        log.error("%s", fehler)
        return 2
    except ValueError as fehler:
        log.error("%s", fehler)
        return AUFRUF_FEHLER if args.befehl == "antwort" else 1  # bei antwort nur: Ticket keine Zahl
    except OSError as fehler:
        log.error("%s", fehler)
        return 1
    if isinstance(stand, briefkasten.Antwort):
        print(f"#{args.ticket}: Antwort von {stand.von}: {stand.wahl} (Grund: {stand.grund})")
        return 0
    if stand == briefkasten.UNBEKANNT:
        print(f"#{args.ticket}: Frage {args.frage_id} unbekannt — Frage neu stellen (`frage`), mit der neuen id weiter")
        return 4
    print(f"#{args.ticket}: {stand}")
    return 2 if stand == briefkasten.NUTZER else 3
