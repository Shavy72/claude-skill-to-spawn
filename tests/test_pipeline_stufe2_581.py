"""#797 (#581 Teil 1): Pipeline-Vorlage Stufe 2 (pytest-split + xdist), Staging-Probe-Schalter und Bau-Server-Doku.

Prüft plattformübergreifend für Linux und Windows (ohne externe YAML-Pflicht und ohne ``testmon``):
- ``to_spawn.pruef.pipeline_stufe2`` erzeugt die wiederverwendbare ``workflow_call``-Vorlage für
  Stufe 2 (Aufteilung auf >= 2 Runner mit ``pytest-split`` + ``pytest-xdist``, bedingter
  Staging-Probe-Schritt, Kopf-Doku mit Zielbild und Rolle des Bau-Servers) sowie den
  Eigenanschluss für das Skill-Repo.
- ``tests/conftest.py`` (``braucht_fehlt``) schließt Bau-Server-Tests (``braucht("server")`` /
  ``braucht("bau-server")``) auf GitHub-Runnern, in generischer CI und auf Nicht-Linux sauber aus.
- Echte Weg-Tests auf frischen Repos für:
  1. Verteilte Ausführung über 2 Teile (disjunkte Teilmengen ergeben die komplette Suite,
     Bau-Server-Tests und Auslassungen bleiben ausgeschlossen).
  2. Staging-Probe-Schalter für beide Werte (``mit-staging-app=false`` überspringt,
     ``mit-staging-app=true`` führt den Probe-Befehl aus; fehlender Befehl fail-closed).
- Verknüpfung mit ``to_spawn.pruefplan.welche`` und ``to_spawn.config.hat_staging`` (eine Stelle).
- ``docs/PIPELINE_VORLAGE.md`` dokumentiert Zielbild, Rolle des Bau-Servers und Stufe-2-Eingaben.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from conftest import braucht_fehlt
from to_spawn import prozessbaum, pruefplan
from to_spawn.pruef import pipeline_stufe2, pipeline_vorlage


def _schreibe(ordner: Path, rel: str, inhalt: str) -> Path:
    ziel = ordner / rel
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(inhalt, encoding="utf-8")
    return ziel


def _cli(
    modul: str, *args: str, cwd: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    umg = {**os.environ, "PYTHONPATH": str(SKILL)}
    if extra_env:
        umg.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", modul, *args],
        cwd=cwd,
        env=umg,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **prozessbaum.fensterlos(),
    )


def _stufe2_probe_repo(ordner: Path, spur_datei: Path) -> Path:
    """Kleines Test-Repo mit 4 normalen Tests, 2 Bau-Server-Tests und 1 Auslassung."""
    conftest_code = textwrap.dedent(f"""\
        import os
        import sys
        import pytest

        def pytest_configure(config):
            config.addinivalue_line("markers", "braucht(*namen): Bedarf des Tests")
            config.addinivalue_line("markers", "braucht_server: nur Bau-Server")

        def pytest_collection_modifyitems(config, items):
            auf_github = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
            aus = os.environ.get("TO_SPAWN_BAU_SERVER") == "0"
            for item in items:
                for marke in item.iter_markers("braucht"):
                    if "server" in marke.args and (auf_github or aus or sys.platform != "linux"):
                        item.add_marker(pytest.mark.skip(reason="braucht server: nur Bau-Server"))
                if item.get_closest_marker("braucht_server") and (auf_github or aus or sys.platform != "linux"):
                    item.add_marker(pytest.mark.skip(reason="braucht_server: nur Bau-Server"))

        def spur(name: str) -> None:
            with open({str(spur_datei)!r}, "a", encoding="utf-8") as handle:
                handle.write(name + "\\n")
        """)
    test_code = textwrap.dedent("""\
        import pytest
        from conftest import spur


        def test_eins() -> None:
            spur("test_eins")
            assert 1 + 1 == 2


        def test_zwei() -> None:
            spur("test_zwei")
            assert 2 + 2 == 4


        def test_drei() -> None:
            spur("test_drei")
            assert 3 + 3 == 6


        def test_vier() -> None:
            spur("test_vier")
            assert 4 + 4 == 8


        def test_ausgelassen() -> None:
            spur("test_ausgelassen")
            assert False, "muss über auslassungen abgewählt werden"


        @pytest.mark.braucht("server")
        def test_nur_bau_server_braucht() -> None:
            spur("test_nur_bau_server_braucht")
            assert True


        @pytest.mark.braucht_server
        def test_nur_bau_server_marker() -> None:
            spur("test_nur_bau_server_marker")
            assert True
        """)
    ordner.mkdir(parents=True, exist_ok=True)
    _schreibe(ordner, "pytest.ini", "[pytest]\ntestpaths = tests\n")
    _schreibe(ordner, "tests/conftest.py", conftest_code)
    _schreibe(ordner, "tests/test_suite.py", test_code)
    _schreibe(
        ordner,
        "auslassungen.txt",
        "# Auslassung für Stufe-2-Probe\ntests/test_suite.py::test_ausgelassen  # Absichtlich rot\n",
    )
    return ordner


# --- 1. workflow_call-Vorlage Stufe 2, Kopf-Doku & Eigenanschluss --------------------------------


def test_stufe2_vorlage_yaml_workflow_call_eingaben_split_xdist_und_kopf_doku() -> None:
    text = pipeline_stufe2.vorlage_stufe2_yaml()
    assert pipeline_stufe2.pruefe_stufe2_vorlage_text(text) == []
    assert "workflow_call:" in text
    assert "permissions:" in text
    assert "contents: read" in text
    # Kopf-Doku: Zielbild + Rolle des Bau-Servers
    assert "Triage" in text
    assert "Beweis" in text
    assert "Audits" in text
    assert "Claude-Sessions" in text
    assert "braucht Server" in text
    # pytest-split + pytest-xdist + Staging-Probe-Schalter
    assert "pytest-split" in text
    assert "pytest-xdist" in text
    assert "inputs.mit-staging-app" in text
    assert "testmon" not in text.lower()
    for eingabe in pipeline_stufe2.EINGABEN_STUFE2:
        assert f"      {eingabe.name}:" in text
        assert eingabe.beschreibung in text
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return
    daten = yaml.safe_load(text)
    assert isinstance(daten, dict)
    on_block = daten.get("on") or daten.get(True)
    assert isinstance(on_block, dict) and "workflow_call" in on_block
    eingaben = on_block["workflow_call"]["inputs"]
    for eingabe in pipeline_stufe2.EINGABEN_STUFE2:
        assert eingabe.name in eingaben
        assert eingaben[eingabe.name]["description"]
        assert eingaben[eingabe.name]["type"] == eingabe.typ
    assert eingaben["teile"]["default"] >= 2
    assert set(daten["jobs"]) == {"vorbereiten", "stufe-2", "staging-probe"}
    assert "mit-staging-app" in str(daten["jobs"]["staging-probe"].get("if", ""))


def test_stufe2_eigenanschluss_yaml_mindestens_zwei_runner() -> None:
    text = pipeline_stufe2.eigenanschluss_stufe2_yaml()
    assert "uses: ./.github/workflows/pipeline-stufe-2.yml" in text
    assert "teile: 2" in text
    assert 'xdist-worker: "auto"' in text
    assert "mit-staging-app: false" in text
    assert "server-marker-ausschliessen: true" in text
    assert "testmon" not in text.lower()
    with pytest.raises(ValueError, match="mindestens 2"):
        pipeline_stufe2.eigenanschluss_stufe2_yaml(teile=1)
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return
    daten = yaml.safe_load(text)
    assert isinstance(daten, dict)
    job = daten["jobs"]["pipeline-stufe-2"]
    assert job["uses"] == "./.github/workflows/pipeline-stufe-2.yml"
    assert job["with"]["teile"] >= 2


def test_pruefe_stufe2_vorlage_text_erkennt_luecken() -> None:
    kaputt = "name: Kaputt\non:\n  push:\njobs:\n  stufe-2:\n    steps:\n      - run: pytest --testmon\n"
    befunde = pipeline_stufe2.pruefe_stufe2_vorlage_text(kaputt)
    assert any("workflow_call" in b for b in befunde)
    assert any("pytest-split" in b for b in befunde)
    assert any("staging-probe" in b for b in befunde)
    assert any("Bau-Server" in b or "Claude-Sessions" in b for b in befunde)
    assert any("testmon" in b.lower() for b in befunde)


# --- 2. Matrix & Befehlsbau (pytest-split + xdist + Server-Ausschluss) --------------------------


def test_teile_matrix_verlangt_mindestens_zwei_runner() -> None:
    assert pipeline_stufe2.teile_matrix(2) == [1, 2]
    assert pipeline_stufe2.teile_matrix(4) == [1, 2, 3, 4]
    for ungueltig in (0, 1, -2, True):
        with pytest.raises(ValueError):
            pipeline_stufe2.teile_matrix(ungueltig)  # type: ignore[arg-type]


def test_stufe2_befehl_baut_pytest_split_xdist_server_ausschluss_und_auslassungen(tmp_path: Path) -> None:
    dauer = _schreibe(tmp_path, ".test_durations", '{"tests/test_a.py::test_1": 0.5}\n')
    befehl = pipeline_stufe2.stufe2_befehl(
        python=sys.executable,
        wurzel=tmp_path,
        splits=3,
        group=2,
        test_pfade=("tests",),
        xdist_worker="auto",
        dauer_datei=dauer,
        auslassungen=(("tests/test_a.py::test_skip", "Grund"),),
        server_ausschliessen=True,
        junitxml=tmp_path / "junit.xml",
        split_modus="pytest-split",
    )
    assert "--splits" in befehl and "3" in befehl
    assert "--group" in befehl and "2" in befehl
    assert "--durations-path" in befehl
    assert "--splitting-algorithm" in befehl and "least_duration" in befehl
    assert "-n" in befehl and "auto" in befehl
    assert "--dist" in befehl and "worksteal" in befehl
    assert "-m" in befehl and f"not {pipeline_stufe2.SERVER_MARKER}" in befehl
    assert "--deselect" in befehl and "tests/test_a.py::test_skip" in befehl
    assert any(arg.startswith("--junitxml=") for arg in befehl)

    kombi = pipeline_stufe2.stufe2_befehl(
        python=sys.executable,
        wurzel=tmp_path,
        splits=2,
        group=1,
        test_pfade=("tests",),
        xdist_worker="2",
        extra_args=["-m", "not langsam"],
        server_ausschliessen=True,
        split_modus="pytest-split",
    )
    pytest_args = kombi[kombi.index("pytest") + 1 :]
    m_idx = pytest_args.index("-m")
    assert pytest_args[m_idx + 1] == f"(not langsam) and not {pipeline_stufe2.SERVER_MARKER}"

    with pytest.raises(ValueError):
        pipeline_stufe2.stufe2_befehl(
            python=sys.executable,
            wurzel=tmp_path,
            splits=1,
            group=1,
            test_pfade=("tests",),
            split_modus="pytest-split",
        )
    with pytest.raises(ValueError):
        pipeline_stufe2.stufe2_befehl(
            python=sys.executable,
            wurzel=tmp_path,
            splits=2,
            group=3,
            test_pfade=("tests",),
            split_modus="pytest-split",
        )


# --- 3. conftest.braucht_fehlt: „braucht Server“ auf GitHub-Runnern sauber ausgeschlossen -------


def test_conftest_braucht_server_auf_github_runner_und_windows_ausgeschlossen() -> None:
    assert braucht_fehlt("server", "linux", umgebung={}) is None
    assert braucht_fehlt("bau-server", "linux", umgebung={}) is None
    gh_grund = braucht_fehlt("server", "linux", umgebung={"GITHUB_ACTIONS": "true"})
    assert gh_grund and "Bau-Server" in gh_grund
    assert braucht_fehlt("server", "linux", umgebung={"GITHUB_ACTIONS": "true", "TO_SPAWN_BAU_SERVER": "1"})
    assert braucht_fehlt("server", "linux", umgebung={"TO_SPAWN_BAU_SERVER": "0"})
    assert braucht_fehlt("server", "linux", umgebung={"CI": "true"})
    assert braucht_fehlt("server", "linux", umgebung={"CI": "true", "TO_SPAWN_BAU_SERVER": "1"}) is None
    win_grund = braucht_fehlt("server", "win32", umgebung={})
    assert win_grund and "Bau-Server" in win_grund


# --- 4. Weg-Test: Aufteilung auf 2 Runner + Bau-Server-Tests ausgeschlossen ---------------------


def test_weg_stufe2_aufteilung_auf_zwei_runner_und_server_tests_ausgeschlossen(tmp_path: Path) -> None:
    spur = tmp_path / "spur.txt"
    repo = _stufe2_probe_repo(tmp_path / "stufe2_repo", spur)
    sum1 = tmp_path / "summary-1.md"
    sum2 = tmp_path / "summary-2.md"

    erg1 = pipeline_stufe2.stufe2_lauf(
        repo,
        splits=2,
        group=1,
        xdist_worker="0",
        auslassungen=repo / "auslassungen.txt",
        server_ausschliessen=True,
        summary_pfad=sum1,
    )
    assert erg1.ok is True, erg1.ausgabe
    assert erg1.exit_code == 0
    nach_1 = spur.read_text(encoding="utf-8").splitlines()
    assert len(nach_1) >= 1

    erg2 = pipeline_stufe2.stufe2_lauf(
        repo,
        splits=2,
        group=2,
        xdist_worker="0",
        auslassungen=repo / "auslassungen.txt",
        server_ausschliessen=True,
        summary_pfad=sum2,
    )
    assert erg2.ok is True, erg2.ausgabe
    assert erg2.exit_code == 0
    alle = spur.read_text(encoding="utf-8").splitlines()
    nur_2 = alle[len(nach_1) :]
    assert len(nur_2) >= 1
    assert set(nach_1).isdisjoint(set(nur_2))
    assert set(alle) == {"test_eins", "test_zwei", "test_drei", "test_vier"}
    assert "test_nur_bau_server_braucht" not in alle
    assert "test_nur_bau_server_marker" not in alle
    assert "test_ausgelassen" not in alle
    assert "Teil 1/2" in sum1.read_text(encoding="utf-8")
    assert "Teil 2/2" in sum2.read_text(encoding="utf-8")


# --- 5. Weg-Test: Staging-Probe-Schalter für beide Werte ----------------------------------------


def test_weg_staging_probe_schalter_beide_werte(tmp_path: Path) -> None:
    repo = tmp_path / "staging_repo"
    repo.mkdir()
    spur = tmp_path / "staging_spur.txt"
    probe_skript = _schreibe(
        repo,
        "staging_probe.py",
        textwrap.dedent(f"""\
            from pathlib import Path
            Path({str(spur)!r}).write_text("staging-probe-ok\\n", encoding="utf-8")
            print("Staging-Probe grün")
            """),
    )
    probe_cmd = f'"{sys.executable}" "{probe_skript.as_posix()}"'
    summary = tmp_path / "staging_summary.md"

    # Wert 1: ohne Staging-App -> Schritt läuft NICHT (API + CLI unter cp1252)
    aus_erg = pipeline_stufe2.staging_probe_lauf(
        repo,
        mit_staging_app=False,
        befehl=probe_cmd,
        summary_pfad=summary,
    )
    assert aus_erg.ok is True
    assert aus_erg.exit_code == 0
    assert aus_erg.ausgefuehrt is False
    assert aus_erg.uebersprungen is True
    assert not spur.exists()

    cli_aus = _cli(
        "to_spawn.pruef.pipeline_stufe2",
        "staging-probe",
        "--repo",
        str(repo),
        "--ohne-staging-app",
        "--befehl",
        probe_cmd,
        "--json",
        cwd=repo,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert cli_aus.returncode == 0, cli_aus.stdout + cli_aus.stderr
    daten_aus = json.loads(cli_aus.stdout)
    assert daten_aus["ausgefuehrt"] is False
    assert daten_aus["uebersprungen"] is True
    assert not spur.exists()

    # Wert 2: mit Staging-App -> Schritt läuft (CLI unter cp1252)
    cli_an = _cli(
        "to_spawn.pruef.pipeline_stufe2",
        "staging-probe",
        "--repo",
        str(repo),
        "--mit-staging-app",
        "--befehl",
        probe_cmd,
        "--summary-pfad",
        str(summary),
        "--json",
        cwd=repo,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert cli_an.returncode == 0, cli_an.stdout + cli_an.stderr
    daten_an = json.loads(cli_an.stdout)
    assert daten_an["ok"] is True
    assert daten_an["ausgefuehrt"] is True
    assert daten_an["uebersprungen"] is False
    assert spur.read_text(encoding="utf-8").strip() == "staging-probe-ok"

    # Fail-closed: mit Staging-App ohne Befehl -> Gate-Fehler (Exit 2)
    fehlend = pipeline_stufe2.staging_probe_lauf(repo, mit_staging_app=True, befehl="")
    assert fehlend.ok is False
    assert fehlend.exit_code == 2

    # Konfig-Ableitung über config.hat_staging (eine Stelle)
    _schreibe(repo, ".to-spawn/config.json", json.dumps({"staging": {"ohne": True}}))
    spur.unlink()
    konfig_ohne = pipeline_stufe2.staging_probe_lauf(repo, mit_staging_app=None, befehl=probe_cmd)
    assert konfig_ohne.ok is True and konfig_ohne.uebersprungen is True and not spur.exists()

    _schreibe(repo, ".to-spawn/config.json", json.dumps({"staging": {"url": "https://staging.example.test"}}))
    konfig_mit = pipeline_stufe2.staging_probe_lauf(repo, mit_staging_app=None, befehl=probe_cmd)
    assert konfig_mit.ok is True and konfig_mit.ausgefuehrt is True and spur.is_file()


# --- 6. Verknüpfung mit pruefplan.welche + config.hat_staging -----------------------------------


def test_ist_stufe2_dran_nutzt_pruefplan_und_hat_staging() -> None:
    mit = {"staging": {"url": "https://staging.example.test"}}
    ohne = {"staging": {"ohne": True}}

    assert pipeline_stufe2.ist_stufe2_dran(mit, pruefplan.SpecStand(pruefplan.Moment.TICKET)) == (False, True)
    assert pipeline_stufe2.ist_stufe2_dran(mit, pruefplan.SpecStand(pruefplan.Moment.ALLE_AUF_STAGING)) == (True, True)
    assert pipeline_stufe2.ist_stufe2_dran(
        mit, pruefplan.SpecStand(pruefplan.Moment.ALLE_AUF_STAGING, komplett_lief=True)
    ) == (False, True)

    assert pipeline_stufe2.ist_stufe2_dran(ohne, pruefplan.SpecStand(pruefplan.Moment.ALLE_AUF_STAGING)) == (
        False,
        False,
    )
    assert pipeline_stufe2.ist_stufe2_dran(ohne, pruefplan.SpecStand(pruefplan.Moment.VOR_LIVE)) == (True, False)
    assert pipeline_stufe2.ist_stufe2_dran(
        ohne, pruefplan.SpecStand(pruefplan.Moment.VOR_LIVE, komplett_lief=True)
    ) == (
        False,
        False,
    )


# --- 7. CLI & Doku (Zielbild + Bau-Server-Rolle) ------------------------------------------------


def test_cli_vorlage_matrix_und_pruefen_weg(tmp_path: Path) -> None:
    ziel = tmp_path / "pipeline-stufe-2.yml"
    schreib = _cli(
        "to_spawn.pruef.pipeline_stufe2",
        "vorlage",
        "--ausgabe",
        str(ziel),
        cwd=SKILL,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert schreib.returncode == 0, schreib.stdout + schreib.stderr
    assert ziel.is_file()

    pruef = _cli(
        "to_spawn.pruef.pipeline_stufe2",
        "vorlage",
        "--pruefen",
        str(ziel),
        cwd=SKILL,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert pruef.returncode == 0, pruef.stdout + pruef.stderr

    mat = _cli(
        "to_spawn.pruef.pipeline_stufe2",
        "matrix",
        "--teile",
        "3",
        cwd=SKILL,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert mat.returncode == 0, mat.stdout + mat.stderr
    assert json.loads(mat.stdout) == [1, 2, 3]

    # Auch über pipeline_vorlage --stufe 2 erreichbar
    via_rahmen = _cli(
        "to_spawn.pruef.pipeline_vorlage",
        "vorlage",
        "--stufe",
        "2",
        cwd=SKILL,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert via_rahmen.returncode == 0, via_rahmen.stdout + via_rahmen.stderr
    assert pipeline_stufe2.pruefe_stufe2_vorlage_text(via_rahmen.stdout) == []


def test_doku_rolle_bau_server_und_zielbild_vorhanden() -> None:
    doku = SKILL / "docs" / "PIPELINE_VORLAGE.md"
    assert doku.is_file()
    inhalt = doku.read_text(encoding="utf-8")
    assert "Zielbild" in inhalt
    assert "Triage" in inhalt
    assert "Beweis" in inhalt
    assert "Audits" in inhalt
    assert "Bau-Server" in inhalt
    assert "Claude-Sessions" in inhalt
    assert "braucht Server" in inhalt
    assert "pytest-split" in inhalt
    assert "pytest-xdist" in inhalt
    assert "mit-staging-app" in inhalt
    assert "testmon" not in inhalt.lower()
    for eingabe in pipeline_stufe2.EINGABEN_STUFE2:
        assert eingabe.name in inhalt
    # Auch die gemeinsame Kopf-Doku aus pipeline_vorlage enthält das Zielbild
    assert any("Claude-Sessions" in z for z in pipeline_vorlage.KOPF_DOKU_ZEILEN)


def test_stufe2_workflows_im_repo_eingehaengt_und_aktuell() -> None:
    """#581: Vorlage und Eigenanschluss Stufe 2 liegen unter .github/workflows/ und entsprechen dem Generator."""
    vorlage = SKILL / ".github" / "workflows" / "pipeline-stufe-2.yml"
    eigen = SKILL / ".github" / "workflows" / "pipeline-stufe-2-eigen.yml"
    assert vorlage.is_file(), f"Stufe-2-Vorlage fehlt: {vorlage}"
    assert eigen.is_file(), f"Stufe-2-Eigenanschluss fehlt: {eigen}"

    vorlage_text = vorlage.read_text(encoding="utf-8")
    eigen_text = eigen.read_text(encoding="utf-8")

    assert vorlage_text == pipeline_stufe2.vorlage_stufe2_yaml()
    assert eigen_text == pipeline_stufe2.eigenanschluss_stufe2_yaml(teile=2)
    assert pipeline_stufe2.pruefe_stufe2_vorlage_text(vorlage_text) == []
    # In GitHub Actions (CI=true) verlangen u. a. test_pruef_sofort_3b und test_ruff_format_190 ruff.
    assert "pytest-split pytest-xdist ruff" in vorlage_text
    assert "uses: ./.github/workflows/pipeline-stufe-2.yml" in eigen_text
    assert "teile: 2" in eigen_text
    assert 'auslassungen: ".github/workflows/auslassungen.txt"' in eigen_text
    assert "mit-staging-app: false" in eigen_text
    assert "server-marker-ausschliessen: true" in eigen_text


def test_stufe2_eigenanschluss_verbietet_pull_request_und_push(tmp_path: Path) -> None:
    """#581: Stufe 2 läuft genau einmal je Spec vor Live — Eigenanschluss nur per workflow_dispatch, nie pull_request/push."""
    eigen_pfad = SKILL / ".github" / "workflows" / "pipeline-stufe-2-eigen.yml"
    assert eigen_pfad.is_file(), f"Stufe-2-Eigenanschluss fehlt: {eigen_pfad}"

    for quelle, text in (
        ("Generator", pipeline_stufe2.eigenanschluss_stufe2_yaml(teile=2)),
        ("Repo-Workflow", eigen_pfad.read_text(encoding="utf-8")),
    ):
        assert "workflow_dispatch:" in text, f"{quelle}: workflow_dispatch fehlt"
        assert "pull_request" not in text, f"{quelle}: verbotener Auslöser pull_request im Stufe-2-Eigenanschluss"
        assert "\n  push:" not in text, f"{quelle}: verbotener Auslöser push im Stufe-2-Eigenanschluss"
        assert pipeline_stufe2.pruefe_eigenanschluss_stufe2_text(text) == []
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            continue
        daten = yaml.safe_load(text)
        assert isinstance(daten, dict)
        on_block = daten.get("on") if "on" in daten else daten.get(True)
        assert isinstance(on_block, dict), f"{quelle}: on-Block muss Mapping sein"
        assert set(on_block) == {"workflow_dispatch"}, f"{quelle}: unerlaubte Auslöser {set(on_block)!r}"

    mit_pr = pipeline_stufe2.eigenanschluss_stufe2_yaml(teile=2).replace(
        "  workflow_dispatch:\n",
        "  workflow_dispatch:\n  pull_request:\n",
    )
    befunde_pr = pipeline_stufe2.pruefe_eigenanschluss_stufe2_text(mit_pr)
    assert any("pull_request" in b for b in befunde_pr)

    mit_push = pipeline_stufe2.eigenanschluss_stufe2_yaml(teile=2).replace(
        "  workflow_dispatch:\n",
        "  workflow_dispatch:\n  push:\n    branches: [main]\n",
    )
    befunde_push = pipeline_stufe2.pruefe_eigenanschluss_stufe2_text(mit_push)
    assert any("push" in b for b in befunde_push)

    # CLI --eigenanschluss --pruefen akzeptiert den sauberen Workflow und lehnt pull_request/push ab.
    ok_cli = _cli(
        "to_spawn.pruef.pipeline_stufe2",
        "vorlage",
        "--eigenanschluss",
        "--pruefen",
        str(eigen_pfad),
        cwd=SKILL,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert ok_cli.returncode == 0, ok_cli.stdout + ok_cli.stderr

    kaputt_pfad = tmp_path / "eigen_mit_push.yml"
    kaputt_pfad.write_text(mit_push, encoding="utf-8")
    rot_cli = _cli(
        "to_spawn.pruef.pipeline_stufe2",
        "vorlage",
        "--eigenanschluss",
        "--pruefen",
        str(kaputt_pfad),
        cwd=SKILL,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert rot_cli.returncode == 1
    assert "push" in rot_cli.stderr
