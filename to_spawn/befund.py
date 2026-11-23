"""Befunde und Folge-Tickets: gelb → Folge-Ticket der Spec, nur rot öffnet wieder (#438, E2-4).

Einzige Stelle der Regel, was ein Befund an einem geschlossenen Ticket auslöst:

* **rot** (Akzeptanz nicht erfüllt, Beleg fehlt): Ticket mit Kommentar
  ``Aufseher: <regel> — <text>`` wieder öffnen. capo nutzt :func:`wieder_oeffnen`
  für seine Maschinen-Verstöße (Commit/Beweis/Tests/VPS) — dieselben Zeilen.
* **gelb** (Hinweis, Kette darf weiter): neues Folge-Ticket auf GitHub anlegen, im
  Manifest der Spec mit Herkunft eintragen (``gelb_von``, ``schaetzung_k``, ``umfang``,
  ``title``) und über :func:`spec_rand.anhaengen` an die Spec hängen (Kanten,
  Unter-Issue). Das Ticket selbst bleibt zu. Bau-Sessions und Aufseher legen
  Folge-Tickets nur so an (``to_spawn.py befund --spec <S>``), nie per rohem
  ``gh issue create`` — sonst hat das Ticket keinen Besitzer (E25/E56).

Folge-Tickets halten das Schließen der Spec auf (umgedreht seit E2-4):
:func:`spec_offen` ist die einzige Regel, wann eine Spec schließbar ist. Der Befehl
``to_spawn.py spec-schliessen`` fragt sie; der Hook ``hook-spec-sperre`` sperrt rohes
``gh issue close`` für jede Nummer, für die :func:`ist_spec` gilt.

:func:`melde` liefert ein :class:`Ergebnis` (``ok`` + Ausgabezeilen); der Exit-Code
der CLI hängt nur an ``ok``, nie an Textsuche in den Zeilen.

Für die Gesamtabnahme liefert :func:`gelbe_folgen` die Folge-Tickets mit Status
(gebaut / verworfen / verschoben / offen / unbekannt), :func:`abschnitt` die fertigen
Ausgabezeilen — ein kaputtes Manifest wird dort als FEHLER-Zeile sichtbar.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias

from . import config, gh, manifest, spec_rand, ticket_zustand

log = logging.getLogger("to_spawn.befund")

#: Kopf jedes Aufseher-Kommentars auf GitHub.
KOPF = "Aufseher:"
STUFEN = ("gelb", "rot")
#: Label, mit dem der Mensch ein gelbes Folge-Ticket bewusst auf später schiebt.
LABEL_VERSCHOBEN = "verschoben"
#: Vorgabe-Schätzung (Tausend Token) für ein gelbes Folge-Ticket — klein, ein Hinweis.
SCHAETZUNG_GELB_K = 30
#: Regel-Name im Reopen-Kommentar eines roten Aufseher-Befunds.
REGEL_ROT = "Belegseite"
#: Höchstzahl nicht-leerer Zeilen je Aufseher-Kommentar (#1522, Fehlerkatalog S13):
#: jeder Kommentar landet im Kontext der Bau-Session.
KOMMENTAR_MAX_ZEILEN = 2
#: Höchstlänge je Kommentarzeile in Zeichen (sonst wird eine Zeile zum Roman).
KOMMENTAR_MAX_ZEICHEN = 500
UEBERSCHRIFT = "Gelbe Folge-Tickets (Gesamtabnahme)"

_ISSUE_URL = re.compile(r"/issues/(\d+)\s*$")

#: Abruf eines Issues: Nummer → GitHub-Daten (``state``, ``state_reason``, ``labels``)
#: oder ``None``, wenn der Abruf scheitert.
Abruf: TypeAlias = Callable[[int], dict[str, Any] | None]


@dataclass(frozen=True)
class Ergebnis:
    """Ausgang von :func:`melde` und :func:`zuruecknehmen`: ``ok`` = vollständig verarbeitet."""

    ok: bool
    zeilen: list[str]


@dataclass(frozen=True)
class GelbeFolge:
    """Ein gelbes Folge-Ticket der Spec."""

    nummer: int
    gelb_von: int
    titel: str
    status: str  # gebaut | verworfen | verschoben | offen | unbekannt


#: Zeitlimit je Status-Abruf; überschritten zählt als „unbekannt“ (schließt nicht).
GH_ZEITLIMIT_S = 30


def melde(
    repo: Path,
    gh_repo: str,
    spec: int | str,
    ticket: int | str,
    stufe: str,
    text: str,
    dry_run: bool = False,
) -> Ergebnis:
    """Befund an Ticket ``ticket`` melden; das Modul entscheidet die Aktion.

    ``ok=False`` heißt: der Befund ist nicht vollständig verarbeitet — die Zeilen
    sagen, was fehlt (ggf. was von Hand nachzutragen ist). Ein schon gemeldeter
    gleicher gelber Befund ist ``ok`` (kein zweites Folge-Ticket).
    ``ValueError`` bei unbekannter Stufe.
    """
    if stufe not in STUFEN:
        raise ValueError(f"Stufe {stufe!r} unbekannt — erlaubt: {', '.join(STUFEN)}")
    n = int(ticket)
    if stufe == "rot":
        return _oeffne(n, gh_repo, [(REGEL_ROT, text)], dry_run)
    return _folge_ticket(repo, gh_repo, int(spec), n, text, dry_run)


def kuerze(text: str) -> str:
    """Kommentartext auf höchstens :data:`KOMMENTAR_MAX_ZEILEN` nicht-leere Zeilen kürzen.

    Leerzeilen fallen weg; was darüber liegt, ersetzt ein Vermerk am Ende der letzten
    Zeile (``… +N Zeilen gekürzt``) — die vollen Gründe stehen im Aufseher-Log. Jede
    Zeile ist höchstens :data:`KOMMENTAR_MAX_ZEICHEN` lang (Rest ``…``).
    """
    zeilen = [z.rstrip() for z in text.splitlines() if z.strip()]
    rest = len(zeilen) - KOMMENTAR_MAX_ZEILEN
    zeilen = zeilen[:KOMMENTAR_MAX_ZEILEN]
    if rest > 0:
        zeilen[-1] += f" … +{rest} Zeilen gekürzt"
    grenze = KOMMENTAR_MAX_ZEICHEN
    return "\n".join(z if len(z) <= grenze else z[: grenze - 1] + "…" for z in zeilen)


def kommentiere(n: int | str, gh_repo: str, text: str, befehl: str = "comment") -> bool:
    """Einzige Schreib-Stelle für Aufseher-Kommentare auf GitHub (#1522); ``True`` = geklappt.

    ``befehl`` = ``comment`` (nur kommentieren), ``reopen`` oder ``close`` (Kommentar am
    Zustandswechsel). Der Text wird immer mit :func:`kuerze` gekürzt; ein Prüf-Test
    bricht, wenn irgendwo am Modul vorbei kommentiert wird.
    """
    erlaubt = ("comment", "reopen", "close")
    if befehl not in erlaubt:
        raise ValueError(f"befehl {befehl!r} unbekannt — erlaubt: {', '.join(erlaubt)}")
    schalter = "--body" if befehl == "comment" else "--comment"
    args = ["issue", befehl, str(n), "--repo", gh_repo, schalter]
    code, _ = gh.lauf([*args, kuerze(text)])
    if code != 0:
        log.warning("gh issue %s #%s gescheitert (Exit %s).", befehl, n, code)
    return code == 0


def wieder_oeffnen(n: int, gh_repo: str, gruende: Sequence[tuple[str, str]], dry_run: bool) -> list[str]:
    """Ticket einmal wieder öffnen; letzte Zeile ``#N wieder geöffnet`` = geklappt.

    ``gruende`` sind Paare (Regel, Text); Kommentar-Form siehe :func:`_gruende_text`. Zeilen-API
    für capo (prüft den Zeilentext); :func:`melde` nutzt dieselbe Regel mit ok-Flag.
    """
    return _oeffne(n, gh_repo, gruende, dry_run).zeilen


def erneut_melden(n: int, gh_repo: str, gruende: Sequence[tuple[str, str]], dry_run: bool) -> list[str]:
    """Gleicher Grund wie bei einem früheren Wiederöffnen: nur kommentieren, nicht öffnen (#275).

    Wiederöffnen ist die härteste Reaktion und gilt je Verstoß (Ticket, Regel, Inhalt)
    einmal; danach muss ein Mensch ran. Letzte Zeile ``#N erneut gemeldet`` = geklappt.
    """
    if dry_run:
        return [f"#{n} [Probe] würde erneut melden (nicht wieder öffnen)"]
    vermerk = " (erneut — nicht wieder geöffnet, ein Mensch muss prüfen)"
    kommentar = _gruende_text([(regel, f"{text}{vermerk}") for regel, text in gruende])
    if kommentiere(n, gh_repo, kommentar):
        return [f"#{n} erneut gemeldet"]
    return [f"#{n} FEHLER: erneut melden gescheitert"]


def _gruende_text(gruende: Sequence[tuple[str, str]]) -> str:
    """Reopen-Kommentar, der in :data:`KOMMENTAR_MAX_ZEILEN` passt, ohne eine Regel zu verlieren.

    Bis zu 2 Gründe: je Grund eine Zeile. Mehr: Zeile 1 nennt alle Regeln gebündelt (die
    Bau-Session erfährt jede, auch wenn :func:`kuerze` Zeile 2 abschneidet), Zeile 2 die
    Texte mit ``·`` getrennt.
    """
    paare = [(regel, " ".join(str(text).split())) for regel, text in gruende]
    if len(paare) <= KOMMENTAR_MAX_ZEILEN:
        return "\n".join(f"{KOPF} {regel} — {text}" for regel, text in paare)
    regeln = " · ".join(regel for regel, _ in paare)
    texte = " · ".join(f"{regel} — {text}" for regel, text in paare)
    return f"{KOPF} {len(paare)} Verstöße: {regeln}\n{KOPF} {texte}"


def _oeffne(n: int, gh_repo: str, gruende: Sequence[tuple[str, str]], dry_run: bool) -> Ergebnis:
    if dry_run:
        return Ergebnis(ok=True, zeilen=[f"#{n} [Probe] würde wieder öffnen"])
    kommentar = _gruende_text(gruende)
    if kommentiere(n, gh_repo, kommentar, befehl="reopen"):
        return Ergebnis(ok=True, zeilen=[f"#{n} wieder geöffnet"])
    return Ergebnis(ok=False, zeilen=[f"#{n} FEHLER: wieder öffnen gescheitert"])


def zuruecknehmen(n: int, gh_repo: str, text: str, dry_run: bool) -> Ergebnis:
    """Eigenes Fehl-Reopen zurücknehmen: Ticket wieder schließen, mit Kommentar (#448).

    ``ok`` = Ticket wirklich geschlossen; sonst Probelauf oder gescheitert (``FEHLER``-Zeile,
    der nächste Tick versucht es wieder). Ob zurückgenommen werden darf,
    entscheidet der Aufrufer (capo).
    """
    if dry_run:
        return Ergebnis(ok=False, zeilen=[f"#{n} [Probe] würde Fehl-Reopen zurücknehmen"])
    kommentar = f"{KOPF} Fehl-Reopen zurückgenommen — {text}"
    if kommentiere(n, gh_repo, kommentar, befehl="close"):
        return Ergebnis(ok=True, zeilen=[f"#{n} Fehl-Reopen zurückgenommen"])
    log.warning("Fehl-Reopen #%s nicht zurückgenommen.", n)
    return Ergebnis(ok=False, zeilen=[f"#{n} FEHLER: Fehl-Reopen zurücknehmen gescheitert"])


def _titel(ticket: int, text: str) -> str:
    erste = (text.strip().splitlines() or [""])[0]
    kurz = erste if len(erste) <= 60 else erste[:59] + "…"
    return f"Gelb aus #{ticket}: {kurz}"


def _gelb_von(eintrag: Any) -> int | None:
    """``gelb_von`` eines Manifest-Eintrags als Zahl; ``None`` = kein/kaputter Wert."""
    if not isinstance(eintrag, dict) or "gelb_von" not in eintrag:
        return None
    try:
        return int(eintrag["gelb_von"])
    except (TypeError, ValueError):
        return None


def _schon_gemeldet(tickets: dict[str, Any], ticket: int, umfang: str) -> str | None:
    """Nummer eines vorhandenen Folge-Tickets mit gleichem Ursprung und Befundtext."""
    for nummer, eintrag in tickets.items():
        if _gelb_von(eintrag) != ticket:
            continue
        if str(eintrag.get("umfang", "")).strip() == umfang:
            return nummer
    return None


def _folge_ticket(repo: Path, gh_repo: str, spec: int, ticket: int, text: str, dry_run: bool) -> Ergebnis:
    titel = _titel(ticket, text)
    umfang = text.strip()
    datei = manifest.manifest_pfad(repo, spec)
    try:
        daten, _ = manifest.lies_json(datei)
    except (OSError, ValueError) as fehler:
        return Ergebnis(
            ok=False,
            zeilen=[f"#{ticket} FEHLER: Manifest {datei.name} nicht lesbar ({fehler}) — kein Folge-Ticket"],
        )
    if not isinstance(daten, dict) or not isinstance(daten.get("tickets"), dict):
        return Ergebnis(
            ok=False,
            zeilen=[f"#{ticket} FEHLER: Manifest {datei.name} ohne Ticket-Tabelle — kein Folge-Ticket"],
        )
    vorhanden = _schon_gemeldet(daten["tickets"], ticket, umfang)
    if vorhanden is not None:
        zeilen = [f"#{ticket} gelb schon gemeldet: Folge-Ticket #{vorhanden}"]
        if dry_run:
            return Ergebnis(ok=True, zeilen=zeilen)
        # Wiederholung holt ein früher gescheitertes Anhängen nach (anhaengen ist idempotent).
        return _an_spec_haengen(repo, gh_repo, spec, int(vorhanden), zeilen)
    if dry_run:
        return Ergebnis(
            ok=True,
            zeilen=[f"#{ticket} [Probe] gelb → würde Folge-Ticket „{titel}“ anlegen"],
        )

    body = (
        f"Teil von Spec #{spec}, gelber Befund aus #{ticket}.\n\n"
        f"{umfang}\n\n"
        f"Steht im Manifest spec-{spec}.json mit `gelb_von: {ticket}`; "
        f"Label `{LABEL_VERSCHOBEN}` = bewusst auf später geschoben."
    )
    code, ausgabe = gh.lauf(["issue", "create", "--repo", gh_repo, "--title", titel, "--body", body])
    treffer = _ISSUE_URL.search(ausgabe or "")
    if code != 0 or not treffer:
        log.warning("gh issue create gescheitert (Code %s): %s", code, (ausgabe or "")[:200])
        return Ergebnis(
            ok=False,
            zeilen=[f"#{ticket} FEHLER: gelbes Folge-Ticket nicht angelegt (gh issue create)"],
        )
    neu = treffer.group(1)
    daten["tickets"][neu] = {
        "title": titel,
        "schaetzung_k": SCHAETZUNG_GELB_K,
        "umfang": umfang,
        "gelb_von": ticket,
    }
    try:
        manifest.schreibe_atomar(datei, daten)
    except OSError as fehler:
        log.error("Manifest %s nicht geschrieben (Folge-Ticket #%s): %s", datei, neu, fehler)
        return Ergebnis(
            ok=False,
            zeilen=[
                (
                    f"#{ticket} FEHLER: Folge-Ticket #{neu} angelegt, Manifest nicht "
                    f"geschrieben — von Hand eintragen ({datei.name}, gelb_von: {ticket})"
                )
            ],
        )
    zeilen = [f"#{ticket} gelb → Folge-Ticket #{neu} angelegt, im Manifest {datei.name}"]
    return _an_spec_haengen(repo, gh_repo, spec, int(neu), zeilen)


def _an_spec_haengen(repo: Path, gh_repo: str, spec: int, neu: int, zeilen: list[str]) -> Ergebnis:
    """Folge-Ticket über :func:`spec_rand.anhaengen` an die Spec hängen (Kanten, Unter-Issue).

    Der Manifest-Eintrag steht schon (mit ``gelb_von``) — damit zählt das Ticket für
    :func:`spec_offen` sofort, auch wenn das Anhängen scheitert. ``anhaengen`` arbeitet
    gegen ``origin``; zeigt ``origin`` nicht auf ``gh_repo`` (Repo ohne GitHub-Zweig),
    bleibt es beim Manifest-Eintrag. Exit 3 (Prüfung bemängelt andere Tickets) ist kein
    Fehler des Folge-Tickets.
    """
    if gh.repo_aus_origin(repo) != gh_repo:
        log.warning("origin von %s ist nicht %s — Folge-Ticket #%s nur im Manifest", repo, gh_repo, neu)
        return Ergebnis(ok=True, zeilen=zeilen)
    lauf = spec_rand.anhaengen(repo, spec, neu, config.lade(repo))
    if lauf.exit == spec_rand.EXIT_FEHLER:
        return Ergebnis(
            ok=False,
            zeilen=[
                *zeilen,
                *lauf.zeilen,
                f"FEHLER: #{neu} nicht an Spec #{spec} gehängt — `to_spawn.py anhaengen {spec} {neu}` erneut",
            ],
        )
    if lauf.exit != 0:
        zeilen.append(f"Hinweis: Manifest-Prüfung der Spec #{spec} meldet Mängel — `to_spawn.py pruefen`")
    return Ergebnis(ok=True, zeilen=[*zeilen, f"#{neu} hängt an Spec #{spec}"])


def _status(daten: dict[str, Any] | None) -> str:
    if not isinstance(daten, dict):
        return "unbekannt"
    stufe = ticket_zustand.stufe(daten)  # Spec-Schluss zählt nur echten Schluss (#279)
    if stufe == "verworfen":
        return "verworfen"
    if stufe == "zu":
        return "gebaut"
    return "verschoben" if LABEL_VERSCHOBEN in ticket_zustand.label_namen(daten) else "offen"


def _abruf_github(repo: Path) -> Abruf:
    slug = gh.repo_aus_origin(repo)

    def abruf(n: int) -> dict[str, Any] | None:
        if not slug:
            return None
        daten = gh.json_lauf(["api", f"repos/{slug}/issues/{n}"], cwd=repo, timeout=GH_ZEITLIMIT_S)
        return daten if isinstance(daten, dict) else None

    return abruf


class ManifestKaputt(ValueError):
    """Manifest der Spec ist da, aber nicht lesbar (kaputtes JSON, keine Ticket-Tabelle)."""


def gelbe_folgen(repo: Path, spec: int | str, abruf: Abruf | None = None) -> list[GelbeFolge]:
    """Gelbe Folge-Tickets der Spec mit Status; leer ohne Manifest.

    ``abruf`` holt je Nummer die GitHub-Daten (Vorgabe: ``gh api issues/<n>``).
    :class:`ManifestKaputt`, wenn das Manifest da, aber nicht lesbar ist.
    Einträge mit kaputtem ``gelb_von`` werden mit Warnung übersprungen.
    """
    datei = manifest.manifest_pfad(repo, spec)
    try:
        daten, _ = manifest.lies_json(datei)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as fehler:
        raise ManifestKaputt(f"{datei.name} nicht lesbar ({fehler})") from fehler
    tickets = daten.get("tickets") if isinstance(daten, dict) else None
    if not isinstance(tickets, dict):
        raise ManifestKaputt(f"{datei.name} ohne Ticket-Tabelle")
    holen = abruf or _abruf_github(repo)
    folgen: list[GelbeFolge] = []
    for nummer in sorted(tickets, key=manifest.ticket_schluessel):
        eintrag = tickets[nummer]
        if not isinstance(eintrag, dict) or "gelb_von" not in eintrag:
            continue
        von = _gelb_von(eintrag)
        if von is None or not nummer.isdigit():
            log.warning(
                "Manifest spec-%s: Eintrag %s mit kaputtem gelb_von %r — übersprungen",
                spec,
                nummer,
                eintrag.get("gelb_von"),
            )
            continue
        n = int(nummer)
        github = holen(n)
        if github is None:
            log.warning("Status von Folge-Ticket #%s nicht abrufbar — unbekannt", n)
        folgen.append(
            GelbeFolge(
                nummer=n,
                gelb_von=von,
                titel=str(eintrag.get("title", "")),
                status=_status(github),
            )
        )
    return folgen


def abschnitt(repo: Path, spec: int | str, abruf: Abruf | None = None) -> list[str]:
    """Ausgabezeilen für die Gesamtabnahme.

    Leer, wenn es keine gelben Folgen (oder kein Manifest) gibt; ein kaputtes
    Manifest erscheint als FEHLER-Zeile statt als leere Liste.
    """
    try:
        folgen = gelbe_folgen(repo, spec, abruf=abruf)
    except ManifestKaputt as fehler:
        log.warning("Gelbe Folgen spec-%s: %s", spec, fehler)
        return [UEBERSCHRIFT, f"FEHLER: Manifest {fehler} — gelbe Folgen unbekannt"]
    if not folgen:
        return []
    return [UEBERSCHRIFT] + [f"#{f.nummer} {f.status:<10} aus #{f.gelb_von}  {f.titel}" for f in folgen]


#: Status, mit dem ein Folge-Ticket das Schließen der Spec nicht aufhält: gebaut,
#: geschlossen (verworfen) oder gebündelt — Label ``verschoben`` = in ein Sammel-Issue
#: gelegt bzw. bewusst auf später geschoben (E25). ``offen``/``unbekannt`` halten auf.
#: ponytail: Bündel = nur das Label, kein Verweis aufs Sammel-Issue; Feld ``sammel`` im
#: Manifest, wenn der Mensch das Sammel-Issue nachverfolgen will.
ERLEDIGT = frozenset({"gebaut", "verworfen", "verschoben"})


def ist_spec(repo: Path, nummer: int | str) -> bool:
    """``True``, wenn ``nummer`` eine Spec dieses Repos ist (Manifest ``spec-<N>.json`` liegt da)."""
    return manifest.manifest_pfad(repo, nummer).is_file()


def spec_offen(repo: Path, spec: int | str, abruf: Abruf | None = None) -> list[str]:
    """Einzige Regel „darf die Spec schließen?“ — leer = ja, sonst je Hindernis eine Zeile.

    Hindernisse: keine Spec (kein Manifest), Manifest kaputt, reguläres Spec-Ticket oder
    Folge-Ticket weder gebaut noch geschlossen noch gebündelt (Status nicht in :data:`ERLEDIGT`, auch ``unbekannt``
    — fail-closed).
    """
    if not ist_spec(repo, spec):
        return [f"#{spec} ist keine Spec dieses Repos (kein Manifest spec-{spec}.json)"]
    holen = abruf or _abruf_github(repo)
    try:
        folgen = gelbe_folgen(repo, spec, abruf=holen)
    except ManifestKaputt as fehler:
        return [f"Manifest {fehler} — Folge-Tickets unbekannt"]
    stand = [(t, titel, _status(holen(t))) for t, titel in _spec_tickets(repo, spec)]
    hindernisse = [f"#{t} {s:<10} Spec-Ticket  {titel}" for t, titel, s in stand if s not in ERLEDIGT]
    return hindernisse + [
        f"#{f.nummer} {f.status:<10} aus #{f.gelb_von}  {f.titel}" for f in folgen if f.status not in ERLEDIGT
    ]


def _spec_tickets(repo: Path, spec: int | str) -> list[tuple[int, str]]:
    """Reguläre Spec-Tickets aus dem Manifest (Einträge ohne ``gelb_von``)."""
    daten, _ = manifest.lies_json(manifest.manifest_pfad(repo, spec))
    tickets = daten.get("tickets") if isinstance(daten, dict) else None
    return [
        (int(n), str(e.get("title", "")))
        for n, e in sorted((tickets or {}).items(), key=lambda p: manifest.ticket_schluessel(p[0]))
        if n.isdigit() and isinstance(e, dict) and "gelb_von" not in e
    ]


def spec_schliessen(repo: Path, gh_repo: str, spec: int, abruf: Abruf | None = None) -> Ergebnis:
    """Spec-Issue schließen, wenn :func:`spec_offen` nichts meldet; sonst Abbruch mit Liste."""
    hindernisse = spec_offen(repo, spec, abruf=abruf)
    if hindernisse:
        return Ergebnis(
            ok=False,
            zeilen=[
                (
                    f"Spec #{spec} bleibt offen — {len(hindernisse)} Hindernis(se), "
                    "Folge-Tickets erst bauen, schließen oder ins Sammel-Issue legen "
                    f"(Label `{LABEL_VERSCHOBEN}`):"
                ),
                *(f"  {z}" for z in hindernisse),
            ],
        )
    text = f"Spec geschlossen über `to_spawn.py spec-schliessen {spec}` — alle Folge-Tickets erledigt."
    if kommentiere(spec, gh_repo, text, befehl="close"):
        return Ergebnis(ok=True, zeilen=[f"Spec #{spec} geschlossen"])
    return Ergebnis(ok=False, zeilen=[f"FEHLER: Spec #{spec} nicht geschlossen (gh issue close)"])
