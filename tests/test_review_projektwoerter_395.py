"""#395: Projektwörter des Routers sind privat je Rechner, nie im ausgelieferten Standard.

- ``review/config.standard.json`` liefert ``projekt_woerter`` leer aus.
- Der Installer überträgt die Wörter aus dem auf dem Rechner installierten alten Stand (nie aus dem Repo)
  in die private ``config.json``, bevor die Entschlackung aufräumt; ein zweiter Lauf ändert nichts.
- Setzt die lokale Config den Schlüssel (auch leer), bleibt er stehen.
- Fehlt der Schlüssel lokal, läuft das Werkzeug weiter und meldet es (Warnung + Kurzzeile).

Alle Wörter hier sind Kunstwörter.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from to_spawn import review_hook

QUELLE = review_hook.WERKZEUG_QUELLE
KUNST = {"datenbank": ["lagerhalle_db"], "kosten": ["funkmast_api"]}


def _standard(woerter: dict) -> dict:
    stand = json.loads((QUELLE / "config.standard.json").read_text(encoding="utf-8"))
    return {**stand, "projekt_woerter": woerter}


def _schreibe(datei: Path, daten: dict) -> None:
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps(daten, indent=2) + "\n", encoding="utf-8")


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(ordner), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def quelle(tmp_path: Path) -> Path:
    """Kunst-Skill-Quelle: früher mit Kunstwörtern ausgeliefert, jetzt leer (git-Verlauf wie im echten Repo)."""
    q = tmp_path / "quelle" / "review"
    _schreibe(q / "config.standard.json", _standard(KUNST))
    _git(q, "init", "-q")
    _git(q, "add", ".")
    _git(q, "commit", "-qm", "alt")
    _schreibe(q / "config.standard.json", _standard({}))
    _git(q, "commit", "-qam", "neu")
    return q


def _einspielen(ziel: Path, quelle: Path) -> list[str]:
    return review_hook.installiere_werkzeug(ziel, quelle, zeit="t")


def _lokal(ziel: Path) -> dict:
    assert (ziel / "config.json").is_file(), "private config.json fehlt"
    return json.loads((ziel / "config.json").read_text(encoding="utf-8"))


def test_standard_liefert_keine_projektwoerter() -> None:
    stand = json.loads((QUELLE / "config.standard.json").read_text(encoding="utf-8"))
    assert stand["projekt_woerter"] == {}


def test_woerter_aus_installiertem_stand_in_private_config(tmp_path: Path, quelle: Path) -> None:
    ziel = tmp_path / "review"
    _schreibe(ziel / "config.standard.json", _standard(KUNST))  # alter installierter Stand

    _einspielen(ziel, quelle)

    assert _lokal(ziel) == {"projekt_woerter": KUNST}
    vorher = (ziel / "config.json").read_bytes()
    zweiter = _einspielen(ziel, quelle)
    assert (ziel / "config.json").read_bytes() == vorher
    assert not [z for z in zweiter if "config" in z.lower()], zweiter


def test_lokale_config_gleich_altem_stand_verliert_woerter_nicht(tmp_path: Path, quelle: Path) -> None:
    """Die Entschlackung räumt eine Config weg, die einem ausgelieferten Stand gleicht — die Wörter bleiben."""
    ziel = tmp_path / "review"
    _schreibe(ziel / "config.standard.json", _standard(KUNST))
    _schreibe(ziel / "config.json", _standard(KUNST))

    _einspielen(ziel, quelle)

    assert _lokal(ziel) == {"projekt_woerter": KUNST}


def test_lokaler_schluessel_bleibt_auch_leer(tmp_path: Path, quelle: Path) -> None:
    ziel = tmp_path / "review"
    _schreibe(ziel / "config.standard.json", _standard(KUNST))
    _schreibe(ziel / "config.json", {"modus": "scharf", "projekt_woerter": {}})

    _einspielen(ziel, quelle)

    assert _lokal(ziel) == {"modus": "scharf", "projekt_woerter": {}}


def test_woerter_aus_alt_sicherung(tmp_path: Path, quelle: Path) -> None:
    ziel = tmp_path / "review"
    _schreibe(tmp_path / "_alt" / "review-20260101-000000" / "config.standard.json", _standard(KUNST))
    _schreibe(ziel / "config.json", {"modus": "scharf"})

    _einspielen(ziel, quelle)

    assert _lokal(ziel) == {"modus": "scharf", "projekt_woerter": KUNST}


def _modul(pfad: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, pfad)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul  # dataclasses im Modul brauchen den Eintrag
    spec.loader.exec_module(modul)
    return modul


def test_fehlender_schluessel_meldet_sichtbar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    lokal = tmp_path / "config.json"
    _schreibe(lokal, {"modus": "scharf"})
    monkeypatch.setenv("REVIEW_CONFIG", str(lokal))
    werkzeug = _modul(QUELLE / "_gemeinsam.py", "_gemeinsam_woerter_395")
    with caplog.at_level(logging.WARNING):
        info = getattr(werkzeug, "projekt_woerter_info", dict)()
    assert info.get("vorhanden") is False
    assert "Projektwörter fehlen" in caplog.text
    _schreibe(lokal, {"projekt_woerter": {}})
    assert werkzeug.projekt_woerter_info()["vorhanden"] is True

    monkeypatch.syspath_prepend(str(QUELLE))
    route_modul = _modul(QUELLE / "review_route.py", "review_route_woerter_395")
    route = {"stufe": "klein", "pruefer": [], "builtin": [], "diff_zeilen": 3, "dateien": ["a.py"]}
    route["projekt_woerter"] = {"datei": lokal.as_posix(), "vorhanden": False}
    assert "Projektwörter fehlen" in route_modul.kurzzeile(route)
    route["projekt_woerter"] = {"datei": lokal.as_posix(), "vorhanden": True}
    assert "Projektwörter" not in route_modul.kurzzeile(route)
