"""Tests L1 Etappe 1 P2b: capo/Staging lesen Pfade aus Konfig und Maschinen-Liste.

(a) ohne Schlüssel gelten exakt die alten Pfade, (b) ein gesetzter Schlüssel bzw.
Maschinen-Eintrag greift, (c) die Env-Naht ``TO_SPAWN_STAGING_SPERRE`` gewinnt.
Die echte Maschinen-Liste liest kein Test (``TO_SPAWN_MASCHINE`` zeigt auf ``tmp_path``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from to_spawn import capo, config, docker_reste, staging_schalter

SPEC_TEXT = (
    "## Hauptschalter\n\n- **Name des Hauptschalters:** `demo_funktion.an` — Demo.\n"
)


def _konfig(repo: Path, daten: dict) -> None:
    datei = repo / ".to-spawn" / "config.json"
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps(daten), encoding="utf-8")


def _maschine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, daten: dict | None
) -> None:
    datei = tmp_path / "maschine.json"
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(datei))
    monkeypatch.delenv(
        "TO_SPAWN_STAGING_SPERRE", raising=False
    )  # conftest setzt die Naht
    if daten is not None:
        datei.write_text(json.dumps(daten), encoding="utf-8")


# --- Fundstelle 10: Staging-Log ---------------------------------------------------


def test_staging_log_vorgabe_unveraendert() -> None:
    assert capo.STAGING_LOG_STANDARD == "/home/bau/staging/deploys.jsonl"
    assert capo.STAGING_LOG_STANDARD == config.DEFAULTS["vps"]["staging_log"]


def test_staging_log_aus_konfig(tmp_path: Path) -> None:
    log = tmp_path / "deploys.jsonl"
    log.write_text("eins\nzwei\n", encoding="utf-8")
    assert capo._staging_zeilen({"staging_log": str(log)}) == ["eins", "zwei"]


# --- Fundstelle 16: Entscheidungs-Bericht -----------------------------------------


def test_entscheidungen_ohne_schluessel_alter_pfad(tmp_path: Path) -> None:
    erwartet = tmp_path.resolve() / "docs" / "agents" / "entscheidungen_7.md"
    assert capo.entscheidungs_datei(tmp_path, 7) == erwartet
    assert capo.uebersicht(tmp_path, 7, [1]) == erwartet
    assert erwartet.is_file()


def test_entscheidungen_aus_konfig(tmp_path: Path) -> None:
    _konfig(tmp_path, {"pfade": {"agenten": "berichte/agenten"}})
    erwartet = tmp_path.resolve() / "berichte" / "agenten" / "entscheidungen_7.md"
    assert capo.entscheidungs_datei(tmp_path, 7) == erwartet
    assert capo.uebersicht(tmp_path, 7, [1]) == erwartet
    assert erwartet.is_file()
    assert not (tmp_path / "docs").exists()


# --- Fundstelle 11: Staging-Sperre ------------------------------------------------


def test_sperre_ohne_maschinen_liste_alter_pfad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _maschine(tmp_path, monkeypatch, None)
    assert docker_reste._sperr_pfade() == [Path("/home/bau/staging/.deploy.lock")]


def test_sperre_aus_maschinen_liste(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = tmp_path / "a.lock", tmp_path / "b.lock"
    _maschine(tmp_path, monkeypatch, {"staging_sperren": [str(a), str(b)]})
    assert docker_reste._sperr_pfade() == [a, b]


@pytest.mark.skipif(docker_reste.fcntl is None, reason="flock nur auf dem Bau-Server")
def test_sperre_maschinen_liste_belegt_heisst_staging_baut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fcntl = docker_reste.fcntl
    frei, belegt = tmp_path / "frei.lock", tmp_path / "belegt.lock"
    frei.touch()
    belegt.touch()
    _maschine(tmp_path, monkeypatch, {"staging_sperren": [str(frei), str(belegt)]})
    with belegt.open("r") as halter:
        fcntl.flock(halter, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert docker_reste._staging_sperre_nehmen() is None
    sperre = docker_reste._staging_sperre_nehmen()
    assert sperre is not None
    sperre.close()


def test_sperre_env_gewinnt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _maschine(
        tmp_path, monkeypatch, {"staging_sperren": [str(tmp_path / "liste.lock")]}
    )
    monkeypatch.setenv("TO_SPAWN_STAGING_SPERRE", str(tmp_path / "env.lock"))
    assert docker_reste._sperr_pfade() == [tmp_path / "env.lock"]


# --- Fundstelle 12: Schalter-Skript -----------------------------------------------


def _schalte_probe(repo: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(staging_schalter, "_spec_text", lambda gh_repo, spec: SPEC_TEXT)
    ergebnis = staging_schalter.schalte_an(repo, "x/y", 9, dry_run=True)
    return "\n".join(ergebnis.zeilen)


def test_schalter_ohne_schluessel_alter_pfad(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        staging_schalter.SKRIPT
        == "scripts/schalter.py"
        == config.DEFAULTS["staging"]["schalter"]
    )
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "schalter.py").write_text("", encoding="utf-8")
    text = _schalte_probe(tmp_path, monkeypatch)
    assert "[Probe]" in text and "scripts/schalter.py an demo_funktion.an" in text, text


def test_schalter_aus_konfig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _konfig(tmp_path, {"staging": {"schalter": "werkzeug/umschalter.py"}})
    text = _schalte_probe(tmp_path, monkeypatch)
    assert "werkzeug/umschalter.py fehlt" in text, text
    (tmp_path / "werkzeug").mkdir()
    (tmp_path / "werkzeug" / "umschalter.py").write_text("", encoding="utf-8")
    text = _schalte_probe(tmp_path, monkeypatch)
    assert "werkzeug/umschalter.py an demo_funktion.an" in text, text


# --- P6a: Lücken aus dem P2b-Review ------------------------------------------------


def test_staging_log_aus_echter_repo_konfig(tmp_path: Path) -> None:
    log = tmp_path / "eigen.jsonl"
    log.write_text("eins\nzwei\n", encoding="utf-8")
    _konfig(tmp_path, {"vps": {"staging_log": str(log)}})
    assert capo._staging_zeilen(config.lade(tmp_path)["vps"]) == ["eins", "zwei"]


@pytest.mark.skipif(docker_reste.fcntl is None, reason="flock nur auf dem Bau-Server")
def test_sperre_nicht_pruefbar_heisst_staging_baut(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ordner = tmp_path / "ist-ein-ordner.lock"  # open("r") → IsADirectoryError, kein FileNotFoundError
    ordner.mkdir()
    _maschine(tmp_path, monkeypatch, {"staging_sperren": [str(ordner)]})
    assert docker_reste._staging_sperre_nehmen() is None


@pytest.mark.skipif(docker_reste.fcntl is None, reason="flock nur auf dem Bau-Server")
@pytest.mark.parametrize("sperren", [[], ["fehlt.lock"]])
def test_staging_nie_eingerichtet_heisst_kein_neubau(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sperren: list[str]
) -> None:
    _maschine(tmp_path, monkeypatch, {"staging_sperren": [str(tmp_path / s) for s in sperren]})
    sperre = docker_reste._staging_sperre_nehmen()
    assert sperre is not None
    sperre.close()


@pytest.mark.skipif(docker_reste.fcntl is None, reason="flock nur auf dem Bau-Server")
def test_sperre_env_gewinnt_beim_nehmen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fcntl = docker_reste.fcntl
    frei, env = tmp_path / "frei.lock", tmp_path / "env.lock"
    frei.touch()
    env.touch()
    _maschine(tmp_path, monkeypatch, {"staging_sperren": [str(frei)]})
    monkeypatch.setenv("TO_SPAWN_STAGING_SPERRE", str(env))
    with env.open("r") as halter:
        fcntl.flock(halter, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert docker_reste._staging_sperre_nehmen() is None
