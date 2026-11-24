"""Einrichtungs-Ticket (T0, #410): fehlt dem Repo Einrichtung, steht vor T1 ein Ticket dafür.

Zwei Türen:

* :func:`luecken` ist die einzige Stelle des Wissens „was fehlt diesem Repo zur
  Einrichtung?“ — heute Prüfung und Staging. Ein Repo, das bewusst ohne Staging
  arbeitet (``staging.ohne: true``), hat dort keine Lücke.
* :func:`sicherstellen` legt bei Lücke das Einrichtungs-Ticket der Spec an (einmal:
  Manifest-Eintrag mit ``einrichtung: true``) und hängt es über
  :func:`spec_rand.anhaengen` an die Spec. ``anhaengen`` setzt die Kante „wartet auf
  das offene Einrichtungs-Ticket“ für jedes Ticket der Spec — auch für spätere
  Nachträge. ``regularien`` ruft :func:`sicherstellen` vor der Manifest-Prüfung.

Ohne GitHub (``--ohne-github``, ``origin`` nicht auf github.com) bleibt es beim Hinweis.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import gh, manifest, pruef, spec_rand, ticket_zustand

log = logging.getLogger("to_spawn.einrichtungs_ticket")

#: Schätzung des Einrichtungs-Tickets (Tausend Token).
SCHAETZUNG_K = 60
_ISSUE_URL = re.compile(r"/issues/(\d+)\s*$")

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
}


@dataclass
class Ergebnis:
    ok: bool
    zeilen: list[str] = field(default_factory=list)


def luecken(repo: Path, konfig: dict[str, Any]) -> list[str]:
    """Was dem Repo zur Einrichtung fehlt (Kurznamen aus ``_AUFTRAG``); leer = nichts."""
    fehlt: list[str] = []
    if not pruef.hat_werkzeug(repo):
        fehlt.append("pruefung")
    staging = konfig.get("staging") if isinstance(konfig.get("staging"), dict) else {}
    if not staging.get("ohne") and not (
        str(staging.get("url") or "").strip()
        or str(konfig.get("staging_start") or "").strip()
    ):
        fehlt.append("staging")
    return fehlt


def sicherstellen(
    repo: Path,
    spec: int | str,
    konfig: dict[str, Any],
    *,
    mit_github: bool = True,
    trocken: bool = False,
) -> Ergebnis:
    """Einrichtungs-Ticket der Spec anlegen/anhängen, falls dem Repo etwas fehlt.

    ``ok`` = nichts fehlt bzw. Ticket steht (oder nur Hinweis ohne GitHub / ``trocken``);
    ``False`` = Lücke, aber das Ticket ließ sich nicht anlegen oder anhängen (WARNUNG-Zeile).
    Wie jede große Lücke hält das den Start nicht auf (Plan L1 Tür 1), es wird nur gemeldet.
    """
    fehlt = luecken(repo, konfig)
    if not fehlt:
        return Ergebnis(True)
    s = str(spec).lstrip("#")
    kopf = f"Einrichtung fehlt: {', '.join(fehlt)}"
    try:
        daten, _ = manifest.lade_manifest_mit_doppelten(repo, s)
    except (OSError, ValueError) as fehler:
        # Fehlt/kaputt: das meldet die Manifest-Prüfung gleich selbst.
        return Ergebnis(True, [f"{kopf} — Manifest nicht lesbar ({fehler})"])
    vorhanden = manifest.einrichtungs_ticket(daten)
    slug = gh.repo_aus_origin(repo) if mit_github else ""
    if not slug or trocken:
        wie = (
            "würde Einrichtungs-Ticket anlegen"
            if not vorhanden
            else f"Einrichtungs-Ticket #{vorhanden}"
        )
        grund = "[trocken]" if trocken else "ohne GitHub"
        return Ergebnis(
            True, [f"{kopf} — {grund}: {wie}. " + " ".join(_AUFTRAG[x] for x in fehlt)]
        )
    if vorhanden is None:
        neu = _anlegen(repo, slug, s, daten, fehlt)
        if isinstance(neu, Ergebnis):
            return neu
        vorhanden, zeilen = neu, [f"{kopf} — Einrichtungs-Ticket #{neu} angelegt"]
    else:
        stand = gh.json_lauf(["api", f"repos/{slug}/issues/{vorhanden}"], cwd=repo)
        if isinstance(stand, dict) and ticket_zustand.endgueltig_zu(stand):
            return Ergebnis(
                True,
                [
                    f"{kopf} — Einrichtungs-Ticket #{vorhanden} ist zu, Lücke bleibt: von Hand prüfen"
                ],
            )
        zeilen = [f"{kopf} — Einrichtungs-Ticket #{vorhanden} steht"]
    # ponytail: je Ticket ein anhaengen samt Manifest-Prüfung (N× gh); ein Sammel-Lauf, wenn Specs groß werden.
    # Einrichtungs-Ticket zuerst (Unter-Issue, Kante vor die Abnahme), dann jedes andere — idempotent.
    for t in [
        vorhanden,
        *sorted(
            (x for x in daten["tickets"] if x != vorhanden),
            key=manifest.ticket_schluessel,
        ),
    ]:
        lauf = spec_rand.anhaengen(repo, s, t, konfig)
        if lauf.exit == spec_rand.EXIT_FEHLER:
            return Ergebnis(
                False,
                [
                    *zeilen,
                    *lauf.zeilen,
                    f"WARNUNG: #{t} nicht hinter Einrichtungs-Ticket #{vorhanden} — erneut aufrufen",
                ],
            )
    return Ergebnis(
        True, [*zeilen, f"alle Tickets der Spec #{s} warten auf #{vorhanden}"]
    )


def _anlegen(
    repo: Path, slug: str, s: str, daten: dict[str, Any], fehlt: list[str]
) -> str | Ergebnis:
    """Issue anlegen und im Manifest eintragen; Rückgabe Nummer oder Fehler-Ergebnis."""
    titel = f"Einrichtung für Spec #{s}: {', '.join(fehlt)}"
    auftrag = "\n".join(f"- {_AUFTRAG[x]}" for x in fehlt)
    body = (
        f"Teil von Spec #{s}. Einrichtungs-Ticket (T0): Dem Repo fehlt Einrichtung, "
        f"alle anderen Tickets der Spec warten darauf.\n\n{auftrag}\n\n"
        f"Steht im Manifest spec-{s}.json mit `einrichtung: true`."
    )
    code, ausgabe = gh.lauf(
        ["issue", "create", "--repo", slug, "--title", titel, "--body", body], cwd=repo
    )
    treffer = _ISSUE_URL.search(ausgabe or "")
    if code != 0 or not treffer:
        log.warning(
            "gh issue create gescheitert (Code %s): %s", code, (ausgabe or "")[:200]
        )
        return Ergebnis(
            False,
            [
                f"WARNUNG: Einrichtung fehlt ({', '.join(fehlt)}), Einrichtungs-Ticket nicht angelegt (gh issue create)"
            ],
        )
    neu = treffer.group(1)
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
                f"WARNUNG: Einrichtungs-Ticket #{neu} angelegt, Manifest nicht geschrieben — von Hand eintragen ({datei.name}, einrichtung: true)"
            ],
        )
    return neu
