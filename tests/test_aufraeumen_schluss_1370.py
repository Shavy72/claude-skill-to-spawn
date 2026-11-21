"""Tests #1370: Aufräumen ist fester Schlussschritt — die Regeln kennt nur der Hausmeister.

Echt laufen: Git-Repo mit Bare-Origin, die CLI ``skripte/capo.py`` (SPEC FERTIG), der echte
Hausmeister über eine Wegwerf-Wurzel und die echte Zustandsdatei. Gestellt ist nur ``gh``
(Ersatz aus #213). Die Exception im Hausmeister ist Rechenlogik-Attrappe (monkeypatch).
"""

from __future__ import annotations

import ast
import json
import subprocess
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


def _spec_fertig(welt: dict[str, Path], *, rueckblick: bool = True) -> None:  # noqa: F811
    """Alle Tickets zu; ``rueckblick=True`` legt Rückblick + Thermo ab, damit die
    Abschluss-Mail im selben Tick erledigt ist (der Hausmeister läuft erst danach)."""
    _commit(welt["repo"], "feat: A (#901)", _beleg("901"))
    _commit(welt["repo"], "feat: B (#902)", _beleg("902"))
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())
    if rueckblick:
        ordner = welt["repo"] / "docs" / "agents"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / f"rueckblick_{SPEC}.md").write_text("Rückblick: keine Vorschläge\n", encoding="utf-8")
        (ordner / f"thermo_{SPEC}.md").write_text("# Thermo\n\n- nichts auffällig\n", encoding="utf-8")


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

    def kaputt(**_: object) -> hausmeister.Rundgang:
        raise RuntimeError("Platte weg")

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TO_SPAWN_REPO", str(welt["repo"]))
        mp.chdir(welt["repo"])
        mp.setattr(hausmeister, "rundgang", kaputt)
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


def _tick(welt: dict[str, Path], **ersatz: object) -> list[str]:  # noqa: F811
    """Ein capo-Tick im Prozess; ``ersatz`` = Attribute des Hausmeisters (nur Nähte)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("TO_SPAWN_REPO", str(welt["repo"]))
        mp.chdir(welt["repo"])
        for name, wert in ersatz.items():
            mp.setattr(hausmeister, name, wert)
        konfig = config.lade(welt["repo"])
        return capo.tick(
            welt["repo"], int(SPEC), "test/wegwerf", konfig, wt_basis=str(welt["wt"])
        ).zeilen


def _sperre_halten(hm: Path):
    fcntl = pytest.importorskip("fcntl")
    sperre = (hm.parent / hausmeister.SPERR_DATEI).open("a", encoding="utf-8")
    fcntl.flock(sperre, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return sperre


def test_sperre_belegt_wartet_ohne_marker_und_ohne_fehlversuch(
    welt: dict[str, Path],  # noqa: F811
    hm: Path,
) -> None:
    _spec_fertig(welt)
    sperre = _sperre_halten(hm)
    try:
        erster = _capo(welt)
    finally:
        sperre.close()
    assert "Hausmeister wartet:" in erster.stdout, _text(erster)
    assert "FEHLER: Hausmeister" not in erster.stdout, _text(erster)
    assert _laeufe(hm) == []
    assert not _zustand(welt).get("aufraeumen_erledigt")
    assert not _zustand(welt).get("aufraeumen_versuche")
    zweiter = _capo(welt)
    assert "Hausmeister: 0 gelöscht, 1 bleibt, 0 Fehler" in zweiter.stdout, _text(zweiter)
    assert _zustand(welt).get("aufraeumen_erledigt")


def test_ordnersuche_scheitert_drei_versuche_dann_aufgegeben(
    welt: dict[str, Path],  # noqa: F811
    hm: Path,
) -> None:
    _spec_fertig(welt)

    def unlesbar() -> list[Path]:
        raise OSError("Wurzel nicht lesbar")

    for versuch in (1, 2):
        text = "\n".join(_tick(welt, _kandidaten=unlesbar))
        assert (
            "FEHLER: Hausmeister — Ordnersuche gescheitert (OSError: Wurzel nicht lesbar)."
            in text
        ), text
        assert "aufgegeben" not in text, text
        assert not _zustand(welt).get("aufraeumen_erledigt")
        assert _zustand(welt).get("aufraeumen_versuche") == versuch
    text = "\n".join(_tick(welt, _kandidaten=unlesbar))
    assert (
        "FEHLER: Hausmeister — nach 3 Versuchen aufgegeben, Netz ist der Aufpasser-Takt"
        in text
    ), text
    assert _zustand(welt).get("aufraeumen_erledigt")
    assert "Hausmeister" not in "\n".join(_tick(welt, _kandidaten=unlesbar))


def test_ordner_mit_fehler_zeile_mit_namen_ohne_marker(
    welt: dict[str, Path],  # noqa: F811
    hm: Path,
) -> None:
    """Echter Fehlerweg: eigener Git-Ordner, GitHub kennt das Ticket nicht (gh Exit 1)."""
    ordner = Path(str(hm.parent / "hm_wurzel" / "wt-903"))
    ordner.mkdir()
    for args in (
        ["init", "-q"],
        ["remote", "add", "origin", "https://github.com/Shavy72/duoplus-management"],
    ):
        subprocess.run(["git", *args], cwd=ordner, check=True, capture_output=True)
    _spec_fertig(welt)
    erster = _capo(welt)
    assert "FEHLER: Hausmeister — 1 Ordner: wt-903" in erster.stdout, _text(erster)
    laeufe = _laeufe(hm)
    assert any(lauf.get("fehler") for lauf in laeufe), laeufe
    assert not _zustand(welt).get("aufraeumen_erledigt")
    assert _zustand(welt).get("aufraeumen_versuche") == 1


def test_hausmeister_erst_nach_abschluss_mail(
    welt: dict[str, Path],  # noqa: F811
    hm: Path,
) -> None:
    _spec_fertig(welt, rueckblick=False)
    erster = _capo(welt)
    assert "SPEC FERTIG" in erster.stdout, _text(erster)
    assert "Abschluss-Mail wartet" in erster.stdout, _text(erster)
    assert "Hausmeister" not in erster.stdout, _text(erster)
    assert _laeufe(hm) == []
    assert not _zustand(welt).get("aufraeumen_erledigt")


def test_rundgang_bei_sperre_nicht_gelaufen(hm: Path) -> None:
    sperre = _sperre_halten(hm)
    try:
        rund = hausmeister.rundgang(loeschen=True)
    finally:
        sperre.close()
    assert rund.gelaufen is False and rund.wartet is True, rund
    assert rund.grund and rund.ergebnisse == () and rund.fehler == 0
    assert hausmeister.aufraeumen_alles(loeschen=False) != []  # frei: läuft wieder


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
