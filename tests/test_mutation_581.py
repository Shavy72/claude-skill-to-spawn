"""#799 (#581 Teil 3): Stufe 3 Diff-Mutationstest (mutmut) – Linux-Teil.

Prüft den Linux-Baustein ``to_spawn/pruef/mutation.py`` (CLI + Python-API):
- Bestimmt geänderte Produktiv-Python-Dateien aus einem echten git-Diff und schließt
  Testdateien, Test-Helfer, Doku-Dateien (auch ``.py`` unter ``docs/``), Werkzeug-Ordner
  und gelöschte Dateien strikt aus.
- Begrenzt den ``mutmut``-Lauf strikt auf die geänderten Produktiv-Python-Dateien
  (unberührte Dateien tauchen nie im ``mutmut``-Aufruf auf) und liefert ein strukturiertes
  Ergebnis (getötete/überlebende Mutanten, geprüfte Dateien, Bericht, Markdown-Zusammenfassung).
- Trennt Linux klar von Nicht-Linux: unter Windows/macOS sauberer, eindeutiger Hinweis
  statt Absturz und ohne ``mutmut``-Start.
- Fail-closed bei fehlendem ``mutmut``, ungültiger git-Ref oder Werkzeug-Absturz.
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

from to_spawn import prozessbaum
from to_spawn.pruef import mutation


def _git(ordner: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.invalid", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **prozessbaum.fensterlos(),
    )
    return erg.stdout.strip()


def _schreibe(ordner: Path, rel: str, inhalt: str) -> Path:
    ziel = ordner / rel
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(inhalt, encoding="utf-8")
    return ziel


def _probe_repo(ordner: Path) -> Path:
    """Frisches Git-Repo mit mehreren Produktivmodulen, Tests, Doku und Tag ``base``."""
    dateien = {
        ".gitignore": "__pycache__/\n.pytest_cache/\n.mutmut-cache\n",
        "pytest.ini": "[pytest]\ntestpaths = tests\npythonpath = .\n",
        "kern/__init__.py": "",
        "kern/rechnen.py": textwrap.dedent("""\
            def addiere(a: int, b: int) -> int:
                return a + b
            """),
        "kern/unberuehrt.py": textwrap.dedent("""\
            def faktor(x: int) -> int:
                return x * 2
            """),
        "dienst/__init__.py": "",
        "dienst/helfer.py": textwrap.dedent("""\
            def gruss(name: str) -> str:
                return f"Hallo {name}"
            """),
        "tests/conftest.py": "# Test-Konfiguration\n",
        "tests/hilfsmodul.py": "KONSTANTE = 42\n",
        "tests/test_rechnen.py": textwrap.dedent("""\
            from kern.rechnen import addiere


            def test_addiere() -> None:
                assert addiere(2, 3) == 5
            """),
        "docs/hinweis.md": "# Hinweis\n",
        "docs/conf.py": "project = 'probe'\n",
        "README.md": "# Probe\n",
    }
    for rel, inhalt in dateien.items():
        _schreibe(ordner, rel, inhalt)
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "basis")
    _git(ordner, "tag", "base")
    return ordner


def _fake_mutmut_paket(
    ziel_ordner: Path,
    spur_datei: Path,
    *,
    version: str = "2.5.1",
    getoetet: int = 3,
    ueberlebt: int = 0,
    zeitgrenze: int = 0,
    verdaechtig: int = 0,
    uebersprungen: int = 0,
    absturz: bool = False,
    runner_ausfuehren: bool = False,
) -> Path:
    """Legt ein kleines Ersatz-Paket ``mutmut`` an, das echte CLI-Aufrufe protokolliert."""
    paket = ziel_ordner / "mutmut"
    paket.mkdir(parents=True, exist_ok=True)
    _schreibe(paket, "__init__.py", f'__version__ = "{version}"\n')
    gesamt = getoetet + ueberlebt + zeitgrenze + verdaechtig + uebersprungen
    _schreibe(
        paket,
        "__main__.py",
        textwrap.dedent(f"""\
            import json
            import shlex
            import subprocess
            import sys
            from pathlib import Path

            SPUR = Path({str(spur_datei)!r})
            args = sys.argv[1:]

            def _merke(eintrag: dict) -> None:
                bisher = json.loads(SPUR.read_text(encoding="utf-8")) if SPUR.is_file() else []
                bisher.append(eintrag)
                SPUR.write_text(json.dumps(bisher, ensure_ascii=False), encoding="utf-8")

            if args == ["--version"]:
                print("mutmut version {version}")
                raise SystemExit(0)

            if args[:2] == ["run", "--help"]:
                print("Usage: mutmut run [OPTIONS]")
                print("  --paths-to-mutate TEXT  Pfade")
                print("  --runner TEXT           Test-Runner")
                raise SystemExit(0)

            if args and args[0] == "run":
                _merke({{"unterbefehl": "run", "args": args}})
                if {absturz!r}:
                    sys.stderr.write("fatal: interner mutmut-Fehler\\n")
                    raise SystemExit(1)
                if {runner_ausfuehren!r} and "--runner" in args:
                    runner = args[args.index("--runner") + 1]
                    erg = subprocess.run(shlex.split(runner), check=False)
                    if erg.returncode != 0:
                        sys.stderr.write("Basis-Tests rot\\n")
                        raise SystemExit(1)
                pfade = ""
                if "--paths-to-mutate" in args:
                    pfade = args[args.index("--paths-to-mutate") + 1]
                elif any(a.startswith("--paths-to-mutate=") for a in args):
                    pfade = next(a.split("=", 1)[1] for a in args if a.startswith("--paths-to-mutate="))
                # Merke geprüfte Dateien im lokalen Cache für ``mutmut results``
                Path(".mutmut-cache").write_text(pfade, encoding="utf-8")
                print(
                    "{gesamt}/{gesamt}  "
                    "\\U0001f389 {getoetet}  "
                    "\\u23f0 {zeitgrenze}  "
                    "\\U0001f914 {verdaechtig}  "
                    "\\U0001f641 {ueberlebt}  "
                    "\\U0001f507 {uebersprungen}"
                )
                code = 2 if {ueberlebt} > 0 else 0
                raise SystemExit(code)

            if args and args[0] == "results":
                _merke({{"unterbefehl": "results", "args": args}})
                pfade_roh = Path(".mutmut-cache").read_text(encoding="utf-8") if Path(".mutmut-cache").is_file() else ""
                erste = (pfade_roh.split(",")[0] if pfade_roh else "unbekannt.py").strip()
                if {ueberlebt} > 0:
                    ids = ", ".join(str(i) for i in range(1, {ueberlebt} + 1))
                    print("Survived \\U0001f641 ({ueberlebt})")
                    print()
                    print(f"---- {{erste}} ({ueberlebt}) ----")
                    print()
                    print(ids)
                raise SystemExit(0)

            sys.stderr.write(f"Unbekannte Argumente: {{args}}\\n")
            raise SystemExit(2)
            """),
    )
    return ziel_ordner


def _cli(
    *args: str,
    cwd: Path,
    extra_pythonpath: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    teile = [str(SKILL)]
    if extra_pythonpath is not None:
        teile.insert(0, str(extra_pythonpath))
    if os.environ.get("PYTHONPATH"):
        teile.append(os.environ["PYTHONPATH"])
    umg = {**os.environ, "PYTHONPATH": os.pathsep.join(teile)}
    return subprocess.run(
        [sys.executable, "-m", "to_spawn.pruef.mutation", *args],
        cwd=cwd,
        env=umg,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **prozessbaum.fensterlos(),
    )


# --- 1. Diff-Filterung: nur geänderte Produktiv-Python-Dateien ---------------------------------


def test_diff_waehlt_nur_geaenderte_produktiv_python_dateien(tmp_path: Path) -> None:
    """Schließt Tests, Test-Helfer, Doku (auch .py in docs/), Werkzeug-Ordner und Unberührtes aus."""
    repo = _probe_repo(tmp_path / "repo")
    _schreibe(
        repo,
        "kern/rechnen.py",
        textwrap.dedent("""\
            def addiere(a: int, b: int) -> int:
                return int(a + b)
            """),
    )
    _schreibe(repo, "tests/test_rechnen.py", "from kern.rechnen import addiere\n\ndef test_addiere():\n    assert addiere(1, 2) == 3\n")
    _schreibe(repo, "tests/hilfsmodul.py", "KONSTANTE = 99\n")
    _schreibe(repo, "tests/conftest.py", "# geändert\n")
    _schreibe(repo, "docs/hinweis.md", "# Aktualisiert\n")
    _schreibe(repo, "docs/conf.py", "project = 'neu'\n")
    _schreibe(repo, "README.md", "# Neu\n")
    _schreibe(repo, ".to-spawn/intern.py", "X = 1\n")
    _schreibe(repo, ".claude/werkzeug.py", "Y = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "gemischter diff")

    dateien, fehler = mutation.geaenderte_produktiv_dateien(repo, "base", "HEAD")
    assert fehler == ""
    assert dateien == ("kern/rechnen.py",)


def test_diff_arbeitsbaum_schliesst_geloeschte_datei_aus_und_nimmt_neue_auf(tmp_path: Path) -> None:
    """Arbeitsbaum-Diff (ziel=None): gelöschte Datei fällt raus, neue Produktiv-Datei kommt rein."""
    repo = _probe_repo(tmp_path / "repo")
    (repo / "dienst" / "helfer.py").unlink()
    _schreibe(repo, "kern/neu.py", "def wert() -> int:\n    return 7\n")
    _schreibe(repo, "tests/test_neu.py", "def test_x():\n    assert True\n")

    dateien, fehler = mutation.geaenderte_produktiv_dateien(repo, "HEAD")
    assert fehler == ""
    assert dateien == ("kern/neu.py",)


def test_filterung_normalisiert_windows_pfade_und_pfad_filter(tmp_path: Path) -> None:
    """Backslash-Pfade werden sauber normalisiert; optionaler Pfad-Filter grenzt weiter ein."""
    repo = _probe_repo(tmp_path / "repo")
    dateien, fehler = mutation.geaenderte_produktiv_dateien(
        repo,
        "base",
        pfade=["kern\\rechnen.py", "dienst\\helfer.py", "tests\\test_rechnen.py", "docs\\conf.py"],
        pfad_filter=("kern",),
    )
    assert fehler == ""
    assert dateien == ("kern/rechnen.py",)


# --- 2. Strikte Begrenzung des mutmut-Laufs auf geänderte Dateien ------------------------------


def test_mutationstest_begrenzt_mutmut_strikt_auf_geaenderte_dateien_gruen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nur geänderte Produktiv-Dateien landen in --paths-to-mutate; unberührte bleiben außen vor."""
    repo = _probe_repo(tmp_path / "repo")
    _schreibe(
        repo,
        "kern/rechnen.py",
        textwrap.dedent("""\
            def addiere(a: int, b: int) -> int:
                ergebnis = a + b
                return ergebnis
            """),
    )
    _schreibe(repo, "docs/hinweis.md", "# Doku-Änderung\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "rechnen geändert")

    spur = tmp_path / "mutmut_spur.json"
    fake_dir = _fake_mutmut_paket(
        tmp_path / "fake_site",
        spur,
        getoetet=3,
        ueberlebt=0,
        runner_ausfuehren=True,
    )
    monkeypatch.setenv("PYTHONPATH", f"{fake_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}")

    erg = mutation.pruefe_diff(repo, "base", "HEAD", plattform="linux")

    assert erg.ok is True
    assert erg.exit_code == mutation.EXIT_GRUEN
    assert erg.unterstuetzt is True
    assert erg.lief is True
    assert erg.gepruefte_dateien == ("kern/rechnen.py",)
    assert "docs/hinweis.md" in erg.ausgeschlossene_dateien
    assert "kern/unberuehrt.py" not in erg.gepruefte_dateien
    assert erg.zaehler.gesamt == 3
    assert erg.zaehler.getoetet == 3
    assert erg.zaehler.ueberlebt == 0
    assert erg.ueberlebende == ()
    assert erg.werkzeug == "mutmut@2.5.1"

    aufrufe = json.loads(spur.read_text(encoding="utf-8"))
    run_aufrufe = [a for a in aufrufe if a["unterbefehl"] == "run"]
    assert len(run_aufrufe) == 1
    run_args = run_aufrufe[0]["args"]
    idx = run_args.index("--paths-to-mutate")
    assert run_args[idx + 1] == "kern/rechnen.py"
    assert "kern/unberuehrt.py" not in " ".join(run_args)


def test_mutationstest_meldet_ueberlebende_mutanten_strukturiert_rot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Überlebende Mutanten liefern Exit 1, Zähler, Einzel-Liste, JSON und Markdown-Bericht."""
    repo = _probe_repo(tmp_path / "repo")
    _schreibe(
        repo,
        "kern/rechnen.py",
        "def addiere(a: int, b: int) -> int:\n    return a + b + 0\n",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "mutierbare änderung")

    spur = tmp_path / "mutmut_spur.json"
    fake_dir = _fake_mutmut_paket(tmp_path / "fake_site", spur, getoetet=4, ueberlebt=2, zeitgrenze=1)
    monkeypatch.setenv("PYTHONPATH", f"{fake_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}")

    erg = mutation.pruefe_diff(repo, "base", "HEAD", plattform="linux")

    assert erg.ok is False
    assert erg.exit_code == mutation.EXIT_ROT
    assert erg.lief is True
    assert erg.gepruefte_dateien == ("kern/rechnen.py",)
    assert erg.zaehler.getoetet == 4
    assert erg.zaehler.ueberlebt == 2
    assert erg.zaehler.zeitgrenze == 1
    assert erg.zaehler.gesamt == 7
    assert [(m.datei, m.kennung) for m in erg.ueberlebende] == [
        ("kern/rechnen.py", "1"),
        ("kern/rechnen.py", "2"),
    ]

    daten = erg.als_dict()
    assert daten["ok"] is False
    assert daten["exit_code"] == 1
    assert daten["gepruefte_dateien"] == ["kern/rechnen.py"]
    assert daten["zaehler"]["getoetet"] == 4
    assert daten["zaehler"]["ueberlebt"] == 2
    assert len(daten["ueberlebende"]) == 2
    assert "kern/rechnen.py" in erg.bericht()
    assert "Überlebend" in erg.zusammenfassung() or "überlebend" in erg.zusammenfassung().lower()


def test_nur_test_und_doku_diff_startet_kein_mutmut(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ändern sich nur Tests/Doku, bleibt der Lauf grün ohne mutmut zu starten."""
    repo = _probe_repo(tmp_path / "repo")
    _schreibe(repo, "docs/hinweis.md", "# Mehr Text\n")
    _schreibe(
        repo,
        "tests/test_rechnen.py",
        "from kern.rechnen import addiere\n\ndef test_addiere():\n    assert addiere(2, 3) == 5\n    assert addiere(0, 0) == 0\n",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "nur doku und test")

    spur = tmp_path / "mutmut_spur.json"
    fake_dir = _fake_mutmut_paket(tmp_path / "fake_site", spur, getoetet=1, ueberlebt=1)
    monkeypatch.setenv("PYTHONPATH", f"{fake_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}")

    erg = mutation.pruefe_diff(repo, "base", "HEAD", plattform="linux")

    assert erg.ok is True
    assert erg.exit_code == mutation.EXIT_GRUEN
    assert erg.lief is False
    assert erg.gepruefte_dateien == ()
    assert not spur.exists()


# --- 3. Linux-Trennung: Nicht-Linux gibt eindeutigen Hinweis statt Absturz ---------------------


@pytest.mark.parametrize("fremd_os", ["win32", "darwin"])
def test_nicht_linux_gibt_sauberen_hinweis_ohne_absturz(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fremd_os: str,
) -> None:
    """Unter Nicht-Linux stürzt nichts ab; eindeutiger Hinweis und Exit 2 (fail-closed)."""
    repo = _probe_repo(tmp_path / "repo")
    spur = tmp_path / "mutmut_spur.json"
    fake_dir = _fake_mutmut_paket(tmp_path / "fake_site", spur)
    monkeypatch.setenv("PYTHONPATH", f"{fake_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}")
    monkeypatch.setattr(mutation.sys, "platform", fremd_os)

    erg = mutation.pruefe_diff(repo, "base", "HEAD")

    assert erg.ok is False
    assert erg.exit_code == mutation.EXIT_FEHLER
    assert erg.unterstuetzt is False
    assert erg.lief is False
    assert erg.plattform == fremd_os
    assert "linux" in erg.hinweis.lower()
    assert "mutmut" in erg.hinweis.lower()
    assert fremd_os in erg.hinweis
    assert not spur.exists()


# --- 4. Fail-closed Fehlerfälle ----------------------------------------------------------------


def test_fehlendes_mutmut_liefert_gate_fehler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ist mutmut nicht installiert, gibt es Exit 2 mit klarer Installations-Meldung."""
    repo = _probe_repo(tmp_path / "repo")
    _schreibe(repo, "kern/rechnen.py", "def addiere(a: int, b: int) -> int:\n    return a + b + 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "änderung")

    monkeypatch.setattr(mutation, "_finde_mutmut", lambda _repo: (None, ""))

    erg = mutation.pruefe_diff(repo, "base", "HEAD", plattform="linux")

    assert erg.ok is False
    assert erg.exit_code == mutation.EXIT_FEHLER
    assert erg.lief is False
    assert erg.gepruefte_dateien == ("kern/rechnen.py",)
    assert "mutmut" in erg.hinweis.lower()


def test_mutmut_absturz_ist_nie_gruen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ein interner mutmut-Absturz (Exit 1 ohne Zähler) endet als Exit 2 (fail-closed)."""
    repo = _probe_repo(tmp_path / "repo")
    _schreibe(repo, "kern/rechnen.py", "def addiere(a: int, b: int) -> int:\n    return a + b + 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "änderung")

    spur = tmp_path / "mutmut_spur.json"
    fake_dir = _fake_mutmut_paket(tmp_path / "fake_site", spur, absturz=True)
    monkeypatch.setenv("PYTHONPATH", f"{fake_dir}{os.pathsep}{os.environ.get('PYTHONPATH', '')}")

    erg = mutation.pruefe_diff(repo, "base", "HEAD", plattform="linux")

    assert erg.ok is False
    assert erg.exit_code == mutation.EXIT_FEHLER


def test_baue_mutmut_befehl_trennt_version_2_und_3() -> None:
    """Version 2 nutzt --paths-to-mutate, Version 3 begrenzt per Modulmuster."""
    cmd2 = mutation.baue_mutmut_befehl(
        ["python", "-m", "mutmut"],
        ["kern/rechnen.py", "dienst/helfer.py"],
        runner="pytest -q tests/test_rechnen.py",
        hauptversion=2,
    )
    assert cmd2 == [
        "python",
        "-m",
        "mutmut",
        "run",
        "--paths-to-mutate",
        "kern/rechnen.py,dienst/helfer.py",
        "--runner",
        "pytest -q tests/test_rechnen.py",
    ]

    cmd3 = mutation.baue_mutmut_befehl(
        ["python", "-m", "mutmut"],
        ["kern/rechnen.py", "src/helfer/mod.py", "kern/__init__.py"],
        hauptversion=3,
    )
    assert cmd3 == [
        "python",
        "-m",
        "mutmut",
        "run",
        "kern.rechnen.*",
        "helfer.mod.*",
        "kern.*",
    ]


def test_ungueltige_git_ref_liefert_fehler(tmp_path: Path) -> None:
    """Unbekannte Basis-Ref führt sauber zu Exit 2 ohne Ausnahme."""
    repo = _probe_repo(tmp_path / "repo")
    erg = mutation.pruefe_diff(repo, "nicht-vorhandene-ref", "HEAD", plattform="linux")
    assert erg.ok is False
    assert erg.exit_code == mutation.EXIT_FEHLER
    assert erg.lief is False
    assert erg.hinweis != ""


# --- 5. Ausgabe-Parser (mutmut 2.x + 3.x) -----------------------------------------------------


def test_parser_erkennt_mutmut2_emoji_und_results() -> None:
    """Parst mutmut-2-Fortschrittszeilen (inkl. ANSI/CR) und ``mutmut results``-Blöcke."""
    ausgabe = (
        "\x1b[32m3/10  🎉 2  ⏰ 0  🤔 0  🙁 1  🔇 0\x1b[0m\r"
        "10/10  🎉 7  ⏰ 1  🤔 0  🙁 2  🔇 0\n"
    )
    zaehler = mutation.parse_mutmut_ausgabe(ausgabe)
    assert zaehler == mutation.Zaehler(
        gesamt=10,
        getoetet=7,
        ueberlebt=2,
        zeitgrenze=1,
        verdaechtig=0,
        uebersprungen=0,
    )

    results_text = textwrap.dedent("""\
        Survived 🙁 (3)

        ---- kern/rechnen.py (3) ----

        2, 5-6

        Timed out ⏰ (1)

        ---- kern/rechnen.py (1) ----

        4
        """)
    funde = mutation.parse_mutmut_results(results_text)
    assert [(f.datei, f.kennung, f.status) for f in funde] == [
        ("kern/rechnen.py", "2", "ueberlebt"),
        ("kern/rechnen.py", "5", "ueberlebt"),
        ("kern/rechnen.py", "6", "ueberlebt"),
        ("kern/rechnen.py", "4", "zeitgrenze"),
    ]


def test_parser_erkennt_mutmut3_results() -> None:
    """Parst auch mutmut-3-Ergebniszeilen (``modul.funktion__mutmut_N: status``)."""
    results_text = textwrap.dedent("""\
        kern.rechnen.x_addiere__mutmut_1: killed
        kern.rechnen.x_addiere__mutmut_2: survived
        kern.rechnen.x_addiere__mutmut_3: timeout
        kern.rechnen.x_addiere__mutmut_4: skipped
        """)
    zaehler = mutation.parse_mutmut_ausgabe(results_text)
    assert zaehler is not None
    assert zaehler.getoetet == 1
    assert zaehler.ueberlebt == 1
    assert zaehler.zeitgrenze == 1
    assert zaehler.uebersprungen == 1
    assert zaehler.gesamt == 4

    funde = mutation.parse_mutmut_results(results_text)
    assert [(f.kennung, f.status) for f in funde] == [
        ("kern.rechnen.x_addiere__mutmut_2", "ueberlebt"),
        ("kern.rechnen.x_addiere__mutmut_3", "zeitgrenze"),
    ]


# --- 6. CLI-Schnittstelle ----------------------------------------------------------------------


def test_cli_json_und_markdown_zusammenfassung(tmp_path: Path) -> None:
    """CLI liefert gültiges JSON, schreibt Markdown-Zusammenfassung und respektiert Exit-Codes."""
    repo = _probe_repo(tmp_path / "repo")
    _schreibe(repo, "kern/rechnen.py", "def addiere(a: int, b: int) -> int:\n    return (a + b)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "cli änderung")

    spur = tmp_path / "cli_spur.json"
    fake_dir = _fake_mutmut_paket(tmp_path / "fake_site", spur, getoetet=2, ueberlebt=1)
    summary_md = tmp_path / "summary.md"

    erg = _cli(
        "--repo",
        str(repo),
        "--basis",
        "base",
        "--ziel",
        "HEAD",
        "--json",
        "--zusammenfassung",
        str(summary_md),
        cwd=repo,
        extra_pythonpath=fake_dir,
    )

    assert erg.returncode == 1, erg.stderr
    daten = json.loads(erg.stdout)
    assert daten["ok"] is False
    assert daten["gepruefte_dateien"] == ["kern/rechnen.py"]
    assert daten["zaehler"]["getoetet"] == 2
    assert daten["zaehler"]["ueberlebt"] == 1
    assert summary_md.is_file()
    md_text = summary_md.read_text(encoding="utf-8")
    assert "kern/rechnen.py" in md_text
    assert "Mutation" in md_text


def test_cli_nicht_linux_liefert_exit_2_und_json_hinweis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI unter Nicht-Linux stürzt nicht ab, sondern liefert JSON mit ``unterstuetzt=False`` und Exit 2."""
    repo = _probe_repo(tmp_path / "repo")
    monkeypatch.setattr(mutation.sys, "platform", "win32")

    code = mutation.main(["--repo", str(repo), "--basis", "base", "--json"])
    assert code == mutation.EXIT_FEHLER
    aus = capsys.readouterr().out
    daten = json.loads(aus)
    assert daten["ok"] is False
    assert daten["unterstuetzt"] is False
    assert "linux" in daten["hinweis"].lower()
