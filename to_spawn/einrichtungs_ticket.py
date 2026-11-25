"""Einrichtungs-Ticket (T0, #410): fehlt dem Repo Einrichtung, steht vor T1 ein Ticket dafür.

Zwei Türen:

* :func:`luecken` ist die einzige Stelle des Wissens „was fehlt diesem Repo zur
  Einrichtung?“ — Prüf-Werkzeug, Staging, Befehle für schnelle Prüfung, Komplett-Prüfung
  und Live-Schritt (#425). Ein Repo, das bewusst ohne Staging (``staging.ohne: true``)
  oder ohne Live-Schritt (``live.ohne: true``, #452) arbeitet, hat dort keine Lücke.
* :func:`sicherstellen` trägt bei Lücke das Einrichtungs-Ticket ins Manifest der Spec
  ein (``einrichtung: true``) und hängt jedes Ticket der Spec über
  :func:`spec_rand.anhaengen` dahinter — auch spätere Nachträge. ``regularien`` ruft es
  vor der Manifest-Prüfung.

Das Einrichtungs-Ticket gilt fürs Repo, nicht je Spec: Ein offenes Issue mit Label
``einrichtung`` wird wiederverwendet (zweite Spec, Abbruch zwischen Anlegen und
Manifest-Eintrag). Unter-Issue wird es nur bei der Spec, für die es angelegt wurde
(GitHub erlaubt eine Eltern-Spec).

Nur ein Hinweis, nichts angelegt, nichts umgehängt: ohne GitHub (``--ohne-github``,
``origin`` nicht auf github.com), im Probelauf (``trocken``, auch ``--nur-wache``), bei
Ticket-Auswahl (``--tickets``) und wenn die Spec schon läuft (ein Ticket zu oder mit
Bau-Log). Scheitert das Anlegen, ist ``ok`` falsch — ``regularien`` weigert sich dann.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import bau_log, config, gh, manifest, pruef, spec_rand, ticket_zustand

log = logging.getLogger("to_spawn.einrichtungs_ticket")

#: Schätzung des Einrichtungs-Tickets (Tausend Token).
SCHAETZUNG_K = 60
_ISSUE_URL = re.compile(r"/issues/(\d+)\s*$")
#: Label, an dem das offene Einrichtungs-Ticket des Repos erkannt wird.
LABEL = "einrichtung"

#: Je Lücke: Kurzname → was das Einrichtungs-Ticket dafür baut.
_AUFTRAG = {
    "pruefung": (
        "Prüfung anlegen: Linter + Tests der geänderten Dateien + kleiner Rauchsatz, "
        "so dass `to_spawn.py pruefen <basis> <ziel>` das Repo erkennt (Python: "
        "pyproject.toml/pytest, Node: package.json mit scripts.test)."
    ),
    "staging": (
        "Staging anlegen: `staging_start` und `staging.url` in `.to-spawn/config.json` "
        "eintragen — oder, wenn das Repo bewusst ohne Staging arbeitet, `staging.ohne: true`."
    ),
    "pruefung.schnell": (
        "Schnelle Prüfung je Ticket eintragen: in `.to-spawn/config.json` unter "
        '`"pruefung"` den Schlüssel `"schnell"` mit einem Befehl als Text (Stil, '
        "betroffene Tests, kleiner Rauchtest; 1–3 min). Beispiel: "
        '`"pruefung": {"schnell": "ruff check . && pytest -q tests/rauch"}`.'
    ),
    "pruefung.komplett": (
        "Komplett-Prüfung einmal je Spec eintragen: in `.to-spawn/config.json` unter "
        '`"pruefung"` den Schlüssel `"komplett"` mit einem Befehl als Text (alle Tests). '
        'Beispiel: `"pruefung": {"komplett": "pytest -q -n 8 tests/"}`.'
    ),
    "live.befehl": (
        'Live-Schritt eintragen: in `.to-spawn/config.json` unter `"live"` den '
        'Schlüssel `"befehl"` mit dem Befehl als Text, der den geprüften Stand live '
        'bringt. Wahlweise `"url"` (Adresse für den Live-Beweis) und `"aufraeumen"` '
        "(Befehl, der danach Reste wegräumt). Beispiel: "
        '`"live": {"befehl": "bash scripts/deploy.sh", "url": "https://app.example"}` '
        "— oder, wenn das Repo bewusst ohne Live-Schritt arbeitet, `live.ohne: true`."
    ),
}

#: Pflicht-Schlüssel der Gruppen ``pruefung``/``live`` (#425, #452): Kurzname → Lesart aus ``config``.
_PFLICHT = {
    "pruefung.schnell": lambda k: config.pruef_befehl(k, "schnell"),
    "pruefung.komplett": lambda k: config.pruef_befehl(k, "komplett"),
    "live.befehl": lambda k: bool(config.live_schritt(k)["befehl"]) or config.live_bewusst_ohne(k),
}


@dataclass
class Ergebnis:
    """``ok`` falsch = Lücke, aber kein Einrichtungs-Ticket steht (Zeilen sagen, was fehlt)."""

    ok: bool
    zeilen: list[str] = field(default_factory=list)


def luecken(repo: Path, konfig: dict[str, Any]) -> list[str]:
    """Was dem Repo zur Einrichtung fehlt (Kurznamen aus ``_AUFTRAG``); leer = nichts.

    ``pruefung`` = kein Prüf-Werkzeug erkannt; ``pruefung.schnell``/``pruefung.komplett``/
    ``live.befehl`` = Befehl fehlt in der Konfig (#425; bei ``live.ohne: true`` entfällt
    ``live.befehl``, #452). Ob ein Befehl eingerichtet ist, sagt allein
    :mod:`to_spawn.config`. ``live.url``/``live.aufraeumen`` sind keine Pflicht.
    """
    fehlt: list[str] = []
    if not pruef.hat_werkzeug(repo):
        fehlt.append("pruefung")
    if not config.hat_staging(konfig) and not config.staging_bewusst_ohne(konfig):
        fehlt.append("staging")
    fehlt.extend(name for name, lies in _PFLICHT.items() if not lies(konfig))
    return fehlt


def sicherstellen(
    repo: Path,
    spec: int | str,
    konfig: dict[str, Any],
    *,
    mit_github: bool = True,
    trocken: bool = False,
    auswahl: bool = False,
) -> Ergebnis:
    """Einrichtungs-Ticket der Spec sicherstellen, falls dem Repo etwas fehlt.

    ``trocken`` = Probelauf, ``auswahl`` = Teil-Start (nur gewählte Tickets per
    ``--tickets`` oder kein Ticket-Fenster per ``--nur-wache``): dann nur Hinweis. ``ok`` falsch nur, wenn es angelegt bzw.
    angehängt werden sollte und das scheiterte oder der Ticket-Stand unklar ist
    (letzte Zeile ``EINRICHTUNG FEHLT``).
    """
    fehlt = luecken(repo, konfig)
    if not fehlt:
        return Ergebnis(True)
    s = str(spec).lstrip("#")
    kopf = f"Einrichtung fehlt: {', '.join(fehlt)}"
    auftrag = " ".join(_AUFTRAG[x] for x in fehlt)
    try:
        daten, doppelt = manifest.lade_manifest_mit_doppelten(repo, s)
    except (OSError, ValueError) as fehler:
        # Fehlt/kaputt: das meldet die Manifest-Prüfung gleich selbst.
        return Ergebnis(True, [f"{kopf} — Manifest nicht lesbar ({fehler})"])
    if doppelt or not s.isdigit():
        # Doppelte Schlüssel: Schreiben löschte den Befund der Manifest-Prüfung.
        # Spec ohne Issue-Nummer: kein Unter-Issue möglich. Beides nur melden.
        grund = "Manifest mit doppelten Nummern" if doppelt else "Spec ohne Issue-Nummer"
        return Ergebnis(True, [f"{kopf} — {grund}: Einrichtungs-Ticket nicht angelegt."])
    vorhanden = manifest.einrichtungs_ticket(daten)
    slug = gh.repo_aus_origin(repo) if mit_github else ""
    grund = (
        "[trocken]"
        if trocken
        else "Teil-Start (--tickets/--nur-wache)"
        if auswahl
        else "ohne GitHub"
        if not slug
        else ""
    )
    if grund:
        wie = (
            f"Einrichtungs-Ticket #{vorhanden}"
            if vorhanden
            else "würde Einrichtungs-Ticket anlegen"
            if trocken or not slug
            else "Einrichtungs-Ticket nicht angelegt, erst beim Start der ganzen Spec"
        )
        return Ergebnis(True, [f"{kopf} — {grund}: {wie}. {auftrag}"])
    if vorhanden is None:
        laeuft, unklar = _laeuft_schon(repo, slug, daten)
        if unklar:
            # Nicht still weiter: sonst startet der Bau ohne T0, danach gilt „hat Bau-Log“.
            return Ergebnis(
                False,
                [
                    (
                        f"EINRICHTUNG FEHLT ({', '.join(fehlt)}): {unklar} — "
                        "Einrichtungs-Ticket nicht angelegt, erneut aufrufen"
                    )
                ],
            )
        if laeuft:
            return Ergebnis(
                True,
                [
                    (
                        f"{kopf} — Spec #{s} läuft schon ({laeuft}): "
                        f"Einrichtungs-Ticket nicht angelegt, nichts umgehängt. Lücke von Hand schließen: {auftrag}"
                    )
                ],
            )
        neu = _einrichtungs_ticket_holen(repo, slug, s, daten, fehlt)
        if isinstance(neu, Ergebnis):
            return neu
        vorhanden, titel, zeile = neu
        zeilen = [f"{kopf} — {zeile}"]
    else:
        stand = gh.json_lauf(["api", f"repos/{slug}/issues/{vorhanden}"], cwd=repo)
        if isinstance(stand, dict) and ticket_zustand.endgueltig_zu(stand):
            return Ergebnis(
                True,
                [f"{kopf} — Einrichtungs-Ticket #{vorhanden} ist zu, Lücke bleibt: von Hand prüfen"],
            )
        titel = str(stand.get("title") or "") if isinstance(stand, dict) else ""
        zeilen = [f"{kopf} — Einrichtungs-Ticket #{vorhanden} steht"]
        if not isinstance(stand, dict):
            # Zuordnung zur Spec unbekannt: Unter-Issue entfällt, Kanten hängen trotzdem.
            warnung = (
                f"WARNUNG: Einrichtungs-Ticket #{vorhanden} nicht lesbar (gh api) — "
                f"nicht als Unter-Issue an Spec #{s} gehängt; erneut aufrufen"
            )
            log.warning(warnung)
            zeilen.append(warnung)
    # Unter-Issue nur bei der Spec, für die es angelegt wurde (eine Eltern-Spec je Issue).
    eigen = _titel_fuer(s) in titel
    # Manifest-Prüfung macht ``regularien`` danach einmal für alle (pruefen=False).
    # Einrichtungs-Ticket zuerst (Unter-Issue, Kante vor die Abnahme), dann jedes andere — idempotent.
    andere = sorted((x for x in daten["tickets"] if x != vorhanden), key=manifest.ticket_schluessel)
    for t in [vorhanden, *andere] if eigen else andere:
        lauf = spec_rand.anhaengen(repo, s, t, konfig, pruefen=False)
        if lauf.exit == spec_rand.EXIT_FEHLER:
            return Ergebnis(
                False,
                [
                    *zeilen,
                    *lauf.zeilen,
                    f"EINRICHTUNG FEHLT: #{t} nicht hinter Einrichtungs-Ticket #{vorhanden} — erneut aufrufen",
                ],
            )
    return Ergebnis(True, [*zeilen, f"alle Tickets der Spec #{s} warten auf #{vorhanden}"])


def _titel_fuer(s: str) -> str:
    return f"Einrichtung für Spec #{s}:"


def _laeuft_schon(repo: Path, slug: str, daten: dict[str, Any]) -> tuple[str, str]:
    """``(läuft, unklar)``: Grund, nichts anzulegen; beide leer = frei.

    ``läuft`` = sicher erkannt (``#N hat Bau-Log`` lokal oder auf ``origin``,
    ``#N ist zu`` oder ``#N ist fertig gebaut``), nur Hinweis. Unversionierte
    Laufdateien auf einem anderen Rechner liegen außerhalb von Git und sind hier
    nicht sichtbar (#438).
    ``unklar`` = ``gh api`` scheitert für ein Ticket: nichts anlegen, aber weigern (Exit 3).
    """
    tickets = sorted(daten["tickets"], key=manifest.ticket_schluessel)
    haupt = gh.hauptzweig(repo)
    for t in tickets:
        # Lokal (Worktree vor Hauptbaum) sowie versioniertes Bau-Log auf origin (#438).
        if bau_log.log_ort_fuer(t, repo) is not None or bau_log.hat_log_auf_origin(repo, t, hauptzweig=haupt):
            return f"#{t} hat Bau-Log", ""
    for t in tickets:
        stand = gh.json_lauf(["api", f"repos/{slug}/issues/{t}"], cwd=repo)
        if not isinstance(stand, dict):
            return "", f"Stand unklar: #{t} nicht lesbar (gh api)"
        if ticket_zustand.endgueltig_zu(stand):
            return f"#{t} ist zu", ""
        if ticket_zustand.fertig_gebaut(stand):
            return f"#{t} ist fertig gebaut", ""
    return "", ""


def _einrichtungs_ticket_holen(
    repo: Path, slug: str, s: str, daten: dict[str, Any], fehlt: list[str]
) -> tuple[str, str, str] | Ergebnis:
    """Offenes Einrichtungs-Ticket des Repos nehmen oder anlegen, dann ins Manifest.

    Rückgabe (Nummer, Titel, Meldung) oder Fehler-Ergebnis. Erst suchen, dann anlegen:
    ein Abbruch zwischen ``gh issue create`` und Manifest-Eintrag findet beim nächsten
    Lauf dasselbe Issue wieder (Label ``einrichtung`` hängt schon beim Anlegen dran).
    """
    fehlt_text = ", ".join(fehlt)
    offen = gh.json_lauf(
        [
            "issue",
            "list",
            "--repo",
            slug,
            "--label",
            LABEL,
            "--state",
            "open",
            "--json",
            "number,title",
        ],
        cwd=repo,
    )
    if not isinstance(offen, list):
        log.warning("gh issue list --label %s gescheitert", LABEL)
        return Ergebnis(
            False,
            [
                (
                    f"EINRICHTUNG FEHLT ({fehlt_text}): offene Einrichtungs-Tickets nicht lesbar "
                    f"(gh issue list --label {LABEL}) — erneut aufrufen"
                )
            ],
        )
    treffer = sorted(
        (x for x in offen if isinstance(x, dict) and x.get("number")),
        key=lambda x: int(x["number"]),
    )
    if treffer:
        neu, titel = str(treffer[0]["number"]), str(treffer[0].get("title") or "")
        zeile = f"offenes Einrichtungs-Ticket #{neu} des Repos übernommen"
    else:
        angelegt = _anlegen(repo, slug, s, fehlt)
        if isinstance(angelegt, Ergebnis):
            return angelegt
        neu, titel = angelegt
        zeile = f"Einrichtungs-Ticket #{neu} angelegt"
    daten["tickets"][neu] = {
        "title": titel,
        "schaetzung_k": SCHAETZUNG_K,
        "umfang": " ".join(_AUFTRAG[x] for x in fehlt),
        "einrichtung": True,
    }
    datei = manifest.manifest_pfad(repo, s)
    try:
        manifest.schreibe_atomar(datei, daten)
    except OSError as fehler:
        log.error(
            "Manifest %s nicht geschrieben (Einrichtungs-Ticket #%s): %s",
            datei,
            neu,
            fehler,
        )
        return Ergebnis(
            False,
            [
                (
                    f"EINRICHTUNG FEHLT: Einrichtungs-Ticket #{neu} steht, Manifest nicht geschrieben "
                    f"({datei.name}) — erneut aufrufen"
                )
            ],
        )
    return neu, titel, zeile


def _anlegen(repo: Path, slug: str, s: str, fehlt: list[str]) -> tuple[str, str] | Ergebnis:
    """Issue mit Label ``einrichtung`` anlegen; Rückgabe (Nummer, Titel) oder Fehler-Ergebnis."""
    titel = f"{_titel_fuer(s)} {', '.join(fehlt)}"
    auftrag = "\n".join(f"- {_AUFTRAG[x]}" for x in fehlt)
    body = (
        f"Teil von Spec #{s}. Einrichtungs-Ticket (T0): Dem Repo fehlt Einrichtung, "
        f"alle anderen Tickets der Spec warten darauf. Es gilt fürs ganze Repo: "
        f"weitere Specs warten auf dasselbe Ticket, solange es offen ist.\n\n{auftrag}\n\n"
        f"Steht im Manifest spec-{s}.json mit `einrichtung: true`."
    )
    gh.label_sicherstellen(
        repo,
        LABEL,
        farbe="0e8a16",
        beschreibung="Einrichtungs-Ticket des Repos: Prüfung/Staging anlegen (to-spawn #410)",
    )
    code, ausgabe = gh.lauf(
        [
            "issue",
            "create",
            "--repo",
            slug,
            "--title",
            titel,
            "--body",
            body,
            "--label",
            LABEL,
        ],
        cwd=repo,
    )
    treffer = _ISSUE_URL.search(ausgabe or "")
    if code != 0 or not treffer:
        log.warning("gh issue create gescheitert (Code %s): %s", code, (ausgabe or "")[:200])
        return Ergebnis(
            False,
            [
                (
                    f"EINRICHTUNG FEHLT ({', '.join(fehlt)}): Einrichtungs-Ticket nicht angelegt "
                    f"(gh issue create, Code {code}) — erneut aufrufen"
                )
            ],
        )
    return treffer.group(1), titel
