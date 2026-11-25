"""Belegseiten-Übersicht einer Spec als eine HTML-Seite (Abschluss-Paket, Seite 2).

Aufruf: ``python belege_uebersicht.py <S> [--repo <pfad>] [--aus <datei.html>] [--ohne-github]``

Je Ticket aus ``docs/agents/manifests/spec-<S>.json``: Titel, Status (offen/fertig gebaut/zu/verworfen über
``ticket_zustand.lesen``; mit ``--ohne-github`` oder ohne ``gh`` „unbekannt“),
Beleg-Zeile (erste Zeile mit „ABNAHME“, sonst mit „Beleg“ aus ``docs/verify-hard/<N>.md`` bzw.
``<N>/*.md``) und die Liste der Belegdateien. Die Dateisuche kommt aus ``to_spawn.belege``
(gleiche Nummern-Logik wie ``test_uebersicht.py``: ``9010`` zählt nicht für ``901``).
Ausgabe: ``docs/belege-uebersicht/<datum>_spec<S>.html``; der Pfad geht auf stdout.
"""

from __future__ import annotations

import argparse
import html
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import quote

_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
_SKRIPTE = str(Path(__file__).resolve().parent)
if _SKRIPTE not in sys.path:
    sys.path.insert(0, _SKRIPTE)
from test_uebersicht import _CSS, lade_tickets  # noqa: E402

from to_spawn import config, ticket_zustand  # noqa: E402
from to_spawn.belege import _lies, finde_bilder, finde_dateien  # noqa: E402

log = logging.getLogger("belege_uebersicht")

KEIN_BELEG = "kein Beleg gefunden"
KEINE_ZEILE = "keine Beleg-Zeile — siehe Dateien unten"


@dataclass
class Eintrag:
    """Ein Ticket der Belegseiten-Übersicht."""

    nummer: str
    titel: str
    status: str  # "offen" | "fertig gebaut" | "zu" | "verworfen" | "unbekannt"
    zeile: str
    dateien: list[str] = field(default_factory=list)
    bilder: list[str] = field(default_factory=list)


def status_von(nummer: str, repo: Path, ohne_github: bool) -> str:
    """offen / fertig gebaut / zu / verworfen aus GitHub (#279); ``unbekannt`` ohne GitHub oder ohne Antwort."""
    if ohne_github:
        return "unbekannt"
    daten = ticket_zustand.lesen(nummer, cwd=repo)
    return "unbekannt" if daten is None else ticket_zustand.ANZEIGE[ticket_zustand.stufe(daten)]


_TRENNER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _ohne_tabellenkopf(zeilen: list[str]) -> list[str]:
    """Markdown-Tabellenköpfe (Zeile vor ``|---|``) und Trennerzeilen sind nie eine Beleg-Zeile."""
    raus: set[int] = set()
    for i, zeile in enumerate(zeilen):
        if _TRENNER.match(zeile):
            raus.add(i)
            if i > 0 and "|" in zeilen[i - 1]:
                raus.add(i - 1)
    return [z for i, z in enumerate(zeilen) if i not in raus]


def beleg_zeile(verify_hard: Path, nummer: str, dateien: list[Path]) -> str:
    """Erste Zeile mit „ABNAHME“ (sonst „Beleg“, ohne Überschriften) aus den Markdown-Belegen."""
    md = [d for d in dateien if d.suffix.lower() == ".md"]
    haupt = verify_hard / f"{nummer}.md"
    md.sort(key=lambda d: d != haupt)
    texte = [_ohne_tabellenkopf(_lies(d).splitlines()) for d in md]
    for zeilen in texte:
        for zeile in zeilen:
            if "ABNAHME" in zeile:
                return zeile.strip()[:300]
    for zeilen in texte:
        for zeile in zeilen:
            if "Beleg" in zeile and not zeile.lstrip().startswith("#"):
                return zeile.strip()[:300]
    return KEINE_ZEILE if dateien else KEIN_BELEG


def eintrag_von(repo: Path, nummer: str, titel: str, ohne_github: bool) -> Eintrag:
    verify_hard = repo / "docs" / "verify-hard"
    dateien = finde_dateien(verify_hard, nummer)
    bilder = finde_bilder(verify_hard, nummer)
    return Eintrag(
        nummer=nummer,
        titel=titel,
        status=status_von(nummer, repo, ohne_github),
        zeile=beleg_zeile(verify_hard, nummer, dateien),
        dateien=[d.relative_to(verify_hard).as_posix() for d in dateien],
        bilder=[b.relative_to(verify_hard).as_posix() for b in bilder],
    )


_KLASSE = {"zu": "gruen", "fertig gebaut": "gebaut", "verworfen": "offen", "offen": "offen", "unbekannt": "offen"}


def _bild_src(bild_rel: str, ziel_ordner: Path | None, verify_hard: Path | None) -> str:
    """Relativer POSIX-Pfad von der HTML-Datei zum Bild-Beleg (Windows-Laufwerke sicher abgefangen)."""
    if ziel_ordner is not None and verify_hard is not None:
        try:
            pfad = os.path.relpath(verify_hard / bild_rel, ziel_ordner).replace("\\", "/")
        except ValueError:
            pfad = f"../verify-hard/{bild_rel}"
    else:
        pfad = f"../verify-hard/{bild_rel}"
    return quote(pfad, safe="/._-~")


def _karte_html(e: Eintrag, *, ziel_ordner: Path | None = None, verify_hard: Path | None = None) -> str:
    x = html.escape
    klasse = "rot" if e.zeile == KEIN_BELEG else _KLASSE[e.status]
    dateien = (
        "<ul>" + "".join(f"<li><code>docs/verify-hard/{x(d)}</code></li>" for d in e.dateien) + "</ul>"
        if e.dateien
        else "<p>keine Dateien</p>"
    )
    bilder_html = ""
    if e.bilder:
        figuren = "".join(
            f'<figure><img src="{x(_bild_src(b, ziel_ordner, verify_hard))}" '
            f'alt="Beleg Ticket #{x(e.nummer)}: {x(b)}" loading="lazy">'
            f"<figcaption><code>docs/verify-hard/{x(b)}</code></figcaption></figure>"
            for b in e.bilder
        )
        bilder_html = f'<dt>Bild-Belege</dt><dd class="bilder">{figuren}</dd>'
    return (
        f'<section class="karte {klasse}"><h2>Ticket #{x(e.nummer)} — {x(e.titel or "ohne Titel")}</h2>'
        f'<span class="marke {_KLASSE[e.status]}">Status: {x(e.status)}</span>'
        f"<dl><dt>Beleg-Zeile</dt><dd><code>{x(e.zeile)}</code></dd>"
        f"<dt>Belegdateien</dt><dd>{dateien}</dd>{bilder_html}</dl></section>"
    )


def baue_html(
    spec: int,
    eintraege: list[Eintrag],
    heute: str,
    *,
    ziel_ordner: Path | None = None,
    verify_hard: Path | None = None,
) -> str:
    """Komplette Seite (mobil tauglich, Hell/Dunkel über Farb-Tokens aus der Test-Übersicht)."""
    zu = sum(e.status == "zu" for e in eintraege)
    ohne = sum(e.zeile == KEIN_BELEG for e in eintraege)
    kopf = f"{len(eintraege)} Tickets — {zu} zu — {ohne} ohne Beleg"
    bild_css = (
        ".bilder{display:grid;gap:10px;margin:6px 0}"
        ".bilder figure{margin:0}"
        ".bilder img{max-width:100%;height:auto;border:1px solid var(--rand);border-radius:8px;display:block}"
        ".bilder figcaption{font-size:.85rem;color:var(--leise);margin-top:4px}"
    )
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Belegseiten Spec {spec}</title><style>{_CSS}ul{{margin:4px 0;padding-left:20px}}.marke.gebaut{{background:var(--grau-bg);color:var(--gruen)}}{bild_css}</style>"
        "</head><body><main>"
        f'<p class="leise">Belegseiten Spec {spec} · Stand {html.escape(heute)}</p>'
        f"<h1>{html.escape(kopf)}</h1>"
        '<div class="erklaer">Jede Karte ist ein Arbeitspaket (<b>Ticket</b>). Die <b>Beleg-Zeile</b> ist '
        "der wichtigste Satz aus dem Nachweis — meist das Ergebnis der Abnahme. Darunter stehen alle "
        "Dateien, in denen der Nachweis liegt. <b>Status zu</b> heißt: das Ticket ist erledigt.</div>"
        + "".join(_karte_html(e, ziel_ordner=ziel_ordner, verify_hard=verify_hard) for e in eintraege)
        + "</main></body></html>\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Belegseiten-Übersicht einer Spec als HTML-Seite.")
    ap.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    ap.add_argument("--repo", type=Path, default=None, help="Repo-Wurzel (Vorgabe: TO_SPAWN_REPO bzw. Git-Wurzel)")
    ap.add_argument(
        "--aus", type=Path, default=None, help="Ziel-Datei (Vorgabe docs/belege-uebersicht/<datum>_spec<S>.html)"
    )
    ap.add_argument("--ohne-github", action="store_true", help="Status nicht bei GitHub abfragen („unbekannt“)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    repo = (
        a.repo or (Path(os.environ["TO_SPAWN_REPO"]) if os.environ.get("TO_SPAWN_REPO") else config.repo_wurzel())
    ).resolve()
    try:
        tickets = lade_tickets(repo, a.spec)
    except (OSError, ValueError, AttributeError) as fehler:
        log.error("Manifest spec-%s nicht lesbar: %s", a.spec, fehler)
        return 2
    eintraege = [eintrag_von(repo, nr, titel, a.ohne_github) for nr, titel in tickets]
    heute = date.today().isoformat()
    ziel = a.aus or repo / "docs" / "belege-uebersicht" / f"{heute}_spec{a.spec}.html"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(
        baue_html(
            a.spec,
            eintraege,
            heute,
            ziel_ordner=ziel.resolve().parent,
            verify_hard=(repo / "docs" / "verify-hard").resolve(),
        ),
        encoding="utf-8",
    )
    log.info("Belegseiten-Übersicht Spec %s: %s Tickets", a.spec, len(eintraege))
    sys.stdout.write(f"{ziel}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
