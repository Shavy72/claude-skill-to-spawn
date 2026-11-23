"""#282 P1: Baustein ``to_spawn.wiederanlauf`` — welche Lage hat ein Ticket beim erneuten Start?

Weg-Tests mit echtem git (bare ``origin`` + Klon, Zweig ``ticket/7-x``, Commit ``… (#7)``,
Belegseite ``docs/verify-hard/7.md``); gestellt ist nur ``gh`` (externer Dienst,
``tests/hilfen/gh_stub.py``) für das Issue-JSON. Dazu die reine Regel ``entscheide`` und die
Lese-Tür ``to_spawn.py ticket-lage <N>``.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parents[1]
STUB = SKILL / "tests" / "hilfen" / "gh_stub.py"

MIT_KLICKWEG = "# Beleg #7\n\nKlickweg live: Seite geöffnet, Knopf gedrückt, Ergebnis sichtbar.\n\n12 passed in 0.4s\n"
OHNE_KLICKWEG = "# Beleg #7\n\n12 passed in 0.4s\n"


@pytest.fixture
def wa() -> ModuleType:
    if str(SKILL) not in sys.path:
        sys.path.insert(0, str(SKILL))
    return importlib.import_module("to_spawn.wiederanlauf")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, encoding="utf-8").stdout


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    klon = tmp_path / "klon"
    _git(tmp_path, "clone", "-q", str(origin), str(klon))
    for schluessel, wert in (("user.email", "t@example.invalid"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _git(klon, "config", schluessel, wert)
    (klon / "README.md").write_text("x\n", encoding="utf-8")
    _git(klon, "add", "README.md")
    _git(klon, "commit", "-q", "-m", "chore: Anfang")
    _git(klon, "push", "-q", "origin", "main")
    daten = tmp_path / "gh_daten.json"
    daten.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(STUB))
    monkeypatch.setenv("GH_STUB_DATEN", str(daten))
    for name in ("GH_STUB_ZU", "GH_STUB_KAPUTT"):
        monkeypatch.delenv(name, raising=False)
    return {"klon": klon, "daten": daten}


def _issue(welt: dict[str, Path], **felder: object) -> None:
    welt["daten"].write_text(json.dumps({"7": felder}), encoding="utf-8")


def _arbeit(klon: Path) -> None:
    """Zweig ``ticket/7-x`` mit einem Commit ``… (#7)`` auf origin."""
    _git(klon, "checkout", "-q", "-b", "ticket/7-x")
    (klon / "a.txt").write_text("a\n", encoding="utf-8")
    _git(klon, "add", "a.txt")
    _git(klon, "commit", "-q", "-m", "feat: erster Schritt (#7)")
    _git(klon, "push", "-q", "-u", "origin", "ticket/7-x")
    _git(klon, "checkout", "-q", "main")


def _beleg(klon: Path, text: str) -> None:
    ordner = klon / "docs" / "verify-hard"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "7.md").write_text(text, encoding="utf-8")


# --- Weg-Tests: alle vier Arten + fail-closed -------------------------------------------


def test_neu_ohne_spur(wa: ModuleType, welt: dict[str, Path]) -> None:
    lage = wa.feststellen(7, welt["klon"])
    assert lage is not None
    assert lage.art == "neu"
    assert lage.fehlt == ()
    assert "#7" in lage.hinweis


def test_fortsetzen_mit_zweig_und_commit(wa: ModuleType, welt: dict[str, Path]) -> None:
    _arbeit(welt["klon"])
    _issue(welt, assignees=["bot-a"])
    lage = wa.feststellen(7, welt["klon"])
    assert lage is not None
    assert lage.art == "fortsetzen"
    assert "ticket/7-x" in lage.hinweis
    assert "bot-a" in lage.hinweis


def test_fortsetzen_nur_commit_ohne_zweig(wa: ModuleType, welt: dict[str, Path]) -> None:
    klon = welt["klon"]
    (klon / "b.txt").write_text("b\n", encoding="utf-8")
    _git(klon, "add", "b.txt")
    _git(klon, "commit", "-q", "-m", "fix: lokal angefangen (#7)")
    lage = wa.feststellen(7, klon)
    assert lage is not None and lage.art == "fortsetzen"


def test_fremde_nummer_ist_keine_spur(wa: ModuleType, welt: dict[str, Path]) -> None:
    klon = welt["klon"]
    (klon / "c.txt").write_text("c\n", encoding="utf-8")
    _git(klon, "add", "c.txt")
    _git(klon, "commit", "-q", "-m", "fix: anderes Ticket (#77)")
    _git(klon, "push", "-q", "origin", "main:ticket/77-y")
    lage = wa.feststellen(7, klon)
    assert lage is not None and lage.art == "neu"


def test_abschluss_fertig_gebaut_ohne_klickweg(wa: ModuleType, welt: dict[str, Path]) -> None:
    _arbeit(welt["klon"])
    _beleg(welt["klon"], OHNE_KLICKWEG)
    _issue(welt, labels=["staging:fertig"])
    lage = wa.feststellen(7, welt["klon"])
    assert lage is not None
    assert lage.art == "abschluss"
    assert lage.fehlt == ("Klickweg-Beleg",)
    assert "Klickweg-Beleg" in lage.hinweis


def test_nichts_fertig_gebaut_mit_klickweg(wa: ModuleType, welt: dict[str, Path]) -> None:
    _arbeit(welt["klon"])
    _beleg(welt["klon"], MIT_KLICKWEG)
    _issue(welt, labels=["staging:fertig"])
    lage = wa.feststellen(7, welt["klon"])
    assert lage is not None
    assert lage.art == "nichts"
    assert lage.fehlt == ()
    assert "nichts zu tun" in lage.hinweis


def test_nichts_ticket_zu_auch_ohne_belege(
    wa: ModuleType, welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_STUB_ZU", "7")
    lage = wa.feststellen(7, welt["klon"])
    assert lage is not None and lage.art == "nichts" and lage.fehlt == ()


def test_unbekannt_fail_closed(wa: ModuleType, welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_STUB_KAPUTT", "7")
    assert wa.feststellen(7, welt["klon"]) is None


# --- reine Regel ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ticket", "zweige", "commits", "fehlt", "art"),
    [
        ({"state": "OPEN"}, (), 0, ("Klickweg-Beleg",), "neu"),
        ({"state": "OPEN"}, ("ticket/7-x",), 0, (), "fortsetzen"),
        ({"state": "OPEN"}, (), 2, (), "fortsetzen"),
        ({"state": "OPEN", "labels": ["staging:fertig"]}, (), 0, (), "nichts"),
        ({"state": "OPEN", "labels": ["staging:fertig"]}, (), 0, ("Klickweg-Beleg",), "abschluss"),
        ({"state": "CLOSED"}, (), 0, ("Klickweg-Beleg",), "nichts"),
        ({"state": "CLOSED", "stateReason": "NOT_PLANNED"}, (), 0, ("grüner Testlauf",), "nichts"),
    ],
)
def test_entscheide(
    wa: ModuleType, ticket: dict, zweige: tuple[str, ...], commits: int, fehlt: tuple[str, ...], art: str
) -> None:
    lage = wa.entscheide(wa.Signale(nummer=7, ticket=ticket, zweige=zweige, commits=commits, assignees=(), fehlt=fehlt))
    assert lage.art == art
    assert lage.fehlt == (fehlt if art == "abschluss" else ())
    assert lage.hinweis.strip()


# --- Lese-Tür ------------------------------------------------------------------------------


def _tuer(klon: Path) -> subprocess.CompletedProcess[str]:
    umgebung = {**os.environ, "TO_SPAWN_REPO": str(klon)}
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "ticket-lage", "7"],
        cwd=klon,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=umgebung,
        check=False,
    )


def test_tuer_eine_zeile_exit_0(wa: ModuleType, welt: dict[str, Path]) -> None:
    _arbeit(welt["klon"])
    _beleg(welt["klon"], OHNE_KLICKWEG)
    _issue(welt, labels=["staging:fertig"])
    ergebnis = _tuer(welt["klon"])
    assert ergebnis.returncode == 0, ergebnis.stderr
    zeilen = ergebnis.stdout.strip().splitlines()
    assert len(zeilen) == 1
    assert zeilen[0].startswith("abschluss") and "Klickweg-Beleg" in zeilen[0]


def test_tuer_unbekannt_exit_2(wa: ModuleType, welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_STUB_KAPUTT", "7")
    ergebnis = _tuer(welt["klon"])
    assert ergebnis.returncode == 2
    assert len(ergebnis.stdout.strip().splitlines()) == 1
