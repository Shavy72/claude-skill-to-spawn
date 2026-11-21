"""L1 E2-1 (#85): Kette-Regeln und die Skills grill-with-docs/to-spec kommen mit dem Installer.

Ein Repo ohne eigene Kopie bekommt so die volle Kette: ``kette/skills/*`` landen unter
``<skills>/<name>``, die Regel-Blöcke aus ``kette/global/*.md`` als Marker-Blöcke in der
globalen ``CLAUDE.md``. Beide Installer laufen gegen ein Wegwerf-HOME (nie das echte).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL))

from to_spawn import globale_regeln

MARKER = "<!-- pflicht:beschluss-protokoll -->"
# Pflicht-Bausteine wie im App-Test test_beschluss_protokoll_skills.py (to-spec).
PFLICHT_TO_SPEC = (
    "docs/GRILL_",
    "(E<n>)",
    "Offene Fragen",
    "Gegenprüfung",
    "Quelle: docs/GRILL_",
)
REGELN = ("beschluss-protokoll", "strategisches-design")
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def _umgebung(heim: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {"HOME": str(heim), "USERPROFILE": str(heim), "PYTHONIOENCODING": "utf-8"}
    )
    return env


def _install_sh(heim: Path) -> subprocess.CompletedProcess[str]:
    befehl = [
        "bash",
        str(SKILL / "install.sh"),
        "--skills",
        str(heim / "skills"),
        "--claude-md",
        str(heim / "CLAUDE.md"),
    ]
    return subprocess.run(
        befehl,
        env=_umgebung(heim),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _pruefe_installiert(heim: Path) -> None:
    to_spec = (heim / "skills" / "to-spec" / "SKILL.md").read_text(encoding="utf-8")
    assert MARKER in to_spec
    for baustein in PFLICHT_TO_SPEC:
        assert baustein in to_spec, f"to-spec: Pflicht-Baustein {baustein!r} fehlt"
    assert to_spec.index(MARKER) < to_spec.index("## Process")
    assert ".to-spawn/kette.md" in to_spec, (
        "to-spec: Verweis auf .to-spawn/kette.md fehlt"
    )
    assert "statt des restlichen Texts" not in to_spec, (
        "to-spec: alter Projekt-Vorrang (ersetzt) noch drin"
    )
    grill = (heim / "skills" / "grill-with-docs" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "LOKALER ZUSATZ" in grill and "Architektur-Runde" in grill
    claude_md = (heim / "CLAUDE.md").read_text(encoding="utf-8")
    for name in REGELN:
        inhalt = (
            (SKILL / "kette" / "global" / f"{name}.md")
            .read_text(encoding="utf-8")
            .strip()
        )
        assert (
            f"<!-- global:{name}:start" in claude_md
            and f"<!-- global:{name}:ende -->" in claude_md
        )
        assert inhalt in claude_md
    assert globale_regeln.pruefe(heim / "CLAUDE.md")


def test_install_sh_legt_kette_an_und_zweiter_lauf_aendert_nichts(
    tmp_path: Path,
) -> None:
    (tmp_path / "CLAUDE.md").write_text(
        "# Eigene Regeln\n\nbleibt stehen\n", encoding="utf-8"
    )
    lauf = _install_sh(tmp_path)
    assert lauf.returncode == 0, lauf.stderr
    _pruefe_installiert(tmp_path)
    vorher = (tmp_path / "CLAUDE.md").read_bytes()
    assert vorher.startswith(b"# Eigene Regeln\n\nbleibt stehen\n")
    lauf = _install_sh(tmp_path)
    assert lauf.returncode == 0, lauf.stderr
    assert (tmp_path / "CLAUDE.md").read_bytes() == vorher
    # Alter Skill-Ordner wird verschoben, nie gelöscht (wie bei to-spawn).
    assert list((tmp_path / "skills" / "_alt").glob("to-spec-*"))


def test_install_ps1_legt_kette_an(tmp_path: Path) -> None:
    assert PWSH, "pwsh/powershell fehlt"
    befehl = [
        PWSH,
        "-NoProfile",
        "-File",
        str(SKILL / "install.ps1"),
        "-Skills",
        str(tmp_path / "skills"),
    ]
    befehl += ["-ClaudeMd", str(tmp_path / "CLAUDE.md")]
    lauf = subprocess.run(
        befehl,
        env=_umgebung(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    _pruefe_installiert(tmp_path)


def test_pruefe_erkennt_veralteten_block_und_schreibe_ersetzt_an_ort_und_stelle(
    tmp_path: Path,
) -> None:
    ziel = tmp_path / "CLAUDE.md"
    assert not globale_regeln.pruefe(ziel)
    globale_regeln.schreibe(ziel)
    assert globale_regeln.pruefe(ziel)
    text = ziel.read_text(encoding="utf-8")
    # Alter Stand aus dem App-Repo: andere Quelle in der Start-Zeile, alter Inhalt.
    alt = text.replace("Tiefe Module", "Flache Module").replace(
        globale_regeln.start_marker("strategisches-design"),
        "<!-- global:strategisches-design:start (Quelle alt/strategisches-design.md) -->",
    )
    ziel.write_text("Kopf\n\n" + alt + "\nFuß\n", encoding="utf-8")
    assert not globale_regeln.pruefe(ziel)
    cli = [
        sys.executable,
        "-m",
        "to_spawn.globale_regeln",
        "--ziel",
        str(ziel),
        "--pruefen",
    ]
    assert (
        subprocess.run(cli, cwd=SKILL, capture_output=True, check=False).returncode == 1
    )
    globale_regeln.schreibe(ziel)
    neu = ziel.read_text(encoding="utf-8")
    assert globale_regeln.pruefe(ziel)
    assert neu.startswith("Kopf\n\n") and neu.endswith("\nFuß\n")
    assert neu.count("<!-- global:strategisches-design:start") == 1
    assert "Flache Module" not in neu
    assert (
        subprocess.run(cli, cwd=SKILL, capture_output=True, check=False).returncode == 0
    )
