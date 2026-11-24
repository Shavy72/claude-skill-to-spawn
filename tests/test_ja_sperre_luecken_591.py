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


# --- PR 610, Fix-Runde 1: Fluss-Regel (die Ausgabe eines Teils mit dem Wort ist selbst das Wort) ---

#: Befund 1 — Pipe/Here-String/Prozess-Ersetzung in ausführende Programme.
FLUSS_1 = [
    "echo 'ja 5' | awk '{system($0)}'",
    "echo 'ja 5' | sed e",
    "echo 'ja 5' | parallel",
    "echo 'ja 5' | at now",
    "echo 'ja 5' | ssh host",
    "echo 'ja 5' | ssh host bash",
    "printf 'ja 5' |& awk '{system($0)}'",
    "echo 'ja 5' |\n  parallel",
    "awk '{system($0)}' <<< 'ja 5'",
    "awk '{system($0)}' < <(echo ja 5)",
    "cat <(echo ja 5) | parallel",
    "(echo ja 5) | parallel",
    "{ echo ja 5; } | parallel",
    "echo 'ja 5' | cat | parallel",
]

#: Befund 2 — xargs-Ziel ist ein Starter.
XARGS_2 = [
    "echo 'ja 5' | xargs env",
    "echo 'ja 5' | xargs nice",
    "echo 'ja 5' | xargs ionice",
]

#: Befund 3 — Starter vor einem stdin-Interpreter.
STARTER_3 = [
    "echo 'ja 5' | ionice bash",
    "echo 'ja 5' | flock /tmp/x bash",
    "echo 'ja 5' | sudo bash",
]

#: Befund 4 — Text-Schalter nur für benannte Unterbefehle, geklebte Kurzform.
GIT_4 = [
    "git rebase -m --exec='ja 5' HEAD~1",
    "git rebase -x'ja 5' HEAD~1",
    "gh issue close 5 --comment 'ja 5'",
    'git commit -m "$(ja 5)"',
    "git -c alias.x='!ja 5' x",
]

#: Befund 5 — Shell-Kommentar mit Quote.
KOMMENTAR_5 = ["echo x #'\nja 5", 'echo x # "\nja 5', "echo x # kommentar\nja 5"]

#: Here-Doc-Rumpf: nur Text, wenn Begrenzer gequotet und Empfänger Text-Befehl.
HEREDOC_UMGEHUNG = [
    "bash <<'EOF'\nja 5\nEOF",
    "cat <<'EOF' | sh\nja 5\nEOF",
    "cat <<EOF\nja 5\nEOF",
    "node <<'EOF'\npython to_spawn.py nutzer-antwort 5\nEOF",
    "git commit -m \"$(cat <<'EOF'\nx\nEOF\n)\" && ja 5",
    "cat <<'EOF'\nja 5",
]

#: Befund 9 — bewusst weiter gesperrt (``sed`` kennt ``s///e`` und den ``e``-Befehl).
BEWUSST_GESPERRT_9 = ["sed -i 's/ja/nein/' datei"]

#: Here-Doc-Rumpf mit dem Wort ist nur in der genauen Form ``cat > datei <<'WORT'`` frei (#567);
#: jeder Befehl davor oder danach sperrt, auch ein harmloser.
BEWUSST_GESPERRT = ["mkdir -p d && cat > d/x.md <<'EOF'\nja 5\nEOF"]


@pytest.mark.parametrize(
    "befehl",
    FLUSS_1
    + XARGS_2
    + STARTER_3
    + GIT_4
    + KOMMENTAR_5
    + HEREDOC_UMGEHUNG
    + BEWUSST_GESPERRT_9
    + BEWUSST_GESPERRT,
)
def test_runde1_umgehung_wird_gesperrt(befehl: str) -> None:
    assert _sperrt(befehl), repr(befehl)


#: Befund 7 — PowerShell: jedes Vorkommen des Worts sperrt.
@pytest.mark.parametrize(
    "befehl",
    [
        "echo 'ja 5' | iex",
        "& (echo ja) 5",
        "Write-Output `ja 5",
        "python to_spawn.py nutzer-antwort 5",
    ],
)
def test_runde1_powershell_wort_sperrt(befehl: str) -> None:
    assert _sperrt(befehl, "PowerShell"), repr(befehl)


#: Befund 8 und sicher prüfbare Fälle aus 9 — Schreib- und Suchweg der Bau-Agenten.
@pytest.mark.parametrize(
    "befehl",
    [
        "git commit -m \"$(cat <<'EOF'\nfix: Zeile mit ja als Wort (#591)\n\n"
        "Dann `! ja 5` eingeben, it's fine.\nEOF\n)\"",
        "cat > d/x.md <<'EOF'\n# Titel\nja (5) 'x\nEOF",
        "gh pr create --title 'x' --body \"$(cat <<'EOF'\nja 5\nEOF\n)\"",
        "git commit -m 'ja' || true",
        "git -C repo commit -m ja",
        "git commit -am 'ja, passt'",
        "git tag -a v1 -m ja",
        "git grep -n ja",
        "git log --grep=ja",
        "find . -name ja",
        "gh issue view 5 --jq '.body | test(\"ja\")'",
        "echo ja | grep ja",
        "echo x # ja\nls",
    ],
)
def test_runde1_schreibweg_bleibt_frei(befehl: str) -> None:
    assert not _sperrt(befehl), repr(befehl)


def test_runde1_ausnahme_im_parser_sperrt(monkeypatch: pytest.MonkeyPatch) -> None:
    def kaputt(befehl: str) -> bool:
        raise RuntimeError("Parser kaputt")

    monkeypatch.setattr(hooks, "ja_aufrufe", kaputt)
    assert _sperrt("echo ja")
    assert _sperrt("python to_spawn.py nutzer-antwort 5")
    assert not _sperrt("ls -la")


@pytest.mark.parametrize(
    "befehl",
    ["eval " * 1200 + "ja 5", "echo " + "$(" * 60 + "echo ja" + ")" * 60],
)
def test_runde1_tiefe_verschachtelung_sperrt(befehl: str) -> None:
    assert _sperrt(befehl)
