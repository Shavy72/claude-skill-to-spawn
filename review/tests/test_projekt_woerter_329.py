"""#329 P2: Projekt-Wörter des Routers kommen aus ``config.json`` → ``projekt_woerter``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REVIEW = Path(__file__).resolve().parent.parent
if str(REVIEW) not in sys.path:
    sys.path.insert(0, str(REVIEW))

from _gemeinsam import DateiDiff, EingabeFehler, signal_trifft, signale_fuer  # noqa: E402


def _ids(cfg: dict, pfad: str, hinzu: list[str], tmp_path: Path) -> set[str]:
    d = DateiDiff(pfad=pfad, zeilen=len(hinzu), hinzu=hinzu)
    return {s.id for s in signale_fuer(cfg) if signal_trifft(s, tmp_path, d)}


def test_mitgelieferte_config_behaelt_bisherige_ausloeser(tmp_path: Path) -> None:
    """Die ausgelieferte Config trägt die früher fest verdrahteten Wörter weiter (gleiches Verhalten)."""
    cfg = json.loads((REVIEW / "config.json").read_text(encoding="utf-8"))
    for signal_id, woerter in cfg["projekt_woerter"].items():
        for wort in woerter:
            assert signal_id in _ids(cfg, "app.py", [f"x = {wort}.run()"], tmp_path), wort
            assert signal_id in _ids(cfg, f"web/{wort}/a.py", ["x = 1"], tmp_path), wort


def test_wort_nur_mit_config(tmp_path: Path) -> None:
    zeile = ["x = lagerhalle_api.senden()"]
    assert "kosten" not in _ids({}, "app.py", zeile, tmp_path)
    assert "kosten" in _ids({"projekt_woerter": {"kosten": ["lagerhalle_api"]}}, "app.py", zeile, tmp_path)


@pytest.mark.parametrize("kaputt", [["x"], {"kosten": "x"}, {"kosten": [1]}])
def test_kaputte_config_ist_eingabefehler(kaputt: object) -> None:
    with pytest.raises(EingabeFehler):
        signale_fuer({"projekt_woerter": kaputt})
