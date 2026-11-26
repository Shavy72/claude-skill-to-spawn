"""Weg- und Unit-Tests für Stufe 3 Aufräum-Job, Doppelte-Vorschläge, Schutzliste und Zweitprüfer (#800, #581)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn.pruef import aufraeumen, pipeline_vorlage, protokoll, quarantaene


def _schreibe_datei(pfad: Path, inhalt: str) -> Path:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(inhalt, encoding="utf-8", newline="\n")
    return pfad


def _beispiel_repo(tmp_path: Path) -> Path:
    """Erstellt ein kleines Test-Repo mit doppelten, überdeckten und geschützten Tests."""
    _schreibe_datei(
        tmp_path / "app" / "rechner.py",
        "def addiere(a: int, b: int) -> int:\n    return a + b\n\n"
        "def subtrahiere(a: int, b: int) -> int:\n    return a - b\n",
    )
    _schreibe_datei(tmp_path / "app" / "__init__.py", "")
    _schreibe_datei(
        tmp_path / "tests" / "test_rechner_basis.py",
        """from app.rechner import addiere, subtrahiere


def test_addiere_einfach():
    ergebnis = addiere(2, 3)
    assert ergebnis == 5


def test_addiere_doppelt_umbenannt():
    wert = addiere(2, 3)
    assert wert == 5


def test_addiere_schmal():
    assert addiere(1, 1) == 2


def test_addiere_breit():
    assert addiere(1, 1) == 2
    assert addiere(2, 3) == 5
    assert subtrahiere(5, 2) == 3
""",
    )
    return tmp_path


class TestDoppelteVorschlaege:
    """Doppelte und überdeckte Tests werden erkannt — ausschließlich als Vorschläge, nie gelöscht."""

    def test_erkennt_duplikat_und_ueberdeckt_ohne_zu_loeschen(self, tmp_path: Path) -> None:
        repo = _beispiel_repo(tmp_path)
        test_datei = repo / "tests" / "test_rechner_basis.py"
        vorher = test_datei.read_text(encoding="utf-8")

        bericht = aufraeumen.finde_doppelte(repo, ersteller_agent="aufraeum-agent-1")

        assert bericht.nur_vorschlaege is True
        assert bericht.geloeschte_dateien == ()
        assert test_datei.read_text(encoding="utf-8") == vorher

        arten = {(v.kandidat, v.art) for v in bericht.vorschlaege}
        assert ("tests/test_rechner_basis.py::test_addiere_doppelt_umbenannt", "duplikat") in arten
        assert ("tests/test_rechner_basis.py::test_addiere_schmal", "ueberdeckt") in arten
        for vorschlag in bericht.vorschlaege:
            assert vorschlag.aktion == "loeschen_vorschlagen"
            assert vorschlag.geschuetzt is False
            assert vorschlag.erfordert_zweitpruefer is True
            assert vorschlag.ersteller_agent == "aufraeum-agent-1"
            assert vorschlag.inhalts_hash


class TestSchutzliste:
    """Nutzer-Ablauf-Tests und Tests mit echtem Fund bleiben immer geschützt (unumgehbare Sperre)."""

    def test_nutzer_ablauf_per_marker_und_pfad_immer_geschuetzt(self, tmp_path: Path) -> None:
        _schreibe_datei(
            tmp_path / "tests" / "test_normal.py",
            """def test_login_einfach():
    status = 200
    assert status == 200
""",
        )
        _schreibe_datei(
            tmp_path / "tests" / "test_ablauf_marker.py",
            """import pytest


@pytest.mark.nutzer_ablauf
def test_login_mit_marker():
    status = 200
    assert status == 200
""",
        )
        _schreibe_datei(
            tmp_path / "tests" / "e2e" / "test_login_flow.py",
            """def test_login_e2e_pfad():
    status = 200
    assert status == 200
""",
        )

        bericht = aufraeumen.finde_doppelte(tmp_path)

        vorgeschlagene_ids = {v.kandidat for v in bericht.vorschlaege}
        assert "tests/test_ablauf_marker.py::test_login_mit_marker" not in vorgeschlagene_ids
        assert "tests/e2e/test_login_flow.py::test_login_e2e_pfad" not in vorgeschlagene_ids
        # Der ungeschützte Test darf dagegen als durch den geschützten Test abgedeckt vorgeschlagen werden.
        assert "tests/test_normal.py::test_login_einfach" in vorgeschlagene_ids
        # Geschützte Duplikate landen höchstens in geschuetzte_treffer mit aktion=geschuetzt_behalten.
        assert any(
            t.kandidat == "tests/e2e/test_login_flow.py::test_login_e2e_pfad"
            and t.geschuetzt
            and aufraeumen.SCHUTZ_NUTZER_ABLAUF in t.schutz_kategorien
            and t.aktion == "geschuetzt_behalten"
            for t in bericht.geschuetzte_treffer
        )

    def test_echter_fund_per_marker_und_regression_immer_geschuetzt(self, tmp_path: Path) -> None:
        _schreibe_datei(
            tmp_path / "tests" / "test_basis.py",
            """def test_datum_basis():
    wert = "2026-01-01"
    assert wert.startswith("2026")
""",
        )
        _schreibe_datei(
            tmp_path / "tests" / "test_fund_marker.py",
            """import pytest


@pytest.mark.echter_fund
def test_datum_echter_fund():
    wert = "2026-01-01"
    assert wert.startswith("2026")
""",
        )
        _schreibe_datei(
            tmp_path / "tests" / "test_regression_412.py",
            '''def test_datum_vorfall():
    """Echter Fund aus Bug #412: Jahreswechsel brach Parser."""
    wert = "2026-01-01"
    assert wert.startswith("2026")
''',
        )

        bericht = aufraeumen.finde_doppelte(tmp_path)
        vorgeschlagene_ids = {v.kandidat for v in bericht.vorschlaege}

        assert "tests/test_fund_marker.py::test_datum_echter_fund" not in vorgeschlagene_ids
        assert "tests/test_regression_412.py::test_datum_vorfall" not in vorgeschlagene_ids
        assert any(
            t.geschuetzt and aufraeumen.SCHUTZ_ECHTER_FUND in t.schutz_kategorien for t in bericht.geschuetzte_treffer
        )

    def test_konfig_ergaenzt_schutzliste_aber_kann_pflicht_nie_abschalten(self, tmp_path: Path) -> None:
        _schreibe_datei(
            tmp_path / ".to-spawn" / "config.json",
            json.dumps(
                {
                    "aufraeumen": {
                        "schutzliste": {
                            "deaktiviert": True,
                            "pflicht_deaktivieren": True,
                            "marker": ["kasse_kritisch"],
                            "pfad_muster": ["*zahlung*"],
                            "namen_muster": ["*steuer*"],
                        }
                    }
                }
            ),
        )
        _schreibe_datei(
            tmp_path / "tests" / "test_mix.py",
            """import pytest


@pytest.mark.nutzer_ablauf
def test_ablauf_bleibt_trotz_deaktiviert():
    assert 1 + 1 == 2


@pytest.mark.echter_fund
def test_fund_bleibt_trotz_deaktiviert():
    assert 1 + 1 == 2


@pytest.mark.kasse_kritisch
def test_konfig_marker_geschuetzt():
    assert 1 + 1 == 2


def test_steuer_berechnung():
    assert 1 + 1 == 2


def test_einfach_frei():
    assert 1 + 1 == 2
""",
        )

        bericht = aufraeumen.finde_doppelte(tmp_path)
        vorgeschlagene_ids = {v.kandidat for v in bericht.vorschlaege}

        assert "tests/test_mix.py::test_ablauf_bleibt_trotz_deaktiviert" not in vorgeschlagene_ids
        assert "tests/test_mix.py::test_fund_bleibt_trotz_deaktiviert" not in vorgeschlagene_ids
        assert "tests/test_mix.py::test_konfig_marker_geschuetzt" not in vorgeschlagene_ids
        assert "tests/test_mix.py::test_steuer_berechnung" not in vorgeschlagene_ids
        assert "tests/test_mix.py::test_einfach_frei" in vorgeschlagene_ids


class TestZweitprueferSperre:
    """Löschen ist technisch erst nach bestätigtem Prüf-Nachweis eines zweiten unabhängigen Agenten zulässig."""

    def _beispiel_vorschlag(self, tmp_path: Path) -> tuple[aufraeumen.DoppelteVorschlag, aufraeumen.TestFall]:
        repo = _beispiel_repo(tmp_path)
        bericht = aufraeumen.finde_doppelte(repo, ersteller_agent="aufraeum-agent-a")
        vorschlag = next(v for v in bericht.vorschlaege if v.kandidat.endswith("::test_addiere_doppelt_umbenannt"))
        faelle = {f.test_id: f for f in aufraeumen.sammle_testfaelle(repo)}
        return vorschlag, faelle[vorschlag.kandidat]

    def test_ohne_nachweis_fail_closed_abgelehnt(self, tmp_path: Path) -> None:
        vorschlag, fall = self._beispiel_vorschlag(tmp_path)
        freigabe = aufraeumen.pruefe_loeschfreigabe(vorschlag, None, test_fall=fall)
        assert freigabe.erlaubt is False
        assert any("Nachweis" in g for g in freigabe.gruende)

    def test_selbst_freigabe_desselben_agenten_abgelehnt(self, tmp_path: Path) -> None:
        vorschlag, fall = self._beispiel_vorschlag(tmp_path)
        nachweis = aufraeumen.ZweitprueferNachweis(
            vorschlag_id=vorschlag.vorschlag_id,
            test_id=vorschlag.kandidat,
            pruefer_agent="  AUFRAEUM-AGENT-A ",
            bestaetigt=True,
            begruendung="Ich bestätige mich selbst.",
            geprueft_am="2026-11-26T01:00:00+00:00",
            pruefsumme=vorschlag.inhalts_hash,
        )
        freigabe = aufraeumen.pruefe_loeschfreigabe(vorschlag, nachweis, test_fall=fall)
        assert freigabe.erlaubt is False
        assert any("Selbst-Freigabe" in g for g in freigabe.gruende)

    def test_gleiche_session_id_als_selbst_freigabe_abgelehnt(self, tmp_path: Path) -> None:
        vorschlag, fall = self._beispiel_vorschlag(tmp_path)
        nachweis = aufraeumen.ZweitprueferNachweis(
            vorschlag_id=vorschlag.vorschlag_id,
            test_id=vorschlag.kandidat,
            pruefer_agent="zweitpruefer-b",
            bestaetigt=True,
            begruendung="Geprüft in derselben Session.",
            geprueft_am="2026-11-26T01:00:00+00:00",
            pruefsumme=vorschlag.inhalts_hash,
            session_id="sess-99",
        )
        freigabe = aufraeumen.pruefe_loeschfreigabe(vorschlag, nachweis, test_fall=fall, ersteller_session_id="sess-99")
        assert freigabe.erlaubt is False
        assert any("Session" in g for g in freigabe.gruende)

    def test_unbestaetigt_oder_falsche_pruefsumme_abgelehnt(self, tmp_path: Path) -> None:
        vorschlag, fall = self._beispiel_vorschlag(tmp_path)
        unbestaetigt = aufraeumen.ZweitprueferNachweis(
            vorschlag_id=vorschlag.vorschlag_id,
            test_id=vorschlag.kandidat,
            pruefer_agent="pruef-agent-b",
            bestaetigt=False,
            begruendung="Noch unklar.",
            geprueft_am="2026-11-26T01:00:00+00:00",
            pruefsumme=vorschlag.inhalts_hash,
        )
        assert aufraeumen.pruefe_loeschfreigabe(vorschlag, unbestaetigt, test_fall=fall).erlaubt is False

        manipuliert = aufraeumen.ZweitprueferNachweis(
            vorschlag_id=vorschlag.vorschlag_id,
            test_id=vorschlag.kandidat,
            pruefer_agent="pruef-agent-b",
            bestaetigt=True,
            begruendung="Duplikat verifiziert.",
            geprueft_am="2026-11-26T01:00:00+00:00",
            pruefsumme="0" * 64,
        )
        freigabe = aufraeumen.pruefe_loeschfreigabe(vorschlag, manipuliert, test_fall=fall)
        assert freigabe.erlaubt is False
        assert any("Prüfsumme" in g for g in freigabe.gruende)

    def test_unabhaengiger_zweitpruefer_erteilt_freigabe(self, tmp_path: Path) -> None:
        vorschlag, fall = self._beispiel_vorschlag(tmp_path)
        nachweis = aufraeumen.ZweitprueferNachweis(
            vorschlag_id=vorschlag.vorschlag_id,
            test_id=vorschlag.kandidat,
            pruefer_agent="pruef-agent-b",
            bestaetigt=True,
            begruendung="Identischer AST-Körper zu test_addiere_einfach, kein Nutzer-Ablauf und kein Fund.",
            geprueft_am="2026-11-26T01:00:00+00:00",
            pruefsumme=vorschlag.inhalts_hash,
            session_id="sess-pruefer-2",
        )
        freigabe = aufraeumen.pruefe_loeschfreigabe(
            vorschlag, nachweis, test_fall=fall, ersteller_session_id="sess-ersteller-1"
        )
        assert freigabe.erlaubt is True
        assert freigabe.ersteller_agent == "aufraeum-agent-a"
        assert freigabe.pruefer_agent == "pruef-agent-b"

    def test_schutzliste_blockt_sogar_mit_gueltigem_zweitpruefer(self, tmp_path: Path) -> None:
        _schreibe_datei(
            tmp_path / "tests" / "test_e2e_checkout.py",
            """import pytest


@pytest.mark.nutzer_ablauf
def test_checkout_ablauf():
    assert True is True
""",
        )
        faelle = aufraeumen.sammle_testfaelle(tmp_path)
        fall = faelle[0]
        # Selbst ein künstlich als ungeschützt konstruierter Vorschlag darf die Schutzliste nicht umgehen!
        vorschlag = aufraeumen.DoppelteVorschlag(
            vorschlag_id="v-manipuliert",
            kandidat=fall.test_id,
            abgedeckt_durch="tests/test_anders.py::test_a",
            art="duplikat",
            datei=fall.datei,
            zeile=fall.zeile,
            aehnlichkeit=1.0,
            begruendung="test",
            geschuetzt=False,
            schutz_kategorien=(),
            schutz_gruende=(),
            aktion="loeschen_vorschlagen",
            ersteller_agent="agent-1",
            inhalts_hash=fall.inhalts_hash,
        )
        nachweis = aufraeumen.ZweitprueferNachweis(
            vorschlag_id="v-manipuliert",
            test_id=fall.test_id,
            pruefer_agent="agent-2",
            bestaetigt=True,
            begruendung="Freigabe versucht",
            geprueft_am="2026-11-26T01:00:00+00:00",
            pruefsumme=fall.inhalts_hash,
        )
        freigabe = aufraeumen.pruefe_loeschfreigabe(vorschlag, nachweis, test_fall=fall)
        assert freigabe.erlaubt is False
        assert freigabe.schutz.geschuetzt is True
        assert aufraeumen.SCHUTZ_NUTZER_ABLAUF in freigabe.schutz.kategorien


class TestStufe3VorlageUndRunde:
    """Wöchentliche workflow_call-Vorlage (.github/workflows/pipeline-stufe-3.yml) und gebündelte Runde."""

    def test_vorlage_stufe3_erfuellt_vertrag(self) -> None:
        yaml_text = aufraeumen.vorlage_stufe3_yaml()
        assert aufraeumen.pruefe_stufe3_vorlage_text(yaml_text) == []
        assert "workflow_call:" in yaml_text
        assert "contents: read" in yaml_text
        assert "contents: write" not in yaml_text
        assert "to_spawn.pruef.aufraeumen" in yaml_text
        assert "to_spawn.pruef.quarantaene" in yaml_text
        assert "to_spawn.pruef.mutation" in yaml_text

    def test_eigenanschluss_stufe3_woechentlich_erfuellt_vertrag(self) -> None:
        yaml_text = aufraeumen.eigenanschluss_stufe3_yaml()
        assert aufraeumen.pruefe_eigenanschluss_stufe3_text(yaml_text) == []
        assert "schedule:" in yaml_text
        assert "cron:" in yaml_text
        assert "./.github/workflows/pipeline-stufe-3.yml" in yaml_text

    def test_pruefe_vorlage_meldet_schreibrechte_und_fehlende_jobs(self) -> None:
        kaputt = aufraeumen.vorlage_stufe3_yaml().replace("contents: read", "contents: write")
        befunde = aufraeumen.pruefe_stufe3_vorlage_text(kaputt)
        assert befunde

    def test_repo_workflow_stufe3_vorlage_vorhanden_und_synchron(self) -> None:
        repo_wurzel = Path(__file__).resolve().parents[1]
        pfad = repo_wurzel / ".github" / "workflows" / "pipeline-stufe-3.yml"
        assert pfad.is_file(), f"Stufe-3-Vorlage fehlt: {pfad}"
        inhalt = pfad.read_text(encoding="utf-8")
        assert aufraeumen.pruefe_stufe3_vorlage_text(inhalt) == []
        assert inhalt == aufraeumen.vorlage_stufe3_yaml()
        assert aufraeumen.main(["vorlage", "--pruefen", str(pfad)]) == 0
        assert pipeline_vorlage.main(["vorlage", "--stufe", "3", "--pruefen", str(pfad)]) == 0

    def test_repo_workflow_stufe3_eigenanschluss_vorhanden_und_synchron(self) -> None:
        repo_wurzel = Path(__file__).resolve().parents[1]
        pfad = repo_wurzel / ".github" / "workflows" / "pipeline-stufe-3-eigen.yml"
        assert pfad.is_file(), f"Stufe-3-Eigenanschluss fehlt: {pfad}"
        inhalt = pfad.read_text(encoding="utf-8")
        assert aufraeumen.pruefe_eigenanschluss_stufe3_text(inhalt) == []
        assert inhalt == aufraeumen.eigenanschluss_stufe3_yaml()
        assert aufraeumen.main(["vorlage", "--eigenanschluss", "--pruefen", str(pfad)]) == 0

    def test_aufraeum_runde_buendelt_doppelte_quarantaene_und_mutation(self, tmp_path: Path) -> None:
        repo = _beispiel_repo(tmp_path)
        # Wackler-Spur für Teil 2 anlegen
        wackler_datei = repo / protokoll.ORDNER / protokoll.WACKLER_DATEI
        wackler_datei.parent.mkdir(parents=True, exist_ok=True)
        eintraege = [
            {
                "zeit": "2026-11-25T10:00:00+00:00",
                "basis": "aaa",
                "ziel": "bbb",
                "test": "tests/test_wacklig.py::test_zeit",
                "adapter": "py",
            },
            {
                "zeit": "2026-11-25T12:00:00+00:00",
                "basis": "bbb",
                "ziel": "ccc",
                "test": "tests/test_wacklig.py::test_zeit",
                "adapter": "py",
            },
        ]
        wackler_datei.write_text("\n".join(json.dumps(e) for e in eintraege) + "\n", encoding="utf-8")
        # JUnit-XML für langsamste Tests anlegen
        junit_datei = _schreibe_datei(
            repo / "junit.xml",
            '<testsuite tests="2"><testcase classname="tests.test_a" name="test_langsam" time="4.25" />'
            '<testcase classname="tests.test_a" name="test_schnell" time="0.05" /></testsuite>',
        )

        ergebnis = aufraeumen.aufraeum_runde(
            repo,
            junit_pfade=[junit_datei],
            mutation_aktiv=False,
            schreiben=True,
            ersteller_agent="woechentlicher-agent",
        )

        assert ergebnis.nur_vorschlaege is True
        assert ergebnis.geloeschte_dateien == ()
        assert len(ergebnis.doppelte.vorschlaege) >= 2
        assert len(ergebnis.quarantaene.quarantaene.vorschlaege) == 1
        assert ergebnis.quarantaene.langsamste.eintraege[0].test == "tests.test_a::test_langsam"
        assert ergebnis.mutation is None
        assert (repo / protokoll.ORDNER / protokoll.AUFRAEUMEN_DATEI).is_file()
        assert (repo / protokoll.ORDNER / protokoll.QUARANTAENE_DATEI).is_file()
        zusammenfassung = ergebnis.zusammenfassung()
        assert "Stufe 3" in zusammenfassung
        assert "nur Vorschläge" in zusammenfassung
        assert isinstance(ergebnis.quarantaene, quarantaene.Stufe3Bericht)

    def test_modul_klassen_marker_und_kommentar_schutzliste(self, tmp_path: Path) -> None:
        _schreibe_datei(
            tmp_path / "tests" / "test_modul_schutz.py",
            """import pytest

pytestmark = [pytest.mark.e2e]


def test_modul_ablauf():
    assert 2 * 3 == 6
""",
        )
        _schreibe_datei(
            tmp_path / "tests" / "test_klassen_schutz.py",
            """import pytest


@pytest.mark.regression
class TestKlassenFund:
    def test_klasse_fund(self):
        assert 2 * 3 == 6


# Echter Fund: Vorfall #901 darf nie entfernt werden
def test_kommentar_fund():
    assert 2 * 3 == 6


def test_frei_duplikat():
    assert 2 * 3 == 6
""",
        )

        bericht = aufraeumen.finde_doppelte(tmp_path)
        vorgeschlagen = {v.kandidat for v in bericht.vorschlaege}
        assert vorgeschlagen == {"tests/test_klassen_schutz.py::test_frei_duplikat"}
        assert len(bericht.geschuetzte_treffer) == 2

    def test_freigabe_fail_closed_bei_mangelhaftem_nachweis_und_pfad_ohne_testfall(self, tmp_path: Path) -> None:
        vorschlag = aufraeumen.DoppelteVorschlag(
            vorschlag_id="auf-1234567890ab",
            kandidat="tests/test_normal.py::test_dup",
            abgedeckt_durch="tests/test_normal.py::test_orig",
            art="duplikat",
            datei="tests/test_normal.py",
            zeile=10,
            aehnlichkeit=1.0,
            begruendung="Duplikat",
            geschuetzt=False,
            schutz_kategorien=(),
            schutz_gruende=(),
            aktion=aufraeumen.AKTION_LOESCHEN_VORSCHLAGEN,
            ersteller_agent="agent-1",
            inhalts_hash="abc123",
        )
        mangelhaft = aufraeumen.ZweitprueferNachweis(
            vorschlag_id="auf-falsch",
            test_id="tests/test_anders.py::test_x",
            pruefer_agent="agent-2",
            bestaetigt=True,
            begruendung="   ",
            geprueft_am="kein-datum",
        )
        freigabe = aufraeumen.pruefe_loeschfreigabe(vorschlag, mangelhaft, pruefsumme_pflicht=True)
        assert freigabe.erlaubt is False
        assert len(freigabe.gruende) >= 4

        # Auch ohne AST-TestFall blockt ein geschützter Pfad (z. B. e2e/regression) fail-closed.
        e2e_vorschlag = aufraeumen.DoppelteVorschlag(
            vorschlag_id="auf-e2e",
            kandidat="tests/e2e/test_login.py::test_login",
            abgedeckt_durch="tests/test_other.py::test_a",
            art="duplikat",
            datei="tests/e2e/test_login.py",
            zeile=1,
            aehnlichkeit=1.0,
            begruendung="dup",
            geschuetzt=False,
            schutz_kategorien=(),
            schutz_gruende=(),
            aktion=aufraeumen.AKTION_LOESCHEN_VORSCHLAGEN,
            ersteller_agent="agent-1",
            inhalts_hash="h1",
        )
        ok_nachweis = aufraeumen.ZweitprueferNachweis(
            vorschlag_id="auf-e2e",
            test_id="tests/e2e/test_login.py::test_login",
            pruefer_agent="agent-2",
            bestaetigt=True,
            begruendung="Geprüft",
            geprueft_am="2026-11-26T01:00:00+00:00",
            pruefsumme="h1",
        )
        freigabe_e2e = aufraeumen.pruefe_loeschfreigabe(e2e_vorschlag, ok_nachweis)
        assert freigabe_e2e.erlaubt is False
        assert aufraeumen.SCHUTZ_NUTZER_ABLAUF in freigabe_e2e.schutz.kategorien

    def test_aufraeum_runde_mit_mutation_nicht_linux_bleibt_sauber(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _beispiel_repo(tmp_path)
        monkeypatch.setattr(aufraeumen.mutation, "ist_linux", lambda _p=None: False)

        erg = aufraeumen.aufraeum_runde(
            repo,
            mutation_aktiv=True,
            basis="HEAD",
            ziel="HEAD",
            schreiben=False,
        )
        assert erg.nur_vorschlaege is True
        assert erg.mutation is not None
        assert erg.mutation.unterstuetzt is False
        assert "nicht unterstützt (nur Linux)" in erg.zusammenfassung()

    def test_cli_runde_und_eigenanschluss_pruefen_und_doku(self, tmp_path: Path) -> None:
        eigen = tmp_path / "pipeline-stufe-3-eigen.yml"
        assert aufraeumen.main(["vorlage", "--eigenanschluss", "--ausgabe", str(eigen)]) == 0
        assert aufraeumen.main(["vorlage", "--eigenanschluss", "--pruefen", str(eigen)]) == 0

        repo = _beispiel_repo(tmp_path / "repo")
        summary = tmp_path / "summary.md"
        out_json = tmp_path / "runde.json"
        rc = aufraeumen.main(
            [
                "runde",
                "--repo",
                str(repo),
                "--nur-pruefen",
                "--summary-pfad",
                str(summary),
                "--ausgabe-json",
                str(out_json),
            ]
        )
        assert rc == 0
        assert out_json.is_file()
        assert "Stufe 3" in summary.read_text(encoding="utf-8")

        doku = (Path(__file__).resolve().parents[1] / "docs" / "PIPELINE_VORLAGE.md").read_text(encoding="utf-8")
        assert "pipeline-stufe-3.yml" in doku
        assert "Schutzliste" in doku
        assert "Zweitprüfer" in doku

    def test_cli_vorlage_stufe3_ueber_pipeline_vorlage_und_freigabe_pruefen(self, tmp_path: Path) -> None:
        ziel = tmp_path / "pipeline-stufe-3.yml"
        assert pipeline_vorlage.main(["vorlage", "--stufe", "3", "--ausgabe", str(ziel)]) == 0
        assert ziel.is_file()
        assert pipeline_vorlage.main(["vorlage", "--stufe", "3", "--pruefen", str(ziel)]) == 0

        repo = _beispiel_repo(tmp_path / "repo")
        bericht_json = tmp_path / "bericht.json"
        rc = aufraeumen.main(
            [
                "doppelte",
                "--repo",
                str(repo),
                "--ersteller-agent",
                "agent-eins",
                "--ausgabe-json",
                str(bericht_json),
            ]
        )
        assert rc == 0
        daten = json.loads(bericht_json.read_text(encoding="utf-8"))
        assert daten["nur_vorschlaege"] is True
        vorschlag = daten["vorschlaege"][0]

        nachweis_selbst = tmp_path / "selbst.json"
        nachweis_selbst.write_text(
            json.dumps(
                {
                    "vorschlag_id": vorschlag["vorschlag_id"],
                    "test_id": vorschlag["kandidat"],
                    "pruefer_agent": "agent-eins",
                    "bestaetigt": True,
                    "begruendung": "Selbst-Check",
                    "geprueft_am": "2026-11-26T01:00:00+00:00",
                    "pruefsumme": vorschlag["inhalts_hash"],
                }
            ),
            encoding="utf-8",
        )
        assert (
            aufraeumen.main(
                [
                    "freigabe-pruefen",
                    "--repo",
                    str(repo),
                    "--bericht",
                    str(bericht_json),
                    "--vorschlag-id",
                    vorschlag["vorschlag_id"],
                    "--nachweis",
                    str(nachweis_selbst),
                ]
            )
            == 1
        )

        nachweis_ok = tmp_path / "zweiter.json"
        nachweis_ok.write_text(
            json.dumps(
                {
                    "vorschlag_id": vorschlag["vorschlag_id"],
                    "test_id": vorschlag["kandidat"],
                    "pruefer_agent": "agent-zwei",
                    "bestaetigt": True,
                    "begruendung": "Unabhängig geprüft und bestätigt.",
                    "geprueft_am": "2026-11-26T01:05:00+00:00",
                    "pruefsumme": vorschlag["inhalts_hash"],
                }
            ),
            encoding="utf-8",
        )
        assert (
            aufraeumen.main(
                [
                    "freigabe-pruefen",
                    "--repo",
                    str(repo),
                    "--bericht",
                    str(bericht_json),
                    "--vorschlag-id",
                    vorschlag["vorschlag_id"],
                    "--nachweis",
                    str(nachweis_ok),
                ]
            )
            == 0
        )

    def test_cli_laeuft_auch_unter_windows_cp1252_ausgabe(self, tmp_path: Path) -> None:
        repo = _beispiel_repo(tmp_path)
        lauf = subprocess.run(
            [sys.executable, "-m", "to_spawn.pruef.aufraeumen", "doppelte", "--repo", str(repo), "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        assert lauf.returncode == 0, lauf.stderr
        daten = json.loads(lauf.stdout)
        assert daten["nur_vorschlaege"] is True
        assert daten["geloeschte_dateien"] == []
