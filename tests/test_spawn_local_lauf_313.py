"""#313: Lauf-Test für spawn_local.ps1 unter Windows.

Der Starter läuft echt (PowerShell) im Trockenlauf gegen ein Probe-Manifest: ein fertig
gebautes Ticket wird nicht gestartet, ein offenes schon. Der Starter liegt dafür als Kopie
in einem Wegwerf-Skill-Ordner, neben ihm eine Attrappe für to_spawn.py (Ticket-Zustand per
`zu-bauen`, kein GitHub). Eine wt-Attrappe vorn im PATH hält fest, falls je ein Fenster
aufginge — im Trockenlauf endet der Starter vor Start-Process.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKRIPT = Path(os.environ.get("SPAWN_LOCAL_SKRIPT") or Path(__file__).resolve().parents[1] / "spawn_local.ps1")
PS = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Lauf-Test für spawn_local.ps1 nur unter Windows"),
    pytest.mark.skipif(PS is None, reason="weder pwsh noch powershell vorhanden"),
]

SPEC = 9100
FERTIG = 9101
OFFEN = 9102

# Attrappe für to_spawn.py: schreibt jeden Aufruf ins Protokoll; `zu-bauen` nennt nur das offene Ticket.
# `config` bedient den Manifest-Ordner-Aufruf des Starters (`from to_spawn import config`).
ATTRAPPE = f"""
import json, os, sys
from pathlib import Path


class config:
    @staticmethod
    def lade(repo):
        return repo

    @staticmethod
    def pfad(repo, name):
        return Path(repo) / "docs" / "agents" / "manifests"


if __name__ == "__main__":
    with open(os.environ["ATTRAPPE_PROTOKOLL"], "a", encoding="utf-8") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
    if sys.argv[1] == "zu-bauen":
        ausgabe = os.environ.get("ATTRAPPE_ZU_BAUEN_AUSGABE", "{OFFEN}")
        if ausgabe:
            print(ausgabe)
        sys.exit(int(os.environ.get("ATTRAPPE_ZU_BAUEN_RC", "0")))
    sys.exit(0)
"""


def _lauf(
    tmp_path: Path,
    *,
    ohne_wache: bool = True,
    zu_bauen_rc: int = 0,
    zu_bauen_ausgabe: str = str(OFFEN),
) -> tuple[subprocess.CompletedProcess[str], list[list[str]], Path]:
    skill = tmp_path / "skill"
    skill.mkdir()
    shutil.copy(SKRIPT, skill / "spawn_local.ps1")
    (skill / "to_spawn.py").write_text(ATTRAPPE, encoding="utf-8")

    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "bau.py").write_text("", encoding="utf-8")
    manifeste = repo / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    (manifeste / f"spec-{SPEC}.json").write_text(
        json.dumps(
            {"spec": SPEC, "feature": "Probe", "tickets": {str(FERTIG): {"title": "A"}, str(OFFEN): {"title": "B"}}}
        ),
        encoding="utf-8",
    )

    attrappen_bin = tmp_path / "bin"
    attrappen_bin.mkdir()
    wt_marke = tmp_path / "wt_aufgerufen.txt"
    (attrappen_bin / "wt.cmd").write_text(f'@echo %* > "{wt_marke}"\r\n', encoding="utf-8")

    protokoll = tmp_path / "protokoll.jsonl"
    env = dict(os.environ)
    env["PATH"] = str(attrappen_bin) + os.pathsep + env.get("PATH", "")
    env["ATTRAPPE_PROTOKOLL"] = str(protokoll)
    env["ATTRAPPE_ZU_BAUEN_RC"] = str(zu_bauen_rc)
    env["ATTRAPPE_ZU_BAUEN_AUSGABE"] = zu_bauen_ausgabe
    env["PYTHONIOENCODING"] = "utf-8"

    cmd = [
        str(PS),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(skill / "spawn_local.ps1"),
        "-Spec",
        str(SPEC),
    ]
    if ohne_wache:
        cmd.append("-OhneWache")
    cmd.append("-DryRun")

    lauf = subprocess.run(
        cmd,
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    aufrufe = [json.loads(z) for z in protokoll.read_text(encoding="utf-8").splitlines()] if protokoll.exists() else []
    return lauf, aufrufe, wt_marke


def test_trockenlauf_startet_nur_das_offene_ticket(tmp_path: Path) -> None:
    lauf, aufrufe, wt_marke = _lauf(tmp_path)
    ausgabe = lauf.stdout + lauf.stderr

    assert lauf.returncode == 0, ausgabe
    assert ["zu-bauen", str(SPEC)] in aufrufe, aufrufe
    assert f"#{FERTIG} fertig gebaut" in ausgabe
    assert "Starte 1 Tabs" in ausgabe
    assert f"bau {OFFEN}" in ausgabe
    assert f"bau {FERTIG}" not in ausgabe
    assert not wt_marke.exists(), "Trockenlauf darf kein Fenster öffnen"


def test_trockenlauf_ruft_pruefer_nur_im_pruefmodus(tmp_path: Path) -> None:
    # Trockenlauf zieht nichts nach und legt kein Einrichtungs-Ticket an (#283, #410).
    _, aufrufe, _ = _lauf(tmp_path)
    assert ["arbeitsstand", "--pruefen"] in aufrufe, aufrufe
    assert ["regularien", str(SPEC), "--dry-run"] in aufrufe, aufrufe


def test_gh_fehler_oeffnet_keine_tabs(tmp_path: Path) -> None:
    """#191: Scheitert ``zu-bauen`` für alle Tickets (gh-Fehler), öffnet spawn_local.ps1 gar keine Tabs."""
    lauf, aufrufe, wt_marke = _lauf(tmp_path, ohne_wache=False, zu_bauen_rc=1, zu_bauen_ausgabe="")
    ausgabe = lauf.stdout + lauf.stderr

    assert lauf.returncode == 2, ausgabe
    assert aufrufe.count(["zu-bauen", str(SPEC)]) == 2, aufrufe
    assert "keine Tabs" in ausgabe, ausgabe
    assert "Starte " not in ausgabe, ausgabe
    assert not wt_marke.exists(), "gh-Fehler darf kein Fenster öffnen"
