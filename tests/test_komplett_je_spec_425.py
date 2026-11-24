"""#425 Teil C1: die Komplett-Prüfung läuft genau einmal je Spec.

Echte Abläufe ohne Attrappen: ein tmp-Git-Repo als Klon, ein echter kleiner Befehl
(``python -c …``), der bei jedem Lauf eine Zeile in eine Zähldatei schreibt und mit dem Exit aus
``exit.txt`` endet. Die Zähldatei beweist „nur einmal“.

Tabelle Repo-Art (mit/ohne Staging-App) × Moment × Merker; dazu nicht eingerichtet, neuer Kopf,
rot dann grün. Vorgabe des Auftraggebers (24.11.): mit Staging, sobald alle Tickets auf Staging
sind; ohne Staging vor dem Live-Schritt.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from to_spawn import config, komplett_lauf
from to_spawn.pruefplan import Moment

SPEC = 777


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name + "\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "-c", "user.name=Bau", "-c", "user.email=bau@example.invalid", "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def klon(tmp_path: Path) -> Path:
    repo = tmp_path / "klon"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, "a.txt")
    return repo


@pytest.fixture
def zaehl(tmp_path: Path) -> Path:
    return tmp_path / "zaehl.txt"


def _befehl(zaehl: Path) -> str:
    """Echter Prüf-Befehl: eine Zeile je Lauf in ``zaehl``, Exit aus ``exit.txt`` daneben (Standard 0)."""
    code = (
        "import pathlib,sys;"
        f"z=pathlib.Path({str(zaehl)!r});"
        "z.open('a').write('lauf\\n');"
        "e=z.with_name('exit.txt');"
        "sys.exit(int(e.read_text()) if e.exists() else 0)"
    )
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def _konfig(zaehl: Path, *, staging: bool, eingerichtet: bool = True, komplett: bool = True) -> dict[str, Any]:
    eigen: dict[str, Any] = {
        "staging_start": "",
        "staging": {"url": "http://127.0.0.1:8001/" if staging else "", "ohne": not staging},
    }
    if eingerichtet:
        eigen["pruefung"] = {"schnell": "true", "komplett": _befehl(zaehl) if komplett else ""}
    return config._mische(config.DEFAULTS, eigen)


def _laeufe(zaehl: Path) -> int:
    return len(zaehl.read_text(encoding="utf-8").splitlines()) if zaehl.exists() else 0


def _rot(zaehl: Path, code: int = 1) -> None:
    zaehl.with_name("exit.txt").write_text(str(code), encoding="utf-8")


# --- Tabelle: Repo-Art × Moment × Merker --------------------------------------------------------


def test_mit_staging_alle_auf_staging_laeuft_einmal(klon: Path, zaehl: Path) -> None:
    konfig = _konfig(zaehl, staging=True)
    erst = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (erst.gelaufen, erst.ok) == (True, True)
    assert erst.kopf == _git(klon, "rev-parse", "HEAD")
    assert erst.dauer_s is not None and erst.dauer_s >= 0
    zweit = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (zweit.gelaufen, zweit.ok) == (False, True)
    assert _laeufe(zaehl) == 1


def test_mit_staging_vor_live_nach_gruenem_lauf_nichts(klon: Path, zaehl: Path) -> None:
    konfig = _konfig(zaehl, staging=True)
    komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    live = komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, konfig)
    assert (live.gelaufen, live.ok) == (False, True)
    assert _laeufe(zaehl) == 1


def test_mit_staging_vor_live_ohne_merker_sicherheitsnetz(klon: Path, zaehl: Path) -> None:
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, _konfig(zaehl, staging=True))
    assert (erg.gelaufen, erg.ok) == (True, True)
    assert _laeufe(zaehl) == 1


def test_ohne_staging_alle_auf_staging_nichts(klon: Path, zaehl: Path) -> None:
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, _konfig(zaehl, staging=False))
    assert (erg.gelaufen, erg.ok) == (False, True)
    assert _laeufe(zaehl) == 0
    assert komplett_lauf.lies_merker(klon, SPEC) is None


def test_ohne_staging_vor_live_laeuft_einmal(klon: Path, zaehl: Path) -> None:
    konfig = _konfig(zaehl, staging=False)
    erst = komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, konfig)
    zweit = komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, konfig)
    assert (erst.gelaufen, erst.ok) == (True, True)
    assert (zweit.gelaufen, zweit.ok) == (False, True)
    assert _laeufe(zaehl) == 1


def test_moment_ticket_ist_kein_komplett_auftrag(klon: Path, zaehl: Path) -> None:
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.TICKET, _konfig(zaehl, staging=True))
    assert (erg.gelaufen, erg.ok) == (False, True)
    assert _laeufe(zaehl) == 0


# --- Übergangsregel und Lücken ------------------------------------------------------------------


def test_nicht_eingerichtet_alter_ablauf(klon: Path, zaehl: Path) -> None:
    konfig = _konfig(zaehl, staging=True, eingerichtet=False)
    for moment in (Moment.ALLE_AUF_STAGING, Moment.VOR_LIVE):
        erg = komplett_lauf.komplett_einmal(klon, SPEC, moment, konfig)
        assert (erg.gelaufen, erg.ok) == (False, True)
        assert "nicht eingerichtet, alter Ablauf" in erg.grund
    assert _laeufe(zaehl) == 0
    assert komplett_lauf.lies_merker(klon, SPEC) is None


def test_komplett_befehl_fehlt_ist_nicht_ok(klon: Path, zaehl: Path) -> None:
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, _konfig(zaehl, staging=False, komplett=False))
    assert (erg.gelaufen, erg.ok) == (False, False)
    assert "pruefung.komplett" in erg.grund
    assert _laeufe(zaehl) == 0


# --- Kopf, rot/grün, Merker ---------------------------------------------------------------------


def test_neuer_kopf_nach_gruenem_lauf_laeuft_einmal_neu(klon: Path, zaehl: Path) -> None:
    konfig = _konfig(zaehl, staging=True)
    komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    neu = _commit(klon, "nachbesserung.txt")
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (erg.gelaufen, erg.ok, erg.kopf) == (True, True, neu)
    komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, konfig)
    assert _laeufe(zaehl) == 2


def test_rot_gilt_nicht_als_gelaufen_dann_gruen(klon: Path, zaehl: Path) -> None:
    konfig = _konfig(zaehl, staging=True)
    _rot(zaehl)
    rot = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (rot.gelaufen, rot.ok) == (True, False)
    assert "Exit 1" in rot.grund and "Handlung" in rot.grund
    merker = komplett_lauf.lies_merker(klon, SPEC)
    assert merker is not None and merker.exit == 1 and not merker.gruen
    _rot(zaehl, 0)
    gruen = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (gruen.gelaufen, gruen.ok) == (True, True)
    nichts = komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, konfig)
    assert nichts.gelaufen is False
    assert _laeufe(zaehl) == 2


def test_merker_format_und_lesen(klon: Path, zaehl: Path) -> None:
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, _konfig(zaehl, staging=False))
    datei = klon / ".to-spawn" / "pruef" / f"komplett-{SPEC}.json"
    roh = json.loads(datei.read_text(encoding="utf-8"))
    assert set(roh) >= {"spec", "exit", "dauer_s", "start", "ende", "kopf", "moment"}
    assert roh["spec"] == SPEC and roh["exit"] == 0 and roh["moment"] == "vor_live"
    merker = komplett_lauf.lies_merker(klon, SPEC)
    assert merker is not None and merker.gruen
    assert merker.kopf == erg.kopf and merker.dauer_s == erg.dauer_s
    assert merker.start <= merker.ende


def test_merker_je_spec_getrennt(klon: Path, zaehl: Path) -> None:
    konfig = _konfig(zaehl, staging=False)
    komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, konfig)
    andere = komplett_lauf.komplett_einmal(klon, SPEC + 1, Moment.VOR_LIVE, konfig)
    assert andere.gelaufen is True
    assert _laeufe(zaehl) == 2


def test_kaputter_merker_gilt_als_nicht_gelaufen(klon: Path, zaehl: Path) -> None:
    datei = klon / ".to-spawn" / "pruef" / f"komplett-{SPEC}.json"
    datei.parent.mkdir(parents=True)
    datei.write_text("{kaputt", encoding="utf-8")
    assert komplett_lauf.lies_merker(klon, SPEC) is None
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, _konfig(zaehl, staging=False))
    assert (erg.gelaufen, erg.ok) == (True, True)
    assert _laeufe(zaehl) == 1
