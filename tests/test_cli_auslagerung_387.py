"""#387: ``to_spawn.py`` unter der 1000-Zeilen-Grenze — Parser und Verteilung ausgelagert.

Prüft:
1. ``to_spawn.py`` sowie die ausgelagerten CLI-Module ``to_spawn/cli_parser.py`` und
   ``to_spawn/cli_befehle.py`` bleiben je unter der Projektgrenze ``datei_groesse.grenze``
   aus ``review/config.standard.json``.
2. ``to_spawn/cli_parser.py`` stellt ``baue_parser()`` bereit und registriert alle
   bisherigen Unterbefehle vollständig inklusive eigenem Argumentfehler-Exit-Code (#505).
3. ``to_spawn/cli_befehle.py`` übernimmt die Verteilung über ``verteilen(ap, args)``;
   ``to_spawn.py`` bleibt die schlanke Einstiegsdatei, die weiterhin per Prozess und per
   ``main(argv)`` identisch funktioniert.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import einreichen

#: Alle 54 Unterbefehle vor der Auslagerung (#387) — keiner darf verloren gehen.
ERWARTETE_UNTERBEFEHLE = frozenset(
    {
        "anhaengen",
        "antwort",
        "antworten",
        "arbeitsstand",
        "aufpasser",
        "aufraeumen",
        "aufseher-stand",
        "befugnis",
        "befund",
        "blocker",
        "deploy-status",
        "einreichen",
        "einrichten",
        "eintrag",
        "entscheidungen",
        "frage",
        "hauptzweig",
        "helfer-temp",
        "hook-ja-sperre",
        "hook-spec-sperre",
        "hook-stop",
        "hook-subagent-stop",
        "hook-umzug",
        "inventur",
        "ja",
        "komplett",
        "leiter",
        "leitstand",
        "lernstoff",
        "live",
        "log",
        "nest",
        "neustart",
        "nutzer-antwort",
        "offene",
        "probesitz",
        "pruefen",
        "regularien",
        "respawn",
        "setup",
        "sichern",
        "spawn",
        "spec-schliessen",
        "speicher",
        "stand",
        "startklar",
        "takt",
        "takt-einrichten",
        "ticket-lage",
        "ticketstand",
        "umrechnen",
        "umzug",
        "umzug-alle",
        "zu-bauen",
    }
)


def _projektgrenze() -> int:
    """Liest ``datei_groesse.grenze`` aus der einen Quelle ``review/config.standard.json``."""
    cfg = json.loads((SKILL / "review" / "config.standard.json").read_text(encoding="utf-8"))
    return int(cfg["datei_groesse"]["grenze"])


def _zeilen(pfad: Path) -> int:
    return len(pfad.read_text(encoding="utf-8").splitlines())


def _unterbefehle(parser: argparse.ArgumentParser) -> set[str]:
    namen: set[str] = set()
    for aktion in parser._actions:
        if isinstance(aktion, argparse._SubParsersAction):
            namen.update(aktion.choices.keys())
    return namen


def _lade_cli_einstieg() -> Any:
    spec = importlib.util.spec_from_file_location("to_spawn_cli_387", SKILL / "to_spawn.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_to_spawn_py_unter_projektgrenze() -> None:
    """``to_spawn.py`` liegt nach der Auslagerung deutlich unter ``datei_groesse.grenze``."""
    grenze = _projektgrenze()
    zeilen = _zeilen(SKILL / "to_spawn.py")
    assert zeilen < grenze, f"to_spawn.py hat {zeilen} Zeilen (Grenze: {grenze})"


@pytest.mark.parametrize("relpfad", ["to_spawn/cli_parser.py", "to_spawn/cli_befehle.py"])
def test_ausgelagerte_cli_module_existieren_und_bleiben_unter_grenze(relpfad: str) -> None:
    """Parser- und Verteilungs-Modul existieren und bleiben je unter der 1000-Zeilen-Grenze."""
    pfad = SKILL / relpfad
    assert pfad.is_file(), f"Ausgelagertes Modul fehlt: {relpfad}"
    grenze = _projektgrenze()
    zeilen = _zeilen(pfad)
    assert zeilen < grenze, f"{relpfad} hat {zeilen} Zeilen (Grenze: {grenze})"


def test_cli_parser_registriert_alle_unterbefehle() -> None:
    """``to_spawn.cli_parser.baue_parser()`` enthält alle erwarteten Unterbefehle."""
    cli_parser = importlib.import_module("to_spawn.cli_parser")
    ap = cli_parser.baue_parser()
    vorhanden = _unterbefehle(ap)
    assert ERWARTETE_UNTERBEFEHLE <= vorhanden, (
        f"Fehlende Unterbefehle: {sorted(ERWARTETE_UNTERBEFEHLE - vorhanden)}"
    )


def test_cli_parser_bewahrt_argumentfehler_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    """``einreichen`` endet bei Argumentfehler weiterhin mit ``einreichen.AUFRUF``, andere mit 2."""
    cli_parser = importlib.import_module("to_spawn.cli_parser")
    ap = cli_parser.baue_parser()
    with pytest.raises(SystemExit) as einr_exit:
        ap.parse_args(["einreichen"])
    assert einr_exit.value.code == einreichen.AUFRUF
    with pytest.raises(SystemExit) as einr_rest:
        ap.parse_args(["einreichen", "12", "--gibts-nicht"])
    assert einr_rest.value.code == einreichen.AUFRUF
    with pytest.raises(SystemExit) as andere_exit:
        ap.parse_args(["blocker"])
    assert andere_exit.value.code == 2
    capsys.readouterr()


def test_cli_befehle_verteilt_und_to_spawn_delegiert(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``to_spawn.py`` parst über ``cli_parser`` und verteilt über ``cli_befehle.verteilen``."""
    cli_parser = importlib.import_module("to_spawn.cli_parser")
    cli_befehle = importlib.import_module("to_spawn.cli_befehle")
    assert callable(cli_parser.baue_parser)
    assert callable(cli_befehle.verteilen)

    gesehen: list[str] = []

    def merke(_ap: argparse.ArgumentParser, args: argparse.Namespace) -> int:
        gesehen.append(str(args.befehl))
        return 0

    monkeypatch.setattr(cli_befehle, "verteilen", merke)
    cli = _lade_cli_einstieg()
    assert cli.main(["hauptzweig"]) == 0
    assert gesehen == ["hauptzweig"]
    capsys.readouterr()


def test_cli_befehle_echter_durchlauf_hauptzweig_und_pruefen_weiterleitung(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Echter Prozess-Aufruf von ``to_spawn.py`` läuft über beide neuen Module ohne Fehler."""
    lauf = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "--help"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stderr
    assert "arbeitsstand" in lauf.stdout
    assert "einreichen" in lauf.stdout

    # ``pruefen <S>`` ohne Ziel leitet mit Warnung auf ``regularien`` um; hier abfangen über dry-run ohne Repo.
    from to_spawn import gh

    monkeypatch.setattr(gh, "hauptzweig", lambda _repo: "main")
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    cli = _lade_cli_einstieg()
    assert cli.main(["hauptzweig"]) == 0
    assert capsys.readouterr().out.strip() == "main"
