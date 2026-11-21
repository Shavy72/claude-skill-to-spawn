"""Hausmeister #1525: Review-Belege, Kompilate und ``data/.secret_key`` sind Erzeugnisse.

Fertige Arbeitsordner blieben stehen, weil ``.review/**`` (Belege des Review-Werkzeugs) und
``data/.secret_key`` (legt die App selbst neu an) nicht als Erzeugnis galten und das Kompilat
``__pycache__/tempo_zugang.*.pyc`` über das Muster ``**/*zugang*`` als Geheimnis zählte.
Weg-Tests mit echtem Wegwerf-Git (Fixture ``welt`` aus #1368).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

from pathlib import Path

from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    Welt,
    _commit,
    welt,
)


def _erzeugnisse_anlegen(wt: Path) -> None:
    _commit(wt, "scripts/tempo_zugang.py", "print('zugang')\n")
    _commit(wt, ".gitignore", "__pycache__/\ndata/.secret_key\n*.env\n", push=True)
    dateien = {
        ".review/.gitignore": "*\n",
        ".review/beleg-x.json": "{}\n",
        "scripts/__pycache__/tempo_zugang.cpython-311.pyc": "kompilat",
        "data/.secret_key": "flask-schluessel",
    }
    for rel, inhalt in dateien.items():
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_text(inhalt, encoding="utf-8")


def test_review_pyc_secret_key_sind_erzeugnisse(welt: Welt) -> None:
    wt = welt.worktree(1525)
    _erzeugnisse_anlegen(wt)
    welt.ticket(1525, "CLOSED", 48)
    r = welt.aufraeumen(wt)
    assert r.art == "gelöscht", r.grund


def test_echte_zugang_env_bleibt(welt: Welt) -> None:
    wt = welt.worktree(1527)
    _erzeugnisse_anlegen(wt)
    (wt / "scripts" / "x_zugang.env").write_text("TOKEN=1\n", encoding="utf-8")
    welt.ticket(1527, "CLOSED", 48)
    r = welt.aufraeumen(wt, loeschen=True)
    assert r.art == "bleibt" and r.fuer_david, r.grund
    assert "scripts/x_zugang.env" in r.grund
    assert (wt / "scripts" / "x_zugang.env").is_file()
