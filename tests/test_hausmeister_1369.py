"""Hausmeister #1369: räumt alles Fertige auf, Platten-Grenzen im Start-Check.

Weg-Tests mit echten Git-Ordnern aus ``test_hausmeister_1368`` (Fixture ``welt``):
gestellt ist nur ``gh`` (PATH-Attrappe) und der Plattenstand über die Test-Naht
``TO_SPAWN_HAUSMEISTER_PLATTE`` (Anteil frei; Datei mit einem Wert je Zeile = je
Messung der nächste, der letzte bleibt).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    SKILL,
    Welt,
    welt,
)

from to_spawn import aufpasser, hausmeister, hindernis, speicher


def _platte(welt: Welt, monkeypatch: pytest.MonkeyPatch, *anteile: float) -> Path:
    datei = welt.tmp / "platte.txt"
    datei.write_text("".join(f"{a}\n" for a in anteile), encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", str(datei))
    return datei


def _drei_ordner(welt: Welt) -> tuple[Path, Path, Path]:
    """wt-21 vor 2 h zu, wt-22 vor 5 h zu (beide < 24 h), wt-23 offen."""
    a, b, c = welt.worktree(21), welt.worktree(22), welt.worktree(23)
    welt.ticket(21, "CLOSED", 2)
    welt.ticket(22, "CLOSED", 5)
    welt.ticket(23, "OPEN")
    return a, b, c


def _geloescht(welt: Welt) -> list[str]:
    return [Path(z["ordner"]).name for z in welt.zeilen() if z["art"] != "bleibt"]


def test_platznot_loescht_fertige_sofort_aelteste_zuerst(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b, c = _drei_ordner(welt)
    _platte(welt, monkeypatch, 0.10)
    erg = hausmeister.aufraeumen_alles(loeschen=True, jetzt=welt.jetzt)
    assert not a.exists() and not b.exists()
    assert c.is_dir()
    assert _geloescht(welt) == ["wt-22", "wt-21"]
    assert {e.ordner.name: e.art for e in erg}["wt-23"] == "bleibt"


def test_ohne_platznot_bleiben_frisch_geschlossene(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b, c = _drei_ordner(welt)
    _platte(welt, monkeypatch, 0.50)
    erg = hausmeister.aufraeumen_alles(loeschen=True, jetzt=welt.jetzt)
    assert a.is_dir() and b.is_dir() and c.is_dir()
    assert [e.art for e in erg] == ["bleibt"] * 3
    assert _geloescht(welt) == []


def test_wieder_ueber_grenze_nach_erster_loeschung(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b, _ = _drei_ordner(welt)
    _platte(welt, monkeypatch, 0.10, 0.20)
    hausmeister.aufraeumen_alles(loeschen=True, jetzt=welt.jetzt)
    assert not b.exists()
    assert a.is_dir()
    assert _geloescht(welt) == ["wt-22"]


def test_platznot_trocken_loescht_nichts(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b, _ = _drei_ordner(welt)
    _platte(welt, monkeypatch, 0.10)
    erg = hausmeister.aufraeumen_alles(jetzt=welt.jetzt)
    assert a.is_dir() and b.is_dir()
    assert [e.ordner.name for e in erg if e.art == "gelöscht"] == ["wt-22", "wt-21"]


def _speicher_cli(welt: Welt) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "speicher"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "TO_SPAWN_REPO": str(welt.haupt)},
    )


def test_platte_voll_speicher_cli_eigener_exit_und_logs(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    alt = welt.worktree(41)
    welt.ticket(41, "CLOSED", 3)
    _platte(welt, monkeypatch, 0.03)
    hindernis_log = welt.tmp / "hindernisse.jsonl"
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(hindernis_log))
    lauf = _speicher_cli(welt)
    assert lauf.returncode == speicher.EXIT_PLATTE == 7, lauf.stdout + lauf.stderr
    assert "Platte" in lauf.stdout
    assert not alt.exists()  # vorher aufgeräumt (Platznot)
    cron_out = aufpasser.zustand_standard() / "cron.out"
    assert str(cron_out).startswith(str(welt.tmp))
    assert "Platte" in cron_out.read_text(encoding="utf-8")
    zeilen = hindernis.lesen()
    assert [z["art"] for z in zeilen] == ["platte_voll"]
    assert "Platte" in zeilen[0]["text"]


def test_platte_frei_speicher_cli_exit_null(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    _platte(welt, monkeypatch, 0.50)
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(welt.tmp / "h.jsonl"))
    lauf = _speicher_cli(welt)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert not (welt.tmp / "h.jsonl").exists()


def test_aufpasser_lauf_ruft_aufraeumen_alles(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    alt = welt.worktree(31)
    welt.ticket(31, "CLOSED", 48)
    _platte(welt, monkeypatch, 0.50)
    zustand = welt.tmp / "zustand"
    e = aufpasser.Einstellungen(
        zustand=zustand,
        tmux_socket=f"hausmeister1369-{os.getpid()}",
        deploy_muster=f"hausmeister1369-niemals-{os.getpid()}",
    )
    assert aufpasser.lauf_mit_einstellungen(e) == 0
    assert not alt.exists()
    text = (zustand / "aufpasser.log").read_text(encoding="utf-8")
    assert re.search(r"Hausmeister: 1 gelöscht, 0 bleibt", text), text


def test_aufpasser_trocken_loescht_nichts(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    alt = welt.worktree(32)
    welt.ticket(32, "CLOSED", 48)
    _platte(welt, monkeypatch, 0.50)
    e = aufpasser.Einstellungen(
        zustand=welt.tmp / "zustand",
        tmux_socket=f"hausmeister1369-{os.getpid()}",
        deploy_muster=f"hausmeister1369-niemals-{os.getpid()}",
        trocken=True,
    )
    assert aufpasser.lauf_mit_einstellungen(e) == 0
    assert alt.is_dir()
    assert [z["trocken"] for z in welt.zeilen()] == [True]


def test_bau_start_wartet_bei_voller_platte(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    datei = _platte(welt, monkeypatch, 0.03)
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(welt.tmp / "h.jsonl"))
    pausen: list[float] = []

    def schlafen(s: float) -> None:
        pausen.append(s)
        datei.write_text("0.50\n", encoding="utf-8")  # jemand hat Platz geschaffen

    zyklen = speicher.auf_platte_warten(wer="bau 999", schlafen=schlafen)
    assert zyklen == 1
    assert pausen == [speicher.PLATTE_TAKT_S]
    assert (
        speicher.PLATTE_TAKT_S >= 600
    )  # teurer Hausmeister-Lauf höchstens ~alle 15 min
    # bau.py: Platten-Warten vor Speicher-Warten, beides vor dem Claude-Start.
    quelle = (SKILL / "skripte" / "bau.py").read_text(encoding="utf-8")
    platte = quelle.index("speicher.auf_platte_warten(")
    ram = quelle.index('auf_speicher_warten(konfig, wer=f"bau {ticket}")')
    assert platte < ram


def test_bau_start_wartet_ueber_pruef_tuer() -> None:
    antworten = iter([(False, "Platte voll"), (False, "Platte voll"), (True, "frei")])
    pausen: list[float] = []
    zyklen = speicher.auf_platte_warten(
        wer="bau 1", pruefen=lambda: next(antworten), schlafen=pausen.append
    )
    assert zyklen == 2 and len(pausen) == 2


def _skill_dateien() -> list[Path]:
    dateien = [SKILL / "to_spawn" / "aufpasser.py", SKILL / "to_spawn" / "speicher.py"]
    dateien += [p for p in (SKILL / "skripte").rglob("*") if p.is_file()]
    dateien += [
        p
        for p in SKILL.rglob("*.md")
        if "tests" not in p.parts and "docs" not in p.parts
    ]
    return [p for p in dateien if "__pycache__" not in p.parts]


def test_schwellen_nur_im_hausmeister() -> None:
    muster = re.compile(
        r"0\.15\b|0\.05\b|\b15 ?%|(?<![\d,.])5 ?%|Startsperre|PLATZNOT_ANTEIL|STARTSPERRE_ANTEIL"
    )
    treffer = []
    for datei in _skill_dateien():
        try:
            text = datei.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for nr, zeile in enumerate(text.splitlines(), 1):
            if muster.search(zeile):
                treffer.append(f"{datei.relative_to(SKILL)}:{nr}: {zeile.strip()}")
    assert treffer == []
    assert hausmeister.PLATZNOT_ANTEIL == 0.15
    assert hausmeister.STARTSPERRE_ANTEIL == 0.05


def test_keine_mail_im_neuen_code() -> None:
    verboten = re.compile(
        r"to_spawn_mail|telegram|smtplib|sendmail|(?<!hindernis\.)\bmelden\(|benachrichtig",
        re.IGNORECASE,
    )
    quellen = {
        "hausmeister.py": (SKILL / "to_spawn" / "hausmeister.py").read_text(
            encoding="utf-8"
        ),
        "speicher.auf_platte_warten": inspect.getsource(speicher.auf_platte_warten),
        "speicher.cli": inspect.getsource(speicher.cli),
        "aufpasser._hausmeister_lauf": inspect.getsource(
            aufpasser.Aufpasser._hausmeister_lauf
        ),
    }
    for name, text in quellen.items():
        assert not verboten.search(text), name
    # hindernis.py definiert ``melden`` selbst — dort zählt nur, dass nichts verschickt wird.
    text = (SKILL / "to_spawn" / "hindernis.py").read_text(encoding="utf-8")
    assert not re.search(
        r"to_spawn_mail|telegram|smtplib|sendmail|urllib|requests", text, re.IGNORECASE
    )


def test_hindernis_wirft_nie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "TO_SPAWN_HINDERNIS_LOG", str(tmp_path / "ordner-ist-datei" / "x.jsonl")
    )
    (tmp_path / "ordner-ist-datei").write_text("", encoding="utf-8")
    assert hindernis.melden("test", "geht nicht") is False
    assert hindernis.lesen() == []
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(tmp_path / "h.jsonl"))
    assert hindernis.melden("test", "geht", ticket=5) is True
    zeile = hindernis.lesen()[0]
    assert (zeile["art"], zeile["text"], zeile["ticket"]) == ("test", "geht", 5)
    assert {"zeit", "wer"} <= set(zeile)
    json.dumps(zeile)
