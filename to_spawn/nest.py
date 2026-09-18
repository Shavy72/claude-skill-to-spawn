"""Nest-Bau (#210): alles mit Logik, was ``nest/nest_server.sh`` und ``nest_push.sh`` brauchen.

Unterbefehle von ``to_spawn.py nest``:

* ``onboarding`` — ``~/.claude.json``: Erststart-Assistent überspringen, Ordner vertrauen.
* ``sandbox``    — ``<worktree>/.to-spawn/sandbox.json`` für ``srt`` (Sandbox je Worktree).
* ``secrets``    — ``.env`` aus Bitwarden Secrets Manager (``bws``), Werte nie ausgeben.
* ``werkzeuge``  — Unterbau und Werkzeuge aus ``.to-spawn/werkzeuge.json`` prüfen/installieren.
* ``rechte``     — Erlaubnis-Liste zeigen; eintragen darf nur ein Mensch im Terminal.
* ``auswahl``    — Skill-/MCP-Namen, die ``nest_push.sh`` mitschickt.
* ``einstellungen`` / ``mcp-export`` — Übertragung ohne Rechte, MCP-Schlüssel verdeckt.

Dieses Modul kennt kein bestimmtes Repo: Repo-Eigenes steht in ``.to-spawn/config.json``
und ``.to-spawn/nest_repo.sh`` des jeweiligen Repos.
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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from to_spawn import config, inventur

log = logging.getLogger("to_spawn.nest")

EXIT_FEHLT = 1
EXIT_FEHLER = 2
EXIT_NUR_MENSCH = 3
NUR_MENSCH = "Das darf nur ein Mensch im eigenen Terminal."

# --- gemeinsame Helfer -------------------------------------------------------------


class NestFehler(Exception):
    """Eingabe unlesbar oder Werkzeug gescheitert — nichts wurde überschrieben."""


def _lies_json_objekt(datei: Path) -> dict[str, Any]:
    """JSON-Objekt aus ``datei``; fehlt sie → leer, kaputt → ``NestFehler``."""
    if not datei.exists():
        return {}
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        raise NestFehler(f"{datei} unlesbar ({fehler}) — nichts überschrieben") from fehler
    if not isinstance(daten, dict):
        raise NestFehler(f"{datei} ist kein JSON-Objekt — nichts überschrieben")
    return daten


def _schreibe_atomar(datei: Path, text: str, modus: int | None = None) -> None:
    """Schreibt über eine Temp-Datei im selben Ordner und ``os.replace`` (nie halb)."""
    datei.parent.mkdir(parents=True, exist_ok=True)
    if modus is None:
        modus = datei.stat().st_mode & 0o777 if datei.exists() else 0o644
    griff, zwischen = tempfile.mkstemp(prefix=f".{datei.name}.", dir=str(datei.parent))
    try:
        os.fchmod(griff, modus)
        with os.fdopen(griff, "w", encoding="utf-8") as strom:
            strom.write(text)
        os.replace(zwischen, datei)
    except OSError:
        Path(zwischen).unlink(missing_ok=True)
        raise


def _json_text(daten: Any) -> str:
    return json.dumps(daten, indent=2, ensure_ascii=False) + "\n"


# --- 1. onboarding -------------------------------------------------------------------


def setze_onboarding(
    claude_json: Path,
    vertrauen: Iterable[str],
    theme: str = "dark",
    kanal: str = "terminal_bell",
) -> None:
    """Erststart-Assistent aus, Vertrauensfrage je Ordner beantwortet; Rest bleibt stehen.

    ``theme``/``preferredNotifChannel`` werden nur gesetzt, wenn sie fehlen — eine
    Wahl des Menschen bleibt.
    """
    ordner_liste = [str(o) for o in vertrauen]
    daten = _lies_json_objekt(claude_json)
    daten["hasCompletedOnboarding"] = True
    daten.setdefault("theme", theme)
    daten.setdefault("preferredNotifChannel", kanal)
    projekte = daten.setdefault("projects", {})
    if not isinstance(projekte, dict):
        raise NestFehler(f"{claude_json}: `projects` ist kein Objekt — nichts überschrieben")
    for ordner in ordner_liste:
        eintrag = projekte.setdefault(ordner, {})
        if isinstance(eintrag, dict):
            eintrag["hasTrustDialogAccepted"] = True
    _schreibe_atomar(claude_json, _json_text(daten), 0o600)
    log.info("Onboarding gesetzt: %s (%d Ordner vertraut)", claude_json, len(ordner_liste))


# --- 2. Sandbox je Worktree ------------------------------------------------------------

#: Netz-Vorgaben für jede Session: Anthropic, GitHub, Fehlerberichte, PyPI, npm.
NETZ_VORGABE: tuple[str, ...] = (
    "api.anthropic.com",
    "*.anthropic.com",
    "claude.ai",
    "*.claude.ai",
    "platform.claude.com",
    "github.com",
    "*.github.com",
    "api.github.com",
    "*.githubusercontent.com",
    "sentry.io",
    "*.sentry.io",
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "*.npmjs.org",
)
SANDBOX_DATEI = Path(".to-spawn") / "sandbox.json"
_TICKET_ENDE = re.compile(r"-(\d+)$")


def _git(ordner: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(ordner), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git_common_dir(hauptrepo: Path) -> Path | None:
    ergebnis = _git(hauptrepo, "rev-parse", "--git-common-dir")
    if ergebnis.returncode != 0 or not ergebnis.stdout.strip():
        log.warning("Git-Ordner von %s nicht lesbar — fehlt in der Sandbox", hauptrepo)
        return None
    pfad = Path(ergebnis.stdout.strip())
    return (pfad if pfad.is_absolute() else hauptrepo / pfad).resolve()


def _standard_zweig(hauptrepo: Path) -> str | None:
    ergebnis = _git(hauptrepo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if ergebnis.returncode == 0 and ergebnis.stdout.strip():
        return ergebnis.stdout.strip()
    for kandidat in ("origin/main", "origin/master"):
        if _git(hauptrepo, "rev-parse", "--verify", "--quiet", kandidat).returncode == 0:
            return kandidat
    return None


def worktree_von(ziel: str) -> tuple[Path, str | None]:
    """Ticket-Nummer oder Pfad → (Worktree-Pfad, Ticket-Nummer oder None)."""
    if ziel.isdigit():
        return Path(config.worktree_pfad(ziel)), ziel
    pfad = Path(ziel).expanduser().absolute()
    treffer = _TICKET_ENDE.search(pfad.name)
    return pfad, treffer.group(1) if treffer else None


def worktree_anlegen(worktree: Path, hauptrepo: Path, ticket: str | None) -> None:
    """Legt den Worktree an, wenn er fehlt; ein vorhandener bleibt unberührt."""
    if worktree.exists():
        return
    zweig = f"ticket-{ticket}" if ticket else worktree.name
    if _git(hauptrepo, "rev-parse", "--verify", "--quiet", zweig).returncode == 0:
        ergebnis = _git(hauptrepo, "worktree", "add", str(worktree), zweig)
    else:
        basis = _standard_zweig(hauptrepo)
        if basis is None:
            raise NestFehler(f"{hauptrepo}: kein origin/main oder origin/master")
        ergebnis = _git(hauptrepo, "worktree", "add", "-b", zweig, str(worktree), basis)
    if ergebnis.returncode != 0:
        raise NestFehler(f"git worktree add scheiterte: {ergebnis.stderr.strip()[:300]}")
    log.info("Worktree angelegt: %s (Zweig %s)", worktree, zweig)


def sandbox_einstellungen(
    worktree: Path,
    hauptrepo: Path,
    konfig: Mapping[str, Any],
    ticket: str | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """srt-Einstellungen: Schreiben nur im Worktree + nötigen Ordnern, Netz per Liste."""
    heim = home or Path.home()
    schreiben: list[str] = [str(worktree)]
    if ticket:
        for nachbar in sorted(worktree.parent.glob(f"*-{ticket}")):
            if nachbar.is_dir() and nachbar != worktree:
                schreiben.append(str(nachbar))
    gemeinsam = _git_common_dir(hauptrepo)
    if gemeinsam is not None:
        schreiben.append(str(gemeinsam))
    schreiben.append(str(hauptrepo / ".to-spawn"))
    for name in (".claude", ".claude.json", ".cache", ".npm"):
        schreiben.append(str(heim / name))
    schreiben.append("/tmp")
    sandbox = konfig.get("sandbox") if isinstance(konfig.get("sandbox"), dict) else {}
    zusatz = sandbox.get("netz_zusatz") if isinstance(sandbox, dict) else None
    netz = list(NETZ_VORGABE)
    for eintrag in zusatz if isinstance(zusatz, list) else []:
        if isinstance(eintrag, str) and eintrag and eintrag not in netz:
            netz.append(eintrag)
    return {
        "network": {"allowedDomains": netz, "deniedDomains": []},
        "filesystem": {
            "denyRead": [],
            "allowWrite": list(dict.fromkeys(schreiben)),
            "denyWrite": [],
        },
    }


def sandbox_vorbereiten(
    ziel: str,
    hauptrepo: Path,
    konfig: Mapping[str, Any] | None = None,
    home: Path | None = None,
) -> Path:
    """Worktree sicherstellen und seine ``sandbox.json`` schreiben. Rückgabe: Dateipfad."""
    hauptrepo = hauptrepo.resolve()
    worktree, ticket = worktree_von(ziel)
    worktree_anlegen(worktree, hauptrepo, ticket)
    worktree = worktree.resolve()
    einstellungen = sandbox_einstellungen(
        worktree, hauptrepo, konfig if konfig is not None else config.lade(hauptrepo),
        ticket, home,
    )
    datei = worktree / SANDBOX_DATEI
    _schreibe_atomar(datei, _json_text(einstellungen), 0o644)
    log.info(
        "Sandbox-Datei: %s (%d Schreib-Ordner, %d Netz-Ziele)",
        datei,
        len(einstellungen["filesystem"]["allowWrite"]),
        len(einstellungen["network"]["allowedDomains"]),
    )
    return datei


def sandbox_praefix(
    konfig: Mapping[str, Any],
    worktree: str,
    hauptrepo: Path,
    which: Callable[[str], str | None] = shutil.which,
    trocken: bool = False,
) -> list[str]:
    """Befehls-Präfix ``[srt, --settings, <datei>, --]`` für ``bau.py`` — oder leer.

    ``--`` ist Pflicht: ohne ihn liest srt das ``--settings`` von claude als sein eigenes.

    Leer bei ``sandbox.modus`` ≠ "an", wenn ``srt``/``bwrap`` fehlen oder der Worktree
    nicht vorbereitet werden kann (jeweils mit Warnung). ``trocken``: nichts schreiben.
    """
    sandbox = konfig.get("sandbox")
    modus = sandbox.get("modus", "an") if isinstance(sandbox, dict) else "an"
    if modus != "an":
        return []
    srt, bwrap = which("srt"), which("bwrap")
    if not srt or not bwrap:
        log.warning(
            "Sandbox ist an, aber %s fehlt — Session läuft OHNE Sandbox. "
            "Abhilfe: `to_spawn.py nest werkzeuge --installieren`.",
            " und ".join(n for n, p in (("srt", srt), ("bwrap", bwrap)) if not p),
        )
        return []
    if trocken:
        pfad, _ticket = worktree_von(worktree)
        return [srt, "--settings", str(pfad / SANDBOX_DATEI), "--"]
    try:
        datei = sandbox_vorbereiten(worktree, hauptrepo, konfig)
    except (NestFehler, OSError) as fehler:
        log.warning("Sandbox nicht vorbereitet (%s) — Session läuft OHNE Sandbox.", fehler)
        return []
    return [srt, "--settings", str(datei), "--"]


# --- 3. Secrets über bws -------------------------------------------------------------

TOKEN_DATEI = Path(".config") / "to-spawn" / "bws_token"
TOKEN_FEHLT = (
    "Setup-Zeile: bws — Machine-Account-Token fehlt → Bitwarden-Web → Secrets Manager → "
    "Machine account → Access token erzeugen, dann als Datei ~/.config/to-spawn/bws_token "
    "(chmod 600) ablegen oder BWS_ACCESS_TOKEN setzen. Den Token nie in den Chat kopieren."
)
_ENV_ZEILE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")
_SICHER = re.compile(r"^[A-Za-z0-9_./:@+,=-]*$")


def token_finden(environ: Mapping[str, str], home: Path) -> str | None:
    """Machine-Account-Token aus der Umgebung oder ``~/.config/to-spawn/bws_token``."""
    wert = environ.get("BWS_ACCESS_TOKEN", "").strip()
    if wert:
        return wert
    datei = home / TOKEN_DATEI
    if not datei.is_file():
        return None
    if datei.stat().st_mode & 0o077:
        log.warning("%s ist für andere lesbar — `chmod 600` ausführen", datei)
    return datei.read_text(encoding="utf-8").strip() or None


def _env_wert(wert: str) -> str:
    if _SICHER.match(wert):
        return wert
    maskiert = wert.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{maskiert}"'


def _bws_geheimnisse(
    bws: str, projekt: str | None, token: str, environ: Mapping[str, str]
) -> dict[str, str]:
    befehl = [bws, "secret", "list", *([projekt] if projekt else []), "--output", "json"]
    umgebung = {**environ, "BWS_ACCESS_TOKEN": token}
    try:
        ergebnis = subprocess.run(
            befehl, env=umgebung, capture_output=True, text=True, check=False, timeout=120
        )
    except (OSError, subprocess.TimeoutExpired) as fehler:
        raise NestFehler(f"bws nicht startbar ({type(fehler).__name__})") from fehler
    if ergebnis.returncode != 0:
        grund = ergebnis.stderr.strip().replace(token, "***")[:200]
        raise NestFehler(f"bws scheiterte (Exit {ergebnis.returncode}): {grund}")
    try:
        liste = json.loads(ergebnis.stdout)
    except ValueError as fehler:
        raise NestFehler("bws lieferte kein JSON") from fehler
    if not isinstance(liste, list):
        raise NestFehler("bws lieferte keine Liste")
    werte: dict[str, str] = {}
    for eintrag in liste:
        if not isinstance(eintrag, dict):
            continue
        schluessel, wert = eintrag.get("key"), eintrag.get("value")
        if isinstance(schluessel, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schluessel):
            werte[schluessel] = wert if isinstance(wert, str) else ""
        else:
            log.warning("bws-Eintrag ohne gültigen Schlüssel-Namen übersprungen")
    return werte


def hole_secrets(
    ziel: Path,
    projekt: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> int:
    """``.env`` aus bws: gleiche Schlüssel überschreiben, übrige Zeilen bleiben.

    Gibt nur Anzahl und Schlüssel-NAMEN aus. Bei jedem Fehler bleibt ``ziel`` unberührt.
    """
    umgebung = dict(environ if environ is not None else os.environ)
    heim = home or Path(umgebung.get("HOME") or Path.home())
    token = token_finden(umgebung, heim)
    if token is None:
        print(TOKEN_FEHLT)
        return EXIT_FEHLT
    bws = which("bws")
    if bws is None:
        print("Setup-Zeile: bws fehlt → `nest/nest_server.sh` installiert es (GitHub-Release).")
        return EXIT_FEHLT
    try:
        werte = _bws_geheimnisse(bws, projekt, token, umgebung)
    except NestFehler as fehler:
        log.error("%s — %s bleibt unverändert.", fehler, ziel)
        return EXIT_FEHLER
    alt = ziel.read_text(encoding="utf-8").splitlines() if ziel.exists() else []
    neu: list[str] = []
    gesehen: set[str] = set()
    nur_env: list[str] = []
    for zeile in alt:
        treffer = _ENV_ZEILE.match(zeile.strip())
        if treffer and treffer.group(1) in werte:
            name = treffer.group(1)
            if name not in gesehen:
                neu.append(f"{name}={_env_wert(werte[name])}")
                gesehen.add(name)
            continue
        if treffer:
            nur_env.append(treffer.group(1))
        neu.append(zeile)
    for name, wert in werte.items():
        if name not in gesehen:
            neu.append(f"{name}={_env_wert(wert)}")
    _schreibe_atomar(ziel, "\n".join(neu) + "\n", 0o600)
    print(f"{len(werte)} Schlüssel aus bws nach {ziel}: {', '.join(sorted(werte)) or '—'}")
    if nur_env:
        print(f"Nur in .env, nicht in bws (bleiben stehen): {', '.join(sorted(set(nur_env)))}")
    return 0


# --- 4. Werkzeuge aus der Inventur ----------------------------------------------------


@dataclass(frozen=True)
class Rezept:
    """Wie ein Unterbau auf Debian/Ubuntu kommt. Erweiterbar: neue Zeile = neues Rezept.

    ``weg``: "apt" (Paket), "npm" (global) oder "skript" (macht ``nest_server.sh``).
    """

    name: str
    befehl: str
    weg: str
    paket: str = ""
    hinweis: str = ""


#: Neben ``inventur.UNTERBAUTEN``: gleiche Namen, hier nur der Installationsweg.
REZEPTE: tuple[Rezept, ...] = (
    Rezept("ffmpeg", "ffmpeg", "apt", "ffmpeg"),
    Rezept("adb", "adb", "apt", "adb"),
    Rezept("gh", "gh", "apt", "gh", "apt-Quelle von cli.github.com legt nest_server.sh an"),
    Rezept("codex", "codex", "npm", "@openai/codex", "Anmeldung bleibt: `codex login`"),
    Rezept("bubblewrap", "bwrap", "apt", "bubblewrap"),
    Rezept("socat", "socat", "apt", "socat"),
    Rezept("ripgrep", "rg", "apt", "ripgrep"),
    Rezept("srt", "srt", "npm", "@anthropic-ai/sandbox-runtime"),
    Rezept("bws", "bws", "skript", hinweis="nest_server.sh lädt es aus dem GitHub-Release"),
)


@dataclass(frozen=True)
class Zeile:
    name: str
    zustand: str  # da | installiert | fehlt
    abhilfe: str = ""


@dataclass
class WerkzeugBericht:
    repo: Path
    unterbau: list[Zeile] = field(default_factory=list)
    werkzeuge_da: int = 0
    werkzeuge_fehlen: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        fehlt = any(z.zustand == "fehlt" for z in self.unterbau) or self.werkzeuge_fehlen
        return EXIT_FEHLT if fehlt else 0

    def text(self) -> str:
        zeilen = [f"Werkzeuge für {self.repo}", "", "## Unterbau"]
        for z in self.unterbau:
            ende = f" → {z.abhilfe}" if z.abhilfe and z.zustand == "fehlt" else ""
            zeilen.append(f"  {z.zustand:<11} {z.name}{ende}")
        zeilen += [
            "",
            (
                f"## Skills/Agenten/MCPs aus werkzeuge.json: {self.werkzeuge_da} da, "
                f"{len(self.werkzeuge_fehlen)} fehlen hier"
            ),
        ]
        for name in self.werkzeuge_fehlen:
            zeilen.append(f"  fehlt hier  {name} → per nest_push.sh mitschicken")
        for hinweis in self.hinweise:
            zeilen.append(f"  Hinweis: {hinweis}")
        return "\n".join(zeilen)


def _installiere_befehl(rezept: Rezept, ist_root: bool) -> list[str] | None:
    vorne = [] if ist_root else ["sudo"]
    if rezept.weg == "apt":
        return [*vorne, "apt-get", "install", "-y", "-qq", rezept.paket]
    if rezept.weg == "npm":
        return [*vorne, "npm", "install", "-g", rezept.paket]
    return None


def _abhilfe(rezept: Rezept) -> str:
    if rezept.weg == "apt":
        text = f"`sudo apt-get install {rezept.paket}`"
    elif rezept.weg == "npm":
        text = f"`sudo npm install -g {rezept.paket}`"
    else:
        text = "nest_server.sh erneut laufen lassen"
    return f"{text} ({rezept.hinweis})" if rezept.hinweis else text


def _freigabe(repo: Path) -> tuple[set[str], set[str] | None]:
    """(abgewählt, gewünschte Namen) aus ``werkzeuge.json``; ohne Datei → (leer, None)."""
    datei = repo / inventur.AUSGABE_PFAD
    abgewaehlt = inventur.lies_abwahl(datei)
    if not datei.exists():
        return abgewaehlt, None
    daten = json.loads(datei.read_text(encoding="utf-8"))
    kategorien = daten.get("kategorien") if isinstance(daten, dict) else None
    namen: set[str] = set()
    for liste in kategorien.values() if isinstance(kategorien, dict) else []:
        if isinstance(liste, list):
            namen |= {n for n in liste if isinstance(n, str)}
    return abgewaehlt, namen - abgewaehlt


def pruefe_werkzeuge(
    repo: Path,
    *,
    claude_home: Path | None = None,
    home_json: Path | None = None,
    which: Callable[[str], str | None] = shutil.which,
    ausfuehren: Callable[[list[str]], int] | None = None,
    installieren: bool = False,
    ist_root: bool | None = None,
) -> WerkzeugBericht:
    """Unterbau prüfen (optional installieren) und Werkzeuge gegen diese Maschine halten."""
    heim = Path.home()
    claude_home = claude_home or heim / ".claude"
    home_json = home_json or heim / ".claude.json"
    ausfuehren = ausfuehren or inventur._fuehre_aus
    root = (os.geteuid() == 0) if ist_root is None else ist_root
    bericht = WerkzeugBericht(repo)
    abgewaehlt, gewuenscht = _freigabe(repo)

    for rezept in REZEPTE:
        if rezept.name in abgewaehlt:
            continue
        if which(rezept.befehl):
            bericht.unterbau.append(Zeile(rezept.name, "da"))
            continue
        befehl = _installiere_befehl(rezept, root) if installieren else None
        if befehl is not None:
            code = ausfuehren(befehl)
            if code == 0 and which(rezept.befehl):
                bericht.unterbau.append(Zeile(rezept.name, "installiert"))
                continue
            log.warning("Installation von %s scheiterte (Exit %s)", rezept.name, code)
        bericht.unterbau.append(Zeile(rezept.name, "fehlt", _abhilfe(rezept)))

    if gewuenscht is None:
        bericht.hinweise.append(
            f"keine {inventur.AUSGABE_PFAD} — nur Unterbau geprüft "
            "(`to_spawn.py inventur --schreiben` legt sie an)"
        )
        return bericht
    katalog = inventur.lies_katalog(repo, claude_home, home_json)
    vorhanden = {w.name for w in katalog.werkzeuge}
    bericht.werkzeuge_fehlen = sorted(gewuenscht - vorhanden)
    bericht.werkzeuge_da = len(gewuenscht & vorhanden)
    return bericht


def auswahl(repo: Path, art: str, claude_home: Path, home_json: Path) -> list[str]:
    """Namen, die ``nest_push.sh`` mitschickt: Skill-Ordner bzw. MCP-Server dieser Maschine.

    Mit ``werkzeuge.json``: nur freigegebene; ohne: alle vorhandenen.
    """
    _abgewaehlt, gewuenscht = _freigabe(repo)
    if art == "skill":
        ordner = claude_home / "skills"
        kandidaten = [
            k.name
            for k in (sorted(ordner.iterdir()) if ordner.is_dir() else [])
            if k.is_dir() and not k.name.startswith("_") and (k / "SKILL.md").is_file()
        ]
    else:
        daten = _lies_json_objekt(home_json)
        server = daten.get("mcpServers")
        kandidaten = sorted(server) if isinstance(server, dict) else []
    if gewuenscht is None:
        return kandidaten
    return [n for n in kandidaten if n in gewuenscht]


# --- Übertragung: settings.json und MCP-Einträge ----------------------------------------

MCP_FELDER: tuple[str, ...] = ("type", "command", "args", "env", "url", "headers")


def uebernehme_einstellungen(quelle: Path, ziel: Path) -> None:
    """``quelle`` → ``ziel``, aber ``permissions`` kommen NIE aus der Quelle.

    Hat ``ziel`` schon ``permissions`` (vom Menschen eingetragen), bleiben sie stehen.
    """
    neu = _lies_json_objekt(quelle)
    if not quelle.exists():
        raise NestFehler(f"{quelle} fehlt")
    alt = _lies_json_objekt(ziel)
    neu.pop("permissions", None)
    if "permissions" in alt:
        neu["permissions"] = alt["permissions"]
    _schreibe_atomar(ziel, _json_text(neu))
    log.info("Einstellungen übernommen: %s (Rechte bleiben Sache des Menschen)", ziel)


def mcp_export(home_json: Path, namen: Sequence[str], ziel: Path) -> list[str]:
    """MCP-Einträge aus ``~/.claude.json`` nach ``ziel`` (chmod 600). Werte nie ausgeben.

    ``chrome-devtools`` bekommt ``--headless`` (Server ohne Bildschirm).
    """
    server = _lies_json_objekt(home_json).get("mcpServers")
    alle = server if isinstance(server, dict) else {}
    raus: dict[str, Any] = {}
    for name in namen:
        eintrag = alle.get(name)
        if not isinstance(eintrag, dict):
            log.warning("MCP %s hier nicht gefunden — nicht mitgeschickt", name)
            continue
        eintrag = {k: v for k, v in eintrag.items() if k in MCP_FELDER}
        if name == "chrome-devtools":
            argumente = list(eintrag.get("args") or [])
            if "--headless" not in argumente:
                argumente.append("--headless")
            eintrag["args"] = argumente
        raus[name] = eintrag
    _schreibe_atomar(ziel, _json_text(raus), 0o600)
    return list(raus)


# --- 5. Rechte: nur ein Mensch trägt ein ----------------------------------------------

RECHTE_ERLAUBEN: tuple[str, ...] = (
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(git add:*)",
    "Bash(git commit:*)",
    "Bash(git fetch:*)",
    "Bash(git rebase:*)",
    "Bash(git worktree:*)",
    "Bash(git branch:*)",
    "Bash(git rev-parse:*)",
    "Bash(gh issue view:*)",
    "Bash(gh issue edit:*)",
    "Bash(gh issue comment:*)",
    "Bash(gh issue list:*)",
    "Bash(gh pr view:*)",
)


def rechte_vorschlag(wt_dir: Path, skill_dir: Path, temp: Path) -> dict[str, list[str]]:
    return {
        "allow": list(RECHTE_ERLAUBEN),
        "additionalDirectories": [str(wt_dir), str(skill_dir), str(temp)],
    }


def rechte_text(datei: Path, vorschlag: Mapping[str, list[str]], cli: Path) -> str:
    return "\n".join(
        [
            "Rechte-Schritt — nur ein Mensch kann das (keine Session, kein Skript).",
            f"Ziel-Datei: {datei}",
            "Vorschlag für `permissions`:",
            json.dumps({"permissions": vorschlag}, indent=2, ensure_ascii=False),
            "",
            "Selbst im eigenen Terminal eintippen (fragt nach `JA`):",
            f"  python3 {cli} nest rechte --eintragen",
        ]
    )


def rechte_eintragen(
    datei: Path,
    vorschlag: Mapping[str, list[str]],
    ist_tty: bool,
    eingabe: Callable[[str], str] = input,
) -> int:
    """Trägt den Vorschlag ein — nur mit Terminal und wörtlichem ``JA``. Sonst Exit 3."""
    if not ist_tty:
        print(NUR_MENSCH, file=sys.stderr)
        return EXIT_NUR_MENSCH
    antwort = eingabe(f"In {datei} eintragen? Wörtlich JA tippen: ").strip()
    if antwort != "JA":
        print(f"Nichts eingetragen. {NUR_MENSCH}", file=sys.stderr)
        return EXIT_NUR_MENSCH
    try:
        daten = _lies_json_objekt(datei)
    except NestFehler as fehler:
        print(str(fehler), file=sys.stderr)
        return EXIT_FEHLER
    rechte = daten.setdefault("permissions", {})
    if not isinstance(rechte, dict):
        print(f"{datei}: `permissions` ist kein Objekt — nichts geschrieben", file=sys.stderr)
        return EXIT_FEHLER
    neu = 0
    for schluessel, werte in vorschlag.items():
        liste = rechte.setdefault(schluessel, [])
        if not isinstance(liste, list):
            print(f"{datei}: `permissions.{schluessel}` ist keine Liste", file=sys.stderr)
            return EXIT_FEHLER
        for wert in werte:
            if wert not in liste:
                liste.append(wert)
                neu += 1
    _schreibe_atomar(datei, _json_text(daten))
    print(f"{neu} Einträge ergänzt in {datei}.")
    return 0


# --- CLI -------------------------------------------------------------------------------


def richte_parser_ein(unter: Any) -> None:
    """Hängt ``nest`` mit Unterbefehlen an den Parser von ``to_spawn.py``."""
    p_nest = unter.add_parser("nest", help="Nest-Bau: Server-Einrichtung, Sandbox, Secrets")
    nest_unter = p_nest.add_subparsers(dest="nest_befehl", required=True)

    p_on = nest_unter.add_parser("onboarding", help="~/.claude.json: Erststart + Vertrauen")
    p_on.add_argument("--claude-json", type=Path, default=Path.home() / ".claude.json")
    p_on.add_argument("--trust", action="append", default=[], help="Ordner (mehrfach)")
    p_on.add_argument("--theme", default="dark")
    p_on.add_argument("--kanal", default="terminal_bell", help="preferredNotifChannel")

    p_sb = nest_unter.add_parser("sandbox", help="sandbox.json für einen Worktree schreiben")
    p_sb.add_argument("ziel", help="Ticket-Nummer oder Worktree-Pfad")
    p_sb.add_argument("--repo", type=Path, help="Hauptrepo (sonst Git-Wurzel hier)")

    p_se = nest_unter.add_parser("secrets", help=".env aus Bitwarden Secrets Manager")
    p_se.add_argument("--ziel", type=Path, required=True, help="Pfad der .env")
    p_se.add_argument("--projekt", help="bws-Projekt-ID (sonst nest.bws_projekt der Konfig)")

    p_wz = nest_unter.add_parser("werkzeuge", help="Unterbau + Werkzeuge prüfen")
    p_wz.add_argument("--installieren", action="store_true", help="fehlenden Unterbau holen")
    p_wz.add_argument("--repo", type=Path)

    p_re = nest_unter.add_parser("rechte", help="Erlaubnis-Liste zeigen (Mensch trägt ein)")
    p_re.add_argument("--eintragen", action="store_true", help="nur im eigenen Terminal")

    p_ei = nest_unter.add_parser("einstellungen", help="settings.json übernehmen, Rechte nie")
    p_ei.add_argument("--quelle", type=Path, required=True)
    p_ei.add_argument("--ziel", type=Path, required=True)

    p_mx = nest_unter.add_parser("mcp-export", help="MCP-Einträge für den Server (chmod 600)")
    p_mx.add_argument("--ziel", type=Path, required=True)
    p_mx.add_argument("namen", nargs="*")

    p_aw = nest_unter.add_parser("auswahl", help="Namen für nest_push.sh")
    p_aw.add_argument("--art", choices=["skill", "mcp"], required=True)
    p_aw.add_argument("--repo", type=Path)


def lauf(args: argparse.Namespace) -> int:
    """Führt ``to_spawn.py nest <unterbefehl>`` aus."""
    heim = Path(os.environ.get("HOME") or Path.home())
    befehl = args.nest_befehl
    try:
        if befehl == "onboarding":
            setze_onboarding(args.claude_json, list(args.trust), args.theme, args.kanal)
            return 0
        if befehl == "sandbox":
            repo = (args.repo or config.repo_wurzel()).resolve()
            print(sandbox_vorbereiten(args.ziel, repo, home=heim))
            return 0
        if befehl == "secrets":
            projekt = args.projekt
            if not projekt:
                wert = config.lade(args.ziel.parent).get("nest", {}).get("bws_projekt")
                projekt = wert if isinstance(wert, str) and wert else None
            return hole_secrets(args.ziel, projekt, home=heim)
        if befehl == "werkzeuge":
            repo = (args.repo or config.repo_wurzel()).resolve()
            bericht = pruefe_werkzeuge(
                repo,
                claude_home=heim / ".claude",
                home_json=heim / ".claude.json",
                installieren=args.installieren,
            )
            print(bericht.text())
            return bericht.exit_code
        if befehl == "einstellungen":
            uebernehme_einstellungen(args.quelle, args.ziel)
            return 0
        if befehl == "mcp-export":
            namen = mcp_export(heim / ".claude.json", list(args.namen), args.ziel)
            print(f"MCPs vorbereitet: {', '.join(namen) or '—'} (Schlüssel bleiben verdeckt)")
            return 0
        if befehl == "auswahl":
            repo = (args.repo or config.repo_wurzel()).resolve()
            namen = auswahl(repo, args.art, heim / ".claude", heim / ".claude.json")
            print("\n".join(namen))
            return 0
        if befehl == "rechte":
            datei = heim / ".claude" / "settings.json"
            wt_dir = Path(config.worktree_pfad("0")).parent
            skill = Path(__file__).resolve().parent.parent
            vorschlag = rechte_vorschlag(wt_dir, skill, Path(tempfile.gettempdir()))
            if args.eintragen:
                ist_tty = sys.stdin.isatty() and sys.stdout.isatty()
                return rechte_eintragen(datei, vorschlag, ist_tty)
            print(rechte_text(datei, vorschlag, skill / "to_spawn.py"))
            return 0
    except (NestFehler, inventur.WerkzeugeKaputt) as fehler:
        log.error("%s", fehler)
        return EXIT_FEHLER
    log.error("Unbekannter nest-Befehl: %s", befehl)
    return EXIT_FEHLER


__all__: Sequence[str] = (
    "NETZ_VORGABE",
    "REZEPTE",
    "auswahl",
    "hole_secrets",
    "mcp_export",
    "pruefe_werkzeuge",
    "rechte_eintragen",
    "rechte_vorschlag",
    "sandbox_praefix",
    "sandbox_vorbereiten",
    "setze_onboarding",
    "uebernehme_einstellungen",
)
