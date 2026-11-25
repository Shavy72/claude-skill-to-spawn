"""#413: Fremdes Repo ohne eigene Hook-/Skill-Einträge erzeugt keine fremden Warnungen.

Prüft beide Fundstellen aus dem Issue:
1. Push-Sperre (``nest.git_hooks_einrichten``): Pfad aus ``.to-spawn/config.json``
   (``git_hooks.installer``); ohne Eintrag und ohne Hook-Datei keine Warnung, mit
   Eintrag und fehlender Datei bleibt die Warnung.
2. Skills (``bau.py``): Repo-Skills aus ``.to-spawn/config.json`` (``skills.pflicht``);
   ohne Eintrag keine ``Skill unbekannt``-Warnung für fremde Vorgaben auf leerem Rechner,
   mit Eintrag und unbekanntem Skill bleibt die Warnung.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest
from test_bau_ohne_prompt_431 import _lade_bau
from test_nest_210 import _git, _neues_repo
from test_push_sperre_nest_1445 import _welt

from to_spawn import config, nest


def _konfig_schreiben(repo: Path, daten: dict[str, object]) -> None:
    ordner = repo / ".to-spawn"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "config.json").write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")


def _bau_vorbereiten(
    tmp_path: Path,
    klon: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    dry_run: bool = True,
) -> tuple[object, list[list[str]]]:
    bau = _lade_bau(monkeypatch, klon)
    monkeypatch.setattr(bau, "CLAUDE_DIR", tmp_path / "leeres-claude")
    monkeypatch.setattr(bau, "HOME", tmp_path / "leeres-home")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.setattr(bau.temp_ordner, "umgebung", lambda *_a, **_k: {"TMPDIR": str(tmp_path / "tmp")})
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    monkeypatch.setattr(bau, "gh_repo_ermitteln", lambda _repo: "test/fremdrepo")
    monkeypatch.setattr(bau.config, "sicherstellen", lambda _repo: None)
    monkeypatch.setattr(bau, "find_manifest", lambda _t: ({"spec": "9"}, {"title": "Fremd-Ticket"}))
    monkeypatch.setattr(bau, "build_prompt", lambda *_a, **_k: "PROMPT")
    monkeypatch.setattr(bau, "mcp_catalog", dict)
    monkeypatch.setattr(bau.context_mode, "server_definition", lambda _d: {})
    monkeypatch.setattr(bau, "blocker_schranke", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "auf_speicher_warten", lambda *_a, **_k: None)
    monkeypatch.setattr(bau.speicher, "auf_platte_warten", lambda *_a, **_k: None)
    monkeypatch.setattr(bau.vertrauen, "still_sicherstellen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "umzug_anfragen_aufraeumen", lambda _t: tmp_path / "anfrage.json")
    monkeypatch.setattr(bau, "session_id_setzen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "bau_log_umgebung", lambda *_a, **_k: {})
    gestartet: list[list[str]] = []
    monkeypatch.setattr(bau, "starte_session", lambda cmd, _u: (gestartet.append(cmd), (0, None))[1])
    argv = ["bau.py", "413", "--sofort", "--staffel-max", "1"]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)
    return bau, gestartet


def _settings_aus_tmp(tmp_dir: Path) -> dict[str, object]:
    dateien = sorted(tmp_dir.glob("*-bau/*/settings.json"))
    assert len(dateien) == 1, dateien
    return json.loads(dateien[0].read_text(encoding="utf-8"))


# --- Push-Sperre / Git-Hooks -------------------------------------------------


def test_fremdrepo_ohne_konfig_eintrag_keine_push_sperre_warnung(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    klon, _heim = _welt(tmp_path, mit_installer=False)
    with caplog.at_level(logging.WARNING, logger=nest.log.name):
        ergebnis = nest.git_hooks_einrichten(klon)
    assert ergebnis is False
    assert caplog.records == [], caplog.text


def test_mit_git_hooks_eintrag_und_fehlender_datei_bleibt_warnung(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    klon, _heim = _welt(tmp_path, mit_installer=False)
    _konfig_schreiben(klon, {"git_hooks": {"installer": "scripts/hooks/install_git_hooks.sh"}})
    with caplog.at_level(logging.WARNING, logger=nest.log.name):
        ergebnis = nest.git_hooks_einrichten(klon)
    assert ergebnis is False
    assert "Push-Sperre nicht eingerichtet" in caplog.text
    assert "scripts/hooks/install_git_hooks.sh" in caplog.text
    assert "fehlt" in caplog.text


def test_mit_eigenem_git_hooks_pfad_richtet_hook_ein(tmp_path: Path) -> None:
    quelle = _neues_repo(tmp_path / "quelle")
    marke = tmp_path / "hook-eingerichtet"
    hooks = quelle / "hooks"
    hooks.mkdir()
    (hooks / "setup_hooks.sh").write_text(
        f'test -d "$1" && printf \'%s\\n\' "$1" > "{marke.as_posix()}"\n',
        encoding="utf-8",
    )
    _git(quelle, "add", "-A")
    _git(quelle, "commit", "-m", "Eigener Hook-Installer")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(quelle), str(origin))
    klon = tmp_path / "klon"
    _git(tmp_path, "clone", "-q", str(origin), str(klon))
    _konfig_schreiben(klon, {"git_hooks": {"installer": "hooks/setup_hooks.sh"}})

    assert nest.git_hooks_einrichten(klon) is True
    assert Path(marke.read_text(encoding="utf-8").strip()).resolve() == klon.resolve()


def test_ungueltiger_git_hooks_pfad_warnt_ohne_ausfuehrung(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    klon, _heim = _welt(tmp_path, mit_installer=False)
    _konfig_schreiben(klon, {"git_hooks": {"installer": "../ausbruch.sh"}})
    with caplog.at_level(logging.WARNING, logger=nest.log.name):
        ergebnis = nest.git_hooks_einrichten(klon)
    assert ergebnis is False
    assert "Push-Sperre nicht eingerichtet" in caplog.text


# --- Skills ------------------------------------------------------------------


def test_fremdrepo_ohne_skill_eintrag_keine_skill_unbekannt_warnung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    klon, _heim = _welt(tmp_path, mit_installer=False)
    bau, _gestartet = _bau_vorbereiten(tmp_path, klon, monkeypatch, dry_run=True)
    with caplog.at_level(logging.WARNING, logger=bau.log.name):
        code = bau.main()
    assert code == 0
    assert "Skill unbekannt" not in caplog.text
    overrides = _settings_aus_tmp(tmp_path / "tmp")["skillOverrides"]
    assert isinstance(overrides, dict)
    for pflicht in bau.PFLICHT_SKILLS:
        assert overrides.get(pflicht) == "on"


def test_mit_skill_eintrag_und_fehlendem_skill_bleibt_warnung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    klon, _heim = _welt(tmp_path, mit_installer=False)
    _konfig_schreiben(klon, {"skills": {"pflicht": ["mein-repo-skill"]}})
    bau, _gestartet = _bau_vorbereiten(tmp_path, klon, monkeypatch, dry_run=True)
    with caplog.at_level(logging.WARNING, logger=bau.log.name):
        code = bau.main()
    assert code == 0
    assert "Skill unbekannt, trotzdem 'on': mein-repo-skill" in caplog.text
    overrides = _settings_aus_tmp(tmp_path / "tmp")["skillOverrides"]
    assert isinstance(overrides, dict)
    assert overrides.get("mein-repo-skill") == "on"


def test_mit_skill_eintrag_und_vorhandenem_repo_skill_keine_warnung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    klon, _heim = _welt(tmp_path, mit_installer=False)
    _konfig_schreiben(klon, {"skills": {"pflicht": ["mein-repo-skill"]}})
    skill_dir = klon / ".claude" / "skills" / "mein-repo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: mein-repo-skill\n---\n", encoding="utf-8")
    bau, _gestartet = _bau_vorbereiten(tmp_path, klon, monkeypatch, dry_run=True)
    with caplog.at_level(logging.WARNING, logger=bau.log.name):
        code = bau.main()
    assert code == 0
    assert "Skill unbekannt" not in caplog.text
    overrides = _settings_aus_tmp(tmp_path / "tmp")["skillOverrides"]
    assert isinstance(overrides, dict)
    assert overrides.get("mein-repo-skill") == "on"


# --- Gesamtlauf bau.main in fremdem Repo -------------------------------------


def test_bau_lauf_fremdrepo_ohne_beide_warnungen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    klon, _heim = _welt(tmp_path, mit_installer=False)
    _konfig_schreiben(klon, {"sandbox": {"modus": "aus"}})
    bau, gestartet = _bau_vorbereiten(tmp_path, klon, monkeypatch, dry_run=False)
    umwelt = dict(os.environ)
    try:
        with caplog.at_level(logging.WARNING):
            code = bau.main()
    finally:
        os.environ.clear()
        os.environ.update(umwelt)
    assert code == 0
    assert gestartet
    meldungen = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert not any("Skill unbekannt" in m for m in meldungen), meldungen
    assert not any("Push-Sperre nicht eingerichtet" in m for m in meldungen), meldungen


# --- Konfig-Helfer -----------------------------------------------------------


def test_config_defaults_und_helfer_fuer_git_hooks_und_skills(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert config.DEFAULTS["git_hooks"] == {"installer": "", "sperre": ""}
    assert config.DEFAULTS["skills"] == {"pflicht": []}
    assert config.git_hooks_konfig(config.DEFAULTS) == ("", "")
    assert config.repo_skills(config.DEFAULTS) == []
    assert config.repo_skills({"skills": ["alpha", " beta ", "alpha", ""]}) == ["alpha", "beta"]
    with caplog.at_level(logging.WARNING, logger=config.log.name):
        assert config.repo_skills({"skills": 123}) == []
    assert caplog.records
