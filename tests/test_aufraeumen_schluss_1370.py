"""Tests #1370: Aufräumen ist fester Schlussschritt — die Regeln kennt nur der Hausmeister.

Echt laufen: Git-Repo mit Bare-Origin, die CLI ``skripte/capo.py`` (SPEC FERTIG), der echte
Hausmeister über eine Wegwerf-Wurzel und die echte Zustandsdatei. Gestellt ist nur ``gh``
(Ersatz aus #213). Die Exception im Hausmeister ist Rechenlogik-Attrappe (monkeypatch).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _beleg,
    _capo,
    _commit,
    _gh_setzen,
    _iso,
    _text,
    welt,
)

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))

from to_spawn import capo, config, hausmeister

HELFER = "_hausmeister_schluss"
REGEL_WOERTER = (
    "rm -rf",
    "git worktree remove",
    "sicherung/",
    "24 h",
    "24h",
    "TO_SPAWN_HAUSMEISTER",
    "PLATZNOT",
    "0.15",
    "0.05",
)


@pytest.fixture()
def hm(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> Path:  # noqa: F811
    """Hausmeister-Wurzel mit einem Ordner, der bleibt; jeder Lauf schreibt eine Log-Zeile."""
    wurzel = welt["tmp"] / "hm_wurzel"
    (wurzel / "wt-999").mkdir(parents=True)
    log = welt["tmp"] / "hm.jsonl"
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", str(wurzel / "*"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(log))
    monkeypatch.setenv("TO_SPAWN_ABSCHLUSS_ORDNER", str(welt["tmp"] / "abschluss"))
    return log


def _laeufe(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [
        json.loads(z) for z in log.read_text(encoding="utf-8").splitlines() if z.strip()
    ]


def _zustand(welt: dict[str, Path]) -> dict:  # noqa: F811
    dateien = list(welt["zustand"].glob(f"*_{SPEC}.json"))
    return json.loads(dateien[0].read_text(encoding="utf-8")) if dateien else {}


def _spec_fertig(welt: dict[str, Path]) -> None:  # noqa: F811
    _commit(welt["repo"], "feat: A (#901)", _beleg("901"))
    _commit(welt["repo"], "feat: B (#902)", _beleg("902"))
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())


def test_spec_fertig_raeumt_einmal_auf(welt: dict[str, Path], hm: Path) -> None:  # noqa: F811
    _spec_fertig(welt)
    erster = _capo(welt)
    assert "SPEC FERTIG" in erster.stdout, _text(erster)
    assert "Hausmeister: 0 gelöscht, 1 bleibt, 0 Fehler" in erster.stdout, _text(erster)
    laeufe = _laeufe(hm)
    assert len(laeufe) == 1 and laeufe[0]["trocken"] is False, laeufe
    assert _zustand(welt).get("aufraeumen_erledigt"), _zustand(welt)
    zweiter = _capo(welt)
    assert "Hausmeister:" not in zweiter.stdout, _text(zweiter)
    assert len(_laeufe(hm)) == 1


def test_probe_raeumt_nur_trocken_ohne_marker(welt: dict[str, Path], hm: Path) -> None:  # noqa: F811
    _spec_fertig(welt)
    probe = _capo(welt, "--dry-run")
    assert "SPEC FERTIG" in probe.stdout, _text(probe)
    assert "Hausmeister (Probe): 0 würde löschen, 1 bleibt, 0 Fehler" in probe.stdout, (
        _text(probe)
    )
    assert [lauf["trocken"] for lauf in _laeufe(hm)] == [True]
    assert not _zustand(welt).get("aufraeumen_erledigt")


def test_spec_nicht_fertig_raeumt_nicht_auf(welt: dict[str, Path], hm: Path) -> None:  # noqa: F811
    ergebnis = _capo(welt)
    assert "SPEC FERTIG" not in ergebnis.stdout, _text(ergebnis)
    assert "Hausmeister" not in ergebnis.stdout
    assert _laeufe(hm) == []
    assert not _zustand(welt).get("aufraeumen_erledigt")


def test_fehler_im_hausmeister_zeile_und_naechster_tick_erneut(
    welt: dict[str, Path],  # noqa: F811
    hm: Path,
) -> None:
    _spec_fertig(welt)

    def kaputt(**_: object) -> list[hausmeister.Ergebnis]:
        raise RuntimeError("Platte weg")

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TO_SPAWN_REPO", str(welt["repo"]))
        mp.chdir(welt["repo"])
        mp.setattr(hausmeister, "aufraeumen_alles", kaputt)
        konfig = config.lade(welt["repo"])
        erg = capo.tick(
            welt["repo"], int(SPEC), "test/wegwerf", konfig, wt_basis=str(welt["wt"])
        )
    text = "\n".join(erg.zeilen)
    assert "SPEC FERTIG" in text, text
    assert (
        "FEHLER: Hausmeister — unerwarteter Fehler (RuntimeError: Platte weg)." in text
    ), text
    assert any("Abschluss-Mail" in z for z in erg.zeilen), text  # Tick lief weiter
    assert not _zustand(welt).get("aufraeumen_erledigt")
    zweiter = _capo(welt)
    assert "Hausmeister: 0 gelöscht, 1 bleibt, 0 Fehler" in zweiter.stdout, _text(
        zweiter
    )
    assert _zustand(welt).get("aufraeumen_erledigt")


# --- Prüf-Test: Aufrufer rufen nur die Tür, Regeln kennt nur der Hausmeister ---------------


def _prompt() -> str:
    import importlib.util

    vorlage = json.loads(
        (SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8")
    )["prompt_template"]
    spec = importlib.util.spec_from_file_location(
        "bau_1370", SKILL / "skripte" / "bau.py"
    )
    assert spec and spec.loader
    bau = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bau)
    return bau.build_prompt(vorlage, "7", "9", "T", "- k", konfig={})


def _helfer_quelle() -> str:
    quelle = (SKILL / "to_spawn" / "capo.py").read_text(encoding="utf-8")
    for knoten in ast.parse(quelle).body:
        if isinstance(knoten, ast.FunctionDef) and knoten.name == HELFER:
            return ast.get_source_segment(quelle, knoten) or ""
    raise AssertionError(f"{HELFER} fehlt in capo.py")


def _kettenzeile() -> str:
    zeilen = [
        z
        for z in (SKILL / "SKILL.md").read_text(encoding="utf-8").splitlines()
        if "Kette am Spec-Ende" in z
    ]
    assert len(zeilen) == 1, zeilen
    return zeilen[0]


def test_prompt_ruft_tuer_genau_einmal() -> None:
    assert _prompt().count("to_spawn.py aufraeumen") == 1


def test_kettenzeile_nennt_tuer() -> None:
    assert "aufraeumen_alles" in _kettenzeile()
    assert "aufraeumen_erledigt" in _kettenzeile()


@pytest.mark.parametrize("quelle", ["prompt", "kettenzeile", "helfer"])
def test_aufrufer_ohne_regel_woerter(quelle: str) -> None:
    text = {"prompt": _prompt, "kettenzeile": _kettenzeile, "helfer": _helfer_quelle}[
        quelle
    ]()
    gefunden = [w for w in REGEL_WOERTER if w in text]
    assert gefunden == [], gefunden
