"""Weg-Test für ``thermo_lauf.py``: plan + sammeln gegen ein echtes temporäres git-Repo.

Kein Mock für git; GitHub bleibt per ``--ohne-github`` aus.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKRIPT = Path(__file__).resolve().parent.parent / "skripte" / "thermo_lauf.py"
SPEC = 900


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.strip()


def _lauf(repo: Path, befehl: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKRIPT), befehl, str(SPEC), "--repo", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=120,
        check=False,
    )


def _schreib(repo: Path, pfad: str, zeilen: int = 3) -> None:
    datei = repo / pfad
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("".join(f"zeile_{i} = {i}\n" for i in range(zeilen)), encoding="utf-8")


def _commit(repo: Path, nachricht: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", nachricht)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "projekt"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    _schreib(r, "alt.py")
    _commit(r, "chore: Start")
    return r


def _manifest(repo: Path, dateien: list[str]) -> str:
    manifest = {"spec": SPEC, "tickets": {"901": {"title": "Knopf", "files": dateien}, "902": {"title": "Aus"}}}
    pfad = repo / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps(manifest), encoding="utf-8")
    _commit(repo, "docs: Manifest")
    return _git(repo, "rev-parse", "HEAD~1")


def test_plan_teile_basis_filter_und_sperre(repo: Path) -> None:
    basis = _manifest(repo, ["web/knopf.py", "web/knopf.js"])
    _schreib(repo, "web/knopf.py", 10)
    _schreib(repo, "tests/test_knopf.py", 10)
    _schreib(repo, "docs/knopf.md", 10)
    _commit(repo, "feat: Knopf (#901)")
    _schreib(repo, "web/aus.py", 5)
    _schreib(repo, "web/test_aus.py", 5)
    _schreib(repo, "web/aus.spec.ts", 5)
    _commit(repo, "feat: Aus (902)")
    _schreib(repo, "web/fremd.py", 5)
    _commit(repo, "feat: Fremd (#1902)")
    kopf = _git(repo, "rev-parse", "HEAD")

    lauf = _lauf(repo, "plan", "--kopf", "HEAD")
    assert lauf.returncode == 0, lauf.stderr
    daten = json.loads(lauf.stdout)
    assert daten["basis"] == basis and daten["kopf"] == kopf
    assert Path(daten["befunde_ordner"]) == (repo / ".to-spawn" / f"thermo_{SPEC}").resolve()
    alle = [d for t in daten["teile"] for d in t["dateien"]]
    assert sorted(alle) == ["web/aus.py", "web/knopf.py"]
    teil = daten["teile"][0]
    assert teil["nr"] == 1 and teil["diff_zeilen"] == 15
    assert teil["prompt"].startswith("Schätzung: ~60k Token · Lese-Budget:")
    assert f"Prüfer Thermo, Teil 1/1, Spec #{SPEC}" in teil["prompt"]
    assert f"git -C {repo.resolve()} diff {basis}..{kopf} -- web/knopf.py web/aus.py" in teil["prompt"]
    assert (Path(daten["befunde_ordner"]) / "lauf.json").is_file()

    zweiter = _lauf(repo, "plan", "--kopf", "HEAD")
    assert zweiter.returncode == 4
    assert "läuft schon" in zweiter.stdout


def test_plan_aufteilung_bei_vielen_dateien(repo: Path) -> None:
    _manifest(repo, [])
    for i in range(40):
        _schreib(repo, f"web/m{i:02d}.py", 2)
    _schreib(repo, "web/riesig.py", 900)
    _commit(repo, "feat: viel (#901)")
    daten = json.loads(_lauf(repo, "plan", "--kopf", "HEAD").stdout)
    assert len(daten["teile"]) == 4
    assert daten["teile"][0]["dateien"] == ["web/riesig.py"]
    assert all(len(t["dateien"]) <= 8 for t in daten["teile"])
    assert all(t["diff_zeilen"] <= 800 for t in daten["teile"][1:])
    assert len(daten["ausgelassen"]) == 41 - 1 - 3 * 8


def test_plan_ohne_code_diff(repo: Path) -> None:
    _manifest(repo, ["web/knopf.py"])
    _schreib(repo, "docs/nur.md")
    _commit(repo, "docs: nur Doku (#901)")
    lauf = _lauf(repo, "plan", "--kopf", "HEAD")
    assert lauf.returncode == 2
    assert len(lauf.stdout.strip().splitlines()) == 1


def _teile(ordner: Path, *inhalte: str) -> None:
    ordner.mkdir(parents=True, exist_ok=True)
    for nr, inhalt in enumerate(inhalte, 1):
        (ordner / f"teil-{nr}.json").write_text(inhalt, encoding="utf-8")


@pytest.mark.xfail(strict=True, reason="überholt durch E15: kein Sammel-Issue, Marker nennt kein --ohne-github mehr")
def test_sammeln_marker_sortierung_dedup_idempotent(repo: Path) -> None:
    ordner = repo / ".to-spawn" / f"thermo_{SPEC}"
    b1 = {"datei": "web/a.py", "zeile": 10, "schwere": "niedrig", "titel": "Klein", "vorschlag": "später"}
    b2 = {"datei": "web/b.py", "zeile": 5, "schwere": "hoch", "titel": "Riesen-Datei", "vorschlag": "zerlegen"}
    b3 = {"datei": "web/c.py", "zeile": 7, "schwere": "mittel", "titel": "Sonder-ifs", "vorschlag": "Tabelle"}
    _teile(
        ordner,
        json.dumps({"teil": 1, "befunde": [b1, b2]}),
        json.dumps({"teil": 2, "befunde": [b3, b2]}),
        "{kaputt",
    )
    (ordner / "lauf.json").write_text(json.dumps({"start": "2026-09-27T10:00:00+02:00"}), encoding="utf-8")
    lauf = _lauf(repo, "sammeln", "--ohne-github")
    assert lauf.returncode == 0, lauf.stderr
    marker = (repo / "docs" / "agents" / f"thermo_{SPEC}.md").read_text(encoding="utf-8")
    assert marker.count("Riesen-Datei") == 1
    assert marker.index("Riesen-Datei") < marker.index("Sonder-ifs") < marker.index("Klein")
    assert "teil-3.json" in marker and "kaputt" in marker
    assert "--ohne-github" in marker
    assert not (ordner / "lauf.json").exists()
    assert _lauf(repo, "sammeln", "--ohne-github").returncode == 0


def test_sammeln_idempotent_mit_issue_url(repo: Path) -> None:
    marker = repo / "docs" / "agents" / f"thermo_{SPEC}.md"
    marker.parent.mkdir(parents=True)
    url = "https://github.com/x/y/issues/77"
    marker.write_text(f"# Thermo\n\nIssue: {url}\n", encoding="utf-8")
    lauf = _lauf(repo, "sammeln")
    assert lauf.returncode == 0 and url in lauf.stdout
    assert marker.read_text(encoding="utf-8") == f"# Thermo\n\nIssue: {url}\n"
    assert _lauf(repo, "plan").returncode == 4


def test_sammeln_teil_fehlt_kein_marker_exit_3(repo: Path) -> None:
    _manifest(repo, ["web/knopf.py", "web/aus.py"])
    _schreib(repo, "web/knopf.py", 900)
    _schreib(repo, "web/aus.py", 900)
    _commit(repo, "feat: Knopf (#901)")
    lauf = _lauf(repo, "plan", "--kopf", "HEAD")
    assert lauf.returncode == 0, lauf.stderr
    daten = json.loads(lauf.stdout)
    ordner = Path(daten["befunde_ordner"])
    assert len(daten["teile"]) == 2
    b = {"datei": "web/knopf.py", "zeile": 1, "schwere": "niedrig", "titel": "Klein", "vorschlag": "egal"}
    (ordner / "teil-1.json").write_text(json.dumps({"teil": 1, "befunde": [b]}), encoding="utf-8")

    ergebnis = _lauf(repo, "sammeln", "--ohne-github")
    assert ergebnis.returncode == 3
    assert "Teil 2 fehlt" in ergebnis.stdout
    assert not (repo / "docs" / "agents" / f"thermo_{SPEC}.md").exists()
    assert (ordner / "lauf.json").exists()


def test_plan_neustart_raeumt_alte_teile_weg(repo: Path) -> None:
    """Alte ``teil-*.json`` eines abgebrochenen Laufs dürfen im neuen Lauf nicht als vorhanden zählen."""
    _manifest(repo, ["web/knopf.py", "web/aus.py"])
    _schreib(repo, "web/knopf.py", 900)
    _schreib(repo, "web/aus.py", 900)
    _commit(repo, "feat: Knopf (#901)")
    ordner = repo / ".to-spawn" / f"thermo_{SPEC}"
    ordner.mkdir(parents=True)
    alt = {"datei": "web/alt.py", "zeile": 1, "schwere": "hoch", "titel": "Veraltet", "vorschlag": "weg"}
    for nr in (1, 2, 7):
        (ordner / f"teil-{nr}.json").write_text(json.dumps({"teil": nr, "befunde": [alt]}), encoding="utf-8")

    assert _lauf(repo, "plan", "--kopf", "HEAD").returncode == 0
    assert not list(ordner.glob("teil-*.json"))
    ergebnis = _lauf(repo, "sammeln", "--ohne-github")
    assert ergebnis.returncode == 3
    assert not (repo / "docs" / "agents" / f"thermo_{SPEC}.md").exists()


def test_sammeln_ohne_lauf_json_keine_erwartung_geht_durch(repo: Path) -> None:
    """``teile``-Angabe fehlt — es gibt nichts zum Abgleichen, kein Fehlschlag."""
    ordner = repo / ".to-spawn" / f"thermo_{SPEC}"
    b = {"datei": "web/a.py", "zeile": 1, "schwere": "niedrig", "titel": "Klein", "vorschlag": "egal"}
    _teile(ordner, json.dumps({"teil": 1, "befunde": [b]}))
    ergebnis = _lauf(repo, "sammeln", "--ohne-github")
    assert ergebnis.returncode == 0
    assert (repo / "docs" / "agents" / f"thermo_{SPEC}.md").exists()


@pytest.mark.xfail(strict=True, reason="überholt durch E15: kein Sammel-Issue, Marker spricht nicht mehr von Issues")
def test_sammeln_nur_niedrig_kein_issue(repo: Path) -> None:
    ordner = repo / ".to-spawn" / f"thermo_{SPEC}"
    b = {"datei": "web/a.py", "zeile": 1, "schwere": "niedrig", "titel": "Klein", "vorschlag": "egal"}
    _teile(ordner, json.dumps({"teil": 1, "befunde": [b]}))
    lauf = _lauf(repo, "sammeln", "--ohne-github")
    assert lauf.returncode == 0
    marker = (repo / "docs" / "agents" / f"thermo_{SPEC}.md").read_text(encoding="utf-8")
    assert "kein Issue" in marker


# --- E15 (#582): ticket-übergreifende Prüfung, kein Sammel-Issue ---------------------------------


def _schreib_code(repo: Path, pfad: str, namen: list[str], fuell: int = 900) -> None:
    datei = repo / pfad
    datei.parent.mkdir(parents=True, exist_ok=True)
    kopf = "".join(f"def {n}(x):\n    return x\n\n" for n in namen)
    datei.write_text(kopf + "".join(f"zeile_{i} = {i}\n" for i in range(fuell)), encoding="utf-8")


def test_plan_jeder_teil_kennt_namen_der_ganzen_spec(repo: Path) -> None:
    """Jeder Prüfer sieht die neuen Namen aller Teile und prüft Dopplung/Namen/Schnittstellen (E15)."""
    _schreib_code(repo, "web/knopf.py", ["alter_name"], fuell=0)
    _commit(repo, "chore: alter Stand vor der Spec")
    _manifest(repo, ["web/knopf.py", "web/aus.py"])
    _schreib_code(repo, "web/knopf.py", ["alter_name", "knopf_farbe", "lade_knopf"])
    _schreib_code(repo, "web/aus.py", ["aus_farbe", "lade_aus"])
    _commit(repo, "feat: Knopf (#901)")
    lauf = _lauf(repo, "plan", "--kopf", "HEAD")
    assert lauf.returncode == 0, lauf.stderr
    teile = json.loads(lauf.stdout)["teile"]
    assert len(teile) == 2
    for teil in teile:
        text = teil["prompt"]
        for name in ("knopf_farbe", "lade_knopf", "aus_farbe", "lade_aus"):
            assert name in text, (teil["nr"], name)
        assert "web/knopf.py" in text and "web/aus.py" in text
        assert "doppelte Helfer" in text
        assert "zwei Namen für dasselbe Ding" in text
        assert "auseinanderlaufende Schnittstellen" in text
        assert "übrigen Code" in text
    # Namen, die es vor der Spec schon gab, gehören nicht in die Liste.
    assert "alter_name" not in teile[0]["prompt"].split("Neue Namen")[1]


def _gh_attrappe(tmp_path: Path) -> tuple[dict[str, str], Path]:
    """Falsches ``gh`` vorn im PATH: protokolliert jeden Aufruf, damit der Test ihn sieht."""
    ordner = tmp_path / "bin"
    ordner.mkdir()
    spur = tmp_path / "gh_aufrufe.txt"
    gh = ordner / "gh"
    gh.write_text(f'#!/bin/sh\necho "$@" >> "{spur}"\necho https://github.com/x/y/issues/1\n', encoding="utf-8")
    gh.chmod(0o755)
    return {**os.environ, "PATH": f"{ordner}{os.pathsep}{os.environ.get('PATH', '')}"}, spur


@pytest.mark.skipif(sys.platform == "win32", reason="gh-Attrappe ist ein sh-Skript")
def test_sammeln_hoch_legt_kein_issue_an_marker_traegt_befunde(repo: Path, tmp_path: Path) -> None:
    ordner = repo / ".to-spawn" / f"thermo_{SPEC}"
    b1 = {"datei": "web/b.py", "zeile": 5, "schwere": "hoch", "titel": "Riesen-Datei", "vorschlag": "zerlegen"}
    b2 = {"datei": "web/c.py", "zeile": 7, "schwere": "mittel", "titel": "Zwei Namen", "vorschlag": "einen nehmen"}
    _teile(ordner, json.dumps({"teil": 1, "befunde": [b1, b2]}))
    env, spur = _gh_attrappe(tmp_path)
    lauf = subprocess.run(
        [sys.executable, str(SKRIPT), "sammeln", str(SPEC), "--repo", str(repo)],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=120, check=False,
    )
    assert lauf.returncode == 0, lauf.stderr
    assert not spur.exists(), spur.read_text(encoding="utf-8")
    marker = (repo / "docs" / "agents" / f"thermo_{SPEC}.md").read_text(encoding="utf-8")
    assert "| Datei:Zeile | Schwere | Titel | Vorschlag |" in marker
    assert "Riesen-Datei" in marker and "Zwei Namen" in marker
    assert "https://" not in marker
    assert not (ordner / "issue_body.md").exists()


def test_sammeln_sortierung_dedup_ohne_issue(repo: Path) -> None:
    """Nachfolger des überholten Sortier-Tests ohne Issue-Annahme (E15/E17)."""
    ordner = repo / ".to-spawn" / f"thermo_{SPEC}"
    b1 = {"datei": "web/a.py", "zeile": 10, "schwere": "niedrig", "titel": "Klein", "vorschlag": "später"}
    b2 = {"datei": "web/b.py", "zeile": 5, "schwere": "hoch", "titel": "Riesen-Datei", "vorschlag": "zerlegen"}
    b3 = {"datei": "web/c.py", "zeile": 7, "schwere": "mittel", "titel": "Sonder-ifs", "vorschlag": "Tabelle"}
    _teile(ordner, json.dumps({"teil": 1, "befunde": [b1, b2]}), json.dumps({"teil": 2, "befunde": [b3, b2]}), "{kaputt")
    (ordner / "lauf.json").write_text(json.dumps({"start": "2026-09-27T10:00:00+02:00"}), encoding="utf-8")
    assert _lauf(repo, "sammeln").returncode == 0
    marker = (repo / "docs" / "agents" / f"thermo_{SPEC}.md").read_text(encoding="utf-8")
    assert marker.count("Riesen-Datei") == 1
    assert marker.index("Riesen-Datei") < marker.index("Sonder-ifs") < marker.index("Klein")
    assert "teil-3.json" in marker and "kaputt" in marker
    assert "Issue" not in marker
    assert not (ordner / "lauf.json").exists()
    assert _lauf(repo, "sammeln").returncode == 0


def test_plan_namen_auch_bei_leerzeichen_und_umlaut_im_pfad(repo: Path) -> None:
    """Gequotete Pfade (Umlaut) und Tab-Suffix (Leerzeichen) dürfen keine Namen verschlucken."""
    _manifest(repo, ["web/grüße.py", "web/zwei teile.py"])
    _schreib_code(repo, "web/grüße.py", ["gruss_helfer"], fuell=5)
    _schreib_code(repo, "web/zwei teile.py", ["teil_helfer"], fuell=5)
    _commit(repo, "feat: Pfade (#901)")
    lauf = _lauf(repo, "plan", "--kopf", "HEAD")
    assert lauf.returncode == 0, lauf.stderr
    text = json.loads(lauf.stdout)["teile"][0]["prompt"]
    assert "- web/grüße.py: gruss_helfer" in text
    assert "- web/zwei teile.py: teil_helfer" in text


def test_plan_geaenderte_signatur_ist_kein_neuer_name(repo: Path) -> None:
    """Steht ein Name auch in einer ``-``-Zeile (Signatur geändert), ist er nicht neu (Review #582)."""
    datei = repo / "web" / "sig.py"
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("def bleibt(a):\n    return a\n", encoding="utf-8")
    _commit(repo, "chore: alte Signatur")
    _manifest(repo, ["web/sig.py"])
    datei.write_text("def bleibt(a, b):\n    return a\n\n\ndef ganz_neu():\n    return 1\n", encoding="utf-8")
    _commit(repo, "feat: Signatur (#901)")
    lauf = _lauf(repo, "plan", "--kopf", "HEAD")
    assert lauf.returncode == 0, lauf.stderr
    text = json.loads(lauf.stdout)["teile"][0]["prompt"]
    assert "- web/sig.py: ganz_neu" in text
    assert "bleibt" not in text.split("Neue Namen")[1]
