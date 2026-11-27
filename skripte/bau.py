"""bau — schlanke Claude-Code-Session für genau ein Ticket.

Aufruf: ``python scripts/bau.py <N> [--dry-run] [--model <m>] [--print-prompt] [--sofort] [--takt <s>] [--umzug <branch>@<sha>:<pfad>] [--probesitz] [--ohne-prompt] [--remote-control] [--auftrag <text>]``

Liest das Ticket-Manifest unter ``docs/agents/manifests/*.json`` (SSOT-Schema siehe
``docs/agents/kontext-manifest.md``), schaltet alle nicht benötigten Skills
per ``--settings skillOverrides`` ab, baut eine ``--mcp-config`` nur mit den
gelisteten MCPs und startet ``claude`` mit dem Loop-Prompt.

Staffel (Grill-Entscheidung 1, 18.09.2026): Erreicht eine Session die Smart-Zone-Grenze,
schreibt sie ihren Handoff und beendet sich über den Stop-Hook
``scripts/hooks/staffel_stop.py``. Dieser Launcher erkennt die Übergabe an der
Staffel-Datei im Session-Temp, zählt die Runde hoch und startet im selben Fenster
die Folge-Session mit dem Handoff-Inhalt als Startkontext — ohne Token-Kosten
für die Übergabe.

Blocker (#906): Vor dem Start wartet der Launcher auf native Blocker. Ohne ``--sofort``
muss jeder Blocker erledigt sein (zu oder ``staging:fertig``, #1527, Regel in
``gh.blocker_erledigt``) und seinen Commit auf dem Hauptzweig haben; mit ``--sofort``
nur erledigt sein. ``--umzug``, ``--resume`` und ``--probesitz`` warten nicht.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

# Skill-Wurzel in sys.path, damit ``to_spawn.config`` (Repo-Wurzel, Konfig) importierbar ist (#205).
_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import (
    anleitung,
    arbeitsstand,
    bau_log,
    befugnis,
    capo,
    config,
    context_mode,
    gh,
    konsole,
    manifest,
    nest,
    probesitz,
    prozessbaum,
    sessions_datei,
    sitzung_env,
    sitzung_filter,
    speicher,
    starter,
    startstelle,
    temp_ordner,
    terminal_maus,
    ticket_zustand,
    umzug,
    vertrauen,
    werkzeug,
)
from to_spawn.manifest import manifest_ordner
from to_spawn.waechter_lauf import transkript_ordner

# Windows-Konsole ist cp1252 — Umlaute/Pfeile im Prompt brauchen UTF-8.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("bau")
#: True, sobald main() einen echten Lauf beginnt (nicht --dry-run/--print-prompt), #1373.
_ECHTER_LAUF = False

#: Repo, in dem gearbeitet wird: ``TO_SPAWN_REPO`` (setzt die Weiterleitung im Repo), sonst
#: Git-Wurzel des aktuellen Ordners — nie der Ort dieses Skripts (liegt im Skill, #205).
REPO = Path(os.environ["TO_SPAWN_REPO"]).resolve() if os.environ.get("TO_SPAWN_REPO") else config.repo_wurzel()
MANIFEST_DIR = manifest_ordner(REPO)
_START_HOME = Path.home()
_START_CLAUDE_DIR = _START_HOME / ".claude"
HOME = _START_HOME
CLAUDE_DIR = _START_CLAUDE_DIR


def _aktuelles_home() -> Path:
    """Heimordner zur Laufzeit — folgt umgebogenem ``HOME``/``USERPROFILE`` (#232),
    respektiert aber ein ausdrücklich gepatchtes Modul-Attribut ``HOME``."""
    return HOME if HOME != _START_HOME else Path.home()


def _aktuelles_claude_dir(home: Path | None = None) -> Path:
    """Claude-Ordner zur Laufzeit (#232): Argument > gepatchtes Attribut > Laufzeit-HOME."""
    if home is not None:
        return home / ".claude"
    if CLAUDE_DIR != _START_CLAUDE_DIR:
        return CLAUDE_DIR
    if HOME != _START_HOME:
        return HOME / ".claude"
    return context_mode.standard_claude_dir()


# Pflicht-Skills und Builtins aus der gemeinsamen Filter-Quelle (to_spawn.sitzung_filter, #412).
PFLICHT_SKILLS = list(sitzung_filter.PFLICHT_SKILLS)
BUILTIN_SKILLS = list(sitzung_filter.BUILTIN_SKILLS)
CHROME_MCP = sitzung_filter.CHROME_MCP
#: Staffel-Hook des Skills (#257): Fremd-Repos brauchen keine eigene Kopie.
STAFFEL_HOOK_SKILL = Path(_SKILL) / "skripte" / "hooks" / "staffel_stop.py"


def staffel_hook_pfad(repo: Path = REPO) -> Path:
    """Repo-Kopie ``scripts/hooks/staffel_stop.py`` wenn vorhanden, sonst die des Skills (#257)."""
    eigene = repo / "scripts" / "hooks" / "staffel_stop.py"
    return eigene if eigene.is_file() else STAFFEL_HOOK_SKILL


#: Frage-Sperre (#321): Bau-Sessions fragen den Menschen nie (AskUserQuestion aus, Stop mit Frage → weiter).
#: Wortgleich mit dem Server-Template (setup_bau_server_push.sh): Claude Code legt identische Befehle
#: zusammen, der Hook läuft auf dem Bau-Server so nur einmal. ``~`` lösen Git-Bash und Linux auf.
FRAGE_SPERRE_BEFEHL = "node ~/.claude/hooks/smart-zone/staffel/frage-sperre.mjs"


#: CLI des Skills (Bau-Log-Hooks #204, Umzug-Anfrage #212) — dieselbe Skill-Wurzel wie oben in sys.path.
TO_SPAWN_CLI = Path(_SKILL) / "to_spawn.py"
STAFFEL_MAX_DEFAULT = 8
#: Werkzeug-Rechte der Probesitz-Wegwerf-Session (#214): nur git, Ordner anlegen, Dateien.
PROBESITZ_RECHTE = ("Bash(git *)", "Bash(mkdir *)", "Write", "Edit", "Read")
#: Auftrag einer Runde (#285) ist ein Aufseher-Kommentar, kein Dokument.
AUFTRAG_MAX_ZEICHEN = 4_000
# Handoffs sind Übersichten, keine Romane — mehr als das wäre ein Fehler in der Vorsession.
STAFFEL_HANDOFF_MAX_ZEICHEN = 40_000


def read_json(path: Path, default: dict | None = None) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise
    except json.JSONDecodeError as exc:
        log.warning("JSON unlesbar, übersprungen: %s (%s)", path, exc)
        return default if default is not None else {}


# --- Manifest ---------------------------------------------------------------


#: Die eine Vorlage des Start-Texts (#274 P1): nur im Skill, nie im Ziel-Repo. Repo-Eigenes
#: kommt über ``start_text.zusatz_datei`` als ``{REPO_ZUSATZ}`` dazu (:func:`repo_zusatz`).
VORLAGE = Path(_SKILL) / "repo-scripts" / "_default.json"


def load_default() -> dict:
    """Skill-Vorlage ``repo-scripts/_default.json`` — eine Repo-Kopie gilt nicht (#274 P1)."""
    path = VORLAGE
    if not path.is_file():
        log.error("Default-Manifest fehlt: %s", path)
        sys.exit(2)
    default = read_json(path, {})
    if "prompt_template" not in default:
        log.error("Default-Manifest ohne prompt_template (JSON kaputt?): %s", path)
        sys.exit(2)
    return default


#: Bau-Log-Ticket für :func:`start_warnung`; ``main`` setzt es nur außerhalb von ``--dry-run``
#: (Probelauf ohne Seiteneffekte, #205). ``None`` → Warnung nur auf stderr.
START_WARNUNG_TICKET: str | None = None


def start_warnung(text: str) -> None:
    """Warnung beim Start: stderr und Bau-Log-Laufdatei (Typ ``start_warnung``, #274 P1).

    Das Fenster übermalt stderr, sobald Claude startet — im Bau-Log bleibt die Zeile lesbar."""
    log.warning("%s", text)
    if START_WARNUNG_TICKET is None:
        return
    try:
        bau_log.schreibe(REPO, START_WARNUNG_TICKET, "start_warnung", text=text)
    except OSError as exc:
        log.warning("Bau-Log nicht beschreibbar (%s) — Warnung nur im Fenster.", exc)


def regel_kopie_warnen() -> None:
    """Liegt noch eine Repo-Kopie ``_default.json``, laut melden: sie wird nicht mehr gelesen (#274 P1)."""
    kopie = MANIFEST_DIR / "_default.json"
    if kopie.is_file():
        try:
            anzeige = kopie.relative_to(REPO)
        except ValueError:
            anzeige = kopie
        start_warnung(
            f"Regel-Kopie {anzeige} wird ignoriert; Repo-Teile nach start_text.zusatz_datei umziehen, "
            "dann Datei löschen."
        )


def find_manifest(ticket: str) -> tuple[dict, dict] | None:
    """Gibt (manifest, ticket_eintrag) zurück, wenn ein Manifest das Ticket führt."""
    for path in sorted(MANIFEST_DIR.glob("*.json")):
        if path.name == "_default.json":
            continue
        manifest = read_json(path, {})
        entry = (manifest.get("tickets") or {}).get(ticket)
        if entry is not None:
            log.info("Manifest: %s", path.relative_to(REPO))
            return manifest, entry
    return None


def ticket_from_gh(ticket: str) -> tuple[str, str | None]:
    """Titel + Spec-Nr. per gh aus dem Issue-Body ermitteln."""
    gh = shutil.which("gh")
    if gh is None:
        log.error("Kein Manifest für #%s und `gh` nicht gefunden — Abbruch.", ticket)
        sys.exit(2)
    proc = subprocess.run(
        [gh, "issue", "view", ticket, "--json", "title,body"],
        capture_output=True,
        **konsole.UNTERPROZESS_DATEN,
        check=False,
        **prozessbaum.fensterlos(),
    )
    if proc.returncode != 0:
        log.error("gh issue view %s fehlgeschlagen: %s", ticket, proc.stderr.strip())
        sys.exit(2)
    data = json.loads(proc.stdout)
    body = data.get("body") or ""
    match = re.search(r"Spec #(\d+)", body) or re.search(r"#(\d+)", body)
    return data.get("title") or f"Ticket {ticket}", match.group(1) if match else None


# --- Blocker-Wache (wartet im Skript, nicht in der Claude-Session) ----------


def repo_aus_origin(fallback: str = "", repo: Path | None = None) -> str:
    """``owner/name`` aus ``git remote get-url origin`` (GitHub, https oder ssh); sonst ``fallback``.

    Damit läuft dasselbe Skript in jedem Repo mit GitHub-Origin — nichts hart verdrahtet.
    Gelesen wird im Arbeits-Repo ``REPO`` (oder ``repo``), nicht im aktuellen Ordner (#257).
    """
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(repo or REPO),
            capture_output=True,
            **konsole.UNTERPROZESS_TEXT,
            check=False,
            **prozessbaum.fensterlos(),
        ).stdout.strip()
    except OSError:
        return fallback
    m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", url)
    return m.group(1) if m else fallback


KEIN_GITHUB_REPO = (
    "Kein GitHub-Repo erkannt (origin fehlt oder zeigt nicht auf github.com) — "
    "im Repo-Ordner starten oder TO_SPAWN_REPO setzen"
)


def repo_slug_oder_abbruch(repo: Path) -> str:
    """``owner/name`` aus dem origin von ``repo`` — ohne GitHub-Origin Abbruch mit Exit 2 (#257 F5).

    Kein stiller Rückfall auf ein festes Repo mehr: der Aufrufer sähe sonst Blocker,
    Labels und Prompt-Text eines fremden Repos.
    """
    slug = repo_aus_origin("", repo)
    if slug:
        return slug
    print(KEIN_GITHUB_REPO, file=sys.stderr)
    raise SystemExit(2)


def gh_repo_ermitteln(repo: Path) -> str:
    """Slug für GitHub-Aufrufe: GitHub-Origin von ``repo``; fehlt der, gilt die ausdrückliche
    Vorgabe ``TO_SPAWN_GH_REPO`` (z. B. Spiegel-Repo mit lokalem origin, Tests); sonst
    :func:`repo_slug_oder_abbruch` → Exit 2. Nie ein fest verdrahtetes Repo."""
    return repo_aus_origin("", repo) or os.environ.get("TO_SPAWN_GH_REPO", "").strip() or repo_slug_oder_abbruch(repo)


#: Wird in ``main()`` über :func:`gh_repo_ermitteln` verbindlich gesetzt; beim Import nur
#: der beste Versuch (leer ohne GitHub-Origin), damit Helfer importierbar bleiben.
GH_REPO = repo_aus_origin("") or os.environ.get("TO_SPAWN_GH_REPO", "").strip()


def _gh_json(gh: str, *args: str) -> object | None:
    proc = subprocess.run(
        [gh, *args], capture_output=True, **konsole.UNTERPROZESS_DATEN, check=False, **prozessbaum.fensterlos()
    )
    if proc.returncode != 0:
        log.warning("gh %s: %s", " ".join(args[:3]), proc.stderr.strip()[:200])
        return None
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError:
        return None


#: Sekunden zwischen zwei Blocker-Prüfungen (#432): die eine Stelle; ``--takt`` überschreibt, Untergrenze 5 s.
TAKT_STANDARD_S = 30
TAKT_MIN_S = 5


def blocker_offen(ticket: str, *, nur_offene: bool = False) -> list[str]:
    """Gründe, warum #ticket noch nicht starten darf (leer = frei).

    Regel nur in ``gh.blocker_gruende`` (#267; dieselbe fragt die Session per
    ``to_spawn.py blocker <N>``): jeder Blocker erledigt (zu oder ``staging:fertig``,
    ``gh.blocker_erledigt``, #1527) und sein Commit ``(#<Blocker>)`` auf
    ``origin/<Hauptzweig>``. Bei gh-/git-Fehlern gilt „offen" (fail-closed), damit kein
    Loop vorzeitig losläuft.

    ``nur_offene`` (``--sofort``, #906): nur nicht erledigte Blocker zählen, die
    Commit-Prüfung macht die Session selbst (über denselben Befehl). gh fehlt oder Abfrage scheitert →
    Warnung und frei (vor #906 prüfte ``--sofort`` gar nicht).
    """
    gh_bin = shutil.which("gh")
    if gh_bin is None:
        if nur_offene:
            log.warning("Ticket #%s: gh fehlt — Blocker ungeprüft, Session startet (--sofort).", ticket)
            return []
        return ["gh fehlt"]
    deps = _gh_json(gh_bin, "api", f"repos/{GH_REPO}/issues/{ticket}/dependencies/blocked_by")
    if not isinstance(deps, list):
        if nur_offene:
            log.warning("Ticket #%s: Blocker-Abfrage fehlgeschlagen — Session startet (--sofort).", ticket)
            return []
        return ["Blocker-Abfrage fehlgeschlagen"]
    return gh.blocker_gruende(REPO, deps, mit_commit=not nur_offene)  # die eine Stelle (#267)


#: Mindestabstand (s) zwischen zwei Warte-Zeilen bei gleichen Gründen (#432, Herzschlag).
WARTE_HERZSCHLAG_S = 600
#: Obergrenze (s) des Abstands nach wiederholt fehlgeschlagener Abfrage (#432, Rate-Limit).
TAKT_FEHLER_MAX_S = 300
#: Gründe aus :func:`blocker_offen`, wenn die Abfrage selbst scheitert (Ticket bleibt blockiert).
ABFRAGE_FEHLER = ("Blocker-Abfrage fehlgeschlagen", "gh fehlt")
#: Die eine Warte-Zeile; ``to_spawn/aufpasser.py`` ``BLOCKER_WARTEN`` erkennt sie, Test prüft beides (#432).
WARTE_ZEILE = "Ticket #%s wartet (%s) — nächste Prüfung in %d s, Meldung höchstens alle %d min [%s]"


def auf_blocker_warten(
    ticket: str, takt: int, *, nur_offene: bool = False, schlafen=time.sleep, uhr=time.monotonic
) -> None:
    """Pollt alle ``takt`` Sekunden, bis #ticket frei ist. Kostet keine Token.

    Loggt nur bei geänderten Gründen oder nach ``WARTE_HERZSCHLAG_S`` (Aufpasser sieht die Zeile).
    Scheitert die Abfrage, wächst der Abstand (takt, 2·takt, … höchstens ``TAKT_FEHLER_MAX_S``).
    """
    runde = 0
    fehler = 0
    letzte: list[str] | None = None
    letzte_zeit = 0.0
    while True:
        gruende = blocker_offen(ticket, nur_offene=nur_offene)
        if not gruende:
            if runde:
                log.info("Ticket #%s ist frei — Session startet.", ticket)
            return
        runde += 1
        fehler = fehler + 1 if tuple(gruende) in {(g,) for g in ABFRAGE_FEHLER} else 0
        abstand = min(max(takt, TAKT_FEHLER_MAX_S), takt * 2 ** (fehler - 1)) if fehler else takt
        if gruende != letzte or uhr() - letzte_zeit >= WARTE_HERZSCHLAG_S:
            log.info(
                WARTE_ZEILE,
                ticket,
                "; ".join(gruende),
                abstand,
                WARTE_HERZSCHLAG_S // 60,
                datetime.now().strftime("%H:%M"),
            )
            letzte, letzte_zeit = gruende, uhr()
        schlafen(abstand)


def warte_modus(args: argparse.Namespace) -> str | None:
    """Blocker-Warten vor dem Start (#906): ``"voll"``, ``"nur_offen"`` (``--sofort``) oder ``None``.

    ``--umzug``, ``--resume``, ``--probesitz`` und Abschluss-Wiederanlauf (#282) setzen
    laufende/fertige Arbeit fort → kein Warten.
    """
    if args.umzug or args.resume or args.probesitz or getattr(args, "abschluss", False):
        return None
    return "nur_offen" if args.sofort else "voll"


def blocker_schranke(ticket: str, args: argparse.Namespace) -> None:
    """Die eine Stelle in main (#906): je nach ``warte_modus`` auf Blocker warten oder nicht."""
    modus = warte_modus(args)
    if modus is not None:
        auf_blocker_warten(ticket, max(TAKT_MIN_S, args.takt), nur_offene=modus == "nur_offen")


def auf_speicher_warten(
    konfig: dict,
    *,
    wer: str = "bau",
    pruefen=speicher.platz_frei_hier,
    schlafen=time.sleep,
    takt_s: int = 60,
) -> int:
    """Warten, bis ``speicher.platz_frei_hier`` (eigene tmux-Sitzung, #409) frei meldet; Rückgabe = Zahl der Wartezyklen (#257 F3)."""
    return speicher.auf_platz_warten(konfig, wer=wer, pruefen=pruefen, schlafen=schlafen, takt_s=takt_s)


# --- Skill-Katalog ----------------------------------------------------------


def known_skill_names(*, repo: Path | None = None, claude_dir: Path | None = None) -> set[str]:
    ziel_repo = repo if repo is not None else REPO
    ziel_claude = claude_dir if claude_dir is not None else _aktuelles_claude_dir()
    # Pflicht-Skills werden ohnehin erzwungen, auch ohne Datei auf dem Rechner (#478).
    return sitzung_filter.bekannte_skills(
        repo=ziel_repo,
        claude_dir=ziel_claude,
        basis=[*BUILTIN_SKILLS, *PFLICHT_SKILLS],
        logger=log,
    )


def skill_auswahl(
    default: Mapping[str, Any],
    entry: Mapping[str, Any],
    konfig: Mapping[str, Any],
    *,
    known: set[str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Whitelist und ``skillOverrides`` für die Bau-Session (#413).

    Aktiv (``on``) sind ``core_skills`` aus der Vorlage, die Pflicht-Skills, die
    Repo-Skills aus ``.to-spawn/config.json`` (``skills.pflicht``) und die Ticket-Skills
    des Manifests. Gegen ``known`` geprüft und bei Fehlen gewarnt werden nur die vom Repo
    bzw. Ticket angeforderten Skills (``skills.pflicht`` + ``entry["skills"]``) — fremde
    Vorgaben aus der Skill-Vorlage erzeugen in einem fremden Repo keine Warnung.
    """
    repo_erwartet = config.repo_skills(konfig)
    ticket_skills = [s.strip() for s in (entry.get("skills") or []) if isinstance(s, str) and s.strip()]
    geprueft = list(dict.fromkeys(repo_erwartet + ticket_skills))
    whitelist = list(
        dict.fromkeys(list(default.get("core_skills", [])) + ticket_skills + repo_erwartet + list(PFLICHT_SKILLS))
    )
    bekannt = known if known is not None else known_skill_names()
    return sitzung_filter.skill_overrides(whitelist, bekannt, geprueft=geprueft, logger=log)


# --- MCP-Katalog ------------------------------------------------------------


def mcp_catalog(
    *, repo: Path | None = None, home: Path | None = None, claude_dir: Path | None = None
) -> dict[str, dict]:
    """Reihenfolge: ~/.claude.json (global + dieses Projekt) → Repo .mcp.json → toolbox/mcp-archive.json."""
    ziel_repo = repo if repo is not None else REPO
    ziel_home = home if home is not None else _aktuelles_home()
    ziel_claude = claude_dir if claude_dir is not None else _aktuelles_claude_dir(home)
    return sitzung_filter.mcp_katalog(repo=ziel_repo, home=ziel_home, claude_dir=ziel_claude, logger=log)


# --- Prompt -----------------------------------------------------------------


def build_kontext(entry: dict) -> str:
    lines = [f"- {item}" for item in (entry.get("files") or []) + (entry.get("docs") or [])]
    if not lines:
        return "(kein Kontext-Paket hinterlegt — Explore-Subagent model:sonnet nutzen, Rückgabe Pfad:Zeilen)"
    return "\n".join(lines)


def worktree_pfad(ticket: str) -> str:
    """Wohin der Worktree dieses Tickets gehört — Regel lebt in ``to_spawn.config`` (#204)."""
    return config.worktree_pfad(ticket, REPO)


def repo_zusatz(konfig: dict, *, repo: Path | None = None) -> str:
    """Text der Repo-Zusatz-Datei (``start_text.zusatz_datei``, relativ zum Repo), sonst ``""``.

    Die eine Stelle für alle Fehlerfälle (#274 P1): nicht konfiguriert → still ``""``;
    Pfad führt aus dem Repo heraus (``../``, absolut, Symlink nach außen) oder Datei fehlt/
    ist kein UTF-8 → :func:`start_warnung`, Start ohne Zusatz — nie ein Abbruch."""
    rel = str((konfig.get("start_text") or {}).get("zusatz_datei") or "").strip()
    if not rel:
        return ""
    wurzel = (repo if repo is not None else REPO).resolve()
    pfad = (wurzel / rel).resolve()
    if not pfad.is_relative_to(wurzel):
        start_warnung(f"Repo-Zusatz {rel} liegt außerhalb des Repos — Start ohne Zusatz.")
        return ""
    try:
        return pfad.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        start_warnung(f"Repo-Zusatz {rel} nicht lesbar ({exc}) — Start ohne Zusatz.")
        return ""


#: Platzhalter, die ein Repo-Zusatz nutzen darf (#274 P1). Alles andere (``{KONTEXT}``,
#: JSON-Klammern …) bleibt im Zusatz wörtlich stehen — nichts wird zweimal eingesetzt.
ZUSATZ_PLATZHALTER = ("{N}", "{S}", "{REPO}", "{WT}", "{HAUPTZWEIG}")
#: Platzhalter-Stil der Start-Vorlage (#501): nur ``{GROSS_BUCHSTABEN}`` — JSON/Code-Klammern nie.
_PLATZHALTER = re.compile(r"\{[A-Z][A-Z0-9_]*\}")


def build_prompt(
    template: str,
    ticket: str,
    spec: str,
    title: str,
    kontext: str,
    konfig: dict | None = None,
    *,
    repo: Path | None = None,
    gh_repo: str | None = None,
) -> str:
    """Platzhalter der Vorlage füllen — auch die repo-neutralen (#257):
    ``{REPO}`` = owner/name, ``{HAUPTZWEIG}``, ``{CHECKPOINT_LABEL}`` aus der Konfig,
    ``{SESSION_KOPF}`` = Marke jedes Session-Kommentars aus ``capo.SESSION_KOPF`` (#402),
    ``{MINDSET}``/``{ABLOESE_SOP}`` = feste Texte aus ``anleitung`` (#500),
    ``{BEFUGNIS}`` = Befugnis-Abschnitt aus ``befugnis.start_text`` (#278),
    ``{ENTSCHEIDUNGS_LINIE}`` = ``anleitung.entscheidungs_linie`` mit dem Hauptzweig (#376),
    ``{ANNAHME_GRENZE}`` = ``capo.annahme_grenze`` (was die Checkpoint-Annahme deckt, #490),
    ``{REPO_ZUSATZ}`` = Inhalt von ``start_text.zusatz_datei`` (#274 P1) — zuletzt eingesetzt, darin
    nur :data:`ZUSATZ_PLATZHALTER` ersetzt; leer → Platzhalter samt Überschrift darüber entfällt.

    Fehlt ``{ANNAHME_GRENZE}`` in der Vorlage, meldet :func:`start_warnung` das (#501); der Text
    bleibt wie die Vorlage ihn will (Wegwerf-Vorlagen der Weg-Tests, #212).
    ``ValueError``, wenn nach dem Füllen ein ``{GROSS}``-Platzhalter übrig bleibt (unbekannt oder
    aus einem Wert ungefüllt), statt ihn roh in den Prompt zu geben (#501). Klammern aus Titel,
    Kontext und Repo-Zusatz zählen nicht — das ist fremder Text, der wörtlich bleibt."""
    ziel_repo = repo if repo is not None else REPO
    ziel_gh = gh_repo if gh_repo is not None else GH_REPO
    konfig = konfig if konfig is not None else config.lade(ziel_repo)
    label = str((konfig.get("regularien") or {}).get("checkpoint_label") or "checkpoint:human")
    haupt = gh.hauptzweig(ziel_repo)
    werte = {
        "{WT}": config.worktree_pfad(ticket, ziel_repo) if repo is not None else worktree_pfad(ticket),
        "{N}": ticket,
        "{S}": spec,
        "{TITLE}": title,
        "{KONTEXT}": kontext,
        "{REPO}": ziel_gh,
        "{HAUPTZWEIG}": haupt,
        "{CHECKPOINT_LABEL}": label,
        "{SESSION_KOPF}": capo.SESSION_KOPF,
        "{MINDSET}": anleitung.MINDSET,
        "{ABLOESE_SOP}": anleitung.ABLOESE_SOP,
        "{BEFUGNIS}": befugnis.start_text(ticket),
        "{ENTSCHEIDUNGS_LINIE}": anleitung.entscheidungs_linie(haupt),
        "{ANNAHME_GRENZE}": capo.annahme_grenze(ticket),
    }
    if "{ANNAHME_GRENZE}" not in template:
        start_warnung("Start-Vorlage ohne {ANNAHME_GRENZE}: Annahme-Regel (#490) fehlt im Start-Text.")
    zusatz = repo_zusatz(konfig, repo=ziel_repo)
    if not zusatz:
        template = re.sub(r"(?:#[^\n]*\n)?\{REPO_ZUSATZ\}\n*", "", template)
    for platzhalter, wert in werte.items():
        template = template.replace(platzhalter, wert)
    fremd = set(_PLATZHALTER.findall(f"{title}\n{kontext}"))
    rest = sorted({p for p in _PLATZHALTER.findall(template) if p != "{REPO_ZUSATZ}"} - fremd)
    if rest:
        raise ValueError(f"Start-Vorlage: Platzhalter {', '.join(rest)} nach dem Füllen übrig (unbekannt/ungefüllt).")
    for platzhalter in ZUSATZ_PLATZHALTER:
        zusatz = zusatz.replace(platzhalter, werte[platzhalter])
    return template.replace("{REPO_ZUSATZ}", zusatz) if zusatz else template


# --- Staffel ----------------------------------------------------------------


def handoff_dirs(ticket: str) -> list[str]:
    """Nur der Ticket-Worktree — und dort beide Handoff-Orte.

    Der Hauptbaum bleibt bewusst außen vor: er ist mit Aufseher- und
    Peer-Sessions geteilt, und ein fremdes ``git checkout``/``pull`` setzt die
    mtime einer Handoff-Datei auf jetzt. Der Hook würde das für eine frische
    Übergabe halten und eine arbeitende Session abschießen. Bau-Sessions
    arbeiten ohnehin nie im Hauptbaum.
    """
    worktree = Path(worktree_pfad(ticket)).expanduser()
    return [str(worktree / "docs" / "handoffs"), str(worktree / "docs")]


def hook_befehl(*teile: Path | str) -> str:
    """Hook-Befehl für Bash: Claude Code startet Hooks auch unter Windows in Git Bash.

    ``subprocess.list2cmdline`` ließe Backslashes stehen, Bash schluckt sie
    (``C:Python313python.exe: command not found``). Daher Pfade mit ``/`` und ``shlex.quote``.
    """
    return " ".join(shlex.quote(t.as_posix() if isinstance(t, Path) else t) for t in teile)


def frage_sperre_befehl() -> str:
    """Frage-Sperre-Befehl; Linux/macOS ``~`` (wortgleich mit Server-Template), Windows absolut.

    Unter Windows vererbt Claude Code ``MSYS_NO_PATHCONV=1``: Git-Bash löst ``~`` zu ``/c/Users/…``
    auf; ``node`` liest daraus einen Pfad unter ``C:/c/`` (#565). Daher dort der absolute Pfad mit ``/``.
    """
    if sys.platform != "win32":
        return FRAGE_SPERRE_BEFEHL
    ziel = Path.home() / FRAGE_SPERRE_BEFEHL.split(" ~/", 1)[1]
    return hook_befehl("node", ziel)


def staffel_aktiv(konfig: dict) -> bool:
    """Staffel-Neustart an der Smart-Zone-Grenze erlaubt? ``staffel.aktiv`` in der Repo-Konfig.

    Aus (Vorgabe des Menschen, 24.09.2026): Neustarts mitten im Deploy-Gate brachen Tests ab und verloren
    Kontext. Die Smart Zone regelt dann allein der Ticket-Schnitt vorab; die Session baut
    ihr Ticket bis zum Ende durch.

    Die Ablöse-Grenze kommt nie aus dieser Konfig: ``staffel.grenze_k`` ist nur der
    Ticket-Schnitt (Beschreibung ``config.DEFAULTS["staffel"]``, #417).
    """
    return bool((konfig.get("staffel") or {}).get("aktiv", True))


def staffel_hooks(mit_staffel: bool = True) -> dict:
    """Hook-Block für die Session-eigene ``settings.json``.

    ``Stop``: zuerst die Frage-Sperre (#321: endet die Antwort mit einer Frage, heißt
    es „Vorschlag nehmen, weitermachen“), dann der Staffel-Hook, danach der Bau-Log-Hook des Skills (Token,
    Modell, Dauer je Runde); er gibt auch die Umzug-Anfrage des Aufsehers weiter (#212). ``SubagentStop``: Bau-Log-Zeile je Subagent (#204).
    ``PreToolUse``: ``AskUserQuestion`` → Frage-Sperre lehnt ab (#321); ``Bash`` → Spec-Sperre
    lehnt rohes ``gh issue close <Spec>`` ab und nennt ``to_spawn.py spec-schliessen`` (E2-4).
    """
    frage_sperre = frage_sperre_befehl()
    staffel = hook_befehl(Path(sys.executable), staffel_hook_pfad(REPO))
    bau_log_stop = hook_befehl(Path(sys.executable), TO_SPAWN_CLI, "hook-stop")
    bau_log_sub = hook_befehl(Path(sys.executable), TO_SPAWN_CLI, "hook-subagent-stop")
    spec_sperre = hook_befehl(Path(sys.executable), TO_SPAWN_CLI, "hook-spec-sperre")
    stop = [{"type": "command", "command": frage_sperre}]
    if mit_staffel:
        stop.append({"type": "command", "command": staffel})
    stop.append({"type": "command", "command": bau_log_stop})
    return {
        "Stop": [{"hooks": stop}],
        "SubagentStop": [{"hooks": [{"type": "command", "command": bau_log_sub}]}],
        "PreToolUse": [
            {"matcher": "AskUserQuestion", "hooks": [{"type": "command", "command": frage_sperre}]},
            {"matcher": "Bash", "hooks": [{"type": "command", "command": spec_sperre}]},
        ],
    }


def session_settings(overrides: dict[str, Any], mit_staffel: bool = True) -> dict[str, Any]:
    """Inhalt der Session-eigenen ``settings.json``; ``AskUserQuestion`` ist gesperrt (#321)."""
    return {
        "skillOverrides": overrides,
        "hooks": staffel_hooks(mit_staffel),
        "permissions": {"deny": ["AskUserQuestion"]},
    }


def bau_log_umgebung(
    ticket: str, runde: int, start: float, effort: str | None, spec: str | None = None
) -> dict[str, str]:
    """Umgebung der Bau-Log-Hooks (#204): Ticket, Runde, Start, Effort, Ziel-Ordner.

    ``TO_SPAWN_LOG_REPO`` zeigt auf den Ticket-Worktree; existiert er (noch/nicht mehr)
    nicht, schreiben die Hooks die unversionierte Laufdatei in den Hauptbaum
    ``TO_SPAWN_LOG_RUECKFALL`` (#257; ``TO_SPAWN_REPO`` selbst nimmt die Session nicht
    mit, #205). Die versionierte Datei schreibt nur ``eintrag`` im Worktree.
    """
    umgebung = {
        "TO_SPAWN_TICKET": ticket,
        "TO_SPAWN_START": str(start),
        "TO_SPAWN_STAFFEL": str(runde),
        "TO_SPAWN_LOG_REPO": str(Path(worktree_pfad(ticket)).expanduser()),
        "TO_SPAWN_LOG_RUECKFALL": str(REPO),
    }
    if effort:
        umgebung["TO_SPAWN_EFFORT"] = effort
    if spec and spec != "?":
        # Smart-Zone-Ledger liest die Spec aus TO_SPAWN_SPEC (Feld „spec“).
        umgebung["TO_SPAWN_SPEC"] = spec
    return umgebung


def staffel_umgebung(
    ticket: str, staffel_datei: Path, runde: int, fingerabdruck: str = "", staffel_max: int = STAFFEL_MAX_DEFAULT
) -> dict[str, str]:
    """Umgebung des Staffel-Hooks; ``BAU_WORKTREE`` + ``BAU_STAFFEL_MAX`` für die Ablösung
    ab eigenem Handoff ohne Aufsicht (#276)."""
    return {
        "BAU_TICKET": ticket,
        "BAU_WORKTREE": str(Path(worktree_pfad(ticket)).expanduser()),
        "BAU_STAFFEL_MAX": str(max(1, staffel_max)),
        "BAU_STAFFEL_DATEI": str(staffel_datei),
        "BAU_SESSION_START": str(time.time()),
        "BAU_HANDOFF_DIRS": os.pathsep.join(handoff_dirs(ticket)),
        "BAU_STAFFEL_RUNDE": str(runde),
        "BAU_STAFFEL_FINGERABDRUCK": fingerabdruck,
        "BAU_LAUNCHER_PID": str(os.getpid()),
    }


def staffel_uebergabe(staffel_datei: Path) -> dict | None:
    """Übergabe einlesen und Datei verbrauchen (sonst startet die nächste Runde endlos)."""
    if not staffel_datei.is_file():
        return None
    daten = read_json(staffel_datei, {})
    staffel_datei.unlink(missing_ok=True)
    return daten or None


def staffel_ziel(uebergabe: dict) -> Path | None:
    """Handoff-Datei aus der Übergabe — nur wenn sie wirklich lesbar dort liegt."""
    rohwert = str(uebergabe.get("handoff") or "").strip()
    if not rohwert:
        return None
    pfad = Path(rohwert)
    return pfad if pfad.is_file() else None


def exit_code(code: int) -> int:
    """Signal-Tode als Shell-Code (143 statt -15) — ``sys.exit(-15)`` käme als 241 an."""
    return 128 + abs(code) if code < 0 else code


def auftrag_prompt(prompt: str, auftrag: str) -> str:
    """Auftrag dieser Runde (z. B. Aufseher-Kommentar) klar abgesetzt vor den Prompt (#285).

    Ohne Auftrag ruft niemand diese Funktion — der Prompt bleibt dann unverändert.
    """
    text = auftrag.strip()
    if len(text) > AUFTRAG_MAX_ZEICHEN:
        text = text[:AUFTRAG_MAX_ZEICHEN] + "\n(… gekürzt)"
    # Keine Backticks/Code-Zäune (Windows-Notnagel shell=True, wie staffel_prompt).
    return (
        "## Auftrag dieser Runde\n"
        "Diese Runde wurde mit einem eigenen Auftrag gestartet — er gilt vor dem "
        "allgemeinen Ticket-Ablauf darunter:\n\n"
        f"{text}\n\n"
        f"---\n\n{prompt}"
    )


def wiederanlauf_prompt(prompt: str, hinweis: str, *, art: str = "") -> str:
    """Wiederanlauf-Stand klar abgesetzt vor den Prompt (#282).

    Ohne Hinweis bleibt der Prompt unverändert. Bei ``art == "abschluss"``: das Ticket ist
    fertig gebaut — Schritt 1 (Blocker-Prüfung) und Neubau entfallen.
    """
    text = hinweis.strip()
    if not text:
        return prompt
    # Keine Backticks/Code-Zäune (Windows-Notnagel shell=True, wie staffel_prompt).
    if art == "abschluss":
        return (
            "## Wiederanlauf-Stand (Abschluss)\n"
            f"{text}\n\n"
            "Überspringe Schritt 1 (Blocker-Prüfung) und den Neubau: das Ticket ist "
            "bereits gebaut. Hole ausschließlich die oben genannten Abschluss-Schritte "
            "nach, belege sie und schließe die Session ab.\n\n"
            f"---\n\n{prompt}"
        )
    return f"## Wiederanlauf-Stand\n{text}\n\n---\n\n{prompt}"


def staffel_prompt(prompt: str, handoff: Path, runde: int) -> str:
    """Startkontext der Folge-Session: Handoff-Inhalt vor dem Originalauftrag."""
    try:
        inhalt = handoff.read_text(encoding="utf-8")
    except OSError as exc:
        inhalt = f"(Handoff {handoff} nicht lesbar: {exc})"
    if len(inhalt) > STAFFEL_HANDOFF_MAX_ZEICHEN:
        inhalt = inhalt[:STAFFEL_HANDOFF_MAX_ZEICHEN] + "\n(… gekürzt)"
    # Keine Backticks/Code-Zäune: auf dem Windows-Notnagel (shell=True) würde die
    # Shell sie auswerten statt durchzureichen.
    return (
        f"## Staffel-Übergabe (Runde {runde})\n"
        f"Die Vorsession hat an der Smart-Zone-Grenze abgegeben. Ihr Handoff "
        f"({handoff}) ist dein Startkontext — lies ihn hier, nicht erneut von der Platte, "
        f"und mach dort weiter, wo er endet.\n\n"
        f"----- HANDOFF ANFANG -----\n{inhalt}\n----- HANDOFF ENDE -----\n\n"
        f"---\n\n{prompt}"
    )


# --- Gesprächs-ID (#236) ----------------------------------------------------


def resume_prompt(ticket: str) -> str:
    """Kurzer Weiter-Text, wenn der Aufpasser eine Session mit ``--resume`` fortsetzt (#236, #288)."""
    return (
        f"Aufpasser: Weiter mit Ticket #{ticket} genau dort, wo du warst. "
        "Ticket noch offen? weiterbauen (bei offenem Blocker oder Checkpoint: ScheduleWakeup); "
        "Ticket fertig gebaut oder geschlossen (nichts zu tun)? Loop mit stop: true beenden und "
        "Session sofort mit /exit schließen — kein ScheduleWakeup."
    )


def prompt_setzen(cmd: list[str], prompt: str, mit_prompt: bool) -> None:
    """Prompt als letztes Argument setzen: ersetzen, wenn der Befehl schon einen trägt,
    sonst anhängen (Folge-Runde nach ``--ohne-prompt``, #431 — sonst überschriebe der
    Prompt den Wert von ``--session-id``)."""
    if mit_prompt:
        cmd[-1] = prompt
    else:
        cmd.append(prompt)


def session_id_setzen(cmd: list[str], out: Path, sid: str | None = None, ticket: str = "", runde: int = 1) -> str:
    """Gesprächs-ID im Befehl setzen (``--resume`` → ``--session-id``, jede Runde frisch),
    in ``session-id.txt`` schreiben und als ``BAU_SESSION_ID`` setzen. Ohne ``sid``:
    nur die vorhandene ID merken (Runde 1). Mit ``ticket`` zusätzlich nach
    ``<repo>/.to-spawn/sessions/<N>.json`` (Aufpasser #236 R2: Fortsetzen nach Fenster-Tod)."""
    for flag in ("--session-id", "--resume"):
        if flag in cmd[:-1]:
            i = cmd.index(flag)
            if sid:
                cmd[i : i + 2] = ["--session-id", sid]
            sid = sid or cmd[i + 1]
            break
    sid = sid or str(uuid.uuid4())
    os.environ["BAU_SESSION_ID"] = sid
    (out / "session-id.txt").write_text(sid + "\n", encoding="utf-8")
    if ticket:
        sessions_datei.schreiben(REPO, ticket, sid, Path.cwd(), runde)
    return sid


# --- Umzug (#212) -----------------------------------------------------------


#: ``<branch>@<sha>:<pfad>`` — der SHA ist der Commit, den der Umzug gepusht hat.
UMZUG_REF_SHA = re.compile(r"^(?P<branch>.+)@(?P<sha>[0-9a-fA-F]{7,64})$")


def _git_lauf(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO,
        capture_output=True,
        **konsole.UNTERPROZESS_TEXT,
        check=False,
        **prozessbaum.fensterlos(),
    )


def umzug_handoff_lesen(ref: str) -> tuple[str, str, str]:
    """``<branch>@<sha>:<pfad>`` → ``(branch, pfad, inhalt)`` aus genau diesem Commit (im REPO).

    Der Commit muss in ``origin/<branch>`` liegen. Die alte Form ``<branch>:<pfad>``
    (ohne SHA) liest den aktuellen Stand von ``origin/<branch>``.
    Nicht lesbar (auch: ``git fetch`` scheitert) → Abbruch mit Exit 2, es startet nichts.
    """
    links, _, pfad = ref.partition(":")
    treffer = UMZUG_REF_SHA.match(links)
    branch, sha = (treffer.group("branch"), treffer.group("sha")) if treffer else (links, "")
    if not branch or not pfad:
        log.error("Umzug-Handoff nicht lesbar: --umzug erwartet <branch>@<sha>:<pfad>, bekam %r.", ref)
        sys.exit(2)
    geholt = _git_lauf("fetch", "-q", "origin", branch)
    if geholt.returncode != 0:
        log.error(
            "Umzug-Handoff nicht lesbar: git fetch origin %s scheiterte (Exit %s: %s) — nichts gestartet.",
            branch,
            geholt.returncode,
            (geholt.stderr or geholt.stdout).strip()[:200] or "keine Meldung",
        )
        sys.exit(2)
    if sha:
        enthalten = _git_lauf("merge-base", "--is-ancestor", sha, f"origin/{branch}")
        if enthalten.returncode != 0:
            log.error(
                "Umzug-Handoff nicht lesbar: Commit %s ist nicht in origin/%s (%s) — nichts gestartet.",
                sha,
                branch,
                enthalten.stderr.strip()[:200] or "nicht enthalten",
            )
            sys.exit(2)
    stand = sha or f"origin/{branch}"
    gezeigt = _git_lauf("show", f"{stand}:{pfad}")
    if gezeigt.returncode != 0 or not gezeigt.stdout.strip():
        log.error(
            "Umzug-Handoff nicht lesbar: %s:%s (%s) — nichts gestartet.",
            stand,
            pfad,
            gezeigt.stderr.strip()[:200] or "leer",
        )
        sys.exit(2)
    return branch, pfad, gezeigt.stdout


def umzug_prompt(prompt: str, text: str, branch: str, ticket: str) -> str:
    """Startkontext einer umgezogenen Session: Worktree-Anweisung + Handoff vor dem Auftrag."""
    if len(text) > STAFFEL_HANDOFF_MAX_ZEICHEN:
        text = text[:STAFFEL_HANDOFF_MAX_ZEICHEN] + "\n(… gekürzt)"
    wt = worktree_pfad(ticket)
    # Keine Backticks/Code-Zäune (Windows-Notnagel shell=True, wie staffel_prompt).
    return (
        "## Umzug auf den Bau-Server\n"
        "Diese Session ist vom lokalen PC auf den Bau-Server umgezogen. "
        f"Arbeitsstand liegt auf Branch {branch} (origin). Worktree {wt} auf diesen Branch setzen: "
        "existiert er → sauberen Baum prüfen (git status --short leer), dann "
        f"git fetch origin && git checkout -B {branch} origin/{branch}; sonst "
        f"git worktree add -B {branch} {wt} origin/{branch}. "
        "Assignee bleibt, kein neuer Claim nötig. Der Handoff unten ist dein Startkontext — "
        "lies ihn hier, nicht erneut von der Platte.\n\n"
        f"----- HANDOFF ANFANG -----\n{text}\n----- HANDOFF ENDE -----\n\n"
        f"---\n\n{prompt}"
    )


def umzug_anfragen_aufraeumen(ticket: str) -> Path:
    """Alte Umzug-Anfragen des Aufsehers löschen; Rückgabe: Pfad der Anfrage-Datei."""
    anfrage = REPO / ".to-spawn" / f"umzug-anfrage-{ticket}"
    anfrage.unlink(missing_ok=True)
    Path(f"{anfrage}.laeuft").unlink(missing_ok=True)
    return anfrage


def starte_session(cmd: list[str], umzug_datei: Path) -> tuple[int, dict | None]:
    """Interaktiv: stdin/stdout durchreichen, dabei auf Umzug- und Fertig-Datei achten (#212, #288).

    Taucht ``umzug_datei`` auf, wird die Session beendet (terminate, nach 20 s kill).
    Auf Linux achtet die Umzug-Wache zusätzlich auf ``BAU_FERTIG_DATEI`` (Stop-Hook meldet
    geschlossenes/erledigtes Ticket → stille Session wird samt Kindern beendet); bei Werkzeugen
    ohne Claude-Stop-Hook (z. B. ``agy``) prüft sie bei Stille regelmäßig über denselben Weg.
    .cmd-Shim auf Windows: ``shell=True``-Notnagel steckt in ``umzug.starte_mit_umzug_wache``.
    """
    fertig_roh = os.environ.get("BAU_FERTIG_DATEI", "").strip() if sys.platform != "win32" else ""
    fertig_datei = Path(fertig_roh) if fertig_roh else None
    ist_claude = any(prozessbaum.programm_name(a) == "claude" for a in cmd[:2])
    fertig_poll_s = umzug.FERTIG_POLL_S if (fertig_datei is not None and not ist_claude) else 0.0
    return umzug.starte_mit_umzug_wache(cmd, umzug_datei, fertig_datei=fertig_datei, fertig_poll_s=fertig_poll_s)


# --- Main -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Schlanke Claude-Session für ein Ticket.")
    parser.add_argument("ticket", type=int, help="GitHub-Issue-Nummer")
    parser.add_argument("--dry-run", action="store_true", help="nur Zusammenfassung + Befehl")
    parser.add_argument("--model", help="Modell (überschreibt Konfig/Manifest)")
    parser.add_argument(
        "--runner",
        choices=list(werkzeug.WERKZEUGE),
        help="Bau-Session-Werkzeug überschreiben (claude oder agy, #660)",
    )
    parser.add_argument("--print-prompt", action="store_true", help="nur den Prompt ausgeben")
    parser.add_argument(
        "--sofort",
        action="store_true",
        help="nur auf offene Blocker warten, Commit-Prüfung macht die Session (#906)",
    )
    parser.add_argument(
        "--takt",
        type=int,
        default=TAKT_STANDARD_S,
        help=f"Sekunden zwischen zwei Blocker-Prüfungen ({TAKT_STANDARD_S}, mindestens {TAKT_MIN_S})",
    )
    parser.add_argument(
        "--staffel-max",
        type=int,
        default=STAFFEL_MAX_DEFAULT,
        help="Höchstzahl Staffel-Runden je Ticket (8); 1 = kein Neustart",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION-ID",
        help="Aufpasser (#236): Session mit dieser Gesprächs-ID fortsetzen, ohne Blocker-Warten",
    )
    parser.add_argument(
        "--umzug",
        metavar="BRANCH@SHA:PFAD",
        help="Umzug vom PC (#212): Handoff aus Commit <sha> auf origin/<branch> als Startkontext "
        "(alte Form <branch>:<pfad> liest origin/<branch>), impliziert --sofort",
    )
    parser.add_argument(
        "--probesitz",
        action="store_true",
        help="Probesitz (#214): Wegwerf-Session ohne Terminal (claude -p), fester Mini-Auftrag, "
        "kein Manifest nötig, impliziert --sofort",
    )
    parser.add_argument(
        "--ohne-prompt",
        action="store_true",
        help="respawn (#431): Session ohne ersten Prompt starten — der Auftrag wird danach "
        "ins Fenster getippt; Konfiguration (Settings, MCP, Session-ID, Umgebung) wie immer",
    )
    parser.add_argument(
        "--remote-control",
        action="store_true",
        help="respawn am PC (#501): Session mit Remote Control „bau <N>“ starten",
    )
    parser.add_argument(
        "--auftrag",
        metavar="TEXT",
        help="Auftrag dieser Runde (#285): kommt als Abschnitt „## Auftrag dieser Runde“ vor den "
        "Prompt; auch als Umgebungsvariable BAU_AUFTRAG (ohne beides bleibt der Prompt gleich)",
    )
    args = parser.parse_args()
    ticket = str(args.ticket)
    # GitHub-Slug verbindlich (#257 F5): kein GitHub-Origin → Exit 2 mit Grund, kein Rückfall.
    global GH_REPO, START_WARNUNG_TICKET
    GH_REPO = gh_repo_ermitteln(REPO)
    if args.probesitz:
        args.sofort = True
        if not staffel_hook_pfad(REPO).is_file():
            log.warning(
                "Staffel-Hook %s fehlt (weder im Repo noch im Skill) — Punkt 6 (Handoff → "
                "Folge-Session) kann nicht grün werden.",
                staffel_hook_pfad(REPO),
            )
    # Arbeitsordner-Stand (#283): hier nur eine Hinweis-Zeile — nie Abbruch, nie Nachziehen
    # (auch nicht bei --resume). Nachziehen/Abbruch macht allein der Erst-Start (to_spawn.py arbeitsstand).
    if (stand_hinweis := arbeitsstand.hinweis(REPO)) is not None:
        log.warning("%s", stand_hinweis)

    if not args.dry_run:  # Probelauf ohne Seiteneffekte (#205)
        config.sicherstellen(REPO)
    konfig = config.lade(REPO)
    runner = args.runner or werkzeug.runner_von(konfig)
    # Fortsetzen nur mit vorhandenem Transkript (#236, F5): ohne die Datei startete
    # ``claude --resume`` eine frische Session — genau das darf der Aufpasser nie.
    # Bei ``agy`` liegen Gespräche nicht als Claude-JSONL unter ``~/.claude/projects``.
    if args.resume and not args.dry_run and runner == "claude":
        transkript = transkript_ordner(Path.cwd()) / f"{args.resume}.jsonl"
        if not transkript.is_file():
            log.error("--resume %s: Transkript %s fehlt — kein Start.", args.resume, transkript)
            return 2

    if not args.dry_run:  # Probelauf schreibt kein Bau-Log (#205)
        START_WARNUNG_TICKET = ticket
    regel_kopie_warnen()
    default = load_default()
    found = None if args.probesitz else find_manifest(ticket)
    manifest_daten: dict[str, Any] | None = None
    if args.probesitz:
        # Wegwerf-Ticket: kein Manifest, kein gh — der Auftrag ist fest (#214).
        entry = {}
        spec, title = "probesitz", f"Probesitz-Wegwerf-Session #{ticket}"
    elif found:
        manifest_daten, entry = found
        spec = str(manifest_daten.get("spec", "?"))
        title = entry.get("title") or f"Ticket {ticket}"
    else:
        log.warning("Kein Manifest führt Ticket #%s — nur Core, Spec aus gh.", ticket)
        entry = {}
        title, spec_gh = ticket_from_gh(ticket)
        if spec_gh is None:
            log.error("Spec-Nr. nicht aus Issue-Body #%s ermittelbar — Abbruch.", ticket)
            return 2
        spec = spec_gh

    auftrag = args.auftrag or os.environ.get("BAU_AUFTRAG", "")
    umzug_teile = umzug_handoff_lesen(args.umzug) if args.umzug else None

    def start_texte(lage_hinweis: str = "", lage_art: str = "") -> tuple[str, str]:
        """Die eine Stelle für den Start-Text (#281, #282): ``(Grund-Prompt, erster Prompt)`` aus
        der Vorlage, wie sie JETZT auf der Platte liegt. ``main`` ruft sie beim Start (Vorlage
        kaputt → sofort Exit 2, ``--print-prompt``) und erneut im Moment des Loslassens — ein
        Fenster, das stundenlang auf Blocker wartet, startet so mit der Regel von jetzt."""
        try:
            grund = (
                probesitz.WEGWERF_PROMPT.format(ticket=ticket)
                if args.probesitz
                else build_prompt(
                    load_default()["prompt_template"], ticket, spec, title, build_kontext(entry), config.lade(REPO)
                )
            )
        except ValueError as fehler:  # Platzhalter-Rest (#501): Vorlage kaputt → Exit 2 mit Grund
            print(f"bau: {fehler}", file=sys.stderr)
            raise SystemExit(2) from fehler
        if auftrag.strip():
            # Vor dem Prompt, damit auch jede Staffel-Runde den Auftrag dieser Runde sieht (#285).
            grund = auftrag_prompt(grund, auftrag)
        if lage_hinweis.strip():
            grund = wiederanlauf_prompt(grund, lage_hinweis, art=lage_art)
        if umzug_teile is not None:
            branch, _pfad, handoff_text = umzug_teile
            return grund, umzug_prompt(grund, handoff_text, branch, ticket)
        return grund, grund

    prompt, erster_prompt = start_texte()
    if args.print_prompt:
        print(erster_prompt)
        return 0

    # Skills
    whitelist, overrides = skill_auswahl(default, entry, konfig)

    # MCPs
    mcp_wanted = list(dict.fromkeys(default.get("core_mcp", []) + (entry.get("mcp") or [])))
    catalog = mcp_catalog()
    mcp_servers, chrome = sitzung_filter.mcp_auswahl(mcp_wanted, catalog, logger=log)
    # Pflicht-MCP context-mode (#237): ``--strict-mcp-config`` sperrt das Plugin-MCP aus,
    # also steht es hier selbst in der mcp.json — kein Manifest kann es abwählen.
    # Gilt nur für ``claude``; ``agy`` nutzt weder ``--mcp-config`` noch das Claude-Plugin (#660).
    if runner == "claude":
        try:
            mcp_servers.update(context_mode.server_definition(_aktuelles_claude_dir()))
        except context_mode.ContextModeFehlt as fehler:
            log.error("%s", fehler)
            return 2

    # Temp-Dateien
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # #136/#177: markierter Ticket-Temp-Ordner auf der Platte (temp_ordner.anlegen)
    temp_umg = temp_ordner.umgebung(args.ticket, repo=GH_REPO, von="bau")
    out = Path(temp_umg.get("TMPDIR") or tempfile.gettempdir())
    basis = out / f"{GH_REPO.rsplit('/', 1)[-1]}-bau"
    basis.mkdir(parents=True, exist_ok=True)
    # mkdtemp statt ``<ticket>-<stamp>``: zwei Starts desselben Tickets in derselben Sekunde
    # (Staffel-Neustart, parallele Tests) teilten sonst settings.json und prompt.txt.
    out = Path(tempfile.mkdtemp(prefix=f"{ticket}-{stamp}-", dir=basis))
    settings_path = out / "settings.json"
    mcp_path = out / "mcp.json"
    staffel_datei = out / "staffel.json"
    if runner == "claude":
        settings_path.write_text(
            json.dumps(
                session_settings(overrides, staffel_aktiv(konfig)),
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        mcp_path.write_text(
            json.dumps({"mcpServers": mcp_servers}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    # Effektiver Effort an genau einer Stelle (to_spawn.manifest, #845):
    # Ticket-Effort > Spec-Effort > Konfig ``effort.ticket`` > ``low``.
    # Immer ausdrücklich setzen: ohne Flag erbt ``claude`` den globalen Standard, und ``agy``
    # übersetzt den Effort in ``gemini-4-neon-<effort>``.
    effort = manifest.effort_fuer(manifest_daten, entry, konfig)
    # Vorrang: --model > Repo-Konfig ``modelle.ticket`` (#205) > Effort-Übersetzung (agy, #845) > Vorgabe (#660).
    model = werkzeug.modell_fuer(
        runner,
        konfig,
        cli_modell=args.model,
        default_modell=default.get("model"),
        effort=effort,
    )
    # Unter Test nur die Attrappe (#1406); explizites ``starter.finde("claude")`` hält
    # die bekannte Start-Stelle (#97) auch für statische Prüfungen sichtbar.
    programm = starter.finde("claude") if runner == "claude" else starter.finde(runner)
    cmd = werkzeug.startbefehl(
        runner,
        programm=programm,
        modell=model,
        prompt=None,
        interaktiv=not args.probesitz,
        effort=effort,
        settings_pfad=settings_path if runner == "claude" else None,
        mcp_pfad=mcp_path if runner == "claude" else None,
        chrome=chrome,
        probesitz=args.probesitz,
        probesitz_rechte=PROBESITZ_RECHTE,
        remote_control=f"bau {ticket}" if args.remote_control else None,
        resume=args.resume,
        session_id=str(uuid.uuid4()) if runner == "claude" and not args.resume else None,
    )
    # ``mit_prompt``: trägt ``cmd`` einen Prompt? (nicht bei --ohne-prompt)
    mit_prompt = not args.ohne_prompt

    off_count = sum(1 for v in overrides.values() if v == "off")
    log.info("Ticket #%s · Spec #%s · %s", ticket, spec, title)
    log.info("Werkzeug: %s", runner)
    log.info("Skills on (%d): %s", len(whitelist), ", ".join(whitelist))
    log.info("Skills off: %d", off_count)
    log.info("MCPs: %s", ", ".join(mcp_servers) or "—")
    log.info("Chrome: %s", "ja" if chrome else "nein")
    log.info(
        "Kontext-Paket-Zeilen: %d",
        len(entry.get("files") or []) + len(entry.get("docs") or []),
    )
    log.info("Modell: %s", model or "(Default)")
    log.info("Temp: %s", out)

    if args.dry_run:
        # Sandbox je Worktree (#210): Probelauf zeigt nur den Präfix, legt nichts an.
        praefix = nest.sandbox_start(konfig, worktree_pfad(ticket), REPO, trocken=True)
        if praefix is None:
            return 2
        dry_cmd = list(cmd)
        if mit_prompt:
            werkzeug.prompt_setzen(dry_cmd, runner, "<prompt>", hat_prompt=False, interaktiv=not args.probesitz)
        dry_cmd = praefix + dry_cmd
        print("\nBefehl:")
        print(" ".join(f'"{c}"' if " " in c or "\n" in c or c == "<prompt>" else c for c in dry_cmd))
        return 0
    # Die eine Start-Stelle (#97): unter Sperre je Ticket prüfen, ob schon eine Session
    # läuft und ob das Ticket offen ist; unbekannt → kein Start (fail-closed).
    # ``--ohne-prompt`` ist der respawn-Start (#431): die alte Session läuft bis Schritt e
    # weiter und hält die Sperre — also Ablösung (höchstens eine je Ticket).
    start: startstelle.Entscheidung | None = None
    if not args.probesitz:
        start = startstelle.betreten(
            ticket,
            GH_REPO,
            REPO,
            abloesung=args.ohne_prompt,
            uebernahme=bool(args.umzug),
        )
        if not start.erlaubt:
            print(start.grund, flush=True)
            return start.exit_code
    # Fertig gebaut mit offenem Abschluss (#282): kein Blocker-Warten, kein Neubau.
    args.abschluss = start is not None and start.art == "abschluss"
    global _ECHTER_LAUF
    _ECHTER_LAUF = True  # ab hier echter Lauf (Ticket-Ende stößt den Hausmeister an)

    blocker_schranke(ticket, args)
    # Speicher-Schutz (#257 Paket B, Fixrunde 1 F3), die EINE Stelle vor dem Prozessstart
    # (gilt für Erststart, --resume und --umzug): RAM knapp oder Obergrenze an
    # Claude-Sessions erreicht → warten statt Exit 5, damit das Fenster und der Grund bleiben.
    # Platte (#1369, #781): Start-Check des Hausmeisters — synchrones Räumen und Warten
    # (Takt ~15 min) erst bei voller Platte; bei Platznot darüber nur Hintergrund-Anstoß.
    speicher.auf_platte_warten(wer=f"bau {ticket}")
    auf_speicher_warten(konfig, wer=f"bau {ticket}")
    # Start-Text erst jetzt, im Moment des Loslassens (#281): in der Wartezeit kann sich die
    # Vorlage geändert haben — der Text vom eigenen Start wäre veraltet.
    prompt, erster_prompt = start_texte()
    if args.resume:
        erster_prompt = resume_prompt(ticket)
    (out / "prompt.txt").write_text(prompt, encoding="utf-8")
    aktueller_prompt = erster_prompt if mit_prompt else ""
    if mit_prompt:
        werkzeug.prompt_setzen(cmd, runner, erster_prompt, hat_prompt=False, interaktiv=not args.probesitz)
    # Push-Sperre (#1445): Git-Hooks vor JEDEM Start (Erststart, --resume, --umzug),
    # unabhängig vom Sandbox-Modus; Quelle origin, nie der Arbeitsordner.
    nest.git_hooks_einrichten(REPO)
    # Sandbox erst jetzt (#210): Worktree entsteht vom frischen origin-Stand (nest holt ihn).
    praefix = nest.sandbox_start(konfig, worktree_pfad(ticket), REPO)
    if praefix is None:
        return 2
    cmd = praefix + cmd

    # Vertrauens-Dialog vorab bestätigen (#257): nur für Claude Code (``~/.claude.json``).
    if runner == "claude":
        vertrauen.still_sicherstellen(Path(worktree_pfad(ticket)).expanduser(), REPO)

    # Aus einer Claude-Session gestartet erben Kind-Sessions die Markierung
    # CLAUDE_CODE_CHILD_SESSION und speichern kein Transkript (kein Resume nach
    # Absturz, Beleg 17.09.2026). Persistenz deshalb ausdrücklich erzwingen.
    sauber = sitzung_env.ohne_sitzung()  # alle Sitzungs-Variablen, nicht nur CHILD_SESSION
    os.environ.clear()
    os.environ.update(sauber)
    # Eine Session im Worktree darf nicht das Repo des Launchers erben (#205).
    os.environ.pop("TO_SPAWN_REPO", None)
    os.environ["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] = "1"
    os.environ.update(temp_umg)  # #136/#177: markierter Ticket-Temp auf der Platte, nie die RAM-Platte
    # Klick-Sperre nur für lokal gespawnte Sessions (Windows, Maus-Müll im Eingabefeld).
    terminal_maus.maus_ruhig()

    # Umzug (#212): bau.py beendet die Session, sobald ``umzug.json`` auftaucht; der
    # Stop-Hook ``hook-umzug`` liest die Anfrage des Aufsehers.
    umzug_datei = out / "umzug.json"
    os.environ["BAU_UMZUG_DATEI"] = str(umzug_datei)
    os.environ["BAU_UMZUG_ANFRAGE"] = str(umzug_anfragen_aufraeumen(ticket))
    # Fertig-Abschluss (nur Linux, #288): Stop-Hook meldet geschlossenes/erledigtes Ticket
    # über ``fertig.json``; bau.py beendet die stille Session dann mit Exit 0 (Fenster schließt).
    fertig_datei: Path | None = None
    if sys.platform != "win32" and not args.probesitz:
        fertig_datei = out / "fertig.json"
        os.environ["BAU_FERTIG_DATEI"] = str(fertig_datei)
    else:
        os.environ.pop("BAU_FERTIG_DATEI", None)

    if args.probesitz:
        # ``claude -p`` liest eine Pipe auf stdin bis zum Ende — ohne Terminal würde die
        # Session hängen. Deshalb stdin fest auf /dev/null (#214).
        leer = os.open(os.devnull, os.O_RDONLY)
        os.dup2(leer, sys.stdin.fileno())
        os.close(leer)

    # Staffel-Schleife: jede Runde eine eigene Session, Übergabe über die Staffel-Datei.
    staffel_datei.unlink(missing_ok=True)
    runde = 1
    fingerabdruck = ""
    handoff_aktuell: Path | None = None
    session_id_setzen(cmd, out, ticket=ticket, runde=runde)
    while True:
        umgebung = staffel_umgebung(ticket, staffel_datei, runde, fingerabdruck, args.staffel_max)
        umgebung.update(bau_log_umgebung(ticket, runde, float(umgebung["BAU_SESSION_START"]), effort, spec))
        os.environ.update(umgebung)
        # Ticket während Blocker-Wartezeit oder Vorrunde geschlossen → keine neue Runde (#97);
        # Stand unbekannt → laut Exit 2, nie still wie „geschlossen“ (#191);
        # Wiederanlauf-Lage je Runde frisch als Vorsatz-Block (#282).
        weiter: startstelle.Entscheidung | None = None
        if not args.probesitz:
            weiter = startstelle.weiter_entscheidung(ticket, REPO)
            if not weiter.erlaubt:
                print(weiter.grund, file=sys.stderr if weiter.exit_code else sys.stdout, flush=True)
                return weiter.exit_code
        lage_hinweis = weiter.hinweis if weiter is not None else ""
        lage_art = weiter.art if weiter is not None else ""
        if runde == 1:
            if lage_hinweis:
                runden_grund, runden_erster = start_texte(lage_hinweis, lage_art)
                (out / "prompt.txt").write_text(runden_grund, encoding="utf-8")
                if mit_prompt:
                    if args.resume:
                        aktueller_prompt = (
                            f"{lage_hinweis}\n\n{resume_prompt(ticket)}"
                            if lage_art == "abschluss"
                            else resume_prompt(ticket)
                        )
                    else:
                        aktueller_prompt = runden_erster
                    werkzeug.prompt_setzen(
                        cmd, runner, aktueller_prompt, hat_prompt=True, interaktiv=not args.probesitz
                    )
        elif handoff_aktuell is not None:
            runden_grund = wiederanlauf_prompt(prompt, lage_hinweis, art=lage_art)
            aktueller_prompt = staffel_prompt(runden_grund, handoff_aktuell, runde)
            werkzeug.prompt_setzen(cmd, runner, aktueller_prompt, hat_prompt=mit_prompt, interaktiv=not args.probesitz)
            mit_prompt = True
        (out / f"prompt-runde{runde}.txt").write_text(aktueller_prompt, encoding="utf-8")
        code, umzug_daten = starte_session(cmd, umzug_datei)
        if umzug_daten is not None:
            # Kein Staffel-Neustart: die Session läuft jetzt auf dem Server weiter.
            staffel_datei.unlink(missing_ok=True)
            if umzug_daten.get("lokal_beendet") is False:
                log.error(
                    "Umzug nach %s bestätigt, aber die lokale Session ist NICHT sicher beendet "
                    "— Doppel-Lauf droht: Claude-Fenster von Hand schließen.",
                    umzug_daten.get("ziel") or "?",
                )
                return 1
            log.info(
                "Umzug nach %s bestätigt — lokale Session beendet (Exit %s).",
                umzug_daten.get("ziel") or "?",
                code,
            )
            return 0
        if runner == "agy":
            wt_pfad = Path(worktree_pfad(ticket)).expanduser()
            try:
                werkzeug.agy_bau_log_nachtragen(
                    REPO,
                    ticket,
                    wt_pfad,
                    start_ts=float(umgebung["BAU_SESSION_START"]),
                    runde=runde,
                    modell=model,
                    effort=effort,
                )
            except OSError as fehler:
                log.warning("agy-Bau-Log #%s nicht nachgetragen: %s", ticket, fehler)
        fertig_daten = umzug.lies_fertig(fertig_datei) if fertig_datei is not None else None
        if fertig_daten is not None and fertig_daten.get("beendet") is True:
            staffel_datei.unlink(missing_ok=True)
            log.info(
                "Ticket #%s erledigt (%s) — Session beendet, Fenster schließt sich.",
                ticket,
                fertig_daten.get("grund") or "fertig",
            )
            return 0
        uebergabe = staffel_uebergabe(staffel_datei)
        if uebergabe is None and runner == "agy" and staffel_aktiv(konfig):
            fund = werkzeug.agy_staffel_handoff(
                [Path(p) for p in handoff_dirs(ticket)],
                ticket,
                seit=float(umgebung["BAU_SESSION_START"]),
                schon_uebergeben=fingerabdruck,
            )
            if fund is not None:
                h_pfad, h_fp = fund
                offen = args.probesitz
                if not offen:
                    stand_daten = ticket_zustand.lesen(ticket, REPO)
                    offen = stand_daten is not None and not ticket_zustand.fertig_gebaut(stand_daten)
                if offen:
                    wt_pfad = Path(worktree_pfad(ticket)).expanduser()
                    try:
                        bau_log.schreibe(
                            wt_pfad if wt_pfad.is_dir() else REPO,
                            ticket,
                            "handoff",
                            datei=h_pfad.name,
                            staffel=runde,
                            runner="agy",
                        )
                    except OSError as fehler:
                        log.warning("agy-Handoff-Zeile #%s nicht geschrieben: %s", ticket, fehler)
                    uebergabe = {"handoff": str(h_pfad), "fingerabdruck": h_fp}
        if uebergabe is None:
            return exit_code(code)
        handoff = staffel_ziel(uebergabe)
        if handoff is None:
            log.warning(
                "Staffel-Datei ohne lesbaren Handoff (%r) — kein Neustart.",
                uebergabe.get("handoff"),
            )
            return exit_code(code)
        if runde >= max(1, args.staffel_max):
            log.warning(
                "Staffel-Grenze %d erreicht — Handoff %s liegt, kein Neustart.",
                args.staffel_max,
                handoff,
            )
            return exit_code(code)
        runde += 1
        fingerabdruck = str(uebergabe.get("fingerabdruck") or "")
        handoff_aktuell = handoff
        log.info(
            "Staffel-Runde %d · Handoff %s · Vorsession endete mit Exit %s",
            runde,
            handoff.name,
            code,
        )
        if runner == "agy":
            if "--resume" in cmd[:-1]:
                idx = cmd.index("--resume")
                del cmd[idx : idx + 2]
            session_id_setzen(cmd, out, ticket=ticket, runde=runde)
        else:
            session_id_setzen(cmd, out, str(uuid.uuid4()), ticket=ticket, runde=runde)


def ticket_ende_anstossen() -> None:
    """Ticket-Ende (#1373): nach einem echten Lauf räumt am Laptop der Hausmeister im
    Hintergrund auf (Server: Aufpasser). Nie nach --dry-run/--print-prompt/Abbruch; wirft nie."""
    if not _ECHTER_LAUF:
        return
    try:
        from to_spawn import hausmeister_aufgabe

        hausmeister_aufgabe.ticket_ende_anstossen()
    except Exception as fehler:  # Aufräumen darf den Exit-Code von main() nie ändern
        log.warning("Hausmeister am Ticket-Ende nicht angestoßen: %s", fehler, exc_info=True)


if __name__ == "__main__":
    exit_wert = main()  # Abbruch (Ausnahme, Strg+C) fliegt durch: kein Anstoßen
    ticket_ende_anstossen()
    sys.exit(exit_wert)
