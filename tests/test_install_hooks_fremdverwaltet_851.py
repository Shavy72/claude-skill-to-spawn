"""#851: ``install.sh`` überspringt absichtlich root-verwalteten Hooks-Ordner (``claude-config-hooks``).

Gehört ``~/.claude/hooks`` einem fremden Nutzer (z. B. ``root``) oder fehlt dort das Schreibrecht
UND ein ``claude-config-hooks``-Dienst oder Marker zeigt die Fremdverwaltung an, installiert
``install.sh`` Skill, Alias-Skills, Kette und Review-Werkzeug trotzdem, überspringt nur die Hooks
mit klarer Meldung und endet mit Exit 0 — ohne ``chown``-Vorschlag dafür.
Fehlt Dienst und Marker, bleibt der Abbruch aus #297 mit ``sudo chown`` erhalten.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import review_hook

SKILL = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
NUR_UNIX = pytest.mark.skipif(
    sys.platform == "win32" or BASH is None,
    reason="POSIX-Besitzer-/Schreibrechte-Prüfung nur unter Unix mit bash",
)


def _env(
    heim: Path,
    *,
    extra_bin: Path | None = None,
    dienst_pfad: Path | None = None,
) -> dict[str, str]:
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
    if dienst_pfad is not None:
        env["TO_SPAWN_HOOKS_DIENST"] = str(dienst_pfad)
    return env


def _install_sh(
    heim: Path,
    *,
    extra_bin: Path | None = None,
    dienst_pfad: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH or "bash", str(SKILL / "install.sh")],
        env=_env(heim, extra_bin=extra_bin, dienst_pfad=dienst_pfad),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=300,
    )


def _ausfuehrbar(pfad: Path, inhalt: str) -> None:
    pfad.write_text(inhalt, encoding="utf-8")
    pfad.chmod(pfad.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_review_hook_ohne_hook_installiert_nur_werkzeug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--ohne-hook`` lässt ``~/.claude/hooks/review`` unberührt und spielt nur ``~/.claude/review`` ein."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "bestehend.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    code = review_hook.main(["--klon", str(SKILL), "--ohne-hook"])
    assert code == 0
    assert not (hooks / "review").exists()
    assert (hooks / "bestehend.sh").is_file()
    assert (tmp_path / ".claude" / "review" / "rot_probe.py").is_file()


@NUR_UNIX
def test_root_verwalteter_hooks_ordner_mit_dienst_wird_uebersprungen(tmp_path: Path) -> None:
    """Fremder Besitzer ``root:root`` + ``claude-config-hooks.service``: Rest wird installiert, Hooks übersprungen."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    bestehend = hooks / "fremd.sh"
    bestehend.write_text("#!/bin/sh\n# von claude-config\n", encoding="utf-8")

    dienst = tmp_path / "claude-config-hooks.service"
    dienst.write_text(
        f"[Unit]\nDescription=claude-config: hooks/ als root nach {hooks} einspielen\n",
        encoding="utf-8",
    )

    echtes_stat = shutil.which("stat")
    assert echtes_stat
    attrappe = tmp_path / "bin"
    attrappe.mkdir()
    _ausfuehrbar(
        attrappe / "stat",
        f"""#!/usr/bin/env bash
ziel="${{@: -1}}"
if [ "$ziel" = "{hooks}" ]; then
  echo "0 root:root"
  exit 0
fi
exec "{echtes_stat}" "$@"
""",
    )

    hooks.chmod(0o555)
    try:
        lauf = _install_sh(tmp_path, extra_bin=attrappe, dienst_pfad=dienst)
    finally:
        hooks.chmod(0o755)

    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "Hooks kommen über claude-config, nicht von hier" in ausgabe
    assert "chown" not in ausgabe
    assert (tmp_path / ".claude" / "skills" / "to-spawn" / "SKILL.md").is_file()
    assert (tmp_path / ".claude" / "skills" / "to-spawn-local" / "SKILL.md").is_file()
    assert (tmp_path / ".claude" / "CLAUDE.md").is_file()
    assert (tmp_path / ".claude" / "review" / "rot_probe.py").is_file()
    assert not (hooks / "review").exists()
    assert bestehend.read_text(encoding="utf-8") == "#!/bin/sh\n# von claude-config\n"


@NUR_UNIX
def test_nicht_schreibbarer_hooks_ordner_mit_marker_wird_uebersprungen(tmp_path: Path) -> None:
    """Nicht schreibbarer Hooks-Ordner + Marker (``config-sync``): Hooks übersprungen, Rest installiert, Exit 0."""
    hooks = tmp_path / ".claude" / "hooks"
    (hooks / "config-sync").mkdir(parents=True)
    kein_dienst = tmp_path / "fehlt.service"

    hooks.chmod(0o555)
    try:
        lauf = _install_sh(tmp_path, dienst_pfad=kein_dienst)
    finally:
        hooks.chmod(0o755)

    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "Hooks kommen über claude-config, nicht von hier" in ausgabe
    assert "chown" not in ausgabe
    assert "chmod" not in ausgabe
    assert (tmp_path / ".claude" / "skills" / "to-spawn" / "SKILL.md").is_file()
    assert (tmp_path / ".claude" / "review" / "rot_probe.py").is_file()
    assert not (hooks / "review").exists()


@NUR_UNIX
def test_fremder_hooks_ordner_ohne_dienst_und_marker_bricht_weiterhin_ab(tmp_path: Path) -> None:
    """Ohne Dienst/Marker bleibt ein root-eigener Hooks-Ordner ein harter Abbruch mit ``sudo chown`` (#297)."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    kein_dienst = tmp_path / "fehlt.service"

    echtes_stat = shutil.which("stat")
    assert echtes_stat
    attrappe = tmp_path / "bin"
    attrappe.mkdir()
    _ausfuehrbar(
        attrappe / "stat",
        f"""#!/usr/bin/env bash
ziel="${{@: -1}}"
if [ "$ziel" = "{hooks}" ]; then
  echo "0 root:root"
  exit 0
fi
exec "{echtes_stat}" "$@"
""",
    )

    lauf = _install_sh(tmp_path, extra_bin=attrappe, dienst_pfad=kein_dienst)
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode != 0, ausgabe
    assert str(hooks) in ausgabe
    assert "sudo chown -R" in ausgabe
    assert not (tmp_path / ".claude" / "skills" / "to-spawn").exists()
