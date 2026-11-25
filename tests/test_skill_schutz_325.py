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
        env={**os.environ, "SKILL_SCHUTZ_WURZEL": str(home), "HOME": str(home), "PYTHONUTF8": "1"},
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

# --- #470: Text ist kein Befehl (Heredoc-Rumpf, mehrzeilige Zeichenkette) ---


def _bash(home: Path, befehl: str) -> int:
    return _hook(home, _json("Bash", home, command=befehl)).returncode


@pytest.mark.parametrize("kopf", ["<<EOF", "<<'EOF'", "<<-EOF", '<<"EOF"'])
def test_heredoc_rumpf_mit_skill_pfad_ist_text(home: Path, kopf: str) -> None:
    befehl = f"cat >> protokoll.md {kopf}\nrm ~/.claude/skills/x\necho > ~/.claude/skills/y\nEOF"
    assert _bash(home, befehl) == 0


def test_mehrzeiliges_argument_mit_skill_pfad_ist_text(home: Path) -> None:
    befehl = 'gh issue create --title t --body "Beispiel:\ncat > ~/.claude/skills/x <<EOF\nrm ~/.claude/skills/x"'
    assert _bash(home, befehl) == 0


def test_heredoc_kopfzeile_ziel_in_skills_bleibt_verweigert(home: Path) -> None:
    befehl = "cat > ~/.claude/skills/x <<'EOF'\ntext\nEOF"
    assert _bash(home, befehl) == 2


def test_heredoc_als_code_fuer_shell_bleibt_verweigert(home: Path) -> None:
    befehl = "bash <<'EOF'\nrm ~/.claude/skills/x\nEOF"
    assert _bash(home, befehl) == 2


def test_zeichenkette_als_code_fuer_shell_bleibt_verweigert(home: Path) -> None:
    befehl = 'sh -c "echo hi\nrm ~/.claude/skills/x"'
    assert _bash(home, befehl) == 2
@pytest.mark.parametrize(
    "befehl",
    [
        "# cat <<EOF\nrm ~/.claude/skills/x\nEOF",
        'echo "<<EOF"\nrm ~/.claude/skills/x\nEOF',
        "cat <<<EOF\nrm ~/.claude/skills/x\nEOF",
        "x=$((1<<N))\nrm ~/.claude/skills/x\nN",
        "echo \\<<EOF\nrm ~/.claude/skills/x\nEOF",
        "# it's\nrm ~/.claude/skills/x\n# don't",
    ],
    ids=["kommentar", "in_anfuehrung", "here_string", "rechenausdruck", "escape", "kommentar_apostroph"],
)
def test_scheinbarer_text_ist_befehl_bleibt_verweigert(home: Path, befehl: str) -> None:
    assert _bash(home, befehl) == 2


# --- #527: python -c mehrzeilig, $(…)/Backticks in Text, ungequoteter Heredoc ---


@pytest.mark.parametrize(
    "befehl",
    [
        "python3 -c 'print(1)\nopen(\"~/.claude/skills/x\",\"w\")'",
        'python3 -c "print(1)\nopen(\'~/.claude/skills/x\',\'w\')"',
        'python -c "import os; os.remove(\'~/.claude/skills/x\')"',
        "python3 -uc 'print(1)\nopen(\"~/.claude/skills/x\",\"w\")'",
        "python3 -c'print(1)\nopen(\"~/.claude/skills/x\",\"w\")'",
        "cd ~/.claude/skills/to-spawn && python3 -c 'print(1)\nopen(\"x\",\"w\")'",
        "python3 - <<EOF\nprint(1)\nEOF\nrm ~/.claude/skills/x",
        'python3 <<EOF\nopen("~/.claude/skills/x","w")\nEOF',
    ],
    ids=[
        "py_c_mehrzeilig_einfach",
        "py_c_mehrzeilig_doppelt",
        "py_c_semikolon",
        "py_uc_gebundelt",
        "py_c_angeklebt",
        "py_c_nach_cd",
        "py_heredoc_danach_rm",
        "py_heredoc_ohne_minus",
    ],
)
def test_python_c_mehrzeilig_wird_verweigert(home: Path, befehl: str) -> None:
    assert _bash(home, befehl) == 2


def test_python_c_mehrzeilig_nur_lesend_bleibt_erlaubt(home: Path) -> None:
    befehl = "python3 -c 'print(1)\nprint(\"~/.claude/skills/x\")'"
    assert _bash(home, befehl) == 0


@pytest.mark.parametrize(
    "befehl",
    [
        'echo "a\n$(rm ~/.claude/skills/x)"',
        'echo "a\n`rm ~/.claude/skills/x`"',
        'echo "a\n$(rm "~/.claude/skills/x")"',
        'echo "a\n$(echo hi\nrm ~/.claude/skills/x)"',
        'echo "a\n$\\\n(rm ~/.claude/skills/x)"',
        'echo "$(rm ~/.claude/skills/x)"',
        'echo "`rm ~/.claude/skills/x`"',
        'cd ~/.claude/skills && echo "a\n$(rm x)"',
    ],
    ids=[
        "dq_mehrzeilig_dollar",
        "dq_mehrzeilig_backtick",
        "dq_mehrzeilig_innere_anfuehrung",
        "dq_mehrzeilig_mehrzeiliges_dollar",
        "dq_zeilenfortsetzung_dollar",
        "dq_einzeilig_dollar",
        "dq_einzeilig_backtick",
        "dq_nach_cd",
    ],
)
def test_ersetzung_in_text_wird_verweigert(home: Path, befehl: str) -> None:
    assert _bash(home, befehl) == 2


@pytest.mark.parametrize(
    "befehl",
    [
        'echo "a\n$(cat ~/.claude/skills/to-spawn/SKILL.md)"',
        "echo 'a\n$(rm ~/.claude/skills/x)'",
    ],
    ids=["dq_lesend_dollar", "einfache_anfuehrung_kein_befehl"],
)
def test_harmlose_ersetzung_oder_einfache_anfuehrung_erlaubt(home: Path, befehl: str) -> None:
    assert _bash(home, befehl) == 0


@pytest.mark.parametrize(
    "befehl",
    [
        "cat <<EOF\n$(rm ~/.claude/skills/x)\nEOF",
        "cat <<EOF\n`rm ~/.claude/skills/x`\nEOF",
        "cat <<-EOF\n\t$(rm ~/.claude/skills/x)\n\tEOF",
        "cat <<EOF\nit's $(rm ~/.claude/skills/x) don't\nEOF",
        "cat <<EOF\n# $(rm ~/.claude/skills/x)\nEOF",
        "cat <<EOF\n$\\\n(rm ~/.claude/skills/x)\nEOF",
        "cat <<EOF\n$(python3 -c 'print(1)\nopen(\"~/.claude/skills/x\",\"w\")')\nEOF",
        "cat <<EOF\n$(echo hi > ~/.claude/skills/x)\nEOF",
        "cat <<EOF\nit's $(rm ~/.claude/skills/x) don't",
        'echo "a\n$(cat <<EOF\nit\'s $(rm ~/.claude/skills/x)\nEOF\n)"',
        'echo "a\n$(cat <<EOF\n)\nEOF\nrm ~/.claude/skills/x)"',
    ],
    ids=[
        "heredoc_ungequotet_dollar",
        "heredoc_ungequotet_backtick",
        "heredoc_tab_dollar",
        "heredoc_apostrophe_dollar",
        "heredoc_kommentarzeichen_dollar",
        "heredoc_zeilenfortsetzung_dollar",
        "heredoc_python_c_mehrzeilig",
        "heredoc_umleitung_in_dollar",
        "heredoc_offen_apostrophe_dollar",
        "heredoc_in_dq_subst_apostroph",
        "heredoc_in_dq_subst_klammer",
    ],
)
def test_heredoc_ohne_anfuehrung_mit_ersetzung_wird_verweigert(home: Path, befehl: str) -> None:
    assert _bash(home, befehl) == 2


@pytest.mark.parametrize(
    "befehl",
    [
        "cat <<'EOF'\n$(rm ~/.claude/skills/x)\nEOF",
        "cat <<EOF\nHallo $(cat ~/.claude/skills/to-spawn/SKILL.md)\nEOF",
    ],
    ids=["heredoc_gequotet_dollar_ist_text", "heredoc_ungequotet_lesend_dollar"],
)
def test_heredoc_mit_harmloser_ersetzung_erlaubt(home: Path, befehl: str) -> None:
    assert _bash(home, befehl) == 0
