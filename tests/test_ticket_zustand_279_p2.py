"""#279 P2: Startwege fragen ``fertig_gebaut`` über ``ticket_zustand``.

Ein Ticket mit Label der Deploy-Schlange (grün auf Staging) ist fertig gebaut: die
Start-Tür startet nicht, die Staffel hört auf, der Stop-Hook löst nicht ab, und
``to_spawn.py zu-bauen`` nennt es nicht. GitHub ist ein externer Dienst — gestellt nur
``gh`` (``tests/hilfen/gh_stub.py``), alles andere echt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_to_spawn_kern import TICKETS, _cli, _staffel_lauf, repo  # noqa: F401 — ``repo`` ist ein Fixture

SKILL = Path(__file__).resolve().parent.parent
GH_STUB = SKILL / "tests" / "hilfen" / "gh_stub.py"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "skripte" / "hooks"))

import staffel_stop  # noqa: E402

from to_spawn import bau_log, startstelle, ticket_zustand  # noqa: E402

FERTIG = ticket_zustand.STAGING_FERTIG_LABEL


def _labels(tmp_path: Path, daten: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    pfad = tmp_path / "gh_daten.json"
    pfad.write_text(json.dumps({n: {"labels": l} for n, l in daten.items()}), encoding="utf-8")
    monkeypatch.setenv("GH_STUB_DATEN", str(pfad))


@pytest.fixture()
def gh_welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(startstelle.TEST_OFFEN_ENV, raising=False)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(GH_STUB))
    monkeypatch.setenv(startstelle.SPERR_ORDNER_ENV, str(tmp_path / "sperren"))
    monkeypatch.setenv(startstelle.STAND_ABSTAND_ENV, "0")
    return tmp_path


# --- Stelle 30: Start-Tür -------------------------------------------------------------


def test_tuer_startet_fertig_gebautes_ticket_nicht(gh_welt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _labels(gh_welt, {"27931": [FERTIG]}, monkeypatch)
    entscheidung = startstelle.betreten("27931", "o/r", gh_welt)
    assert not entscheidung.erlaubt
    assert entscheidung.exit_code == 0
    assert "Nachbesserung" in entscheidung.grund
    assert FERTIG in entscheidung.grund


def test_tuer_staffel_runde_endet_bei_fertig_gebaut(gh_welt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _labels(gh_welt, {"27932": [FERTIG]}, monkeypatch)
    entscheidung = startstelle.weiter_entscheidung("27932", gh_welt)
    assert not entscheidung.erlaubt
    assert entscheidung.exit_code == 0


def test_tuer_offenes_ticket_mit_anderem_label_startet(gh_welt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _labels(gh_welt, {"27933": ["bug"]}, monkeypatch)
    assert startstelle.betreten("27933", "o/r", gh_welt).erlaubt


def test_tuer_gh_fehler_bleibt_fail_closed(gh_welt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_STUB_KAPUTT", "27934")
    entscheidung = startstelle.betreten("27934", "o/r", gh_welt)
    assert not entscheidung.erlaubt
    assert entscheidung.exit_code == startstelle.EXIT_UNKLAR


# --- Stelle 31: Folge-Session nach Handoff --------------------------------------------


def test_keine_folge_session_fuer_fertig_gebautes(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _labels(tmp_path, {TICKETS[0]: [FERTIG]}, monkeypatch)
    assert _staffel_lauf(repo) == 0
    zeilen = bau_log.lese(repo, TICKETS[0])
    assert sum(1 for z in zeilen if z["typ"] == "session_start") == 1


def test_folge_session_laeuft_bei_lese_fehler_weiter(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: F811
    """Fail-open: ein Lese-Fehler hält keinen laufenden Bau an (die Start-Tür fängt es danach ab)."""
    _labels(tmp_path, {}, monkeypatch)
    monkeypatch.setenv("GH_STUB_KAPUTT", TICKETS[0])
    assert _staffel_lauf(repo) == 0
    zeilen = bau_log.lese(repo, TICKETS[0])
    assert sum(1 for z in zeilen if z["typ"] == "session_start") == 2


# --- Stelle 32: Stop-Hook --------------------------------------------------------------


def test_hook_fertig_gebaut_gilt_nicht_offen(gh_welt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _labels(gh_welt, {"27935": [FERTIG], "27936": []}, monkeypatch)
    assert staffel_stop.ticket_offen("27935") is False
    assert staffel_stop.ticket_offen("27936") is True


def test_hook_lese_fehler_bleibt_nicht_offen(gh_welt: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_STUB_KAPUTT", "27937")
    monkeypatch.setenv("GH_STUB_ZU", "27938")
    assert staffel_stop.ticket_offen("27937") is False
    assert staffel_stop.ticket_offen("27938") is False


def test_hook_ohne_eigene_regel_kopie() -> None:
    quelle = (SKILL / "skripte" / "hooks" / "staffel_stop.py").read_text(encoding="utf-8")
    assert "ticket_zustand" in quelle
    assert '"OPEN"' not in quelle


# --- Stelle 3: ``to_spawn.py zu-bauen`` + spawn_local.ps1 ------------------------------


def test_zu_bauen_nennt_nur_nicht_fertig_gebaute(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    _labels(tmp_path, {TICKETS[0]: [FERTIG]}, monkeypatch)
    lauf = _cli(repo, "zu-bauen", "900")
    assert lauf.returncode == 0, lauf.stderr
    assert lauf.stdout.split() == [TICKETS[1]]
    lauf = _cli(repo, "zu-bauen", "900", zusatz_env={"GH_STUB_ZU": TICKETS[1]})
    assert lauf.returncode == 0, lauf.stderr
    assert lauf.stdout.split() == []


def test_zu_bauen_unbekannter_stand_startet_nicht_und_meldet(repo: Path) -> None:  # noqa: F811
    lauf = _cli(repo, "zu-bauen", "900", zusatz_env={"GH_STUB_KAPUTT": TICKETS[0]})
    assert lauf.returncode == 1
    assert lauf.stdout.split() == [TICKETS[1]]
    assert f"#{TICKETS[0]}" in lauf.stderr


def test_spawn_local_fragt_zu_bauen_statt_eigenem_filter() -> None:
    """Windows-Skript: unter Linux nicht ausführbar, darum Text-Prüfung."""
    text = (SKILL / "spawn_local.ps1").read_text(encoding="utf-8")
    assert '"zu-bauen"' in text
    assert "--state closed" not in text
    assert "$zu -contains" not in text


def test_spawn_local_wiederholt_zu_bauen_einmal() -> None:
    """Windows-Skript: ein Wiederholversuch nach 5 s, danach bisheriges Verhalten (Text-Prüfung)."""
    text = (SKILL / "spawn_local.ps1").read_text(encoding="utf-8")
    assert text.count('"zu-bauen"') == 2
    assert "Start-Sleep -Seconds 5" in text
