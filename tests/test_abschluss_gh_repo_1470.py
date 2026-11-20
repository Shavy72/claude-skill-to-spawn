"""#1470: Die Fertig-Sperre (#1410) greift auch, wenn capo mit abweichendem ``--gh-repo`` läuft.

capo vermerkt in seiner Zustandsdatei, für welches lokale Repo es die Spec führt. Die Sperre in
``abschluss_paket.py`` findet die Datei darüber auch unter fremdem Repo-Namen — kein Aufrufer
muss das Repo mitgeben. Echt laufen das CLI, capos Tick und die Zustandsdateien; nichts gestellt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_waechter_213 import welt  # noqa: F401  (Fixture)
from test_waechter_takt_1410 import _LINKS, SPEC, _abschluss, repo  # noqa: F401  (repo = Fixture)
from to_spawn import capo


def test_sperre_greift_bei_abweichendem_repo_namen(repo: Path, tmp_path: Path) -> None:  # noqa: F811
    """Zustand unter anderem Namen, aber für dieses Repo vermerkt → Exit 4, nichts abgelegt."""
    ordner = tmp_path / "w"
    ordner.mkdir(parents=True)
    (ordner / f"anderer_name_{SPEC}.json").write_text(
        json.dumps({"erledigt": [], "repo": str(repo.resolve())}), encoding="utf-8"
    )
    lauf = _abschluss("ablegen", repo, tmp_path, *_LINKS)
    assert lauf.returncode == 4, lauf.stdout + lauf.stderr
    assert f"anderer_name_{SPEC}.json" in lauf.stderr
    assert not (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").exists()
    nach = _abschluss("nachsehen", repo, tmp_path)
    assert nach.returncode == 4, nach.stdout + nach.stderr


def test_fremde_nummer_anderes_repo_sperrt_nicht(repo: Path, tmp_path: Path) -> None:  # noqa: F811
    """Gleiche Nummer, aber für ein anderes lokales Repo vermerkt → keine Sperre (nur Log-Zeile)."""
    ordner = tmp_path / "w"
    ordner.mkdir(parents=True)
    (ordner / f"anderer_name_{SPEC}.json").write_text(
        json.dumps({"erledigt": [], "repo": str(tmp_path / "ganz_woanders")}), encoding="utf-8"
    )
    lauf = _abschluss("ablegen", repo, tmp_path, *_LINKS)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr


def test_tick_mit_abweichendem_gh_repo_sperrt_abschluss(
    welt: dict[str, Path],  # noqa: F811 (Fixture-Parameter)
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Echter capo-Tick mit ``--gh-repo`` ≠ origin, Spec nicht fertig → Sperre ohne Repo-Angabe greift."""
    from test_waechter_213 import SPEC as SPEC_213
    from test_waechter_213 import _beleg, _commit

    monkeypatch.setattr(capo, "abschluss_anstossen", lambda *_a, **_k: [])
    _commit(welt["repo"], "feat: A (#901)", _beleg("901"))
    erg = capo.tick(welt["repo"], int(SPEC_213), "test/wegwerf", {}, wt_basis=str(welt["wt"]))
    assert not any("SPEC FERTIG" in z for z in erg.zeilen), erg.zeilen
    luecke = capo.fertig_luecke(welt["repo"], int(SPEC_213))
    assert luecke is not None, "Sperre findet capos Zustand unter dem --gh-repo-Namen nicht"
    assert luecke[1] == "Feld spec_fertig_seit fehlt", luecke
