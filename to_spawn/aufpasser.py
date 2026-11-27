"""Aufpasser (Hausmeister) für Bau-Sessions im tmux — Ebene 3 über Aufseher und Bau-Sessions.

Läuft per Cron alle 15 Minuten (0 Token, reines Skript) und sieht je tmux-Sitzung
``spec-<S>`` nach den Fenstern ``bau <N>`` und ``wache <S>``:

0. **Deploy-Wache (#262):** läuft ein Deploy (``--deploy-muster`` plus die Skripte aus
   ``.to-spawn/config.json`` gegen ``prozessbaum.passende``), wird in diesem Lauf
   nichts angefasst — weder der Hausmeister noch der externe Wecker noch tmux-Fenster
   (nur die Hashes werden fortgeschrieben; bei gestörter Abfrage fail-closed). Prozesse
   älter als 6 h zählen nicht (verwaiste Läufe), sie werden als „Waise ignoriert“
   geloggt. Ein Gate (``pytest``) sperrt nur das eigene Fenster — im Fensterbaum oder
   im Ticket-Worktree —, nie den ganzen Server (#592).
1. **Fehlendes Fenster:** offenes Unter-Ticket ohne ``bau <N>`` (oder fehlendes
   ``wache <S>`` bei offenen Tickets) → Fenster anlegen. Liegt
   ``<repo>/.to-spawn/sessions/<N>.json`` (``wache-<S>.json``) und das Transkript
   dazu, geht es mit ``--resume <id>`` weiter statt frisch (R2). Ein Start gilt
   erst, wenn Fenster und Prozess 5 s lang bleiben (R6). Nie bei Label
   ``ready-for-human``/``needs-info``/``wontfix``; verschwindet das Fenster nach
   dem Start gleich wieder, gibt es höchstens 2 Starts in 6 h, dann einmal
   „braucht den Menschen“.
2. **Ticket zu:** ``bau <N>`` mit geschlossenem Ticket, Pane-Text seit ≥ 15 min
   unverändert und keine laufende Arbeit → Worktree sichern (falls schmutzig),
   Session beenden. Ist die Grenze voll (Speicher/Session-Zahl) und meldet die
   Session ``idle``, reicht statt 15 min eine kurze Gegenprobe
   (``karenz_platznot_s``). Lebt im Fensterbaum Claude, bekommt es ``/exit`` und bis
   ``exit_warten_s`` Zeit, Reste werden beendet — Fenster und bau.py-Kette leben
   weiter, das nächste Ticket startet (#592). Ohne Claude wird das Fenster
   geschlossen.
3. **Stilles Fenster** (Pane-Text ≥ ``hang_min`` Minuten unverändert und Session
   nicht ``busy``), Sicherheitskette mit Zähler ``stufe`` je Fenster:
   - Stufe 0 → anstupsen (Text ins Fenster tippen).
   - Stufe 1 → Gesprächs-ID aus ``~/.claude/sessions/<pid>.json`` lesen, Worktree
     auf ``sicherung/<N>`` sichern und pushen, Fenster per tmux-Respawn mit
     ``--resume <id>`` neu starten (Fenster, Index und Sitzung bleiben — die Sitzung
     stirbt nie, auch als letztes Fenster). Danach bis 120 s nachweisen, dass die
     Session mit derselben ID wieder läuft. Scheitert Sicherung oder Nachweis,
     bleibt alles stehen und der Mensch wird gerufen.
   - Stufe 2 → nichts mehr ändern, einmal „braucht den Menschen“ melden (Stufe 3).
   Die Stufe steigt nur bei einem Eingriff. Ändert sich der Pane-Text danach, hat
   der Eingriff gewirkt: Stufe 0, Uhr neu. Kill/Resume gibt es also nur, wenn nach
   dem Anstupsen ``hang_min`` lang GAR NICHTS passiert ist; Stufe 3 bleibt, bis der
   Text sich ändert — kein Zeit-Reset. Direkt vor jedem Eingriff wird der Pane-Text
   neu gelesen: anders als zu Laufbeginn, arbeitend oder Deploy/Gate inzwischen
   gestartet ⇒ Kette abbrechen (R4). Mehr als 3 Anstupser je Fenster in 24 h ⇒
   Stufe 3, einmal „braucht den Menschen“ (R5). Das Aufseher-Fenster geht denselben Weg,
   nur ohne Sicherung (kein Worktree): ``wache.py --resume <id>`` (R1).
   Lebt im Pane nur noch die Shell (Session beendet), wird nie angestupst — der
   Text liefe als Befehl —, sondern nach ``hang_min`` Stille gleich bei Stufe 1
   begonnen, mit der ID aus ``.to-spawn/sessions/`` (R2).

**Gesprächs-ID und Arbeitszustand** kommen aus ``~/.claude/sessions/<pid>.json``
(Claude Code schreibt sie je laufender Session: ``sessionId``, ``cwd``, ``status``
``busy``/``idle``, ``tmux`` = ``sitzung:@fenster.%pane``). Gültig nur, wenn der
Prozess lebt und das ``tmux``-Feld zu diesem Fenster passt. Ein Transkript-Fallback
über die neueste ``.jsonl`` gibt es nicht mehr: alle Bau-Fenster eines Repos teilen
sich das cwd, die neueste Datei gehört nachweislich oft einer fremden Session.

**Wartende Loop-Sessions** (ScheduleWakeup) zeigen nur ``❯`` + „done HH:MM“ — kein
Marker. Sicher sind sie durch ``hang_min``: jeder Wakeup ≤ 60 min ändert den
Pane-Text vorher, deshalb ist ``hang_min`` nie kleiner als 61 (``HANG_MIN_UNTERGRENZE``).

Es wird nie gestasht, nie der Branch gewechselt, nie der Index angefasst — die
Sicherung ist ein Commit aus einem temporären Index, ohne ``.env``/Zugangsdateien,
mit der vorigen Sicherung als zweitem Elternteil (Kette bleibt, Push ohne Zwang).
Das Hauptrepo wird nie gesichert (geteilter Baum mehrerer Sessions, fremde Arbeit).
Genau eine Stelle schließt ein Fenster (``_fenster_schliessen``), genau eine setzt
es fort (``_fenster_fortsetzen``); beide brauchen eine ``Freigabe``, die nur nach
„Ticket zu“ oder „gesichert + ID bekannt“ entsteht. Ein Lauf hält ``<zustand>/lock``
(flock); läuft schon einer, endet der zweite sofort.

Nur Linux mit tmux und ``/proc`` (Bau-Server). Windows wird nicht unterstützt.
Jeder Eingriff: Logzeile + eigener Kommentar im Spec-Issue; nur Meldungen ohne
Eingriff kommen je Fenster/Ereignis einmal am Tag (R3). Trockenlauf: ``--trocken``
(legt keinen Zustandsordner an, Log auf stderr).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from to_spawn import (
    anleitung,
    auswahl,
    bau_log,
    briefkasten,
    capo,
    config,
    eigener_handoff,
    gh,
    manifest,
    prozessbaum,
    sessions_datei,
    sitzung_env,
    spawn_exit,
    speicher,
    starter,
    ticket_zustand,
    tmux_aufruf,
    uebergabe_sichern,
    vorfall,
    werkzeug,
)
from to_spawn import sperre as _sperre
from to_spawn.waechter_lauf import _LIMIT_TEXT as LIMIT_TEXT
from to_spawn.waechter_lauf import ist_echte_antwort, limit_offen, transkript_ordner

log = logging.getLogger("aufpasser")

HANG_MIN = 90
#: Wakeups wartender Loop-Sessions kommen spätestens alle 60 min — darunter wäre
#: eine wartende Session „still“.
HANG_MIN_UNTERGRENZE = 61
#: Offene Ja/Nein-Rückfrage: nach so vielen Minuten einmal melden.
RUECKFRAGE_MIN = 15
#: Ja/Nein-Frage im Text am Prompt (nicht Dialog): nach so vielen Minuten anstupsen (#485).
FRAGE_PROMPT_MIN = 5
#: „Ticket zu“: Fenster erst schließen, wenn der Text so lange unverändert ist.
KARENZ_MIN = 15
#: Nach dem Fortsetzen so lange auf die Session mit derselben ID warten.
# Echter Weg bash -lc → bau.py (Manifest, gh, MCP-Prüfung) → claude → Session-JSON
# dauert deutlich länger als die Attrappe; Fehlalarm wäre Stufe 3 ohne Not.
NACHWEIS_S = 120
#: Höchstens so viele Starts je Ticket im Zeitfenster.
START_MAX = 2
START_FENSTER_S = 6 * 3600
#: Nach einem Fenster-Start so lange prüfen, dass Fenster und Prozess bleiben (R6).
START_NACHWEIS_S = 5.0
#: Mehr als so viele Anstupser je Fenster im Zeitfenster ⇒ „braucht den Menschen“ (R5).
STUPSER_MAX = 3
STUPSER_FENSTER_S = 24 * 3600
#: Limit-Probe (#1453, #892) höchstens einmal je so viele Sekunden je (Programm, Modell);
#: der Zeitstempel liegt in ``stand.json`` und überlebt so den 15-min-Cron-Neustart.
LIMIT_PROBE_ABSTAND_S = 30 * 60


def _als_zahl(wert: Any) -> float | None:
    """Konvertiert ``wert`` in eine endliche Gleitkommazahl (``bool`` zählt nicht)."""
    if isinstance(wert, bool):
        return None
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return None
    return zahl if zahl == zahl and abs(zahl) != float("inf") else None


def _probe_gueltig(eintrag: dict[str, Any], jetzt: float) -> bool:
    """Probe-Eintrag zählt nur mit Alter in [0, Abstand) — Zukunftszeit = unbekannt."""
    zeit = _als_zahl(eintrag.get("zeit", 0))
    if zeit is None:
        return False
    return 0 <= jetzt - zeit < LIMIT_PROBE_ABSTAND_S


#: Meldungen ohne Eingriff kommen je Fenster/Ereignis nur einmal am Tag ins Issue;
#: jeder Eingriff (gestartet, angestupst, fortgesetzt, geschlossen) immer (R3).
MELDUNG_EINMAL_PRO_TAG = frozenset(
    {
        "rueckfrage",
        "braucht_mensch",
        "sicherung_fehlgeschlagen",
        "fortsetzen_fehlgeschlagen",
        "startet_nicht",
        "start_fehlgeschlagen",
        "stupser_erschoepft",
        "session_beendet",
        "folge_handoff_fehler",
    }
)
#: Übergangs-Aliase für gespeicherte Stände und bestehende Aufrufer (#325).
EREIGNIS_ALIAS: dict[str, str] = {"braucht_david": "braucht_mensch"}  # Übergangs-Alias (#325)
_METHODE_ALIAS: dict[str, str] = {"_braucht_david": "_braucht_mensch"}  # Übergangs-Alias (#325)
#: Ereignis → Vorfall (Klasse, Symptom, Ursache, Lösung) für die Lernschleife (#286).
#: Jeder erkannte Stillstand steht hier, auch der erste Anstupser. Nur die Züge, bei
#: denen nichts stockte — ``gestartet``, ``fortgesetzt``, ``geschlossen`` — fehlen:
#: sie sind der Normalbetrieb und lernen nichts.
EREIGNIS_VORFALL: dict[str, tuple[str, str, str, str]] = {
    "startet_nicht": (
        "skill",
        "Fenster steht leer, die Bau-Session läuft nicht an",
        "Startbefehl im Fenster lief nicht an (Vorlage, Pfad oder Repo stimmt nicht)",
        "Aufpasser startet neu; Startbefehl und Repo-Pfad des Fensters prüfen",
    ),
    "start_fehlgeschlagen": (
        "skill",
        "Neustart des Fensters schlägt fehl",
        "tmux-Befehl oder Startvorlage scheitert",
        "Fehlertext im Aufpasser-Log lesen, Vorlage (--bau-vorlage) richtigstellen",
    ),
    "fortsetzen_fehlgeschlagen": (
        "skill",
        "Angehaltene Session lässt sich nicht fortsetzen",
        "Resume-ID fehlt oder das Transkript ist weg",
        "Session neu starten statt fortsetzen (bau <N> --sofort)",
    ),
    "sicherung_fehlgeschlagen": (
        "skill",
        "Arbeit eines Fensters konnte nicht gesichert werden",
        "Commit/Push vor dem Schließen scheiterte",
        "Worktree von Hand sichern, erst dann das Fenster schließen",
    ),
    "stupser_erschoepft": (
        "skill",
        "Session reagiert auf keinen Anstupser mehr",
        "Session hängt oder wartet auf etwas, das nie kommt",
        "Fenster ansehen, Session beenden und die Runde neu starten",
    ),
    "braucht_mensch": (
        "mensch",
        "Bau-Kette wartet auf die Entscheidung des Menschen",
        "Die Frage wurde nicht vor dem Spawn im Grill entschieden",
        "Entscheidung in den Grill vorziehen; sonst greift die 60-min-Annahme (#285)",
    ),
    "session_beendet": (
        "skill",
        "Session beendet, aber das Ticket ist offen — niemand baut weiter",
        "Keine Gesprächs-ID gemerkt, der Aufpasser kann nicht fortsetzen",
        "Neue Runde starten (bau <N> --sofort); Sitzungs-Datei der Session prüfen",
    ),
    "folge_handoff_fehler": (
        "skill",
        "Folge-Session ab Handoff startet nicht — die Arbeit bleibt liegen",
        "Neustart ab Handoff-Datei scheiterte (Exit ≠ 0: Pfad, Vorlage oder Repo stimmt nicht)",
        "Fehlertext im Aufpasser-Log lesen, Folge-Session von Hand ab Handoff starten",
    ),
    "angestupst": (
        "skill",
        "Session steht still und muss angestupst werden",
        "Session wartet auf nichts, meldet aber nichts — Stufe 1 des Aufpassers",
        "Aufpasser stupst an; wiederholt es sich am selben Ticket, Auftrag schärfen",
    ),
    "blocker_falsch_gewartet": (
        "skill",
        "Bau-Session wartet auf Blocker, die schon erledigt sind",
        "Blocker wurde während ihres Weckrufs (ScheduleWakeup) fertig; sie prüft erst beim nächsten Wecken",
        "Aufpasser stößt die Session an (to_spawn.py blocker <N> neu prüfen); häuft es sich, Weckruf früher auslösen",
    ),
    "rueckfrage": (
        "mensch",
        "Session stellt eine Rückfrage und wartet",
        "Der Auftrag ließ eine Entscheidung offen",
        "Auftrag vorab schärfen (Bleibt-gleich-Liste, Umfang beziffern)",
    ),
}

#: Ticket-Nummer aus einem Fenster-Schlüssel wie ``spec-282/bau 286``.
_FENSTER_TICKET = re.compile(r"bau[ _-]?(\d+)")

#: Nur noch das im Pane ⇒ die Session ist beendet, ein Anstupser liefe als Befehl.
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "fish", "ksh"})
#: Deploy-Prozesse älter als das sind Waisen.
DEPLOY_ALTER_MAX_S = 6 * 3600
#: Deploy-Skripte ohne Repo-Konfig: ``deploy.skripte`` der Vorgabe (L1 #7/#8).
VORGABE_SKRIPTE: list[dict[str, Any]] = config.DEFAULTS["deploy"]["skripte"]
#: Regex, das auf keine Befehlszeile passt (in Python und pgrep-ERE): Muster ohne Skripte.
NIE_MUSTER = "$^"


def deploy_muster_aus(skripte: Iterable[dict[str, Any]]) -> str:
    """``pgrep``-Regex aus den Skriptnamen, ODER-verknüpft; leere Liste ⇒ :data:`NIE_MUSTER`."""
    return "|".join(dict.fromkeys(re.escape(s["name"]) for s in skripte)) or NIE_MUSTER


def deploy_skripte(konfig: dict[str, Any]) -> list[dict[str, Any]]:
    """Gültige Einträge aus ``deploy.skripte`` der Konfig (Trust-Grenze: Repo-Datei).

    Kein Objekt/keine Liste ⇒ Vorgabe. Ein Eintrag zählt nur mit nicht-leerem
    ``name`` und ``art`` "live"/"pruef"; fehlt ``pruef_schalter`` bzw. ``fern``, gilt
    "" bzw. False. Ungültige Einträge fallen mit Warnung weg.
    """
    deploy = konfig.get("deploy")
    roh = deploy.get("skripte") if isinstance(deploy, dict) else None
    if not isinstance(roh, list):
        if roh is not None:
            log.warning("deploy.skripte ist keine Liste — Vorgabe gilt")
        return VORGABE_SKRIPTE
    gueltig = []
    for s in roh:
        name = s.get("name") if isinstance(s, dict) else None
        if not isinstance(name, str) or not name.strip() or s.get("art") not in ("live", "pruef"):
            log.warning("deploy.skripte: Eintrag ungültig, übersprungen: %r", s)
            continue
        schalter = s.get("pruef_schalter")
        gueltig.append(
            {
                "name": name,
                "art": s["art"],
                "pruef_schalter": schalter if isinstance(schalter, str) else "",
                "fern": s.get("fern") is True,
            }
        )
    return gueltig


#: Alter Name, Tests lesen ihn: Muster der Vorgabe-Skripte.
DEPLOY_MUSTER = deploy_muster_aus(VORGABE_SKRIPTE)
#: Gate/Testlauf: sperrt nur das eigene Fenster (Fensterbaum oder Ticket-Worktree),
#: nie den ganzen Server — fremde Testläufe halten keinen Platz fest (#592).
#: Welche Deploy-Aufrufe ebenso nur prüfen, entscheidet ``deploy_art`` (#598).
GATE_MUSTER = r"pytest"
#: Grenze voll + Session idle + Ticket zu: so lange vor dem Schließen warten und
#: dann den Fenstertext vergleichen, statt die volle Karenz abzusitzen (#592).
KARENZ_PLATZNOT_S = 30
#: Abgeschlossene Spec (alle Tickets zu oder nur Abnahme offen): ab dieser Stille
#: darf ``platz_schaffen`` stille ``leitstand``-/``wache``-Fenster schließen (#624/#460).
FERTIGE_SPEC_STILL_MIN = KARENZ_MIN
#: So lange wartet :func:`platz_schaffen` auf eine laufende Aufpasser-Sperre,
#: statt sofort „Sperre belegt“ zu melden und 60 s leer zu schlafen (#624).
PLATZ_SPERRE_WARTEN_S = 45.0
#: Ticket zu: so lange darf Claude nach ``/exit`` brauchen, dann wird es beendet (#592).
EXIT_WARTEN_S = 30
#: Nach ``/exit``: so lange darf die Fenster-Shell brauchen, um den nächsten
#: Kettenbefehl zu starten, bevor das Fenster als „nur noch Shell“ gilt (#598).
KETTE_RUHE_S = 1.0
STOPP_LABELS = frozenset({"ready-for-human", "needs-info", "wontfix", "bau:lokal", "bau:pause"})
CRON_MARKE = "# to-spawn aufpasser"
CRON_TAKT = "*/15 * * * *"
#: Datei im Zustandsordner, in die der Cron-Lauf schreibt (Aufpasser-Log für Menschen).
CRON_LOG = "cron.out"
SKRIPT_STARTER = Path(__file__).resolve().parent.parent / "skripte" / "aufpasser.py"
ARBEITS_MARKER = ("esc to interrupt",)
#: Limit-Meldung im Fenster: zählt in :func:`arbeitet` mit, ist aber im ``bau``-Fenster
#: ein eigener Zustand „Limit-Pause“ (:func:`limit_pause`, #1453, #892).
LIMIT_MARKER = (
    "usage limit",
    "limit reset",
)
#: Zeilenanfang neuer Claude-Ausgabe bzw. neuer Eingabe unter einer Limit-Zeile.
NEUE_AUSGABE = ("⏺", "●", "✻", "❯", ">")
#: Nur echte laufende Arbeit (ohne Limit-Hinweise) — genutzt vom externen Wecker (#287),
#: damit eine alte Limit-Meldung nach dem Reset einen neuen Weckruf nicht dauerhaft sperrt.
LAUFEND_MARKER = ("esc to interrupt",)
#: Marker, die nur in der Statuszeile unter dem Eingabefeld Arbeit bedeuten (#434):
#: im Verlauf oben bleibt „✻ Waiting for 1 background agent to finish“ oft stehen,
#: obwohl das Eingabefeld längst still ist.
STATUSZEILEN_ARBEITS_MARKER = ("background agent",)
#: Aktiver Subagent-Hinweis in der Statuszeile von ``agy`` (z. B. ``1 subagent(s)``, #288).
_AGY_SUBAGENT_AKTIV = re.compile(r"\b[1-9]\d*\s+subagent\(s\)")
# Warte-Zeile von bau.py (Blocker offen). Nur diese Form zählt — „nächste Prüfung“
# allein stand am 21.09. als Claude-Prosa im Scrollback eines idle Fensters.
BLOCKER_WARTEN = re.compile(r"Ticket #\d+ wartet \(.*\) — nächste Prüfung in \d+ (?:min|s)\b")
RUECKFRAGE_MARKER = (
    "Do you want to proceed?",
    "Do you want to",
    "Yes, and don't ask again",
    "(y/n)",
    "Trust",
    "Esc to cancel",
)
#: Letzte Zeile des Befehls ``to_spawn.py blocker <N>`` im Pane (#267, #269): ``WARTET: <Gründe>``
#: oder ``FREI``, allein auf der Zeile — davor nur Rahmen der Claude-Ausgabe (``⎿``), sonst Prosa.
BLOCKER_STATUS = re.compile(r"^[ \t⎿│]*(WARTET: .+?|FREI)[ \t]*$", re.MULTILINE)
#: Anstoß, wenn die Session laut eigener Meldung auf Erledigtes wartet (#269, #786).
BLOCKER_FREI_TEXT = (
    "Aufpasser: Deine Blocker ({blocker}) sind erledigt — jetzt "
    "`python {cli} blocker {ticket}` erneut prüfen und weiterbauen, "
    "nicht auf den Weckruf warten."
)


def blocker_frei_text(blocker: str, ticket: int) -> str:
    """Anstoß-Text bei erledigten Blockern mit aktuellem Skill-Ort (#269, #786)."""
    cli = shlex.quote((config.skill_ordner() / "to_spawn.py").as_posix())
    return BLOCKER_FREI_TEXT.format(blocker=blocker, ticket=ticket, cli=cli)


#: Anstupser: derselbe Mindset-Stoß wie die Eingriffs-Leiter (#500, eine Quelle).
ANSTUPS_TEXT = anleitung.anstupser("Aufpasser")
VORLAGE_WACHE_REPO = "cd {repo} && {py} scripts/wache.py {s}{resume}"
VORLAGE_WACHE_SKILL = "cd {repo} && TO_SPAWN_REPO={repo} {py} {wache} {s}{resume}"
#: Nie in eine Sicherung: Geheimnisse und Zugangsdateien — in JEDEM Unterordner.
#: Glob-Muster für ``:(exclude,glob)``: ohne ``glob``-Magie ist ``.env.*`` an den
#: Pfadanfang gebunden und ``web/.env.local`` wäre mit hochgeladen worden (#1368).
SICHERUNG_AUSSCHLUSS = (
    "**/.env",
    "**/.env.*",
    "**/*.env",
    "**/*zugang*",
    "**/*zugang*/**",
    "**/*.pem",
    "**/*.key",
    "**/.credentials.json",
)
#: #1536: Datenbanken (Test-Kopien, oft Abzüge echter Daten, beliebig groß) gehen nie in
#: eine Sicherung — auch nicht, wenn sie nicht ignoriert sind. Einzige Datenbank-Liste:
#: welche davon Wegwerf-Ware ist, entscheidet ``hausmeister.ist_test_db``; jede andere hält
#: den Ordner (wird nie ungesichert gelöscht).
NIE_HOCHLADEN = ("**/*.db", "**/*.db-wal", "**/*.db-shm")
GIT_IDENTITAET = {
    "GIT_AUTHOR_NAME": "Aufpasser",
    "GIT_AUTHOR_EMAIL": "aufpasser@to-spawn.invalid",
    "GIT_COMMITTER_NAME": "Aufpasser",
    "GIT_COMMITTER_EMAIL": "aufpasser@to-spawn.invalid",
}


def zustand_standard() -> Path:
    return config.heim_ordner() / ".local" / "state" / "to-spawn" / "aufpasser"


def wache_log(repo: Path, spec: int | str) -> Path:
    """Log eines tmux-Aufseher-Fensters: stderr von ``wache.py`` und Exit-Code bei Fehler (#271)."""
    return Path(repo) / ".to-spawn" / f"fenster_wache_{spec}.log"


def _fenster_log_pfad(repo: Path, spec: str, ticket: int | None) -> Path:
    """Start-Log eines Fensters: :func:`capo.fenster_log` (Bau) oder :func:`wache_log` (Aufseher)."""
    return capo.fenster_log(repo, ticket) if ticket is not None else wache_log(repo, spec)


def _start_log_auszug(datei: Path, ab_byte: int = 0, *, max_zeilen: int = 3, max_zeichen: int = 240) -> str:
    """Lesbarer Auszug der neuen Zeilen eines Fenster-Logs ab ``ab_byte`` (#288).

    Wurde die Datei seit ``ab_byte`` gekürzt oder neu angelegt, wird ab 0 gelesen.
    Leer, wenn die Datei fehlt oder keine neuen nicht-leeren Zeilen enthält.
    """
    try:
        daten = datei.read_bytes()
    except OSError:
        return ""
    ausschnitt = daten[ab_byte:] if ab_byte <= len(daten) else daten
    zeilen = [z.strip() for z in ausschnitt.decode("utf-8", errors="replace").splitlines() if z.strip()]
    if not zeilen:
        return ""
    text = " | ".join(zeilen[-max_zeilen:])
    return text if len(text) <= max_zeichen else text[: max_zeichen - 1] + "…"


def _hauptklon_aus_worktree(pfad: Path) -> Path | None:
    """Löst bei einem Git-Worktree (``.git``-Datei mit ``gitdir: …/.git/worktrees/<name>``) den Hauptklon auf."""
    git_pfad = pfad / ".git"
    if not git_pfad.is_file():
        return None
    try:
        erste = git_pfad.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    if not erste.lower().startswith("gitdir:"):
        return None
    ziel_roh = erste.split(":", 1)[1].strip()
    ziel = Path(ziel_roh)
    if not ziel.is_absolute():
        ziel = (pfad / ziel).resolve()
    else:
        ziel = ziel.resolve()
    if ziel.parent.name == "worktrees" and ziel.parent.parent.name == ".git":
        klon = ziel.parent.parent.parent
        if klon.is_dir():
            return klon
    return None


def _origin_kennung(pfad: Path) -> str:
    """Normalisierte ``origin``-URL eines Git-Repos (ohne ``.git``), leer bei Fehler."""
    try:
        url = _git(pfad, "remote", "get-url", "origin").strip()
    except (RuntimeError, OSError):
        return ""
    return url.removesuffix(".git")


# --- Bausteine ohne Seiteneffekte -------------------------------------------


@dataclass
class Einstellungen:
    """Alles, was der Aufpasser von außen bekommt (Tests spritzen es ein)."""

    zustand: Path
    tmux_socket: str | None = None
    hang_min: float = HANG_MIN
    bau_vorlage: str | None = None
    #: Startbefehl des Aufseher-Fensters mit {repo} {py} {s} {resume} (R1).
    wache_vorlage: str | None = None
    #: Fester ``pgrep``-Regex; None ⇒ aus ``deploy.skripte`` der Repo-Konfigs.
    deploy_muster: str | None = None
    repo: Path | None = None
    trocken: bool = False
    jetzt: Callable[[], float] = time.time
    heim: Path | None = None
    #: Künstliche Pause zwischen Lesen und Eingriff (nur Tests, Fix F6).
    verzoegerung_s: float = 0.0
    #: Wartezeit vor dem Schließen bei voller Session-Grenze (Tests setzen sie klein).
    karenz_platznot_s: float = KARENZ_PLATZNOT_S
    #: Stille-Schwelle (Minuten) für ``leitstand``-/``wache``-Fenster fertiger Specs (#624/#460).
    fertige_spec_still_min: float = FERTIGE_SPEC_STILL_MIN
    #: Wartezeit auf ``<zustand>/lock`` in :meth:`Aufpasser.platz_schaffen` (0 = nicht warten, #624).
    sperre_warten_s: float = 0.0
    #: Wartezeit auf das Ende von Claude nach ``/exit`` (Tests setzen sie klein).
    exit_warten_s: float = EXIT_WARTEN_S
    #: Optionaler Zustandsordner für den externen Wecker (#287); None ⇒ Standardordner.
    wecker_zustand: Path | None = None

    def __post_init__(self) -> None:
        if self.hang_min < HANG_MIN_UNTERGRENZE:
            raise ValueError(
                f"hang_min {self.hang_min} liegt unter der Untergrenze "
                f"{HANG_MIN_UNTERGRENZE} (Wakeups wartender Sessions kommen alle ≤ 60 min)"
            )


@dataclass(frozen=True)
class Freigabe:
    """Erlaubnis, ein Fenster zu schließen/fortzusetzen — entsteht nur in den zwei
    Fabriken unten."""

    grund: str
    sicherung: str | None
    session_id: str | None = None


def freigabe_ticket_zu(ticket: int, sicherung: str | None, *, grund: str | None = None) -> Freigabe:
    """Ticket ist CLOSED (oder abgeschlossene Spec); ein schmutziger Worktree wurde vorher gesichert."""
    return Freigabe(grund=grund or f"Ticket #{ticket} zu", sicherung=sicherung)


def freigabe_gesichert(session_id: str, sicherung: str | None) -> Freigabe:
    """Sicherung erledigt (oder nichts zu sichern) und die Gesprächs-ID ist bekannt."""
    if not session_id:
        raise ValueError("Freigabe ohne Gesprächs-ID")
    return Freigabe(grund="gesichert", sicherung=sicherung, session_id=session_id)


def freigabe_spec_fertig(spec: str, grund: str) -> Freigabe:
    """Spec abgeschlossen (alle Tickets zu oder nur Abnahme offen); kein Worktree zu sichern (#624)."""
    return freigabe_ticket_zu(int(spec), None, grund=f"Spec #{spec} {grund}")


@dataclass
class Fenster:
    sitzung: str
    spec: str
    index: str
    name: str
    pfad: str
    pane_pid: int
    window_id: str = ""
    pane_id: str = ""
    start_pfad: str = ""

    @property
    def ziel(self) -> str:
        return f"={self.sitzung}:{self.index}"

    @property
    def fest(self) -> str:
        """Stabiles tmux-Ziel für Eingriffe nach Wartezeiten: die ``window_id`` gehört
        nur diesem Fenster. Den Index vergibt tmux nach dem Schließen neu — ein
        späteres Schließen träfe sonst ein fremdes Fenster (#598). Ohne
        ``window_id`` (alte Daten) der Index."""
        return self.window_id or self.ziel

    @property
    def ticket(self) -> int | None:
        treffer = re.fullmatch(r"bau (\d+)", self.name)
        return int(treffer.group(1)) if treffer else None

    @property
    def ist_wache(self) -> bool:
        return self.name == f"wache {self.spec}"

    @property
    def ist_leitstand(self) -> bool:
        return self.name == f"leitstand {self.spec}"

    @property
    def sessions_name(self) -> str:
        """Name der Datei ``.to-spawn/sessions/<name>.json`` (R2)."""
        return f"wache-{self.spec}" if self.ist_wache else str(self.ticket or "")


@dataclass(frozen=True)
class SessionInfo:
    """Was Claude Code über eine laufende Session verrät (``~/.claude/sessions/``)."""

    session_id: str
    cwd: Path | None
    status: str | None
    pid: int
    #: True: aus der Session-JSON (dann sind ``cwd``/``status`` verlässlich).
    aus_json: bool = True
    #: Änderungszeitpunkt der Session-JSON (Unix-Sekunden), ``None`` = unbekannt (#624).
    mtime: float | None = None


def arbeits_marker_aktiv(text: str) -> bool:
    """Zeigt der Pane-Text einen laufenden Arbeits- oder Limit-Marker (#434, #288)?

    :data:`ARBEITS_MARKER`, :data:`LIMIT_TEXT` und aktive ``agy``-Subagenten gelten im
    ganzen Bild; :data:`STATUSZEILEN_ARBEITS_MARKER` (``background agent``) nur in der
    :func:`statuszeile` unter dem Eingabefeld, damit stehengebliebene Verlaufszeilen
    wie ``✻ Waiting for 1 background agent to finish`` ein stilles Fenster nicht sperren.
    """
    return (
        any(m in text for m in ARBEITS_MARKER + LIMIT_MARKER)
        or any(m in statuszeile(text) for m in STATUSZEILEN_ARBEITS_MARKER)
        or bool(LIMIT_TEXT.search(text))
        or bool(_AGY_SUBAGENT_AKTIV.search(text))
    )


def arbeitet(text: str, status: str | None = None) -> bool:
    """Fenster arbeitet oder wartet auf Antwort → nie anfassen.

    Marker im Pane-Text (auch die Limit-Meldung des Aufsehers) oder ``status == busy``
    aus der Session-JSON.
    """
    if status == "busy":
        return True
    return arbeits_marker_aktiv(text) or rueckfrage(text) or bool(BLOCKER_WARTEN.search(text))


def _ist_limit_zeile(zeile: str) -> bool:
    return any(m in zeile for m in LIMIT_MARKER) or bool(LIMIT_TEXT.search(zeile))


def limit_pause(text: str) -> bool:
    """Fenster steht nur wegen des Nutzungs-Limits still (#1453, #892).

    Wahr, wenn eine Limit-Zeile die letzte Ausgabe über dem Eingabefeld ist (darunter
    keine neue Ausgabe/Eingabe) und ohne die Limit-Zeilen nichts nach Arbeit aussieht.
    Ob das Limit wieder offen ist, sagt diese Funktion nicht — das prüft die Probe.
    """
    zeilen = ohne_statuszeile(text).splitlines()
    linien = [i for i, z in enumerate(zeilen) if EINGABEFELD_LINIE.fullmatch(z)]
    if linien:
        zeilen = zeilen[: linien[-1]]
    treffer = [i for i, z in enumerate(zeilen) if _ist_limit_zeile(z)]
    if not treffer:
        return False
    if any(z.lstrip().startswith(NEUE_AUSGABE) for z in zeilen[treffer[-1] + 1 :]):
        return False
    return not arbeitet(ohne_limit_zeilen(text))


def ohne_limit_zeilen(text: str) -> str:
    """Pane-Text ohne Limit-Zeilen — was bleibt, entscheidet über „arbeitet“."""
    return "\n".join(z for z in text.splitlines() if not _ist_limit_zeile(z))


#: So viele Bytes vom Transkript-Ende reichen für die letzte echte Antwort (#1463, #892).
TRANSKRIPT_ENDE_BYTES = 512_000


def fenster_modell(transkript: Path) -> str | None:
    """Modell der letzten echten Antwort im Transkript einer Session (#1463, #892).

    Künstliche Zeilen von Claude Code (Limit, API-Fehler, ``<synthetic>``) zählen nicht.
    Kein Transkript/keine Antwort → ``None`` (der Aufrufer nimmt das Konfig-Modell).
    """
    try:
        with transkript.open("rb") as datei:
            datei.seek(0, os.SEEK_END)
            datei.seek(max(0, datei.tell() - TRANSKRIPT_ENDE_BYTES))
            zeilen = datei.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for zeile in reversed(zeilen):
        try:
            eintrag = json.loads(zeile)
        except ValueError:
            continue
        if isinstance(eintrag, dict) and ist_echte_antwort(eintrag):
            modell = (eintrag.get("message") or {}).get("model")
            if isinstance(modell, str) and modell:
                return modell
    return None


def arbeitet_echt(text: str, status: str | None = None) -> bool:
    """Wie :func:`arbeitet`, aber ohne Limit-Marker (#287).

    Für den externen Wecker: nach einem Limit-Reset steht die alte Limit-Meldung oft noch
    im Verlauf über ``❯``; sie darf den erneuten Weckruf nicht dauerhaft blockieren.
    """
    if status == "busy":
        return True
    return (
        any(m in text for m in LAUFEND_MARKER)
        or any(m in statuszeile(text) for m in STATUSZEILEN_ARBEITS_MARKER)
        or bool(_AGY_SUBAGENT_AKTIV.search(text))
        or rueckfrage(text)
        or bool(BLOCKER_WARTEN.search(text))
    )


def gemeldete_blocker(text: str) -> str | None:
    """Gründe der letzten ``WARTET``-Zeile im Pane; ``None`` = keine Zeile oder zuletzt ``FREI`` (#269)."""
    treffer = BLOCKER_STATUS.findall(text)
    if not treffer or treffer[-1] == "FREI":
        return None
    return treffer[-1].removeprefix("WARTET: ").strip()


def erledigt_gewartet(text: str, ticket: int, repo: Path, gh_repo: str) -> str | None:
    """Die eine Regel „wartet auf Erledigtes“ (#269, #596): Blocker-Nummern (``#5, #6``) oder ``None``.

    Die Session wartet laut letzter ``WARTET``-Zeile im Pane, ``gh.blocker_gruende`` sieht aber
    alles erledigt. GitHub wird nur gefragt, wenn das Pane wirklich ``WARTET`` meldet (Rate-Limit);
    scheitert die Abfrage, ``None`` (fail-closed). Fragen: Aufpasser (Takt) und Briefkasten-Wecker (60 s).
    """
    gemeldet = gemeldete_blocker(text)
    if gemeldet is None or not gh_repo:
        return None
    deps = gh.blocked_by(gh_repo, str(ticket), cwd=repo)
    if deps is None or gh.blocker_gruende(repo, deps) != []:
        return None
    return ", ".join(f"#{n}" for n in re.findall(r"#(\d+)", gemeldet)) or gemeldet


def notiere_vorfall(repo: Path, ticket: str, ereignis: str, beispiel: str) -> None:
    """Vorfall-Zeile ``ereignis`` (:data:`EREIGNIS_VORFALL`) ins Bau-Log von ``ticket`` (#286).

    Ziel wie :meth:`Aufpasser.log_ordner`; dieselbe Zeile steht höchstens einmal drin
    (:func:`vorfall.schreibe`). Ein Fehler beim Schreiben kippt den Aufrufer nie.
    """
    ereignis = EREIGNIS_ALIAS.get(ereignis, ereignis)
    worte = EREIGNIS_VORFALL.get(ereignis)
    if worte is None:
        return
    klasse, symptom, ursache, loesung = worte
    try:
        vorfall.schreibe(
            Aufpasser.log_ordner(repo, ticket),
            ticket,
            klasse=klasse,
            symptom=symptom,
            ursache=ursache,
            loesung=loesung,
            beispiel=beispiel,
            regel=ereignis,
            quelle="aufpasser",
        )
    except (OSError, ValueError) as fehler:
        log.warning("Vorfall zu %s nicht notiert: %s", ereignis, fehler)


def notiere_blocker_falsch(repo: Path, ticket: int, blocker: str, datum: str) -> None:
    """Vorfall ``blocker_falsch_gewartet`` — die eine Stelle für Aufpasser und Wecker (#596)."""
    notiere_vorfall(repo, str(ticket), "blocker_falsch_gewartet", f"#{ticket} wartete auf {blocker} {datum}")


def rueckfrage(text: str) -> bool:
    sauber = _ohne_fluechtiges_rauschen(text)
    return any(m in sauber for m in RUECKFRAGE_MARKER)


# Ein Fehl-Treffer ist harmlos: er löst nur Stufe 0 aus (ein Anstupser, begrenzt durch STUPSER_MAX).
FRAGE_JA_NEIN = re.compile(
    r"\bja\s*/\s*nein\b|\byes\s*/\s*no\b|\bja oder nein\b|\byes or no\b"
    r"|[(\[]\s*(?:j/n|y/n)\s*[)\]]",
    re.IGNORECASE,
)
#: Frage mit nachgestelltem „Vorschlag: …“ (ein- oder zweizeilig, #286 Anomalie 8).
FRAGE_VORSCHLAG = re.compile(r"\?\s*Vorschlag:", re.IGNORECASE)


def frage_am_prompt(text: str) -> bool:
    """Steht in den letzten Zeilen über dem Eingabefeld eine Ja/Nein-Frage (#485, #286)?

    Eine Bau-Session fragt nie (sie nimmt den Vorschlag); bleibt sie an so einer
    Frage stehen, wartet sie sonst bis ``hang_min`` auf den ersten Anstupser.
    """
    zeilen = _zeilen_am_prompt(text)
    if any(briefkasten.JA_BEFEHL in z for z in zeilen[-3:]):
        return False  # Live-Ja-Aufforderung (#531): Session wartet zu Recht auf den Menschen
    return (
        any(FRAGE_JA_NEIN.search(z) for z in zeilen[-3:])
        or (bool(zeilen) and zeilen[-1].rstrip().endswith("?"))
        or bool(FRAGE_VORSCHLAG.search("\n".join(zeilen[-3:])))
    )


#: „Wartet auf Nachfolger“ (#381): Session hält das Ticket für übergeben, obwohl keiner kommt.
NACHFOLGER_WARTEN = re.compile(
    r"\bwarte\w*\b[^.\n]{0,60}?\bauf\b[^.\n]{0,40}?"
    r"(?:\b(?:neue|nächste)\w*\s+Session|Folge-?\s?session|Nachfolger)",
    re.IGNORECASE,
)


def wartet_auf_nachfolger(text: str) -> bool:
    """Sagt die Session am Prompt, sie warte auf eine neue Session/einen Nachfolger (#381)?

    Nur der Wortlaut; ob eine echte Ablösung aussteht, weiß allein
    :func:`eigener_handoff.aktuell` (siehe :meth:`Aufpasser.wartet_umsonst`).
    """
    return any(NACHFOLGER_WARTEN.search(z) for z in _zeilen_am_prompt(text)[-3:])


def wartet_umsonst(text: str, ticket: int | None, repo: Path) -> bool:
    """Die eine Regel „wartet umsonst auf Nachfolger“ (#381, #613): Wortlaut ohne eigenen Handoff.

    Liegt ein gültiger eigener Handoff (fällig oder nicht), ist die Ablösung echt und
    :meth:`Aufpasser.folge_ab_handoff` startet den Nachfolger — dann kein Anstoß.
    Fragen: Aufpasser (Takt, kurze Schwelle) und Briefkasten-Wecker (60 s).
    """
    if ticket is None or not wartet_auf_nachfolger(text):
        return False
    return eigener_handoff.aktuell(repo, ticket) is None


def steht_umsonst(text: str, ticket: int | None, repo: Path) -> bool:
    """Die eine Regel „steht umsonst am Prompt“ (#381, #485, #613): früher Anstoß statt ``hang_min``.

    Umsonst steht, wer auf einen Nachfolger wartet, der nicht kommt (:func:`wartet_umsonst`),
    oder wer mit einer Frage endet (:func:`frage_am_prompt`) — eine Bau-Session fragt nie.
    Hat das Ticket eine offene Frage im Briefkasten, wartet die Session zu Recht auf die
    Antwort (Briefkasten nicht lesbar ⇒ ebenso). Fragen: Aufpasser (Takt, kurze Schwelle) und
    Briefkasten-Wecker (60 s).
    """
    if wartet_umsonst(text, ticket, repo):
        return True
    if not frage_am_prompt(text):
        return gemergt_und_offen(text, ticket, repo)
    if ticket is None:
        return True
    try:
        return not briefkasten.offene([ticket], hauptbaum=repo)
    except (OSError, ValueError, RuntimeError) as fehler:
        log.warning("#%s: Briefkasten nicht lesbar (%s) — Frage am Prompt gilt als gewollt", ticket, fehler)
        return False


def gemergt_und_offen(text: str, ticket: int | None, repo: Path) -> bool:
    """Still am Prompt, Commit ``(#N)`` liegt auf ``origin/<Hauptzweig>``, Ticket noch offen (#459).

    Die Session hat ihre Arbeit abgegeben und schließt nicht ab — kein Mensch muss etwas tun.
    Nie anstoßen bei Live-Ja-Aufforderung, Abnahme-Ticket (``checkpoint:human``), offener
    Briefkasten-Frage; Ticket-Stand unlesbar ⇒ fail-closed. Billige Prüfungen (Bildschirm, Briefkasten-Datei) vor der
    git-Suche, GitHub zuletzt.
    """
    if ticket is None or arbeitet(text):
        return False
    if any(briefkasten.JA_BEFEHL in z for z in _zeilen_am_prompt(text)[-3:]):
        return False
    try:
        if briefkasten.offene([ticket], hauptbaum=repo):
            return False
    except (OSError, ValueError, RuntimeError) as fehler:
        log.warning("#%s: Briefkasten nicht lesbar (%s) — kein Anstoß", ticket, fehler)
        return False
    betreffe = gh.commit_betreffe(repo, ticket, f"origin/{gh.hauptzweig(repo)}")
    if not any(capo.betreff_hat_nummer(b, ticket) for b in betreffe or ()):
        return False
    daten = ticket_zustand.lesen(ticket, repo)  # GitHub zuletzt
    if daten is None or ticket_zustand.stufe(daten) != "offen":
        return False
    return manifest.checkpoint_label(config.lade(repo)) not in ticket_zustand.label_namen(daten)


def _zeilen_am_prompt(text: str) -> list[str]:
    """Nicht-leere Text-Zeilen über dem Eingabefeld (ohne Rahmen, ``❯``, Rauschen und Statuszeile)."""
    zeilen = [z for z in ohne_statuszeile(text).splitlines() if z.strip()]
    return [z for z in zeilen if not z.lstrip().startswith("❯") and not ist_pane_rauschen(z)]


#: Rahmenlinie des Claude-Code-Eingabefelds: Zeile nur aus ``─`` (mind. 20).
EINGABEFELD_LINIE = re.compile(r"\s*─{20,}\s*")
#: Abgesetzte Claude-Code-Fußzeile nach einer Antwort (z. B. ``✻ Crunched for 6s · done 7:25 PM``
#: oder ``✻ Baked for 40s``) — reines Abschluss-Rauschen, kein Antwort-Inhalt (#372).
_CLAUDE_FUSSZEILE = re.compile(
    r"^[✻✶✳✢✽]\s+(?:"
    r".+\s+for\s+(?:<\s*)?\d+\s*[hms](?:\s+\d+\s*[hms])*(?:\s*[·•][^\n?]*)?"
    r"|.+?\s[·•]\s*done\b[^\n?]*"
    r")$"
)
#: Eingeblendete Claude-Code-Feedback-Umfrage über dem Eingabefeld — Rauschen, keine Frage der
#: Bau-Session (#372, #624).
_CLAUDE_UMFRAGE = re.compile(
    r"^(?:"
    r"(?:[●✻✶✳✢✽│]\s+)?How is Claude doing this session\?(?:\s*\(optional\))?(?:\s*│)?"
    r"|(?:│\s+)?1:\s*Bad\b.*\b2:\s*Fine\b.*\b3:\s*Good\b.*\b0:\s*Dismiss\b.*"
    r")$",
    re.IGNORECASE,
)
#: Eingeblendeter Kontext-Hinweis direkt über dem Eingabefeld (``new task? /clear to save … tokens``) —
#: flüchtiges UI-Rauschen ohne Antwort-Inhalt (#624).
_CLAUDE_CLEAR_HINWEIS = re.compile(r"^new task\?\s+/clear\s+to\s+save\s+\S+\s+tokens$", re.IGNORECASE)
#: Eingeblendeter Remote-Control-Hinweis über dem Eingabefeld samt Sitzungs-URL — flüchtiges
#: Claude-Code-UI-Rauschen ohne Antwort-Inhalt (#624).
_CLAUDE_REMOTE_CONTROL = re.compile(r"^/remote-control\s+is\s+active(?:\s*[·•].*)?$", re.IGNORECASE)
_CLAUDE_REMOTE_URL = re.compile(r"^https://claude\.ai/code/session_[A-Za-z0-9_-]+$", re.IGNORECASE)
#: Eingeblendete Claude-Code-Tipps (``⎿ Tip: …`` / ``Tip: …``) — flüchtiges UI-Rauschen (#624).
_CLAUDE_TIP_ZEILE = re.compile(r"^(?:⎿\s+)?Tip:\s+.+$", re.IGNORECASE)
#: Flüchtige Claude-Code-Update-Hinweise — kein Antwort-Inhalt (#624).
_CLAUDE_UPDATE_HINWEIS = re.compile(
    r"^(?:Checking for updates(?:\.\.\.|…)|Update available!\s+Run:|Auto-update failed\b).*$",
    re.IGNORECASE,
)
#: Klartext-Rückfall für den gedimmten Claude-Code-Eingabe-Platzhalter (``❯ Try "…"``), falls
#: ``pane_hash`` ein Bild ohne ANSI-Farben (``capture-pane -p``) bekommt (#624).
_CLAUDE_EINGABE_PLATZHALTER = re.compile(r'^❯\s+Try\s+["“][^"”]+["”]$')
#: Führende blinkende Status- und Spinner-Zeichen aus Claude-Code- bzw. Terminal-Ausgaben, die
#: bei demselben unberührten Bild zwischen zwei Aufnahmen wechseln können (#372, #624).
#: Einzige Quelle für Aufpasser und Aufseher-Stand.
STATUS_PRAEFIX = re.compile(r"^(?:[●⏺○◯◐◓◑◒◔◕◌✻✶✳✢✽✦✧∗·⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏⣾⣽⣻⢿⡿⣟⣯⣷](?:\s+|$))+")
#: Unsichtbare flüchtige Zeichen (Zero-Width-Space/Joiner, BOM), die den Hash nicht ändern dürfen (#624).
_NULLBREITE = re.compile(r"[\u200b\u200c\u200d\ufeff]")


def _fluechtige_zeichen_bereinigen(zeile: str) -> str:
    """Entfernt flüchtige unsichtbare Zeichen (CR, NBSP, Nullbreite) und nachlaufenden Leerraum (#624)."""
    return _NULLBREITE.sub("", zeile.replace("\r", "").replace("\xa0", " ")).rstrip()


def _ist_fluechtiges_ui_rauschen(zeile: str) -> bool:
    """Flüchtige Claude-Code-UI-Einblendungen über dem Eingabefeld (#624):
    Feedback-Umfrage (:data:`_CLAUDE_UMFRAGE`), ``/clear``-Hinweis (:data:`_CLAUDE_CLEAR_HINWEIS`),
    ``/remote-control``-Banner, ``Tip:``-Hinweise und Update-Meldungen.
    Ihr Erscheinen oder Verschwinden ist weder Aktivität noch eine Rückfrage."""
    sauber = _fluechtige_zeichen_bereinigen(ohne_farben(zeile)).strip()
    if not sauber:
        return False
    ohne_status = STATUS_PRAEFIX.sub("", sauber).strip()
    return bool(
        _CLAUDE_UMFRAGE.fullmatch(sauber)
        or (ohne_status and _CLAUDE_UMFRAGE.fullmatch(ohne_status))
        or _CLAUDE_CLEAR_HINWEIS.fullmatch(sauber)
        or _CLAUDE_REMOTE_CONTROL.fullmatch(sauber)
        or _CLAUDE_REMOTE_URL.fullmatch(sauber)
        or _CLAUDE_TIP_ZEILE.fullmatch(sauber)
        or _CLAUDE_UPDATE_HINWEIS.fullmatch(sauber)
    )


def _ohne_fluechtiges_rauschen(text: str) -> str:
    """Text ohne :func:`_ist_fluechtiges_ui_rauschen` (inkl. umgebrochener ``Tip:``-Folgezeilen)
    samt umgebender Leerzeilen (#624)."""
    zeilen = text.splitlines()
    if not any(_ist_fluechtiges_ui_rauschen(z) for z in zeilen):
        return text
    raus: list[str] = []
    entfernt = False
    tip_einzug: int | None = None
    for z in zeilen:
        sauber = _fluechtige_zeichen_bereinigen(ohne_farben(z))
        kern = sauber.lstrip()
        if tip_einzug is not None:
            einzug = len(sauber) - len(kern)
            if (
                kern
                and einzug > tip_einzug
                and not EINGABEFELD_LINIE.fullmatch(kern)
                and not kern.startswith(("❯", "⎿"))
                and not STATUS_PRAEFIX.match(kern)
                and not _ist_fluechtiges_ui_rauschen(z)
            ):
                entfernt = True
                continue
            tip_einzug = None
        if _ist_fluechtiges_ui_rauschen(z):
            entfernt = True
            if _CLAUDE_TIP_ZEILE.fullmatch(kern):
                tip_einzug = len(sauber) - len(kern)
            continue
        if entfernt:
            entfernt = False
            while raus and not raus[-1].strip():
                raus.pop()
            if not z.strip():
                continue
        raus.append(z)
    if entfernt:
        while raus and not raus[-1].strip():
            raus.pop()
    return "\n".join(raus)


def ist_pane_rauschen(zeile: str) -> bool:
    """Ist ``zeile`` reines Claude-Code-UI-Rauschen ohne Antwort-Inhalt (#372, #624)?

    Einzige Stelle für Aufpasser und Aufseher-Stand: erkennt waagerechte Trennlinien
    (:data:`EINGABEFELD_LINIE`), abgesetzte Dauer-/Abschluss-Fußzeilen
    (``✻ Crunched for 6s · done 7:25 PM``, ``✻ Baked for 40s``), die Feedback-Umfrage
    (``How is Claude doing this session?`` samt Auswahlzeile ``1: Bad … 0: Dismiss``),
    den ``/clear``-Hinweis, ``/remote-control``-Banner und ``Tip:``-Hinweise.
    """
    sauber = _fluechtige_zeichen_bereinigen(ohne_farben(zeile)).strip()
    if not sauber:
        return False
    return bool(
        EINGABEFELD_LINIE.fullmatch(sauber)
        or _CLAUDE_FUSSZEILE.fullmatch(sauber)
        or _ist_fluechtiges_ui_rauschen(sauber)
    )


def ohne_statuszeile(text: str) -> str:
    """Bildschirm-Text ohne die Statuszeile unter dem Eingabefeld (#429, E18).

    Claude Code zeigt unten ein Eingabefeld zwischen zwei Rahmenlinien
    (``────`` / ``❯ …`` / ``────``) und darunter die Statuszeile (Sitzungsdauer,
    Reset-Uhr, Kontext-Stand). Sie ändert sich von allein und ist keine Arbeit.
    Geschnitten wird nur, wenn genau dieses Eingabefeld zu sehen ist; sonst
    (Shell, Dialog mit nur einer Linie, fremdes Programm) bleibt der Text ganz.
    """
    zeilen = text.splitlines()
    feld = _eingabefeld([ohne_farben(z) for z in zeilen])
    return text if feld is None else "\n".join(zeilen[: feld[1]])


def statuszeile(text: str) -> str:
    """Statuszeile unter dem Claude-Eingabefeld (#434); ohne Eingabefeld der ganze Text.

    Gegenstück zu :func:`ohne_statuszeile`: ist das Claude-Eingabefeld zwischen zwei
    Rahmenlinien sichtbar, gehört alles unter der unteren Rahmenlinie zur Statuszeile.
    """
    zeilen = text.splitlines()
    feld = _eingabefeld([ohne_farben(z) for z in zeilen])
    return text if feld is None else "\n".join(zeilen[feld[1] + 1 :])


def _eingabefeld(zeilen: list[str]) -> tuple[int, int] | None:
    """Zeilen-Index der oberen und unteren Rahmenlinie des Claude-Eingabefelds (``❯`` dazwischen)."""
    linien = [i for i, z in enumerate(zeilen) if EINGABEFELD_LINIE.fullmatch(z)]
    if len(linien) < 2:
        return None
    oben, unten = linien[-2], linien[-1]
    if not any(z.lstrip().startswith("❯") for z in zeilen[oben + 1 : unten]):
        return None
    return oben, unten


#: Steuerfolgen im Bild aus ``capture-pane -e``: CSI-Folgen (SGR-Farben, Cursor) mit Parametern
#: in Gruppe 1 und Endzeichen in Gruppe 2, dazu OSC-Folgen (z. B. OSC 8 Links), die mit BEL
#: oder ``ESC \`` enden — bei OSC sind beide Gruppen ``None``.
_STEUERFOLGE = re.compile(r"\x1b\[([0-9;:?]*)([@-~])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def ohne_farben(text: str) -> str:
    """Bild aus ``capture-pane -e`` ohne Steuerfolgen — so, wie ``capture-pane`` ohne ``-e`` es liefert."""
    return _STEUERFOLGE.sub("", text)


def _ohne_gedimmtes(zeile: str) -> str:
    """Zeile ohne gedimmten Text (SGR 2) und ohne Steuerfolgen.

    Claude Code zeigt Platzhalter und Prompt-Vorschläge gedimmt; getippter Text ist es nie.
    Ein einzelner inverser Cursor-Buchstabe (SGR 7 ohne SGR 2) direkt vor einem gedimmten
    Platzhalter hinter ``❯`` gehört ebenfalls zum Platzhalter (#435).
    ``38;2;r;g;b``/``48;5;n`` sind Farbwerte, ihre Zahlen zählen nicht als „gedimmt“ oder
    „invers“. Parameter werden nur an ``;`` geteilt; Unterparameter hinter ``:`` (``4:2``,
    ``38:2::r:g:b``) gehören zu ihrem Code und zählen nie als eigener Wert.
    """
    stuecke: list[tuple[str, bool, bool]] = []
    gedimmt = False
    invers = False
    pos = 0

    def _anhaengen(text: str) -> None:
        if not text:
            return
        if stuecke and stuecke[-1][1] == gedimmt and stuecke[-1][2] == invers:
            stuecke[-1] = (stuecke[-1][0] + text, gedimmt, invers)
        else:
            stuecke.append((text, gedimmt, invers))

    for treffer in _STEUERFOLGE.finditer(zeile):
        _anhaengen(zeile[pos : treffer.start()])
        pos = treffer.end()
        if treffer.group(2) != "m":
            continue
        werte = treffer.group(1).split(";")
        i = 0
        while i < len(werte):
            wert, _, unterwerte = werte[i].partition(":")
            if unterwerte:
                pass  # Code mit eigenen Unterparametern (4:2, 38:2:…): nie Dim/Invers, nichts überspringen
            elif wert in ("", "0"):
                gedimmt = False
                invers = False
            elif wert == "22":
                gedimmt = False
            elif wert == "2":
                gedimmt = True
            elif wert == "7":
                invers = True
            elif wert == "27":
                invers = False
            elif wert in ("38", "48", "58") and i + 1 < len(werte):
                i += 4 if werte[i + 1] == "2" else 2  # Farbmodus + Werte überspringen
            i += 1
    _anhaengen(zeile[pos:])

    raus: list[str] = []
    for i, (text, ist_gedimmt, ist_invers) in enumerate(stuecke):
        if ist_gedimmt:
            continue
        # Inverser Cursor-Buchstabe (SGR 7 ohne SGR 2) direkt vor einem gedimmten Platzhalter (#435).
        vor = "".join(raus).lstrip()
        if (
            ist_invers
            and len(text) == 1
            and i + 1 < len(stuecke)
            and stuecke[i + 1][1]
            and stuecke[i + 1][0].strip()
            and not any(s[1] for s in stuecke[:i])
            and vor.startswith("❯")
            and not vor[1:].strip()
        ):
            continue
        raus.append(text)
    return "".join(raus)


def eingabe_leer(text: str) -> bool:
    """Das Bild zeigt das Claude-Eingabefeld, und hinter ``❯`` steht nichts (#414).

    ``text`` darf Farbcodes tragen (``capture-pane -e``): gedimmter Text hinter ``❯`` ist ein
    Platzhalter oder Prompt-Vorschlag von Claude Code und zählt als leer. Normal gezeigter Text
    (halber Text eines Menschen, auch gemischt mit gedimmtem) zählt als belegt; ohne Farbcodes
    ist jeder Text belegt. Kein Eingabefeld (Shell, Dialog, fremdes Programm) zählt nie als leer.
    """
    zeilen = text.splitlines()
    feld = _eingabefeld([ohne_farben(z) for z in zeilen])
    if feld is None:
        return False
    inhalt = "".join(_ohne_gedimmtes(z) for z in zeilen[feld[0] + 1 : feld[1]]).strip()
    return inhalt.removeprefix("❯").strip() == ""


#: Format-Version von :func:`pane_hash` (#624). Bei neuer Normalisierung hochzählen, damit
#: bestehende ``stand.json``-Einträge mit älterem Hash-Format sauber migrieren, ohne ``seit``
#: oder ``pane_aenderung`` als vermeintliche Aktivität zurückzusetzen.
PANE_HASH_VERSION = 2
#: Format eines 40-stelligen SHA-1-Hex-Digests (unversionierter Alt-Hash v1 und Digest-Anteil ab v2, #624).
_SHA1_HEX = re.compile(r"^[0-9a-f]{40}$")


def _pane_hash_version(wert: object) -> int:
    """Format-Version eines gespeicherten ``pane_hash``-Werts (#624):
    ``v<N>:<sha1>`` ⇒ ``N``, unversionierter 40-stelliger SHA-1-Alt-Hash ⇒ ``1``,
    alles andere (leer, Platzhalter wie ``"alt"``) ⇒ ``0`` (kein Formatwechsel)."""
    if not isinstance(wert, str) or not wert:
        return 0
    if wert.startswith("v"):
        praefix, trenner, rest = wert[1:].partition(":")
        if trenner and praefix.isdigit() and _SHA1_HEX.fullmatch(rest):
            return int(praefix)
        return 0
    return 1 if _SHA1_HEX.fullmatch(wert) else 0


def _zeile_fuer_hash(zeile: str) -> str:
    """Einzelne Inhaltszeile für :func:`pane_hash` ohne blinkende Status-/Spinner-Präfixe (#624)."""
    sauber = _fluechtige_zeichen_bereinigen(zeile)
    if not sauber:
        return ""
    einzug = len(sauber) - len(sauber.lstrip())
    kern = STATUS_PRAEFIX.sub("", sauber[einzug:])
    return f"{sauber[:einzug]}{kern}".rstrip() if kern else ""


def _pane_hash_text(text: str) -> str:
    """Kanonischer Pane-Inhalt für :func:`pane_hash` (#429, #624):
    ohne Statuszeile, Steuerfolgen, gedimmte Eingabe-Platzhalter, flüchtige UI-Einblendungen
    (Umfrage, ``/clear``-Hinweis, ``/remote-control``, ``Tip:``, Update-Hinweise) sowie blinkende
    Status-/Spinner-Zeichen und nachlaufenden Leerraum."""
    zeilen = text.splitlines()
    feld = _eingabefeld([ohne_farben(z) for z in zeilen])
    if feld is not None:
        oben, unten = feld
        eingabe: list[str] = []
        for z in zeilen[oben + 1 : unten]:
            sauber = _fluechtige_zeichen_bereinigen(_ohne_gedimmtes(z))
            if _CLAUDE_EINGABE_PLATZHALTER.fullmatch(sauber.lstrip()):
                sauber = "❯"
            eingabe.append(sauber)
        kopf = _ohne_fluechtiges_rauschen("\n".join(ohne_farben(z) for z in zeilen[:oben]))
        kopf_zeilen = [_zeile_fuer_hash(z) for z in kopf.splitlines()]
        while kopf_zeilen and not kopf_zeilen[-1]:
            kopf_zeilen.pop()
        rahmen = _fluechtige_zeichen_bereinigen(ohne_farben(zeilen[oben]))
        return "\n".join([*kopf_zeilen, rahmen, *eingabe])
    sauber = _ohne_fluechtiges_rauschen(ohne_farben(text))
    raus = [_zeile_fuer_hash(z) for z in sauber.splitlines()]
    while raus and not raus[-1]:
        raus.pop()
    return "\n".join(raus)


def pane_hash(text: str) -> str:
    """Versionierter Hash für „arbeitet oder still“: Statuszeile (#429), flüchtiges UI-Rauschen
    und flüchtige Zeichen (#624) zählen nicht mit."""
    digest = hashlib.sha1(_pane_hash_text(text).encode("utf-8", "replace")).hexdigest()
    return f"v{PANE_HASH_VERSION}:{digest}"


#: Programmname aus ``argv[0]`` bzw. einem Befehlswort, plattformfest
#: (:func:`prozessbaum.programm_name`: unter Windows ``ssh.exe``/``SSH.EXE`` mit Pfad = ``ssh``).
#: Alle Namensvergleiche im Aufpasser gehen hierüber: ``session_id_aus_argv`` und
#: ``session_finden`` (``claude``), ``deploy_art`` (``ssh`` der Live-Tür, ``pytest``),
#: ``kette_folge`` und ``session_beendet`` (:data:`SHELLS`). Linux unverändert.
_programm = prozessbaum.programm_name


def session_id_aus_argv(argv: list[str]) -> str | None:
    """``--session-id <id>``, ``--resume <id>`` oder ``--conversation <id>`` aus ``claude``/``agy``."""
    if not any(werkzeug.ist_session_programm(a) for a in argv):
        return None
    for i, a in enumerate(argv):
        if a in ("--session-id", "--resume", "--conversation") and i + 1 < len(argv):
            return argv[i + 1]
        for flag in ("--session-id=", "--resume=", "--conversation="):
            if a.startswith(flag):
                return a[len(flag) :]
    return None


def _argv(pid: int) -> list[str]:
    """Argumente eines Prozesses (Linux ``/proc``, Windows zerlegte Kommandozeile), unlesbar = ``[]``."""
    return prozessbaum.argv(pid)


def _prozess_cwd(pid: int) -> Path | None:
    """Arbeitsordner eines Prozesses aus ``/proc/<pid>/cwd``, ``None`` = unbekannt."""
    try:
        return Path(os.readlink(f"/proc/{pid}/cwd"))
    except OSError:
        return None


def prozess_argv_text(pid: int) -> str:
    """Argumente eines Prozesses als Text (für Prüfungen)."""
    return " ".join(_argv(pid))


# Prozessbaum und „lebt“ kommen aus dem gemeinsamen Modul (auch respawn nutzt es).
prozess_baum = prozessbaum.baum
_prozess_lebt = prozessbaum.lebt

#: Stufen zum Beenden eines Prozessbaums: SIGHUP, SIGTERM, SIGKILL mit je 5 s Frist.
#: SIGHUP/SIGKILL gibt es unter Windows nicht — dort bleibt nur SIGTERM, sonst bricht
#: schon der Import (Muster wie ``respawn._ABGEFANGENE_SIGNALE``).
BEENDEN_STUFEN = tuple(
    (getattr(signal, name), 5.0) for name in ("SIGHUP", "SIGTERM", "SIGKILL") if hasattr(signal, name)
)

#: Ausgang von ``_fenster_schliessen``: Fenster zu, Claude per ``/exit`` beendet,
#: Claude nach ``/exit`` zwangsbeendet, Claude lebt trotz allem noch.
Schliessart = Literal["fenster", "exit", "zwang", "offen"]


DeployArt = Literal["live", "pruef"]


def deploy_art(argv: list[str], skripte: Sequence[dict[str, Any]] | None = None) -> DeployArt | None:
    """Einzige Stelle, die einen Prozess als Deploy einstuft (#598).

    Die Skripte kommen aus ``deploy.skripte`` (:func:`deploy_skripte`, None ⇒ Vorgabe);
    die Beispiele unten sind die DuoPlus-Vorgabe. Erster Treffer in Listenreihenfolge zählt.

    ``live`` — echter Deploy, sperrt serverweit: ``safe_deploy_vps.sh`` ohne
    ``--nur-gate`` (auch ``--rebuild-runner``, ``--to``), ``staging_deploy.sh``,
    die Live-Tür der Deploy-Schlange (``ssh … server_tuer.sh``).
    ``pruef`` — Gate/Prüflauf, sperrt nur das eigene Fenster: ``pytest``,
    ``safe_deploy_vps.sh --nur-gate``, ``deploy_schlange.py`` selbst (der Dienst
    deployt nur über die Kinder oben, die einzeln zählen).
    ``None`` — nichts davon. Skriptnamen zählen nur in ``argv[:3]``: der Prompt
    einer Claude-Session nennt sie als Text.
    """
    if not argv:
        return None
    liste = VORGABE_SKRIPTE if skripte is None else skripte
    if _programm(argv[0]) == "ssh":
        for s in liste:
            if s["fern"] and s["name"] in argv[-1]:
                return s["art"]
        return None
    kopf = " ".join(argv[:3])
    for s in liste:
        if s["fern"] or s["name"] not in kopf:
            continue
        if s["pruef_schalter"] and s["pruef_schalter"] in " ".join(argv).split():
            return "pruef"
        return s["art"]
    # pytest zählt nur als Programmname (``pytest``, ``python -m pytest``), nie als
    # Pfadteil — sonst wäre jeder Prozess unter ``/tmp/pytest-of-<nutzer>/`` ein Gate (#1411).
    # Tokens trennen auch an Shell-Zeichen/Anführung (``cd x && pytest;``, ``"pytest"``), der
    # Programmname ist der letzte Teil nach ``/`` oder ``\`` (Windows-Pfad), ``-mpytest`` zählt mit.
    for teil in re.split(r"[\s;&|()'\"]+", kopf):
        name = re.split(r"[/\\]", teil.removeprefix("-m"))[-1]
        # ``_programm``: unter Windows auch ``PYTEST.EXE``/``pytest.cmd``; ``.exe`` ab bleibt
        # für Windows-Namen in Linux-Befehlszeilen (so schon vor #251).
        if re.fullmatch(GATE_MUSTER, _programm(name).removesuffix(".exe")):
            return "pruef"
    return None


def _pids_beenden(pids: list[int], wer: str) -> bool:
    """``pids`` stufenweise beenden (``BEENDEN_STUFEN``) — Kinder zuerst übergeben.

    ``wer`` steht in der Logzeile, wenn Prozesse eine Stufe überleben.
    Ergebnis: True, wenn am Ende keiner mehr lebt.
    """
    return prozessbaum.beenden(
        pids,
        BEENDEN_STUFEN,
        takt_s=0.1,
        eskalation=lambda sig, lebende: log.warning("%s: %d Prozesse überlebt, Signal %d", wer, len(lebende), sig),
    )


#: ``bau <N>`` bzw. ``… bau.py <N>`` in der Kommandozeile einer Ketten-Shell.
_BAU_AUFRUF = re.compile(r"\bbau(?:\.py)?\s+(\d+)\b")


def _baum_lesen(pane_pid: int) -> list[int]:
    """Fensterbaum; nicht lesbar (``/proc`` weg) ⇒ leer — Aufrufer fallen zurück."""
    try:
        return prozess_baum(pane_pid) if pane_pid else []
    except OSError as fehler:
        log.warning("Prozessbaum %s nicht lesbar: %s", pane_pid, fehler)
        return []


def bau_lauf_im_baum(pane_pid: int) -> tuple[int, int] | None:
    """(PID, Ticket) des bau.py, das gerade im Fensterbaum läuft (``… bau.py <N> …``).

    In einer Fenster-Kette (``bau N; bau M``) heißt das Fenster nach N weiter
    „bau N“, obwohl bau.py schon M bearbeitet. ``None`` = kein bau.py im Baum.
    """
    for pid in _baum_lesen(pane_pid):
        argv = _argv(pid)
        for i, teil in enumerate(argv[:-1]):
            if teil.endswith("bau.py") and argv[i + 1].isdigit():
                return pid, int(argv[i + 1])
    return None


def echtes_ticket(f: Fenster) -> int | None:
    """Ticket, das in diesem Fenster wirklich läuft (#598).

    Läuft bau.py im Fensterbaum, zählt dessen Ticket (Fenster-Kette); sonst — oder
    wenn die Prozessliste nicht lesbar ist — der Fenstername „bau <N>“.
    """
    lauf = bau_lauf_im_baum(f.pane_pid)
    return lauf[1] if lauf else f.ticket


def kette_folge(pane_pid: int, ticket: int) -> list[int]:
    """Tickets, die die Fenster-Kette nach ``ticket`` noch startet.

    Gelesen aus den Kommandozeilen der Shells im Fensterbaum (``bash -lc '…; bau N;
    bau M'``); Claude (Prompt-Text) zählt nie. Steht ``ticket`` nicht darin, gelten
    alle anderen als Folge — lieber einmal zu vorsichtig als ein Doppel-Start.
    ponytail: von Hand getippte Ketten stehen in keiner Kommandozeile; Upgrade:
    Kette beim Start als Datei je Fenster ablegen.
    """
    funde: list[int] = []
    for pid in _baum_lesen(pane_pid):
        argv = _argv(pid)
        if argv and _programm(argv[0]).lstrip("-") in SHELLS:
            funde.extend(int(n) for n in _BAU_AUFRUF.findall(" ".join(argv)))
    funde = list(dict.fromkeys(funde))
    if ticket in funde:
        return funde[funde.index(ticket) + 1 :]
    return funde


def session_beendet(pane_pid: int) -> bool:
    """Im Pane lebt nur noch eine Shell (oder gar nichts): die Session ist zu Ende.

    Ein Anstupser würde dort als Shell-Befehl laufen und als „Erfolg“ zählen —
    deshalb wird so ein Fenster nie angestupst, sondern gleich fortgesetzt (R2).
    ``cat``/``sleep`` und alles andere gelten nicht als beendet.
    """
    if not pane_pid:
        return True
    baum = prozess_baum(pane_pid)
    lebend = [pid for pid in baum if _prozess_lebt(pid)]
    if not lebend:
        return True
    for pid in lebend:
        argv = _argv(pid)
        if not argv:
            continue
        if _programm(argv[0]).lstrip("-") not in SHELLS:
            return False
    return True


def session_laeuft_schon(sid: str, heim: Path | None = None) -> int | None:
    """PID eines lebenden Claude-Prozesses, der laut ``~/.claude/sessions/*.json``
    dieses Gespräch schon führt — sonst None. Schutz vor zwei ``--resume`` auf
    dasselbe Transkript (anderes Fenster, andere tmux-Sitzung, ohne tmux)."""
    ordner = (heim or Path.home()) / ".claude" / "sessions"
    try:
        dateien = sorted(ordner.glob("*.json"))
    except OSError:
        return None
    for datei in dateien:
        try:
            roh = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(roh, dict) or roh.get("sessionId") != sid:
            continue
        try:
            pid = int(roh.get("pid") or datei.stem)
        except ValueError:
            continue
        if _prozess_lebt(pid):
            return pid
    return None


def _session_json_lesen(pid: int, window_id: str, pane_id: str, heim: Path | None = None) -> SessionInfo | None:
    """``<heim>/.claude/sessions/<pid>.json`` lesen — nur gültig, wenn der Prozess lebt
    und das ``tmux``-Feld (``sitzung:@fenster.%pane``) zu diesem Pane passt oder fehlt.
    PID- und ``procStart``-Abgleich: :func:`speicher.session_json_lesen` (eine Quelle, #460)."""
    datei = (heim or Path.home()) / ".claude" / "sessions" / f"{pid}.json"
    roh = speicher.session_json_lesen(pid, datei.parent, Path("/proc"))  # echte Prozesse, nie die Test-Tür
    if roh is None or not _prozess_lebt(pid):
        return None
    sid = roh.get("sessionId")
    if not isinstance(sid, str) or not sid:
        return None
    tmux_feld = roh.get("tmux")
    if isinstance(tmux_feld, str) and tmux_feld and not tmux_feld.endswith(f"{window_id}.{pane_id}"):
        log.info(
            "Session-JSON %s gehört zu %s, nicht zu %s.%s — ignoriert",
            datei.name,
            tmux_feld,
            window_id,
            pane_id,
        )
        return None
    cwd = roh.get("cwd")
    status = roh.get("status")
    try:
        mtime: float | None = datei.stat().st_mtime
    except OSError:
        mtime = None
    return SessionInfo(
        session_id=sid,
        cwd=Path(cwd) if isinstance(cwd, str) and cwd else None,
        status=status if isinstance(status, str) else None,
        pid=pid,
        aus_json=True,
        mtime=mtime,
    )


def session_finden(pane_pid: int, window_id: str, pane_id: str, heim: Path | None = None) -> SessionInfo | None:
    """Gesprächs-ID und Zustand des Claude-Prozesses unter ``pane_pid``.

    Reihenfolge: ``--session-id``/``--resume`` aus argv (muss zur Session-JSON
    passen, falls es eine gibt) → Session-JSON → None. Kein Transkript-Fallback.
    """
    for pid in prozess_baum(pane_pid):
        argv = _argv(pid)
        if not any(werkzeug.ist_session_programm(a) for a in argv):
            continue
        argv_sid = session_id_aus_argv(argv)
        info = _session_json_lesen(pid, window_id, pane_id, heim)
        if info is not None:
            if argv_sid and argv_sid != info.session_id:
                log.warning(
                    "PID %s: argv nennt %s…, Session-JSON %s… — Widerspruch, keine ID",
                    pid,
                    argv_sid[:8],
                    info.session_id[:8],
                )
                return None
            return info
        if argv_sid:
            try:
                cwd: Path | None = Path(os.readlink(f"/proc/{pid}/cwd"))
            except OSError:
                cwd = None
            return SessionInfo(argv_sid, cwd, None, pid, aus_json=False)
    return None


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    try:
        ergebnis = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=300,
            **prozessbaum.fensterlos(),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args[:3])}: Zeitgrenze 300 s") from None
    except FileNotFoundError as fehler:
        raise RuntimeError(f"git {' '.join(args[:3])}: {fehler}") from None
    if ergebnis.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])}: {ergebnis.stderr.strip()[:300]}")
    return ergebnis.stdout


#: Öffentlicher Name für andere Module (Hausmeister #1368); ``_git`` bleibt.
git_lauf = _git


def worktree_schmutzig(worktree: Path) -> bool:
    # ``status.showUntrackedFiles=no`` darf neue Dateien nicht verstecken (#1368).
    return bool(_git(worktree, "status", "--porcelain", "--untracked-files=normal").strip())


def worktree_finden(repo: Path, ticket: int, cwd: Path | None = None) -> Path | None:
    """Worktree eines Tickets aus ``git worktree list`` — Basename ``wt-<N>``, sonst
    ``cwd`` der Session, falls es ein registrierter Worktree ist.

    Das Hauptrepo zählt nie: es ist der geteilte Baum aller Sessions, dort liegt
    fremde Arbeit, die niemand als „Sicherung #N“ wegpushen darf. Kein Treffer ⇒
    None („nichts zu sichern“), ohne Umweg über Umgebungsvariablen.
    """
    haupt = Path(_git(repo, "rev-parse", "--show-toplevel").strip()).resolve()
    kandidaten: list[Path] = []
    for zeile in _git(repo, "worktree", "list", "--porcelain").splitlines():
        if zeile.startswith("worktree "):
            pfad = Path(zeile[len("worktree ") :]).resolve()
            if pfad != haupt:
                kandidaten.append(pfad)
    for pfad in kandidaten:
        if pfad.name == f"wt-{ticket}":
            return pfad
    if cwd is not None:
        try:
            ziel = cwd.resolve()
        except OSError:
            return None
        if ziel in kandidaten:
            return ziel
    return None


def _ref_lesen(worktree: Path, ref: str) -> str | None:
    try:
        return _git(worktree, "rev-parse", "--verify", "-q", ref).strip() or None
    except RuntimeError:
        return None


def sichern(
    worktree: Path,
    ticket: int,
    jetzt: float | None = None,
    zusatz_koepfe: Sequence[str] = (),
    kopf: str | None = None,
) -> str | None:
    """Arbeitsstand als Commit auf ``sicherung/<N>`` sichern und pushen.

    Kein stash, kein Branch-Wechsel, kein Index-Zugriff: temporärer ``GIT_INDEX_FILE``
    ab ``HEAD`` → ``add -A`` ohne Geheimnisse (``SICHERUNG_AUSSCHLUSS``) →
    ``write-tree`` → ``commit-tree`` mit Eltern ``HEAD`` und (falls vorhanden) der
    vorigen ``sicherung/<N>`` → ``push`` → ``update-ref`` (kein ``+``, die Kette ist
    fast-forward). Rückgabe = Commit-SHA; sauberer Baum → None. Fehler ⇒ RuntimeError.

    ``zusatz_koepfe`` (Refs/SHAs, z. B. lokale Zweige mit Commits auf keinem Remote):
    werden zusätzliche Eltern, damit der Push sie mitnimmt — dann gibt es auch bei
    sauberem Baum einen Commit (Baum = ``HEAD``). Leer ⇒ Verhalten wie zuvor (#1368).

    ``kopf`` (Zweig ohne Arbeitsordner, Hausmeister #1439): sichert diesen Stand statt
    ``HEAD`` + Arbeitsdateien — Baum = Baum von ``kopf``, keine Datei aus dem Ordner
    (im Hauptrepo läge dort fremde Arbeit); geht auch im Bare-Repo. Immer ein Commit.
    """
    if kopf is None and not zusatz_koepfe and not worktree_schmutzig(worktree):
        return None
    handle, index = tempfile.mkstemp(prefix="aufpasser-index-")
    os.close(handle)
    os.unlink(index)
    env = {**os.environ, **GIT_IDENTITAET, "GIT_INDEX_FILE": index}
    ref = f"refs/heads/sicherung/{ticket}"
    try:
        if kopf is None:
            kopf = _git(worktree, "rev-parse", "HEAD").strip()
            _git(worktree, "read-tree", kopf, env=env)
            ausschluss = [f":(exclude,glob){m}" for m in (*SICHERUNG_AUSSCHLUSS, *NIE_HOCHLADEN)]
            _git(worktree, "add", "-A", "--", ".", *ausschluss, env=env)
            baum = _git(worktree, "write-tree", env=env).strip()
        else:
            kopf = _git(worktree, "rev-parse", "--verify", f"{kopf}^{{commit}}").strip()
            baum = _git(worktree, "rev-parse", f"{kopf}^{{tree}}").strip()
        eltern = ["-p", kopf]
        vorige = _ref_lesen(worktree, ref)
        gesehen = {kopf}
        for weiterer in [vorige or "", *zusatz_koepfe]:
            sha = _git(worktree, "rev-parse", "--verify", f"{weiterer}^{{commit}}").strip() if weiterer else ""
            if sha and sha not in gesehen:
                gesehen.add(sha)
                eltern += ["-p", sha]
        zeit = datetime.fromtimestamp(jetzt or time.time(), tz=UTC).astimezone()
        commit = _git(
            worktree,
            "commit-tree",
            baum,
            *eltern,
            "-m",
            f"Aufpasser: Sicherung #{ticket} {zeit.isoformat(timespec='seconds')} [skip ci]",
            env=env,
        ).strip()
    finally:
        Path(index).unlink(missing_ok=True)
    # #215: dieselbe Sperre wie beim Zweig-Upload und beim Hausmeister — nie vor dem Push
    # eine Übergabe oder ein Entscheidungs-Log (auch in anderer Großschreibung) hochladen.
    ausschluss_refs = (
        ("--remotes=origin", "--glob=refs/haupt/*", "--glob=refs/sicherung/*", "--glob=refs/zweige/*")
        if kopf is not None
        else ("--remotes=origin",)
    )
    try:
        gesperrt = uebergabe_sichern.gesperrte_pfade(worktree, commit, "--not", *ausschluss_refs)
    except OSError as fehler:
        raise RuntimeError(f"Prüfung gesperrter Pfade gescheitert: {fehler}") from None
    if gesperrt:
        log.error(
            "Sicherung #%s verweigert: %s nie öffentlich",
            ticket,
            ", ".join(gesperrt),
        )
        raise RuntimeError(f"Sicherung verweigert: {', '.join(gesperrt)} nie öffentlich")
    # #242: erst pushen, dann lokalen Ref nachziehen — ein gescheiterter Push darf
    # keinen ungepushten lokalen sicherung/<N>-Zweig hinterlassen.
    _git(worktree, "push", "-q", "origin", f"{commit}:{ref}")
    _git(worktree, "update-ref", ref, commit)
    return commit


def _crontab(*args: str, eingabe: str | None = None) -> subprocess.CompletedProcess[str]:
    """Ruft ``crontab`` auf; Dateipfade als Argument sind gesperrt (#757).

    Debian-/Vixie-``crontab`` speichert den Dateinamen in einem festen Puffer
    (``MAX_FNAME = 100``, also max. 99 Zeichen + NUL) und schneidet längere Pfade
    still ab. Neue Crontabs gehen darum immer über ``"-"`` + ``eingabe`` (stdin).
    """
    i = 0
    while i < len(args):
        teil = args[i]
        if teil == "-u":
            i += 2
            continue
        if teil == "-" or teil.startswith("-"):
            i += 1
            continue
        raise ValueError(
            f"crontab nie mit Dateipfad aufrufen ({teil!r}): Linux-Vixie-cron schneidet Pfade ab 99 Zeichen ab "
            "— stattdessen '-' mit eingabe=... (stdin) nutzen (#757)"
        )
    try:
        return subprocess.run(
            ["crontab", *args],
            input=eingabe,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
            **prozessbaum.fensterlos(),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("crontab antwortet nicht (30 s)") from None
    except FileNotFoundError:
        raise RuntimeError("crontab nicht installiert") from None


def cron_einrichten(
    zustand: Path,
    skript: Path = SKRIPT_STARTER,
    python: str = sys.executable,
    jetzt: float | None = None,
    trocken: bool = False,
) -> str:
    """Cron-Zeile anlegen oder ersetzen (idempotent); alte crontab wird vorher gesichert.

    Ersetzt wird jede Zeile mit der Marke UND jede Alt-Zeile, die ``aufpasser.py``
    aufruft (z. B. der alte Pfad ``~/aufpasser/aufpasser.py`` ohne Marke) — danach
    gibt es genau eine Aufpasser-Zeile. ``trocken``: nur zeigen, nichts schreiben.
    """
    alt = _crontab("-l")
    zeilen = alt.stdout.splitlines() if alt.returncode == 0 else []
    zeile = f"{CRON_TAKT} {python} {skript} >> {zustand}/{CRON_LOG} 2>&1  {CRON_MARKE}"

    def ist_aufpasser(z: str) -> bool:
        return CRON_MARKE in z or ("aufpasser.py" in z and not z.lstrip().startswith("#"))

    neu = [z for z in zeilen if not ist_aufpasser(z)]
    stellen = [i for i, z in enumerate(zeilen) if ist_aufpasser(z)]
    neu.insert(stellen[0] if stellen else len(neu), zeile)
    if neu == zeilen:
        return f"Cron unverändert: {zeile}"
    if trocken:
        return f"[trocken] Cron würde eingerichtet: {zeile}"
    zustand.mkdir(parents=True, exist_ok=True)
    stempel = datetime.fromtimestamp(jetzt or time.time(), tz=UTC).astimezone()
    stempel = stempel.strftime("%Y%m%d-%H%M%S")
    (zustand / f"crontab.vorher.{stempel}").write_text(alt.stdout, encoding="utf-8")
    ergebnis = _crontab("-", eingabe="\n".join(neu) + "\n")
    if ergebnis.returncode != 0:
        raise RuntimeError(f"crontab schreiben: {ergebnis.stderr.strip()[:300]}")
    return f"Cron eingerichtet: {zeile}"


# --- Der Aufpasser -----------------------------------------------------------


@dataclass
class Stand:
    fenster: dict[str, dict] = field(default_factory=dict)
    meldungen: dict[str, float] = field(default_factory=dict)
    #: Start-Gedächtnis je Ticket: Zeitstempel der Fenster-Starts (Regel 1).
    starts: dict[str, list[float]] = field(default_factory=dict)
    #: Zeitstempel des letzten abgeschlossenen :meth:`Aufpasser.platz_schaffen`-Laufs (#624).
    platz_zuletzt: float = 0.0
    #: Letzte Limit-Probe je ``Programm|Modell``: ``{"zeit": t, "offen": bool}`` (#1453, #892).
    limit_proben: dict[str, dict[str, Any]] = field(default_factory=dict)


class Aufpasser:
    def __init__(self, e: Einstellungen) -> None:
        self.e = e
        self.jetzt = e.jetzt()
        #: Deploy-Muster dieses Laufs (mit Repo-Konfig) — Deploy-Wache vor jedem Eingriff (R4).
        self.muster = e.deploy_muster or DEPLOY_MUSTER
        #: Deploy-Skripte dieses Laufs (``deploy.skripte`` der Repos) für ``deploy_art``.
        self.skripte: list[dict[str, Any]] = VORGABE_SKRIPTE
        self.datum = datetime.fromtimestamp(self.jetzt, tz=UTC).astimezone().strftime("%Y-%m-%d")
        self.stand = self._stand_laden()
        #: Echte Fenster-Starts in diesem Tick (Speicher-Staffel, #257 Paket B):
        #: ab dem zweiten Start wartet der Tick ``staffel_s`` Sekunden, damit die
        #: Claude-Starts sich nicht stapeln. Am Tick-Anfang wieder 0.
        self._starts_in_tick = 0
        #: Letzte Beobachtung, warum :meth:`_start_nachweisen` ``False`` lieferte (#288).
        self._letzter_start_befund = ""
        #: Sammel-Liste für gemeinsame Gegenprobe in :meth:`platz_schaffen` (#624).
        self._platz_kandidaten: list[dict[str, Any]] | None = None
        #: Je Sitzung der Abschluss-Grund für stille ``leitstand``-/``wache``-Fenster (#624).
        self._platz_spec_grund: dict[str, str | None] = {}

    # -- Werkzeuge ---------------------------------------------------------------

    def sh(self, *args: str, cwd: Path | None = None, timeout: float = 120, start: bool = False) -> str:
        """Befehl ausführen, Ausgabe zurück; Exit ≠ 0 → ``RuntimeError``.

        ``start=True`` für Aufrufe, die etwas starten — die gehen durch die Start-Tür (#1406).
        """
        optionen = {
            "cwd": str(cwd) if cwd else None,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "check": False,
            "timeout": timeout,
        }
        try:
            if start:
                ergebnis = starter.starte(args, warten=True, **optionen)
            else:
                ergebnis = subprocess.run(args, **optionen, **prozessbaum.fensterlos())
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{' '.join(args[:4])}: Zeitgrenze {timeout:g} s überschritten") from None
        except FileNotFoundError as fehler:
            # Fester Kennsatz: der Systemtext ist unter Windows übersetzt („WinError 2 …“).
            raise RuntimeError(f"{' '.join(args[:4])}: No such file — {fehler}") from None
        if ergebnis.returncode != 0:
            raise RuntimeError(f"{' '.join(args[:4])}: {ergebnis.stderr.strip()[:300]}")
        return ergebnis.stdout

    def tmux(self, *args: str, start: bool = False) -> str:
        socket = ["-L", self.e.tmux_socket] if self.e.tmux_socket else []
        return self.sh("tmux", *socket, *args, start=start)

    def _stand_laden(self) -> Stand:
        datei = self.e.zustand / "stand.json"
        try:
            roh = json.loads(datei.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return Stand()
        if not isinstance(roh, dict):
            log.warning("stand.json: Wurzelobjekt ist kein Dict (%s) — starte leer", datei)
            return Stand()
        verworfen = 0

        platz_roh = roh.get("platz_zuletzt")
        if platz_roh is None:
            platz_zuletzt = 0.0
        else:
            zahl = _als_zahl(platz_roh)
            if zahl is None:
                platz_zuletzt = 0.0
                verworfen += 1
            else:
                platz_zuletzt = zahl

        fenster: dict[str, dict[str, Any]] = {}
        fenster_roh = roh.get("fenster")
        if isinstance(fenster_roh, dict):
            for k, v in fenster_roh.items():
                if isinstance(k, str) and isinstance(v, dict):
                    fenster[k] = v
                else:
                    verworfen += 1
        elif fenster_roh is not None:
            verworfen += 1

        meldungen: dict[str, float] = {}
        meldungen_roh = roh.get("meldungen")
        if isinstance(meldungen_roh, dict):
            for k, v in meldungen_roh.items():
                zahl = _als_zahl(v)
                if isinstance(k, str) and zahl is not None:
                    meldungen[k] = zahl
                else:
                    verworfen += 1
        elif meldungen_roh is not None:
            verworfen += 1

        starts: dict[str, list[float]] = {}
        starts_roh = roh.get("starts")
        if isinstance(starts_roh, dict):
            for k, v in starts_roh.items():
                if not isinstance(k, str) or not isinstance(v, list):
                    verworfen += 1
                    continue
                zeiten: list[float] = []
                for t in v:
                    zahl = _als_zahl(t)
                    if zahl is None:
                        verworfen += 1
                    else:
                        zeiten.append(zahl)
                if zeiten:
                    starts[k] = zeiten
        elif starts_roh is not None:
            verworfen += 1

        limit_proben: dict[str, dict[str, Any]] = {}
        proben_roh = roh.get("limit_proben")
        if isinstance(proben_roh, dict):
            for k, v in proben_roh.items():
                if isinstance(k, str) and isinstance(v, dict) and _als_zahl(v.get("zeit")) is not None:
                    limit_proben[k] = v
                else:
                    verworfen += 1
        elif proben_roh is not None:
            verworfen += 1

        if verworfen:
            log.warning("stand.json: %d beschädigte Einträge verworfen (%s)", verworfen, datei)
        return Stand(
            fenster=fenster,
            meldungen=meldungen,
            starts=starts,
            platz_zuletzt=platz_zuletzt,
            limit_proben=limit_proben,
        )

    def _stand_schreiben(self) -> None:
        if self.e.trocken:
            return
        grenze = self.jetzt - 2 * 86400
        meldungen = {k: t for k, t in self.stand.meldungen.items() if t >= grenze}
        starts = {k: [t for t in v if self.jetzt - t < START_FENSTER_S] for k, v in self.stand.starts.items()}
        starts = {k: v for k, v in starts.items() if v}
        limit_proben = {
            k: v for k, v in self.stand.limit_proben.items() if isinstance(v, dict) and _probe_gueltig(v, self.jetzt)
        }
        self.e.zustand.mkdir(parents=True, exist_ok=True)
        daten: dict[str, Any] = {
            "fenster": self.stand.fenster,
            "meldungen": meldungen,
            "starts": starts,
            "limit_proben": limit_proben,
        }
        if self.stand.platz_zuletzt:
            daten["platz_zuletzt"] = self.stand.platz_zuletzt
        (self.e.zustand / "stand.json").write_text(
            json.dumps(
                daten,
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

    # -- Lesen -------------------------------------------------------------------

    def deploy_muster_fuer(self, repos: Iterable[Path]) -> str:
        """Deploy-Muster dieses Laufs, ODER-verknüpft; setzt :attr:`skripte`.

        ``--deploy-muster`` (fest) oder sonst die Namen aus ``deploy.skripte`` der Repos
        (keine lesbare Konfig ⇒ Vorgabe), dazu ``deploy_befehl``/``staging_start`` der
        Repo-Konfigs (jeweils der Basename)."""
        teile = [self.e.deploy_muster] if self.e.deploy_muster else []
        skripte: dict[str, dict[str, Any]] = {}
        gelesen = False
        for repo in repos:
            try:
                konfig = config.lade(repo)
            except (OSError, ValueError) as fehler:
                log.warning("%s: Konfig nicht lesbar: %s", repo, fehler)
                continue
            gelesen = True
            for s in deploy_skripte(konfig):
                skripte.setdefault(s["name"], s)
            for schluessel in ("deploy_befehl", "staging_start"):
                wert = konfig.get(schluessel)
                if not isinstance(wert, str) or not wert.strip():
                    continue
                for wort in shlex.split(wert):
                    name = Path(wort).name
                    if name.endswith((".sh", ".py")):
                        teile.append(re.escape(name))
        self.skripte = list(skripte.values()) if gelesen else VORGABE_SKRIPTE
        if not self.e.deploy_muster and self.skripte:
            teile.insert(0, deploy_muster_aus(self.skripte))
        return "|".join(dict.fromkeys(teile)) or NIE_MUSTER

    def _prozess_alter_s(self, pid: int) -> float | None:
        if sys.platform == "win32":
            return prozessbaum.alter_s(pid)
        try:
            raus = self.sh("ps", "-o", "etimes=", "-p", str(pid), timeout=30).strip()
            return float(raus) if raus else None
        except (RuntimeError, ValueError):
            return None

    def deploy_laeuft(self, muster_text: str | None = None) -> list[str]:
        """Prozesse, die auf das Deploy-Muster passen (ohne uns selbst, ohne Waisen).

        Sperrt serverweit; Prüfläufe (``deploy_art`` „pruef“) zählen nicht — die
        sperren nur ihr Fenster (:meth:`gate_im_fenster`). Regeln der Trefferzählung
        in :meth:`_prozesse_treffer`.
        """
        return [kopf for _, kopf, art in self._prozesse_treffer(muster_text or self.muster) if art != "pruef"]

    def gate_im_fenster(self, f: Fenster, worktree: Path | None) -> str | None:
        """Läuft ein Gate/Prüflauf (``deploy_art`` „pruef“) genau dieses Fensters?
        Dann dessen Kopfzeile.

        Zählt nur, wenn der Prozess im Prozessbaum des Panes hängt oder sein
        Arbeitsordner im Ticket-Worktree liegt — fremde Testläufe (andere Worktrees,
        andere Repos) sperren dieses Fenster nicht (#592). ``worktree`` None (z. B.
        ``wache``-Fenster) ⇒ nur der Fensterbaum zählt.
        """
        treffer = [
            (pid, kopf) for pid, kopf, art in self._prozesse_treffer(f"{GATE_MUSTER}|{self.muster}") if art == "pruef"
        ]
        if not treffer:
            return None
        baum = set(prozess_baum(f.pane_pid)) if f.pane_pid else set()
        wt = worktree.resolve() if worktree is not None else None
        for pid, kopf in treffer:
            if pid in baum:
                return kopf
            if wt is not None:
                cwd = _prozess_cwd(pid)
                if cwd is not None and cwd.is_relative_to(wt):
                    return kopf
        return None

    def _prozesse_treffer(self, muster_text: str) -> list[tuple[int, str, DeployArt | None]]:
        """(PID, Kopfzeile, ``deploy_art``) der Prozesse, die auf ``muster_text`` passen.

        :func:`prozessbaum.passende` liefert die Kandidaten (Linux ``pgrep -af``, Windows
        Kernel-Prozessliste, eigene Aufrufkette nie dabei); gezählt wird nur ein Treffer im Programm,
        Skript oder ersten Argument (``argv[:3]``; Live-Tür per ssh: ``deploy_art``)
        — der Prompt einer Claude-Session
        nennt ``safe_deploy_vps.sh`` und ``pytest`` als Text, ist aber kein Deploy.
        Die eigene Aufrufkette (Cron, Shell) zählt nie; Prozesse älter als 6 h sind
        verwaist („Waise ignoriert“).
        """
        try:
            kandidaten = prozessbaum.passende(muster_text, zeitgrenze=30)
        except prozessbaum.ProzesslisteFehler as fehler:
            # Abfrage gestört: lieber nichts anfassen (fail-closed) als ein Deploy übersehen.
            raise RuntimeError(f"Deploy-Wache: {fehler}") from None
        muster = re.compile(muster_text)
        treffer = []
        for zahl, args in kandidaten:
            pid = str(zahl)
            argv = _argv(zahl)
            kopf = " ".join(argv[:3]) or args
            art = deploy_art(argv, self.skripte)
            if not muster.search(kopf) and art != "live":
                continue
            alter = self._prozess_alter_s(int(pid))
            if alter is not None and alter > DEPLOY_ALTER_MAX_S:
                log.info(
                    "Waise ignoriert: PID %s seit %.1f h (%s)",
                    pid,
                    alter / 3600,
                    kopf[:120],
                )
                continue
            treffer.append((int(pid), kopf[:200], art))
        return treffer

    def sitzungen(self) -> list[tuple[str, str]]:
        try:
            namen = self.tmux("list-sessions", "-F", "#{session_name}").split()
        except RuntimeError as fehler:
            log.info("kein tmux-Server erreichbar: %s", fehler)
            return []
        gefunden = []
        for name in namen:
            spec = tmux_aufruf.spec_aus_sitzung(name)
            if spec is not None:
                gefunden.append((name, spec))
        return gefunden

    def fenster(self, sitzung: str, spec: str) -> list[Fenster]:
        liste = []
        for zeile in self.tmux(
            "list-windows",
            "-t",
            f"={sitzung}",
            "-F",
            "#{window_index}\t#{window_name}\t#{pane_current_path}\t#{pane_pid}\t#{window_id}\t#{pane_id}\t#{pane_start_path}",
        ).splitlines():
            teile = zeile.split("\t")
            if len(teile) not in (6, 7):
                continue
            start_pfad = teile[6] if len(teile) == 7 else ""
            # Direkt nach ``tmux new-session -d`` kann ``pane_current_path`` unter Last noch
            # leer sein (Kindprozess hat tcgetpgrp/chdir noch nicht abgeschlossen);
            # ``pane_start_path`` kennt tmux sofort beim Anlegen des Panes.
            pfad = teile[2] or start_pfad
            liste.append(
                Fenster(
                    sitzung,
                    spec,
                    teile[0],
                    teile[1],
                    pfad,
                    int(teile[3] or 0),
                    teile[4],
                    teile[5],
                    start_pfad,
                )
            )
        return liste

    def belegte_tickets(self, ausser_pane: int = 0) -> set[int]:
        """Tickets, die in einem Pane dieses tmux-Servers laufen oder in dessen
        Fenster-Kette noch folgen (#598) — ``ausser_pane`` zählt nicht mit."""
        belegt: set[int] = set()
        try:
            raus = self.tmux("list-panes", "-a", "-F", "#{window_name}\t#{pane_pid}")
        except RuntimeError as fehler:
            log.warning("Panes nicht lesbar: %s — nur Fensternamen zählen", fehler)
            return belegt
        for zeile in raus.splitlines():
            name, _, pid_text = zeile.partition("\t")
            pane_pid = int(pid_text) if pid_text.isdigit() else 0
            if pane_pid == ausser_pane:
                continue
            f = Fenster("", "", "", name, "", pane_pid)
            ticket = echtes_ticket(f)
            if ticket is not None:
                belegt.add(ticket)
                belegt.update(kette_folge(pane_pid, ticket))
        return belegt

    def repo_ordner(self, fenster: list[Fenster]) -> Path | None:
        for attr in ("pfad", "start_pfad"):
            for f in fenster:
                roh_pfad = getattr(f, attr, "")
                if not roh_pfad:
                    continue
                pfad = Path(roh_pfad)
                if (pfad / config.KONFIG_PFAD).is_file():
                    return pfad
        if self.e.repo:
            return self.e.repo
        roh = os.environ.get("TO_SPAWN_REPO")
        return Path(roh) if roh else None

    def spec_repo_ordner(self, spec: str, fenster: list[Fenster], basis: Path | None) -> Path | None:
        """Ordner des Spec-Klons finden, in dem das Manifest liegt (#271 Nachtrag 4).

        Startet der Aufpasser den Aufseher im Hauptrepo statt im Spec-Klon, fehlt dort oft
        das Spec-Manifest (:func:`manifest.manifest_pfad`) und ``startklar.gate`` endet mit Exit 2.
        """
        kandidaten: list[Path] = []
        gesehen: set[Path] = set()

        def merk(pfad: Path | None) -> None:
            if pfad is None:
                return
            try:
                echt = pfad.resolve()
            except OSError:
                return
            if echt not in gesehen and echt.is_dir():
                gesehen.add(echt)
                kandidaten.append(echt)

        for f in fenster:
            for attr in ("pfad", "start_pfad"):
                f_pfad = getattr(f, attr, None)
                if not f_pfad:
                    continue
                p = Path(f_pfad)
                merk(p)
                merk(_hauptklon_aus_worktree(p))
        merk(basis)

        def hat_manifest(ordner: Path) -> bool:
            if not (ordner / config.KONFIG_PFAD).is_file():
                return False
            try:
                return manifest.manifest_pfad(ordner, spec).is_file()
            except (OSError, ValueError):
                return False

        for kand in kandidaten:
            if hat_manifest(kand):
                return kand

        # Nachbar-Klone im selben Elternordner prüfen (z. B. ``projekt-1330`` neben ``projekt-haupt``).
        basis_origin = _origin_kennung(basis) if basis is not None and basis.is_dir() else ""
        eltern = {k.parent for k in kandidaten if k.parent.is_dir()}
        treffer_origin: Path | None = None
        treffer_name: Path | None = None
        for ordner in sorted(eltern):
            try:
                kinder = sorted(ordner.iterdir())
            except OSError:
                continue
            for kind in kinder:
                try:
                    kind_echt = kind.resolve()
                except OSError:
                    continue
                if not kind.is_dir() or kind_echt in gesehen:
                    continue
                if not hat_manifest(kind):
                    continue
                if basis_origin and _origin_kennung(kind) == basis_origin:
                    treffer_origin = kind
                    break
                if kind.name.endswith(f"-{spec}") and treffer_name is None:
                    treffer_name = kind
            if treffer_origin is not None:
                return treffer_origin
        if treffer_name is not None:
            return treffer_name
        return basis

    def offene_tickets(self, spec: str, repo: Path) -> dict[int, dict]:
        """Unter-Tickets, die nicht :func:`ticket_zustand.endgueltig_zu` sind, je Nummer
        mit ``state``/``labels`` — aus EINEM Listen-Aufruf, kein ``issue view`` je Ticket
        (#279: ein hängendes ``gh`` hielt sonst den ganzen Takt auf). Unlesbar ⇒
        ``RuntimeError``."""
        raus = self.sh(
            "gh",
            "api",
            f"repos/{{owner}}/{{repo}}/issues/{spec}/sub_issues",
            "--paginate",
            "--jq",
            ".[] | {number, state, state_reason, labels: [.labels[]?.name]} | tojson",
            cwd=repo,
        )
        try:
            tickets = [json.loads(z) for z in raus.splitlines() if z.strip()]
            if not all(isinstance(t, dict) for t in tickets):
                raise TypeError("Zeile ist kein JSON-Objekt")
            return {int(t["number"]): t for t in tickets if not ticket_zustand.endgueltig_zu(t)}
        except (json.JSONDecodeError, KeyError, TypeError) as fehler:
            raise RuntimeError(f"sub_issues {spec}: unlesbar ({fehler})") from None

    def ticket_lesen(self, ticket: int, repo: Path) -> dict:
        """Ticket über :func:`ticket_zustand.lesen` — alle Felder des Bausteins, ``labels``
        als Menge der Namen (für ``& STOPP_LABELS``). ``RuntimeError``, wenn GitHub
        nichts Lesbares liefert; der Aufrufer entscheidet dann vorsichtig."""
        daten = ticket_zustand.lesen(ticket, repo)
        if daten is None:
            raise RuntimeError(f"#{ticket}: Ticket-Zustand nicht lesbar (gh)")
        return {**daten, "labels": ticket_zustand.label_namen(daten)}

    def zu_bauen(self, offen: dict[int, dict], *, repo: Path | None = None) -> set[int]:
        """Die offenen Tickets, die noch als Bau-Session starten dürfen (#279, #854).
        Urteil aus den Listen-Daten über :func:`ticket_zustand.bau_startbar`, kein weiterer Aufruf."""
        label = ticket_zustand.abnahme_label(repo)
        return {n for n, t in offen.items() if ticket_zustand.bau_startbar(t, checkpoint_label=label)}

    def pane_text(self, ziel: str) -> str:
        return self.tmux("capture-pane", "-p", "-t", ziel)

    _ORIGINAL_PANE_TEXT = pane_text

    def _pane_lesen(self, ziel: str) -> tuple[str, str]:
        """Liefert ``(klartext, farbig)`` für ein tmux-Pane (#624).

        Mit ``capture-pane -e -p`` erkennt :func:`pane_hash` auch gedimmte Claude-Code-
        Eingabevorschläge (SGR 2) über :func:`_ohne_gedimmtes`. Ist :meth:`pane_text`
        (z. B. per ``monkeypatch.setattr(Aufpasser, "pane_text", …)``) überschrieben,
        wird dessen Rückgabe für beide Werte genutzt.
        """
        if type(self).pane_text is not Aufpasser._ORIGINAL_PANE_TEXT or "pane_text" in self.__dict__:
            text = self.pane_text(ziel)
            return text, text
        farbig = self.tmux("capture-pane", "-e", "-p", "-t", ziel)
        klartext = "\n".join(z.rstrip() for z in ohne_farben(farbig).splitlines())
        if farbig.endswith("\n"):
            klartext += "\n"
        return klartext, farbig

    def session(self, f: Fenster) -> SessionInfo | None:
        if not f.pane_pid:
            return None
        return session_finden(f.pane_pid, f.window_id, f.pane_id, self.e.heim)

    # -- Melden ------------------------------------------------------------------

    def melden(self, spec: str, repo: Path, fenster: str, ereignis: str, text: str) -> None:
        """Logzeile + Kommentar im Spec-Issue. Jeder Eingriff bekommt seine Zeile;
        Meldungen ohne Eingriff (``MELDUNG_EINMAL_PRO_TAG``) je Fenster/Ereignis/Tag
        nur einmal — der Doppelschutz-Schlüssel wird erst nach erfolgreichem
        Kommentar gesetzt (R3)."""
        ereignis = EREIGNIS_ALIAS.get(ereignis, ereignis)
        log.info("spec %s: %s", spec, text)
        schluessel = f"{fenster}|{ereignis}|{self.datum}"
        if self.e.trocken:
            print(f"[trocken] spec {spec}: {text}")
            return
        # Vorfall VOR der Tages-Sperre: die Lernschleife hat ihren eigenen
        # Doppelschutz, sonst verlöre ein gescheiterter Schreibversuch den Tag.
        self.vorfall_notieren(spec, repo, fenster, ereignis, text)
        schon_gemeldet = schluessel in self.stand.meldungen or any(
            f"{fenster}|{alt}|{self.datum}" in self.stand.meldungen
            for alt, neu in EREIGNIS_ALIAS.items()
            if neu == ereignis
        )
        if ereignis in MELDUNG_EINMAL_PRO_TAG and schon_gemeldet:
            log.info("spec %s: Meldung heute schon abgesetzt (%s)", spec, ereignis)
            return
        try:
            self.sh("gh", "issue", "comment", spec, "--body", f"Aufpasser: {text}", cwd=repo)
        except RuntimeError as fehler:
            log.error("spec %s: Kommentar fehlgeschlagen: %s", spec, fehler)
            return
        self.stand.meldungen[schluessel] = self.jetzt

    @staticmethod
    def log_ordner(repo: Path, ticket: str) -> Path:
        """Wohin die Vorfall-Zeile gehört: Worktree des Tickets, sonst Hauptbaum.

        capo liest Laufdateien nur im Repo selbst und in ``wt-<Ticket>``. Ein
        Vorfall zu #291, der im Worktree von #286 landet, sieht niemand wieder.
        """
        if repo.name == f"wt-{ticket}":
            return repo
        haupt = bau_log.log_rueckfall()
        if haupt is not None and haupt.is_dir():
            return haupt
        return repo

    def vorfall_notieren(
        self, spec: str, repo: Path, fenster: str, ereignis: str, text: str, *, beispiel: str = ""
    ) -> None:
        """Stillstand als ``vorfall``-Zeile ins Bau-Log — die Lernschleife (#286).

        Ticket aus dem Fenster-Namen (``bau <N>``), sonst die Spec. ``capo …
        --katalog`` hängt die Zeile später an den Fehlerkatalog. Ein Fehler beim
        Schreiben darf den Aufpasser-Lauf nie kippen.
        """
        treffer = _FENSTER_TICKET.search(fenster)
        ticket = treffer.group(1) if treffer else spec
        # Aufseher-Fenster arbeiten in einem Worktree; dessen ``.to-spawn`` liest
        # niemand für ein fremdes Ticket. Darum in den Hauptbaum schreiben, wenn
        # das Fenster nicht der Worktree dieses Tickets ist (#286, :meth:`log_ordner`).
        notiere_vorfall(repo, ticket, ereignis, beispiel or f"#{ticket} {self.datum}")

    # -- Eingreifen --------------------------------------------------------------

    def bau_befehl(self, repo: Path, ticket: int, resume: str | None) -> str:
        """Startbefehl eines Bau-Fensters über :func:`capo.bau_startzeile` (#419).

        Nur ein gesetztes ``--bau-vorlage`` (Tests/Überschreibung) nutzt die eigene
        Vorlage; sonst baut ``capo.bau_startzeile`` dieselbe Startzeile wie Folge-Runde
        und Ablösung — samt ``.to-spawn/fenster_<N>.log``.
        """
        if self.e.bau_vorlage:
            venv = repo / ".venv" / "bin" / "python"
            py = str(venv) if venv.is_file() else "python3"
            return self.e.bau_vorlage.format(
                repo=shlex.quote(str(repo)),
                py=shlex.quote(py),
                n=ticket,
                resume=f" --resume {resume}" if resume else "",
            )
        schalter = ("--resume", resume) if resume else ()
        return capo.bau_startzeile(repo, ticket, "", *schalter)

    def wache_befehl(self, repo: Path, spec: str, resume: str | None) -> str:
        """Startbefehl des Aufseher-Fensters (R1), analog ``bau_befehl``.

        Schreibt stderr und Exit-Code (bei Fehler) zusätzlich nach :func:`wache_log`,
        damit ein sofort verschwindendes Aufseher-Fenster seinen Grund belegt (#271).
        """
        venv = repo / ".venv" / "bin" / "python"
        py = str(venv) if venv.is_file() else "python3"
        skill = config.skill_ordner()
        skill_q = shlex.quote(skill.as_posix())
        wache_q = shlex.quote((skill / "skripte" / "wache.py").as_posix())
        if self.e.wache_vorlage:
            vorlage = self.e.wache_vorlage
        elif (repo / "scripts" / "wache.py").is_file():
            vorlage = VORLAGE_WACHE_REPO
        else:
            vorlage = VORLAGE_WACHE_SKILL
        kern = vorlage.format(
            repo=shlex.quote(str(repo)),
            py=shlex.quote(py),
            s=spec,
            resume=f" --resume {resume}" if resume else "",
            skill=skill_q,
            wache=wache_q,
        )
        log_pfad = wache_log(repo, spec)
        log_q = shlex.quote(str(log_pfad))
        ordner_q = shlex.quote(str(log_pfad.parent))
        vorspann = sitzung_env.shell_vorspann(
            repo,
            skill_home=skill.as_posix(),
            warte_datei=spawn_exit.warte_datei(repo, spec),
        )
        tee = f"exec 3>&2 2> >(tee -a {log_q} >&3); tee_pid=$!"
        spur = (
            f'rc=$?; exec 2>&3 3>&-; [ "$rc" -eq 0 ] || {{ '
            f'timeout 3 tail -s 0.1 --pid="$tee_pid" -f /dev/null 2>/dev/null; '
            f'echo "$(date -Is) wache.py #{spec} Exit $rc" >> {log_q}; }}; exit "$rc"'
        )
        return f"{vorspann} mkdir -p {ordner_q}; {tee}; {{ {kern}; }}; {spur}"

    def resume_aus_datei(self, repo: Path, name: str) -> tuple[str, Path] | None:
        """Gesprächs-ID + cwd aus ``.to-spawn/sessions/<name>.json`` — nur wenn das
        Transkript ``~/.claude/projects/<cwd-slug>/<id>.jsonl`` noch da ist (R2)."""
        daten = sessions_datei.lesen(repo, name)
        if not daten:
            return None
        sid = str(daten["session_id"])
        cwd = Path(str(daten.get("cwd") or repo))
        if not cwd.is_dir():
            cwd = repo
        transkript = transkript_ordner(cwd, self.e.heim) / f"{sid}.jsonl"
        if not transkript.is_file():
            log.info(
                "%s: Gesprächs-ID %s… gemerkt, aber Transkript %s fehlt — frischer Start",
                name,
                sid[:8],
                transkript,
            )
            return None
        pid = session_laeuft_schon(sid, self.e.heim)
        if pid is not None:
            log.warning(
                "%s: Gespräch %s… läuft schon (PID %d) — kein zweites --resume",
                name,
                sid[:8],
                pid,
            )
            return None
        return sid, cwd

    def fenster_starten(self, sitzung: str, name: str, cwd: Path, befehl: str) -> None:
        """Regel 1: neues Fenster ``bau <N>`` bzw. ``wache <S>`` anlegen."""
        log.info("%s: Fenster „%s“ starten: %s", sitzung, name, befehl)
        if self.e.trocken:
            print(f"[trocken] {sitzung}: Fenster „{name}“ starten: {befehl}")
            return
        self.tmux(
            "new-window",
            "-d",
            "-t",
            f"={sitzung}",
            "-n",
            name,
            "-c",
            str(cwd),
            f"bash -lc {shlex.quote(befehl)}",
            start=True,
        )

    def _start_nachweisen(self, sitzung: str, name: str) -> bool:
        """Bis ``START_NACHWEIS_S`` prüfen, dass das Fenster bleibt und sein
        Pane-Prozess lebt — erst dann gilt der Start (R6).

        Bei ``False`` hält :attr:`_letzter_start_befund` die konkrete tmux-/Prozess-Ursache (#288).
        """
        self._letzter_start_befund = ""
        frist = time.monotonic() + START_NACHWEIS_S
        while True:
            lebt = False
            gefunden = False
            pane_tot = False
            pid_tot = 0
            tmux_fehler = ""
            try:
                for zeile in self.tmux(
                    "list-windows",
                    "-t",
                    f"={sitzung}",
                    "-F",
                    "#{window_name}\t#{pane_pid}\t#{pane_dead}",
                ).splitlines():
                    teile = zeile.split("\t")
                    if len(teile) == 3 and teile[0] == name:
                        gefunden = True
                        pid = int(teile[1] or 0)
                        if teile[2] == "1":
                            pane_tot = True
                        elif _prozess_lebt(pid):
                            lebt = True
                        else:
                            pid_tot = pid
            except (RuntimeError, ValueError) as fehler:
                tmux_fehler = str(fehler).strip()
                lebt = False
            if not lebt:
                if tmux_fehler:
                    self._letzter_start_befund = f"tmux-Fehler: {tmux_fehler[:160]}"
                elif pane_tot:
                    self._letzter_start_befund = "Pane-Prozess sofort beendet (pane_dead=1)"
                elif gefunden:
                    self._letzter_start_befund = f"Pane-Prozess {pid_tot} lebt nicht mehr"
                else:
                    self._letzter_start_befund = "Fenster nach dem Start nicht mehr in tmux"
                return False
            if time.monotonic() >= frist:
                return True
            time.sleep(0.5)

    def _prozesse_beenden(self, pane_pid: int) -> None:
        """Alten Prozessbaum des Panes beenden: SIGHUP, bis 5 s warten, SIGTERM, nach
        weiteren 5 s SIGKILL — sonst liefe dieselbe Session doppelt."""
        baum = prozess_baum(pane_pid) if pane_pid else []
        _pids_beenden(list(reversed(baum)), f"Pane {pane_pid}")

    def _fenster_schliessen(
        self,
        ziel: str,
        freigabe: Freigabe,
        pane_pid: int = 0,
        kette_beenden: bool = False,
        ticket: int | None = None,
    ) -> Schliessart:
        """Einzige Stelle, die ein Fenster schließt — nur mit gültiger Freigabe.

        Lebt Claude im Fensterbaum, endet nur Claude (``/exit``, nach
        ``exit_warten_s`` beenden) samt seinen Kindern; Fenster und bau.py-Kette
        leben weiter (#592). ``kette_beenden``: das ganze Fenster endet samt Kette —
        wenn deren Folge-Ticket schon woanders läuft (#598). Läuft nach ``/exit`` keine
        Kette (``ticket``) weiter, bleibt nur eine Shell: das Fenster geht gleich zu
        („fenster“), statt 15 min später ein zweites Mal. Ergebnis siehe ``Schliessart``.
        """
        if not isinstance(freigabe, Freigabe):
            raise TypeError("Fenster schließen braucht eine Freigabe")
        log.info(
            "Fenster %s schließen (%s, Sicherung %s)",
            ziel,
            freigabe.grund,
            freigabe.sicherung,
        )
        if self.e.trocken:
            print(f"[trocken] Fenster {ziel} schließen ({freigabe.grund})")
            return "fenster"
        baum = prozess_baum(pane_pid) if pane_pid else []
        claude = [pid for pid in baum if werkzeug.ist_session_pid(pid)]
        nach_exit = bool(claude) and not kette_beenden
        if nach_exit:
            ende = self._claude_exit(ziel, claude)
            if ende == "offen" or (ticket is not None and self._kette_lebt(pane_pid, ticket)):
                return ende
            log.info("%s: nach /exit keine Kette, nur Shell — Fenster geht zu", ziel)
        try:
            self.tmux("kill-window", "-t", ziel)
        except RuntimeError as fehler:
            # Nach /exit kann das Fenster mit seiner Shell schon selbst zu sein.
            if not nach_exit:
                raise
            log.info("%s: Fenster schon zu (%s)", ziel, fehler)
        self._prozesse_beenden(pane_pid)
        return "fenster"

    def _claude_exit(self, ziel: str, claude: list[int]) -> Schliessart:
        """Claude per ``/exit`` beenden, nach ``exit_warten_s`` samt Kindern hart.

        Ergebnis „exit“ (sauber), „zwang“ (beendet) oder „offen“ (lebt noch).
        """
        # Claude-Teilbaum vor /exit merken: endet Claude, hängen seine Kinder
        # (MCP-Server, pytest) nicht mehr im Fensterbaum. bau.py und Shell liegen
        # über Claude und gehören nie dazu.
        # ponytail: gemerkte PIDs könnten Sekunden später neu vergeben sein,
        # Upgrade: Startzeit aus /proc/<pid>/stat mit vergleichen.
        teilbaum = list(dict.fromkeys(p for c in claude for p in prozess_baum(c)))
        # Halber Text im Eingabefeld machte aus /exit eine Nachricht.
        self.tmux("send-keys", "-t", ziel, "C-u")
        self._eingeben(ziel, anleitung.EXIT)
        frist = time.monotonic() + self.e.exit_warten_s
        while any(_prozess_lebt(pid) for pid in claude) and time.monotonic() < frist:
            time.sleep(0.2)
        sauber = not any(_prozess_lebt(pid) for pid in claude)
        if not sauber:
            log.warning("%s: Claude %s nach /exit noch da — wird beendet", ziel, claude)
        reste = [pid for pid in teilbaum if _prozess_lebt(pid)]
        if reste:
            log.info("%s: %d Prozesse aus Claudes Baum übrig — werden beendet", ziel, len(reste))
            _pids_beenden(list(reversed(reste)), ziel)
        if any(_prozess_lebt(pid) for pid in claude):
            log.error("%s: Claude %s lebt nach /exit und Beenden noch", ziel, claude)
            return "offen"
        return "exit" if sauber else "zwang"

    def _kette_lebt(self, pane_pid: int, ticket: int | None) -> bool:
        """Läuft nach ``/exit`` noch etwas außer Shells im Fenster (bau.py, nächster
        Kettenbefehl) oder steht ein Folge-Ticket in der Shell-Zeile? Die Shell bekommt
        ``KETTE_RUHE_S``, den nächsten Befehl zu starten. Unlesbar ⇒ ja (nichts kappen).
        """
        if ticket is not None and kette_folge(pane_pid, ticket):
            return True
        frist = time.monotonic() + KETTE_RUHE_S
        while True:
            try:
                if not session_beendet(pane_pid):
                    return True
            except OSError as fehler:
                log.warning("Prozessbaum %s nicht lesbar: %s", pane_pid, fehler)
                return True
            if time.monotonic() >= frist:
                return False
            time.sleep(0.1)

    def _fenster_fortsetzen(
        self,
        ziel: str,
        freigabe: Freigabe,
        cwd: Path,
        befehl: str,
        pane_pid: int = 0,
    ) -> None:
        """Einzige Stelle, die ein Fenster neu bestückt — nur mit gültiger Freigabe.

        tmux-Respawn (``-k``) behält Fenster, Index und Name; die Sitzung stirbt nie,
        auch wenn es das letzte Fenster ist. Vorher werden die Prozesse des alten
        Baums beendet (``_prozesse_beenden``).
        """
        if not isinstance(freigabe, Freigabe) or not freigabe.session_id:
            raise TypeError("Fenster fortsetzen braucht eine Freigabe mit Gesprächs-ID")
        log.info("Fenster %s fortsetzen (%s): %s", ziel, freigabe.grund, befehl)
        if self.e.trocken:
            print(f"[trocken] Fenster {ziel} fortsetzen: {befehl}")
            return
        # Das Fenster muss den Tod seines Prozesses überleben, sonst ist es weg,
        # bevor der neue Befehl hineinkommt.
        self.tmux("set-option", "-w", "-t", ziel, "remain-on-exit", "on")
        try:
            self._prozesse_beenden(pane_pid)
            self.tmux(
                "respawn-window",
                "-k",
                "-t",
                ziel,
                "-c",
                str(cwd),
                f"bash -lc {shlex.quote(befehl)}",
                start=True,
            )
        finally:
            self.tmux("set-option", "-w", "-t", ziel, "-u", "remain-on-exit")

    def _kette_fortsetzen(self, f: Fenster, ticket: int, freigabe: Freigabe, cwd: Path, befehl: str) -> Fenster:
        """Fenster-Kette (#598): Ticket ``ticket`` hängt im Fenster „bau N“.

        Nur sein bau.py samt Kindern endet — die Kette dahinter läuft weiter —, und
        die Session geht im eigenen Fenster „bau <ticket>“ mit ``befehl`` weiter.
        Ergebnis: das neue Fenster. Jeder Fehlschlag ist ein ``RuntimeError``.
        """
        if not isinstance(freigabe, Freigabe) or not freigabe.session_id:
            raise TypeError("Fenster fortsetzen braucht eine Freigabe mit Gesprächs-ID")
        name = f"bau {ticket}"
        if any(x.name == name for x in self.fenster(f.sitzung, f.spec)):
            raise RuntimeError(f"Fenster „{name}“ gibt es schon")
        log.info(
            "Fenster %s: Kette, #%d fortsetzen (%s): %s",
            f.ziel,
            ticket,
            freigabe.grund,
            befehl,
        )
        if self.e.trocken:
            print(f"[trocken] Fenster {f.ziel}: bau.py {ticket} beenden, „{name}“ starten: {befehl}")
            return f
        lauf = bau_lauf_im_baum(f.pane_pid)
        if lauf is None or lauf[1] != ticket:
            raise RuntimeError(f"bau.py {ticket} läuft nicht mehr in „{f.name}“")
        if not _pids_beenden(list(reversed(prozess_baum(lauf[0]))), f.name):
            raise RuntimeError(f"bau.py {ticket} lebt nach dem Beenden noch")
        self.fenster_starten(f.sitzung, name, cwd, befehl)
        neu = next((x for x in self.fenster(f.sitzung, f.spec) if x.name == name), None)
        if neu is None:
            raise RuntimeError(f"Fenster „{name}“ nach dem Start nicht da")
        return neu

    def _fortsetzen_nachweisen(self, f: Fenster, sid: str) -> bool:
        """Bis ``NACHWEIS_S`` prüfen, ob im Fenster wieder eine Session mit
        ``sessions/<pid>.json`` und derselben ID läuft.

        Gilt erst, wenn die Session bei zwei Abfragen nacheinander (1 s Abstand)
        da ist: eine Session, die gleich nach dem Start wieder endet, schreibt ihre
        Datei kurz und löscht sie — ein einzelner Treffer hinge an der Zeitplanung (#149)."""
        frist = time.monotonic() + NACHWEIS_S
        zuletzt_da = False
        while True:
            try:
                pane_pid = int(self.tmux("display-message", "-p", "-t", f.ziel, "#{pane_pid}").strip() or 0)
            except (RuntimeError, ValueError):
                pane_pid = 0
            da = False
            if pane_pid:
                info = session_finden(pane_pid, f.window_id, f.pane_id, self.e.heim)
                da = bool(info and info.aus_json and info.session_id == sid)
            if da and zuletzt_da:
                return True
            zuletzt_da = da
            if time.monotonic() >= frist:
                return False
            time.sleep(1)

    def _eingeben(self, ziel: str, text: str) -> None:
        """Text wörtlich ins Fenster tippen, 1 s warten, dann Enter."""
        self.tmux("send-keys", "-t", ziel, "-l", text)
        time.sleep(1)
        self.tmux("send-keys", "-t", ziel, "Enter")

    def anstupsen(self, ziel: str, still_min: float) -> None:
        text = ANSTUPS_TEXT.format(min=int(still_min))
        if self.e.trocken:
            print(f"[trocken] {ziel}: anstupsen")
            return
        self._eingeben(ziel, text)
        time.sleep(1)

    def sichern_worktree(self, repo: Path, ticket: int, cwd: Path | None = None) -> str | None:
        """Sicherung des Ticket-Worktrees; gibt es keinen oder ist er sauber → None."""
        worktree = worktree_finden(repo, ticket, cwd)
        if worktree is None:
            log.info("#%s: kein Worktree im Repo %s — nichts zu sichern", ticket, repo)
            return None
        if self.e.trocken:
            print(f"[trocken] #{ticket}: Worktree {worktree} würde gesichert")
            return None
        return sichern(worktree, ticket, self.jetzt)

    def vor_eingriff(self, f: Fenster, hash_start: str, repo: Path, *, limit_ignorieren: bool = False) -> bool:
        """Direkt vor jedem Eingriff neu lesen: Deploy (serverweit), Gate dieses
        Fensters (Fensterbaum oder Ticket-Worktree in ``repo``), Text anders als zu
        Laufbeginn oder arbeitend ⇒ Kette abbrechen (False).

        ``limit_ignorieren=True`` beim Schließen fertiger Fenster (#624): eine alte
        Limit-Meldung im Pane einer stillen Session (``status=idle``) ist keine Arbeit.
        """
        if self.e.verzoegerung_s:
            time.sleep(self.e.verzoegerung_s)
        deploy = self.deploy_laeuft(self.muster)
        if deploy:
            log.info(
                "%s: Deploy/Gate inzwischen gestartet (%s) — kein Eingriff",
                f.name,
                deploy[0],
            )
            return False
        info = self.session(f)
        worktree: Path | None = None
        ticket = echtes_ticket(f)
        if ticket is not None:
            try:
                worktree = worktree_finden(repo, ticket, info.cwd if info else None)
            except RuntimeError as fehler:
                # Ohne Worktree-Wissen kein sicheres Gate-Urteil — lieber nichts anfassen.
                log.error("%s: Worktree nicht lesbar: %s — kein Eingriff", f.name, fehler)
                return False
        gate = self.gate_im_fenster(f, worktree)
        if gate:
            log.info("%s: Gate läuft in diesem Fenster (%s) — kein Eingriff", f.name, gate)
            return False
        text, hash_text = self._pane_lesen(f.ziel)
        status = info.status if info else None
        aktiv = arbeitet_echt(text, status) if limit_ignorieren else self._arbeitet(f, text, status, repo)
        h_neu = pane_hash(hash_text)
        if h_neu != hash_start or aktiv:
            log.info("%s: inzwischen aktiv — kein Eingriff", f.name)
            schluessel = f"{f.sitzung}/{f.name}"
            if schluessel in self.stand.fenster and h_neu != hash_start:
                self.eintrag(
                    schluessel,
                    hash_text,
                    seit_hinweis=self._seit_hinweis(f, repo, info, aktiv, ticket=ticket),
                )
            return False
        return True

    # -- Regeln je Fenster -------------------------------------------------------

    def _arbeitet(self, f: Fenster, text: str, status: str | None, repo: Path) -> bool:
        """:func:`arbeitet`, aber ein ``bau``-Fenster in Limit-Pause gilt als still,
        sobald die Limit-Probe aus #1449 das Limit offen meldet (#1453, #892).

        Steht unter der Limit-Zeile schon neue Ausgabe, ist sie alt: dann zählt nur
        der Rest des Textes (B3, #1463) — fertige Arbeit darunter heißt „still“.

        Aufseher-Fenster bleiben außen vor: der Aufseher wartet sein Limit selbst ab.
        """
        if not arbeitet(text, status):
            return False
        if f.ist_wache or status == "busy":
            return True
        if limit_pause(text):
            return not self._limit_offen(f, repo)
        return arbeitet(ohne_limit_zeilen(text))

    def _fenster_modell(self, f: Fenster) -> str | None:
        """Modell, mit dem das Fenster zuletzt geantwortet hat (Transkript, #1463, #892)."""
        info = self.session(f)
        if not info or not info.cwd:
            return None
        return fenster_modell(transkript_ordner(info.cwd, self.e.heim) / f"{info.session_id}.jsonl")

    def _limit_offen(self, f: Fenster, repo: Path) -> bool:
        """Limit-Probe mit dem Modell des Fensters (#1463, #892), gedrosselt.

        Modell aus dem Session-Transkript; fehlt es, ``modelle.ticket`` der Repo-Konfig.

        Höchstens eine echte Probe je :data:`LIMIT_PROBE_ABSTAND_S` je (Programm, Modell),
        über Läufe hinweg (Merker in ``stand.json``); dazwischen gilt das letzte Ergebnis.
        Im Trockenlauf keine Probe (kostet Abo): nur melden, Limit gilt als zu.
        """
        konfig = config.lade(repo)
        claude = str(konfig.get("runner") or "claude")
        modell = self._fenster_modell(f) or str((konfig.get("modelle") or {}).get("ticket") or "")
        if not modell:
            log.warning("Limit-Probe: kein Modell (Transkript/Konfig) — gilt als zu.")
            return False
        schluessel = f"{claude}|{modell}"
        letzte = self.stand.limit_proben.get(schluessel)
        if isinstance(letzte, dict) and _probe_gueltig(letzte, self.jetzt):
            return bool(letzte.get("offen"))
        if self.e.trocken:
            print(f"[trocken] Limit-Probe würde laufen ({claude}, {modell}) — gilt als zu")
            return False
        offen = limit_offen(claude, modell, repo)
        self.stand.limit_proben[schluessel] = {"zeit": self.jetzt, "offen": offen}
        return offen

    def _agy_still_seit(self, f: Fenster, repo: Path | None, ticket: int | None) -> float | None:
        """Letzte Aktivitätszeit (``mtime`` des Transkripts) einer laufenden ``agy``-Session (#288)."""
        if not f.pane_pid:
            return None
        hat_agy = False
        for pid in prozess_baum(f.pane_pid):
            argv = _argv(pid)
            if argv and any(prozessbaum.programm_name(a) == "agy" for a in argv[:2]):
                hat_agy = True
                break
        if not hat_agy:
            return None
        agy_ordner = werkzeug.agy_daten_ordner(self.e.heim)
        kandidaten: list[Path] = []
        if ticket is not None and repo is not None:
            try:
                wt = worktree_finden(repo, ticket)
            except RuntimeError:
                wt = None
            if wt is not None and wt.is_dir():
                kandidaten.append(wt)
        if f.pfad:
            pane_ordner = Path(f.pfad)
            if pane_ordner.is_dir() and pane_ordner not in kandidaten:
                kandidaten.append(pane_ordner)
        if repo is not None and repo.is_dir() and repo not in kandidaten:
            kandidaten.append(repo)
        for ordner in kandidaten:
            cid = werkzeug.agy_gespraech_fuer_ordner(ordner, agy_ordner=agy_ordner)
            if not cid:
                continue
            transkript = werkzeug.agy_transkript_pfad(cid, agy_ordner=agy_ordner)
            try:
                if transkript.is_file():
                    return transkript.stat().st_mtime
            except OSError:
                continue
        return None

    def _seit_hinweis(
        self,
        f: Fenster,
        repo: Path | None,
        info: SessionInfo | None,
        arbeitend: bool,
        ticket: int | None = None,
    ) -> float | None:
        """Nachweislicher Beginn der Stille je Werkzeug: Claude-JSON ``mtime`` oder ``agy``-Transkript (#288/#624)."""
        if arbeitend:
            return None
        if info is not None and info.aus_json and info.status == "idle":
            return info.mtime
        if info is None:
            return self._agy_still_seit(f, repo, ticket)
        return None

    def _gueltiger_seit_hinweis(self, seit_hinweis: float | None) -> float | None:
        if seit_hinweis is not None and 0 < seit_hinweis < self.jetzt and abs(time.time() - self.jetzt) < 60:
            return seit_hinweis
        return None

    def eintrag(self, schluessel: str, text: str, *, seit_hinweis: float | None = None) -> dict:
        """Hash/Zeit fortschreiben. Ändert sich der Text nach einem Eingriff
        (``stufe`` > 0), hat der Eingriff gewirkt: Stufe 0 — kein Zeit-Reset.

        ``seit_hinweis`` (``mtime`` der Session-JSON bei ``status: idle`` oder des
        ``agy``-Transkripts, #624/#288) gilt bei realer Uhr beim Neuanlegen, bei
        Format-/Versionswechsel des Hashes und bei unverändertem Eintrag ohne jüngeren
        Eingriff oder jüngere echte Pane-Änderung — so bleibt ``seit`` auf dem echten
        Stille-Beginn statt bei Takt, Hash-Migration oder flüchtigem UI-Rauschen
        zurückgesetzt zu werden.
        """
        h = pane_hash(text)
        hinweis = self._gueltiger_seit_hinweis(seit_hinweis)
        vorhanden = self.stand.fenster.get(schluessel)
        if vorhanden is None:
            anfang = hinweis if hinweis is not None else self.jetzt
            e = {"hash": h, "seit": anfang, "stufe": 0}
        else:
            e = vorhanden
        e.setdefault("stufe", 0)
        sperr_zeit = max(float(e.get("eingriff") or 0), float(e.get("pane_aenderung") or 0))
        alt_hash = e.get("hash")
        if alt_hash != h:
            alt_seit = float(e.get("seit") or 0)
            e["hash"] = h
            alt_version = _pane_hash_version(alt_hash)
            if alt_version > 0 and alt_version != _pane_hash_version(h):
                # Hash-Format-/Versionswechsel (#624): bestehenden Stand migrieren, ohne
                # ``seit``/``pane_aenderung`` als vermeintliche Aktivität zurückzusetzen.
                if "pane_aenderung" in e and eingabe_leer(text):
                    e.pop("pane_aenderung", None)
                    sperr_zeit = float(e.get("eingriff") or 0)
                if not alt_seit:
                    e["seit"] = hinweis if hinweis is not None else self.jetzt
                elif hinweis is not None and not e["stufe"] and hinweis > sperr_zeit and hinweis < alt_seit:
                    e["seit"] = hinweis
            else:
                if hinweis is not None and hinweis > alt_seit and hinweis > sperr_zeit:
                    e["seit"] = hinweis
                    e.pop("pane_aenderung", None)
                else:
                    e["seit"] = self.jetzt
                    e["pane_aenderung"] = self.jetzt
                if e["stufe"]:
                    log.info(
                        "%s: Text geändert nach Eingriff (Stufe %s) — Kette zurück auf 0",
                        schluessel,
                        e["stufe"],
                    )
                    e["stufe"] = 0
        elif (
            hinweis is not None
            and not e["stufe"]
            and hinweis > sperr_zeit
            and hinweis < float(e.get("seit", self.jetzt))
        ):
            e["seit"] = hinweis
        self.stand.fenster[schluessel] = e
        return e

    def hashes_fortschreiben(self, sitzung: str, fenster: list[Fenster], repo: Path | None = None) -> None:
        for f in fenster:
            text, hash_text = self._pane_lesen(f.ziel)
            info = self.session(f)
            arbeitend = arbeitet_echt(text, info.status if info else None)
            ticket = echtes_ticket(f)
            self.eintrag(
                f"{sitzung}/{f.name}",
                hash_text,
                seit_hinweis=self._seit_hinweis(f, repo, info, arbeitend, ticket=ticket),
            )

    def _braucht_mensch(
        self,
        e: dict,
        f: Fenster,
        repo: Path,
        schluessel: str,
        ereignis: str,
        text: str,
    ) -> None:
        """Kette beenden: Stufe 3 (nichts mehr anfassen) + einmalige Meldung (R7)."""
        e.update(stufe=3, eingriff=self.jetzt)
        self.melden(f.spec, repo, schluessel, ereignis, text)

    def _braucht_david(  # Übergangs-Alias (#325)
        self,
        e: dict,
        f: Fenster,
        repo: Path,
        schluessel: str,
        ereignis: str,
        text: str,
    ) -> None:
        """Übergangs-Alias für :meth:`_braucht_mensch` (#325)."""
        self._braucht_mensch(e, f, repo, schluessel, ereignis, text)

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(_METHODE_ALIAS.get(name, name), value)

    def __delattr__(self, name: str) -> None:
        super().__delattr__(_METHODE_ALIAS.get(name, name))

    def _stupser_zaehlen(self, e: dict) -> list[float]:
        """Anstupser der letzten 24 h (R5) — ältere fallen heraus."""
        stupser = [float(t) for t in e.get("stupser") or [] if self.jetzt - float(t) < STUPSER_FENSTER_S]
        e["stupser"] = stupser
        return stupser

    def folge_ab_handoff(self, f: Fenster, ticket: int, repo: Path, schluessel: str, hash_start: str) -> bool:
        """Eigener committeter Handoff + Session arbeitet nicht → Folge-Session (#542).

        Die Zeit seit dem Handoff-Commit zählt als Stille (nicht ``hang_min``); Regel in
        :mod:`to_spawn.eigener_handoff`. True = Fenster fertig geprüft (Neustart erledigt oder nichts zu tun, siehe
        :data:`eigener_handoff.EXIT_NICHTS`; nur bei echtem Start Meldung + Fensterstand weg).
        """
        treffer = eigener_handoff.pruefe(repo, ticket, self.jetzt)
        if treffer is None:
            return False
        eintrag = self.stand.fenster.get(schluessel)
        gemerkt = [treffer.datei, treffer.commit_zeit]
        if eintrag is not None and eintrag.get("handoff_fehler") == gemerkt:
            return False  # dieser Handoff ist schon einmal gescheitert — erst ein neuer startet wieder
        if not self.vor_eingriff(f, hash_start, repo):
            return False
        if self.e.trocken:
            print(f"[trocken] #{ticket}: Neustart ab eigenem Handoff {treffer.datei}")
            return True
        try:
            code = eigener_handoff.folge_starten(repo, int(f.spec), ticket, treffer, sperren=True)
        except Exception:
            log.exception("%s: Neustart ab Handoff %s abgestürzt", f.name, treffer.datei)
            code = -1
        if code == eigener_handoff.EXIT_NICHTS:
            # Sperre belegt / nicht mehr fällig: nichts gestartet — still, Fensterstand
            # bleibt, kein Merker; der nächste Takt prüft neu.
            return True
        if code != 0:
            log.error(
                "%s: Neustart ab Handoff %s gescheitert (Exit %s)",
                f.name,
                treffer.datei,
                code,
            )
            if eintrag is not None:
                eintrag["handoff_fehler"] = gemerkt
            self.melden(
                f.spec,
                repo,
                schluessel,
                "folge_handoff_fehler",
                f"„{f.name}“: Neustart ab Handoff {treffer.datei} gescheitert "
                f"(Exit {code}) — kein weiterer Versuch für diesen Handoff.",
            )
            return False
        self.stand.fenster.pop(schluessel, None)
        self.melden(
            f.spec,
            repo,
            schluessel,
            "folge_handoff",
            f"„{f.name}“ hat Handoff {treffer.datei} committet und ist seit "
            f"{treffer.still_min(self.jetzt)} min still — Folge-Session ab Handoff gestartet.",
        )
        return True

    def _fertiges_fenster(
        self,
        f: Fenster,
        repo: Path,
        offen: set[int],
        ticket: int | None,
        text: str,
        info: SessionInfo | None,
        e: dict,
        still_min: float,
        schluessel: str,
        arbeitend: bool,
    ) -> bool:
        """Die einzige Regel „wann darf ein fertiges Fenster zu“ (#416/#592).

        Wahr ⇒ das Fenster ist erledigt (geschlossen oder von einer offenen Frage gehalten),
        :meth:`fenster_pruefen` macht nichts weiter damit. Falsch ⇒ weiter in der Stufenkette.
        """
        hash_start = e["hash"]
        if ticket is not None and ticket not in offen and not arbeitend:
            # Volle Grenze + idle Session: nicht 15 min Karenz absitzen, sondern kurz
            # warten und Text vergleichen — der Platz wird gebraucht (#592).
            platznot = still_min < KARENZ_MIN and self._platznot(repo, info, f.sitzung)
            if still_min >= KARENZ_MIN or platznot:
                try:
                    daten = self.ticket_lesen(ticket, repo)
                except RuntimeError as fehler:
                    log.error("%s: Ticket-Zustand nicht lesbar: %s", f.name, fehler)
                    daten = {}  # gilt als offen: nichts schließen
                if ticket_zustand.fertig_gebaut(daten):
                    if not self._frage_haelt(f, ticket, repo, daten, e):
                        if self._platz_kandidaten is not None:
                            self._platz_kandidaten.append(
                                {
                                    "art": "bau",
                                    "fenster": f,
                                    "ticket": ticket,
                                    "repo": repo,
                                    "schluessel": schluessel,
                                    "hash_start": hash_start,
                                    "info": info,
                                    "gegenprobe": platznot,
                                    "grund": ticket_zustand.wortlaut(daten),
                                }
                            )
                            return True
                        self.ticket_zu_schliessen(
                            f,
                            ticket,
                            repo,
                            schluessel,
                            hash_start,
                            info,
                            platznot,
                            grund=ticket_zustand.wortlaut(daten),
                        )
                        return True
                    # Frage hält nur das Schließen: lebt Claude, bleibt das Fenster
                    # unberührt; beendete Session läuft in die Stufenkette (#416).
                    return info is not None or not session_beendet(f.pane_pid)
        e.pop("frage_gehalten", None)
        return False

    def _spec_abgeschlossen_grund(
        self,
        spec: str,
        repo: Path,
        offen_roh: dict[int, dict],
        fenster: list[Fenster],
    ) -> str | None:
        """Ist die Spec abgeschlossen (alle Tickets zu oder nur Abnahme/fertig gebaut offen)?

        Fail-closed (#624/#460): bei unlesbarer Konfig, arbeitendem Bau-Fenster derselben
        Sitzung oder offener Briefkasten-Frage (bei offenen Tickets) liefert die Methode ``None``.
        """
        if not offen_roh:
            grund = "alle Tickets zu"
        else:
            try:
                konfig = config.lade(repo)
            except (OSError, ValueError) as fehler:
                log.warning("%s: Konfig nicht lesbar: %s", repo, fehler)
                return None
            abnahme = manifest.checkpoint_label(konfig)
            if not all(
                ticket_zustand.fertig_gebaut(t) or abnahme in ticket_zustand.label_namen(t) for t in offen_roh.values()
            ):
                return None
            try:
                if briefkasten.offene(list(offen_roh), hauptbaum=repo):
                    return None
            except (OSError, ValueError, RuntimeError) as fehler:
                log.warning("spec %s: Briefkasten nicht lesbar (%s) — Fenster bleiben", spec, fehler)
                return None
            grund = "nur Abnahme offen"
        for fb in fenster:
            if echtes_ticket(fb) is None:
                continue
            try:
                fb_text = self.pane_text(fb.ziel)
            except RuntimeError:
                return None
            fb_info = self.session(fb)
            if arbeitet_echt(fb_text, fb_info.status if fb_info else None):
                return None
        return grund

    def _spec_fenster_schliessen(
        self,
        f: Fenster,
        repo: Path,
        schluessel: str,
        hash_start: str,
        grund: str,
    ) -> None:
        """Schließt ein stilles ``leitstand``-/``wache``-Fenster einer abgeschlossenen Spec (#624)."""
        if not self.vor_eingriff(f, hash_start, repo, limit_ignorieren=True):
            return
        ende = self._fenster_schliessen(
            f.fest,
            freigabe_spec_fertig(f.spec, grund),
            f.pane_pid,
            ticket=None,
        )
        if ende == "offen":
            log.error("%s: Claude lebt noch — nicht als geschlossen gemeldet", f.name)
            return
        if not self.e.trocken:
            self.stand.fenster.pop(schluessel, None)
        self.melden(
            f.spec,
            repo,
            schluessel,
            "geschlossen",
            f"„{f.name}“ geschlossen (Spec #{f.spec} {grund}).",
        )

    def _platz_fenster(self, f: Fenster, repo: Path, offen: set[int]) -> int:
        schluessel = f"{f.sitzung}/{f.name}"
        text, hash_text = self._pane_lesen(f.ziel)
        info = self.session(f)
        status = info.status if info else None
        ticket = echtes_ticket(f)
        arbeitend = arbeitet_echt(text, status)
        seit_hinweis = self._seit_hinweis(f, repo, info, arbeitend, ticket=ticket)
        e = self.eintrag(schluessel, hash_text, seit_hinweis=seit_hinweis)
        still_min = (self.jetzt - e["seit"]) / 60
        if self._fertiges_fenster(
            f,
            repo,
            offen,
            ticket,
            text,
            info,
            e,
            still_min,
            schluessel,
            arbeitend,
        ):
            return 1
        spec_grund = self._platz_spec_grund.get(f.sitzung)
        if (
            (f.ist_leitstand or f.ist_wache)
            and spec_grund is not None
            and not arbeitend
            and still_min >= self.e.fertige_spec_still_min
            and (
                (info is not None and info.aus_json and info.status == "idle")
                or (info is None and session_beendet(f.pane_pid))
            )
        ):
            if self._platz_kandidaten is not None:
                self._platz_kandidaten.append(
                    {
                        "art": "spec_fertig",
                        "fenster": f,
                        "repo": repo,
                        "schluessel": schluessel,
                        "hash_start": e["hash"],
                        "grund": spec_grund,
                        "gegenprobe": True,
                    }
                )
            else:
                if not self.e.trocken:
                    time.sleep(self.e.karenz_platznot_s)
                self._spec_fenster_schliessen(f, repo, schluessel, e["hash"], spec_grund)
        return 1

    def platz_schaffen(self) -> int:
        """Nur die Schließ-Regel über alle Fenster, sofort statt im 15-min-Cron (#624/#460).

        Wartende Starts rufen das bei voller Gesamtgrenze. Schließt sowohl fertige stille
        ``bau``-Fenster als auch stille ``leitstand``-/``wache``-Fenster abgeschlossener Specs
        mit einer gemeinsamen Gegenprobe. Wartet bei gesetztem ``sperre_warten_s`` auf eine
        laufende Sperre und prüft danach sofort neu statt endlos „Sperre belegt“ zu melden.
        Kein Hausmeister, keine Stufenkette, kein Nachstarten. Gibt die Zahl der geprüften
        Fenster zurück.
        """
        wartebeginn = time.time()
        sperre = self._sperren(warten=self.e.sperre_warten_s)
        if sperre is None:
            log.info("Platz schaffen: Aufpasser läuft schon (Sperre belegt)")
            return 0
        geprueft = 0
        stand_schreiben = False
        try:
            self.stand = self._stand_laden()
            self.jetzt = self.e.jetzt()
            if self.e.sperre_warten_s > 0 and self.stand.platz_zuletzt >= wartebeginn:
                log.info("Platz schaffen: gerade von anderem Lauf erledigt — prüfe Platz neu")
                return 0
            stand_schreiben = True
            gesehen: list[tuple[str, str, list[Fenster], Path]] = []
            for sitzung, spec in self.sitzungen():
                try:
                    fenster = self.fenster(sitzung, spec)
                except RuntimeError as fehler:
                    log.error("%s: Fenster nicht lesbar: %s", sitzung, fehler)
                    continue
                repo = self.spec_repo_ordner(spec, fenster, self.repo_ordner(fenster))
                if repo is not None:
                    gesehen.append((sitzung, spec, fenster, repo))
            deploy = self._deploy_wache({repo for _, _, _, repo in gesehen})
            if deploy:
                log.info("Platz schaffen: Deploy/Gate läuft (%s): nichts angefasst", "; ".join(deploy[:3]))
                return 0
            self._platz_kandidaten = []
            self._platz_spec_grund = {}
            for sitzung, spec, fenster, repo in gesehen:
                try:
                    offen_roh = self.offene_tickets(spec, repo)
                except (RuntimeError, ValueError) as fehler:
                    log.error("%s: GitHub nicht lesbar: %s", sitzung, fehler)
                    continue
                offen = self.zu_bauen(offen_roh, repo=repo)
                if any(isinstance(fb, Fenster) and (fb.ist_leitstand or fb.ist_wache) for fb in fenster):
                    self._platz_spec_grund[sitzung] = self._spec_abgeschlossen_grund(spec, repo, offen_roh, fenster)
                for f in fenster:
                    try:
                        geprueft += self._platz_fenster(f, repo, offen)
                    except RuntimeError as fehler:
                        log.error("%s/%s: Fenster übersprungen: %s", sitzung, f.name, fehler)
            kandidaten = list(self._platz_kandidaten)
            if kandidaten:
                braucht_gegenprobe = any(k.get("gegenprobe") for k in kandidaten)
                if braucht_gegenprobe and not self.e.trocken:
                    namen = ", ".join(k["fenster"].name for k in kandidaten)
                    log.info(
                        "Platz schaffen: %d fertige stille Fenster (%s), Grenze voll — %g s Gegenprobe statt Karenz",
                        len(kandidaten),
                        namen,
                        self.e.karenz_platznot_s,
                    )
                    time.sleep(self.e.karenz_platznot_s)
                for k in kandidaten:
                    f = k["fenster"]
                    try:
                        if k["art"] == "bau":
                            self.ticket_zu_schliessen(
                                f,
                                k["ticket"],
                                k["repo"],
                                k["schluessel"],
                                k["hash_start"],
                                k["info"],
                                False,
                                grund=k["grund"],
                            )
                        else:
                            self._spec_fenster_schliessen(
                                f,
                                k["repo"],
                                k["schluessel"],
                                k["hash_start"],
                                k["grund"],
                            )
                    except RuntimeError as fehler:
                        log.error("%s/%s: Schließen übersprungen: %s", f.sitzung, f.name, fehler)
            self.stand.platz_zuletzt = time.time()
        finally:
            self._platz_kandidaten = None
            self._platz_spec_grund = {}
            if stand_schreiben:
                self._stand_schreiben()
            if hasattr(sperre, "close"):
                sperre.close()
        return geprueft

    def fenster_pruefen(self, f: Fenster, repo: Path, offen: set[int]) -> None:
        schluessel = f"{f.sitzung}/{f.name}"
        text, hash_text = self._pane_lesen(f.ziel)
        info = self.session(f)
        status = info.status if info else None
        ticket = echtes_ticket(f)
        if ticket != f.ticket:
            log.info("%s: bau.py arbeitet an Ticket %s", f.name, ticket)
        echt_arbeitend = arbeitet_echt(text, status)
        seit_hinweis = self._seit_hinweis(f, repo, info, echt_arbeitend, ticket=ticket)
        e = self.eintrag(schluessel, hash_text, seit_hinweis=seit_hinweis)
        hash_start = e["hash"]
        still_min = (self.jetzt - e["seit"]) / 60

        if self._fertiges_fenster(f, repo, offen, ticket, text, info, e, still_min, schluessel, echt_arbeitend):
            return

        if rueckfrage(text):
            if still_min >= RUECKFRAGE_MIN:
                self.melden(
                    f.spec,
                    repo,
                    schluessel,
                    "rueckfrage",
                    f"„{f.name}“ wartet seit {int(still_min)} min auf eine Ja/Nein-Bestätigung — braucht den Menschen.",
                )
            return
        arbeitend = self._arbeitet(f, text, status, repo)
        if (
            ticket is not None
            and ticket in offen
            and not arbeitend
            and self.blocker_falsch_gewartet(f, ticket, repo, schluessel, e, hash_start, text)
        ):
            return
        if (
            ticket is not None
            and ticket in offen
            and not arbeitend
            and self.folge_ab_handoff(f, ticket, repo, schluessel, hash_start)
        ):
            return
        # Kurze Schwelle nur fürs erste Anstupsen; Fortsetzen (Kill) bleibt bei hang_min ≥ 61.
        kurz = int(e.get("stufe", 0)) == 0 and steht_umsonst(text, ticket, repo)
        schwelle = FRAGE_PROMPT_MIN if kurz else self.e.hang_min
        if arbeitend or still_min < schwelle:
            return
        if ticket is None and (not f.ist_wache or not offen):
            return

        stufe = int(e.get("stufe", 0))
        # Session beendet, nur noch die Shell im Pane (R2): nie anstupsen — der
        # Text liefe als Befehl. Kette gleich bei Stufe 1 beginnen, ID aus der Datei.
        if stufe < 2 and info is None and session_beendet(f.pane_pid):
            gemerkt = self.resume_aus_datei(repo, f.sessions_name)
            if gemerkt is None:
                self._braucht_mensch(
                    e,
                    f,
                    repo,
                    schluessel,
                    "session_beendet",
                    f"„{f.name}“: Session beendet, keine Gesprächs-ID — braucht den Menschen.",
                )
                return
            sid, cwd = gemerkt
            self.sichern_und_fortsetzen(
                f,
                ticket,
                repo,
                schluessel,
                e,
                hash_start,
                SessionInfo(sid, cwd, None, 0, aus_json=False),
            )
            return
        if stufe == 0:
            if len(self._stupser_zaehlen(e)) >= STUPSER_MAX:
                self._braucht_mensch(
                    e,
                    f,
                    repo,
                    schluessel,
                    "stupser_erschoepft",
                    f"„{f.name}“ wurde {STUPSER_MAX}× angestupst und wird immer wieder still — braucht den Menschen.",
                )
                return
            if not self.vor_eingriff(f, hash_start, repo):
                return
            self.anstupsen(f.ziel, still_min)
            if not self.e.trocken:
                _, nachher = self._pane_lesen(f.ziel)
                e.update(hash=pane_hash(nachher))
                e["stupser"].append(self.jetzt)
            e.update(stufe=1, eingriff=self.jetzt, seit=self.jetzt)
            self.melden(
                f.spec,
                repo,
                schluessel,
                "angestupst",
                f"„{f.name}“ stand {int(still_min)} min still, angestupst.",
            )
        elif stufe == 1:
            self.sichern_und_fortsetzen(f, ticket, repo, schluessel, e, hash_start, info)
        elif stufe == 2:
            self._braucht_mensch(
                e,
                f,
                repo,
                schluessel,
                "braucht_mensch",
                f"„{f.name}“ reagiert nach Anstupsen und Fortsetzen nicht — braucht den Menschen.",
            )

    def blocker_falsch_gewartet(
        self, f: Fenster, ticket: int, repo: Path, schluessel: str, e: dict, hash_start: str, text: str
    ) -> bool:
        """Session wartet laut eigener ``WARTET``-Zeile, ``gh.blocker_gruende`` sieht alles erledigt (#269).

        Dann Vorfall ``blocker_falsch_gewartet`` mit Ticketnummern und sofort ein Anstoß ins
        Fenster (zählt als Anstupser, Grenze R5). Wartet sie zu Recht oder ist die Lage
        unbekannt (Abfrage scheitert), passiert nichts. True = Fenster fertig geprüft.
        """
        if gemeldete_blocker(text) is None:  # ohne WARTET-Zeile kein origin-Lesen
            return False
        blocker = erledigt_gewartet(text, ticket, repo, gh.repo_aus_origin(repo))
        if blocker is None:
            return False
        log.info("%s: wartet auf %s, laut GitHub erledigt — Anstoß", f.name, blocker)
        if self.e.trocken:
            print(f"[trocken] {f.ziel}: Blocker {blocker} erledigt, anstoßen")
            return True
        notiere_blocker_falsch(repo, ticket, blocker, self.datum)
        if len(self._stupser_zaehlen(e)) >= STUPSER_MAX or not self.vor_eingriff(f, hash_start, repo):
            return False  # Kette unten entscheidet (braucht den Menschen / nächster Lauf)
        self._eingeben(f.ziel, blocker_frei_text(blocker, ticket))
        e["stupser"].append(self.jetzt)
        _, nachher = self._pane_lesen(f.ziel)
        e.update(hash=pane_hash(nachher), seit=self.jetzt)
        return True

    def wartet_umsonst(self, text: str, ticket: int | None, repo: Path) -> bool:
        """Siehe :func:`wartet_umsonst` (Modul-Funktion, auch vom Wecker gefragt)."""
        return wartet_umsonst(text, ticket, repo)

    def _frage_haelt(self, f: Fenster, ticket: int, repo: Path, daten: dict[str, Any], e: dict[str, Any]) -> bool:
        """Offene Frage des Tickets im Briefkasten hält das Fenster, auch bei Platznot (#416).

        Die Bau-Session wartet auf die Antwort und schließt danach selbst das Ticket — ohne
        sie bliebe es ohne Schließer. Ist das Issue schon zu, braucht es keinen mehr: eine
        alte, nie beantwortete Frage hält dann nicht. Briefkasten nicht lesbar ⇒ hält.
        Die Log-Zeile wird über ``e["frage_gehalten"]`` entprellt: einmal beim Eintritt,
        danach erst wieder bei Änderung (#440).
        """
        if ticket_zustand.endgueltig_zu(daten):
            e.pop("frage_gehalten", None)
            return False
        try:
            fragen = briefkasten.offene([ticket], hauptbaum=repo)
        except (OSError, ValueError, RuntimeError) as fehler:
            stand = f"fehler:{fehler}"
            if e.get("frage_gehalten") != stand:
                log.warning("%s: Briefkasten nicht lesbar (%s) — Fenster bleibt", f.name, fehler)
                e["frage_gehalten"] = stand
            return True
        if fragen:
            anzeige = ", ".join(q["id"] or q["frage"] for q in fragen)
            stand = f"offen:{anzeige}"
            if e.get("frage_gehalten") != stand:
                log.info(
                    "%s: offene Frage im Briefkasten (%s) — Fenster bleibt, Ticket braucht die Session",
                    f.name,
                    anzeige,
                )
                e["frage_gehalten"] = stand
            return True
        e.pop("frage_gehalten", None)
        return False

    def _platznot(self, repo: Path, info: SessionInfo | None, sitzung: str) -> bool:
        """Grenze voll (Speicher/Session-Zahl der tmux-Sitzung) und die Session meldet selbst ``idle``."""
        if info is None or not info.aus_json or info.status != "idle":
            return False
        try:
            konfig = config.lade(repo)
        except (OSError, ValueError) as fehler:
            log.warning("%s: Konfig nicht lesbar: %s", repo, fehler)
            return False
        return not speicher.platz_frei(konfig, sitzung=sitzung, tmux_socket=self.e.tmux_socket)[0]

    def ticket_zu_schliessen(
        self,
        f: Fenster,
        ticket: int,
        repo: Path,
        schluessel: str,
        hash_start: str,
        info: SessionInfo | None,
        platznot: bool = False,
        *,
        grund: str,
    ) -> None:
        """``grund``: :func:`ticket_zustand.wortlaut` für die Meldung (#279)."""
        if platznot and not self.e.trocken:
            log.info(
                "%s: Ticket zu, Grenze voll — %g s Gegenprobe statt Karenz",
                f.name,
                self.e.karenz_platznot_s,
            )
            time.sleep(self.e.karenz_platznot_s)
        try:
            sha = self.sichern_worktree(repo, ticket, info.cwd if info else None)
        except RuntimeError as fehler:
            log.error("%s: Sicherung vor dem Schließen fehlgeschlagen: %s", f.name, fehler)
            self.melden(
                f.spec,
                repo,
                schluessel,
                "sicherung_fehlgeschlagen",
                f"„{f.name}“: Ticket zu, aber Sicherung fehlgeschlagen — braucht den Menschen.",
            )
            return
        if not self.vor_eingriff(f, hash_start, repo, limit_ignorieren=True):
            return
        # Folgt in der Kette ein Ticket, das schon woanders läuft, endet das ganze
        # Fenster — nach /exit startete die Kette sonst ein zweites bau.py (#598).
        folge = kette_folge(f.pane_pid, ticket)
        doppelt = sorted(set(folge) & self.belegte_tickets(f.pane_pid)) if folge else []
        if doppelt:
            log.info(
                "%s: Folge-Ticket %s läuft schon woanders — ganzes Fenster endet",
                f.name,
                doppelt,
            )
        ende = self._fenster_schliessen(
            f.fest,
            freigabe_ticket_zu(ticket, sha),
            f.pane_pid,
            kette_beenden=bool(doppelt),
            ticket=ticket,
        )
        if ende == "offen":
            log.error("%s: Claude lebt noch — nicht als geschlossen gemeldet", f.name)
            return
        if not self.e.trocken:
            self.stand.fenster.pop(schluessel, None)
        zusatz = f" Änderungen auf sicherung/{ticket} gesichert ({sha[:7]})." if sha else ""
        if ende == "exit":
            zusatz += " Per /exit beendet, Kette läuft weiter."
        elif ende == "zwang":
            zusatz += " /exit ohne Wirkung, Claude beendet, Kette läuft weiter."
        self.melden(
            f.spec,
            repo,
            schluessel,
            "geschlossen",
            f"„{f.name}“ geschlossen ({grund}).{zusatz}",
        )

    def sichern_und_fortsetzen(
        self,
        f: Fenster,
        ticket: int | None,
        repo: Path,
        schluessel: str,
        e: dict,
        hash_start: str,
        info: SessionInfo | None,
    ) -> None:
        """Stufe 1 → 2: Worktree sichern (nur ``bau <N>``; der Aufseher hat keinen),
        Fenster mit ``--resume <id>`` fortsetzen, Nachweis (R1: auch ``wache <S>``)."""
        if info is None:
            self._braucht_mensch(
                e,
                f,
                repo,
                schluessel,
                "braucht_mensch",
                f"„{f.name}“ weiter still, aber keine Gesprächs-ID gefunden — braucht den Menschen.",
            )
            return
        sid = info.session_id
        sha: str | None = None
        if ticket is not None:
            try:
                sha = self.sichern_worktree(repo, ticket, info.cwd)
            except RuntimeError as fehler:
                log.error("%s: Sicherung fehlgeschlagen, Fenster bleibt: %s", f.name, fehler)
                self._braucht_mensch(
                    e,
                    f,
                    repo,
                    schluessel,
                    "sicherung_fehlgeschlagen",
                    f"„{f.name}“ weiter still — Sicherung fehlgeschlagen — braucht den Menschen.",
                )
                return
        if not self.vor_eingriff(f, hash_start, repo):
            return
        cwd = info.cwd if info.cwd and info.cwd.is_dir() else repo
        if ticket is not None:
            befehl = self.bau_befehl(repo, ticket, resume=sid)
        else:
            befehl = self.wache_befehl(repo, f.spec, resume=sid)
        # Kette: bau.py arbeitet an einem anderen Ticket als der Fenstername (#598).
        kette = ticket is not None and f.ticket is not None and ticket != f.ticket
        neu = f
        try:
            if kette:
                neu = self._kette_fortsetzen(f, ticket, freigabe_gesichert(sid, sha), cwd, befehl)
            else:
                self._fenster_fortsetzen(f.fest, freigabe_gesichert(sid, sha), cwd, befehl, f.pane_pid)
        except RuntimeError as fehler:
            # Prozess ist womöglich schon beendet, das Fenster aber leer: nicht
            # noch einmal versuchen, sondern den Menschen rufen (Stufe 3).
            log.error("Fenster %s fortsetzen fehlgeschlagen: %s", f.ziel, fehler)
            self._braucht_mensch(
                e,
                f,
                repo,
                schluessel,
                "fortsetzen_fehlgeschlagen",
                f"„{f.name}“: Fortsetzen fehlgeschlagen (tmux: {fehler}) — braucht den Menschen.",
            )
            return
        if ticket is None:
            gesichert = "nichts zu sichern (Aufseher ohne Worktree)"
        elif sha:
            gesichert = f"Änderungen auf sicherung/{ticket} gesichert ({sha[:7]})"
        else:
            gesichert = "nichts zu sichern"
        if kette and not self.e.trocken:
            # Die Session lebt jetzt in „bau <M>“; das alte Fenster fährt die Kette weiter.
            self.stand.fenster.pop(schluessel, None)
            schluessel = f"{neu.sitzung}/{neu.name}"
            self.stand.fenster[schluessel] = e
        nachgewiesen = self.e.trocken or self._fortsetzen_nachweisen(neu, sid)
        text_neu = ""
        if nachgewiesen and not self.e.trocken:
            # Unter Last kann die Session gleich nach dem Nachweis wieder sterben;
            # dann ist das Fenster weg und der Lauf darf nicht abbrechen (#149).
            try:
                _, text_neu = self._pane_lesen(neu.fest)
            except RuntimeError as fehler:
                log.error("Fenster %s nach dem Nachweis weg: %s", neu.ziel, fehler)
                nachgewiesen = False
        if not nachgewiesen:
            self._braucht_mensch(
                e,
                neu,
                repo,
                schluessel,
                "fortsetzen_fehlgeschlagen",
                f"„{neu.name}“: {gesichert}, aber Fortsetzen fehlgeschlagen "
                f"(Session {sid[:8]} nicht wieder da) — braucht den Menschen.",
            )
            return
        if not self.e.trocken:
            e.update(hash=pane_hash(text_neu))
        e.update(stufe=2, eingriff=self.jetzt, seit=self.jetzt)
        self.melden(
            f.spec,
            repo,
            schluessel,
            "fortgesetzt",
            f"„{neu.name}“ nach Anstupsen weiter still — {gesichert}, Session {sid[:8]}… fortgesetzt.",
        )

    # -- Ein Lauf ----------------------------------------------------------------

    def start_erlaubt(self, spec: str, repo: Path, ticket: int | None, *, sitzung: str = "") -> bool:
        """Regel 1: kein Start außerhalb der gespeicherten Teil-Start-Auswahl (#271), bei
        Stopp-Label oder wenn das Fenster in 6 h schon ``START_MAX``-mal gestartet wurde.
        ``ticket`` None = Aufseher-Fenster (prüft Auswahl/``--ohne-wache``/Rechner,
        Start-Gedächtnis ``wache-<S>``)."""
        name = f"bau {ticket}" if ticket is not None else f"wache {spec}"
        auswahl_stand = auswahl.lesen(repo, spec)
        if ticket is None:
            if not auswahl.wache_erlaubt(repo, spec, auswahl=auswahl_stand, gelesen=True):
                log.info(
                    "spec %s: Aufseher laut Auswahl hier nicht erlaubt (%s) — kein Start",
                    spec,
                    auswahl.beschreibung(auswahl_stand),
                )
                return False
        else:
            if not auswahl.ticket_erlaubt(repo, spec, ticket, auswahl=auswahl_stand, gelesen=True):
                log.info(
                    "#%s: außerhalb der Auswahl für Spec #%s (%s) — kein Start",
                    ticket,
                    spec,
                    auswahl.beschreibung(auswahl_stand),
                )
                return False
            try:
                daten = self.ticket_lesen(ticket, repo)
            except RuntimeError as fehler:
                log.error("#%s: Ticket nicht lesbar, kein Start: %s", ticket, fehler)
                return False
            if not ticket_zustand.bau_startbar(daten, repo=repo):
                grund = (
                    f"Abnahme-Ticket ({ticket_zustand.abnahme_label(repo)})"
                    if ticket_zustand.ist_abnahme(daten, repo=repo)
                    else ticket_zustand.wortlaut(daten)
                )
                log.info("#%s: %s — kein Start", ticket, grund)
                return False
            gesperrt = daten["labels"] & STOPP_LABELS
            if gesperrt:
                log.info("#%s: Label %s — kein Start", ticket, ", ".join(sorted(gesperrt)))
                return False
        starts = [
            t
            for t in self.stand.starts.get(self._start_schluessel(spec, ticket), [])
            if self.jetzt - t < START_FENSTER_S
        ]
        if len(starts) >= START_MAX:
            wer = f"#{ticket}" if ticket is not None else f"„{name}“"
            self.melden(
                spec,
                repo,
                f"{sitzung or tmux_aufruf.sitzung_alt(spec)}/{name}",
                "startet_nicht",
                f"{wer} startet nicht (Fenster verschwindet wieder) — braucht den Menschen.",
            )
            return False
        return True

    @staticmethod
    def _start_schluessel(spec: str, ticket: int | None) -> str:
        return str(ticket) if ticket is not None else f"wache-{spec}"

    def fehlendes_fenster(self, sitzung: str, spec: str, repo: Path, ticket: int | None) -> None:
        """Regel 1 für ein Fenster: liegt ``.to-spawn/sessions/<name>.json`` samt
        Transkript, geht es mit ``--resume`` weiter (R2), sonst frisch. Erst nach
        dem Start-Nachweis (R6) gilt es als gestartet."""
        name = f"bau {ticket}" if ticket is not None else f"wache {spec}"
        datei_name = str(ticket) if ticket else f"wache-{spec}"
        gemerkt_roh = sessions_datei.lesen(repo, datei_name)
        if gemerkt_roh and session_laeuft_schon(str(gemerkt_roh.get("session_id") or ""), self.e.heim):
            # Gespräch lebt woanders (andere tmux-Sitzung, von Hand fortgesetzt):
            # kein zweites Fenster, sonst zwei Sessions auf demselben Ticket.
            log.info("%s: „%s“ läuft außerhalb dieser Sitzung — kein Start", sitzung, name)
            return
        gemerkt = self.resume_aus_datei(repo, datei_name)
        sid, cwd = gemerkt if gemerkt else (None, repo)
        if ticket is not None:
            befehl = self.bau_befehl(repo, ticket, resume=sid)
        else:
            befehl = self.wache_befehl(repo, spec, resume=sid)
        # Speicher-Schutz (#257 Paket B): kein Start, wenn RAM knapp oder die Obergrenze
        # an Claude-Sessions dieser tmux-Sitzung erreicht ist (#409) — der nächste Tick prüft neu. Zwischen zwei
        # echten Starts in einem Tick liegt die Staffel-Pause (nicht im Trockenlauf).
        konfig = config.lade(repo)
        frei, grund = speicher.platz_frei(konfig, sitzung=sitzung, tmux_socket=self.e.tmux_socket)
        if not frei:
            log.warning("%s: Fenster „%s“ nicht gestartet — %s", sitzung, name, grund)
            if self.e.trocken:
                print(f"[trocken] {sitzung}: Fenster „{name}“ nicht gestartet — {grund}")
            return
        if self._starts_in_tick > 0 and not self.e.trocken:
            pause = speicher.staffel_s(konfig)
            log.info("%s: Staffel — %d s Pause vor Fenster „%s“", sitzung, pause, name)
            time.sleep(pause)
        log_pfad = _fenster_log_pfad(repo, spec, ticket)
        try:
            log_offset = log_pfad.stat().st_size if log_pfad.is_file() else 0
        except OSError:
            log_offset = 0
        try:
            self.fenster_starten(sitzung, name, cwd, befehl)
        except RuntimeError as fehler:
            log.error("%s: Fenster „%s“ nicht startbar: %s", sitzung, name, fehler)
            return
        if not self.e.trocken:
            self._starts_in_tick += 1
        wer = f"#{ticket}" if ticket is not None else "Aufseher-Fenster"
        was = "hatte keine Session" if ticket is not None else "fehlte"
        if not self.e.trocken:
            self.stand.starts.setdefault(self._start_schluessel(spec, ticket), []).append(self.jetzt)
        if not self.e.trocken and not self._start_nachweisen(sitzung, name):
            auszug = _start_log_auszug(log_pfad, log_offset)
            if not auszug:
                # Kurz warten, falls tee/spur bei schnellem Ende die Log-Datei noch anlegt oder flusht (#288).
                time.sleep(0.2)
                auszug = _start_log_auszug(log_pfad, log_offset)
            if auszug:
                grund_text = f" — Grund: {auszug} (Log: {log_pfad})"
            elif self._letzter_start_befund:
                hinweis = f" (Log: {log_pfad})" if log_pfad.is_file() else ""
                grund_text = f" — Grund: {self._letzter_start_befund}{hinweis}"
            else:
                grund_text = ""
            self.melden(
                spec,
                repo,
                f"{sitzung}/{name}",
                "start_fehlgeschlagen",
                f"{wer}: Start fehlgeschlagen (Fenster gleich wieder weg){grund_text}.",
            )
            return
        wie = f"mit Gespräch {sid[:8]}… fortgesetzt." if sid else "gestartet."
        self.melden(spec, repo, f"{sitzung}/{name}", "gestartet", f"{wer} {was}, {wie}")

    def offene_blocker_von(self, ticket: int, repo: Path, gh_repo: str) -> list[int]:
        """Noch nicht erledigte Blocker eines Tickets (#418, Regel: :func:`gh.offene_blocker`).

        Kein GitHub-Slug oder Abfrage fehlgeschlagen ⇒ ``[]`` (fail-open; ``bau.py``
        prüft seine Start-Schranke beim Anlauf selbst).
        """
        if not gh_repo:
            return []
        deps = gh.blocked_by(gh_repo, str(ticket), cwd=repo)
        if deps is None:
            log.warning("#%d: Blocker nicht lesbar — Start nicht gesperrt", ticket)
            return []
        return gh.offene_blocker(deps)

    def sitzung_pruefen(self, sitzung: str, spec: str, fenster: list[Fenster], repo: Path) -> None:
        try:
            offen = self.offene_tickets(spec, repo)
        except (RuntimeError, ValueError) as fehler:
            log.error("%s: GitHub nicht lesbar: %s", sitzung, fehler)
            return
        auswahl_stand = auswahl.lesen(repo, spec)
        offen_hier = {
            n: t for n, t in offen.items() if auswahl.ticket_erlaubt(repo, spec, n, auswahl=auswahl_stand, gelesen=True)
        }
        vorhanden = {f.name for f in fenster}
        log.info(
            "%s: %d Fenster (%s), offene Tickets %s%s",
            sitzung,
            len(fenster),
            ", ".join(sorted(vorhanden)),
            sorted(offen_hier) or "-",
            f" ({auswahl.beschreibung(auswahl_stand)})" if auswahl_stand is not None else "",
        )
        # Aufseher-Fenster fehlt (R2): zuerst nachstarten (#418), solange die Spec (hier)
        # offene Tickets hat und die Auswahl einen Aufseher auf diesem Rechner erlaubt (#271).
        # Gehört die Auswahl einem anderen Rechner oder gilt --ohne-wache, keinen zweiten
        # Start versuchen und das in einer Zeile melden (#288).
        if offen and f"wache {spec}" not in vorhanden:
            if not auswahl.wache_erlaubt(repo, spec, auswahl=auswahl_stand, gelesen=True):
                log.info(
                    "spec %s: Aufseher läuft laut Auswahl nicht auf diesem Rechner (%s) — kein zweiter Start",
                    spec,
                    auswahl.beschreibung(auswahl_stand),
                )
            elif offen_hier and self.start_erlaubt(spec, repo, None, sitzung=sitzung):
                self.fehlendes_fenster(sitzung, spec, repo, None)
        # Bau-Fenster nur für „noch nicht fertig gebaut“ (#279) innerhalb der Auswahl (#271);
        # der Aufseher oben bleibt, bis alle gewählten Tickets endgültig zu sind.
        bauen = self.zu_bauen(offen_hier, repo=repo)
        fehlend = [t for t in sorted(bauen) if f"bau {t}" not in vorhanden]
        # Läuft das Ticket schon in einer Fenster-Kette (oder folgt dort noch),
        # kein eigenes Fenster — sonst zwei Sessions auf einem Ticket (#598).
        belegt = self.belegte_tickets() if fehlend else set()
        gh_repo = gh.repo_aus_origin(repo) if fehlend else ""
        for ticket in fehlend:
            if ticket in belegt:
                log.info("%s: #%d läuft in einer Fenster-Kette — kein Start", sitzung, ticket)
                continue
            blocker = self.offene_blocker_von(ticket, repo, gh_repo)
            if blocker:
                log.info(
                    "%s: #%d hat offene Blocker (%s) — kein Start",
                    sitzung,
                    ticket,
                    ", ".join(f"#{b}" for b in blocker),
                )
                continue
            if not self.start_erlaubt(spec, repo, ticket, sitzung=sitzung):
                continue
            self.fehlendes_fenster(sitzung, spec, repo, ticket)
        for f in fenster:
            if f.ist_wache and not auswahl.wache_erlaubt(repo, spec, auswahl=auswahl_stand, gelesen=True):
                log.info("%s: Aufseher-Fenster „%s“ außerhalb der Auswahl — übersprungen", sitzung, f.name)
                continue
            if f.ticket is not None and not auswahl.ticket_erlaubt(
                repo, spec, f.ticket, auswahl=auswahl_stand, gelesen=True
            ):
                log.info("%s: Fenster „%s“ außerhalb der Auswahl — übersprungen", sitzung, f.name)
                continue
            try:
                self.fenster_pruefen(f, repo, bauen)
            except RuntimeError as fehler:
                log.error("%s: Fenster „%s“ übersprungen: %s", sitzung, f.name, fehler)

    def _sperren(self, warten: float = 0.0) -> object | None:
        """Lauf-Sperre ``<zustand>/lock`` (:func:`to_spawn.sperre.halte`).

        ``warten=0.0`` (Standard für Cron): nicht blockierend. Belegt ⇒ None. Sperr-Fehler
        (z. B. ENOLCK) gelten wie vor #94 als belegt: lieber einen Takt auslassen als
        ungesperrt doppelt eingreifen. Sperr-Datei nicht öffenbar ⇒
        :class:`to_spawn.sperre.SperreNichtOeffenbar` (#165).
        """
        if self.e.trocken:
            return object()
        stapel = ExitStack()
        try:
            gehalten = stapel.enter_context(_sperre.halte(self.e.zustand / "lock", warten=warten))
        except _sperre.SperreNichtOeffenbar:
            raise
        except OSError as fehler:
            log.warning("Lauf-Sperre %s nicht nutzbar (%s) — gilt als belegt", self.e.zustand / "lock", fehler)
            gehalten = False
        if not gehalten:
            stapel.close()
            return None
        return stapel

    def lauf(self) -> int:
        try:
            sperre = self._sperren()
        except _sperre.SperreNichtOeffenbar:
            log.exception("Lauf-Sperre nicht öffenbar — Aufpasser endet mit Fehler")
            return 1
        if sperre is None:
            log.info("Aufpasser läuft schon (Sperre belegt) — dieser Lauf endet")
            return 0
        code = 0
        try:
            self._lauf()
        except RuntimeError as fehler:
            # Werkzeug-Störung (z. B. Prozessliste der Deploy-Wache): Lauf endet ohne
            # Eingriff — lieber einmal nichts tun als blind eingreifen.
            log.error("Lauf abgebrochen, nichts angefasst: %s", fehler)
            code = 1
        finally:
            self._stand_schreiben()
            if hasattr(sperre, "close"):
                sperre.close()
        return code

    def _hausmeister_lauf(self) -> None:
        """Einmal je Takt alles Fertige aufräumen (#1369); die Regeln kennt nur der Hausmeister."""
        from to_spawn import hausmeister  # spät: hausmeister importiert aufpasser

        try:
            ergebnisse = hausmeister.aufraeumen_alles(loeschen=not self.e.trocken)
        except Exception:  # Aufräumen darf den Lauf nie aufhalten
            log.exception("Hausmeister-Lauf gescheitert")
            return
        weg = sum(1 for e in ergebnisse if e.art != "bleibt")
        fehler = sum(1 for e in ergebnisse if e.fehler)
        (log.warning if fehler else log.info)(
            "Hausmeister: %d gelöscht, %d bleibt, %d Fehler%s",
            weg,
            len(ergebnisse) - weg - fehler,
            fehler,
            " (trocken)" if self.e.trocken else "",
        )
        # #261: Bleib-Gründe gezählt auch im Aufpasser-Log (eine Format-Stelle im Hausmeister).
        for zeile in hausmeister.bleib_zeilen(ergebnisse):
            log.info("%s", zeile)

    def _wecker_lauf(self) -> None:
        """Sicherheitsnetz für den externen Wecker (#287): stößt fällige Weckaufträge an.

        Fehlt ``auftraege.json``, endet :func:`to_spawn.wecker.durchgang` sofort ohne
        Unterprozesse. Fehler halten den übrigen Aufpasser-Lauf nie auf.
        """
        from to_spawn import wecker  # spät: wecker importiert aufpasser

        try:
            ergebnisse = wecker.durchgang(
                self.e.wecker_zustand,
                tmux_socket=self.e.tmux_socket,
                trocken=self.e.trocken,
                jetzt=self.jetzt,
            )
        except NotImplementedError:
            return
        except Exception:  # Wecker darf den Aufpasser-Lauf nie aufhalten
            log.exception("Externer Wecker gescheitert")
            return
        if ergebnisse:
            geweckt = sum(1 for e in ergebnisse if e.ereignis == "geweckt")
            gescheitert = sum(1 for e in ergebnisse if e.ereignis == "gescheitert")
            (log.warning if gescheitert else log.info)(
                "Wecker: %d geweckt, %d gescheitert, %d gesamt%s",
                geweckt,
                gescheitert,
                len(ergebnisse),
                " (trocken)" if self.e.trocken else "",
            )

    def _deploy_wache(self, repos: Iterable[Path]) -> list[str]:
        """Setzt das Deploy-Muster für diese Repos und liefert die laufenden Deploy-/Gate-Prozesse."""
        self.muster = self.deploy_muster_fuer(repos)
        return self.deploy_laeuft(self.muster)

    def _lauf(self) -> None:
        self._starts_in_tick = 0
        gesehen: list[tuple[str, str, list[Fenster], Path]] = []
        for sitzung, spec in self.sitzungen():
            try:
                fenster = self.fenster(sitzung, spec)
            except RuntimeError as fehler:
                log.error("%s: Fenster nicht lesbar: %s", sitzung, fehler)
                continue
            repo = self.spec_repo_ordner(spec, fenster, self.repo_ordner(fenster))
            if repo is None:
                log.warning("%s: kein Repo-Ordner gefunden — übersprungen", sitzung)
                continue
            gesehen.append((sitzung, spec, fenster, repo))
        deploy = self._deploy_wache({repo for _, _, _, repo in gesehen})
        if deploy:
            log.info("Deploy/Gate läuft (%s): nichts angefasst", "; ".join(deploy[:3]))
            if self.e.trocken:
                print(f"[trocken] Deploy/Gate läuft ({deploy[0]}): nichts angefasst")
            for sitzung, _, fenster, repo in gesehen:
                try:
                    self.hashes_fortschreiben(sitzung, fenster, repo=repo)
                except RuntimeError as fehler:
                    log.error("%s: Fenster nicht lesbar: %s", sitzung, fehler)
            return
        # Entscheidung #262: Die Deploy-Wache läuft immer VOR dem Hausmeister (und dem
        # externen Wecker). Ein laufender Deploy/Prüflauf (oder eine gestörte Deploy-Wache,
        # fail-closed) sperrt damit auch das Aufräumen von Ordnern, Zweigen und
        # Docker-Resten; außerhalb des Aufpassers schützen den Hausmeister weiter seine
        # eigenen Sperren je Ordner (laufender Prozess im Ordner, offenes Ticket, 24 h).
        self._hausmeister_lauf()
        self._wecker_lauf()
        for sitzung, spec, fenster, repo in gesehen:
            try:
                self.sitzung_pruefen(sitzung, spec, fenster, repo)
            except RuntimeError as fehler:
                log.error("%s: Sitzung übersprungen: %s", sitzung, fehler)


# --- Kommandozeile -------------------------------------------------------------


def parser_fuellen(ap: argparse.ArgumentParser) -> argparse.ArgumentParser:
    ap.add_argument("--trocken", action="store_true", help="nur zeigen, nichts ausführen")
    ap.add_argument("--tmux-socket", help="tmux-Socket-Name (-L); Vorgabe: Standard-Server")
    ap.add_argument(
        "--zustand",
        type=Path,
        default=None,
        help="Zustandsordner (Vorgabe: ~/.local/state/to-spawn/aufpasser)",
    )
    ap.add_argument(
        "--hang-min",
        type=float,
        default=HANG_MIN,
        help=f"Minuten Stille ({HANG_MIN}; nie unter {HANG_MIN_UNTERGRENZE})",
    )
    ap.add_argument(
        "--bau-vorlage",
        help="Startbefehl mit {repo} {py} {n} {resume} (Vorgabe: capo.bau_startzeile)",
    )
    ap.add_argument(
        "--wache-vorlage",
        help="Startbefehl des Aufseher-Fensters mit {repo} {py} {s} {resume} (Vorgabe: scripts/wache.py bzw. Skill)",
    )
    ap.add_argument(
        "--deploy-muster",
        default=None,
        help="Regex für pgrep -af (Vorgabe: aus deploy.skripte der Repo-Konfig)",
    )
    ap.add_argument("--repo", type=Path, help="Repo-Ordner, falls kein Fenster ihn verrät")
    ap.add_argument(
        "--cron-einrichten",
        action="store_true",
        help="Cron-Zeile (alle 15 min) anlegen/ersetzen",
    )
    return ap


def _logging_einrichten(zustand: Path, trocken: bool) -> logging.Handler:
    """Log in ``<zustand>/aufpasser.log`` — im Trockenlauf auf stderr (kein Ordner)."""
    handler: logging.Handler
    if trocken:
        handler = logging.StreamHandler(sys.stderr)
    else:
        zustand.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(zustand / "aufpasser.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    return handler


def lauf_mit_einstellungen(e: Einstellungen) -> int:
    handler = _logging_einrichten(e.zustand, e.trocken)
    try:
        return Aufpasser(e).lauf()
    except Exception:  # Cron darf nie still sterben — jede Überraschung ins Log.
        log.exception("Aufpasser-Lauf abgebrochen")
        return 1
    finally:
        log.removeHandler(handler)
        handler.close()


def lauf_mit_args(args: argparse.Namespace) -> int:
    zustand = args.zustand or zustand_standard()
    if args.cron_einrichten:
        handler = _logging_einrichten(zustand, args.trocken)
        try:
            print(cron_einrichten(zustand, trocken=args.trocken))
            return 0
        except RuntimeError as fehler:
            log.error("Cron einrichten: %s", fehler)
            return 1
        finally:
            log.removeHandler(handler)
            handler.close()
    try:
        einstellungen = Einstellungen(
            zustand=zustand,
            tmux_socket=args.tmux_socket,
            hang_min=args.hang_min,
            bau_vorlage=args.bau_vorlage,
            wache_vorlage=args.wache_vorlage,
            deploy_muster=args.deploy_muster,
            repo=args.repo.resolve() if args.repo else None,
            trocken=args.trocken,
        )
    except ValueError as fehler:  # z. B. --hang-min unter 61 über to_spawn.py
        print(f"aufpasser: {fehler}", file=sys.stderr)
        return 2
    return lauf_mit_einstellungen(einstellungen)


def platz_schaffen(
    tmux_socket: str | None = None,
    *,
    sperre_warten_s: float = PLATZ_SPERRE_WARTEN_S,
) -> int:
    """Die eine Tür für wartende Starts (#624/#460): fertige, stille Fenster jetzt schließen.

    Gleiche Regeln wie der Cron-Lauf (:meth:`Aufpasser._fertiges_fenster`), plus stille
    ``leitstand``-/``wache``-Fenster abgeschlossener Specs. Wartet bis ``sperre_warten_s``
    auf eine laufende Sperre statt sofort mit „Sperre belegt“ abzubrechen. Fehler gehen nie
    nach außen: ``-1`` ⇒ der Start wartet wie bisher.
    """
    zustand = zustand_standard()
    try:
        handler = _logging_einrichten(zustand, False)
    except OSError:
        log.warning("Platz schaffen: Log nicht schreibbar")
        return -1
    try:
        return Aufpasser(
            Einstellungen(zustand=zustand, tmux_socket=tmux_socket, sperre_warten_s=sperre_warten_s)
        ).platz_schaffen()
    except Exception:  # Start darf daran nie scheitern
        log.exception("Platz schaffen gescheitert — Start wartet normal weiter")
        return -1
    finally:
        log.removeHandler(handler)
        handler.close()


def main(argv: list[str] | None = None) -> int:
    ap = parser_fuellen(argparse.ArgumentParser(description="Aufpasser für Bau-Sessions im tmux."))
    args = ap.parse_args(argv)
    if args.hang_min < HANG_MIN_UNTERGRENZE:
        ap.error(
            f"--hang-min {args.hang_min:g} liegt unter {HANG_MIN_UNTERGRENZE} "
            "(Wakeups wartender Sessions kommen alle ≤ 60 min)"
        )
    return lauf_mit_args(args)


if __name__ == "__main__":
    sys.exit(main())
