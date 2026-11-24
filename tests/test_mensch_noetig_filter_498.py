"""#498: Filter „nur Einträge aus dem eigenen Repo oder Worktree“ lebt in ``mensch_noetig``.

Echte ``mensch_noetig.jsonl`` (Env ``REVIEW_MENSCH_NOETIG``), echte Ordner, keine Attrappen.
Verworfene fremde Einträge hinterlassen je eine Log-Zeile (Repo, Ticket).
"""

from __future__ import annotations

import json
import logging
import os
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
    zeilen = [r.getMessage() for r in caplog.records if "verworfen" in r.getMessage()]
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
    zeilen = [r.getMessage() for r in caplog.records if "verworfen" in r.getMessage()]
    assert len(zeilen) == 2, caplog.text
    assert "#7" in zeilen[0] and fremd.as_posix() in zeilen[0]
    assert "#8" in zeilen[1]


def test_capo_verworfener_eintrag_hinterlaesst_log(
    welt: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
    assert "verworfen" in ergebnis.stderr and "#902" in ergebnis.stderr and fremd in ergebnis.stderr, text
