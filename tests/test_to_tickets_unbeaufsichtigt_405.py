"""Tests für #405: Modus „unbeaufsichtigt“ für ``/to-tickets`` und Spec-Prüfung.

Prüft:
1. Ohne Modus „unbeaufsichtigt“: fehlende ``Quelle:``-Zeile oder fehlende ``(E<n>)``
   blockieren den Schnitt hart (``darf_schneiden=False``, ``zustimmung_noetig=True``).
2. Mit Modus „unbeaufsichtigt“ über Konfig (``regularien.unbeaufsichtigt: true``) oder
   Umgebungsvariable (``TO_SPAWN_UNBEAUFSICHTIGT=1``): Zustimmung gilt als gegeben
   (``zustimmung_noetig=False``), fehlende ``Quelle:``/``(E<n>)`` sind Warnung im Log
   und Ergebnis (``darf_schneiden=True``).
3. ``TO_SPAWN_UNBEAUFSICHTIGT=0`` schaltet Konfig ``true`` wieder hart.
4. Echte CLI ``to_spawn.py spec-pruefen <datei|issue>`` (Exit 3 hart, Exit 0 mit Warnung
   im Modus unbeaufsichtigt).
5. ``kette/skills/to-tickets/SKILL.md`` dokumentiert den Modus für Beschluss-Protokoll
   und Zustimmung (Schritt 4) und lässt die Prüfung ohne den Modus hart.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import config, manifest

SKILL = Path(__file__).resolve().parents[1]
CLI = SKILL / "to_spawn.py"
TO_TICKETS = SKILL / "kette" / "skills" / "to-tickets" / "SKILL.md"

SPEC_OHNE_PROTOKOLL = """# Probe-Spec: Notiz-App

## Problem Statement
Nutzer möchten Notizen anlegen und auflisten.

## User Stories
1. Als Nutzer möchte ich eine Notiz anlegen.
2. Als Nutzer möchte ich alle Notizen sehen.
"""

SPEC_MIT_PROTOKOLL = """Quelle: docs/GRILL_2026-11-25_probe.md (Stand 2026-11-25)

## User Stories
1. Als Nutzer möchte ich eine Notiz anlegen. (E1)
2. Als Nutzer möchte ich alle Notizen sehen. (E2)
"""


def _git(ort: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=ort, check=True, capture_output=True)


def _cli(repo: Path, *args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    umg = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_UNBEAUFSICHTIGT"}
    if env_extra:
        umg.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=repo,
        env=umg,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def test_defaults_enthaelt_regularien_unbeaufsichtigt_false() -> None:
    assert config.DEFAULTS["regularien"]["unbeaufsichtigt"] is False


def test_spec_ohne_quelle_und_e_nummern_hart_ohne_modus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Abnahme #405: ohne Modus bleibt die Prüfung hart."""
    monkeypatch.delenv("TO_SPAWN_UNBEAUFSICHTIGT", raising=False)
    ergebnis = manifest.pruefe_spec(SPEC_OHNE_PROTOKOLL, {})
    assert ergebnis.unbeaufsichtigt is False
    assert ergebnis.zustimmung_noetig is True
    assert ergebnis.darf_schneiden is False
    assert ergebnis.warnungen == []
    assert any("Quelle" in f for f in ergebnis.fehler), ergebnis.fehler
    assert any("(E<n>)" in f for f in ergebnis.fehler), ergebnis.fehler
    assert "STOPP" in ergebnis.text()


def test_spec_ohne_quelle_und_e_nummern_unbeaufsichtigt_per_konfig(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Abnahme #405: Konfig ``regularien.unbeaufsichtigt: true`` → Schnitt frei, Warnung im Log."""
    monkeypatch.delenv("TO_SPAWN_UNBEAUFSICHTIGT", raising=False)
    konfig = {"regularien": {"unbeaufsichtigt": True}}
    with caplog.at_level(logging.WARNING, logger="to_spawn.manifest"):
        ergebnis = manifest.pruefe_spec(SPEC_OHNE_PROTOKOLL, konfig)
    assert ergebnis.unbeaufsichtigt is True
    assert ergebnis.zustimmung_noetig is False
    assert ergebnis.darf_schneiden is True
    assert ergebnis.fehler == []
    assert any("Quelle" in w for w in ergebnis.warnungen), ergebnis.warnungen
    assert any("(E<n>)" in w for w in ergebnis.warnungen), ergebnis.warnungen
    assert any("Quelle" in r.message for r in caplog.records), caplog.records
    assert any("(E<n>)" in r.message for r in caplog.records), caplog.records
    assert "Warnung" in ergebnis.text()
    assert "STOPP" not in ergebnis.text()


def test_unbeaufsichtigt_umgebungsvariable_ueberschreibt_konfig(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``TO_SPAWN_UNBEAUFSICHTIGT`` gewinnt vor der Konfig — sowohl an (1) als auch aus (0)."""
    monkeypatch.setenv("TO_SPAWN_UNBEAUFSICHTIGT", "1")
    with caplog.at_level(logging.WARNING, logger="to_spawn.manifest"):
        weich = manifest.pruefe_spec(SPEC_OHNE_PROTOKOLL, {"regularien": {"unbeaufsichtigt": False}})
    assert weich.unbeaufsichtigt is True
    assert weich.zustimmung_noetig is False
    assert weich.darf_schneiden is True
    assert len(weich.warnungen) == 2

    monkeypatch.setenv("TO_SPAWN_UNBEAUFSICHTIGT", "0")
    hart = manifest.pruefe_spec(SPEC_OHNE_PROTOKOLL, {"regularien": {"unbeaufsichtigt": True}})
    assert hart.unbeaufsichtigt is False
    assert hart.zustimmung_noetig is True
    assert hart.darf_schneiden is False
    assert len(hart.fehler) == 2

    # Ungültiger Env-Wert fällt fail-closed auf Konfig zurück (mit Warnung).
    caplog.clear()
    monkeypatch.setenv("TO_SPAWN_UNBEAUFSICHTIGT", "vielleicht")
    with caplog.at_level(logging.WARNING, logger="to_spawn.manifest"):
        rueckfall = manifest.pruefe_spec(SPEC_OHNE_PROTOKOLL, {"regularien": {"unbeaufsichtigt": False}})
    assert rueckfall.unbeaufsichtigt is False
    assert rueckfall.darf_schneiden is False
    assert any("TO_SPAWN_UNBEAUFSICHTIGT" in r.message for r in caplog.records)


def test_vollstaendige_spec_besteht_ohne_modus_ohne_warnung(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TO_SPAWN_UNBEAUFSICHTIGT", raising=False)
    ergebnis = manifest.pruefe_spec(SPEC_MIT_PROTOKOLL, {})
    assert ergebnis.darf_schneiden is True
    assert ergebnis.zustimmung_noetig is True
    assert ergebnis.fehler == []
    assert ergebnis.warnungen == []
    assert "alles erfüllt" in ergebnis.text()


def test_cli_spec_pruefen_datei_und_issue_mit_und_ohne_modus(tmp_path: Path) -> None:
    """Echter CLI-Durchlauf ``to_spawn.py spec-pruefen`` mit Datei und mit GitHub-Issue-Stub."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    spec_datei = repo / "spec.md"
    spec_datei.write_text(SPEC_OHNE_PROTOKOLL, encoding="utf-8")

    # 1. Ohne Modus: Exit 3 (hart).
    hart = _cli(repo, "spec-pruefen", str(spec_datei))
    assert hart.returncode == manifest.EXIT_WEIGERUNG, hart.stdout + hart.stderr
    assert "FEHLER" in hart.stdout and "STOPP" in hart.stdout

    # 2. Mit Umgebungsvariable: Exit 0 + Warnung.
    env_lauf = _cli(
        repo,
        "spec-pruefen",
        str(spec_datei),
        env_extra={"TO_SPAWN_UNBEAUFSICHTIGT": "1"},
    )
    assert env_lauf.returncode == 0, env_lauf.stdout + env_lauf.stderr
    assert "unbeaufsichtigt" in env_lauf.stdout
    assert "Warnung" in env_lauf.stdout
    assert "FEHLER" not in env_lauf.stdout

    # 3. Mit Repo-Konfig und GitHub-Issue (über Stub): Exit 0 + Warnung.
    konfig_pfad = repo / config.KONFIG_PFAD
    konfig_pfad.parent.mkdir(parents=True)
    konfig_pfad.write_text(
        json.dumps({"regularien": {"unbeaufsichtigt": True}}),
        encoding="utf-8",
    )
    stub = tmp_path / "gh_issue_stub.py"
    stub.write_text(
        f"""
import json, sys
if sys.argv[1:3] == ["issue", "view"]:
    print(json.dumps({{"state": "OPEN", "labels": [], "assignees": [], "body": {SPEC_OHNE_PROTOKOLL!r}}}))
    sys.exit(0)
sys.exit(1)
""".lstrip(),
        encoding="utf-8",
    )
    issue_lauf = _cli(
        repo,
        "spec-pruefen",
        "42",
        env_extra={"TO_SPAWN_GH_STUB": str(stub)},
    )
    assert issue_lauf.returncode == 0, issue_lauf.stdout + issue_lauf.stderr
    assert "Warnung" in issue_lauf.stdout
    assert "unbeaufsichtigt" in issue_lauf.stdout


def test_to_tickets_skill_dokumentiert_modus_unbeaufsichtigt() -> None:
    """``kette/skills/to-tickets/SKILL.md`` beschreibt Modus unbeaufsichtigt für Protokoll + Zustimmung."""
    text = TO_TICKETS.read_text(encoding="utf-8")
    assert "unbeaufsichtigt" in text
    assert "TO_SPAWN_UNBEAUFSICHTIGT" in text
    assert "regularien.unbeaufsichtigt" in text
    assert "to_spawn.py spec-pruefen" in text
    # Ohne den Modus bleiben harte Prüfung und Zustimmung erhalten.
    assert "Vor der Zustimmung nichts veröffentlichen" in text
    assert "erst `/to-spec` aus dem Protokoll" in text
