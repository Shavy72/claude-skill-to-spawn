"""Rot-/Grün-Tests für die Mess-Vorstufe 3b-0 (#98).

Prüft:
- reproduzierbare Auswertung des Rückblicks (Median, P90, RAM-Spitze, Last-Spanne,
  Fehlgriffe und Gegenprobe ``ce8a95f``),
- R1/R3/R2-Quote je Ticket inkl. Fehlerhärtung (``n == 0``, Git-Fehler, kaputte
  JSONL-Zeilen, Windows-Pfadtrenner),
- Abbruch-Kriterium 3b-0 ((a) Auswahl+Lauf >= 50 % Voll-Lauf, (b) R1–R3 >= 50 %,
  (c) unbefangene Fehlgriffe),
- ``try/finally``-Rücksetzung von Sparse-Checkout und HEAD im Rückblick-Skript.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
if str(WURZEL) not in sys.path:
    sys.path.insert(0, str(WURZEL))


def test_r1_quote_leere_daten_kein_zerodivision(tmp_path: Path) -> None:
    """Befund 1 (Dirigent 3b-0): Leeres Fahrtenbuch darf keinen ZeroDivisionError werfen."""
    from messung import r1_quote_duoplus as rq

    bericht = rq.werte_quote_aus([])
    assert bericht["n"] == 0
    assert bericht["r1_anteil"] == 0.0
    assert bericht["r3_anteil"] == 0.0
    assert bericht["voll_anteil"] == 0.0

    # Auch mit Windows-Pfadtrennern funktioniert die Klassifikation
    assert rq.ist_doku(r"docs\handbuch.txt") is True
    assert rq.ist_r3(r"tests\conftest.py") is True
    assert rq.ist_r3(r"requirements-dev.txt") is True
    assert rq.ist_doku("scripts/safe_deploy_vps.sh") is False


def test_r1_quote_git_fehler_nicht_still_verschluckt(tmp_path: Path) -> None:
    """Befund 2 (Dirigent 3b-0): Gescheitertes git log meldet einen Fehler statt stiller leerer Menge."""
    from messung import r1_quote_duoplus as rq

    kein_repo = tmp_path / "kein_git"
    kein_repo.mkdir()
    with pytest.raises(RuntimeError, match="git log"):
        rq.dateien_ticket(kein_repo, 123)


def test_r1_quote_kaputte_jsonl_und_fehlende_schluessel(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Befund 7 (Dirigent 3b-0): Kaputte JSONL-Zeilen werden geloggt, fehlende Schlüssel werfen keinen KeyError."""
    from messung import r1_quote_duoplus as rq

    schlange = tmp_path / "schlange"
    schlange.mkdir()
    (schlange / "fahrtenbuch.jsonl").write_text(
        '{"zeit": "2026-11-10T10:00:00", "ergebnis": "ticket-gruen", "tickets": [42]}\n'
        "kaputtes json{\n"
        '{"zeit": "2026-11-11T10:00:00", "ergebnis": "ticket-rot", "sha": "abc1234", "tickets": [43]}\n',
        encoding="utf-8",
    )
    rb = tmp_path / "rueckblick.jsonl"
    rb.write_text(
        '{"sha": "abc1234", "geaendert_n": 2, "b": {"urteil": "FEHLER"}}\n'
        '{"sha": "ohne_urteil", "b": {}}\n'
        "defekte zeile\n",
        encoding="utf-8",
    )

    with caplog.at_level("WARNING"):
        fahrten = rq.lese_fahrtenbuch(schlange)
        tickets = rq.lese_tickets(fahrten, "2026-11-07")
        r2 = rq.r2_tickets(rb, fahrten)

    assert 42 in tickets and 43 in tickets
    assert r2.get(43) is True
    assert "kaputte" in caplog.text.lower() or "json" in caplog.text.lower()


def test_auswertung_3b0_kennzahlen_gegenprobe_und_abbruch_kriterium() -> None:
    """Befund 5 + Abnahme #98: Kennzahlen, Gegenprobe ce8a95f und Entscheidung (a)/(b)/(c) reproduzierbar."""
    from messung import auswertung_3b0 as aw

    rueckblick = [
        {"sha": "0000001", "fehler": "keine Basis"},
        {
            "sha": "ce8a95f111",
            "ergebnis": "ticket-rot",
            "basis": "c711a62",
            "geaendert_n": 7,
            "kennungen": ["tests/test_gate_bash_240.py::test_a", "tests/test_pytest_deckel_352.py::test_b"],
            "a": {
                "urteil": "AUSWAHL",
                "dateien": 20,
                "tests": 944,
                "anteil": 0.0292,
                "dauer_s": 0.4,
                "ram_mb": 64.0,
                "last": 8.0,
                "rot_dateien": 2,
                "rot_getroffen": 2,
                "rot_verpasst": [],
            },
            "b": {
                "urteil": "AUSWAHL",
                "dateien": 5,
                "tests": 129,
                "anteil": 0.004,
                "dauer_s": 90.0,
                "ram_mb": 440.0,
                "last": 8.5,
                "rot_dateien": 2,
                "rot_getroffen": 1,
                "rot_verpasst": ["tests/test_gate_bash_240.py"],
            },
        },
        {
            "sha": "1ed5b52222",
            "ergebnis": "ticket-rot",
            "basis": "2b2c8c2",
            "geaendert_n": 4,
            "kennungen": ["tests/test_x.py::test_1"],
            "a": {
                "urteil": "AUSWAHL",
                "dateien": 10,
                "tests": 400,
                "anteil": 0.012,
                "dauer_s": 0.2,
                "ram_mb": 62.0,
                "last": 5.0,
                "rot_dateien": 1,
                "rot_getroffen": 0,
                "rot_verpasst": ["tests/test_x.py"],
            },
            "b": {
                "urteil": "AUSWAHL",
                "dateien": 31,
                "tests": 593,
                "anteil": 0.018,
                "dauer_s": 80.0,
                "ram_mb": 430.0,
                "last": 12.0,
                "rot_dateien": 1,
                "rot_getroffen": 0,
                "rot_verpasst": ["tests/test_x.py"],
            },
        },
    ]
    r1_zeilen = [
        {"ticket": 1, "dateien": 3, "r1": True, "r3": False, "r2": False},
        {"ticket": 2, "dateien": 2, "r1": False, "r3": True, "r2": False},
        {"ticket": 3, "dateien": 1, "r1": False, "r3": False, "r2": False},
    ]
    teil_b = {
        "voll_loadfile": {"dauer_s": 1000.0, "ram_mb": 2200.0, "rc": 0},
        "voll_worksteal": {"dauer_s": 800.0, "ram_mb": 2400.0, "rc": 0},
        "auswahl_laeufe": [
            {"sha": "a1", "auswahl_s": 80.0, "lauf_s": 120.0},
            {"sha": "a2", "auswahl_s": 90.0, "lauf_s": 150.0},
            {"sha": "a3", "auswahl_s": 100.0, "lauf_s": 180.0},
        ],
    }

    rb_stats = aw.auswerten_rueckblick(rueckblick)
    assert rb_stats["gesamt"] == 3
    assert rb_stats["gerechnet"] == 2
    assert rb_stats["ohne_basis"] == 1
    assert rb_stats["gegenprobe_ce8a95f"] is True
    assert rb_stats["b"]["dauer_median_s"] == pytest.approx(85.0)
    assert rb_stats["b"]["ram_max_mb"] == pytest.approx(440.0)
    assert rb_stats["b"]["fehlgriffe_n"] == 2
    assert rb_stats["a"]["fehlgriffe_n"] == 1
    assert rb_stats["last_min"] == pytest.approx(5.0)
    assert rb_stats["last_max"] == pytest.approx(12.0)

    r1_stats = aw.auswerten_r1_quote(r1_zeilen)
    assert r1_stats["n"] == 3
    assert r1_stats["r1_oder_r3"] == 2
    assert r1_stats["r1_oder_r3_anteil"] == pytest.approx(2 / 3)

    urteil = aw.entscheide_3b0(
        rb_stats=rb_stats,
        r1_stats=r1_stats,
        teil_b=teil_b,
        ungefangen_shas=["1ed5b52"],
    )
    assert urteil["kriterium_a"]["ausgeloest"] is False  # (90+150)/800 = 30 % < 50 %
    assert urteil["kriterium_a"]["anteil"] == pytest.approx(240.0 / 800.0)
    assert urteil["kriterium_b"]["ausgeloest"] is True  # 66.7 % >= 50 %
    assert urteil["kriterium_c"]["ausgeloest"] is True  # 1 unbefangener Fehlgriff
    assert urteil["entscheidung"] == "variante_3"

    # Gegenfall: kein Kriterium reißt → Kombination
    urteil_kombi = aw.entscheide_3b0(
        rb_stats=rb_stats,
        r1_stats={"n": 10, "r1": 2, "r3": 1, "r1_oder_r3": 3, "r1_oder_r3_anteil": 0.3},
        teil_b=teil_b,
        ungefangen_shas=[],
    )
    assert urteil_kombi["entscheidung"] == "kombination"

    md = aw.formatiere_markdown(rb_stats, r1_stats, teil_b, urteil)
    assert "ce8a95f" in md
    assert "Variante 3" in md


def test_rueckblick_sparse_und_checkout_wird_bei_fehler_zurueckgesetzt(tmp_path: Path) -> None:
    """Befund 3 (Dirigent 3b-0): Bei Fehler während des Laufs bleiben Sparse-Checkout und HEAD sauber zurückgesetzt."""
    from messung import rueckblick_auswahl_duoplus as rb

    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (
        ["git", "-C", str(repo), "init", "-q", "-b", "master"],
        ["git", "-C", str(repo), "config", "user.email", "t@t"],
        ["git", "-C", str(repo), "config", "user.name", "t"],
        ["git", "-C", str(repo), "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, check=True)
    (repo / "archive").mkdir()
    (repo / "archive" / "alt.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "app.py").write_text("y = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "basis"], check=True)
    basis = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "update-ref", "refs/remotes/origin/master", basis], check=True)

    with pytest.raises(RuntimeError, match="abbruch"), rb.gefilterter_klon(repo, ["archive"]):
        assert not (repo / "archive" / "alt.py").exists()
        raise RuntimeError("abbruch")

    # Nach dem Fehler ist archive/ wieder vollständig da und kein Sparse-Checkout aktiv
    assert (repo / "archive" / "alt.py").exists()
    sp = subprocess.run(
        ["git", "-C", str(repo), "sparse-checkout", "list"], capture_output=True, text=True, check=False
    )
    assert sp.returncode != 0


def test_auswertung_3b0_klassifiziert_fehlgriffe_aus_git_diff(tmp_path: Path) -> None:
    """Reproduzierbare R1/R2/R3-Prüfung je Rückblick-Lauf direkt aus ``git diff basis..sha``."""
    from messung import auswertung_3b0 as aw

    repo = tmp_path / "app"
    repo.mkdir()
    for cmd in (
        ["git", "-C", str(repo), "init", "-q", "-b", "master"],
        ["git", "-C", str(repo), "config", "user.email", "t@t"],
        ["git", "-C", str(repo), "config", "user.name", "t"],
        ["git", "-C", str(repo), "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, check=True)
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "deploy.sh").write_text("echo 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "basis"], check=True)
    basis = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    # Commit 1: Shell-Skript geändert -> R1 fängt den Fehlgriff
    (repo / "deploy.sh").write_text("echo 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "deploy.sh"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "r1"], check=True)
    sha_r1 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    # Commit 2: nur Python-Datei geändert -> R1/R3 greifen nicht, bei AUSWAHL ungefangen
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", basis], check=True)
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "nur-py"], check=True)
    sha_py = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    zeilen = [
        {
            "sha": sha_r1,
            "basis": basis,
            "geaendert_n": 1,
            "kennungen": ["tests/test_gate_bash_240.py::test_a"],
            "a": {"urteil": "AUSWAHL", "rot_verpasst": []},
            "b": {"urteil": "AUSWAHL", "rot_verpasst": ["tests/test_gate_bash_240.py"]},
        },
        {
            "sha": sha_py,
            "basis": basis,
            "geaendert_n": 1,
            "kennungen": ["tests/test_app.py::test_b"],
            "a": {"urteil": "AUSWAHL", "rot_verpasst": ["tests/test_app.py"]},
            "b": {"urteil": "AUSWAHL", "rot_verpasst": ["tests/test_app.py"]},
        },
        {
            "sha": sha_py,
            "basis": basis,
            "geaendert_n": 1,
            "kennungen": ["tests/test_app.py::test_c"],
            "a": {"urteil": "AUSWAHL", "rot_verpasst": []},
            "b": {"urteil": "FEHLER", "rot_verpasst": ["tests/test_app.py"]},
        },
    ]
    pruefung = aw.pruefe_rueckblick_r1_r3(zeilen, repo)
    assert pruefung["laeufe_n"] == 3
    assert pruefung["r1_laeufe"] == 1
    assert pruefung["r2_laeufe"] == 1
    assert pruefung["voll_laeufe"] == 2
    assert pruefung["ungefangen_shas"] == [sha_py[:7]]


def test_doku_3b0_enthaelt_zahlen_gegenprobe_und_entscheidung() -> None:
    """Abnahme #98: Vergleich 4.3/5 und Plan-Stand tragen 3b-0-Zahlen, Gegenprobe und Entscheidung."""
    vergleich = (WURZEL / "docs" / "PRUEFTOR_VERGLEICH.md").read_text(encoding="utf-8")
    plan = (WURZEL / "docs" / "PLAN_L1_usb_stick.md").read_text(encoding="utf-8")

    for pflicht in ("[3B0]", "83,63", "436,5", "59,3", "179", "302", "ce8a95f", "1ed5b52", "61e877f", "0cba2e3"):
        assert pflicht in vergleich, f"{pflicht!r} fehlt in docs/PRUEFTOR_VERGLEICH.md"

    for pflicht in ("3b-0", "59,3", "179/302", "ce8a95f", "1ed5b52", "61e877f", "0cba2e3", "Variante 3"):
        assert pflicht in plan, f"{pflicht!r} fehlt in docs/PLAN_L1_usb_stick.md"

    # #169: entscheidungen.md bleibt wegen .gitignore außerhalb des öffentlichen Repos
    assert not (WURZEL / "docs" / "agents" / "entscheidungen.md").exists()
