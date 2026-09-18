"""Setup-Wizard Teil 2 (#209): Werkzeug-Inventur.

Echt laufen: Git (``git ls-files``), Dateibaum-Leser für Skills, Plugins, Agenten,
MCP-Server und Session-Historie, die CLI per Unterprozess. Gestellt sind nur die
Ordner-Bäume selbst (``tmp_path`` = Fixture-Daten, ein Wegwerf-``HOME``) und ein
Wegwerf-``PATH`` mit leeren ausführbaren Dateien. Der Weg-Test läuft ohne
Attrappen gegen das echte Repo und das echte ``~/.claude``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"

sys.path.insert(0, str(SKILL))

from to_spawn import inventur

KATEGORIEN = [
    "bauen",
    "testen",
    "design",
    "recherche",
    "medien",
    "deploy",
    "projekt",
    "sicherheit",
    "kontext",
    "steuerung",
    "sehen",
    "zweitmeinung",
]


# --- Fixture-Bäume -------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def _skill(ordner: Path, name: str, beschreibung: str) -> None:
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {beschreibung}\n---\n\n# {name}\n", encoding="utf-8"
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    arbeit = tmp_path / "projekt-repo"
    arbeit.mkdir()
    _git(arbeit, "init")
    (arbeit / "app.py").write_text("print('x')\n", encoding="utf-8")
    (arbeit / "werk.py").write_text("x = 1\n", encoding="utf-8")
    (arbeit / "Dockerfile").write_text("FROM python\n", encoding="utf-8")
    (arbeit / "web" / "templates").mkdir(parents=True)
    (arbeit / "web" / "templates" / "seite.html").write_text("<p/>\n", encoding="utf-8")
    (arbeit / "CLAUDE.md").write_text("Regeln: immer `hausregel` nutzen.\n", encoding="utf-8")
    _git(arbeit, "add", "-A")
    return arbeit


@pytest.fixture()
def heim(tmp_path: Path) -> Path:
    """Wegwerf-HOME mit leerem ``.claude``-Baum."""
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    (home / ".claude.json").write_text("{}", encoding="utf-8")
    return home


def _slug(repo: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(repo.resolve()))


def _pfad_mit(tmp_path: Path, *befehle: str) -> str:
    """Wegwerf-PATH, in dem genau diese Befehle als ausführbare Dateien liegen."""
    ordner = tmp_path / "bin"
    ordner.mkdir(exist_ok=True)
    for befehl in befehle:
        datei = ordner / befehl
        datei.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        datei.chmod(0o755)
    return str(ordner)


def _erstelle(repo: Path, heim: Path, environ: dict[str, str] | None = None) -> inventur.Inventur:
    return inventur.erstelle(
        repo,
        claude_home=heim / ".claude",
        home_json=heim / ".claude.json",
        home=heim,
        environ=environ if environ is not None else {"PATH": ""},
    )


def _werkzeug(inv: inventur.Inventur, name: str) -> inventur.Werkzeug:
    treffer = [w for w in inv.werkzeuge if w.name == name]
    assert treffer, f"{name} fehlt in {[w.name for w in inv.werkzeuge]}"
    return treffer[0]


# --- Einordnung -------------------------------------------------------------------

EINORDNUNG = [
    ("refactor-helfer", "Hilft beim Umbauen von Funktionen", "bauen"),
    ("mp-tdd", "Test-driven development", "testen"),
    ("ds-anti-slop-ui", "Frontend ohne Slop", "design"),
    ("tief-recherche", "Recherche mit Perplexity", "recherche"),
    ("ffmpeg-production", "Videos schneiden und Ton mischen", "medien"),
    ("vps-deploy", "Deploy auf den Server", "deploy"),
    ("duoplus-api", "DuoPlus Cloud-Phones steuern", "projekt"),
    ("hausregel", "Etwas ganz Eigenes", "projekt"),
    ("bitwarden-keys", "Secrets aus dem Tresor holen", "sicherheit"),
    ("graphify", "Wissensgraph des Codes", "kontext"),
    ("to-spawn", "Sessions starten, Wache aufstellen", "steuerung"),
    ("screenshot-leser", "Bildschirmfotos verstehen", "sehen"),
    ("codex-consult", "Zweitmeinung von Codex holen", "zweitmeinung"),
]


@pytest.mark.parametrize(("name", "beschreibung", "erwartet"), EINORDNUNG)
def test_einordnung_je_kategorie(
    repo: Path, heim: Path, name: str, beschreibung: str, erwartet: str
) -> None:
    _skill(heim / ".claude" / "skills" / name, name, beschreibung)
    inv = _erstelle(repo, heim)
    w = _werkzeug(inv, name)
    assert w.kategorie == erwartet
    assert w.art == "skill"
    assert w.an is True


def test_katalog_liest_plugins_agenten_mcp_und_ueberspringt_ablage(repo: Path, heim: Path) -> None:
    claude = heim / ".claude"
    _skill(claude / "skills" / "_alt" / "alter-stand", "alter-stand", "Sicherung")
    _skill(claude / "skills" / "synced" / "gespiegelt", "gespiegelt", "Spiegel")
    # Symlink auf einen Skill-Ordner außerhalb wird verfolgt.
    _skill(heim / "extern" / "verlinkt", "verlinkt", "Screenshot ansehen")
    (claude / "skills" / "verlinkt").symlink_to(heim / "extern" / "verlinkt")
    plugin = heim / "plugin-bau" / "ctxp"
    _skill(plugin / "skills" / "merker", "merker", "Kontext durchsuchen")
    (claude / "plugins").mkdir()
    (claude / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "ctxp@markt": [{"installPath": str(plugin)}],
                    "kaputt@markt": [{"ohnePfad": True}],
                    "leer@markt": "kein-array",
                },
            }
        ),
        encoding="utf-8",
    )
    (claude / "agents").mkdir()
    (claude / "agents" / "executor-opus.md").write_text(
        "---\nname: executor-opus\ndescription: baut Code\n---\n", encoding="utf-8"
    )
    (heim / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {"firecrawl": {}, "chrome-devtools": {}},
                "projects": {str(repo.resolve()): {"mcpServers": {"graphify-mcp": {}}}},
            }
        ),
        encoding="utf-8",
    )
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"postproxy": {}}}), "utf-8")

    inv = _erstelle(repo, heim)
    namen = {w.name for w in inv.werkzeuge}
    assert "alter-stand" not in namen and "gespiegelt" not in namen
    assert _werkzeug(inv, "verlinkt").kategorie == "sehen"
    assert _werkzeug(inv, "ctxp:merker").art == "plugin-skill"
    assert _werkzeug(inv, "ctxp:merker").kategorie == "kontext"
    assert _werkzeug(inv, "executor-opus").art == "agent"
    assert _werkzeug(inv, "firecrawl").kategorie == "recherche"
    assert _werkzeug(inv, "chrome-devtools").kategorie == "sehen"
    assert _werkzeug(inv, "graphify-mcp").kategorie == "kontext"
    assert _werkzeug(inv, "postproxy").kategorie == "projekt"
    assert _werkzeug(inv, "postproxy").quelle.endswith(".mcp.json")
    assert _werkzeug(inv, "firecrawl").quelle == "~/.claude.json"


def test_alle_zwoelf_kategorien_immer_in_reihenfolge(repo: Path, heim: Path) -> None:
    inv = _erstelle(repo, heim)
    daten = inv.als_dict()
    assert [k["schluessel"] for k in daten["kategorien"]] == KATEGORIEN
    text = inv.text()
    positionen = [text.index(f"## {anzeige}") for anzeige in inventur.KATEGORIEN.values()]
    assert positionen == sorted(positionen)
    assert list(inventur.KATEGORIEN) == KATEGORIEN


def test_tote_winkel_bei_leerer_kategorie(repo: Path, heim: Path) -> None:
    _skill(heim / ".claude" / "skills" / "mp-tdd", "mp-tdd", "Test-driven development")
    inv = _erstelle(repo, heim)
    assert any("„Medien“ leer" in z and "Vorschlag" in z for z in inv.tote_winkel)
    assert not any("„Testen/Beweisen“ leer" in z for z in inv.tote_winkel)
    assert "Tote Winkel" in inv.text()


# --- Historie ---------------------------------------------------------------------


def _tool_use(name: str, eingabe: dict[str, object]) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": eingabe}]},
        }
    )


def test_historie_zaehlt_skill_agent_mcp_und_ueberspringt_kaputtes(repo: Path, heim: Path) -> None:
    claude = heim / ".claude"
    _skill(claude / "skills" / "mp-tdd", "mp-tdd", "Test-driven development")
    plugin = heim / "plugin-bau" / "ctxp"
    _skill(plugin / "skills" / "suche", "suche", "Kontext durchsuchen")
    (claude / "plugins").mkdir()
    (claude / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"ctxp@markt": [{"installPath": str(plugin)}]}}), "utf-8"
    )
    (claude / "agents").mkdir()
    (claude / "agents" / "executor-opus.md").write_text("# ohne Frontmatter\n", "utf-8")
    (heim / ".claude.json").write_text(json.dumps({"mcpServers": {"firecrawl": {}}}), "utf-8")
    projekt = claude / "projects" / _slug(repo)
    projekt.mkdir(parents=True)
    zeilen = [
        _tool_use("Skill", {"skill": "mp-tdd"}),
        _tool_use("Skill", {"skill": "mp-tdd"}),
        _tool_use("Skill", {"skill": "ctxp:suche"}),
        _tool_use("Skill", {"skill": "nie-installiert"}),
        _tool_use("Agent", {"subagent_type": "executor-opus"}),
        _tool_use("mcp__firecrawl__firecrawl_scrape", {"url": "x"}),
        _tool_use("mcp__firecrawl__firecrawl_search", {"q": "x"}),
        _tool_use("Bash", {"command": "cd /x && ffmpeg -i a.mp4 b.mp4 | tail -3"}),
        "{kaputte zeile",
        "",
        json.dumps(["kein", "objekt"]),
        json.dumps({"message": {"content": "nur text"}}),
    ]
    (projekt / "sitzung.jsonl").write_text("\n".join(zeilen) + "\n", encoding="utf-8")

    inv = _erstelle(repo, heim)
    assert _werkzeug(inv, "mp-tdd").genutzt == 2
    assert _werkzeug(inv, "ctxp:suche").genutzt == 1
    assert _werkzeug(inv, "executor-opus").genutzt == 1
    assert _werkzeug(inv, "firecrawl").genutzt == 2
    assert inv.historie["unterbauten"].get("ffmpeg") == 1
    assert inv.historie["dateien"] == 1
    assert any("nie-installiert" in z and "nicht installiert" in z for z in inv.tote_winkel)
    assert "[x] mp-tdd (skill, genutzt 2×)" in inv.text()


# --- .env und Setup-Zeilen -------------------------------------------------------------


def test_env_werte_landen_nie_in_ausgabe_oder_datei(repo: Path, heim: Path, tmp_path: Path) -> None:
    (repo / ".env").write_text(
        "# Kommentar\nGEHEIM_TOKEN=GEHEIM123\nGEMINI_API_KEY='GEHEIM123'\nexport X=GEHEIM123\n",
        encoding="utf-8",
    )
    inv = _erstelle(repo, heim)
    ziel = tmp_path / "werkzeuge.json"
    inventur.schreibe(inv, ziel)
    assert "GEHEIM123" not in inv.text()
    assert "GEHEIM123" not in json.dumps(inv.als_dict(), ensure_ascii=False)
    assert "GEHEIM123" not in ziel.read_text(encoding="utf-8")
    # Schlüssel-Name zählt: gemini ist über die .env vorhanden.
    assert "gemini" not in {z.name for z in inv.setup_zeilen}
    assert "GEMINI_API_KEY" in inv.als_dict()["projekt"]["env_schluessel"]


def test_setup_zeilen_mit_fehlgrund_nur_fuer_fehlendes(
    repo: Path, heim: Path, tmp_path: Path
) -> None:
    pfad = _pfad_mit(tmp_path, "ffmpeg")
    inv = _erstelle(repo, heim, environ={"PATH": pfad})
    zeilen = {z.name: z for z in inv.setup_zeilen}
    assert set(zeilen) == {"codex", "gemini", "adb", "gh"}
    assert "`adb` nicht im PATH" in zeilen["adb"].fehlgrund
    assert zeilen["adb"].abhilfe
    assert "GEMINI_API_KEY" in zeilen["gemini"].fehlgrund
    assert "GOOGLE_API_KEY" in zeilen["gemini"].fehlgrund
    assert "`codex`" in zeilen["codex"].fehlgrund
    assert "OPENAI_API_KEY" in zeilen["codex"].fehlgrund
    assert "Setup-Zeilen" in inv.text()


def test_keine_setup_zeile_wenn_alles_da(repo: Path, heim: Path, tmp_path: Path) -> None:
    pfad = _pfad_mit(tmp_path, "ffmpeg", "adb", "gh")
    (heim / ".codex").mkdir()
    (heim / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    inv = _erstelle(repo, heim, environ={"PATH": pfad, "GOOGLE_API_KEY": "x"})
    assert inv.setup_zeilen == []


def test_codex_zaehlt_als_da_mit_befehl_und_schluessel(
    repo: Path, heim: Path, tmp_path: Path
) -> None:
    # #209 F6: früher reichte der Befehl allein; jetzt braucht codex zusätzlich
    # OPENAI_API_KEY oder ~/.codex/auth.json (Gegenprobe: test_f6_codex_…).
    pfad = _pfad_mit(tmp_path, "codex")
    inv = _erstelle(repo, heim, environ={"PATH": pfad, "OPENAI_API_KEY": "x"})
    assert "codex" not in {z.name for z in inv.setup_zeilen}


def test_projekt_merkmale(repo: Path, heim: Path) -> None:
    projekt = _erstelle(repo, heim).als_dict()["projekt"]
    assert projekt["sprachen"][0]["endung"] == ".py"
    assert "Dockerfile" in projekt["merkmale"]
    assert "Frontend" in projekt["merkmale"]


# --- CLI: Abwahl, Anwahl, unbekannte Namen --------------------------------------------


def _cli(repo: Path, heim: Path, *args: str) -> subprocess.CompletedProcess[str]:
    umgebung = dict(os.environ)
    umgebung["HOME"] = str(heim)
    return subprocess.run(
        [sys.executable, str(CLI), "inventur", *args],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def test_abwahl_bleibt_beim_zweiten_lauf_und_anwahl_hebt_auf(
    repo: Path, heim: Path, tmp_path: Path
) -> None:
    for name in ("mp-tdd", "graphify", "ffmpeg-production"):
        _skill(heim / ".claude" / "skills" / name, name, "irgendwas")
    ziel = tmp_path / "aus" / "werkzeuge.json"

    erst = _cli(repo, heim, "--abwahl", "mp-tdd,graphify", "--schreiben", "--ausgabe", str(ziel))
    assert erst.returncode == 0, erst.stderr
    daten = json.loads(ziel.read_text(encoding="utf-8"))
    assert daten["abgewaehlt"] == ["graphify", "mp-tdd"]
    assert daten["standard"] == "alles an"
    assert "[ ] mp-tdd" in erst.stdout

    zweit = _cli(repo, heim, "--schreiben", "--ausgabe", str(ziel))
    assert zweit.returncode == 0, zweit.stderr
    assert json.loads(ziel.read_text(encoding="utf-8"))["abgewaehlt"] == ["graphify", "mp-tdd"]

    dritt = _cli(repo, heim, "--anwahl", "graphify", "--schreiben", "--ausgabe", str(ziel))
    assert dritt.returncode == 0, dritt.stderr
    assert json.loads(ziel.read_text(encoding="utf-8"))["abgewaehlt"] == ["mp-tdd"]
    assert not (repo / ".to-spawn").exists()


def test_ohne_schreiben_wird_nichts_angelegt(repo: Path, heim: Path) -> None:
    lauf = _cli(repo, heim, "--json")
    assert lauf.returncode == 0, lauf.stderr
    assert [k["schluessel"] for k in json.loads(lauf.stdout)["kategorien"]] == KATEGORIEN
    assert not (repo / ".to-spawn").exists()


def test_unbekannter_name_exit_2(repo: Path, heim: Path, tmp_path: Path) -> None:
    _skill(heim / ".claude" / "skills" / "mp-tdd", "mp-tdd", "Test")
    ziel = tmp_path / "werkzeuge.json"
    lauf = _cli(repo, heim, "--abwahl", "gibts-nicht", "--schreiben", "--ausgabe", str(ziel))
    assert lauf.returncode == 2
    assert "gibts-nicht" in lauf.stderr
    assert "mp-tdd" in lauf.stderr
    assert not ziel.exists()
    lauf = _cli(repo, heim, "--anwahl", "auch-nicht")
    assert lauf.returncode == 2


# --- Weg-Test ohne Attrappen: echtes Repo, echtes HOME ------------------------------------

ECHTES_REPO = Path("/home/bau/duoplus-management")


def _env_namen(datei: Path) -> set[str]:
    namen: set[str] = set()
    if not datei.is_file():
        return namen
    for zeile in datei.read_text(encoding="utf-8", errors="replace").splitlines():
        teil = zeile.strip().removeprefix("export ").split("=", 1)
        if len(teil) == 2 and teil[1].strip().strip("'\""):
            namen.add(teil[0].strip())
    return namen


@pytest.mark.skipif(not ECHTES_REPO.is_dir(), reason="echtes Repo fehlt auf diesem Rechner")
def test_weg_echtes_repo_echtes_home(tmp_path: Path) -> None:
    echte_datei = ECHTES_REPO / ".to-spawn" / "werkzeuge.json"
    vorher = echte_datei.exists()
    ziel = tmp_path / "werkzeuge.json"
    lauf = subprocess.run(
        [sys.executable, str(CLI), "inventur", "--json", "--schreiben", "--ausgabe", str(ziel)],
        cwd=str(ECHTES_REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stderr[-2000:]
    assert echte_datei.exists() == vorher, "echte werkzeuge.json darf nicht entstehen"
    daten = json.loads(lauf.stdout)
    assert [k["schluessel"] for k in daten["kategorien"]] == KATEGORIEN
    alle = [w for k in daten["kategorien"] for w in k["werkzeuge"]]
    assert len(alle) >= 20
    je_name = {w["name"]: w for w in alle}

    skills = Path.home() / ".claude" / "skills"
    for name, kategorie in (("mp-tdd", "testen"), ("ffmpeg-production", "medien")):
        if (skills / name).exists():
            assert je_name[name]["kategorie"] == kategorie
    if not (skills / "mp-tdd").exists() and not (skills / "ffmpeg-production").exists():
        pytest.skip("weder mp-tdd noch ffmpeg-production in ~/.claude/skills")

    slug = re.sub(r"[^A-Za-z0-9]", "-", str(ECHTES_REPO.resolve()))
    if (Path.home() / ".claude" / "projects" / slug).is_dir():
        assert any(w["genutzt"] > 0 for w in alle), "Historie ergibt keine Nutzung"

    env_namen = _env_namen(ECHTES_REPO / ".env")
    zeilen = {z["name"]: z for z in daten["setup_zeilen"]}
    for befehl in ("ffmpeg", "adb", "gh"):
        assert (befehl in zeilen) == (shutil.which(befehl) is None), befehl
        if befehl in zeilen:
            assert f"`{befehl}`" in zeilen[befehl]["fehlgrund"]
    gemini_da = bool({"GEMINI_API_KEY", "GOOGLE_API_KEY"} & (set(os.environ) | env_namen))
    assert ("gemini" in zeilen) == (not gemini_da)
    codex_da = (
        shutil.which("codex") is not None
        or "OPENAI_API_KEY" in os.environ
        or "OPENAI_API_KEY" in env_namen
        or (Path.home() / ".codex" / "auth.json").is_file()
    )
    assert ("codex" in zeilen) == (not codex_da)

    datei = json.loads(ziel.read_text(encoding="utf-8"))
    assert datei["standard"] == "alles an"
    assert datei["abgewaehlt"] == []
    assert list(datei["kategorien"]) == KATEGORIEN


# === Fixrunde #209 (Prüfpanel F1–F12) ====================================================


def _pfad_mit_exit(tmp_path: Path, befehl: str, code: int) -> str:
    """Wegwerf-PATH mit einem Befehl, der mit ``code`` endet."""
    ordner = tmp_path / f"bin-{befehl}-{code}"
    ordner.mkdir(exist_ok=True)
    datei = ordner / befehl
    datei.write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8")
    datei.chmod(0o755)
    return str(ordner)


# --- F1: Abwahl geht nie verloren ----------------------------------------------------------


@pytest.mark.parametrize(
    "inhalt",
    [
        "{kaputt",
        '{"abgewaehlt": "mp-tdd"}',
        '["mp-tdd"]',
        '{"abgewaehlt": [1, 2]}',
        '{"stand": "ohne Liste"}',
    ],
)
def test_f1_kaputte_werkzeuge_json_exit_4_und_bleibt_unberuehrt(
    repo: Path, heim: Path, tmp_path: Path, inhalt: str
) -> None:
    _skill(heim / ".claude" / "skills" / "mp-tdd", "mp-tdd", "Test")
    ziel = tmp_path / "werkzeuge.json"
    ziel.write_text(inhalt, encoding="utf-8")
    lauf = _cli(repo, heim, "--schreiben", "--ausgabe", str(ziel))
    assert lauf.returncode == 4, lauf.stderr
    assert str(ziel) in lauf.stderr
    assert "nicht überschrieben" in lauf.stderr
    assert ziel.read_text(encoding="utf-8") == inhalt


def test_f1_abwahl_ausserhalb_des_katalogs_bleibt_erhalten(
    repo: Path, heim: Path, tmp_path: Path
) -> None:
    _skill(heim / ".claude" / "skills" / "mp-tdd", "mp-tdd", "Test")
    ziel = tmp_path / "werkzeuge.json"
    ziel.write_text(json.dumps({"abgewaehlt": ["weg-skill", "mp-tdd"]}), encoding="utf-8")
    lauf = _cli(repo, heim, "--schreiben", "--ausgabe", str(ziel))
    assert lauf.returncode == 0, lauf.stderr
    assert json.loads(ziel.read_text(encoding="utf-8"))["abgewaehlt"] == ["mp-tdd", "weg-skill"]
    assert "[ ] weg-skill (abgewählt, derzeit nicht gefunden)" in lauf.stdout
    assert "[ ] mp-tdd" in lauf.stdout
    zurueck = _cli(repo, heim, "--anwahl", "weg-skill", "--schreiben", "--ausgabe", str(ziel))
    assert zurueck.returncode == 0, zurueck.stderr
    assert json.loads(ziel.read_text(encoding="utf-8"))["abgewaehlt"] == ["mp-tdd"]


# --- F2: .env-Namen streng, leere Werte zählen nicht --------------------------------------


def test_f2_env_namen_nur_gueltig_und_nicht_leer(tmp_path: Path) -> None:
    datei = tmp_path / ".env"
    datei.write_text(
        "export GUT_A=wert\n"
        'GUT_B="zwei"\n'
        "LEER_KOMMENTAR= # nur Kommentar\n"
        "LEER_SPACE=   \n"
        'LEER_QUOTES=""\n'
        "LEER_KOMMENTAR_QUOTES='' # später\n"
        'MEHR="-----BEGIN KEY-----\n'
        "QUJDREVG==\n"
        "TUlJRXZR=abc\n"
        '-----END KEY-----"\n'
        "kaputt name=1\n"
        "1ZAHL=x\n"
        "MIT-STRICH=x\n"
        "NACH_MEHR=ok # Kommentar\n",
        encoding="utf-8",
    )
    assert inventur.env_schluessel(datei) == {"GUT_A", "GUT_B", "MEHR", "NACH_MEHR"}


def test_f2_leere_werte_in_umgebung_und_env_zaehlen_nicht(repo: Path, heim: Path) -> None:
    (repo / ".env").write_text("GEMINI_API_KEY= # später\nOPENAI_API_KEY=''\n", "utf-8")
    inv = _erstelle(repo, heim, environ={"PATH": "", "GOOGLE_API_KEY": "   "})
    zeilen = {z.name for z in inv.setup_zeilen}
    assert {"gemini", "codex"} <= zeilen
    assert "GEMINI_API_KEY" not in inv.als_dict()["projekt"]["env_schluessel"]


# --- F3: Projekt-Skills und -Agenten ------------------------------------------------------


def test_f3_projekt_skills_und_agenten_aus_dem_repo(repo: Path, heim: Path) -> None:
    _skill(repo / ".claude" / "skills" / "haus-skill", "haus-skill", "Deploy auf den Server")
    (repo / ".claude" / "agents").mkdir(parents=True)
    (repo / ".claude" / "agents" / "haus-agent.md").write_text(
        "---\nname: haus-agent\ndescription: Recherche im Netz\n---\n", encoding="utf-8"
    )
    inv = _erstelle(repo, heim)
    skill = _werkzeug(inv, "haus-skill")
    agent = _werkzeug(inv, "haus-agent")
    assert (skill.art, skill.kategorie) == ("skill", "deploy")
    assert (agent.art, agent.kategorie) == ("agent", "recherche")
    assert skill.quelle.startswith(str(repo.resolve()))
    assert agent.quelle.startswith(str(repo.resolve()))


# --- F4: Historie fehlt / leer ------------------------------------------------------------


def test_f4_historie_fehlt_warnung_und_toter_winkel(
    repo: Path, heim: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ordner = heim / ".claude" / "projects" / _slug(repo)
    with caplog.at_level("WARNING", logger="to_spawn.inventur"):
        inv = _erstelle(repo, heim)
    assert any(str(ordner) in r.getMessage() for r in caplog.records)
    assert any("Historie" in z and str(ordner) in z for z in inv.tote_winkel)
    ordner.mkdir(parents=True)
    caplog.clear()
    with caplog.at_level("WARNING", logger="to_spawn.inventur"):
        inv = _erstelle(repo, heim)
    assert any(str(ordner) in r.getMessage() for r in caplog.records)
    assert any("Historie" in z and "0 Dateien" in z for z in inv.tote_winkel)


def test_f4_kaputte_zeilen_stehen_im_text(repo: Path, heim: Path) -> None:
    ordner = heim / ".claude" / "projects" / _slug(repo)
    ordner.mkdir(parents=True)
    (ordner / "s.jsonl").write_text("{kaputt\n{auch kaputt\n" + _tool_use("Read", {}) + "\n")
    inv = _erstelle(repo, heim)
    assert "kaputte Zeilen: 2" in inv.text()
    assert not any("Historie" in z for z in inv.tote_winkel)


# --- F5: git ls-files scheitert --------------------------------------------------------------


def test_f5_git_ls_files_fehler_warnung_und_hinweis(
    tmp_path: Path, heim: Path, caplog: pytest.LogCaptureFixture
) -> None:
    kein_repo = tmp_path / "kein-repo"
    kein_repo.mkdir()
    with caplog.at_level("WARNING", logger="to_spawn.inventur"):
        inv = _erstelle(kein_repo, heim)
    warnungen = [r.getMessage() for r in caplog.records if "git ls-files" in r.getMessage()]
    assert warnungen and "git" in warnungen[0].lower()
    assert "git ls-files" in inv.projekt.get("hinweis", "")
    projekt_zeile = next(z for z in inv.text().splitlines() if z.startswith("Projekt:"))
    assert "git ls-files" in projekt_zeile


# --- F6: gh angemeldet? codex angemeldet? -------------------------------------------------


def test_f6_gh_da_aber_nicht_angemeldet(repo: Path, heim: Path, tmp_path: Path) -> None:
    pfad = _pfad_mit_exit(tmp_path, "gh", 1)
    inv = _erstelle(repo, heim, environ={"PATH": pfad})
    zeilen = {z.name: z for z in inv.setup_zeilen}
    assert "nicht angemeldet" in zeilen["gh"].fehlgrund
    assert "gh auth login" in zeilen["gh"].abhilfe
    assert "nicht im PATH" not in zeilen["gh"].fehlgrund


def test_f6_pruefung_injizierbar(repo: Path, heim: Path, tmp_path: Path) -> None:
    pfad = _pfad_mit(tmp_path, "gh", "ffmpeg", "adb")
    aufrufe: list[list[str]] = []

    def scheitert(befehl: list[str]) -> int:
        aufrufe.append(befehl)
        return 1

    inv = inventur.erstelle(
        repo,
        claude_home=heim / ".claude",
        home_json=heim / ".claude.json",
        home=heim,
        environ={"PATH": pfad},
        ausfuehren=scheitert,
    )
    assert "gh" in {z.name for z in inv.setup_zeilen}
    assert aufrufe and aufrufe[0][-2:] == ["auth", "status"]
    assert Path(aufrufe[0][0]).name == "gh"
    inv = inventur.erstelle(
        repo,
        claude_home=heim / ".claude",
        home_json=heim / ".claude.json",
        home=heim,
        environ={"PATH": pfad},
        ausfuehren=lambda befehl: 0,
    )
    assert "gh" not in {z.name for z in inv.setup_zeilen}


def test_f6_codex_befehl_allein_reicht_nicht(repo: Path, heim: Path, tmp_path: Path) -> None:
    pfad = _pfad_mit(tmp_path, "codex")
    inv = _erstelle(repo, heim, environ={"PATH": pfad})
    zeilen = {z.name: z for z in inv.setup_zeilen}
    assert "codex" in zeilen
    assert "OPENAI_API_KEY" in zeilen["codex"].fehlgrund
    assert "auth.json" in zeilen["codex"].fehlgrund
    inv = _erstelle(repo, heim, environ={"PATH": pfad, "OPENAI_API_KEY": " "})
    assert "codex" in {z.name for z in inv.setup_zeilen}
    inv = _erstelle(repo, heim, environ={"PATH": pfad, "OPENAI_API_KEY": "sk-x"})
    assert "codex" not in {z.name for z in inv.setup_zeilen}


# --- F7: Historie wird gestreamt ----------------------------------------------------------


def test_f7_historie_zeilenweise_ohne_read_text(
    repo: Path, heim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ordner = heim / ".claude" / "projects" / _slug(repo)
    ordner.mkdir(parents=True)
    (ordner / "s.jsonl").write_text(_tool_use("Skill", {"skill": "x"}) + "\n", "utf-8")
    original = Path.read_text

    def kein_ganzes_lesen(self: Path, *args: object, **kwargs: object) -> str:
        if self.suffix == ".jsonl":
            raise AssertionError("Historie per read_text gelesen")
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", kein_ganzes_lesen)
    historie = inventur.lies_historie(repo.resolve(), heim / ".claude", 10)
    assert historie["skills"] == {"x": 1}


# --- F8: Einordnung nach echtem Katalog ------------------------------------------------------

EINORDNUNG_F8 = [
    (
        "german-umlauts",
        (
            "PFLICHT bei jedem deutschen Text/Code/UI: echte ä ö ü ß statt ae/oe/ue/ss. "
            "Trigger: deutsch, german, de-DE, Umlaut, deutsche Seite/Copy/Labels, UI-Text"
        ),
        "bauen",
    ),
    (
        "wizard",
        (
            "Generate an interactive bash wizard that walks a human through steps only they "
            "can perform. Use when provisioning infrastructure, setting up credentials"
        ),
        "steuerung",
    ),
    (
        "grilling",
        "Grill the user relentlessly about a plan, decision, or idea.",
        "zweitmeinung",
    ),
    ("grill-me", "A relentless interview to sharpen a plan or design.", "zweitmeinung"),
    (
        "grill-with-docs",
        (
            "A relentless interview to sharpen a plan or design, which also creates docs "
            "(ADR's and glossary) as we go."
        ),
        "zweitmeinung",
    ),
    (
        "diagnosing-bugs",
        (
            "Diagnosis loop for hard bugs and performance regressions. Use when the user says "
            '"diagnose"/"debug this", or reports something broken'
        ),
        "testen",
    ),
    (
        "codebase-design",
        (
            "Shared vocabulary for designing deep modules. Use when the user wants to design "
            "or improve a module's interface"
        ),
        "bauen",
    ),
    (
        "api-design",
        "REST API design patterns including resource naming, status codes, pagination",
        "bauen",
    ),
    (
        "fable-1080",
        (
            "PFLICHT-Protokoll auf Fable 5 bei Dev-Tasks: Fable plant+reviewt, "
            "executor-opus/-sonnet bauen."
        ),
        "steuerung",
    ),
    (
        "triage",
        (
            "Move issues and external PRs through a state machine of triage roles, categorise, "
            "verify, grill if needed, and write agent-ready briefs."
        ),
        "steuerung",
    ),
    (
        "writing-for-agents",
        (
            "Writing documents for agents. Use when creating or editing skills, or modifying "
            "AGENTS.md or CLAUDE.md."
        ),
        "kontext",
    ),
    ("prototype", "Build a throwaway prototype to answer a design question.", "bauen"),
    ("frontend-design", "Distinctive visual design for new UI", "design"),
]


@pytest.mark.parametrize(("name", "beschreibung", "erwartet"), EINORDNUNG_F8)
def test_f8_einordnung_echte_faelle(
    repo: Path, heim: Path, name: str, beschreibung: str, erwartet: str
) -> None:
    _skill(heim / ".claude" / "skills" / name, name, beschreibung)
    inv = _erstelle(repo, heim)
    w = _werkzeug(inv, name)
    assert w.kategorie == erwartet
    assert w.art == "skill"


# --- F9: tote Winkel aktiv suchen -----------------------------------------------------------


def test_f9a_skill_ordner_ohne_skill_md(repo: Path, heim: Path) -> None:
    (heim / ".claude" / "skills" / "handoff").mkdir()
    (heim / ".claude" / "skills" / "_alt").mkdir()
    inv = _erstelle(repo, heim)
    treffer = [z for z in inv.tote_winkel if "kaputt: Ordner ohne SKILL.md" in z]
    assert len(treffer) == 1 and "handoff" in treffer[0]


@pytest.mark.parametrize("wo", ["repo", "heim"])
def test_f9b_agenten_erwaehnt_aber_keine_da(repo: Path, heim: Path, wo: str) -> None:
    text = "Regel steht in `~/.claude/agents/*`.\n"
    if wo == "repo":
        (repo / "CLAUDE.md").write_text(text, encoding="utf-8")
    else:
        (heim / ".claude" / "CLAUDE.md").write_text(text, encoding="utf-8")
    inv = _erstelle(repo, heim)
    assert any("agents/" in z and "CLAUDE.md" in z for z in inv.tote_winkel)
    (heim / ".claude" / "agents").mkdir()
    (heim / ".claude" / "agents" / "executor-opus.md").write_text("# x\n", "utf-8")
    inv = _erstelle(repo, heim)
    assert not any("agents/" in z and "CLAUDE.md" in z for z in inv.tote_winkel)


def test_f9c_doppelte_skill_plugin_paare(repo: Path, heim: Path) -> None:
    claude = heim / ".claude"
    _skill(claude / "skills" / "code-review", "code-review", "Review the changes")
    plugin = heim / "plugin-bau" / "mps"
    _skill(plugin / "skills" / "code-review", "code-review", "Review the changes")
    (claude / "plugins").mkdir()
    (claude / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"mps@markt": [{"installPath": str(plugin)}]}}), "utf-8"
    )
    inv = _erstelle(repo, heim)
    doppelt = [z for z in inv.tote_winkel if z.startswith("doppelt:")]
    assert len(doppelt) == 1
    assert "`code-review`" in doppelt[0] and "`mps:code-review`" in doppelt[0]


def test_f9d_vorschlag_nennt_kein_vorhandenes_werkzeug(repo: Path, heim: Path) -> None:
    inv = _erstelle(repo, heim)
    # verify-hard steht (absichtlich falsch) in Kontext, Testen ist leer.
    inv.werkzeuge = [inventur.Werkzeug("verify-hard", "skill", "kontext", "x")]
    hinweise = inventur._tote_winkel(inv)
    testen = [z for z in hinweise if "„Testen/Beweisen“ leer" in z]
    assert testen and "verify-hard" not in testen[0]
    assert "Vorschlag" in testen[0]


# --- F10: eingebaute Skills/Agenten, Kommandos aus der Historie -------------------------------


def test_f10_eingebaute_skills_und_agenten_feste_kategorie(repo: Path, heim: Path) -> None:
    inv = _erstelle(repo, heim)
    erwartet = {
        "loop": ("skill", "steuerung"),
        "run": ("skill", "testen"),
        "claude-api": ("skill", "bauen"),
        "security-review": ("skill", "sicherheit"),
        "general-purpose": ("agent", "bauen"),
        "Explore": ("agent", "recherche"),
        "Plan": ("agent", "steuerung"),
        "statusline-setup": ("agent", "steuerung"),
        "claude-code-guide": ("agent", "recherche"),
    }
    for name, (art, kategorie) in erwartet.items():
        w = _werkzeug(inv, name)
        assert (w.art, w.kategorie, w.quelle) == (art, kategorie, "eingebaut"), name
    for beschreibung, _kategorie in inventur.EINGEBAUTE_AGENTEN.values():
        assert "(" not in beschreibung, "keine eingeschmuggelten Schlüsselwörter"


def test_f10_kommando_genutzt_aber_nicht_installiert(repo: Path, heim: Path) -> None:
    (heim / ".claude" / "commands").mkdir()
    (heim / ".claude" / "commands" / "easy.md").write_text("vereinfachen\n", "utf-8")
    ordner = heim / ".claude" / "projects" / _slug(repo)
    ordner.mkdir(parents=True)
    zeilen = [
        json.dumps({"message": {"content": f"<command-name>/{k}</command-name>"}})
        for k in ("gibts-nicht", "loop", "model", "easy", "gibts-nicht")
    ]
    (ordner / "s.jsonl").write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    inv = _erstelle(repo, heim)
    fehlend = [z for z in inv.tote_winkel if "nicht installiert" in z]
    assert len(fehlend) == 1
    assert "`/gibts-nicht`" in fehlend[0] and "2×" in fehlend[0]
    assert _werkzeug(inv, "loop").genutzt == 1


# --- F12: Hinweis ohne --schreiben, Hilfetext --------------------------------------------------


def test_f12_hinweis_nicht_gespeichert(repo: Path, heim: Path, tmp_path: Path) -> None:
    _skill(heim / ".claude" / "skills" / "mp-tdd", "mp-tdd", "Test")
    ziel = tmp_path / "werkzeuge.json"
    lauf = _cli(repo, heim, "--abwahl", "mp-tdd", "--ausgabe", str(ziel))
    assert lauf.returncode == 0, lauf.stderr
    assert lauf.stdout.rstrip().endswith("nicht gespeichert – `--schreiben` fehlt")
    assert not ziel.exists()
    ohne = _cli(repo, heim, "--ausgabe", str(ziel))
    assert "nicht gespeichert" not in ohne.stdout


def test_f12_letzte_hat_hilfetext() -> None:
    lauf = subprocess.run(
        [sys.executable, str(CLI), "inventur", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert lauf.returncode == 0
    hilfe = " ".join(lauf.stdout.split())
    assert "--letzte" in hilfe and "Historie" in hilfe


# --- F11: Weg-Test gegen das echte Repo mit Abwahl über drei Läufe -----------------------------


def _echt(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "inventur", *args],
        cwd=str(ECHTES_REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )


@pytest.mark.skipif(not ECHTES_REPO.is_dir(), reason="echtes Repo fehlt auf diesem Rechner")
def test_f11_weg_echtes_repo_abwahl_ueber_drei_laeufe(tmp_path: Path) -> None:
    echte_datei = ECHTES_REPO / ".to-spawn" / "werkzeuge.json"
    vorher = echte_datei.exists()
    null = _echt("--json")
    assert null.returncode == 0, null.stderr[-2000:]
    daten = json.loads(null.stdout)
    medien = next(k for k in daten["kategorien"] if k["schluessel"] == "medien")["werkzeuge"]
    kandidaten = medien or next(k["werkzeuge"] for k in daten["kategorien"] if k["werkzeuge"])
    name = kandidaten[0]["name"]
    ziel = tmp_path / "w.json"

    eins = _echt("--json", "--schreiben", "--ausgabe", str(ziel), "--abwahl", name)
    assert eins.returncode == 0, eins.stderr[-2000:]
    assert json.loads(ziel.read_text(encoding="utf-8"))["abgewaehlt"] == [name]

    zwei = _echt("--schreiben", "--ausgabe", str(ziel))
    assert zwei.returncode == 0, zwei.stderr[-2000:]
    assert json.loads(ziel.read_text(encoding="utf-8"))["abgewaehlt"] == [name]
    assert f"[ ] {name}" in zwei.stdout

    drei = _echt("--anwahl", name, "--schreiben", "--ausgabe", str(ziel))
    assert drei.returncode == 0, drei.stderr[-2000:]
    assert json.loads(ziel.read_text(encoding="utf-8"))["abgewaehlt"] == []
    assert echte_datei.exists() == vorher, "echte werkzeuge.json darf nicht entstehen"

    je_name = {w["name"]: w for k in daten["kategorien"] for w in k["werkzeuge"]}
    skills = Path.home() / ".claude" / "skills"
    geprueft = 0
    for skill, kategorie in (("german-umlauts", "bauen"), ("diagnosing-bugs", "testen")):
        if (skills / skill / "SKILL.md").is_file():
            assert je_name[skill]["kategorie"] == kategorie, skill
            geprueft += 1
    if not geprueft:
        pytest.skip("weder german-umlauts noch diagnosing-bugs in ~/.claude/skills")
