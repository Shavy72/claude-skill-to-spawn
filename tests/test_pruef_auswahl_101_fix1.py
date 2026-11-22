"""Fix-Runde 1 zu #101 (PR 207): Doku nur aus erklärten Pfaden, schmutziger Baum → voller Lauf.

Läuft ohne ``TO_SPAWN_PROBE``: kleines echtes git-Repo, echter pytest-Lauf. Ersetzt ist nur der
Auswahl-Befehl (pytest-impacted fehlt hier): Er wählt fest „keinen Test“ bzw. ``tests/test_werk.py``
— so wie pytest-impacted für eine ``.md``-Änderung ohne Import-Kante.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from to_spawn.pruef import adapter_py, leiter, sofort
from to_spawn.pruef.leiter import Plan


class _FesteAuswahl(adapter_py.PythonAdapter):
    """Echter Python-Adapter; nur ``auswahl`` hängt feste pytest-Argumente an den vollen Befehl."""

    def __init__(self, *argumente: str) -> None:
        self.argumente = list(argumente)

    def auswahl(self, repo: Path, basis: str) -> Plan:
        plan = self.voll(repo)
        return Plan(plan.werkzeug, [*plan.befehl, *self.argumente], plan.fehler)


KEIN_TEST = _FesteAuswahl("-k", "passt_auf_keinen_test")
NUR_WERK = _FesteAuswahl("tests/test_werk.py")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Basis-Commit: Paket ``app``, Test liest ``app/gruss.md``, Doku-Pfad ``docs/**`` erklärt."""
    dateien = {
        "app/__init__.py": "",
        "app/werk.py": "def eins() -> int:\n    return 1\n",
        "app/gruss.md": "Hallo\n",
        "docs/handbuch.md": "# Handbuch\n",
        "tests/test_gruss.py": (
            "from pathlib import Path\n\n\n"
            "def test_gruss() -> None:\n"
            '    text = (Path(__file__).parents[1] / "app" / "gruss.md").read_text(encoding="utf-8")\n'
            '    assert text == "Hallo\\n"\n'
        ),
        "tests/test_werk.py": "from app.werk import eins\n\n\ndef test_eins() -> None:\n    assert eins() == 1\n",
        ".to-spawn/config.json": '{"pruefen": {"doku_pfade": ["docs/**"]}}\n',
        ".gitignore": "__pycache__/\n",
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


def _commit(repo: Path, pfad: str, text: str) -> str:
    basis = _git(repo, "rev-parse", "HEAD")
    (repo / pfad).write_text(text, encoding="utf-8")
    _git(repo, "commit", "-qam", f"ändert {pfad}")
    return basis


def _pruef_stufe(liste: list[leiter.Stufe]) -> leiter.Stufe:
    return next(s for s in liste if s.name in ("auswahl", "voll"))


def test_a_md_ausserhalb_doku_pfade_prueft_voll(repo: Path) -> None:
    basis = _commit(repo, "app/gruss.md", "Tschüss\n")
    liste = leiter.stufen(repo, (KEIN_TEST,), basis)
    stufe = _pruef_stufe(liste)
    assert stufe.auswahl is not None
    assert (stufe.name, stufe.auswahl["sicher"]) == ("voll", False), stufe.grund
    assert stufe.grund.startswith("R1"), stufe.grund
    assert leiter.ergebnis(liste)[0] == leiter.ROT  # voll sieht den roten Test


def test_a2_md_unter_erklaertem_doku_pfad_bleibt_abkuerzung(repo: Path) -> None:
    basis = _commit(repo, "docs/handbuch.md", "# Handbuch neu\n")
    liste = leiter.stufen(repo, (KEIN_TEST,), basis)
    stufe = _pruef_stufe(liste)
    assert (stufe.lief, stufe.grund) == (False, sofort.NUR_DOKU)
    assert leiter.ergebnis(liste)[0] == leiter.GRUEN


def test_b_schmutziger_baum_prueft_voll_mit_grund(repo: Path) -> None:
    basis = _commit(repo, "app/werk.py", "def eins() -> int:\n    return 1  # neu\n")
    (repo / "app" / "neu.py").write_text("X = 1\n", encoding="utf-8")  # untracked, nicht committet
    stufe = _pruef_stufe(leiter.stufen(repo, (NUR_WERK,), basis))
    assert stufe.auswahl is not None
    assert (stufe.name, stufe.auswahl["sicher"]) == ("voll", False), stufe.grund
    assert "Arbeitsbaum" in stufe.auswahl["grund"], stufe.auswahl["grund"]
