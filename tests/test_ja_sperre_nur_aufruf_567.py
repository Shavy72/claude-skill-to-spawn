"""#567: Die Ja-Sperre sperrt echte Aufrufe, nicht das Wort als Text.

Erlaubt sind genau zwei Fälle: der Rumpf eines heredocs, der per ``cat``/``tee`` in eine Datei
geht, und das Suchmuster von ``grep``/``rg``. Jeder Umgehungsweg bleibt gesperrt.
"""

from __future__ import annotations

import base64
import io
import json

import pytest

from to_spawn import hooks


def _enc(text: str) -> str:
    return base64.b64encode(text.encode("utf-16-le")).decode()


@pytest.mark.parametrize(
    "befehl",
    [
        # direkt, Pfad, .cmd
        "ja 5",
        "~/.claude/bin/ja 5",
        "C:/tools/bin/ja.cmd 5",
        # Shells mit Text
        'bash -c "ja 5"',
        "sh -c 'ja 5'",
        'powershell -Command "ja 5"',
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ja 5",
        "pwsh -c 'Write-Host x; ja 5'",
        f"powershell -EncodedCommand {_enc('ja 5')}",
        "powershell -ec kaputt!!",
        "cmd /c ja 5",
        # python … to_spawn.py <unterbefehl>
        "python to_spawn.py ja 5",
        "python3 ~/.claude/skills/to-spawn/to_spawn.py nutzer-antwort 5 --id x --wahl ja",
        # Verkettung
        "echo x; ja 5",
        "true && ja 5",
        "echo 5 | ja",
        # Befehls-Ersetzung
        "echo $(ja 5)",
        'echo "$(ja 5)"',
        "echo `ja 5`",
        # eval, xargs, Umgebungs-Präfix
        "eval 'ja 5'",
        "echo 5 | xargs ja",
        "VAR=x ja 5",
        "env VAR=x ja 5",
    ],
)
def test_umgehung_bleibt_gesperrt(befehl: str) -> None:
    assert hooks.ja_aufrufe(befehl), befehl


@pytest.mark.parametrize(
    "befehl",
    [
        "bash <<'EOF'\nja 5\nEOF",
        "cat <<'EOF' | bash\nja 5\nEOF",
        "cat > f.txt <<EOF\n$(ja 5)\nEOF",
        "cat > f.txt <<'EOF'\nja 5\n",
        "cat > f.txt <<'EOF'\nnur Text\nEOF\nja 5",
        "cat > f.txt <<'EOF' && ja 5\ntext\nEOF",
        "bash -s \\\ncat > f.txt <<'EOF'\nja 5\nEOF",
        "echo \"\ncat > f.txt <<'EOF'\n\"; ja 5\nEOF",
        "cat > f.txt<<EO'F'\nEOF\nja 5\nEO",
        "cat() { bash; }\ncat > f.txt <<'EOF'\nja 5\nEOF",
        'cat > "x<<EOF"\nja 5\nEOF',
    ],
)
def test_heredoc_in_shell_bleibt_gesperrt(befehl: str) -> None:
    assert hooks.ja_aufrufe(befehl), befehl


@pytest.mark.parametrize(
    "befehl",
    [
        "grep nutzer-antwort f; ja 5",
        "grep -rn x $(ja 5)",
        "rg --pre ja muster .",
        "rg --pre=~/.claude/bin/ja muster .",
        "grep x f | xargs ja",
    ],
)
def test_suche_mit_aufruf_bleibt_gesperrt(befehl: str) -> None:
    assert hooks.ja_aufrufe(befehl), befehl


@pytest.mark.parametrize(
    "befehl",
    [
        "cat > notiz.md <<'EOF'\nDann `! ja 5` eingeben oder python to_spawn.py nutzer-antwort 5\nEOF",
        "cat <<'EOF' > notiz.md\n~/.claude/bin/ja 5\nEOF\necho fertig",
        'tee -a notiz.md <<"EOF" >/dev/null\nja 5\nEOF',
        "cat > notiz.md <<EOF\nja 5\nEOF",
        "cat >> notiz.md <<-'EOF'\n\tja 5\n\tEOF",
    ],
)
def test_heredoc_in_datei_erlaubt(befehl: str) -> None:
    assert not hooks.ja_aufrufe(befehl), befehl


@pytest.mark.parametrize(
    "befehl",
    [
        "grep -rn nutzer-antwort .",
        "grep -n 'to_spawn.py ja' docs/x.md",
        'rg -n "~/.claude/bin/ja" .',
        "grep -e nutzer-antwort -e ja to_spawn.py",
        "rg ja to_spawn.py",
        "grep -A 3 -E 'nutzer-antwort|ja' to_spawn/hooks.py",
    ],
)
def test_suchmuster_erlaubt(befehl: str) -> None:
    assert not hooks.ja_aufrufe(befehl), befehl


def test_hook_laesst_heredoc_in_datei_durch() -> None:
    roh = json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat > n.md <<'EOF'\nja 5\nEOF"}})
    assert hooks.hook_ja_sperre(io.StringIO(roh), io.StringIO()) == 0
