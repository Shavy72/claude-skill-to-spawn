"""L1 Etappe 1 P3a: Manifest-, Bau-Log- und Agenten-Ordner kommen aus ``pfade.*`` der Repo-Konfig.

Fehlt der Schlüssel, gilt die Vorgabe (heutiger DuoPlus-Pfad). Pfade bleiben unter dem
übergebenen ``repo`` (nicht unter der aufgelösten Git-Wurzel), damit Aufrufer exakt
dieselben Pfade sehen wie vorher.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))

from to_spawn import bau_log, config, manifest, vorfall  # noqa: E402


def _repo(tmp_path: Path, pfade: dict[str, str] | None = None) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    if pfade is not None:
        datei = repo / config.KONFIG_PFAD
        datei.parent.mkdir(parents=True)
        datei.write_text(json.dumps({"pfade": pfade}), encoding="utf-8")
    return repo


def test_ohne_schluessel_gilt_die_vorgabe(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert manifest.manifest_pfad(repo, 7) == repo / manifest.MANIFEST_ORDNER / "spec-7.json"
    assert bau_log.log_pfad(repo, 12) == repo / bau_log.LOG_ORDNER / "12.jsonl"
    assert vorfall.katalog_pfad(repo) == repo / vorfall.KATALOG_PFAD
    # Aliase bleiben die Vorgaben (andere Module und Tests lesen sie).
    assert manifest.MANIFEST_ORDNER == Path(config.DEFAULTS["pfade"]["manifeste"])
    assert bau_log.LOG_ORDNER == Path(config.DEFAULTS["pfade"]["bau_log"])
    assert vorfall.KATALOG_PFAD == Path(config.DEFAULTS["pfade"]["agenten"]) / "FEHLERKATALOG_spawn.md"


def test_manifeste_aus_konfig(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"manifeste": "plan/manifeste"})
    ordner = repo / "plan" / "manifeste"
    assert manifest.manifest_pfad(repo, 7) == ordner / "spec-7.json"
    ordner.mkdir(parents=True)
    (ordner / "spec-7.json").write_text("{}", encoding="utf-8")
    assert manifest.alle_manifeste(repo) == {"7": ordner / "spec-7.json"}


def test_bau_log_aus_konfig(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"bau_log": "logbuch"})
    assert bau_log.log_pfad(repo, 12) == repo / "logbuch" / "12.jsonl"
    (repo / "logbuch").mkdir()
    (repo / "logbuch" / "12.jsonl").write_text("", encoding="utf-8")
    assert bau_log.hat_log(repo, 12)
    assert bau_log.alle_tickets(repo) == ["12"]


def test_katalog_aus_agenten_ordner(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"agenten": "wissen"})
    erwartet = repo / "wissen" / "FEHLERKATALOG_spawn.md"
    assert vorfall.katalog_pfad(repo) == erwartet
    assert vorfall.katalog_pfad(repo, config.lade(repo)) == erwartet
    # Übergebene Konfig gilt, auch als einfaches dict.
    assert vorfall.katalog_pfad(repo, {"pfade": {"agenten": "anders"}}) == (repo / "anders" / "FEHLERKATALOG_spawn.md")


def test_fehlerkatalog_schluessel_hat_vorrang(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"agenten": "wissen"})
    konfig = {
        "pfade": {"agenten": "wissen"},
        "regularien": {"fehlerkatalog": "eigen/KATALOG.md"},
    }
    assert vorfall.katalog_pfad(repo, konfig) == repo / "eigen" / "KATALOG.md"


@pytest.mark.skipif(sys.platform == "win32", reason="Symlink braucht Rechte")
def test_pfad_bleibt_unter_dem_uebergebenen_repo(tmp_path: Path) -> None:
    echt = _repo(tmp_path, {"manifeste": "m", "bau_log": "b", "agenten": "a"})
    verweis = tmp_path / "verweis"
    verweis.symlink_to(echt, target_is_directory=True)
    assert manifest.manifest_pfad(verweis, 1) == verweis / "m" / "spec-1.json"
    assert bau_log.log_pfad(verweis, 1) == verweis / "b" / "1.jsonl"
    assert vorfall.katalog_pfad(verweis) == verweis / "a" / "FEHLERKATALOG_spawn.md"
