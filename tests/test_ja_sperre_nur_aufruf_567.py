"""#567: Die Ja-Sperre sperrt echte Aufrufe, nicht das Wort als Text.

Erlaubt sind genau zwei Fälle, nur im Bash-Werkzeug: ``cat > datei <<'WORT'`` + Rumpf und eine
einzelne ``grep``/``rg``-Suche in der engen Form. Jeder Umgehungsweg bleibt gesperrt.
"""

from __future__ import annotations

import base64
import io
import json

import pytest

from to_spawn import hooks


def _enc(text: str) -> str:
    return base64.b64encode(text.encode("utf-16-le")).decode()


def _sperrt(befehl: str, werkzeug: str = "Bash") -> bool:
    """Ganzer Hook-Weg mit Werkzeug-Namen: True bei Exit 2."""
    roh = json.dumps({"tool_name": werkzeug, "tool_input": {"command": befehl}})
    return hooks.hook_ja_sperre(io.StringIO(roh), io.StringIO()) == 2


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
    assert _sperrt(befehl), befehl


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
    assert _sperrt(befehl), befehl


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
    assert _sperrt(befehl), befehl


@pytest.mark.parametrize(
    "befehl",
    [
        "cat > notiz.md <<'EOF'\nDann `! ja 5` eingeben oder python to_spawn.py nutzer-antwort 5\nEOF",
        "cat <<'EOF' > notiz.md\n~/.claude/bin/ja 5\nEOF\n",
        "cat >> docs/notiz.md <<'EOF'\nja 5\nEOF",
    ],
)
def test_heredoc_in_datei_erlaubt(befehl: str) -> None:
    assert not _sperrt(befehl), befehl


@pytest.mark.parametrize(
    "befehl",
    [
        "grep -rn nutzer-antwort .",
        "grep -n 'to_spawn.py ja' docs/x.md",
        'rg -n "/.claude/bin/ja" .',
        "grep -e nutzer-antwort -e ja to_spawn.py",
        "rg ja to_spawn.py",
        "grep -A 3 -E 'nutzer-antwort' to_spawn/hooks.py",
        "grep -rn ja docs",
        "rg -n 'to_spawn.py ja' .",
    ],
)
def test_suchmuster_erlaubt(befehl: str) -> None:
    assert not _sperrt(befehl), befehl


@pytest.mark.parametrize(
    "befehl",
    [
        # R1: äußerer heredoc verschluckt den inneren Begrenzer
        ": <<'X'\ncat > f <<'EOF'\nX\nja 5\nEOF",
        "bash <<'X'\ncat > f <<'EOF'\nX\nja 5\nEOF",
        # R2: cat umgedeutet
        "hash -p /usr/bin/bash cat\ncat > f <<'EOF'\nja 5\nEOF",
        "cp /usr/bin/bash ./cat; PATH=.:$PATH\ncat > f <<'EOF'\nja 5\nEOF",
        # R3: Datei schreiben und im selben Befehl ausführen
        "cat > a.sh <<'EOF'\nja 5\nEOF\nbash a.sh",
        "cat > a.sh <<'EOF'\nja 5\nEOF\nsource a.sh",
        "cat > a.sh <<'EOF'\nja 5\nEOF\nrg --pre sh x a.sh",
        # R4: Funktion namens grep/rg
        'rg() { python to_spawn.py "$@"; }; rg nutzer-antwort 5 --wahl ja',
        'grep() { python to_spawn.py "$@"; }\ngrep nutzer-antwort 5',
        # enge Form + irgendetwas dahinter/davor
        "cat > n.md <<'EOF'\nja 5\nEOF\necho fertig",
        "cat > n.md <<'EOF'\nja 5\nEOF; true",
        "cd docs && cat > n.md <<'EOF'\nja 5\nEOF",
        "grep nutzer-antwort f && true",
        "grep nutzer-antwort f > out.txt",
        "grep nutzer-antwort $(ja 5)",
        # nicht die enge Form
        'tee -a n.md <<"EOF" >/dev/null\nja 5\nEOF',
        "cat > n.md <<EOF\nja 5\nEOF",
        "cat >> n.md <<-'EOF'\n\tja 5\n\tEOF",
        "cat > $HOME/n.md <<'EOF'\nja 5\nEOF",
        "cat > *.md <<'EOF'\nja 5\nEOF",
        "/usr/bin/grep nutzer-antwort f",
        "grep -f muster.txt nutzer-antwort f",
        "rg --pre ja nutzer-antwort .",
    ],
)
def test_enge_form_verlassen_bleibt_gesperrt(befehl: str) -> None:
    assert _sperrt(befehl), befehl


def test_hook_laesst_heredoc_in_datei_durch() -> None:
    roh = json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat > n.md <<'EOF'\nja 5\nEOF"}})
    assert hooks.hook_ja_sperre(io.StringIO(roh), io.StringIO()) == 0


@pytest.mark.parametrize(
    "befehl",
    [
        # Runde 3, Fund 1: shlex-Kommentar mitten im Wort, bash kennt ihn dort nicht
        "grep a#; ja 5",
        "grep a#;ja 5 f",
        "rg x#&& ja 5 .",
        # Fund 2: typografische Anführungszeichen (PowerShell nimmt sie als echte)
        "grep 'x\u2019;ja 5;\u2019'",
        'grep "x\u201d;ja 5;\u201c"',
        # Fund 3: rg startet ein Programm
        "rg --hostname-bin=ja --hyperlink-format=file://{host}{path} x .",
        "rg --hostname-bin ja x .",
        # Positivliste: alles außerhalb sperrt
        "grep --unbekannt nutzer-antwort .",
        "rg --pr ja x .",
        "grep nutzer-antwort f & ja 5",
        "grep nutzer-antwort f || ja 5",
        "grep nutzer-antwort f |& ja 5",
        "grep nutzer-antwort f\r\nja 5",
        "grep nutzer-antwort\x0cf",
        "grep nutzer-antwort {a,b}",
        "grep $'nutzer-antwort' f",
        "grep\u00a0nutzer-antwort f",
        "grep nutzer-antwort\u00a0f",
        "grep  nutzer-antwort f",
        "grep 'nutzer-antwort*' f",
        'grep "nutzer-antwort!" f',
        # heredoc: Begrenzer/Ziel außerhalb des Zeichenvorrats, CR, NUL
        "cat > n.md <<'EOF'\r\nja 5\r\nEOF",
        "cat > n.md <<'EOF'\nja 5\nEOF\x00\nja 5\nEOF",
        "cat > n\u00e4.md <<'EOF'\nja 5\nEOF",
        "cat > n.md <<'E\u00d6F'\nja 5\nE\u00d6F",
        "cat\u00a0> n.md <<'EOF'\nja 5\nEOF",
    ],
)
def test_runde3_keine_ausnahme(befehl: str) -> None:
    """Außerhalb der Positivliste: keine Ausnahme, der Hook urteilt wie die allgemeine Prüfung."""
    assert not hooks._bash_text_ohne_aufruf(befehl), repr(befehl)
    assert _sperrt(befehl) == hooks.ja_aufrufe(befehl), repr(befehl)


@pytest.mark.parametrize("befehl", ["grep a#; ja 5", "grep a#;ja 5 f", "rg x#&& ja 5 ."])
def test_runde3_kommentarzeichen_bleibt_gesperrt(befehl: str) -> None:
    assert _sperrt(befehl), befehl


@pytest.mark.parametrize(
    "befehl",
    [
        "cat > n.md <<'EOF'\nja 5\nEOF",
        "grep -rn nutzer-antwort .",
        "rg ja to_spawn.py",
    ],
)
def test_powershell_werkzeug_bekommt_keine_ausnahme(befehl: str) -> None:
    assert _sperrt(befehl, "PowerShell"), befehl


@pytest.mark.parametrize(
    "befehl",
    [
        # Runde 4: Option in Anführungszeichen — bash entfernt die Quotes, das Programm sieht die Option
        "rg '--pre=./ja' x 1234",
        'rg "--pre=./ja" x 1234',
        "rg '--hostname-bin=./ja' x .",
        'rg "--hostname-bin=./ja" x .',
        "grep '--pre=./ja' x f",
        'grep "-f" muster.txt f',
        "grep '-f' muster.txt f",
        "rg ' --pre=./ja' x .",
        "rg '' x .",
        'rg "" x .',
        # Klebe-Formen: ein Wort ist ganz nackt oder ganz in Quotes
        "rg -'-pre=./ja' x .",
        "rg --pr'e=./ja' x .",
        "grep -e'x' f",
        "grep a'b' f",
        "rg ''--pre=./ja x .",
        'rg "-"-pre=./ja x .',
        "rg -A1'--pre=./ja' x .",
        # Wert nach -e mit führendem -
        "grep -e -f muster.txt",
        "grep -e '-f' muster.txt",
        # nackte Wörter mit führendem - außerhalb der Liste
        "rg --pre=./ja x .",
        "grep -f muster.txt f",
    ],
)
def test_runde4_option_in_quotes_keine_ausnahme(befehl: str) -> None:
    assert not hooks._bash_text_ohne_aufruf(befehl), repr(befehl)


@pytest.mark.parametrize(
    "befehl",
    [
        "rg '--pre=./ja' x 1234",
        'rg "--pre=./ja" x 1234',
        "rg --pr'e=./ja' x .",
        "rg '--hostname-bin=./ja' x .",
        "rg --hostname-bin ./ja x .",
        "rg --hostname-bin=ja x .",
    ],
)
def test_runde4_vorfilter_in_quotes_bleibt_gesperrt(befehl: str) -> None:
    assert _sperrt(befehl), befehl


@pytest.mark.parametrize(
    "befehl",
    ["grep -rn nutzer-antwort .", "rg -n 'to_spawn.py ja' docs", "grep -e 'nutzer-antwort' f"],
)
def test_runde4_positiv_bleibt_erlaubt(befehl: str) -> None:
    assert not _sperrt(befehl), befehl
