"""Isolierter Test-Heimordner für alle Tests (#232).

Start-Tests (``wache.py`` / ``bau.py``) dürfen nie den echten Heimordner der Maschine
lesen oder beschreiben: keine privaten Hooks aus ``~/.claude/settings.json``, keine
Änderung an ``~/.claude.json``, und sie laufen auch auf leeren Rechnern ohne installiertes
``context-mode``-Plugin oder ``aufraeumen.mjs``.

Die Kind-Läufe in dieser Datei nutzen einen eigenen, gestellten „echten“ Heimordner unter
``tmp_path``, damit der echte Heimordner der Maschine auch im Rot-Test nie berührt wird.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import site
import subprocess
import sys
import types
from pathlib import Path

import pytest

import conftest
from to_spawn import context_mode, startklar, vertrauen

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
TESTS = SKILL / "tests"


def _sha256(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path) -> types.ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location("bau_testheim_232", SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules["bau_testheim_232"] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop("bau_testheim_232", None)
    return modul


def _kind_env(echtes_heim: Path, **extra: str) -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "TO_SPAWN_")) and k != "CLAUDE_CONFIG_DIR"
    }
    env["HOME"] = str(echtes_heim)
    env["USERPROFILE"] = str(echtes_heim)
    env.setdefault("PYTHONUSERBASE", site.getuserbase())
    env["TO_SPAWN_HEIM_WAECHTER"] = "1"
    env.update(extra)
    return env


def _kind_pytest(
    echtes_heim: Path,
    *args: str,
    cwd: Path = SKILL,
    **extra_env: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist", *args],
        cwd=str(cwd),
        env=_kind_env(echtes_heim, **extra_env),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def _waechter_suite_anlegen(
    claude_json: Path,
    ordner: Path,
    *,
    scandir_log: Path | None = None,
) -> None:
    """Legt eine Mini-Suite an, die ``tests/conftest.py``-Hooks lädt und ``claude_json`` ändert."""
    ordner.mkdir(parents=True, exist_ok=True)
    # Eigene pytest.ini begrenzt Konfig-Suche auf die Mini-Suite (#191).
    (ordner / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    scandir_haken = ""
    if scandir_log is not None:
        scandir_haken = (
            "import os\n"
            "_orig_scandir = os.scandir\n"
            f"_SCANDIR_LOG = {str(scandir_log)!r}\n"
            "def _protokoll_scandir(pfad='.'):\n"
            "    if isinstance(pfad, (str, os.PathLike)):\n"
            "        with open(_SCANDIR_LOG, 'a', encoding='utf-8') as _f:\n"
            "            _f.write(f'{os.fspath(pfad)}\\n')\n"
            "    return _orig_scandir(pfad)\n"
            "os.scandir = _protokoll_scandir\n"
        )
    (ordner / "conftest.py").write_text(
        "import importlib.util\n"
        f"{scandir_haken}"
        f"_s = importlib.util.spec_from_file_location('_ct232', {str(TESTS / 'conftest.py')!r})\n"
        "_m = importlib.util.module_from_spec(_s)\n"
        "_s.loader.exec_module(_m)\n"
        "for _n in ('pytest_configure', 'pytest_sessionstart', 'pytest_sessionfinish'):\n"
        "    if hasattr(_m, _n):\n"
        "        globals()[_n] = getattr(_m, _n)\n",
        encoding="utf-8",
    )
    (ordner / "test_leck.py").write_text(
        "from pathlib import Path\n"
        f"ZIEL = Path({str(claude_json)!r})\n"
        "def test_schreibt_fremde_konfig():\n"
        "    ZIEL.write_text('{\"geaendert\": true}\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )


def _waechter_suite_starten(
    echtes_heim: Path,
    ordner: Path,
    *,
    cwd: Path = SKILL,
    **extra_env: str,
) -> subprocess.CompletedProcess[str]:
    """Startet die Wächter-Mini-Suite isoliert auf ``ordner`` (kein Eltern-/Temp-Scan unter Windows, #191)."""
    kind_tmp = ordner.parent / f"{ordner.name}-tmp"
    kind_tmp.mkdir(parents=True, exist_ok=True)
    env = {"TMP": str(kind_tmp), "TEMP": str(kind_tmp), "TMPDIR": str(kind_tmp), **extra_env}
    return _kind_pytest(
        echtes_heim,
        f"--rootdir={ordner}",
        f"--confcutdir={ordner}",
        str(ordner),
        cwd=cwd,
        **env,
    )


def test_testheim_fixture_lenkt_home_und_userprofile_um(test_heim: Path) -> None:
    """``test_heim`` lenkt HOME/USERPROFILE auf einen eigenen Ordner mit Attrappen um."""
    echtes_heim = conftest._ECHTES_HEIM
    assert test_heim.is_dir()
    assert test_heim.resolve() != echtes_heim.resolve()
    assert Path.home().resolve() == test_heim.resolve()
    assert os.environ["HOME"] == str(test_heim)
    assert os.environ["USERPROFILE"] == str(test_heim)
    assert "CLAUDE_CONFIG_DIR" not in os.environ

    # Attrappen für Plugin und Aufräum-Skript liegen im Test-Heimordner, keine privaten Hooks.
    wurzel = context_mode.pruefen(streng=True)
    assert wurzel.resolve().is_relative_to(test_heim.resolve())
    aufraeumen = startklar.aufraeumen_pfad()
    assert aufraeumen.is_file()
    assert aufraeumen.resolve().is_relative_to(test_heim.resolve())
    assert not (test_heim / ".claude" / "settings.json").exists()


def test_vertrauen_schreibt_ins_testheim_und_sperrt_echtes_heim(test_heim: Path, tmp_path: Path) -> None:
    """``vertrauen.sicherstellen`` schreibt ins Test-Heim und sperrt die echte Nutzer-Konfiguration."""
    ziel = tmp_path / "projekt"
    ziel.mkdir()
    vertrauen.sicherstellen(ziel)
    assert (test_heim / ".claude.json").is_file()

    echte_datei = conftest._ECHTES_HEIM / ".claude.json"
    with pytest.raises(PermissionError, match="echte"):
        vertrauen.sicherstellen(ziel, claude_json=echte_datei)


def test_bau_und_context_mode_folgen_umgebogenem_home_zur_laufzeit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nach dem Import folgen ``context_mode`` und ``bau.py`` einem neuen HOME/USERPROFILE."""
    repo = tmp_path / "repo"
    repo.mkdir()
    bau = _lade_bau(monkeypatch, repo)

    neues_heim = tmp_path / "spaeteres-heim"
    conftest.heim_anlegen(neues_heim)
    (neues_heim / ".claude.json").write_text(
        json.dumps({"mcpServers": {"probe-mcp": {"command": "node", "args": ["x.js"]}}}),
        encoding="utf-8",
    )
    skill_md = neues_heim / ".claude" / "skills" / "probe-skill-232" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("# Probe\n", encoding="utf-8")

    monkeypatch.setenv("HOME", str(neues_heim))
    monkeypatch.setenv("USERPROFILE", str(neues_heim))

    wurzel = context_mode.plugin_wurzel()
    assert wurzel is not None and wurzel.resolve().is_relative_to(neues_heim.resolve())
    assert "probe-skill-232" in bau.known_skill_names()
    assert "probe-mcp" in bau.mcp_catalog()

    # Ausdrückliches Patching von Modul-Attributen bleibt weiter wirksam.
    leer = tmp_path / "leer"
    monkeypatch.setattr(bau, "CLAUDE_DIR", leer)
    assert "probe-skill-232" not in bau.known_skill_names()
    monkeypatch.setattr(context_mode, "CLAUDE_DIR", leer)
    assert context_mode.plugin_wurzel() is None


def test_aufseher_prompts_folgen_umgebogenem_home_nach_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nach dem Import folgen ``waechter_takt`` und ``wache`` einem neuen HOME (Notiz-Tresor, #232)."""
    from to_spawn import befugnis, waechter_takt

    spec = importlib.util.spec_from_file_location("wache_testheim_232", SKRIPTE / "wache.py")
    assert spec is not None and spec.loader is not None
    wache = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wache)

    # Nach dem Import HOME/USERPROFILE auf einen zweiten Test-Heimordner umbiegen (wie im nächsten Test).
    neues_heim = tmp_path / "zweites-heim"
    neues_heim.mkdir()
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    monkeypatch.setenv("HOME", str(neues_heim))
    monkeypatch.setenv("USERPROFILE", str(neues_heim))

    erwarteter_tresor = (neues_heim / "obsidian").as_posix()
    takt_prompt = waechter_takt.baue_prompt(293, Path("/r"), ["- x"], None)
    assert befugnis.aufseher_text(f"python {waechter_takt.SKILL.as_posix()}/to_spawn.py") in takt_prompt
    assert erwarteter_tresor in waechter_takt.PROMPT

    wache_prompt = wache.prompt_bauen(293, "/r", 1800, {})
    erwartet_wache = startklar.aufseher_vorlage(befugnis.aufseher_text(f"python {startklar.skill_pfad()}/to_spawn.py"))
    assert erwartet_wache in wache_prompt
    assert erwarteter_tresor in wache.PROMPT


def test_notiz_tresor_und_wache_prompt_trotz_umgebogenem_os_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``config.notiz_tresor`` und ``wache.prompt_bauen`` scheitern nie an umgebogenem ``os.name`` (#232).

    Spiegelt ``test_prompt_linux_python3_und_aufpasser_serverpfad`` unter Windows: dort biegt der
    Test ``os.name`` auf ``"posix"`` um — ``Path(...)`` wirft in Python 3.13 dann
    ``UnsupportedOperation``.
    """
    from to_spawn import config

    spec = importlib.util.spec_from_file_location("wache_osname_232", SKRIPTE / "wache.py")
    assert spec is not None and spec.loader is not None
    wache = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wache)

    erwartet = config.notiz_tresor().as_posix()
    fremd_os = "nt" if os.name == "posix" else "posix"
    monkeypatch.setattr(wache.os, "name", fremd_os)

    assert config.notiz_tresor().as_posix() == erwartet
    text = wache.prompt_bauen(900, "x/y", 1800, {"ssh_ziel": "bau-server", "server_repo": "/home/bau/r"})
    assert erwartet in text


def test_start_tests_fuehren_keine_privaten_hooks_aus_und_lassen_claude_json_unveraendert(
    tmp_path: Path,
) -> None:
    """Start-Tests mit gestelltem „echtem“ Heim: private Hooks laufen nie, ``.claude.json`` bleibt byte-gleich."""
    echtes_heim = tmp_path / "echtes-heim"
    echtes_heim.mkdir()
    claude_json = echtes_heim / ".claude.json"
    claude_json.write_text('{"projects": {}, "privat": "unveraendert"}\n', encoding="utf-8")
    pruefsumme_vorher = _sha256(claude_json)

    # Privater PreToolUse-Hook als Falle: würde jeden Bash-Aufruf melden und blocken.
    falle_beweis = tmp_path / "privater_hook_lief.txt"
    falle_skript = tmp_path / "privater_hook.py"
    falle_skript.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        f"Path({str(falle_beweis)!r}).write_text('ausgefuehrt\\n', encoding='utf-8')\n"
        "sys.stderr.write('Privater Hook hat geblockt\\n')\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )
    settings = echtes_heim / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    hook_befehl = f"{shlex.quote(Path(sys.executable).as_posix())} {shlex.quote(falle_skript.as_posix())}"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": hook_befehl}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    # Auch Plugin + Aufräum-Skript im „echten“ Heim anlegen, damit ein ungeschützter Lauf
    # tatsächlich durch Startklar und vertrauen.sicherstellen läuft.
    import context_mode_attrappe

    context_mode_attrappe.plugin_anlegen(echtes_heim)
    echtes_aufraeumen = echtes_heim / ".claude" / "hooks" / "smart-zone" / "staffel" / "aufraeumen.mjs"
    echtes_aufraeumen.parent.mkdir(parents=True)
    echtes_aufraeumen.write_text("process.exit(0);\n", encoding="utf-8")

    ergebnis = _kind_pytest(
        echtes_heim,
        "tests/test_umzug_212.py::test_wache_setzt_umzug_umgebung",
        "tests/test_waechter_213.py::test_wache_dry_run_zeigt_remote_control_und_ausweich",
        "tests/test_umzug_212.py::test_bau_loescht_alte_anfragen",
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert not falle_beweis.exists(), "Privater Hook aus dem echten Heimordner wurde ausgeführt!"
    assert _sha256(claude_json) == pruefsumme_vorher, "Echte .claude.json wurde verändert!"
    assert not (echtes_heim / ".claude.json.to-spawn.lock").exists()


def test_start_tests_laufen_auch_mit_leerem_echtem_heimordner(tmp_path: Path) -> None:
    """Wie auf CI: ein leerer echter Heimordner genügt und bleibt leer."""
    leeres_heim = tmp_path / "leeres-heim"
    leeres_heim.mkdir()

    ergebnis = _kind_pytest(
        leeres_heim,
        "tests/test_umzug_212.py::test_wache_setzt_umzug_umgebung",
        "tests/test_waechter_213.py::test_wache_dry_run_zeigt_remote_control_und_ausweich",
        "tests/test_umzug_212.py::test_bau_loescht_alte_anfragen",
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert list(leeres_heim.iterdir()) == [], f"Echter Heimordner wurde beschrieben: {list(leeres_heim.iterdir())}"


def test_heim_waechter_bricht_lauf_bei_geaenderter_claude_json(tmp_path: Path) -> None:
    """Der Prüfsummen-Wächter in conftest bricht den Lauf, falls ein Test die echte ``.claude.json`` ändert."""
    echtes_heim = tmp_path / "waechter-heim"
    echtes_heim.mkdir()
    claude_json = echtes_heim / ".claude.json"
    claude_json.write_text('{"vorher": true}\n', encoding="utf-8")

    # Mini-Suite, die tests/conftest.py-Hooks lädt und die echte .claude.json absichtlich ändert.
    ordner = tmp_path / "waechter-suite"
    _waechter_suite_anlegen(claude_json, ordner)
    ergebnis = _waechter_suite_starten(echtes_heim, ordner)
    assert ergebnis.returncode != 0, ergebnis.stdout + ergebnis.stderr
    assert ".claude.json" in (ergebnis.stdout + ergebnis.stderr)


def test_heim_waechter_suite_scannt_keine_elternordner(tmp_path: Path) -> None:
    """Kind-pytest für die Wächter-Suite scannt nie Eltern- oder Temp-Ordner (Windows-Laufwerke C:/D:, #191).

    Unter Windows (``cwd`` auf ``D:``, ``tmp_path`` auf ``C:\\Users\\...\\Temp``) blieb ``rootdir``
    ohne ``--rootdir``/``--confcutdir`` auf ``D:``; pytest lief dann ``C:\\...\\Temp`` ab und brach
    beim Einsammeln ab, sobald ein paralleler xdist-Worker ``to_spawn_pytest_cache_*`` löschte.
    """
    echtes_heim = tmp_path / "waechter-heim"
    echtes_heim.mkdir()
    claude_json = echtes_heim / ".claude.json"
    claude_json.write_text('{"vorher": true}\n', encoding="utf-8")

    eltern = tmp_path / "gemeinsam"
    arbeitsordner = eltern / "repo"
    arbeitsordner.mkdir(parents=True)
    # Falle im Elternordner: wäre ohne --rootdir/--confcutdir als Wurzel geladen und gescannt worden.
    (eltern / "conftest.py").write_text(
        "raise RuntimeError('Elternordner-conftest darf nicht geladen werden')\n",
        encoding="utf-8",
    )
    temp_eltern = eltern / "temp"
    (temp_eltern / "to_spawn_pytest_cache_probe").mkdir(parents=True)

    ordner = temp_eltern / "waechter-suite"
    scandir_log = tmp_path / "scandir.log"
    _waechter_suite_anlegen(claude_json, ordner, scandir_log=scandir_log)

    altes_pypath = os.environ.get("PYTHONPATH", "")
    pypath = f"{SKILL}{os.pathsep}{altes_pypath}" if altes_pypath else str(SKILL)
    ergebnis = _waechter_suite_starten(echtes_heim, ordner, cwd=arbeitsordner, PYTHONPATH=pypath)

    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode != 0, ausgabe
    assert ".claude.json" in ausgabe, ausgabe
    assert "Elternordner-conftest" not in ausgabe, ausgabe

    gescannt = {Path(z).resolve() for z in scandir_log.read_text(encoding="utf-8").splitlines() if z.strip()}
    eltern_menge = {p.resolve() for p in ordner.resolve().parents}
    assert ordner.resolve() in gescannt, gescannt
    assert not (gescannt & eltern_menge), f"Elternordner gescannt: {gescannt & eltern_menge}"
