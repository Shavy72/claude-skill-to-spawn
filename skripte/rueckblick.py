"""Rückblick einer fertigen Spec: Bau-Logs aller Tickets einsammeln und Auftrag an die Rückblick-KI schreiben.

Aufruf:
``python rueckblick.py planen <S> [--repo <pfad>] [--ohne-bau-server]``

``planen`` liest das Manifest ``docs/agents/manifests/spec-<S>.json``, vereinigt je Ticket die
Bau-Log-Zeilen aller Ablageorte (doppelte Zeilen einmal) und schreibt
``.to-spawn/rueckblick/<S>/auftrag.md`` + ``lauf.json`` (Sperre). Erste Zeile auf stdout und im
Auftrag: ``Datenlage: <x> von <n> Tickets mit Bau-Log``. Gezählt werden nur feste Namen (Feld
``regel`` der ``vorfall``-Zeilen), freie Texte nie. Schritt „sammeln“ (Marker
``docs/agents/rueckblick_<S>.md``) ist nicht Teil dieses Skripts.

Exit-Codes wie ``thermo_lauf.py plan``: 0 = Auftrag geschrieben, 4 = schon erledigt (Marker da)
oder läuft schon (Sperre jünger als 120 min), 1 = Fehler. (Thermos 2 „keine Code-Änderung“ gibt es
hier nicht — fehlende Logs sind eine Datenlage, kein Abbruch.)

Ablageorte:
- Repo: ``<repo>/docs/agents/bau_log/<N>.jsonl``
- Laufdateien: ``<wt>/.to-spawn/bau_log/<N>.jsonl`` für jeden Worktree aus ``git worktree list``
  (das Repo selbst ist der erste Worktree)
- Bau-Server: ``server_repo`` + alle Spec-Klone daneben (Ordner ``<Name>-<Zahl>`` mit demselben
  ``origin``, z. B. ``duoplus-551``), je samt Worktrees. Vom PC aus per ``ssh -o BatchMode=yes <ssh_ziel>`` über den
  versteckten Unterbefehl ``logs <S>`` dieses Skripts (installierter Skill) im ``server_repo``.

Jeder Ort bekommt eine Statuszeile; Orte, die fehlen, nicht lesbar/erreichbar sind, ausgelassen
wurden oder Zeilen verwerfen, sind 🔴 und lösen ``🔴 Datenlage unvollständig`` im Kopf und in der
Zählung aus — „kein Vorfall“ steht so nie ohne Warnung.

Gelesen wird über ``bau_log.rohzeilen``, nicht ``bau_log.lese``: ``lese`` verrechnet genau eine
versionierte Datei mit Laufdateien als Mehrfachmenge; hier werden beliebig viele Orte vereinigt
(gleiche Zeile = einmal), über kanonischen JSON-Text, damit auch die SSH-Antwort mitzählt.

Bau-Server-Erkennung: existiert der Ordner ``server_repo`` (Konfig, sonst ``~/<Repo-Name>`` wie beim
Umzug) auf diesem Rechner, ist dieser Rechner der Bau-Server — dann wird der Ordner samt Worktrees
lokal gelesen, ohne SSH. Hostnamen taugen nicht: ``ssh_ziel`` ist ein SSH-Alias, der Rechner selbst
heißt anders. Auf dem PC gibt es den Linux-Pfad des Servers nicht.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

# Skill-Wurzel in sys.path, damit ``to_spawn`` importierbar ist (#205).
_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from thermo_lauf import SPERRE_MIN, Fehler, sperre_aktiv  # noqa: E402

from to_spawn import config  # noqa: E402
from to_spawn.bau_log import LAUF_ORDNER, LOG_ORDNER, rohzeilen  # noqa: E402
from to_spawn.spawn import fern_ordner  # noqa: E402
from to_spawn.umzug import _ssh, server_repo  # noqa: E402

log = logging.getLogger("rueckblick")

SSH_TIMEOUT_S = 60
GIT_TIMEOUT_S = 30
UNVOLLSTAENDIG = "🔴 Datenlage unvollständig"
#: Ort = (Statuszeile, rot). Rot = dort fehlen womöglich Daten.
Ort = tuple[str, bool]
#: Pfad dieses Skripts im installierten Skill auf dem Bau-Server.
FERN_SKRIPT = "~/.claude/skills/to-spawn/skripte/rueckblick.py"

AUFTRAG = """## Auftrag an die Rückblick-KI

Methode: Skill `mp-retro`. Werte nur die Daten oben aus, rate keine fehlenden Logs dazu.

Leitfrage je Fehlerbild: „Erkannt wird er schon, wie verhindern wir ihn vorher?“

1. Ähnliche freie Texte (Schwierigkeiten) zu Fehlerbildern gruppieren; feste Namen aus der Zählung
   sind schon gruppiert.
2. Vor jeder Maßnahme prüfen, ob es eine passende Prüfung schon gibt, sie aber nicht angeschlossen
   oder kaputt ist — genau das ist dann der Befund (Prüfung anschließen/reparieren), keine neue.
3. Erkennt eine Prüfung den Fehler schon (Beispiel `session_verwaist`), ist der Kandidat
   „Ursache beheben“: ein Ticket gegen die Ursache, keine neue Text-Zeile.
4. Je Kandidat den höchsten möglichen Weg wählen, stärkster zuerst:
   - Weg 1: Prüf-Skript / capo-Regel / Hook (bricht maschinell, kein Agent überliest es)
   - Weg 2: Review-Auftrag für `review-dirigent`
   - Weg 3: Hinweis im Ticket-Kontext-Paket von `to-tickets`
   - Weg 4: Text-Regel in CLAUDE.md — Weg 4 nur mit Begründung, warum Weg 1–3 nicht gehen.
5. Die 2–3 teuersten Kandidaten (Tickets × Aufwand) oben.

## Ergebnisformat

Schreibe `ergebnis.json` in diesen Ordner (liest der spätere Schritt „sammeln“):

```json
{"spec": <S>, "kandidaten": [{
  "titel": "kurz",
  "fehlerbild": "fester Name oder Gruppe freier Texte",
  "tickets": [331, 333],
  "kosten": "warum teuer (Zeit, Runden, Tokens)",
  "weg": 1,
  "massnahme": "konkret: welche Datei/Regel/welcher Prüfer",
  "vorhandene_pruefung": "nicht angeschlossen | kaputt | erkennt schon | keine",
  "ursache_beheben": false,
  "begruendung_weg4": "nur bei weg 4: warum 1–3 nicht gehen"
}]}
```
Reihenfolge der Liste = Rang, teuerster zuerst.
"""


def rueckblick_ordner(repo: Path, spec: int) -> Path:
    return repo / ".to-spawn" / "rueckblick" / str(spec)


def marker_pfad(repo: Path, spec: int) -> Path:
    return repo / "docs" / "agents" / f"rueckblick_{spec}.md"


def lade_tickets(repo: Path, spec: int) -> list[str]:
    """Ticketnummern aus dem Manifest — ``tickets`` als dict (Nummer → Daten) oder Liste."""
    pfad = repo / "docs" / "agents" / "manifests" / f"spec-{spec}.json"
    try:
        tickets = json.loads(pfad.read_text(encoding="utf-8")).get("tickets") or []
    except (OSError, ValueError, AttributeError) as fehler:
        raise Fehler(f"Manifest {pfad} nicht lesbar: {fehler}") from fehler
    if not isinstance(tickets, (dict, list)) or not tickets:
        raise Fehler(f"Manifest {pfad}: 'tickets' leer oder weder dict noch Liste")
    falsch = [t for t in tickets if isinstance(t, bool) or not isinstance(t, (int, str))]
    if falsch:
        raise Fehler(f"Manifest {pfad}: Ticketelemente weder Zahl noch Text: {falsch[:3]}")
    return [str(t) for t in tickets]


def _worktrees(repo: Path) -> tuple[list[Path], str | None]:
    """Alle Worktree-Ordner des Repos (Repo selbst zuerst) + Fehlergrund, falls git scheitert."""
    try:
        lauf = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [repo], f"git worktree list: keine Antwort nach {GIT_TIMEOUT_S} s"
    except OSError as fehler:
        return [repo], f"git nicht aufrufbar: {fehler}"
    if lauf.returncode != 0:
        return [repo], lauf.stderr.strip() or f"git Exit {lauf.returncode}"
    ordner = [repo]
    for zeile in lauf.stdout.splitlines():
        if zeile.startswith("worktree "):
            pfad = Path(zeile[len("worktree ") :])
            if pfad.resolve() != repo.resolve():
                ordner.append(pfad)
    return ordner, None


def _kanonisch(roh: Iterable[object]) -> tuple[set[str], int]:
    """Rohzeilen → kanonischer JSON-Text (sortierte Schlüssel) + Zahl verworfener Zeilen.

    Ein Parser für Dateien und SSH-Antwort: gleiche Zeile = gleicher Text, egal woher.
    """
    gut: set[str] = set()
    verworfen = 0
    for zeile in roh:
        try:
            daten = json.loads(zeile)  # type: ignore[arg-type]
        except (ValueError, TypeError):
            verworfen += 1
            continue
        if isinstance(daten, dict):
            gut.add(json.dumps(daten, ensure_ascii=False, sort_keys=True))
        else:
            verworfen += 1
    return gut, verworfen


def _lies_dateien(logs: dict[str, set[str]], dateien: Iterable[tuple[str, Path]]) -> tuple[int, list[str]]:
    """Jede (Ticket, Datei) in ``logs`` einlesen; zurück: verworfene Zeilen, unlesbare Dateien."""
    verworfen = 0
    unlesbar: list[str] = []
    for ticket, pfad in dateien:
        try:
            roh = rohzeilen(pfad)
        except (OSError, UnicodeDecodeError) as fehler:
            log.warning("%s nicht lesbar: %s", pfad, fehler)
            unlesbar.append(pfad.as_posix())
            continue
        gut, weg = _kanonisch(roh)
        logs[ticket] |= gut
        verworfen += weg
    return verworfen, unlesbar


def _ort(name: str, verworfen: int, unlesbar: list[str]) -> Ort:
    teile = [f"nicht lesbar ({', '.join(unlesbar)})" if unlesbar else "gelesen"]
    if verworfen:
        teile.append(f"{verworfen} Zeilen verworfen")
    return f"{name}: {', '.join(teile)}", bool(unlesbar or verworfen)


def lokale_logs(repo: Path, tickets: Iterable[str]) -> tuple[dict[str, set[str]], list[Ort]]:
    """Bau-Log-Zeilen je Ticket aus Repo + Laufdateien aller Worktrees, dazu je Ort eine Statuszeile."""
    tickets = list(tickets)
    logs: dict[str, set[str]] = {t: set() for t in tickets}
    ordner = repo / LOG_ORDNER
    if ordner.is_dir():
        dateien = ((t, ordner / f"{t}.jsonl") for t in tickets)
        orte = [_ort(f"Repo {ordner.as_posix()}", *_lies_dateien(logs, dateien))]
    else:
        orte = [(f"Repo {ordner.as_posix()}: fehlt", True)]
    worktrees, fehler = _worktrees(repo)
    dateien = ((t, wt / LAUF_ORDNER / f"{t}.jsonl") for wt in worktrees for t in tickets)
    name = f"Laufdateien {repo.name}/{LAUF_ORDNER.as_posix()} in {len(worktrees)} Worktree(s)"
    orte.append(_ort(name, *_lies_dateien(logs, dateien)))
    if fehler:
        orte.append((f"Worktrees von {repo.name}: nicht erreichbar ({fehler})", True))
    return logs, orte


def _origin(repo: Path) -> str | None:
    try:
        lauf = subprocess.run(
            ["git", "-C", str(repo), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        log.warning("origin von %s nicht lesbar: %s", repo, fehler)
        return None
    return lauf.stdout.strip() or None


def server_klone(server: Path) -> tuple[list[Path], list[Ort]]:
    """``server_repo`` + Spec-Klone daneben: Ordner ``<Name>-<Zahl>`` mit demselben ``origin``.

    Erkannt über ``origin``, nicht über den Namen: der Bau-Server klont ``duoplus-management`` als
    ``duoplus-551``, der Präfix ist also nicht der Name des ``server_repo``. Lässt sich nicht suchen
    (kein ``origin``, Ordner nicht auflistbar), kommt nur ``server`` zurück plus ein roter Ort.
    """
    name = f"Spec-Klone neben {server.as_posix()}"
    eigen = _origin(server)
    if eigen is None:
        return [server], [(f"{name}: nicht gesucht (kein origin in {server.name})", True)]
    try:
        kandidaten = sorted(p for p in server.parent.iterdir() if p != server and re.fullmatch(r".+-\d+", p.name))
    except OSError as fehler:
        log.warning("%s nicht auflistbar: %s", server.parent, fehler)
        return [server], [(f"{name}: nicht lesbar ({fehler})", True)]
    return [server, *(p for p in kandidaten if p.is_dir() and _origin(p) == eigen)], []


def server_logs(server: Path, tickets: list[str]) -> tuple[dict[str, set[str]], list[Ort]]:
    """Logs aller Klone des Bau-Servers samt Worktrees — lokal und im Fern-Unterbefehl ``logs`` gleich."""
    logs: dict[str, set[str]] = {t: set() for t in tickets}
    klone, orte = server_klone(server)
    for klon in klone:
        teil, teil_orte = lokale_logs(klon, tickets)
        for t in tickets:
            logs[t] |= teil[t]
        orte += teil_orte
    return logs, orte


def _fern_antwort(name: str, stdout: str) -> tuple[dict[str, set[str]], list[Ort]]:
    """JSON des Fern-Unterbefehls ``logs`` prüfen; jede Abweichung = Ort „Antwort unbrauchbar“."""
    try:
        daten = json.loads(stdout)
        tickets, orte = daten["tickets"], daten["orte"]
        if not isinstance(tickets, dict) or not all(isinstance(z, list) for z in tickets.values()):
            raise TypeError("'tickets' ist kein dict aus Listen")
        if not isinstance(orte, list) or not all(
            isinstance(o, dict) and isinstance(o.get("text"), str) and isinstance(o.get("rot"), bool)
            for o in orte
        ):
            raise TypeError("'orte' ist keine Liste aus {text, rot}")
    except (ValueError, KeyError, TypeError) as fehler:
        return {}, [(f"{name}: Antwort unbrauchbar ({fehler})", True)]
    logs: dict[str, set[str]] = {}
    verworfen = 0
    for t, zeilen in tickets.items():
        logs[str(t)], weg = _kanonisch(zeilen)
        verworfen += weg
    ergebnis: list[Ort] = [(f"{name}: per SSH gelesen", False)]
    ergebnis += [(f"Bau-Server: {o['text']}", o["rot"]) for o in orte]
    if verworfen:
        ergebnis.append((f"{name}: Antwort, {verworfen} Zeilen verworfen", True))
    return logs, ergebnis


def _bau_server(
    repo: Path, spec: int, tickets: list[str], ohne: bool
) -> tuple[dict[str, set[str]], list[Ort]]:
    """Logs vom Bau-Server: lokal gelesen, per SSH geholt oder mit Grund ausgelassen — nie Absturz."""
    if ohne:
        return {}, [("Bau-Server: ausgelassen (--ohne-bau-server)", True)]
    konfig = config.lade(repo)
    ziel = str(konfig.get("ssh_ziel") or "").strip()
    ordner = server_repo(konfig, repo)
    lokal = Path(ordner).expanduser()
    if lokal.is_dir():
        logs, orte = server_logs(lokal.resolve(), tickets)
        kopf: Ort = (f"Bau-Server: dieser Rechner, {lokal.as_posix()} + Spec-Klone lokal gelesen", False)
        return logs, [kopf, *((f"Bau-Server: {text}", rot) for text, rot in orte)]
    if not ziel:
        return {}, [("Bau-Server: nicht erreichbar (kein ssh_ziel in .to-spawn/config.json)", True)]
    name = f"Bau-Server {ziel}:{ordner}"
    lauf = _ssh(ziel, f"cd {fern_ordner(ordner)} && python3 {FERN_SKRIPT} logs {spec} --repo .", SSH_TIMEOUT_S)
    if lauf is None:
        return {}, [(f"{name}: nicht erreichbar (keine Antwort nach {SSH_TIMEOUT_S} s oder ssh fehlt)", True)]
    if lauf.returncode == 2:
        log.warning("Fernaufruf Exit 2: %s", lauf.stderr.strip())
        return {}, [(f"{name}: Skill-Stand auf dem Bau-Server alt (to-spawn neu installieren)", True)]
    if lauf.returncode != 0:
        grund = (lauf.stderr.strip().splitlines() or [f"Exit {lauf.returncode}"])[-1]
        log.warning("Bau-Server-Abruf gescheitert: %s", lauf.stderr.strip())
        return {}, [(f"{name}: nicht erreichbar ({grund})", True)]
    return _fern_antwort(name, lauf.stdout)


def _zeilen(logs: set[str]) -> list[dict[str, Any]]:
    return sorted((json.loads(z) for z in logs), key=lambda z: str(z.get("ts") or ""))


def zaehle_namen(logs: dict[str, list[dict[str, Any]]]) -> list[tuple[str, list[str]]]:
    """Fester Name (``regel`` der Vorfälle) → Tickets; meiste Tickets zuerst, dann Name."""
    namen: dict[str, set[str]] = {}
    for ticket, zeilen in logs.items():
        for z in zeilen:
            regel = z.get("regel")
            if z.get("typ") == "vorfall" and isinstance(regel, str) and regel.strip():
                namen.setdefault(regel.strip(), set()).add(ticket)
    return sorted(
        ((n, sorted(t, key=_nr)) for n, t in namen.items()), key=lambda p: (-len(p[1]), p[0])
    )


def _nr(ticket: str) -> tuple[int, str]:
    return (int(ticket), ticket) if ticket.isdigit() else (sys.maxsize, ticket)


def _namen_zeile(name: str, tickets: list[str]) -> str:
    wort = "Ticket" if len(tickets) == 1 else "Tickets"
    return f"- {name}: {len(tickets)} {wort} ({', '.join('#' + t for t in tickets)})"


def datenlage(tickets: list[str], logs: dict[str, list[dict[str, Any]]], orte: list[Ort]) -> list[str]:
    mit = [t for t in tickets if logs.get(t)]
    zeilen = [f"Datenlage: {len(mit)} von {len(tickets)} Tickets mit Bau-Log"]
    if any(rot for _, rot in orte):
        zeilen.append(UNVOLLSTAENDIG)
    zeilen += [f"- {'🔴 ' if rot else ''}{text}" for text, rot in orte]
    zeilen += [f"🔴 #{t}: kein Bau-Log" for t in tickets if not logs.get(t)]
    return zeilen


def auftrag_text(spec: int, tickets: list[str], logs: dict[str, list[dict[str, Any]]], kopf: list[str]) -> str:
    teile = [*kopf, "", f"# Rückblick Spec #{spec}", "", "## Zählung feste Namen", ""]
    if UNVOLLSTAENDIG in kopf:
        teile += [f"{UNVOLLSTAENDIG}: nicht alle Ablageorte gelesen (siehe Kopf) — Zählung kann Vorfälle übersehen.", ""]
    if not any(logs.get(t) for t in tickets):
        teile.append("Keine Auswertung möglich: kein einziges Bau-Log gefunden.")
    else:
        namen = zaehle_namen(logs)
        oben = [p for p in namen if len(p[1]) >= 2]
        rest = [p for p in namen if len(p[1]) < 2]
        teile += [_namen_zeile(*p) for p in oben] or ["(kein fester Name bei mindestens 2 Tickets)"]
        if rest:
            teile += ["", "Übrige feste Namen (1 Ticket):", *(_namen_zeile(*p) for p in rest)]
        if not namen:
            teile.append("Kein Vorfall mit festem Namen in den gefundenen Logs.")
    teile += ["", "## Schwierigkeiten je Ticket", ""]
    for t in tickets:
        if not logs.get(t):
            teile.append(f"🔴 #{t}: kein Bau-Log")
            continue
        texte = [
            str(z["schwierigkeiten"]).strip()
            for z in logs[t]
            if z.get("typ") == "zusammenfassung" and str(z.get("schwierigkeiten") or "").strip()
        ]
        teile.append(f"### #{t}")
        teile += [f"- {s}" for s in dict.fromkeys(texte)] or ["- (keine Schwierigkeiten eingetragen)"]
    teile += ["", AUFTRAG]
    return "\n".join(teile)


def sammle(
    repo: Path, spec: int, tickets: list[str], ohne_bau_server: bool
) -> tuple[dict[str, list[dict[str, Any]]], list[Ort]]:
    roh, orte = lokale_logs(repo, tickets)
    fern, fern_orte = _bau_server(repo, spec, tickets, ohne_bau_server)
    orte += fern_orte
    for t, zeilen in fern.items():
        if t in roh:
            roh[t] |= zeilen
    return {t: _zeilen(z) for t, z in roh.items()}, orte


def planen(repo: Path, spec: int, ohne_bau_server: bool) -> int:
    ordner = rueckblick_ordner(repo, spec)
    marker = marker_pfad(repo, spec)
    if marker.exists():
        print(f"Schon erledigt: {marker}")
        return 4
    sperre = ordner / "lauf.json"
    laeuft = f"Rückblick Spec #{spec} läuft schon (Sperre {sperre}, jünger als {SPERRE_MIN} min)."
    if sperre_aktiv(sperre):
        print(laeuft)
        return 4
    tickets = lade_tickets(repo, spec)
    ordner.mkdir(parents=True, exist_ok=True)
    start = datetime.now().astimezone().isoformat(timespec="seconds")
    if not _sperre_anlegen(sperre, {"start": start, "spec": spec}):
        print(laeuft)
        return 4
    try:
        return _planen_gesperrt(repo, spec, tickets, ohne_bau_server, start)
    except BaseException:
        sperre.unlink(missing_ok=True)  # Fehlversuch darf keinen 120-min-Stillstand hinterlassen
        raise


def _sperre_anlegen(sperre: Path, inhalt: dict[str, Any]) -> bool:
    """Sperre atomar anlegen (``open(..., "x")``); eine abgelaufene wird vorher entfernt. ``False`` = belegt."""
    for _ in range(2):
        try:
            datei = sperre.open("x", encoding="utf-8")
        except FileExistsError:
            if sperre_aktiv(sperre):
                return False
            # ponytail: zwei Läufe, die dieselbe abgelaufene Sperre gleichzeitig löschen, können beide
            # starten; Upgrade: Sperre per os.replace auf eindeutigen Namen übernehmen.
            sperre.unlink(missing_ok=True)
            continue
        fertig = False
        try:
            with datei:
                datei.write(json.dumps(inhalt, ensure_ascii=False))
            fertig = True
        finally:
            if not fertig:  # halbe Sperre darf keinen 120-min-Stillstand hinterlassen (Datei erst zu, dann weg)
                sperre.unlink(missing_ok=True)
        return True
    return False


def _planen_gesperrt(repo: Path, spec: int, tickets: list[str], ohne_bau_server: bool, start: str) -> int:
    ordner = rueckblick_ordner(repo, spec)
    sperre = ordner / "lauf.json"
    logs, orte = sammle(repo, spec, tickets, ohne_bau_server)
    kopf = datenlage(tickets, logs, orte)
    auftrag = ordner / "auftrag.md"
    auftrag.write_text(auftrag_text(spec, tickets, logs, kopf), encoding="utf-8")
    lauf = {
        "start": start,
        "spec": spec,
        "tickets": tickets,
        "mit_log": [t for t in tickets if logs.get(t)],
        "ohne_log": [t for t in tickets if not logs.get(t)],
        "orte": [text for text, _ in orte],
        "auftrag": str(auftrag),
        "ergebnis": str(ordner / "ergebnis.json"),
    }
    sperre.write_text(json.dumps(lauf, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(kopf))
    print(f"Auftrag: {auftrag}")
    return 0


def logs_ausgeben(repo: Path, spec: int) -> int:
    """Versteckter Unterbefehl für den SSH-Abruf: lokale Zeilen je Ticket als JSON."""
    roh, orte = server_logs(repo, lade_tickets(repo, spec))
    antwort = {
        "tickets": {t: sorted(z) for t, z in roh.items()},
        "orte": [{"text": text, "rot": rot} for text, rot in orte],
    }
    print(json.dumps(antwort, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Rückblick einer Spec planen.")
    unter = parser.add_subparsers(dest="befehl", required=True)
    p_plan = unter.add_parser("planen")
    p_plan.add_argument("spec", type=int)
    p_plan.add_argument("--repo", default=".")
    p_plan.add_argument("--ohne-bau-server", action="store_true")
    p_logs = unter.add_parser("logs")
    p_logs.add_argument("spec", type=int)
    p_logs.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        if args.befehl == "planen":
            return planen(repo, args.spec, args.ohne_bau_server)
        return logs_ausgeben(repo, args.spec)
    except Fehler as fehler:
        log.error("%s", fehler)
        return 1
    except Exception as fehler:  # Sperre räumt planen() selbst ab
        log.error("Unerwarteter Fehler (%s): %s", type(fehler).__name__, fehler)
        return 1


if __name__ == "__main__":
    sys.exit(main())
