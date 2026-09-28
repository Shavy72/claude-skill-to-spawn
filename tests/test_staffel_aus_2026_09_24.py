"""Staffel-Schalter (David, 24.09.2026): ``staffel.aktiv: false`` → kein Staffel-Hook in der Bau-Session."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skripte"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bau  # noqa: E402


def _befehle(settings: dict) -> list[str]:
    return [h["command"] for block in settings["hooks"]["Stop"] for h in block["hooks"]]


def test_staffel_aktiv_vorgabe_an() -> None:
    assert bau.staffel_aktiv({}) is True
    assert bau.staffel_aktiv({"staffel": {"modus": "eltern"}}) is True


def test_staffel_aus_per_konfig() -> None:
    assert bau.staffel_aktiv({"staffel": {"aktiv": False}}) is False


def test_ohne_staffel_kein_staffel_hook_aber_rest_bleibt() -> None:
    mit = _befehle(bau.session_settings({}, True))
    ohne = _befehle(bau.session_settings({}, False))
    assert any("staffel_stop" in b for b in mit)
    assert not any("staffel_stop" in b for b in ohne)
    assert len(ohne) == len(mit) - 1
    assert any("frage-sperre" in b for b in ohne)
    assert any("hook-stop" in b for b in ohne)


def test_repo_konfig_duoplus_hat_staffel_aus() -> None:
    repo = Path(__file__).resolve().parents[4] / "Desktop" / "DuoPlus" / "duoplus-management"
    datei = repo / ".to-spawn" / "config.json"
    if not datei.is_file():
        return
    import json

    assert bau.staffel_aktiv(json.loads(datei.read_text(encoding="utf-8"))) is False
