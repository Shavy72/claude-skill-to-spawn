"""#216 — ``einrichten --pruefen`` ohne Widerspruch (drei Zustände) und ``STAND.json``-Klon.

Belegt beide Punkte aus Issue #216:
1. ``einrichten --pruefen`` nutzt genau drei Zustände (``da_aktuell`` · ``weicht_ab`` ·
   ``nicht_pruefbar``) aus einer Stelle; ein vollständig eingerichtetes Repo ergibt Exit 0
   ohne „nicht eingerichtet“ (auch ohne oder mit älterer ``stand.json``), eine echte
   Abweichung meldet genau diesen Punkt mit Exit 3.
2. ``STAND.json`` Feld ``klon`` zeigt bei Installation aus einem Worktree oder geteilten
   Klon auf den dauerhaften Haupt-Klon und entfällt bei Wegwerf-Ordnern; ``vergleich``
   nutzt keinen entfernten Klon-Pfad.
"""

from __future__ import annotations

import dataclasses
import io
import json
import subprocess
from pathlib import Path

import pytest

from to_spawn import config, einrichten, skill_stand


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=ort,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _commit(ort: Path, nachricht: str) -> str:
    _git(ort, "add", "-A")
    _git(ort, "commit", "-q", "-m", nachricht)
    return _git(ort, "rev-parse", "HEAD")


def _zeilen(text: str) -> dict[str, tuple[str, str]]:
    """Parst eingerückte ``  <name>: <zustand> — <text>``-Zeilen aus ``einrichten``."""
    ergebnis: dict[str, tuple[str, str]] = {}
    for zeile in text.splitlines():
        if not zeile.startswith("  ") or ": " not in zeile:
            continue
        name, rest = zeile.strip().split(": ", 1)
        if " — " in rest:
            zustand, beschreibung = rest.split(" — ", 1)
            ergebnis[name] = (zustand, beschreibung)
    return ergebnis


def _abweichungen(text: str) -> list[str]:
    for zeile in text.splitlines():
        if zeile.startswith("Abweichung: "):
            return [teil.strip() for teil in zeile.removeprefix("Abweichung: ").split(",")]
    return []


@pytest.fixture
def probe_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Echtes eingerichtetes Probe-Repo mit GitHub-origin und lokalem gh-Stub."""
    ort = tmp_path / "probe-app"
    ort.mkdir()
    _git(ort, "init", "-q", "-b", "main")
    _git(ort, "remote", "add", "origin", "https://github.com/test-org/probe-app.git")
    (ort / "README.md").write_text("probe\n", encoding="utf-8")
    (ort / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    _commit(ort, "start")
    stub = tmp_path / "gh_stub.py"
    stub.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    assert einrichten.einrichten(ort, ausgabe=io.StringIO()) == 0
    return ort


def _pruefen(repo: Path) -> tuple[int, str]:
    ausgabe = io.StringIO()
    code = einrichten.einrichten(repo, pruefen=True, ausgabe=ausgabe)
    return code, ausgabe.getvalue()


def _skill_repo(tmp: Path) -> tuple[Path, Path, str]:
    """Dauerhafter Skill-Klon mit lokalem Bare-Repo als ``origin`` (main)."""
    bare = tmp / "skill-origin.git"
    haupt = tmp / "skill-haupt"
    _git(tmp, "init", "-q", "--bare", "-b", "main", str(bare))
    haupt.mkdir()
    _git(haupt, "init", "-q", "-b", "main")
    (haupt / "SKILL.md").write_text("skill\n", encoding="utf-8")
    sha = _commit(haupt, "start")
    _git(haupt, "remote", "add", "origin", str(bare))
    _git(haupt, "push", "-q", "origin", "main")
    return haupt, bare, sha


# --- Teil 1: einrichten --pruefen (drei Zustände aus einer Stelle) ---------------------------


def test_drei_zustaende_aus_einer_stelle(probe_repo: Path) -> None:
    """Genau drei Prüfzustände; jeder Baustein liefert Zustand und Text aus derselben Stelle."""
    assert set(einrichten.PRUEF_ZUSTAENDE) == {
        einrichten.PRUEF_OK,
        einrichten.PRUEF_ABWEICHUNG,
        einrichten.PRUEF_UNPRUEFBAR,
    }
    assert len(einrichten.PRUEF_ZUSTAENDE) == 3
    ort = einrichten.Ort(probe_repo, io.StringIO())
    for baustein in einrichten._BAUSTEINE:
        befund = einrichten._pruefe_baustein(baustein, ort, None)
        assert befund.zustand in einrichten.PRUEF_ZUSTAENDE
        assert befund.weicht_ab is (befund.zustand == einrichten.PRUEF_ABWEICHUNG)


def test_probe_repo_vollstaendig_eingerichtet_ohne_stand_json_exit_0(
    probe_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vollständig eingerichtet, aber ``stand.json`` fehlt → Exit 0, kein „nicht eingerichtet“."""
    monkeypatch.setattr(
        skill_stand, "vergleich", lambda *a, **k: (skill_stand.AKTUELL, "abc123456789 = main")
    )
    (probe_repo / einrichten.STAND_PFAD).unlink()
    code, text = _pruefen(probe_repo)
    assert code == 0, text
    assert "nicht eingerichtet" not in text, text
    zeilen = _zeilen(text)
    for baustein in einrichten._BAUSTEINE:
        zustand, _ = zeilen[baustein.name]
        assert zustand in einrichten.PRUEF_ZUSTAENDE, f"{baustein.name}: {zustand}"
    assert zeilen["gh"][0] == einrichten.PRUEF_OK
    assert zeilen["venv"][0] == einrichten.PRUEF_OK
    assert zeilen["skill_aktuell"] == (einrichten.PRUEF_OK, "abc123456789 = main")


def test_probe_repo_mit_alter_stand_json_ohne_neue_bausteine_exit_0(
    probe_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ältere ``stand.json`` ohne ``gh``/``venv``/``skill_aktuell`` → Exit 0, kein Widerspruch."""
    monkeypatch.setattr(
        skill_stand, "vergleich", lambda *a, **k: (skill_stand.AKTUELL, "deadbeef1234 = main")
    )
    stand_datei = probe_repo / einrichten.STAND_PFAD
    daten = json.loads(stand_datei.read_text(encoding="utf-8"))
    for name in ("gh", "venv", "skill_aktuell"):
        daten["bausteine"].pop(name, None)
    stand_datei.write_text(json.dumps(daten), encoding="utf-8")

    code, text = _pruefen(probe_repo)
    assert code == 0, text
    assert "nicht eingerichtet" not in text, text
    zeilen = _zeilen(text)
    assert zeilen["gh"][0] == einrichten.PRUEF_OK
    assert zeilen["venv"][0] == einrichten.PRUEF_OK
    assert zeilen["skill_aktuell"] == (einrichten.PRUEF_OK, "deadbeef1234 = main")


@pytest.mark.parametrize("fall", ["manifest", "venv", "skill_aktuell", "version"])
def test_ein_punkt_abweichend_meldet_genau_diesen_punkt_exit_3(
    probe_repo: Path, monkeypatch: pytest.MonkeyPatch, fall: str
) -> None:
    """Genau ein Punkt weicht ab → genau dieser Punkt gemeldet, Exit 3, übrige in Ordnung."""
    monkeypatch.setattr(
        skill_stand, "vergleich", lambda *a, **k: (skill_stand.AKTUELL, "abc123456789 = main")
    )
    erwartet = fall
    if fall == "manifest":
        config.pfad(config.lade(probe_repo), "manifeste").rmdir()
    elif fall == "venv":
        (probe_repo / ".venv").mkdir()
    elif fall == "skill_aktuell":
        monkeypatch.setattr(
            skill_stand,
            "vergleich",
            lambda *a, **k: (skill_stand.VERALTET, "veraltet: installiert aaa, main bbb"),
        )
    elif fall == "version":
        erwartet = "manifest"
        neu = [
            dataclasses.replace(b, version=b.version + 1) if b.name == "manifest" else b
            for b in einrichten._BAUSTEINE
        ]
        monkeypatch.setattr(einrichten, "_BAUSTEINE", neu)

    code, text = _pruefen(probe_repo)
    assert code == einrichten.EXIT_ABWEICHUNG, text
    assert "nicht eingerichtet" not in text, text
    assert _abweichungen(text) == [erwartet], text
    zeilen = _zeilen(text)
    assert zeilen[erwartet][0] == einrichten.PRUEF_ABWEICHUNG, zeilen[erwartet]
    if fall == "version":
        assert "Stand-Version" in zeilen[erwartet][1], zeilen[erwartet]
    for baustein in einrichten._BAUSTEINE:
        if baustein.name != erwartet:
            assert zeilen[baustein.name][0] in (
                einrichten.PRUEF_OK,
                einrichten.PRUEF_UNPRUEFBAR,
            ), f"{baustein.name}: {zeilen[baustein.name]}"


def test_nicht_pruefbar_ist_eigener_zustand_und_ergibt_exit_0(
    probe_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nicht prüfbare Punkte melden ``nicht_pruefbar`` statt ``da_aktuell`` und ergeben Exit 0."""
    monkeypatch.setattr(
        skill_stand,
        "vergleich",
        lambda *a, **k: (skill_stand.UNBEKANNT, "Hinweis: main nicht erreichbar, Stand abc123456789"),
    )
    code, text = _pruefen(probe_repo)
    assert code == 0, text
    zeilen = _zeilen(text)
    assert zeilen["skill_aktuell"][0] == einrichten.PRUEF_UNPRUEFBAR, zeilen["skill_aktuell"]
    assert "nicht erreichbar" in zeilen["skill_aktuell"][1]
    assert zeilen["server_klon"][0] == einrichten.PRUEF_UNPRUEFBAR, zeilen["server_klon"]
    assert "nicht geprüft" in zeilen["server_klon"][1]


# --- Teil 2: STAND.json Feld klon ------------------------------------------------------------


def test_stempel_aus_worktree_zeigt_auf_dauerhaften_hauptklon(tmp_path: Path) -> None:
    """Install aus Git-Worktree (Arbeitsordner): ``klon`` zeigt auf den dauerhaften Haupt-Klon."""
    haupt, _bare, sha = _skill_repo(tmp_path)
    wt = tmp_path / "wt-install"
    _git(haupt, "worktree", "add", "-q", "--detach", str(wt), "HEAD")
    ziel = tmp_path / "installiert"
    ziel.mkdir()

    daten = skill_stand.stempel(ziel, wt)
    gelesen = json.loads((ziel / skill_stand.STEMPEL).read_text(encoding="utf-8"))
    assert daten["commit"] == sha
    assert daten["klon"] == str(haupt.resolve())
    assert gelesen["klon"] == str(haupt.resolve())

    # Arbeitsordner entfernen und main weiterbewegen: Befehl zeigt weiter auf den Haupt-Klon.
    _git(haupt, "worktree", "remove", "--force", str(wt))
    assert not wt.exists()
    (haupt / "NEU.txt").write_text("neu\n", encoding="utf-8")
    _commit(haupt, "neu")
    _git(haupt, "push", "-q", "origin", "main")
    zustand, text = skill_stand.vergleich(ziel)
    assert zustand == skill_stand.VERALTET
    assert str(haupt.resolve()) in text
    assert str(wt) not in text


def test_stempel_aus_worktree_von_bare_repo_laesst_klon_weg(tmp_path: Path) -> None:
    """Worktree eines Bare-Repos hat keinen dauerhaften Arbeitsbaum → ``klon`` entfällt."""
    _haupt, bare, sha = _skill_repo(tmp_path)
    wt = tmp_path / "wt-bare"
    _git(bare, "worktree", "add", "-q", "--detach", str(wt), "main")
    ziel = tmp_path / "installiert"
    ziel.mkdir()

    daten = skill_stand.stempel(ziel, wt)
    gelesen = json.loads((ziel / skill_stand.STEMPEL).read_text(encoding="utf-8"))
    assert daten["commit"] == sha
    assert "klon" not in daten
    assert "klon" not in gelesen


def test_stempel_aus_flachem_wegwerf_klon_laesst_klon_weg(tmp_path: Path) -> None:
    """Flacher Wegwerf-Klon (``--depth 1``): ``klon`` entfällt in ``STAND.json``."""
    _haupt, bare, sha = _skill_repo(tmp_path)
    flach = tmp_path / "wegwerf-flach"
    _git(tmp_path, "clone", "-q", "--depth", "1", bare.as_uri(), str(flach))
    ziel = tmp_path / "installiert"
    ziel.mkdir()

    daten = skill_stand.stempel(ziel, flach)
    gelesen = json.loads((ziel / skill_stand.STEMPEL).read_text(encoding="utf-8"))
    assert daten["commit"] == sha
    assert "klon" not in daten
    assert "klon" not in gelesen


def test_stempel_aus_nicht_git_ordner_laesst_klon_weg(tmp_path: Path) -> None:
    """Kopierter Wegwerf-Ordner ohne ``.git``: ``klon`` entfällt in ``STAND.json``."""
    kopie = tmp_path / "wegwerf-kopie"
    kopie.mkdir()
    ziel = tmp_path / "installiert"
    ziel.mkdir()

    daten = skill_stand.stempel(ziel, kopie)
    gelesen = json.loads((ziel / skill_stand.STEMPEL).read_text(encoding="utf-8"))
    assert daten["commit"] == ""
    assert "klon" not in daten
    assert "klon" not in gelesen


def test_stempel_aus_geteiltem_klon_folgt_hauptklon_oder_entfaellt(tmp_path: Path) -> None:
    """``git clone --shared`` folgt dem dauerhaften Haupt-Klon; bei Bare-Quelle entfällt ``klon``."""
    haupt, bare, _sha = _skill_repo(tmp_path)
    geteilt_haupt = tmp_path / "geteilt-haupt"
    _git(tmp_path, "clone", "-q", "--shared", str(haupt), str(geteilt_haupt))
    ziel_haupt = tmp_path / "ziel-haupt"
    ziel_haupt.mkdir()
    daten_haupt = skill_stand.stempel(ziel_haupt, geteilt_haupt)
    assert daten_haupt["klon"] == str(haupt.resolve())

    geteilt_bare = tmp_path / "geteilt-bare"
    _git(tmp_path, "clone", "-q", "--shared", str(bare), str(geteilt_bare))
    ziel_bare = tmp_path / "ziel-bare"
    ziel_bare.mkdir()
    daten_bare = skill_stand.stempel(ziel_bare, geteilt_bare)
    assert "klon" not in daten_bare


def test_vergleich_ignoriert_geloeschten_klon_pfad_in_altem_stempel(tmp_path: Path) -> None:
    """Alter Stempel mit inzwischen entferntem Klon-Ordner nennt keinen toten Pfad im Befehl."""
    haupt, bare, sha = _skill_repo(tmp_path)
    weg = tmp_path / "entfernter-arbeitsordner"
    ziel = tmp_path / "installiert"
    ziel.mkdir()
    (ziel / skill_stand.STEMPEL).write_text(
        json.dumps(
            {
                "commit": sha,
                "zeit": "2026-11-22T00:00:00+00:00",
                "quelle": str(bare),
                "klon": str(weg),
                "dirty": "nein",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (haupt / "NEU.txt").write_text("neu\n", encoding="utf-8")
    _commit(haupt, "neu")
    _git(haupt, "push", "-q", "origin", "main")

    zustand, text = skill_stand.vergleich(ziel)
    assert zustand == skill_stand.VERALTET
    assert str(weg) not in text
    assert "<Skill-Klon>" in text
