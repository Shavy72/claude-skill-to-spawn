"""#572: Jeder Kette-Skill liefert ``agents/openai.yaml`` mit aus (wie die Geschwister)."""

from __future__ import annotations

from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / "kette" / "skills"
ORDNER = sorted(p for p in SKILLS.iterdir() if p.is_dir())


def test_selbstprobe_kette_skills_gefunden() -> None:
    assert len(ORDNER) >= 3


@pytest.mark.parametrize("skill", ORDNER, ids=lambda p: p.name)
def test_kette_skill_hat_openai_yaml(skill: Path) -> None:
    yaml = skill / "agents" / "openai.yaml"
    assert yaml.is_file(), f"{skill.name}/agents/openai.yaml fehlt im Repo"
    text = yaml.read_text(encoding="utf-8")
    assert "display_name:" in text and "allow_implicit_invocation: false" in text
