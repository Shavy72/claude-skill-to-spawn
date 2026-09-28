"""Review-Befunde #402 an den Skripten: Stichwort-Wortgrenzen, kaputtes Manifest, gh-Fehlertext, kaputte Seiten-Datei.

Nur die gh-Antwort ist gestellt (externer Dienst); alles andere läuft echt gegen Dateien.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parents[1]
SKRIPTE = SKILL / "skripte"


def _lade(name: str, datei: str) -> ModuleType:
    if str(SKILL) not in sys.path:
        sys.path.insert(0, str(SKILL))
    spec = importlib.util.spec_from_file_location(name, SKRIPTE / datei)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture(scope="module")
def tu() -> ModuleType:
    return _lade("test_uebersicht_402", "test_uebersicht.py")


@pytest.fixture(scope="module")
def ls() -> ModuleType:
    return _lade("leitstand_402", "leitstand.py")


def _status(tu: ModuleType, tmp_path: Path, abnahme: str) -> str:
    """Status einer Karte, deren Belegseite nur die ABNAHME-Zeile enthält."""
    ordner = tmp_path / "verify-hard"
    ordner.mkdir(exist_ok=True)
    (ordner / "7_beleg.md").write_text(f"ABNAHME: {abnahme}\n", encoding="utf-8")
    karte = tu.werte_ticket(ordner, "7", "T")
    return karte.status


def test_unbelegt_ist_nicht_gruen(tu: ModuleType, tmp_path: Path) -> None:
    assert _status(tu, tmp_path, "unbelegt") != "gruen"


def test_nichts_offen_ist_nicht_rot(tu: ModuleType, tmp_path: Path) -> None:
    assert _status(tu, tmp_path, "GRÜN, nichts offen") == "gruen"


def test_echte_stichwoerter_bleiben(tu: ModuleType, tmp_path: Path) -> None:
    assert _status(tu, tmp_path, "BELEGT") == "gruen"
    assert _status(tu, tmp_path, "noch OFFEN") == "rot"
    assert _status(tu, tmp_path, "GRÜN") == "gruen"


@pytest.mark.parametrize("inhalt", ["[1, 2]", '{"tickets": 5}', '{"tickets": [1, 2]}'])
def test_kaputtes_manifest_gibt_exit_2(tu: ModuleType, tmp_path: Path, inhalt: str) -> None:
    m = tmp_path / "docs" / "agents" / "manifests"
    m.mkdir(parents=True)
    (m / "spec-901.json").write_text(inhalt, encoding="utf-8")
    assert tu.main(["901", "--repo", str(tmp_path), "--aus", str(tmp_path / "o.html")]) == 2


def test_gh_fehler_nennt_stderr(ls: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ls, "repo_slug", lambda _r: "eigner/name")
    monkeypatch.setattr(ls.gh_modul, "gh_befehl", lambda: ["gh"])
    monkeypatch.setattr(
        ls,
        "starte",
        lambda cmd, cwd, **kw: subprocess.CompletedProcess(cmd, 4, stdout="", stderr="HTTP 401: Bad credentials\n"),
    )
    with pytest.raises(RuntimeError, match="Bad credentials"):
        ls.gh_issues(tmp_path, ["1"])


def test_seite_datei_kaputt_wirft(ls: ModuleType, tmp_path: Path) -> None:
    fenster = sys.modules[ls.lies_url.__module__]
    ablage = fenster.Ablage(tmp_path, 901) if hasattr(fenster, "Ablage") else None
    assert ablage is not None
    assert fenster.lies_url(ablage) is None  # fehlt: bleibt None
    ablage.seite_datei.parent.mkdir(parents=True, exist_ok=True)
    ablage.seite_datei.write_text("{kaputt", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        fenster.lies_url(ablage)
