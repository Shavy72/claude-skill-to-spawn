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


@pytest.mark.parametrize("befehl", SCHREIBWEISE + ANDERE_SHELL + OHNE_TRENNER + EINGABE + SUCHE + STARTER_538)
def test_variante_wird_gesperrt(befehl: str) -> None:
    assert _sperrt(befehl), repr(befehl)


@pytest.mark.parametrize("befehl", ["JA 5", "iex 'ja 5'", "Start-Process ja -ArgumentList 5", "watch ja"])
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
    FLUSS_1 + XARGS_2 + STARTER_3 + GIT_4 + KOMMENTAR_5 + HEREDOC_UMGEHUNG + BEWUSST_GESPERRT_9 + BEWUSST_GESPERRT,
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


# --- PR 610, Fix-Runde 2: Träger-Regel (ein Teil gibt das Wort aus → ganzer Befehl Positivliste) ---

#: Befund 1 — Prozess-Ersetzung als Ausgabe-Ziel.
PROZESS_ZIEL_1 = [
    "echo 'ja 5' | tee >(awk '{system($0)}')",
    "echo 'ja 5' > >(awk '{system($0)}')",
]

#: Befund 2 — Fluss endet nicht an ``;`` in Gruppen, Schleifen, Subshells.
GRUPPE_2 = [
    "{ echo 'ja 5'; true; } | awk '{system($0)}'",
    "(echo 'ja 5'; true) | awk '{system($0)}'",
    "for i in 1; do echo 'ja 5'; true; done | awk '{system($0)}'",
    "if true; then echo 'ja 5'; true; fi | awk '{system($0)}'",
    "echo 'ja 5' | (ls; awk '{system($0)}')",
    "echo 'ja 5' | { ls; awk '{system($0)}'; }",
]

#: Befund 3 — Umleitung und Ausführung im selben Befehl, Deskriptoren, coproc, mkfifo.
UMLEITUNG_3 = [
    "echo 'ja 5' > x.sh; chmod +x x.sh; ./x.sh",
    "echo 'ja 5' > f; awk '{system($0)}' f",
    "exec 3> >(awk '{system($0)}'); echo 'ja 5' >&3",
    "coproc awk '{system($0)}'; echo 'ja 5' >&4",
    "mkfifo p; awk '{system($0)}' p & echo 'ja 5' > p",
    "echo 'ja 5' >&3",
]

#: Befund 4 — xargs mit unbekanntem langem Schalter.
XARGS_4 = ["echo 'ja 5' | xargs --process-slot-var echo -I{} sh -ec '{}'"]

#: Befund 5 — Schreib-Ziele, die später von selbst laufen (Start- und Hook-Dateien).
STARTDATEI_5 = [
    "cat >> /home/x/.bashrc <<'EOF'\nja 5\nEOF",
    "cat >> .git/hooks/pre-commit <<'EOF'\nja 5\nEOF",
    "echo 'ja 5' | tee -a ~/.bashrc",
    "echo 'ja 5' >> \"$HOME/.zshrc\"",
    "echo 'ja 5' > Documents/PowerShell/Microsoft.PowerShell_profile.ps1",
]

#: Eigene Funde derselben Klasse: Pager aus der Umgebung, rg-Vorfilter, find schreibt Dateien.
KLASSE_EIGEN = [
    "GIT_PAGER=sh git log -1 --format='ja 5'",
    "echo 'ja 5' > f; rg --pre sh x f",
    "find . -maxdepth 0 -fprintf x.sh 'ja 5'; sh x.sh",
]


@pytest.mark.parametrize(
    "befehl",
    PROZESS_ZIEL_1 + GRUPPE_2 + UMLEITUNG_3 + XARGS_4 + STARTDATEI_5 + KLASSE_EIGEN,
)
def test_runde2_umgehung_wird_gesperrt(befehl: str) -> None:
    assert _sperrt(befehl), repr(befehl)


#: Alltag der Bau-Agenten — Wert-Teile und reine Text-Befehle bleiben frei.
@pytest.mark.parametrize(
    "befehl",
    [
        "grep -rn ja .",
        "grep -rn 'ja 5' to_spawn | head -5",
        "rg ja to_spawn",
        "cat datei | grep ja",
        "cd pfad && git commit -m ja",
        "git add -A && git commit -m 'ja 5' && git push origin zweig",
        "git add -A && git commit -m \"$(cat <<'EOF'\nja 5\nEOF\n)\" && git push origin zweig",
        "gh issue comment 5 --body \"$(cat <<'EOF'\nja 5\nEOF\n)\"",
        "git log --grep=ja | head -5",
        "git grep -n ja | wc -l",
        "find . -name ja",
        "echo 'ja' > notiz.txt",
        "cat >> notiz.md <<'EOF'\nja 5\nEOF",
        "cd d && grep -c ja datei",
        "echo ja | tee notiz.txt",
    ],
)
def test_runde2_alltag_bleibt_frei(befehl: str) -> None:
    assert not _sperrt(befehl), repr(befehl)


# --- PR 610, Fix-Runde 3: printf -v, Here-Doc-Ende in Ersetzung, .git-Ziele, Wert-Teil, Ziel-Normalform ---

#: Befund 1 — ``printf -v`` wertet den Array-Index aus (auch geklebt, auch über eine Variable).
PRINTF_V_1 = [
    "printf -v 'x[$(ja 5)0]' y",
    "printf -vx 'x[$(ja 5)0]' y",
    "o=-v; printf \"$o\" 'x[$(ja 5)0]' y",
]

#: Befund 2 — bash beendet den Rumpf in ``$(…)`` auch an ``EOF)``.
HEREDOC_ENDE_2 = [
    'git commit -m "$(cat <<\'EOF\'\nmsg\nEOF)"\nja 5\nEOF\n)"',
    'git commit -m "$(cat <<\'EOF\'\nmsg\nEOF)";ja 5\nEOF\n)"',
]

#: Befund 3 — Schreib-Ziele unter ``.git/`` und Git-Konfigurationsdateien.
GIT_ZIEL_3 = [
    "printf '[diff \"x\"]\\n\\ttextconv = ja 5 #\\n' >> .git/config; "
    "echo '* diff=x' >> .git/info/attributes; git log -p -1",
    "echo 'textconv = ja 5' >> .git/config; git log -p -1",
    "echo 'textconv = ja 5' >> ~/.gitconfig; git log -p -1",
    "echo '* diff=ja 5' >> .gitattributes; git log -p -1",
]

#: Befund 4 — Wert-Teil schreibt das Wort, ein anderer Teil liest es zurück und führt es aus.
WERT_ZURUECK_4 = [
    "git commit --allow-empty -m 'ja 5' && git log -1 --format=%s | awk '{system($0)}'",
    "gh issue comment 5 --body 'ja 5' && gh issue view 5 --json comments "
    "--jq '.comments[-1].body' | awk '{system($0)}'",
    "git commit -m 'ja 5' && git log -1 --format=%s | sh",
    "git commit -m 'ja 5' && git push --receive-pack=sh origin zweig",
    "git commit -m 'ja 5' && git fetch --up=sh origin",
]

#: Befund 5 — Ziel nur wörtlich verglichen (Glob, Klammer, doppelte Schrägstriche, uniq mit Ausgabe-Datei).
ZIEL_NORMALFORM_5 = [
    "echo 'ja 5' >> ~/.bashr[c]",
    "echo 'ja 5' | tee -a ~/.bash{rc,_x}",
    "echo 'ja 5' >> .git//hooks/pre-commit",
    "echo 'ja 5' >> .git/./hooks/pre-commit",
    "echo 'ja 5' >> .git/hook[s]/pre-commit",
    "echo 'ja 5' >> a/../.bashrc",
    "echo 'ja 5' > f; uniq f ~/.bashrc",
]


@pytest.mark.parametrize(
    "befehl",
    PRINTF_V_1 + HEREDOC_ENDE_2 + GIT_ZIEL_3 + WERT_ZURUECK_4 + ZIEL_NORMALFORM_5,
)
def test_runde3_umgehung_wird_gesperrt(befehl: str) -> None:
    assert _sperrt(befehl), repr(befehl)


#: Alltag aus dem Befund der Runde 3 — muss frei bleiben.
@pytest.mark.parametrize(
    "befehl",
    [
        "git add -A && git commit -m \"$(cat <<'EOF'\nfix: ja 5\nEOF\n)\" && git push origin zweig",
        "git add -A && git commit -m 'ja 5' && git push -u origin zweig",
        "gh pr create --title t --body \"$(cat <<'EOF'\nja 5\nEOF\n)\"",
        "gh issue comment 5 --body 'Bitte ja 5 eingeben'",
        "git status --short && git commit -m 'ja 5'",
        "git fetch origin && git commit -m 'ja 5'",
        "grep -rn ja .",
        "rg -n ja",
        "cat datei | grep ja | head -5",
        "cd pfad && git commit -m 'ja 5'",
        "git log --grep=ja | head -5",
        "git grep -n ja | wc -l",
        "find . -name ja",
        "cat > docs/x.md <<'EOF'\nja 5\nEOF",
        "printf '%s\\n' 'ja 5'",
        "echo 'ja' > ~/notiz.txt",
        "echo ja | uniq",
    ],
)
def test_runde3_alltag_bleibt_frei(befehl: str) -> None:
    assert not _sperrt(befehl), repr(befehl)


# --- PR 610, Fix-Runde 4: maskierter Backslash, gequoteter Here-Doc, Konfig-Schreiber + git/gh ---

#: Befund 1 — ``\\`` vor dem Zeilenumbruch ist ein maskierter Backslash, keine Zeilenfortsetzung.
FORTSETZUNG_1 = [
    "echo a\\\\\nja 5",
    "cd pfad\\\\\nja 5",
    "# kommentar \\\nja 5",
]

#: Befund 2 — im gequoteten Here-Doc bleibt ``\`` + Zeilenumbruch stehen, die Endzeile verschiebt sich nicht.
HEREDOC_FORTSETZUNG_2 = [
    "git commit -m \"$(cat <<'EOF'\nx\\\nEOF\nja 5\nEOF\n)\"",
    "cat <<'EOF'\nx\\\nEOF\nja 5\nEOF",
]

#: Befund 3 — ein Träger schreibt eine Konfig-Datei, git im selben Befehl liest sie und startet das Programm.
KONFIG_GIT_3 = [
    "mkdir -p b/objects b/refs && echo 'ref: refs/heads/m' > b/HEAD && "
    "printf '%s\\n' '[remote \"o\"]' 'url = .' 'uploadpack = ja 5' > b/config && git -C b fetch o",
    "mkdir -p b/objects b/refs && echo 'ref: refs/heads/m' > b/HEAD && "
    "printf '%s\\n' '[remote \"o\"]' 'url = .' 'uploadpack = ja 5' > b/config && cd b && git fetch o",
    "printf 'uploadpack = ja 5\\n' | tee b/config && git -C b fetch o",
]

#: Befund 4 — ``gh … --web`` startet den Browser aus der gh-Konfiguration.
KONFIG_GH_4 = [
    "printf 'browser: ja 5\\n' > ~/.config/gh/config.yml && gh issue view 5 --web",
    "printf 'browser: ja 5\\n' > ~/.config/gh/config.yml && gh issue view 5 -w",
    "printf 'pager: ja 5\\n' > ~/.config/gh/config.yml && gh issue view 5",
    "echo 'ja 5' | grep ja && gh issue view 5 --web",
]


@pytest.mark.parametrize("befehl", FORTSETZUNG_1 + HEREDOC_FORTSETZUNG_2 + KONFIG_GIT_3 + KONFIG_GH_4)
def test_runde4_umgehung_wird_gesperrt(befehl: str) -> None:
    assert _sperrt(befehl), repr(befehl)


#: Alltag aus dem Befund der Runde 4 (zusätzlich zur Liste der Runde 3) — muss frei bleiben.
ALLTAG_RUNDE4 = [
    "git add -A && git commit -m \"$(cat <<'EOF'\nZeile mit Wort\nEOF\n)\" && git push origin zweig",
    "git add -A && git commit -m \"$(cat <<'EOF'\nfix: a\\b ja 5\nEOF\n)\" && git push origin zweig",
    "git commit -m 'ja 5' \\\n  && git push origin zweig",
    "echo 'ja' > ~/notiz.txt && cat ~/notiz.txt",
    "grep -rn ja . 2>/dev/null && git status --short",
]


@pytest.mark.parametrize("befehl", ALLTAG_RUNDE4)
def test_runde4_alltag_bleibt_frei(befehl: str) -> None:
    assert not _sperrt(befehl), repr(befehl)


# --- PR 610, Fix-Runde 5: xargs mit Ziel tee, Wort nur im Wert-Teil + Datei-Schreiben, Deskriptor-Kopien ---

#: Befund 1 (xargs + tee) und Befund 2 (Wort nur im Wert-Teil, ``git log`` schreibt es in die Konfiguration).
SPERREN_RUNDE5 = [
    "mkdir -p b/objects b/refs && echo 'ref: refs/heads/m' | xargs --arg-file=/dev/null tee b/HEAD && "
    "printf '%s\\n' '[remote \"o\"]' 'url = .' 'uploadpack = ja 5' | xargs --arg-file=/dev/null tee b/config "
    "&& git -C b fetch o",
    "mkdir -p b/objects b/refs && echo 'ref: refs/heads/m' > b/HEAD && git commit -q --allow-empty -m x "
    "-m '[remote \"o\"]' -m 'url = .' -m 'uploadpack = ja 5' && git log -1 --format=%b > b/config "
    "&& git -C b fetch o",
    "mkdir -p b/objects b/refs && echo 'ref: refs/heads/m' > b/HEAD && git commit -q --allow-empty -m x "
    "-m '[remote \"o\"]' -m 'url = .' -m 'uploadpack = ja 5' && git log -1 --format=%b | tee b/config "
    "&& git -C b fetch o",
]


@pytest.mark.parametrize("befehl", SPERREN_RUNDE5)
def test_runde5_umgehung_wird_gesperrt(befehl: str) -> None:
    assert _sperrt(befehl), repr(befehl)


#: Alltag der Runde 5 — Wert-Teil ohne Datei-Ziel und Deskriptor-Kopien bleiben frei.
ALLTAG_RUNDE5 = [
    "git add -A && git commit -m \"$(cat <<'EOF'\nja 5\nEOF\n)\" && git push origin zweig",
    "git commit -m 'ja 5' 2>/dev/null && git push origin zweig",
    "echo 'ja 5' 2>&1 && git status",
    "echo 'ja 5' >&2 && git status",
    "echo 'ja 5' 1>&2 && git status",
]


@pytest.mark.parametrize("befehl", ALLTAG_RUNDE5)
def test_runde5_alltag_bleibt_frei(befehl: str) -> None:
    assert not _sperrt(befehl), repr(befehl)


#: Runde 6 — ``cmd`` liest das Wort auch direkt an ``/c``/``//c``/``/k`` geklebt (am PC belegt).
CMD_GEKLEBT_RUNDE6 = [
    "cmd //cja 5",
    'cmd //c"ja 5"',
    "MSYS_NO_PATHCONV=1 cmd /cja 5",
    "cmd.exe /cja.cmd 5",
    "cmd /Q /cja 5",
    "cmd /Cja 5",
    "CMD /cja 5",
    "CMD.EXE /Cja 5",
    "cmd /kja 5",
    'cmd /c "ja 5"',
    "cmd /cj^a 5",
]


@pytest.mark.parametrize("befehl", CMD_GEKLEBT_RUNDE6)
def test_runde6_cmd_geklebt_wird_gesperrt(befehl: str) -> None:
    assert _sperrt(befehl), repr(befehl)
