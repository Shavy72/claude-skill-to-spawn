"""Test-Übersicht einer Spec als selbsterklärende HTML-Seite (Abschluss-Paket, Seite 3).

Aufruf: ``python test_uebersicht.py <S> [--repo <pfad>] [--aus <datei.html>]``

Liest die Tickets aus ``docs/agents/manifests/spec-<S>.json`` und deren Belegseiten
unter ``docs/verify-hard/`` (``<N>.md``, ``<N>_*.txt|md``, Ordner ``<N>/`` bzw.
``<datum>_<N>/``). Je Ticket zählt der neueste Testlauf (Commit-Zeit der Belegdatei)
aus Dateien, die kein Rot-Beweis sind (Rot-Beweise = gewolltes Scheitern vor dem Fix, Name enthält ``rot``).
Fallback ohne Testlauf: ``ABNAHME: …``-Zeile bzw. ``X/Y``-Zählung der Belegseite. Jede Karte
nennt Belegdatei und Stand der Zahl; ohne auswertbaren Lauf steht „offen – kein Testlauf
gefunden“ statt einer Zahl.
Ausgabe: ``docs/test-uebersicht/<datum>_spec<S>.html``; der Pfad geht auf stdout.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import belege, config, manifest  # noqa: E402
from to_spawn.belege import Lauf  # noqa: E402

log = logging.getLogger("test_uebersicht")

KEIN_LAUF = "offen – kein Testlauf gefunden"


@dataclass
class Karte:
    """Eine Ticket-Karte der Übersicht."""

    nummer: str
    titel: str
    status: str  # "gruen" | "rot" | "offen"
    lauf: Lauf | None = None
    abnahme: str = ""
    klickweg: bool = False
    rot_beweis: bool = False
    dateien: list[str] = field(default_factory=list)
    verdeckt: Lauf | None = None
    abnahme_datei: str = ""
    hinweis: str = ""  # was fehlt (Text aus belege.Belege.hinweis)


def werte_ticket(verify_hard: Path, nummer: str, titel: str) -> Karte:
    """Karte eines Tickets aus seinen Belegdateien (Erkennung: :func:`belege.auswerten`)."""
    b = belege.auswerten(verify_hard, nummer)
    return Karte(
        nummer=nummer,
        titel=titel,
        status=b.status,
        lauf=b.lauf,
        abnahme=b.abnahme,
        klickweg=b.klickweg,
        rot_beweis=b.rot_beweis,
        dateien=list(b.dateien),
        verdeckt=b.verdeckt,
        abnahme_datei=b.abnahme_datei,
        hinweis=b.hinweis,
    )


def lade_tickets(repo: Path, spec: int) -> list[tuple[str, str]]:
    """``[(nummer, titel)]`` aus dem Spec-Manifest (Tickets als Objekt oder Liste)."""
    daten = json.loads(manifest.manifest_pfad(repo, spec).read_text(encoding="utf-8"))
    tickets = daten.get("tickets") or {}
    if isinstance(tickets, dict):
        return [(str(n), str((t or {}).get("title", ""))) for n, t in tickets.items()]
    return [(str(t.get("nummer") or t.get("number") or t.get("id")), str(t.get("title", ""))) for t in tickets]


def kopfzeile(karten: list[Karte]) -> str:
    """Große Zeile oben: ✅/❌/⚠️ + ``X von Y Tests grün``."""
    passed = sum(k.lauf.passed for k in karten if k.lauf)
    gesamt = passed + sum(k.lauf.failed for k in karten if k.lauf)
    rot = sum(k.status == "rot" for k in karten)
    ungeklaert = sum(k.status == "offen" and k.verdeckt is not None for k in karten)
    offen = sum(k.status == "offen" and k.verdeckt is None for k in karten)
    zeichen = "❌" if rot else ("⚠️" if offen or ungeklaert else "✅")
    if gesamt:
        text = f"{zeichen} {passed} von {gesamt} Tests grün"
    elif not rot and not offen:  # alle grün nur über ABNAHME-Zeilen
        text = f"✅ {len(karten)} von {len(karten)} Tickets per Abnahme grün (kein Testlauf)"
    else:  # keine Zahl vortäuschen, wenn kein Lauf gezählt wurde (#366)
        text = f"{'❌' if rot else '⚠️'} {KEIN_LAUF}"
    if rot:
        text += f" — {rot} Ticket{'s' if rot > 1 else ''} rot"
    if offen:
        text += f" — {offen} ohne Beleg"
    if ungeklaert:
        text += f" — {ungeklaert} ungeklärt (neuer Teil-Lauf deckt alten roten Lauf nicht ab)"
    return text


_CSS = """
:root{--bg:#f7f7f5;--karte:#fff;--text:#1d1d1f;--leise:#6b6b70;--rand:#e3e3e0;
--gruen:#1f8a4c;--gruen-bg:#e6f4ec;--rot:#c62828;--rot-bg:#fdecea;--grau:#8a8a8f;--grau-bg:#f0f0f0;
--code-bg:#f2f2ef}
@media (prefers-color-scheme:dark){:root{--bg:#121214;--karte:#1c1c1f;--text:#ececef;--leise:#a0a0a8;
--rand:#2e2e33;--gruen:#4cc47f;--gruen-bg:#16301f;--rot:#ff6b6b;--rot-bg:#3a1a1a;--grau:#a0a0a8;
--grau-bg:#2a2a2e;--code-bg:#26262a}}
*{box-sizing:border-box}body{margin:0;font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;
background:var(--bg);color:var(--text)}main{max-width:760px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.9rem;line-height:1.25;margin:.2em 0 .4em}.leise{color:var(--leise)}
.erklaer{background:var(--karte);border:1px solid var(--rand);border-radius:12px;padding:12px 16px;margin:16px 0}
.karte{background:var(--karte);border:1px solid var(--rand);border-left:6px solid var(--grau);
border-radius:12px;padding:14px 16px;margin:12px 0}.karte.gruen{border-left-color:var(--gruen)}
.karte.rot{border-left-color:var(--rot)}.karte h2{font-size:1.05rem;margin:0 0 6px}
.marke{display:inline-block;font-weight:600;border-radius:999px;padding:2px 10px;font-size:.9rem}
.marke.gruen{background:var(--gruen-bg);color:var(--gruen)}.marke.rot{background:var(--rot-bg);color:var(--rot)}
.marke.offen{background:var(--grau-bg);color:var(--grau)}dl{margin:8px 0 0}dt{font-weight:600;margin-top:6px}
dd{margin:0}code{background:var(--code-bg);border-radius:6px;padding:1px 5px;font-size:.88rem;
overflow-wrap:anywhere}
"""

_STATUS_TEXT = {
    "gruen": "✅ grün — alles hat geklappt",
    "rot": "❌ rot — mindestens eine Prüfung ist gescheitert",
    "offen": "⚠️ kein Beleg gefunden",
}


def _stand(lauf: Lauf) -> str:
    return datetime.fromtimestamp(lauf.zeit).strftime("%Y-%m-%d %H:%M") if lauf.zeit else "unbekannt"


def _karte_html(k: Karte) -> str:
    e = html.escape
    marke, titel_quelle = _STATUS_TEXT[k.status], "Zahl stammt aus"
    if k.lauf:
        befehl = k.lauf.befehl or f"Befehl nicht mitgeschrieben — Ausgabe aus docs/verify-hard/{k.lauf.datei}"
        beleg = f"<code>{e(befehl)}</code><br><code>{e(k.lauf.zeile)}</code>"
        zahl = f" ({k.lauf.passed} von {k.lauf.passed + k.lauf.failed} Tests grün)"
        quelle = f"<code>docs/verify-hard/{e(k.lauf.datei)}</code> · Stand {_stand(k.lauf)}"
        if k.verdeckt and k.status == "offen":
            v = k.verdeckt
            marke = "⚠️ offen — neuer Teil-Lauf deckt älteren roten Lauf nicht ab"
            beleg += (
                f"<br>Der neueste Lauf war grün, hat aber nur {k.lauf.passed} Tests geprüft. Ein älterer Lauf "
                f"mit {v.passed + v.failed} Tests hatte {v.failed} Fehler "
                f"(<code>docs/verify-hard/{e(v.datei)}</code> · Stand {_stand(v)}). Es fehlt: "
                f"{e(k.hinweis)}."
            )
    else:
        zahl, quelle = f" ({KEIN_LAUF})", "—"
        if k.abnahme:
            beleg = f"<code>ABNAHME: {e(k.abnahme)}</code>"
            titel_quelle = "Aussage stammt aus"
            quelle = f"Abnahme-Zeile in <code>docs/verify-hard/{e(k.abnahme_datei)}</code>"
            if k.status != "offen":  # grün/rot steht in der Marke, ein „offen“ daneben widerspräche
                zahl = ""
                marke = "✅ grün — laut Abnahme-Zeile" if k.status == "gruen" else "❌ rot — laut Abnahme-Zeile"
        elif k.dateien:
            beleg = "Belegseite ohne Testzahl: " + ", ".join(f"<code>{e(d)}</code>" for d in k.dateien)
        else:
            beleg = "Keine Belegseite unter docs/verify-hard/ gefunden."
    klick = "ja — jemand hat es am echten System durchgeklickt" if k.klickweg else "nein"
    rot = (
        " Vorher gab es einen Rot-Beweis (der Test ist vor der Reparatur absichtlich gescheitert)."
        if k.rot_beweis
        else ""
    )
    return (
        f'<section class="karte {k.status}"><h2>Ticket #{e(k.nummer)}</h2>'
        f'<span class="marke {k.status}">{marke}</span>{e(zahl)}'
        f"<dl><dt>Was wurde geprüft?</dt><dd>{e(k.titel or 'ohne Titel')}.{e(rot)}</dd>"
        f"<dt>Beleg</dt><dd>{beleg}</dd>"
        f"<dt>{titel_quelle}</dt><dd>{quelle}</dd>"
        f"<dt>Live-Klickweg</dt><dd>{klick}</dd></dl></section>"
    )


def baue_html(spec: int, karten: list[Karte], heute: str) -> str:
    """Komplette Seite (mobil tauglich, Hell/Dunkel über Farb-Tokens)."""
    e = html.escape
    return (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Test-Übersicht Spec {spec}</title><style>{_CSS}</style></head><body><main>"
        f'<p class="leise">Test-Übersicht Spec {spec} · Stand {e(heute)}</p>'
        f"<h1>{e(kopfzeile(karten))}</h1>"
        '<div class="erklaer">Ein <b>Test</b> ist ein kleines Prüf-Programm: es probiert eine Funktion '
        "aus und meldet „grün“ (klappt) oder „rot“ (klappt nicht). Jede Karte unten ist ein Arbeitspaket "
        "(<b>Ticket</b>). Der <b>Beleg</b> zeigt den Befehl, mit dem geprüft wurde, und die Zeile, die "
        "der Computer als Ergebnis ausgegeben hat. <b>Live-Klickweg</b> heißt: ein Mensch (oder ein "
        "Programm in seiner Rolle) hat es im echten System ausprobiert.</div>"
        + "".join(_karte_html(k) for k in karten)
        + "</main></body></html>\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Test-Übersicht einer Spec als HTML-Seite.")
    ap.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    ap.add_argument("--repo", type=Path, default=None, help="Repo-Wurzel (Vorgabe: TO_SPAWN_REPO bzw. Git-Wurzel)")
    ap.add_argument(
        "--aus", type=Path, default=None, help="Ziel-Datei (Vorgabe docs/test-uebersicht/<datum>_spec<S>.html)"
    )
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    repo = (
        a.repo or (Path(os.environ["TO_SPAWN_REPO"]) if os.environ.get("TO_SPAWN_REPO") else config.repo_wurzel())
    ).resolve()
    try:
        tickets = lade_tickets(repo, a.spec)
    except (OSError, ValueError, AttributeError, TypeError) as fehler:
        log.error("Manifest spec-%s nicht lesbar: %s", a.spec, fehler)
        return 2
    verify_hard = repo / "docs" / "verify-hard"
    karten = [werte_ticket(verify_hard, nr, titel) for nr, titel in tickets]
    jetzt = datetime.now()
    ziel = a.aus or repo / "docs" / "test-uebersicht" / f"{jetzt.date().isoformat()}_spec{a.spec}.html"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(baue_html(a.spec, karten, jetzt.strftime("%Y-%m-%d %H:%M")), encoding="utf-8")
    log.info("%s", kopfzeile(karten))
    sys.stdout.write(f"{ziel}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
