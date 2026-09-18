"""Setup-Wizard Teil 2 (#209): Werkzeug-Inventur.

Liest Projekt, Session-Historie und Werkzeug-Katalog (Skills, Plugin-Skills,
Agenten, MCP-Server) echt aus und ordnet jedes Werkzeug genau einer von zwölf
Kategorien zu. Ergebnis ist eine Abwahl-Liste: Standard = alles an, der Nutzer
wählt ab statt an. Fehlende Unterbauten (codex, gemini, ffmpeg, adb, gh) werden
eigene Setup-Zeilen mit konkretem Fehlgrund; leere Kategorien und genutzte, aber
nicht installierte Werkzeuge werden „tote Winkel“.

Einordnung (Reihenfolge ist Absicht):
1. Namens-Durchgang: die Regeln aus ``REGELN`` in Tabellen-Reihenfolge nur gegen
   den Namen. Der Name ist das stärkste Signal (``mp-tdd`` ist ein Test-Werkzeug,
   auch wenn die Beschreibung von „Features bauen“ spricht).
2. Beschreibungs-Durchgang: dieselben Regeln gegen die Beschreibung.
3. Rückfall: Name steht als eigenes Wort in der ``CLAUDE.md`` des Repos →
   ``projekt``, sonst ``bauen``.

Eingebaute Skills und Agenten von Claude Code (``EINGEBAUTE_SKILLS``,
``EINGEBAUTE_AGENTEN``) laufen nicht durch die Regeln, sie haben eine feste Kategorie.

Innerhalb eines Durchgangs gewinnt die erste Regel der Tabelle. Deshalb stehen
die engen Regeln (Projekt-Wörter, Zweitmeinung, Sicherheit, Testen) vor den
breiten (Design, Deploy, Steuerung). Schlüsselwörter ohne Leer-/Bindestrich
treffen Wort-Anfänge (``test`` trifft „testing“, ``ui`` trifft nicht „build“),
Schlüsselwörter mit Leer-/Bindestrich treffen als Teilzeichenkette.

``.env``: gelesen werden nur Schlüssel-NAMEN (``[A-Za-z_][A-Za-z0-9_]*``, optional mit
``export``) mit nicht leerem Wert; ``KEY= # Kommentar`` und reine Leerzeichen gelten als
nicht gesetzt, Folgezeilen mehrzeiliger Werte werden übersprungen. Der Wert wird nur
intern auf Leere geprüft, nie gespeichert und nie ausgegeben.

``werkzeuge.json`` liest heute noch niemand beim Start — die Übergabe an die
Bau-Session (z. B. ``--disallowedTools``) folgt im Nest-Ticket #210.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("to_spawn.inventur")

AUSGABE_PFAD = Path(".to-spawn") / "werkzeuge.json"
HISTORIE_VORGABE = 200
EXIT_UNBEKANNT = 2
EXIT_KAPUTT = 4
NICHT_GESPEICHERT = "nicht gespeichert – `--schreiben` fehlt"

# Schlüssel → Anzeige, feste Reihenfolge.
KATEGORIEN: dict[str, str] = {
    "bauen": "Bauen",
    "testen": "Testen/Beweisen",
    "design": "Design/Frontend",
    "recherche": "Recherche",
    "medien": "Medien",
    "deploy": "Deploy/Betrieb",
    "projekt": "Projekt-Spezial",
    "sicherheit": "Sicherheit/Secrets",
    "kontext": "Kontext/Gedächtnis",
    "steuerung": "Steuerung/Meldung",
    "sehen": "Sehen/Verstehen",
    "zweitmeinung": "Zweitmeinung/Sonderfähigkeiten",
}

# Fester Vorschlag je Kategorie, wenn sie leer ist: (Text, Beispiel-Namen).
# Beispiele, die schon installiert sind, fallen beim Ausgeben weg.
VORSCHLAEGE: dict[str, tuple[str, tuple[str, ...]]] = {
    "bauen": ("einen Bau-Agenten oder Bau-Skill installieren", ("executor-opus", "implement")),
    "testen": ("einen TDD-/Beweis-Skill installieren", ("mp-tdd", "verify-hard")),
    "design": ("einen Design-Skill für Oberflächen-Arbeit installieren", ("frontend-design",)),
    "recherche": ("einen Such-MCP einrichten", ("perplexity", "firecrawl")),
    "medien": ("einen Medien-Skill installieren", ("ffmpeg-production",)),
    "deploy": ("einen Deploy-/Git-Skill installieren", ("git-workflow",)),
    "projekt": ("einen Skill mit dem Projekt-Wissen (API, Abläufe) anlegen", ()),
    "sicherheit": (
        "einen Secrets-Skill (z. B. Bitwarden-Abruf) einrichten",
        ("security-review",),
    ),
    "kontext": ("ein Gedächtnis-Werkzeug installieren", ("handoff", "graphify", "context-mode")),
    "steuerung": ("ein Steuer-/Melde-Werkzeug (z. B. Telegram-Meldung) einrichten", ("to-spawn",)),
    "sehen": ("ein Browser-/Bild-Werkzeug einrichten", ("chrome-devtools", "playwright")),
    "zweitmeinung": ("eine Zweitmeinung einrichten", ("codex-consult", "code-review", "council")),
}

# Projekt-Wörter: treffen immer ``projekt`` (erste Regel).
PROJEKT_WOERTER: tuple[str, ...] = ("duoplus", "postproxy", "n8n", "adb", "airtable")

# Einordnungs-Regeln als Daten: (Kategorie, Schlüsselwörter). Reihenfolge = Vorrang.
# Am echten Katalog nachgerechnet (Prüfpanel #209 F8): ``bauen`` steht früh mit engen
# Wörtern, damit ``api-design``/``german-umlauts`` nicht in Design landen; nacktes
# ``design`` und ``review`` sind zu breit und fehlen absichtlich.
REGELN: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("projekt", PROJEKT_WOERTER),
    (
        "zweitmeinung",
        (
            "codex",
            "council",
            "judge",
            "second opinion",
            "zweitmeinung",
            "consult",
            "code-review",
            "grill",
        ),
    ),
    ("sicherheit", ("secret", "bitwarden", "security", "sicherheit", "credential", "vault")),
    (
        "testen",
        (
            "test",
            "tdd",
            "verify",
            "verif",
            "beweis",
            "qa",
            "abnahme",
            "pytest",
            "diagnos",
            "debug",
            "bug",
        ),
    ),
    (
        "bauen",
        (
            "codebase-design",
            "codebase-architecture",
            "api-design",
            "umlaut",
            "prototype",
            "implement",
            "refactor",
            "executor",
        ),
    ),
    (
        "sehen",
        (
            "screenshot",
            "vision",
            "sehen",
            "chrome-devtools",
            "browser",
            "playwright",
            "bildschirm",
            "bildanalyse",
            "videoanalyse",
            "image analysis",
            "video analysis",
        ),
    ),
    ("medien", ("ffmpeg", "video", "bild", "image", "media", "medien", "audio", "foto")),
    (
        "design",
        (
            "frontend-design",
            "design-system",
            "anti-slop",
            "ui",
            "ux",
            "css",
            "frontend",
            "tailwind",
            "polish",
            "slop",
            "dashboard",
            "mobile-first",
        ),
    ),
    (
        "kontext",
        (
            "memory",
            "context",
            "kontext",
            "graph",
            "handoff",
            "domain",
            "gedächtnis",
            "knowledge",
            "wissen",
            "writing-for-agents",
        ),
    ),
    (
        "recherche",
        (
            "research",
            "recherche",
            "perplexity",
            "firecrawl",
            "search",
            "suche",
            "scrape",
            "crawl",
            "explore",
            "discovery",
        ),
    ),
    ("deploy", ("deploy", "git", "docker", "vps", "server", "betrieb", "migration", "release")),
    (
        "steuerung",
        (
            "spawn",
            "loop",
            "wache",
            "notification",
            "meldung",
            "telegram",
            "mail",
            "cron",
            "statusline",
            "plan",
            "orchestr",
            "wizard",
            "triage",
            "ticket",
            "spec",
            "setup",
            "fable",
        ),
    ),
    ("bauen", ("build", "bauen")),
)

# Eingebaute Skills und Agenten von Claude Code: (Beschreibung, feste Kategorie).
EINGEBAUTE_SKILLS: dict[str, tuple[str, str]] = {
    "loop": ("Befehl oder Prompt wiederholt laufen lassen", "steuerung"),
    "run": ("App starten und eine Änderung live ansehen", "testen"),
    "claude-api": ("Nachschlagewerk für Claude-API und Anthropic-SDK", "bauen"),
    "security-review": ("Sicherheits-Prüfung der offenen Änderungen", "sicherheit"),
}
EINGEBAUTE_AGENTEN: dict[str, tuple[str, str]] = {
    "general-purpose": ("Allzweck-Agent für mehrstufige Aufgaben", "bauen"),
    "Explore": ("Nur-Lese-Agent zum Durchsuchen der Codebasis", "recherche"),
    "Plan": ("Plan-Agent für Umsetzungspläne", "steuerung"),
    "claude-code-guide": ("Fragen zu Claude Code und der Claude-API", "recherche"),
    "statusline-setup": ("Statuszeile einrichten", "steuerung"),
}

# Eingebaute Slash-Befehle der CLI: in der Historie kein „nicht installiert“.
EINGEBAUTE_KOMMANDOS: frozenset[str] = frozenset(
    {
        "add-dir", "agents", "artifacts", "bashes", "btw", "bug", "chrome", "clear", "compact",
        "config", "context", "copy", "cost", "desktop", "doctor", "effort", "exit", "export",
        "extra-usage", "fast", "feedback", "files", "fork", "help", "hooks", "ide", "init",
        "insights", "install-github-app", "keybindings", "login", "logout", "mcp", "memory",
        "mobile", "model", "output-style", "permissions", "plan", "plugin", "plugins",
        "pr-comments", "privacy-settings", "release-notes", "remote-control", "rename",
        "resume", "review", "rewind", "sandbox", "skills", "stats", "status", "statusline",
        "tasks", "terminal-setup", "theme", "todos", "upgrade", "usage", "vim",
    }
)  # fmt: skip

# Befehle aus der Bash-Historie, die als Unterbau gezählt werden.
UNTERBAU_BEFEHLE: frozenset[str] = frozenset(
    {"ffmpeg", "adb", "codex", "gemini", "docker", "ssh", "gh"}
)


@dataclass(frozen=True)
class Unterbau:
    """Eine Setup-Prüfung: da, wenn EINER der Wege klappt.

    ``befehl_reicht=False``: der Befehl im PATH allein zählt nicht (codex braucht
    Anmeldung per Schlüssel oder Datei). ``pruefung``: Argumente, mit denen der
    gefundene Befehl laufen muss (Exit 0), sonst Zeile ``pruef_fehlgrund``.
    """

    name: str
    befehle: tuple[str, ...] = ()
    schluessel: tuple[str, ...] = ()
    dateien: tuple[str, ...] = ()  # relativ zu HOME
    abhilfe: str = ""
    befehl_reicht: bool = True
    pruefung: tuple[str, ...] = ()
    pruef_fehlgrund: str = ""
    pruef_abhilfe: str = ""


# Erweiterbar: neue Zeile = neuer Unterbau.
UNTERBAUTEN: tuple[Unterbau, ...] = (
    Unterbau(
        "codex",
        befehle=("codex",),
        schluessel=("OPENAI_API_KEY",),
        dateien=(".codex/auth.json",),
        befehl_reicht=False,
        abhilfe="`npm install -g @openai/codex` und `codex login`, oder OPENAI_API_KEY "
        "in Bitwarden anlegen und in .env eintragen",
    ),
    Unterbau(
        "gemini",
        schluessel=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        abhilfe="Schlüssel in Bitwarden anlegen und in .env eintragen",
    ),
    Unterbau("ffmpeg", befehle=("ffmpeg",), abhilfe="`sudo apt install ffmpeg`"),
    Unterbau("adb", befehle=("adb",), abhilfe="`sudo apt install adb`"),
    Unterbau(
        "gh",
        befehle=("gh",),
        abhilfe="`sudo apt install gh` und `gh auth login`",
        pruefung=("auth", "status"),
        pruef_fehlgrund="gh nicht angemeldet (`gh auth status` scheitert)",
        pruef_abhilfe="`gh auth login`",
    ),
)


@dataclass
class Werkzeug:
    name: str
    art: str  # skill | plugin-skill | agent | mcp
    kategorie: str
    quelle: str
    beschreibung: str = ""
    genutzt: int = 0
    an: bool = True

    def als_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "art": self.art,
            "kategorie": self.kategorie,
            "quelle": self.quelle,
            "beschreibung": self.beschreibung[:160],
            "genutzt": self.genutzt,
            "an": self.an,
        }


@dataclass(frozen=True)
class SetupZeile:
    name: str
    fehlgrund: str
    abhilfe: str

    def als_dict(self) -> dict[str, str]:
        return {"name": self.name, "fehlgrund": self.fehlgrund, "abhilfe": self.abhilfe}


@dataclass
class Inventur:
    repo: Path
    werkzeuge: list[Werkzeug]
    projekt: dict[str, Any]
    historie: dict[str, Any]
    setup_zeilen: list[SetupZeile] = field(default_factory=list)
    tote_winkel: list[str] = field(default_factory=list)
    # Befunde beim Katalog-Lesen (kaputte Ordner, fehlende Agenten-Dateien).
    katalog_befunde: list[str] = field(default_factory=list)
    # Namen aus commands/-Ordnern: gelten in der Historie als installiert.
    kommando_dateien: set[str] = field(default_factory=set)
    # Abgewählt, aber gerade nicht im Katalog: bleibt erhalten, nie still fallen lassen.
    fehlend_abgewaehlt: list[str] = field(default_factory=list)

    @property
    def namen(self) -> list[str]:
        return sorted(w.name for w in self.werkzeuge)

    @property
    def abgewaehlt(self) -> list[str]:
        return sorted({w.name for w in self.werkzeuge if not w.an} | set(self.fehlend_abgewaehlt))

    def je_kategorie(self) -> dict[str, list[Werkzeug]]:
        gruppen: dict[str, list[Werkzeug]] = {k: [] for k in KATEGORIEN}
        for w in self.werkzeuge:
            gruppen[w.kategorie].append(w)
        for liste in gruppen.values():
            liste.sort(key=lambda w: (-w.genutzt, w.name.lower()))
        return gruppen

    def setze_abwahl(self, abgewaehlt: set[str]) -> None:
        for w in self.werkzeuge:
            w.an = w.name not in abgewaehlt
        self.fehlend_abgewaehlt = sorted(abgewaehlt - set(self.namen))

    def text(self) -> str:
        zeilen = [
            f"Werkzeug-Inventur für {self.repo} — Standard: alles an, abwählen statt anwählen"
        ]
        sprachen = ", ".join(
            f"{s['endung']} {s['dateien']}" for s in self.projekt.get("sprachen", [])
        )
        merkmale = ", ".join(self.projekt.get("merkmale", [])) or "keine"
        projekt = f"Projekt: {sprachen or 'keine Dateien'} · Merkmale: {merkmale}"
        if self.projekt.get("hinweis"):
            projekt += f" · Achtung: {self.projekt['hinweis']}"
        zeilen.append(projekt)
        zeilen.append(
            f"Historie: {self.historie.get('dateien', 0)} Dateien gelesen · "
            f"kaputte Zeilen: {self.historie.get('kaputte_zeilen', 0)} · "
            f"{len(self.werkzeuge)} Werkzeuge · {len(self.abgewaehlt)} abgewählt"
        )
        for schluessel, liste in self.je_kategorie().items():
            zeilen.append("")
            zeilen.append(f"## {KATEGORIEN[schluessel]} ({len(liste)})")
            if not liste:
                zeilen.append("  (leer)")
            for w in liste:
                haken = "x" if w.an else " "
                zeilen.append(f"  [{haken}] {w.name} ({w.art}, genutzt {w.genutzt}×)")
        if self.fehlend_abgewaehlt:
            zeilen.append("")
            zeilen.append(f"## Abgewählt, derzeit nicht gefunden ({len(self.fehlend_abgewaehlt)})")
            for name in self.fehlend_abgewaehlt:
                zeilen.append(f"  [ ] {name} (abgewählt, derzeit nicht gefunden)")
        zeilen.append("")
        zeilen.append(f"## Setup-Zeilen ({len(self.setup_zeilen)})")
        if not self.setup_zeilen:
            zeilen.append("  alle Unterbauten vorhanden")
        for z in self.setup_zeilen:
            zeilen.append(f"  - {z.name}: {z.fehlgrund} → {z.abhilfe}")
        zeilen.append("")
        zeilen.append(f"## Tote Winkel ({len(self.tote_winkel)})")
        if not self.tote_winkel:
            zeilen.append("  keine")
        for hinweis in self.tote_winkel:
            zeilen.append(f"  - {hinweis}")
        return "\n".join(zeilen)

    def als_dict(self) -> dict[str, Any]:
        return {
            "repo": str(self.repo),
            "standard": "alles an",
            "projekt": self.projekt,
            "historie": self.historie,
            "kategorien": [
                {
                    "schluessel": schluessel,
                    "anzeige": KATEGORIEN[schluessel],
                    "werkzeuge": [w.als_dict() for w in liste],
                }
                for schluessel, liste in self.je_kategorie().items()
            ],
            "abgewaehlt": self.abgewaehlt,
            "abgewaehlt_nicht_gefunden": list(self.fehlend_abgewaehlt),
            "setup_zeilen": [z.als_dict() for z in self.setup_zeilen],
            "tote_winkel": list(self.tote_winkel),
        }


# --- Einordnung ------------------------------------------------------------------


def _woerter(text: str) -> list[str]:
    return [w for w in re.split(r"[^0-9a-zäöüß]+", text.lower()) if w]


def _trifft(text: str, woerter: list[str], schluesselwoerter: tuple[str, ...]) -> bool:
    for sw in schluesselwoerter:
        if " " in sw or "-" in sw:
            if sw in text:
                return True
        elif any(w.startswith(sw) for w in woerter):
            return True
    return False


def ordne_ein(name: str, beschreibung: str, claude_md: str = "", repo_name: str = "") -> str:
    """Kategorie-Schlüssel für ein Werkzeug (Ablauf siehe Modul-Doku)."""
    regeln = list(REGELN)
    if repo_name:
        eigene = tuple({repo_name.lower(), repo_name.lower().split("-")[0]} - {""})
        regeln[0] = ("projekt", PROJEKT_WOERTER + eigene)
    for text in (name, beschreibung):
        klein = text.lower()
        woerter = _woerter(klein)
        for kategorie, schluesselwoerter in regeln:
            if _trifft(klein, woerter, schluesselwoerter):
                return kategorie
    kurz = name.split(":")[-1]
    if claude_md and kurz and re.search(rf"(?<![\w-]){re.escape(kurz)}(?![\w-])", claude_md):
        return "projekt"
    return "bauen"


# --- Leser: Katalog ----------------------------------------------------------------


def _frontmatter(datei: Path) -> dict[str, str]:
    """Einfacher YAML-Frontmatter-Leser (Schlüssel: Wert, Block-Werte mit | oder >)."""
    try:
        text = datei.read_text(encoding="utf-8", errors="replace")
    except OSError as fehler:
        log.warning("Nicht lesbar: %s (%s)", datei, fehler)
        return {}
    zeilen = text.splitlines()
    if not zeilen or zeilen[0].strip() != "---":
        return {}
    daten: dict[str, str] = {}
    schluessel: str | None = None
    block: list[str] = []
    for zeile in zeilen[1:]:
        if zeile.strip() == "---":
            break
        treffer = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", zeile)
        if treffer:
            if schluessel and block:
                daten[schluessel] = " ".join(block).strip()
            schluessel, wert = treffer.group(1), treffer.group(2).strip()
            block = []
            if wert in ("|", ">", "|-", ">-", "|+", ">+"):
                continue
            daten[schluessel] = wert.strip("\"'")
            schluessel = None
        elif schluessel and zeile.startswith((" ", "\t")):
            block.append(zeile.strip())
    if schluessel and block:
        daten[schluessel] = " ".join(block).strip()
    return daten


def _lies_json(datei: Path) -> Any:
    if not datei.is_file():
        return None
    try:
        return json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        log.warning("JSON unlesbar: %s (%s)", datei, fehler)
        return None


def _skills_in(ordner: Path, befunde: list[str]) -> list[tuple[str, str, Path]]:
    """(name, beschreibung, SKILL.md) je Unterordner; Ordner ohne SKILL.md → Befund."""
    if not ordner.is_dir():
        return []
    ergebnis: list[tuple[str, str, Path]] = []
    for kind in sorted(ordner.iterdir()):
        if kind.name.startswith("_") or kind.name == "synced" or not kind.is_dir():
            continue
        datei = kind / "SKILL.md"
        if not datei.is_file():
            befunde.append(f"kaputt: Ordner ohne SKILL.md — {kind}")
            continue
        fm = _frontmatter(datei)
        ergebnis.append((fm.get("name") or kind.name, fm.get("description", ""), datei))
    return ergebnis


def _agenten_in(ordner: Path) -> list[tuple[str, str, Path]]:
    if not ordner.is_dir():
        return []
    ergebnis: list[tuple[str, str, Path]] = []
    for datei in sorted(ordner.glob("*.md")):
        fm = _frontmatter(datei)
        ergebnis.append((fm.get("name") or datei.stem, fm.get("description", ""), datei))
    return ergebnis


def _kommandos_in(ordner: Path, vorsilbe: str = "") -> set[str]:
    """Slash-Befehle aus einem commands/-Ordner (Unterordner → ``ordner:befehl``)."""
    if not ordner.is_dir():
        return set()
    namen: set[str] = set()
    for datei in ordner.rglob("*.md"):
        teile = datei.relative_to(ordner).with_suffix("").parts
        namen.add(vorsilbe + ":".join(teile))
    return namen


def _plugin_skill_ordner(install: Path, manifest: Any) -> list[Path]:
    """Skill-Ordner eines Plugins: laut plugin.json (Liste oder Ordner), sonst skills/*."""
    angabe = manifest.get("skills") if isinstance(manifest, dict) else None
    if isinstance(angabe, list):
        return [install / str(p) for p in angabe if isinstance(p, str)]
    basis = install / (angabe if isinstance(angabe, str) else "skills")
    if not basis.is_dir():
        return []
    return [k for k in sorted(basis.iterdir()) if k.is_dir() and not k.name.startswith("_")]


@dataclass
class Katalog:
    werkzeuge: list[Werkzeug]
    befunde: list[str] = field(default_factory=list)
    kommandos: set[str] = field(default_factory=set)


def lies_katalog(repo: Path, claude_home: Path, home_json: Path) -> Katalog:
    """Alle installierten Werkzeuge (Kategorie nur bei eingebauten schon gesetzt)."""
    werkzeuge: list[Werkzeug] = []
    befunde: list[str] = []
    kommandos = _kommandos_in(claude_home / "commands") | _kommandos_in(
        repo / ".claude" / "commands"
    )

    for skill_ordner in (claude_home / "skills", repo / ".claude" / "skills"):
        for name, beschreibung, datei in _skills_in(skill_ordner, befunde):
            werkzeuge.append(Werkzeug(name, "skill", "", str(datei.parent), beschreibung))

    plugins = _lies_json(claude_home / "plugins" / "installed_plugins.json")
    eintraege = plugins.get("plugins") if isinstance(plugins, dict) else None
    for schluessel, liste in (eintraege or {}).items() if isinstance(eintraege, dict) else []:
        plugin = str(schluessel).split("@")[0]
        for eintrag in liste if isinstance(liste, list) else []:
            pfad = eintrag.get("installPath") if isinstance(eintrag, dict) else None
            if not isinstance(pfad, str):
                log.info("Plugin %s ohne installPath — übersprungen", schluessel)
                continue
            install = Path(pfad)
            manifest = _lies_json(install / ".claude-plugin" / "plugin.json")
            for ordner in _plugin_skill_ordner(install, manifest):
                datei = ordner / "SKILL.md"
                if not datei.is_file():
                    befunde.append(f"kaputt: Ordner ohne SKILL.md — {ordner}")
                    continue
                fm = _frontmatter(datei)
                name = f"{plugin}:{fm.get('name') or ordner.name}"
                werkzeuge.append(
                    Werkzeug(name, "plugin-skill", "", str(ordner), fm.get("description", ""))
                )
            kommandos |= _kommandos_in(install / "commands", f"{plugin}:")
            mcp = manifest.get("mcpServers") if isinstance(manifest, dict) else None
            for server in mcp if isinstance(mcp, dict) else {}:
                werkzeuge.append(
                    Werkzeug(f"plugin_{plugin}_{server}", "mcp", "", str(install), plugin)
                )

    for agent_ordner in (claude_home / "agents", repo / ".claude" / "agents"):
        for name, beschreibung, datei in _agenten_in(agent_ordner):
            werkzeuge.append(Werkzeug(name, "agent", "", str(datei), beschreibung))
    for name, (beschreibung, kategorie) in EINGEBAUTE_AGENTEN.items():
        werkzeuge.append(Werkzeug(name, "agent", kategorie, "eingebaut", beschreibung))
    for name, (beschreibung, kategorie) in EINGEBAUTE_SKILLS.items():
        werkzeuge.append(Werkzeug(name, "skill", kategorie, "eingebaut", beschreibung))

    heim = _lies_json(home_json)
    if isinstance(heim, dict):
        server = heim.get("mcpServers")
        for name in server if isinstance(server, dict) else {}:
            werkzeuge.append(Werkzeug(name, "mcp", "", "~/.claude.json"))
        projekte = heim.get("projects")
        eigen = projekte.get(str(repo)) if isinstance(projekte, dict) else None
        server = eigen.get("mcpServers") if isinstance(eigen, dict) else None
        for name in server if isinstance(server, dict) else {}:
            werkzeuge.append(Werkzeug(name, "mcp", "", "~/.claude.json (Projekt)"))
    repo_mcp = _lies_json(repo / ".mcp.json")
    server = repo_mcp.get("mcpServers") if isinstance(repo_mcp, dict) else None
    for name in server if isinstance(server, dict) else {}:
        werkzeuge.append(Werkzeug(name, "mcp", "", str(repo / ".mcp.json")))

    # Gleicher Name: der erste Fund gewinnt (Skill vor Projekt-Skill vor Plugin vor
    # Agent vor Eingebautem vor MCP), der zweite wird als Befund gemeldet.
    erster: dict[str, Werkzeug] = {}
    eindeutig: list[Werkzeug] = []
    for w in werkzeuge:
        if w.name in erster:
            befunde.append(
                f"doppelt: `{w.name}` aus {erster[w.name].quelle} und {w.quelle} — "
                "nur der erste zählt"
            )
            continue
        erster[w.name] = w
        eindeutig.append(w)
    return Katalog(eindeutig, befunde, kommandos)


# --- Leser: Projekt ---------------------------------------------------------------


_ENV_ZEILE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def env_schluessel(datei: Path) -> set[str]:
    """Namen der Schlüssel mit nicht leerem Wert. Werte werden nur auf Leere geprüft.

    ``KEY= # Kommentar``, ``KEY=""`` und reine Leerzeichen gelten als nicht gesetzt.
    Folgezeilen eines mehrzeiligen Werts in Anführungszeichen werden übersprungen.
    """
    namen: set[str] = set()
    if not datei.is_file():
        return namen
    offen = ""  # Anführungszeichen eines noch offenen mehrzeiligen Werts
    offen_name = ""
    offen_inhalt = False
    try:
        with datei.open(encoding="utf-8", errors="replace") as f:
            for roh in f:
                if offen:
                    if offen in roh:
                        if offen_inhalt or roh.split(offen, 1)[0].strip():
                            namen.add(offen_name)
                        offen = ""
                    elif roh.strip():
                        offen_inhalt = True
                    continue
                zeile = roh.strip()
                treffer = _ENV_ZEILE.match(zeile)
                if not zeile or zeile.startswith("#") or not treffer:
                    continue
                name, wert = treffer.group(1), treffer.group(2).strip()
                if wert[:1] in ("'", '"'):
                    zeichen, rest = wert[0], wert[1:]
                    if zeichen in rest:
                        if rest.split(zeichen, 1)[0].strip():
                            namen.add(name)
                    else:
                        offen, offen_name, offen_inhalt = zeichen, name, bool(rest.strip())
                    continue
                if re.split(r"(?:^|\s)#", wert, maxsplit=1)[0].strip():
                    namen.add(name)
    except OSError as fehler:
        log.warning(".env nicht lesbar: %s", fehler)
    return namen


def lies_projekt(repo: Path) -> dict[str, Any]:
    """Top-Dateiendungen aus ``git ls-files`` + Merkmal-Dateien + .env-Schlüsselnamen."""
    hinweis = ""
    dateien: list[str] = []
    try:
        lauf = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if lauf.returncode == 0:
            dateien = lauf.stdout.splitlines()
        else:
            fehlertext = lauf.stderr.strip()
            log.warning(
                "git ls-files fehlgeschlagen (Exit %s) in %s: %s", lauf.returncode, repo, fehlertext
            )
            erste = fehlertext.splitlines()[0] if fehlertext else "ohne Meldung"
            hinweis = f"git ls-files fehlgeschlagen (Exit {lauf.returncode}): {erste}"
    except (OSError, subprocess.SubprocessError) as fehler:
        log.warning("git ls-files fehlgeschlagen in %s: %s", repo, fehler)
        hinweis = f"git ls-files fehlgeschlagen: {fehler}"
    endungen = Counter(Path(d).suffix.lower() for d in dateien if Path(d).suffix)
    namen = {Path(d).name for d in dateien}
    merkmale: list[str] = []
    if "Dockerfile" in namen:
        merkmale.append("Dockerfile")
    if any(n.startswith(("docker-compose", "compose.")) for n in namen):
        merkmale.append("docker-compose")
    if "package.json" in namen:
        merkmale.append("package.json")
    if "pyproject.toml" in namen or any(n.startswith("requirements") for n in namen):
        merkmale.append("Python-Pakete")
    if "pytest.ini" in namen or any(d.startswith("tests/") or "/tests/" in d for d in dateien):
        merkmale.append("Tests")
    if any(d.startswith(".github/workflows/") for d in dateien):
        merkmale.append("GitHub-Workflows")
    if (repo / "web" / "templates").is_dir() or endungen[".html"] or endungen[".css"]:
        merkmale.append("Frontend")
    if (repo / ".mcp.json").is_file():
        merkmale.append(".mcp.json")
    ergebnis: dict[str, Any] = {
        "dateien": len(dateien),
        "sprachen": [{"endung": e, "dateien": n} for e, n in endungen.most_common(6)],
        "merkmale": merkmale,
        "env_schluessel": sorted(env_schluessel(repo / ".env")),
    }
    if hinweis:
        ergebnis["hinweis"] = hinweis
    return ergebnis


# --- Leser: Historie --------------------------------------------------------------


def projekt_slug(repo: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(repo))


def _bash_befehle(befehl: str) -> list[str]:
    """Erstes Wort je Teilbefehl (getrennt an && || ; | Zeilenumbruch), ohne VAR=wert."""
    woerter: list[str] = []
    for teil in re.split(r"&&|\|\||;|\||\n", befehl):
        for wort in teil.split():
            if re.match(r"^[A-Za-z_]\w*=", wort):
                continue
            woerter.append(Path(wort).name)
            break
    return woerter


def lies_historie(repo: Path, claude_home: Path, letzte: int) -> dict[str, Any]:
    """Zähler aus den neuesten ``letzte`` JSONL-Dateien des Projekts (inkl. Subagenten)."""
    ordner = claude_home / "projects" / projekt_slug(repo)
    skills: Counter[str] = Counter()
    agenten: Counter[str] = Counter()
    mcp: Counter[str] = Counter()
    befehle: Counter[str] = Counter()
    kommandos: Counter[str] = Counter()
    ordner_da = ordner.is_dir()
    dateien = sorted(ordner.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime) if ordner_da else []
    dateien = dateien[-letzte:] if letzte > 0 else []
    if not ordner_da:
        log.warning("Historie-Ordner fehlt: %s — Nutzung bleibt überall 0", ordner)
    elif not dateien:
        log.warning("Historie-Ordner ohne JSONL-Dateien: %s — Nutzung bleibt überall 0", ordner)
    kaputt = 0
    for datei in dateien:
        try:
            with datei.open(encoding="utf-8", errors="replace") as f:
                kaputt += _zaehle_historie(f, skills, agenten, mcp, befehle, kommandos)
        except OSError as fehler:
            log.warning("Historie nicht lesbar: %s (%s)", datei, fehler)
    return {
        "ordner": str(ordner),
        "ordner_da": ordner_da,
        "dateien": len(dateien),
        "kaputte_zeilen": kaputt,
        "skills": dict(skills),
        "agenten": dict(agenten),
        "mcp": dict(mcp),
        "kommandos": dict(kommandos),
        "unterbauten": dict(befehle),
    }


def _zaehle_historie(
    zeilen: Iterable[str],
    skills: Counter[str],
    agenten: Counter[str],
    mcp: Counter[str],
    befehle: Counter[str],
    kommandos: Counter[str],
) -> int:
    """Zählt eine JSONL-Datei Zeile für Zeile; Rückgabe = Anzahl kaputter Zeilen."""
    kaputt = 0
    for zeile in zeilen:
        if not zeile.strip():
            continue
        try:
            eintrag = json.loads(zeile)
        except json.JSONDecodeError:
            kaputt += 1
            continue
        nachricht = eintrag.get("message") if isinstance(eintrag, dict) else None
        inhalt = nachricht.get("content") if isinstance(nachricht, dict) else None
        if isinstance(inhalt, str):
            for k in re.findall(r"<command-name>/?([^<\s]+)</command-name>", inhalt):
                kommandos[k] += 1
            continue
        for teil in inhalt if isinstance(inhalt, list) else []:
            if not isinstance(teil, dict) or teil.get("type") != "tool_use":
                continue
            name = str(teil.get("name", ""))
            eingabe = teil.get("input") if isinstance(teil.get("input"), dict) else {}
            if name == "Skill" and eingabe.get("skill"):
                skills[str(eingabe["skill"]).lstrip("/")] += 1
            elif name in ("Agent", "Task") and eingabe.get("subagent_type"):
                agenten[str(eingabe["subagent_type"])] += 1
            elif name.startswith("mcp__"):
                mcp[name.split("__")[1]] += 1
            elif name == "Bash" and isinstance(eingabe.get("command"), str):
                for wort in _bash_befehle(eingabe["command"]):
                    if wort in UNTERBAU_BEFEHLE:
                        befehle[wort] += 1
    return kaputt


# --- Setup-Zeilen ------------------------------------------------------------------


def _fuehre_aus(befehl: list[str]) -> int:
    """Echte Prüfung (z. B. ``gh auth status``): Exit-Code, Fehler zählen als 1."""
    try:
        return subprocess.run(
            befehl, capture_output=True, timeout=30, check=False, stdin=subprocess.DEVNULL
        ).returncode
    except (OSError, subprocess.SubprocessError) as fehler:
        log.warning("Prüfung %s gescheitert: %s", " ".join(befehl), fehler)
        return 1


def pruefe_unterbauten(
    environ: Mapping[str, str],
    env_namen: set[str],
    home: Path,
    which: Callable[[str], str | None],
    ausfuehren: Callable[[list[str]], int] = _fuehre_aus,
) -> list[SetupZeile]:
    """Nur FEHLENDE Unterbauten werden Zeilen, jede mit konkretem Fehlgrund."""
    zeilen: list[SetupZeile] = []
    for u in UNTERBAUTEN:
        befehl_pfad = next((p for p in (which(b) for b in u.befehle) if p), None)
        if any((environ.get(s) or "").strip() or s in env_namen for s in u.schluessel):
            continue
        if any((home / d).is_file() for d in u.dateien):
            continue
        if befehl_pfad and u.befehl_reicht:
            if u.pruefung and ausfuehren([befehl_pfad, *u.pruefung]) != 0:
                zeilen.append(SetupZeile(u.name, u.pruef_fehlgrund, u.pruef_abhilfe))
            continue
        gruende: list[str] = []
        if befehl_pfad:
            gruende.append(f"Befehl `{Path(befehl_pfad).name}` da, aber nicht angemeldet")
        elif u.befehle:
            gruende.append(" und ".join(f"Befehl `{b}`" for b in u.befehle) + " nicht im PATH")
        if u.schluessel:
            gruende.append(
                ("weder " if len(u.schluessel) > 1 else "")
                + " noch ".join(u.schluessel)
                + " in Umgebung oder .env"
            )
        for d in u.dateien:
            gruende.append(f"~/{d} fehlt")
        zeilen.append(SetupZeile(u.name, "; ".join(gruende), u.abhilfe))
    return zeilen


# --- Zusammenbau -------------------------------------------------------------------


def _nutzung(w: Werkzeug, historie: dict[str, Any]) -> int:
    if w.art in ("skill", "plugin-skill"):
        # Plugin-Skills heißen in Historie und Katalog gleich: ``plugin:skill``.
        return int(historie["skills"].get(w.name, 0) + historie["kommandos"].get(w.name, 0))
    if w.art == "agent":
        return int(historie["agenten"].get(w.name, 0))
    if w.art == "mcp":
        return int(historie["mcp"].get(w.name, 0))
    return 0


def erstelle(
    repo: Path,
    *,
    claude_home: Path | None = None,
    home_json: Path | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
    ausfuehren: Callable[[list[str]], int] = _fuehre_aus,
    letzte: int = HISTORIE_VORGABE,
) -> Inventur:
    """Liest alles echt und baut die Inventur (Standard: alles an)."""
    repo = repo.resolve()
    heim = home or Path.home()
    claude_home = claude_home or heim / ".claude"
    home_json = home_json or heim / ".claude.json"
    umgebung = os.environ if environ is None else environ
    if which is None:
        pfad = umgebung.get("PATH", "")

        def which(befehl: str) -> str | None:
            return shutil.which(befehl, path=pfad)

    projekt = lies_projekt(repo)
    historie = lies_historie(repo, claude_home, letzte)
    katalog = lies_katalog(repo, claude_home, home_json)
    werkzeuge = katalog.werkzeuge

    # claude.ai-Connectoren tauchen nur in der Historie auf (mcp__claude_ai_*).
    bekannt = {w.name for w in werkzeuge}
    for server in sorted(historie["mcp"]):
        if server.startswith("claude_ai_") and server not in bekannt:
            werkzeuge.append(Werkzeug(server, "mcp", "", "claude.ai-Connector (Historie)"))

    claude_md = _lies_text(repo / "CLAUDE.md")
    for w in werkzeuge:
        if not w.kategorie:
            w.kategorie = ordne_ein(w.name, w.beschreibung, claude_md, repo.name)
        w.genutzt = _nutzung(w, historie)

    befunde = list(katalog.befunde)
    agenten = claude_home / "agents"
    if not (agenten.is_dir() and any(agenten.glob("*.md"))):
        for datei in (repo / "CLAUDE.md", claude_home / "CLAUDE.md"):
            if "agents/" in _lies_text(datei):
                befunde.append(
                    f"{datei} erwähnt `agents/`, aber {agenten} fehlt oder ist leer — "
                    "Agenten-Dateien (z. B. executor-opus) anlegen oder den Verweis streichen"
                )

    inventur = Inventur(
        repo=repo,
        werkzeuge=werkzeuge,
        projekt=projekt,
        historie=historie,
        katalog_befunde=befunde,
        kommando_dateien=katalog.kommandos,
    )
    inventur.setup_zeilen = pruefe_unterbauten(
        umgebung, set(projekt["env_schluessel"]), heim, which, ausfuehren
    )
    inventur.tote_winkel = _tote_winkel(inventur)
    return inventur


def _lies_text(datei: Path) -> str:
    if not datei.is_file():
        return ""
    try:
        return datei.read_text(encoding="utf-8", errors="replace")
    except OSError as fehler:
        log.warning("Nicht lesbar: %s (%s)", datei, fehler)
        return ""


def _vorschlag(schluessel: str, namen: set[str]) -> str:
    """Vorschlag für eine leere Kategorie, ohne Beispiele, die schon installiert sind."""
    text, beispiele = VORSCHLAEGE[schluessel]
    vorhanden = namen | {n.split(":")[-1] for n in namen}
    frei = [b for b in beispiele if b not in vorhanden]
    return f"{text} (z. B. {', '.join(f'`{b}`' for b in frei)})" if frei else text


def _tote_winkel(inventur: Inventur) -> list[str]:
    hinweise: list[str] = []
    namen = {w.name for w in inventur.werkzeuge}
    for schluessel, liste in inventur.je_kategorie().items():
        if not liste:
            hinweise.append(
                f"Kategorie „{KATEGORIEN[schluessel]}“ leer — "
                f"Vorschlag: {_vorschlag(schluessel, namen)}"
            )
    hinweise.extend(inventur.katalog_befunde)

    je_kurzname: dict[str, list[str]] = {}
    for w in inventur.werkzeuge:
        if w.art in ("skill", "plugin-skill"):
            je_kurzname.setdefault(w.name.split(":")[-1], []).append(w.name)
    for gruppe in je_kurzname.values():
        if len(gruppe) > 1:
            paar = " und ".join(f"`{n}`" for n in sorted(gruppe))
            hinweise.append(f"doppelt: {paar} — gleicher Kurzname, einen davon abwählen")

    historie = inventur.historie
    if historie.get("dateien", 0) == 0:
        if historie.get("ordner_da"):
            hinweise.append(
                f"Historie: {historie['ordner']} hat 0 Dateien — Nutzung unbekannt (alles 0×)"
            )
        else:
            hinweise.append(f"Historie: {historie['ordner']} fehlt — Nutzung unbekannt (alles 0×)")
    for skill, anzahl in sorted(historie["skills"].items()):
        if skill not in namen:
            hinweise.append(f"Skill `{skill}` genutzt ({anzahl}×), aber nicht installiert")
    for server, anzahl in sorted(historie["mcp"].items()):
        if server not in namen:
            hinweise.append(f"MCP-Server `{server}` genutzt ({anzahl}×), aber nicht installiert")
    for agent, anzahl in sorted(historie["agenten"].items()):
        if agent not in namen:
            hinweise.append(f"Agent `{agent}` genutzt ({anzahl}×), aber nicht installiert")
    bekannt = namen | inventur.kommando_dateien | EINGEBAUTE_KOMMANDOS | set(historie["skills"])
    for kommando, anzahl in sorted(historie["kommandos"].items()):
        if kommando not in bekannt:
            hinweise.append(f"Kommando `/{kommando}` genutzt ({anzahl}×), aber nicht installiert")
    return hinweise


# --- Freigabeliste ---------------------------------------------------------------------


class WerkzeugeKaputt(Exception):
    """werkzeuge.json ist da, aber unlesbar oder ohne gültige ``abgewaehlt``-Liste."""


def lies_abwahl(datei: Path) -> set[str]:
    """Gespeicherte Abwahl. Fehlt die Datei → leer; kaputt → ``WerkzeugeKaputt``."""
    if not datei.exists():
        return set()
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        raise WerkzeugeKaputt(f"{datei} unlesbar ({fehler})") from fehler
    liste = daten.get("abgewaehlt") if isinstance(daten, dict) else None
    if not isinstance(liste, list) or not all(isinstance(n, str) for n in liste):
        raise WerkzeugeKaputt(f"{datei}: `abgewaehlt` fehlt oder ist keine Liste von Namen")
    return set(liste)


def schreibe(inventur: Inventur, datei: Path) -> Path:
    """Schreibt die Freigabeliste. Enthält nur Namen, nie Werte aus .env."""
    daten = {
        "stand": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "standard": "alles an",
        "abgewaehlt": inventur.abgewaehlt,
        "kategorien": {
            schluessel: [w.name for w in liste]
            for schluessel, liste in inventur.je_kategorie().items()
        },
        "setup_zeilen": [z.als_dict() for z in inventur.setup_zeilen],
        "tote_winkel": list(inventur.tote_winkel),
    }
    datei.parent.mkdir(parents=True, exist_ok=True)
    zwischen = datei.with_name(datei.name + ".tmp")
    zwischen.write_text(json.dumps(daten, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    zwischen.replace(datei)
    return datei


def _namen_liste(wert: str | None) -> list[str]:
    return [n.strip() for n in (wert or "").split(",") if n.strip()]


def lauf(
    repo: Path,
    *,
    als_json: bool = False,
    abwahl: str | None = None,
    anwahl: str | None = None,
    schreiben: bool = False,
    ausgabe: Path | None = None,
    letzte: int = HISTORIE_VORGABE,
) -> int:
    """Befehl ``inventur``: lesen, Abwahl anwenden, ausgeben, optional schreiben."""
    inventur = erstelle(repo, letzte=letzte)
    ziel = ausgabe or repo / AUSGABE_PFAD
    try:
        gespeichert = lies_abwahl(ziel)
    except WerkzeugeKaputt as fehler:
        log.error(
            "%s — Datei wird nicht überschrieben (sonst ginge die Abwahl verloren). "
            "Reparieren oder löschen (dann ist wieder alles an).",
            fehler,
        )
        return EXIT_KAPUTT
    neu_ab = _namen_liste(abwahl)
    neu_an = _namen_liste(anwahl)
    gueltig = set(inventur.namen)
    unbekannt = [n for n in neu_ab if n not in gueltig]
    unbekannt += [n for n in neu_an if n not in gueltig | gespeichert]
    if unbekannt:
        log.error(
            "Unbekannte Werkzeug-Namen: %s. Gültige Namen (erste 10): %s",
            ", ".join(unbekannt),
            ", ".join(inventur.namen[:10]),
        )
        return EXIT_UNBEKANNT
    inventur.setze_abwahl((gespeichert | set(neu_ab)) - set(neu_an))
    ungespeichert = bool(neu_ab or neu_an) and not schreiben
    if als_json:
        print(json.dumps(inventur.als_dict(), indent=2, ensure_ascii=False))
    else:
        print(inventur.text())
        if ungespeichert:
            print(f"\n{NICHT_GESPEICHERT}")
    if ungespeichert:
        log.warning(NICHT_GESPEICHERT)
    if schreiben:
        schreibe(inventur, ziel)
        log.info("Freigabeliste geschrieben: %s", ziel)
    return 0
