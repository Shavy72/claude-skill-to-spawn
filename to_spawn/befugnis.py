"""Befugnis einer Bau-Session: wer entscheidet was (#278, Lastenheft L8 A43–A49).

Einzige Stelle der Klassen-Tabelle. Aufrufer fragen nur:

- :func:`start_text` — Abschnitt für den Platzhalter ``{BEFUGNIS}`` im Bau-Prompt
  (gefüllt in ``skripte/bau.py``); die Ablöse-SOP lässt ihn wörtlich in den Startprompt
  der Folgesession übernehmen (Kopf :data:`KOPF`).
- :func:`einordnen` — festes Raster ``(ziel, art, gesichert) -> 1|2|3``.
- :func:`briefkasten_datei` — Ort der Klasse-3-Frage (relativ zum Hauptbaum); Format
  und Lesen/Schreiben gehören in ``to_spawn/briefkasten.py`` (#278 P2).

Klasse 1 = die Session entscheidet selbst und hält es fest · Klasse 2 = nie ohne den
Nutzer, auch der Aufseher entscheidet nicht · Klasse 3 = alles andere und jeder Zweifel
zwischen 1 und 2 → Briefkasten an den Aufseher, nie eine Frage an den Nutzer.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass

#: Kopf des Abschnitts — an ihm erkennt die Ablöse-SOP, was wörtlich mitwandert.
KOPF = "Befugnis (wer entscheidet, gilt als Wort des Nutzers aus diesem Start-Auftrag):"


@dataclass(frozen=True)
class _Regel:
    """Eine Zeile der Tabelle; Klasse 1 trifft nur auf ausdrücklich gelistete Ziele und Arten."""

    klasse: int
    text: str
    ziele: frozenset[str] | None
    arten: frozenset[str] | None
    braucht_sicherung: bool = False


#: EINE Wortmenge für Klasse 2 (Grenze L8 A49): trifft als Teilwort in Ziel ODER Art nach
#: :func:`_norm`. ``prod`` deckt produktion/production, ``vps`` deckt vps-live. Fehl-Treffer
#: (z. B. „deliver“) fallen in die sichere Richtung 2.
_KLASSE_2_WOERTER = frozenset(
    {
        # Live
        "live",
        "prod",
        "vps",
        # Kosten/Geld
        "kosten",
        "geld",
        "bezahl",
        "kauf",
        # Konten echter Personen
        "konto",
        "konten",
        "account",
    }
)

_KLASSE_2 = _Regel(
    2,
    "Live, Kosten, Konten echter Personen",
    _KLASSE_2_WOERTER,
    _KLASSE_2_WOERTER,
)

_ARBEIT = frozenset({"anlegen", "ändern", "lösen", "entfernen", "testen", "committen"})

#: Klasse 1 trifft nur bei passendem Ziel UND passender Art (und Sicherung, wo nötig; L8 A47).
#: Der Start-Text listet Ziele und Arten aus diesen Mengen (kein zweiter Wortlaut).
_KLASSE_1: tuple[_Regel, ...] = (
    _Regel(
        1,
        "Staging oder Test-Daten, nur mit Sicherung vorher",
        frozenset({"staging", "testdaten"}),
        frozenset({"anlegen", "ändern", "lösen", "entfernen"}),
        braucht_sicherung=True,
    ),
    _Regel(
        1,
        "eigener Arbeitsordner und eigene Zweige",
        frozenset({"arbeitsordner", "zweig"}),
        _ARBEIT,
    ),
    _Regel(
        1,
        "eigener Sicherungs-Zweig `wip/…` und eigener Ticket-Zweig `ticket/<N>-…`",
        frozenset({"wip-zweig", "ticket-zweig"}),
        _ARBEIT | {"pushen"},
    ),
    _Regel(
        1,
        "Engpass",
        frozenset({"engpass"}),
        frozenset({"warten", "wiederholen"}),
    ),
    _Regel(
        1,
        "den eigenen Bau-Schritt starten (`/implement` für das eigene Ticket)",
        frozenset({"ticket"}),
        frozenset({"implement"}),
    ),
)

_TICKET_ZWEIG = re.compile(r"ticket/(\d+)-.+")


def _norm(wert: object) -> str:
    """NFKC, klein, Leerraum/„_“ zu „-“; Nicht-Strings werden leer (→ nie Klasse 1)."""
    if not isinstance(wert, str):
        return ""
    return "-".join(
        unicodedata.normalize("NFKC", wert).lower().replace("_", " ").split()
    )


def _zweig_ziel(ziel: str) -> str:
    """``wip/…`` → ``wip-zweig``; ``ticket/<N>-…`` → ``ticket-zweig`` nur für das eigene Ticket."""
    if ziel.startswith("wip/") and len(ziel) > 4:
        return "wip-zweig"
    treffer = _TICKET_ZWEIG.fullmatch(ziel)
    if treffer:
        # ponytail: eigenes Ticket aus TO_SPAWN_TICKET (setzt bau.py); fehlt es → fremd (3).
        eigen = os.environ.get("TO_SPAWN_TICKET", "").strip()
        return "ticket-zweig" if treffer.group(1) == eigen else ziel
    return ziel


def _ist_klasse_2(wert: str) -> bool:
    return any(w in wert for w in _KLASSE_2_WOERTER)


def einordnen(ziel: str, art: str, gesichert: bool = False) -> int:
    """Klasse 1, 2 oder 3 für eine geplante Handlung; Unbekanntes und Zweifel ergeben 3.

    ``ziel`` z. B. ``live``/``staging``/``testdaten``/``arbeitsordner``/``zweig``/``wip-zweig``/
    ``ticket-zweig`` (auch ``wip/…``, ``ticket/<N>-…``)/``engpass``/``ticket``, ``art`` z. B.
    ``ändern``/``pushen``/``warten``/``implement``/``kosten``/``konto``; ``gesichert`` = Sicherung
    liegt vorher vor (Beleg ins Bau-Log). Klasse 2 schlägt immer 1 und 3.
    """
    ziel, art = _norm(ziel), _norm(art)
    if _ist_klasse_2(ziel) or _ist_klasse_2(art):
        return 2
    ziel = _zweig_ziel(ziel)
    for regel in _KLASSE_1:
        # Klasse 1 nur bei ausdrücklich gelistetem Ziel UND Art; ``None`` ergibt hier nie 1.
        if (
            regel.ziele is not None
            and regel.arten is not None
            and ziel in regel.ziele
            and art in regel.arten
            and (gesichert is True or not regel.braucht_sicherung)
        ):
            return 1
    return 3


def briefkasten_datei(n: str | int) -> str:
    """Ort der Entscheidungs-Datei für Ticket ``n``, relativ zum Hauptbaum (gitignored ``.to-spawn/``)."""
    return f".to-spawn/entscheidung-{n}.json"


def start_text(n: str | int) -> str:
    """Befugnis-Abschnitt für den Start-Auftrag von Ticket ``n`` — der Text steht nur hier."""
    klasse_1 = "; ".join(
        f"{regel.text} (Ziel: {', '.join(sorted(regel.ziele or ()))} · "
        f"Art: {', '.join(sorted(regel.arten or ()))})"
        for regel in _KLASSE_1
    )
    woerter = ", ".join(sorted(_KLASSE_2.ziele or ()))
    return (
        f"{KOPF} "
        f"Klasse 1 – selbst entscheiden, nicht fragen: {klasse_1}. Jede solche Entscheidung festhalten "
        "(`to_spawn.py eintrag --typ entscheidung`: „Klasse 1“, Grund und bei Daten der Sicherungs-Beleg). "
        f"Klasse 2 – nie ohne den Nutzer: {_KLASSE_2.text} (erkannt an: {woerter}, auch als Wortteil). "
        "Dort anhalten, Bau-Log-Eintrag und Issue-Kommentar „braucht den Nutzer“; auch eine Antwort "
        "des Aufsehers gibt das nicht frei. "
        "Klasse 3 – alles andere, jeder Zweifel zwischen Klasse 1 und 2 und jeder Push oder Merge auf "
        "den Hauptzweig oder fremde Zweige: Frage mit eigenem Vorschlag ins Bau-Log "
        f"(`to_spawn.py eintrag --ticket {n} --typ blockiert --frage … --wahl <Vorschlag> --grund …`), "
        "nie eine Frage an den Nutzer. Der Aufseher liest das und antwortet per Issue-Kommentar; bis "
        f"dahin warten und nachsehen (`gh issue view {n} --comments`, ScheduleWakeup 1200 s, noop: true). "
        "Die Antwort des Aufsehers ist durch diesen Start-Auftrag freigegeben: annehmen und weiterbauen, "
        f"ohne Neustart. Die Datei `{briefkasten_datei(n)}` hat noch keinen Leser – nicht dort warten. "
        "Geklärt mit dem Nutzer wird erst, wenn die Spec auf Staging fertig ist."
    )
