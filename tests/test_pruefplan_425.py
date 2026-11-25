"""#425 Teil A: eine Stelle entscheidet, welche Prüfung jetzt dran ist.

Tabelle Repo-Art (mit/ohne Staging-App) × Spec-Stand. Vorgabe des Auftraggebers (24.11.): je Ticket nur
die schnelle Prüfung; die Komplett-Prüfung genau einmal je Spec — mit Staging, sobald alle
Tickets auf Staging sind, ohne Staging vor dem Live-Schritt.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from to_spawn import config, pruefplan
from to_spawn.pruefplan import Moment, SpecStand

MIT_STAGING: dict[str, Any] = {
    "staging_start": "python skripte/ausrollen.py staging",
    "staging": {"url": "http://127.0.0.1:8001/", "ohne": False},
    "pruefung": {
        "schnell": "ruff check . && pytest -q tests/betroffen",
        "komplett": "pytest -q",
    },
}
OHNE_STAGING: dict[str, Any] = {
    "staging_start": "",
    "staging": {"url": "", "ohne": True},
    "pruefung": {
        "schnell": "ruff check .",
        "komplett": "python skripte/pruefen_komplett.py",
    },
}


def _konfig(eigen: dict[str, Any]) -> dict[str, Any]:
    """Wie ``config.lade``: Vorgaben + Repo-Datei gemischt."""
    return config._mische(config.DEFAULTS, eigen)


TABELLE = [
    # (Repo-Art, Spec-Stand, erwartete Prüfung)
    ("mit", SpecStand(Moment.TICKET), "schnell"),
    ("mit", SpecStand(Moment.TICKET, komplett_lief=True), "schnell"),
    ("mit", SpecStand(Moment.ALLE_AUF_STAGING), "komplett"),
    ("mit", SpecStand(Moment.ALLE_AUF_STAGING, komplett_lief=True), "keine"),
    ("mit", SpecStand(Moment.VOR_LIVE, komplett_lief=True), "keine"),
    # Sicherheitsnetz: lief sie auf Staging nie (z. B. abgebrochen), dann vor Live.
    ("mit", SpecStand(Moment.VOR_LIVE), "komplett"),
    ("ohne", SpecStand(Moment.TICKET), "schnell"),
    # Ohne Staging-App gibt es diesen Moment nicht: nie dort prüfen.
    ("ohne", SpecStand(Moment.ALLE_AUF_STAGING), "keine"),
    ("ohne", SpecStand(Moment.VOR_LIVE), "komplett"),
    ("ohne", SpecStand(Moment.VOR_LIVE, komplett_lief=True), "keine"),
]


@pytest.mark.parametrize(("art", "stand", "erwartet"), TABELLE)
def test_tabelle_repo_art_mal_spec_stand(art: str, stand: SpecStand, erwartet: str) -> None:
    konfig = _konfig(MIT_STAGING if art == "mit" else OHNE_STAGING)
    assert config.hat_staging(konfig) is (art == "mit")
    assert pruefplan.welche(konfig, stand) == erwartet


@pytest.mark.parametrize("eigen", [MIT_STAGING, OHNE_STAGING])
def test_komplett_laeuft_genau_einmal_je_spec(eigen: dict[str, Any]) -> None:
    """Ganzer Spec-Ablauf: nach dem ersten „komplett“ merkt der Aufrufer es, danach nie wieder."""
    konfig = _konfig(eigen)
    lief = False
    arten: list[str] = []
    ablauf = [
        Moment.TICKET,
        Moment.TICKET,
        Moment.TICKET,
        Moment.ALLE_AUF_STAGING,
        Moment.VOR_LIVE,
    ]
    for moment in ablauf:
        art = pruefplan.welche(konfig, SpecStand(moment, komplett_lief=lief))
        arten.append(art)
        lief = lief or art == "komplett"
    assert arten.count("komplett") == 1
    assert arten[:3] == ["schnell"] * 3


def test_befehl_aus_konfig() -> None:
    konfig = _konfig(MIT_STAGING)
    assert pruefplan.befehl(konfig, "schnell") == "ruff check . && pytest -q tests/betroffen"
    assert pruefplan.befehl(konfig, "komplett") == "pytest -q"
    assert pruefplan.befehl(konfig, "keine") is None


@pytest.mark.parametrize("wert", ["", "   ", None])
def test_befehl_fehlt_gibt_none(wert: str | None) -> None:
    """Leer, nur Leerzeichen oder gar nicht eingetragen = nicht eingerichtet → None."""
    konfig = _konfig({"pruefung": {"schnell": wert, "komplett": wert}})
    assert pruefplan.befehl(konfig, "schnell") is None
    assert pruefplan.befehl(konfig, "komplett") is None
    assert pruefplan.befehl(_konfig({}), "komplett") is None
    assert pruefplan.befehl({}, "schnell") is None
    assert pruefplan.befehl({"pruefung": "kaputt"}, "schnell") is None


def test_spec_stand_ist_unveraenderlich_und_moment_pflicht() -> None:
    stand = SpecStand(Moment.TICKET)
    with pytest.raises(dataclasses.FrozenInstanceError):
        stand.komplett_lief = True  # type: ignore[misc]
    with pytest.raises(TypeError):
        SpecStand()  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        SpecStand("tikket")  # type: ignore[arg-type]


def test_vorgaben_haben_neue_gruppen_leer() -> None:
    assert config.DEFAULTS["pruefung"] == {"schnell": "", "komplett": ""}
    assert config.DEFAULTS["live"] == {
        "befehl": "",
        "url": "",
        "aufraeumen": "",
        "ohne": False,
    }


def test_live_schritt_liest_gruppe() -> None:
    konfig = _konfig({"live": {"befehl": " python skripte/ausrollen.py live ", "url": "http://x/"}})
    assert config.live_schritt(konfig) == {
        "befehl": "python skripte/ausrollen.py live",
        "url": "http://x/",
        "aufraeumen": "",
    }
    assert config.live_schritt({"live": None}) == {
        "befehl": "",
        "url": "",
        "aufraeumen": "",
    }


VORLAGE = {
    "pruefung": {
        "schnell": "python -m unittest discover -s tests -t .",
        "komplett": "python skripte/pruefen_komplett.py",
    },
    "live": {
        "befehl": "python skripte/ausrollen.py live",
        "url": "http://127.0.0.1:@@LIVE_PORT@@/",
        "aufraeumen": "python skripte/ausrollen.py live --aufraeumen",
    },
}


def test_schluessel_passen_zu_probe_vorlagen_442(tmp_path: Path) -> None:
    """Schlüsselnamen wie in ``probe/*/.to-spawn/config.json`` (PR 442): beide Gruppen werden gelesen."""
    datei = tmp_path / ".to-spawn" / "config.json"
    datei.parent.mkdir()
    datei.write_text(json.dumps(VORLAGE), encoding="utf-8")
    konfig = config._mische(config.DEFAULTS, json.loads(datei.read_text(encoding="utf-8")))
    assert set(konfig["pruefung"]) == set(config.DEFAULTS["pruefung"])
    assert set(konfig["live"]) == set(config.DEFAULTS["live"])
    assert pruefplan.befehl(konfig, "komplett") == "python skripte/pruefen_komplett.py"
    assert config.live_schritt(konfig)["aufraeumen"] == "python skripte/ausrollen.py live --aufraeumen"
