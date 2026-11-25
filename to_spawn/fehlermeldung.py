"""Werkzeug-Fehler melden (#578, Spec #557 E8): eigenes Log + Ticket im Skill-Repo statt Mail.

Ein Modul, eine Tür :func:`melde`:

* Schreibt jeden Fehler des Bau-Werkzeugs (rote Skill-Tests in der Bau-Spur/nachts,
  unerwartete Werkzeug-Ausnahmen) als JSONL-Zeile ins eigene Log unter
  ``~/.claude/to-spawn/werkzeug_fehler.jsonl`` (überschreibbar per ``TO_SPAWN_FEHLER_LOG``).
* Legt im Skill-Repo ein offenes Ticket (Label ``werkzeug:fehler``) an bzw. kommentiert
  ein bestehendes offenes Ticket zum selben Fehler-Schlüssel (kein Duplikat).
* Ziel-Repo ist immer das Skill-Repo, nie ein App-Repo — unabhängig vom aktuellen
  Arbeitsordner oder von ``TO_SPAWN_REPO``.
* Kein Mail-Versand und keine Benachrichtigung; die Funktion wirft nie.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, gh, hindernis, skill_stand

log = logging.getLogger("to_spawn.fehlermeldung")

#: Umgebungsvariable zum Umbiegen des Werkzeug-Fehler-Logs (Tests).
FEHLER_LOG_ENV = "TO_SPAWN_FEHLER_LOG"
#: Einheitlicher Dateiname unter ``~/.claude/to-spawn/``.
FEHLER_LOG_NAME = "werkzeug_fehler.jsonl"
#: Schalter ``1``/``true``/``ja`` zum Abschalten von GitHub-Tickets (Tests / Offline-Lauf).
OHNE_TICKET_ENV = "TO_SPAWN_FEHLER_OHNE_TICKET"
#: Label aller automatisch gemeldeten Werkzeug-Fehler im Skill-Repo.
TICKET_LABEL = "werkzeug:fehler"
#: Obergrenzen für Ticket-Titel und Detail-Block.
MAX_TITEL_ZEICHEN = 160
MAX_DETAILS_ZEICHEN = 4000

_ISSUE_URL = re.compile(r"https?://[^\s]+/issues/(\d+)")
_ISSUE_ENDE = re.compile(r"/issues/(\d+)\s*$")
_MARKER_VORLAGE = "<!-- werkzeug-fehler: {signatur} -->"


@dataclass(frozen=True)
class Meldung:
    """Ergebnis eines :func:`melde`-Aufrufs."""

    log_geschrieben: bool
    log_pfad: Path | None
    ticket: str | None
    ticket_url: str | None
    aktion: str
    ziel_repo: str
    schluessel: str


def log_pfad() -> Path:
    """Einheitlicher Pfad des Werkzeug-Fehler-Logs (``~/.claude/to-spawn/werkzeug_fehler.jsonl``)."""
    roh = os.environ.get(FEHLER_LOG_ENV, "").strip()
    if roh:
        return config.expand_heim(roh)
    return hindernis.daten_ordner() / FEHLER_LOG_NAME


def skill_repo(skill_ordner: Path | None = None) -> str:
    """GitHub-Slug (``owner/name``) des Skill-Repos — niemals ein App-Repo.

    Reihenfolge:
    1. Git-Origin des Skill-Ordners (falls Git-Klon auf github.com).
    2. ``quelle`` aus ``STAND.json`` (installierte Kopie ohne ``.git``).
    3. Skill-Eintrag der Maschinen-Liste bzw. Vorgabe aus :data:`config.OEFFENTLICHE_SLUGS`.
    """
    ordner = skill_ordner or skill_stand.SKILL
    if (ordner / ".git").exists():
        slug = gh.github_slug_aus_origin(ordner)
        if slug:
            return slug
    stempel = skill_stand._lies_stempel(ordner)
    slug = gh.github_slug(stempel.get("quelle", ""))
    if slug:
        return slug
    try:
        repos = config.maschine().get("repos") or []
    except config.KeinHeimOrdner:
        repos = []
    for eintrag in repos:
        if isinstance(eintrag, dict) and config.ist_skill_slug(str(eintrag.get("slug") or "")):
            ziel = str(eintrag.get("ticket_repo") or eintrag.get("slug") or "").strip()
            if ziel:
                return ziel
    return config.OEFFENTLICHE_SLUGS[0] if config.OEFFENTLICHE_SLUGS else ""


def _norm(text: str) -> str:
    return " ".join(text.strip().lower().split())


def fehler_schluessel(
    quelle: str,
    titel: str,
    *,
    schluessel: str = "",
    ausnahme: BaseException | None = None,
) -> str:
    """Stabile 16-Zeichen-Kennung zur Deduplizierung desselben Werkzeug-Fehlers."""
    if schluessel.strip():
        basis = f"schluessel:{_norm(schluessel)}"
    else:
        ausnahme_typ = type(ausnahme).__name__ if ausnahme is not None else ""
        basis = f"{_norm(quelle)}\n{_norm(titel)}\n{ausnahme_typ}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _tickets_aus() -> bool:
    return os.environ.get(OHNE_TICKET_ENV, "").strip().lower() in ("1", "true", "ja", "yes")


def _details_bauen(details: str, ausnahme: BaseException | None) -> str:
    teile: list[str] = []
    text = details.strip()
    if text:
        teile.append(text)
    if ausnahme is not None:
        kurz = f"{type(ausnahme).__name__}: {ausnahme}".strip()
        if kurz and kurz not in text:
            teile.append(kurz)
        if ausnahme.__traceback__ is not None:
            spur = "".join(traceback.format_exception(type(ausnahme), ausnahme, ausnahme.__traceback__)).strip()
            if spur and spur not in text:
                teile.append(spur)
    gesamt = "\n\n".join(teile).strip()
    if len(gesamt) > MAX_DETAILS_ZEICHEN:
        return gesamt[: MAX_DETAILS_ZEICHEN - 12].rstrip() + "\n… (gekürzt)"
    return gesamt


def _issue_titel(quelle: str, titel: str) -> str:
    q = " ".join(quelle.strip().split())
    t = " ".join(titel.strip().split()) or "Werkzeug-Fehler"
    if q and q.lower() not in t.lower():
        roh = f"fix(werkzeug): {q} — {t}"
    else:
        roh = f"fix(werkzeug): {t}"
    if len(roh) > MAX_TITEL_ZEICHEN:
        return roh[: MAX_TITEL_ZEICHEN - 1].rstrip() + "…"
    return roh


def _kontext_zeilen(kontext: Mapping[str, Any] | None) -> list[str]:
    if not kontext:
        return []
    return [f"- **{k}:** `{v}`" for k, v in sorted(kontext.items()) if v is not None and str(v).strip()]


def _ticket_body(
    *,
    quelle: str,
    titel: str,
    details: str,
    zeit: str,
    wer: str,
    stand: str,
    signatur: str,
    ausnahme_text: str,
    kontext: Mapping[str, Any] | None,
) -> str:
    zeilen = [
        "Automatische Werkzeug-Fehlermeldung des Bau-Systems (ohne Mail-Versand, #578).",
        "",
        f"- **Quelle:** `{quelle}`",
        f"- **Titel:** {titel}",
        f"- **Zeit:** `{zeit}`",
        f"- **Rechner:** `{wer}`",
        f"- **Skill-Stand:** `{stand or 'unbekannt'}`",
        f"- **Schlüssel:** `{signatur}`",
    ]
    if ausnahme_text:
        zeilen.append(f"- **Ausnahme:** `{ausnahme_text}`")
    zeilen.extend(_kontext_zeilen(kontext))
    if details:
        zeilen += ["", "### Details", "", "```text", details, "```"]
    zeilen += ["", _MARKER_VORLAGE.format(signatur=signatur)]
    return "\n".join(zeilen)


def _kommentar_body(
    *,
    details: str,
    zeit: str,
    wer: str,
    stand: str,
    signatur: str,
    ausnahme_text: str,
    kontext: Mapping[str, Any] | None,
) -> str:
    zeilen = [
        f"Erneut aufgetreten (`{zeit}`, Rechner `{wer}`, Skill-Stand `{stand or 'unbekannt'}`):",
    ]
    if ausnahme_text:
        zeilen.append(f"- **Ausnahme:** `{ausnahme_text}`")
    zeilen.extend(_kontext_zeilen(kontext))
    if details:
        zeilen += ["", "```text", details, "```"]
    zeilen += ["", _MARKER_VORLAGE.format(signatur=signatur)]
    return "\n".join(zeilen)


def _issue_url_bauen(ziel_repo: str, nr: int, vorhanden: str = "") -> str:
    url = vorhanden.strip()
    return url if url else f"https://github.com/{ziel_repo}/issues/{nr}"


def _ticket_melden(
    *,
    ziel_repo: str,
    quelle: str,
    titel: str,
    details: str,
    zeit: str,
    wer: str,
    stand: str,
    signatur: str,
    ausnahme_text: str,
    kontext: Mapping[str, Any] | None,
) -> tuple[str, str | None, str | None]:
    """Legt im Skill-Repo ein Ticket an oder kommentiert das offene Ticket desselben Schlüssels."""
    if not ziel_repo:
        log.warning("Kein Skill-Repo bestimmbar — Werkzeug-Fehler wird nur geloggt.")
        return "fehler", None, None

    marker = _MARKER_VORLAGE.format(signatur=signatur)
    offen = gh.json_lauf(
        [
            "issue",
            "list",
            "--repo",
            ziel_repo,
            "--label",
            TICKET_LABEL,
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,title,body,url",
        ],
    )
    if not isinstance(offen, list):
        log.warning("Offene Werkzeug-Tickets (%s) in %s nicht lesbar — nur geloggt.", TICKET_LABEL, ziel_repo)
        return "fehler", None, None

    treffer = sorted(
        (
            eintrag
            for eintrag in offen
            if isinstance(eintrag, dict) and eintrag.get("number") and marker in str(eintrag.get("body") or "")
        ),
        key=lambda eintrag: int(eintrag["number"]),
    )
    if treffer:
        passend = treffer[0]
        nr = int(passend["number"])
        url = _issue_url_bauen(ziel_repo, nr, str(passend.get("url") or ""))
        kommentar = _kommentar_body(
            details=details,
            zeit=zeit,
            wer=wer,
            stand=stand,
            signatur=signatur,
            ausnahme_text=ausnahme_text,
            kontext=kontext,
        )
        code, _ausgabe = gh.lauf(["issue", "comment", str(nr), "--repo", ziel_repo, "--body", kommentar])
        if code != 0:
            log.warning("Kommentar an Werkzeug-Ticket #%d (%s) gescheitert (Exit %d).", nr, ziel_repo, code)
            return "fehler", None, None
        return "kommentiert", f"#{nr}", url

    gh.label_sicherstellen(
        None,
        TICKET_LABEL,
        farbe="b60205",
        beschreibung="Fehler im Bau-Werkzeug (to-spawn #578)",
        repo_slug=ziel_repo,
    )
    body = _ticket_body(
        quelle=quelle,
        titel=titel,
        details=details,
        zeit=zeit,
        wer=wer,
        stand=stand,
        signatur=signatur,
        ausnahme_text=ausnahme_text,
        kontext=kontext,
    )
    code, ausgabe = gh.lauf(
        [
            "issue",
            "create",
            "--repo",
            ziel_repo,
            "--title",
            _issue_titel(quelle, titel),
            "--body",
            body,
            "--label",
            TICKET_LABEL,
        ],
    )
    if code != 0:
        log.warning("Werkzeug-Ticket in %s nicht angelegt (Exit %d).", ziel_repo, code)
        return "fehler", None, None

    url_treffer = _ISSUE_URL.search(ausgabe or "")
    if url_treffer:
        nr = int(url_treffer.group(1))
        return "angelegt", f"#{nr}", url_treffer.group(0)
    ende_treffer = _ISSUE_ENDE.search(ausgabe or "")
    if ende_treffer:
        nr = int(ende_treffer.group(1))
        return "angelegt", f"#{nr}", _issue_url_bauen(ziel_repo, nr)
    return "angelegt", None, (ausgabe.strip() or None)


def melde(
    quelle: str,
    titel: str,
    details: str = "",
    *,
    schluessel: str = "",
    ausnahme: BaseException | None = None,
    kontext: Mapping[str, Any] | None = None,
    log_datei: Path | None = None,
    mit_ticket: bool = True,
    skill_ordner: Path | None = None,
) -> Meldung:
    """Meldet einen Werkzeug-Fehler ins eigene JSONL-Log und ins Skill-Repo (nie Mail, wirft nie)."""
    zeit = datetime.now().astimezone().isoformat(timespec="seconds")
    wer = hindernis.wer()
    stand = skill_stand.stand(skill_ordner or skill_stand.SKILL)
    signatur = fehler_schluessel(quelle, titel, schluessel=schluessel, ausnahme=ausnahme)
    ausnahme_text = f"{type(ausnahme).__name__}: {ausnahme}".strip() if ausnahme is not None else ""
    detail_text = _details_bauen(details, ausnahme)
    ziel_repo = skill_repo(skill_ordner)

    if mit_ticket and not _tickets_aus():
        aktion, ticket, ticket_url = _ticket_melden(
            ziel_repo=ziel_repo,
            quelle=quelle,
            titel=titel,
            details=detail_text,
            zeit=zeit,
            wer=wer,
            stand=stand,
            signatur=signatur,
            ausnahme_text=ausnahme_text,
            kontext=kontext,
        )
    else:
        aktion, ticket, ticket_url = "ohne_ticket", None, None

    eintrag: dict[str, Any] = {
        "zeit": zeit,
        "quelle": quelle,
        "titel": titel,
        "schluessel": signatur,
        "details": detail_text,
        "wer": wer,
        "skill_stand": stand,
        "ziel_repo": ziel_repo,
        "aktion": aktion,
        "ticket": ticket,
        "ticket_url": ticket_url,
    }
    if ausnahme_text:
        eintrag["ausnahme"] = ausnahme_text
    if kontext:
        eintrag["kontext"] = dict(kontext)

    ziel_pfad: Path | None = None
    log_ok = False
    try:
        ziel_pfad = log_datei or log_pfad()
        log_ok = hindernis.zeile_anhaengen(ziel_pfad, eintrag)
    except config.KeinHeimOrdner as fehler:
        log.warning("Werkzeug-Fehler-Log ohne Heim-Ordner nicht schreibbar: %s", fehler)

    return Meldung(
        log_geschrieben=log_ok,
        log_pfad=ziel_pfad,
        ticket=ticket,
        ticket_url=ticket_url,
        aktion=aktion,
        ziel_repo=ziel_repo,
        schluessel=signatur,
    )


def lesen(datei: Path | None = None) -> list[dict[str, Any]]:
    """Liest alle gültigen Zeilen des Werkzeug-Fehler-Logs (älteste zuerst); wirft nie."""
    try:
        ziel = datei or log_pfad()
    except config.KeinHeimOrdner:
        return []
    return hindernis.zeilen_lesen(ziel)


def main(argv: list[str] | None = None) -> int:
    """CLI-Einstieg: ``python -m to_spawn.fehlermeldung --quelle Q --titel T [--details D]``."""
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Werkzeug-Fehler ins eigene Log + Skill-Repo melden (#578)")
    parser.add_argument("--quelle", required=True, help="Ursprung des Werkzeug-Fehlers (z. B. bau-nachtlauf)")
    parser.add_argument("--titel", required=True, help="Kurzer Titel des Fehlers")
    parser.add_argument("--details", default="", help="Detailtext oder Fehlerausgabe")
    parser.add_argument("--schluessel", default="", help="Stabiler Deduplizierungs-Schlüssel (optional)")
    parser.add_argument("--log-datei", type=Path, default=None, help="Abweichender Pfad für das JSONL-Log")
    parser.add_argument("--ohne-ticket", action="store_true", help="Nur ins Log schreiben, kein GitHub-Ticket")
    args = parser.parse_args(argv)

    erg = melde(
        args.quelle,
        args.titel,
        args.details,
        schluessel=args.schluessel,
        log_datei=args.log_datei,
        mit_ticket=not args.ohne_ticket,
    )
    sys.stdout.write(f"{erg.aktion}: {erg.ticket or '-'} ({erg.ticket_url or '-'}) · Log: {erg.log_pfad or '-'}\n")
    return 0 if erg.log_geschrieben and erg.aktion != "fehler" else 1


if __name__ == "__main__":
    sys.exit(main())
