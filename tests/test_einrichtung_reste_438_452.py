"""Tests für #438 (Reste aus PR 433) und #452 (Schalter ``live.ohne`` + Docstring ``pruef_befehl``).

Echt laufen: ``bau_log``, ``config``, ``einrichtungs_ticket``, ``aufseher_stand``, Git-Repos
mit echtem Bare-``origin``, CLI (``to_spawn.py regularien``). Gestellt ist nur ``gh``
(externer Dienst) über ``TO_SPAWN_GH_STUB``.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import test_einrichtungs_ticket_410 as t410
import test_spec_rand_e2 as e2

from to_spawn import aufseher_stand, bau_log, config, einrichtungs_ticket, gh, manifest

STAGING = {
    "staging": {"url": "https://staging.example"},
    "staging_start": "make staging",
}
PRUEFUNG = {
    "pruefung": {"schnell": "make pruefen-schnell", "komplett": "make pruefen-alles"},
}


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _repo_mit_python(pfad: Path) -> Path:
    (pfad / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    return pfad


def _konfig(**eigen: Any) -> dict[str, Any]:
    return config._mische(config.DEFAULTS, {**STAGING, **eigen})


# --- #438 Punkt 1: gemeinsamer Helfer worktree_pfad + log_ort -----------------


def test_log_ort_fuer_bevorzugt_worktree_vor_hauptbaum(tmp_path: Path) -> None:
    wt_basis = tmp_path / "wt"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"worktree_basis": wt_basis.as_posix()}), encoding="utf-8"
    )
    assert bau_log.log_ort_fuer(901, repo) is None

    # Nur im Hauptbaum → liefert Hauptbaum.
    haupt_log = bau_log.lauf_pfad(repo, 901)
    haupt_log.parent.mkdir(parents=True, exist_ok=True)
    haupt_log.write_text("{}\n", encoding="utf-8")
    assert bau_log.log_ort_fuer(901, repo) == repo

    # Zusätzlich im Worktree → Worktree gewinnt.
    wt = wt_basis / "wt-901"
    wt_log = bau_log.lauf_pfad(wt, 901)
    wt_log.parent.mkdir(parents=True, exist_ok=True)
    wt_log.write_text("{}\n", encoding="utf-8")
    assert bau_log.log_ort_fuer(901, repo) == wt


def test_aufseher_stand_und_einrichtung_nutzen_log_ort_fuer(tmp_path: Path) -> None:
    wt_basis = tmp_path / "wt"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps({"worktree_basis": wt_basis.as_posix()}), encoding="utf-8"
    )
    wt = wt_basis / "wt-901"
    bau_log.schreibe(wt, 901, "entscheidung", text="Probe-Text")
    lage = aufseher_stand._log_lage(901, repo)
    assert lage is not None and lage[2] == "Probe-Text"

    # Quell-Nachweis: gemeinsame Zusammensetzung statt Einzelkopien.
    wurzel = Path(__file__).resolve().parents[1]
    for modul in (
        "to_spawn/aufseher_stand.py",
        "to_spawn/einrichtungs_ticket.py",
        "to_spawn/eigener_handoff.py",
    ):
        text = (wurzel / modul).read_text(encoding="utf-8")
        assert "bau_log.log_ort_fuer(" in text, modul
        assert "bau_log.log_ort(" not in text, modul


# --- #438 Punkt 2: Bau-Log auch auf origin + staging:fertig erkennen ---------


def _mit_echtem_origin(
    tmp_path: Path, *, zweig: str, log_datei_inhalt: str = '{"typ":"session_start"}\n'
) -> tuple[Path, Path, dict[str, str]]:
    """Repo mit Bare-``origin``: ein zweiter Klon pusht ein Bau-Log auf ``zweig``;
    ``repo`` holt die Remote-Refs (``git fetch``), hat das Log aber NICHT im Arbeitsbaum.
    """
    repo, weltdatei, umgebung = t410._aufbau(
        tmp_path, konfig={"hauptzweig": "main", "worktree_basis": (tmp_path / "wt").as_posix()}
    )
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    _git(repo, "config", "user.email", "t@e")
    _git(repo, "config", "user.name", "t")
    _git(repo, "branch", "-M", "main")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    _git(repo, "remote", "set-url", "origin", str(bare))
    _git(repo, "push", "-u", "origin", "main")

    # Zweiter Klon (z. B. Bau-Server) committet versioniertes Bau-Log und pusht.
    klon2 = tmp_path / "klon2"
    _git(tmp_path, "clone", str(bare), str(klon2))
    _git(klon2, "config", "user.email", "t@e")
    _git(klon2, "config", "user.name", "t")
    if zweig != "main":
        _git(klon2, "checkout", "-b", zweig)
    ziel = klon2 / bau_log.LOG_ORDNER / "901.jsonl"
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(log_datei_inhalt, encoding="utf-8")
    _git(klon2, "add", ".")
    _git(klon2, "commit", "-m", "bau_log 901")
    _git(klon2, "push", "origin", zweig)

    # Erster Klon holt Remote-Refs (kein Pull in den Arbeitsbaum), origin zeigt wieder auf GitHub-Slug.
    _git(repo, "fetch", "origin")
    _git(repo, "remote", "set-url", "origin", f"https://github.com/{e2.SLUG}.git")
    wt = Path(config.worktree_pfad(901, repo)).expanduser()
    assert bau_log.log_ort(901, wt, repo) is None
    return repo, weltdatei, umgebung


def test_bau_log_nur_auf_origin_hauptzweig_legt_nichts_an(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _mit_echtem_origin(tmp_path, zweig="main")
    vorher = e2._manifest(repo).read_bytes()
    lauf = t410._cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    t410._nichts_angelegt(repo, weltdatei, vorher)
    assert "läuft schon" in lauf.stdout and "#901" in lauf.stdout and "Bau-Log" in lauf.stdout, ausgabe


def test_bau_log_nur_auf_origin_ticket_zweig_legt_nichts_an(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _mit_echtem_origin(tmp_path, zweig="ticket/901-kern")
    vorher = e2._manifest(repo).read_bytes()
    lauf = t410._cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    t410._nichts_angelegt(repo, weltdatei, vorher)
    assert "läuft schon" in lauf.stdout and "#901" in lauf.stdout and "Bau-Log" in lauf.stdout, ausgabe


def test_ticket_staging_fertig_gilt_als_laeuft_schon(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = t410._aufbau(tmp_path)
    welt = e2._welt(weltdatei)
    welt["issues"]["901"]["labels"].append({"id": 2, "name": "staging:fertig"})
    weltdatei.write_text(json.dumps(welt), encoding="utf-8")
    vorher = e2._manifest(repo).read_bytes()
    lauf = t410._cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    t410._nichts_angelegt(repo, weltdatei, vorher)
    assert "läuft schon" in lauf.stdout and "#901" in lauf.stdout, ausgabe


# --- #438 Punkt 5: Zeitlimit für gh.json_lauf (Start hängt nicht) -------------


def test_gh_json_lauf_und_regularien_brechen_bei_haengendem_gh_ab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub = tmp_path / "gh_haengt.py"
    stub.write_text("import time\ntime.sleep(20)\n", encoding="utf-8")
    monkeypatch.setenv(gh.GH_STUB_ENV, str(stub))
    monkeypatch.setenv(gh.GH_ZEIT_ENV, "1.0")

    ergebnis: dict[str, Any] = {}

    def _rufe() -> None:
        ergebnis["wert"] = gh.json_lauf(["api", "repos/o/r/issues/1"])

    start = time.monotonic()
    faden = threading.Thread(target=_rufe, daemon=True)
    faden.start()
    faden.join(8.0)
    dauer = time.monotonic() - start
    assert not faden.is_alive(), f"gh.json_lauf hängt nach {dauer:.1f} s noch"
    assert ergebnis["wert"] is None

    # Weg-Nachweis: regularien mit hängendem gh weigert mit Exit 3 statt zu hängen.
    weg = tmp_path / "weg"
    weg.mkdir()
    repo, _welt, umgebung = t410._aufbau(weg)
    umgebung[gh.GH_STUB_ENV] = str(stub)
    umgebung[gh.GH_ZEIT_ENV] = "0.5"
    start_cli = time.monotonic()
    lauf = t410._cli(repo, umgebung, "regularien", str(e2.SPEC))
    dauer_cli = time.monotonic() - start_cli
    assert dauer_cli < 10.0, f"regularien hing {dauer_cli:.1f} s"
    assert lauf.returncode == 3, lauf.stdout + lauf.stderr
    assert lauf.stdout.strip().splitlines()[-1].startswith("EINRICHTUNG FEHLT")


def test_hole_github_spart_blocked_by_wenn_ticket_daten_scheitern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scheitert ``gh.ticket_daten``, fragt ``manifest._hole_github`` nicht zusätzlich ``blocked_by`` ab (#353)."""
    aufrufe: list[str] = []
    monkeypatch.setattr(gh, "gh_befehl", lambda: ["gh"])
    monkeypatch.setattr(gh, "repo_aus_origin", lambda _repo: "o/r")
    monkeypatch.setattr(gh, "ticket_daten", lambda nr, cwd=None: aufrufe.append(f"daten:{nr}") or None)
    monkeypatch.setattr(gh, "blocked_by", lambda _slug, nr, cwd=None: aufrufe.append(f"kanten:{nr}") or [])

    bericht = manifest.Bericht(spec="900", tickets={"901": {}, "909": {}})
    ergebnis = manifest._hole_github(tmp_path, bericht)

    assert ergebnis == {"901": None, "909": None}
    assert len(bericht.fehler) == 2
    assert aufrufe == ["daten:901", "daten:909"], aufrufe


# --- #452: Schalter live.ohne analog staging.ohne + Docstring pruef_befehl ----


def test_live_bewusst_ohne_analog_staging_ohne(caplog: pytest.LogCaptureFixture) -> None:
    assert config.DEFAULTS["live"]["ohne"] is False
    assert config.live_bewusst_ohne({"live": {"ohne": True}}) is True
    assert config.live_bewusst_ohne({"live": {"ohne": False}}) is False
    assert config.live_bewusst_ohne({}) is False
    assert config.live_bewusst_ohne({"live": "kaputt"}) is False
    assert config.live_bewusst_ohne({"live": {"ohne": "true"}}) is False

    # live_schritt bleibt reines Text-dict ohne Warnung über den Bool-Schalter ``ohne``.
    with caplog.at_level(logging.WARNING, logger="to_spawn.config"):
        werte = config.live_schritt(config.DEFAULTS)
    assert werte == {"befehl": "", "url": "", "aufraeumen": ""}
    assert not caplog.records


def test_luecken_laesst_live_befehl_bei_live_ohne_weg(tmp_path: Path) -> None:
    repo = _repo_mit_python(tmp_path)
    konfig = _konfig(**PRUEFUNG, live={"ohne": True})
    assert einrichtungs_ticket.luecken(repo, konfig) == []

    # Fehlende andere Pflichten bleiben sichtbar.
    konfig_ohne_schnell = _konfig(
        pruefung={"schnell": "", "komplett": "make pruefen-alles"},
        live={"ohne": True},
    )
    assert einrichtungs_ticket.luecken(repo, konfig_ohne_schnell) == ["pruefung.schnell"]

    # Auftragstext nennt die Möglichkeit live.ohne: true analog staging.ohne: true.
    assert "live.ohne: true" in einrichtungs_ticket._AUFTRAG["live.befehl"]


def test_weg_regularien_mit_live_ohne_bleibt_still(tmp_path: Path) -> None:
    konfig = {**STAGING, **PRUEFUNG, "live": {"ohne": True}}
    repo, weltdatei, umgebung = t410._aufbau(tmp_path, konfig=konfig, python=True)
    vorher = e2._manifest(repo).read_bytes()
    lauf = t410._cli(repo, umgebung, "regularien", str(e2.SPEC))
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    t410._nichts_angelegt(repo, weltdatei, vorher)
    assert "Einrichtung" not in lauf.stdout


def test_pruef_befehl_docstring_nennt_einrichtungs_ticket() -> None:
    doc = config.pruef_befehl.__doc__ or ""
    assert "Nur für :mod:`to_spawn.pruefplan`" not in doc
    assert "einrichtungs_ticket" in doc
