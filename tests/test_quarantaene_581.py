"""#798 (#581 Teil 2): Stufe 3 Wackler-Quarantäne-Ledger und Meldung langsamster Tests (Linux + Windows).

Prüft plattformübergreifend ohne externe Abhängigkeiten:
- Lesen und Normalisieren der bestehenden Wackler-Spur (``.to-spawn/pruef/wackler.jsonl``)
  über :func:`to_spawn.pruef.wackler.lese_spur` (inkl. Windows-Pfadtrenner und kaputter Zeilen).
- Erkennung wiederkehrender Wackler mit Schwelle und Zeitfenster sowie Fortschreibung des
  Quarantäne-Ledgers (``.to-spawn/pruef/quarantaene.json``) ohne je Testdateien zu löschen
  oder zu verändern.
- Ermittlung der langsamsten Tests (Top-N, maschinenlesbar + Markdown-Kurzfassung) aus einzelnen
  oder mehreren JUnit-XML-Dateien bzw. Ordnern.
- Echter Weg-Test mit frischem Git-Repo, echtem pytest-JUnit-XML, Wackler-Spur, CLI-Unterbefehlen
  und cp1252-Pipe-Sicherheit (Windows).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import prozessbaum
from to_spawn.pruef import protokoll, quarantaene, wackler


def _cli(*args: str, cwd: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    umg = {**os.environ, "PYTHONPATH": str(SKILL)}
    if extra_env:
        umg.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "to_spawn.pruef.quarantaene", *args],
        cwd=cwd,
        env=umg,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **prozessbaum.fensterlos(),
    )


def _schreibe_wackler_spur(repo: Path, zeilen: list[dict[str, str] | str]) -> Path:
    ordner = repo / protokoll.ORDNER
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / protokoll.WACKLER_DATEI
    inhalt = [z if isinstance(z, str) else json.dumps(z, ensure_ascii=False) for z in zeilen]
    pfad.write_text("\n".join(inhalt) + "\n", encoding="utf-8")
    return pfad


def _hash_baum(ordner: Path) -> dict[str, str]:
    ergebnis: dict[str, str] = {}
    for pfad in sorted(ordner.rglob("*")):
        if pfad.is_file() and "__pycache__" not in pfad.parts:
            rel = pfad.relative_to(ordner).as_posix()
            ergebnis[rel] = hashlib.sha256(pfad.read_bytes()).hexdigest()
    return ergebnis


# --- 1. Wackler-Spur lesen & normalisieren -------------------------------------------------------


def test_lese_spur_normalisiert_windows_pfade_und_ueberspringt_kaputte_zeilen(tmp_path: Path) -> None:
    _schreibe_wackler_spur(
        tmp_path,
        [
            {
                "zeit": "2026-11-20T10:00:00+00:00",
                "basis": "aaa",
                "ziel": "bbb",
                "test": r"tests\sub\test_a.py::test_wackelt",
                "adapter": "python",
            },
            "{kaputtes json",
            json.dumps({"zeit": "2026-11-21T10:00:00+00:00", "test": ""}),
            {
                "zeit": "2026-11-22T12:30:00+00:00",
                "basis": "bbb",
                "ziel": "ccc",
                "test": "tests/sub/test_a.py::test_wackelt",
                "adapter": "python",
            },
        ],
    )
    funde = wackler.lese_spur(tmp_path)
    assert len(funde) == 2
    assert [f.test for f in funde] == [
        "tests/sub/test_a.py::test_wackelt",
        "tests/sub/test_a.py::test_wackelt",
    ]
    assert funde[0].ziel == "bbb"
    assert funde[1].ziel == "ccc"


def test_lese_spur_fehlt_liefert_leer(tmp_path: Path) -> None:
    assert wackler.lese_spur(tmp_path) == ()


# --- 2. Wiederkehrende Wackler erkennen ----------------------------------------------------------


def test_erkenne_wiederkehrende_wackler_schwelle_und_zeitfenster() -> None:
    jetzt = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
    funde = (
        # Alt (außerhalb 14-Tage-Fenster)
        wackler.WacklerFund("tests/test_alt.py::test_1", "python", "2026-10-01T10:00:00+00:00", "b0", "z0"),
        wackler.WacklerFund("tests/test_alt.py::test_1", "python", "2026-10-05T10:00:00+00:00", "b1", "z1"),
        # Einmaliger Wackler (unter Schwelle 2)
        wackler.WacklerFund("tests/test_einmal.py::test_2", "python", "2026-11-24T10:00:00+00:00", "b2", "z2"),
        # Wiederkehrend (2 Treffer)
        wackler.WacklerFund("tests/test_oft.py::test_b", "python", "2026-11-20T08:00:00+00:00", "b3", "z3"),
        wackler.WacklerFund("tests/test_oft.py::test_b", "python", "2026-11-24T09:00:00+00:00", "b4", "z4"),
        # Noch häufiger (3 Treffer) — muss zuerst kommen
        wackler.WacklerFund("tests/test_sehr_oft.py::test_a", "python", "2026-11-18T08:00:00+00:00", "b5", "z5"),
        wackler.WacklerFund("tests/test_sehr_oft.py::test_a", "python", "2026-11-19T08:00:00+00:00", "b6", "z6"),
        wackler.WacklerFund("tests/test_sehr_oft.py::test_a", "python", "2026-11-25T08:00:00+00:00", "b7", "z7"),
    )

    vorschlaege = quarantaene.erkenne_wiederkehrende_wackler(funde, schwelle=2, fenster_tage=14, jetzt=jetzt)
    assert [v.test for v in vorschlaege] == [
        "tests/test_sehr_oft.py::test_a",
        "tests/test_oft.py::test_b",
    ]
    assert vorschlaege[0].treffer == 3
    assert vorschlaege[0].status == quarantaene.STATUS_VORGESCHLAGEN
    assert vorschlaege[0].aktion == "quarantaene_vorschlagen"
    assert vorschlaege[0].staende == ("z5", "z6", "z7")
    assert vorschlaege[1].treffer == 2

    # Ohne Zeitfenster (None) wird auch der alte Wackler erkannt
    alle = quarantaene.erkenne_wiederkehrende_wackler(funde, schwelle=2, fenster_tage=None, jetzt=jetzt)
    assert "tests/test_alt.py::test_1" in [v.test for v in alle]


def test_erkenne_wiederkehrende_wackler_ungueltige_grenzen() -> None:
    with pytest.raises(ValueError):
        quarantaene.erkenne_wiederkehrende_wackler((), schwelle=0)
    with pytest.raises(ValueError):
        quarantaene.erkenne_wiederkehrende_wackler((), fenster_tage=0)


# --- 3. Quarantäne-Ledger: Fortschreibung, Statuserhalt, nie löschen -----------------------------


def test_aktualisiere_ledger_erzeugt_vorschlaege_und_loescht_nie_tests(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_datei = tests_dir / "test_wichtig.py"
    test_datei.write_text("def test_wackel():\n    assert True\n", encoding="utf-8")
    vorher = _hash_baum(tests_dir)

    _schreibe_wackler_spur(
        tmp_path,
        [
            {
                "zeit": "2026-11-23T10:00:00+00:00",
                "basis": "a1",
                "ziel": "c1",
                "test": "tests/test_wichtig.py::test_wackel",
                "adapter": "python",
            },
            {
                "zeit": "2026-11-24T10:00:00+00:00",
                "basis": "c1",
                "ziel": "c2",
                "test": "tests/test_wichtig.py::test_wackel",
                "adapter": "python",
            },
        ],
    )
    jetzt = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)

    # Dry-Run (schreiben=False) schreibt kein Ledger
    trocken = quarantaene.aktualisiere_ledger(tmp_path, schwelle=2, fenster_tage=14, jetzt=jetzt, schreiben=False)
    assert not trocken.geschrieben
    assert len(trocken.vorschlaege) == 1
    assert not (tmp_path / protokoll.ORDNER / protokoll.QUARANTAENE_DATEI).exists()

    # Echter Lauf schreibt Ledger und .gitignore, lässt Testdatei bytegleich
    erg = quarantaene.aktualisiere_ledger(tmp_path, schwelle=2, fenster_tage=14, jetzt=jetzt, schreiben=True)
    assert erg.geschrieben
    assert erg.neu == ("tests/test_wichtig.py::test_wackel",)
    assert erg.bestehend == ()
    ledger_datei = tmp_path / protokoll.ORDNER / protokoll.QUARANTAENE_DATEI
    assert ledger_datei.is_file()
    assert (tmp_path / protokoll.ORDNER / ".gitignore").read_text(encoding="utf-8").strip() == "*"
    assert _hash_baum(tests_dir) == vorher, "Quarantäne-Lauf darf niemals Testdateien löschen oder ändern"

    geladen = quarantaene.lade_ledger(tmp_path)
    assert len(geladen.eintraege) == 1
    eintrag = geladen.eintraege[0]
    assert eintrag.test == "tests/test_wichtig.py::test_wackel"
    assert eintrag.status == quarantaene.STATUS_VORGESCHLAGEN
    assert eintrag.treffer == 2


def test_aktualisiere_ledger_erhaelt_bestaetigte_quarantaene_und_reaktiviert_aufgehoben(tmp_path: Path) -> None:
    jetzt = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
    alt = quarantaene.QuarantaeneLedger(
        aktualisiert="2026-11-20T10:00:00+00:00",
        schwelle=2,
        fenster_tage=14,
        eintraege=(
            quarantaene.QuarantaeneEintrag(
                test="tests/test_a.py::test_bestaetigt",
                adapter="python",
                status=quarantaene.STATUS_QUARANTAENE,
                treffer=2,
                erster_fund="2026-11-15T10:00:00+00:00",
                letzter_fund="2026-11-18T10:00:00+00:00",
                aktualisiert="2026-11-20T10:00:00+00:00",
                grund="Manuell bestätigt",
            ),
            quarantaene.QuarantaeneEintrag(
                test="tests/test_b.py::test_alt_ruhig",
                adapter="python",
                status=quarantaene.STATUS_QUARANTAENE,
                treffer=3,
                erster_fund="2026-11-01T10:00:00+00:00",
                letzter_fund="2026-11-02T10:00:00+00:00",
                aktualisiert="2026-11-02T10:00:00+00:00",
                grund="Bleibt erhalten",
            ),
            quarantaene.QuarantaeneEintrag(
                test="tests/test_c.py::test_wieder_wacklig",
                adapter="python",
                status=quarantaene.STATUS_AUFGEHOBEN,
                treffer=2,
                erster_fund="2026-11-01T10:00:00+00:00",
                letzter_fund="2026-11-02T10:00:00+00:00",
                aktualisiert="2026-11-10T10:00:00+00:00",
                grund="War behoben",
            ),
        ),
    )
    quarantaene.schreibe_ledger(tmp_path, alt)

    _schreibe_wackler_spur(
        tmp_path,
        [
            {
                "zeit": "2026-11-22T10:00:00+00:00",
                "basis": "a",
                "ziel": "b1",
                "test": "tests/test_a.py::test_bestaetigt",
                "adapter": "python",
            },
            {
                "zeit": "2026-11-24T10:00:00+00:00",
                "basis": "b1",
                "ziel": "b2",
                "test": "tests/test_a.py::test_bestaetigt",
                "adapter": "python",
            },
            {
                "zeit": "2026-11-23T10:00:00+00:00",
                "basis": "b2",
                "ziel": "b3",
                "test": "tests/test_c.py::test_wieder_wacklig",
                "adapter": "python",
            },
            {
                "zeit": "2026-11-25T10:00:00+00:00",
                "basis": "b3",
                "ziel": "b4",
                "test": "tests/test_c.py::test_wieder_wacklig",
                "adapter": "python",
            },
        ],
    )

    erg = quarantaene.aktualisiere_ledger(tmp_path, schwelle=2, fenster_tage=14, jetzt=jetzt)
    nach_name = {e.test: e for e in erg.ledger.eintraege}
    assert nach_name["tests/test_a.py::test_bestaetigt"].status == quarantaene.STATUS_QUARANTAENE
    assert nach_name["tests/test_a.py::test_bestaetigt"].treffer == 2
    assert nach_name["tests/test_b.py::test_alt_ruhig"].status == quarantaene.STATUS_QUARANTAENE
    assert nach_name["tests/test_c.py::test_wieder_wacklig"].status == quarantaene.STATUS_VORGESCHLAGEN
    assert erg.ledger.deselect_argumente() == (
        "--deselect",
        "tests/test_a.py::test_bestaetigt",
        "--deselect",
        "tests/test_b.py::test_alt_ruhig",
    )


def test_lade_ledger_kaputte_datei_liefert_leer(tmp_path: Path) -> None:
    pfad = tmp_path / protokoll.ORDNER / protokoll.QUARANTAENE_DATEI
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text("{kaputt", encoding="utf-8")
    geladen = quarantaene.lade_ledger(tmp_path)
    assert geladen.eintraege == ()


def test_aufgehobener_eintrag_wird_ohne_neue_funde_nicht_erneut_vorgeschlagen(tmp_path: Path) -> None:
    jetzt = datetime(2026, 11, 25, 12, 0, 0, tzinfo=UTC)
    alt = quarantaene.QuarantaeneLedger(
        aktualisiert="2026-11-24T12:00:00+00:00",
        schwelle=2,
        fenster_tage=14,
        eintraege=(
            quarantaene.QuarantaeneEintrag(
                test="tests/test_behoben.py::test_ok",
                adapter="python",
                status=quarantaene.STATUS_AUFGEHOBEN,
                treffer=2,
                erster_fund="2026-11-20T10:00:00+00:00",
                letzter_fund="2026-11-22T10:00:00+00:00",
                aktualisiert="2026-11-24T12:00:00+00:00",
                grund="Behoben",
            ),
        ),
    )
    quarantaene.schreibe_ledger(tmp_path, alt)
    _schreibe_wackler_spur(
        tmp_path,
        [
            {
                "zeit": "2026-11-20T10:00:00+00:00",
                "basis": "a",
                "ziel": "b1",
                "test": "tests/test_behoben.py::test_ok",
                "adapter": "python",
            },
            {
                "zeit": "2026-11-22T10:00:00+00:00",
                "basis": "b1",
                "ziel": "b2",
                "test": "tests/test_behoben.py::test_ok",
                "adapter": "python",
            },
        ],
    )
    erg = quarantaene.aktualisiere_ledger(tmp_path, schwelle=2, fenster_tage=14, jetzt=jetzt)
    assert erg.ledger.eintraege[0].status == quarantaene.STATUS_AUFGEHOBEN


# --- 4. Langsamste Tests aus JUnit-XML -----------------------------------------------------------


def test_langsamste_tests_mehrere_junit_dateien_und_kurzfassung(tmp_path: Path) -> None:
    ordner = tmp_path / "berichte"
    ordner.mkdir()
    (ordner / "shard1.xml").write_text(
        textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <testsuites>
              <testsuite name="pytest" tests="3" failures="1" errors="0" skipped="0">
                <testcase classname="tests.test_kern" name="test_schnell" file="tests/test_kern.py" time="0.05" />
                <testcase classname="tests.test_kern" name="test_mittel" file="tests/test_kern.py" time="1.25" />
                <testcase classname="tests.test_kern" name="test_rot_langsam" file="tests/test_kern.py" time="4.50">
                  <failure message="boom">Traceback</failure>
                </testcase>
              </testsuite>
            </testsuites>
            """),
        encoding="utf-8",
    )
    (ordner / "shard2.xml").write_text(
        textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <testsuite name="pytest" tests="3" failures="0" errors="0" skipped="1">
              <testcase classname="tests.test_ui" name="test_sehr_langsam" file="tests\\test_ui.py" time="9.80" />
              <testcase classname="tests.test_ui" name="test_skip" time="5.00">
                <skipped message="nur lokal" />
              </testcase>
              <testcase classname="tests.test_ui" name="test_kaputte_zeit" time="NaN" />
            </testsuite>
            """),
        encoding="utf-8",
    )

    bericht = quarantaene.langsamste_tests(ordner, top_n=3)
    assert bericht.fehler == ()
    assert bericht.gesamt_tests == 6
    assert [e.test for e in bericht.eintraege] == [
        "tests.test_ui::test_sehr_langsam",
        "tests.test_ui::test_skip",
        "tests.test_kern::test_rot_langsam",
    ]
    assert bericht.eintraege[0].datei == "tests/test_ui.py"
    assert bericht.eintraege[0].dauer_s == pytest.approx(9.8)
    assert bericht.eintraege[1].status == "uebersprungen"
    assert bericht.eintraege[2].status == "rot"

    # Maschinenlesbares Dict und Kurzfassung
    daten = bericht.als_dict()
    assert daten["top_n"] == 3
    assert daten["gesamt_tests"] == 6
    assert len(daten["eintraege"]) == 3
    kurz = bericht.kurzfassung()
    assert "tests.test_ui::test_sehr_langsam" in kurz
    assert "9.80 s" in kurz

    # Filter: ohne übersprungene + Mindestdauer
    gefiltert = quarantaene.langsamste_tests(ordner, top_n=5, mindest_s=1.0, ohne_uebersprungene=True)
    assert [e.test for e in gefiltert.eintraege] == [
        "tests.test_ui::test_sehr_langsam",
        "tests.test_kern::test_rot_langsam",
        "tests.test_kern::test_mittel",
    ]


def test_langsamste_tests_fehlende_und_kaputte_xml(tmp_path: Path) -> None:
    kaputt = tmp_path / "kaputt.xml"
    kaputt.write_text("<testsuite><unclosed>", encoding="utf-8")
    fehlt = tmp_path / "fehlt.xml"

    bericht = quarantaene.langsamste_tests([fehlt, kaputt], top_n=5)
    assert bericht.eintraege == ()
    assert bericht.gesamt_tests == 0
    assert len(bericht.fehler) == 2


# --- 5. Echter Weg-Test mit pytest-JUnit-XML, Wackler-Spur, CLI und cp1252-Pipe ------------------


def test_weg_echtes_pytest_junit_wackler_cli_und_keine_loeschung(tmp_path: Path) -> None:
    repo = tmp_path / "probe_repo"
    tests_dir = repo / "tests"
    tests_dir.mkdir(parents=True)
    (repo / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (tests_dir / "test_laufzeit.py").write_text(
        textwrap.dedent("""\
            import time


            def test_flott() -> None:
                assert 1 + 1 == 2


            def test_traege() -> None:
                time.sleep(0.08)
                assert True
            """),
        encoding="utf-8",
    )
    vorher = _hash_baum(tests_dir)

    # 1. Echtes pytest-JUnit-XML erzeugen
    junit_xml = tmp_path / "junit.xml"
    lauf = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit_xml}", "tests"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        **prozessbaum.fensterlos(),
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert junit_xml.is_file()

    # 2. Echte Wackler-Spur über protokoll.schreibe anlegen (2 wiederkehrende Funde)
    for basis, ziel in (("c0", "c1"), ("c1", "c2")):
        zeile = protokoll.zeile(
            basis=basis,
            ziel=ziel,
            exit_code=0,
            stufen=[],
            werkzeug=["pytest"],
            fehler_kurz="",
            gesamt_s=0.5,
            wackler=[{"test": "tests/test_laufzeit.py::test_traege", "adapter": "python"}],
        )
        protokoll.schreibe(repo, zeile)

    # 3. Stufe-3-Gesamtbericht per CLI (JSON + Summary + cp1252-Pipe-Sicherheit)
    summary = tmp_path / "summary.md"
    json_ausgabe = tmp_path / "stufe3.json"
    erg = _cli(
        "bericht",
        "--repo",
        str(repo),
        "--junit",
        str(junit_xml),
        "--top",
        "2",
        "--schwelle",
        "2",
        "--summary-pfad",
        str(summary),
        "--ausgabe-json",
        str(json_ausgabe),
        "--json",
        cwd=repo,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert erg.returncode == 0, erg.stdout + erg.stderr

    daten = json.loads(erg.stdout)
    assert daten["nur_vorschlaege"] is True
    assert daten["geloeschte_dateien"] == []
    assert [v["test"] for v in daten["quarantaene"]["vorschlaege"]] == ["tests/test_laufzeit.py::test_traege"]
    assert daten["langsamste"]["gesamt_tests"] == 2
    assert daten["langsamste"]["eintraege"][0]["name"] == "test_traege"

    # JSON-Datei und Markdown-Summary geschrieben
    assert json.loads(json_ausgabe.read_text(encoding="utf-8")) == daten
    summary_text = summary.read_text(encoding="utf-8")
    assert "Quarantäne" in summary_text
    assert "test_traege" in summary_text
    assert "keine Tests gelöscht" in summary_text

    # Ledger angelegt, Testdateien unverändert (kein eigenmächtiges Löschen)
    assert (repo / protokoll.ORDNER / protokoll.QUARANTAENE_DATEI).is_file()
    assert _hash_baum(tests_dir) == vorher


def test_cli_unterbefehle_quarantaene_und_langsamste_fehlercode(tmp_path: Path) -> None:
    # Fehlende JUnit-Datei → Exit 2
    erg_fehlt = _cli("langsamste", "--junit", str(tmp_path / "gibts_nicht.xml"), cwd=tmp_path)
    assert erg_fehlt.returncode == 2

    # Unterbefehl quarantaene im leeren Repo → Exit 0 mit 0 Vorschlägen (auch unter cp1252)
    erg_leer = _cli(
        "quarantaene",
        "--repo",
        str(tmp_path),
        "--nur-pruefen",
        "--json",
        cwd=tmp_path,
        extra_env={"PYTHONIOENCODING": "cp1252"},
    )
    assert erg_leer.returncode == 0, erg_leer.stdout + erg_leer.stderr
    daten = json.loads(erg_leer.stdout)
    assert daten["vorschlaege"] == []
    assert daten["geschrieben"] is False
