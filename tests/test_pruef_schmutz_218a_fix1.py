"""#218 Fix-Runde 1: Die Ausnahme für ungetrackte Werkzeug-Reste darf nicht zu breit sein.

Abnahme M12: Die schnelle Auswahl meldet nie grün, wo der volle Lauf rot wäre. Eine unbekannte
Datei unter ``.to-spawn/`` oder eine Nicht-``.pyc``-Datei in einem ``__pycache__/``-Ordner kann
ein Test lesen — sie zählt als Schmutz und erzwingt ``voll``.
"""

from __future__ import annotations

from pathlib import Path

from test_pruef_schmutz_218 import _code_commit, _pruef_stufe, repo  # noqa: F401 — ``repo`` ist ein Fixture

from to_spawn.pruef import cache


def test_h1_unbekannte_datei_unter_to_spawn_erzwingt_voll(repo: Path) -> None:  # noqa: F811
    basis = _code_commit(repo)
    (repo / ".to-spawn" / "daten.json").write_text('"rot"\n', encoding="utf-8")
    stufe = _pruef_stufe(repo, basis)
    assert stufe.name == "voll", stufe.grund
    assert stufe.grund.startswith("AB"), stufe.grund


def test_h2_fremde_datei_im_pycache_erzwingt_voll(repo: Path) -> None:  # noqa: F811
    basis = _code_commit(repo)
    (repo / "app" / "__pycache__").mkdir()
    (repo / "app" / "__pycache__" / "d.txt").write_text("rot\n", encoding="utf-8")
    stufe = _pruef_stufe(repo, basis)
    assert stufe.name == "voll", stufe.grund
    assert stufe.grund.startswith("AB"), stufe.grund


def test_laufdateien_bleiben_ausgenommen(repo: Path) -> None:  # noqa: F811
    basis = _code_commit(repo)
    for pfad in ("sessions/5.json", "stop-7", "bau_log/7.jsonl", "pruef/protokoll.jsonl"):
        ziel = repo / ".to-spawn" / pfad
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text("{}\n", encoding="utf-8")
    (repo / "app" / "__pycache__").mkdir()
    (repo / "app" / "__pycache__" / "werk.cpython-311.pyc").write_bytes(b"\0")
    stufe = _pruef_stufe(repo, basis)
    assert stufe.name == "auswahl", stufe.grund


def test_ist_werkzeug_rest_grenzen(tmp_path: Path) -> None:
    rest = cache.ist_werkzeug_rest
    (tmp_path / "a" / "__pycache__").mkdir(parents=True)
    (tmp_path / "a" / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"\0")
    (tmp_path / "b" / "__pycache__").mkdir(parents=True)
    (tmp_path / "b" / "__pycache__" / "d.txt").write_text("rot\n", encoding="utf-8")
    assert rest(tmp_path, ".to-spawn/sessions/5.json")
    assert rest(tmp_path, ".to-spawn/sessions/")
    assert rest(tmp_path, ".to-spawn/stop-12")
    assert not rest(tmp_path, ".to-spawn/daten.json")
    assert not rest(tmp_path, ".to-spawn/config.json")
    assert rest(tmp_path, "a/__pycache__/")
    assert rest(tmp_path, "a/__pycache__/x.cpython-311.pyc")
    assert not rest(tmp_path, "b/__pycache__/")
    assert not rest(tmp_path, "b/__pycache__/d.txt")
    assert rest(tmp_path, ".pytest_cache/")
    assert rest(tmp_path, ".venv/")
    assert not rest(tmp_path, "src/venv/")
    assert not rest(tmp_path, "app/neu.py")
