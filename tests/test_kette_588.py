"""Tests #588: Kette am Spec-Ende — ablegen → Thermo → Rückblick → genau EINE Mail über ``nachsehen``.

Echt laufen: Git-Repo mit Bare-Origin, die CLI ``skripte/capo.py`` (SPEC FERTIG), die echte
CLI ``skripte/abschluss_paket.py`` (ablegen/nachsehen) und die echte Zustandsdatei. Gestellt sind
nur die externen Dienste: ``gh`` (Ersatz aus #213) und der Mail-Befehl (schreibt in eine Datei).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from test_waechter_213 import (
    SPEC,
    _beleg,
    _capo,
    _commit,
    _gh_setzen,
    _iso,
    _mails,
    _text,
    welt,  # noqa: F401  (Fixture)
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
def kette(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:  # noqa: F811 (Fixture-Parameter)
    """Welt aus #213 + Abschluss-Ordner im Wegwerf-Verzeichnis (nie ~/.claude/data)."""
    monkeypatch.setenv("TO_SPAWN_ABSCHLUSS_ORDNER", str(welt["tmp"] / "abschluss"))
    return welt


def _ablegen(kette: dict[str, Path], spec_fertig: datetime) -> None:
    lauf = subprocess.run(
        [
            sys.executable,
            str(SKRIPTE / "abschluss_paket.py"),
            "ablegen",
            SPEC,
            "--repo",
            str(kette["repo"]),
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


def _marker(kette: dict[str, Path]) -> None:
    ordner = kette["repo"] / "docs" / "agents"
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / f"rueckblick_{SPEC}.md").write_text("Rückblick: keine Vorschläge\n", encoding="utf-8")
    (ordner / f"thermo_{SPEC}.md").write_text("# Thermo 900\n\n- nichts auffällig\n", encoding="utf-8")


def _spec_fertig(kette: dict[str, Path]) -> None:
    _commit(kette["repo"], "feat: A (#901)", _beleg("901"))
    _commit(kette["repo"], "feat: B (#902)", _beleg("902"))
    _gh_setzen(kette, "902", state="closed", closed_at=_iso())


def _jetzt() -> datetime:
    return datetime.now().astimezone()


def _keine_kurzmail(kette: dict[str, Path]) -> None:
    assert all(m["betreff"] != f"Spec #{SPEC} fertig" for m in _mails(kette)), _mails(kette)


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
    assert "abschluss_mail_erledigt" not in _zustand(kette)


def test_d_ohne_ablage_legt_capo_selbst_ab_keine_kurzmail(
    kette: dict[str, Path],
) -> None:
    _spec_fertig(kette)
    ergebnis = _capo(kette)
    datei = kette["repo"] / ".to-spawn" / f"abschluss_{SPEC}_paket.json"
    assert datei.is_file(), _text(ergebnis)
    paket = json.loads(datei.read_text(encoding="utf-8"))["paket"]
    assert paket["stage"] == "fehlt" and paket["rundschau"].startswith("fehlt")
    assert json.loads(datei.read_text(encoding="utf-8"))["spec_fertig"] == _zustand(kette)["spec_fertig_seit"]
    assert "Abschluss-Mail wartet" in ergebnis.stdout, _text(ergebnis)
    assert _mails(kette) == []


@pytest.fixture(scope="module")
def prompts() -> Iterator[dict[str, str]]:
    """Beide Prompts einmal je Modul laden; sys.path nur während des Ladens erweitert."""
    import importlib.util

    with pytest.MonkeyPatch.context() as mp:
        mp.syspath_prepend(str(SKILL))
        from to_spawn import waechter_takt

        modul_spec = importlib.util.spec_from_file_location("wache_588", SKRIPTE / "wache.py")
        assert modul_spec and modul_spec.loader
        wache = importlib.util.module_from_spec(modul_spec)
        modul_spec.loader.exec_module(wache)
    yield {"wache": wache.PROMPT, "takt": waechter_takt.PROMPT}


@pytest.mark.parametrize("name", ["wache", "takt"])
def test_prompt_kette_in_reihenfolge(name: str, prompts: dict[str, str]) -> None:
    prompt = prompts[name]
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
    assert "capo stößt live nicht an" in prompt
    assert "capo stößt es je Tick selbst an" in prompt.split("--stand live")[0]


def test_wache_prompt_formatiert_ablegen_und_nachsehen(prompts: dict[str, str]) -> None:
    text = prompts["wache"].format(S=900, REPO="x/y", DATUM="2026-09-24", TAKT=1800, SKILL="/skill")
    assert "/skill/skripte/abschluss_paket.py ablegen 900 --stand abnahme" in text
    assert "/skill/skripte/rueckblick.py planen 900" in text
    assert "/skill/skripte/abschluss_paket.py nachsehen 900" in text


def _zustand(kette: dict[str, Path]) -> dict:
    dateien = list(kette["zustand"].glob(f"*_{SPEC}.json"))
    assert len(dateien) == 1, dateien
    return json.loads(dateien[0].read_text(encoding="utf-8"))


def test_e_ohne_mail_befehl_zeile_mail_aus(kette: dict[str, Path]) -> None:
    from test_waechter_213 import _konfig

    _konfig(kette, mail={"ziel": "", "nur_kritisch": True, "befehl": ""})
    _spec_fertig(kette)
    _ablegen(kette, _jetzt())
    _marker(kette)
    ergebnis = _capo(kette)
    assert "INFO: Mail nicht eingerichtet (mail.befehl leer)" in ergebnis.stdout, _text(ergebnis)
    assert "FEHLER: Abschluss-Mail" not in ergebnis.stdout
    assert _mails(kette) == []


def test_f_drei_fehlversuche_einmal_kommentar_erfolg_setzt_zurueck(
    kette: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_waechter_213 import _kommentare

    _spec_fertig(kette)
    _ablegen(kette, _jetzt())
    _marker(kette)
    monkeypatch.setenv("MAIL_FAKE_EXIT", "1")
    for _ in range(2):
        ergebnis = _capo(kette)
        assert "FEHLER: Abschluss-Mail" in ergebnis.stdout, _text(ergebnis)
    assert _kommentare(kette, SPEC) == []
    assert _zustand(kette)["abschluss_fehlversuche"] == 2
    _capo(kette)
    _capo(kette)
    kommentare = _kommentare(kette, SPEC)
    assert len(kommentare) == 1, kommentare
    assert kommentare[0].startswith("Aufseher: Abschluss-Mail scheitert seit 3 Ticks:")
    assert kommentare[0].endswith("— bitte prüfen")
    monkeypatch.delenv("MAIL_FAKE_EXIT")
    ergebnis = _capo(kette)
    assert "Abschluss-Mail: verschickt/erledigt" in ergebnis.stdout, _text(ergebnis)
    zustand = _zustand(kette)
    assert not zustand.get("abschluss_fehlversuche")
    assert zustand.get("abschluss_alarm")


@pytest.mark.parametrize(
    ("url", "erwartet"),
    [("https://a:b@stage.x/pfad", "https://stage.x/pfad"), ("stage.x", "fehlt")],
)
def test_g_notfall_ablage_ohne_zugangsdaten(kette: dict[str, Path], url: str, erwartet: str) -> None:
    from test_waechter_213 import _konfig

    _konfig(kette, staging={"url": url})
    _spec_fertig(kette)
    ergebnis = _capo(kette)
    datei = kette["repo"] / ".to-spawn" / f"abschluss_{SPEC}_paket.json"
    assert datei.is_file(), _text(ergebnis)
    assert "a:b@" not in datei.read_text(encoding="utf-8")
    assert json.loads(datei.read_text(encoding="utf-8"))["paket"]["stage"] == erwartet


def test_h_ticket_wieder_offen_anstoss_laeuft_weiter(kette: dict[str, Path]) -> None:
    _spec_fertig(kette)
    _ablegen(kette, _jetzt())
    erster = _capo(kette)
    assert "Abschluss-Mail wartet" in erster.stdout, _text(erster)
    _gh_setzen(kette, "902", state="open")
    _marker(kette)
    zweiter = _capo(kette)
    assert "SPEC FERTIG" not in zweiter.stdout
    assert "Abschluss-Mail: verschickt/erledigt" in zweiter.stdout, _text(zweiter)
    assert len(_mails(kette)) == 1
