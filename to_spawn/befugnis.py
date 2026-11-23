"""Befugnis einer Bau-Session: wer entscheidet was (#278, Lastenheft L8 A43–A49).

Einzige Stelle der Klassen-Tabelle. Aufrufer fragen nur:

- :func:`start_text` — Abschnitt für den Platzhalter ``{BEFUGNIS}`` im Bau-Prompt
  (gefüllt in ``skripte/bau.py``); die Ablöse-SOP lässt ihn wörtlich in den Startprompt
  der Folgesession übernehmen (Kopf :data:`KOPF`).
- :func:`einordnen` — festes Raster ``(ziel, art, gesichert) -> 1|2|3``.
- :func:`ist_klasse_2` — schlägt ein freier Text (Frage, Vorschlag, Ziel, Art) an Klasse 2 an?
  Ort, Format und Lesen/Schreiben des Briefkastens gehören allein in ``to_spawn/briefkasten.py``.

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
#: (z. B. „deliver“, „europa“) fallen in die sichere Richtung 2. Bewusst NICHT drin: ``deploy`` (Deploy auf
#: Staging ist normaler Bau-Weg; Live fängt das Ziel-Wort „live“) und das nackte ``token`` (meint
#: im Bau-Alltag Kontext-Token und würde jede zweite Handlung stoppen; ``api-token`` u. a. stehen drin).
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
        "gebühr",
        "gebuehr",
        "euro",
        "preis",
        "tarif",
        "lizenz",
        "bestellung",
        "vertrag",
        # Konten echter Personen
        "konto",
        "konten",
        "account",
        "zahlung",
        "zahlen",
        "abbuch",
        "rechnung",
        "kredit",
        "payment",
        "billing",
        "passwort",
        "password",
        "login",
        # Zugang
        "zugang",
        "secret",
        "credential",
        "api-key",
        "apikey",
        "api-token",
        "access-token",
        "bitwarden",
        ".env",
        "oauth",
        "2fa",
        "dns",
        # Abos/Zahlungen
        "abonnement",
        "abos",
        "subscription",
        "überweis",
        "ueberweis",
        "invoice",
        "paypal",
        "stripe",
        "purchase",
        "kündig",
        "kuendig",
        "zahlt",
    }
)

#: Kurze Wörter nur als ganzes Wort (zwischen „-“ „/“ „.“): „abo“ ≠ „labor“, „zahl“ ≠ „anzahl“.
#: Zahlungs-Wörter mit Endung stehen oben als Teilwort (z. B. „auszahlung“).
_KLASSE_2_GANZWORT = frozenset({"abo", "zahl", "pay", "buy", "cost", "charge", "ssh"})

_KLASSE_2 = _Regel(
    2,
    "Live, Kosten, Konten echter Personen",
    _KLASSE_2_WOERTER,
    _KLASSE_2_WOERTER,
)

#: Interne Marke für „eigener Ticket-Zweig“; als Eingabe nie gültig: ``_norm`` macht aus „…“ per
#: NFKC „...“, die Marke entsteht also nur in :func:`_zweig_ziel`.
_EIGENER_TICKET_ZWEIG = "ticket/<n>-…"
_EIGENER_WIP_ZWEIG = "wip/<n>-…"  # wie oben: nur über :func:`_zweig_ziel`, nie als Eingabe

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
        "Deploy auf Staging (Bau-Alltag, fasst keine Daten an, darum ohne Sicherung; L8 A47)",
        frozenset({"staging"}),
        frozenset({"deploy", "deployen"}),
    ),
    _Regel(
        1,
        "eigener Arbeitsordner und eigene Zweige",
        frozenset({"arbeitsordner", "zweig"}),
        _ARBEIT,
    ),
    _Regel(
        1,
        "eigener Sicherungs-Zweig `wip/<N>-<kurzname>` und eigener Ticket-Zweig `ticket/<N>-<kurzname>`",
        frozenset({_EIGENER_WIP_ZWEIG, _EIGENER_TICKET_ZWEIG}),
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

#: Gültiger einfacher Zweigname, positiv definiert: nach ``ticket/<N>-`` bzw. ``wip/<N>-`` nur
#: ``[a-z0-9._-]`` — kein ``:`` (Refspec), ``+``, Leerraum, ``;``, ``~``, ``@{``, ``/``. Geprüft auf der
#: ROHEN Eingabe, weil ``_norm`` Leerraum zu „-“ macht. wip-Zweige heißen ``wip/<N>-<kurzname>``
#: (to_spawn/anleitung.py), darum auch dort nur die eigene Nummer.
# Kurzname beginnt und endet auf Buchstabe/Ziffer (kein ``--force``, ``.lock``, ``x.``).
_EIGENER_ZWEIG = re.compile(r"(ticket|wip)/([0-9]+)-[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?")


def _norm(wert: object) -> str:
    """NFKC, klein, Leerraum/„_“ zu „-“; Nicht-Strings werden leer (→ nie Klasse 1)."""
    if not isinstance(wert, str):
        return ""
    return "-".join(unicodedata.normalize("NFKC", wert).lower().replace("_", " ").split())


def _zweig_ziel(roh: object, ziel: str) -> str:
    """Eigener ``wip/<N>-…`` → Marke, eigener ``ticket/<N>-…`` → Marke; sonst ``ziel``.

    ``roh`` = Eingabe vor :func:`_norm`; nur ein gültiger einfacher Zweigname zählt (``fullmatch``).
    """
    if not isinstance(roh, str) or ".." in roh or roh.endswith(".lock"):
        return ziel  # nie Klasse 1 (Pfad-Tricks wie ``wip/../master``)
    treffer = _EIGENER_ZWEIG.fullmatch(roh)
    # ponytail: eigenes Ticket aus TO_SPAWN_TICKET (setzt bau.py); fehlt es → fremd (3).
    eigen = os.environ.get("TO_SPAWN_TICKET", "").strip()
    if not treffer or not eigen or treffer.group(2) != eigen:
        return ziel
    return _EIGENER_WIP_ZWEIG if treffer.group(1) == "wip" else _EIGENER_TICKET_ZWEIG


def _ist_klasse_2(wert: str) -> bool:
    teile = re.split(r"[-/.]+", wert)
    return any(w in wert for w in _KLASSE_2_WOERTER) or any(w in teile for w in _KLASSE_2_GANZWORT)


def einordnen(ziel: str, art: str, gesichert: bool = False) -> int:
    """Klasse 1, 2 oder 3 für eine geplante Handlung; Unbekanntes und Zweifel ergeben 3.

    ``ziel`` z. B. ``live``/``staging``/``testdaten``/``arbeitsordner``/``zweig``/
    ``wip/<N>-…``, ``ticket/<N>-…`` (nur eigene Nummer)/``engpass``/``ticket``, ``art`` z. B.
    ``ändern``/``pushen``/``warten``/``implement``/``kosten``/``konto``; ``gesichert`` = Sicherung
    liegt vorher vor (Beleg ins Bau-Log). Klasse 2 schlägt immer 1 und 3.
    """
    roh = ziel
    ziel, art = _norm(ziel), _norm(art)
    ziel_neu = _zweig_ziel(roh, ziel)
    # Ziel = eigener gültiger Zweig: Wörter im Kurznamen zählen nicht; die Art bleibt geprüft.
    if (ziel_neu == ziel and _ist_klasse_2(ziel)) or _ist_klasse_2(art):
        return 2
    ziel = ziel_neu
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


def ist_klasse_2(*texte: object) -> bool:
    """``True``, sobald einer der Texte ein Klasse-2-Wort trägt (gleiches Raster wie :func:`einordnen`).

    Für freie Texte ohne Ziel/Art-Raster (z. B. Frage und Vorschlag im Briefkasten); Nicht-Strings
    zählen nicht. Kein eigener-Zweig-Ausnahme: hier gilt jedes Wort.
    """
    return any(_ist_klasse_2(_norm(t)) for t in texte if _norm(t))


def _ziel_text(ziel: str, n: str | int) -> str:
    """Marke → lesbare Regel; ohne bekannte eigene Nummer Klartext statt ``<n>``."""
    if ziel not in (_EIGENER_TICKET_ZWEIG, _EIGENER_WIP_ZWEIG):
        return ziel
    art = ziel.split("/")[0]
    if str(n).strip().isdigit():
        return f"{art}/{str(n).strip()}-<kurzname>"
    return f"eigener {'Ticket' if art == 'ticket' else 'Sicherungs'}-Zweig {art}/<eigene Nummer>-<kurzname>"


def start_text(n: str | int) -> str:
    """Befugnis-Abschnitt für den Start-Auftrag von Ticket ``n`` — der Text steht nur hier."""
    from to_spawn import briefkasten  # spät: briefkasten importiert dieses Modul

    klasse_1 = "; ".join(
        f"{regel.text.replace('<N>', str(n))} (Ziel: {', '.join(sorted(_ziel_text(z, n) for z in regel.ziele or ()))} · "
        f"Art: {', '.join(sorted(regel.arten or ()))})"
        for regel in _KLASSE_1
    )
    teilwort = ", ".join(sorted(_KLASSE_2_WOERTER))
    ganzwort = ", ".join(sorted(_KLASSE_2_GANZWORT))
    return (
        f"{KOPF} "
        f"Klasse 1 – selbst entscheiden, nicht fragen: {klasse_1}. Kurzname nur Kleinbuchstaben, Ziffern, `.`, `_`, `-` und endet auf Buchstabe/Ziffer. "
        "Jede solche Entscheidung festhalten "
        "(`to_spawn.py eintrag --typ entscheidung`: „Klasse 1“, Grund und bei Daten der Sicherungs-Beleg). "
        f"Klasse 2 – nie ohne den Nutzer: {_KLASSE_2.text} (erkannt an: {teilwort}, auch als Wortteil; "
        f"{ganzwort} nur als ganzes Wort). "
        "Dort anhalten, Bau-Log-Eintrag und Issue-Kommentar „braucht den Nutzer“; auch eine Antwort "
        "des Aufsehers gibt das nicht frei. "
        "Klasse 3 – alles andere, jeder Zweifel zwischen Klasse 1 und 2 und jeder Push oder Merge auf "
        "den Hauptzweig oder fremde Zweige: Frage mit eigenem Vorschlag ins Bau-Log "
        f"(`to_spawn.py eintrag --ticket {n} --typ blockiert --frage … --wahl <Vorschlag> --grund …`), "
        "nie eine Frage an den Nutzer. Der Aufseher liest das und antwortet per Issue-Kommentar; bis "
        f"dahin warten und nachsehen (`gh issue view {n} --comments`, ScheduleWakeup 1200 s, noop: true). "
        "Die Antwort des Aufsehers ist durch diesen Start-Auftrag freigegeben: annehmen und weiterbauen, "
        f"ohne Neustart. Die Datei `{briefkasten.datei_relativ(n)}` hat noch keinen Leser – nicht dort warten. "
        "Geklärt mit dem Nutzer wird erst, wenn die Spec auf Staging fertig ist."
    )
