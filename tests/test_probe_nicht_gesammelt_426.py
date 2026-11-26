"""Skill-pytest sammelt unter ``probe/`` nichts ein (#426 Fix-Runde 1, PR 442).

Die Probe-Vorlagen sind Daten: Ihre App-Tests und der absichtlich rote Werkzeug-Test laufen
nur im angelegten Lauf-Repo (geprüft in tests/test_probe_vorlagen_426.py). Lädt pytest ein
``probe/conftest.py`` als Modul ``conftest``, verdeckt es die Wurzel-``conftest.py`` und
~100 Tests mit ``from conftest import attrappe`` brechen beim Sammeln.
"""

from __future__ import annotations

import re
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent


def test_sammlung_an_der_wurzel_ohne_probe(tmp_path: Path) -> None:
    from conftest import wurzel_sammlung

    lauf = wurzel_sammlung(tmp_path)
    schluss = lauf.stdout.strip().splitlines()[-1] if lauf.stdout.strip() else lauf.stderr
    assert lauf.returncode == 0, schluss
    assert not re.search(r"\d+ errors?", schluss), schluss
    aus_probe = [z for z in lauf.stdout.splitlines() if z.startswith("probe/")]
    assert aus_probe == [], aus_probe[:5]
