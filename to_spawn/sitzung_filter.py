"""Gemeinsamer Skill- und MCP-Filter für Bau- und Aufseher-Sessions (#412).

Einzige Quelle (SSOT) für ``skripte/bau.py``, ``skripte/wache.py`` und
``to_spawn/start_last.py``:

* :data:`PFLICHT_SKILLS` — Review-Skills, die kein Manifest abwählen darf.
* :data:`BUILTIN_SKILLS` — Claude-Code-Builtins und Plugin-Befehle ohne eigene Datei.
* :data:`AUFSEHER_SKILLS` — Skills, die der Bau-Aufseher für Loop, Umzug, Ablösung
  und Abschluss-Paket braucht.
* :func:`bekannte_skills` — sammelt alle installierten Skills/Befehle aus Repo,
  globalem Claude-Ordner, Plugin-Cache und ``toolbox/registry.json``.
* :func:`skill_overrides` / :func:`aufseher_skill_auswahl` — erzeugt Whitelist und
  ``skillOverrides`` (alle ungenutzten Skills auf ``off``).
* :func:`mcp_katalog` / :func:`mcp_auswahl` / :func:`aufseher_mcp_auswahl` — löst
  gewünschte MCP-Server für ``--mcp-config`` + ``--strict-mcp-config`` auf.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("to_spawn.sitzung_filter")

#: Pflicht-Skills: der globale Review-Stop-Hook (``~/.claude/hooks/review/stop.py``) fordert
#: „Skill review-dirigent ausführen“, der Dirigent ruft diese Skills per Skill-Werkzeug.
#: Kein Manifest und keine Konfig kann sie abwählen — wie ``context-mode`` bei den MCPs.
PFLICHT_SKILLS: tuple[str, ...] = (
    "review-dirigent",
    "code-review",
    "security-review",
    "sentry-security-review",
)

#: Builtins, die Claude Code ohne Datei mitbringt (per ``skillOverrides`` abschaltbar).
BUILTIN_SKILLS: tuple[str, ...] = (
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
)

#: Skills, die der Bau-Aufseher direkt braucht (Loop, Umzug, Ablöse-SOP, Abschluss-Paket,
#: Fehlerdiagnose und Commits). Zusammen mit :data:`PFLICHT_SKILLS`, ``skills.pflicht``
#: und optional ``waechter.skills`` bleiben alle übrigen Skills per ``skillOverrides`` aus (#412).
AUFSEHER_SKILLS: tuple[str, ...] = (
    "loop",
    "to-spawn-of",
    "respawn",
    "handoff",
    "rundschau",
    "mp-retro",
    "german-umlauts",
    "verify-hard",
    "diagnosing-bugs",
    "prp-commit",
)

CHROME_MCP = "claude-in-chrome"


def _lies_json(pfad: Path, *, logger: logging.Logger | None = None) -> dict[str, Any]:
    if not pfad.is_file():
        return {}
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        (logger or log).warning("JSON unlesbar, übersprungen: %s (%s)", pfad, fehler)
        return {}
    return daten if isinstance(daten, dict) else {}


def bekannte_skills(
    *,
    repo: Path,
    claude_dir: Path,
    basis: Iterable[str] | None = None,
    logger: logging.Logger | None = None,
) -> set[str]:
    """Alle bekannten Skill- und Befehlsnamen aus Builtins, Registry, Plugins, global und Repo."""
    namen: set[str] = set(basis) if basis is not None else (set(BUILTIN_SKILLS) | set(PFLICHT_SKILLS))

    registry = _lies_json(claude_dir / "toolbox" / "registry.json", logger=logger)
    for eintrag in registry.get("entries") or []:
        if isinstance(eintrag, dict) and eintrag.get("type") in ("skill", "command") and eintrag.get("name"):
            namen.add(str(eintrag["name"]))

    # Namespaced Commands: ~/.claude/commands/<ns>/<name>.md → "<ns>:<name>"
    commands_dir = claude_dir / "commands"
    if commands_dir.is_dir():
        for md in commands_dir.glob("*.md"):
            namen.add(md.stem)
        for md in commands_dir.glob("*/*.md"):
            namen.add(f"{md.parent.name}:{md.stem}")

    # Plugin-Skills: plugins/cache/<marketplace>/<plugin>/<version>/skills/<skill>/SKILL.md
    # und plugins/cache/<marketplace>/<plugin>/<version>/commands/<name>.md
    cache = claude_dir / "plugins" / "cache"
    if cache.is_dir():
        for skill_md in cache.glob("*/*/*/skills/*/SKILL.md"):
            plugin = skill_md.parents[3].name
            namen.add(f"{plugin}:{skill_md.parent.name}")
        for cmd_md in cache.glob("*/*/*/commands/*.md"):
            plugin = cmd_md.parents[2].name
            namen.add(f"{plugin}:{cmd_md.stem}")

    # Repo-Skills + Repo-Commands (.claude/commands/<name>.md → "<name>")
    for skill_md in (repo / ".claude" / "skills").glob("*/SKILL.md"):
        namen.add(skill_md.parent.name)
    for md in (repo / ".claude" / "commands").glob("*.md"):
        namen.add(md.stem)
    for md in (repo / ".claude" / "commands").glob("*/*.md"):
        namen.add(f"{md.parent.name}:{md.stem}")
    for skill_md in (claude_dir / "skills").glob("*/SKILL.md"):
        namen.add(skill_md.parent.name)

    return namen


def skill_overrides(
    whitelist: Iterable[str],
    bekannt: set[str],
    *,
    geprueft: Iterable[str] = (),
    logger: logging.Logger | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Whitelist bereinigen und ``skillOverrides`` bauen (alle übrigen ``bekannt`` auf ``off``).

    Gegen ``bekannt`` geprüft und bei Fehlen gewarnt werden nur die Namen aus ``geprueft``
    (vom Repo bzw. Ticket/Konfig angeforderte Skills — fremde Skill-Vorgaben erzeugen in
    einem fremden Repo keine Warnung, #413).
    """
    ziel_log = logger or log
    liste = list(dict.fromkeys(s.strip() for s in whitelist if isinstance(s, str) and s.strip()))
    for name in dict.fromkeys(s.strip() for s in geprueft if isinstance(s, str) and s.strip()):
        if name not in bekannt:
            ziel_log.warning("Skill unbekannt, trotzdem 'on': %s", name)
    overrides = {name: "off" for name in sorted(bekannt - set(liste))}
    overrides.update({name: "on" for name in liste})
    return liste, overrides


def waechter_liste(
    konfig: Mapping[str, Any],
    feld: str,
    *,
    logger: logging.Logger | None = None,
) -> list[str]:
    """Optionale Namensliste ``waechter.<feld>`` (z. B. ``skills``/``mcp``) aus der Repo-Konfig."""
    gruppe = konfig.get("waechter")
    if not isinstance(gruppe, Mapping):
        return []
    werte = gruppe.get(feld)
    if werte is None:
        return []
    if not isinstance(werte, list):
        (logger or log).warning(
            "Konfig-Wert 'waechter.%s' ist keine Liste (%s) — gilt als leer",
            feld,
            type(werte).__name__,
        )
        return []
    ergebnis: list[str] = []
    for eintrag in werte:
        if isinstance(eintrag, str) and eintrag.strip():
            name = eintrag.strip()
            if name not in ergebnis:
                ergebnis.append(name)
    return ergebnis


def aufseher_skill_auswahl(
    konfig: Mapping[str, Any],
    *,
    repo: Path | None = None,
    claude_dir: Path | None = None,
    known: set[str] | None = None,
    logger: logging.Logger | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Whitelist und ``skillOverrides`` für den Bau-Aufseher (#412).

    Aktiv (``on``) sind :data:`AUFSEHER_SKILLS`, optionale ``waechter.skills`` aus der
    Repo-Konfig, die Repo-Skills (``skills.pflicht``) und :data:`PFLICHT_SKILLS`.
    """
    repo_erwartet = config.repo_skills(konfig)
    waechter_extra = waechter_liste(konfig, "skills", logger=logger)
    geprueft = list(dict.fromkeys(repo_erwartet + waechter_extra))
    whitelist = list(dict.fromkeys([*AUFSEHER_SKILLS, *waechter_extra, *repo_erwartet, *PFLICHT_SKILLS]))
    if known is not None:
        bekannt = known
    else:
        if repo is None or claude_dir is None:
            raise ValueError("repo und claude_dir sind Pflicht, wenn known nicht übergeben wird")
        bekannt = bekannte_skills(repo=repo, claude_dir=claude_dir, logger=logger)
    return skill_overrides(whitelist, bekannt, geprueft=geprueft, logger=logger)


def aufseher_settings(overrides: Mapping[str, str]) -> dict[str, Any]:
    """Inhalt der Aufseher-``settings.json`` (nur ``skillOverrides``, keine Bau-Hooks)."""
    return {"skillOverrides": dict(overrides)}


def mcp_katalog(
    *,
    repo: Path,
    home: Path,
    claude_dir: Path,
    logger: logging.Logger | None = None,
) -> dict[str, dict[str, Any]]:
    """MCP-Server-Katalog: ``~/.claude.json`` (global + Projekt) → ``.mcp.json`` → ``mcp-archive.json``."""
    ziel_repo = repo.resolve()
    katalog: dict[str, dict[str, Any]] = {}
    claude_json = _lies_json(home / ".claude.json", logger=logger)
    project_servers: dict[str, Any] = {}
    for key, project in (claude_json.get("projects") or {}).items():
        if not isinstance(project, dict):
            continue
        try:
            gleich = Path(key).resolve() == ziel_repo
        except OSError:
            gleich = False
        if gleich:
            project_servers = project.get("mcpServers") or {}
    quellen = (
        claude_json.get("mcpServers") or {},
        project_servers,
        _lies_json(ziel_repo / ".mcp.json", logger=logger).get("mcpServers") or {},
        _lies_json(claude_dir / "toolbox" / "mcp-archive.json", logger=logger).get("servers") or {},
    )
    for servers in quellen:
        if not isinstance(servers, dict):
            continue
        for name, definition in servers.items():
            if isinstance(definition, dict):
                katalog.setdefault(str(name), definition)
    return katalog


def mcp_auswahl(
    gewuenscht: Iterable[str],
    katalog: Mapping[str, dict[str, Any]],
    *,
    logger: logging.Logger | None = None,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Löst gewünschte MCP-Namen gegen ``katalog`` auf; Rückgabe ``(mcp_servers, chrome)``."""
    ziel_log = logger or log
    namen = list(dict.fromkeys(s.strip() for s in gewuenscht if isinstance(s, str) and s.strip()))
    chrome = CHROME_MCP in namen
    mcp_servers: dict[str, dict[str, Any]] = {}
    for name in namen:
        if name == CHROME_MCP:
            continue
        if name in katalog:
            mcp_servers[name] = katalog[name]
        else:
            ziel_log.warning("MCP unauflösbar, übersprungen: %s", name)
    return mcp_servers, chrome


def aufseher_mcp_auswahl(
    konfig: Mapping[str, Any],
    *,
    repo: Path,
    home: Path,
    claude_dir: Path,
    katalog: Mapping[str, dict[str, Any]] | None = None,
    logger: logging.Logger | None = None,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Gewünschte Zusatz-MCPs des Aufsehers aus ``waechter.mcp`` (ohne Pflicht-MCP ``context-mode``)."""
    gewuenscht = waechter_liste(konfig, "mcp", logger=logger)
    kat = katalog if katalog is not None else mcp_katalog(repo=repo, home=home, claude_dir=claude_dir, logger=logger)
    return mcp_auswahl(gewuenscht, kat, logger=logger)
