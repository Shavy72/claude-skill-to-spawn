"""Abschluss-Paket einer Spec: alles zum Durchschauen an David (vor und nach dem Live-Deploy).

Aufruf: ``python abschluss_paket.py <S> --stage <url> --rundschau <link> --tests <link>
[--belege <link>] [--direkt "<Titel>=<url>"]… [--basic-auth-nutzer <name>]
[--app-rolle "<Name (rolle)>"]… [--bitwarden <Eintragsname>] [--neu "<Satz>"]…
[--tun "<Satz>"]… [--stand abnahme|live] [--repo <pfad>] [--dry-run]``

Schreibt ``docs/agents/abschluss_<S>.md`` (bei ``--stand live``: ``abschluss_<S>_live.md``) und
``~/.claude/data/abschluss/offen/<projekt>_<S>.json`` (liest der Hook
``abschluss-melder.mjs`` und zeigt die Links in der nächsten Session einmal an).
Mail über ``mail.befehl`` aus ``.to-spawn/config.json`` (Art ``spec_fertig``, geht auch bei
``mail.nur_kritisch`` raus) — nur wenn dort eingerichtet. Schlüssel gegen doppelte Mails:
``abschluss_<S>`` bzw. ``abschluss_<S>_live``. ``--dry-run``: nichts schreiben, nichts mailen.
Ohne ``--belege``/``--direkt``/Zugang/``--tun``/``live`` bleibt die alte Kurzform („3 Links“).
Die Mail nennt nur Namen, nie ein Passwort; steht in einem Argument etwas wie ``passwort=`` oder
``https://nutzer:pass@``, bricht das Skript mit Exit 2 ab, ohne etwas zu schreiben oder zu mailen.
Ordner-Override für Tests: ``TO_SPAWN_ABSCHLUSS_ORDNER``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
_SKRIPTE = str(Path(__file__).resolve().parent)
if _SKRIPTE not in sys.path:
    sys.path.insert(0, _SKRIPTE)
from test_uebersicht import lade_tickets  # noqa: E402

from to_spawn import config, melder  # noqa: E402

log = logging.getLogger("abschluss_paket")

#: Geheimnis-Muster in Argumenten: ``passwort=``, ``password:``, ``pw=`` — auch als Wortende
#: (``Zugangspasswort:``, ``meinPW=``) — oder ``nutzer:pass@`` mit oder ohne ``https://``.
_GEHEIM = re.compile(
    r"(?i)(?:(?:passwort|password|passwd|kennwort|pw)\s*[:=])"
    r"|(?:\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:[^/\s@]+@)"
    r"|(?:(?:^|[\s=])[^\s/:@=]+:[^\s/@]+@)"
)
_URL = re.compile(r"^https?://\S+$", re.I)


@dataclass
class Paket:
    """Alles, was in Datei und Mail steht."""

    spec: int
    stage: str
    rundschau: str
    tests: str
    saetze: list[str]
    belege: str = ""
    direkt: list[tuple[str, str]] = field(default_factory=list)
    basic_auth_nutzer: str = ""
    app_rollen: list[str] = field(default_factory=list)
    bitwarden: str = ""
    tun: list[str] = field(default_factory=list)
    stand: str = "abnahme"


def abschluss_ordner() -> Path:
    """Wurzel ``…/abschluss`` (Unterordner ``offen/`` und ``gezeigt/``)."""
    return Path(os.environ.get("TO_SPAWN_ABSCHLUSS_ORDNER") or Path.home() / ".claude" / "data" / "abschluss")


def geheimnis_in(argv: list[str]) -> str | None:
    """Schalter, in dessen Wert ein Passwort-Muster steckt — sonst ``None``. Nennt nie den Wert."""
    schalter = "ein Argument"
    for teil in argv:
        if teil.startswith("--") and "=" not in teil:
            schalter = teil
            continue
        if _GEHEIM.search(teil):
            return teil.split("=", 1)[0] if teil.startswith("--") else schalter
    return None


def _tickets(repo: Path, spec: int) -> list[tuple[str, str]]:
    try:
        return lade_tickets(repo, spec)
    except (OSError, ValueError, AttributeError) as fehler:
        log.warning("Manifest spec-%s nicht lesbar: %s", spec, fehler)
        return []


def neu_saetze(repo: Path, spec: int, neu: list[str]) -> list[str]:
    """Bis zu 3 Sätze „was ist neu“: ``--neu`` vor Ticket-Titeln aus dem Manifest."""
    saetze = [s.strip() for s in neu if s.strip()]
    if len(saetze) < 3:
        saetze += [f"Neu: {titel.strip()}." for _nr, titel in _tickets(repo, spec) if titel.strip()]
    return saetze[:3]


def tun_saetze(repo: Path, spec: int, tun: list[str], stand: str) -> list[str]:
    """Bis zu 3 Sätze „was du tun musst“: ``--tun`` oder Standard (Zettel, Abnahme-Ticket)."""
    eigene = [s.strip() for s in tun if s.strip()]
    if eigene:
        return eigene[:3]
    abnahme = next((nr for nr, titel in _tickets(repo, spec) if titel.strip().lower().startswith("abnahme")), "")
    if stand == "live":
        saetze = ["Die echte App kurz über die Direkt-Links durchklicken."]
        if abnahme:
            saetze.append(f"Passt alles: Abnahme-Ticket #{abnahme} schließen.")
        return saetze
    saetze = [
        "Die Test-App über die Direkt-Links durchklicken.",
        "Passt alles: den Zettel für den Live-Deploy ausstellen.",
    ]
    if abnahme:
        saetze.append(f"Abnahme-Ticket #{abnahme} prüfen und abhaken.")
    return saetze


def markdown(spec: int, stage: str, rundschau: str, tests: str, saetze: list[str]) -> str:
    """Alte Kurzform: 3 Links + was neu ist."""
    zeilen = [
        f"# Spec {spec} fertig — 3 Links",
        "",
        f"1. **Stage-App** (auch am Handy): {stage}",
        f"2. **Rundschau**: {rundschau}",
        f"3. **Test-Übersicht**: {tests}",
        "",
        "## Was ist neu",
        "",
        *[f"- {s}" for s in saetze],
        "",
    ]
    return "\n".join(zeilen)


def betreff(p: Paket, neue_form: bool) -> str:
    """Betreff: alte Kurzform, „bereit zur Abnahme“ oder „live“."""
    if not neue_form:
        return f"Spec {p.spec} fertig — 3 Links"
    if p.stand == "live":
        return f"Spec {p.spec} live — alles zum Durchschauen"
    return f"Spec {p.spec} bereit zur Abnahme — alles zum Durchschauen"


def markdown_voll(p: Paket) -> str:
    """Neue Form: 5 nummerierte Punkte, dann „Was ist neu“ und „Was du tun musst“."""
    direkt = [f"   - {titel}: {url}" for titel, url in p.direkt] or ["   - (keine angegeben)"]
    zugang = [f"   - Stage-App: {p.stage}"]
    if p.basic_auth_nutzer:
        zugang.append(f"   - Anmelde-Name (Basic-Auth): {p.basic_auth_nutzer}")
    zugang += [f"   - App-Rolle: {rolle}" for rolle in p.app_rollen]
    if p.bitwarden:
        zugang.append(f"   - Passwort: Bitwarden-Eintrag {p.bitwarden}")
    zeilen = [
        f"# {betreff(p, True)}",
        "",
        f"1. **Rundschau** (was gebaut wurde, in einfachen Worten): {p.rundschau}",
        f"2. **Belegseiten** (je Ticket der Nachweis): {p.belege or '(noch keine)'}",
        f"3. **Test-Übersicht** (welche Prüfungen grün sind): {p.tests}",
        "4. **Direkt-Links** (springen an die richtige Stelle, auch am Handy):",
        *direkt,
        "5. **Zugang**:",
        *zugang,
        "",
        "## Was ist neu",
        "",
        *[f"- {s}" for s in p.saetze],
        "",
        "## Was du tun musst",
        "",
        *[f"- {s}" for s in p.tun],
        "",
    ]
    return "\n".join(zeilen)


def _direkt(roh: list[str], ap: argparse.ArgumentParser) -> list[tuple[str, str]]:
    """``"<Titel>=<url>"`` zerlegen; falsches Format → Exit 2 mit klarer Meldung."""
    paare: list[tuple[str, str]] = []
    for eintrag in roh:
        titel, gleich, url = eintrag.partition("=")
        titel, url = titel.strip(), url.strip()
        if not gleich or not titel or not _URL.match(url):
            ap.error(f"--direkt „{eintrag}“: erwartet „<Titel>=<url>“ mit Titel und Link ab http:// oder https://")
        paare.append((titel, url))
    return paare


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Abschluss-Paket einer Spec (alles zum Durchschauen an David).")
    ap.add_argument("spec", type=int)
    ap.add_argument("--stage", required=True, help="Stage-App-Link (staging.url)")
    ap.add_argument("--rundschau", required=True, help="Link der veröffentlichten Rundschau")
    ap.add_argument("--tests", required=True, help="Link der veröffentlichten Test-Übersicht")
    ap.add_argument("--belege", default="", help="Link der veröffentlichten Belegseiten-Übersicht")
    ap.add_argument("--direkt", action="append", default=[], help="Direkt-Link „<Titel>=<url>“ (mehrfach)")
    ap.add_argument("--basic-auth-nutzer", default="", help="Anmelde-Name der Stage (nie das Passwort)")
    ap.add_argument("--app-rolle", action="append", default=[], help="App-Rolle „<Name (rolle)>“ (mehrfach)")
    ap.add_argument("--bitwarden", default="", help="Name des Bitwarden-Eintrags mit dem Passwort")
    ap.add_argument("--neu", action="append", default=[], help="Satz „was ist neu“ (bis 3×)")
    ap.add_argument("--tun", action="append", default=[], help="Satz „was du tun musst“ (bis 3×)")
    ap.add_argument("--stand", choices=("abnahme", "live"), default="abnahme")
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true", help="nichts schreiben, nichts mailen")
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stelle = geheimnis_in(argv)
    if stelle:
        sys.stderr.write(
            f"FEHLER: {stelle} enthält ein Passwort — die Mail nennt nur Namen. Passwort weglassen, "
            "nur den Bitwarden-Eintrag nennen (--bitwarden). Nichts geschrieben, nichts gemailt.\n"
        )
        return 2
    ap = _parser()
    a = ap.parse_args(argv)
    direkt = _direkt(a.direkt, ap)
    repo = (
        a.repo or (Path(os.environ["TO_SPAWN_REPO"]) if os.environ.get("TO_SPAWN_REPO") else config.repo_wurzel())
    ).resolve()
    projekt = repo.name
    rollen = [r.strip() for r in a.app_rolle if r.strip()]
    neue_form = bool(a.belege or direkt or a.basic_auth_nutzer or rollen or a.bitwarden or a.tun or a.stand == "live")
    p = Paket(
        spec=a.spec,
        stage=a.stage,
        rundschau=a.rundschau,
        tests=a.tests,
        saetze=neu_saetze(repo, a.spec, a.neu),
        belege=a.belege,
        direkt=direkt,
        basic_auth_nutzer=a.basic_auth_nutzer.strip(),
        app_rollen=rollen,
        bitwarden=a.bitwarden.strip(),
        tun=tun_saetze(repo, a.spec, a.tun, a.stand),
        stand=a.stand,
    )
    text = markdown_voll(p) if neue_form else markdown(p.spec, p.stage, p.rundschau, p.tests, p.saetze)
    endung = "_live" if p.stand == "live" else ""
    md_datei = repo / "docs" / "agents" / f"abschluss_{p.spec}{endung}.md"
    offen = abschluss_ordner() / "offen" / f"{projekt}_{p.spec}.json"
    daten: dict[str, Any] = {
        "projekt": projekt,
        "repo": str(repo),
        "spec": p.spec,
        "stage": p.stage,
        "rundschau": p.rundschau,
        "tests": p.tests,
        "belege": p.belege,
        "direkt": [{"titel": t, "url": u} for t, u in p.direkt],
        "stand": p.stand,
        "zugang": {"basic_auth_nutzer": p.basic_auth_nutzer, "app_rollen": p.app_rollen, "bitwarden": p.bitwarden},
        "erstellt": datetime.now().isoformat(timespec="seconds"),
    }
    konfig = config.lade(repo)
    titel = betreff(p, neue_form)
    if a.dry_run:
        mail = "ja" if melder.mail_eingerichtet(konfig) else "nein (mail.befehl leer)"
        sys.stdout.write(f"[Probe] {md_datei}\n{text}\n[Probe] {offen}\n[Probe] Mail „{titel}“: {mail}\n")
        return 0
    md_datei.parent.mkdir(parents=True, exist_ok=True)
    md_datei.write_text(text, encoding="utf-8")
    offen.parent.mkdir(parents=True, exist_ok=True)
    offen.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Abschluss geschrieben: %s · %s", md_datei, offen)
    if not melder.mail_eingerichtet(konfig):
        log.info("Mail nicht eingerichtet (mail.befehl leer) — nur Datei.")
        return 0
    if not melder.melden(repo, "spec_fertig", titel, text, f"abschluss_{p.spec}{endung}", konfig=konfig):
        log.warning("Mail „%s“ nicht verschickt (schon gesendet oder Befehl gescheitert).", titel)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
