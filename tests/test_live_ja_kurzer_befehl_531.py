"""Live-Ja als kurzer Befehl ``ja <N>`` ohne Terminal (#531).

Echt laufen: die CLI (``to_spawn.py ja``/``hook-ja-sperre``, stdin = DEVNULL, also kein TTY) und die
Einrichtung gegen ein tmp-HOME — die echte ``~/.claude/settings.json`` wird nie berührt.
Attrappen: keine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import briefkasten, capo, ja_einrichten, waechter_takt
from to_spawn.befugnis import aufseher_text


def _cli(
    repo: Path, heim: Path, *argv: str, eingabe: str | None = None, **zusatz: str
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env.update(
        {
            "TO_SPAWN_REPO": str(repo),
            "HOME": str(heim),
            "USERPROFILE": str(heim),
            **zusatz,
        }
    )
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), *argv],
        cwd=repo,
        env=env,
        input=eingabe,
        stdin=None if eingabe is not None else subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.fixture
def heim(tmp_path: Path) -> Path:
    ordner = tmp_path / "heim"
    ordner.mkdir()
    return ordner


@pytest.fixture
def mit_sperre(heim: Path) -> Path:
    assert ja_einrichten.sperre_eintragen(heim / ".claude", sys.executable) is True
    return heim


def _frage(repo: Path, heim: Path, ticket: str = "7") -> str:
    lauf = _cli(
        repo,
        heim,
        "frage",
        ticket,
        "--frage",
        "Live schalten?",
        "--vorschlag",
        "nein",
        "--klasse",
        "2",
    )
    assert lauf.returncode == 0, lauf.stderr
    return lauf.stdout.rsplit("id", 1)[1].strip()


# --- ja ohne Terminal ---------------------------------------------------------------------------


def test_ja_ohne_terminal_mit_sperre_schreibt_das_ja(
    tmp_path: Path, mit_sperre: Path
) -> None:
    fid = _frage(tmp_path, mit_sperre)
    lauf = _cli(tmp_path, mit_sperre, "ja", "7")
    assert lauf.returncode == 0, lauf.stderr
    stand = briefkasten.lesen("7", fid, hauptbaum=tmp_path)
    assert stand.von == briefkasten.NUTZER_VON and stand.wahl == "ja"


def test_ja_ohne_terminal_ohne_sperre_fail_closed(tmp_path: Path, heim: Path) -> None:
    fid = _frage(tmp_path, heim)
    lauf = _cli(tmp_path, heim, "ja", "7")
    assert lauf.returncode == 2
    assert "Skill installieren" in lauf.stderr and "Terminal" in lauf.stderr
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER


def test_nutzer_antwort_ohne_terminal_ohne_sperre_bleibt_verweigert(
    tmp_path: Path, heim: Path
) -> None:
    fid = _frage(tmp_path, heim)
    lauf = _cli(
        tmp_path,
        heim,
        "nutzer-antwort",
        "7",
        "--id",
        fid,
        "--wahl",
        "ja",
        "--grund",
        "x",
    )
    assert lauf.returncode == 2


def test_bau_session_bleibt_gesperrt_auch_mit_sperre(
    tmp_path: Path, mit_sperre: Path
) -> None:
    fid = _frage(tmp_path, mit_sperre)
    lauf = _cli(tmp_path, mit_sperre, "ja", "7", TO_SPAWN_TICKET="7")
    assert lauf.returncode == 2
    assert briefkasten.lesen("7", fid, hauptbaum=tmp_path) == briefkasten.NUTZER


def test_ja_ohne_offene_frage_eine_zeile(tmp_path: Path, mit_sperre: Path) -> None:
    lauf = _cli(tmp_path, mit_sperre, "ja", "7")
    assert lauf.returncode == 1
    assert (
        "0 offene Fragen" in lauf.stderr and len(lauf.stderr.strip().splitlines()) == 1
    )


def test_ja_bei_mehreren_offenen_fragen_eine_zeile(
    tmp_path: Path, mit_sperre: Path
) -> None:
    _frage(tmp_path, mit_sperre)
    _frage(tmp_path, mit_sperre)
    lauf = _cli(tmp_path, mit_sperre, "ja", "7")
    assert lauf.returncode == 1
    assert (
        "2 offene Fragen" in lauf.stderr and len(lauf.stderr.strip().splitlines()) == 1
    )


# --- Hook -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "befehl",
    [
        "ja 5",
        "~/.claude/bin/ja 5",
        "~/.claude/bin/ja.cmd 5",
        "python to_spawn.py ja 5",
        "python ~/.claude/skills/to-spawn/to_spawn.py nutzer-antwort 5 --id x --wahl ja --grund y",
        'bash -c "ja 5"',
        "eval 'ja 5'",
        "cd /tmp && ja 5",
        "echo hi; sudo -u bau ja 5",
    ],
)
def test_hook_lehnt_ja_formen_ab(befehl: str) -> None:
    lauf = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "hook-ja-sperre"],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": befehl}}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 2, befehl
    assert "Gesperrt" in lauf.stderr


@pytest.mark.parametrize(
    "befehl",
    [
        "ls -la",
        "gh issue comment 5 --body 'ja, passt'",
        "echo ja",
        "python to_spawn.py offene 5",
        "git commit -m 'ja 5'",
    ],
)
def test_hook_laesst_unverwandtes_durch(befehl: str) -> None:
    lauf = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "hook-ja-sperre"],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": befehl}}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 0, befehl


# --- Einrichtung ----------------------------------------------------------------------------------


def test_einrichtung_idempotent_erhaelt_fremdes_mit_sicherung(heim: Path) -> None:
    ordner = heim / ".claude"
    ordner.mkdir()
    fremd = {
        "model": "x",
        "hooks": {
            "PreToolUse": [
                {"matcher": "Edit", "hooks": [{"type": "command", "command": "fremd"}]}
            ]
        },
    }
    (ordner / "settings.json").write_text(json.dumps(fremd), encoding="utf-8")
    assert ja_einrichten.sperre_eintragen(ordner, sys.executable, zeit="t1") is True
    assert ja_einrichten.sperre_eintragen(ordner, sys.executable, zeit="t2") is False
    daten = json.loads((ordner / "settings.json").read_text(encoding="utf-8"))
    assert (
        daten["model"] == "x"
        and daten["hooks"]["PreToolUse"][0] == fremd["hooks"]["PreToolUse"][0]
    )
    assert len(daten["hooks"]["PreToolUse"]) == 2
    assert (
        json.loads((ordner / "settings.json.bak-t1").read_text(encoding="utf-8"))
        == fremd
    )
    assert not (ordner / "settings.json.bak-t2").exists()


def test_einrichtung_ueberschreibt_kaputtes_json_nie(heim: Path) -> None:
    ordner = heim / ".claude"
    ordner.mkdir()
    (ordner / "settings.json").write_text("{kaputt", encoding="utf-8")
    with pytest.raises(ValueError):
        ja_einrichten.sperre_eintragen(ordner, sys.executable)
    assert (ordner / "settings.json").read_text(encoding="utf-8") == "{kaputt"


def test_befehl_ja_wird_angelegt_und_laeuft_ohne_terminal(
    tmp_path: Path, mit_sperre: Path
) -> None:
    sh, cmd = ja_einrichten.befehl_anlegen(mit_sperre / ".claude", sys.executable)
    assert os.access(sh, os.X_OK) and cmd.read_bytes().endswith(b"%*\r\n")
    _frage(tmp_path, mit_sperre)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env.update({"TO_SPAWN_REPO": str(tmp_path), "HOME": str(mit_sperre)})
    lauf = subprocess.run(
        [str(sh), "7"],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stderr


def test_sperre_eingetragen_liest_nur_dieses_home(
    heim: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("USERPROFILE", str(heim))
    assert briefkasten.sperre_eingetragen() is False
    ja_einrichten.sperre_eintragen(heim / ".claude", sys.executable)
    assert briefkasten.sperre_eingetragen() is True


# --- Aufforderungstext ------------------------------------------------------------------------------


def test_aufforderung_kurz_und_ueberall_gleich() -> None:
    befehl = briefkasten.ja_aufforderung(5)
    assert befehl == "`! ~/.claude/bin/ja 5`" and len(befehl) <= 40
    for text in (
        capo.annahme_grenze(5),
        waechter_takt.bremse_text("5:abc", 3),
        aufseher_text("python X/to_spawn.py").replace("<N>", "5"),
    ):
        assert befehl in text
