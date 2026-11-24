"""Kein Kommentar-Weg zur Nutzer-Antwort (#278 P3b, Fix-Runde).

Bau-Sessions kommentieren mit dem Konto des Besitzers; ein Weg „Antwort aus Ticket-Kommentar
übernehmen“ wäre also eine Selbst-Freigabe für Klasse 2. Festgeschrieben: (a) ``briefkasten`` hat
keine solche Funktion und keine Kommentar-Form mehr; (b) die CLI kennt ``nutzer-uebertragen``
nicht, der Aufruf endet mit Fehler-Exit und schreibt nichts; (c) eine Klasse-2-Frage bleibt offen,
egal was im Ticket steht — einziger Schreiber von ``von`` = Nutzer ist ``nutzer_antworten``
(CLI ``nutzer-antwort`` im echten Terminal, geprüft in ``test_briefkasten_p3a_278.py``).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))


@pytest.fixture(scope="module")
def briefkasten() -> ModuleType:
    from to_spawn import briefkasten as modul

    return modul


@pytest.fixture(autouse=True)
def _ohne_bau_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)


def _cli(repo: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TO_SPAWN_")}
    env["TO_SPAWN_REPO"] = str(repo)
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), *argv],
        cwd=repo,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_briefkasten_ohne_kommentar_uebernahme(briefkasten: ModuleType) -> None:
    assert not hasattr(briefkasten, "nutzer_antwort_uebertragen")
    assert not hasattr(briefkasten, "NUTZER_KOMMENTAR")
    oeffentlich = [n for n in dir(briefkasten) if not n.startswith("_") and callable(getattr(briefkasten, n))]
    assert not [n for n in oeffentlich if "uebertrag" in n or "kommentar" in n.lower()]


def test_cli_kennt_nutzer_uebertragen_nicht(briefkasten: ModuleType, tmp_path: Path) -> None:
    from to_spawn import briefkasten_cli

    assert "nutzer-uebertragen" not in briefkasten_cli.BEFEHLE
    fid = briefkasten.einwerfen("278", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    vorher = briefkasten.datei_relativ("278")
    inhalt = (tmp_path / vorher).read_bytes()
    lauf = _cli(
        tmp_path,
        "nutzer-uebertragen",
        "278",
        "--id",
        fid,
        "--kommentar",
        "https://github.com/Shavy72/claude-skill-to-spawn/issues/278#issuecomment-1",
    )
    assert lauf.returncode != 0
    assert (tmp_path / vorher).read_bytes() == inhalt
    assert briefkasten.lesen("278", fid, hauptbaum=tmp_path) == briefkasten.NUTZER


def test_klasse_2_bleibt_offen_nur_nutzer_antworten_schreibt_nutzer(briefkasten: ModuleType, tmp_path: Path) -> None:
    fid = briefkasten.einwerfen("278", "Live schalten?", "nein", 2, [], hauptbaum=tmp_path)
    with pytest.raises(briefkasten.Verweigert):
        briefkasten.antworten("278", fid, "ja", [], "Kommentar im Ticket sagt ja", "aufseher", hauptbaum=tmp_path)
    assert briefkasten.lesen("278", fid, hauptbaum=tmp_path) == briefkasten.NUTZER
    assert [o["klasse"] for o in briefkasten.offene(["278"], hauptbaum=tmp_path)] == ["2"]
    baum = ast.parse((SKILL / "to_spawn" / "briefkasten.py").read_text(encoding="utf-8"))
    schreiber = [
        f.name
        for f in baum.body
        if isinstance(f, ast.FunctionDef)
        for k in ast.walk(f)
        if isinstance(k, ast.Call)
        and getattr(k.func, "id", "") == "_schreibe_antwort"
        and any(isinstance(a, ast.Name) and a.id == "NUTZER_VON" for a in k.args)
    ]
    assert schreiber == ["nutzer_antworten"]


def test_aufseher_text_ohne_kommentar_weg_mit_fertigem_befehl() -> None:
    from to_spawn import befugnis

    text = befugnis.aufseher_text("python X/to_spawn.py")
    assert "uebertragen" not in text and "Antwort <id>:" not in text
    assert "dem Nutzer melden, mit fertigem Befehl" in text
    assert "python X/to_spawn.py nutzer-antwort <N> --id <id> --wahl" in text
