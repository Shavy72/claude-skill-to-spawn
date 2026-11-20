"""Gemeinsame Test-Einstellungen.

Die Wächter-Tests aus dem ersten Bau von #213 (``test_waechter_213.py`` und der
Weg-Test) prüfen die Regeln selbst: Ticket schließen, sofort ein Tick, Ergebnis.
Seit der Fixrunde wartet capo 15 min nach dem Schließen (Karenz) und nimmt beim
ersten Tick alle geschlossenen Tickets als Ausgangsstand. Diese beiden
Zeitgrenzen schaltet ``TO_SPAWN_WAECHTER_SOFORT=1`` ab — nur für diese beiden
Dateien. Die Zeitgrenzen selbst prüft ``test_waechter_213_fix.py`` ohne Schalter.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Test-Helfer (``tests/hilfen``) einmal importierbar machen — z. B. ``context_mode_attrappe`` (#237).
_HILFEN = str(Path(__file__).resolve().parent / "hilfen")
if _HILFEN not in sys.path:
    sys.path.insert(0, _HILFEN)

_SOFORT_MODULE = frozenset({"test_waechter_213", "test_waechter_213_weg"})


@pytest.fixture(autouse=True)
def _keine_to_spawn_umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    """Löscht vor jedem Test alle ``TO_SPAWN_*``-Variablen.

    Die Fremd-Repo-Suite darf nie in die laufende Bau-Session schreiben (#257):
    ohne diese Fixture erbt jeder Testlauf ``TO_SPAWN_LOG_REPO``/``TO_SPAWN_REPO``
    der Bau-Session, und Hook-Zeilen landeten so in
    ``wt-257/.to-spawn/bau_log/901.jsonl`` statt im Test-``tmp_path``. Läuft vor
    ``_waechter_sofort`` (Parameter-Reihenfolge unten), die danach gezielt
    ``TO_SPAWN_WAECHTER_SOFORT`` setzt.
    """
    for name in list(os.environ):
        if name.startswith("TO_SPAWN_"):
            monkeypatch.delenv(name, raising=False)
    # Seit #257 Fixrunde 1 (F5) brechen bau.py/wache.py ohne GitHub-Origin mit Exit 2 ab
    # (kein stiller DuoPlus-Rückfall mehr). Die Weg-Tests arbeiten mit lokalen bare-Origins
    # → die ausdrückliche Vorgabe ``TO_SPAWN_GH_REPO`` gilt nur, wenn kein GitHub-Origin
    # da ist; Tests mit github.com-Origin sehen weiter ihren eigenen Slug.
    monkeypatch.setenv("TO_SPAWN_GH_REPO", "test-org/test-repo")


@pytest.fixture(autouse=True)
def _speicher_immer_frei(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    _keine_to_spawn_umgebung: None,
) -> None:
    """Speicher-Schutz (#257 Paket B) sieht in Tests immer „frei“.

    Die Starter (``bau``, ``wache``, ``spawn_srv.sh``, Aufpasser) fragen vor jedem
    Claude-Start ``to_spawn.speicher``. Ohne diese Fixture hinge das Ergebnis der
    ganzen Suite an den Sessions, die gerade auf der Testmaschine laufen (12 Claude-
    Prozesse → jeder Start-Test rot). Die Test-Tür des Moduls zeigt deshalb auf eine
    Attrappe mit 64 GiB frei und leerer Prozessliste. ``test_speicher_257`` stellt
    für seine Fälle eigene Attrappen (knapp/voll) über dieselben Variablen.
    """
    ordner = tmp_path_factory.mktemp("speicher-frei")
    meminfo = ordner / "meminfo"
    meminfo.write_text(f"MemAvailable:    {64 * 1024 * 1024} kB\n", encoding="utf-8")
    (ordner / "proc").mkdir()
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PROC", str(ordner / "proc"))


@pytest.fixture(autouse=True)
def _waechter_sofort(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    _keine_to_spawn_umgebung: None,
) -> None:
    if request.module.__name__ in _SOFORT_MODULE:
        monkeypatch.setenv("TO_SPAWN_WAECHTER_SOFORT", "1")
    else:
        monkeypatch.delenv("TO_SPAWN_WAECHTER_SOFORT", raising=False)


@pytest.fixture(autouse=True)
def _start_nur_attrappen(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    _keine_to_spawn_umgebung: None,
) -> None:
    """Start-Schutz (#1406): ``to_spawn.starter`` startet unter Test nur Attrappen.

    Setzt nur die beiden Variablen; sie erben sich in Kind-Prozesse (``wache.py``,
    ``bau.py``), wo monkeypatch nicht hinreicht. Attrappen gehören in den Ordner
    ``TO_SPAWN_TEST_BIN``. Läuft nach ``_keine_to_spawn_umgebung`` (löscht ``TO_SPAWN_*``).
    """
    monkeypatch.setenv("TO_SPAWN_TESTLAUF", "1")
    monkeypatch.setenv(
        "TO_SPAWN_TEST_BIN", str(tmp_path_factory.mktemp("start-attrappen"))
    )


@pytest.fixture(autouse=True)
def _hausmeister_nur_im_test(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    _keine_to_spawn_umgebung: None,
) -> None:
    """Hausmeister (#1369) räumt in Tests nie echte Ordner auf.

    Start-Check (``to_spawn.py speicher``, ``bau.py``) und Aufpasser rufen
    ``hausmeister.aufraeumen_alles`` — ohne diese Fixture liefe das mit
    ``loeschen=True`` über die echten ``~/wt/*`` der Testmaschine. Wurzeln zeigen
    auf einen leeren Ordner, Logs in ``tmp``, Platte gilt als frei. Tests mit
    eigener Welt (``test_hausmeister_1368/1369``) setzen die Variablen neu.
    """
    ordner = tmp_path_factory.mktemp("hausmeister")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", str(ordner / "keine" / "*"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(ordner / "hausmeister.jsonl"))
    monkeypatch.setenv("TO_SPAWN_HINDERNIS_LOG", str(ordner / "hindernisse.jsonl"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "1")
    # #1371: keine echten GitHub-Remotes — sonst löschte ein Test alte sicherung/*-Zweige.
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", "")
