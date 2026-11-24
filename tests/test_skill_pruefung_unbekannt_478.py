"""#478: Pflicht-Skills sind der Bau-Session bekannt — auch auf einem Rechner ohne die Dateien.

Fund (Probe-Lauf 2): „Skill unbekannt, trotzdem 'on': security-review / sentry-security-review“.
``security-review`` ist ein Builtin von Claude Code, ``sentry-security-review`` ein Plugin-Skill
ohne Datei hier; beide stehen in ``PFLICHT_SKILLS`` und gehören darum in die Bekannt-Liste.
"""

from __future__ import annotations

from pathlib import Path

from test_bau_ohne_prompt_431 import _lade_bau


def test_pflicht_skills_sind_bekannt_ohne_claude_ordner(tmp_path: Path, monkeypatch) -> None:
    bau = _lade_bau(monkeypatch, tmp_path)
    monkeypatch.setattr(bau, "CLAUDE_DIR", tmp_path / "leer")  # nichts installiert
    ohne_datei = ("security-review", "sentry-security-review")  # kein Skill-Ordner, nur Builtin/Plugin
    assert set(ohne_datei) <= set(bau.PFLICHT_SKILLS)
    unbekannt = [n for n in ohne_datei if n not in bau.known_skill_names()]
    assert not unbekannt, f"Pflicht-Skills unbekannt: {unbekannt}"
