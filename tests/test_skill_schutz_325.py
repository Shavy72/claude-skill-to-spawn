"""Weg-Tests für den Skill-Schutz-Hook auf dem Bau-Server (duoplus-management#325).

Echt laufen: das Hook-Skript als Subprozess mit echtem Hook-JSON auf stdin.
``SKILL_SCHUTZ_WURZEL`` setzt das Home auf einen Wegwerf-Ordner, damit ``~`` im
Test dort landet (keine Attrappen).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "nest" / "skill_schutz.py"


def _hook(home: Path, eingabe: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=eingabe,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "SKILL_SCHUTZ_WURZEL": str(home), "HOME": str(home)},
        check=False,
    )


def _json(werkzeug: str, cwd: Path, **tool_input: str) -> str:
    return json.dumps(
        {"tool_name": werkzeug, "tool_input": tool_input, "cwd": str(cwd)}
    )


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    (tmp_path / ".claude" / "skills" / "to-spawn").mkdir(parents=True)
    return tmp_path


def test_edit_in_skills_wird_verweigert(home: Path) -> None:
    ziel = home / ".claude" / "skills" / "to-spawn" / "SKILL.md"
    ergebnis = _hook(home, _json("Edit", home, file_path=str(ziel)))
    assert ergebnis.returncode == 2, ergebnis.stderr
    assert "Skill-Dateien sind auf dem Bau-Server schreibgeschützt" in ergebnis.stderr


def test_edit_relativ_aus_skill_ordner_wird_verweigert(home: Path) -> None:
    cwd = home / ".claude" / "skills" / "to-spawn"
    ergebnis = _hook(home, _json("Write", cwd, file_path="to_spawn/neu.py"))
    assert ergebnis.returncode == 2, ergebnis.stderr


def test_edit_mit_tilde_wird_verweigert(home: Path) -> None:
    ergebnis = _hook(
        home, _json("MultiEdit", home, file_path="~/.claude/skills/to-spawn/x.py")
    )
    assert ergebnis.returncode == 2, ergebnis.stderr


def test_write_ausserhalb_ist_erlaubt(home: Path) -> None:
    ergebnis = _hook(
        home, _json("Write", home, file_path=str(home / "repo" / "datei.py"))
    )
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_bash_sed_i_in_skills_wird_verweigert(home: Path) -> None:
    befehl = "sed -i 's/a/b/' ~/.claude/skills/to-spawn/x"
    ergebnis = _hook(home, _json("Bash", home, command=befehl))
    assert ergebnis.returncode == 2, ergebnis.stderr
    assert "schreibgeschützt" in ergebnis.stderr


def test_bash_skill_aufruf_mit_umleitung_nach_aussen_ist_erlaubt(home: Path) -> None:
    befehl = (
        "python3 ~/.claude/skills/to-spawn/to_spawn.py eintrag --ticket 1 > /tmp/log"
    )
    ergebnis = _hook(home, _json("Bash", home, command=befehl))
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_bash_cat_in_skills_ist_erlaubt(home: Path) -> None:
    befehl = "cat ~/.claude/skills/to-spawn/SKILL.md"
    ergebnis = _hook(home, _json("Bash", home, command=befehl))
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_bash_umleitung_in_skills_wird_verweigert(home: Path) -> None:
    ergebnis = _hook(home, _json("Bash", home, command="echo x > ~/.claude/skills/a"))
    assert ergebnis.returncode == 2, ergebnis.stderr


def test_kaputtes_json_ist_fail_open(home: Path) -> None:
    ergebnis = _hook(home, "{kein json")
    assert ergebnis.returncode == 0
    assert ergebnis.stderr.strip()


# --- Fixrunde nach Prüfpanel (#325) --------------------------------------------


def test_unerwartete_ausnahme_verweigert(home: Path) -> None:
    """Gültiges JSON mit kaputter Form (tool_input als Liste) → verweigern, nicht durchlassen."""
    eingabe = json.dumps({"tool_name": "Edit", "tool_input": ["x"], "cwd": str(home)})
    ergebnis = _hook(home, eingabe)
    assert ergebnis.returncode == 2, ergebnis.stderr
    assert "Traceback" not in ergebnis.stderr


def test_str_replace_im_python_code_ist_lesen(home: Path) -> None:
    cwd = home / ".claude" / "skills" / "to-spawn"
    befehl = "python3 -c \"print('a'.replace('a','b'))\" ~/.claude/skills/x"
    ergebnis = _hook(home, _json("Bash", cwd, command=befehl))
    assert ergebnis.returncode == 0, ergebnis.stderr


def test_path_replace_im_python_code_bleibt_verweigert(home: Path) -> None:
    # ohne „;“ im Code: der Hook trennt Befehlsteile an „;“ auch innerhalb von Anführungszeichen
    befehl = "python3 -c \"__import__('pathlib').Path('a').replace('/root/.claude/skills/b')\""
    ergebnis = _hook(home, _json("Bash", home, command=befehl))
    assert ergebnis.returncode == 2, ergebnis.stderr
