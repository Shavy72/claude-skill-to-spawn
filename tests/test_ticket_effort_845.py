"""#845: Effort je Ticket im Manifest + Übersetzung in den ``agy``-Modellnamen.

Prüft:
1. Ticket-Feld ``effort`` im Manifest (Validierung in ``manifest.pruefe_eintrag``/``pruefe``).
2. Einzige Auflösungs-Stelle ``manifest.effort_fuer`` mit Vorrang:
   Ticket-Effort > Spec ``manifest["effort"]`` > ``config["effort"]["ticket"]`` > ``low``.
3. ``werkzeug.modell_fuer`` und ``werkzeug.startbefehl`` bei ``runner == "agy"``:
   Übersetzung von ``effort`` nach ``gemini-4-neon-<effort>`` (``max`` → ``xhigh``),
   außer ``--model`` oder ``config.modelle.ticket`` (Nicht-Claude) ist ausdrücklich gesetzt.
4. Echter Weg-Test über ``skripte/bau.py --dry-run`` als Subprozess für ``claude`` und ``agy``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import context_mode_attrappe
import pytest

from to_spawn import config, manifest, werkzeug

SKILL = Path(__file__).resolve().parent.parent


def _repo(
    tmp_path: Path,
    *,
    konfig: dict[str, Any] | None = None,
    spec_effort: Any = None,
    ticket_effort: Any = None,
    mit_spec_effort: bool = False,
    mit_ticket_effort: bool = False,
) -> Path:
    arbeit = tmp_path / "repo"
    manifeste = arbeit / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(arbeit), check=True, capture_output=True)
    if konfig is not None:
        konfig_datei = arbeit / ".to-spawn" / "config.json"
        konfig_datei.parent.mkdir(parents=True, exist_ok=True)
        konfig_datei.write_text(json.dumps(konfig), encoding="utf-8")
    ticket_daten: dict[str, Any] = {
        "title": "Effort-Ticket",
        "schaetzung_k": 100,
        "umfang": "Effort je Ticket prüfen.",
        "mcp": [],
    }
    if mit_ticket_effort or ticket_effort is not None:
        ticket_daten["effort"] = ticket_effort
    manifest_daten: dict[str, Any] = {
        "spec": 845,
        "tickets": {"901": ticket_daten},
    }
    if mit_spec_effort or spec_effort is not None:
        manifest_daten["effort"] = spec_effort
    (manifeste / "spec-845.json").write_text(json.dumps(manifest_daten), encoding="utf-8")
    return arbeit


def _bau_dry_run(tmp_path: Path, repo: Path, *extra_args: str) -> str:
    home = tmp_path / "home"
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    context_mode_attrappe.plugin_anlegen(home, "1.0.169", True)
    env = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_REPO"}
    env.update(
        HOME=str(home),
        USERPROFILE=str(home),
        TMPDIR=str(home / "tmp"),
        TO_SPAWN_GH_REPO="test/effort-845",
    )
    ergebnis = subprocess.run(
        [sys.executable, str(SKILL / "skripte" / "bau.py"), "901", "--dry-run", *extra_args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    return ausgabe.split("Befehl:", 1)[1]


# --- 1. Manifest-Validierung für Ticket-Effort ------------------------------


@pytest.mark.parametrize(
    ("wert", "sauber"),
    [
        ("low", True),
        ("medium", True),
        ("high", True),
        ("xhigh", True),
        ("max", True),
        ("turbo", False),
        ("", False),
        (None, False),
        (42, False),
    ],
)
def test_manifest_prueft_ticket_effort(tmp_path: Path, wert: Any, sauber: bool) -> None:
    repo = _repo(tmp_path, ticket_effort=wert, mit_ticket_effort=True)
    bericht = manifest.pruefe(repo, 845, {}, mit_github=False)
    assert bericht.sauber is sauber, bericht.text()
    if not sauber:
        assert "#901" in bericht.text()
        assert "effort" in bericht.text()


# --- 2. Einzige Auflösungs-Stelle manifest.effort_fuer ----------------------


def test_effort_fuer_vorrang_reihenfolge(caplog: pytest.LogCaptureFixture) -> None:
    # Ticket-Effort > Spec-Effort > Konfig effort.ticket > Fallback "low".
    assert (
        manifest.effort_fuer(
            {"effort": "high"},
            {"effort": "low"},
            {"effort": {"ticket": "xhigh"}},
        )
        == "low"
    )
    assert (
        manifest.effort_fuer(
            {"effort": "high"},
            {"title": "Ohne eigenes Effort"},
            {"effort": {"ticket": "low"}},
        )
        == "high"
    )
    assert (
        manifest.effort_fuer(
            {"spec": 845},
            {"title": "Ohne eigenes Effort"},
            {"effort": {"ticket": "medium"}},
        )
        == "medium"
    )
    assert manifest.effort_fuer(None, {}, {}) == "low"
    assert manifest.effort_fuer(None, None, None) == "low"

    # Ungültiger Wert am Ticket wird gewarnt und fällt auf die nächste gültige Stufe zurück.
    with caplog.at_level("WARNING"):
        assert (
            manifest.effort_fuer(
                {"effort": "high"},
                {"effort": "turbo"},
                {"effort": {"ticket": "medium"}},
            )
            == "high"
        )
    assert "turbo" in caplog.text


# --- 3. werkzeug.modell_fuer + startbefehl für agy --------------------------


@pytest.mark.parametrize(
    ("stufe", "erwartetes_modell"),
    [
        ("low", "gemini-4-neon-low"),
        ("medium", "gemini-4-neon-medium"),
        ("high", "gemini-4-neon-high"),
        ("xhigh", "gemini-4-neon-xhigh"),
        ("max", "gemini-4-neon-xhigh"),
    ],
)
def test_werkzeug_agy_uebersetzt_effort_in_modell_und_startbefehl(stufe: str, erwartetes_modell: str) -> None:
    # Geerbtes Claude-Modell in der Konfig zählt nicht als ausdrückliche agy-Wahl.
    konfig_agy = {**config.DEFAULTS, "runner": "agy"}
    assert werkzeug.modell_fuer("agy", konfig_agy, effort=stufe) == erwartetes_modell

    # startbefehl ohne explizites modell übersetzt effort direkt in --model gemini-4-neon-<stufe>.
    cmd = werkzeug.startbefehl(
        "agy",
        programm="/opt/bin/agy",
        modell=None,
        prompt="Baue #845",
        interaktiv=True,
        effort=stufe,
    )
    assert cmd == [
        "/opt/bin/agy",
        "-i",
        "Baue #845",
        "--model",
        erwartetes_modell,
        "--dangerously-skip-permissions",
    ]
    assert "--effort" not in cmd


def test_werkzeug_agy_ausdrueckliches_modell_gewinnt_ueber_effort() -> None:
    konfig_agy = {**config.DEFAULTS, "runner": "agy"}
    konfig_eigen = {**config.DEFAULTS, "runner": "agy", "modelle": {"ticket": "gemini-4-neon-xhigh"}}

    # config.modelle.ticket (Nicht-Claude) gewinnt über effort.
    assert werkzeug.modell_fuer("agy", konfig_eigen, effort="low") == "gemini-4-neon-xhigh"
    # --model gewinnt über config.modelle.ticket und effort.
    assert (
        werkzeug.modell_fuer("agy", konfig_eigen, cli_modell="gemini-4-neon-medium", effort="low")
        == "gemini-4-neon-medium"
    )
    assert (
        werkzeug.modell_fuer("agy", konfig_agy, cli_modell="gemini-4-neon-low", effort="xhigh") == "gemini-4-neon-low"
    )


def test_werkzeug_agy_unbekannter_effort_warnt_und_nimmt_standard(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        assert werkzeug.agy_modell_fuer_effort("turbo") == werkzeug.AGY_STANDARD_MODELL
    assert "turbo" in caplog.text
    assert werkzeug.agy_modell_fuer_effort(None) is None
    assert werkzeug.agy_modell_fuer_effort("  ") is None


# --- 4. Echter Weg-Test über skripte/bau.py --dry-run -----------------------


@pytest.mark.parametrize(
    ("stufe", "erwartetes_modell"),
    [
        ("low", "gemini-4-neon-low"),
        ("medium", "gemini-4-neon-medium"),
        ("high", "gemini-4-neon-high"),
        ("xhigh", "gemini-4-neon-xhigh"),
    ],
)
def test_bau_dry_run_agy_ticket_effort_je_stufe(tmp_path: Path, stufe: str, erwartetes_modell: str) -> None:
    repo = _repo(
        tmp_path,
        konfig={"runner": "agy", "effort": {"ticket": "high"}},
        spec_effort="high",
        ticket_effort=stufe,
    )
    befehl = _bau_dry_run(tmp_path, repo)
    assert f"--model {erwartetes_modell}" in befehl
    assert "--effort" not in befehl


def test_bau_dry_run_vorrang_claude_und_agy(tmp_path: Path) -> None:
    # claude: Ticket > Spec > Konfig
    r1 = _repo(
        tmp_path / "c1",
        konfig={"runner": "claude", "effort": {"ticket": "low"}},
        spec_effort="medium",
        ticket_effort="xhigh",
    )
    assert "--effort xhigh" in _bau_dry_run(tmp_path / "c1", r1)

    r2 = _repo(
        tmp_path / "c2",
        konfig={"runner": "claude", "effort": {"ticket": "low"}},
        spec_effort="high",
    )
    assert "--effort high" in _bau_dry_run(tmp_path / "c2", r2)

    # agy: ohne Ticket-Effort gilt Spec-Effort; ohne beides gilt config.effort.ticket.
    r3 = _repo(
        tmp_path / "a1",
        konfig={"runner": "agy", "effort": {"ticket": "low"}},
        spec_effort="xhigh",
    )
    assert "--model gemini-4-neon-xhigh" in _bau_dry_run(tmp_path / "a1", r3)

    r4 = _repo(
        tmp_path / "a2",
        konfig={"runner": "agy", "effort": {"ticket": "low"}},
    )
    assert "--model gemini-4-neon-low" in _bau_dry_run(tmp_path / "a2", r4)

    # agy: ausdrückliches config.modelle.ticket bzw. --model überschreibt Ticket-Effort.
    r5 = _repo(
        tmp_path / "a3",
        konfig={"runner": "agy", "modelle": {"ticket": "gemini-4-neon-xhigh"}, "effort": {"ticket": "medium"}},
        ticket_effort="low",
    )
    assert "--model gemini-4-neon-xhigh" in _bau_dry_run(tmp_path / "a3", r5)
    assert "--model gemini-4-neon-medium" in _bau_dry_run(tmp_path / "a3", r5, "--model", "gemini-4-neon-medium")
