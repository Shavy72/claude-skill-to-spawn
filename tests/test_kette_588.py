"""Tests #588: Kette am Spec-Ende — ablegen → Thermo → Rückblick → genau EINE Mail über ``nachsehen``.

Echt laufen: Git-Repo mit Bare-Origin, die CLI ``skripte/capo.py`` (SPEC FERTIG), die echte
CLI ``skripte/abschluss_paket.py`` (ablegen/nachsehen) und die echte Zustandsdatei. Gestellt sind
nur die externen Dienste: ``gh`` (Ersatz aus #213) und der Mail-Befehl (schreibt in eine Datei).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus test_waechter_213 und wird als Parameter genannt.
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _beleg,
    _capo,
    _commit,
    _gh_setzen,
    _iso,
    _mails,
    _text,
    welt,
)

SKILL = Path(__file__).resolve().parents[1]
SKRIPTE = SKILL / "skripte"
_LINKS = [
    "--stage",
    "https://stage.test",
    "--rundschau",
    "https://r.test/1",
    "--tests",
    "https://t.test/2",
]
_BETREFF = f"Spec {SPEC} fertig — 3 Links"


@pytest.fixture()
def kette(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Welt aus #213 + Abschluss-Ordner im Wegwerf-Verzeichnis (nie ~/.claude/data)."""
    monkeypatch.setenv("TO_SPAWN_ABSCHLUSS_ORDNER", str(welt["tmp"] / "abschluss"))
    return welt


def _ablegen(welt: dict[str, Path], spec_fertig: datetime) -> None:
    lauf = subprocess.run(
        [
            sys.executable,
            str(SKRIPTE / "abschluss_paket.py"),
            "ablegen",
            SPEC,
            "--repo",
            str(welt["repo"]),
            *_LINKS,
            "--spec-fertig",
            spec_fertig.isoformat(timespec="seconds"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=120,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr


def _marker(welt: dict[str, Path]) -> None:
    ordner = welt["repo"] / "docs" / "agents"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / f"rueckblick_{SPEC}.md").write_text("Rückblick: keine Vorschläge\n", encoding="utf-8")
    (ordner / f"thermo_{SPEC}.md").write_text("# Thermo 900\n\n- nichts auffällig\n", encoding="utf-8")


def _spec_fertig(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: A (#901)", _beleg("901"))
    _commit(welt["repo"], "feat: B (#902)", _beleg("902"))
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())


def _jetzt() -> datetime:
    return datetime.now().astimezone()


def _keine_kurzmail(welt: dict[str, Path]) -> None:
    assert all(m["betreff"] != f"Spec #{SPEC} fertig" for m in _mails(welt)), _mails(welt)


def test_a_ablage_und_marker_genau_eine_mail(kette: dict[str, Path]) -> None:
    _spec_fertig(kette)
    _ablegen(kette, _jetzt())
    _marker(kette)
    erster = _capo(kette)
    assert "SPEC FERTIG" in erster.stdout, _text(erster)
    assert "Abschluss-Mail: verschickt/erledigt" in erster.stdout, _text(erster)
    mails = _mails(kette)
    assert [(m["art"], m["betreff"]) for m in mails] == [("spec_fertig", _BETREFF)], mails
    zweiter = _capo(kette)
    assert len(_mails(kette)) == 1, _text(zweiter)
    assert "Abschluss-Mail" not in zweiter.stdout
    _keine_kurzmail(kette)


def test_b_ohne_marker_nach_120_min_mail_mit_vermerk(kette: dict[str, Path]) -> None:
    _spec_fertig(kette)
    _ablegen(kette, _jetzt() - timedelta(minutes=150))
    ergebnis = _capo(kette)
    mails = _mails(kette)
    assert len(mails) == 1, _text(ergebnis)
    assert mails[0]["betreff"] == _BETREFF
    assert "Rückblick fehlgeschlagen" in mails[0]["text"]


def test_c_ohne_marker_unter_120_min_wartet(kette: dict[str, Path]) -> None:
    _spec_fertig(kette)
    _ablegen(kette, _jetzt() - timedelta(minutes=10))
    ergebnis = _capo(kette)
    assert "Abschluss-Mail wartet" in ergebnis.stdout, _text(ergebnis)
    assert "FEHLER: Abschluss-Mail" not in ergebnis.stdout
    assert _mails(kette) == []


def test_d_ohne_ablage_legt_capo_selbst_ab_keine_kurzmail(
    kette: dict[str, Path],
) -> None:
    _spec_fertig(kette)
    ergebnis = _capo(kette)
    datei = kette["repo"] / ".to-spawn" / f"abschluss_{SPEC}_paket.json"
    assert datei.is_file(), _text(ergebnis)
    paket = json.loads(datei.read_text(encoding="utf-8"))["paket"]
    assert paket["stage"] == "fehlt" and paket["rundschau"].startswith("fehlt")
    assert "Abschluss-Mail wartet" in ergebnis.stdout, _text(ergebnis)
    assert _mails(kette) == []


def _prompts() -> dict[str, str]:
    import importlib.util

    sys.path.insert(0, str(SKILL))
    from to_spawn import waechter_takt

    modul_spec = importlib.util.spec_from_file_location("wache_588", SKRIPTE / "wache.py")
    assert modul_spec and modul_spec.loader
    wache = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)
    return {"wache": wache.PROMPT, "takt": waechter_takt.PROMPT}


@pytest.mark.parametrize("name", ["wache", "takt"])
def test_prompt_kette_in_reihenfolge(name: str) -> None:
    prompt = _prompts()[name]
    stellen = [
        prompt.index(k)
        for k in (
            "abschluss_paket.py ablegen",
            "thermo_lauf.py plan",
            "rueckblick.py planen",
            "rueckblick.py sammeln",
            "abschluss_paket.py nachsehen",
        )
    ]
    assert stellen == sorted(stellen)
    assert "mp-retro" in prompt
    assert "mailt David" not in prompt


def test_wache_prompt_formatiert_ablegen_und_nachsehen() -> None:
    text = _prompts()["wache"].format(S=900, REPO="x/y", DATUM="2026-09-24", TAKT=1800, SKILL="/skill")
    assert "/skill/skripte/abschluss_paket.py ablegen 900 --stand abnahme" in text
    assert "/skill/skripte/rueckblick.py planen 900" in text
    assert "/skill/skripte/abschluss_paket.py nachsehen 900" in text
