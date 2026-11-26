"""#329 P2: Installer spielt ``review/`` nach ``~/.claude/review`` ein — eingespielter Ordner = Quelle.

Gleiche Überschreib-Regel wie der Review-Hook (``to_spawn/review_hook.py``): abweichende Dateien
werden vorher gesichert, Laufzeit-Daten (``sitzungen/``, Logs), die nur im Ziel liegen, bleiben.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import review_hook

SKILL = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def _dateien(ordner: Path) -> dict[str, bytes]:
    return {
        p.relative_to(ordner).as_posix(): p.read_bytes()
        for p in ordner.rglob("*")
        if p.is_file() and not {"__pycache__", ".pytest_cache"} & set(p.relative_to(ordner).parts)
    }


def test_werkzeug_quelle_ist_review_ordner() -> None:
    assert review_hook.WERKZEUG_QUELLE == SKILL / "review"


def test_eingespielt_gleich_quelle_laufzeit_bleibt(tmp_path: Path) -> None:
    ziel = tmp_path / "review"
    (ziel / "sitzungen").mkdir(parents=True)
    (ziel / "sitzungen" / "s.json").write_text("{}", encoding="utf-8")
    (ziel / "hook-fehler.log").write_text("alt\n", encoding="utf-8")
    (ziel / "rot_probe.py").write_text("# ältere laufende Fassung\n", encoding="utf-8")

    bericht = review_hook.installiere(ziel, review_hook.WERKZEUG_QUELLE, zeit="t")

    quelle = _dateien(review_hook.WERKZEUG_QUELLE)
    eingespielt = {k: v for k, v in _dateien(ziel).items() if k != review_hook.STAND_DATEI}
    laufzeit = {"sitzungen/s.json", "hook-fehler.log"}
    assert {k: v for k, v in eingespielt.items() if k not in laufzeit} == quelle
    assert laufzeit <= eingespielt.keys()
    gesichert = tmp_path / "_alt" / "review-t" / "rot_probe.py"
    assert gesichert.read_text(encoding="utf-8") == "# ältere laufende Fassung\n"
    assert any("gesichert: rot_probe.py" in z for z in bericht)


def test_main_spielt_hook_und_werkzeug_ein(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert review_hook.main([]) == 0
    assert (tmp_path / ".claude" / "hooks" / "review" / "_hookbasis.py").is_file()
    werkzeug = tmp_path / ".claude" / "review"
    assert {k: v for k, v in _dateien(werkzeug).items() if k != review_hook.STAND_DATEI} == _dateien(
        review_hook.WERKZEUG_QUELLE
    )


def _versionierte_dateien(quelle: Path) -> dict[str, bytes]:
    return {rel: (quelle / rel).read_bytes() for rel in review_hook._quell_dateien(quelle)}


def _installer_env(heim: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(heim),
            "USERPROFILE": str(heim),
            "XDG_CONFIG_HOME": str(heim / ".config"),
            "PYTHONIOENCODING": "utf-8",
            "TO_SPAWN_PY": sys.executable,
        }
    )
    return env


def _installer_lauf(art: str, heim: Path) -> subprocess.CompletedProcess[str]:
    skills = heim / ".claude" / "skills"
    claude_md = heim / ".claude" / "CLAUDE.md"
    if art == "sh":
        befehl = [BASH or "bash", str(SKILL / "install.sh"), "--skills", str(skills), "--claude-md", str(claude_md)]
    else:
        befehl = [
            PWSH or "pwsh",
            "-NoProfile",
            "-File",
            str(SKILL / "install.ps1"),
            "-Skills",
            str(skills),
            "-ClaudeMd",
            str(claude_md),
        ]
    return subprocess.run(
        befehl,
        env=_installer_env(heim),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )


def test_pruefe_erkennt_veraltete_und_fehlende_datei(tmp_path: Path) -> None:
    """#329: ``pruefe`` meldet veraltete/fehlende Dateien; CRLF und Laufzeit-Daten stören nicht."""
    ziel = tmp_path / "review"
    review_hook.installiere_werkzeug(ziel, review_hook.WERKZEUG_QUELLE, zeit="t")
    (ziel / "sitzungen").mkdir()
    (ziel / "sitzungen" / "s.json").write_text("{}", encoding="utf-8")
    (ziel / "regeln.md").write_text("# privat\n", encoding="utf-8")
    assert review_hook.pruefe(ziel, review_hook.WERKZEUG_QUELLE) == []

    # CRLF-Unterschied allein gilt als gleich (wie bei installiere).
    rot_probe = ziel / "rot_probe.py"
    rot_probe.write_bytes((review_hook.WERKZEUG_QUELLE / "rot_probe.py").read_bytes().replace(b"\n", b"\r\n"))
    assert review_hook.pruefe(ziel, review_hook.WERKZEUG_QUELLE) == []

    # Veraltete rot_probe.py + fehlende review_route.py werden gemeldet.
    rot_probe.write_text("# alter Stand\n", encoding="utf-8")
    (ziel / "review_route.py").unlink()
    abweichungen = review_hook.pruefe(ziel, review_hook.WERKZEUG_QUELLE)
    assert "abweichend: rot_probe.py" in abweichungen
    assert "fehlt: review_route.py" in abweichungen

    with pytest.raises(FileNotFoundError):
        review_hook.pruefe(ziel, tmp_path / "fehlt")


def test_main_pruefen_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#329: ``--pruefen`` liefert Exit 0 wenn aktuell, Exit 1 bei Drift — ohne Dateien zu ändern."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert review_hook.main(["--klon", str(SKILL)]) == 0
    assert review_hook.main(["--pruefen", "--klon", str(SKILL)]) == 0

    rot_probe = tmp_path / ".claude" / "review" / "rot_probe.py"
    rot_probe.write_text("# zurückgesetzter Stand\n", encoding="utf-8")
    assert review_hook.main(["--pruefen", "--klon", str(SKILL)]) == 1
    assert rot_probe.read_text(encoding="utf-8") == "# zurückgesetzter Stand\n"


@pytest.mark.parametrize(
    "art",
    [
        pytest.param("sh", marks=pytest.mark.skipif(not BASH, reason="bash fehlt")),
        pytest.param("ps1", marks=pytest.mark.skipif(not PWSH, reason="pwsh fehlt")),
    ],
)
def test_installer_spielt_review_werkzeug_vollstaendig_ein(art: str, tmp_path: Path) -> None:
    """#329: ``install.sh`` und ``install.ps1`` bringen ``~/.claude/review`` und Skill-Kopie auf Quellen-Stand."""
    werkzeug = tmp_path / ".claude" / "review"
    (werkzeug / "sitzungen").mkdir(parents=True)
    (werkzeug / "sitzungen" / "s.json").write_text('{"id": 1}', encoding="utf-8")
    (werkzeug / "hook-fehler.log").write_text("alt\n", encoding="utf-8")
    (werkzeug / "rot_probe.py").write_text("# veraltete Fassung\n", encoding="utf-8")

    lauf = _installer_lauf(art, tmp_path)
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "Review-Werkzeug installiert" in ausgabe

    quelle = _versionierte_dateien(review_hook.WERKZEUG_QUELLE)
    eingespielt = {k: v for k, v in _dateien(werkzeug).items() if k != review_hook.STAND_DATEI}
    laufzeit = {"sitzungen/s.json", "hook-fehler.log"}
    assert {k: v for k, v in eingespielt.items() if k not in laufzeit} == quelle
    assert laufzeit <= eingespielt.keys()
    assert review_hook.pruefe(werkzeug, review_hook.WERKZEUG_QUELLE) == []

    gesichert = list((tmp_path / ".claude" / "_alt").glob("review-*/rot_probe.py"))
    assert len(gesichert) == 1 and gesichert[0].read_text(encoding="utf-8") == "# veraltete Fassung\n"

    skill_kopie = tmp_path / ".claude" / "skills" / "to-spawn" / "review"
    for rel, soll in quelle.items():
        assert (skill_kopie / rel).read_bytes() == soll, rel

    pruef = subprocess.run(
        [sys.executable, "-m", "to_spawn.review_hook", "--pruefen", "--klon", str(SKILL)],
        cwd=SKILL,
        env=_installer_env(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert pruef.returncode == 0, pruef.stdout + pruef.stderr


def test_review_tests_laufen_in_wurzel_suite_mit(tmp_path: Path) -> None:
    """#329: Alle Tests unter ``review/tests/`` werden im Suite-Lauf ab Repo-Wurzel gesammelt."""
    from conftest import wurzel_sammlung

    lauf = wurzel_sammlung(tmp_path)
    schluss = lauf.stdout.strip().splitlines()[-1] if lauf.stdout.strip() else lauf.stderr
    assert lauf.returncode == 0, schluss
    assert not re.search(r"\d+ errors?", schluss), schluss

    gesammelte_dateien = {
        zeile.split("::", 1)[0].replace("\\", "/")
        for zeile in lauf.stdout.splitlines()
        if zeile.startswith(("review/tests/", "review\\tests\\"))
    }
    soll_dateien = {f"review/tests/{p.name}" for p in (SKILL / "review" / "tests").glob("test_*.py")}
    assert soll_dateien and soll_dateien <= gesammelte_dateien, sorted(soll_dateien - gesammelte_dateien)

    suite_zeilen = [
        z.strip()
        for z in (SKILL / "skripte" / "suite.sh").read_text(encoding="utf-8").splitlines()
        if z.lstrip().startswith("exec ")
    ]
    pytest_zeilen = [z for z in suite_zeilen if "python -m pytest " in z]
    assert len(pytest_zeilen) == 1 and "pytest tests" not in pytest_zeilen[0], suite_zeilen
    assert all(z == pytest_zeilen[0] or "skripte/suite.py" in z for z in suite_zeilen), suite_zeilen
    if any("skripte/suite.py" in z for z in suite_zeilen):
        from skripte import suite as suite_laeufer

        suite_py_dateien = {Path(p).as_posix() for p in suite_laeufer.sammle_dateien(SKILL, [])}
        assert soll_dateien <= suite_py_dateien, sorted(soll_dateien - suite_py_dateien)


def test_gitattributes_haelt_review_auf_lf() -> None:
    """#329: ``review/`` bleibt im Windows-Klon auf LF (wie ``review-hook/`` in #214)."""
    zeilen = {
        z.strip()
        for z in (SKILL / ".gitattributes").read_text(encoding="utf-8").splitlines()
        if z.strip() and not z.lstrip().startswith("#")
    }
    for muster in ("review/**/*.py text eol=lf", "review/**/*.md text eol=lf", "review/**/*.json text eol=lf"):
        assert muster in zeilen, f"fehlt in .gitattributes: {muster}"
