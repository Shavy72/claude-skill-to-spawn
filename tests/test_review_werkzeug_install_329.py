"""#329 P2: Installer spielt ``review/`` nach ``~/.claude/review`` ein — eingespielter Ordner = Quelle.

Gleiche Überschreib-Regel wie der Review-Hook (``to_spawn/review_hook.py``): abweichende Dateien
werden vorher gesichert, Laufzeit-Daten (``sitzungen/``, Logs), die nur im Ziel liegen, bleiben.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from to_spawn import review_hook

SKILL = Path(__file__).resolve().parents[1]


def _dateien(ordner: Path) -> dict[str, bytes]:
    return {
        p.relative_to(ordner).as_posix(): p.read_bytes()
        for p in ordner.rglob("*")
        if p.is_file() and not {"__pycache__", ".pytest_cache"} & set(p.relative_to(ordner).parts)
    }


def test_werkzeug_quelle_ist_review_ordner() -> None:
    assert review_hook.WERKZEUG_QUELLE == SKILL / "review"


def test_eingespielt_gleich_quelle_laufzeit_bleibt(tmp_path: Path) -> None:
    ziel = tmp_path / "review"
    (ziel / "sitzungen").mkdir(parents=True)
    (ziel / "sitzungen" / "s.json").write_text("{}", encoding="utf-8")
    (ziel / "hook-fehler.log").write_text("alt\n", encoding="utf-8")
    (ziel / "rot_probe.py").write_text("# ältere laufende Fassung\n", encoding="utf-8")

    bericht = review_hook.installiere(ziel, review_hook.WERKZEUG_QUELLE, zeit="t")

    quelle = _dateien(review_hook.WERKZEUG_QUELLE)
    eingespielt = {k: v for k, v in _dateien(ziel).items() if k != review_hook.STAND_DATEI}
    laufzeit = {"sitzungen/s.json", "hook-fehler.log"}
    assert {k: v for k, v in eingespielt.items() if k not in laufzeit} == quelle
    assert laufzeit <= eingespielt.keys()
    gesichert = tmp_path / "_alt" / "review-t" / "rot_probe.py"
    assert gesichert.read_text(encoding="utf-8") == "# ältere laufende Fassung\n"
    assert any("gesichert: rot_probe.py" in z for z in bericht)


def test_main_spielt_hook_und_werkzeug_ein(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert review_hook.main([]) == 0
    assert (tmp_path / ".claude" / "hooks" / "review" / "_hookbasis.py").is_file()
    werkzeug = tmp_path / ".claude" / "review"
    assert {k: v for k, v in _dateien(werkzeug).items() if k != review_hook.STAND_DATEI} == _dateien(
        review_hook.WERKZEUG_QUELLE
    )
