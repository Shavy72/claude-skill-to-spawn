"""Test-Übersicht einer Spec als selbsterklärende HTML-Seite (Abschluss-Paket, Seite 3).

Aufruf: ``python test_uebersicht.py <S> [--repo <pfad>] [--aus <datei.html>]``

Liest die Tickets aus ``docs/agents/manifests/spec-<S>.json`` und deren Belegseiten
unter ``docs/verify-hard/`` (``<N>.md``, ``<N>_*.txt|md``, Ordner ``<N>/`` bzw.
``<datum>_<N>/``). Je Ticket zählt der größte Testlauf aus Dateien, die kein
Rot-Beweis sind (Rot-Beweise = gewolltes Scheitern vor dem Fix, Name enthält ``rot``).
Fallback ohne Testlauf: ``ABNAHME: …``-Zeile bzw. ``X/Y``-Zählung der Belegseite.
Ausgabe: ``docs/test-uebersicht/<datum>_spec<S>.html``; der Pfad geht auf stdout.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import config  # noqa: E402

log = logging.getLogger("test_uebersicht")

MAX_BYTES = 2_000_000
_ROT_DATEI = re.compile(r"(?:^|[_\-/.])rot(?:[_\-/.]|$)", re.I)
_PASSED = re.compile(r"\b(\d+) passed\b")
_FAILED = re.compile(r"\b(\d+) (?:failed|errors?)\b")
_BEFEHL = re.compile(r"(?<![\w./])(?:python3? -m pytest|pytest|node --test|npm (?:run )?test) [^\n`|]*")
_ABNAHME = re.compile(r"ABNAHME[^:\n]*:\s*\**\s*([^\n*|]+)")
_ABNAHME_GUT = re.compile(r"\b(?:ABGENOMMEN|BESTANDEN|GRÜN|BELEGT|FREIGEGEBEN)\b", re.I)
_ABNAHME_SCHLECHT = re.compile(r"\b(?:ABGELEHNT|ROT|NICHT|FEHLT)\b|(?<!nichts )(?<!keine )(?<!nix )\bOFFEN\b", re.I)
_XY = re.compile(r"\b(\d+)\s*/\s*(\d+)\s+(?:Tests?|grün|bestanden|belegt|Kriterien|Abnahme)", re.I)
_KLICKWEG = re.compile(r"klick.?weg|live-klick|live-beweis", re.I)
_VERNEINT = re.compile(r"\b(?:kein|keine|keiner|nicht|entfällt|n/a)\b", re.I)


@dataclass
class Lauf:
    """Ein Testlauf: Zählung + Beleg (Befehl, Ausgabezeile, Datei)."""

    passed: int
    failed: int
    zeile: str
    befehl: str
    datei: str


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


def _lies(datei: Path) -> str:
    try:
        if datei.stat().st_size > MAX_BYTES:
            return ""
        return datei.read_text(encoding="utf-8", errors="replace")
    except OSError as fehler:
        log.warning("Belegdatei nicht lesbar %s: %s", datei, fehler)
        return ""


def finde_dateien(verify_hard: Path, nummer: str) -> list[Path]:
    """Alle .md/.txt-Belegdateien eines Tickets (Datei ``<N>…`` oder Ordner ``[<datum>_]<N>…``)."""
    muster = re.compile(rf"^(?:\d{{4}}-\d{{2}}-\d{{2}}_)?{re.escape(nummer)}(?=$|[_.\-])")
    treffer: list[Path] = []
    if not verify_hard.is_dir():
        return treffer
    for eintrag in sorted(verify_hard.iterdir()):
        if not muster.match(eintrag.name):
            continue
        kandidaten = sorted(eintrag.rglob("*")) if eintrag.is_dir() else [eintrag]
        treffer += [k for k in kandidaten if k.is_file() and k.suffix.lower() in (".md", ".txt")]
    return treffer


def lauf_aus_text(text: str, datei: str, nur_saubere_zeilen: bool = False) -> Lauf | None:
    """Letzte Zusammenfassungs-Zeile (``N passed``/``N failed``) eines Textes als Lauf.

    ``nur_saubere_zeilen`` (Markdown): nur Zeilen ohne ``failed``/``rot``/``→`` — dort stehen
    oft Vorher-Nachher-Übergänge, die nichts über den Endstand sagen; dann zählt der größte Lauf.
    """
    befehl_m = _BEFEHL.search(text)
    befehl = befehl_m.group(0).strip()[:200] if befehl_m else ""
    beste: Lauf | None = None
    for zeile in text.splitlines():
        p = _PASSED.search(zeile)
        f = _FAILED.search(zeile)
        if not p and not f:
            continue
        if nur_saubere_zeilen and (f or "→" in zeile or re.search(r"\brot\b", zeile, re.I)):
            continue
        lauf = Lauf(
            passed=int(p.group(1)) if p else 0,
            failed=sum(int(x) for x in _FAILED.findall(zeile)),
            zeile=zeile.strip(" =|\t")[:200],
            befehl=befehl,
            datei=datei,
        )
        if not nur_saubere_zeilen or beste is None or lauf.passed >= beste.passed:
            beste = lauf
    return beste


def werte_ticket(verify_hard: Path, nummer: str, titel: str) -> Karte:
    """Karte eines Tickets aus seinen Belegdateien."""
    karte = Karte(nummer=nummer, titel=titel, status="offen")
    laeufe: list[Lauf] = []
    for datei in finde_dateien(verify_hard, nummer):
        rel = datei.relative_to(verify_hard).as_posix()
        karte.dateien.append(rel)
        text = _lies(datei)
        if _ROT_DATEI.search(rel):
            karte.rot_beweis = True
            continue
        if datei.suffix.lower() == ".txt":
            lauf = lauf_aus_text(text, rel)
        else:
            lauf = None
            if not karte.abnahme and (m := _ABNAHME.search(text)):
                karte.abnahme = m.group(1).strip()
            for zeile in text.splitlines():
                if _KLICKWEG.search(zeile) and not _VERNEINT.search(zeile):
                    karte.klickweg = True
                    break
        if lauf:
            laeufe.append(lauf)
    if not laeufe:  # Fallback: Zählung aus den Markdown-Belegseiten
        for rel in karte.dateien:
            if rel.lower().endswith(".md") and not _ROT_DATEI.search(rel):
                text = _lies(verify_hard / rel)
                lauf = lauf_aus_text(text, rel, nur_saubere_zeilen=True)
                if not lauf and (xy := _XY.search(text)):
                    x, y = int(xy.group(1)), int(xy.group(2))
                    if 0 < y and x <= y:
                        lauf = Lauf(x, y - x, xy.group(0), "", rel)
                if lauf:
                    laeufe.append(lauf)
    if laeufe:
        karte.lauf = max(laeufe, key=lambda l: (l.passed + l.failed, l.datei))
    abnahme_schlecht = bool(karte.abnahme) and bool(_ABNAHME_SCHLECHT.search(karte.abnahme))
    abnahme_gut = bool(karte.abnahme) and bool(_ABNAHME_GUT.search(karte.abnahme))
    if (karte.lauf and karte.lauf.failed > 0) or abnahme_schlecht:
        karte.status = "rot"
    elif karte.lauf or abnahme_gut:
        karte.status = "gruen"
    return karte


def lade_tickets(repo: Path, spec: int) -> list[tuple[str, str]]:
    """``[(nummer, titel)]`` aus dem Spec-Manifest (Tickets als Objekt oder Liste)."""
    manifest = repo / "docs" / "agents" / "manifests" / f"spec-{spec}.json"
    daten = json.loads(manifest.read_text(encoding="utf-8"))
    tickets = daten.get("tickets") or {}
    if isinstance(tickets, dict):
        return [(str(n), str((t or {}).get("title", ""))) for n, t in tickets.items()]
    return [(str(t.get("nummer") or t.get("number") or t.get("id")), str(t.get("title", ""))) for t in tickets]


def kopfzeile(karten: list[Karte]) -> str:
    """Große Zeile oben: ✅/❌/⚠️ + ``X von Y Tests grün``."""
    passed = sum(k.lauf.passed for k in karten if k.lauf)
    gesamt = passed + sum(k.lauf.failed for k in karten if k.lauf)
    rot = sum(k.status == "rot" for k in karten)
    offen = sum(k.status == "offen" for k in karten)
    zeichen = "❌" if rot else ("⚠️" if offen else "✅")
    if gesamt:
        text = f"{zeichen} {passed} von {gesamt} Tests grün"
    else:
        text = f"{zeichen} {len(karten) - rot - offen} von {len(karten)} Tickets grün"
    if rot:
        text += f" — {rot} Ticket{'s' if rot > 1 else ''} rot"
    if offen:
        text += f" — {offen} ohne Beleg"
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


def _karte_html(k: Karte) -> str:
    e = html.escape
    if k.lauf:
        befehl = k.lauf.befehl or f"Befehl nicht mitgeschrieben — Ausgabe aus docs/verify-hard/{k.lauf.datei}"
        beleg = f"<code>{e(befehl)}</code><br><code>{e(k.lauf.zeile)}</code>"
        zahl = f" ({k.lauf.passed} von {k.lauf.passed + k.lauf.failed} Tests grün)"
    elif k.abnahme:
        beleg, zahl = f"<code>ABNAHME: {e(k.abnahme)}</code>", ""
    else:
        beleg, zahl = "Keine Belegseite unter docs/verify-hard/ gefunden.", ""
    klick = "ja — jemand hat es am echten System durchgeklickt" if k.klickweg else "nein"
    rot = (
        " Vorher gab es einen Rot-Beweis (der Test ist vor der Reparatur absichtlich gescheitert)."
        if k.rot_beweis
        else ""
    )
    return (
        f'<section class="karte {k.status}"><h2>Ticket #{e(k.nummer)}</h2>'
        f'<span class="marke {k.status}">{_STATUS_TEXT[k.status]}</span>{e(zahl)}'
        f"<dl><dt>Was wurde geprüft?</dt><dd>{e(k.titel or 'ohne Titel')}.{e(rot)}</dd>"
        f"<dt>Beleg</dt><dd>{beleg}</dd>"
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
    heute = date.today().isoformat()
    ziel = a.aus or repo / "docs" / "test-uebersicht" / f"{heute}_spec{a.spec}.html"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(baue_html(a.spec, karten, heute), encoding="utf-8")
    log.info("%s", kopfzeile(karten))
    sys.stdout.write(f"{ziel}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
