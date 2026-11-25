"""#794 (#580 Teil 1): Test-Schutz-Prüfung (AST-Diff) und einheitlicher Stufe-0-Ablauf.

Prüft plattformübergreifend (Linux und Windows):
- AST-Diff-Test-Schutz (``to_spawn/pruef/test_schutz.py``): erkennt gelöschte Testdateien,
  entfernte Tests, deaktivierte Tests (``skip``/``xfail``), entfernte Prüfungen
  (``assert``, ``pytest.raises``, ``unittest``-Asserts), entschärfte Prüfungen
  (``assert True``, ``or``-Aufweichung, reduziertes ``parametrize``) sowie geänderte
  Grenzwerte (Vergleichszahlen, Operatoren, ``pytest.approx``, Grenzwert-Konstanten).
- Normaler Diff (Code-Änderung, neuer Test, zusätzlicher Assert, Kommentar/Formatierung)
  bleibt grün.
- Stufe-0-Konfiguration unter ``pruefung.*`` (``to_spawn/config.py``) ohne Änderung von
  ``DEFAULTS["pruefung"]``.
- Einheitlicher Stufe-0-Ablauf (``to_spawn/pruef/sofort.py``): ruff, konfigurierbare
  Typ-Prüfung und Test-Schutz als ein Schritt vor dem Push und in der CI (CLI + Python-API).
- Kein ``testmon`` im Produktivcode.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import config, prozessbaum
from to_spawn.pruef import sofort, test_schutz


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
        **prozessbaum.fensterlos(),
    )


def _probe_repo(ordner: Path, *, konfig: dict[str, Any] | None = None) -> Path:
    """Frisches Python-Repo mit Code, Tests und Tag ``base``."""
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "rechnen.py").write_text(
        textwrap.dedent("""\
            def addiere(a: int, b: int) -> int:
                return a + b


            def teile(a: float, b: float) -> float:
                if b == 0:
                    raise ValueError("Teilung durch Null")
                return a / b
            """),
        encoding="utf-8",
    )
    (ordner / "tests").mkdir()
    (ordner / "tests" / "test_rechnen.py").write_text(
        textwrap.dedent("""\
            import pytest
            from rechnen import addiere, teile

            MAX_DAUER_S = 10


            def test_addiere() -> None:
                assert addiere(1, 2) == 3
                assert addiere(2, 3) < 10


            def test_teile_null() -> None:
                with pytest.raises(ValueError):
                    teile(1.0, 0.0)


            def test_schwelle() -> None:
                grenze = 5
                assert addiere(1, 2) <= grenze
                assert addiere(1, 2) < MAX_DAUER_S


            @pytest.mark.parametrize(("a", "b", "erwartet"), [(1, 2, 3), (2, 2, 4)])
            def test_paare(a: int, b: int, erwartet: int) -> None:
                assert addiere(a, b) == erwartet
            """),
        encoding="utf-8",
    )
    if konfig is not None:
        (ordner / ".to-spawn").mkdir(exist_ok=True)
        (ordner / ".to-spawn" / "config.json").write_text(json.dumps(konfig), encoding="utf-8")
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "base")
    _git(ordner, "tag", "base")
    return ordner


def _commit(ordner: Path, datei: str, text: str) -> None:
    ziel = ordner / datei
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(text, encoding="utf-8")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", f"ändert {datei}")


def _cli(
    modul: str, *args: str, cwd: Path, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SKILL) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", modul, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **prozessbaum.fensterlos(),
    )


# --- AST-Diff Test-Schutz: normale Diffs bleiben grün ------------------------------------------


def test_normaler_code_diff_und_neuer_test_sind_gruen(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "normal")
    _commit(
        repo,
        "rechnen.py",
        textwrap.dedent("""\
            def addiere(a: int, b: int) -> int:
                return int(a + b)


            def teile(a: float, b: float) -> float:
                if b == 0:
                    raise ValueError("Teilung durch Null")
                return a / b
            """),
    )
    alt_test = (repo / "tests" / "test_rechnen.py").read_text(encoding="utf-8")
    neu_test = (
        "# Zusätzlicher Kommentar\n"
        + alt_test.replace(
            "assert addiere(2, 3) < 10",
            'assert addiere(2, 3) < 10, "geänderter Hinweis"\n    assert addiere(0, 0) == 0',
        ).replace(
            "[(1, 2, 3), (2, 2, 4)]",
            "[(1, 2, 3), (2, 2, 4), (3, 4, 7)]",
        )
        + "\n\ndef test_neu() -> None:\n    assert addiere(5, 5) == 10\n"
    )
    _commit(repo, "tests/test_rechnen.py", neu_test)
    _commit(repo, "tests/test_extra.py", "def test_extra() -> None:\n    assert True is not False\n")

    erg = test_schutz.pruefe_diff(repo, "base", "HEAD")
    assert erg.ok is True, erg.bericht()
    assert erg.exit_code == 0
    assert erg.verstoesse == ()
    assert erg.zaehler.entfernte_tests == 0
    assert erg.zaehler.entfernte_asserts == 0
    assert erg.zaehler.geaenderte_grenzwerte == 0


# --- AST-Diff Test-Schutz: entfernte Prüfungen und Tests sind rot ------------------------------


def test_entfernter_assert_ist_rot(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "assert_weg")
    text = (repo / "tests" / "test_rechnen.py").read_text(encoding="utf-8")
    _commit(repo, "tests/test_rechnen.py", text.replace("    assert addiere(2, 3) < 10\n", ""))

    erg = test_schutz.pruefe_diff(repo, "base", "HEAD")
    assert erg.ok is False
    assert erg.exit_code == 1
    assert erg.zaehler.entfernte_asserts == 1
    assert any(v.art == "assert_entfernt" and v.test == "test_addiere" for v in erg.verstoesse), erg.verstoesse


def test_entferntes_pytest_raises_ist_rot(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "raises_weg")
    text = (repo / "tests" / "test_rechnen.py").read_text(encoding="utf-8")
    ersetzt = text.replace(
        "    with pytest.raises(ValueError):\n        teile(1.0, 0.0)\n",
        "    assert teile(4.0, 2.0) == 2.0\n",
    )
    _commit(repo, "tests/test_rechnen.py", ersetzt)

    erg = test_schutz.pruefe_diff(repo, "base", "HEAD")
    assert erg.ok is False
    assert erg.exit_code == 1
    assert erg.zaehler.entfernte_raises == 1
    assert any(v.art == "pytest_raises_entfernt" and v.test == "test_teile_null" for v in erg.verstoesse), (
        erg.verstoesse
    )


def test_entfernte_testfunktion_und_geloeschte_testdatei_sind_rot(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "test_weg")
    text = (repo / "tests" / "test_rechnen.py").read_text(encoding="utf-8")
    ohne_fn = text.split("\ndef test_teile_null")[0] + "\n"
    _commit(repo, "tests/test_rechnen.py", ohne_fn)

    erg = test_schutz.pruefe_diff(repo, "base", "HEAD")
    assert erg.ok is False
    assert erg.exit_code == 1
    assert erg.zaehler.entfernte_tests >= 3
    assert any(v.art == "test_entfernt" and v.test == "test_teile_null" for v in erg.verstoesse), erg.verstoesse

    # Ganze Testdatei löschen -> ebenfalls rot.
    (repo / "tests" / "test_rechnen.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "löscht testdatei")
    erg_weg = test_schutz.pruefe_diff(repo, "base", "HEAD")
    assert erg_weg.ok is False
    assert erg_weg.exit_code == 1
    assert any(v.art == "testdatei_geloescht" for v in erg_weg.verstoesse), erg_weg.verstoesse


def test_entschaerfungen_skip_trivial_and_or_parametrize_sind_rot() -> None:
    alt = textwrap.dedent("""\
        import pytest

        def test_eins():
            assert wert() == 1 and wert() > 0

        def test_zwei():
            assert ergebnis() == 2

        def test_drei():
            assert code() == 0

        @pytest.mark.parametrize("x", [1, 2, 3])
        def test_vier(x):
            assert x > 0
        """)
    neu = textwrap.dedent("""\
        import pytest

        @pytest.mark.skip(reason="später")
        def test_eins():
            assert wert() == 1

        def test_zwei():
            assert True

        def test_drei():
            assert code() == 0 or code() == 1

        @pytest.mark.parametrize("x", [1, 2])
        def test_vier(x):
            assert x > 0
        """)
    verstoesse, _zaehler = test_schutz.pruefe_inhalt("tests/test_probe.py", alt, neu)
    arten = {v.art for v in verstoesse}
    assert "test_deaktiviert" in arten, verstoesse
    assert "assert_entfernt" in arten, verstoesse
    assert "pruefung_entschaerft" in arten, verstoesse
    assert "parametrize_reduziert" in arten, verstoesse


# --- AST-Diff Test-Schutz: geänderte Grenzwerte ------------------------------------------------


@pytest.mark.parametrize(
    ("alt_zeile", "neu_zeile"),
    [
        ("assert addiere(2, 3) < 10", "assert addiere(2, 3) < 50"),
        ("assert addiere(2, 3) < 10", "assert addiere(2, 3) <= 10"),
        ("assert addiere(1, 2) == 3", "assert addiere(1, 2) == 4"),
        ("MAX_DAUER_S = 10", "MAX_DAUER_S = 60"),
        ("grenze = 5", "grenze = 25"),
    ],
)
def test_geaenderter_grenzwert_ist_rot(tmp_path: Path, alt_zeile: str, neu_zeile: str) -> None:
    repo = _probe_repo(tmp_path / "grenzwert")
    text = (repo / "tests" / "test_rechnen.py").read_text(encoding="utf-8")
    assert alt_zeile in text
    _commit(repo, "tests/test_rechnen.py", text.replace(alt_zeile, neu_zeile, 1))

    erg = test_schutz.pruefe_diff(repo, "base", "HEAD")
    assert erg.ok is False, erg.bericht()
    assert erg.exit_code == 1
    assert erg.zaehler.geaenderte_grenzwerte >= 1
    assert any(v.art == "grenzwert_geaendert" for v in erg.verstoesse), erg.verstoesse


def test_negative_zahlen_approx_und_unittest_grenzwerte_erkannt() -> None:
    alt = textwrap.dedent("""\
        import pytest
        import unittest

        def test_negativ_und_approx():
            assert delta() >= -5
            assert quote() == pytest.approx(0.95, abs=0.01)

        class TestUni(unittest.TestCase):
            def test_grenze(self):
                self.assertLess(dauer(), 10)
        """)
    neu = textwrap.dedent("""\
        import pytest
        import unittest

        def test_negativ_und_approx():
            assert delta() >= -50
            assert quote() == pytest.approx(0.95, abs=0.2)

        class TestUni(unittest.TestCase):
            def test_grenze(self):
                self.assertLess(dauer(), 99)
        """)
    verstoesse, zaehler = test_schutz.pruefe_inhalt("tests/test_grenzen.py", alt, neu)
    assert zaehler.geaenderte_grenzwerte == 3, verstoesse
    assert all(v.art == "grenzwert_geaendert" for v in verstoesse)


def test_syntaxfehler_in_testdatei_ist_rot_fail_closed() -> None:
    alt = "def test_ok():\n    assert 1 == 1\n"
    neu = "def test_ok(\n    assert 1 == 1\n"
    verstoesse, _ = test_schutz.pruefe_inhalt("tests/test_kaputt.py", alt, neu)
    assert any(v.art == "syntax_fehler" for v in verstoesse), verstoesse


def test_ungespeicherte_aenderung_im_arbeitsbaum_wird_erkannt(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "arbeitsbaum")
    datei = repo / "tests" / "test_rechnen.py"
    datei.write_text(datei.read_text(encoding="utf-8").replace("assert addiere(2, 3) < 10", ""), encoding="utf-8")

    # ziel=None prüft den Arbeitsbaum (vor dem Commit/Push).
    erg = test_schutz.pruefe_diff(repo, "base")
    assert erg.ok is False
    assert erg.exit_code == 1
    assert erg.zaehler.entfernte_asserts == 1


def test_kein_git_repo_gibt_exit_2(tmp_path: Path) -> None:
    leer = tmp_path / "kein_repo"
    leer.mkdir()
    erg = test_schutz.pruefe_diff(leer, "HEAD")
    assert erg.ok is False
    assert erg.exit_code == 2
    assert erg.hinweis


def test_test_schutz_cli_weg_gruen_und_rot(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "cli_schutz")
    _commit(repo, "rechnen.py", "def addiere(a: int, b: int) -> int:\n    return a + b\n")
    gruen = _cli(
        "to_spawn.pruef.test_schutz", "--repo", str(repo), "--basis", "base", "--ziel", "HEAD", "--json", cwd=repo
    )
    assert gruen.returncode == 0, gruen.stdout + gruen.stderr
    daten_gruen = json.loads(gruen.stdout)
    assert daten_gruen["ok"] is True
    assert daten_gruen["verstoesse"] == []

    # Assert entfernen + Grenzwert ändern -> CLI rot (Exit 1).
    text = (repo / "tests" / "test_rechnen.py").read_text(encoding="utf-8")
    geaendert = text.replace("    assert addiere(2, 3) < 10\n", "").replace("MAX_DAUER_S = 10", "MAX_DAUER_S = 99")
    _commit(repo, "tests/test_rechnen.py", geaendert)
    rot = _cli(
        "to_spawn.pruef.test_schutz", "--repo", str(repo), "--basis", "base", "--ziel", "HEAD", "--json", cwd=repo
    )
    assert rot.returncode == 1, rot.stdout + rot.stderr
    daten_rot = json.loads(rot.stdout)
    assert daten_rot["ok"] is False
    assert daten_rot["zaehler"]["entfernte_asserts"] == 1
    assert daten_rot["zaehler"]["geaenderte_grenzwerte"] == 1


# --- Konfigurations-Schema pruefung.* ----------------------------------------------------------


def test_config_stufe0_vorgaben_und_defaults_unveraendert(caplog: pytest.LogCaptureFixture) -> None:
    # DEFAULTS["pruefung"] bleibt für #425 unverändert.
    assert config.DEFAULTS["pruefung"] == {"schnell": "", "komplett": ""}

    vorgabe = config.stufe0_konfig({})
    assert vorgabe == config.Stufe0Konfig(ruff="", typ_pruefung="", test_schutz=True)

    eigen = config.stufe0_konfig(
        {"pruefung": {"ruff": " ruff check . ", "typ_pruefung": " mypy to_spawn ", "test_schutz": False}}
    )
    assert eigen == config.Stufe0Konfig(ruff="ruff check .", typ_pruefung="mypy to_spawn", test_schutz=False)

    with caplog.at_level("WARNING", logger="to_spawn.config"):
        kaputt = config.stufe0_konfig({"pruefung": {"typ_pruefung": ["mypy"], "test_schutz": "nein"}})
    assert kaputt == config.Stufe0Konfig(ruff="", typ_pruefung="", test_schutz=True)
    assert caplog.records


# --- Einheitlicher Stufe-0-Ablauf (sofort.stufe0 + CLI) ----------------------------------------


def _schreibe_typ_attrappe(ordner: Path, exit_code: int, ausgabe: str) -> str:
    skript = ordner / "typ_attrappe.py"
    skript.write_text(
        f"import sys\nprint({ausgabe!r})\nsys.exit({exit_code})\n",
        encoding="utf-8",
    )
    # Plattformübergreifender Befehl ohne Leerzeichen-Probleme (POSIX-Pfade).
    return f'"{Path(sys.executable).as_posix()}" "{skript.as_posix()}"'


def test_stufe0_normaler_diff_mit_typ_pruefung_gruen_und_rot(tmp_path: Path) -> None:
    attrappe_ok = _schreibe_typ_attrappe(tmp_path, 0, "typ-pruefung sauber")
    repo = _probe_repo(tmp_path / "stufe0_ok", konfig={"pruefung": {"typ_pruefung": attrappe_ok}})
    _commit(repo, "rechnen.py", "def addiere(a: int, b: int) -> int:\n    return a + b + 0\n")

    erg = sofort.stufe0(repo, "base", "HEAD")
    assert erg.ok is True, erg.bericht()
    assert erg.exit_code == 0
    namen = [s.name for s in erg.stufen]
    assert namen == ["ruff", "typ_pruefung", "test_schutz"]
    typ_stufe = next(s for s in erg.stufen if s.name == "typ_pruefung")
    assert typ_stufe.lief is True and typ_stufe.ergebnis == 0

    # Typ-Prüfung rot -> gesamter Stufe-0-Ablauf rot.
    attrappe_rot = _schreibe_typ_attrappe(tmp_path, 1, "rechnen.py:1: error: Typfehler")
    erg_rot = sofort.stufe0(repo, "base", "HEAD", typ_befehl=attrappe_rot)
    assert erg_rot.ok is False
    assert erg_rot.exit_code == 1
    assert "Typfehler" in erg_rot.fehler_kurz

    # Fehlendes Typ-Prüf-Werkzeug -> Gate-Fehler (Exit 2), nie still grün.
    erg_fehlt = sofort.stufe0(repo, "base", "HEAD", typ_befehl="gibt_es_nicht_typpruefer_xyz_794")
    assert erg_fehlt.ok is False
    assert erg_fehlt.exit_code == 2


def test_stufe0_test_schutz_rot_auch_bei_gruener_typ_pruefung(tmp_path: Path) -> None:
    attrappe_ok = _schreibe_typ_attrappe(tmp_path, 0, "ok")
    repo = _probe_repo(tmp_path / "stufe0_schutz", konfig={"pruefung": {"typ_pruefung": attrappe_ok}})
    text = (repo / "tests" / "test_rechnen.py").read_text(encoding="utf-8")
    _commit(repo, "tests/test_rechnen.py", text.replace("    assert addiere(2, 3) < 10\n", ""))

    erg = sofort.stufe0(repo, "base", "HEAD")
    assert erg.ok is False
    assert erg.exit_code == 1
    assert erg.test_schutz is not None and erg.test_schutz.ok is False
    assert "assert_entfernt" in erg.bericht() or "Assert" in erg.bericht()


def test_stufe0_commit_kann_test_schutz_und_typ_pruefung_nicht_abschalten(tmp_path: Path) -> None:
    """Fail-closed: Basis-Konfig schlägt Abschalt-Versuch im geprüften Commit."""
    attrappe_rot = _schreibe_typ_attrappe(tmp_path, 1, "error: bleibt aktiv")
    repo = _probe_repo(
        tmp_path / "stufe0_sicher",
        konfig={"pruefung": {"typ_pruefung": attrappe_rot, "test_schutz": True}},
    )
    # Commit versucht beide Prüfungen in .to-spawn/config.json abzuschalten UND einen Assert zu löschen.
    _commit(
        repo,
        ".to-spawn/config.json",
        json.dumps({"pruefung": {"typ_pruefung": "", "test_schutz": False}}),
    )
    text = (repo / "tests" / "test_rechnen.py").read_text(encoding="utf-8")
    _commit(repo, "tests/test_rechnen.py", text.replace("    assert addiere(2, 3) < 10\n", ""))

    erg = sofort.stufe0(repo, "base", "HEAD")
    assert erg.ok is False
    assert erg.exit_code == 1
    stufen_nach_name = {s.name: s for s in erg.stufen}
    assert stufen_nach_name["typ_pruefung"].ergebnis == 1
    assert stufen_nach_name["test_schutz"].ergebnis == 1


@pytest.mark.skipif(shutil.which("ruff") is None and not os.environ.get("CI"), reason="ruff nicht im PATH")
def test_stufe0_ruff_syntaxfehler_ist_rot(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "stufe0_ruff")
    _commit(repo, "rechnen.py", "def addiere(a, b)\n    return a + b\n")

    erg = sofort.stufe0(repo, "base", "HEAD")
    assert erg.ok is False
    assert erg.exit_code == 1
    ruff_stufe = next(s for s in erg.stufen if s.name == "ruff")
    assert ruff_stufe.lief is True and ruff_stufe.ergebnis == 1


def test_stufe0_cli_gruen_und_rot(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "stufe0_cli")
    _commit(repo, "rechnen.py", "def addiere(a: int, b: int) -> int:\n    return b + a\n")

    gruen = _cli("to_spawn.pruef.sofort", "--repo", str(repo), "--basis", "base", "--ziel", "HEAD", "--json", cwd=repo)
    assert gruen.returncode == 0, gruen.stdout + gruen.stderr
    daten = json.loads(gruen.stdout)
    assert daten["ok"] is True
    assert [s["name"] for s in daten["stufen"]] == ["ruff", "typ_pruefung", "test_schutz"]

    # Grenzwert im Test ändern -> CLI rot (Exit 1).
    text = (repo / "tests" / "test_rechnen.py").read_text(encoding="utf-8")
    _commit(repo, "tests/test_rechnen.py", text.replace("assert addiere(2, 3) < 10", "assert addiere(2, 3) < 99"))
    rot = _cli("to_spawn.pruef.sofort", "--repo", str(repo), "--basis", "base", "--ziel", "HEAD", "--json", cwd=repo)
    assert rot.returncode == 1, rot.stdout + rot.stderr
    daten_rot = json.loads(rot.stdout)
    assert daten_rot["ok"] is False


def test_cli_ausgabe_utf8_unter_cp1252_pipe(tmp_path: Path) -> None:
    """Windows-Pipes nutzen ohne UTF-8-Modus cp1252 — beide CLIs müssen dennoch UTF-8 ausgeben (#794)."""
    repo = _probe_repo(tmp_path / "cp1252_cli")
    text = (repo / "tests" / "test_rechnen.py").read_text(encoding="utf-8")
    geaendert = text.replace("    assert addiere(2, 3) < 10\n", "").replace("MAX_DAUER_S = 10", "MAX_DAUER_S = 99")
    _commit(repo, "tests/test_rechnen.py", geaendert)
    cp1252 = {"PYTHONIOENCODING": "cp1252"}

    for modul in ("to_spawn.pruef.test_schutz", "to_spawn.pruef.sofort"):
        json_lauf = _cli(
            modul, "--repo", str(repo), "--basis", "base", "--ziel", "HEAD", "--json", cwd=repo, extra_env=cp1252
        )
        assert json_lauf.returncode == 1, json_lauf.stdout + json_lauf.stderr
        assert "Traceback" not in json_lauf.stderr, json_lauf.stderr
        daten = json.loads(json_lauf.stdout)
        assert daten["ok"] is False
        assert "\ufffd" not in json_lauf.stdout
        assert "→" in json_lauf.stdout

        text_lauf = _cli(modul, "--repo", str(repo), "--basis", "base", "--ziel", "HEAD", cwd=repo, extra_env=cp1252)
        assert text_lauf.returncode == 1, text_lauf.stdout + text_lauf.stderr
        assert "Traceback" not in text_lauf.stderr, text_lauf.stderr
        assert "\ufffd" not in text_lauf.stdout
        assert "→" in text_lauf.stdout


# --- Abnahme-Kriterium: kein testmon im Produktivcode ------------------------------------------


def test_kein_testmon_im_produktivcode() -> None:
    treffer: list[str] = []
    for datei in sorted((SKILL / "to_spawn").rglob("*.py")):
        if "__pycache__" in datei.parts:
            continue
        for nr, zeile in enumerate(datei.read_text(encoding="utf-8").splitlines(), 1):
            if "testmon" in zeile.lower():
                treffer.append(f"{datei.relative_to(SKILL).as_posix()}:{nr}")
    assert treffer == [], f"testmon im Produktivcode gefunden: {treffer}"
