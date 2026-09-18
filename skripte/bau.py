"""bau — schlanke Claude-Code-Session für genau ein Ticket.

Aufruf: ``python scripts/bau.py <N> [--dry-run] [--model <m>] [--print-prompt] [--sofort] [--takt <s>]``

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
from datetime import datetime
from pathlib import Path

# Skill-Wurzel in sys.path, damit ``to_spawn.config`` (Repo-Wurzel, Konfig) importierbar ist (#205).
_SKILL = str(Path(__file__).resolve().parent.parent)
if _SKILL not in sys.path:
    sys.path.insert(0, _SKILL)
from to_spawn import config  # noqa: E402

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
STAFFEL_HOOK = REPO / "scripts" / "hooks" / "staffel_stop.py"
#: CLI des Skills (Bau-Log-Hooks, #204) — dieselbe Skill-Wurzel wie oben in sys.path.
TO_SPAWN_CLI = Path(_SKILL) / "to_spawn.py"
STAFFEL_MAX_DEFAULT = 8
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


def load_default() -> dict:
    path = MANIFEST_DIR / "_default.json"
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


def repo_aus_origin(fallback: str) -> str:
    """``owner/name`` aus ``git remote get-url origin`` (GitHub, https oder ssh); sonst ``fallback``.

    Damit läuft dasselbe Skript in jedem Repo mit GitHub-Origin — nichts hart verdrahtet.
    """
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        return fallback
    m = re.search(r"github\.com[:/]([^/]+/[^/\s]+?)(?:\.git)?$", url)
    return m.group(1) if m else fallback


GH_REPO = repo_aus_origin("Shavy72/duoplus-management")


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
    seinen Commit ``(#<Blocker>)`` auf ``origin/master`` haben — sonst baut die
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
    for d in deps:
        nr, state = d.get("number"), d.get("state")
        if state != "closed":
            gruende.append(f"#{nr} offen")
            continue
        subprocess.run(["git", "fetch", "-q", "origin"], cwd=REPO, check=False)
        found = subprocess.run(
            ["git", "log", "origin/master", "--oneline", "--fixed-strings", f"--grep=(#{nr})"],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        ).stdout.strip()
        if not found:
            gruende.append(f"#{nr} zu, aber kein Commit „(#{nr})“ auf origin/master")
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
    return config.worktree_pfad(ticket)


def build_prompt(template: str, ticket: str, spec: str, title: str, kontext: str) -> str:
    return (
        template.replace("{WT}", worktree_pfad(ticket))
        .replace("{N}", ticket)
        .replace("{S}", spec)
        .replace("{TITLE}", title)
        .replace("{KONTEXT}", kontext)
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
    Modell, Dauer je Runde). ``SubagentStop``: Bau-Log-Zeile je Subagent (#204).
    """
    staffel = subprocess.list2cmdline([sys.executable, str(STAFFEL_HOOK)])
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

    ``TO_SPAWN_LOG_REPO`` zeigt auf den Ticket-Worktree; existiert er noch nicht,
    schreiben die Hooks nichts (nie in den geteilten Hauptbaum).
    """
    umgebung = {
        "TO_SPAWN_TICKET": ticket,
        "TO_SPAWN_START": str(start),
        "TO_SPAWN_STAFFEL": str(runde),
        "TO_SPAWN_LOG_REPO": str(Path(worktree_pfad(ticket)).expanduser()),
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


def starte_session(cmd: list[str]) -> int:
    """Interaktiv: stdin/stdout durchreichen. .cmd-Shim auf Windows braucht shell=True als Notnagel."""
    try:
        return subprocess.run(cmd, check=False).returncode
    except OSError:
        return subprocess.run(subprocess.list2cmdline(cmd), shell=True, check=False).returncode


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
    args = parser.parse_args()
    ticket = str(args.ticket)
    if not args.dry_run:  # Probelauf ohne Seiteneffekte (#205)
        config.sicherstellen(REPO)

    default = load_default()
    found = find_manifest(ticket)
    if found:
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

    prompt = build_prompt(default["prompt_template"], ticket, spec, title, build_kontext(entry))
    if args.print_prompt:
        print(prompt)
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
    cmd.append(prompt)

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
        print("\nBefehl:")
        print(
            " ".join(f'"{c}"' if " " in c or "\n" in c else c for c in cmd[:-1]),
            '"<prompt>"',
        )
        return 0

    if not args.sofort:
        auf_blocker_warten(ticket, max(60, args.takt))

    # Aus einer Claude-Session gestartet erben Kind-Sessions die Markierung
    # CLAUDE_CODE_CHILD_SESSION und speichern kein Transkript (kein Resume nach
    # Absturz, Beleg 17.09.2026). Persistenz deshalb ausdrücklich erzwingen.
    os.environ.pop("CLAUDE_CODE_CHILD_SESSION", None)
    # Eine Session im Worktree darf nicht das Repo des Launchers erben (#205).
    os.environ.pop("TO_SPAWN_REPO", None)
    os.environ["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] = "1"

    # Staffel-Schleife: jede Runde eine eigene Session, Übergabe über die Staffel-Datei.
    staffel_datei.unlink(missing_ok=True)
    if sys.platform != "linux":
        log.warning("Staffel braucht /proc — auf %s bleibt die Übergabe von Hand.", sys.platform)
    runde = 1
    fingerabdruck = ""
    while True:
        umgebung = staffel_umgebung(ticket, staffel_datei, runde, fingerabdruck)
        umgebung.update(
            bau_log_umgebung(ticket, runde, float(umgebung["BAU_SESSION_START"]), effort)
        )
        os.environ.update(umgebung)
        (out / f"prompt-runde{runde}.txt").write_text(cmd[-1], encoding="utf-8")
        code = starte_session(cmd)
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


if __name__ == "__main__":
    sys.exit(main())
