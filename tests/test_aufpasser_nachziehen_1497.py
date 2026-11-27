"""Aufpasser zieht Klone laufender Sessions nach (duoplus-management#1497).

Weg-Test mit echten Git-Repos (bare origin + Klon), ohne Attrappen.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import attrappe

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repo: Path, name: str, inhalt: str) -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(inhalt, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", name)


def _aufbau(tmp: Path, nachziehen: bool = True) -> tuple[Path, Path]:
    """origin mit Konfig, Klon, danach ein neuer Commit nur auf origin."""
    origin = tmp / "origin.git"
    _git(tmp, "init", "-q", "--bare", "-b", "master", str(origin))
    quelle = tmp / "quelle"
    _git(tmp, "clone", "-q", str(origin), str(quelle))
    konfig: dict = {"hauptzweig": "master"}
    if nachziehen:
        konfig["nachziehen"] = {
            "danach": [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('danach.txt').write_text('lief\\n', encoding='utf-8')",
            ]
        }
    _commit(quelle, ".to-spawn/config.json", json.dumps(konfig))
    _git(quelle, "push", "-q", "origin", "master")
    klon = tmp / "klon"
    _git(tmp, "clone", "-q", str(origin), str(klon))
    _commit(quelle, "neu.txt", "neu")
    _git(quelle, "push", "-q", "origin", "master")
    return quelle, klon


def test_klon_hinter_origin_wird_vorgespult_und_danach_laeuft(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    (klon / "eigene_notiz.txt").write_text("bleibt", encoding="utf-8")  # unversioniert
    ergebnis = aufpasser.klon_nachziehen(klon)
    assert ergebnis == "nachgezogen"
    assert _git(klon, "rev-parse", "HEAD") == _git(klon, "rev-parse", "origin/master")
    assert (klon / "neu.txt").is_file()
    assert (klon / "danach.txt").read_text(encoding="utf-8").strip() == "lief"
    assert (klon / "eigene_notiz.txt").read_text(encoding="utf-8") == "bleibt"


def test_eigener_commit_bleibt_unberuehrt(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    _commit(klon, "lokal.txt", "lokal")
    vorher = _git(klon, "rev-parse", "HEAD")
    assert aufpasser.klon_nachziehen(klon).startswith("übersprungen")
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_anderer_zweig_bleibt_unberuehrt(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    _git(klon, "checkout", "-q", "-b", "ticket/1-x")
    vorher = _git(klon, "rev-parse", "HEAD")
    assert aufpasser.klon_nachziehen(klon).startswith("übersprungen")
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_ohne_schluessel_in_origin_nichts_angefasst(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path, nachziehen=False)
    vorher = _git(klon, "rev-parse", "HEAD")
    assert aufpasser.klon_nachziehen(klon) == "aus"
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_kein_git_ordner(tmp_path: Path) -> None:
    assert aufpasser.klon_nachziehen(tmp_path) == "aus"


# --- Fix-Runde 1: nur saubere Klone auf dem Hauptzweig anfassen --------------


def _fetch_lief(klon: Path) -> bool:
    return (klon / ".git" / "FETCH_HEAD").exists()


def test_getrackte_aenderung_bleibt_kopf_und_datei_unveraendert(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    datei = klon / ".to-spawn" / "config.json"
    inhalt = datei.read_text(encoding="utf-8") + "\n"
    datei.write_text(inhalt, encoding="utf-8")  # versioniert, nicht committet
    vorher = _git(klon, "rev-parse", "HEAD")
    assert aufpasser.klon_nachziehen(klon).startswith("übersprungen")
    assert _git(klon, "rev-parse", "HEAD") == vorher
    assert datei.read_text(encoding="utf-8") == inhalt
    assert not (klon / "neu.txt").exists()
    assert not _fetch_lief(klon)


def test_index_lock_kein_git_schreibbefehl(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    sperre = klon / ".git" / "index.lock"
    sperre.write_text("", encoding="utf-8")
    vorher = _git(klon, "rev-parse", "HEAD")
    assert aufpasser.klon_nachziehen(klon).startswith("übersprungen")
    assert _git(klon, "rev-parse", "HEAD") == vorher
    assert sperre.is_file()
    assert not _fetch_lief(klon)


def test_offener_merge_wird_uebersprungen(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    _git(klon, "checkout", "-q", "-b", "seite")
    _commit(klon, "seite.txt", "seite")
    _git(klon, "checkout", "-q", "master")
    _git(klon, "merge", "-q", "--no-ff", "--no-commit", "seite")
    vorher = _git(klon, "rev-parse", "HEAD")
    assert aufpasser.klon_nachziehen(klon).startswith("übersprungen")
    assert _git(klon, "rev-parse", "HEAD") == vorher
    assert (klon / ".git" / "MERGE_HEAD").is_file()
    assert not _fetch_lief(klon)


def test_abgehaengter_kopf_wird_uebersprungen(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    _git(klon, "checkout", "-q", "--detach")
    vorher = _git(klon, "rev-parse", "HEAD")
    assert aufpasser.klon_nachziehen(klon).startswith("übersprungen")
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_uebersprungener_ordner_holt_nicht(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    _git(klon, "checkout", "-q", "-b", "ticket/1-x")
    assert aufpasser.klon_nachziehen(klon).startswith("übersprungen")
    assert not _fetch_lief(klon)


def test_sauberer_hauptzweig_vorgespult_danach_gerufen(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    assert aufpasser.klon_nachziehen(klon) == "nachgezogen"
    assert _git(klon, "rev-parse", "HEAD") == _git(klon, "rev-parse", "origin/master")
    assert (klon / "danach.txt").is_file()
    assert aufpasser.klon_nachziehen(klon) == "aktuell"


def _aufpasser_attrappe(pfade: list[Path]) -> SimpleNamespace:
    """Nur tmux (externer Dienst) ist ersetzt; Stand und git sind echt."""
    return SimpleNamespace(
        tmux=lambda *_a: "\n".join(str(p) for p in pfade),
        e=SimpleNamespace(trocken=False),
        stand=aufpasser.Stand(),
        jetzt=1000.0,
    )


def test_takt_je_repo_einmal_und_grund_nur_bei_wechsel(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _, klon = _aufbau(tmp_path)
    (klon / "unter").mkdir()
    datei = klon / ".to-spawn" / "config.json"
    datei.write_text(datei.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    selbst = _aufpasser_attrappe([klon, klon / "unter"])
    with caplog.at_level(logging.INFO, logger="aufpasser"):
        aufpasser.Aufpasser.klone_nachziehen(selbst)
        aufpasser.Aufpasser.klone_nachziehen(selbst)
    zeilen = [r.getMessage() for r in caplog.records if "übersprungen" in r.getMessage()]
    assert len(zeilen) == 1, zeilen
    # Grund weg (sauber) → nachgezogen, Merk-Eintrag gelöscht
    _git(klon, "checkout", "-q", "--", ".to-spawn/config.json")
    aufpasser.Aufpasser.klone_nachziehen(selbst)
    assert (klon / "neu.txt").is_file()
    assert not any(k.startswith("nachziehen:") for k in selbst.stand.meldungen)


# --- Fix-Runde 2 (Review B1, B4, B5) ----------------------------------------


def _aufpasser_mit_fenster(klon: Path, text: list[str]) -> SimpleNamespace:
    """tmux (externer Dienst) ersetzt: ein Pane in ``klon``, Bildschirm aus ``text``."""

    def tmux(*args: str) -> str:
        if args[0] == "list-panes":
            return f"{klon}\t%1" if "\t" in args[-1] else str(klon)
        return text[0]

    return SimpleNamespace(
        tmux=tmux,
        pane_text=lambda ziel: tmux("capture-pane", "-p", "-t", ziel),
        e=SimpleNamespace(trocken=False),
        stand=aufpasser.Stand(),
        jetzt=1000.0,
    )


def test_b1_arbeitendes_fenster_wird_nicht_nachgezogen(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    vorher = _git(klon, "rev-parse", "HEAD")
    text = ["● Bash(pytest tests -q)\n  ⎿ Running…\n✻ Thinking… (esc to interrupt)"]
    selbst = _aufpasser_mit_fenster(klon, text)
    aufpasser.Aufpasser.klone_nachziehen(selbst)
    assert _git(klon, "rev-parse", "HEAD") == vorher
    assert any("Fenster arbeitet" in k for k in selbst.stand.meldungen)
    # Fenster steht wieder still am Prompt → jetzt nachziehen
    text[0] = "╭──╮\n│ > │\n╰──╯\n  ? for shortcuts"
    aufpasser.Aufpasser.klone_nachziehen(selbst)
    assert (klon / "neu.txt").is_file()


def test_b4_fetch_laesst_fetch_head_der_session_stehen(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    fetch_head = Path(_git(klon, "rev-parse", "--absolute-git-dir")) / "FETCH_HEAD"
    fetch_head.write_text("eigener Stand der Session\n", encoding="utf-8")
    assert aufpasser.klon_nachziehen(klon) == "nachgezogen"
    assert fetch_head.read_text(encoding="utf-8") == "eigener Stand der Session\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-Prozessgruppe (killpg)")
def test_b5_zeitgrenze_beendet_ganze_prozessgruppe(tmp_path: Path) -> None:
    _, klon = _aufbau(tmp_path)
    pid_datei = tmp_path / "helfer.pid"
    helfer = attrappe(
        tmp_path,
        "haeng",
        f"""
        import os, pathlib, time
        pathlib.Path({str(pid_datei)!r}).write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(30)
        """,
    )
    with pytest.raises(RuntimeError, match="Zeitgrenze"):
        aufpasser._git(
            klon,
            "-c",
            "protocol.ext.allow=always",
            "fetch",
            f"ext::{helfer}",
            zeitgrenze=2,
        )
    pid = int(pid_datei.read_text().strip())
    lebt = subprocess.run(["kill", "-0", str(pid)], capture_output=True, check=False).returncode == 0
    if lebt:
        subprocess.run(["kill", "-9", str(pid)], check=False)
    assert not lebt, "Remote-Helfer überlebt die Zeitgrenze"
