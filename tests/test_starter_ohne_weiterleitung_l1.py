"""Etappe 1, Paket P4a: Starter ohne Weiterleitung im App-Repo (Fundstellen 19, 21).

Ein Repo ohne ``scripts/spawn_srv.sh`` bzw. ``scripts/sessions_stand.py`` startet über den Fern-Befehl
direkt das Skill-Skript (``~/.claude/skills/to-spawn/skripte/…``, ``TO_SPAWN_REPO`` = Repo); mit
Weiterleitung läuft der Befehl unverändert über sie (DuoPlus). Echt läuft: der erzeugte Fern-Befehl in
einer echten ``bash`` mit Wegwerf-``HOME`` (statt SSH), die Skripte sind kleine Stellvertreter.
"""

from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import spawn, startklar, umzug, waechter_lauf  # noqa: E402


@pytest.fixture
def welt(tmp_path: Path) -> dict[str, Path]:
    """Wegwerf-HOME mit Skill-Stellvertretern und Server-Repo ``~/r`` ohne ``scripts/``."""
    home = tmp_path / "home"
    skripte = home / ".claude" / "skills" / "to-spawn" / "skripte"
    skripte.mkdir(parents=True)
    (skripte / "spawn_srv.sh").write_text('echo "SKILL repo=$TO_SPAWN_REPO args=$*"\n', encoding="utf-8")
    (skripte / "sessions_stand.py").write_text(
        "import os\nprint('Ticket Zustand')\nprint('#9121       läuft seit 12:00   pid 1   ' + os.environ['TO_SPAWN_REPO'])\n",
        encoding="utf-8",
    )
    repo = home / "r"
    repo.mkdir()
    return {"home": home, "repo": repo}


def _bash(welt: dict[str, Path], befehl: str) -> subprocess.CompletedProcess[str]:
    """Fern-Befehl in echter ``bash`` mit Wegwerf-``HOME``.

    Ohne ``MSYS_NO_PATHCONV``/``MSYS2_ARG_CONV_EXCL``: Git-Bash muss ``~/…``-Pfade für das native
    Windows-Python umwandeln (sonst ``/tmp/…`` → ``C:/tmp/…``); Linux kennt beide nicht.
    """
    weg = {"TO_SPAWN_REPO", "MSYS_NO_PATHCONV", "MSYS2_ARG_CONV_EXCL"}
    umgebung = {k: v for k, v in os.environ.items() if k not in weg}
    return subprocess.run(
        ["bash", "-c", befehl],
        env={**umgebung, "HOME": str(welt["home"])},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _bash_pfad(welt: dict[str, Path], pfad: Path) -> str:
    """``pfad``, wie die Fern-``bash`` ihn als ``$PWD`` sieht (Linux: unverändert, Git-Bash: ``/tmp/…``)."""
    lauf = _bash(welt, f"cd {shlex.quote(str(pfad))} && pwd")
    assert lauf.returncode == 0, lauf.stderr
    return lauf.stdout.strip()


def _server_befehl(monkeypatch: pytest.MonkeyPatch, *args: object) -> list[str]:
    """``spawn.baue_befehl`` im Server-Zweig (``_befehl_unix``) — der Fern-Befehl läuft auf Linux."""
    with monkeypatch.context() as m:
        m.setattr(sys, "platform", "linux")
        return spawn.baue_befehl(*args)  # type: ignore[arg-type]


def _weiterleitung(welt: dict[str, Path], name: str, text: str) -> None:
    (welt["repo"] / "scripts").mkdir(exist_ok=True)
    (welt["repo"] / "scripts" / name).write_text(text, encoding="utf-8")


def test_spawn_srv_ohne_weiterleitung_ruft_skill_skript(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    befehl = _server_befehl(monkeypatch, "srv", 42, ["3"], {"server_repo": "~/r", "ssh_ziel": "x"}, True)
    assert befehl[:2] == ["ssh", "x"]
    assert "~/.claude/skills/to-spawn/skripte/spawn_srv.sh" in befehl[-1]
    lauf = _bash(welt, befehl[-1])
    assert lauf.returncode == 0, lauf.stderr
    assert lauf.stdout.strip() == f"SKILL repo={_bash_pfad(welt, welt['repo'])} args=42 --tickets 3 --dry-run"


def test_spawn_srv_mit_weiterleitung_bleibt_wie_bisher(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _weiterleitung(welt, "spawn_srv.sh", 'echo "WEITERLEITUNG args=$*"; exit 4\n')
    befehl = _server_befehl(monkeypatch, "srv", 42, None, {"server_repo": "~/r"}, False)
    assert "cd ~/r && bash scripts/spawn_srv.sh 42" in befehl[-1]
    lauf = _bash(welt, befehl[-1])
    assert (lauf.returncode, lauf.stdout.strip()) == (4, "WEITERLEITUNG args=42")


def test_umzug_start_ohne_weiterleitung_ruft_skill_skript(welt: dict[str, Path]) -> None:
    lauf = _bash(welt, umzug.spawn_befehl("9120", "9121", "~/r", umzug_ref="ticket/9121"))
    assert lauf.returncode == 0, lauf.stderr
    assert lauf.stdout.strip() == (
        f"SKILL repo={_bash_pfad(welt, welt['repo'])} args=9120 --tickets 9121 --ohne-wache --umzug ticket/9121"
    )


def test_umzug_server_stand_ohne_weiterleitung(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    befehle: list[str] = []

    def ssh(_ziel: str, befehl: str, _timeout: float) -> subprocess.CompletedProcess[str]:
        befehle.append(befehl)
        if "tmux list-windows" in befehl:
            return subprocess.CompletedProcess([], 0, "bau 9121\n", "")
        return _bash(welt, befehl)

    monkeypatch.setattr(umzug, "_ssh", ssh)
    assert umzug.server_laeuft("9120", "9121", ziel="x", ordner="~/r") is True
    assert "~/.claude/skills/to-spawn/skripte/sessions_stand.py" in befehle[-1]


def test_aufseher_prompt_nennt_skill_skripte() -> None:
    datei = SKILL / "skripte" / "wache.py"
    spec = importlib.util.spec_from_file_location("wache_l1_p4a", datei)
    assert spec and spec.loader
    wache: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wache)
    text = wache.prompt_bauen(900, "x/y", 1800, {})
    skill = startklar.skill_pfad()
    assert f" {skill}/skripte/capo.py 900 --katalog" in text
    assert f" {skill}/skripte/sessions_stand.py 900 --alle" in text


def test_waechter_weiter_text_nennt_skill_capo() -> None:
    assert waechter_lauf.capo_aufruf(7) == f"python {startklar.skill_pfad()}/skripte/capo.py 7"
    quelle = (SKILL / "to_spawn" / "waechter_lauf.py").read_text(encoding="utf-8")
    assert "python scripts/capo.py" not in quelle
