"""Hausmeister #1491: Zweige (``ticket/*``, ``sicherung/*``) nur mit ausdrücklichem Schalter löschen.

Der Schalter ``TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN=1`` lebt allein im Hausmeister und gilt für
jeden Aufrufer gleich: Aufpasser-Takt, capo-Schlussschritt, Start-Check von ``bau.py`` (bei
Platznot), Handaufruf. Weg-Tests mit echtem Wegwerf-Git und lokalem Bare-Remote als „origin“
(Fixture ``welt`` aus #1368) — nie GitHub. Die Remote-/Repo-Liste steht wie im Betrieb ohne
Variablen auf „alle eigenen“ (hier: Bare-Remote + Hauptrepo der Welt), nur der Schalter fehlt.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem #1368-Test und wird als Parameter genannt.
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

import pytest
from test_hausmeister_1368 import (  # noqa: F401 — ``welt`` ist ein Fixture
    ORIGIN,
    Welt,
    git,
    welt,
)
from test_hausmeister_1371 import _sicherung

from to_spawn import aufpasser, capo, hausmeister, speicher

SCHALTER = "TO_SPAWN_HAUSMEISTER_ZWEIGE_LOESCHEN"
ZWEIG = "ticket/60-fertig"


@pytest.fixture
def ohne(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Welt:
    """Welt mit fertigem Ticket-Zweig auf dem Remote (Ticket zu, übernommen), Schalter fehlt."""
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", ORIGIN)
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", str(welt.haupt))
    monkeypatch.delenv(SCHALTER, raising=False)
    h = welt.haupt
    git(h, "checkout", "-q", "-b", ZWEIG)
    (h / "arbeit.txt").write_text("fertig\n", encoding="utf-8")
    git(h, "add", "arbeit.txt")
    git(h, "commit", "-q", "-m", "arbeit")
    git(
        h, "push", "-q", "origin", f"{ZWEIG}:{ZWEIG}", f"{ZWEIG}:master"
    )  # Schlange hat übernommen
    git(h, "checkout", "-q", "master")
    git(h, "reset", "-q", "--hard", f"origin/{ZWEIG}")
    git(h, "branch", "-q", "-D", ZWEIG)  # nur noch auf dem Remote
    welt.ticket(60, "CLOSED", vor_stunden=48)
    return welt


def _auf_remote(w: Welt) -> bool:
    return ZWEIG in w.im_bare("for-each-ref", "--format=%(refname:short)").split()


def _zweig_zeilen(w: Welt) -> list[dict]:
    return [z for z in w.zeilen() if "zweig" in z]


def _aus_gemeldet(caplog: pytest.LogCaptureFixture) -> bool:
    return any("Zweig-Löschen aus" in r.getMessage() for r in caplog.records)


# --- Rot-Beweis: ohne Schalter über jeden Weg kein Lösch-Aufruf ---------------


def test_ohne_schalter_aufpasser_takt_loescht_keinen_zweig(
    ohne: Welt, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="to_spawn.hausmeister")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "0.50")
    e = aufpasser.Einstellungen(
        zustand=ohne.tmp / "zustand",
        tmux_socket=f"hausmeister1491-{os.getpid()}",
        deploy_muster=f"hausmeister1491-niemals-{os.getpid()}",
    )
    assert aufpasser.lauf_mit_einstellungen(e) == 0
    assert _auf_remote(ohne)
    assert _zweig_zeilen(ohne) == []
    assert _aus_gemeldet(caplog)


def test_ohne_schalter_capo_schlussschritt_loescht_keinen_zweig(
    ohne: Welt, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="to_spawn.hausmeister")
    zeilen = capo._hausmeister_schluss(1491, {}, datetime.now(UTC), False)
    assert zeilen and "Hausmeister" in zeilen[0]
    assert _auf_remote(ohne)
    assert _zweig_zeilen(ohne) == []
    assert _aus_gemeldet(caplog)


def test_ohne_schalter_bau_start_check_bei_platznot_loescht_keinen_zweig(
    ohne: Welt, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="to_spawn.hausmeister")
    monkeypatch.setenv(
        "TO_SPAWN_HAUSMEISTER_PLATTE", "0.10"
    )  # Platznot, über der Startsperre
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(ohne.tmp / "h.jsonl"))
    assert speicher.auf_platte_warten(wer="bau 1491") == 0
    assert _auf_remote(ohne)
    assert _zweig_zeilen(ohne) == []
    assert _aus_gemeldet(caplog)


def test_ohne_schalter_trockenlauf_keine_zweig_zeile(ohne: Welt) -> None:
    hausmeister.aufraeumen_alles(loeschen=False, jetzt=ohne.jetzt)
    assert _zweig_zeilen(ohne) == []


def test_ohne_schalter_sicherungs_zweig_bleibt(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", ORIGIN)
    monkeypatch.delenv(SCHALTER, raising=False)
    _sicherung(welt, monkeypatch, 31, 91)
    assert hausmeister.sicherungen_aufraeumen(loeschen=True, jetzt=welt.jetzt) == []
    assert (
        "sicherung/31"
        in welt.im_bare("for-each-ref", "--format=%(refname:short)").split()
    )


# --- Mit Schalter wie bisher (#1439) --------------------------------------------


@pytest.mark.parametrize("wert", ["0", "", "ja"])
def test_nur_eins_schaltet_ein(
    ohne: Welt, monkeypatch: pytest.MonkeyPatch, wert: str
) -> None:
    monkeypatch.setenv(SCHALTER, wert)
    hausmeister.aufraeumen_alles(loeschen=True, jetzt=ohne.jetzt)
    assert _auf_remote(ohne)


def test_mit_schalter_loescht_wie_bisher_ueber_jeden_weg(
    ohne: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SCHALTER, "1")
    hausmeister.aufraeumen_alles(loeschen=False, jetzt=ohne.jetzt)
    assert [(z["zweig"], z["art"], z["trocken"]) for z in _zweig_zeilen(ohne)] == [
        (ZWEIG, "gelöscht", True)
    ]
    assert _auf_remote(ohne)
    zeilen = capo._hausmeister_schluss(1491, {}, datetime.now(UTC), False)
    assert zeilen and "Hausmeister" in zeilen[0]
    assert not _auf_remote(ohne)


# --- Fix 1 (Review M1): Zweig-Tür und Lösch-Senke ---------------------------------


def _cli():
    import importlib.util

    from test_hausmeister_1368 import SKILL

    spec = importlib.util.spec_from_file_location(
        "to_spawn_cli_1491", SKILL / "to_spawn.py"
    )
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    return cli


def test_ohne_schalter_befehl_zweig_tuer_loescht_nicht_auf_github(
    ohne: Welt, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pushes: list[tuple[str, ...]] = []
    echt = hausmeister._git_ohne_frage

    def zaehlt(cwd, *args: str):
        if args and args[0] == "push":
            pushes.append(args)
        return echt(cwd, *args)

    monkeypatch.setattr(hausmeister, "_git_ohne_frage", zaehlt)
    code = _cli().main(["aufraeumen", "--zweig", ZWEIG, "--repo", ORIGIN, "--loeschen"])
    aus = capsys.readouterr().out
    assert pushes == []  # weder Lösch-Push noch Sicherungs-Push Richtung GitHub
    assert _auf_remote(ohne)
    assert "Zweig-Löschen aus" in aus
    assert (
        code == 1
    )  # Aufrufer (z. B. Deploy-Schlange) merkt: verlangt, aber nicht gelöscht


def test_ohne_schalter_zweig_tuer_trocken_zeigt_aus(ohne: Welt) -> None:
    r = hausmeister.zweig_aufraeumen(ORIGIN, ZWEIG)
    assert (r.art, r.trocken, r.fehler) == ("bleibt", True, False)
    assert "Zweig-Löschen aus" in r.grund
    assert _auf_remote(ohne)


def test_loesch_senke_ohne_schalter_pusht_nie(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(SCHALTER, raising=False)
    aufrufe: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        hausmeister, "_git_ohne_frage", lambda cwd, *a: aufrufe.append(a)
    )
    with pytest.raises(RuntimeError, match="Zweig-Löschen aus"):
        hausmeister._zweig_loeschen(tmp_path, ZWEIG, "0" * 40)
    assert aufrufe == []
    monkeypatch.setenv(SCHALTER, "1")  # Gegenprobe: mit Schalter genau ein Lösch-Push
    hausmeister._zweig_loeschen(tmp_path, ZWEIG, "0" * 40)
    assert [a[0] for a in aufrufe] == ["push"]
