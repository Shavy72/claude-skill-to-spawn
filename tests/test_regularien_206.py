"""Weg-Tests für die Regularien-Prüfung und den Lernstoff (duoplus-management#206).

Echt laufen: CLI, Manifeste, Bau-Log-Dateien, Git-Repo mit Bare-Remote. Gestellt
ist nur die ``gh``-CLI (GitHub ist ein externer Dienst, per ``TO_SPAWN_GH_STUB``)
und im Spawn-Weg-Test ``pwsh`` (Terminal-Programm, schreibt nur eine Marker-Datei).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
GH_STUB = Path(__file__).resolve().parent / "hilfen" / "gh_stub.py"

SPEC = 900
TICKETS = ("901", "902")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def _manifest_ordner(repo: Path) -> Path:
    ordner = repo / "docs" / "agents" / "manifests"
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner


def _manifest_schreiben(repo: Path, eintraege: dict[str, dict], spec: int = SPEC) -> None:
    (_manifest_ordner(repo) / f"spec-{spec}.json").write_text(
        json.dumps({"spec": spec, "feature": "wegwerf", "tickets": eintraege}, ensure_ascii=False),
        encoding="utf-8",
    )


def _voll(titel: str = "Wegwerf-Ticket", schaetzung: float = 120) -> dict:
    return {"title": titel, "schaetzung_k": schaetzung, "umfang": "Kern bauen, Test schreiben."}


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Wegwerf-Repo mit Bare-Remote, vollständigem Manifest und gestelltem gh."""
    fern = tmp_path / "fern.git"
    subprocess.run(["git", "init", "--bare", str(fern)], check=True, capture_output=True)
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    _git(arbeit, "init")
    _git(arbeit, "remote", "add", "origin", str(fern))
    _manifest_schreiben(arbeit, {t: _voll(f"Wegwerf-Ticket {t}") for t in TICKETS})
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(GH_STUB))
    monkeypatch.setenv("GH_STUB_BLOCKER", f"{TICKETS[1]}:{TICKETS[0]}")
    for name in ("GH_STUB_ZU", "GH_STUB_DATEN", "TO_SPAWN_TICKET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(arbeit)
    return arbeit


def _gh_daten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, daten: dict[str, dict]) -> None:
    datei = tmp_path / "gh_daten.json"
    datei.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GH_STUB_DATEN", str(datei))


def _cli(
    repo: Path, *args: str, zusatz_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, **(zusatz_env or {})},
        check=False,
    )


def _ausgabe(ergebnis: subprocess.CompletedProcess[str]) -> str:
    return ergebnis.stdout + ergebnis.stderr


# --- Pflichtfelder und Grenze ------------------------------------------------


def test_fehlendes_umfang_feld_weigert_sich(repo: Path) -> None:
    _manifest_schreiben(repo, {TICKETS[0]: {"title": "ohne Umfang", "schaetzung_k": 100}})
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "umfang" in ergebnis.stdout
    assert "WEIGERUNG — nichts gestartet." in ergebnis.stdout
    assert "Erst /to-tickets" in ergebnis.stdout


def test_schaetzung_genau_200_ist_weigerung(repo: Path) -> None:
    """Die Spec verlangt „unter 200k“ — 200 genau reicht nicht."""
    _manifest_schreiben(repo, {TICKETS[0]: _voll(schaetzung=200)})
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "in Teil-Tickets je unter 200k schneiden (/to-tickets)" in ergebnis.stdout


def test_schaetzung_199_geht_durch(repo: Path) -> None:
    _manifest_schreiben(repo, {TICKETS[0]: _voll(schaetzung=199)})
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "FEHLER" not in ergebnis.stdout


@pytest.mark.parametrize("wert", [0, -5, True])
def test_schaetzung_muss_positive_zahl_sein(repo: Path, wert: object) -> None:
    _manifest_schreiben(repo, {TICKETS[0]: {"title": "x", "schaetzung_k": wert, "umfang": "y"}})
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "schaetzung_k" in ergebnis.stdout


def test_doppelter_ticket_schluessel_weigert_sich(repo: Path) -> None:
    """``json.loads`` würde den ersten Eintrag still verschlucken."""
    eintrag = json.dumps(_voll(), ensure_ascii=False)
    text = (
        f'{{"spec": {SPEC}, "tickets": {{"901": {eintrag}, "902": {eintrag}, "901": {eintrag}}}}}'
    )
    (_manifest_ordner(repo) / f"spec-{SPEC}.json").write_text(text, encoding="utf-8")
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "#901 steht zweimal im Manifest (doppelt belegt)" in ergebnis.stdout
    assert f"spec-{SPEC}.json zusammenführen" in ergebnis.stdout


# --- Ticket in einem zweiten Manifest ---------------------------------------


def test_ticket_offen_in_zweitem_manifest_weigert_sich(repo: Path) -> None:
    _manifest_schreiben(repo, {TICKETS[0]: _voll()}, spec=800)
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "#901 steht auch in spec-800.json — doppelt belegt" in ergebnis.stdout
    assert "Aus einem Manifest streichen." in ergebnis.stdout


def test_ticket_zu_in_zweitem_manifest_ist_egal(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _manifest_schreiben(repo, {TICKETS[0]: _voll()}, spec=800)
    monkeypatch.setenv("GH_STUB_ZU", TICKETS[0])
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "spec-800.json" not in ergebnis.stdout


def test_fremde_dateinamen_und_kaputtes_manifest(repo: Path) -> None:
    """Nur ``spec-*.json`` zählt; ein unlesbares Manifest ist eine Warnung."""
    ordner = _manifest_ordner(repo)
    (ordner / "vorlage-900.json").write_text(
        json.dumps({"tickets": {TICKETS[0]: _voll()}}), encoding="utf-8"
    )
    (ordner / "spec-801.json").write_text("{ kaputt", encoding="utf-8")
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "vorlage-900.json" not in ergebnis.stdout
    assert "Warnung" in ergebnis.stdout and "spec-801.json" in ergebnis.stdout


# --- GitHub-Teil -------------------------------------------------------------


def test_body_verweis_ohne_native_kante_weigert_sich(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_STUB_BLOCKER", "")
    _gh_daten(
        tmp_path,
        monkeypatch,
        {
            TICKETS[1]: {
                "labels": ["checkpoint:human"],
                "body": "Text\n\n## Blocked by\n\n- #901\n\n## Hinweise\n\nsiehe #999\n",
            }
        },
    )
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert (
        "#902: laut Ticket-Text blockiert von #901, aber die native Kante fehlt" in ergebnis.stdout
    )
    assert "gh api -X POST repos/" in ergebnis.stdout
    assert "/issues/902/dependencies/blocked_by -F issue_id=$(gh api repos/" in ergebnis.stdout
    assert "/issues/901 --jq .id)" in ergebnis.stdout
    assert "#999" not in ergebnis.stdout


def test_body_verweis_mit_kante_geht_durch(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gh_daten(
        tmp_path,
        monkeypatch,
        {TICKETS[1]: {"labels": ["checkpoint:human"], "body": "## Blocked by\n- #901\n"}},
    )
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)


def test_entwurfs_nummer_im_body_ist_warnung(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_STUB_ZU", "3")
    _gh_daten(
        tmp_path,
        monkeypatch,
        {TICKETS[1]: {"labels": ["checkpoint:human"], "body": "## Blocked by\n- #3\n- #901\n"}},
    )
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "#902: Blocker-Verweis #3 ist kein Ticket dieser Spec" in ergebnis.stdout


def test_geschlossenes_ticket_ueberspringt_body_pruefung(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_STUB_BLOCKER", "")
    monkeypatch.setenv("GH_STUB_ZU", TICKETS[1])
    _gh_daten(
        tmp_path,
        monkeypatch,
        {
            TICKETS[0]: {"labels": ["checkpoint:human"]},
            TICKETS[1]: {"labels": [], "body": "## Blocked by\n- #901\n"},
        },
    )
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "native Kante fehlt" not in ergebnis.stdout


def test_fehlendes_checkpoint_label_weigert_sich(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gh_daten(tmp_path, monkeypatch, {t: {"labels": ["enhancement"]} for t in TICKETS})
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert (
        "Kein Checkpoint-Ticket: keins der Tickets trägt das Label checkpoint:human"
        in ergebnis.stdout
    )


@pytest.mark.parametrize("zu", ["", f"{TICKETS[0]},{TICKETS[1]}"])
def test_vorhandenes_checkpoint_label_geht_durch(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, zu: str
) -> None:
    """Das Label zählt auch an einem schon geschlossenen Ticket (alle Tickets zu)."""
    monkeypatch.setenv("GH_STUB_ZU", zu)
    _gh_daten(
        tmp_path,
        monkeypatch,
        {TICKETS[0]: {"labels": ["enhancement"]}, TICKETS[1]: {"labels": ["checkpoint:human"]}},
    )
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "Checkpoint" not in ergebnis.stdout


def test_ohne_labels_in_antwort_nur_warnung(repo: Path) -> None:
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "Checkpoint ungeprüft (GitHub-Antwort ohne Labels)" in ergebnis.stdout


def test_belegtes_ticket_ist_nur_warnung(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_STUB_BLOCKER", f"{TICKETS[0]}:{TICKETS[1]}")
    _gh_daten(
        tmp_path,
        monkeypatch,
        {TICKETS[0]: {"labels": ["checkpoint:human"], "assignees": ["Shavy72"]}},
    )
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "#901 ist schon belegt (Assignee Shavy72)" in ergebnis.stdout
    assert f"sessions {SPEC}" in ergebnis.stdout


def test_github_abfrage_scheitert_weigert_sich(repo: Path, tmp_path: Path) -> None:
    """Fail-closed: kaputtes gh ist ein Fehler, keine Warnung mehr."""
    kaputt = {"TO_SPAWN_GH_STUB": str(tmp_path / "gibt_es_nicht.py")}
    ergebnis = _cli(repo, "pruefen", str(SPEC), zusatz_env=kaputt)
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "GitHub-Abfrage für #901 fehlgeschlagen" in ergebnis.stdout
    assert "gh auth status" in ergebnis.stdout
    assert f"pruefen {SPEC} --ohne-github" in ergebnis.stdout
    bewusst = _cli(repo, "pruefen", str(SPEC), "--ohne-github", zusatz_env=kaputt)
    assert bewusst.returncode == 0, _ausgabe(bewusst)


# --- Spawn-Weg: Weigerung startet kein Terminal ------------------------------


@pytest.mark.parametrize("probelauf", [True, False])
def test_spawn_weigert_sich_und_startet_kein_pwsh(
    repo: Path, tmp_path: Path, probelauf: bool
) -> None:
    _manifest_schreiben(repo, {TICKETS[0]: {"title": "ohne Umfang", "schaetzung_k": 100}})
    marker = tmp_path / "pwsh_gestartet"
    bin_ordner = tmp_path / "bin"
    bin_ordner.mkdir()
    for name in ("pwsh", "powershell"):
        programm = bin_ordner / name
        programm.write_text(f'#!/bin/sh\necho "$@" >> "{marker}"\n', encoding="utf-8")
        programm.chmod(programm.stat().st_mode | stat.S_IEXEC)
    args = ["spawn", str(SPEC), "--ziel", "srv"] + (["--dry-run"] if probelauf else [])
    ergebnis = _cli(
        repo, *args, zusatz_env={"PATH": f"{bin_ordner}{os.pathsep}{os.environ['PATH']}"}
    )
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "WEIGERUNG" in ergebnis.stdout
    assert not marker.exists()


# --- Lernstoff ---------------------------------------------------------------


def _log(repo: Path, ticket: str, zeilen: list[dict]) -> None:
    ordner = repo / "docs" / "agents" / "bau_log"
    ordner.mkdir(parents=True, exist_ok=True)
    with (ordner / f"{ticket}.jsonl").open("a", encoding="utf-8") as fh:
        for zeile in zeilen:
            fh.write(json.dumps({"ticket": ticket, **zeile}, ensure_ascii=False) + "\n")


def _lauf(
    repo: Path,
    ticket: str,
    ts: str,
    sessions: int,
    ist_gesamt: int,
    auftrag: dict | None = None,
) -> None:
    zeilen: list[dict] = []
    if auftrag is not None:
        zeilen.append({"ts": ts, "typ": "auftrag", **auftrag})
    for staffel in range(1, sessions + 1):
        zeilen.append({"ts": ts, "typ": "session_start", "staffel": staffel})
        zeilen.append(
            {"ts": ts, "typ": "session_ende", "tokens": {"gesamt": ist_gesamt // sessions}}
        )
    _log(repo, ticket, zeilen)


def test_lernstoff_waehlt_nach_zeitstempel_nicht_nach_nummer(repo: Path) -> None:
    _lauf(repo, "5", "2026-09-18T12:00:00+02:00", 1, 50_000, {"schaetzung_k": 40, "umfang": "a"})
    _lauf(repo, "7", "2026-09-17T12:00:00+02:00", 1, 50_000, {"schaetzung_k": 40, "umfang": "b"})
    _lauf(repo, "900", "2026-09-10T12:00:00+02:00", 1, 50_000, {"schaetzung_k": 40, "umfang": "c"})
    ergebnis = _cli(repo, "lernstoff", "--letzte", "2")
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "#5:" in ergebnis.stdout and "#7:" in ergebnis.stdout
    assert "#900:" not in ergebnis.stdout
    assert ergebnis.stdout.index("#5:") < ergebnis.stdout.index("#7:")


def test_lernstoff_nimmt_schaetzung_aus_manifest(repo: Path) -> None:
    _manifest_schreiben(
        repo,
        {"55": {"title": "Knopf bauen", "schaetzung_k": 100, "umfang": "Tabelle anlegen"}},
        spec=50,
    )
    _manifest_schreiben(repo, {"56": {"title": "Nur Titel da", "schaetzung_k": 80}}, spec=51)
    _lauf(repo, "55", "2026-09-18T10:00:00+02:00", 1, 130_000)
    _lauf(repo, "56", "2026-09-18T09:00:00+02:00", 1, 80_000)
    ergebnis = _cli(repo, "lernstoff")
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    zeile55 = next(z for z in ergebnis.stdout.splitlines() if z.startswith("- #55:"))
    assert "100k geschätzt" in zeile55 and "Tabelle anlegen" in zeile55
    assert "Faktor 1,3" in zeile55
    zeile56 = next(z for z in ergebnis.stdout.splitlines() if z.startswith("- #56:"))
    assert "Nur Titel da" in zeile56


def test_lernstoff_faustregeln(repo: Path) -> None:
    umfang_ui = "Datenbank-Spalte ergänzen und Oberfläche anpassen"
    for nummer, sessions in zip(("11", "12", "13", "14", "15"), (2, 2, 2, 2, 1)):
        _lauf(
            repo,
            nummer,
            f"2026-09-18T0{int(nummer) - 10}:00:00+02:00",
            sessions,
            130_000,
            {"schaetzung_k": 100, "umfang": umfang_ui},
        )
    _lauf(
        repo,
        "16",
        "2026-09-18T07:00:00+02:00",
        1,
        130_000,
        {"schaetzung_k": 100, "umfang": "CLI-Skript schreiben"},
    )
    ergebnis = _cli(repo, "lernstoff")
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    text = ergebnis.stdout
    assert "Faustregeln für den Schnitt:" in text
    assert (
        "Schätzungen lagen im Mittel bei Faktor 1,3 — Schätzung × 1,3 muss unter 200k bleiben."
        in text
    )
    assert "Datenbank+Oberfläche brauchte im Mittel 1,8 Sessions (n=5)" in text
    assert "Skript brauchte im Mittel 1,0 Sessions (n=1)" in text
    assert text.index("Datenbank+Oberfläche brauchte") < text.index("Skript brauchte")
    assert "Staffel > 1 bei 4 Tickets" in text
    assert "#11" in text.split("Staffel > 1")[1]


def test_lernstoff_leeres_log(repo: Path) -> None:
    ergebnis = _cli(repo, "lernstoff")
    assert ergebnis.returncode == 0
    assert "Kein Bau-Log vorhanden — noch kein Lernstoff." in ergebnis.stdout


# --- Fixrunde #206: F1 Ticket-Auswahl läuft durch die Prüfung ---------------

_CP_902 = {TICKETS[1]: {"labels": ["checkpoint:human"]}}


def test_f1_pruefen_auswahl_ausserhalb_manifest_weigert_sich(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gh_daten(tmp_path, monkeypatch, _CP_902)
    ergebnis = _cli(repo, "pruefen", str(SPEC), "--tickets", "901,999")
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert (
        f"#999 steht nicht im Manifest spec-{SPEC}.json — erst /to-tickets "
        "(Eintrag mit schaetzung_k + umfang)." in ergebnis.stdout
    )
    assert "#901 steht nicht" not in ergebnis.stdout


def test_f1_pruefen_auswahl_im_manifest_geht_durch(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gh_daten(tmp_path, monkeypatch, _CP_902)
    ergebnis = _cli(repo, "pruefen", str(SPEC), "--tickets", "901, #902")
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)


def test_f1_regeln_laufen_trotz_auswahl_ueber_ganzes_manifest(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _manifest_schreiben(
        repo, {TICKETS[0]: _voll(), TICKETS[1]: {"title": "ohne Umfang", "schaetzung_k": 50}}
    )
    _gh_daten(tmp_path, monkeypatch, _CP_902)
    ergebnis = _cli(repo, "pruefen", str(SPEC), "--tickets", TICKETS[0])
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "#902: Feld umfang (Klartext) fehlt" in ergebnis.stdout


@pytest.mark.parametrize("probelauf", [True, False])
def test_f1_spawn_mit_fremder_auswahl_startet_kein_pwsh(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probelauf: bool
) -> None:
    _gh_daten(tmp_path, monkeypatch, _CP_902)
    marker = tmp_path / "pwsh_gestartet"
    bin_ordner = tmp_path / "bin"
    bin_ordner.mkdir()
    for name in ("pwsh", "powershell"):
        programm = bin_ordner / name
        programm.write_text(f'#!/bin/sh\necho "$@" >> "{marker}"\n', encoding="utf-8")
        programm.chmod(programm.stat().st_mode | stat.S_IEXEC)
    args = ["spawn", str(SPEC), "--ziel", "srv", "--tickets", "999"]
    args += ["--dry-run"] if probelauf else []
    ergebnis = _cli(
        repo, *args, zusatz_env={"PATH": f"{bin_ordner}{os.pathsep}{os.environ['PATH']}"}
    )
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert f"#999 steht nicht im Manifest spec-{SPEC}.json" in ergebnis.stdout
    assert not marker.exists()


# --- Fixrunde #206: F2 Blocker-Verweis außerhalb der Spec --------------------


def test_f2_offener_fremder_verweis_ohne_kante_weigert_sich(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gh_daten(
        tmp_path,
        monkeypatch,
        {TICKETS[1]: {"labels": ["checkpoint:human"], "body": "## Blocked by\n- #901\n- #950\n"}},
    )
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert (
        "#902: laut Ticket-Text blockiert von #950 (offen, nicht in dieser Spec), aber keine "
        "native Kante — Entwurfs-Nummer? Text auf die echte Nummer korrigieren oder Kante "
        "setzen: gh api -X POST repos/" in ergebnis.stdout
    )
    assert "/issues/902/dependencies/blocked_by -F issue_id=$(gh api repos/" in ergebnis.stdout
    assert "/issues/950 --jq .id)" in ergebnis.stdout


def test_f2_geschlossener_fremder_verweis_ist_warnung(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_STUB_ZU", "950")
    _gh_daten(
        tmp_path,
        monkeypatch,
        {TICKETS[1]: {"labels": ["checkpoint:human"], "body": "## Blocked by\n- #901\n- #950\n"}},
    )
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "Warnung #902: Blocker-Verweis #950 ist kein Ticket dieser Spec" in ergebnis.stdout


def test_f2_zustand_nicht_abfragbar_weigert_sich(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed: ohne Zustand des Verweises kein Start."""
    monkeypatch.setenv("GH_STUB_KAPUTT", "950")
    _gh_daten(
        tmp_path,
        monkeypatch,
        {TICKETS[1]: {"labels": ["checkpoint:human"], "body": "## Blocked by\n- #901\n- #950\n"}},
    )
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "GitHub-Abfrage für Blocker-Verweis #950 fehlgeschlagen" in ergebnis.stdout


def test_f2_fremder_verweis_mit_nativer_kante_ist_still(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_STUB_BLOCKER", "902:901,902:950")
    _gh_daten(
        tmp_path,
        monkeypatch,
        {TICKETS[1]: {"labels": ["checkpoint:human"], "body": "## Blocked by\n- #901\n- #950\n"}},
    )
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "#950" not in ergebnis.stdout


def test_f2_zustand_je_nummer_nur_einmal_abgefragt(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protokoll = tmp_path / "gh_aufrufe.txt"
    monkeypatch.setenv("GH_STUB_PROTOKOLL", str(protokoll))
    monkeypatch.setenv("GH_STUB_ZU", "950")
    _gh_daten(
        tmp_path,
        monkeypatch,
        {
            TICKETS[0]: {"body": "## Blocked by\n- #950\n"},
            TICKETS[1]: {"labels": ["checkpoint:human"], "body": "## Blocked by\n- #901\n- #950\n"},
        },
    )
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    aufrufe = protokoll.read_text(encoding="utf-8").splitlines()
    assert sum(1 for z in aufrufe if z.startswith("issue view 950 ")) == 1, aufrufe


# --- Fixrunde #206: F3 Abschnitt „Blocked by“ in allen Schreibweisen ---------
# Regel: Überschrift (#, ##, ### …, Doppelpunkt optional) → bis zur nächsten
# Überschrift. Fett-Variante (**Blocked by:**) → Rest der Zeile; ist der leer,
# die folgenden Zeilen bis zur nächsten Leerzeile oder Überschrift.


@pytest.mark.parametrize(
    "body",
    [
        "### Blocked by\n- #901\n",
        "## Blocked by:\n- #901\n",
        "# blocked by\n\n- #901\n",
        "**Blocked by:** #901\n",
        "- **blocked by** #901 (Grundlage)\n",
        "**Blocked by:**\n- #901\n",
        "## BLOCKED BY\n- https://github.com/Shavy72/duoplus-management/issues/901\n",
    ],
)
def test_f3_schreibweisen_werden_erkannt(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    monkeypatch.setenv("GH_STUB_BLOCKER", "")
    _gh_daten(tmp_path, monkeypatch, {TICKETS[1]: {"labels": ["checkpoint:human"], "body": body}})
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "#902: laut Ticket-Text blockiert von #901, aber die native Kante fehlt" in (
        ergebnis.stdout
    )


@pytest.mark.parametrize(
    "body",
    [
        "## Blocked by\n- #901\n### Weiteres\n- #950\n",
        "**Blocked by:** #901\nDanach #950\n",
        "**Blocked by:**\n- #901\n\nSpäter #950\n",
    ],
)
def test_f3_abschnitt_endet_an_klarer_grenze(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    _gh_daten(tmp_path, monkeypatch, {TICKETS[1]: {"labels": ["checkpoint:human"], "body": body}})
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "#950" not in ergebnis.stdout


@pytest.mark.parametrize("leer", ["None", "Keine", "-", "keine."])
def test_f3_kein_verweis_woerter(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, leer: str
) -> None:
    body = f"## Blocked by\n{leer}\n\n**Blocked by:** {leer}\n"
    _gh_daten(tmp_path, monkeypatch, {TICKETS[1]: {"labels": ["checkpoint:human"], "body": body}})
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "Blocker-Verweis" not in ergebnis.stdout


# --- Fixrunde #206: F4 Checkpoint muss offen sein und warten -----------------


def test_f4_geschlossener_checkpoint_bei_offenen_tickets_weigert_sich(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_STUB_ZU", TICKETS[1])
    _gh_daten(tmp_path, monkeypatch, _CP_902)
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert (
        "Checkpoint-Ticket #902 ist schon zu, aber #901 ist offen — neues Abnahme-Ticket "
        "anlegen." in ergebnis.stdout
    )


def test_f4_offener_checkpoint_ohne_kante_weigert_sich(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gh_daten(tmp_path, monkeypatch, {TICKETS[0]: {"labels": ["checkpoint:human"]}})
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert (
        "Checkpoint-Ticket #901 hat keine native Kante — es startet sofort statt nach den "
        "anderen; Kanten auf die übrigen Tickets setzen." in ergebnis.stdout
    )


def test_f4_checkpoint_allein_offen_geht_durch(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alle anderen Tickets zu: der offene Checkpoint braucht keine Kante mehr."""
    monkeypatch.setenv("GH_STUB_BLOCKER", "")
    monkeypatch.setenv("GH_STUB_ZU", TICKETS[0])
    _gh_daten(tmp_path, monkeypatch, _CP_902)
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)


# --- Fixrunde #206: F5 zweite Manifeste robust -------------------------------


def test_f5_nicht_numerischer_schluessel_stuerzt_nicht(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    eintraege = {t: _voll() for t in TICKETS}
    eintraege["neu"] = _voll()
    _manifest_schreiben(repo, eintraege)
    _manifest_schreiben(repo, {"neu": _voll()}, spec=800)
    _gh_daten(tmp_path, monkeypatch, _CP_902)
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "Traceback" not in ergebnis.stderr
    assert "#neu steht auch in spec-800.json" in ergebnis.stdout


def test_f5_ticket_manifest_mit_namen_zaehlt_mit(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (_manifest_ordner(repo) / "spec-800-ticket-901.json").write_text(
        json.dumps({"tickets": {TICKETS[0]: _voll()}}), encoding="utf-8"
    )
    _gh_daten(tmp_path, monkeypatch, _CP_902)
    ergebnis = _cli(repo, "pruefen", str(SPEC))
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "#901 steht auch in spec-800-ticket-901.json — doppelt belegt" in ergebnis.stdout


def test_f5_eigene_datei_mit_namen_zaehlt_nicht_doppelt(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (_manifest_ordner(repo) / f"spec-{SPEC}.json").unlink()
    (_manifest_ordner(repo) / "spec-149-ticket-179.json").write_text(
        json.dumps({"tickets": {TICKETS[0]: _voll(), TICKETS[1]: _voll()}}), encoding="utf-8"
    )
    _gh_daten(tmp_path, monkeypatch, _CP_902)
    ergebnis = _cli(repo, "pruefen", "149-ticket-179")
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "steht auch in" not in ergebnis.stdout


def test_f5_ohne_github_bleibt_zweites_manifest_fehler(repo: Path) -> None:
    _manifest_schreiben(repo, {TICKETS[0]: _voll()}, spec=800)
    ergebnis = _cli(repo, "pruefen", str(SPEC), "--ohne-github")
    assert ergebnis.returncode == 3, _ausgabe(ergebnis)
    assert "#901 steht auch in spec-800.json" in ergebnis.stdout
    assert "(Zustand ohne GitHub unbekannt)" in ergebnis.stdout


# --- Fixrunde #206: F6 Hilfetext ---------------------------------------------


def test_f6_hilfetext_ohne_github(repo: Path) -> None:
    ergebnis = _cli(repo, "pruefen", "--help", zusatz_env={"COLUMNS": "200"})
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "GitHub-Teil auslassen (Kanten, Checkpoint, Belegung, Zustände)" in ergebnis.stdout
    assert "--tickets" in ergebnis.stdout


# --- Fixrunde #206: F7 Umfang-Arten nach Wortstamm, bool keine Schätzung ------


def _bau_log_modul():  # Modul-Import zur Laufzeit (Skill-Ordner in sys.path)
    import importlib

    if str(SKILL) not in sys.path:
        sys.path.insert(0, str(SKILL))
    return importlib.import_module("to_spawn.bau_log")


@pytest.mark.parametrize(
    ("text", "art"),
    [
        ("Skripte und Tests", "Skript+Test"),
        ("Hooks und Tabellen", "Datenbank+Hook"),
        ("Neue Testfälle schreiben", "Test"),
        ("UI anpassen", "Oberfläche"),
        ("Guide lesen", "Sonstiges"),
    ],
)
def test_f7_umfang_art_nach_wortstamm(text: str, art: str) -> None:
    assert _bau_log_modul().umfang_art(text) == art


def test_f7_bool_zaehlt_nie_als_schaetzung(repo: Path) -> None:
    _lauf(repo, "21", "2026-09-18T10:00:00+02:00", 1, 90_000, {"schaetzung_k": True, "umfang": "x"})
    ergebnis = _cli(repo, "lernstoff")
    assert ergebnis.returncode == 0, _ausgabe(ergebnis)
    assert "ohne Schätzung" in ergebnis.stdout
    assert "Schätzungen lagen im Mittel" not in ergebnis.stdout
    zeile = _bau_log_modul().tabelle(repo, ["21"]).splitlines()[-1]
    assert zeile.split()[1] == "—", zeile
