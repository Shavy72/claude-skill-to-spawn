"""Alt-Bestand alter ``~/tmp-l<N>-…``-Ordner im Home sicher wegräumen (#615).

Rest aus #551: der neue Befehl ``helfer-temp`` räumt bewusst nur unter ``~/tmp-bau/helfer``.
Für den Alt-Bestand direkt im Home stellt der Hausmeister einen eigenen, erlaubten Weg
mit festem Namensmuster, Mindestalter, Prozess-/Symlink-Schutz und Log-Pflicht bereit —
ohne Shell-Löschen mit Platzhalter.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from to_spawn import befugnis, hausmeister

SKILL = Path(__file__).resolve().parent.parent


def _ordner(pfad: Path, *, alter_tage: float, datei_alter_tage: float | None = None) -> Path:
    pfad.mkdir(parents=True, exist_ok=True)
    datei = pfad / "notiz.txt"
    datei.write_text("rest\n", encoding="utf-8")
    jetzt = time.time()
    d_zeit = jetzt - (alter_tage if datei_alter_tage is None else datei_alter_tage) * 86400
    o_zeit = jetzt - alter_tage * 86400
    os.utime(datei, (d_zeit, d_zeit))
    os.utime(pfad, (o_zeit, o_zeit))
    return pfad


@pytest.fixture
def welt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    heim = tmp_path / "heim"
    heim.mkdir()
    log_datei = tmp_path / "logs" / "hausmeister.jsonl"
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("USERPROFILE", str(heim))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(log_datei))
    return heim, log_datei


def test_trockenlauf_meldet_passende_alte_ordner_ohne_zu_loeschen(welt: tuple[Path, Path]) -> None:
    heim, log_datei = welt
    alt_a = _ordner(heim / "tmp-l207-551", alter_tage=3)
    alt_b = _ordner(heim / "tmp-l161-433f4", alter_tage=5)
    alt_c = _ordner(heim / "tmp-l213-belege", alter_tage=2)
    frisch = _ordner(heim / "tmp-l209-616", alter_tage=0.2)
    innen_frisch = _ordner(heim / "tmp-l205-596", alter_tage=4, datei_alter_tage=0.1)
    tmp_bau = _ordner(heim / "tmp-bau", alter_tage=10)
    fremd = _ordner(heim / "anderer-ordner", alter_tage=10)
    log_rest = heim / "tmp-l162-install.log"
    log_rest.write_text("log\n", encoding="utf-8")

    erg = hausmeister.helfer_altbestand_aufraeumen(loeschen=False)
    je_name = {e.ordner.name: e for e in erg}

    assert set(je_name) == {"tmp-l207-551", "tmp-l161-433f4", "tmp-l213-belege", "tmp-l209-616", "tmp-l205-596"}
    for name in ("tmp-l207-551", "tmp-l161-433f4", "tmp-l213-belege"):
        assert je_name[name].art == "gelöscht"
        assert je_name[name].trocken is True
    for name in ("tmp-l209-616", "tmp-l205-596"):
        assert je_name[name].art == "bleibt"
        assert "frisch" in je_name[name].grund.lower()

    for pfad in (alt_a, alt_b, alt_c, frisch, innen_frisch, tmp_bau, fremd, log_rest):
        assert pfad.exists()
    assert log_datei.is_file()


def test_loeschen_entfernt_nur_alte_passende_ordner_und_schreibt_log(welt: tuple[Path, Path]) -> None:
    heim, log_datei = welt
    alt = _ordner(heim / "tmp-l165-kombi2", alter_tage=3)
    frisch = _ordner(heim / "tmp-l209-616", alter_tage=0.2)
    innen_frisch = _ordner(heim / "tmp-l205-596", alter_tage=5, datei_alter_tage=0.2)
    tmp_bau = _ordner(heim / "tmp-bau", alter_tage=10)
    bak = _ordner(heim / "tmp-l196-543-capo.bak", alter_tage=10)

    erg = hausmeister.helfer_altbestand_aufraeumen(loeschen=True, aelter_als_tage=1)
    je_name = {e.ordner.name: e for e in erg}

    assert je_name["tmp-l165-kombi2"].art == "gelöscht"
    assert je_name["tmp-l165-kombi2"].trocken is False
    assert not alt.exists()
    assert frisch.is_dir() and innen_frisch.is_dir() and tmp_bau.is_dir() and bak.is_dir()

    zeilen = [json.loads(z) for z in log_datei.read_text(encoding="utf-8").splitlines()]
    geloescht = [z for z in zeilen if z["art"] == "gelöscht" and z["trocken"] is False]
    assert len(geloescht) == 1 and Path(geloescht[0]["ordner"]).name == "tmp-l165-kombi2"


def test_symlink_wird_nie_geloescht(welt: tuple[Path, Path], tmp_path: Path) -> None:
    heim, _ = welt
    aussen = _ordner(tmp_path / "fremd", alter_tage=10)
    falle = heim / "tmp-l200-falle"
    try:
        falle.symlink_to(aussen, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink nicht anlegbar (Windows ohne Recht)")

    erg = hausmeister.helfer_altbestand_aufraeumen(loeschen=True, jetzt=time.time() + 10 * 86400)
    assert len(erg) == 1
    assert erg[0].art == "bleibt" and "symlink" in erg[0].grund.lower()
    assert (aussen / "notiz.txt").is_file()
    assert falle.is_symlink()


def test_ordner_mit_git_bleibt_stehen(welt: tuple[Path, Path]) -> None:
    heim, _ = welt
    repo = _ordner(heim / "tmp-l200-repo", alter_tage=5)
    (repo / ".git").mkdir()
    vor = time.time() - 5 * 86400
    os.utime(repo / ".git", (vor, vor))
    os.utime(repo, (vor, vor))

    erg = hausmeister.helfer_altbestand_aufraeumen(loeschen=True)
    assert len(erg) == 1
    assert erg[0].art == "bleibt" and ".git" in erg[0].grund
    assert repo.is_dir()


def test_laufender_prozess_haelt_ordner_fest(welt: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    heim, _ = welt
    alt = _ordner(heim / "tmp-l201-aktiv", alter_tage=5)

    def belegt(ordner: Path, *, probe: bool = False) -> None:
        raise hausmeister._Bleibt("Prozess 42 arbeitet darin")

    monkeypatch.setattr(hausmeister, "_kein_prozess", belegt)
    erg = hausmeister.helfer_altbestand_aufraeumen(loeschen=True)
    assert len(erg) == 1
    assert erg[0].art == "bleibt" and "Prozess 42" in erg[0].grund
    assert alt.is_dir()


def test_ohne_schreibbares_log_kein_loeschen(
    welt: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    heim, _ = welt
    alt = _ordner(heim / "tmp-l202-logtest", alter_tage=5)
    kaputt_log = tmp_path / "log_ist_ordner"
    kaputt_log.mkdir()
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(kaputt_log))

    erg = hausmeister.helfer_altbestand_aufraeumen(loeschen=True)
    assert len(erg) == 1
    assert erg[0].art == "bleibt" and erg[0].fehler is True
    assert alt.is_dir()


@pytest.mark.parametrize("tage", [0, -1, 0.5, float("nan"), float("inf")])
def test_unsinniges_mindestalter_abgelehnt(welt: tuple[Path, Path], tage: float) -> None:
    heim, _ = welt
    alt = _ordner(heim / "tmp-l203-alt", alter_tage=10)
    with pytest.raises(ValueError):
        hausmeister.helfer_altbestand_aufraeumen(loeschen=True, aelter_als_tage=tage)
    assert alt.is_dir()


def test_cli_aufraeumen_helfer_altbestand_trocken_und_loeschen(
    welt: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    heim, _ = welt
    alt = _ordner(heim / "tmp-l207-615", alter_tage=3)
    spec = importlib.util.spec_from_file_location("to_spawn_cli_615", SKILL / "to_spawn.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    assert cli.main(["aufraeumen", "--helfer-altbestand"]) == 0
    aus = capsys.readouterr().out
    assert "tmp-l207-615" in aus and "[trocken]" in aus
    assert alt.is_dir()

    assert cli.main(["aufraeumen", "--helfer-altbestand", "--aelter-als-tage", "2", "--loeschen"]) == 0
    aus = capsys.readouterr().out
    assert "tmp-l207-615" in aus and "[echt]" in aus
    assert not alt.exists()

    with pytest.raises(SystemExit) as aus_err:
        cli.main(["aufraeumen", "--helfer-altbestand", "--aelter-als-tage", "0"])
    assert aus_err.value.code == 2

    with pytest.raises(SystemExit) as aus_err2:
        cli.main(["aufraeumen", "--aelter-als-tage", "2", "--alles"])
    assert aus_err2.value.code == 2


def test_schutz_hooks_erlauben_helfer_altbestand_befehl() -> None:
    befehl = "python3 ~/.claude/skills/to-spawn/to_spawn.py aufraeumen --helfer-altbestand --loeschen"
    spec = importlib.util.spec_from_file_location("skill_schutz_615", SKILL / "nest" / "skill_schutz.py")
    assert spec and spec.loader
    skill_schutz = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(skill_schutz)

    assert befugnis.handlung_klasse_2(befehle=[befehl]) is None
    assert (
        skill_schutz.pruefe({"tool_name": "Bash", "tool_input": {"command": befehl}, "cwd": str(Path.home())}) is False
    )
    hook = Path.home() / ".claude" / "hooks" / "block-dangerous.sh"
    bash = shutil.which("bash")
    if hook.is_file() and bash is not None:
        erg = subprocess.run(
            [bash, str(hook)],
            input=json.dumps({"tool_input": {"command": befehl}}),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert erg.returncode == 0, erg.stdout + erg.stderr


def test_trockenlauf_log_meldet_wuerde_geloescht_statt_geloescht(
    welt: tuple[Path, Path],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """#615 Befund 1: Im Trockenlauf steht im Log und in der Ausgabe „würde gelöscht“, nie „gelöscht“."""
    heim, log_datei = welt
    alt = _ordner(heim / "tmp-l234-trocken", alter_tage=3)

    with caplog.at_level(logging.INFO, logger=hausmeister.log.name):
        erg = hausmeister.helfer_altbestand_aufraeumen(loeschen=False)

    assert len(erg) == 1 and erg[0].trocken is True
    assert alt.is_dir()
    assert "würde gelöscht" in caplog.text
    assert "— gelöscht (" not in caplog.text

    zeilen = [json.loads(z) for z in log_datei.read_text(encoding="utf-8").splitlines()]
    assert len(zeilen) == 1
    assert zeilen[0]["art"] == "würde gelöscht"
    assert zeilen[0]["trocken"] is True

    spec = importlib.util.spec_from_file_location("to_spawn_cli_615_b1", SKILL / "to_spawn.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.main(["aufraeumen", "--helfer-altbestand"]) == 0
    aus = capsys.readouterr().out
    assert "würde gelöscht" in aus and "[trocken]" in aus


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-Prozess-Suche über /proc (#615)")
def test_weg_linux_prozess_suche_scannt_proc_einmal_und_ist_schnell(
    welt: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#615 Befund 2: Echter Weg-Test unter tmp_path — /proc wird pro Lauf höchstens einmal gescannt."""
    heim, _ = welt
    alte = [_ordner(heim / f"tmp-l{300 + i}-615", alter_tage=3) for i in range(25)]
    frische = [_ordner(heim / f"tmp-l{400 + i}-frisch", alter_tage=0.1) for i in range(2)]

    proc_scans = 0
    echt_scandir = os.scandir
    echt_iterdir = Path.iterdir

    def zaehle_scandir(pfad: os.PathLike[str] | str = "."):
        nonlocal proc_scans
        if str(pfad) == "/proc":
            proc_scans += 1
        return echt_scandir(pfad)

    def zaehle_iterdir(self: Path):
        nonlocal proc_scans
        if str(self) == "/proc":
            proc_scans += 1
        return echt_iterdir(self)

    monkeypatch.setattr(os, "scandir", zaehle_scandir)
    monkeypatch.setattr(Path, "iterdir", zaehle_iterdir)

    start = time.monotonic()
    erg = hausmeister.helfer_altbestand_aufraeumen(loeschen=True, aelter_als_tage=1)
    dauer = time.monotonic() - start

    assert proc_scans == 1, f"/proc wurde {proc_scans}x statt 1x gescannt (Dauer {dauer:.3f}s)"
    assert dauer < 5.0, f"Lauf zu langsam: {dauer:.3f}s für 25 Ordner"
    assert all(not p.exists() for p in alte)
    assert all(p.is_dir() for p in frische)
    assert sum(1 for e in erg if e.art == "gelöscht" and not e.trocken) == 25


def test_zeitgrenze_bricht_sauber_mit_klarer_meldung_ab(
    welt: tuple[Path, Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#615 Befund 2: Bei erreichter Zeitgrenze bricht der Lauf sauber mit klarer Meldung ab."""
    heim, _ = welt
    ordner = [_ordner(heim / f"tmp-l{500 + i}-zeit", alter_tage=3) for i in range(5)]

    takt = iter([0.0, 0.2, 0.4, 1.5, 2.0, 2.5, 3.0])
    letzter = 0.0

    def fake_uhr() -> float:
        nonlocal letzter
        letzter = next(takt, letzter + 1.0)
        return letzter

    with caplog.at_level(logging.WARNING, logger=hausmeister.log.name):
        erg = hausmeister.helfer_altbestand_aufraeumen(
            loeschen=True,
            zeitgrenze_s=1.0,
            uhr=fake_uhr,
        )

    assert any(p.is_dir() for p in ordner), "Nach Zeitgrenze müssen restliche Ordner stehen bleiben"
    letztes = erg[-1]
    assert letztes.art == "bleibt" and letztes.fehler is True
    assert "Zeitgrenze" in letztes.grund and "noch offen" in letztes.grund and "erneut" in letztes.grund
    assert "Zeitgrenze" in caplog.text


@pytest.mark.parametrize("sekunden", [0, -5, float("nan"), float("inf")])
def test_unsinnige_zeitgrenze_abgelehnt(welt: tuple[Path, Path], sekunden: float) -> None:
    heim, _ = welt
    alt = _ordner(heim / "tmp-l600-zeit", alter_tage=5)
    with pytest.raises(ValueError):
        hausmeister.helfer_altbestand_aufraeumen(loeschen=True, zeitgrenze_s=sekunden)
    assert alt.is_dir()


def test_cli_hilfe_nennt_aktualisierung_und_zeitgrenze(capsys: pytest.CaptureFixture[str]) -> None:
    """#615 Befund 3: Hilfe nennt Weg zum Aktualisieren, falls der installierte Skill veraltet ist."""
    spec = importlib.util.spec_from_file_location("to_spawn_cli_615_b3", SKILL / "to_spawn.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    with pytest.raises(SystemExit) as aus:
        cli.main(["aufraeumen", "--help"])
    assert aus.value.code == 0
    hilfe = capsys.readouterr().out
    assert "--helfer-altbestand" in hilfe
    assert "--zeitgrenze" in hilfe
    assert "install.sh" in hilfe
    assert "~/.claude/skills/to-spawn" not in hilfe

    with pytest.raises(SystemExit) as err1:
        cli.main(["aufraeumen", "--helfer-altbestand", "--zeitgrenze", "0"])
    assert err1.value.code == 2

    with pytest.raises(SystemExit) as err2:
        cli.main(["aufraeumen", "--zeitgrenze", "30", "--alles"])
    assert err2.value.code == 2
