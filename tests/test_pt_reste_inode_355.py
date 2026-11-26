"""pt-Räumer bei Inode-Knappheit (#355 Teil 2).

Eine rote volle Suite hinterlässt ~220k Einträge; bei Suiten im 10-Minuten-Takt lief ``/tmp``
(tmpfs, 1 Mio. Inodes) vor Ablauf der Schonfrist von 1 h voll. Jetzt: ab 70 % Inode-Belegung
räumt der Räumer tote eigene Reste schon ab 10 min. Echte Ordner in ``tmp_path``, der
Inode-Wert kommt über die einspritzbare Funktion ``inode_anteil``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from test_pt_reste_177 import _HAT_XDIST, _env, _pt_ordner, _rest, _start, _tote_pid, _warten, _welt

import conftest

#: Zwischen knapper (10 min) und normaler (1 h) Schonfrist.
MITTEL_ALT_S = 20 * 60


def _marker_alter(ordner: Path, sekunden: float) -> None:
    zeit = time.time() - sekunden
    os.utime(ordner / conftest._MARKER, (zeit, zeit))


def _reste(temp: Path) -> dict[str, Path]:
    tot = _tote_pid()
    reste = {
        "mittel_tot": _rest(temp, "pt-mitteltt", tot, alt=False),
        "frisch_tot": _rest(temp, "pt-frischtt", tot, alt=False),
        "mittel_lebt": _rest(temp, "pt-mittellb", os.getpid(), alt=False),
        "fremd": _rest(temp, "pt-fremd355", None, alt=True),
    }
    for name in ("mittel_tot", "mittel_lebt"):
        _marker_alter(reste[name], MITTEL_ALT_S)
    return reste


@pytest.mark.parametrize("anteil", [0.70, 0.95])
def test_knappe_inodes_raeumen_tote_reste_schon_nach_kurzer_frist(tmp_path: Path, anteil: float) -> None:
    reste = _reste(tmp_path)
    conftest._reste_raeumen(tmp_path, inode_anteil=lambda _p: anteil)
    assert not reste["mittel_tot"].exists(), "toter Rest älter als 10 min muss bei Knappheit weg"
    assert reste["frisch_tot"].is_dir(), "frischer Rest bleibt (nie einen startenden Lauf treffen)"
    assert reste["mittel_lebt"].is_dir(), "Ordner eines lebenden Prozesses bleibt"
    assert reste["fremd"].is_dir(), "Ordner ohne Marker ist fremd und bleibt"


@pytest.mark.parametrize("anteil", [0.0, 0.69, None])
def test_ohne_knappheit_gilt_die_normale_schonfrist(tmp_path: Path, anteil: float | None) -> None:
    reste = _reste(tmp_path)
    conftest._reste_raeumen(tmp_path, inode_anteil=lambda _p: anteil)
    assert sorted(p.name for p in reste.values()) == _pt_ordner(tmp_path)


def test_ohne_statvfs_ist_der_anteil_unbekannt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows hat kein ``os.statvfs``: Inode-Regel greift dann nicht, kein Absturz."""
    monkeypatch.delattr(os, "statvfs", raising=False)
    assert conftest._inode_anteil(tmp_path) is None


@pytest.mark.braucht("posix")
def test_inode_anteil_misst_das_echte_laufwerk(tmp_path: Path) -> None:
    anteil = conftest._inode_anteil(tmp_path)
    assert anteil is None or 0.0 <= anteil <= 1.0, anteil


@pytest.mark.skipif(not _HAT_XDIST, reason="pytest-xdist fehlt")
def test_gruener_xdist_lauf_nach_rotem_laesst_nur_den_roten_ordner(tmp_path: Path) -> None:
    """Zwei Läufe hintereinander: der rote behält seinen Ordner, der grüne (``-n 2``) räumt seinen."""
    temp = tmp_path / "t"
    temp.mkdir()
    welt = _welt(tmp_path / "welt")
    code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "rot.txt", FEHLER="1")))
    assert code == 1, ausgabe
    rot = _pt_ordner(temp)
    assert len(rot) == 1, rot
    code, ausgabe = _warten(_start(welt, _env(temp, tmp_path / "gruen.txt"), "-n", "2"))
    assert code == 0, ausgabe
    gruen = Path((tmp_path / "gruen.txt").read_text(encoding="utf-8")).parent
    assert gruen.name.startswith("pt-") and gruen.name not in rot, gruen
    assert _pt_ordner(temp) == rot, "grüner Lauf muss seinen eigenen pt-Ordner gelöscht haben"


# --- #355 Rest-Punkte: _inode_anteil Randfälle, Marker-Rettung, Hausmeister-Zähler, wache ---


def test_inode_anteil_null_dateien_und_oserror_sind_unbekannt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``f_files == 0`` (z. B. btrfs) und ``OSError`` bei ``statvfs`` liefern ``None``."""
    from types import SimpleNamespace

    monkeypatch.setattr(os, "statvfs", lambda _p: SimpleNamespace(f_files=0, f_ffree=0), raising=False)
    assert conftest._inode_anteil(tmp_path) is None

    def kaputt(_p: object) -> object:
        raise OSError("statvfs gescheitert")

    monkeypatch.setattr(os, "statvfs", kaputt, raising=False)
    assert conftest._inode_anteil(tmp_path) is None


def test_marker_wird_nach_teilweisem_rmtree_im_raeumer_neu_geschrieben(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Löscht ``rmtree`` zuerst den Marker und scheitert dann an einer Nachzügler-Datei, darf
    der ``pt-*``-Ordner nicht markerlos verwaisen — der Marker wird mit altem Stand erneuert."""
    tot = _tote_pid()
    ordner = _rest(tmp_path, "pt-teilweg", tot, alt=False)
    _marker_alter(ordner, 2 * 3600)
    alter_mtime = (ordner / conftest._MARKER).stat().st_mtime

    def halb_loeschen(ziel: Path) -> None:
        (ziel / conftest._MARKER).unlink()
        raise OSError("Directory not empty")

    monkeypatch.setattr(conftest, "_pt_ordner_loeschen", halb_loeschen)
    conftest._reste_raeumen(tmp_path, inode_anteil=lambda _p: 0.10)

    marker = ordner / conftest._MARKER
    assert marker.is_file(), "Marker muss nach gescheitertem rmtree wieder da sein"
    assert marker.read_text(encoding="utf-8").strip() == str(tot)
    assert abs(marker.stat().st_mtime - alter_mtime) < 2.0


def test_marker_wird_nach_teilweisem_rmtree_bei_sessionfinish_neu_geschrieben(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scheitert das Löschen nach einem grünen Lauf, nachdem der Marker schon weg war,
    schreibt ``pytest_sessionfinish`` den Marker neu, damit der nächste Lauf den Rest räumt."""
    from types import SimpleNamespace

    ordner = _rest(tmp_path, "pt-gruenhalb", os.getpid(), alt=False)
    stash = pytest.Stash()
    stash[conftest._EIGENER_BASETEMP] = ordner
    session = SimpleNamespace(config=SimpleNamespace(stash=stash), exitstatus=0)

    def halb_loeschen(ziel: Path) -> None:
        (ziel / conftest._MARKER).unlink()
        raise OSError("Directory not empty")

    monkeypatch.setattr(conftest, "_pt_ordner_loeschen", halb_loeschen)
    conftest.pytest_sessionfinish(session, 0)  # type: ignore[arg-type]

    marker = ordner / conftest._MARKER
    assert marker.is_file(), "Marker muss nach gescheitertem Löschen wieder da sein"
    assert marker.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_inode_schwelle_und_messung_kommen_aus_einer_quelle() -> None:
    """Schwelle und Messung liegen an genau einer Stelle (``to_spawn.hausmeister``)."""
    from to_spawn import hausmeister

    assert conftest._INODE_GRENZE == hausmeister.INODE_WARN_ANTEIL == 0.70
    assert hausmeister.INODE_WARN_ANTEIL < hausmeister.INODE_SPERRE_ANTEIL < 1.0
    assert conftest._inode_anteil is hausmeister.inode_anteil


def test_hausmeister_start_erlaubt_warnt_raeumt_und_sperrt_bei_inodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hausmeister prüft neben dem Plattenplatz den Inode-Zähler der Temp-Wurzel (#355):
    ab ``INODE_WARN_ANTEIL`` räumt und warnt er, ab ``INODE_SPERRE_ANTEIL`` sperrt er Starts."""
    from to_spawn import hausmeister, hindernis

    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "0.80")  # Bytes reichlich frei
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(tmp_path / "h.jsonl"))

    aufrufe: list[bool] = []

    def rundgang_attrappe(*, loeschen: bool, jetzt: float | None = None) -> hausmeister.Rundgang:
        aufrufe.append(loeschen)
        return hausmeister.Rundgang(True, "")

    monkeypatch.setattr(hausmeister, "rundgang", rundgang_attrappe)

    # 1. Unter Warnschwelle: kein Rundgang, kein Hindernis, Start erlaubt
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_INODES", "0.50")
    ok, satz = hausmeister.start_erlaubt()
    assert ok, satz
    assert aufrufe == []
    assert hindernis.lesen() == []

    # 2. Über Warnschwelle (75 %): räumt auf, warnt ins Hindernis-Log, Start noch erlaubt
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_INODES", "0.75")
    ok, satz = hausmeister.start_erlaubt()
    assert ok and "Inodes" in satz, satz
    assert aufrufe == [True]
    assert [z["art"] for z in hindernis.lesen()] == ["temp_inodes_knapp"]

    # 3. Über Sperrschwelle (95 %): räumt auf, sperrt Start und meldet temp_inodes_voll
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_INODES", "0.95")
    ok, satz = hausmeister.start_erlaubt()
    assert not ok and "Inodes" in satz, satz
    assert aufrufe == [True, True]
    assert hindernis.lesen()[-1]["art"] == "temp_inodes_voll"


def test_hausmeister_rundgang_warnt_bei_knappen_temp_inodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Auch im regulären Takt (``aufraeumen_alles``) warnt der Hausmeister bei hohem Inode-Stand."""
    from to_spawn import hausmeister, hindernis

    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "0.80")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_INODES", "0.82")
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(tmp_path / "h.jsonl"))
    with caplog.at_level("WARNING", logger="to_spawn.hausmeister"):
        hausmeister.aufraeumen_alles(loeschen=False)
    assert any("Inodes" in r.getMessage() for r in caplog.records), caplog.text
    assert [z["art"] for z in hindernis.lesen()] == ["temp_inodes_knapp"]


def test_wache_raeumt_eigenen_lauf_ordner_nach_ende_auf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``skripte/wache.py`` hinterlässt nach dem Ende keinen ``wache-<spec>-*``-Ordner (#355)."""
    import importlib.util
    import sys
    from contextlib import nullcontext

    skill = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("wache_inode_355", skill / "skripte" / "wache.py")
    assert spec and spec.loader
    wache = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wache)

    temp = tmp_path / "tmp-wache"
    temp.mkdir()
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(temp))
    monkeypatch.setattr(wache.handoff_blick, "laeuft", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(wache.briefkasten_wecker, "laeuft", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(wache.waechter_lauf, "fahre", lambda **_k: 0)
    monkeypatch.setattr(wache, "REPO_ORDNER", tmp_path)
    monkeypatch.setattr(wache, "repo_slug_oder_abbruch", lambda: "o/r")
    monkeypatch.setattr(wache, "start_ordner", lambda: tmp_path)
    monkeypatch.setattr(wache, "auf_speicher_warten", lambda *_a, **_k: 0)
    monkeypatch.setattr(wache, "_nachstarter_starten", lambda *_a, **_k: None)
    monkeypatch.setattr(wache.config, "sicherstellen", lambda *_a, **_k: None)
    monkeypatch.setattr(wache.config, "lade", lambda *_a, **_k: {})
    monkeypatch.setattr(wache.startklar, "gate", lambda *_a, **_k: 0)
    monkeypatch.setattr(wache.context_mode, "pruefen", lambda **_k: tmp_path)
    monkeypatch.setattr(wache.vertrauen, "still_sicherstellen", lambda *_a, **_k: None)
    monkeypatch.setattr(wache.nest, "git_hooks_einrichten", lambda *_a, **_k: None)
    for name in ("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", "BAU_UMZUG_DATEI", "TO_SPAWN_WACHE_SPEC", wache.ABLOESE_ENV):
        monkeypatch.setenv(name, "")
    monkeypatch.setattr(sys, "argv", ["wache.py", "900"])

    assert wache.main() == 0
    reste = [p.name for p in temp.iterdir() if p.name.startswith("wache-900-")]
    assert reste == [], f"wache.py ließ Lauf-Ordner liegen: {reste}"


def test_wache_dry_run_legt_temp_im_konfigurierten_temp_ordner_statt_system_temp_an(
    tmp_path: Path,
) -> None:
    """``skripte/wache.py --dry-run`` nutzt ``temp_ordner`` statt System-Temp (#355, Probe 5: 3 wache-900*-Reste)."""
    import re
    import subprocess
    import sys

    skill = Path(__file__).resolve().parent.parent
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    sys_temp = tmp_path / "sys-temp"
    sys_temp.mkdir()
    platte = tmp_path / "platte"
    platte.mkdir()
    env = {
        **os.environ,
        "TO_SPAWN_REPO": str(repo),
        "TO_SPAWN_GH_REPO": "Shavy72/claude-skill-to-spawn",
        "TO_SPAWN_TEMP_ORDNER": str(platte),
        "TMPDIR": str(sys_temp),
        "TEMP": str(sys_temp),
        "TMP": str(sys_temp),
    }
    lauf = subprocess.run(
        [sys.executable, str(skill / "skripte" / "wache.py"), "900", "--dry-run"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert not list(sys_temp.glob("wache-900-*")), f"wache --dry-run schrieb in System-Temp: {list(sys_temp.iterdir())}"
    treffer = re.search(r"Temp:\s*(.+)", ausgabe)
    assert treffer is not None, ausgabe
    dry_ordner = Path(treffer.group(1).strip()).resolve()
    assert platte.resolve() in dry_ordner.parents, (dry_ordner, platte)


@pytest.mark.braucht("posix")
def test_hausmeister_rundgang_raeumt_tote_markierte_pt_reste_bei_inode_knappheit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hausmeister räumt im Takt tote markierte ``pt-*``-Reste bei Inode-Knappheit ab (#355)."""
    import json

    from to_spawn import hausmeister

    temp = tmp_path / "temp-wurzel"
    temp.mkdir()
    reste = _reste(temp)
    log_datei = tmp_path / "hausmeister.jsonl"
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(temp))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(log_datei))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "0.80")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_INODES", "0.80")

    # Probelauf löscht nichts.
    hausmeister.aufraeumen_alles(loeschen=False)
    assert all(p.is_dir() for p in reste.values())

    # Echter Lauf räumt den toten markierten Rest (> 10 min bei Inode-Knappheit) und loggt ihn.
    hausmeister.aufraeumen_alles(loeschen=True)
    assert not reste["mittel_tot"].exists(), "Hausmeister muss toten markierten pt-Rest bei Knappheit räumen"
    assert reste["frisch_tot"].is_dir(), "frischer toter Rest bleibt (Schonfrist)"
    assert reste["mittel_lebt"].is_dir(), "lebender Lauf bleibt geschützt"
    assert reste["fremd"].is_dir(), "unmarkierter Ordner ist fremd und bleibt"
    zeilen = [json.loads(z) for z in log_datei.read_text(encoding="utf-8").splitlines() if z.strip()]
    assert any(z.get("ordner") == str(reste["mittel_tot"]) and z.get("art") == "gelöscht" for z in zeilen), zeilen


@pytest.mark.braucht("posix")
def test_hausmeister_pt_reste_respektiert_normale_frist_und_rettet_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne Inode-Knappheit bleibt ein 20-min-Rest liegen; bei Teil-rmtree wird der Marker gerettet (#355)."""
    from to_spawn import hausmeister

    temp = tmp_path / "temp-wurzel"
    temp.mkdir()
    reste = _reste(temp)
    assert hausmeister.pt_reste_raeumen(temp, loeschen=True, inode_fn=lambda _p: 0.20) == []
    assert reste["mittel_tot"].is_dir(), "Ohne Knappheit gilt die 1-h-Schonfrist"

    alter_mtime = (reste["mittel_tot"] / hausmeister.PT_MARKER).stat().st_mtime
    orig_rmtree = hausmeister.shutil.rmtree

    def halb_weg(pfad: Path, *_a: object, **_k: object) -> None:
        (Path(pfad) / hausmeister.PT_MARKER).unlink()
        raise OSError("Directory not empty")

    monkeypatch.setattr(hausmeister.shutil, "rmtree", halb_weg)
    try:
        assert hausmeister.pt_reste_raeumen(temp, loeschen=True, inode_fn=lambda _p: 0.85) == []
    finally:
        monkeypatch.setattr(hausmeister.shutil, "rmtree", orig_rmtree)
    marker = reste["mittel_tot"] / hausmeister.PT_MARKER
    assert marker.is_file(), "Marker muss nach gescheitertem Hausmeister-rmtree erhalten bleiben"
    assert abs(marker.stat().st_mtime - alter_mtime) < 2.0


@pytest.mark.braucht("posix")
def test_autouse_hilfsordner_werden_nach_gruenem_test_sofort_geraeumt(tmp_path: Path) -> None:
    """Unter Linux räumt ``tests/conftest.py`` Autouse-Hilfsordner (``heim``, ``hausmeister`` usw.)
    nach jedem grünen Test sofort ab, statt ~185k Inodes bis Suite-Ende anzusammeln (#355)."""
    import subprocess
    import sys

    skill = Path(__file__).resolve().parent.parent
    mini = tmp_path / "mini"
    mini.mkdir()
    (mini / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (mini / "conftest.py").write_text(
        f"import importlib.util\n"
        f"_s = importlib.util.spec_from_file_location('_c355', {str(skill / 'tests' / 'conftest.py')!r})\n"
        f"_m = importlib.util.module_from_spec(_s)\n"
        f"_s.loader.exec_module(_m)\n"
        f"for _n in dir(_m):\n"
        f"    if not _n.startswith('__'):\n"
        f"        globals()[_n] = getattr(_m, _n)\n",
        encoding="utf-8",
    )
    spur_gruen = tmp_path / "heim_gruen.txt"
    spur_rot = tmp_path / "heim_rot.txt"
    (mini / "test_mini.py").write_text(
        f"from pathlib import Path\n\n"
        f"def test_1_gruen(test_heim):\n"
        f"    (test_heim / 'nur_test_1.txt').write_text('1', encoding='utf-8')\n"
        f"    Path({str(spur_gruen)!r}).write_text(str(test_heim), encoding='utf-8')\n"
        f"    assert test_heim.is_dir()\n\n"
        f"def test_2_prueft_dass_test_1_heim_weg_ist():\n"
        f"    alt = Path(Path({str(spur_gruen)!r}).read_text(encoding='utf-8'))\n"
        f"    assert not (alt / 'nur_test_1.txt').exists(), f'Autouse-Heim von test_1 blieb liegen: {{alt}}'\n\n"
        f"def test_3_rot(test_heim):\n"
        f"    (test_heim / 'nur_test_3.txt').write_text('3', encoding='utf-8')\n"
        f"    Path({str(spur_rot)!r}).write_text(str(test_heim), encoding='utf-8')\n"
        f"    assert False, 'absichtlich rot'\n",
        encoding="utf-8",
    )
    temp = tmp_path / "t"
    temp.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "TO_SPAWN_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(skill), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["TMPDIR"] = env["TEMP"] = env["TMP"] = str(temp)
    lauf = subprocess.run(
        [sys.executable, "-m", "pytest", str(mini), "-q", "-p", "no:cacheprovider", "-p", "no:xdist"],
        cwd=str(mini),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert "test_2_prueft_dass_test_1_heim_weg_ist" not in lauf.stdout, lauf.stdout + lauf.stderr
    rot_heim = Path(spur_rot.read_text(encoding="utf-8"))
    assert (rot_heim / "nur_test_3.txt").is_file(), "Roter Autouse-Heimordner bleibt zum Nachsehen"
    pt_ordner = rot_heim.parent
    echte = [p.name for p in pt_ordner.glob("heim*") if not p.is_symlink()]
    assert echte == [rot_heim.name], echte


@pytest.mark.braucht("posix")
def test_autouse_raeumung_laeuft_erst_nach_monkeypatch_undo(tmp_path: Path) -> None:
    """Ein Test, der ``shutil.rmtree``, ``os.scandir`` oder ``os.open`` per ``monkeypatch``
    ersetzt, darf im Teardown der Autouse-Fixtures keinen Fehler werfen; geräumt wird erst
    nach ``monkeypatch.undo()`` (#355 Fix-Runde 2)."""
    import subprocess
    import sys

    skill = Path(__file__).resolve().parent.parent
    mini = tmp_path / "mini_mp"
    mini.mkdir()
    (mini / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (mini / "conftest.py").write_text(
        f"import importlib.util\n"
        f"_s = importlib.util.spec_from_file_location('_c355_mp', {str(skill / 'tests' / 'conftest.py')!r})\n"
        f"_m = importlib.util.module_from_spec(_s)\n"
        f"_s.loader.exec_module(_m)\n"
        f"for _n in dir(_m):\n"
        f"    if not _n.startswith('__'):\n"
        f"        globals()[_n] = getattr(_m, _n)\n",
        encoding="utf-8",
    )
    spur = tmp_path / "heim_mp.txt"
    (mini / "test_mp.py").write_text(
        f"import os\n"
        f"import shutil\n"
        f"from pathlib import Path\n\n"
        f"def test_1_patched_fs(test_heim, monkeypatch):\n"
        f"    (test_heim / 'marker.txt').write_text('x', encoding='utf-8')\n"
        f"    Path({str(spur)!r}).write_text(str(test_heim), encoding='utf-8')\n"
        f"    def kaputt(*_a, **_k):\n"
        f"        raise RuntimeError('monkeypatch noch aktiv')\n"
        f"    monkeypatch.setattr(shutil, 'rmtree', kaputt)\n"
        f"    monkeypatch.setattr(os, 'scandir', kaputt)\n"
        f"    monkeypatch.setattr(os, 'open', kaputt)\n\n"
        f"def test_2_heim_ist_trotzdem_weg():\n"
        f"    alt = Path(Path({str(spur)!r}).read_text(encoding='utf-8'))\n"
        f"    assert not (alt / 'marker.txt').exists(), f'Autouse-Heim blieb liegen: {{alt}}'\n",
        encoding="utf-8",
    )
    temp = tmp_path / "t"
    temp.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "TO_SPAWN_"))}
    env["PYTHONPATH"] = os.pathsep.join([str(skill), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env["TMPDIR"] = env["TEMP"] = env["TMP"] = str(temp)
    lauf = subprocess.run(
        [sys.executable, "-m", "pytest", str(mini), "-q", "-p", "no:cacheprovider", "-p", "no:xdist"],
        cwd=str(mini),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
