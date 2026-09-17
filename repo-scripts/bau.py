"""bau — schlanke Claude-Code-Session für genau ein Ticket.

Aufruf: ``python scripts/bau.py <N> [--dry-run] [--model <m>] [--print-prompt] [--sofort] [--takt <s>]``

Liest das Ticket-Manifest unter ``docs/agents/manifests/*.json`` (SSOT-Schema siehe
``docs/agents/kontext-manifest.md``), schaltet alle nicht benötigten Skills
per ``--settings skillOverrides`` ab, baut eine ``--mcp-config`` nur mit den
gelisteten MCPs und startet ``claude`` mit dem Loop-Prompt.
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

# Windows-Konsole ist cp1252 — Umlaute/Pfeile im Prompt brauchen UTF-8.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("bau")

REPO = Path(__file__).resolve().parent.parent
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
    """Wohin der Worktree dieses Tickets gehört — Windows wie bisher, Linux unter ``$BAU_WT_DIR``."""
    if sys.platform == "win32":
        return f"C:/dev/wt-{ticket}"
    basis = os.environ.get("BAU_WT_DIR") or "~/wt"
    return f"{Path(basis).expanduser().as_posix().rstrip('/')}/wt-{ticket}"


def build_prompt(template: str, ticket: str, spec: str, title: str, kontext: str) -> str:
    return (
        template.replace("{WT}", worktree_pfad(ticket))
        .replace("{N}", ticket)
        .replace("{S}", spec)
        .replace("{TITLE}", title)
        .replace("{KONTEXT}", kontext)
    )


# --- Main -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Schlanke Claude-Session für ein Ticket.")
    parser.add_argument("ticket", type=int, help="GitHub-Issue-Nummer")
    parser.add_argument("--dry-run", action="store_true", help="nur Zusammenfassung + Befehl")
    parser.add_argument("--model", help="Claude-Modell (überschreibt Manifest)")
    parser.add_argument("--print-prompt", action="store_true", help="nur den Prompt ausgeben")
    parser.add_argument("--sofort", action="store_true", help="nicht auf Blocker warten (Session prüft selbst)")
    parser.add_argument("--takt", type=int, default=600, help="Sekunden zwischen zwei Blocker-Prüfungen (600)")
    args = parser.parse_args()
    ticket = str(args.ticket)

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
    settings_path.write_text(
        json.dumps({"skillOverrides": overrides}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    mcp_path.write_text(
        json.dumps({"mcpServers": mcp_servers}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    (out / "prompt.txt").write_text(prompt, encoding="utf-8")

    model = args.model or default.get("model")
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

    # Interaktiv: stdin/stdout durchreichen. .cmd-Shim auf Windows braucht shell=True als Notnagel.
    # Aus einer Claude-Session gestartet erben Kind-Sessions die Markierung
    # CLAUDE_CODE_CHILD_SESSION und speichern kein Transkript (kein Resume nach
    # Absturz, Beleg 17.09.2026). Persistenz deshalb ausdrücklich erzwingen.
    os.environ.pop("CLAUDE_CODE_CHILD_SESSION", None)
    os.environ["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] = "1"
    try:
        return subprocess.run(cmd, check=False).returncode
    except OSError:
        return subprocess.run(subprocess.list2cmdline(cmd), shell=True, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
