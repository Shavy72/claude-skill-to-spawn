"""M12-Probe: zweite, vorher unbekannte Fehlerreihe (≥ 20 Fehler, Py + Node, #104).

Prüft gegen Überanpassung der eingefrorenen Rückfall-Regeln (``to_spawn/pruef/regeln.py``):
- mindestens 20 neue, vielfältige Fehler (Python + Node) aus ``messung/fehler_reihe_2.py``,
- Gegenprobe: mindestens je ein Fall Daten-Datei, Template, dynamischer Import und
  ``mock.patch``-String, den die rohe statische Auswahl übersieht,
- jeder Fehler im isolierten Temp-Probe-Repo: voller Lauf rot ⇒ ``pruef.pruefen`` rot,
  0 übersehen, eingefrorener Regel-SHA im Protokoll,
- Last-Schutz (< 12) und Dokumentation des Ergebnisses in ``docs/PRUEFTOR_VERGLEICH.md`` 4.2.
"""

from __future__ import annotations

import ast
import os
from collections.abc import Iterator
from pathlib import Path

import pruef_probe_helfer as probe_helfer
import pytest

from to_spawn.pruef import regeln

WURZEL = Path(__file__).resolve().parents[1]
REIHE_1 = WURZEL / "messung" / "fehler_reihe.py"
VERGLEICH = WURZEL / "docs" / "PRUEFTOR_VERGLEICH.md"


def _reihe_1_mutationen() -> set[tuple[str, str, str]]:
    """Liest (datei, alt, neu) der ersten Fehlerreihe per AST aus ``messung/fehler_reihe.py``."""
    baum = ast.parse(REIHE_1.read_text(encoding="utf-8"))
    ergebnis: set[tuple[str, str, str]] = set()
    for knoten in baum.body:
        if isinstance(knoten, ast.Assign) and any(
            getattr(ziel, "id", "") in ("PY_FEHLER", "NODE_FEHLER") for ziel in knoten.targets
        ):
            for eintrag in ast.literal_eval(knoten.value):
                ergebnis.add((eintrag[2], eintrag[3], eintrag[4]))
    return ergebnis


@pytest.fixture(scope="module")
def isoliertes_probe(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Path]]:
    """Erzeugt frische, isolierte Probe-Git-Repos (Python + Node) im Temp-Ordner."""
    ziel = tmp_path_factory.mktemp("m12_reihe2")
    repos = probe_helfer.baue_probe_repos(ziel)
    py_repo = repos["py"]
    alt_pypath = os.environ.get("PYTHONPATH", "")
    pypath = os.pathsep.join(teil for teil in (str(py_repo), str(py_repo / "deps" / "greetlib-1"), alt_pypath) if teil)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PYTHONPATH", pypath)
        yield repos


def test_reihe2_umfang_pflichtklassen_und_eingefrorener_stand() -> None:
    from messung import fehler_reihe_2 as fr2

    assert len(fr2.ALLE_FEHLER) >= 20
    assert len(fr2.PY_FEHLER) >= 10
    assert len(fr2.NODE_FEHLER) >= 5

    ids = [f.id for f in fr2.ALLE_FEHLER]
    assert len(ids) == len(set(ids)), "Doppelte Fehler-IDs"

    py_dateien = {f.datei for f in fr2.PY_FEHLER}
    node_dateien = {f.datei for f in fr2.NODE_FEHLER}
    assert len(py_dateien) >= 10, f"Zu wenige verschiedene Python-Dateien: {py_dateien}"
    assert len(node_dateien) >= 8, f"Zu wenige verschiedene Node-Dateien: {node_dateien}"

    klassen = {klasse for f in fr2.ALLE_FEHLER for klasse in f.klassen}
    for pflicht in fr2.PFLICHT_KLASSEN:
        assert pflicht in klassen, f"Pflicht-Klasse fehlt in Reihe 2: {pflicht}"

    reihe_1 = _reihe_1_mutationen()
    neue = [f for f in fr2.ALLE_FEHLER if (f.datei, f.alt, f.neu) not in reihe_1]
    assert len(neue) >= 20, f"Mindestens 20 neue Mutationen erwartet, gefunden: {len(neue)}"

    assert fr2.EINGEFROREN_REGELN_STAND == regeln.stand()


def test_reihe2_m12_probe_null_uebersehen(isoliertes_probe: dict[str, Path]) -> None:
    from messung import fehler_reihe_2 as fr2

    ergebnis = fr2.messe_reihe(isoliertes_probe, max_last=0.0)

    assert ergebnis["regeln_stand"] == regeln.stand()
    assert ergebnis["gesamt"] == len(fr2.ALLE_FEHLER)
    assert ergebnis["voll_rot"] == len(fr2.ALLE_FEHLER), [f["id"] for f in ergebnis["faelle"] if not f["voll_rot"]]
    assert ergebnis["pruefen_rot"] == len(fr2.ALLE_FEHLER), [
        f["id"] for f in ergebnis["faelle"] if not f["pruefen_rot"]
    ]
    assert ergebnis["uebersehen"] == [], f"Übersehene Fehler: {ergebnis['uebersehen']}"

    # Eingefrorener Regel-SHA steht in jeder Protokoll-Zeile.
    for eintrag in ergebnis["faelle"]:
        assert eintrag["regeln_stand"] == regeln.stand(), eintrag["id"]

    # Gegenprobe: rohe statische Auswahl übersieht tatsächlich alle vier Pflicht-Klassen.
    roh_uebersehen_ids = set(ergebnis["roh_uebersehen"])
    roh_uebersehen_klassen = {
        klasse for fall in fr2.ALLE_FEHLER if fall.id in roh_uebersehen_ids for klasse in fall.klassen
    }
    for pflicht in fr2.PFLICHT_KLASSEN:
        assert pflicht in roh_uebersehen_klassen, (
            f"Gegenprobe: rohe Auswahl hat Pflicht-Klasse {pflicht!r} nicht übersehen "
            f"(roh übersehen: {sorted(roh_uebersehen_ids)})"
        )


def test_reihe2_last_schutz_und_markdown_bericht(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from messung import fehler_reihe_2 as fr2

    monkeypatch.setattr(fr2, "aktuelle_last", lambda: 15.2)
    with pytest.raises(RuntimeError, match="Last"):
        fr2.messe_reihe({"py": tmp_path, "node": tmp_path}, max_last=12.0)

    beispiel = {
        "regeln_stand": regeln.stand(),
        "regeln_commit": fr2.EINGEFROREN_REGELN_COMMIT,
        "gesamt": 26,
        "py_gesamt": 16,
        "node_gesamt": 10,
        "voll_rot": 26,
        "pruefen_rot": 26,
        "uebersehen": [],
        "roh_uebersehen": ["Q01", "Q02", "Q06", "Q07", "M02", "M05"],
        "faelle": [
            {
                "id": "Q01",
                "repo": "py",
                "kategorie": "Daten-Datei",
                "datei": "app/data/catalog.json",
                "voll_rot": True,
                "roh_rot": False,
                "pruefen_rot": True,
                "stufe": "voll",
                "grund": "R1: Nicht-Code-Datei geändert (app/data/catalog.json)",
                "regeln_stand": regeln.stand(),
            }
        ],
    }
    md = fr2.formatiere_markdown(beispiel)
    assert regeln.stand() in md
    assert "0" in md
    assert "Q01" in md


def test_vergleich_4_2_dokumentiert_zweite_reihe() -> None:
    from messung import fehler_reihe_2 as fr2

    text = VERGLEICH.read_text(encoding="utf-8")
    assert "### 4.2" in text
    assert "fehler_reihe_2.py" in text
    assert fr2.EINGEFROREN_REGELN_STAND in text
    assert "0 übersehen" in text or "**0**" in text
