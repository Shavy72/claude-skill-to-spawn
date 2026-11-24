"""#564: ``zuordnen`` urteilt nur aus der übergebenen Prozessliste.

Seit #500 schaute ``zuordnen`` selbst in ``/proc/<pid>`` nach, ob ein Prozess noch lebt. Eine
aufgezeichnete ``ps``-Ausgabe (Test-Daten, erfundene Prozess-Nummern) ergab dann für alles „aus“,
Tabelle „(keine Ticket-Sessions gefunden)“. Die Lebens-Prüfung (#494) gehört zum Lesen der echten
Liste, nicht zur Auswertung.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKRIPTE = Path(__file__).resolve().parents[1] / "skripte"

#: Prozess-Nummern über ``pid_max`` (2**22): gibt es auf keinem Rechner.
P = 2**22 + 1000
PS_AUSGABE = f"""\
{P + 1} 1 Wed Sep 17 09:12:33 2026 python3 python3 scripts/bau.py 179 --takt 600
{P + 2} 1 Wed Sep 17 08:00:01 2026 python3 python3 scripts/wache.py 192
{P + 3} 1 Wed Sep 17 09:12:00 2026 python3 python3 scripts/bau.py 188
{P + 4} {P + 3} Wed Sep 17 09:13:07 2026 node node /h/.local/bin/claude --settings /tmp/x-bau/188-20260917-091300/settings.json
{P + 5} 1 Wed Sep 17 07:40:00 2026 node node /h/.local/bin/claude --settings /tmp/x-bau/187-20260917-074000/settings.json
"""


@pytest.fixture
def modul(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    spec = importlib.util.spec_from_file_location("sessions_stand_564", SKRIPTE / "sessions_stand.py")
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m  # dataclass-Auswertung braucht das Modul in sys.modules
    try:
        spec.loader.exec_module(m)
    finally:
        sys.modules.pop(spec.name, None)
    return m


def test_uebergebene_liste_mit_erfundenen_nummern_wird_zugeordnet(modul: ModuleType) -> None:
    eintraege = {
        "179": modul.Eintrag("179", "ticket", "Ticket 179"),
        "188": modul.Eintrag("188", "ticket", "Ticket 188"),
        "192": modul.Eintrag("192", "spec", "Aufseher Spec #192"),
    }
    modul.zuordnen(eintraege, modul.ps_zeilen_parsen(PS_AUSGABE))
    assert eintraege["179"].zustand == "wartet"
    assert eintraege["179"].pid == P + 1
    assert eintraege["192"].zustand == "startet"
    assert eintraege["188"].zustand == "läuft seit 09:13"
    assert eintraege["188"].session_pid == P + 4
    assert eintraege["187"].zustand == "VERWAIST seit 07:40"


def test_tabelle_zeigt_zeile_aus_uebergebener_liste(modul: ModuleType) -> None:
    eintraege = {"179": modul.Eintrag("179", "ticket", "Ticket 179")}
    modul.zuordnen(eintraege, modul.ps_zeilen_parsen(PS_AUSGABE))
    ausgabe = modul.tabelle(eintraege, alle_zeigen=False)
    assert "#179" in ausgabe
    assert "wartet" in ausgabe
