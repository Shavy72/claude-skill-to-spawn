"""Bau-Leitstand: rechnet den Stand einer Spec für die Live-Seite (claude.ai-Artifact mit Datenbank).

Nicht zu verwechseln mit ``to_spawn/leitstand.py`` (Sperren + gemeinsamer Zustand, #313).

Die Datenbank der Seite schreibt nur das Claude-Werkzeug ``ArtifactData`` in einer
interaktiven Session (headless ``claude -p`` hat es nicht). Dieses Skript rechnet
deshalb nur und legt die Schreibaufträge als Dateien ab; die Session sendet sie mit
genau einem ``ArtifactData``-batch und ruft danach ``bestaetigen`` auf.

Ablage je Repo: ``<repo>/.to-spawn/leitstand/`` — ``zustand-<S>.json`` (was schon
gesendet ist), ``writes-<S>.json`` (batch-Einträge), ``offen-<S>.json`` (Merkwerte bis
zur Bestätigung), ``docs/<S>/`` (ein JSON je Dokument), ``seite-<S>.json`` (Artifact-URL),
``leitstand-<S>.log``.

Unterbefehle (Repo = ``TO_SPAWN_REPO``, sonst Git-Wurzel des aktuellen Ordners):
  leitstand.py <S> seed [--ziel local|server] [--gestartet ISO]   Exit 0 = writes da, 3 = nichts Neues
  leitstand.py <S> vorbereiten                                    Exit 0 = writes da, 3 = nichts Neues
  leitstand.py <S> bestaetigen                                    writes als gesendet merken
  leitstand.py <S> url [--setzen <url>]                           Exit 4 = keine URL gespeichert
  leitstand.py <S> seite                                          Vorlage → ``seite-<S>.html`` (Pfad auf stdout)
  leitstand.py <S> anweisung                                      Takt-Anweisung ``takt-<S>.md`` (Pfad auf stdout)
  leitstand.py <S> mail                                           Start-Mail mit Link, genau einmal je Spec
  leitstand.py <S> aktiv                                          Exit 0 = ``leitstand.aktiv`` an, 5 = aus
  leitstand.py <S> sitzung [--probe]                              interaktive Sonnet-Session (``/loop``) im Fenster
  leitstand.py <S> uebernehmen --von <zustand.json> [--url <url>] Zustand des Prototyps übernehmen

Die Takt-Anweisung wird nach jedem Unterbefehl neu geschrieben (sobald es sie gibt) und
spiegelt den Stand: ohne URL die Ersteinrichtung, danach nur noch den Takt (plus seed/Mail,
solange die fehlen). Die Session denkt nicht, sie führt nur diese Datei aus.

Dokumente: ``meta/stand`` und ``tickets/<nr>`` (statisch, per ``seed`` einmal),
``live/<id>`` (Stand je Ticket, bei Änderung oder Herzschlag) und ``log/<id>`` (Ereignisse).
"""
# Dieses Modul rechnet und ist die CLI; Fenster/Session-Teil siehe leitstand_fenster.py.

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import logging.handlers
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any

_SKRIPTE = Path(__file__).resolve().parent
_SKILL = str(_SKRIPTE.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import bau_log, config  # noqa: E402
from to_spawn import gh as gh_modul  # noqa: E402
from to_spawn import manifest as manifest_modul  # noqa: E402

# Fenster-Teil (Ablage, Zustands-/URL-Datei, seite, anweisung, mail, aktiv, sitzung) liegt in leitstand_fenster.py.
if str(_SKRIPTE) not in sys.path:
    sys.path.append(str(_SKRIPTE))
from leitstand_fenster import (  # noqa: E402, F401 — Re-Export: Tests und Aufrufer nutzen leitstand.<name>
    EXIT_AUS,
    EXIT_KEINE_URL,
    MAIL_ART,
    MODELL,
    TAKT,
    VORLAGE,
    Ablage,
    anweisung_text,
    erlaubte_werkzeuge,
    fuehre_aus,
    ist_aktiv,
    jetzt_iso,
    lade_zustand,
    leerer_zustand,
    lies_url,
    nimm_sperre,
    python_befehl,
    rendere_seite,
    schreibe_anweisung,
    schreibe_seite,
    setze_url,
    sitzung,
    sitzung_befehl,
    skript_pfad,
    speichere_json,
    start_mail,
)

log = logging.getLogger("leitstand_seite")

#: Kindprozesse unter Windows nie mit eigenem Fenster (nie ``DETACHED_PROCESS``); Linux: 0.
OHNE_FENSTER: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HERZSCHLAG_S = 600
#: ``ArtifactData`` batch nimmt höchstens 50 Schreibvorgänge.
MAX_DOKUMENTE = 50
#: Ist-Kontext in k-Token je Arbeitsminute (Messwert Spec 376) — ``leitstand.k_pro_min`` in der Konfig überschreibt.
K_PRO_MIN = 3.0
EXIT_NICHTS = 3
AKTIVITAET = ("session_start", "session_ende", "subagent_ende")
ROT = ("rot", "fehler", "failed", "fail", "abbruch", "error")
ZIELE = ("local", "server")
#: Abschnitte im Ticket-Text (Englisch aus /to-tickets, Deutsch als Ausweich).
KOPF_WAS = ("what to build", "was gebaut wird", "was", "ziel")
KOPF_BEWEIS = ("acceptance criteria", "akzeptanzkriterien", "akzeptanz", "abnahme", "beweis")
_UEBERSCHRIFT = re.compile(r"^\s*#{1,6}\s+(.*?)\s*:?\s*$")


# ---------------------------------------------------------------- Hilfen


def als_zeit(text: Any) -> datetime | None:
    if not text:
        return None
    try:
        zeit = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return zeit if zeit.tzinfo else zeit.astimezone()


def lokal_iso(text: Any) -> str | None:
    zeit = als_zeit(text)
    return zeit.astimezone().isoformat(timespec="seconds") if zeit else None


def repariere(text: str) -> str:
    """Doppelt kodiertes UTF-8 („lÃ¤uft“) zurück in echte Umlaute."""
    if "Ã" in text or "â€" in text or "ðŸ" in text:
        try:
            return text.encode("cp1252").decode("utf-8")
        except UnicodeError:
            return text
    return text


def kurz(text: Any, grenze: int) -> str:
    wert = " ".join(repariere(str(text or "")).split())
    return wert if len(wert) <= grenze else wert[: grenze - 1].rstrip() + "…"


def _ohne_markdown(text: Any) -> str:
    wert = re.sub(r"[*`]+|^\s*[#>]+\s*", "", repariere(str(text or "")), flags=re.MULTILINE)
    wert = re.sub(r"\s*\(fp [0-9a-f]+\)", "", wert)
    return " ".join(wert.split())


def einfach(text: Any, grenze: int = 220, saetze: int = 2) -> str:
    """Höchstens ``saetze`` Sätze ohne Markdown."""
    teile = re.split(r"(?<=[.!?])\s+", _ohne_markdown(text))
    return kurz(" ".join(teile[:saetze]), grenze)


def doc_id(zeit_iso: str, quelle: str) -> str:
    """Sortierbar nach Zeit, eindeutig über die Quelle."""
    stempel = re.sub(r"\D", "", zeit_iso)[:14]
    return f"{stempel}-{hashlib.sha1(quelle.encode('utf-8')).hexdigest()[:10]}"


def starte(cmd: list[str], cwd: Path, *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Kindprozess ohne Fenster (Windows ``CREATE_NO_WINDOW``, Linux normal)."""
    return subprocess.run(  # noqa: S603 — Befehle aus festen Listen
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=OHNE_FENSTER,
    )


def repo_ermitteln() -> Path:
    """Wie ``sessions_stand``: ``TO_SPAWN_REPO``, sonst Git-Wurzel des aktuellen Ordners (#205)."""
    if os.environ.get("TO_SPAWN_REPO"):
        return Path(os.environ["TO_SPAWN_REPO"]).expanduser().resolve()
    return config.repo_wurzel()


def lege_writes_ab(
    ablage: Ablage, docs: list[tuple[str, str, dict[str, Any]]], offen: dict[str, Any]
) -> list[dict[str, str]]:
    """Doc-Dateien + ``writes-<S>.json`` + ``offen-<S>.json`` schreiben (alte vorher weg)."""
    for alt in (ablage.writes_datei, ablage.offen_datei):
        alt.unlink(missing_ok=True)
    if not docs:
        return []
    if ablage.docs.exists():
        shutil.rmtree(ablage.docs)
    ablage.docs.mkdir(parents=True)
    writes: list[dict[str, str]] = []
    for sammlung, did, doc in docs:
        pfad = ablage.docs / f"{sammlung}-{did}.json"
        pfad.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        writes.append({"op": "set", "collection": sammlung, "doc_id": did, "file_path": pfad.as_posix()})
    speichere_json(ablage.offen_datei, offen)
    speichere_json(ablage.writes_datei, writes)
    return writes


# ---------------------------------------------------------------- GitHub (ohne Fenster)


def gh_lauf(args: list[str], repo: Path) -> tuple[int, str]:
    """``gh <args>`` (oder ``TO_SPAWN_GH_STUB``) ohne Fenster; (Exit-Code, stdout)."""
    vorspann = gh_modul.gh_befehl()
    if vorspann is None:
        return 127, ""
    try:
        erg = starte([*vorspann, *args], repo)
    except (OSError, subprocess.SubprocessError) as fehler:
        log.warning("gh-Aufruf fehlgeschlagen: %s", fehler)
        return 127, ""
    return erg.returncode, (erg.stdout or "").strip()


def gh_json(args: list[str], repo: Path) -> Any:
    code, ausgabe = gh_lauf(args, repo)
    if code != 0 or not ausgabe:
        return None
    try:
        return json.loads(ausgabe)
    except ValueError:
        log.warning("gh-Ausgabe ist kein JSON: %s", ausgabe[:200])
        return None


def repo_slug(repo: Path) -> str:
    """``owner/name`` aus ``git remote get-url origin``."""
    try:
        url = starte(["git", "remote", "get-url", "origin"], repo).stdout.strip()
    except (OSError, subprocess.SubprocessError) as fehler:
        log.warning("git remote nicht lesbar: %s", fehler)
        return ""
    treffer = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", url) or re.search(
        r"([^/\\]+/[^/\\]+?)(?:\.git)?$", url
    )
    return treffer.group(1) if treffer else ""


def gh_issues(repo: Path, nummern: list[str]) -> dict[str, dict[str, Any]]:
    """Zustand, closedAt und Kommentare aller Issues in EINEM GraphQL-Aufruf."""
    slug = repo_slug(repo)
    if "/" not in slug:
        raise RuntimeError("origin nicht lesbar — kein owner/name")
    besitzer, name = slug.split("/", 1)
    teile = " ".join(
        f"i{n}: issue(number: {n}) {{ state closedAt comments(last: 100) {{ nodes {{ id createdAt body }} }} }}"
        for n in nummern
    )
    abfrage = f'query {{ repository(owner: "{besitzer}", name: "{name}") {{ {teile} }} }}'
    code, ausgabe = gh_lauf(["api", "graphql", "-f", f"query={abfrage}"], repo)
    if code != 0:
        raise RuntimeError(f"gh api graphql: Exit {code}")
    daten = json.loads(ausgabe)["data"]["repository"]
    return {n: daten.get(f"i{n}") or {} for n in nummern}


# ---------------------------------------------------------------- Quellen (Prozesse, Bau-Log)


def lade_sessions_stand(repo: Path) -> ModuleType:
    """``sessions_stand`` frisch laden — ``REPO`` ist eine Modul-Konstante aus ``TO_SPAWN_REPO``."""
    os.environ["TO_SPAWN_REPO"] = str(repo)
    spec = importlib.util.spec_from_file_location("leitstand_sessions_stand", _SKRIPTE / "sessions_stand.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"sessions_stand.py fehlt unter {_SKRIPTE}")
    ss = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ss  # dataclasses brauchen den Modul-Eintrag
    spec.loader.exec_module(ss)
    return ss


def prozess_stand(ss: ModuleType, spec: str) -> tuple[dict[str, str], dict[str, str]]:
    """(Titel je Ticket aus dem Manifest, Prozess-Zustandstext je Ticket)."""
    eintraege = ss.manifeste_lesen(spec)
    titel = {n: e.titel for n, e in eintraege.items() if e.art == "ticket"}
    ss.zuordnen(eintraege, ss.prozesse_lesen())
    return titel, {n: eintraege[n].zustand for n in titel}


def bau_zeilen(repo: Path, ticket: str) -> tuple[list[dict[str, Any]], float | None]:
    """Bau-Log-Zeilen + Spitzen-Kontext (k) — erst Worktree, dann Repo (wie ``sessions_stand.token_text``)."""
    for ort in (Path(config.worktree_pfad(ticket, repo)).expanduser(), repo):
        try:
            if not bau_log.hat_log(ort, ticket):
                continue
            zeilen = bau_log.lese(ort, ticket, hauptbaum=repo)
            spitze = bau_log.zusammenfassung(ort, ticket, hauptbaum=repo).get("spitze_k")
        except OSError as fehler:
            log.warning("Bau-Log #%s unlesbar: %s", ticket, fehler)
            return [], None
        return zeilen, (round(float(spitze), 1) if spitze else None)
    return [], None


# ---------------------------------------------------------------- Ereignisse


@dataclass
class Ereignis:
    quelle: str
    zeit: str
    art: str
    titel: str
    text: str
    ticket: int | None = None

    @property
    def id(self) -> str:
        return doc_id(self.zeit, self.quelle)

    def dokument(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"zeit": self.zeit, "art": self.art, "titel": kurz(self.titel, 60), "text": self.text}
        if self.ticket is not None:
            doc["ticket"] = self.ticket
        return doc


def felder_text(z: dict[str, Any], schluessel: tuple[str, ...]) -> str:
    return " ".join(kurz(z[k], 120) for k in schluessel if z.get(k))


def _ist_rot(z: dict[str, Any]) -> bool:
    return any(str(z.get(k) or "").strip().lower() in ROT for k in ("phase", "status", "ergebnis"))


def aus_bau_log(ticket: str, zeilen: list[dict[str, Any]]) -> list[Ereignis]:
    ergebnis: list[Ereignis] = []
    nr = int(ticket)
    for z in zeilen:
        typ, zeit = z.get("typ"), lokal_iso(z.get("ts"))
        if not zeit:
            continue
        quelle = f"bau_log|{ticket}|{z.get('ts')}|{typ}"
        if typ == "blockiert":
            grund = kurz(z.get("grund") or z.get("text"), 160) or "ohne Grund"
            ergebnis.append(
                Ereignis(quelle, zeit, "problem", f"#{nr} blockiert", einfach(f"Der Bau hängt fest: {grund}."), nr)
            )
        elif typ == "entscheidung":
            frage = z.get("frage") or z.get("text") or z.get("umfang") or ""
            wahl = z.get("wahl") or z.get("entscheidungen") or ""
            text = f"{kurz(frage, 140)} → {kurz(wahl, 80)}" if wahl else kurz(frage, 200)
            ergebnis.append(Ereignis(quelle, zeit, "befund", f"#{nr} Entscheidung", einfach(text), nr))
        elif typ == "mensch_noetig":
            ergebnis.append(
                Ereignis(
                    quelle,
                    zeit,
                    "david",
                    f"#{nr} braucht David",
                    "Die Bau-Session kommt ohne dich nicht weiter. Bitte ins Fenster schauen.",
                    nr,
                )
            )
        elif typ == "deploy_phase":
            inhalt = felder_text(z, ("phase", "status", "ergebnis", "grund"))
            art = "problem" if _ist_rot(z) else "info"
            ergebnis.append(
                Ereignis(
                    quelle, zeit, art, f"#{nr} Deploy: {kurz(inhalt, 40)}", einfach(f"Deploy-Schritt: {inhalt}."), nr
                )
            )
        elif typ == "waechter_modell":
            inhalt = felder_text(z, ("von", "nach", "modell", "grund", "text"))
            ergebnis.append(
                Ereignis(
                    quelle,
                    zeit,
                    "info",
                    f"#{nr} Modell gewechselt",
                    einfach(f"Der Wächter hat das Modell gewechselt: {inhalt}."),
                    nr,
                )
            )
    return ergebnis


def aus_kommentaren(nummer: str, issue: dict[str, Any], spec: str) -> list[Ereignis]:
    ergebnis: list[Ereignis] = []
    ticket = None if nummer == spec else int(nummer)
    for k in (issue.get("comments") or {}).get("nodes") or []:
        body = str(k.get("body") or "").strip()
        zeit = lokal_iso(k.get("createdAt"))
        if not body.startswith("Wächter") or not zeit:
            continue
        rein = re.sub(r"Wächter:\s*", "", body)
        if "Mensch nötig" in body:
            art = "david"
        elif re.search(r"\b(rot|Fehler|blockiert)\b", body):
            art = "problem"
        else:
            art = "befund"
        titel = re.split(r"(?<=[.!?])\s", einfach(rein, 200))[0]
        vorn = f"#{ticket} " if ticket else ""
        ergebnis.append(Ereignis(f"gh|{k.get('id')}", zeit, art, f"{vorn}{titel}", einfach(rein), ticket))
    return ergebnis


def ohne_doppelte_david(aus_log: list[Ereignis], kommentare: list[Ereignis]) -> list[Ereignis]:
    """Bau-Log-„mensch_noetig“ weglassen, wenn der Wächter binnen 5 Min dasselbe kommentiert hat."""
    wache = [t for t in (als_zeit(e.zeit) for e in kommentare if e.art == "david") if t]

    def doppelt(e: Ereignis) -> bool:
        zeit = als_zeit(e.zeit)
        return e.art == "david" and zeit is not None and any(abs((w - zeit).total_seconds()) <= 300 for w in wache)

    return [e for e in aus_log if not doppelt(e)]


# ---------------------------------------------------------------- Stand eines Tickets


def ticket_stand(
    nr: str,
    prozess: str,
    issue: dict[str, Any],
    zeilen: list[dict[str, Any]],
    david_zeiten: list[datetime],
    zustand: dict[str, Any],
) -> tuple[str, str | None]:
    """(zustand, phase) — Reihenfolge: fertig > verwaist > Gate rot > David > läuft > wartet."""
    if issue.get("state") == "CLOSED":
        return "fertig", None
    if "VERWAIST" in prozess:
        return "fehler", "Session verwaist"
    wichtig = [
        z
        for z in zeilen
        if z.get("typ") in ("deploy_phase", "blockiert", "handoff", "waechter_modell", "waechter_pause")
    ]
    letzte = wichtig[-1] if wichtig else None
    if letzte and letzte.get("typ") == "deploy_phase" and _ist_rot(letzte):
        return "fehler", kurz(f"Deploy rot: {felder_text(letzte, ('phase', 'grund'))}", 40)
    aktiv = [t for t in (als_zeit(z.get("ts")) for z in zeilen if z.get("typ") in AKTIVITAET) if t]
    if david_zeiten and (not aktiv or max(david_zeiten) > max(aktiv)):
        return "braucht_david", "Wartet auf David"
    phase = None
    if letzte is not None:
        typ = letzte.get("typ")
        if typ == "deploy_phase":
            phase = "Deploy: " + felder_text(letzte, ("phase", "status"))
        elif typ == "blockiert":
            phase = "Blockiert: " + kurz(letzte.get("grund"), 60)
        elif typ == "handoff":
            phase = "Übergabe an neue Session"
        elif typ == "waechter_pause":
            phase = "Pause bis Limit-Reset"
        elif typ == "waechter_modell":
            phase = "Modell gewechselt"
    if prozess.startswith("läuft"):
        return "läuft", kurz(phase, 40) if phase else None
    if prozess == "aus" and nr in zustand["start"]:
        return "wartet", "Session beendet, Ticket offen"
    return "wartet", kurz(phase, 40) if phase else None


# ---------------------------------------------------------------- vorbereiten (live + log)


def berechne(
    spec: str,
    titel: dict[str, str],
    prozesse: dict[str, str],
    issues: dict[str, dict[str, Any]],
    zeilen_je_ticket: dict[str, tuple[list[dict[str, Any]], float | None]],
    zustand: dict[str, Any],
) -> tuple[dict[str, Any], list[Ereignis], dict[str, Any]]:
    """(live-Inhalt, alle Ereignisse, neue Merkwerte start/ende) — reine Rechnung, schreibt nichts."""
    nummern = sorted(titel, key=int)
    ereignisse = aus_kommentaren(spec, issues.get(spec, {}), spec)
    start, ende = dict(zustand["start"]), dict(zustand["ende"])
    tickets: dict[str, Any] = {}
    for nr in nummern:
        zeilen, ist_k = zeilen_je_ticket.get(nr, ([], None))
        issue = issues.get(nr, {})
        kommentare = aus_kommentaren(nr, issue, spec)
        ereignisse += kommentare + ohne_doppelte_david(aus_bau_log(nr, zeilen), kommentare)
        david = [t for t in (als_zeit(e.zeit) for e in kommentare if e.art == "david") if t]
        david += [t for t in (als_zeit(z.get("ts")) for z in zeilen if z.get("typ") == "mensch_noetig") if t]
        stand, phase = ticket_stand(nr, prozesse.get(nr, "aus"), issue, zeilen, david, zustand)
        if stand in ("läuft", "fertig", "braucht_david") and nr not in start:
            erster = min((z.get("ts") for z in zeilen if z.get("typ") == "session_start"), default=None)
            start[nr] = lokal_iso(erster) or jetzt_iso()
        if stand == "fertig" and nr not in ende:
            ende[nr] = lokal_iso(issue.get("closedAt")) or jetzt_iso()
        eintrag: dict[str, Any] = {"zustand": stand, "ist_k": ist_k}
        if phase:
            eintrag["phase"] = phase
        if nr in start:
            eintrag["start"] = start[nr]
            ereignisse.append(
                Ereignis(
                    f"start|{spec}|{nr}",
                    start[nr],
                    "info",
                    f"#{nr} gestartet",
                    f"Ticket #{nr} ist in Arbeit: {kurz(titel[nr], 70)}",
                    int(nr),
                )
            )
        if nr in ende:
            eintrag["ende"] = ende[nr]
            ereignisse.append(
                Ereignis(
                    f"ende|{spec}|{nr}",
                    ende[nr],
                    "info",
                    f"#{nr} fertig",
                    f"Ticket #{nr} ist abgeschlossen und auf GitHub geschlossen.",
                    int(nr),
                )
            )
        tickets[nr] = eintrag
    return {"tickets": tickets}, ereignisse, {"start": start, "ende": ende}


def neue_dokumente(
    zustand: dict[str, Any], live: dict[str, Any], ereignisse: list[Ereignis], jetzt: datetime | None = None
) -> list[tuple[str, str, dict[str, Any]]]:
    """(collection, doc_id, dokument) — nur Neues, höchstens ``MAX_DOKUMENTE``, ältestes zuerst."""
    jetzt = jetzt or datetime.now().astimezone()
    docs: list[tuple[str, str, dict[str, Any]]] = []
    inhalt = json.dumps(live, sort_keys=True, ensure_ascii=False)
    letzte = als_zeit(zustand.get("live_zeit"))
    herzschlag = letzte is None or (jetzt - letzte).total_seconds() >= HERZSCHLAG_S
    if inhalt != zustand.get("live_inhalt") or herzschlag:
        zeit = jetzt.isoformat(timespec="seconds")
        docs.append(("live", doc_id(zeit, "live"), {"zeit": zeit, **live}))
    gesendet = set(zustand["gesendet"])
    eindeutig = {e.id: e for e in ereignisse if e.id not in gesendet}
    for e in sorted(eindeutig.values(), key=lambda e: (e.zeit, e.id))[: MAX_DOKUMENTE - len(docs)]:
        docs.append(("log", e.id, e.dokument()))
    return docs


def vorbereiten(ablage: Ablage) -> list[dict[str, str]]:
    """Stand aus Prozessen, Bau-Log und GitHub rechnen, nur Neues als writes ablegen (leer = nichts Neues)."""
    zustand = lade_zustand(ablage.zustand_datei)
    ss = lade_sessions_stand(ablage.repo)
    titel, prozesse = prozess_stand(ss, ablage.spec)
    nummern = sorted(titel, key=int)
    issues = gh_issues(ablage.repo, [ablage.spec, *nummern])
    zeilen = {nr: bau_zeilen(ablage.repo, nr) for nr in nummern}
    live, ereignisse, merk = berechne(ablage.spec, titel, prozesse, issues, zeilen, zustand)
    docs = neue_dokumente(zustand, live, ereignisse)
    live_zeit = next((doc["zeit"] for s, _, doc in docs if s == "live"), None)
    offen = {
        "art": "takt",
        **merk,
        "log_ids": [d for s, d, _ in docs if s == "log"],
        "live_inhalt": json.dumps(live, sort_keys=True, ensure_ascii=False) if live_zeit else None,
        "live_zeit": live_zeit,
    }
    writes = lege_writes_ab(ablage, docs, offen)
    log.info("vorbereiten: %d neue Dokumente.", len(writes))
    return writes


# ---------------------------------------------------------------- seed (meta/stand + tickets/<nr>)


def ziel_normal(wert: str | None, konfig: dict[str, Any]) -> str:
    """``local``/``lokal`` → local, ``srv``/``server``/``remote`` → server (sonst Konfig ``ziel_default``)."""
    roh = str(wert or konfig.get("ziel_default") or "srv").strip().lower()
    return "local" if roh in ("local", "lokal", "pc") else "server"


def spec_titel(title: str) -> str:
    """„Spec: Verwerfen in der Reelstraße – Mülleimer, …“ → „Verwerfen in der Reelstraße“."""
    rein = re.sub(r"^\s*\[?(spec|checkpoint)\]?\s*[:#-]?\s*", "", title or "", flags=re.IGNORECASE)
    return kurz(re.split(r"\s+[–—-]\s+", rein, maxsplit=1)[0], 80)


def ticket_titel(title: str) -> str:
    """„Verwerfen 1/9: Rollen …“ → „Rollen …“ (Serien-Vorspann weg)."""
    return kurz(re.sub(r"^[^:]{0,40}?\b\d+\s*/\s*\d+\s*:\s*", "", title or ""), 80)


def abschnitt(body: str, namen: tuple[str, ...]) -> str:
    """Text unter der ersten Überschrift, deren Name in ``namen`` steht (bis zur nächsten Überschrift)."""
    zeilen = (body or "").splitlines()
    for i, zeile in enumerate(zeilen):
        kopf = _UEBERSCHRIFT.match(zeile)
        if not kopf or kopf.group(1).strip().lower() not in namen:
            continue
        rest: list[str] = []
        for folge in zeilen[i + 1 :]:
            if _UEBERSCHRIFT.match(folge):
                break
            rest.append(folge)
        return "\n".join(rest).strip()
    return ""


def punkte(text: str) -> list[str]:
    """Listenpunkte („- [ ] …“, „- …“, „1. …“) ohne Kästchen und Markdown."""
    ergebnis = []
    for zeile in (text or "").splitlines():
        treffer = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.*\S)", zeile)
        if treffer:
            ergebnis.append(_ohne_markdown(treffer.group(1)))
    return ergebnis


def _zahl_oder_none(wert: Any) -> float | None:
    return float(wert) if bau_log.ist_schaetzung(wert) else None


def erwartet_min(schaetzung_k: Any, faktor: float | None, k_pro_min: float) -> int | None:
    """Erwartete Bauzeit: Schätzung × Faktor (Ist/Schätzung) ÷ k-Token je Minute."""
    soll = _zahl_oder_none(schaetzung_k)
    if soll is None or k_pro_min <= 0:
        return None
    return max(1, round(soll * (faktor or 1.0) / k_pro_min))


def blocker_nummern(kanten: list[dict[str, Any]] | None, body: str) -> list[int]:
    """Native Blocker-Kanten (SSOT), sonst „Blocked by“-Abschnitt im Ticket-Text."""
    nummern = [int(k["number"]) for k in kanten or [] if str(k.get("number") or "").isdigit()]
    if not nummern:
        nummern = [int(n) for n in manifest_modul._blocker_verweise(body) if str(n).isdigit()]  # noqa: SLF001
    return sorted(dict.fromkeys(nummern))


def beobachten_fuer(ziel: str, spec: str, ssh_ziel: str) -> list[dict[str, str]]:
    if ziel == "local":
        return [
            {"titel": "Stand als Tabelle", "befehl": f"sessions {spec}"},
            {
                "titel": "Live zuschauen (lokal)",
                "befehl": f"Windows-Terminal-Fenster der Spec {spec} – je Ticket ein Tab „bau <N>“, dazu „wache {spec}“",
            },
        ]
    return [
        {"titel": "Stand als Tabelle", "befehl": f"ssh {ssh_ziel} sessions {spec}"},
        {"titel": "Live zuschauen (Bau-Server)", "befehl": f"ssh -t {ssh_ziel} tmux attach -t spec-{spec}"},
    ]


def hinweise_fuer(
    ziel: str, spec: str, ssh_ziel: str, checkpoints: list[int], aus_manifest: Any
) -> list[dict[str, str]]:
    if ziel == "local":
        hinweise = [
            {
                "art": "info",
                "titel": "Lokal auf diesem PC",
                "text": "Alles läuft auf diesem Rechner. PC muss an bleiben, Windows-Terminal-Fenster nicht schließen.",
            },
            {"art": "info", "titel": "Stand abfragen", "text": f"In PowerShell `sessions {spec}` eintippen."},
            {
                "art": "achtung",
                "titel": "Nie hart beenden",
                "text": "Einen bau-Tab nie schließen, solange er arbeitet. Sonst bleibt ein verwaister Prozess übrig.",
            },
        ]
    else:
        hinweise = [
            {
                "art": "info",
                "titel": "Auf dem Bau-Server",
                "text": f"Alles läuft auf dem Bau-Server ({ssh_ziel}). Der Laptop darf aus.",
            },
            {
                "art": "info",
                "titel": "Stand abfragen",
                "text": f"Im Terminal `ssh {ssh_ziel} sessions {spec}` eintippen.",
            },
            {
                "art": "achtung",
                "titel": "Nie hart beenden",
                "text": "Ein bau-Fenster in tmux nie schließen, solange es arbeitet. Sonst bleibt ein verwaister Prozess übrig.",
            },
        ]
    for nr in checkpoints:
        hinweise.append(
            {
                "art": "info",
                "titel": "Abnahme",
                "text": f"Bei Ticket #{nr} schreibst du deinen Kommentar, dann schließt es.",
            }
        )
    zusatz = aus_manifest if isinstance(aus_manifest, list) else [aus_manifest] if aus_manifest else []
    for text in zusatz:
        if str(text).strip():
            hinweise.append({"art": "info", "titel": "Aus der Planung", "text": kurz(text, 300)})
    return hinweise


def seed_docs(
    spec: str,
    manifest: dict[str, Any],
    github: dict[str, dict[str, Any]],
    *,
    ziel: str,
    gestartet: str,
    faktor: float | None,
    ssh_ziel: str,
    checkpoint_label: str = "checkpoint:human",
    k_pro_min: float = K_PRO_MIN,
    repo_slug: str = "",
) -> list[tuple[str, str, dict[str, Any]]]:
    """Statische Dokumente ``meta/stand`` + ``tickets/<nr>`` — reine Rechnung.

    ``github[<nr>]`` = ``{title, body, labels, _kanten}`` (fehlt ein Eintrag, zählen nur Manifest-Werte).
    """
    tickets_roh: dict[str, Any] = manifest.get("tickets") or {}
    nummern = sorted(tickets_roh, key=manifest_modul.ticket_schluessel)
    docs: list[tuple[str, str, dict[str, Any]]] = []
    checkpoints: list[int] = []
    for reihe, nr in enumerate(nummern, start=1):
        roh = tickets_roh[nr] if isinstance(tickets_roh[nr], dict) else {}
        daten = github.get(str(nr)) or {}
        body = str(daten.get("body") or "")
        labels = {str(e.get("name")) for e in daten.get("labels") or [] if isinstance(e, dict)}
        art = "checkpoint" if checkpoint_label in labels else "bau"
        if art == "checkpoint":
            checkpoints.append(int(nr))
        was = abschnitt(body, KOPF_WAS)
        umfang = str(roh.get("umfang") or "")
        beweis_punkte = punkte(abschnitt(body, KOPF_BEWEIS))
        doc: dict[str, Any] = {
            "nr": int(nr),
            "titel": ticket_titel(str(roh.get("title") or daten.get("title") or f"Ticket {nr}")),
            "kurz": einfach(was, 140, saetze=1) if was else kurz(umfang, 140),
            "art": art,
            "blocked_by": blocker_nummern(daten.get("_kanten"), body),
            "schaetzung_k": roh.get("schaetzung_k"),
            "erwartet_min": erwartet_min(roh.get("schaetzung_k"), faktor, k_pro_min),
            "liefert": kurz(umfang, 240) if umfang else einfach(was, 240),
            "beweis": kurz("; ".join(beweis_punkte), 240) if beweis_punkte else "Tests grün + Beleg im Ticket",
            "reihe": reihe,
        }
        if roh.get("naht") is not None:
            doc["naht"] = roh["naht"]
        docs.append(("tickets", str(nr), doc))
    spec_daten = github.get(str(spec)) or {}
    meta: dict[str, Any] = {
        "spec": int(spec),
        "titel": spec_titel(str(spec_daten.get("title") or manifest.get("feature") or f"Spec {spec}")),
        "ziel": ziel,
        "gestartet": gestartet,
        "beobachten": beobachten_fuer(ziel, str(spec), ssh_ziel),
        "hinweise": hinweise_fuer(ziel, str(spec), ssh_ziel, checkpoints, manifest.get("hinweise")),
    }
    if faktor is not None:
        meta["faktor"] = faktor
    if manifest.get("naehte"):
        meta["naehte"] = manifest["naehte"]
    if re.fullmatch(r"[\w.-]+/[\w.-]+", repo_slug or ""):
        meta["repo"] = repo_slug
    return [("meta", "stand", meta), *docs]


def faktor_aus_historie(repo: Path, letzte: int = 30) -> float | None:
    """Mittel Ist/Schätzung der jüngsten Tickets im Bau-Log (wie ``bau_log.lernstoff``)."""
    try:
        tickets = sorted(bau_log.alle_tickets(repo), key=manifest_modul.ticket_schluessel, reverse=True)[:letzte]
        aus_manifest = bau_log._manifest_eintraege(repo)  # noqa: SLF001 — gleiche Quelle wie lernstoff
        faktoren = []
        for t in tickets:
            z = bau_log.zusammenfassung(repo, t)
            soll = _zahl_oder_none(z.get("schaetzung_k") or aus_manifest.get(str(t), {}).get("schaetzung_k"))
            ist = z.get("ist_k")
            if soll and ist:
                faktoren.append(float(ist) / soll)
    except (OSError, ValueError) as fehler:
        log.warning("Faktor aus Bau-Log nicht lesbar: %s", fehler)
        return None
    return round(sum(faktoren) / len(faktoren), 1) if faktoren else None


def hole_github_seed(repo: Path, spec: str, nummern: list[str]) -> dict[str, dict[str, Any]]:
    """Titel/Text/Labels je Issue + native Blocker-Kanten (Ausfall = Eintrag fehlt, Manifest reicht)."""
    slug = repo_slug(repo)
    ergebnis: dict[str, dict[str, Any]] = {}
    for nr in [spec, *nummern]:
        daten = gh_json(["issue", "view", str(nr), "--json", "title,state,labels,body"], repo)
        if not isinstance(daten, dict):
            log.warning("gh issue view #%s gescheitert — nur Manifest-Werte.", nr)
            continue
        if nr != spec and slug:
            kanten = gh_json(["api", f"repos/{slug}/issues/{nr}/dependencies/blocked_by"], repo)
            daten["_kanten"] = kanten if isinstance(kanten, list) else []
        ergebnis[str(nr)] = daten
    return ergebnis


def seed(ablage: Ablage, ziel: str | None = None, gestartet: str | None = None) -> list[dict[str, str]]:
    """Statische Docs als writes ablegen — nur die, die noch nicht bestätigt gesendet sind."""
    zustand = lade_zustand(ablage.zustand_datei)
    konfig = config.lade(ablage.repo)
    manifest = manifest_modul.lade_manifest(ablage.repo, ablage.spec)
    if not manifest:
        raise FileNotFoundError(f"Manifest fehlt: {manifest_modul.manifest_pfad(ablage.repo, ablage.spec)}")
    nummern = sorted((manifest.get("tickets") or {}), key=manifest_modul.ticket_schluessel)
    github = hole_github_seed(ablage.repo, ablage.spec, [str(n) for n in nummern])
    leitstand_konfig = konfig.get("leitstand") if isinstance(konfig.get("leitstand"), dict) else {}
    start = gestartet or zustand.get("gestartet") or jetzt_iso()
    alle = seed_docs(
        ablage.spec,
        manifest,
        github,
        ziel=ziel_normal(ziel, konfig),
        gestartet=start,
        faktor=faktor_aus_historie(ablage.repo),
        ssh_ziel=str(konfig.get("ssh_ziel") or "bau-server"),
        checkpoint_label=str(konfig.get("regularien", {}).get("checkpoint_label", "checkpoint:human")),
        k_pro_min=float(leitstand_konfig.get("k_pro_min") or K_PRO_MIN),
        repo_slug=repo_slug(ablage.repo),
    )
    schon = set(zustand["seed_gesendet"])
    docs = [d for d in alle if f"{d[0]}/{d[1]}" not in schon][:MAX_DOKUMENTE]
    offen = {"art": "seed", "seed_ids": [f"{s}/{d}" for s, d, _ in docs], "gestartet": start}
    writes = lege_writes_ab(ablage, docs, offen)
    log.info("seed: %d Dokumente (%d schon gesendet).", len(writes), len(schon))
    return writes


# ---------------------------------------------------------------- bestaetigen + url


def bestaetigen(ablage: Ablage) -> int:
    """Nach erfolgreichem Batch: ids als gesendet merken, writes-/offen-Datei löschen."""
    if not ablage.offen_datei.exists():
        log.error("Nichts zu bestätigen: %s fehlt.", ablage.offen_datei)
        return 1
    offen = json.loads(ablage.offen_datei.read_text(encoding="utf-8"))
    zustand = lade_zustand(ablage.zustand_datei)
    if offen.get("art") == "seed":
        zustand["seed_gesendet"] = sorted(set(zustand["seed_gesendet"]) | set(offen.get("seed_ids") or []))
        zustand["gestartet"] = zustand.get("gestartet") or offen.get("gestartet")
        anzahl = len(offen.get("seed_ids") or [])
    else:
        zustand["start"], zustand["ende"] = offen["start"], offen["ende"]
        zustand["gesendet"] = sorted(set(zustand["gesendet"]) | set(offen["log_ids"]))
        if offen.get("live_zeit"):
            zustand["live_inhalt"], zustand["live_zeit"] = offen["live_inhalt"], offen["live_zeit"]
        anzahl = len(offen["log_ids"]) + (1 if offen.get("live_zeit") else 0)
    speichere_json(ablage.zustand_datei, zustand)
    ablage.writes_datei.unlink(missing_ok=True)
    ablage.offen_datei.unlink(missing_ok=True)
    log.info("Bestätigt (%s): %d Dokumente.", offen.get("art", "takt"), anzahl)
    return 0


def uebernehmen(ablage: Ablage, von: Path, url: str | None = None) -> int:
    """Zustand des Prototyps (``C:/dev/leitstand/zustand-<S>.json``) übernehmen — gleiche Doc-IDs, nichts doppelt.

    seed gilt als gesendet (``meta/stand`` + ``tickets/<nr>`` aus dem Manifest), ``gestartet`` = frühester Start.
    """
    alt = json.loads(von.read_text(encoding="utf-8"))
    zustand = lade_zustand(ablage.zustand_datei)
    zustand["gesendet"] = sorted(set(zustand["gesendet"]) | set(alt.get("gesendet") or []))
    zustand["start"] = {**(alt.get("start") or {}), **zustand["start"]}
    zustand["ende"] = {**(alt.get("ende") or {}), **zustand["ende"]}
    for feld in ("live_inhalt", "live_zeit"):
        zustand[feld] = zustand.get(feld) or alt.get(feld)
    manifest = manifest_modul.lade_manifest(ablage.repo, ablage.spec) or {}
    seed_ids = ["meta/stand", *(f"tickets/{n}" for n in (manifest.get("tickets") or {}))]
    zustand["seed_gesendet"] = sorted(set(zustand["seed_gesendet"]) | set(seed_ids))
    starts = sorted(zustand["start"].values())
    zustand["gestartet"] = zustand.get("gestartet") or (starts[0] if starts else None)
    speichere_json(ablage.zustand_datei, zustand)
    if url:
        setze_url(ablage, url)
    log.info("Übernommen aus %s: %d Log-IDs, %d seed-Dokumente.", von, len(zustand["gesendet"]), len(seed_ids))
    return 0


# ---------------------------------------------------------------- CLI


def richte_logging_ein(datei: Path) -> None:
    datei.parent.mkdir(parents=True, exist_ok=True)
    format_ = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    handler = logging.handlers.RotatingFileHandler(datei, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(format_)
    konsole = logging.StreamHandler(sys.stderr)
    konsole.setFormatter(format_)
    logging.basicConfig(level=logging.INFO, handlers=[handler, konsole], force=True)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", help="Spec-Nummer (Manifest docs/agents/manifests/spec-<S>.json)")
    unter = ap.add_subparsers(dest="befehl", required=True)
    s = unter.add_parser("seed", help="meta/stand + tickets/<nr> als writes (nur Ungesendetes)")
    s.add_argument("--ziel", choices=("local", "lokal", "server", "srv", "remote"), help="Vorgabe: Konfig ziel_default")
    s.add_argument("--gestartet", help="Startzeit ISO (Vorgabe: gemerkt oder jetzt)")
    unter.add_parser("vorbereiten", help="live/log rechnen; Exit 0 Neues, 3 nichts")
    unter.add_parser("bestaetigen", help="writes-<S>.json als gesendet merken")
    u = unter.add_parser("url", help="gespeicherte Artifact-URL ausgeben (Exit 4 = keine)")
    u.add_argument("--setzen", metavar="URL", help="Artifact-URL speichern")
    unter.add_parser("seite", help="Vorlage für diese Spec rendern (Pfad auf stdout)")
    unter.add_parser("anweisung", help="Takt-Anweisung takt-<S>.md schreiben (Pfad auf stdout)")
    unter.add_parser("mail", help="Start-Mail mit Link, genau einmal je Spec")
    unter.add_parser("aktiv", help="Exit 0 = leitstand.aktiv an, 5 = aus")
    si = unter.add_parser("sitzung", help="interaktive Leitstand-Session in diesem Fenster starten")
    si.add_argument("--probe", action="store_true", help="nur Befehl als JSON ausgeben, nichts starten")
    ue = unter.add_parser("uebernehmen", help="Zustand des Prototyps übernehmen")
    ue.add_argument("--von", required=True, type=Path, metavar="ZUSTAND_JSON")
    ue.add_argument("--url", help="Artifact-URL gleich mit speichern")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = parser().parse_args(argv)
    if not str(a.spec).isdigit():
        print(f"Spec muss eine Nummer sein: {a.spec}", file=sys.stderr)
        return 2
    ablage = Ablage(repo_ermitteln(), str(a.spec))
    richte_logging_ein(ablage.log_datei)
    try:
        return _main(a, ablage)
    finally:
        # Anweisung spiegelt den neuen Stand (URL, seed, Mail) — nur wenn es sie schon gibt.
        if a.befehl not in ("anweisung", "sitzung") and ablage.anweisung_datei.exists():
            try:
                schreibe_anweisung(ablage)
            except (OSError, ValueError) as fehler:
                log.warning("Takt-Anweisung nicht erneuert: %s", fehler)


def _main(a: argparse.Namespace, ablage: Ablage) -> int:
    if a.befehl in ("url", "seite", "anweisung", "mail", "aktiv", "sitzung"):
        try:
            return fuehre_aus(a, ablage)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as fehler:
            log.exception("%s fehlgeschlagen: %s", a.befehl, fehler)
            return 1
    sperre = nimm_sperre(ablage.sperre_datei)
    if sperre is None:
        log.error("Leitstand für Spec %s läuft schon (Sperre belegt).", ablage.spec)
        return 2
    try:
        if a.befehl == "bestaetigen":
            return bestaetigen(ablage)
        if a.befehl == "uebernehmen":
            return uebernehmen(ablage, a.von, a.url)
        writes = seed(ablage, a.ziel, a.gestartet) if a.befehl == "seed" else vorbereiten(ablage)
    except (OSError, RuntimeError, ValueError, KeyError, ImportError, subprocess.SubprocessError) as fehler:
        log.exception("%s fehlgeschlagen: %s", a.befehl, fehler)
        return 1
    finally:
        sperre.close()
    if not writes:
        return EXIT_NICHTS
    print(ablage.writes_datei.as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
