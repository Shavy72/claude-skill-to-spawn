"""Tests für #406: ``anhaengen`` meldet fehlendes Checkpoint-Ticket nicht als FEHLER/WEIGERUNG.

Beim schrittweisen Anhängen der Tickets vor dem Abnahme-Ticket (das laut Skill
zuletzt folgt) darf ``to_spawn.py anhaengen`` höchstens eine Info ausgeben — kein
``FEHLER`` und keine ``WEIGERUNG``. ``regularien`` und ``spawn`` prüfen das
Checkpoint-Ticket weiterhin hart (Exit 3).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from test_spec_rand_e2 import CLI, SLUG, _git, _issue

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import config, manifest

SPEC = 950
BAU_TICKETS = tuple(range(951, 957))  # sechs Bau-Tickets nacheinander
ABNAHME = 957


def _welt_ohne_abnahme() -> dict[str, Any]:
    issues: dict[str, Any] = {str(SPEC): _issue(SPEC, "Spec: Reihenfolge")}
    for nr in BAU_TICKETS:
        body = f"## Blocked by\n#{nr - 1}" if nr > BAU_TICKETS[0] else ""
        issues[str(nr)] = _issue(nr, f"Bau-Schritt {nr}", body=body)
    issues[str(ABNAHME)] = _issue(
        ABNAHME,
        "Abnahme durch den Menschen",
        labels=["checkpoint:human"],
        body="## Blocked by\n" + "\n".join(f"#{nr}" for nr in BAU_TICKETS),
    )
    return {"issues": issues, "unter": {str(SPEC): []}, "kanten": {}, "posts": []}


def _aufbau(
    tmp_path: Path, welt: dict[str, Any] | None = None
) -> tuple[Path, Path, dict[str, str]]:
    """Wegwerf-Repo mit vollständiger Einrichtungs-Konfig und gestelltem gh."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "remote", "add", "origin", f"https://github.com/{SLUG}.git")
    (repo / "pyproject.toml").write_text("[project]\nname = 'wegwerf'\n", encoding="utf-8")
    konfig_pfad = repo / ".to-spawn" / "config.json"
    konfig_pfad.parent.mkdir(parents=True)
    konfig_pfad.write_text(
        json.dumps(
            {
                "staging": {"ohne": True},
                "pruefung": {"schnell": "pytest -q", "komplett": "pytest -q"},
                "live": {"befehl": "true"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    weltdatei = tmp_path / "welt.json"
    weltdatei.write_text(json.dumps(welt or _welt_ohne_abnahme()), encoding="utf-8")
    umgebung = {
        k: v
        for k, v in os.environ.items()
        if k.startswith("TO_SPAWN_SPEICHER_") or not k.startswith(("GH_STUB", "TO_SPAWN"))
    }
    umgebung.update(
        {
            "TO_SPAWN_GH_STUB": str(Path(__file__).resolve().parent / "test_spec_rand_e2.py"),
            "E2_GH_WELT": str(weltdatei),
        }
    )
    return repo, weltdatei, umgebung


def _cli(repo: Path, umgebung: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        check=False,
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _anhaengen(
    repo: Path, umgebung: dict[str, str], nummer: int, *extra: str
) -> subprocess.CompletedProcess[str]:
    return _cli(
        repo,
        umgebung,
        "anhaengen",
        str(SPEC),
        str(nummer),
        "--schaetzung",
        "40",
        "--umfang",
        f"Schritt {nummer} umsetzen.",
        *extra,
    )


def test_sechs_tickets_nacheinander_ohne_fehler_regularien_bleibt_hart(tmp_path: Path) -> None:
    """Abnahme #406: 6 Tickets vor Abnahme anhängen ohne FEHLER/WEIGERUNG; regularien/spawn hart."""
    repo, _, umgebung = _aufbau(tmp_path)

    for idx, nr in enumerate(BAU_TICKETS):
        extra = ("--blockiert-von", str(BAU_TICKETS[idx - 1])) if idx > 0 else ()
        lauf = _anhaengen(repo, umgebung, nr, *extra)
        assert lauf.returncode == 0, lauf.stdout + lauf.stderr
        assert "FEHLER" not in lauf.stdout, lauf.stdout
        assert "WEIGERUNG" not in lauf.stdout, lauf.stdout
        assert "Info" in lauf.stdout and "Checkpoint-Ticket" in lauf.stdout, lauf.stdout

    # regularien ohne Abnahme-Ticket bleibt hart (Exit 3 + FEHLER + WEIGERUNG).
    reg = _cli(repo, umgebung, "regularien", str(SPEC))
    assert reg.returncode == manifest.EXIT_WEIGERUNG, reg.stdout + reg.stderr
    assert "FEHLER  Kein Checkpoint-Ticket" in reg.stdout, reg.stdout
    assert "WEIGERUNG" in reg.stdout, reg.stdout

    # spawn ohne Abnahme-Ticket bleibt ebenfalls hart.
    sp = _cli(repo, umgebung, "spawn", str(SPEC), "--ziel", "srv", "--dry-run")
    assert sp.returncode == manifest.EXIT_WEIGERUNG, sp.stdout + sp.stderr
    assert "FEHLER  Kein Checkpoint-Ticket" in sp.stdout, sp.stdout
    assert "WEIGERUNG" in sp.stdout, sp.stdout

    # Abnahme-Ticket zuletzt anhängen: Prüfung sauber ohne Info/Fehler.
    abn = _anhaengen(
        repo,
        umgebung,
        ABNAHME,
        "--blockiert-von",
        ",".join(str(nr) for nr in BAU_TICKETS),
    )
    assert abn.returncode == 0, abn.stdout + abn.stderr
    assert "alles erfüllt" in abn.stdout, abn.stdout
    assert "Info" not in abn.stdout, abn.stdout

    reg_danach = _cli(repo, umgebung, "regularien", str(SPEC))
    assert reg_danach.returncode == 0, reg_danach.stdout + reg_danach.stderr
    assert "alles erfüllt" in reg_danach.stdout, reg_danach.stdout


def test_pruefe_checkpoint_pflicht_schalter(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """``manifest.pruefe`` meldet fehlendes Checkpoint-Ticket nur bei ``checkpoint_pflicht=True`` hart."""
    repo, _, umgebung = _aufbau(tmp_path)
    lauf = _anhaengen(repo, umgebung, BAU_TICKETS[0])
    assert lauf.returncode in (0, manifest.EXIT_WEIGERUNG), lauf.stdout + lauf.stderr

    for k in ("TO_SPAWN_GH_STUB", "E2_GH_WELT"):
        monkeypatch.setenv(k, umgebung[k])
    konfig = config.lade(repo)

    weich = manifest.pruefe(repo, SPEC, konfig, checkpoint_pflicht=False)
    assert weich.sauber, weich.text()
    assert weich.fehler == []
    assert any("Checkpoint-Ticket" in h for h in weich.hinweise), weich.hinweise
    assert "FEHLER" not in weich.text()
    assert "WEIGERUNG" not in weich.text()
    assert "Info" in weich.text()

    hart = manifest.pruefe(repo, SPEC, konfig)
    assert not hart.sauber
    assert any("Kein Checkpoint-Ticket" in f for f in hart.fehler), hart.fehler
    assert "FEHLER  Kein Checkpoint-Ticket" in hart.text()
    assert "WEIGERUNG" in hart.text()


def test_anhaengen_meldet_andere_regelfehler_weiterhin_hart(tmp_path: Path) -> None:
    """Ohne Abnahme-Ticket bleiben echte Mängel (z. B. fehlende Kante) bei ``anhaengen`` Exit 3."""
    repo, _, umgebung = _aufbau(tmp_path)
    assert _anhaengen(repo, umgebung, BAU_TICKETS[0]).returncode == 0
    # Ticket 952 nennt im Text ``## Blocked by #951``, wird aber ohne --blockiert-von angehängt.
    lauf = _anhaengen(repo, umgebung, BAU_TICKETS[1])
    assert lauf.returncode == manifest.EXIT_WEIGERUNG, lauf.stdout + lauf.stderr
    assert "FEHLER" in lauf.stdout and "native Kante fehlt" in lauf.stdout, lauf.stdout
    assert "Kein Checkpoint-Ticket" not in lauf.stdout, lauf.stdout
    assert "WEIGERUNG" in lauf.stdout, lauf.stdout
