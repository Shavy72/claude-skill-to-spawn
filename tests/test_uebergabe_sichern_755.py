"""L5 #755 (Linux-Teil): Prüfung der Maschinen-Pfade (``sicherung.dateien`` / ``sicherung.ziel``).

Prüft :func:`to_spawn.uebergabe_sichern.pruefen` und den CLI-Schalter ``to_spawn.py sichern --pruefen``
rein lesend in ``tmp_path`` (über ``TO_SPAWN_MASCHINE``) sowie die aktualisierten Belege in
``docs/WIEDEREINSTIEG.md`` und ``docs/ABNAHME_L1.md``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import konsole
from to_spawn import uebergabe_sichern as us

WURZEL = Path(__file__).resolve().parent.parent


@pytest.fixture
def pruef_welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    home = tmp_path / "home"
    handoff = home / "leit" / ".claude-handoff-loop.md"
    handoff.parent.mkdir(parents=True)
    handoff.write_text("Übergabe\n", encoding="utf-8")
    entscheidungen = home / "systemcheck" / "entscheidungen.md"
    entscheidungen.parent.mkdir(parents=True)
    entscheidungen.write_text("E1 Grund\n", encoding="utf-8")
    tresor = home / "obsidian"
    tresor.mkdir(parents=True)
    maschine = tmp_path / "maschine.json"
    maschine.write_text(
        json.dumps(
            {
                "repos": [],
                "sicherung": {
                    "dateien": [
                        str(handoff),
                        "~/systemcheck/entscheidungen.md",
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    monkeypatch.setenv("OBSIDIAN_VAULT", str(tresor))
    monkeypatch.delenv(us.UMGEBUNG_ZIEL, raising=False)
    monkeypatch.setenv(us.UMGEBUNG_ZWEIG_REPOS, "")
    return {
        "tmp": tmp_path,
        "home": home,
        "tresor": tresor,
        "maschine": maschine,
        "handoff": handoff,
        "entscheidungen": entscheidungen,
    }


def test_pruefen_gruen_mit_notiz_tresor_und_lesend(pruef_welt: dict[str, Path]) -> None:
    """Gültige ``sicherung.dateien`` + Vorgabe Notiz-Tresor: ok, legt keinen Ziel-Ordner an."""
    erg = us.pruefen()
    assert erg.ok, erg.fehler
    assert erg.fehler == []
    assert erg.maschine_vorhanden is True
    assert erg.ziel_herkunft == "notiz-tresor"
    assert erg.ziel.is_relative_to(pruef_welt["tresor"])
    assert erg.ziel_oeffentlich is False
    assert erg.ziel_vorhanden is False
    assert not erg.ziel.exists()  # rein lesend
    assert erg.trefferlose_muster == []
    assert len(erg.muster) == 2
    assert {p.resolve() for p in erg.quellen} == {
        pruef_welt["handoff"].resolve(),
        pruef_welt["entscheidungen"].resolve(),
    }
    bericht = erg.bericht()
    assert "OK" in bericht
    assert "notiz-tresor" in bericht
    assert "2 Muster" in bericht


def test_pruefen_meldet_trefferlose_sicherung_dateien(pruef_welt: dict[str, Path]) -> None:
    """Ein fehlender Pfad oder Tippfehler in ``sicherung.dateien`` schlägt laut an."""
    fehlt = str(pruef_welt["home"] / "gibt-es-nicht" / "entscheidungen.md")
    pruef_welt["maschine"].write_text(
        json.dumps(
            {
                "repos": [],
                "sicherung": {"dateien": [str(pruef_welt["handoff"]), fehlt]},
            }
        ),
        encoding="utf-8",
    )
    erg = us.pruefen()
    assert not erg.ok
    assert erg.trefferlose_muster == [fehlt]
    assert any(fehlt in f for f in erg.fehler), erg.fehler
    assert "LÜCKE" in erg.bericht()


def test_pruefen_erkennt_explizites_ziel_und_sperrt_oeffentliches_ziel(
    pruef_welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``sicherung.ziel`` aus ``maschine.json`` wird erkannt; öffentliches Ziel wird verweigert."""
    privat = pruef_welt["tmp"] / "privat-ziel"
    pruef_welt["maschine"].write_text(
        json.dumps(
            {
                "repos": [],
                "sicherung": {
                    "ziel": str(privat),
                    "dateien": [str(pruef_welt["handoff"])],
                },
            }
        ),
        encoding="utf-8",
    )
    erg_ok = us.pruefen()
    assert erg_ok.ok, erg_ok.fehler
    assert erg_ok.ziel_herkunft == "maschine.json"
    assert erg_ok.ziel.parent == privat
    assert not privat.exists()

    skill = pruef_welt["tmp"] / "skill"
    monkeypatch.setattr(us, "_skill_wurzel", lambda: skill)
    pruef_welt["maschine"].write_text(
        json.dumps(
            {
                "repos": [],
                "sicherung": {
                    "ziel": str(skill / "docs" / "sicherung"),
                    "dateien": [str(pruef_welt["handoff"])],
                },
            }
        ),
        encoding="utf-8",
    )
    erg_rot = us.pruefen()
    assert not erg_rot.ok
    assert erg_rot.ziel_oeffentlich is True
    assert any("öffentlichen Repo" in f for f in erg_rot.fehler), erg_rot.fehler


def test_pruefen_rueckfall_ohne_tresor_und_ohne_sicherung_dateien(
    pruef_welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Tresor greift die Vorgabe unter ``~/.local/share``; fehlende ``sicherung.dateien`` geben Hinweis."""
    monkeypatch.setenv("OBSIDIAN_VAULT", str(pruef_welt["tmp"] / "tresor-fehlt"))
    pruef_welt["maschine"].write_text(json.dumps({"repos": []}), encoding="utf-8")
    erg = us.pruefen()
    assert erg.ok, erg.fehler
    assert erg.ziel_herkunft == "vorgabe"
    assert erg.ziel.is_relative_to(pruef_welt["home"] / ".local" / "share")
    assert any("sicherung.dateien" in h for h in erg.hinweise), erg.hinweise


def test_pruefen_meldet_kaputte_maschine_json_und_ungueltige_typen(pruef_welt: dict[str, Path]) -> None:
    """Kaputtes JSON oder falsche Typen in ``sicherung`` werfen nicht, sondern melden klare Fehler."""
    pruef_welt["maschine"].write_text("{kaputt", encoding="utf-8")
    erg_kaputt = us.pruefen()
    assert not erg_kaputt.ok
    assert any("unlesbar" in f for f in erg_kaputt.fehler), erg_kaputt.fehler

    pruef_welt["maschine"].write_text(
        json.dumps({"repos": [], "sicherung": {"ziel": 123, "dateien": "kein-array"}}),
        encoding="utf-8",
    )
    erg_typ = us.pruefen()
    assert not erg_typ.ok
    assert any("sicherung.ziel" in f for f in erg_typ.fehler), erg_typ.fehler
    assert any("sicherung.dateien" in f for f in erg_typ.fehler), erg_typ.fehler


def test_cli_sichern_pruefen_exit_codes(pruef_welt: dict[str, Path]) -> None:
    """``to_spawn.py sichern --pruefen`` liefert Exit 0 bei gültiger Konfig und Exit 1 bei Lücke."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    ok = subprocess.run(
        [sys.executable, str(WURZEL / "to_spawn.py"), "sichern", "--pruefen"],
        cwd=WURZEL,
        env=env,
        capture_output=True,
        text=True,
        **konsole.UNTERPROZESS_TEXT,
        timeout=60,
        check=False,
    )
    assert ok.returncode == 0, ok.stderr + ok.stdout
    assert "Sicherung-Prüfung: OK" in ok.stdout
    assert not (pruef_welt["tresor"] / us.TRESOR_UNTERORDNER).exists()

    pruef_welt["maschine"].write_text(
        json.dumps({"repos": [], "sicherung": {"dateien": [str(pruef_welt["tmp"] / "fehlt.md")]}}),
        encoding="utf-8",
    )
    rot = subprocess.run(
        [sys.executable, str(WURZEL / "to_spawn.py"), "sichern", "--pruefen"],
        cwd=WURZEL,
        env=env,
        capture_output=True,
        text=True,
        **konsole.UNTERPROZESS_TEXT,
        timeout=60,
        check=False,
    )
    assert rot.returncode == 1, rot.stderr + rot.stdout
    assert "LÜCKE" in rot.stdout


def test_abnahme_m15_und_wiedereinstieg_stand_755() -> None:
    """#755: ABNAHME_L1 verweist bei M15 auf #755/#756 statt #145 und belegt die Bau-Server-Prüfung."""
    abnahme = (WURZEL / "docs" / "ABNAHME_L1.md").read_text(encoding="utf-8")
    m15_zeilen = [z for z in abnahme.splitlines() if z.startswith("| M15 |")]
    assert len(m15_zeilen) == 1
    m15 = m15_zeilen[0]
    assert "#755" in m15 and "#756" in m15
    assert "→ #145" not in m15
    assert "sichern --pruefen" in m15

    wiedereinstieg = (WURZEL / "docs" / "WIEDEREINSTIEG.md").read_text(encoding="utf-8")
    assert "sichern --pruefen" in wiedereinstieg
