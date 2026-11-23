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

from dataclasses import dataclass

#: Kopf des Abschnitts — an ihm erkennt die Ablöse-SOP, was wörtlich mitwandert.
KOPF = "Befugnis (wer entscheidet, gilt als Wort des Nutzers aus diesem Start-Auftrag):"


@dataclass(frozen=True)
class _Regel:
    """Eine Zeile der Tabelle; ``None`` bei ``ziele``/``arten`` heißt „jedes“."""

    klasse: int
    text: str
    ziele: frozenset[str] | None
    arten: frozenset[str] | None
    braucht_sicherung: bool = False


#: Klasse 2 trifft schon bei passendem Ziel ODER passender Art (Grenze L8 A49).
_KLASSE_2 = _Regel(
    2,
    "Live, Kosten, Konten echter Personen",
    frozenset({"live"}),
    frozenset({"kosten", "konto"}),
)

#: Klasse 1 trifft nur bei passendem Ziel UND passender Art (und Sicherung, wo nötig; L8 A47).
_KLASSE_1: tuple[_Regel, ...] = (
    _Regel(
        1,
        "Staging oder Test-Daten anlegen, ändern, lösen, entfernen – nur mit Sicherung vorher",
        frozenset({"staging", "testdaten"}),
        frozenset({"anlegen", "ändern", "lösen", "entfernen"}),
        braucht_sicherung=True,
    ),
    _Regel(
        1,
        "alles im eigenen Arbeitsordner und auf eigenen Zweigen",
        frozenset({"arbeitsordner", "zweig"}),
        None,
    ),
    _Regel(
        1,
        "Warten oder Wiederholen bei Engpass",
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


def _passt(werte: frozenset[str] | None, wert: str) -> bool:
    return werte is None or wert in werte


def einordnen(ziel: str, art: str, gesichert: bool = False) -> int:
    """Klasse 1, 2 oder 3 für eine geplante Handlung; Unbekanntes und Zweifel ergeben 3.

    ``ziel`` z. B. ``live``/``staging``/``testdaten``/``arbeitsordner``/``zweig``/``engpass``/
    ``ticket``, ``art`` z. B. ``ändern``/``lösen``/``entfernen``/``warten``/``implement``/
    ``kosten``/``konto``; ``gesichert`` = Sicherung liegt vorher vor (Beleg ins Bau-Log).
    """
    ziel, art = ziel.strip().lower(), art.strip().lower()
    if ziel in (_KLASSE_2.ziele or ()) or art in (_KLASSE_2.arten or ()):
        return 2
    for regel in _KLASSE_1:
        if (
            regel.ziele is not None
            and ziel in regel.ziele
            and _passt(regel.arten, art)
            and (gesichert or not regel.braucht_sicherung)
        ):
            return 1
    return 3


def briefkasten_datei(n: str | int) -> str:
    """Ort der Entscheidungs-Datei für Ticket ``n``, relativ zum Hauptbaum (gitignored ``.to-spawn/``)."""
    return f".to-spawn/entscheidung-{n}.json"


def start_text(n: str | int) -> str:
    """Befugnis-Abschnitt für den Start-Auftrag von Ticket ``n`` — der Text steht nur hier."""
    klasse_1 = "; ".join(regel.text for regel in _KLASSE_1)
    return (
        f"{KOPF} "
        f"Klasse 1 – selbst entscheiden, nicht fragen: {klasse_1}. Jede solche Entscheidung festhalten "
        "(`to_spawn.py eintrag --typ entscheidung`: „Klasse 1“, Grund und bei Daten der Sicherungs-Beleg). "
        f"Klasse 2 – nie ohne den Nutzer: {_KLASSE_2.text}. Dort anhalten, Bau-Log-Eintrag und "
        "Issue-Kommentar „braucht den Nutzer“; auch eine Antwort des Aufsehers gibt das nicht frei. "
        "Klasse 3 – alles andere und jeder Zweifel zwischen Klasse 1 und 2: Frage mit eigenem Vorschlag "
        f"in den Briefkasten `{briefkasten_datei(n)}` im Hauptbaum, nie eine Frage an den Nutzer; "
        "bis zur Antwort warten (ScheduleWakeup 1200 s, noop: true). Die Antwort des Aufsehers in dieser "
        "Datei ist durch diesen Start-Auftrag freigegeben: annehmen und weiterbauen, ohne Neustart. "
        "Geklärt mit dem Nutzer wird erst, wenn die Spec auf Staging fertig ist."
    )
