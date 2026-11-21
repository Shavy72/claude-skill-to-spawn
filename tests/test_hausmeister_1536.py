"""Hausmeister #1536: Behalten ist die Ausnahme mit echtem Grund.

Neu: Test-Datenbanken (:func:`hausmeister.ist_test_db`), Sperren, Logs, graphify-out,
Vorschaubilder der Daten-Ordner und ``.pyc.<zahl>`` sind Erzeugnisse; jede andere Datenbank, Ordner ohne
Ticket-Nummer oder ohne Git und ungleiche Geheimnis-Dateien halten den Ordner — auch
alt (Fix-Runde 1); je Takt eine Kennzahl-Zeile im Log. Dazu je Schutz-Regel ein Test.
Weg-Tests mit echtem Wegwerf-Git und lokalem bare-„origin“ (Fixture ``welt`` aus #1368);
gestellt ist nur ``gh`` (Ticket-Zustand).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import json
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
#: Weit genug, dass eine frühere Alters-Regel („verlassen“) gegriffen hätte.
ALT_TAGE = 30
GEHEIM = "TOKEN=geheim-1536-wert"


@pytest.fixture(autouse=True)
def eigenes_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Eigener tmux-Server je Test: die echten Sitzungen des Bau-Servers zählen nicht."""
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))


def _altern(welt: Welt) -> None:
    """Die Uhr springt :data:`ALT_TAGE` Tage weiter (Dateien bleiben, wie sie sind)."""
    welt.jetzt += ALT_TAGE * TAG


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
            "data/content_studio/uploads/_characters/6/.thumb_ab12_480.webp",
            "data/reel_street/preview/.thumb_cd34_480.webp",
            "scripts/__pycache__/tempo_zugang.cpython-311.pyc.4711",
        ),
    )
    welt.ticket(1601, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gelöscht", r.grund
    assert not wt.exists()


# --- Soll 2: ohne Ticket-Nummer, ohne Git bleiben (Out of Scope #1536) -------------


def test_soll2_ohne_git_bleibt_auch_alt(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/reste-notizen")
    _altern(welt)
    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and not r.fehler, r.grund
    assert (ordner / "notiz.txt").is_file()


def test_soll2_verwaister_worktree_alt_und_ticket_zu_bleibt(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/wt-1602", ("arbeit.txt",))
    (ordner / ".git").write_text(f"gitdir: {welt.tmp / 'gibt-es-nicht' / 'worktrees' / 'wt-1602'}\n", encoding="utf-8")
    _altern(welt)
    welt.ticket(1602, "CLOSED", 48)
    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and "kein eigener Git-Ordner" in r.grund, r.grund
    assert (ordner / "arbeit.txt").is_file()


def test_soll2_git_ohne_nummer_bleibt_auch_alt(welt: Welt) -> None:
    spiel = welt.heim / "wt" / "spielwiese"
    git(welt.haupt, "worktree", "add", "-q", "-b", "spielwiese", str(spiel))
    (spiel / "neu.txt").write_text("ungesichert\n", encoding="utf-8")
    _altern(welt)
    r = welt.aufraeumen(spiel, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and "Ticket-Nummer" in r.grund, r.grund
    assert (spiel / "neu.txt").read_text(encoding="utf-8") == "ungesichert\n"


# --- Soll 3: Geheimnis-Dateien ----------------------------------------------------


def test_soll3_geheimnis_ungleich_bleibt_auch_alt(welt: Welt) -> None:
    wt = welt.worktree(1604, schmutzig=True)
    (wt / ".env").write_text(GEHEIM + "\n", encoding="utf-8")  # Hauptrepo hat keine
    _altern(welt)
    welt.ticket(1604, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and ".env" in r.grund, r.grund
    assert (wt / ".env").read_text(encoding="utf-8") == GEHEIM + "\n"
    assert GEHEIM not in _alle_inhalte(welt)


# --- Soll 4: Kennzahl je Takt -----------------------------------------------------


def test_soll4_kennzahl_zeile_je_takt(welt: Welt) -> None:
    welt.worktree(1605)
    welt.ticket(1605, "CLOSED", 48)
    welt.worktree(1606)
    welt.ticket(1606, "OPEN")
    _ordner(welt, "wt/reste")
    hausmeister.aufraeumen_alles(jetzt=welt.jetzt)
    zeilen = welt.zeilen()
    assert all(z.get("ordner") for z in zeilen)  # Ordner-Log bleibt rein (Leser zählen Zeilen)
    datei = welt.log.with_name(hausmeister.KENNZAHL_DATEI)
    kennzahl = [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines()]
    assert [z["art"] for z in kennzahl] == ["kennzahl"]
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
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "offen" in r.grund, r.grund
    assert wt.is_dir()
    # Fix-Runde 1: ohne Git bleibt der Ordner schon vor der Ticket-Abfrage.
    r = welt.aufraeumen(ohne_git, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david, r.grund
    assert ohne_git.is_dir()


def test_sicher_prozess_im_alten_ordner_ohne_git_bleibt(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/laeuft-noch")
    _altern(welt)
    proc = subprocess.Popen(["sleep", "60"], cwd=ordner)
    try:
        r = welt.aufraeumen(ordner, loeschen=True)
        assert r.art == "bleibt", r.grund  # Fix-Runde 1: ohne Git bleibt er schon vorher
        assert ordner.is_dir()
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux fehlt")
def test_sicher_spec_klon_mit_lebender_tmux_sitzung_bleibt(welt: Welt) -> None:
    ordner = welt.klon("duoplus-1607")  # Fix-Runde 1: echter Klon, ohne Git bliebe er immer
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
    klon = welt.klon("wt/wt-1612")  # Fix-Runde 1: ohne Ticket-Nummer bliebe er vorher
    welt.ticket(1612, "CLOSED", 48)
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
        # Fix-Runde 1: ohne Git bleibt der Ordner schon vor jeder Messung (kein Fehler mehr).
        assert r.art == "bleibt", r.grund
        assert ordner.is_dir()
    finally:
        (ordner / "zu").chmod(0o755)


def test_sicherung_laedt_keine_datenbank_hoch(welt: Welt) -> None:
    """Nicht ignorierte Test-DB (evtl. Abzug echter Daten) geht nie nach ``sicherung/…``."""
    wt = welt.worktree(1609)
    (wt / "accounts.db").write_text(GEHEIM + "\n", encoding="utf-8")
    (wt / "notiz.txt").write_text("ungesichert\n", encoding="utf-8")
    welt.ticket(1609, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gesichert und gelöscht", r.grund
    assert welt.im_bare("show", f"{r.sicherung}:notiz.txt") == "ungesichert\n"
    assert GEHEIM not in _alle_inhalte(welt)
    assert not wt.exists()


def test_nur_datenbank_braucht_keine_sicherung(welt: Welt) -> None:
    wt = welt.worktree(1610)
    (wt / "accounts.db").write_text("x", encoding="utf-8")
    welt.ticket(1610, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gelöscht" and not r.sicherung, r.grund
    assert not wt.exists()


def test_sicher_datenbank_im_geschuetzten_ordner_bleibt(welt: Welt) -> None:
    ordner = _ordner(welt, "wt/deploy-schlange", ("daten/accounts.db",))
    _altern(welt)
    r = welt.aufraeumen(ordner, loeschen=True)
    assert r.art == "bleibt" and "geschützt" in r.grund, r.grund
    assert (ordner / "daten" / "accounts.db").is_file()


def test_sicher_datenbank_im_ordner_offenen_tickets_bleibt(welt: Welt) -> None:
    wt = welt.worktree(1611)
    _commit(wt, ".gitignore", "*.db\n", push=True)
    (wt / "accounts.db").write_text("x", encoding="utf-8")
    _altern(welt)
    welt.ticket(1611, "OPEN")
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and "offen" in r.grund, r.grund
    assert (wt / "accounts.db").is_file()


# --- Fix-Runde 1: nur bekannte Test-Ware ist Wegwerf-Ware (H1) ----------------------


def _db_ordner(welt: Welt, nr: int, dateien: dict[str, int]) -> Path:
    """Worktree eines seit 48 h geschlossenen Tickets mit ignorierten DB-Dateien
    (Name → Größe in Byte; große Dateien sparse, kosten keinen Platz)."""
    wt = welt.worktree(nr)
    _commit(wt, ".gitignore", "*.db\n*.db-wal\n*.db-shm\n", push=True)
    for name, groesse in dateien.items():
        (wt / name).parent.mkdir(parents=True, exist_ok=True)
        with open(wt / name, "wb") as f:
            f.truncate(groesse)
    _altern(welt)
    welt.ticket(nr, "CLOSED", 48)
    return wt


def test_h1_wal_und_shm_einer_fremden_db_halten_den_ordner(welt: Welt) -> None:
    wt = _db_ordner(welt, 1620, {"echt.db-wal": 10, "echt.db-shm": 10})
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and "echt.db-" in r.grund, r.grund
    assert (wt / "echt.db-wal").is_file() and (wt / "echt.db-shm").is_file()


def test_h1_test_db_name_im_unterordner_haelt_den_ordner(welt: Welt) -> None:
    wt = _db_ordner(welt, 1621, {"daten/accounts.db": 10})
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and "daten/accounts.db" in r.grund, r.grund
    assert (wt / "daten" / "accounts.db").is_file()


def test_h1_grosse_unbekannte_db_im_geschlossenen_ticket_bleibt(welt: Welt) -> None:
    wt = _db_ordner(welt, 1622, {"daten/echt.db": hausmeister.TEST_DB_GRENZE_BYTES + 1})
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and "daten/echt.db" in r.grund, r.grund
    assert (wt / "daten" / "echt.db").is_file()


def test_h1_zu_grosse_accounts_db_bleibt(welt: Welt) -> None:
    gross = hausmeister.TEST_DB_GRENZE_BYTES + 1
    wt = _db_ordner(welt, 1623, {"accounts.db": 10, "accounts.db-wal": gross})
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and "accounts.db-wal" in r.grund, r.grund
    assert (wt / "accounts.db-wal").is_file()


def test_h1_nicht_ignorierte_fremde_db_bleibt_statt_ungesichert_weg(welt: Welt) -> None:
    wt = welt.worktree(1624)
    (wt / "kopie.db").write_text(GEHEIM + "\n", encoding="utf-8")
    welt.ticket(1624, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and "kopie.db" in r.grund, r.grund
    assert (wt / "kopie.db").read_text(encoding="utf-8") == GEHEIM + "\n"
    assert GEHEIM not in _alle_inhalte(welt)


def test_h1_kleine_bekannte_test_dbs_gehen_weg(welt: Welt) -> None:
    wt = _db_ordner(
        welt,
        1625,
        {"accounts.db": 10, "accounts.db-wal": 10, "data/funnel.db": 10, "data/rum.db": 10},
    )
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gelöscht", r.grund
    assert not wt.exists()


# --- Fix-Runde 2: Geheimnis mit anderem Inhalt (B1), Daten-Ordner nur Vorschaubilder ---


def test_b1_env_im_hauptrepo_mit_anderem_inhalt_bleibt(welt: Welt) -> None:
    wt = welt.worktree(1631)
    (wt / ".env").write_text(GEHEIM + "\n", encoding="utf-8")
    (welt.haupt / ".env").write_text("TOKEN=anderer-wert\n", encoding="utf-8")
    welt.ticket(1631, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david and ".env" in r.grund, r.grund
    assert (wt / ".env").read_text(encoding="utf-8") == GEHEIM + "\n"
    assert GEHEIM not in _alle_inhalte(welt)


def _daten_ordner(welt: Welt, nr: int, dateien: tuple[str, ...]) -> Path:
    wt = welt.worktree(nr)
    _commit(wt, ".gitignore", "data/content_studio/\ndata/reel_street/\n", push=True)
    _ordner(welt, f"wt/{wt.name}", dateien)
    welt.ticket(nr, "CLOSED", 48)
    return wt


@pytest.mark.parametrize(
    "datei",
    [
        "data/content_studio/uploads/_characters/6/ab12.png",
        "data/content_studio/results/4/0.mp4",
        "data/reel_street/preview/job4.mp4",
    ],
)
def test_fix2_echte_datei_im_daten_ordner_haelt_den_ordner(welt: Welt, datei: str) -> None:
    wt = _daten_ordner(
        welt, 1632, ("data/content_studio/uploads/_characters/6/.thumb_ab12_480.webp", datei)
    )
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and datei in r.grund, r.grund
    assert (wt / datei).is_file()


def test_fix2_nur_vorschaubilder_halten_den_ordner_nicht(welt: Welt) -> None:
    wt = _daten_ordner(
        welt,
        1633,
        (
            "data/content_studio/.thumb_wurzel.webp",
            "data/content_studio/uploads/_characters/6/.thumb_ab12_480.webp",
            "data/reel_street/preview/.thumb_cd34_480.webp",
        ),
    )
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "gelöscht", r.grund
    assert not wt.exists()
