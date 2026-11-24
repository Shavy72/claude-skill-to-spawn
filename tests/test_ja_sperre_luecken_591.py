"""#591 + #538: Die Ja-Sperre erkennt den Aufruf in jeder Schreib- und Start-Variante.

Positivliste statt Verbotsliste: Nur Befehle, deren Argumente sicher Text sind (``echo``, ``grep`` …),
dürfen das Wort tragen; jeder unbekannte Starter mit dem Wort sperrt (fail-closed).
"""

from __future__ import annotations

import io
import json

import pytest

from to_spawn import hooks


def _sperrt(befehl: str, werkzeug: str = "Bash") -> bool:
    roh = json.dumps({"tool_name": werkzeug, "tool_input": {"command": befehl}})
    return hooks.hook_ja_sperre(io.StringIO(roh), io.StringIO()) == 2


#: Gruppe 1 — Schreibweise des Aufrufs (Groß/klein unter Windows, ANSI-C-Quotes, Klammern, Glob).
SCHREIBWEISE = [
    "JA 5",
    "Ja.CMD 5",
    "~/.claude/bin/JA 5",
    "C:\\tools\\JA.CMD 5",
    "$'ja' 5",
    "$'\\x6a\\x61' 5",
    "$'\\152\\141' 5",
    '$"ja" 5',
    "{ja,} 5",
    "{j,x}a 5",
    "~/.claude/bin/j? 5",
    "~/.claude/bin/j[a] 5",
    "~/.claude/bin/j* 5",
]

#: Gruppe 2 — Start über eine andere Shell (Linux und Windows).
ANDERE_SHELL = [
    "ksh -c 'ja 5'",
    "fish -c 'ja 5'",
    "busybox sh -c 'ja 5'",
    "BASH -c 'ja 5'",
    "script -qc 'ja 5' /dev/null",
    "su -c 'ja 5'",
    "wsl ja 5",
    "cmd.exe /C JA 5",
    "Start-Process ja -ArgumentList 5",
    "iex 'ja 5'",
]

#: Gruppe 3 — Start ohne Trennzeichen (Umleitung angeklebt, Befehls-Ersetzung als Befehlsname).
OHNE_TRENNER = [
    "ja<5",
    "ja>out.txt",
    "ja<<<5",
    ">out.txt ja 5",
    "2>/dev/null ja 5",
    "`printf ja` 5",
    "$(printf ja) 5",
    "sudo $(printf ja) 5",
]

#: Gruppe 4 — Eingabe-Umleitung in eine Shell.
EINGABE = [
    "echo 'ja 5' | sh",
    "echo ja 5 | bash",
    "printf 'ja 5' | bash -s",
    "bash <<< 'ja 5'",
    "bash < <(echo ja 5)",
    "source <(echo ja 5)",
    ". <(echo ja 5)",
    "echo 'ja 5' | python3 -c 'import os,sys; os.system(sys.stdin.read())'",
]

#: Gruppe 5 — Start über umgedeutete Such-Befehle.
SUCHE = [
    "find . -name x -exec ja 5 ;",
    "git grep -O ja x",
    "git grep --open-files-in-pager=ja x",
    "git -c core.pager=ja log",
    "alias grep=ja; grep 5",
    "hash -p ./ja grep; grep 5",
]

#: #538 — seltene Starter vor dem Aufruf.
STARTER_538 = [
    "command -p ja 5",
    "ionice -c3 ja 5",
    "flock /tmp/sperre ja 5",
    "watch ja",
    "watch -n1 'ja 5'",
    "taskset 1 ja 5",
    "chrt -i 0 ja 5",
]


@pytest.mark.parametrize(
    "befehl", SCHREIBWEISE + ANDERE_SHELL + OHNE_TRENNER + EINGABE + SUCHE + STARTER_538
)
def test_variante_wird_gesperrt(befehl: str) -> None:
    assert _sperrt(befehl), repr(befehl)


@pytest.mark.parametrize(
    "befehl", ["JA 5", "iex 'ja 5'", "Start-Process ja -ArgumentList 5", "watch ja"]
)
def test_variante_wird_im_powershell_werkzeug_gesperrt(befehl: str) -> None:
    assert _sperrt(befehl, "PowerShell"), repr(befehl)


@pytest.mark.parametrize(
    "befehl",
    [
        "echo ja",
        "printf 'ja\\n'",
        "git commit -m ja",
        "git commit -m 'ja, passt'",
        "git log --oneline -5",
        "gh issue comment 5 --body ja",
        "echo 5 | xargs -n1 echo ja",
        "python to_spawn.py offene 5",
        "cat ja_liste.txt",
        "ls -la",
        "grep ja datei",
        "watch -n1 date",
        "ionice -c3 make",
    ],
)
def test_text_bleibt_frei(befehl: str) -> None:
    assert not _sperrt(befehl), repr(befehl)
