"""Einrichtungs-Lücken für schnelle Prüfung, Komplett-Prüfung und Live-Schritt (#425 Teil D, #448).

Vorgabe des Auftraggebers (24.11.): Das Bau-System arbeitet mit jedem Repo, mit und ohne
Staging-App; je Ticket die schnelle Prüfung, die Komplett-Prüfung einmal je Spec, der
Live-Schritt mit eigenem Befehl. Fehlt die Einrichtung dafür, fordert das
Einrichtungs-Ticket sie nach. Ein Wert, der kein Text ist (#448), gilt als nicht
eingerichtet und taucht dadurch als Lücke auf.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import test_einrichtungs_ticket_410 as t410
import test_spec_rand_e2 as e2

from to_spawn import config, einrichtungs_ticket

STAGING = {
    "staging": {"url": "https://staging.example"},
    "staging_start": "make staging",
}
NEU = {
    "pruefung": {"schnell": "make pruefen-schnell", "komplett": "make pruefen-alles"},
    "live": {"befehl": "make live"},
}


def _repo(tmp_path: Path, werkzeug: bool = True) -> Path:
    if werkzeug:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    return tmp_path


def _konfig(**eigen: Any) -> dict[str, Any]:
    """Konfig wie ``config.lade`` sie liefert: Vorgaben plus eigene Werte."""
    return config._mische(config.DEFAULTS, {**STAGING, **eigen})


def test_altes_repo_ohne_neue_schluessel_meldet_alle_drei(tmp_path: Path) -> None:
    fehlt = einrichtungs_ticket.luecken(_repo(tmp_path), _konfig())
    assert fehlt == ["pruefung.schnell", "pruefung.komplett", "live.befehl"]


def test_voll_eingerichtet_keine_luecke(tmp_path: Path) -> None:
    assert einrichtungs_ticket.luecken(_repo(tmp_path), _konfig(**NEU)) == []


def test_live_url_und_aufraeumen_sind_keine_pflicht(tmp_path: Path) -> None:
    konfig = _konfig(**NEU)
    assert config.live_schritt(konfig)["url"] == ""
    assert config.live_schritt(konfig)["aufraeumen"] == ""
    assert einrichtungs_ticket.luecken(_repo(tmp_path), konfig) == []


@pytest.mark.parametrize(
    ("weg", "lucke"),
    [
        (("pruefung", "schnell"), "pruefung.schnell"),
        (("pruefung", "komplett"), "pruefung.komplett"),
        (("live", "befehl"), "live.befehl"),
    ],
)
def test_je_fehlender_schluessel_genau_eine_luecke(tmp_path: Path, weg: tuple[str, str], lucke: str) -> None:
    eigen = json.loads(json.dumps(NEU))
    eigen[weg[0]][weg[1]] = "   "
    assert einrichtungs_ticket.luecken(_repo(tmp_path), _konfig(**eigen)) == [lucke]


def test_ohne_staging_app_gleiche_pflichten(tmp_path: Path) -> None:
    konfig = config._mische(config.DEFAULTS, {"staging": {"ohne": True}})
    fehlt = einrichtungs_ticket.luecken(_repo(tmp_path), konfig)
    assert fehlt == ["pruefung.schnell", "pruefung.komplett", "live.befehl"]


def test_werkzeug_fehlt_bleibt_eigene_luecke(tmp_path: Path) -> None:
    fehlt = einrichtungs_ticket.luecken(_repo(tmp_path, werkzeug=False), _konfig(**NEU))
    assert fehlt == ["pruefung"]


@pytest.mark.parametrize("lucke", ["pruefung.schnell", "pruefung.komplett", "live.befehl"])
def test_auftrag_sagt_was_wo_und_beispiel(lucke: str) -> None:
    text = einrichtungs_ticket._AUFTRAG[lucke]
    gruppe, schluessel = lucke.split(".")
    assert ".to-spawn/config.json" in text
    assert f'"{gruppe}"' in text and f'"{schluessel}"' in text
    assert "Beispiel" in text


# --- #448: Nicht-Text gilt als nicht eingerichtet -------------------------------------


@pytest.mark.parametrize("wert", [["pytest", "-q"], 5, True, {"a": 1}])
def test_pruef_befehl_nicht_text_ist_leer(wert: Any) -> None:
    assert config.pruef_befehl({"pruefung": {"schnell": wert}}, "schnell") == ""


@pytest.mark.parametrize("wert", [["make", "live"], 0.5, False])
def test_live_schritt_nicht_text_ist_leer(wert: Any) -> None:
    assert config.live_schritt({"live": {"befehl": wert}})["befehl"] == ""


def test_text_bleibt_text() -> None:
    assert config.pruef_befehl({"pruefung": {"schnell": "  pytest -q "}}, "schnell") == ("pytest -q")


def test_liste_als_befehl_wird_luecke(tmp_path: Path) -> None:
    eigen = json.loads(json.dumps(NEU))
    eigen["pruefung"]["schnell"] = ["pytest", "-q"]
    fehlt = einrichtungs_ticket.luecken(_repo(tmp_path), _konfig(**eigen))
    assert fehlt == ["pruefung.schnell"]


# --- Weg über die echte Kommandozeile ----------------------------------------------


def test_neue_spec_bekommt_t0_mit_neuen_luecken(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = t410._aufbau(tmp_path, konfig=STAGING, python=True)
    lauf = t410._cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    titel = e2._welt(weltdatei).get("angelegt", [])
    assert len(titel) == 1, ausgabe
    for lucke in ("pruefung.schnell", "pruefung.komplett", "live.befehl"):
        assert lucke in titel[0]
    assert "staging" not in titel[0]


def test_laufender_bau_bleibt_nicht_stehen(tmp_path: Path) -> None:
    """Altes Repo mitten im Bau: nur Hinweis, nichts angelegt, nichts umgehängt."""
    repo, weltdatei, umgebung = t410._aufbau(tmp_path, konfig=STAGING, python=True)
    (repo / ".to-spawn" / "bau_log").mkdir(parents=True)
    (repo / ".to-spawn" / "bau_log" / "901.jsonl").write_text("{}\n", encoding="utf-8")
    vorher = e2._manifest(repo).read_bytes()
    lauf = t410._cli(repo, umgebung, "regularien", str(e2.SPEC))
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "angelegt" not in e2._welt(weltdatei)
    assert e2._manifest(repo).read_bytes() == vorher
    assert "läuft schon" in lauf.stdout and "pruefung.schnell" in lauf.stdout


def test_voll_eingerichtet_bleibt_still(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = t410._aufbau(tmp_path, konfig={**STAGING, **NEU}, python=True)
    lauf = t410._cli(repo, umgebung, "regularien", str(e2.SPEC))
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "angelegt" not in e2._welt(weltdatei)
    assert "Einrichtung" not in lauf.stdout
