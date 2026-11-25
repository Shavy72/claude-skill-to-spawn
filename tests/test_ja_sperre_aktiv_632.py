"""#632: Enge Sperre-Erkennung (``nur_aktiv=True``) + fehlende Fälle aus PR 630."""

from __future__ import annotations

import json
import logging
import shlex
import sys
from pathlib import Path

import pytest

from to_spawn import briefkasten, ja_einrichten

SKILL = Path(__file__).resolve().parent.parent
CLI = shlex.quote((SKILL / "to_spawn.py").as_posix())
PY = shlex.quote(Path(sys.executable).as_posix())


def _main(claude: Path) -> int:
    return ja_einrichten.main(["--claude-dir", str(claude), "--python", sys.executable])


def _einstellungen(befehl: str, matcher: str = "Bash|PowerShell") -> dict[str, object]:
    return {"hooks": {"PreToolUse": [{"matcher": matcher, "hooks": [{"type": "command", "command": befehl}]}]}}


# --- Enge Regel: nur echter Aufruf, kein Verschlucken des Ergebnisses ---------------------------


@pytest.mark.parametrize(
    "befehl",
    [
        f"{PY} -c 'print(1)' {CLI} hook-ja-sperre",
        f"{PY} -m site {CLI} hook-ja-sperre",
        f"{PY} {CLI} offene {CLI} hook-ja-sperre",
        f"{PY} --version {CLI} hook-ja-sperre",
        f"{CLI} offene {CLI} hook-ja-sperre",
        f"echo {CLI} hook-ja-sperre",
        f"true {CLI} hook-ja-sperre",
    ],
    ids=[
        "python-c-arg",
        "python-m-arg",
        "anderer-unterbefehl",
        "python-version",
        "skript-anderer-unterbefehl",
        "echo-arg",
        "true-arg",
    ],
)
def test_nur_aktiv_lehnt_wortfolge_als_argument_ab(befehl: str) -> None:
    """Wortfolge ``to_spawn.py hook-ja-sperre`` nur als Argument eines anderen Programms sperrt nicht."""
    assert briefkasten.ist_sperre_befehl(befehl, nur_aktiv=True) is False
    assert briefkasten.sperre_eingetragen_in(_einstellungen(befehl)) is False


def test_nur_aktiv_lehnt_anderes_python_skript_mit_sperre_als_argument_ab(tmp_path: Path) -> None:
    """``python anderes.py <cli> hook-ja-sperre`` startet ``anderes.py``, nicht die Sperre."""
    anderes = tmp_path / "anderes.py"
    anderes.write_text("pass\n", encoding="utf-8")
    befehl = f"{PY} {shlex.quote(anderes.as_posix())} {CLI} hook-ja-sperre"
    assert briefkasten.ist_sperre_befehl(befehl, nur_aktiv=True) is False
    assert briefkasten.sperre_eingetragen_in(_einstellungen(befehl)) is False


@pytest.mark.parametrize(
    "befehl",
    [
        f"{PY} {CLI} hook-ja-sperre || true",
        f"{PY} {CLI} hook-ja-sperre||true",
        f"{PY} {CLI} hook-ja-sperre ; true",
        f"{PY} {CLI} hook-ja-sperre;true",
        f"{PY} {CLI} hook-ja-sperre | cat",
        f"{PY} {CLI} hook-ja-sperre &",
        f"{PY} {CLI} hook-ja-sperre && echo ok",
        f"{PY} {CLI} hook-ja-sperre < /dev/null",
        f"{PY} {CLI} hook-ja-sperre --help",
        f"{PY} {CLI} hook-ja-sperre\ntrue",
        f"{PY} {CLI}\nhook-ja-sperre",
        f"! {PY} {CLI} hook-ja-sperre",
        f"({PY} {CLI} hook-ja-sperre)",
        f"FOO=`true` {PY} {CLI} hook-ja-sperre",
        f"FOO=$(true) {PY} {CLI} hook-ja-sperre",
    ],
    ids=[
        "or-true",
        "or-true-ohne-leerzeichen",
        "semikolon-true",
        "semikolon-true-ohne-leerzeichen",
        "pipe-cat",
        "hintergrund",
        "und-danach",
        "stdin-umgeleitet",
        "hilfe-schalter",
        "zeilenumbruch-true",
        "zeilenumbruch-vor-unterbefehl",
        "negation",
        "subshell",
        "backtick-env",
        "ersetzung-env",
    ],
)
def test_nur_aktiv_lehnt_anhaengsel_und_shell_operatoren_ab(befehl: str) -> None:
    """Anhängsel oder Shell-Operatoren, die das Ergebnis verschlucken oder verändern, zählen nie als aktiv."""
    assert briefkasten.ist_sperre_befehl(befehl, nur_aktiv=True) is False
    assert briefkasten.sperre_eingetragen_in(_einstellungen(befehl)) is False


@pytest.mark.parametrize(
    "befehl",
    [
        f"{PY} {CLI} hook-ja-sperre",
        f'"{Path(sys.executable).as_posix()}" "{(SKILL / "to_spawn.py").as_posix()}" hook-ja-sperre',
        f"python {CLI} hook-ja-sperre",
        f"python3 -X utf8 {CLI} hook-ja-sperre",
        f"python3 -u {CLI} hook-ja-sperre",
        f"PYTHONPATH=/tmp {PY} {CLI} hook-ja-sperre",
        f"{CLI} hook-ja-sperre",
    ],
    ids=[
        "standard",
        "gequotet",
        "python-name",
        "python-x-utf8",
        "python-u",
        "mit-env",
        "direkt",
    ],
)
def test_nur_aktiv_bejaht_echten_sperre_aufruf(befehl: str) -> None:
    """Saubere direkte Sperre-Aufrufe bleiben aktiv erkannt."""
    assert briefkasten.ist_sperre_befehl(befehl, nur_aktiv=True) is True
    assert briefkasten.sperre_eingetragen_in(_einstellungen(befehl)) is True


def test_nur_aktiv_mit_leerzeichen_im_pfad(tmp_path: Path) -> None:
    """Gequoteter Zielpfad mit Leerzeichen und Klammern gilt als aktiv."""
    skript = tmp_path / "mein ordner (1)" / "to_spawn.py"
    skript.parent.mkdir()
    skript.write_text("# stub\n", encoding="utf-8")
    befehl = f"{PY} {shlex.quote(skript.as_posix())} hook-ja-sperre"
    assert briefkasten.ist_sperre_befehl(befehl, nur_aktiv=True) is True


def test_breite_regel_ersetzt_entschaerften_eintrag(tmp_path: Path) -> None:
    """Ein Eintrag mit ``|| true`` zählt nicht als aktiv, wird beim Einrichten aber erkannt und ersetzt."""
    entschaerft = f"{PY} {CLI} hook-ja-sperre || true"
    assert briefkasten.ist_sperre_befehl(entschaerft, nur_aktiv=True) is False
    assert briefkasten.ist_sperre_befehl(entschaerft) is True
    claude = tmp_path / ".claude"
    claude.mkdir()
    datei = claude / "settings.json"
    datei.write_text(json.dumps(_einstellungen(entschaerft)), encoding="utf-8")
    assert ja_einrichten.sperre_eintragen(claude, sys.executable, zeit="t") is True
    daten = json.loads(datei.read_text(encoding="utf-8"))
    assert briefkasten.sperre_eingetragen_in(daten) is True
    befehle = [h["command"] for b in daten["hooks"]["PreToolUse"] for h in b["hooks"]]
    assert len(befehle) == 1 and "||" not in befehle[0]


# --- Fehlende Fälle aus PR 630: kaputtes JSON, hooks: [], hooks als Objekt im Block -------------


def test_kaputtes_json_klare_meldung_und_nicht_aktiv(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kaputtes JSON: Exit 1 mit klarer Meldung, Datei bleibt, Nachweis meldet ``False``."""
    claude = tmp_path / ".claude"
    claude.mkdir()
    datei = claude / "settings.json"
    datei.write_text("{kaputt", encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="to_spawn.ja_einrichten"):
        assert _main(claude) == 1
    meldung = " ".join(r.getMessage() for r in caplog.records)
    assert "Ja-Sperre nicht eingetragen" in meldung
    assert "ungültiges JSON" in meldung, meldung
    assert datei.read_text(encoding="utf-8") == "{kaputt"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert briefkasten.sperre_eingetragen() is False


def test_hooks_als_liste_klare_meldung_und_nicht_aktiv(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """``hooks: []`` statt Objekt: Exit 1 mit Meldung zu ``hooks``, Datei bleibt, Nachweis ``False``."""
    claude = tmp_path / ".claude"
    claude.mkdir()
    datei = claude / "settings.json"
    roh = json.dumps({"hooks": []})
    datei.write_text(roh, encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="to_spawn.ja_einrichten"):
        assert _main(claude) == 1
    meldung = " ".join(r.getMessage() for r in caplog.records)
    assert "Ja-Sperre nicht eingetragen" in meldung
    assert "hooks muss ein Objekt sein" in meldung, meldung
    assert datei.read_text(encoding="utf-8") == roh
    assert briefkasten.sperre_eingetragen_in({"hooks": []}) is False


def test_block_hooks_als_objekt_klare_meldung_und_nicht_aktiv(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """``hooks`` als Objekt in einem Block: Exit 1 mit klarer Meldung, Datei bleibt, Nachweis ``False``."""
    claude = tmp_path / ".claude"
    claude.mkdir()
    datei = claude / "settings.json"
    daten = {
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": {"type": "command", "command": f"{PY} {CLI} hook-ja-sperre"}}]
        }
    }
    roh = json.dumps(daten)
    datei.write_text(roh, encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="to_spawn.ja_einrichten"):
        assert _main(claude) == 1
    meldung = " ".join(r.getMessage() for r in caplog.records)
    assert "Ja-Sperre nicht eingetragen" in meldung
    assert "hooks muss eine Liste sein" in meldung, meldung
    assert datei.read_text(encoding="utf-8") == roh
    assert briefkasten.sperre_eingetragen_in(daten) is False
