"""Spec-Rand (E2-2, App #1394 „Ticket nachtragen“): ein Issue an eine Spec hängen.

Eine Tür, :func:`anhaengen`, erledigt alle Handgriffe, die ein Issue zum Ticket
einer Spec machen — zusammen und wiederholbar (ein zweiter Aufruf ändert nichts):

1. native Blocker-Kanten ``blocked_by`` aus ``blockiert_von``,
2. Kante vor das offene Abnahme-Ticket (``checkpoint:human``) seiner eigenen
   Kette — nie blind vor fremde Abnahmen (#598); bei mehrdeutiger Zuordnung
   (mehrere Abnahmen ohne Blocker oder Blocker aus mehreren Ketten) Abbruch mit
   Hinweis auf ``--abnahme``,
3. Manifest-Eintrag (``schaetzung_k``, ``umfang``, ``files``, ``title``),
4. Unter-Issue der Spec — zuletzt: erst damit sieht der Aufpasser das Ticket
   (``offene_tickets``) und startet es; bis dahin steht es fertig verkantet da.

Erst wird alles gelesen und geplant. Verletzt eine Vorbedingung (Spec geschlossen,
Issue nicht in diesem Repo, Eintrag verletzt Pflichtfeld oder Grenze
``staffel.grenze_k`` laut :func:`manifest.pruefe_eintrag`, Abnahme-Ticket als
Blocker, mehrdeutige Abnahme-Zuordnung), wird nichts geschrieben — ein zu großes
oder falsch verkantetes Ticket wird so nie startbar.
Nach dem Schreiben prüft :func:`manifest.pruefe` die ganze Spec; deren Weigerung
(Exit 3) betrifft dann nur noch andere Tickets oder die GitHub-Kette.
Das GitHub-Repo kommt aus ``origin`` (:func:`gh.repo_aus_origin`), nie fest.
Gestartet wird hier nichts — das bleibt beim Aufpasser.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gh, manifest, ticket_zustand

log = logging.getLogger("to_spawn.spec_rand")

#: Exit-Code bei verletzter Vorbedingung oder gescheitertem Schritt.
EXIT_FEHLER = 1
#: Schlüsselwort für ``--abnahme``, wenn ein Ticket bewusst noch keiner Abnahme gehört.
ABNAHME_KEINE = "keine"


@dataclass
class Ergebnis:
    exit: int
    zeilen: list[str] = field(default_factory=list)


@dataclass
class _Schritt:
    text: str
    ausfuehren: Callable[[], bool]


class _Abbruch(Exception):
    """Vorbedingung verletzt — nichts geschrieben."""


def anhaengen(
    repo: Path,
    spec: int | str,
    ticket: int | str,
    konfig: dict[str, Any],
    *,
    blockiert_von: Sequence[int | str] = (),
    abnahme: Sequence[int | str] = (),
    schaetzung_k: float | None = None,
    umfang: str | None = None,
    dateien: Sequence[str] = (),
    trocken: bool = False,
    pruefen: bool = True,
) -> Ergebnis:
    """Issue ``ticket`` an Spec ``spec`` hängen (Kanten, Manifest, Unter-Issue).

    ``pruefen=False`` lässt die Manifest-Prüfung am Ende aus — für Aufrufer, die sie
    selbst einmal für die ganze Spec machen (``einrichtungs_ticket``, sonst N× ``gh``).

    ``abnahme`` legt explizit fest, welche offenen Abnahme-Tickets auf ``ticket``
    warten (oder ``("keine",)`` für den Start einer neuen Kette). Ohne Angabe ordnet
    :func:`_abnahme_ziele` das Ticket über seine Blocker (oder ``gelb_von``) seiner
    Abnahme-Kette zu und bricht bei Mehrdeutigkeit ab (#598).

    ``schaetzung_k``/``umfang`` sind Pflicht, solange das Ticket nicht im Manifest
    steht; sonst überschreiben sie nur, was angegeben ist. ``trocken`` zeigt die
    Schritte ohne Schreibzugriff. Exit 0 = angehängt und Prüfung sauber,
    1 = nichts bzw. nicht alles geschrieben, 3 = angehängt, Prüfung weigert sich.
    """
    s, n = _nummer(spec), _nummer(ticket)
    try:
        schritte = _planen(
            repo,
            s,
            n,
            konfig,
            [_nummer(b) for b in blockiert_von],
            [_nummer(a) for a in abnahme],
            schaetzung_k,
            umfang,
            dateien,
        )
    except _Abbruch as fehler:
        return Ergebnis(EXIT_FEHLER, [f"FEHLER: {fehler} — nichts geschrieben."])
    if not schritte:
        zeilen = [f"#{n} hängt schon vollständig an Spec #{s} — nichts zu tun."]
    elif trocken:
        return Ergebnis(
            0,
            [f"[trocken] #{n} an Spec #{s} — würde:"]
            + [f"  {x.text}" for x in schritte],
        )
    else:
        zeilen = []
        for schritt in schritte:
            if not schritt.ausfuehren():
                zeilen.append(
                    f"FEHLER: {schritt.text} gescheitert — erneut aufrufen, Erledigtes wird übersprungen."
                )
                return Ergebnis(EXIT_FEHLER, zeilen)
            zeilen.append(f"ok  {schritt.text}")
    if not pruefen:
        return Ergebnis(0, zeilen)
    bericht = manifest.pruefe(repo, s, konfig)
    zeilen.extend(bericht.text().splitlines())
    return Ergebnis(0 if bericht.sauber else manifest.EXIT_WEIGERUNG, zeilen)


def _nummer(wert: int | str) -> str:
    return str(wert).strip().lstrip("#").strip()


def _planen(
    repo: Path,
    s: str,
    n: str,
    konfig: dict[str, Any],
    blocker: list[str],
    abnahmen: list[str],
    schaetzung_k: float | None,
    umfang: str | None,
    dateien: Sequence[str],
) -> list[_Schritt]:
    """Alles lesen, Vorbedingungen prüfen, fehlende Schritte in Reihenfolge liefern."""
    if gh.gh_befehl() is None:
        raise _Abbruch("gh fehlt — GitHub-CLI installieren, `gh auth status` prüfen")
    slug = gh.repo_aus_origin(repo)
    if not slug:
        raise _Abbruch(
            "kein GitHub-Repo erkannt (origin fehlt oder zeigt nicht auf github.com)"
        )
    if n == s or n in blocker:
        raise _Abbruch(f"#{n} kann nicht an sich selbst hängen")
    label = manifest.checkpoint_label(konfig)

    issues_cache: dict[str, dict[str, Any]] = {}

    def hole_issue(nummer: str) -> dict[str, Any]:
        if nummer not in issues_cache:
            issues_cache[nummer] = _issue(repo, slug, nummer)
        return issues_cache[nummer]

    spec_daten = hole_issue(s)
    if not _offen(spec_daten):
        raise _Abbruch(
            f"Spec #{s} ist geschlossen — kein Nachtrag an eine fertige Spec"
        )
    ticket_daten = hole_issue(n)

    datei = manifest.manifest_pfad(repo, s)
    try:
        daten, _ = manifest.lade_manifest_mit_doppelten(repo, s)
    except FileNotFoundError:
        daten = {"spec": int(s) if s.isdigit() else s, "tickets": {}}
    except (OSError, ValueError) as fehler:
        raise _Abbruch(f"Manifest {datei.name} unlesbar ({fehler})") from fehler
    neu = _eintrag(
        daten["tickets"].get(n), ticket_daten, n, schaetzung_k, umfang, dateien
    )
    if fehler_eintrag := manifest.pruefe_eintrag(n, neu, konfig):
        raise _Abbruch("; ".join(fehler_eintrag))

    kanten_cache: dict[str, set[str]] = {}

    def hole_kanten(nummer: str) -> set[str]:
        if nummer not in kanten_cache:
            kanten_cache[nummer] = _kanten(repo, slug, nummer)
        return kanten_cache[nummer]

    schritte: list[_Schritt] = []
    native = hole_kanten(n)
    for b in blocker:
        b_daten = hole_issue(b)
        if label in _labels(b_daten):
            raise _Abbruch(
                f"#{b} ist Abnahme-Ticket ({label}) — es gehört ans Kettenende, nicht davor"
            )
        if b not in native:
            schritte.append(_kante(repo, slug, n, b, b_daten["id"]))

    # Offenes Einrichtungs-Ticket der Spec (#410): jedes andere offene Ticket wartet darauf.
    t0 = manifest.einrichtungs_ticket(daten)
    if (
        t0
        and t0 != n
        and t0 not in blocker
        and t0 not in native
        and _offen(ticket_daten)
    ):
        t0_daten = hole_issue(t0)
        if _offen(t0_daten):
            schritte.append(_kante(repo, slug, n, t0, t0_daten["id"]))

    for abn in _abnahme_ziele(
        s=s,
        n=n,
        label=label,
        ticket_daten=ticket_daten,
        neu=neu,
        daten=daten,
        blocker=blocker,
        native=native,
        abnahmen=abnahmen,
        t0=t0,
        hole_issue=hole_issue,
        hole_kanten=hole_kanten,
    ):
        schritte.append(
            _kante(repo, slug, abn, n, ticket_daten["id"], abnahme=True)
        )

    if daten["tickets"].get(n) != neu:

        def manifest_schreiben() -> bool:
            daten["tickets"][n] = neu
            datei.parent.mkdir(parents=True, exist_ok=True)
            try:
                manifest.schreibe_atomar(datei, daten)
            except OSError as fehler:
                log.error("Manifest %s nicht geschrieben (#%s): %s", datei, n, fehler)
                return False
            return True

        felder = ", ".join(f"{k}={v}" for k, v in neu.items() if k != "title")
        schritte.append(
            _Schritt(
                f"Manifest {datei.name}: #{n} eintragen ({felder})", manifest_schreiben
            )
        )

    code, ausgabe = gh.lauf(
        [
            "api",
            f"repos/{slug}/issues/{s}/sub_issues",
            "--paginate",
            "--jq",
            ".[].number",
        ],
        cwd=repo,
    )
    if code != 0:
        raise _Abbruch(f"Unter-Issues von Spec #{s} nicht lesbar (gh-Code {code})")
    if n not in ausgabe.split():
        pfad = f"repos/{slug}/issues/{s}/sub_issues"
        schritte.append(
            _Schritt(
                f"Unter-Issue: #{n} unter Spec #{s}",
                lambda: _post(repo, pfad, "sub_issue_id", ticket_daten["id"]),
            )
        )
    return schritte


def _abnahme_ziele(
    *,
    s: str,
    n: str,
    label: str,
    ticket_daten: dict[str, Any],
    neu: dict[str, Any],
    daten: dict[str, Any],
    blocker: list[str],
    native: set[str],
    abnahmen: list[str],
    t0: str | None,
    hole_issue: Callable[[str], dict[str, Any]],
    hole_kanten: Callable[[str], set[str]],
) -> list[str]:
    """Offene Abnahme-Tickets bestimmen, die neu auf ``n`` warten sollen (#598).

    Regel:
    * Abnahme-Tickets selbst bekommen nie automatische Kanten vor andere Abnahmen.
    * Explizites ``--abnahme`` (Nummern oder ``keine``) hat Vorrang und wird geprüft.
    * Ein Einrichtungs-Ticket (``einrichtung: true``) steht vor allen offenen Abnahmen.
    * Sonst zählt allein die Kette: Vorgänger von ``n`` (Blocker + ``gelb_von``, ohne T0)
      gegen die transitiven Vorgänger jeder offenen Abnahme (ohne T0). Passt genau eine
      Abnahme, bekommt nur sie die Kante. Berührt ``n`` mehrere Ketten oder fehlt bei
      mehreren offenen Abnahmen jeder Blocker-Anker, bricht ``anhaengen`` klar ab.
    """
    ist_abnahme = label in _labels(ticket_daten)
    if ist_abnahme:
        if abnahmen:
            raise _Abbruch(
                f"#{n} ist selbst Abnahme-Ticket ({label}) — --abnahme nicht angeben"
            )
        return []

    tickets: dict[str, Any] = daten["tickets"]
    if abnahmen:
        eindeutige = list(dict.fromkeys(a for a in abnahmen if a))
        if any(a.lower() == ABNAHME_KEINE for a in eindeutige):
            if len(eindeutige) > 1:
                raise _Abbruch(
                    f"--abnahme {ABNAHME_KEINE} nicht mit Ticket-Nummern mischen"
                )
            return []
        ziele: list[str] = []
        for a in eindeutige:
            if a == n:
                raise _Abbruch(f"#{n} kann nicht seine eigene Abnahme sein")
            if a not in tickets:
                raise _Abbruch(
                    f"Abnahme-Ticket #{a} steht nicht im Manifest spec-{s}.json"
                )
            a_daten = hole_issue(a)
            if label not in _labels(a_daten):
                raise _Abbruch(f"#{a} ist kein Abnahme-Ticket ({label})")
            if not _offen(a_daten):
                raise _Abbruch(f"Abnahme-Ticket #{a} ist schon geschlossen")
            if n not in hole_kanten(a):
                ziele.append(a)
        return ziele

    offene_abnahmen = [
        t
        for t in sorted(tickets, key=manifest.ticket_schluessel)
        if t != n and _offen(hole_issue(t)) and label in _labels(hole_issue(t))
    ]
    if not offene_abnahmen:
        return []

    if neu.get("einrichtung") is True:
        return [cp for cp in offene_abnahmen if n not in hole_kanten(cp)]

    spec_tickets = set(tickets) | {n}
    n_kanten = native | set(blocker)

    def _vorgaenger(start: Iterable[str]) -> set[str]:
        besucht: set[str] = set()
        stapel = [x for x in start if x != t0 and x in spec_tickets]
        while stapel:
            aktuell = stapel.pop()
            if aktuell in besucht:
                continue
            besucht.add(aktuell)
            nachbarn = n_kanten if aktuell == n else hole_kanten(aktuell)
            for b in nachbarn:
                if b != t0 and b in spec_tickets and b not in besucht:
                    stapel.append(b)
        return besucht

    cp_kette = {cp: _vorgaenger(hole_kanten(cp)) for cp in offene_abnahmen}

    n_direkt = {b for b in n_kanten if b != t0}
    if neu.get("gelb_von") is not None:
        gv = _nummer(neu["gelb_von"])
        if gv and gv != n and gv != t0:
            n_direkt.add(gv)
    anker = _vorgaenger(n_direkt)

    abgedeckt = [cp for cp in offene_abnahmen if n in cp_kette[cp]]
    passend = [
        cp
        for cp in offene_abnahmen
        if n not in cp_kette[cp] and bool(anker & cp_kette[cp])
    ]
    beruehrt = sorted(set(abgedeckt) | set(passend), key=manifest.ticket_schluessel)
    if len(passend) > 1 or (passend and len(beruehrt) > 1):
        liste = ", ".join(f"#{c}" for c in beruehrt)
        raise _Abbruch(
            f"#{n} berührt mehrere Abnahme-Ketten ({liste}) — mit --abnahme festlegen"
        )
    if len(passend) == 1:
        cp = passend[0]
        return [cp] if n not in hole_kanten(cp) else []
    if abgedeckt:
        return []
    if not anker and len(offene_abnahmen) > 1:
        liste = ", ".join(f"#{c}" for c in offene_abnahmen)
        raise _Abbruch(
            f"Spec #{s} hat mehrere offene Abnahmen ({liste}), #{n} lässt sich ohne "
            f"Blocker keiner Kette zuordnen — --blockiert-von, --abnahme <Nr> oder "
            f"--abnahme {ABNAHME_KEINE} angeben"
        )
    log.info(
        "#%s ohne Blocker in einer bestehenden Abnahme-Kette — keine automatische Abnahme-Kante.",
        n,
    )
    return []


def _eintrag(
    alt: Any,
    ticket_daten: dict[str, Any],
    n: str,
    schaetzung_k: float | None,
    umfang: str | None,
    dateien: Sequence[str],
) -> dict[str, Any]:
    """Manifest-Eintrag nach dem Aufruf: Bestand + angegebene Felder."""
    eintrag: dict[str, Any] = dict(alt) if isinstance(alt, dict) else {}
    eintrag.setdefault("title", str(ticket_daten.get("title") or ""))
    if schaetzung_k is not None:
        if schaetzung_k <= 0:
            raise _Abbruch("--schaetzung muss über 0 liegen (Tausend Token)")
        eintrag["schaetzung_k"] = (
            int(schaetzung_k) if float(schaetzung_k).is_integer() else schaetzung_k
        )
    if umfang is not None and umfang.strip():
        eintrag["umfang"] = umfang.strip()
    if dateien:
        eintrag["files"] = [d.strip() for d in dateien if d.strip()]
    if "schaetzung_k" not in eintrag or "umfang" not in eintrag:
        raise _Abbruch(
            f"#{n} steht noch nicht im Manifest — --schaetzung und --umfang angeben"
        )
    return eintrag


def _issue(repo: Path, slug: str, nummer: str) -> dict[str, Any]:
    """Issue über die REST-API; Abbruch, wenn es fehlt oder in einem anderen Repo liegt.

    Ein umgezogenes Issue leitet GitHub still um — die Antwort trägt dann das fremde
    ``repository_url``. Fehlt das Feld, gilt das Repo als unbekannt (fail-closed).
    """
    daten = gh.json_lauf(["api", f"repos/{slug}/issues/{nummer}"], cwd=repo)
    if not isinstance(daten, dict) or not isinstance(daten.get("id"), int):
        raise _Abbruch(
            f"#{nummer} in {slug} nicht lesbar (fehlt, kein Zugriff oder gh-Fehler)"
        )
    heimat = str(daten.get("repository_url") or "").rstrip("/")
    if not heimat.lower().endswith(f"/repos/{slug.lower()}"):
        fremd = heimat.rsplit("/repos/", 1)[-1] or "unbekannt"
        raise _Abbruch(
            f"#{nummer} liegt nicht in {slug}, sondern in {fremd} — falsches Repo"
        )
    if "pull_request" in daten:
        raise _Abbruch(f"#{nummer} ist ein Pull-Request, kein Issue")
    return daten


def _offen(daten: dict[str, Any]) -> bool:
    """Noch nicht endgültig zu (#279): Staging-Label allein schließt weder Spec noch Abnahme."""
    return not ticket_zustand.endgueltig_zu(daten)


def _labels(daten: dict[str, Any]) -> set[str]:
    return {
        str(x.get("name")) for x in daten.get("labels") or [] if isinstance(x, dict)
    }


def _kanten(repo: Path, slug: str, nummer: str) -> set[str]:
    kanten = gh.blocked_by(slug, nummer, cwd=repo)
    if kanten is None:
        raise _Abbruch(f"Blocker-Kanten von #{nummer} nicht lesbar")
    return {str(k.get("number")) for k in kanten if isinstance(k, dict)}


def _kante(
    repo: Path, slug: str, wartet: str, auf: str, auf_id: int, *, abnahme: bool = False
) -> _Schritt:
    art = "Abnahme-Kante" if abnahme else "Kante"
    pfad = f"repos/{slug}/issues/{wartet}/dependencies/blocked_by"
    return _Schritt(
        f"{art}: #{wartet} blockiert von #{auf}",
        lambda: _post(repo, pfad, "issue_id", auf_id),
    )


def _post(repo: Path, pfad: str, feld: str, wert: int) -> bool:
    code, ausgabe = gh.lauf(
        ["api", "-X", "POST", pfad, "-F", f"{feld}={wert}"], cwd=repo
    )
    if code != 0:
        log.warning(
            "gh api POST %s gescheitert (Code %s): %s", pfad, code, ausgabe[:200]
        )
    return code == 0
