"""Tests für #553 (Gruppe E) — „ohne Terminal“ heißt auch unter Windows ohne Terminal.

Anlass: Unter Windows meldet ``isatty()`` das Gerät ``NUL`` (``< /dev/null``, ``stdin=DEVNULL``) als
Terminal. Jede Entscheidung „nur ein Mensch im Terminal“ ging damit auf. Die Prüfung lebt nur in
``konsole.stdin_ist_terminal``; der Wächter-Test liest den Quelltext, damit kein neuer
``isatty()``-Aufruf an ihr vorbei entscheidet.

Echt laufen: die CLI (``to_spawn.py nest rechte --eintragen``, ``to_spawn.py setup``) mit
stdin = ``NUL``/``/dev/null``. Gestellt ist nur das Home-Verzeichnis (Wegwerf-Ordner).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import konsole

WURZEL = Path(__file__).resolve().parent.parent
CLI = WURZEL / "to_spawn.py"

#: Wer außer der einen Prüf-Stelle ``isatty()`` aufrufen darf, und warum.
ERLAUBT = {
    ("to_spawn/konsole.py", "stdin_ist_terminal"): "die eine Prüf-Stelle (#553)",
    ("to_spawn/nest.py", "lauf"): "``sys.stdout.isatty()`` nur zusätzlich zur Prüf-Stelle, macht die Sperre strenger",
}
#: Ordner, die der Prüf-Test nicht liest (Tests stellen Terminals nach, der Rest ist kein Quelltext).
AUSGENOMMEN = {"tests", ".git", ".venv", "venv", "__pycache__", ".pytest_cache", "archive"}


def _lauf(*args: str, heim: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    """CLI mit stdin und stdout = NUL: unter Windows hält ``isatty()`` beide für ein Terminal."""
    umgebung = {**os.environ, "HOME": str(heim), "USERPROFILE": str(heim)}
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=cwd,
        env=umgebung,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_stdin_nul_ist_kein_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    with open(os.devnull, encoding="utf-8") as nul:
        monkeypatch.setattr(sys, "stdin", nul)
        assert konsole.stdin_ist_terminal() is False


def test_stdin_none_ist_kein_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", None)
    assert konsole.stdin_ist_terminal() is False


def test_rechte_eintragen_ohne_terminal_verweigert(tmp_path: Path) -> None:
    lauf = _lauf("nest", "rechte", "--eintragen", heim=tmp_path, cwd=WURZEL)
    assert lauf.returncode == 3, lauf.stderr
    assert "nur ein Mensch" in lauf.stderr
    assert not (tmp_path / ".claude" / "settings.json").exists()


def test_setup_ohne_terminal_kein_dialog(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    lauf = _lauf("setup", heim=tmp_path, cwd=repo)
    assert lauf.returncode == 2, lauf.stderr
    assert "Kein Terminal" in lauf.stderr
    assert not (repo / ".to-spawn" / "config.json").exists()


def _isatty_aufrufe(baum: ast.Module) -> list[tuple[str, int]]:
    """``(Funktion, Zeile)`` je ``….isatty()``-Aufruf; Funktion = innerste umschließende ``def``."""
    funde: list[tuple[str, int]] = []

    def besuche(knoten: ast.AST, funktion: str) -> None:
        for kind in ast.iter_child_nodes(knoten):
            name = kind.name if isinstance(kind, (ast.FunctionDef, ast.AsyncFunctionDef)) else funktion
            if isinstance(kind, ast.Call) and isinstance(kind.func, ast.Attribute) and kind.func.attr == "isatty":
                funde.append((name, kind.lineno))
            besuche(kind, name)

    besuche(baum, "")
    return funde


def test_isatty_nur_an_der_einen_pruef_stelle() -> None:
    fremde: list[str] = []
    gesehen: set[tuple[str, str]] = set()
    for datei in sorted(WURZEL.rglob("*.py")):
        rel = datei.relative_to(WURZEL)
        if AUSGENOMMEN & set(rel.parts):
            continue
        for funktion, zeile in _isatty_aufrufe(ast.parse(datei.read_text(encoding="utf-8"))):
            schluessel = (rel.as_posix(), funktion)
            if schluessel in ERLAUBT:
                gesehen.add(schluessel)
            else:
                fremde.append(f"{rel.as_posix()}:{zeile} ({funktion or 'Modul'})")
    assert not fremde, "isatty() am Prüf-Modul vorbei — konsole.stdin_ist_terminal() nutzen: " + ", ".join(fremde)
    assert gesehen == set(ERLAUBT), f"Erlaubt-Liste veraltet: {set(ERLAUBT) - gesehen}"
