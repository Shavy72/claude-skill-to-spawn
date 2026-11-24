"""#498: Filter „nur Einträge aus dem eigenen Repo oder Worktree“ lebt in ``mensch_noetig``.

Echte ``mensch_noetig.jsonl`` (Env ``REVIEW_MENSCH_NOETIG``), echte Ordner, keine Attrappen.
Verworfene fremde Einträge hinterlassen je eine Log-Zeile (Repo, Ticket).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_capo_mensch_noetig import _schreibe
from test_waechter_213 import SPEC, _capo, _text, welt  # noqa: F401  (welt = Fixture)

from to_spawn import mensch_noetig


def _zeile(repo: str | None, ticket: str | None = "7", spec: str = "3", fp: str = "abcdef01") -> str:
    eintrag: dict[str, object] = {"session_id": "s1", "fingerprint": fp, "spec": spec, "fix_runde": 3}
    if repo is not None:
        eintrag["repo"] = repo
    if ticket is not None:
        eintrag["ticket"] = ticket
    return json.dumps(eintrag)


@pytest.fixture()
def ort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    datei = tmp_path / "review" / "mensch_noetig.jsonl"
    datei.parent.mkdir()
    monkeypatch.setenv("REVIEW_MENSCH_NOETIG", str(datei))
    repo = tmp_path / "repo"
    repo.mkdir()
    wt = tmp_path / "wt"
    (wt / "wt-7").mkdir(parents=True)
    (wt / "wt-3").mkdir()
    return {"datei": datei, "repo": repo, "wt": wt, "tmp": tmp_path}


def _eigene(ort: dict[str, Path]) -> list[dict[str, object]]:
    return mensch_noetig.eintraege_fuer_lauf(3, set(), ort["repo"], lambda n: ort["wt"] / f"wt-{n}")


def test_regel_liegt_in_mensch_noetig_eigen_und_fremd(ort: dict[str, Path]) -> None:
    """Das Modul selbst trennt eigen (Hauptbaum, eigener Worktree) von fremd."""
    fremd = ort["tmp"] / "anderes" / "wt-7"
    _schreibe(
        ort["datei"],
        _zeile(ort["repo"].as_posix(), fp="a1"),
        _zeile((ort["wt"] / "wt-7").as_posix(), fp="a2"),
        _zeile(fremd.as_posix(), fp="a3"),
    )
    assert [e["fingerprint"] for e in _eigene(ort)] == ["a1", "a2"]


def test_ohne_repo_verworfen_mit_log(ort: dict[str, Path], caplog: pytest.LogCaptureFixture) -> None:
    _schreibe(ort["datei"], _zeile(None, ticket="7"))
    with caplog.at_level(logging.INFO, logger="to_spawn.mensch_noetig"):
        assert _eigene(ort) == []
    zeilen = [r.getMessage() for r in caplog.records if "ignoriert" in r.getMessage()]
    assert len(zeilen) == 1, caplog.text
    assert "#7" in zeilen[0] and "ohne repo" in zeilen[0]


def test_pfad_mit_schraegstrich_am_ende_ist_eigen(ort: dict[str, Path]) -> None:
    _schreibe(
        ort["datei"],
        _zeile(ort["repo"].as_posix() + "/", fp="b1"),
        _zeile((ort["wt"] / "wt-7").as_posix() + "/", fp="b2"),
    )
    assert [e["fingerprint"] for e in _eigene(ort)] == ["b1", "b2"]


def test_symlink_auf_repo_ist_eigen(ort: dict[str, Path]) -> None:
    link = ort["tmp"] / "repo-link"
    try:
        os.symlink(ort["repo"], link, target_is_directory=True)
    except (OSError, NotImplementedError) as fehler:
        pytest.skip(f"Symlink nicht anlegbar: {fehler}")
    _schreibe(ort["datei"], _zeile(link.as_posix(), fp="c1"))
    assert [e["fingerprint"] for e in _eigene(ort)] == ["c1"]


def test_ohne_ticket_zaehlt_hauptbaum_und_spec_worktree(ort: dict[str, Path]) -> None:
    """Ohne ``ticket`` gilt die Spec-Nummer: Hauptbaum und ``wt-<Spec>`` eigen, ``wt-7`` nicht."""
    _schreibe(
        ort["datei"],
        _zeile(ort["repo"].as_posix(), ticket=None, fp="d1"),
        _zeile((ort["wt"] / "wt-3").as_posix(), ticket=None, fp="d2"),
        _zeile((ort["wt"] / "wt-7").as_posix(), ticket=None, fp="d3"),
    )
    assert [e["fingerprint"] for e in _eigene(ort)] == ["d1", "d2"]


def test_fremder_eintrag_log_zeile_mit_repo_und_ticket(ort: dict[str, Path], caplog: pytest.LogCaptureFixture) -> None:
    fremd = ort["tmp"] / "anderes" / "wt-7"
    _schreibe(ort["datei"], _zeile(fremd.as_posix(), fp="e1"), _zeile(fremd.as_posix(), ticket="8", fp="e2"))
    with caplog.at_level(logging.INFO, logger="to_spawn.mensch_noetig"):
        assert _eigene(ort) == []
    zeilen = [r.getMessage() for r in caplog.records if "ignoriert" in r.getMessage()]
    assert len(zeilen) == 2, caplog.text
    assert "#7" in zeilen[0] and fremd.as_posix() in zeilen[0]
    assert "#8" in zeilen[1]


def test_capo_verworfener_eintrag_hinterlaesst_log(
    welt: dict[str, Path],  # noqa: F811  (Fixture)
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Weg-Test über den echten Aufseher-Lauf: fremder Eintrag → kein Kommentar, aber eine Log-Zeile."""
    datei = tmp_path / "review-498" / "mensch_noetig.jsonl"
    datei.parent.mkdir()
    monkeypatch.setenv("REVIEW_MENSCH_NOETIG", str(datei))
    fremd = (welt["tmp"] / "anderes-repo" / "wt-902").as_posix()
    _schreibe(datei, _zeile(fremd, ticket="902", spec=SPEC))
    ergebnis = _capo(welt)
    text = _text(ergebnis)
    assert "MENSCH NÖTIG" not in text
    assert "ignoriert" in ergebnis.stderr and "#902" in ergebnis.stderr and fremd in ergebnis.stderr, text


def test_fremder_eintrag_wird_ueber_laeufe_nur_einmal_gemeldet(
    ort: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    """Zustand zwischen Läufen bleibt erhalten: derselbe fremde Eintrag einmal, ein neuer danach noch einmal."""
    fremd = (ort["tmp"] / "anderes" / "wt-7").as_posix()
    _schreibe(ort["datei"], _zeile(fremd, fp="f1"))
    zustand: set[str] = set()

    def lauf() -> list[str]:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="to_spawn.mensch_noetig"):
            mensch_noetig.eintraege_fuer_lauf(
                3, set(), ort["repo"], lambda n: ort["wt"] / f"wt-{n}", fremd=zustand
            )
        return [r.getMessage() for r in caplog.records]

    erster, zweiter = lauf(), lauf()
    assert len(erster) == 1 and not zweiter, (erster, zweiter)
    assert not erster[0].startswith("Mensch nötig"), erster[0]
    _schreibe(ort["datei"], _zeile(fremd, fp="f1"), _zeile(fremd, fp="f2"))
    dritter = lauf()
    assert len(dritter) == 1, dritter


# --- #515: Zustand (Laden/Erkennen/Sichern) liegt in mensch_noetig, echte Zustandsdatei ---

_LAUF = """
import json, sys
from pathlib import Path
from to_spawn import mensch_noetig
datei = Path(sys.argv[1]); repo = Path(sys.argv[2]); wt = Path(sys.argv[3])
zustand = json.loads(datei.read_text()) if datei.exists() else {}
def sichern():
    datei.write_text(json.dumps(zustand))
eigene = mensch_noetig.eintraege_mit_zustand(
    3, zustand, sichern, repo, lambda n: wt / f"wt-{n}"
)
print(json.dumps([e["fingerprint"] for e in eigene]))
"""


def _prozess(ort: dict[str, Path], datei: Path) -> subprocess.CompletedProcess[str]:
    skript = ort["tmp"] / "lauf.py"
    skript.write_text(_LAUF, encoding="utf-8")
    wurzel = Path(__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(wurzel), "REVIEW_MENSCH_NOETIG": str(ort["datei"])}
    return subprocess.run(
        [sys.executable, str(skript), str(datei), str(ort["repo"]), str(ort["wt"])],
        capture_output=True, text=True, env=env, check=True,
    )


def test_zustand_ueber_zwei_prozesse(ort: dict[str, Path]) -> None:
    fremd = ort["tmp"] / "anderes" / "wt-7"
    _schreibe(ort["datei"], _zeile(ort["repo"].as_posix(), fp="a1"), _zeile(fremd.as_posix(), fp="a3"))
    zdatei = ort["tmp"] / "zustand.json"
    assert json.loads(_prozess(ort, zdatei).stdout) == ["a1"]
    assert json.loads(zdatei.read_text())["mensch_noetig_fremd"] == ["s1|a3"]
    assert json.loads(_prozess(ort, zdatei).stdout) == ["a1"]  # eigener Eintrag bleibt (nichts gesehen)


def test_dry_run_schreibt_nichts(ort: dict[str, Path]) -> None:
    _schreibe(ort["datei"], _zeile((ort["tmp"] / "x").as_posix(), fp="a3"))
    zustand: dict[str, object] = {}
    mensch_noetig.eintraege_mit_zustand(3, zustand, None, ort["repo"], lambda n: ort["wt"] / f"wt-{n}")
    assert zustand == {}


@pytest.mark.parametrize("kaputt", ["abc", 5, {"a": 1}, [1, None, "ok"]])
def test_kaputter_zustand_kein_absturz(ort: dict[str, Path], kaputt: object) -> None:
    _schreibe(ort["datei"], _zeile(ort["repo"].as_posix(), fp="a1"))
    zustand: dict[str, object] = {"mensch_noetig_fremd": kaputt}
    eigene = mensch_noetig.eintraege_mit_zustand(3, zustand, lambda: None, ort["repo"], lambda n: ort["wt"] / f"wt-{n}")
    assert [e["fingerprint"] for e in eigene] == ["a1"]


def test_fremd_liste_gekappt_aelteste_zuerst_raus(ort: dict[str, Path]) -> None:
    grenze = mensch_noetig.FREMD_MAX
    alt = [f"alt|{i}" for i in range(grenze)]
    _schreibe(ort["datei"], _zeile((ort["tmp"] / "x").as_posix(), fp="neu"))
    zustand: dict[str, object] = {"mensch_noetig_fremd": alt}
    mensch_noetig.eintraege_mit_zustand(3, zustand, lambda: None, ort["repo"], lambda n: ort["wt"] / f"wt-{n}")
    liste = zustand["mensch_noetig_fremd"]
    assert len(liste) == grenze and liste[-1] == "s1|neu" and "alt|0" not in liste
