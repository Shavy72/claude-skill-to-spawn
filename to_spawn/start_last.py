"""Start-Last-Messung je Fenstertyp (#412): Bau-Session und Aufseher.

Misst den Kontext nach Start für beide Fenstertypen (``bau`` und ``aufseher``) und
schlüsselt die größten Posten auf, damit sich Start-Last-Treiber gezielt streichen oder
nachladen lassen:

1. **Transkript-Istwert** (wenn Transkripte vorliegen — automatisch über
   ``.to-spawn/sessions/`` oder explizit per ``--bau-transkript`` /
   ``--aufseher-transkript``): Kontextgröße des ersten echten Haupt-Modellaufrufs über
   :func:`to_spawn.hooks.start_kontext` (``input + cache_read + cache_creation``).
2. **Posten-Aufschlüsselung** (immer verfügbar, deterministisch, ohne API-Aufruf):
   Start-Prompt (aus ``skripte/bau.py`` bzw. ``skripte/wache.py``), aktive
   Skill-Beschreibungen (YAML-Frontmatter), globale Regeln (``CLAUDE.md`` + ``rules/``),
   Repo-Ladedateien (``CLAUDE.md``, ``AGENTS.md``, ``.claude/rules/``), Kontext-Paket
   des Tickets und aktive MCP-Server.

Obergrenzen (Zielwerte an genau einer Stelle): :data:`GRENZEN_K` (Bau ≤ 50k, Aufseher ≤ 80k),
überschreibbar per ``.to-spawn/config.json`` (Block ``start_last``) oder CLI-Flags.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from . import (
    bau_log,
    config,
    context_mode,
    hooks,
    konsole,
    manifest,
    sessions_datei,
    sitzung_filter,
    waechter_lauf,
)

log = logging.getLogger("to_spawn.start_last")

SKILL_WURZEL = Path(__file__).resolve().parent.parent

#: Gemessene Fenstertypen in fester Ausgabereihenfolge.
FENSTERTYPEN: tuple[str, ...] = ("bau", "aufseher")

#: Ziel-Obergrenzen (k-Token) je Fenstertyp (#412, einzige Vorgabe-Stelle).
GRENZEN_K: dict[str, float] = {
    "bau": 50.0,
    "aufseher": 80.0,
}

#: Konservative Faustregel für die Vorab-Schätzung von Text-Posten (Zeichen je Token).
ZEICHEN_PRO_TOKEN = 4

EXIT_OK = 0
EXIT_UEBER_GRENZE = 1
EXIT_FEHLER = 2

#: Gruppen-Anzeigenamen in fester Reihenfolge.
GRUPPEN_NAMEN: dict[str, str] = {
    "prompt": "Start-Prompt",
    "skills": "Aktive Skills (Beschreibungen)",
    "regeln_global": "Globale Regeln & Ladedateien",
    "regeln_repo": "Repo-Ladedateien",
    "kontext_paket": "Kontext-Paket (Manifest)",
    "mcp": "Aktive MCP-Server",
}

_ZEILEN_BEREICH = re.compile(r"^(?P<pfad>.+?):(?P<start>\d+)(?:-(?P<ende>\d+))?$")
_FRONTMATTER = re.compile(r"\A\s*---\s*\r?\n(?P<kopf>.*?)\r?\n---(?:\s*\r?\n|\Z)", re.DOTALL)
_SKRIPT_CACHE: dict[str, ModuleType] = {}


class EingabeFehler(ValueError):
    """Unbrauchbare CLI-Eingabe (fehlendes Repo, fehlendes oder leeres Transkript)."""


@dataclass(frozen=True)
class Posten:
    """Einzelner gemessener Text-Posten der Start-Last."""

    gruppe: str
    name: str
    zeichen: int
    tokens: int
    quelle: str = ""


@dataclass(frozen=True)
class TranskriptMessung:
    """Gemessener Start-Kontext aus einem echten Claude-Transkript."""

    name: str
    pfad: str
    start_tokens: int
    spitze_tokens: int
    aufrufe: int


@dataclass(frozen=True)
class FensterMessung:
    """Start-Last-Ergebnis für genau einen Fenstertyp (``bau`` oder ``aufseher``)."""

    typ: str
    obergrenze_k: float
    posten: tuple[Posten, ...]
    skills_aktiv: tuple[str, ...]
    skills_aus: int
    mcp_aktiv: tuple[str, ...]
    transkripte: tuple[TranskriptMessung, ...] = ()

    @property
    def geschaetzt_tokens(self) -> int:
        return sum(p.tokens for p in self.posten)

    @property
    def geschaetzt_k(self) -> float:
        return round(self.geschaetzt_tokens / 1000, 1)

    @property
    def gemessen_tokens(self) -> int | None:
        if not self.transkripte:
            return None
        return max(t.start_tokens for t in self.transkripte)

    @property
    def gemessen_k(self) -> float | None:
        wert = self.gemessen_tokens
        return round(wert / 1000, 1) if wert is not None else None

    @property
    def massgeblich_k(self) -> float:
        """Maßgeblicher Wert für die Obergrenze: Transkript-Istwert, sonst Posten-Schätzung."""
        return self.gemessen_k if self.gemessen_k is not None else self.geschaetzt_k

    @property
    def quelle_massgeblich(self) -> str:
        return "transkript" if self.gemessen_k is not None else "posten"

    @property
    def bestanden(self) -> bool:
        return self.massgeblich_k <= self.obergrenze_k


def schaetze_tokens(text: str) -> int:
    """Konservative Token-Schätzung aus der Zeichenzahl (``ceil(len / ZEICHEN_PRO_TOKEN)``)."""
    if not text:
        return 0
    return math.ceil(len(text) / ZEICHEN_PRO_TOKEN)


def _lade_skript(name: str) -> ModuleType:
    """Lädt ``skripte/<name>.py`` erst bei Bedarf (vermeidet Import-Seiteneffekte in ``to_spawn.py``)."""
    if name in _SKRIPT_CACHE:
        return _SKRIPT_CACHE[name]
    pfad = SKILL_WURZEL / "skripte" / f"{name}.py"
    modul_name = f"_to_spawn_start_last_{name}"
    spez = importlib.util.spec_from_file_location(modul_name, pfad)
    if spez is None or spez.loader is None:
        raise RuntimeError(f"Skript nicht ladbar: {pfad}")
    modul = importlib.util.module_from_spec(spez)
    spez.loader.exec_module(modul)
    _SKRIPT_CACHE[name] = modul
    return modul


def _lies_text(pfad: Path) -> str | None:
    try:
        return pfad.read_text(encoding="utf-8", errors="replace")
    except OSError as fehler:
        log.warning("Datei %s nicht lesbar: %s", pfad, fehler)
        return None


def _lies_json(pfad: Path) -> dict[str, Any]:
    if not pfad.is_file():
        return {}
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        log.warning("JSON %s nicht lesbar: %s", pfad, fehler)
        return {}
    return daten if isinstance(daten, dict) else {}


def _frontmatter_text(text: str) -> str:
    """Extrahiert das YAML-Frontmatter eines Skills/Befehls (das Claude Code beim Start lädt)."""
    treffer = _FRONTMATTER.match(text)
    if treffer:
        return treffer.group("kopf").strip()
    for zeile in text.splitlines():
        sauber = zeile.strip()
        if sauber:
            return sauber[:240]
    return ""


def _skill_beschreibungen(repo: Path, claude_dir: Path) -> dict[str, tuple[str, str]]:
    """Liefert ``{skill_name: (frontmatter_text, quelle)}`` aus allen Claude-Skill-Quellen."""
    gefunden: dict[str, tuple[str, str]] = {}

    def eintragen(name: str, datei: Path) -> None:
        if name in gefunden:
            return
        inhalt = _lies_text(datei)
        if inhalt is None:
            return
        kopf = _frontmatter_text(inhalt)
        if kopf:
            gefunden[name] = (kopf, str(datei))

    # Repo-Skills und -Befehle haben Vorrang vor globalen.
    for md in sorted((repo / ".claude" / "skills").glob("*/SKILL.md")):
        eintragen(md.parent.name, md)
    for md in sorted((repo / ".claude" / "commands").glob("*.md")):
        eintragen(md.stem, md)
    for md in sorted((repo / ".claude" / "commands").glob("*/*.md")):
        eintragen(f"{md.parent.name}:{md.stem}", md)

    # Globale Skills und Befehle.
    for md in sorted((claude_dir / "skills").glob("*/SKILL.md")):
        eintragen(md.parent.name, md)
    commands_dir = claude_dir / "commands"
    if commands_dir.is_dir():
        for md in sorted(commands_dir.glob("*.md")):
            eintragen(md.stem, md)
        for md in sorted(commands_dir.glob("*/*.md")):
            eintragen(f"{md.parent.name}:{md.stem}", md)

    # Plugin-Skills und -Befehle.
    cache = claude_dir / "plugins" / "cache"
    if cache.is_dir():
        for md in sorted(cache.glob("*/*/*/skills/*/SKILL.md")):
            plugin = md.parents[3].name
            eintragen(f"{plugin}:{md.parent.name}", md)
        for md in sorted(cache.glob("*/*/*/commands/*.md")):
            plugin = md.parents[2].name
            eintragen(f"{plugin}:{md.stem}", md)

    # Rückfall: Beschreibungen aus toolbox/registry.json.
    registry = _lies_json(claude_dir / "toolbox" / "registry.json")
    for eintrag in registry.get("entries") or []:
        if not isinstance(eintrag, dict):
            continue
        name = str(eintrag.get("name") or "").strip()
        beschr = str(eintrag.get("description") or "").strip()
        if name and beschr and name not in gefunden:
            gefunden[name] = (f"{name}: {beschr}", str(claude_dir / "toolbox" / "registry.json"))

    return gefunden


def _ladedateien_global(claude_dir: Path) -> list[Posten]:
    """Globale CLAUDE.md und globale Regeldateien unter ``<claude_dir>/rules/``."""
    kandidaten: list[tuple[str, Path]] = []
    claude_md = claude_dir / "CLAUDE.md"
    if claude_md.is_file():
        kandidaten.append(("global/CLAUDE.md", claude_md))
    regeln_dir = claude_dir / "rules"
    if regeln_dir.is_dir():
        for datei in sorted(regeln_dir.rglob("*.md")):
            if datei.is_file():
                rel = datei.relative_to(claude_dir).as_posix()
                kandidaten.append((f"global/{rel}", datei))
    return _datei_posten("regeln_global", kandidaten)


def _ladedateien_repo(repo: Path) -> list[Posten]:
    """Vom Projekt automatisch geladene Dateien (CLAUDE.md, AGENTS.md, .claude/rules/)."""
    kandidaten: list[tuple[str, Path]] = []
    for rel in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md", "AGENTS.md"):
        pfad = repo / rel
        if pfad.is_file():
            kandidaten.append((rel, pfad))
    regeln_dir = repo / ".claude" / "rules"
    if regeln_dir.is_dir():
        for datei in sorted(regeln_dir.rglob("*.md")):
            if datei.is_file():
                kandidaten.append((datei.relative_to(repo).as_posix(), datei))
    return _datei_posten("regeln_repo", kandidaten)


def _datei_posten(gruppe: str, kandidaten: list[tuple[str, Path]]) -> list[Posten]:
    gesehen: set[Path] = set()
    posten: list[Posten] = []
    for name, pfad in kandidaten:
        try:
            aufgeloest = pfad.resolve()
        except OSError:
            continue
        if aufgeloest in gesehen:
            continue
        gesehen.add(aufgeloest)
        text = _lies_text(pfad)
        if text is None or not text:
            continue
        posten.append(
            Posten(
                gruppe=gruppe,
                name=name,
                zeichen=len(text),
                tokens=schaetze_tokens(text),
                quelle=str(pfad),
            )
        )
    return posten


def _kontext_paket_posten(repo: Path, eintrag: dict[str, Any]) -> list[Posten]:
    """Misst die im Ticket-Manifest gelisteten ``files`` (inkl. Zeilenbereich) und ``docs``."""
    wurzel = repo.resolve()
    posten: list[Posten] = []
    eintraege = list(eintrag.get("files") or []) + list(eintrag.get("docs") or [])
    for roh in eintraege:
        angabe = str(roh or "").strip()
        if not angabe:
            continue
        treffer = _ZEILEN_BEREICH.match(angabe)
        if treffer:
            rel = treffer.group("pfad").strip()
            start = max(1, int(treffer.group("start")))
            ende = int(treffer.group("ende") or start)
        else:
            rel, start, ende = angabe, None, None
        ziel = (wurzel / rel).resolve()
        if not ziel.is_relative_to(wurzel) or not ziel.is_file():
            log.debug("Kontext-Paket-Eintrag %r übersprungen (fehlt oder außerhalb des Repos).", angabe)
            continue
        text = _lies_text(ziel)
        if text is None:
            continue
        if start is not None and ende is not None:
            zeilen = text.splitlines(keepends=True)
            ausschnitt = "".join(zeilen[start - 1 : max(start, ende)])
        else:
            ausschnitt = text
        if not ausschnitt:
            continue
        posten.append(
            Posten(
                gruppe="kontext_paket",
                name=angabe,
                zeichen=len(ausschnitt),
                tokens=schaetze_tokens(ausschnitt),
                quelle=str(ziel),
            )
        )
    return posten


def _manifest_auswahl(repo: Path, spec: int | None, ticket: str | None) -> tuple[int, str, dict[str, Any]]:
    """Wählt (spec, ticket, eintrag) aus den Repo-Manifesten; ohne Manifest sichere Vorgaben."""
    alle = manifest.alle_manifeste(repo)
    kandidaten: list[tuple[int, dict[str, Any]]] = []
    if spec is not None and spec in alle:
        try:
            daten, _ = manifest.lies_json(alle[spec])
            if isinstance(daten, dict):
                kandidaten.append((spec, daten))
        except (OSError, ValueError) as fehler:
            log.warning("Manifest #%s unlesbar: %s", spec, fehler)
    for nr in sorted(alle):
        if spec is not None and nr == spec:
            continue
        try:
            daten, _ = manifest.lies_json(alle[nr])
        except (OSError, ValueError) as fehler:
            log.warning("Manifest #%s unlesbar: %s", nr, fehler)
            continue
        if isinstance(daten, dict):
            kandidaten.append((nr, daten))

    if ticket is not None:
        for nr, daten in kandidaten:
            tickets = daten.get("tickets")
            if isinstance(tickets, dict) and ticket in tickets and isinstance(tickets[ticket], dict):
                return nr, ticket, tickets[ticket]
        return spec if spec is not None else 1, ticket, {}

    for nr, daten in kandidaten:
        tickets = daten.get("tickets")
        if isinstance(tickets, dict) and tickets:
            erstes = sorted(tickets, key=manifest.ticket_schluessel)[0]
            eintrag = tickets[erstes] if isinstance(tickets[erstes], dict) else {}
            return nr, str(erstes), eintrag

    return spec if spec is not None else 1, "1", {}


def _aktive_mcps(
    gewuenscht: list[str],
    repo: Path,
    home: Path,
    claude_dir: Path,
) -> tuple[str, ...]:
    """Aktive MCP-Server unter ``--strict-mcp-config`` (eine Quelle für ``bau`` und ``aufseher``, #412)."""
    katalog = sitzung_filter.mcp_katalog(repo=repo, home=home, claude_dir=claude_dir)
    aktiv: list[str] = []
    for name in dict.fromkeys(gewuenscht):
        if name == sitzung_filter.CHROME_MCP or name in katalog:
            aktiv.append(name)
    if context_mode.plugin_wurzel(claude_dir) is not None and context_mode.SERVER_NAME not in aktiv:
        aktiv.append(context_mode.SERVER_NAME)
    return tuple(sorted(aktiv))


def _aufseher_mcps(
    konfig: dict[str, Any],
    repo: Path,
    home: Path,
    claude_dir: Path,
) -> tuple[str, ...]:
    """Aktive MCP-Server des Aufsehers (``--strict-mcp-config``, #412)."""
    return _aktive_mcps(sitzung_filter.waechter_liste(konfig, "mcp"), repo, home, claude_dir)


def _bau_mcps(
    default: dict[str, Any],
    eintrag: dict[str, Any],
    repo: Path,
    home: Path,
    claude_dir: Path,
) -> tuple[str, ...]:
    """Aktive MCP-Server der Bau-Session (``--strict-mcp-config``)."""
    gewuenscht = list(default.get("core_mcp") or []) + list(eintrag.get("mcp") or [])
    return _aktive_mcps(gewuenscht, repo, home, claude_dir)


def transkript_messen(pfad: Path, *, name: str | None = None) -> TranskriptMessung:
    """Liest den Start- und Spitzen-Kontext aus einer Transkript-Datei (JSONL).

    Wirft :class:`EingabeFehler`, wenn die Datei fehlt oder keinen Haupt-Modellaufruf enthält.
    """
    if not pfad.is_file():
        raise EingabeFehler(f"Transkript nicht gefunden: {pfad}")
    zeilen = hooks.lese_transkript(pfad)
    start = hooks.start_kontext(zeilen)
    haupt = hooks.kontext(hooks.haupt_zeilen(zeilen))
    if start is None or haupt is None:
        raise EingabeFehler(f"Transkript enthält keinen lesbaren Haupt-Modellaufruf: {pfad}")
    return TranskriptMessung(
        name=name or pfad.stem,
        pfad=str(pfad),
        start_tokens=start,
        spitze_tokens=haupt["spitze"],
        aufrufe=haupt["aufrufe"],
    )


def _session_transkripte(
    repo: Path,
    *,
    home: Path,
    transkript_wurzel: Path | None = None,
) -> dict[str, list[TranskriptMessung]]:
    """Findet vorhandene Transkripte aus ``<repo>/.to-spawn/sessions/*.json`` je Fenstertyp."""
    ergebnis: dict[str, list[TranskriptMessung]] = {"bau": [], "aufseher": []}
    ordner = repo / ".to-spawn" / "sessions"
    if not ordner.is_dir():
        return ergebnis
    for datei in sorted(ordner.glob("*.json")):
        stamm = datei.stem
        if stamm.isdigit():
            typ, bezeichnung = "bau", f"bau {stamm}"
        elif stamm.startswith("wache-"):
            typ, bezeichnung = "aufseher", f"wache {stamm.removeprefix('wache-')}"
        else:
            continue
        daten = sessions_datei.lesen(repo, stamm)
        if not daten:
            continue
        sid = str(daten["session_id"])
        cwd_wert = str(daten.get("cwd") or "").strip()
        kandidaten: list[Path] = []
        if transkript_wurzel is not None:
            kandidaten.append(transkript_wurzel / f"{sid}.jsonl")
            if cwd_wert:
                kandidaten.append(transkript_wurzel / re.sub(r"[^A-Za-z0-9]", "-", cwd_wert) / f"{sid}.jsonl")
        if cwd_wert:
            kandidaten.append(waechter_lauf.transkript_ordner(Path(cwd_wert), heim=home) / f"{sid}.jsonl")
        for kandidat in kandidaten:
            if not kandidat.is_file():
                continue
            try:
                ergebnis[typ].append(transkript_messen(kandidat, name=bezeichnung))
            except EingabeFehler as fehler:
                log.warning("Session-Transkript %s übersprungen: %s", kandidat, fehler)
            break
    return ergebnis


def grenzen_ermitteln(
    konfig: dict[str, Any],
    *,
    grenze_bau: float | None = None,
    grenze_aufseher: float | None = None,
) -> dict[str, float]:
    """Obergrenzen (k-Token): CLI-Flag > Repo-Konfig ``start_last`` > :data:`GRENZEN_K`."""
    abschnitt = konfig.get("start_last") if isinstance(konfig.get("start_last"), dict) else {}

    def wert(typ: str, cli_wert: float | None) -> float:
        if cli_wert is not None:
            return float(cli_wert)
        aus_konfig = abschnitt.get(f"grenze_{typ}_k")
        if isinstance(aus_konfig, (int, float)) and not isinstance(aus_konfig, bool) and aus_konfig > 0:
            return float(aus_konfig)
        return GRENZEN_K[typ]

    return {
        "bau": wert("bau", grenze_bau),
        "aufseher": wert("aufseher", grenze_aufseher),
    }


def _skill_posten(aktive: tuple[str, ...], beschreibungen: dict[str, tuple[str, str]]) -> list[Posten]:
    posten: list[Posten] = []
    for name in aktive:
        treffer = beschreibungen.get(name)
        if treffer is None:
            continue
        text, quelle = treffer
        posten.append(
            Posten(
                gruppe="skills",
                name=f"skill:{name}",
                zeichen=len(text),
                tokens=schaetze_tokens(text),
                quelle=quelle,
            )
        )
    return posten


def messen(
    repo: Path,
    *,
    spec: int | None = None,
    ticket: str | None = None,
    claude_dir: Path | None = None,
    home: Path | None = None,
    gh_repo: str | None = None,
    grenze_bau: float | None = None,
    grenze_aufseher: float | None = None,
    bau_transkripte: list[Path] | None = None,
    aufseher_transkripte: list[Path] | None = None,
    transkript_wurzel: Path | None = None,
    mit_sessions: bool = True,
) -> dict[str, FensterMessung]:
    """Misst die Start-Last für ``bau`` und ``aufseher`` in ``repo``."""
    repo = repo.expanduser().resolve()
    if not repo.is_dir():
        raise EingabeFehler(f"Repo-Ordner existiert nicht: {repo}")

    ziel_home = (home if home is not None else Path.home()).expanduser().resolve()
    ziel_claude = claude_dir.expanduser().resolve() if claude_dir is not None else (ziel_home / ".claude")

    bau = _lade_skript("bau")
    wache = _lade_skript("wache")
    konfig = config.lade(repo)
    grenzen = grenzen_ermitteln(konfig, grenze_bau=grenze_bau, grenze_aufseher=grenze_aufseher)
    spec_nr, ticket_nr, eintrag = _manifest_auswahl(repo, spec, ticket)

    slug = (
        gh_repo
        or bau.repo_aus_origin("", repo)
        or os.environ.get("TO_SPAWN_GH_REPO", "").strip()
        or f"lokal/{repo.name}"
    )

    # Gemeinsame Quellen einmal lesen.
    beschreibungen = _skill_beschreibungen(repo, ziel_claude)
    globale_posten = _ladedateien_global(ziel_claude)
    repo_posten = _ladedateien_repo(repo)
    bekannte_skills = sitzung_filter.bekannte_skills(repo=repo, claude_dir=ziel_claude)

    # --- Bau-Session ------------------------------------------------------------
    default = bau.load_default()
    titel = str(eintrag.get("title") or f"Ticket {ticket_nr}")
    bau_prompt_text = bau.build_prompt(
        default["prompt_template"],
        ticket_nr,
        str(spec_nr),
        titel,
        bau.build_kontext(eintrag),
        konfig,
        repo=repo,
        gh_repo=slug,
    )
    bau_whitelist = tuple(
        dict.fromkeys(
            list(default.get("core_skills") or [])
            + list(eintrag.get("skills") or [])
            + config.repo_skills(konfig)
            + list(sitzung_filter.PFLICHT_SKILLS)
        )
    )
    bau_aus = len(bekannte_skills - set(bau_whitelist))
    bau_mcps = _bau_mcps(default, eintrag, repo, ziel_home, ziel_claude)
    bau_posten = sorted(
        [
            Posten(
                gruppe="prompt",
                name="Start-Prompt (bau)",
                zeichen=len(bau_prompt_text),
                tokens=schaetze_tokens(bau_prompt_text),
                quelle=str(bau.VORLAGE),
            ),
            *_skill_posten(bau_whitelist, beschreibungen),
            *globale_posten,
            *repo_posten,
            *_kontext_paket_posten(repo, eintrag),
        ],
        key=lambda p: (-p.tokens, p.name),
    )

    # --- Aufseher-Session -------------------------------------------------------
    wache_prompt_text = wache.prompt_bauen(spec_nr, slug, wache.TAKT_S, konfig, repo_ordner=repo)
    aufseher_whitelist, aufseher_overrides = sitzung_filter.aufseher_skill_auswahl(konfig, known=bekannte_skills)
    aufseher_skills = tuple(aufseher_whitelist)
    aufseher_aus = sum(1 for v in aufseher_overrides.values() if v == "off")
    aufseher_mcps = _aufseher_mcps(konfig, repo, ziel_home, ziel_claude)
    aufseher_posten = sorted(
        [
            Posten(
                gruppe="prompt",
                name="Start-Prompt (aufseher)",
                zeichen=len(wache_prompt_text),
                tokens=schaetze_tokens(wache_prompt_text),
                quelle=str(SKILL_WURZEL / "skripte" / "wache.py"),
            ),
            *_skill_posten(aufseher_skills, beschreibungen),
            *globale_posten,
            *repo_posten,
        ],
        key=lambda p: (-p.tokens, p.name),
    )

    # --- Transkripte ------------------------------------------------------------
    tr_je_typ: dict[str, list[TranskriptMessung]] = {"bau": [], "aufseher": []}
    if bau_transkripte or aufseher_transkripte:
        for pfad in bau_transkripte or []:
            tr_je_typ["bau"].append(transkript_messen(pfad))
        for pfad in aufseher_transkripte or []:
            tr_je_typ["aufseher"].append(transkript_messen(pfad))
    elif mit_sessions:
        tr_je_typ = _session_transkripte(repo, home=ziel_home, transkript_wurzel=transkript_wurzel)

    return {
        "bau": FensterMessung(
            typ="bau",
            obergrenze_k=grenzen["bau"],
            posten=tuple(bau_posten),
            skills_aktiv=bau_whitelist,
            skills_aus=bau_aus,
            mcp_aktiv=bau_mcps,
            transkripte=tuple(tr_je_typ["bau"]),
        ),
        "aufseher": FensterMessung(
            typ="aufseher",
            obergrenze_k=grenzen["aufseher"],
            posten=tuple(aufseher_posten),
            skills_aktiv=aufseher_skills,
            skills_aus=aufseher_aus,
            mcp_aktiv=aufseher_mcps,
            transkripte=tuple(tr_je_typ["aufseher"]),
        ),
    }


def _gruppen_summen(messung: FensterMessung) -> dict[str, int]:
    summen: dict[str, int] = {}
    for p in messung.posten:
        summen[p.gruppe] = summen.get(p.gruppe, 0) + p.tokens
    return summen


def als_dict(repo: Path, ergebnisse: dict[str, FensterMessung], *, top: int = 10) -> dict[str, Any]:
    """Maschinenlesbare Darstellung für ``--json`` und Tests."""
    ok = all(ergebnisse[typ].bestanden for typ in FENSTERTYPEN)
    fenster: dict[str, Any] = {}
    for typ in FENSTERTYPEN:
        m = ergebnisse[typ]
        gruppen = _gruppen_summen(m)
        fenster[typ] = {
            "obergrenze_k": m.obergrenze_k,
            "geschaetzt_tokens": m.geschaetzt_tokens,
            "geschaetzt_k": m.geschaetzt_k,
            "gemessen_tokens": m.gemessen_tokens,
            "gemessen_k": m.gemessen_k,
            "massgeblich_k": m.massgeblich_k,
            "quelle": m.quelle_massgeblich,
            "bestanden": m.bestanden,
            "skills_aktiv": list(m.skills_aktiv),
            "skills_aktiv_anzahl": len(m.skills_aktiv),
            "skills_aus": m.skills_aus,
            "mcp_aktiv": list(m.mcp_aktiv),
            "gruppen_tokens": gruppen,
            "top_posten": [
                {
                    "gruppe": p.gruppe,
                    "name": p.name,
                    "zeichen": p.zeichen,
                    "tokens": p.tokens,
                    "quelle": p.quelle,
                }
                for p in m.posten[: max(0, top)]
            ],
            "transkripte": [
                {
                    "name": t.name,
                    "pfad": t.pfad,
                    "start_tokens": t.start_tokens,
                    "start_k": round(t.start_tokens / 1000, 1),
                    "spitze_tokens": t.spitze_tokens,
                    "spitze_k": round(t.spitze_tokens / 1000, 1),
                    "aufrufe": t.aufrufe,
                }
                for t in m.transkripte
            ],
        }
    return {
        "repo": str(repo.resolve()),
        "grenzen_k": {typ: ergebnisse[typ].obergrenze_k for typ in FENSTERTYPEN},
        "ok": ok,
        "fenster": fenster,
    }


def formatiere_text(repo: Path, ergebnisse: dict[str, FensterMessung], *, top: int = 10) -> str:
    """Übersichtlicher Klartextbericht je Fenstertyp mit den größten Posten."""
    zeilen: list[str] = [f"Start-Last je Fenstertyp ({repo.resolve()})", ""]
    kopf = ("Fenster", "Start-Kontext", "Quelle", "Obergrenze", "Status", "Skills", "MCPs")
    reihen: list[tuple[str, ...]] = []
    for typ in FENSTERTYPEN:
        m = ergebnisse[typ]
        status = "ok" if m.bestanden else "UEBER GRENZE"
        skills_text = f"{len(m.skills_aktiv)} an / {m.skills_aus} aus"
        mcp_text = f"{len(m.mcp_aktiv)}" + (f" ({', '.join(m.mcp_aktiv)})" if m.mcp_aktiv else "")
        reihen.append(
            (
                typ,
                bau_log.kontext_text(m.massgeblich_k),
                m.quelle_massgeblich,
                bau_log.kontext_text(m.obergrenze_k),
                status,
                skills_text,
                mcp_text,
            )
        )
    breiten = [max(len(kopf[i]), *(len(r[i]) for r in reihen)) for i in range(len(kopf))]
    zeilen.append("  ".join(kopf[i].ljust(breiten[i]) for i in range(len(kopf))))
    zeilen.append("  ".join("-" * breiten[i] for i in range(len(kopf))))
    zeilen.extend("  ".join(r[i].ljust(breiten[i]) for i in range(len(kopf))) for r in reihen)

    for typ in FENSTERTYPEN:
        m = ergebnisse[typ]
        zeilen.append("")
        zeilen.append(
            f"## {typ} (geschätzt {bau_log.kontext_text(m.geschaetzt_k)}, Obergrenze {bau_log.kontext_text(m.obergrenze_k)})"
        )
        if m.transkripte:
            for tr in m.transkripte:
                zeilen.append(
                    f"  Transkript {tr.name}: Start {bau_log.kontext_text(round(tr.start_tokens / 1000, 1))}"
                    f" · Spitze {bau_log.kontext_text(round(tr.spitze_tokens / 1000, 1))}"
                    f" ({tr.aufrufe} Aufrufe)"
                )
        gruppen = _gruppen_summen(m)
        for schluessel, anzeige in GRUPPEN_NAMEN.items():
            tok = gruppen.get(schluessel, 0)
            if tok:
                zeilen.append(f"  - {anzeige}: {tok} Token ({bau_log.kontext_text(round(tok / 1000, 1))})")
        if top > 0 and m.posten:
            zeilen.append(f"  Größte Posten (Top {min(top, len(m.posten))}):")
            for p in m.posten[:top]:
                zeilen.append(f"    {p.tokens:>6} Token  [{p.gruppe}] {p.name} ({p.zeichen} Zeichen)")

    if not any(ergebnisse[typ].transkripte for typ in FENSTERTYPEN):
        zeilen.append("")
        zeilen.append(
            "Hinweis: Ohne Transkripte zeigt der Wert nur die statisch messbaren Posten "
            "(ohne Claude-Systemprompt und MCP-Werkzeugschemata). Für den vollen Laufzeit-Kontext "
            "Transkripte über .to-spawn/sessions/ oder --bau-transkript / --aufseher-transkript angeben."
        )
    return "\n".join(zeilen)


def parser_fuellen(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """CLI-Argumente für ``skripte/start_last.py`` und ``to_spawn.py start-last`` (eine Quelle)."""
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Ziel-Repo (Vorgabe: TO_SPAWN_REPO bzw. Git-Wurzel des aktuellen Ordners)",
    )
    parser.add_argument("--spec", type=int, default=None, help="Spec-Nummer (Manifest-Auswahl)")
    parser.add_argument("--ticket", default=None, help="Ticket-Nummer für die Bau-Messung")
    parser.add_argument(
        "--claude-dir",
        type=Path,
        default=None,
        help="Claude-Konfig-Ordner (Vorgabe: ~/.claude)",
    )
    parser.add_argument(
        "--bau-transkript",
        type=Path,
        action="append",
        default=[],
        help="Transkript-JSONL einer Bau-Session (mehrfach angebbar)",
    )
    parser.add_argument(
        "--aufseher-transkript",
        type=Path,
        action="append",
        default=[],
        help="Transkript-JSONL einer Aufseher-Session (mehrfach angebbar)",
    )
    parser.add_argument(
        "--transkripte",
        type=Path,
        default=None,
        help="Ordner mit Session-Transkripten (überschreibt ~/.claude/projects für .to-spawn/sessions/)",
    )
    parser.add_argument(
        "--ohne-transkripte",
        action="store_true",
        help="nur statische Posten messen, .to-spawn/sessions/ ignorieren",
    )
    parser.add_argument(
        "--grenze-bau",
        type=float,
        default=None,
        help=f"Obergrenze Bau-Session in k-Token (Vorgabe: {GRENZEN_K['bau']:g})",
    )
    parser.add_argument(
        "--grenze-aufseher",
        type=float,
        default=None,
        help=f"Obergrenze Aufseher-Session in k-Token (Vorgabe: {GRENZEN_K['aufseher']:g})",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="so viele größte Einzelposten je Fenstertyp anzeigen (Vorgabe: 10)",
    )
    parser.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")
    parser.add_argument(
        "--pruefen",
        action="store_true",
        help="Exit 1, wenn mindestens ein Fenstertyp seine Obergrenze überschreitet",
    )
    return parser


def lauf_mit_args(args: argparse.Namespace) -> int:
    """Führt den Messbefehl mit geparsten Argumenten aus."""
    konsole.utf8_ausgabe()
    if args.repo is not None:
        repo = args.repo.expanduser()
    elif os.environ.get("TO_SPAWN_REPO", "").strip():
        repo = Path(os.environ["TO_SPAWN_REPO"].strip()).expanduser()
    else:
        repo = config.repo_wurzel()

    try:
        ergebnisse = messen(
            repo,
            spec=args.spec,
            ticket=args.ticket,
            claude_dir=args.claude_dir,
            grenze_bau=args.grenze_bau,
            grenze_aufseher=args.grenze_aufseher,
            bau_transkripte=list(args.bau_transkript or []),
            aufseher_transkripte=list(args.aufseher_transkript or []),
            transkript_wurzel=args.transkripte,
            mit_sessions=not args.ohne_transkripte,
        )
    except EingabeFehler as fehler:
        sys.stderr.write(f"start-last: {fehler}\n")
        return EXIT_FEHLER

    if args.json:
        sys.stdout.write(json.dumps(als_dict(repo, ergebnisse, top=args.top), ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(formatiere_text(repo, ergebnisse, top=args.top) + "\n")

    if args.pruefen and not all(ergebnisse[typ].bestanden for typ in FENSTERTYPEN):
        return EXIT_UEBER_GRENZE
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    ap = parser_fuellen(
        argparse.ArgumentParser(
            prog="start_last",
            description="Misst die Start-Last (Kontext nach Start) je Fenstertyp (bau, aufseher).",
        )
    )
    return lauf_mit_args(ap.parse_args(argv))
