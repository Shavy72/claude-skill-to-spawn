"""Befugnis einer Bau-Session: wer entscheidet was (#278, Lastenheft L8 A43–A49).

Einzige Stelle der Klassen-Tabelle. Aufrufer fragen nur:

- :func:`start_text` — Abschnitt für den Platzhalter ``{BEFUGNIS}`` im Bau-Prompt
  (gefüllt in ``skripte/bau.py``); die Ablöse-SOP lässt ihn wörtlich in den Startprompt
  der Folgesession übernehmen (Kopf :data:`KOPF`).
- :func:`einordnen` — festes Raster ``(ziel, art, gesichert) -> 1|2|3``.
- :func:`ist_klasse_2` — schlägt ein freier Text (Frage, Vorschlag, Ziel, Art) an Klasse 2 an?
- :func:`handlung_klasse_2` — steht in einem Verlauf (Texte, Befehle, Werkzeuge, Pfade) eine
  Klasse-2-Handlung? (Muster-Katalog nur hier, PR 374 Runde 9)
- :func:`aufseher_text` — Befugnis-Abschnitt beider Aufseher-Texte (Takt + Dauer-Aufseher):
  Klasse-2-Pflicht, Briefkasten-Weg, Umgehungs-Verbot.
- :func:`klasse_2_erkennung` — Klasse 2 samt Erkennungs-Wörtern als ein Text (für jede Liste
  „was ist Live/Kosten/Konto“ außerhalb dieses Moduls).
  Ort, Format und Lesen/Schreiben des Briefkastens gehören allein in ``to_spawn/briefkasten.py``.

Klasse 1 = die Session entscheidet selbst und hält es fest · Klasse 2 = nie ohne den
Nutzer, auch der Aufseher entscheidet nicht · Klasse 3 = alles andere und jeder Zweifel
zwischen 1 und 2 → Briefkasten an den Aufseher, nie eine Frage an den Nutzer.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass

#: Kopf des Abschnitts — an ihm erkennt die Ablöse-SOP, was wörtlich mitwandert.
KOPF = "Befugnis (wer entscheidet, gilt als Wort des Nutzers aus diesem Start-Auftrag):"

#: Was der Aufseher mit Klasse 2 tut — die eine Wendung für jeden Aufseher-Text und jeden Takt-Eintrag.
KLASSE_2_WEITERGABE = "nie selbst freigeben, geht an den Nutzer"
#: Anfang des Ticket-Kommentars an den Nutzer — der Takt erkennt daran (mit der id) den Beleg (#315).
KLASSE_2_KOMMENTAR = "Aufseher: braucht den Nutzer"


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
    # Ganzwörter an jedem Nicht-Wortzeichen trennen (Satzzeichen, Klammern, Anführungszeichen);
    # ``\W`` ist Unicode-bewusst, Umlaute bleiben Wortzeichen.
    teile = re.split(r"\W+", wert)
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


#: Steht vor jedem Handlungs-Grund ohne eigenes Klasse-2-Wort (``gh pr merge 374``): der Grund
#: wandert vor die Frage, und Briefkasten/Leiter ordnen die Frage mit :func:`ist_klasse_2` ein.
HANDLUNG_KOPF = "Live-/Kosten-/Konto-Handlung:"


def handlung_klasse_2(
    *,
    texte: Iterable[str] = (),
    befehle: Iterable[str] = (),
    werkzeuge: Iterable[str] = (),
    pfade: Iterable[str] = (),
) -> str | None:
    """Erste Zeile mit einer Klasse-2-HANDLUNG (Live, Kosten, Konto, Zugang, Löschen) oder ``None``.

    Für lange Verläufe (Transkript einer Session, :mod:`to_spawn.verlauf_pruefung`, PR 374
    Runde 9): dort stehen Projekt-Wörter (live, vps, Kosten …) fast überall, gezählt wird darum nur
    eine Handlung. ``texte`` = eigene Texte und Unter-Agent-Aufträge (Verb + Ziel in einem Satz),
    ``befehle`` = Shell-Befehle (ohne Inhalte von Anführungszeichen und Heredocs, außer hinter
    ``bash -c``/``eval``/``ssh <host>`` …), ``werkzeuge`` = Werkzeug-Namen, ``pfade`` =
    Datei-Pfade (Inhalte nie). Rückgabe: die Zeile, zusammengezogen; trägt sie selbst kein
    Klasse-2-Wort, steht :data:`HANDLUNG_KOPF` davor. Freie Fragen und Vorschläge bleiben bei der
    breiten :func:`ist_klasse_2` (Wortteile zählen dort).

    Bekannte Rest-Risiken: ein Deploy-Weg ohne Muster rutscht durch (Skript ohne Deploy-Wort im
    Namen und ohne ``--prod``, Befehl in einer Datei); Kosten durch Lese-Aufrufe bezahlter
    APIs werden nicht erkannt (wie vorher); unverändert offen: T1, T10, T11 und Schreib-Verzug.
    """
    zeile = (
        _erste(texte, _text_zeile)
        or _erste(befehle, _befehl_zeile)
        or _erste(werkzeuge, lambda w: w if _WERKZEUG.search(w.lower()) else None)
        or _erste(pfade, lambda p: p if _PFAD.search(p.lower()) else None)
    )
    if zeile is None:
        return None
    zeile = " ".join(zeile.split())
    return zeile if ist_klasse_2(zeile) else f"{HANDLUNG_KOPF} {zeile}"


def _erste(werte: Iterable[str], finde: Callable[[str], str | None]) -> str | None:
    for wert in werte:
        if isinstance(wert, str) and wert.strip():
            if (treffer := finde(unicodedata.normalize("NFKC", wert))) is not None:
                return treffer
    return None


# --- Befehle ---------------------------------------------------------------------------------

#: Heredoc-Inhalt bleibt immer im Befehl: jeder Interpreter (bash, python, psql, node …) führt ihn
#: aus. ponytail: ``cat > datei <<EOF`` mit Live-Wörtern wird Fehlalarm, sichere Richtung (PR 374 R10).
_ZITAT = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
#: Inhalt in Anführungszeichen direkt dahinter wird ausgeführt, bleibt also drin.
_DURCHREICHEN = re.compile(
    r"(?:\b(?:bash|sh|zsh|su)\s+(?:-\S+\s+)*-\w*c|\beval|\bssh\b[^;&|\n]*|\bxargs(?:\s+-\S+)*"
    r"|\btimeout\s+(?:-\S+\s+)*\S+|\bwatch(?:\s+-\S+)*)\s*$"
)
_ENV = r"\.env(?!\.(?:example|sample|template|dist))(?!\w)"
#: Anfang eines Befehlsteils samt Vorsätzen, die den Befehl nur weiterreichen.
_START = (
    r"(?:^|[;&|(`\n]|\$\()\s*(?:(?:sudo|exec|nohup|env|time|command)\s+(?:-\S+\s+)*)*(?:\w+=\S*\s+)*"
    r"(?:(?:timeout\s+(?:-\S+\s+)*\S+|xargs(?:\s+-\S+)*|watch(?:\s+-\S+)*)\s+)?"
)
_DEPLOY_AUFRUF = re.compile(
    _START + r"(?:(?:bash|sh|zsh|source|\.|python3?|uv\s+run(?:\s+python3?)?|make|node|npx)\s+(?:-\S+\s+)*)?"
    r"(?P<skript>[^\s;&|()`]*(?:deploy|rollout|release|publish|live|ship)[^\s;&|()`]*)"
)
#: Deploy-Skript-Name irgendwo im Befehl, auch in Anführungszeichen, Variable oder Schleife.
#: Nur als Wert (``S=…``, ``for s in …``, ``['…']``), nicht als bloßes Argument (``git add …``).
_DEPLOY_SKRIPT = re.compile(
    r"(?:=|\bin\s+|['\"(\[,]\s*)[\w./~-]*(?:deploy|rollout|release|publish)[\w.-]*\.(?:sh|ps1)(?![\w.])"
)
#: Heredoc-Inhalt; dort zählt auch Zitiertes (``subprocess.run(['ssh', …])``).
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)(\w+)\1[^\n]*\n(.*?)(?=\n[ \t]*\2[ \t]*(?:\n|$)|\Z)", re.S)
_GIT = r"\bgit(?:\s+(?:-c\s+\S+|--[\w-]+=\S+))*"  # „git -C <pfad> push“ (Text ist klein geschrieben)
_BEFEHL = re.compile(
    "|".join(
        (
            # Fernzugriff
            r"(?<![\w.-])(?:ssh|scp|sftp)(?![\w-])",
            r"\brsync\b[^;&|\n]*\s[\w.@-]+:",
            # weitere Deploy-Befehle
            r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:deploy|release|publish)\b",
            r"\b(?:vercel|netlify|fly|flyctl)\s+deploy\b|\s--prod(?:uction)?(?![\w-])",
            r"\bkubectl\s+(?:apply|delete|rollout)\b|\bterraform\s+(?:apply|destroy)\b",
            r"\bgh\s+workflow\s+run\b|\bdocker\s+push\b|\bgh\s+release\s+create\b",
            r"\bsystemctl\s+(?:restart|stop|disable)\b|\bcrontab\s+-r\b",
            # Merge
            r"\bgh\s+pr\s+merge\b|\bgh\s+api\b[^;&|\n]*/merge\b",
            _GIT + r"\s+push\b[^;&|\n]*(?:\s|:|refs/heads/)(?:main|master)(?=$|[\s;&|)])",
            _GIT + r"\s+push\b[^;&|\n]*\s--(?:mirror|all)\b",
            # HTTP mit Schreib-Methode
            r"\bcurl\b[^;&|\n]*?(?:\s-[a-z]*x\s*(?:post|put|delete|patch)\b|--request[\s=]+(?:post|put|delete|patch)\b"
            r"|\s-d(?![\w-])|\s--data[\w-]*|\s--form\b)",
            r"\bwget\b[^;&|\n]*--post",
            # Zugang
            r"(?<![\w.-])bw\s+[a-z]",
            r"\bgh\s+(?:auth|secret)\b|\bop\s+(?:read|item|signin|inject)\b",
            r"\b(?:cat|source|less|more|head|tail|bat)\b[^;&|\n]*" + _ENV,
            r"(?:^|[;&|(\s])\.\s+\S*" + _ENV + r"|>>?\s*\S*" + _ENV,
            # Löschen
            r"\bgh\s+(?:repo|release)\s+delete\b",
            r"\bdocker\s+volume\s+(?:rm|prune)\b|\bdocker\s+system\s+prune\b",
            r"\bdocker[\s-]compose\b[^;&|\n]*\sdown\b[^;&|\n]*\s-v\b",
            # Geld
            r"(?<![\w.-])(?:stripe|paypal)(?![\w-])",
        )
    )
)
#: Auch in Anführungszeichen gesucht (dort stehen Pfade und SQL meist).
_SQL = re.compile(r"\b(?:drop\s+(?:table|database|schema)|delete\s+from|truncate\s+table)\b")
_RM = re.compile(r"(?:^|[;&|(`\s])rm\s+([^;&|\n`)]*)")
_RM_ERLAUBT = re.compile(r"(?:/tmp/|/var/tmp/|(?:~|\$\{?home\}?|/home/[^/\s]+|/root)/wt/)")
_PUSH = re.compile(_GIT + r"\s+push\b([^;&|\n]*)")
_EIGENER_ZIEL = re.compile(r"(?:refs/heads/)?(?:wip|ticket)/\S")


def _nur_umbrueche(text: str) -> str:
    return "\n" * text.count("\n")


def _ohne_zitate(b: str) -> str:
    """Inhalte von ``'…'``/``"…"`` weg (Umbrüche bleiben), außer sie werden ausgeführt."""
    teile: list[str] = []
    pos = 0
    for m in _ZITAT.finditer(b):
        innen = m.group(0)[1:-1]
        teile.append(b[pos : m.start()])
        if _DURCHREICHEN.search(b[max(0, m.start() - 200) : m.start()]) or (m.group(0)[0] == '"' and "$(" in innen):
            teile.append(f";{_ohne_zitate(innen)};")  # „;“: Inhalt ist ein eigener Befehlsteil
        else:
            teile.append(" " + _nur_umbrueche(innen) + " ")
        pos = m.end()
    teile.append(b[pos:])
    return "".join(teile)


def _rm_treffer(b: str) -> re.Match[str] | None:
    for m in _RM.finditer(b):
        teile = m.group(1).split()
        if not any(re.fullmatch(r"-[a-z]*r[a-z]*|--recursive", t) for t in teile):
            continue
        for t in teile:
            if t[:1] in "/~$" and not t.startswith("-") and not _RM_ERLAUBT.match(t):
                return m
    return None


def _force_push_treffer(b: str) -> re.Match[str] | None:
    for m in _PUSH.finditer(b):
        teile = re.split(r"\s+\d*[<>]", m.group(1))[0].split()  # Umleitung „2>…“ gehört nicht dazu
        flags = [t for t in teile if t.startswith("-")]
        rest = [t for t in teile if not t.startswith("-")]
        force = any(re.fullmatch(r"-[a-z]*f[a-z]*|--force(?:-with-lease|-if-includes)?(?:=\S*)?", t) for t in flags)
        if not force and not any(t.startswith("+") for t in rest):
            continue
        ziele = [t.lstrip("+").rpartition(":")[2] for t in rest[1:]]
        if not ziele or not all(_EIGENER_ZIEL.match(z) for z in ziele):
            return m
    return None


def _befehl_zeile(befehl: str) -> str | None:
    """Zeile von ``befehl`` mit Klasse-2-Handlung (siehe :func:`handlung_klasse_2`)."""
    roh = befehl.lower()
    ohne = _ohne_zitate(roh)
    offen = re.sub(r"['\"]", " ", roh)  # Inhalte bleiben, nur die Zeichen fallen
    # Beide Sichten behalten die Zeilen-Umbrüche: Zeilen-Nummer = Umbrüche vor dem Treffer.
    nr: list[int] = []
    if m := _BEFEHL.search(ohne):
        nr.append(ohne.count("\n", 0, m.end()))
    for m in _DEPLOY_AUFRUF.finditer(ohne):
        skript = m.group("skript")
        if "staging" not in skript and "tests/" not in skript and "=" not in skript:
            nr.append(ohne.count("\n", 0, m.start("skript")))
            break
    if m := _force_push_treffer(ohne):
        nr.append(ohne.count("\n", 0, m.end()))
    for m in _DEPLOY_SKRIPT.finditer(roh):
        if "staging" not in m.group(0) and "tests/" not in m.group(0):
            nr.append(roh.count("\n", 0, m.end()))
            break
    for h in _HEREDOC.finditer(roh):
        if m := _BEFEHL.search(re.sub(r"['\"]", " ", h.group(3))):
            nr.append(roh.count("\n", 0, h.start(3) + m.end()))
            break
    if m := _rm_treffer(offen) or _SQL.search(offen):
        nr.append(offen.count("\n", 0, m.end()))
    if not nr:
        return None
    zeilen = befehl.splitlines() or [befehl]
    return zeilen[min(min(nr), len(zeilen) - 1)]


# --- Texte -----------------------------------------------------------------------------------

#: Ziel einer Live-Handlung; als Wortteil erlaubt („neuerVps“, „deployProd“), aber nicht vor einem
#: weiteren Buchstaben („deliver“, „reproduziert“, „Produkt“).
_LIVE_ZIEL = re.compile(r"(?:live|prod|produktion|production|vps|hauptapp)(?![a-zäöüß])", re.I)
_LIVE_VERB = re.compile(
    # Trennbare Verben („spiele … ein“, „rolle … aus“, „starte … neu“): Stamm allein zählt.
    r"(?<!\w)(?:(?:ein|aus|neu)?(?:ge)?(?:deploy|schalt|roll|spiel|start|restart)|aus(?:ge)?liefer"
    r"|veröffentlich|hoch(?:ge)?lad)\w*",
    re.I,
)
_TEXT_FEST = re.compile(
    r"go[-_ ]?live(?![a-zäöüß])"
    r"|(?<!\w)(?:geht|ging|gehen|gegangen)\s+(?:(?:jetzt|direkt|sofort|gleich)\s+)?live(?!\w)"
    r"|(?<!\w)(?:live|prod)[-_ ]?(?:deploy\w*|stand|server|system|db|datenbank|daten|umgebung|app|instanz"
    r"|container|api)(?!\w)",
    re.I,
)
#: (Verb, Objekt) je Satz, höchstens :data:`_ABSTAND` Zeichen auseinander, beliebige Reihenfolge;
#: Konto-Objekte dürfen Wortteil sein („Kundenkonto“).
_TEXT_PAARE = tuple(
    (re.compile(v, re.I), re.compile(o, re.I))
    for v, o in (
        (  # Kosten
            r"(?<!\w)(?:kauf|bestell|bezahl|abonnier|kündig|überweis|gekauf|gekündig|buch(?:e|en|t|te|ten|ung))\w*",
            r"(?<!\w)abo|(?<!\w)plan(?!\w)|tarif|lizenz|paket|euro(?!pa)|€|guthaben|credit|phone|gerät|vertrag",
        ),
        (  # Löschen
            r"(?<!\w)(?:lösch|gelösch|delete|wipe|drop)\w*",
            r"(?<!\w)(?:live(?!\w)|prod|datenbank|db(?!\w)|kunden|backup|repo)|konto|konten|account",
        ),
        (  # Konten und Zugang
            r"(?<!\w)(?:anleg|erstell|registrier|einlogg|anmeld|eintrag|rotier|leg|(?:ge)?logg|(?:ge)?setz|(?:ge)?änder)\w*",
            r"konto|konten|account|passw|api-?key|secret|zugang|2fa",
        ),
    )
)
#: Satz-Ende; „;“ und „|“ trennen im Text nicht (Tabellenzeile „| Kauf | Abo |“, PR 374 R10).
_SATZ = re.compile(r"(?<=[.!?·])\s+")
_ABSTAND = 120


def _paar(verb: re.Pattern[str], ziel: re.Pattern[str], satz: str) -> bool:
    verben = [m.span() for m in verb.finditer(satz)]
    return any(max(0, z.start() - e, a - z.end()) <= _ABSTAND for z in ziel.finditer(satz) for a, e in verben)


def _text_zeile(text: str) -> str | None:
    """Zeile von ``text`` mit Klasse-2-Handlung (alles innerhalb eines Satzes)."""
    for zeile in text.splitlines():
        for satz in _SATZ.split(zeile):
            if (
                _TEXT_FEST.search(satz)
                or _paar(_LIVE_VERB, _LIVE_ZIEL, satz)
                or any(_paar(v, o, satz) for v, o in _TEXT_PAARE)
            ):
                return zeile
    return None


# --- Werkzeuge und Pfade ---------------------------------------------------------------------

_WERKZEUG = re.compile(
    r"^mcp__.*(?:purchase|buy|order|renew|pay|billing|delete|remove|destroy|deploy|create_?phone|send"
    r"|ssh|remote)"
)
_PFAD = re.compile(_ENV + r"|secret|credential")


def _ziel_text(ziel: str, n: str | int) -> str:
    """Marke → lesbare Regel; ohne bekannte eigene Nummer Klartext statt ``<n>``."""
    if ziel not in (_EIGENER_TICKET_ZWEIG, _EIGENER_WIP_ZWEIG):
        return ziel
    art = ziel.split("/")[0]
    if str(n).strip().isdigit():
        return f"{art}/{str(n).strip()}-<kurzname>"
    return f"eigener {'Ticket' if art == 'ticket' else 'Sicherungs'}-Zweig {art}/<eigene Nummer>-<kurzname>"


def klasse_2_erkennung() -> str:
    """Klasse 2 mit allen Erkennungs-Wörtern — die eine Fassung für jede Liste „was gehört dem Nutzer“."""
    teilwort = ", ".join(sorted(_KLASSE_2_WOERTER))
    ganzwort = ", ".join(sorted(_KLASSE_2_GANZWORT))
    return f"{_KLASSE_2.text} (erkannt an: {teilwort}, auch als Wortteil; {ganzwort} nur als ganzes Wort)"


def start_text(n: str | int) -> str:
    """Befugnis-Abschnitt für den Start-Auftrag von Ticket ``n`` — der Text steht nur hier."""
    klasse_1 = "; ".join(
        f"{regel.text.replace('<N>', str(n))} (Ziel: {', '.join(sorted(_ziel_text(z, n) for z in regel.ziele or ()))} · "
        f"Art: {', '.join(sorted(regel.arten or ()))})"
        for regel in _KLASSE_1
    )
    return (
        f"{KOPF} "
        f"Klasse 1 – selbst entscheiden, nicht fragen: {klasse_1}. Kurzname nur Kleinbuchstaben, Ziffern, `.`, `_`, `-` und endet auf Buchstabe/Ziffer. "
        "Jede solche Entscheidung festhalten "
        "(`to_spawn.py eintrag --typ entscheidung`: „Klasse 1“, Grund und bei Daten der Sicherungs-Beleg). "
        f"Klasse 2 – nie ohne den Nutzer: {klasse_2_erkennung()}. "
        f"Dort anhalten, die Frage in den Briefkasten werfen (`to_spawn.py frage {n} --klasse 2 --frage … --vorschlag …`; "
        "der Aufseher bringt sie zum Nutzer), Bau-Log-Eintrag und Issue-Kommentar „braucht den Nutzer“; "
        "auch eine Antwort des Aufsehers gibt das nicht frei, nur die des Nutzers im Briefkasten. "
        "Klasse 3 – alles andere, jeder Zweifel zwischen Klasse 1 und 2 und jeder Push oder Merge auf "
        f"den Hauptzweig oder fremde Zweige: Frage mit eigenem Vorschlag in den Briefkasten "
        f"(`to_spawn.py frage {n} --frage … --vorschlag <Vorschlag>`, "
        "gibt die id aus), nie eine Frage an den Nutzer. Der Aufseher liest den Briefkasten und antwortet dort. "
        f"Nachsehen mit `to_spawn.py antwort {n} --id <id>`: Exit 0 = Antwort da → annehmen und weiterbauen, "
        "ohne Neustart (durch diesen Start-Auftrag freigegeben) · 3 = wartet → ScheduleWakeup 1200 s, noop: true, "
        "dann wieder nachsehen · 2 = braucht den Nutzer → angehalten lassen · 4 = Frage unbekannt → Frage neu stellen "
        "und mit der neuen id weiter · 1 = Briefkasten nicht lesbar → wie 3 warten, dann wieder nachsehen · "
        "5 = Aufruf falsch → Ticket-Nummer und `--id` prüfen, richtig aufrufen. Nur die Antwort im Briefkasten gilt, keine Nachricht und kein Kommentar. "
        "Geklärt mit dem Nutzer wird erst, wenn die Spec auf Staging fertig ist."
    )


def aufseher_text(befehl: str) -> str:
    """Befugnis-Abschnitt für beide Aufseher-Texte; ``befehl`` = Aufruf von ``to_spawn.py``.

    Ohne geschweifte Klammern (die Aufseher-Vorlagen laufen danach durch ``str.format``).
    """
    b = befehl
    return (
        f"Befugnis und Briefkasten (Tür B): PFLICHT: Klasse 2 – {klasse_2_erkennung()} – "
        f"{KLASSE_2_WEITERGABE}; die Handlung auch nach deiner Antwort nie selbst ausführen. "
        f"Offene Briefkasten-Fragen: `{b} offene` (je Ticket id, Klasse, Frage, Vorschlag). "
        "Klasse 3 entscheidest du: Helfer suchen die Haltung des Nutzers (Gedächtnis, Übergaben, Lastenheft, "
        f"Chat-Protokolle), dann `{b} antworten <N> --id <id> --wahl … --grund … --beleg …` (Antwort gilt nur "
        "im Briefkasten) plus Spiegel-Kommentar im Ticket „Aufseher: Entscheidung <id>: <Wahl> — Belege: …“ "
        "(max. 2 Zeilen); kein zusätzlicher Bau-Log-Eintrag dafür (die Entscheidungs-Liste liest den Briefkasten). "
        f"Klasse 2 oder gesperrt (leere id = kaputte Datei) → nicht antworten, {KLASSE_2_WEITERGABE}: dem Nutzer "
        f"melden, mit fertigem Befehl für sein eigenes Terminal: Kommentar im Ticket „{KLASSE_2_KOMMENTAR}: "
        f"<Frage> — bitte im eigenen Terminal ausführen: `{b} nutzer-antwort <N> --id <id> --wahl <Wahl> "
        "--grund <Grund>`“ (N und id eingesetzt). Nur diese Antwort im Briefkasten gibt frei, nie ein "
        "Kommentar im Ticket; eine kaputte Datei prüft und löscht nur der Nutzer. "
        "VERBOTEN: die Sperre von `nutzer-antwort` umgehen (Schein-Terminal wie `script -qc`, direkter "
        "Python-Aufruf von `nutzer_antworten`, Hand-Eintrag in die Briefkasten-Datei) und `nutzer-antwort` "
        "selbst ausführen oder von einer Session ausführen lassen. "
        "Übergang (alter Start-Auftrag: Rückfrage nur im Bau-Log als blockiert/frage, ohne Briefkasten-Frage): "
        "gleiche Klassen; Klasse 3 per Issue-Kommentar (max. 2 Zeilen) + "
        f"`{b} eintrag --ticket <N> --typ entscheidung --frage … --wahl … --grund …`; "
        f"Klasse 2 {KLASSE_2_WEITERGABE}: Kommentar im Ticket „{KLASSE_2_KOMMENTAR}: <Frage>“."
    )
