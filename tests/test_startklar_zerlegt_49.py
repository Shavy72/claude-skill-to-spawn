"""#49 (App #461): ``to_spawn/startklar.py`` unter der 1000-Zeilen-Grenze zerlegt.

Prüft:
1. ``to_spawn/startklar.py`` sowie die ausgelagerten Module ``startklar_basis.py``,
   ``startklar_schluessel.py`` und ``startklar_werkzeug.py`` existieren und bleiben
   je unter ``datei_groesse.grenze`` aus ``review/config.standard.json``.
2. Die öffentliche Schnittstelle von ``to_spawn.startklar`` bleibt unverändert
   importierbar und nutzbar.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import startklar

#: Ausgelagerte Startklar-Module neben der Fassade ``to_spawn/startklar.py``.
STARTKLAR_MODULE = (
    "to_spawn/startklar.py",
    "to_spawn/startklar_basis.py",
    "to_spawn/startklar_schluessel.py",
    "to_spawn/startklar_werkzeug.py",
)

#: Öffentliche Symbole, die Aufrufer und Tests weiterhin von ``to_spawn.startklar`` beziehen.
OEFFENTLICHE_SYMBOLE = (
    "SKILL",
    "SCHALTER",
    "BEFEHL_AUFRAEUMEN",
    "BEFEHL_NEUSTART",
    "BEFEHL_RESPAWN",
    "WERKZEUGE",
    "Befund",
    "Werkzeug",
    "ManifestKaputt",
    "aufraeumen_pfad",
    "aufseher_vorlage",
    "skill_pfad",
    "befehl_text",
    "hauptrepo",
    "venv_sicherstellen",
    "konfig_pfad",
    "konfig_pruefen",
    "schluessel_behebung_befehl",
    "schluessel_pruefen",
    "standard_settings_dateien",
    "werkzeug_probe",
    "pruefe",
    "ausgabe",
    "gate",
)


def _projektgrenze() -> int:
    """Liest ``datei_groesse.grenze`` aus der einen Quelle ``review/config.standard.json``."""
    cfg = json.loads((SKILL / "review" / "config.standard.json").read_text(encoding="utf-8"))
    return int(cfg["datei_groesse"]["grenze"])


def _zeilen(pfad: Path) -> int:
    return len(pfad.read_text(encoding="utf-8").splitlines())


@pytest.mark.parametrize("relpfad", STARTKLAR_MODULE)
def test_startklar_module_existieren_und_bleiben_unter_grenze(relpfad: str) -> None:
    """``startklar.py`` und die Teilmodule existieren und bleiben je unter der Projektgrenze."""
    pfad = SKILL / relpfad
    assert pfad.is_file(), f"Modul fehlt: {relpfad}"
    grenze = _projektgrenze()
    zeilen = _zeilen(pfad)
    assert zeilen < grenze, f"{relpfad} hat {zeilen} Zeilen (Grenze: {grenze})"


def test_startklar_oeffentliche_schnittstelle_vollstaendig() -> None:
    """Alle öffentlichen Symbole bleiben auf ``to_spawn.startklar`` erreichbar."""
    fehlend = [name for name in OEFFENTLICHE_SYMBOLE if not hasattr(startklar, name)]
    assert fehlend == [], f"Fehlende Symbole auf to_spawn.startklar: {fehlend}"


@pytest.mark.parametrize(
    "modulname",
    ("to_spawn.startklar_basis", "to_spawn.startklar_schluessel", "to_spawn.startklar_werkzeug"),
)
def test_teilmodule_einzeln_importierbar(modulname: str) -> None:
    """Jedes Teilmodul lässt sich sauber einzeln importieren."""
    modul = importlib.import_module(modulname)
    assert modul is not None
