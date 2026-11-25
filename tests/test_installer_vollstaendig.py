"""Beide Installer kopieren jeden Eintrag der Skill-Wurzel.

Vorfall 2026-10-05: ``leitstand/`` (Vorlage ``seite.html`` für
``skripte/leitstand_fenster.py``) fehlte in den Kopierlisten von ``install.ps1``
und ``install.sh`` — nach jeder Installation fehlte die Leitstand-Seite.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
# Bewusst nicht installiert: Git-Steuerdatei, Review-Ablage und ``messung/`` (Entwicklungs-Ordner:
# Mess-Skripte des Prüftor-Vergleichs #81, nur Linux, kein Laufzeit-Code des Skills).
# ``conftest.py``: nur Testsammlung, #214 Teil A; Teil B entfernt die Datei wieder.
# ``review`` (#329 P2): wird mitkopiert und von ``to_spawn/review_hook.py`` nach ~/.claude/review eingespielt.
# ``probe`` ist nicht mehr ausgenommen (#466): wird mitinstalliert, damit ``probe/anlegen.sh`` auch
# aus der installierten Kopie läuft.
# ``.github`` (#537): GitHub-Actions-Workflow, nur für die Prüfung im Repo.
NICHT_INSTALLIERT = {
    ".github",
    ".gitignore",
    ".gitattributes",
    ".review",
    "messung",
    "conftest.py",
}


def _wurzel_eintraege() -> set[str]:
    raus = subprocess.run(
        ["git", "ls-tree", "--name-only", "HEAD"],
        cwd=SKILL,
        capture_output=True,
        text=True,
        check=False,
    )
    if raus.returncode == 0 and raus.stdout.strip():
        return set(raus.stdout.split()) - NICHT_INSTALLIERT
    # Installierte Kopie ohne Git: Ordnerinhalt ohne Caches.
    return {p.name for p in SKILL.iterdir() if not p.name.startswith((".", "__"))}


def _kopiert_ps1() -> set[str]:
    text = (SKILL / "install.ps1").read_text(encoding="utf-8")
    liste = re.search(r"foreach \(\$f in @\((.*?)\)\)", text, re.DOTALL)
    assert liste, "Kopierliste in install.ps1 nicht gefunden"
    return set(re.findall(r'"([^"]+)"', liste.group(1)))


def _kopiert_sh() -> set[str]:
    text = (SKILL / "install.sh").read_text(encoding="utf-8")
    liste = re.search(r"for eintrag in (.*?); do", text, re.DOTALL)
    assert liste, "Kopierliste in install.sh nicht gefunden"
    return set(liste.group(1).replace("\\", " ").split())


def test_install_ps1_kopiert_jeden_wurzel_eintrag() -> None:
    assert _wurzel_eintraege() - _kopiert_ps1() == set()


def test_install_sh_kopiert_jeden_wurzel_eintrag() -> None:
    assert _wurzel_eintraege() - _kopiert_sh() == set()


def test_beide_installer_kopieren_gleiche_wurzel_eintraege() -> None:
    """#329: ``install.sh`` und ``install.ps1`` pflegen dieselbe Kopierliste inkl. ``review``."""
    sh, ps1 = _kopiert_sh(), _kopiert_ps1()
    assert sh == ps1
    assert "review" in sh
