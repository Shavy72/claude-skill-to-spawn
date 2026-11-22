"""#218 Punkt 1: Regel AB und das Gate nutzen dieselbe Stelle für „was zählt als schmutzig“.

Echtes git-Repo, echter pytest-Lauf. Ersetzt ist nur der Auswahl-Befehl (pytest-impacted fehlt
hier): er wählt fest ``tests/test_werk.py``. Laufdateien unter ``.to-spawn/`` und Caches, die der
Lauf selbst erzeugt, dürfen die Auswahl nicht abschalten; echter neuer Code muss es weiter.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from to_spawn.pruef import adapter_py, leiter
from to_spawn.pruef.leiter import Plan


class _FesteAuswahl(adapter_py.PythonAdapter):
    """Echter Python-Adapter; nur ``auswahl`` hängt feste pytest-Argumente an den vollen Befehl."""

    def __init__(self, *argumente: str) -> None:
        self.argumente = list(argumente)

    def auswahl(self, repo: Path, basis: str) -> Plan:
        plan = self.voll(repo)
        return Plan(plan.werkzeug, [*plan.befehl, *self.argumente], plan.fehler)


NUR_WERK = _FesteAuswahl("tests/test_werk.py")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Basis-Commit: Paket ``app`` mit Test; ``.gitignore`` kennt weder ``__pycache__/`` noch Sitzungen."""
    dateien = {
        "app/__init__.py": "",
        "app/werk.py": "def eins() -> int:\n    return 1\n",
        "tests/test_werk.py": "from app.werk import eins\n\n\ndef test_eins() -> None:\n    assert eins() == 1\n",
        ".to-spawn/config.json": '{"pruefen": {"doku_pfade": ["docs/**"]}}\n',
        ".gitignore": "*.log\n",
    }
    for pfad, text in dateien.items():
        ziel = tmp_path / pfad
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(text, encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "probe@example.org")
    _git(tmp_path, "config", "user.name", "probe")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "basis")
    return tmp_path


def _code_commit(repo: Path) -> str:
    basis = _git(repo, "rev-parse", "HEAD")
    (repo / "app" / "werk.py").write_text("def eins() -> int:\n    return 1  # neu\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "ändert app/werk.py")
    return basis


def _pruef_stufe(repo: Path, basis: str) -> leiter.Stufe:
    return next(s for s in leiter.stufen(repo, (NUR_WERK,), basis) if s.name in ("auswahl", "voll"))


def test_a_ungetrackte_sitzungsdatei_laesst_auswahl_zu(repo: Path) -> None:
    basis = _code_commit(repo)
    (repo / ".to-spawn" / "sessions").mkdir()
    (repo / ".to-spawn" / "sessions" / "5.json").write_text('{"nr": 5}\n', encoding="utf-8")
    stufe = _pruef_stufe(repo, basis)
    assert stufe.name == "auswahl", stufe.grund
    assert "AB" not in stufe.grund, stufe.grund


def test_b_zwei_laeufe_ohne_pycache_ignore_beide_auswahl(repo: Path) -> None:
    basis = _code_commit(repo)
    erste = _pruef_stufe(repo, basis)
    assert erste.name == "auswahl", erste.grund
    assert any(repo.rglob("__pycache__")), "erster Lauf sollte Caches erzeugen"
    zweite = _pruef_stufe(repo, basis)
    assert zweite.name == "auswahl", zweite.grund


def test_c_ungetrackter_code_erzwingt_voll_mit_ab(repo: Path) -> None:
    basis = _code_commit(repo)
    (repo / "app" / "neu.py").write_text("X = 1\n", encoding="utf-8")
    stufe = _pruef_stufe(repo, basis)
    assert stufe.name == "voll", stufe.grund
    assert stufe.grund.startswith("AB"), stufe.grund


def test_d_geaenderte_konfig_unter_to_spawn_erzwingt_voll(repo: Path) -> None:
    basis = _code_commit(repo)
    (repo / ".to-spawn" / "config.json").write_text(
        '{"pruefen": {"doku_pfade": ["docs/**", "notizen/**"]}}\n', encoding="utf-8"
    )
    stufe = _pruef_stufe(repo, basis)
    assert stufe.name == "voll", stufe.grund
    assert stufe.grund.startswith("AB"), stufe.grund
