"""Bau-Aufseher-Session für eine Spec starten (frische Claude-Session, Opus mit vollem Kontextfenster).

Aufruf: ``python scripts/wache.py <S> [--model <m>] [--takt <s>] [--dry-run] [--print-prompt] [--resume <id>]``
Selbstablösung aus dem laufenden Aufseher: ``python scripts/wache.py <S> --abloesen <handoff>``.

Der Aufseher ist eine voll fähige Session und verantwortlich, dass der Bau autonom durchläuft:
je Tick ``scripts/capo.py <S>``, Sessions prüfen, hängende Tickets per
``to_spawn.py neustart`` ablösen, Übergaben an der Smart-Zone-Grenze starten. Stand in
``docs/HANDOFF_<datum>_waechter_<S>.md``.

Start mit ``--effort`` (Konfig ``effort.waechter``, Vorgabe medium), Opus-Modelle mit
``[1m]`` (volles Kontextfenster wie eine normale Session), ``--remote-control "Aufseher #<S>"``
(Konfig ``waechter.remote_control``) und ``--fallback-model`` (``modelle.waechter_ausweich``).
Beim Nutzungs-Limit wechselt die Aufsicht (``to_spawn/waechter_lauf.py``) selbst auf das
Ausweich-Modell (#213). ``--abloesen`` legt die Ablöse-Datei an; die Aufsicht beendet die
Session und startet im selben Fenster den Nachfolge-Aufseher mit dem Handoff als Startkontext.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

# Skill-Wurzel in sys.path, damit ``to_spawn.config`` (Repo-Wurzel, Konfig) importierbar ist (#205).
_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import (  # noqa: E402
    anleitung,
    aufseher_stand,
    befugnis,
    config,
    context_mode,
    briefkasten_wecker,
    handoff_blick,
    konsole,
    manifest,
    nest,
    sitzung_env,
    speicher,
    starter,
    startklar,
    temp_ordner,
    terminal_maus,
    umzug,
    vertrauen,
    waechter_lauf,
)

log = logging.getLogger("wache")
#: Repo, in dem gearbeitet wird: ``TO_SPAWN_REPO`` (setzt die Weiterleitung im Repo), sonst
#: Git-Wurzel des aktuellen Ordners — nie der Ort dieses Skripts (liegt im Skill, #205).
def start_ordner() -> Path:
    """Ordner der Aufseher-Session (#542): der aktuelle Ordner, wenn er in einem Git-Repo
    liegt (Haupt-Repo oder Worktree — dort läuft der Aufseher, #450 F1). Sonst
    ``TO_SPAWN_REPO`` bzw. ``REPO`` aus der Umgebung, wenn das ein Ordner ist: Ein Neustart
    aus einem fremden Ordner (z. B. Aufpasser, tmux im Heimordner) landet so trotzdem im Repo."""
    cwd = Path.cwd().resolve()
    if any((k / ".git").exists() for k in (cwd, *cwd.parents)):
        return cwd
    for name in ("TO_SPAWN_REPO", "REPO"):
        wert = os.environ.get(name, "").strip()
        if wert and Path(wert).expanduser().is_dir():
            return Path(wert).expanduser().resolve()
    return Path.cwd()


REPO_ORDNER = Path(os.environ["TO_SPAWN_REPO"]).resolve() if os.environ.get("TO_SPAWN_REPO") else config.repo_wurzel(start_ordner())


def repo_aus_origin(fallback: str = "") -> str:
    """``owner/name`` aus ``git remote get-url origin`` (GitHub, https oder ssh); sonst ``fallback``.

    Damit läuft dasselbe Skript in jedem Repo mit GitHub-Origin — nichts hart verdrahtet.
    Gelesen wird im Arbeits-Repo ``REPO_ORDNER``, nicht im aktuellen Ordner (#257).
    """
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(REPO_ORDNER),
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        return fallback
    m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", url)
    return m.group(1) if m else fallback


KEIN_GITHUB_REPO = (
    "Kein GitHub-Repo erkannt (origin fehlt oder zeigt nicht auf github.com) — "
    "im Repo-Ordner starten oder TO_SPAWN_REPO setzen"
)


def repo_slug_oder_abbruch() -> str:
    """``owner/name`` aus dem origin von ``REPO_ORDNER`` — ohne GitHub-Origin Exit 2 (#257 F5).

    Fehlt der GitHub-Origin, gilt die ausdrückliche Vorgabe ``TO_SPAWN_GH_REPO`` (z. B.
    Spiegel-Repo mit lokalem origin, Tests); ein stiller Rückfall auf ein festes Repo
    gibt es nicht mehr.
    """
    slug = repo_aus_origin("") or os.environ.get("TO_SPAWN_GH_REPO", "").strip()
    if slug:
        return slug
    print(KEIN_GITHUB_REPO, file=sys.stderr)
    raise SystemExit(2)


#: Beim Import nur der beste Versuch (leer ohne GitHub-Origin); ``main()`` setzt verbindlich.
REPO = repo_aus_origin("") or os.environ.get("TO_SPAWN_GH_REPO", "").strip()


def auf_speicher_warten(
    konfig: dict,
    *,
    wer: str = "Aufseher",
    pruefen=speicher.platz_frei_hier,
    schlafen=time.sleep,
    takt_s: int = 60,
) -> int:
    """Warten, bis ``speicher.platz_frei_hier`` (eigene tmux-Sitzung, #409) frei meldet; Rückgabe = Zahl der Wartezyklen (#257 F3)."""
    return speicher.auf_platz_warten(konfig, wer=wer, pruefen=pruefen, schlafen=schlafen, takt_s=takt_s)


#: Umgebungsvariable mit der Warte-Datei (#560): ``spawn_srv.sh`` setzt sie im Fenster ``wache <S>``
#: und schreibt dorthin die Tickets, die beim Start „wartet auf Platz“ bekamen.
WARTE_ENV = "TO_SPAWN_WARTE_DATEI"
#: Takt des Nachstarters (#560): so viele Sekunden nach „Platz frei“ startet das nächste Fenster.
NACHSTART_TAKT_S = 20


def wartende_nachstarten(
    datei: Path,
    spec: int,
    repo: Path,
    *,
    env: dict[str, str] | None = None,
    schlafen=time.sleep,
    takt_s: float | None = None,
    max_laeufe: int | None = None,
) -> int:
    """Startet die Fenster nach, die ``spawn_srv.sh`` in ``datei`` als wartend vermerkt hat (#560).

    Je Takt ein ``spawn_srv.sh <S> --tickets <liste>`` — es überspringt Laufendes und prüft vor
    jedem Fenster den Speicher. Nur ``spawn_srv.sh`` schreibt und löscht die Datei, unter seiner
    Sperre je Spec: es vereinigt neue Wartende mit der vorhandenen Liste, trägt nur gestartete oder
    schon laufende Tickets aus und löscht die Datei erst, wenn sie leer ist. Exit 0 ohne „wartet
    auf Platz“ und Datei weg = Ende; Datei noch da (weitere Wartende) oder sonst (wartet weiter,
    voll, Fehler) → nächster Takt, unbegrenzt. Leere Datei = kein Auftrag, nächster Takt (#560).
    Datei fehlt → im Takt weiter nachsehen:
    ``spawn_srv.sh`` schreibt sie erst nach der Staffel-Pause, also nach dem Start des Aufsehers.
    Rückgabe = Zahl der ``spawn_srv.sh``-Läufe. ``takt_s`` leer = ``NACHSTART_TAKT_S`` (je Takt gelesen).
    """
    from to_spawn import bash_wahl

    skript = Path(_SKILL) / "skripte" / "spawn_srv.sh"
    laeufe = 0
    while max_laeufe is None or laeufe < max_laeufe:
        if not datei.is_file():
            schlafen(NACHSTART_TAKT_S if takt_s is None else takt_s)
            continue
        tickets = datei.read_text(encoding="utf-8").strip()
        if not tickets:
            schlafen(NACHSTART_TAKT_S if takt_s is None else takt_s)
            continue
        umgebung = dict(os.environ if env is None else env, TO_SPAWN_REPO=str(repo))
        laeufe += 1
        try:
            lauf = bash_wahl.lauf(
                [str(skript), str(spec), "--tickets", tickets],
                cwd=str(repo),
                env=umgebung,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as fehler:
            log.warning("Nachstart Spec #%s: spawn_srv.sh nicht lauffähig: %s", spec, fehler)
        else:
            ausgabe = (lauf.stdout or "") + (lauf.stderr or "")
            if lauf.returncode == 0 and "wartet auf Platz" not in ausgabe:
                if not datei.is_file():
                    log.info("Nachstart Spec #%s: wartende Fenster laufen (%s)", spec, tickets)
                    return laeufe
                log.info("Nachstart Spec #%s: %s läuft, neue Warteliste da", spec, tickets)
            else:
                log.info("Nachstart Spec #%s: wartet weiter (Exit %s)", spec, lauf.returncode)
        schlafen(NACHSTART_TAKT_S if takt_s is None else takt_s)
    return laeufe


def _nachstarter_starten(spec: int, repo: Path) -> threading.Thread | None:
    """Nachstarter-Faden (#560), nur wenn ``spawn_srv.sh`` eine Warte-Datei mitgegeben hat.

    Lebt so lange wie der Aufseher: auch spätere ``spawn_srv.sh``-Läufe (Aufseher läuft schon, wird
    nicht neu gestartet) vermerken ihre Wartenden in derselben Datei.
    """
    wert = os.environ.get(WARTE_ENV, "").strip()
    if not wert:
        return None
    umgebung = dict(os.environ)  # vor dem Umgebungs-Wechsel für Claude festhalten

    def faden() -> None:
        while True:
            try:
                wartende_nachstarten(Path(wert), spec, repo, env=umgebung)
            except Exception:  # Faden darf den Aufseher nie mitreißen
                log.exception("Nachstart Spec #%s gescheitert", spec)
                time.sleep(NACHSTART_TAKT_S)

    neu = threading.Thread(target=faden, name=f"nachstart-{spec}", daemon=True)
    neu.start()
    return neu


MODELL = "claude-opus-5-5"

#: Vorgabe-Denkstufe; Quelle ist ``waechter_lauf.EFFORT`` (eine Stelle für Aufseher und Takt, #402).
EFFORT = waechter_lauf.EFFORT
#: Umgebungsvariable mit dem Pfad der Ablöse-Datei (TO_SPAWN_WACHE_ABLOESUNG, setzt ``main`` je Lauf);
#: Quelle ist ``waechter_lauf.ABLOESE_ENV`` (dort steht auch die einzige Schreibstelle, #436).
ABLOESE_ENV = waechter_lauf.ABLOESE_ENV
#: Obergrenze Selbstablösungen je Fenster.
# ponytail: feste Obergrenze gegen Ablöse-Schleifen, Zähler je Spec im Leitstand wenn Aufseher länger laufen
MAX_ABLOESUNGEN = 10
#: Zeichen-Obergrenze für den Aufseher-Handoff im Folge-Prompt (wie ``bau.py``).
HANDOFF_MAX_ZEICHEN = 40_000


def volles_fenster(modell: str) -> str:
    """Opus ohne Kontext-Angabe → ``[1m]`` (volles Fenster wie eine normale Session)."""
    if "opus" in modell and "[" not in modell:
        return modell + "[1m]"
    return modell


def prompt_bauen(
    spec: int,
    repo: str,
    takt: int,
    konfig: dict,
    *,
    repo_ordner: Path | None = None,
) -> str:
    """Aufseher-Prompt mit Server-Befehlen aus der Konfig (``ssh_ziel``, ``server_repo``)
    und dem Manifest-Pfad aus ``pfade.manifeste``."""
    # aufseher_vorlage (#450 F5): Linux/Bau-Server kennt nur ``python3``.
    vorlage = startklar.aufseher_vorlage(PROMPT)
    text = vorlage.format(
        S=spec, REPO=repo, DATUM=date.today().isoformat(), TAKT=max(600, takt), SKILL=startklar.skill_pfad()
    )
    ziel_ordner = repo_ordner if repo_ordner is not None else REPO_ORDNER
    text = text.replace("<MANIFEST>", manifest.manifest_im_repo(ziel_ordner, spec))
    return text.replace("<SSH>", str(konfig.get("ssh_ziel") or "bau-server")).replace(
        "<SERVER_REPO>", str(konfig.get("server_repo") or "<server_repo fehlt in .to-spawn/config.json>")
    )


def abloesung_anlegen(handoff: str) -> int:
    """``--abloesen``: Ablöse-Datei der laufenden Aufsicht schreiben (nur aus einem Aufseher heraus)."""
    ziel = os.environ.get(ABLOESE_ENV, "").strip()
    if not ziel:
        print(f"{ABLOESE_ENV} fehlt — --abloesen geht nur aus einem laufenden Aufseher.", file=sys.stderr)
        return 2
    pfad = (REPO_ORDNER / handoff) if not Path(handoff).is_absolute() else Path(handoff)
    if not pfad.is_file():
        print(f"Handoff {pfad} fehlt — erst schreiben und committen.", file=sys.stderr)
        return 2
    waechter_lauf.abloesung_schreiben(Path(ziel), pfad, "")
    print(f"Ablösung angelegt: Nachfolge-Aufseher startet mit {pfad}.")
    return 0


def abloese_prompt(prompt: str, handoff: Path, runde: int, spec: int | None = None, start: str = "") -> str:
    """Startkontext des Nachfolge-Aufsehers: Handoff, letzter Stand, Start-Prompt, dann der Auftrag.

    ``spec`` → jüngste Nicht-noop-Zeile der Stand-Datei (``aufseher_stand.letzter_stand``);
    ``start`` = Start-Prompt des Vorgängers (erzwungene Ablösung über respawn, #436).
    """
    try:
        inhalt = handoff.read_text(encoding="utf-8")
    except OSError as fehler:
        inhalt = f"(Handoff {handoff} nicht lesbar: {fehler})"
    if len(inhalt) > HANDOFF_MAX_ZEICHEN:
        inhalt = inhalt[:HANDOFF_MAX_ZEICHEN] + "\n(… gekürzt)"
    stand = ""
    if spec is not None:
        try:
            zeile = aufseher_stand.letzter_stand(spec)
        except (OSError, ValueError) as fehler:
            log.warning("Stand-Datei für #%s nicht lesbar: %s", spec, fehler)
            zeile = None
        if zeile:
            zeilen = zeile.get("zeilen")
            text = "\n".join(str(z) for z in zeilen) if isinstance(zeilen, list) else json.dumps(zeile, ensure_ascii=False)
            stand = f"----- LETZTER STAND ({zeile.get('zeit') or '?'}) -----\n{text}\n----- STAND ENDE -----\n\n"
    start_teil = f"----- START-PROMPT DES VORGÄNGERS -----\n{start.strip()}\n----- START ENDE -----\n\n" if start.strip() else ""
    return (
        f"## Aufseher-Ablösung (Runde {runde})\n"
        f"Der Vorgänger-Aufseher hat an seiner Handoff-Grenze übergeben. Sein Handoff ({handoff}) ist "
        f"dein Startkontext — setze dort fort.\n\n"
        f"----- HANDOFF ANFANG -----\n{inhalt}\n----- HANDOFF ENDE -----\n\n{stand}{start_teil}{prompt}"
    )


#: Aufseher-Takt: 30 min (E28).
TAKT_S = 1800
#: Takt, wenn alle offenen Tickets nur noch auf die Abnahme des Menschen warten: 60 min (E38, #500).
TAKT_ABNAHME_S = 3600

PROMPT = """/loop Bau-Aufseher Spec #{S} ({REPO})

## Auftrag
Du bist verantwortlich, dass Spec #{S} vollständig, sauber und autonom fertig gebaut wird. Du bist eine voll fähige Session: lesen, prüfen, Subagenten nutzen, Fehler selbst beheben — was nötig ist, damit der Bau durchläuft.
- {BEFUGNIS} Klasse 3 im besten Interesse des Menschen (Nordstern, Doktrinen). Nur Label checkpoint:human bleibt für den Menschen.
- Es gelten die Regeln der Projekt-CLAUDE.md: bau.py nie beenden, ohne vorher `sessions {S}` bzw. pstree geprüft zu haben; Deploy live nur mit Freigabe des Menschen; Commits nur mit Pathspec + [skip ci].
- Eine Bau-Session hängt oder ist tot → du handelst (Befehle unten), nicht nur melden.

## Mindset (gilt für dich und jede Bau-Session; derselbe Text steht im Anstupser)
{MINDSET}

## Hänger erkennen (eine Regel, kein anderer Eingriffsweg)
- Arbeitet: Status `arbeitet` im Aufseher-Stand (Session busy, Pane ändert sich) → nie eingreifen, egal wie lange es dauert.
- Still am Prompt: Status `still` — die Session sitzt fertig oder wartend am Eingabe-Prompt, nichts ändert sich. Das ist der Hänger-Fall, auch wenn sie „nur fertig“ ist. Eingriff dann ausschließlich über die Eingriffs-Leiter je Ticket (Befehl C); sie entscheidet die Stufe selbst: still ≥ 20 min → Mindset-Stoß (Anstupser „{STOSS}“), weitere 15 min still oder Handoff-Grenze erreicht → Ablöse-SOP, Ticket zu → `/exit`. Keine eigenen Minuten-Regeln daneben.
- Rückfrage: Status `Rückfrage` — die Session steht an einem Rückfragen-/Freigabe-Fenster → Abschnitt „Rückfragen-Fenster“.
- Tot: Ticket `aus` oder VERWAIST in Befehl A, nur noch die Shell im Fenster → Befehl B.

## Rückfragen-Fenster (Freigabe-Dialoge)
Bleibt eine Bau-Session an einem Rückfragen-/Freigabe-Fenster hängen: angefragten Befehl bzw. Skript lesen. Harmlos (lesen, testen, bauen, committen, auf der Staging-App schalten) → selbst bestätigen (Auswahl per `tmux send-keys`, Text und Enter getrennt) und eine Zeile ins Bau-Log (`eintrag --typ entscheidung`). Nie bestätigen bei Löschen (`rm`, auch in `shell -c`) oder Klasse 2 – {KLASSE_2} – (z. B. `safe_deploy_vps.sh`, Live-URL, Live-API, irgendetwas in der Live-App) — dort Fenster stehen lassen, Stand-Zeile „Rückfrage wartet: <Befehl>“ statt Klick, `eintrag --typ blockiert`.

## Checkpoints (Teilabnahmen)
Checkpoints legt der Mensch beim Planen fest: im Manifest `<MANIFEST>` das Feld `teilabnahme_nach` (Ticketnummern, nach denen der Mensch einen Teil auf Staging abnimmt) bzw. das Label `checkpoint:human`. Ist so ein Ticket fertig, kommentierst du auf dem Ticket „Aufseher: Teilabnahme bereit: <Staging-Link> + Klickweg“ und wartest ohne Stillstand-Alarm — kein Anstupsen, kein Respawn, keine Mail; die Kette dahinter wartet. Der Kommentar „passt“ des Menschen gibt frei (wie bei `checkpoint:human`), dann geht die Kette weiter. Stecken bleiben ist nie erlaubt: alles bis zum Checkpoint baust du durch.

## Ablösung
{ABLOESE_SOP}

## Jeder Tick
0. `python {SKILL}/to_spawn.py aufseher-stand {S}` — erster Stand-Blick (ersetzt `~/waechter/stand.sh`): je Ticket eine Kurz-Zeile (offen/zu · arbeitet/still/Rückfrage · Kontext · Phase · letzte Aussage · neue Kommentare). Die Stand-Datei `~/.local/state/to-spawn/aufseher/stand-{S}.jsonl` ist dein Gedächtnis: je Aufruf eine Zeile, ohne Änderung `noop: true`; ein Nachfolger startet mit ihrer letzten Nicht-noop-Zeile. Kein Pane-Text in deinen Kontext.
0b. `python {SKILL}/to_spawn.py ticketstand {S}` — Ticketstand-Tabelle für den Menschen (Kopf „X von Y fertig“, je Ticket Nummer · Kurztitel · Zustand fertig/läuft/wartet/blockiert/checkpoint). Ausgabe wörtlich und unverändert an den Anfang deiner Antwort dieses Ticks stellen — jeder Tick, auch Leer-Tick, ohne dass der Mensch fragt.
1. `python {SKILL}/skripte/capo.py {S} --katalog` (mit Weiterleitung im Repo gleichwertig: `python scripts/capo.py {S} --katalog`) — Stand je Ticket, neue Bau-Log-Zeilen, Verstöße. capo öffnet selbst wieder, kommentiert verwaiste Sessions und mailt Kritisches (nicht doppelt tun); `--katalog` schreibt neue Vorfälle ins Bau-Log und nach docs/agents/FEHLERKATALOG_spawn.md (mit Pathspec mitcommitten).
2. `{AUFRAEUMEN}` — schließt Fenster übergebener Sessions.
   Schritte 1 und 2 je als eigenen Bash-Aufruf, Befehl wörtlich wie hier: ohne Shell-Variable (`$…`), ohne Umleitung oder Pipe (`2>&1`, `>`, `|`), ohne Nachbar-Befehl (`&&`, `;`) — sonst sperrt der Git-Sperr-Hook den Aufruf (#473).
3. Befehl A (unten) — läuft jedes offene, entblockte Ticket? Je Ticket mit Status `still` → Befehl C (Leiter), `aus`/VERWAIST → Befehl B, `Rückfrage` → Abschnitt „Rückfragen-Fenster“.
4. Ticket neu zu ohne Verstoß → Belegseite unter docs/verify-hard/ prüfen (Akzeptanz erfüllt? Live-Klick-Weg-Beleg mit Rolle da?); Mangel einstufen: rot (Akzeptanz nicht erfüllt / Beleg fehlt; z. B. Akzeptanz-Häkchen nicht erfüllt oder Live-Beleg fehlt) → `python {SKILL}/to_spawn.py befund --spec {S} --ticket <N> --stufe rot --text "…"` (öffnet wieder); gelb (Verbesserung/Hinweis, Akzeptanz trotzdem erfüllt, Kette darf weiter; z. B. fehlender Zusatz-Test für einen Nebenpfad) → `python {SKILL}/to_spawn.py befund --spec {S} --ticket <N> --stufe gelb --text "…"` (Folge-Ticket + Manifest-Eintrag, Ticket bleibt zu), Manifest <MANIFEST> mit Pathspec + [skip ci] committen.
5. Stand in `docs/HANDOFF_{DATUM}_waechter_{S}.md` fortschreiben (Stand + Nachträge mit Uhrzeit), Commit mit Pathspec + [skip ci], Rebase nur bei sauberem Baum (`git diff --quiet`), dann `python {SKILL}/to_spawn.py einreichen {S}` (bringt die Übergabe ohne Rückfrage auf den Hauptzweig; nie selbst auf den Hauptzweig pushen; Exit 1 → Grund lesen, beheben, erneut; Exit 2 → Stand unbekannt: denselben Befehl nach kurzer Pause erneut rufen (prüft nur, reicht nie doppelt ein); Exit 5 → Aufruf falsch: Befehl/Ordner prüfen, nicht unverändert wiederholen).
6. Takt: ScheduleWakeup {TAKT} s (ca. alle 30 min), immer (eine neue Briefkasten-Frage weckt dich vorher mit „Briefkasten: neue Frage …“ — dann nur die Fragen bearbeiten, Takt unverändert; ebenso wecken „Ticket #N ist geschlossen …“ und „Spec #S: Live ist gelaufen …“ — dann gleich den nächsten Schritt, Takt unverändert); warten alle offenen Tickets nur noch auf die Abnahme des Menschen (Checkpoint/Gesamtabnahme), dann ScheduleWakeup {TAKT_ABNAHME} s (ca. alle 60 min). Leer-Tick (nichts geändert): Ticketstand-Tabelle (0b) plus genau eine Kurz-Zeile als Antwort, noop: true, keine Rohausgaben (Pane-Text, Log-Volltexte) lesen.

## Befehle (fertig zum Kopieren; <N> = Ticket, <PID> aus Befehl A)
Ort: Aufseher im tmux des Bau-Servers → Spalte „hier“ ist der Bau-Server. Aufseher am PC → „hier“ = PC, Server-Tickets mit der Server-Form.
A Stand aller Sessions
  zuerst: `python {SKILL}/to_spawn.py aufseher-stand {S} --alle` (Kurz-Zeile je Ticket, ersetzt `~/waechter/stand.sh`)
  hier:   `python {SKILL}/skripte/sessions_stand.py {S} --alle` (aus / wartet / läuft seit / VERWAIST) · `python {SKILL}/to_spawn.py log {S}` (Token, Dauer je Ticket)
  Server: `ssh <SSH> 'cd <SERVER_REPO> && python3 scripts/sessions_stand.py {S} --alle'`
B Ticket neu starten (Ticket steht auf „aus“)
  hier:   `{NEUSTART}`
  Server: `{NEUSTART} --ziel srv`
  Erst mit `--dry-run` ansehen, dann ohne.
C Stille Session: Eingriffs-Leiter (einziger Eingriffsweg, Regel „Hänger erkennen“)
  Per Ablöse-Subagent (`model: sonnet`, 1 Zeile Antwort): `python {SKILL}/to_spawn.py leiter {S} <N>` — erst `--dry-run`, dann ohne. Die Leiter merkt die Stufe, tippt nie in ein arbeitendes Fenster oder eine Rückfrage, stößt an, fordert den Handoff an und löst per Skill `respawn` ab (Ablöse-SOP Schritt 1–5). Stufe 3 ruft `{RESPAWN}` — nie direkt, nur über die Leiter. Exit 1 = du prüfst selbst (Zeile lesen; Bau-Server `pstree -p <PID>`, PC: Kinder-Spalte in Befehl A). Am PC (kein tmux) gibt es kein Anstupsen: die Leiter springt direkt zur Ablösung (Zeile „PC: Stufe 1 übersprungen“) — alte Session beenden, neuer Windows-Terminal-Tab `bau <N>` mit Remote Control und Handoff.
  Auf dem Server läuft zusätzlich der Aufpasser (Cron, 15 min, Hausmeister für tote Fenster): `python3 ~/.claude/skills/to-spawn/skripte/aufpasser.py --trocken` (vom PC: `ssh <SSH> 'python3 ~/.claude/skills/to-spawn/skripte/aufpasser.py --trocken'`) zeigt, was er tun würde.
D Session an der Smart-Zone-Grenze (Handoff-Grenze aus ~/.claude/smart-zone.json)
  Läuft sie noch: Befehl C — die Leiter erkennt die Grenze selbst und fährt die Ablöse-SOP (Stufe 3 = `{RESPAWN}`, nie direkt). Liegt schon ein Handoff, aber die Session ist tot / Ticket „aus“:
  `{NEUSTART} --handoff docs/handoffs/HANDOFF_<datum>_<N>.md --beenden` (Server: zusätzlich `--ziel srv`). Die neue Session startet mit dem Auftrag „Weiter ab Handoff …“.
  Session mit eigenem committetem Handoff startet Leiter/Aufpasser automatisch neu; nicht von Hand nachtragen.
E Dich selbst ablösen (deine Handoff-Grenze ist erreicht) — Selbstneustart nach der Ablöse-SOP oben
  1. `docs/HANDOFF_{DATUM}_waechter_{S}.md` vollständig: Stand je Ticket, offene Entscheidungen, laufende Neustarts, nächster Schritt. Commit mit Pathspec + [skip ci], dann `python {SKILL}/to_spawn.py einreichen {S}`.
  2. `python {SKILL}/skripte/wache.py {S} --abloesen docs/HANDOFF_{DATUM}_waechter_{S}.md` — die Aufsicht beendet diese Session und startet im selben Fenster den Nachfolge-Aufseher mit dem Handoff als Startkontext. Danach nichts mehr tun.
  An der Handoff-Grenze stößt die Aufsicht die Ablösung selbst an (respawn): kommt der Auftrag „Ablösung dieser Session“, Handoff und Start-Prompt an die genannten Pfade schreiben und danach nichts mehr tun.

## Abschluss
ABSCHLUSS schon vor Live: Bau fertig, bereit zur Abnahme (alle Bau-Tickets zu oder nur noch Live-Belege/checkpoint:human-Abnahme offen, bzw. „Kette … durch“ oder „SPEC FERTIG“) → PFLICHT Abschluss-Paket `--stand abnahme` (einmal): Rundschau als Artifact: Skill `rundschau` (fehlt er: melden, kein Ersatz), Aufbau und Prüfung allein nach to_spawn/rundschau_deck.py — `python {SKILL}/skripte/rundschau_deck.py erzeugen --texte <json> --entscheidungen <json> --nach <html>` (Teil 1 Nutzer-Präsentation, Teil 2 gewohnter Rest, Schluss Entscheidungs-Zeitstrahl aus dem Bau-Verlauf; Zahl nennen), Screenshots per echtem Browser-Blick; `ablegen --stand abnahme` verlangt `--rundschau-datei <html>`, fehlt sie oder ist die Prüfung rot: Exit 4 mit Mängelliste, nichts abgelegt, `python {SKILL}/skripte/belege_uebersicht.py {S}` und `python {SKILL}/skripte/test_uebersicht.py {S}` je als Artifact, Direkt-Links je Ticket in die Stage-App (staging.url aus .to-spawn/config.json + Route an die richtige Stelle, Rolle im Titel), Zugang nur als Namen (Basic-Auth-Nutzer, App-Rolle, Bitwarden-Eintragsname — nie Passwort), dann `python {SKILL}/skripte/abschluss_paket.py ablegen {S} --stand abnahme --stage <url> --rundschau <link> --rundschau-datei <html> --belege <link> --tests <link> --direkt "<Titel (als Rolle)>=<url>"… --basic-auth-nutzer <name> --app-rolle "<Name (rolle)>" --bitwarden <eintrag>` (legt ab, schreibt docs/agents/entscheidungen_{S}.md, mailt nicht).
Danach Aufbau-Prüfung (einmal): `python {SKILL}/skripte/thermo_lauf.py plan {S}`. Exit 0 → je Eintrag in `teile` ein Subagent, alle parallel im selben Zug (`model: opus`, Prompt = Feld `prompt` unverändert); jede JSON-Antwort als `<befunde_ordner>/teil-<nr>.json` speichern, dann `python {SKILL}/skripte/thermo_lauf.py sammeln {S}`. Exit 2/4 → überspringen. Exit 3 → fehlenden Teil nachstarten, erneut sammeln. Kein Umbau in der Spec — Befunde stehen als Code-Befunde im Marker docs/agents/thermo_{S}.md (dritter Teil der Abschluss-Mail), kein Issue. Danach Rückblick (einmal): `python {SKILL}/skripte/rueckblick.py planen {S}`. Exit 0 → ein Subagent (`model: opus`, Skill mp-retro) liest `.to-spawn/rueckblick/{S}/auftrag.md` und schreibt dort `ergebnis.json` wie im Auftrag beschrieben, dann `python {SKILL}/skripte/rueckblick.py sammeln {S}` (schreibt docs/agents/rueckblick_{S}.md, mit Pathspec + [skip ci] committen + `python {SKILL}/to_spawn.py einreichen {S}`). Exit 4 → überspringen. Exit 3 → KI nachstarten, erneut sammeln. Exit 1 → Grund notieren, weiter. Zum Schluss `python {SKILL}/skripte/abschluss_paket.py nachsehen {S}` (genau eine Mail an den Menschen; nur für `--stand abnahme` gilt: Exit 3 = wartet noch, capo stößt es je Tick selbst an — nicht warten). Live-Schritt nur über `python {SKILL}/to_spawn.py live {S}` (die Tür prüft selbst vorher die Komplett-Prüfung, höchstens einmal je Spec): Exit 0 → live; Exit 1 → kein Live, Grund aus der Zeile beheben und erneut rufen; Exit 2 → `live.befehl` fehlt, Komplett-Prüfung grün, Live-Schritt nach Repo-Ablauf von Hand. Nach dem Live-Deploy dasselbe einmal mit `--stand live` (`abschluss_paket.py ablegen {S} --stand live …` + `abschluss_paket.py nachsehen {S} --stand live`); `nachsehen --stand live` bis Exit 0 wiederholen (capo stößt live nicht an).
ENDE erst bei „SPEC FERTIG“ (alle Tickets zu; capo schreibt dann docs/agents/entscheidungen_{S}.md noch einmal neu). Übersichten + Abschluss + docs/agents/entscheidungen_{S}.md + docs/agents/thermo_{S}.md + docs/agents/rueckblick_{S}.md mit Pathspec + [skip ci] committen + `python {SKILL}/to_spawn.py einreichen {S}`, Abschlussbericht als Kommentar auf #{S} (max. 10 Zeilen, die Links); bei „SPEC FERTIG“ stop: true.

Erste Zeile jeder Antwort: 🧭 Opus · medium · Aufseher #{S}"""


# Werkzeug-Befehle aus derselben Quelle wie die Startklar-Probe (#450 F5).
PROMPT = PROMPT.replace("{AUFRAEUMEN}", startklar.BEFEHL_AUFRAEUMEN).replace("{NEUSTART}", startklar.BEFEHL_NEUSTART).replace("{RESPAWN}", startklar.BEFEHL_RESPAWN)
# Feste Texte der Aufseher-Anleitung aus einer Quelle (#500).
PROMPT = PROMPT.replace("{MINDSET}", anleitung.MINDSET).replace("{STOSS}", anleitung.STOSS).replace("{ABLOESE_SOP}", anleitung.ABLOESE_SOP)
PROMPT = PROMPT.replace("{TAKT_ABNAHME}", str(TAKT_ABNAHME_S))
# Befugnis aus einer Quelle (#278 P3b): ``{SKILL}`` bleibt für ``format`` in ``prompt_bauen`` stehen.
PROMPT = PROMPT.replace("{BEFUGNIS}", befugnis.aufseher_text("python {SKILL}/to_spawn.py")).replace(
    "{KLASSE_2}", befugnis.klasse_2_erkennung()
)


def logging_einrichten(repo_ordner: Path, spec: int, auf_terminal: bool) -> Path | None:
    """Root-Logging des Aufsehers (#541): Datei ``.to-spawn/wache_<S>.log`` plus stderr, damit
    Startfehler im Fenster sichtbar bleiben. Vor dem Start von Claude ``terminal_log_abschalten()``
    rufen: danach zeichnet Claude dort sein Eingabefeld. Probeläufe ohne Claude (``auf_terminal``)
    loggen nur auf stderr."""
    fmt = "%(asctime)s %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    datei = None
    if not auf_terminal:
        datei = repo_ordner / ".to-spawn" / f"wache_{spec}.log"
        datei.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(datei, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
    return datei


def terminal_log_abschalten() -> None:
    """Entfernt die Terminal-Handler des Root-Loggers (Datei-Handler bleiben), vor Claude + Fäden (#541)."""
    root = logging.getLogger()
    for h in root.handlers[:]:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            root.removeHandler(h)


def main() -> int:
    konsole.utf8_ausgabe()  # Prompt enthält „→“, Windows-Konsole ist cp1252 (#68)
    ap = argparse.ArgumentParser(description="Bau-Aufseher-Session für eine Spec.")
    ap.add_argument("spec", type=int, help="Spec-Issue-Nummer")
    ap.add_argument(
        "--model", default=None, help=f"Claude-Modell (Vorgabe: Repo-Konfig modelle.waechter, sonst {MODELL})"
    )
    ap.add_argument("--takt", type=int, default=TAKT_S, help=f"Sekunden zwischen zwei Ticks ({TAKT_S})")
    ap.add_argument("--dry-run", action="store_true", help="nur Befehl zeigen")
    ap.add_argument("--print-prompt", action="store_true", help="nur den Prompt ausgeben")
    ap.add_argument(
        "--resume",
        default=None,
        metavar="SESSION_ID",
        help="vorhandenes Aufseher-Gespräch fortsetzen statt frisch zu starten (Aufpasser #236)",
    )
    ap.add_argument(
        "--abloesen",
        default=None,
        metavar="HANDOFF",
        help="aus dem laufenden Aufseher: Session beenden, Nachfolge-Aufseher mit diesem Handoff starten",
    )
    a = ap.parse_args()
    if a.abloesen:
        return abloesung_anlegen(a.abloesen)
    logging_einrichten(REPO_ORDNER, a.spec, a.dry_run or a.print_prompt)
    # GitHub-Slug verbindlich (#257 F5): kein GitHub-Origin → Exit 2 mit Grund, kein Rückfall.
    global REPO
    REPO = repo_slug_oder_abbruch()
    if not a.dry_run:  # Probelauf ohne Seiteneffekte (#205)
        config.sicherstellen(REPO_ORDNER)
    konfig = config.lade(REPO_ORDNER)
    a.model = a.model or volles_fenster(str(konfig.get("modelle", {}).get("waechter") or MODELL))
    effort = waechter_lauf.effort(konfig)

    prompt = prompt_bauen(a.spec, REPO, a.takt, konfig)
    if a.print_prompt:
        print(prompt)
        return 0
    # Startklar-Prüfung (#450): venv, Schlüssel, Werkzeuge vor dem Aufseher-Start;
    # nicht beim Probelauf und nicht beim Fortsetzen durch den Aufpasser (--resume).
    ordner = start_ordner()  # vor dem Entfernen von TO_SPAWN_REPO unten (#542)
    if not a.dry_run and not a.resume:
        startklar_code = startklar.gate(ordner, a.spec)  # Ordner wie fahre(cwd=…) (#450 F1)
        if startklar_code:
            return startklar_code
    if not a.dry_run:  # Push-Sperre (#1445) auch für den Aufseher, gleiche Funktion wie bau.py
        nest.git_hooks_einrichten(ordner)
    claude = starter.finde("claude")  # unter Test nur die Attrappe (#1406)
    # Pflicht-MCP context-mode (#237): der Aufseher startet ohne ``--strict-mcp-config``,
    # das Plugin-MCP lädt also von selbst — nur fehlen darf es nicht.
    try:
        ctx_wurzel = context_mode.pruefen(streng=True)
    except context_mode.ContextModeFehlt as fehler:
        log.error("%s", fehler)
        return 2
    ausweich = str(konfig.get("modelle", {}).get("waechter_ausweich") or "")
    remote_control = bool(konfig.get("waechter", {}).get("remote_control", True))
    cmd = waechter_lauf.befehl(claude, a.model, ausweich, remote_control, a.spec, prompt, effort=effort)
    log.info(
        "Aufseher Spec #%s · Modell %s · Effort %s · Ausweich %s · Takt %ss",
        a.spec,
        a.model,
        effort,
        ausweich or "-",
        a.takt,
    )
    log.info("context-mode (Pflicht-MCP, lädt als Plugin): %s", ctx_wurzel)
    if a.resume:  # Aufpasser (#236 R1): Sicherheitskette auch für das Aufseher-Fenster
        cmd = waechter_lauf.resume_befehl(claude, a.resume, a.model, effort, remote_control, a.spec, "<weiter>")
    if a.dry_run:
        print(" ".join(cmd[:-1]), '"<prompt>"')
        return 0
    # Speicher-Schutz (#257 Paket B, Fixrunde 1 F3): RAM knapp oder Obergrenze an
    # Claude-Sessions erreicht → warten statt Exit 5, damit Fenster und Grund bleiben.
    auf_speicher_warten(konfig, wer=f"Aufseher #{a.spec}")
    # Wartende Fenster (#560): sobald wieder Platz ist, in Sekunden statt erst beim Aufpasser-Takt.
    _nachstarter_starten(a.spec, REPO_ORDNER)
    # Vertrauens-Dialog für die Repo-Wurzel vorab bestätigen (#257) — Fehler nur Warnung.
    vertrauen.still_sicherstellen(REPO_ORDNER)
    # Aus einer Claude-Session gestartet erben Kind-Sessions die Markierung
    # CLAUDE_CODE_CHILD_SESSION und speichern kein Transkript (kein Resume nach
    # Absturz, Beleg 17.09.2026). Persistenz deshalb ausdrücklich erzwingen.
    sauber = sitzung_env.ohne_sitzung()  # alle Sitzungs-Variablen, nicht nur CHILD_SESSION
    os.environ.clear()
    os.environ.update(sauber)
    # Eine Session im Worktree darf nicht das Repo des Launchers erben (#205).
    os.environ.pop("TO_SPAWN_REPO", None)
    os.environ["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] = "1"
    os.environ.update(temp_ordner.umgebung())  # #136: Temp auf die Platte, nie die RAM-Platte
    # Klick-Sperre nur für lokal gespawnte Sessions (Windows, Maus-Müll im Eingabefeld).
    terminal_maus.maus_ruhig()
    # Umzug (#212): /to-spawn-of im Aufseher zieht alle Sessions um und beendet am Ende
    # diese Aufseher-Session über die Umzug-Datei (Temp-Ordner je Lauf).
    lauf_ordner = Path(tempfile.mkdtemp(prefix=f"wache-{a.spec}-", dir=os.environ.get("TMPDIR")))
    umzug_datei = lauf_ordner / "umzug.json"
    abloese_datei = lauf_ordner / "abloesung.json"
    os.environ["BAU_UMZUG_DATEI"] = str(umzug_datei)
    os.environ["TO_SPAWN_WACHE_SPEC"] = str(a.spec)
    os.environ[ABLOESE_ENV] = str(abloese_datei)
    umzug_datei.unlink(missing_ok=True)
    session_id = a.resume
    start_prompt = prompt
    terminal_log_abschalten()  # #541: ab hier gehört das Terminal Claude, Fäden loggen nur in die Datei
    # Handoff-Blick (#542): eigener Faden prüft alle ≤ 2,5 min fällige Handoffs und
    # startet die Leiter — nicht erst beim nächsten Aufseher-Tick (bis 30 min).
    # Briefkasten-Wecker (#414): neue Frage weckt diese Session, Antwort die Bau-Session (≤ 1 min).
    with handoff_blick.laeuft(
        a.spec, handoff_blick.EchteNaht(REPO_ORDNER, ordner, REPO)
    ), briefkasten_wecker.laeuft(a.spec, REPO_ORDNER, gh_repo=REPO):
        for runde in range(1, MAX_ABLOESUNGEN + 2):
            # Aufsicht (#213): Limit im Transkript → Ausweich-Modell; Umzug-/Ablöse-Datei → Ende.
            code = waechter_lauf.fahre(
                claude=claude,
                spec=a.spec,
                prompt=start_prompt,
                modell=a.model,
                ausweich=ausweich,
                remote_control=remote_control,
                repo=REPO_ORDNER,
                cwd=ordner,
                takt=waechter_lauf.zahl_aus_umgebung(
                    "TO_SPAWN_AUFSICHT_TAKT", waechter_lauf.TAKT_S
                )
                or waechter_lauf.TAKT_S,
                puffer=waechter_lauf.zahl_aus_umgebung(
                    "TO_SPAWN_RESET_PUFFER_S", waechter_lauf.RESET_PUFFER_S
                ),
                hoechstens=waechter_lauf.zahl_aus_umgebung(
                    "TO_SPAWN_RESET_MAX_S", waechter_lauf.MAX_WARTE_S
                )
                or waechter_lauf.MAX_WARTE_S,
                abbruch=lambda: umzug_datei.exists() or abloese_datei.exists(),
                session_id=session_id,
                effort=effort,
            )
            umzug_daten = umzug.lies_umzug(umzug_datei)
            if umzug_daten is not None:
                log.info(
                    "Umzug nach %s bestätigt — lokaler Aufseher beendet (Exit %s).",
                    umzug_daten.get("ziel") or "?",
                    code,
                )
                return 0
            if not abloese_datei.exists():
                return code
            try:
                daten = json.loads(abloese_datei.read_text(encoding="utf-8"))
                handoff = Path(daten["handoff"])
                start = str(daten.get("start") or "")
            except (OSError, ValueError, KeyError, TypeError) as fehler:
                log.error(
                    "Ablöse-Datei %s unlesbar (%s) — Aufseher endet.",
                    abloese_datei,
                    fehler,
                )
                return 2
            abloese_datei.unlink(missing_ok=True)
            if runde > MAX_ABLOESUNGEN:
                log.error(
                    "Aufseher #%s: %s Ablösungen erreicht — keine weitere Nachfolge.",
                    a.spec,
                    MAX_ABLOESUNGEN,
                )
                return 2
            log.info(
                "Aufseher #%s: Ablösung %s — Nachfolge-Aufseher startet mit %s.",
                a.spec,
                runde,
                handoff,
            )
            session_id = None
            start_prompt = abloese_prompt(prompt, handoff, runde + 1, a.spec, start)
    return code


if __name__ == "__main__":
    sys.exit(main())
