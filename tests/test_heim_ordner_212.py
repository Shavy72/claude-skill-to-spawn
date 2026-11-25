"""Heimat-Ordner erst bei Gebrauch über eine Tür bestimmen (#212, P7f/#68).

Prüft:
- Kein Import- oder Parser-Aufruf löst ``Path.home()`` / ``expanduser("~")`` aus
  (``context_mode``, ``hausmeister``, ``waechter_takt``, ``nest``, ``cli_parser``).
- Eine Tür :func:`to_spawn.config.heim_ordner` (+ :class:`to_spawn.config.KeinHeimOrdner`):
  Windows und Linux gleich — klare Meldung statt Absturz beim Import oder stillem
  ``/etc/passwd``-Rückfall.
- Echte Prozesse (``to_spawn.py``) ohne ``HOME`` / ``USERPROFILE`` / ``HOMEDRIVE`` /
  ``HOMEPATH`` in der Umgebung:
  - Befehle ohne Heimordner-Bedarf laufen sauber durch (Exit 0).
  - Befehle mit Heimordner-Bedarf starten ohne Import-Absturz/Traceback und melden
    verständlich, was fehlt (Exit != 0).
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import config, context_mode, hausmeister

#: Umgebungsvariablen, aus denen Windows oder Linux den Heimat-Ordner ableiten.
HEIM_VARIABLEN = ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "CLAUDE_CONFIG_DIR")


def _env_ohne_heim(**zusatz: str) -> dict[str, str]:
    """Minimale Prozess-Umgebung ohne jeden Hinweis auf den Heimat-Ordner."""
    basis = {k: v for k, v in os.environ.items() if k.upper() not in HEIM_VARIABLEN and not k.startswith("TO_SPAWN_")}
    basis["PYTHONIOENCODING"] = "utf-8"
    basis.update(zusatz)
    return basis


def _cli(cwd: Path, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), *args],
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def _ohne_heim_monkeypatch(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in HEIM_VARIABLEN:
        monkeypatch.delenv(var, raising=False)


# --- 1: Kein Path.home() / expanduser("~") beim Import oder Parser-Aufbau ---------


def test_import_und_parser_ohne_heimordner_im_prozess(tmp_path: Path) -> None:
    """Frischer Python-Prozess mit gesperrtem ``Path.home``/``expanduser`` importiert
    alle CLI-Module (inkl. ``context_mode``, ``hausmeister``, ``waechter_takt``) und
    baut den Parser ohne Fehler (stellt Windows ohne ``USERPROFILE`` nach)."""
    code = f"""
import pathlib, sys
from unittest.mock import patch

sys.path.insert(0, {str(SKILL)!r})

def _kein_heim(cls=None):
    raise RuntimeError("Could not determine home directory.")

def _kein_expand(self):
    if str(self).startswith("~"):
        raise RuntimeError("Could not determine home directory.")
    return self

with patch.object(pathlib.Path, "home", classmethod(_kein_heim)), \\
     patch.object(pathlib.Path, "expanduser", _kein_expand):
    import to_spawn.config
    import to_spawn.context_mode
    import to_spawn.hausmeister
    import to_spawn.waechter_takt
    import to_spawn.nest
    import to_spawn.cli_parser as cp
    import to_spawn.cli_befehle
    ap = cp.baue_parser()
    assert ap is not None
print("IMPORT_UND_PARSER_OK")
"""
    lauf = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env=_env_ohne_heim(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "IMPORT_UND_PARSER_OK" in lauf.stdout


# --- 2: Eine Tür (config.heim_ordner) — Windows und Linux gleich ------------------


def test_heim_ordner_eine_tuer_ohne_umgebung_wirft_klar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne HOME/USERPROFILE wirft :func:`config.heim_ordner` :class:`config.KeinHeimOrdner`
    mit verständlicher Meldung — auf Linux genauso wie unter Windows."""
    _ohne_heim_monkeypatch(monkeypatch)
    with pytest.raises(config.KeinHeimOrdner) as fehler:
        config.heim_ordner()
    text = str(fehler.value)
    assert "Heimat-Ordner" in text
    assert "HOME" in text and "USERPROFILE" in text


def test_heim_ordner_liest_home_oder_userprofile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mit ``HOME`` oder ``USERPROFILE`` liefert die eine Tür den jeweiligen Pfad."""
    _ohne_heim_monkeypatch(monkeypatch)
    a = tmp_path / "heim-a"
    b = tmp_path / "heim-b"
    monkeypatch.setenv("USERPROFILE", str(b))
    assert config.heim_ordner() == b
    # HOME allein: unter Windows zieht conftest._HeimMonkeyPatch USERPROFILE mit → danach entfernen.
    monkeypatch.setenv("HOME", str(a))
    monkeypatch.delenv("USERPROFILE", raising=False)
    assert config.heim_ordner() == a
    # Beide gesetzt: USERPROFILE nach HOME setzen, damit _HeimMonkeyPatch es unter Windows nicht überschreibt.
    monkeypatch.setenv("USERPROFILE", str(b))
    if sys.platform == "win32":
        assert config.heim_ordner() == b
    else:
        assert config.heim_ordner() == a


def test_heim_ordner_respektiert_umgebogenes_path_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Biegt ein Test ``Path.home`` um, respektiert :func:`config.heim_ordner` das
    (z. B. ``test_maschine_standardort_unter_home``) und übersetzt ``RuntimeError`` klar."""
    ziel = tmp_path / "umgebogen"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: ziel))
    assert config.heim_ordner() == ziel

    def _kaputt(cls: type[Path]) -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(Path, "home", classmethod(_kaputt))
    with pytest.raises(config.KeinHeimOrdner) as fehler:
        config.heim_ordner()
    assert "Heimat-Ordner" in str(fehler.value)


def test_context_mode_claude_ordner_lazy_und_respektiert_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``context_mode`` löst den Heimordner erst beim Aufruf auf; ``CLAUDE_CONFIG_DIR``
    reicht auch ganz ohne ``HOME``/``USERPROFILE``."""
    _ohne_heim_monkeypatch(monkeypatch)
    with pytest.raises(config.KeinHeimOrdner):
        context_mode.claude_ordner()
    with pytest.raises(config.KeinHeimOrdner):
        context_mode.plugin_wurzel()

    eigen = tmp_path / "eigen-claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(eigen))
    assert context_mode.claude_ordner() == eigen
    assert context_mode.CLAUDE_DIR == eigen
    assert context_mode.plugin_wurzel() is None

    # Nach reload() ohne CLAUDE_CONFIG_DIR und ohne HOME kein Import-Absturz.
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    modul = importlib.reload(context_mode)
    with pytest.raises(config.KeinHeimOrdner):
        modul.claude_ordner()


def test_hausmeister_hauptrepo_namen_ohne_heimordner(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_hauptrepo_namen_aus`` liest Namen von ``~/…``-Einträgen ohne Heimordner."""
    _ohne_heim_monkeypatch(monkeypatch)
    namen = hausmeister._hauptrepo_namen_aus([{"pfad": "~/alpha"}, {"pfad": "/srv/beta"}, {"pfad": "C:/dev/gamma"}])
    assert namen == ("alpha", "beta", "gamma")


# --- 3: Echte CLI-Prozesse ohne HOME / USERPROFILE --------------------------------


def test_cli_ohne_heim_befehl_ohne_heimbedarf_laeuft_durch(tmp_path: Path) -> None:
    """Echter Prozess: ``to_spawn.py --help`` und ``to_spawn.py eintrag`` brauchen keinen
    Heimat-Ordner und laufen ohne ``HOME``/``USERPROFILE`` mit Exit 0 durch."""
    hilfe = _cli(tmp_path, "--help", env=_env_ohne_heim())
    assert hilfe.returncode == 0, hilfe.stdout + hilfe.stderr
    assert "eintrag" in hilfe.stdout

    repo = tmp_path / "repo"
    repo.mkdir()
    eintrag = _cli(
        repo,
        "eintrag",
        "--ticket",
        "212",
        "--typ",
        "vorfall",
        "--klasse",
        "skill",
        "--symptom",
        "Start ohne HOME",
        "--ursache",
        "Path.home beim Import",
        "--loesung",
        "erst bei Gebrauch bestimmen",
        env=_env_ohne_heim(TO_SPAWN_REPO=str(repo)),
    )
    assert eintrag.returncode == 0, eintrag.stdout + eintrag.stderr
    assert (repo / "docs" / "agents" / "bau_log" / "212.jsonl").is_file()


def test_cli_ohne_heim_befehl_mit_heimbedarf_meldet_klar_was_fehlt(tmp_path: Path) -> None:
    """Echter Prozess: ein CLI-Befehl, der den Heimat-Ordner braucht (``setup --zeigen``,
    ``nest werkzeuge``), startet ohne Import-Absturz und meldet verständlich ohne Traceback."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for befehl in (("setup", "--zeigen"), ("nest", "werkzeuge")):
        lauf = _cli(repo, *befehl, env=_env_ohne_heim(TO_SPAWN_REPO=str(repo)))
        ausgabe = lauf.stdout + lauf.stderr
        assert lauf.returncode != 0, f"{befehl}: hätte ohne Heimordner scheitern müssen: {ausgabe}"
        assert "Traceback" not in ausgabe, f"{befehl}: roher Traceback statt klarer Meldung:\n{ausgabe}"
        assert "Heimat-Ordner" in ausgabe, f"{befehl}: Meldung nennt Heimat-Ordner nicht:\n{ausgabe}"
        assert "HOME" in ausgabe and "USERPROFILE" in ausgabe, ausgabe


def test_cli_ohne_heim_hausmeister_aufraeumen_meldet_klar(tmp_path: Path) -> None:
    """Echter Prozess: ``to_spawn.py aufraeumen <ordner>`` ohne ``HOME``/``USERPROFILE``
    stürzt nicht beim Import ab, sondern meldet verständlich den fehlenden Heimat-Ordner."""
    ordner = tmp_path / "wt-212"
    ordner.mkdir()
    lauf = _cli(tmp_path, "aufraeumen", str(ordner), env=_env_ohne_heim())
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 1, ausgabe
    assert "Traceback" not in ausgabe, ausgabe
    assert "Heimat-Ordner" in ausgabe, ausgabe
    assert "HOME" in ausgabe and "USERPROFILE" in ausgabe, ausgabe
