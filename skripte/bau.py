"""bau — schlanke Claude-Code-Session für genau ein Ticket.

Aufruf: ``python scripts/bau.py <N> [--dry-run] [--model <m>] [--print-prompt] [--sofort] [--takt <s>] [--umzug <branch>@<sha>:<pfad>] [--probesitz]``

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
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

# Skill-Wurzel in sys.path, damit ``to_spawn.config`` (Repo-Wurzel, Konfig) importierbar ist (#205).
_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import config, context_mode, gh, nest, probesitz, sessions_datei, speicher, umzug, vertrauen  # noqa: E402
from to_spawn.waechter_lauf import transkript_ordner

# Windows-Konsole ist cp1252 — Umlaute/Pfeile im Prompt brauchen UTF-8.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("bau")

#: Repo, in dem gearbeitet wird: ``TO_SPAWN_REPO`` (setzt die Weiterleitung im Repo), sonst
#: Git-Wurzel des aktuellen Ordners — nie der Ort dieses Skripts (liegt im Skill, #205).
REPO = Path(os.environ["TO_SPAWN_REPO"]).resolve() if os.environ.get("TO_SPAWN_REPO") else config.repo_wurzel()
MANIFEST_DIR = REPO / "docs" / "agents" / "manifests"
HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"

# Builtins, die Claude Code ohne Datei mitbringt (per skillOverrides abschaltbar).
BUILTIN_SKILLS = [
    # Plugin-Skills ohne Datei auf der Platte (nur per Override erreichbar)
    "super-token-saver:report-limit",
    "super-token-saver:s-compact",
    "super-token-saver:s-continue",
    "super-token-saver:setup-git-lite",
    "super-token-saver:setup-statusline",
    "super-token-saver:usage-view",
    "loop",
    "run",
    "simplify",
    "dataviz",
    "update-config",
    "schedule",
    "init",
    "keybindings-help",
    "fewer-permission-prompts",
    "workflow-authoring",
    "design",
    "artifact-design",
    "artifact-diagramming",
    "artifact-capabilities",
    "claude-in-chrome",
    "tdd",
]
CHROME_MCP = "claude-in-chrome"
#: Staffel-Hook des Skills (#257): Fremd-Repos brauchen keine eigene Kopie.
STAFFEL_HOOK_SKILL = Path(_SKILL) / "skripte" / "hooks" / "staffel_stop.py"


def staffel_hook_pfad(repo: Path = REPO) -> Path:
    """Repo-Kopie ``scripts/hooks/staffel_stop.py`` wenn vorhanden, sonst die des Skills (#257)."""
    eigene = repo / "scripts" / "hooks" / "staffel_stop.py"
    return eigene if eigene.is_file() else STAFFEL_HOOK_SKILL


#: CLI des Skills (Bau-Log-Hooks #204, Umzug-Anfrage #212) — dieselbe Skill-Wurzel wie oben in sys.path.
TO_SPAWN_CLI = Path(_SKILL) / "to_spawn.py"
STAFFEL_MAX_DEFAULT = 8
#: Werkzeug-Rechte der Probesitz-Wegwerf-Session (#214): nur git, Ordner anlegen, Dateien.
PROBESITZ_RECHTE = ("Bash(git *)", "Bash(mkdir *)", "Write", "Edit", "Read")
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


def load_default(ersatz: Path | None = None) -> dict:
    """``docs/agents/manifests/_default.json`` des Repos; fehlt sie, ``ersatz`` (Probesitz, #214)."""
    path = MANIFEST_DIR / "_default.json"
    if not path.is_file() and ersatz is not None and ersatz.is_file():
        log.warning("Default-Manifest fehlt im Repo — nehme %s.", ersatz)
        path = ersatz
    if not path.is_file():
        log.error("Default-Manifest fehlt: %s", path)
        sys.exit(2)
    default = read_json(path, {})
    if "prompt_template" not in default:
        log.error("Default-Manifest ohne prompt_template (JSON kaputt?): %s", path)
        sys.exit(2)
    return default


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
        text=True,
        encoding="utf-8",
        check=False,
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
    proc = subprocess.run([gh, *args], capture_output=True, text=True, encoding="utf-8", check=False)
    if proc.returncode != 0:
        log.warning("gh %s: %s", " ".join(args[:3]), proc.stderr.strip()[:200])
        return None
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError:
        return None


def blocker_offen(ticket: str) -> list[str]:
    """Gründe, warum #ticket noch nicht starten darf (leer = frei).

    Zwei Prüfungen wie im Loop-Prompt: jeder Blocker muss CLOSED sein und
    seinen Commit ``(#<Blocker>)`` auf ``origin/<Hauptzweig>`` haben — sonst baut die
    Session auf einem Stand ohne die Blocker-Arbeit. Bei gh-/git-Fehlern gilt
    „offen" (fail-closed), damit kein Loop vorzeitig losläuft.
    """
    gh = shutil.which("gh")
    if gh is None:
        return ["gh fehlt"]
    deps = _gh_json(gh, "api", f"repos/{GH_REPO}/issues/{ticket}/dependencies/blocked_by")
    if not isinstance(deps, list):
        return ["Blocker-Abfrage fehlgeschlagen"]
    gruende: list[str] = []
    haupt = f"origin/{gh.hauptzweig(REPO)}"  # master/main/… je Repo (#257)
    for d in deps:
        nr, state = d.get("number"), d.get("state")
        if state != "closed":
            gruende.append(f"#{nr} offen")
            continue
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=REPO, check=False)
        found = subprocess.run(
            ["git", "log", haupt, "--oneline", "--fixed-strings", f"--grep=(#{nr})"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        ).stdout.strip()
        if not found:
            gruende.append(f"#{nr} zu, aber kein Commit „(#{nr})“ auf {haupt}")
    return gruende


def auf_blocker_warten(ticket: str, takt: int) -> None:
    """Pollt alle ``takt`` Sekunden, bis #ticket frei ist. Kostet keine Token."""
    runde = 0
    while True:
        gruende = blocker_offen(ticket)
        if not gruende:
            if runde:
                log.info("Ticket #%s ist frei — Session startet.", ticket)
            return
        runde += 1
        log.info(
            "Ticket #%s wartet (%s) — nächste Prüfung in %d min [%s]",
            ticket,
            "; ".join(gruende),
            takt // 60,
            datetime.now().strftime("%H:%M"),
        )
        time.sleep(takt)


def auf_speicher_warten(
    konfig: dict,
    *,
    wer: str = "bau",
    pruefen=speicher.platz_frei,
    schlafen=time.sleep,
    takt_s: int = 60,
) -> int:
    """Warten, bis ``speicher.platz_frei`` frei meldet; Rückgabe = Zahl der Wartezyklen (#257 F3)."""
    return speicher.auf_platz_warten(konfig, wer=wer, pruefen=pruefen, schlafen=schlafen, takt_s=takt_s)


# --- Skill-Katalog ----------------------------------------------------------


def known_skill_names() -> set[str]:
    names: set[str] = set(BUILTIN_SKILLS)

    registry = read_json(CLAUDE_DIR / "toolbox" / "registry.json", {})
    for entry in registry.get("entries") or []:
        if entry.get("type") in ("skill", "command") and entry.get("name"):
            names.add(entry["name"])

    # Namespaced Commands: ~/.claude/commands/<ns>/<name>.md → "<ns>:<name>"
    commands_dir = CLAUDE_DIR / "commands"
    if commands_dir.is_dir():
        for md in commands_dir.glob("*.md"):
            names.add(md.stem)
        for md in commands_dir.glob("*/*.md"):
            names.add(f"{md.parent.name}:{md.stem}")

    # Plugin-Skills: plugins/cache/<marketplace>/<plugin>/<version>/skills/<skill>/SKILL.md
    # und plugins/cache/<marketplace>/<plugin>/<version>/commands/<name>.md
    cache = CLAUDE_DIR / "plugins" / "cache"
    if cache.is_dir():
        for skill_md in cache.glob("*/*/*/skills/*/SKILL.md"):
            plugin = skill_md.parents[3].name
            names.add(f"{plugin}:{skill_md.parent.name}")
        for cmd_md in cache.glob("*/*/*/commands/*.md"):
            plugin = cmd_md.parents[2].name
            names.add(f"{plugin}:{cmd_md.stem}")

    # Repo-Skills + Repo-Commands (.claude/commands/<name>.md → "<name>")
    for skill_md in (REPO / ".claude" / "skills").glob("*/SKILL.md"):
        names.add(skill_md.parent.name)
    for md in (REPO / ".claude" / "commands").glob("*.md"):
        names.add(md.stem)
    for md in (REPO / ".claude" / "commands").glob("*/*.md"):
        names.add(f"{md.parent.name}:{md.stem}")
    for skill_md in (CLAUDE_DIR / "skills").glob("*/SKILL.md"):
        names.add(skill_md.parent.name)

    return names


# --- MCP-Katalog ------------------------------------------------------------


def mcp_catalog() -> dict[str, dict]:
    """Reihenfolge: ~/.claude.json (global + dieses Projekt) → Repo .mcp.json → toolbox/mcp-archive.json."""
    catalog: dict[str, dict] = {}
    claude_json = read_json(HOME / ".claude.json", {})
    project_servers: dict[str, dict] = {}
    for key, project in (claude_json.get("projects") or {}).items():
        try:
            same = Path(key).resolve() == REPO
        except OSError:
            same = False
        if same:
            project_servers = project.get("mcpServers") or {}
    sources = (
        (claude_json.get("mcpServers") or {}),
        project_servers,
        (read_json(REPO / ".mcp.json", {}).get("mcpServers") or {}),
        (read_json(CLAUDE_DIR / "toolbox" / "mcp-archive.json", {}).get("servers") or {}),
    )
    for servers in sources:
        for name, definition in servers.items():
            catalog.setdefault(name, definition)
    return catalog


# --- Prompt -----------------------------------------------------------------


def build_kontext(entry: dict) -> str:
    lines = [f"- {item}" for item in (entry.get("files") or []) + (entry.get("docs") or [])]
    if not lines:
        return "(kein Kontext-Paket hinterlegt — Explore-Subagent model:sonnet nutzen, Rückgabe Pfad:Zeilen)"
    return "\n".join(lines)


def worktree_pfad(ticket: str) -> str:
    """Wohin der Worktree dieses Tickets gehört — Regel lebt in ``to_spawn.config`` (#204)."""
    return config.worktree_pfad(ticket, REPO)


def build_prompt(
    template: str,
    ticket: str,
    spec: str,
    title: str,
    kontext: str,
    konfig: dict | None = None,
) -> str:
    """Platzhalter der Vorlage füllen — auch die repo-neutralen (#257):
    ``{REPO}`` = owner/name, ``{HAUPTZWEIG}``, ``{CHECKPOINT_LABEL}`` aus der Konfig."""
    konfig = konfig if konfig is not None else config.lade(REPO)
    label = str((konfig.get("regularien") or {}).get("checkpoint_label") or "checkpoint:human")
    return (
        template.replace("{WT}", worktree_pfad(ticket))
        .replace("{N}", ticket)
        .replace("{S}", spec)
        .replace("{TITLE}", title)
        .replace("{KONTEXT}", kontext)
        .replace("{REPO}", GH_REPO)
        .replace("{HAUPTZWEIG}", gh.hauptzweig(REPO))
        .replace("{CHECKPOINT_LABEL}", label)
    )


# --- Staffel ----------------------------------------------------------------


def handoff_dirs(ticket: str) -> list[str]:
    """Nur der Ticket-Worktree — und dort beide Handoff-Orte.

    Der Hauptbaum bleibt bewusst außen vor: er ist mit Wächter- und
    Peer-Sessions geteilt, und ein fremdes ``git checkout``/``pull`` setzt die
    mtime einer Handoff-Datei auf jetzt. Der Hook würde das für eine frische
    Übergabe halten und eine arbeitende Session abschießen. Bau-Sessions
    arbeiten ohnehin nie im Hauptbaum.
    """
    worktree = Path(worktree_pfad(ticket)).expanduser()
    return [str(worktree / "docs" / "handoffs"), str(worktree / "docs")]


def staffel_hooks() -> dict:
    """Hook-Block für die Session-eigene ``settings.json``.

    ``Stop``: zuerst der Staffel-Hook, danach der Bau-Log-Hook des Skills (Token,
    Modell, Dauer je Runde); er gibt auch die Umzug-Anfrage des Wächters weiter (#212). ``SubagentStop``: Bau-Log-Zeile je Subagent (#204).
    """
    staffel = subprocess.list2cmdline([sys.executable, str(staffel_hook_pfad(REPO))])
    bau_log_stop = subprocess.list2cmdline([sys.executable, str(TO_SPAWN_CLI), "hook-stop"])
    bau_log_sub = subprocess.list2cmdline([sys.executable, str(TO_SPAWN_CLI), "hook-subagent-stop"])
    return {
        "Stop": [
            {
                "hooks": [
                    {"type": "command", "command": staffel},
                    {"type": "command", "command": bau_log_stop},
                ]
            }
        ],
        "SubagentStop": [{"hooks": [{"type": "command", "command": bau_log_sub}]}],
    }


def bau_log_umgebung(ticket: str, runde: int, start: float, effort: str | None) -> dict[str, str]:
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
    return umgebung


def staffel_umgebung(ticket: str, staffel_datei: Path, runde: int, fingerabdruck: str = "") -> dict[str, str]:
    return {
        "BAU_TICKET": ticket,
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
    """Kurzer Weiter-Text, wenn der Aufpasser eine Session mit ``--resume`` fortsetzt."""
    return (
        f"Aufpasser: Weiter mit Ticket #{ticket} genau dort, wo du warst. "
        "Ticket offen? weiterbauen; nichts zu tun? ScheduleWakeup."
    )


def session_id_setzen(
    cmd: list[str], out: Path, sid: str | None = None, ticket: str = "", runde: int = 1
) -> str:
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
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
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
        log.error(
            "Umzug-Handoff nicht lesbar: --umzug erwartet <branch>@<sha>:<pfad>, bekam %r.", ref
        )
        sys.exit(2)
    geholt = _git_lauf("fetch", "-q", "origin", branch)
    if geholt.returncode != 0:
        log.error(
            "Umzug-Handoff nicht lesbar: git fetch origin %s scheiterte (Exit %s: %s) — "
            "nichts gestartet.",
            branch,
            geholt.returncode,
            (geholt.stderr or geholt.stdout).strip()[:200] or "keine Meldung",
        )
        sys.exit(2)
    if sha:
        enthalten = _git_lauf("merge-base", "--is-ancestor", sha, f"origin/{branch}")
        if enthalten.returncode != 0:
            log.error(
                "Umzug-Handoff nicht lesbar: Commit %s ist nicht in origin/%s (%s) — "
                "nichts gestartet.",
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
    """Alte Umzug-Anfragen des Wächters löschen; Rückgabe: Pfad der Anfrage-Datei."""
    anfrage = REPO / ".to-spawn" / f"umzug-anfrage-{ticket}"
    anfrage.unlink(missing_ok=True)
    Path(f"{anfrage}.laeuft").unlink(missing_ok=True)
    return anfrage


def starte_session(cmd: list[str], umzug_datei: Path) -> tuple[int, dict | None]:
    """Interaktiv: stdin/stdout durchreichen, dabei auf die Umzug-Datei achten (#212).

    Taucht ``umzug_datei`` auf, wird die Session beendet (terminate, nach 20 s kill).
    .cmd-Shim auf Windows: ``shell=True``-Notnagel steckt in ``umzug.starte_mit_umzug_wache``.
    """
    return umzug.starte_mit_umzug_wache(cmd, umzug_datei)


# --- Main -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Schlanke Claude-Session für ein Ticket.")
    parser.add_argument("ticket", type=int, help="GitHub-Issue-Nummer")
    parser.add_argument("--dry-run", action="store_true", help="nur Zusammenfassung + Befehl")
    parser.add_argument("--model", help="Claude-Modell (überschreibt Manifest)")
    parser.add_argument("--print-prompt", action="store_true", help="nur den Prompt ausgeben")
    parser.add_argument("--sofort", action="store_true", help="nicht auf Blocker warten (Session prüft selbst)")
    parser.add_argument("--takt", type=int, default=600, help="Sekunden zwischen zwei Blocker-Prüfungen (600)")
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
    args = parser.parse_args()
    ticket = str(args.ticket)
    # GitHub-Slug verbindlich (#257 F5): kein GitHub-Origin → Exit 2 mit Grund, kein Rückfall.
    global GH_REPO
    GH_REPO = gh_repo_ermitteln(REPO)
    if args.probesitz:
        args.sofort = True
        if not staffel_hook_pfad(REPO).is_file():
            log.warning(
                "Staffel-Hook %s fehlt (weder im Repo noch im Skill) — Punkt 6 (Handoff → "
                "Folge-Session) kann nicht grün werden.",
                staffel_hook_pfad(REPO),
            )
    if not args.dry_run:  # Probelauf ohne Seiteneffekte (#205)
        config.sicherstellen(REPO)
    # Fortsetzen nur mit vorhandenem Transkript (#236, F5): ohne die Datei startete
    # ``claude --resume`` eine frische Session — genau das darf der Aufpasser nie.
    if args.resume and not args.dry_run:
        transkript = transkript_ordner(Path.cwd()) / f"{args.resume}.jsonl"
        if not transkript.is_file():
            log.error("--resume %s: Transkript %s fehlt — kein Start.", args.resume, transkript)
            return 2

    default = load_default(Path(_SKILL) / "repo-scripts" / "_default.json" if args.probesitz else None)
    found = None if args.probesitz else find_manifest(ticket)
    if args.probesitz:
        # Wegwerf-Ticket: kein Manifest, kein gh — der Auftrag ist fest (#214).
        entry = {}
        spec, title = "probesitz", f"Probesitz-Wegwerf-Session #{ticket}"
    elif found:
        manifest, entry = found
        spec = str(manifest.get("spec", "?"))
        title = entry.get("title") or f"Ticket {ticket}"
    else:
        log.warning("Kein Manifest führt Ticket #%s — nur Core, Spec aus gh.", ticket)
        entry = {}
        title, spec_gh = ticket_from_gh(ticket)
        if spec_gh is None:
            log.error("Spec-Nr. nicht aus Issue-Body #%s ermittelbar — Abbruch.", ticket)
            return 2
        spec = spec_gh

    prompt = (
        probesitz.WEGWERF_PROMPT.format(ticket=ticket)
        if args.probesitz
        else build_prompt(
            default["prompt_template"], ticket, spec, title, build_kontext(entry), config.lade(REPO)
        )
    )
    erster_prompt = prompt
    if args.umzug:
        branch, _pfad, handoff_text = umzug_handoff_lesen(args.umzug)
        erster_prompt = umzug_prompt(prompt, handoff_text, branch, ticket)
    if args.print_prompt:
        print(erster_prompt)
        return 0

    # Skills
    whitelist = list(dict.fromkeys(default.get("core_skills", []) + (entry.get("skills") or [])))
    known = known_skill_names()
    for name in whitelist:
        if name not in known:
            log.warning("Skill unbekannt, trotzdem 'on': %s", name)
    overrides = {name: "off" for name in sorted(known - set(whitelist))}
    overrides.update({name: "on" for name in whitelist})

    # MCPs
    mcp_wanted = list(dict.fromkeys(default.get("core_mcp", []) + (entry.get("mcp") or [])))
    chrome = CHROME_MCP in mcp_wanted
    catalog = mcp_catalog()
    mcp_servers: dict[str, dict] = {}
    for name in mcp_wanted:
        if name == CHROME_MCP:
            continue
        if name in catalog:
            mcp_servers[name] = catalog[name]
        else:
            log.warning("MCP unauflösbar, übersprungen: %s", name)
    # Pflicht-MCP context-mode (#237): ``--strict-mcp-config`` sperrt das Plugin-MCP aus,
    # also steht es hier selbst in der mcp.json — kein Manifest kann es abwählen.
    try:
        mcp_servers.update(context_mode.server_definition(CLAUDE_DIR))
    except context_mode.ContextModeFehlt as fehler:
        log.error("%s", fehler)
        return 2

    # Temp-Dateien
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "Temp"
    if not out.is_dir():
        out = Path(tempfile.gettempdir())
    out = out / f"{GH_REPO.rsplit('/', 1)[-1]}-bau" / f"{ticket}-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    settings_path = out / "settings.json"
    mcp_path = out / "mcp.json"
    staffel_datei = out / "staffel.json"
    settings_path.write_text(
        json.dumps(
            {"skillOverrides": overrides, "hooks": staffel_hooks()},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    mcp_path.write_text(
        json.dumps({"mcpServers": mcp_servers}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    (out / "prompt.txt").write_text(prompt, encoding="utf-8")

    # Vorrang: --model > Repo-Konfig ``modelle.ticket`` (#205) > _default.json.
    konfig = config.lade(REPO)
    model = args.model or konfig.get("modelle", {}).get("ticket") or default.get("model")
    effort = konfig.get("effort", {}).get("ticket")
    claude = shutil.which("claude") or "claude"
    cmd = [
        claude,
        # Probesitz (#214): Print-Modus, die Session endet ohne Terminal von selbst. Ohne
        # bypassPermissions (frisches Setup) verweigert ``-p`` jedes Werkzeug still — deshalb
        # die nötigen Rechte ausdrücklich mitgeben. ``--allowedTools`` ist variadisch: das
        # nächste Flag muss direkt folgen, sonst frisst es den Prompt am Ende.
        *(["-p", "--allowedTools", *PROBESITZ_RECHTE] if args.probesitz else []),
        "--settings",
        str(settings_path),
        "--mcp-config",
        str(mcp_path),
        "--strict-mcp-config",
    ]
    if not chrome:
        cmd.append("--no-chrome")
    if model:
        cmd += ["--model", model]
    # Feste Gesprächs-ID (#236): der Aufpasser liest sie aus dem Prozessbaum und setzt
    # die Session nach einer Sicherung mit ``--resume`` fort.
    if args.resume:
        cmd += ["--resume", args.resume]
        erster_prompt = resume_prompt(ticket)
    else:
        cmd += ["--session-id", str(uuid.uuid4())]
    cmd.append(erster_prompt)

    off_count = sum(1 for v in overrides.values() if v == "off")
    log.info("Ticket #%s · Spec #%s · %s", ticket, spec, title)
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
        cmd = praefix + cmd
        print("\nBefehl:")
        print(
            " ".join(f'"{c}"' if " " in c or "\n" in c else c for c in cmd[:-1]),
            '"<prompt>"',
        )
        return 0

    if not args.sofort and not args.umzug and not args.resume:
        auf_blocker_warten(ticket, max(60, args.takt))
    # Speicher-Schutz (#257 Paket B, Fixrunde 1 F3), die EINE Stelle vor dem Prozessstart
    # (gilt für Erststart, --resume und --umzug): RAM knapp oder Obergrenze an
    # Claude-Sessions erreicht → warten statt Exit 5, damit das Fenster und der Grund bleiben.
    auf_speicher_warten(konfig, wer=f"bau {ticket}")
    # Sandbox erst jetzt (#210): Worktree entsteht vom frischen origin-Stand (nest holt ihn).
    praefix = nest.sandbox_start(konfig, worktree_pfad(ticket), REPO)
    if praefix is None:
        return 2
    cmd = praefix + cmd

    # Vertrauens-Dialog vorab bestätigen (#257): ohne Terminal-Antwort hinge die Session
    # an „Do you trust the files in this folder?“ — Fehler nur als Warnung.
    vertrauen.still_sicherstellen(Path(worktree_pfad(ticket)).expanduser(), REPO)

    # Aus einer Claude-Session gestartet erben Kind-Sessions die Markierung
    # CLAUDE_CODE_CHILD_SESSION und speichern kein Transkript (kein Resume nach
    # Absturz, Beleg 17.09.2026). Persistenz deshalb ausdrücklich erzwingen.
    os.environ.pop("CLAUDE_CODE_CHILD_SESSION", None)
    # Eine Session im Worktree darf nicht das Repo des Launchers erben (#205).
    os.environ.pop("TO_SPAWN_REPO", None)
    os.environ["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] = "1"

    # Umzug (#212): bau.py beendet die Session, sobald ``umzug.json`` auftaucht; der
    # Stop-Hook ``hook-umzug`` liest die Anfrage des Wächters.
    umzug_datei = out / "umzug.json"
    os.environ["BAU_UMZUG_DATEI"] = str(umzug_datei)
    os.environ["BAU_UMZUG_ANFRAGE"] = str(umzug_anfragen_aufraeumen(ticket))

    if args.probesitz:
        # ``claude -p`` liest eine Pipe auf stdin bis zum Ende — ohne Terminal würde die
        # Session hängen. Deshalb stdin fest auf /dev/null (#214).
        leer = os.open(os.devnull, os.O_RDONLY)
        os.dup2(leer, sys.stdin.fileno())
        os.close(leer)

    # Staffel-Schleife: jede Runde eine eigene Session, Übergabe über die Staffel-Datei.
    staffel_datei.unlink(missing_ok=True)
    if sys.platform != "linux":
        log.warning("Staffel braucht /proc — auf %s bleibt die Übergabe von Hand.", sys.platform)
    runde = 1
    fingerabdruck = ""
    session_id_setzen(cmd, out, ticket=ticket, runde=runde)
    while True:
        umgebung = staffel_umgebung(ticket, staffel_datei, runde, fingerabdruck)
        umgebung.update(
            bau_log_umgebung(ticket, runde, float(umgebung["BAU_SESSION_START"]), effort)
        )
        os.environ.update(umgebung)
        (out / f"prompt-runde{runde}.txt").write_text(cmd[-1], encoding="utf-8")
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
        uebergabe = staffel_uebergabe(staffel_datei)
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
        log.info(
            "Staffel-Runde %d · Handoff %s · Vorsession endete mit Exit %s",
            runde,
            handoff.name,
            code,
        )
        cmd[-1] = staffel_prompt(prompt, handoff, runde)
        session_id_setzen(cmd, out, str(uuid.uuid4()), ticket=ticket, runde=runde)


if __name__ == "__main__":
    sys.exit(main())
