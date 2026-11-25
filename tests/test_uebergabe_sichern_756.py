"""L5 #756 (Windows-Teil): Tilde-Auflösung bei CLI-Argumenten, Maschinen-Pfade und M15-Windows-Beleg.

Prüft, dass direkt übergebene Datei-Argumente (z. B. ``~/ober_aufsicht.md`` unter Windows
ohne Shell-Expansion) über :func:`to_spawn.config.expand_heim` aufgelöst werden, dass
``sicherung.ziel`` aus ``maschine.json`` auch ohne gesetztes ``OBSIDIAN_VAULT`` greift,
und dass der Windows-Kaltstart- sowie Maschinen-Pfad-Beleg (#756) in ``docs/ABNAHME_L1.md``
und ``docs/WIEDEREINSTIEG.md`` festgehalten ist.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import konsole, prozessbaum
from to_spawn import uebergabe_sichern as us

WURZEL = Path(__file__).resolve().parent.parent


@pytest.fixture
def win_welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    home = tmp_path / "home"
    notizen = home / "systemcheck"
    notizen.mkdir(parents=True)
    ober = notizen / "ober_aufsicht.md"
    ober.write_text("Ober-Aufsicht Windows\n", encoding="utf-8")
    entscheidungen = notizen / "entscheidungen.md"
    entscheidungen.write_text("E1 Windows\n", encoding="utf-8")
    tresor = home / "tresor"
    tresor.mkdir(parents=True)
    maschine = tmp_path / "maschine.json"
    maschine.write_text(
        json.dumps(
            {
                "rolle": "pc",
                "repos": [],
                "sicherung": {
                    "ziel": "~/tresor/to-spawn/sicherung",
                    "dateien": [
                        str(ober),
                        str(entscheidungen),
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    monkeypatch.delenv(us.UMGEBUNG_ZIEL, raising=False)
    monkeypatch.setenv(us.UMGEBUNG_ZWEIG_REPOS, "")
    return {
        "tmp": tmp_path,
        "home": home,
        "tresor": tresor,
        "maschine": maschine,
        "ober": ober,
        "entscheidungen": entscheidungen,
    }


def test_sichern_erweitert_tilde_in_direkten_datei_argumenten(win_welt: dict[str, Path]) -> None:
    """Direkt übergebene ``~/…``-Pfade (unter Windows ohne Shell-Expansion) werden gesichert."""
    extra = win_welt["home"] / "notizen" / "pc_handoff.md"
    extra.parent.mkdir(parents=True)
    extra.write_text("Kaltstart PC\n", encoding="utf-8")

    erg = us.sichern([Path("~/notizen/pc_handoff.md")])
    assert erg.fehler == [], erg.fehler
    assert erg.ziel is not None
    assert (erg.ziel / "notizen__pc_handoff.md").read_text(encoding="utf-8") == "Kaltstart PC\n"


def test_cli_sichern_erweitert_tilde_argument_ohne_shell_expansion(win_welt: dict[str, Path]) -> None:
    """``to_spawn.py sichern ~/…`` löst ``~`` im Prozess über ``config.expand_heim`` auf."""
    extra = win_welt["home"] / "notizen" / "cli_handoff.md"
    extra.parent.mkdir(parents=True)
    extra.write_text("CLI PC\n", encoding="utf-8")

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    aus = subprocess.run(
        [sys.executable, str(WURZEL / "to_spawn.py"), "sichern", "~/notizen/cli_handoff.md"],
        cwd=WURZEL,
        env=env,
        capture_output=True,
        text=True,
        **konsole.UNTERPROZESS_TEXT,
        **prozessbaum.fensterlos(),
        timeout=60,
        check=False,
    )
    assert aus.returncode == 0, aus.stderr + aus.stdout
    kopien = list((win_welt["tresor"] / "to-spawn" / "sicherung").glob("*/notizen__cli_handoff.md"))
    assert len(kopien) == 1
    assert kopien[0].read_text(encoding="utf-8") == "CLI PC\n"


def test_pruefen_maschine_ziel_mit_tilde_auch_ohne_obsidian_env(win_welt: dict[str, Path]) -> None:
    """``sicherung.ziel`` mit ``~`` in ``maschine.json`` greift auch ohne ``OBSIDIAN_VAULT`` (z. B. Aufgabenplanung)."""
    erg = us.pruefen()
    assert erg.ok, erg.fehler
    assert erg.ziel_herkunft == "maschine.json"
    assert erg.ziel.is_relative_to(win_welt["tresor"] / "to-spawn" / "sicherung")
    assert erg.ziel_oeffentlich is False
    assert not erg.ziel.exists()  # rein lesend
    assert len(erg.muster) == 2
    assert erg.trefferlose_muster == []


def test_abnahme_m15_und_wiedereinstieg_windows_beleg_756() -> None:
    """#756: M15 belegt Windows-Maschinen-Pfade und Kaltstart-Probe; WIEDEREINSTIEG trägt Windows-Hinweise."""
    abnahme = (WURZEL / "docs" / "ABNAHME_L1.md").read_text(encoding="utf-8")
    m15_zeilen = [z for z in abnahme.splitlines() if z.startswith("| M15 |")]
    assert len(m15_zeilen) == 1
    m15 = m15_zeilen[0]
    assert "#756" in m15
    assert "Windows-PC" in m15
    assert "Windows-Kaltstart-Probe" in m15
    assert "Windows → #756" not in m15

    wiedereinstieg = (WURZEL / "docs" / "WIEDEREINSTIEG.md").read_text(encoding="utf-8")
    assert "Get-Content" in wiedereinstieg
    assert "$env:TEMP\\claude-skill-to-spawn" not in wiedereinstieg
    assert "skill_aktuell: weicht_ab" in wiedereinstieg
