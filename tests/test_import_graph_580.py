"""#795 (#580 Teil 2): AST-Import-Graph-Testauswahl und „Nach Rot“-Regel (Linux + Windows).

Echte Git-Repos und echte pytest-Läufe ohne externes ``pytest-impacted`` und ohne ``testmon``:
- Weg-Test Import-Graph: Änderung an Modul A wählt genau die Tests, die A direkt oder transitiv
  importieren — auch mit mehreren Top-Level-Paketen, ``src/``-Layout und relativen Importen.
- Andocken an ``adapter_py.PythonAdapter`` und ``leiter.stufen`` (auch ``art="schnell"``).
- „Nach Rot“-Regel: rote Tests werden im Protokoll gemerkt; rote Läufe werden bei unverändertem
  Stand nie blind wiederholt; der nächste Lauf führt zuerst gezielt nur die zuletzt roten Tests
  plus die Auswahl des Tickets aus.
- Produktivcode unter ``to_spawn/`` bleibt frei von ``testmon``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from to_spawn import pruef
from to_spawn.pruef import import_graph, protokoll, regeln

WURZEL = Path(__file__).resolve().parents[1]


def _git(ordner: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.invalid", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return erg.stdout.strip()


def _schreibe(ordner: Path, rel: str, inhalt: str) -> Path:
    ziel = ordner / rel
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(inhalt, encoding="utf-8")
    return ziel


def _mehrpaket_repo(ordner: Path, spur_datei: Path | None = None) -> Path:
    """Echtes Git-Repo mit mehreren Top-Level-Paketen (``kern``, ``dienst``, ``src/helfer``) + Skript."""
    spur_code = (
        f"import pathlib\n"
        f"def _spur(name: str) -> None:\n"
        f"    with pathlib.Path({str(spur_datei)!r}).open('a', encoding='utf-8') as f:\n"
        f"        f.write(name + '\\n')\n"
        if spur_datei is not None
        else "def _spur(name: str) -> None:\n    pass\n"
    )
    dateien = {
        "pytest.ini": "[pytest]\ntestpaths = tests\npythonpath = . src\n",
        ".gitignore": "__pycache__/\n.pytest_cache/\n",
        "kern/__init__.py": "from .mod_a import wert_a\n\n__all__ = ['wert_a']\n",
        "kern/mod_a.py": "def wert_a() -> int:\n    return 10\n",
        "dienst/__init__.py": "",
        "dienst/mod_b.py": "from kern.mod_a import wert_a\n\n\ndef wert_b() -> int:\n    return wert_a() + 5\n",
        "dienst/mod_c.py": "def wert_c() -> int:\n    return 99\n",
        "src/helfer/__init__.py": "",
        "src/helfer/mod_d.py": "from kern import wert_a\n\n\ndef wert_d() -> int:\n    return wert_a() * 2\n",
        "skript.py": "def sag() -> str:\n    return 'ok'\n",
        "tests/spur_hilfe.py": spur_code,
        "tests/test_a.py": (
            "from kern.mod_a import wert_a\n"
            "from spur_hilfe import _spur\n\n\n"
            "def test_a_ok() -> None:\n"
            "    _spur('test_a_ok')\n"
            "    assert True\n\n\n"
            "def test_a_wert() -> None:\n"
            "    _spur('test_a_wert')\n"
            "    assert wert_a() == 10\n"
        ),
        "tests/test_b.py": (
            "from dienst.mod_b import wert_b\n"
            "from spur_hilfe import _spur\n\n\n"
            "def test_b_wert() -> None:\n"
            "    _spur('test_b_wert')\n"
            "    assert wert_b() == 15\n"
        ),
        "tests/test_c.py": (
            "from dienst.mod_c import wert_c\n"
            "from spur_hilfe import _spur\n\n\n"
            "def test_c_wert() -> None:\n"
            "    _spur('test_c_wert')\n"
            "    assert wert_c() == 99\n"
        ),
        "tests/test_d.py": (
            "from helfer.mod_d import wert_d\n"
            "from spur_hilfe import _spur\n\n\n"
            "def test_d_wert() -> None:\n"
            "    _spur('test_d_wert')\n"
            "    assert wert_d() == 20\n"
        ),
        "tests/test_skript.py": (
            "import skript\n"
            "from spur_hilfe import _spur\n\n\n"
            "def test_skript() -> None:\n"
            "    _spur('test_skript')\n"
            "    assert skript.sag() == 'ok'\n"
        ),
        # Werkzeug-Ordner darf nie eingesammelt werden.
        ".to-spawn/werkzeug/tests/test_fremd.py": (
            "from kern.mod_a import wert_a\n\ndef test_fremd() -> None:\n    assert wert_a() == -1\n"
        ),
        ".claude/skills/x/tests/test_skill.py": (
            "from kern.mod_a import wert_a\n\ndef test_skill() -> None:\n    assert wert_a() == -1\n"
        ),
    }
    for rel, inhalt in dateien.items():
        _schreibe(ordner, rel, inhalt)
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "basis")
    _git(ordner, "tag", "base")
    return ordner


# --- 1. Import-Graph-Auswahl (direkt, transitiv, Mehrpaket, Windows-Pfade) -----------------------


def test_import_graph_waehlt_genau_direkt_und_transitiv_abhaengige_tests(tmp_path: Path) -> None:
    repo = _mehrpaket_repo(tmp_path / "repo")

    # Änderung an Modul A (auch mit Windows-Pfadtrenner): direkt test_a, transitiv test_b + test_d.
    wahl_a = import_graph.auswaehlen(repo, geaendert=["kern\\mod_a.py"])
    assert wahl_a.tests == ("tests/test_a.py", "tests/test_b.py", "tests/test_d.py")
    assert wahl_a.dauer_s >= 0.0

    # Änderung an unabhängigem Modul C: nur test_c.
    wahl_c = import_graph.auswaehlen(repo, geaendert=["dienst/mod_c.py"])
    assert wahl_c.tests == ("tests/test_c.py",)

    # Änderung an Top-Level-Skript: nur test_skript.
    wahl_s = import_graph.auswaehlen(repo, geaendert=["skript.py"])
    assert wahl_s.tests == ("tests/test_skript.py",)

    # Änderung an Test-Hilfsmodul: alle Tests, die es importieren.
    wahl_h = import_graph.auswaehlen(repo, geaendert=["tests/spur_hilfe.py"])
    assert wahl_h.tests == (
        "tests/test_a.py",
        "tests/test_b.py",
        "tests/test_c.py",
        "tests/test_d.py",
        "tests/test_skript.py",
    )


def test_import_graph_relative_importe_und_mehrstufige_kette(tmp_path: Path) -> None:
    repo = tmp_path / "rel"
    _schreibe(repo, "pak/__init__.py", "")
    _schreibe(repo, "pak/basis.py", "X = 1\n")
    _schreibe(repo, "pak/mitte.py", "from . import basis\nY = basis.X + 1\n")
    _schreibe(repo, "pak/sub/__init__.py", "")
    _schreibe(repo, "pak/sub/ende.py", "from ..mitte import Y\nZ = Y + 1\n")
    _schreibe(repo, "pak/fremd.py", "W = 0\n")
    _schreibe(repo, "tests/test_ende.py", "from pak.sub.ende import Z\n\ndef test_z():\n    assert Z == 3\n")
    _schreibe(repo, "tests/test_fremd.py", "from pak.fremd import W\n\ndef test_w():\n    assert W == 0\n")

    wahl = import_graph.auswaehlen(repo, geaendert=["pak/basis.py"])
    assert wahl.tests == ("tests/test_ende.py",)


def test_import_graph_paket_hat_vorrang_vor_gleichnamigem_skript(tmp_path: Path) -> None:
    """``pak/__init__.py`` verdeckt ``pak.py`` wie in Python — kein Übergriff auf unbeteiligte Tests."""
    repo = tmp_path / "vorrang"
    _schreibe(repo, "pak/__init__.py", "")
    _schreibe(repo, "pak/a.py", "A = 1\n")
    _schreibe(repo, "pak/b.py", "B = 2\n")
    _schreibe(repo, "pak.py", "from pak import a\n")
    _schreibe(repo, "tests/test_a.py", "from pak import a\n\ndef test_a():\n    assert a.A == 1\n")
    _schreibe(repo, "tests/test_b.py", "from pak import b\n\ndef test_b():\n    assert b.B == 2\n")

    wahl = import_graph.auswaehlen(repo, geaendert=["pak/a.py"])
    assert wahl.tests == ("tests/test_a.py",)


def test_import_graph_waehlt_tests_fuer_geloeschtes_oder_umbenanntes_modul(tmp_path: Path) -> None:
    """Wird ein Modul gelöscht/umbenannt, werden verbliebene Tests mit Alt-Import weiter gewählt."""
    repo = tmp_path / "umbenannt_mod"
    _schreibe(repo, "rechner.py", "def addiere(a: int, b: int) -> int:\n    return a + b\n")
    _schreibe(
        repo, "tests/test_neu.py", "from rechner import addiere\n\ndef test_neu():\n    assert addiere(1, 2) == 3\n"
    )
    _schreibe(
        repo,
        "tests/test_alt.py",
        "from rechnen import addiere\n\ndef test_alt():\n    assert addiere(1, 2) == 3\n",
    )
    _schreibe(repo, "tests/test_fremd.py", "def test_fremd():\n    assert True\n")

    # rechnen.py existiert nach ``git mv rechnen.py rechner.py`` nicht mehr auf Platte.
    wahl = import_graph.auswaehlen(repo, geaendert=["rechnen.py", "rechner.py", "tests/test_neu.py"])
    assert wahl.tests == ("tests/test_alt.py", "tests/test_neu.py")


def test_import_graph_laufzeit_auf_skill_repo_in_sekunden() -> None:
    """Lokal auf dem echten Skill-Repo (ohne Einzelpaket-Zwang): echte Auswahl unter 5 s."""
    wahl = import_graph.auswaehlen(WURZEL, geaendert=["to_spawn/pruef/regeln.py"])
    assert "tests/test_pruef_auswahl_3b.py" in wahl.tests
    assert "tests/test_import_graph_580.py" in wahl.tests
    assert len(wahl.tests) < len(wahl.alle_tests)
    assert wahl.dauer_s < 5.0, f"Import-Graph zu langsam: {wahl.dauer_s:.2f} s"


def test_import_graph_cli_json_ausgabe(tmp_path: Path) -> None:
    repo = _mehrpaket_repo(tmp_path / "cli")
    erg = subprocess.run(
        [
            sys.executable,
            "-m",
            "to_spawn.pruef.import_graph",
            "--repo",
            str(repo),
            "--geaendert",
            "kern/mod_a.py",
            "--json",
        ],
        cwd=WURZEL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert erg.returncode == 0, erg.stderr
    assert erg.stderr == "", erg.stderr
    daten = json.loads(erg.stdout)
    assert daten["tests"] == ["tests/test_a.py", "tests/test_b.py", "tests/test_d.py"]
    assert daten["dauer_s"] >= 0.0


# --- 2. Andocken an adapter_py + leiter (pruefung.schnell ohne Einzelpaket) ----------------------


def test_schnell_pruefung_nutzt_import_graph_ohne_einzelpaket(tmp_path: Path) -> None:
    spur = tmp_path / "spur.txt"
    repo = _mehrpaket_repo(tmp_path / "schnell", spur_datei=spur)
    _schreibe(repo, "dienst/mod_c.py", "def wert_c() -> int:\n    return 99  # kommentar\n")
    _git(repo, "commit", "-qam", "ändert mod_c")

    urteil = pruef.pruefen(repo, "base", "HEAD", art="schnell")
    assert urteil.exit == 0, urteil.zeile
    stufe = next(s for s in urteil.zeile["stufen"] if s["name"] in ("auswahl", "voll"))
    assert stufe["name"] == "auswahl", stufe
    assert any("import-graph" in w for w in urteil.zeile["werkzeug"]), urteil.zeile["werkzeug"]
    assert spur.read_text(encoding="utf-8").splitlines() == ["test_c_wert"]


# --- 3. „Nach Rot“-Regel: merken, nie blind wiederholen, zuerst nur Rote + Auswahl --------------


def test_nach_rot_merkt_rote_verbietet_blinde_wiederholung_und_laeuft_zuerst_rote_plus_auswahl(
    tmp_path: Path,
) -> None:
    spur = tmp_path / "spur.txt"
    repo = _mehrpaket_repo(tmp_path / "nach_rot", spur_datei=spur)

    # Schritt 1: Fehler in kern/mod_a.py einbauen -> test_a_wert, test_b_wert, test_d_wert werden rot.
    # Für klaren Nachweis bauen wir den Fehler direkt in test_a_wert ein (nur 1 roter Test in test_a.py).
    _schreibe(
        repo,
        "tests/test_a.py",
        (
            "from kern.mod_a import wert_a\n"
            "from spur_hilfe import _spur\n\n\n"
            "def test_a_ok() -> None:\n"
            "    _spur('test_a_ok')\n"
            "    assert True\n\n\n"
            "def test_a_wert() -> None:\n"
            "    _spur('test_a_wert')\n"
            "    assert wert_a() == 999\n"
        ),
    )
    _git(repo, "commit", "-qam", "bricht test_a_wert")
    rot_sha = _git(repo, "rev-parse", "HEAD")

    urteil_1 = pruef.pruefen(repo, "base", "HEAD", art="schnell")
    assert urteil_1.exit == 1, urteil_1.zeile
    assert urteil_1.zeile["urteil"] == "rot"
    assert protokoll.letzte_rote(repo, "py") == ("tests/test_a.py::test_a_wert",)

    # Schritt 2: Unveränderter Stand darf NICHT blind wiederholt werden (kein neuer pytest-Lauf).
    spur.write_text("", encoding="utf-8")
    urteil_blind = pruef.pruefen(repo, "base", "HEAD", art="schnell")
    assert urteil_blind.exit == 1, urteil_blind.zeile
    assert urteil_blind.zeile["urteil"] == "rot"
    assert urteil_blind.zeile["cache_treffer"] is False
    assert spur.read_text(encoding="utf-8") == "", "roter Lauf wurde blind wiederholt"
    assert urteil_blind.zeile["stufen"][0]["name"] == "nach_rot"
    assert urteil_blind.zeile["stufen"][0]["lief"] is False
    # Gemerkte rote Tests bleiben für den nächsten echten Lauf erhalten.
    assert protokoll.letzte_rote(repo, "py") == ("tests/test_a.py::test_a_wert",)

    # Schritt 3: Neues Commit ändert NUR dienst/mod_c.py (Basis = rot_sha).
    # Der nächste Lauf muss ZUERST den roten Test (test_a_wert) PLUS die Auswahl (test_c_wert) laufen lassen —
    # NICHT test_a_ok, NICHT test_b_wert, NICHT test_d_wert, NICHT test_skript!
    _schreibe(repo, "dienst/mod_c.py", "def wert_c() -> int:\n    return 99  # angepasst\n")
    _git(repo, "commit", "-qam", "ändert mod_c")

    spur.write_text("", encoding="utf-8")
    urteil_2 = pruef.pruefen(repo, rot_sha, "HEAD", art="schnell")
    assert urteil_2.exit == 1, urteil_2.zeile
    gelaufen = spur.read_text(encoding="utf-8").splitlines()
    # Erster Lauf führt genau test_a_wert (zuerst!) + test_c_wert aus; Wackler-Wiederholung wiederholt nur test_a_wert.
    assert gelaufen[:2] == ["test_a_wert", "test_c_wert"], gelaufen
    assert set(gelaufen) == {"test_a_wert", "test_c_wert"}, gelaufen
    stufe_2 = next(s for s in urteil_2.zeile["stufen"] if s["name"] in ("auswahl", "voll"))
    assert stufe_2["name"] == "auswahl", stufe_2

    # Schritt 4: Fehler in test_a.py beheben -> Lauf wird grün, rote Tests geleert.
    _schreibe(
        repo,
        "tests/test_a.py",
        (
            "from kern.mod_a import wert_a\n"
            "from spur_hilfe import _spur\n\n\n"
            "def test_a_ok() -> None:\n"
            "    _spur('test_a_ok')\n"
            "    assert True\n\n\n"
            "def test_a_wert() -> None:\n"
            "    _spur('test_a_wert')\n"
            "    assert wert_a() == 10\n"
        ),
    )
    _git(repo, "commit", "-qam", "repariert test_a_wert")
    urteil_3 = pruef.pruefen(repo, rot_sha, "HEAD", art="schnell")
    assert urteil_3.exit == 0, urteil_3.zeile
    assert urteil_3.zeile["urteil"] == "grün"
    assert protokoll.letzte_rote(repo, "py") == ()


def test_regeln_nach_rot_mit_gemerkten_tests_erlaubt_gezielte_auswahl() -> None:
    assert regeln.vorab(["app/x.py"], "py", [], "rot").startswith("LF")
    assert regeln.vorab(["app/x.py"], "py", [], "rot", rote_tests=("tests/test_a.py::test_1",)) == ""
    assert regeln.vorab(["app/x.py"], "py", [], "gate-fehler", rote_tests=("tests/test_a.py::test_1",)).startswith("LF")


def test_ziele_mit_roten_faellt_bei_umbenanntem_test_auf_datei_zurueck(tmp_path: Path) -> None:
    repo = _mehrpaket_repo(tmp_path / "umbenannt")
    ziele = import_graph.ziele_mit_roten(
        repo,
        auswahl_tests=("tests/test_c.py",),
        rote_tests=("tests/test_a.py::test_gibt_es_nicht_mehr", "tests/geloescht.py::test_weg"),
    )
    assert ziele == ("tests/test_a.py", "tests/test_c.py")


# --- 4. Kein testmon im Produktivcode ------------------------------------------------------------


def test_kein_testmon_im_produktivcode() -> None:
    treffer: list[str] = []
    for datei in sorted((WURZEL / "to_spawn").rglob("*.py")):
        text = datei.read_text(encoding="utf-8", errors="replace")
        for nr, zeile in enumerate(text.splitlines(), 1):
            if "testmon" in zeile.lower():
                treffer.append(f"{datei.relative_to(WURZEL).as_posix()}:{nr}")
    assert treffer == [], f"testmon im Produktivcode gefunden: {treffer}"
