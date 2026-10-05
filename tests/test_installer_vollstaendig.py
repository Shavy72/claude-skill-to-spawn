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
# Bewusst nicht installiert: Git-Steuerdatei und Review-Ablage.
NICHT_INSTALLIERT = {".gitignore", ".review"}


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


def test_install_ps1_kopiert_jeden_wurzel_eintrag() -> None:
    text = (SKILL / "install.ps1").read_text(encoding="utf-8")
    liste = re.search(r"foreach \(\$f in @\((.*?)\)\)", text, re.S)
    assert liste, "Kopierliste in install.ps1 nicht gefunden"
    kopiert = set(re.findall(r'"([^"]+)"', liste.group(1)))
    assert _wurzel_eintraege() - kopiert == set()


def test_install_sh_kopiert_jeden_wurzel_eintrag() -> None:
    text = (SKILL / "install.sh").read_text(encoding="utf-8")
    liste = re.search(r"for eintrag in (.*?); do", text, re.S)
    assert liste, "Kopierliste in install.sh nicht gefunden"
    kopiert = set(liste.group(1).replace("\\", " ").split())
    assert _wurzel_eintraege() - kopiert == set()
