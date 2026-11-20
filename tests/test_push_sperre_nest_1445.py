"""#1445: ``nest sandbox`` richtet im Klon die master-Push-Sperre aus #1442 ein.

Weg-Test ohne Attrappen: echtes Bare-Repo als ``origin``, echter Klon, echte CLI
``to_spawn.py nest sandbox`` (der Weg, den ``bau.py`` vor jedem Session-Start geht),
echter Installer und echter Hook des Projekt-Repos, echte ``git push``-Aufrufe.
Der Modus ``tickets`` steht wie im Betrieb in der ``.env`` der Schlange; der Hook
liest sie über ``SCHLANGE_ENV_DATEI``. Telegram geht über ``WAECHTER_MELDUNG_DATEI``
in eine Datei statt ins Netz.

Die beiden Hook-Dateien unter ``tests/hilfen/push_sperre_1442/`` sind Testdaten:
Stand ``Shavy72/duoplus-management`` origin/master 6f3f961 (``scripts/hooks/``).
Der Skill selbst kopiert nichts, er ruft nur den Installer im Klon auf.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from tests.test_nest_210 import _cli, _git, _neues_repo, _umgebung

HILFEN = Path(__file__).parent / "hilfen" / "push_sperre_1442"


def _welt(tmp_path: Path, mit_installer: bool = True) -> tuple[Path, Path]:
    """Bare-origin mit (oder ohne) Hook-Dateien, daneben ein Hauptklon. Rückgabe (klon, heim)."""
    quelle = _neues_repo(tmp_path / "quelle")
    if mit_installer:
        hooks = quelle / "scripts" / "hooks"
        hooks.mkdir(parents=True)
        for name in ("install_git_hooks.sh", "pre-push-master-sperre.sh"):
            shutil.copy(HILFEN / name, hooks / name)
        _git(quelle, "add", "-A")
        _git(quelle, "commit", "-m", "Push-Sperre (#1442)")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(quelle), str(origin))
    klon = tmp_path / "klon"
    _git(tmp_path, "clone", "-q", str(origin), str(klon))
    _git(klon, "config", "user.email", "test@example.invalid")
    _git(klon, "config", "user.name", "Test")
    heim = tmp_path / "heim"
    heim.mkdir()
    return klon, heim


def _nest_sandbox(tmp_path: Path, klon: Path, heim: Path) -> subprocess.CompletedProcess[str]:
    return _cli(
        "sandbox",
        "1445",
        "--repo",
        str(klon),
        cwd=klon,
        env=_umgebung(heim, BAU_WT_DIR=str(tmp_path / "wt")),
    )


def _push(wt: Path, tmp_path: Path, ziel: str) -> subprocess.CompletedProcess[str]:
    env_datei = tmp_path / "schlange.env"
    env_datei.write_text("SCHLANGE_MODUS=tickets\n", encoding="utf-8")
    umgebung = _umgebung(
        tmp_path / "heim",
        SCHLANGE_ENV_DATEI=str(env_datei),
        WAECHTER_MELDUNG_DATEI=str(tmp_path / "meldungen.jsonl"),
    )
    for name in ("MASTER_PUSH_ERLAUBT", "DEPLOY_SCHLANGE_MERGE", "SCHLANGE_MELDUNG_DATEI"):
        umgebung.pop(name, None)
    return subprocess.run(
        ["git", "push", "origin", f"HEAD:{ziel}"],
        cwd=str(wt),
        env=umgebung,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_frisches_nest_sperrt_master_push_im_modus_tickets(tmp_path: Path) -> None:
    klon, heim = _welt(tmp_path)
    ergebnis = _nest_sandbox(tmp_path, klon, heim)
    assert ergebnis.returncode == 0, ergebnis.stderr
    wt = Path(ergebnis.stdout.strip()).parent.parent  # <worktree>/.to-spawn/sandbox.json
    (wt / "arbeit.txt").write_text("neu\n", encoding="utf-8")
    _git(wt, "add", "arbeit.txt")
    _git(wt, "commit", "-m", "Arbeit")

    auf_master = _push(wt, tmp_path, "refs/heads/master")
    assert auf_master.returncode != 0, f"Push auf master ging durch:\n{auf_master.stderr}"
    assert "SCHLANGE_MODUS=tickets" in auf_master.stderr
    assert "Arbeit" not in _git(klon, "log", "-1", "--format=%s", "origin/master")

    auf_ticket = _push(wt, tmp_path, "refs/heads/ticket/1445-probe")
    assert auf_ticket.returncode == 0, auf_ticket.stderr


def test_klon_ohne_installer_startet_trotzdem(tmp_path: Path) -> None:
    """Alter Projekt-Stand ohne Installer: Sandbox-Datei entsteht, klare Log-Zeile, kein Abbruch."""
    klon, heim = _welt(tmp_path, mit_installer=False)
    ergebnis = _nest_sandbox(tmp_path, klon, heim)
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert Path(ergebnis.stdout.strip()).is_file()
    assert "Push-Sperre" in ergebnis.stderr and "fehlt" in ergebnis.stderr
