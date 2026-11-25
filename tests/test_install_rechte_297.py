"""#297: ``install.sh`` prüft Besitzer und Schreibrecht aller Zielordner vor jeder Änderung.

Gehört ein Zielordner (z. B. ``~/.claude/skills`` oder ``~/.claude/hooks``) einem fremden
Nutzer (etwa ``root``) oder fehlt das Schreibrecht, bricht ``install.sh`` vor dem ersten
Verschieben/Kopieren mit klarer Meldung ab (Ordner, Besitzer, Behebungsbefehl).
Läuft der Installer selbst als ``root`` (wie in ``nest/nest_server.sh``), bleibt das erlaubt.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
NUR_UNIX = pytest.mark.skipif(
    sys.platform == "win32" or BASH is None,
    reason="POSIX-Besitzer-/Schreibrechte-Prüfung nur unter Unix mit bash",
)


def _env(heim: Path, *, extra_bin: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    pfad = env.get("PATH", "")
    if extra_bin is not None:
        pfad = f"{extra_bin}{os.pathsep}{pfad}"
    env.update(
        {
            "HOME": str(heim),
            "USERPROFILE": str(heim),
            "XDG_CONFIG_HOME": str(heim / ".config"),
            "PYTHONIOENCODING": "utf-8",
            "TO_SPAWN_PY": sys.executable,
            "PATH": pfad,
        }
    )
    return env


def _install_sh(heim: Path, *, extra_bin: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH or "bash", str(SKILL / "install.sh")],
        env=_env(heim, extra_bin=extra_bin),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )


def _ausfuehrbar(pfad: Path, inhalt: str) -> None:
    pfad.write_text(inhalt, encoding="utf-8")
    pfad.chmod(pfad.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_install_sh_prueft_zielrechte_vor_erstem_kopieren() -> None:
    """Struktur-Prüfung (läuft auch unter Windows): Vorab-Prüfung steht vor ``sichere_und_kopiere``."""
    text = (SKILL / "install.sh").read_text(encoding="utf-8")
    assert "pruefe_zielrechte" in text
    assert "stat -c" in text and "id -u" in text and "chown" in text
    assert text.index("pruefe_zielrechte") < text.index("sichere_und_kopiere to-spawn")


@NUR_UNIX
def test_schreibgeschuetzter_skills_ordner_bricht_ab_ohne_zu_verschieben(tmp_path: Path) -> None:
    skills = tmp_path / ".claude" / "skills"
    alt = skills / "to-spawn"
    alt.mkdir(parents=True)
    marker = alt / "bestand.txt"
    marker.write_text("alter Stand\n", encoding="utf-8")

    skills.chmod(0o555)
    try:
        lauf = _install_sh(tmp_path)
    finally:
        skills.chmod(0o755)

    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode != 0, ausgabe
    assert marker.read_text(encoding="utf-8") == "alter Stand\n"
    assert not (skills / "_alt").exists()
    assert str(skills) in ausgabe
    assert "chmod" in ausgabe


@NUR_UNIX
def test_schreibgeschuetzter_hooks_ordner_bricht_vor_skills_verschiebung_ab(tmp_path: Path) -> None:
    """``skills`` ist beschreibbar, aber ``hooks`` nicht → Abbruch vor jeder Änderung an ``skills``."""
    skills = tmp_path / ".claude" / "skills"
    alt = skills / "to-spawn"
    alt.mkdir(parents=True)
    marker = alt / "bestand.txt"
    marker.write_text("unveraendert\n", encoding="utf-8")
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)

    hooks.chmod(0o555)
    try:
        lauf = _install_sh(tmp_path)
    finally:
        hooks.chmod(0o755)

    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode != 0, ausgabe
    assert marker.read_text(encoding="utf-8") == "unveraendert\n"
    assert not (skills / "_alt").exists()
    assert str(hooks) in ausgabe
    assert "chmod" in ausgabe


@NUR_UNIX
def test_fremder_besitzer_root_meldet_alle_ordner_und_chown_ohne_aenderung(tmp_path: Path) -> None:
    """Simuliert root-eigene ``skills`` und ``hooks`` ohne echte root-Rechte."""
    skills = tmp_path / ".claude" / "skills"
    alt = skills / "to-spawn"
    alt.mkdir(parents=True)
    marker = alt / "bestand.txt"
    marker.write_text("bleibt\n", encoding="utf-8")
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)

    echtes_stat = shutil.which("stat")
    assert echtes_stat
    attrappe = tmp_path / "bin"
    attrappe.mkdir()
    _ausfuehrbar(
        attrappe / "stat",
        f"""#!/usr/bin/env bash
ziel="${{@: -1}}"
if [ "$ziel" = "{skills}" ] || [ "$ziel" = "{hooks}" ]; then
  echo "0 root:root"
  exit 0
fi
exec "{echtes_stat}" "$@"
""",
    )

    lauf = _install_sh(tmp_path, extra_bin=attrappe)
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode != 0, ausgabe
    assert marker.read_text(encoding="utf-8") == "bleibt\n"
    assert not (skills / "_alt").exists()
    assert str(skills) in ausgabe and str(hooks) in ausgabe
    assert "root:root" in ausgabe
    assert "sudo chown -R" in ausgabe


@NUR_UNIX
def test_aufruf_als_root_darf_fremde_nutzer_ordner_installieren(tmp_path: Path) -> None:
    """``nest/nest_server.sh`` ruft ``install.sh`` als root auf und setzt ``chown`` danach."""
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    echtes_id = shutil.which("id")
    echtes_stat = shutil.which("stat")
    assert echtes_id and echtes_stat
    attrappe = tmp_path / "bin"
    attrappe.mkdir()
    _ausfuehrbar(
        attrappe / "id",
        f"""#!/usr/bin/env bash
case "${{1:-}}" in
  -u) echo 0; exit 0 ;;
  -un) echo root; exit 0 ;;
  -gn) echo root; exit 0 ;;
esac
exec "{echtes_id}" "$@"
""",
    )
    _ausfuehrbar(
        attrappe / "stat",
        f"""#!/usr/bin/env bash
ziel="${{@: -1}}"
if [ "$ziel" = "{skills}" ]; then
  echo "1000 nutzer:nutzer"
  exit 0
fi
exec "{echtes_stat}" "$@"
""",
    )

    lauf = _install_sh(tmp_path, extra_bin=attrappe)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert (skills / "to-spawn" / "SKILL.md").is_file()
