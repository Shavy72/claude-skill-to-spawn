"""Doku-Tests #431: Alias-Skill `/respawn` nennt die SOP-Schritte und ist in SKILL.md verlinkt."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
ALIAS = SKILL / "aliase" / "respawn" / "SKILL.md"
HAUPT = SKILL / "SKILL.md"


def test_respawn_alias_existiert_mit_frontmatter() -> None:
    assert ALIAS.is_file(), ALIAS
    text = ALIAS.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: respawn" in text.split("---\n", 2)[1]


@pytest.mark.parametrize("buchstabe", ["a", "b", "c", "d", "e"])
def test_respawn_alias_nennt_sop_schritt(buchstabe: str) -> None:
    text = ALIAS.read_text(encoding="utf-8")
    assert re.search(rf"^- {buchstabe}\) \S", text, flags=re.MULTILINE), buchstabe


def test_respawn_alias_nennt_aufruf() -> None:
    assert "to_spawn.py respawn <S> <N>" in ALIAS.read_text(encoding="utf-8")


def test_skill_md_verweist_auf_respawn() -> None:
    text = HAUPT.read_text(encoding="utf-8")
    assert "aliase/respawn/SKILL.md" in text
    assert "to_spawn.py respawn" in text
