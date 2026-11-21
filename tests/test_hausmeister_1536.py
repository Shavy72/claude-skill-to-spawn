"""Hausmeister #1536: Behalten ist die Ausnahme mit echtem Grund.

Neu: Test-Datenbanken, Sperren, Logs, graphify-out, Daten-Ordner und ``.pyc.<zahl>`` sind
Erzeugnisse; Ordner ohne Ticket-Nummer oder ohne Git gehen nach
:data:`hausmeister.UNBERUEHRT_TAGE` Tagen ohne Änderung; ungleiche Geheimnis-Dateien
gehen ungesichert mit weg, wenn das Ticket zu und der Ordner so lange unberührt ist; je
Takt eine Kennzahl-Zeile im Log. Dazu je Schutz-Regel ein Test.
Weg-Tests mit echtem Wegwerf-Git und lokalem bare-„origin“ (Fixture ``welt`` aus #1368);
gestellt ist nur ``gh`` (Ticket-Zustand).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    Welt,
    _commit,
    git,
    welt,
)

from to_spawn import hausmeister

TAG = 24 * 3600.0
GEHEIM = "TOKEN=geheim-1536-wert"


@pytest.fixture(autouse=True)
def eigenes_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Eigener tmux-Server je Test: die echten Sitzungen des Bau-Servers zählen nicht."""
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))


def _altern(welt: Welt) -> None:
    """Die Uhr springt über die Unberührt-Grenze (Dateien bleiben, wie sie sind)."""
    welt.jetzt += (hausmeister.UNBERUEHRT_TAGE + 1) * TAG


def _ordner(welt: Welt, rel: str, dateien: tuple[str, ...] = ("notiz.txt",)) -> Path:
    ordner = welt.heim / rel
    for name in dateien:
        (ordner / name).parent.mkdir(parents=True, exist_ok=True)
        (ordner / name).write_text("inhalt\n", encoding="utf-8")
    return ordner


def _alle_inhalte(welt: Welt) -> str:
    return welt.im_bare("log", "--all", "-p", "--format=%H")


# --- Soll 1: Erzeugnis-Klasse ----------------------------------------------------


def test_soll1_test_db_lock_logs_daten_pyc_zahl_sind_erzeugnisse(welt: Welt) -> None:
    wt = welt.worktree(1601)
    _commit(wt, "scripts/tempo_zugang.py", "print('zugang')\n")
    _commit(
        wt,
        ".gitignore",
        "*.db\n*.db-wal\n*.db-shm\n*.lock\nlogs/\ngraphify-out/\n"
        "data/content_studio/\ndata/reel_street/\n__pycache__/\n",
        push=True,
    )
    _ordner(
        welt,
        f"wt/{wt.name}",
        (
            "accounts.db",
            "accounts.db-wal",
            "accounts.db-shm",
            "web/test.db",
            "bau.lock",
            "logs/app.txt",
            "graphify-out/graph.json",
            "data/content_studio/bild.png",
            "data/reel_street/clip.mp4",
            "scripts/__pycache__/tempo_zugang.cpython-311.pyc.4711",
        ),
    )
    welt.ticket(1601, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gelöscht", r.grund
    assert not wt.exists()


# --- Soll 2: ohne Ticket-Nummer, ohne Git ------------------------------------------


def test_soll2_ohne_git_erst_nach_unberuehrt_tagen_weg(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/reste-notizen")
    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt" and not r.fehler, r.grund
    assert ordner.is_dir()
    _altern(welt)
    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "gelöscht", r.grund
    assert not ordner.exists()


def test_soll2_verwaister_worktree_alt_und_ticket_zu_geht_weg(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/wt-1602", ("arbeit.txt",))
    (ordner / ".git").write_text(f"gitdir: {welt.tmp / 'gibt-es-nicht' / 'worktrees' / 'wt-1602'}\n", encoding="utf-8")
    _altern(welt)
    welt.ticket(1602, "CLOSED", 48)
    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "gelöscht", r.grund
    assert not ordner.exists()


def test_soll2_git_ohne_nummer_alt_wird_gesichert_und_geloescht(welt: Welt) -> None:
    spiel = welt.heim / "wt" / "spielwiese"
    git(welt.haupt, "worktree", "add", "-q", "-b", "spielwiese", str(spiel))
    (spiel / "neu.txt").write_text("ungesichert\n", encoding="utf-8")
    assert welt.aufraeumen(spiel, loeschen=True).art == "bleibt"  # jung
    _altern(welt)
    r = welt.aufraeumen(spiel, loeschen=True)
    assert r.art == "gesichert und gelöscht", r.grund
    assert not spiel.exists()
    sicherung = r.sicherung
    assert sicherung
    assert welt.im_bare("show", f"{sicherung}:neu.txt") == "ungesichert\n"


# --- Soll 3: Geheimnis-Dateien ----------------------------------------------------


def test_soll3_geheimnis_ungleich_ticket_zu_alt_geht_ohne_upload(welt: Welt) -> None:
    wt = welt.worktree(1604, schmutzig=True)
    (wt / ".env").write_text(GEHEIM + "\n", encoding="utf-8")  # Hauptrepo hat keine
    welt.ticket(1604, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david, r.grund  # jung: Regel wie bisher
    _altern(welt)
    welt.ticket(1604, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gesichert und gelöscht", r.grund
    assert not wt.exists()
    assert "ungesichert" in welt.im_bare("show", f"{r.sicherung}:geaendert.txt")
    assert GEHEIM not in _alle_inhalte(welt)
    assert GEHEIM not in welt.log.read_text(encoding="utf-8")


# --- Soll 4: Kennzahl je Takt -----------------------------------------------------


def test_soll4_kennzahl_zeile_je_takt(welt: Welt) -> None:
    welt.worktree(1605)
    welt.ticket(1605, "CLOSED", 48)
    welt.worktree(1606)
    welt.ticket(1606, "OPEN")
    _ordner(welt, "wt/reste")
    hausmeister.aufraeumen_alles(jetzt=welt.jetzt)
    zeilen = welt.zeilen()
    kennzahl = [z for z in zeilen if z.get("art") == "kennzahl"]
    assert len(kennzahl) == 1
    k = kennzahl[0]
    assert (k["gesamt"], k["geloescht"], k["bleiben"]) == (3, 1, 2)
    assert k["grund"] == "1 von 3 gelöscht, 2 bleiben" and k["trocken"] is True
    assert sum(k["bleib_gruende"].values()) == 2
    assert k["bleib_gruende"].get("Ticket #N offen") == 1
    felder = {"zeit", "ordner", "art", "grund", "ticket", "sicherung", "trocken", "fuer_david", "fehler"}
    assert all(felder <= z.keys() for z in zeilen if z.get("ordner"))


# --- Sicherheit: nie gelöscht -----------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "duoplus-leit",
        "duoplus-management",
        "wt/deploy-schlange",
        "wt/claude-skill-to-spawn",
        "wt/obsidian",
    ],
)
def test_sicher_geschuetzte_ordner_bleiben(welt: Welt, rel: str) -> None:
    ordner = _ordner(welt, rel)
    _altern(welt)
    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt", r.grund
    assert (ordner / "notiz.txt").is_file()


def test_sicher_offenes_ticket_bleibt_auch_alt(welt: Welt) -> None:
    wt = welt.worktree(1608)
    ohne_git = _ordner(welt, "wt/wt-1608-reste")
    _altern(welt)
    welt.ticket(1608, "OPEN")
    for ordner in (wt, ohne_git):
        r = welt.aufraeumen(ordner, loeschen=True)
        assert r.art == "bleibt" and "offen" in r.grund, r.grund
        assert ordner.is_dir()


def test_sicher_prozess_im_alten_ordner_ohne_git_bleibt(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/laeuft-noch")
    _altern(welt)
    proc = subprocess.Popen(["sleep", "60"], cwd=ordner)
    try:
        r = welt.aufraeumen(ordner, loeschen=True)
        assert r.art == "bleibt" and "Prozess" in r.grund, r.grund
        assert ordner.is_dir()
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux fehlt")
def test_sicher_spec_klon_mit_lebender_tmux_sitzung_bleibt(welt: Welt) -> None:
    ordner = _ordner(welt, "duoplus-1607")
    _altern(welt)
    welt.ticket(1607, "CLOSED", 48)
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", "spec-1607", "-c", str(welt.tmp), "sleep 60"],
        check=True,
    )
    try:
        r = welt.aufraeumen(ordner, loeschen=True)
        assert r.art == "bleibt" and "spec-1607" in r.grund, r.grund
        assert ordner.is_dir()
    finally:
        subprocess.run(["tmux", "kill-server"], check=False)
    assert welt.aufraeumen(ordner, loeschen=True).art == "gelöscht"


def test_sicher_unterordner_eines_repos_bleibt(welt: Welt) -> None:
    subprocess.run(["git", "init", "-q", str(welt.heim / "wt")], check=True)
    ordner = _ordner(welt, "wt/unter")
    _altern(welt)
    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt" and "kein eigener Git-Ordner" in r.grund, r.grund
    assert ordner.is_dir()


def test_sicher_ausserhalb_der_wurzeln_bleibt(welt: Welt) -> None:
    ordner = _ordner(welt, "anderswo")
    _altern(welt)
    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt" and "Aufräum-Bereich" in r.grund, r.grund
    assert ordner.is_dir()


def test_sicher_sicherung_scheitert_bleibt_mit_fehler(welt: Welt) -> None:
    klon = welt.klon("wt/spielklon")
    _commit(klon, "nur-lokal.txt", "nicht gepusht\n")
    sperre = welt.bare / "hooks" / "pre-receive"
    sperre.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    sperre.chmod(0o755)
    _altern(welt)
    r = welt.aufraeumen(klon, loeschen=True)
    assert r.art == "bleibt" and r.fehler, r.grund
    assert (klon / "nur-lokal.txt").is_file()


def test_sicher_pruefung_scheitert_bleibt_mit_fehler(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/unlesbar", ("zu/geheim.txt",))
    _altern(welt)
    (ordner / "zu").chmod(0)
    try:
        r = welt.aufraeumen(ordner, loeschen=True)
        assert r.art == "bleibt" and r.fehler, r.grund
        assert ordner.is_dir()
    finally:
        (ordner / "zu").chmod(0o755)
