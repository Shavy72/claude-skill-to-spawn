"""#478: Pflicht-Skills sind der Bau-Session bekannt — auch auf einem leeren Rechner.

Fund (Probe-Lauf 2): „Skill unbekannt, trotzdem 'on': security-review / sentry-security-review“.
Pflicht-Skills werden ohnehin erzwungen, auch ohne Datei auf dem Rechner; die Warnung war Lärm.
"""

from __future__ import annotations

from pathlib import Path

from test_bau_ohne_prompt_431 import _lade_bau


def test_jeder_pflicht_skill_ist_bekannt_auf_leerem_rechner(tmp_path: Path, monkeypatch) -> None:
    bau = _lade_bau(monkeypatch, tmp_path)
    monkeypatch.setattr(bau, "CLAUDE_DIR", tmp_path / "leer")  # kein Skill-Ordner, keine Registry, kein Cache
    bekannt = bau.known_skill_names()
    assert {"security-review", "sentry-security-review"} <= set(bau.PFLICHT_SKILLS)
    unbekannt = [n for n in bau.PFLICHT_SKILLS if n not in bekannt]
    assert not unbekannt, f"Pflicht-Skills unbekannt: {unbekannt}"
