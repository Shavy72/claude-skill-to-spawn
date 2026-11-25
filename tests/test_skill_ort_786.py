"""#786: Skill-Ort aus einer Quelle (``TO_SPAWN_HOME`` bzw. laufender Skill-Ordner).

Ein getrennter Install außerhalb von ``~/.claude/skills/to-spawn`` (z. B. Probe-Lauf
neben einem laufenden Projekt) darf keine Neustarts, Anstöße oder SSH-Fernbefehle mit
dem fest verdrahteten Pfad ``~/.claude/skills/to-spawn`` auslösen.

Geprüft werden alle fünf Stellen aus #786 plus die Aufseher-Vorlage:
1. ``to_spawn/aufpasser.py``: Blocker-Anstoß (``BLOCKER_FREI_TEXT`` / ``blocker_falsch_gewartet``)
2. ``to_spawn/aufpasser.py``: Wache-Neustart (``VORLAGE_WACHE_SKILL`` / ``wache_befehl``)
3. ``to_spawn/umzug.py``: Server-Fernbefehl (``fern_skill_befehl`` / ``spawn_befehl``)
4. ``to_spawn/spawn.py``: Einzelticket-Neustart per SSH (``neustart --ziel srv``)
5. ``skripte/rueckblick.py``: Fern-Log-Abruf per SSH (``_bau_server``)
6. ``skripte/wache.py``: Aufpasser-Hinweis im Aufseher-Prompt (``prompt_bauen``)

Zusätzlich ein Build-brechender Prüf-Test: kein festes ``~/.claude/skills/to-spawn`` in
``to_spawn/*.py`` oder ``skripte/*.py`` außer der einen Rückfall-Stelle in ``to_spawn/config.py``.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from conftest import repo_dateien
from to_spawn import aufpasser, briefkasten_wecker, config, spawn, stand, starter, umzug

SKILL = Path(__file__).resolve().parent.parent

FEST_PFAD = "~/.claude/skills/to-spawn"
_FEST_MUSTER = re.compile(r"~/\.claude/skills/to-spawn")
#: Die eine erlaubte Rückfall-Stelle im gesamten Skill-Code (Server-Vorgabe ohne TO_SPAWN_HOME).
ERLAUBTE_RUECKFALL_STELLE: tuple[str, str] = (
    "to_spawn/config.py",
    'FERN_SKILL_VORGABE = "~/.claude/skills/to-spawn"',
)


def _lade_skript(name: str, modulname: str) -> ModuleType:
    skripte_dir = str(SKILL / "skripte")
    if skripte_dir not in sys.path:
        sys.path.insert(0, skripte_dir)
    pfad = SKILL / "skripte" / name
    spec = importlib.util.spec_from_file_location(modulname, pfad)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture()
def tmp_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Simuliert einen getrennten Install außerhalb von ``~/.claude/skills`` via ``TO_SPAWN_HOME``."""
    ort = tmp_path / "probe-skill"
    ort.mkdir()
    monkeypatch.setenv("TO_SPAWN_HOME", str(ort))
    return ort


def test_aufpasser_blocker_anstoss_nutzt_to_spawn_home(
    tmp_path: Path, tmp_skill: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stelle 1: ``blocker_falsch_gewartet`` nennt ``TO_SPAWN_HOME/to_spawn.py`` statt festem Pfad."""
    repo = tmp_path / "app"
    repo.mkdir()
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "z", repo=repo))
    getippt: list[str] = []
    monkeypatch.setattr(aufpasser, "erledigt_gewartet", lambda *_a, **_k: "#10")
    monkeypatch.setattr(aufpasser, "notiere_blocker_falsch", lambda *_a, **_k: None)
    monkeypatch.setattr(aufpasser.gh, "repo_aus_origin", lambda _r: "o/r")
    monkeypatch.setattr(a, "vor_eingriff", lambda *_a, **_k: True)
    monkeypatch.setattr(a, "pane_text", lambda *_a, **_k: "")
    monkeypatch.setattr(a, "_eingeben", lambda _ziel, text: getippt.append(text))
    fenster = aufpasser.Fenster(sitzung="spec-42", spec="42", index="1", name="bau 20", pfad=str(repo), pane_pid=1)
    assert a.blocker_falsch_gewartet(fenster, 20, repo, "bau 20", {"stupser": []}, "h", "  ⎿  WARTET: #10 offen\n")
    assert len(getippt) == 1
    assert f"{tmp_skill.as_posix()}/to_spawn.py blocker 20" in getippt[0], getippt[0]
    assert FEST_PFAD not in getippt[0], getippt[0]


def test_wecker_blocker_anstoss_nutzt_to_spawn_home(
    tmp_path: Path, tmp_skill: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mitnutzer ``briefkasten_wecker``: Blocker-Anstoß nennt ebenfalls ``TO_SPAWN_HOME``."""
    repo = tmp_path / "app"
    repo.mkdir()
    getippt: list[str] = []

    class AttrappenWerkzeug:
        def bildschirm(self, _ziel: str, *, farben: bool = False) -> str:
            return "  ⎿  WARTET: #10 offen\n"

    w = briefkasten_wecker.Wecker(42, repo, "=spec-42:@1", gh_repo="o/r", werkzeug=AttrappenWerkzeug())
    monkeypatch.setattr(briefkasten_wecker.aufseher_stand, "bau_fenster", lambda *_a, **_k: {20: "=spec-42:@2"})
    monkeypatch.setattr(aufpasser, "erledigt_gewartet", lambda *_a, **_k: "#10")
    monkeypatch.setattr(aufpasser, "notiere_blocker_falsch", lambda *_a, **_k: None)
    monkeypatch.setattr(w, "_tippe", lambda _ziel, text: (getippt.append(text), True)[1])
    w._blocker_blick()
    assert len(getippt) == 1
    assert f"{tmp_skill.as_posix()}/to_spawn.py blocker 20" in getippt[0], getippt[0]
    assert FEST_PFAD not in getippt[0], getippt[0]


def test_aufpasser_wache_befehl_nutzt_to_spawn_home(tmp_path: Path, tmp_skill: Path) -> None:
    """Stelle 2: ``wache_befehl`` (ohne Repo-Weiterleitung) startet ``TO_SPAWN_HOME/skripte/wache.py``."""
    repo = tmp_path / "app"
    repo.mkdir()
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "z", repo=repo))
    befehl = a.wache_befehl(repo, "900", resume=None)
    assert f"{tmp_skill.as_posix()}/skripte/wache.py 900" in befehl, befehl
    assert FEST_PFAD not in befehl, befehl


def test_aufpasser_ohne_to_spawn_home_faellt_auf_eigenen_skill_ordner_zurueck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne ``TO_SPAWN_HOME`` greift lokal der Ordner der laufenden Datei, nie festes ``~/.claude/skills/to-spawn``."""
    monkeypatch.delenv("TO_SPAWN_HOME", raising=False)
    repo = tmp_path / "app"
    repo.mkdir()
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "z", repo=repo))
    befehl = a.wache_befehl(repo, "900", resume=None)
    assert f"{SKILL.as_posix()}/skripte/wache.py 900" in befehl, befehl
    assert FEST_PFAD not in befehl, befehl


def test_umzug_fern_skill_befehl_nutzt_to_spawn_home(tmp_skill: Path) -> None:
    """Stelle 3: ``umzug.fern_skill_befehl`` / ``spawn_befehl`` nennt ``TO_SPAWN_HOME``."""
    befehl = umzug.spawn_befehl("42", "7", "~/r", umzug_ref="ticket/7")
    assert f"{tmp_skill.as_posix()}/skripte/spawn_srv.sh" in befehl, befehl
    assert FEST_PFAD not in befehl, befehl


def test_spawn_neustart_srv_nutzt_to_spawn_home(
    tmp_path: Path, tmp_skill: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stelle 4: ``spawn.neustart --ziel srv`` startet ``TO_SPAWN_HOME/to_spawn.py`` per SSH."""
    repo = tmp_path / "app"
    repo.mkdir()
    gestartet: list[list[str]] = []
    monkeypatch.setattr(config, "ist_bau_server", lambda _k=None: False)
    monkeypatch.setattr(stand, "sichere_stand", lambda *_a, **_k: 0)

    def _attrappe(befehl: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        gestartet.append(befehl)
        return subprocess.CompletedProcess(befehl, 0, "", "")

    monkeypatch.setattr(starter, "starte", _attrappe)
    rc = spawn.neustart(repo, 42, 7, {"server_repo": "~/r", "ssh_ziel": "bau-srv"}, ziel="srv")
    assert rc == 0
    assert len(gestartet) == 1
    fern = gestartet[0][-1]
    assert f"{tmp_skill.as_posix()}/to_spawn.py neustart 42 7 --ziel local" in fern, fern
    assert FEST_PFAD not in fern, fern


def test_rueckblick_bau_server_nutzt_to_spawn_home(
    tmp_path: Path, tmp_skill: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stelle 5: ``skripte/rueckblick.py`` ruft per SSH ``TO_SPAWN_HOME/skripte/rueckblick.py`` auf."""
    rueckblick = _lade_skript("rueckblick.py", "rueckblick_786")
    repo = tmp_path / "app"
    repo.mkdir()
    fern_befehle: list[str] = []
    monkeypatch.setattr(
        rueckblick.config,
        "lade",
        lambda _r: {"ssh_ziel": "bau-srv", "server_repo": "/gibt/es/nicht/repo-786"},
    )

    def _ssh_attrappe(_ziel: str, befehl: str, _timeout: float) -> subprocess.CompletedProcess[str]:
        fern_befehle.append(befehl)
        return subprocess.CompletedProcess([], 0, json.dumps({"tickets": {}, "orte": []}), "")

    monkeypatch.setattr(rueckblick, "_ssh", _ssh_attrappe)
    _logs, _orte = rueckblick._bau_server(repo, 42, ["7"], ohne=False)
    assert len(fern_befehle) == 1
    assert f"{tmp_skill.as_posix()}/skripte/rueckblick.py logs 42 --repo ." in fern_befehle[0], fern_befehle[0]
    assert FEST_PFAD not in fern_befehle[0], fern_befehle[0]


def test_wache_prompt_aufpasser_pfad_nutzt_to_spawn_home(tmp_skill: Path) -> None:
    """Aufseher-Prompt: auch der Aufpasser-Hinweis in ``skripte/wache.py`` folgt ``TO_SPAWN_HOME``."""
    wache = _lade_skript("wache.py", "wache_786")
    # Bestehender 5-Platzhalter-Vertrag von wache.PROMPT.format bleibt unverändert (#786).
    formatiert = wache.PROMPT.format(S=900, REPO="x/y", DATUM="2026-11-25", TAKT=1800, SKILL="/skill")
    assert "<FERN_SKILL>/skripte/aufpasser.py --trocken" in formatiert
    text = wache.prompt_bauen(900, "x/y", 1800, {"ssh_ziel": "bau-server", "server_repo": "/home/bau/r"})
    assert f"{tmp_skill.as_posix()}/skripte/aufpasser.py --trocken" in text
    assert "<FERN_SKILL>" not in text
    assert FEST_PFAD not in text


def test_to_spawn_home_mit_leerzeichen_wird_sauber_gequotet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leerzeichen in ``TO_SPAWN_HOME`` werden in Shell-Befehlen sauber gequotet."""
    ort = tmp_path / "mein probe skill"
    ort.mkdir()
    monkeypatch.setenv("TO_SPAWN_HOME", str(ort))
    repo = tmp_path / "app"
    repo.mkdir()
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "z", repo=repo))
    wache_cmd = a.wache_befehl(repo, "900", resume=None)
    assert f"'{ort.as_posix()}/skripte/wache.py'" in wache_cmd, wache_cmd
    fern_cmd = umzug.fern_skill_befehl("~/r", "bash", "spawn_srv.sh", ["42"])
    assert f"'{ort.as_posix()}/skripte/spawn_srv.sh'" in fern_cmd, fern_cmd


def _pruefe_zeilen(rel: str, text: str) -> list[str]:
    """Alle Zeilen mit festem ``~/.claude/skills/to-spawn`` außer der einen Rückfall-Stelle."""
    erlaubt_datei, erlaubt_zeile = ERLAUBTE_RUECKFALL_STELLE
    funde: list[str] = []
    for nr, zeile in enumerate(text.splitlines(), 1):
        if not _FEST_MUSTER.search(zeile):
            continue
        if rel == erlaubt_datei and erlaubt_zeile in zeile:
            continue
        funde.append(f"{rel}:{nr}: {zeile.strip()}")
    return funde


def _pruefe_skill_py_dateien(wurzel: Path) -> tuple[list[str], bool]:
    """Durchsucht ``to_spawn/**/*.py`` und ``skripte/*.py`` nach festem ``~/.claude/skills/to-spawn``."""
    verstoesse: list[str] = []
    rueckfall_gesehen = False
    erlaubt_datei, erlaubt_zeile = ERLAUBTE_RUECKFALL_STELLE
    for pfad in repo_dateien(wurzel):
        rel = pfad.relative_to(wurzel).as_posix()
        teile = pfad.relative_to(wurzel).parts
        if pfad.suffix != ".py":
            continue
        im_umfang = teile[0] == "to_spawn" or (teile[0] == "skripte" and len(teile) == 2)
        if not im_umfang:
            continue
        inhalt = pfad.read_text(encoding="utf-8", errors="replace")
        if rel == erlaubt_datei and erlaubt_zeile in inhalt:
            rueckfall_gesehen = True
        verstoesse.extend(_pruefe_zeilen(rel, inhalt))
    return verstoesse, rueckfall_gesehen


def test_kein_fester_skill_pfad_in_to_spawn_und_skripte() -> None:
    """Build-brechender Check (#786): kein festes ``~/.claude/skills/to-spawn`` außer der einen Rückfall-Stelle."""
    verstoesse, rueckfall_gesehen = _pruefe_skill_py_dateien(SKILL)
    assert not verstoesse, (
        "Fester Skill-Pfad `~/.claude/skills/to-spawn` im Code — bitte über `to_spawn.config` "
        "(skill_ordner / fern_skill_pfad) beziehen:\n  " + "\n  ".join(verstoesse)
    )
    assert rueckfall_gesehen, (
        f"Die zentrale Rückfall-Stelle {ERLAUBTE_RUECKFALL_STELLE[0]} ({ERLAUBTE_RUECKFALL_STELLE[1]}) fehlt."
    )


def test_build_check_wird_rot_bei_neuem_festen_skill_pfad() -> None:
    """Gegenprobe: eine eingeschmuggelte feste Pfad-Zeile wird vom Check gemeldet."""
    funde = _pruefe_zeilen("to_spawn/beispiel.py", 'PFAD = "~/.claude/skills/to-spawn/to_spawn.py"\n')
    assert len(funde) == 1
    assert "to_spawn/beispiel.py:1:" in funde[0]
