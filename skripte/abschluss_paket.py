"""Abschluss-Paket einer Spec: alles zum Durchschauen an den Menschen (vor und nach dem Live-Deploy).

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
Das Skript schließt die Spec nicht: die Spec schließt nur ``to_spawn.py spec-schliessen <S>``
(prüft offene Spec- und Folge-Tickets, E2-4 #88).

Eine Mail je Spec (#586), zusätzlich zum Aufruf oben:

* ``abschluss_paket.py ablegen <S> <gleiche Argumente> [--spec-fertig <ISO>]`` baut das Paket
  (gleicher Geheimnis-Check), mailt nicht und legt es als ``.to-spawn/abschluss_<S>[_live]_paket.json`` ab.
  Eine schon abgelegte SPEC-FERTIG-Zeit bleibt (erste gewinnt). Exit 0, 2 = Passwort/falsches Argument.
* ``abschluss_paket.py nachsehen <S> [--stand abnahme|live] [--jetzt <ISO>] [--repo <pfad>] [--dry-run]`` verschickt die eine
  Mail (Spec-Teil mit „Je Ticket“ + „Wirkungskreis“ → System-Teil = Rückblick-Marker
  ``docs/agents/rueckblick_<S>.md`` → Code-Befunde = Thermo-Marker ``docs/agents/thermo_<S>.md``),
  sobald beide Marker da sind; fehlt einer ``WARTE_MINUTEN`` (= Thermo-Sperre, 120) nach SPEC FERTIG noch, geht sie mit
  Vermerk „Rückblick fehlgeschlagen: …“ raus. Gleicher Mail-Schlüssel wie oben.
  Exit 0 = verschickt/schon verschickt/Probe, 1 = Mail-Befehl gescheitert, 2 = keine Ablage,
  3 = wartet noch.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
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
from thermo_lauf import SPERRE_MIN, live_bereit_marker_pfad, live_bereit_pruefen, repo_ordner  # noqa: E402
from thermo_lauf import marker_pfad as thermo_marker_pfad  # noqa: E402

from to_spawn import (  # noqa: E402
    bau_log,
    befund,
    briefkasten,
    capo,
    config,
    gh,
    melder,
    prozessbaum,
    rundschau_deck,
    selbstpruefung,
    ticket_zustand,
)

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
    rundschau_datei: str = ""
    rundschau_inhalt: dict[str, Any] = field(default_factory=dict)
    live_bereit: str = ""
    selbstpruefung: dict[str, Any] = field(default_factory=dict)
    runde: int = 1


@dataclass(frozen=True)
class AbschlussStand:
    """Zustand des Abschluss-Pakets einer Spec — einzige Stelle für Runden-Logik (#370)."""

    runde: int
    stand: str
    faellig: bool
    naechste_runde: int
    neue_tickets: tuple[int, ...] = ()
    grund: str = ""


_FOLGE_NR = re.compile(r"Folge-Ticket\s+#(\d+)")


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


def _bau_tickets(repo: Path, spec: int) -> list[tuple[str, str]]:
    """Bau-Tickets der Spec aus dem Manifest (ohne reine Abnahme-Tickets)."""
    return [(nr, titel) for nr, titel in _tickets(repo, spec) if not titel.strip().lower().startswith("abnahme")]


def _inhalt_zeile(inhalt: dict[str, Any]) -> str:
    """Kurze Inhalts-Angabe der Rundschau (#367) oder leer, wenn nicht erhoben."""
    if not inhalt or "bilder" not in inhalt:
        return ""
    bilder = int(inhalt.get("bilder", 0))
    klick = int(inhalt.get("klickschritte", 0))
    ent = "ja" if inhalt.get("entscheidungen") else "nein"
    return f"{bilder} Folien mit Bild, {klick} Klick-Schrittfolgen, Entscheidungs-Folie: {ent}"


def _rundschau_text(rundschau: str, rundschau_datei: str = "", rundschau_inhalt: dict[str, Any] | None = None) -> str:
    """Rundschau-Zeile: Link plus Repo-Pfad und Inhalts-Angabe, wenn vorhanden (#367)."""
    zusatz: list[str] = []
    if rundschau_datei:
        zusatz.append(f"Datei im Repo: {rundschau_datei}")
    inhalt = _inhalt_zeile(rundschau_inhalt or {})
    if inhalt:
        zusatz.append(inhalt)
    return f"{rundschau} ({' · '.join(zusatz)})" if zusatz else rundschau


def markdown(
    spec: int,
    stage: str,
    rundschau: str,
    tests: str,
    saetze: list[str],
    *,
    rundschau_datei: str = "",
    rundschau_inhalt: dict[str, Any] | None = None,
    live_bereit: str = "",
    sp_daten: dict[str, Any] | None = None,
    runde: int = 1,
) -> str:
    """Alte Kurzform: 3 Links + was neu ist."""
    runde_zusatz = f" (Runde {runde})" if runde > 1 else ""
    zeilen = [
        f"# Spec {spec} fertig{runde_zusatz} — 3 Links",
        "",
        f"1. **Stage-App** (auch am Handy): {stage}",
        f"2. **Rundschau**: {_rundschau_text(rundschau, rundschau_datei, rundschau_inhalt)}",
        f"3. **Test-Übersicht**: {tests}",
        *(["", f"**Bereit für Live?**: {live_bereit}"] if live_bereit else []),
        "",
        "## Was ist neu",
        "",
        *[f"- {s}" for s in saetze],
        "",
        *selbstpruefung.paket_abschnitt(sp_daten or {}),
    ]
    return "\n".join(zeilen)


def betreff(p: Paket, neue_form: bool) -> str:
    """Betreff: alte Kurzform, „bereit zur Abnahme“ (mit Runden-Zahl ab Runde 2, #370) oder „live“."""
    runde_zusatz = f" (Runde {p.runde})" if p.runde > 1 and p.stand != "live" else ""
    if not neue_form:
        return f"Spec {p.spec} fertig{runde_zusatz} — 3 Links"
    if p.stand == "live":
        return f"Spec {p.spec} live — alles zum Durchschauen"
    return f"Spec {p.spec} bereit zur Abnahme{runde_zusatz} — alles zum Durchschauen"


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
        f"1. **Rundschau** (was gebaut wurde, in einfachen Worten): {_rundschau_text(p.rundschau, p.rundschau_datei, p.rundschau_inhalt)}",
        f"2. **Belegseiten** (je Ticket der Nachweis): {p.belege or '(noch keine)'}",
        f"3. **Test-Übersicht** (welche Prüfungen grün sind): {p.tests}",
        "4. **Direkt-Links** (springen an die richtige Stelle, auch am Handy):",
        *direkt,
        "5. **Zugang**:",
        *zugang,
        *(["", f"**Bereit für Live?**: {p.live_bereit}"] if p.live_bereit else []),
        "",
        "## Was ist neu",
        "",
        *[f"- {s}" for s in p.saetze],
        "",
        "## Was du tun musst",
        "",
        *[f"- {s}" for s in p.tun],
        "",
        *selbstpruefung.paket_abschnitt(p.selbstpruefung),
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
    ap = argparse.ArgumentParser(description="Abschluss-Paket einer Spec (alles zum Durchschauen an den Menschen).")
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
    ap.add_argument(
        "--ohne-rundschau",
        action="store_true",
        help="nur Notfall-Ablage des capo (--rundschau nennt dann den Grund): Deck-Pflicht entfällt",
    )
    ap.add_argument(
        "--rundschau-datei",
        type=Path,
        default=None,
        help="Rundschau-Deck als Datei (Pflicht bei --stand abnahme); Prüfung gegen to_spawn/rundschau_deck.py (#529), rot = Exit 4",
    )
    ap.add_argument("--stand", choices=("abnahme", "live"), default="abnahme")
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true", help="nichts schreiben, nichts mailen")
    return ap


def _geheimnis_abbruch(argv: list[str]) -> bool:
    """Passwort in einem Argument → Meldung auf stderr und ``True`` (Aufrufer endet mit Exit 2)."""
    stelle = geheimnis_in(argv)
    if stelle:
        sys.stderr.write(
            f"FEHLER: {stelle} enthält ein Passwort — die Mail nennt nur Namen. Passwort weglassen, "
            "nur den Bitwarden-Eintrag nennen (--bitwarden). Nichts geschrieben, nichts gemailt.\n"
        )
    return bool(stelle)


def _repo(roh: Path | None) -> Path:
    return (
        roh or (Path(os.environ["TO_SPAWN_REPO"]) if os.environ.get("TO_SPAWN_REPO") else config.repo_wurzel())
    ).resolve()


def _paket(a: argparse.Namespace, ap: argparse.ArgumentParser, repo: Path) -> tuple[Paket, bool]:
    """Paket aus den Argumenten bauen; zweiter Wert: neue Form (sonst alte Kurzform „3 Links“)."""
    direkt = _direkt(a.direkt, ap)
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
    return p, neue_form


def _endung(p: Paket) -> str:
    return "_live" if p.stand == "live" else ""


def _md_datei(repo: Path, p: Paket) -> Path:
    return repo_ordner(repo, "agenten") / f"abschluss_{p.spec}{_endung(p)}.md"


def _offen_datei(repo: Path, p: Paket) -> Path:
    return abschluss_ordner() / "offen" / f"{repo.name}_{p.spec}.json"


def _offen_daten(repo: Path, p: Paket) -> dict[str, Any]:
    return {
        "projekt": repo.name,
        "repo": str(repo),
        "spec": p.spec,
        "stage": p.stage,
        "rundschau": p.rundschau,
        "rundschau_datei": p.rundschau_datei,
        "rundschau_inhalt": p.rundschau_inhalt,
        "selbstpruefung": p.selbstpruefung,
        "tests": p.tests,
        "belege": p.belege,
        "direkt": [{"titel": t, "url": u} for t, u in p.direkt],
        "stand": p.stand,
        "runde": p.runde,
        "live_bereit": p.live_bereit,
        "zugang": {"basic_auth_nutzer": p.basic_auth_nutzer, "app_rollen": p.app_rollen, "bitwarden": p.bitwarden},
        "erstellt": datetime.now().isoformat(timespec="seconds"),
    }


def _probe(md_datei: Path, text: str, offen: Path, titel: str, konfig: dict[str, Any]) -> None:
    mail = "ja" if melder.mail_eingerichtet(konfig) else "nein (mail.befehl leer)"
    sys.stdout.write(f"[Probe] {md_datei}\n{text}\n[Probe] {offen}\n[Probe] Mail „{titel}“: {mail}\n")


def _schreiben(md_datei: Path, text: str, offen: Path, daten: dict[str, Any]) -> None:
    md_datei.parent.mkdir(parents=True, exist_ok=True)
    md_datei.write_text(text, encoding="utf-8")
    offen.parent.mkdir(parents=True, exist_ok=True)
    offen.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Abschluss geschrieben: %s · %s", md_datei, offen)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if argv[:1] == ["rundschau-auftrag"]:
        return rundschau_auftrag(argv[1:])
    if argv[:1] == ["selbstpruefung"]:
        return selbstpruefung_cli(argv[1:])
    if argv[:1] == ["nachbesserung"]:
        return nachbesserung(argv[1:])
    if argv[:1] == ["stand"]:
        return stand_cli(argv[1:])
    if argv[:1] == ["ablegen"]:
        return ablegen(argv[1:])
    if argv[:1] == ["nachsehen"]:
        return nachsehen(argv[1:])
    if argv[:1] == ["morgen-bericht"]:
        return morgen_bericht_cli(argv[1:])
    if _geheimnis_abbruch(argv):
        return 2
    ap = _parser()
    a = ap.parse_args(argv)
    repo = _repo(a.repo)
    p, neue_form = _paket(a, ap, repo)
    if live_bereit_marker_pfad(repo, p.spec).is_file():
        p = replace(p, live_bereit=live_bereit_pruefen(repo, p.spec).zeile)
    text = (
        markdown_voll(p)
        if neue_form
        else markdown(
            p.spec,
            p.stage,
            p.rundschau,
            p.tests,
            p.saetze,
            rundschau_datei=p.rundschau_datei,
            rundschau_inhalt=p.rundschau_inhalt,
            live_bereit=p.live_bereit,
            sp_daten=p.selbstpruefung,
            runde=p.runde,
        )
    )
    md_datei, offen = _md_datei(repo, p), _offen_datei(repo, p)
    konfig = config.lade(repo)
    titel = betreff(p, neue_form)
    if a.dry_run:
        _probe(md_datei, text, offen, titel, konfig)
        return 0
    _schreiben(md_datei, text, offen, _offen_daten(repo, p))
    if not melder.mail_eingerichtet(konfig):
        log.info("Mail nicht eingerichtet (mail.befehl leer) — nur Datei.")
        return 0
    if not melder.darf_raus("spec_fertig", konfig):
        melder.melden(repo, "spec_fertig", titel, text, f"abschluss_{p.spec}{_endung(p)}", konfig=konfig)
        return 0
    if not melder.melden(repo, "spec_fertig", titel, text, f"abschluss_{p.spec}{_endung(p)}", konfig=konfig):
        log.warning("Mail „%s“ nicht verschickt (schon gesendet oder Befehl gescheitert).", titel)
        return 1
    return 0


# --- #586: eine Mail je Spec (ablegen bei SPEC FERTIG, nachsehen bis Rückblick + Thermo da) ---------

#: So lange wartet ``nachsehen`` nach SPEC FERTIG auf Rückblick- und Thermo-Marker, dann geht die
#: Mail mit Vermerk raus — ausdrücklich dieselbe Grenze wie die Thermo-Sperre (Grill E16).
WARTE_MINUTEN = SPERRE_MIN
# Echter Lauf über Spec 578 brauchte 137 s (graphify update + affected je Datei).
WIRKUNGSKREIS_ZEITLIMIT_S = 600


def ablage_pfad(repo: Path, spec: int, stand: str = "abnahme") -> Path:
    """Abgelegtes Paket einer Spec je Stand (schreibt ``ablegen``, liest ``nachsehen``)."""
    endung = "_live" if stand == "live" else ""
    return repo / ".to-spawn" / f"abschluss_{spec}{endung}_paket.json"


def _runde_aus_ablage(ablage: dict[str, Any]) -> int:
    """Runden-Zahl aus der Ablage (Vorgabe 1 für Bestands-Ablagen ohne Feld, #370)."""
    roh = ablage.get("runde")
    if roh is None and isinstance(ablage.get("paket"), dict):
        roh = ablage["paket"].get("runde")
    return roh if type(roh) is int and roh >= 1 else 1


def _int_menge(werte: Any) -> set[int]:
    if not isinstance(werte, list):
        return set()
    return {int(x) for x in werte if isinstance(x, (int, str)) and str(x).strip().isdigit()}


def _ablage_tickets(ablage: dict[str, Any]) -> set[int] | None:
    """Ticket-Snapshot der letzten Abnahme-Runde (``None`` bei Alt-Ablage ohne Feld)."""
    if "tickets" not in ablage or not isinstance(ablage.get("tickets"), list):
        return None
    return _int_menge(ablage["tickets"])


def _ablage_folge_tickets(ablage: dict[str, Any]) -> set[int]:
    return _int_menge(ablage.get("folge_tickets"))


def folge_tickets_vormerken(repo: Path, spec: int, tickets: Iterable[int]) -> Path | None:
    """Merkt Nachbesserungs-Folge-Tickets in der Abnahme-Ablage vor (#370); ``None`` ohne Ablage."""
    datei = ablage_pfad(repo, spec, "abnahme")
    ablage = melder.lade_json(datei)
    if not isinstance(ablage, dict) or not isinstance(ablage.get("paket"), dict):
        return None
    aktuell = _ablage_folge_tickets(ablage)
    neu = {int(t) for t in tickets if int(t) > 0}
    if not neu:
        return datei
    ablage["folge_tickets"] = sorted(aktuell | neu)
    ablage.setdefault("runde", _runde_aus_ablage(ablage))
    melder.speichere_json(datei, ablage)
    return datei


def abschluss_faellig(
    repo: Path,
    spec: int,
    issues: Sequence[dict[str, Any]] | None = None,
    *,
    checkpoint_label: str = "checkpoint:human",
    verstoesse: bool = False,
) -> AbschlussStand:
    """Einzige Stelle für Runden-Zustand und die Frage „Abschluss fällig?“ (#370).

    Der Aufseher fragt nur diese Funktion — sie kapselt die Ablage (``--stand abnahme`` /
    ``live``), den Runden-Zähler und die Erkennung neuer Folge-Tickets seit der letzten Runde.
    """
    ablage = melder.lade_json(ablage_pfad(repo, spec, "abnahme"))
    hat_abnahme = isinstance(ablage, dict) and isinstance(ablage.get("paket"), dict)
    hat_live = ablage_pfad(repo, spec, "live").is_file()
    runde = _runde_aus_ablage(ablage) if hat_abnahme else 0
    stand = "live" if hat_live else (str(ablage["paket"].get("stand") or "abnahme") if hat_abnahme else "")
    bekannte = _ablage_tickets(ablage) if hat_abnahme else None
    vorgemerkt = _ablage_folge_tickets(ablage) if hat_abnahme else set()

    manifest_eintraege = _tickets(repo, spec)
    manifest_nrs = {int(nr) for nr, _titel in manifest_eintraege if nr.isdigit()}
    manifest_abnahme = {
        int(nr) for nr, titel in manifest_eintraege if nr.isdigit() and titel.strip().lower().startswith("abnahme")
    }

    if issues is not None:
        issue_by_nr = {int(i["number"]): i for i in issues if str(i.get("number", "")).isdigit()}
        cp_nrs = {nr for nr, i in issue_by_nr.items() if checkpoint_label in capo.label_namen(i)}
        bau_nrs = {nr for nr in issue_by_nr if nr not in cp_nrs}
        offene_bau = {nr for nr in bau_nrs if not ticket_zustand.fertig_gebaut(issue_by_nr[nr])}
        if hat_abnahme:
            neue: set[int] = set(vorgemerkt)
            if bekannte is not None:
                neue |= set(issue_by_nr) - bekannte - cp_nrs
                neue |= manifest_nrs - bekannte - manifest_abnahme - cp_nrs
            neue_sortiert = tuple(sorted(neue))
            if verstoesse:
                return AbschlussStand(
                    runde, stand, False, runde + 1 if neue else runde, neue_sortiert, "offene capo-Verstöße"
                )
            if hat_live and not neue:
                return AbschlussStand(runde, stand, False, runde, (), "Spec ist bereits live")
            if neue:
                alle_neue_fertig = all(
                    nr in issue_by_nr and ticket_zustand.fertig_gebaut(issue_by_nr[nr]) for nr in neue
                )
                faellig = alle_neue_fertig and not offene_bau
                naechste = runde + 1
                liste_txt = ", ".join(f"#{n}" for n in neue_sortiert)
                grund = (
                    f"Folge-Ticket(s) {liste_txt} fertig — Abschluss Runde {naechste} fällig"
                    if faellig
                    else f"Folge-Ticket(s) {liste_txt} noch offen"
                )
                return AbschlussStand(runde, stand, faellig, naechste, neue_sortiert, grund)
            return AbschlussStand(runde, stand, False, runde, (), f"Abnahme Runde {runde} liegt vor")
        if hat_live:
            return AbschlussStand(0, stand, False, 1, (), "Spec ist bereits live")
        if verstoesse:
            return AbschlussStand(0, stand, False, 1, (), "offene capo-Verstöße")
        if bau_nrs and not offene_bau:
            return AbschlussStand(0, stand, True, 1, (), "alle Bau-Tickets fertig — Abschluss Runde 1 fällig")
        return AbschlussStand(0, stand, False, 1, (), "Bau-Tickets noch offen")

    if hat_abnahme:
        neue = set(vorgemerkt)
        if bekannte is not None:
            neue |= manifest_nrs - bekannte - manifest_abnahme
        neue_sortiert = tuple(sorted(neue))
        if neue:
            naechste = runde + 1
            liste_txt = ", ".join(f"#{n}" for n in neue_sortiert)
            return AbschlussStand(
                runde,
                stand,
                True,
                naechste,
                neue_sortiert,
                f"Folge-Ticket(s) {liste_txt} seit Runde {runde} — Abschluss Runde {naechste} fällig",
            )
        return AbschlussStand(runde, stand, False, runde, (), f"Abnahme Runde {runde} liegt vor")
    return AbschlussStand(0, stand, False, 1, (), "noch kein Abnahme-Paket abgelegt")


def rueckblick_pfad(repo: Path, spec: int) -> Path:
    """Marker des Rückblicks (#581/#585)."""
    return repo_ordner(repo, "agenten") / f"rueckblick_{spec}.md"


def _zeit(text: str | None, ap: argparse.ArgumentParser, schalter: str) -> datetime:
    """ISO-Zeitpunkt (ohne Zone = Ortszeit) oder jetzt; falsches Format → Exit 2."""
    if not text:
        return datetime.now().astimezone()
    try:
        return datetime.fromisoformat(text).astimezone()
    except ValueError:
        ap.error(f"{schalter} „{text}“: erwartet ISO-Zeitpunkt wie 2026-10-06T10:00:00+02:00")


def rundschau_auftrag_pfad(repo: Path, spec: int) -> Path:
    """Ziel-Datei für den Rundschau-Auftrag einer Spec (#367)."""
    return repo_ordner(repo, "agenten") / f"rundschau_auftrag_{spec}.md"


def rundschau_ziel_rel(repo: Path, spec: int) -> str:
    """Standard-Pfad der Rundschau-HTML-Datei im Repo (relativ als POSIX)."""
    pfad = repo_ordner(repo, "agenten") / f"rundschau_{spec}.html"
    try:
        return pfad.relative_to(repo).as_posix()
    except ValueError:
        return pfad.as_posix()


def rundschau_auftrag_text(
    repo: Path,
    spec: int,
    stage: str,
    direkt: list[tuple[str, str]],
    *,
    runde: int = 1,
    neue_tickets: Sequence[int] = (),
) -> str:
    """Markdown-Auftrag für die Abnahme-Rundschau einer Spec (#367, Runden #370)."""
    ziel_html = rundschau_ziel_rel(repo, spec)
    stage_zeile = stage or "(staging.url fehlt in .to-spawn/config.json)"
    tickets = _tickets(repo, spec)
    genutzt: set[int] = set()
    ticket_bloecke: list[str] = []
    for nr, titel in tickets:
        titel_sauber = titel.strip() or "ohne Titel"
        passend: list[str] = []
        for idx, (d_titel, d_url) in enumerate(direkt):
            if re.search(rf"(?<!\d)#{re.escape(nr)}(?!\d)", d_titel) or (
                titel_sauber.lower() != "ohne titel" and titel_sauber.lower() in d_titel.lower()
            ):
                passend.append(f"{d_titel}: {d_url}")
                genutzt.add(idx)
        link_text = (
            " · ".join(passend)
            if passend
            else (f"{stage} (Route für #{nr} ergänzen)" if stage else "(Direkt-Link in die Staging-App ergänzen)")
        )
        if titel_sauber.lower().startswith("abnahme"):
            ticket_bloecke.extend(
                [
                    f"### Ticket #{nr} — {titel_sauber} (Abnahme-Ticket)",
                    f"- **Direkt-Link in die Staging-App**: {link_text}",
                    '- **Hinweis**: gehört auf die Pflicht-Folie „Offenes“ (Teil 2, `data-art="offenes"`).',
                    "",
                ]
            )
        else:
            ticket_bloecke.extend(
                [
                    f"### Ticket #{nr} — {titel_sauber}",
                    f"- **Direkt-Link in die Staging-App**: {link_text}",
                    f'- **Pflicht-Folie**: Bild + Klick-Schritte (`data-ticket="{nr}"`)',
                    "  - **Bild**: echter Screenshot aus Staging (Browser-Blick, mind. 200×120 px)",
                    "  - **Klick-Schritte**: nummerierte Schrittfolge (`ol.klickschritte`) zum Nachklicken auf Staging",
                    "",
                ]
            )
    if not ticket_bloecke:
        ticket_bloecke = ["- (keine Tickets im Manifest gefunden)", ""]
    weitere = [f"- {d_titel}: {d_url}" for idx, (d_titel, d_url) in enumerate(direkt) if idx not in genutzt]
    weitere_block = ["### Weitere Direkt-Links", "", *weitere, ""] if weitere else []

    ent_link = entscheidungen_link(repo, spec)
    ent_zeilen = entscheidungs_zeilen(repo, spec)
    if ent_zeilen:
        ent_block = [
            "| Ticket | was | warum | Quelle |",
            "|---|---|---|---|",
            *ent_zeilen,
        ]
    else:
        ent_block = ["- Keine Entscheidungen in diesem Bau-Verlauf."]

    runde_zusatz = f" (Runde {runde})" if runde > 1 else ""
    folge_zeile = (
        [f"- **Nachbesserungs-Runde**: Runde {runde} (neue Folge-Tickets: {', '.join(f'#{n}' for n in neue_tickets)})"]
        if runde > 1 and neue_tickets
        else ([f"- **Nachbesserungs-Runde**: Runde {runde}"] if runde > 1 else [])
    )
    zeilen = [
        f"# Rundschau-Auftrag Spec {spec}{runde_zusatz}",
        "",
        f"- **Stage-App**: {stage_zeile}",
        *folge_zeile,
        f"- **Ziel-Datei im Repo**: `{ziel_html}` (Pflicht für `abschluss_paket.py ablegen --stand abnahme --rundschau-datei {ziel_html}`)",
        "- **Aufbau**: Definition allein in `to_spawn/rundschau_deck.py` (Teil 1 Nutzer-Präsentation, Teil 2 Details mit Bild + Klick-Schritten + „Offenes“, Teil 3 Entscheidungs-Zeitstrahl).",
        "",
        "## Tickets und Pflicht-Folien (Bild + Klick-Schritte)",
        "",
        *ticket_bloecke,
        *weitere_block,
        "## Pflicht-Folie „Für dich entschieden“",
        "",
        f"Quelle: `{ent_link}`",
        "",
        *ent_block,
        "",
        "## Abgabe",
        "",
        f"1. Rundschau mit Skill `rundschau` und `skripte/rundschau_deck.py erzeugen` bauen und unter `{ziel_html}` im Repo speichern.",
        f"2. Mit `skripte/abschluss_paket.py ablegen {spec} --stand abnahme … --rundschau-datei {ziel_html}` ablegen (prüft Bild + Klick-Schritte je Bau-Ticket, Entscheidungs-Folie und Repo-Pfad).",
        "",
    ]
    return "\n".join(zeilen)


def rundschau_auftrag(argv: list[str]) -> int:
    """Schreibt den Rundschau-Auftrag einer Spec (#367, Runden #370) und gibt den Dateipfad auf stdout aus."""
    if _geheimnis_abbruch(argv):
        return 2
    ap = argparse.ArgumentParser(
        description="Rundschau-Auftrag einer Spec (Tickets, Direkt-Links, Pflicht-Folien, Entscheidungen)."
    )
    ap.add_argument("spec", type=int)
    ap.add_argument("--stage", default="", help="Stage-App-Link (Vorgabe: staging.url aus .to-spawn/config.json)")
    ap.add_argument("--direkt", action="append", default=[], help="Direkt-Link „<Titel>=<url>“ (mehrfach)")
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--aus", type=Path, default=None, help="Ziel-Datei (Vorgabe docs/agents/rundschau_auftrag_<S>.md)")
    a = ap.parse_args(argv)
    direkt = _direkt(a.direkt, ap)
    repo = _repo(a.repo)
    konfig = config.lade(repo)
    stage = a.stage.strip() or str((konfig.get("staging") or {}).get("url") or "").strip()
    # Entscheidungs-Liste sicherstellen, damit die Pflicht-Folie „Für dich entschieden“ vollständig ist (#364/#367).
    if entscheidungs_daten(repo, a.spec) or not capo.entscheidungs_datei(repo, a.spec).is_file():
        _entscheidungen_schreiben(repo, a.spec)
    stand = abschluss_faellig(repo, a.spec)
    ziel = a.aus or rundschau_auftrag_pfad(repo, a.spec)
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(
        rundschau_auftrag_text(
            repo,
            a.spec,
            stage,
            direkt,
            runde=stand.naechste_runde,
            neue_tickets=stand.neue_tickets,
        ),
        encoding="utf-8",
    )
    log.info("Rundschau-Auftrag geschrieben: %s", ziel)
    sys.stdout.write(f"{ziel}\n")
    return 0


def selbstpruefung_cli(argv: list[str]) -> int:
    """Unterbefehle ``selbstpruefung planen|sammeln <S>`` (#368, Runden #370)."""
    if _geheimnis_abbruch(argv):
        return 2
    ap = argparse.ArgumentParser(
        description="KI-Selbstprüfung der Rundschau gegen Staging planen und Ergebnis sammeln (#368)."
    )
    unter = ap.add_subparsers(dest="aktion", required=True)
    p_plan = unter.add_parser("planen")
    p_plan.add_argument("spec", type=int)
    p_plan.add_argument("--stage", default="", help="Stage-App-Link (Vorgabe: staging.url aus .to-spawn/config.json)")
    p_plan.add_argument(
        "--rundschau-datei",
        type=Path,
        default=None,
        help="Rundschau-HTML-Datei (Vorgabe: docs/agents/rundschau_<S>.html)",
    )
    p_plan.add_argument("--neu", action="store_true", help="Auch nach grünem Marker erneut planen")
    p_plan.add_argument("--repo", type=Path, default=None)
    p_sam = unter.add_parser("sammeln")
    p_sam.add_argument("spec", type=int)
    p_sam.add_argument("--repo", type=Path, default=None)
    a = ap.parse_args(argv)
    repo = _repo(a.repo)
    try:
        if a.aktion == "planen":
            stand = abschluss_faellig(repo, a.spec)
            ziel_runde = stand.naechste_runde if stand.naechste_runde > 1 else 1
            return selbstpruefung.planen(
                repo,
                a.spec,
                stage=a.stage,
                rundschau_datei=a.rundschau_datei,
                neu=a.neu or ziel_runde > 1,
                runde=ziel_runde,
            )
        return selbstpruefung.sammeln(repo, a.spec)
    except selbstpruefung.Fehler as fehler:
        log.error("%s", fehler)
        sys.stderr.write(f"FEHLER: {fehler}\n")
        return 1


def nachbesserung(argv: list[str]) -> int:
    """Nachbesserungs-Punkte nach Abnahme je als Folge-Ticket an derselben Spec anlegen (#370).

    Nutzt :func:`to_spawn.befund.melde` (idempotent, ergänzt Manifest und hängt als Sub-Issue an)
    und merkt die Folge-Tickets in der Abnahme-Ablage für die nächste Runde vor.
    """
    if _geheimnis_abbruch(argv):
        return 2
    ap = argparse.ArgumentParser(
        description="Nachbesserung nach Abnahme: je Punkt ein Folge-Ticket an derselben Spec anlegen (#370)."
    )
    ap.add_argument("spec", type=int)
    ap.add_argument(
        "--punkt",
        action="append",
        required=True,
        help="Nachbesserungs-Punkt aus dem Nutzer-Kommentar (mehrfach für mehrere Punkte)",
    )
    ap.add_argument("--ticket", type=int, default=None, help="Ursprungs-Ticket (Vorgabe: Spec-Nummer)")
    ap.add_argument("--gh-repo", default="", help="GitHub-Repo owner/name (Vorgabe: aus origin)")
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true", help="nur zeigen, nichts anlegen oder schreiben")
    a = ap.parse_args(argv)
    punkte = [p.strip() for p in a.punkt if p.strip()]
    if not punkte:
        ap.error("--punkt darf nicht leer sein")
    repo = _repo(a.repo)
    gh_repo = a.gh_repo.strip() or gh.repo_aus_origin(repo, fallback="")
    if not gh_repo:
        sys.stderr.write("FEHLER: GitHub-Repo unbekannt — --gh-repo owner/name angeben.\n")
        return 2
    stand = abschluss_faellig(repo, a.spec)
    runde = max(1, stand.runde)
    ursprung = a.ticket or a.spec
    angelegt: list[int] = []
    ok = True
    for punkt in punkte:
        text = f"Nachbesserung (Abnahme Runde {runde}): {punkt}"
        erg = befund.melde(repo, gh_repo, a.spec, ursprung, "gelb", text, dry_run=a.dry_run)
        for zeile in erg.zeilen:
            sys.stdout.write(f"{zeile}\n")
        if not erg.ok:
            ok = False
        treffer = _FOLGE_NR.search(" | ".join(erg.zeilen))
        if treffer:
            angelegt.append(int(treffer.group(1)))
    if not a.dry_run and angelegt:
        folge_tickets_vormerken(repo, a.spec, angelegt)
    return 0 if ok else 1


def stand_cli(argv: list[str]) -> int:
    """Runden-Zustand und „Abschluss fällig?“ einer Spec anzeigen (#370)."""
    ap = argparse.ArgumentParser(description="Runden-Zustand des Abschluss-Pakets einer Spec anzeigen (#370).")
    ap.add_argument("spec", type=int)
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--json", dest="als_json", action="store_true", help="Ausgabe als JSON")
    a = ap.parse_args(argv)
    repo = _repo(a.repo)
    stand = abschluss_faellig(repo, a.spec)
    if a.als_json:
        daten = asdict(stand)
        daten["neue_tickets"] = list(stand.neue_tickets)
        sys.stdout.write(json.dumps(daten, ensure_ascii=False, indent=2) + "\n")
    else:
        status = "fällig" if stand.faellig else "nicht fällig"
        sys.stdout.write(
            f"Spec #{a.spec} · Stand: {stand.stand or 'offen'} · Runde {stand.runde} · "
            f"Abschluss {status} (nächste Runde: {stand.naechste_runde}) — {stand.grund}\n"
        )
    return 0


def ablegen(argv: list[str]) -> int:
    """Paket bauen und ablegen, nicht mailen. Eine schon abgelegte SPEC-FERTIG-Zeit bleibt."""
    if _geheimnis_abbruch(argv):
        return 2
    ap = _parser()
    ap.add_argument("--spec-fertig", default=None, help="Zeitpunkt „SPEC FERTIG“ (ISO), sonst jetzt")
    ap.add_argument(
        "--neue-runde", action="store_true", help="Runden-Zähler für eine neue Abnahme-Runde erhöhen (#370)"
    )
    a = ap.parse_args(argv)
    repo = _repo(a.repo)
    p, neue_form = _paket(a, ap, repo)
    vor_stand = abschluss_faellig(repo, p.spec)
    if p.stand == "abnahme":
        if vor_stand.runde == 0:
            ziel_runde = 1
        elif vor_stand.neue_tickets or a.neue_runde:
            ziel_runde = vor_stand.runde + 1
        else:
            ziel_runde = vor_stand.runde
    else:
        ziel_runde = max(1, vor_stand.runde)
    rel_pfad = ""
    info: dict[str, Any] = {}
    live_bereit_zeile = ""
    sp_daten: dict[str, Any] = {}
    if a.dry_run:
        maengel: list[str] = []
    elif a.rundschau_datei is None and p.stand == "abnahme" and not a.ohne_rundschau:
        maengel = [
            f"`--rundschau-datei <html>` fehlt: ohne Rundschau-Deck im Repo "
            f"(z. B. {rundschau_ziel_rel(repo, p.spec)}, #367/#529) keine Abnahme."
        ]
    elif a.rundschau_datei is not None:
        maengel, rel_pfad, info = _rundschau_pruefen(repo, p.spec, a.rundschau_datei)
    else:
        maengel = []
    if not a.dry_run and p.stand == "abnahme":
        if a.ohne_rundschau:
            sp_daten = {
                "status": "nicht_moeglich",
                "grund": "Selbstprüfung nicht möglich, weil Notfall-Ablage ohne Rundschau-Deck.",
            }
        else:
            sp_maengel, sp_daten = selbstpruefung.pruefe_fuer_abnahme(
                repo,
                p.spec,
                min_runde=ziel_runde,
                pflicht_tickets=vor_stand.neue_tickets,
            )
            maengel = [*maengel, *sp_maengel]
    lb_maengel: list[str] = []
    if not a.dry_run and p.stand == "abnahme" and not a.ohne_rundschau:
        lb = live_bereit_pruefen(repo, p.spec)
        if lb.bereit:
            live_bereit_zeile = lb.zeile
        else:
            lb_maengel = lb.maengel
    elif not a.dry_run and live_bereit_marker_pfad(repo, p.spec).is_file():
        live_bereit_zeile = live_bereit_pruefen(repo, p.spec).zeile
    elif not a.dry_run and a.ohne_rundschau and p.stand == "abnahme":
        live_bereit_zeile = "nicht geprüft (Notfall-Ablage)"
    if maengel or lb_maengel:
        bloecke: list[str] = []
        if maengel:
            bloecke.append("Rundschau/Abnahme nicht bereit:\n" + "\n".join(f"- {m}" for m in maengel))
        if lb_maengel:
            bloecke.append("Prüfung „Bereit für Live?“ nicht bestanden:\n" + "\n".join(f"- {m}" for m in lb_maengel))
        sys.stdout.write("\n".join(bloecke) + "\n")
        return 4
    p = replace(p, runde=ziel_runde)
    if rel_pfad:
        p = replace(p, rundschau_datei=rel_pfad, rundschau_inhalt=info)
    if live_bereit_zeile:
        p = replace(p, live_bereit=live_bereit_zeile)
    if sp_daten:
        p = replace(p, selbstpruefung=sp_daten)
    if a.ohne_rundschau and a.rundschau_datei is None and p.stand == "abnahme":
        p = replace(p, rundschau=f"nicht abnahmebereit (ohne Rundschau-Deck) — {p.rundschau}")
    datei = ablage_pfad(repo, p.spec, p.stand)
    fertig = _zeit(a.spec_fertig, ap, "--spec-fertig").isoformat(timespec="seconds")
    alt = melder.lade_json(datei)
    if alt.get("spec_fertig"):
        fertig = str(alt["spec_fertig"])
    titel = betreff(p, neue_form)
    daten = {
        "paket": asdict(p),
        "neue_form": neue_form,
        "spec_fertig": fertig,
        "runde": p.runde,
        "titel": titel,
        "abgelegt_am": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tickets": sorted(ticket_nummern(repo, p.spec)),
        "folge_tickets": [],
    }
    if a.dry_run:
        sys.stdout.write(f"[Probe] {datei}\n{json.dumps(daten, ensure_ascii=False, indent=2)}\n")
        return 0
    melder.speichere_json(datei, daten)
    # Ab Runde 2 die Markdown-Ablage im Repo mit neuem Runden-Titel fortschreiben — ohne neue Mail/Meldung (#370).
    if p.stand == "abnahme" and p.runde > 1:
        text = (
            markdown_voll(p)
            if neue_form
            else markdown(
                p.spec,
                p.stage,
                p.rundschau,
                p.tests,
                p.saetze,
                rundschau_datei=p.rundschau_datei,
                rundschau_inhalt=p.rundschau_inhalt,
                live_bereit=p.live_bereit,
                sp_daten=p.selbstpruefung,
                runde=p.runde,
            )
        )
        md_datei = _md_datei(repo, p)
        md_datei.parent.mkdir(parents=True, exist_ok=True)
        md_datei.write_text(text, encoding="utf-8")
    # #421: Das Paket liegt schon vor „SPEC FERTIG“ (Abnahme offen) — die Entscheidungs-Liste
    # entsteht darum hier mit; capo schreibt sie bei „SPEC FERTIG“ nur noch einmal neu.
    liste = _entscheidungen_schreiben(repo, p.spec)
    log.info("Abschluss-Paket abgelegt: %s (Runde %s, SPEC FERTIG %s) · %s", datei, p.runde, fertig, liste)
    return 0


def entscheidungs_daten(repo: Path, spec: int) -> list[dict[str, str]]:
    """Entscheidungen der Spec für Rundschau-Grafik und -Prüfung (``briefkasten.liste``)."""
    return briefkasten.liste(sorted(ticket_nummern(repo, spec)), log_repo=repo, hauptbaum=repo)


def _rundschau_pruefen(repo: Path, spec: int, datei: Path) -> tuple[list[str], str, dict[str, Any]]:
    """Prüft Ort im Repo, Aufbau, Entscheidungen, Bilder & Klick-Schritte je Bau-Ticket (#367/#529)."""
    kandidat = datei if datei.is_absolute() else (repo / datei)
    if not kandidat.is_file() and not datei.is_absolute() and datei.is_file():
        kandidat = datei
    aufgeloest = kandidat.resolve()
    try:
        rel = aufgeloest.relative_to(repo.resolve()).as_posix()
    except ValueError:
        return (
            [
                f"Rundschau-Datei {datei} liegt außerhalb des Repos {repo.name} — "
                f"im Repo ablegen (z. B. {rundschau_ziel_rel(repo, spec)})."
            ],
            "",
            {},
        )
    try:
        text = aufgeloest.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"Rundschau-Datei nicht lesbar: {exc}"], rel, {}
    bau = [nr for nr, _titel in _bau_tickets(repo, spec)]
    maengel = rundschau_deck.pruefe(text, entscheidungs_daten(repo, spec), tickets=bau or None)
    info = rundschau_deck.inhalt(text)
    return maengel, rel, info


def _rundschau_maengel(repo: Path, spec: int, datei: Path) -> list[str]:
    """Prüft die Rundschau-Datei (``rundschau_deck.pruefe``) gegen die Entscheidungen der Spec."""
    return _rundschau_pruefen(repo, spec, datei)[0]


def _spec_tickets_aus_bau_logs(repo: Path, spec: int) -> list[int]:
    """Ohne Manifest: nur Bau-Logs mit ausdrücklichem ``spec``-Bezug auf diese Spec (#445)."""
    ziel = str(spec)
    gefunden: list[int] = []
    for t in bau_log.alle_tickets(repo):
        if not t.isdigit():
            continue
        try:
            zeilen = bau_log.lese(repo, t)
        except (OSError, ValueError) as fehler:
            log.warning("Bau-Log #%s nicht lesbar: %s", t, fehler)
            continue
        if any(str(z.get("spec") or "").strip() == ziel for z in zeilen):
            gefunden.append(int(t))
    return gefunden


def ticket_nummern(repo: Path, spec: int) -> list[int]:
    """Ticket-Nummern der Spec aus dem Manifest; ohne Manifest nur eindeutig dieser Spec zugeordnete Bau-Logs (#445)."""
    nummern = [int(nr) for nr, _titel in _tickets(repo, spec) if nr.isdigit()]
    if nummern:
        return nummern
    return _spec_tickets_aus_bau_logs(repo, spec)


def _entscheidungen_schreiben(repo: Path, spec: int) -> Path:
    """Schreibt ``entscheidungen_<S>.md``; markiert sie ohne Manifest klar als unvollständig (#445)."""
    hat_manifest = bool(_tickets(repo, spec))
    hinweis = (
        ""
        if hat_manifest
        else (
            f"unvollständig: Manifest `spec-{spec}.json` fehlt oder führt keine Tickets — "
            "nur eindeutig dieser Spec zugeordnete Bau-Logs berücksichtigt, fremde Tickets ausgelassen."
        )
    )
    return capo.uebersicht(repo, spec, ticket_nummern(repo, spec), hinweis=hinweis)


def entscheidungen_link(repo: Path, spec: int) -> str:
    """Pfad der Entscheidungs-Liste (``capo.entscheidungs_datei``), relativ zum Repo wo möglich."""
    datei = capo.entscheidungs_datei(repo, spec)
    try:
        return datei.relative_to(repo).as_posix()
    except ValueError:
        return str(datei)


def entscheidungs_zeilen(repo: Path, spec: int) -> list[str]:
    """Alle Tabellenzeilen der Entscheidungs-Liste (``capo.entscheidungs_datei``); ohne Datei leer."""
    datei = capo.entscheidungs_datei(repo, spec)
    try:
        text = datei.read_text(encoding="utf-8")
    except OSError:
        return []
    return [z for z in text.splitlines() if z.startswith("| #")]


def _ticket_zeilen(repo: Path, spec: int, konfig: dict[str, Any]) -> list[str]:
    """Je Ticket: Titel, Umfang aus der jüngsten Bau-Log-Zusammenfassung, Belegseiten (wie capo)."""
    ordner = capo.belege_ordner(konfig)
    zeilen = []
    for nr, titel in _tickets(repo, spec):
        try:
            umfang = str(bau_log.zusammenfassung(repo, nr).get("umfang_ist") or "").replace("\n", " ").strip()
            umfang = umfang or "kein Bau-Log"
        except (OSError, ValueError) as fehler:
            log.warning("Bau-Log #%s unlesbar: %s", nr, fehler)
            umfang = f"Bau-Log unlesbar: {fehler}"
        try:
            belege = ", ".join(capo.belegseiten(repo, int(nr), ordner=ordner)) or "Belegseite fehlt"
        except (OSError, ValueError) as fehler:
            log.warning("Belegseite #%s nicht ermittelt: %s", nr, fehler)
            belege = f"Belegseite nicht ermittelt: {fehler}"
        zeilen.append(f"- #{nr} {titel.strip()} — {umfang} · Vorher/Nachher: {belege}")
    return zeilen or ["- (keine Tickets im Manifest)"]


def wirkungskreis(repo: Path, spec: int) -> str:
    """Ausgabe von ``befehle.wirkungskreis`` (#583, L1 #46): Exit 0 = Text, Exit 2 = „nicht ermittelbar: …“."""
    roh = (config.lade(repo).get("befehle") or {}).get("wirkungskreis")
    name = roh.strip() if isinstance(roh, str) and roh.strip() else config.DEFAULTS["befehle"]["wirkungskreis"]
    skript = repo / name
    if not skript.is_file():
        return f"Wirkungskreis nicht ermittelt: {name} fehlt in {repo.name}"
    try:
        lauf = subprocess.run(
            [sys.executable, "-X", "utf8", str(skript), str(spec), "--repo", str(repo)],
            cwd=str(repo),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=WIRKUNGSKREIS_ZEITLIMIT_S,
            check=False,
            **prozessbaum.fensterlos(),
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        return f"Wirkungskreis nicht ermittelt: {fehler}"
    if lauf.returncode in (0, 2):
        return lauf.stdout.strip() or "Wirkungskreis nicht ermittelt: keine Ausgabe"
    return f"Wirkungskreis nicht ermittelt: Exit {lauf.returncode} — {(lauf.stderr or lauf.stdout).strip()[-300:]}"


def mail_text(repo: Path, p: Paket, neue_form: bool, konfig: dict[str, Any], system: str, befunde: str) -> str:
    """Die eine Mail: Spec-Teil → System-Teil (Rückblick) → Code-Befunde (Thermo)."""
    heute = (
        markdown_voll(p)
        if neue_form
        else markdown(
            p.spec,
            p.stage,
            p.rundschau,
            p.tests,
            p.saetze,
            rundschau_datei=p.rundschau_datei,
            rundschau_inhalt=p.rundschau_inhalt,
            live_bereit=p.live_bereit,
            sp_daten=p.selbstpruefung,
            runde=p.runde,
        )
    )
    eintraege = entscheidungs_zeilen(repo, p.spec)
    unvollstaendig = (
        [
            f"_Hinweis (unvollständig): Manifest `spec-{p.spec}.json` fehlt — fremde Bau-Logs wurden ausgelassen._",
            "",
        ]
        if not _tickets(repo, p.spec)
        else []
    )
    return "\n".join(
        [
            "## Spec-Teil",
            "",
            heute,
            "### Je Ticket",
            "",
            *_ticket_zeilen(repo, p.spec, konfig),
            "",
            "### Wirkungskreis",
            "",
            wirkungskreis(repo, p.spec),
            "",
            f"### Für dich entschieden · Entscheidungen ({len(eintraege)})",
            "",
            f"Alle Entscheidungen der Spec (Bau-Log + Briefkasten): {entscheidungen_link(repo, p.spec)}",
            "",
            *unvollstaendig,
            *eintraege,
            "",
            "## System-Teil",
            "",
            system.rstrip("\n"),
            "",
            "## Code-Befunde",
            "",
            befunde.rstrip("\n"),
            "",
        ]
    )


def nachsehen(argv: list[str]) -> int:
    """Mail verschicken, sobald Rückblick + Thermo da sind (oder WARTE_MINUTEN um sind).

    Exit 0 = verschickt/schon verschickt/Probe, 1 = Mail-Befehl gescheitert, 2 = keine Ablage,
    3 = wartet noch auf einen Marker.
    """
    ap = argparse.ArgumentParser(description="Abgelegtes Abschluss-Paket als eine Mail verschicken.")
    ap.add_argument("spec", type=int)
    ap.add_argument("--stand", choices=("abnahme", "live"), default="abnahme", help="welche Ablage")
    ap.add_argument("--jetzt", default=None, help="Zeitpunkt jetzt (ISO), sonst Uhr")
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true", help="Mailtext zeigen, nichts schreiben, nichts mailen")
    a = ap.parse_args(argv)
    repo = _repo(a.repo)
    datei = ablage_pfad(repo, a.spec, a.stand)
    ablage = melder.lade_json(datei)
    try:
        roh = dict(ablage["paket"])
        roh["direkt"] = [(str(t), str(u)) for t, u in roh.get("direkt") or []]
        p = Paket(**roh)
        fertig = datetime.fromisoformat(str(ablage["spec_fertig"])).astimezone()
    except (KeyError, TypeError, ValueError) as fehler:
        sys.stderr.write(
            f"FEHLER: keine gültige Ablage {datei} ({fehler!r}) — erst „abschluss_paket.py ablegen {a.spec} …“.\n"
        )
        return 2
    schluessel = f"abschluss_{p.spec}{_endung(p)}"
    if not a.dry_run and melder.schon_gesendet(repo, schluessel):
        log.info("Mail zu %s schon verschickt — nichts zu tun.", schluessel)
        return 0
    jetzt = _zeit(a.jetzt, ap, "--jetzt")
    minuten = (jetzt - fertig).total_seconds() / 60
    if minuten < 0:
        log.warning("SPEC FERTIG %s liegt nach jetzt %s — zähle als 0 Minuten.", fertig, jetzt)
        minuten = 0.0
    marker = {
        "System": ("Rückblick-Marker", rueckblick_pfad(repo, p.spec)),
        "Code": ("Thermo-Marker", thermo_marker_pfad(repo, p.spec)),
    }
    fehlen = [name for name, (_art, pfad) in marker.items() if not pfad.is_file()]
    if fehlen and minuten < WARTE_MINUTEN:
        namen = ", ".join(str(marker[n][1].relative_to(repo)) for n in fehlen)
        sys.stdout.write(f"wartet: {namen} fehlt noch ({int(minuten)} von {WARTE_MINUTEN} Minuten nach SPEC FERTIG)\n")
        return 3
    unlesbar: set[str] = set()

    def teil(name: str) -> str:
        """Marker-Inhalt unverändert oder Vermerk — nie ein Absturz, sonst gäbe es nie eine Mail (E14)."""
        art, pfad = marker[name]
        pfad_rel = pfad.relative_to(repo).as_posix()
        if name in fehlen:
            return f"Rückblick fehlgeschlagen: {art} {pfad_rel} fehlt {WARTE_MINUTEN} Minuten nach SPEC FERTIG"
        try:
            return pfad.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as fehler:
            log.warning("%s %s unlesbar: %s", art, pfad_rel, fehler)
            unlesbar.add(name)
            return f"Rückblick fehlgeschlagen: {art} {pfad_rel} unlesbar: {fehler}"

    system = teil("System")
    if "System" not in fehlen and "System" not in unlesbar:
        system = f"{system.rstrip()}\n\nAbhaken per Chat: „Rückblick {p.spec}: 1 ja, 2 nein“"
    konfig = config.lade(repo)
    neue_form = bool(ablage.get("neue_form"))
    text = mail_text(repo, p, neue_form, konfig, system, teil("Code"))
    titel = betreff(p, neue_form)
    md_datei, offen = _md_datei(repo, p), _offen_datei(repo, p)
    if a.dry_run:
        _probe(md_datei, text, offen, titel, konfig)
        return 0
    _schreiben(md_datei, text, offen, _offen_daten(repo, p))
    if not melder.mail_eingerichtet(konfig):
        log.warning("Mail nicht eingerichtet (mail.befehl leer) — nur Datei %s, keine Mail.", md_datei)
        return 0
    if not melder.darf_raus("spec_fertig", konfig, jetzt=jetzt):
        melder.melden(repo, "spec_fertig", titel, text, schluessel, konfig=konfig, jetzt=jetzt)
        return 0
    if not melder.melden(repo, "spec_fertig", titel, text, schluessel, konfig=konfig, jetzt=jetzt):
        log.warning("Mail „%s“ nicht verschickt (Befehl gescheitert).", titel)
        return 1
    return 0


def morgen_bericht_cli(argv: list[str]) -> int:
    """Einmal morgens genau 1 Morgen-Bericht (4 Teile, mit Deckel) als Mail verschicken (#885)."""
    ap = argparse.ArgumentParser(description="Morgen-Bericht einer Spec als eine Mail verschicken (#885).")
    ap.add_argument("spec", type=int)
    ap.add_argument("--repo", type=Path, default=None)
    ap.add_argument("--jetzt", default=None, help="Zeitpunkt jetzt (ISO), sonst Uhr")
    ap.add_argument("--dry-run", action="store_true", help="Bericht zeigen, nicht mailen")
    a = ap.parse_args(argv)
    repo = _repo(a.repo)
    jetzt = _zeit(a.jetzt, ap, "--jetzt")
    datei = capo.morgen_bericht_datei(repo, a.spec)
    if datei.is_file():
        text = datei.read_text(encoding="utf-8")
    else:
        liste = [
            {"number": int(nr), "title": titel, "state": "closed"}
            for nr, titel in _tickets(repo, a.spec)
            if nr.isdigit()
        ]
        datei, text = capo.morgen_bericht(repo, capo.haupt_ref(repo), a.spec, liste)
    konfig = config.lade(repo)
    if a.dry_run:
        sys.stdout.write(text)
        return 0
    if not melder.mail_eingerichtet(konfig):
        log.info("Mail nicht eingerichtet (mail.befehl leer) — Morgen-Bericht nur in %s.", datei)
        return 0
    schluessel = f"morgen_bericht|{a.spec}"
    if melder.schon_gesendet(repo, schluessel):
        log.info("Morgen-Bericht %s schon verschickt — keine zweite Mail.", schluessel)
        return 0
    if not melder.darf_raus("morgen_bericht", konfig, jetzt=jetzt):
        capo.morgen_bericht_verschicken(repo, a.spec, text, konfig=konfig, schluessel=schluessel, jetzt=jetzt)
        return 0
    if not capo.morgen_bericht_verschicken(repo, a.spec, text, konfig=konfig, schluessel=schluessel, jetzt=jetzt):
        log.warning("Morgen-Bericht Spec #%s nicht verschickt (Befehl gescheitert).", a.spec)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
