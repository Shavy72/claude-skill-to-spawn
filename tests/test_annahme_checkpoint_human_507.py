"""Die 60-min-Annahme steht höchstens einmal je Ticket am Checkpoint (#507).

Probe-Lauf 2 (``to-spawn-probe-2-ohne`` #15, 24.11.): Der Aufseher setzte 11:34 „Annahme
nach 60 min“, nahm sie 12:08 selbst zurück (Label ``checkpoint:human`` wieder gesetzt) und
setzte sie 12:51 erneut. Die zweite Annahme gab nichts frei, nur Rauschen und widersprüchliche
Aussagen. Regel an genau einer Stelle: ``capo.annahme_schon_gesetzt`` liest die Kommentare
am Ticket — steht dort schon eine Annahme des Aufsehers, kommt keine neue, auch wenn der
Zustand des Aufsehers verloren ging oder die Session einen neuen Vorschlag schreibt.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import test_capo_checkpoint_285 as cp
from test_waechter_213 import _capo, _gh_setzen, _text, welt  # noqa: F401  (welt = Fixture)

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import capo

ABNAHME = (
    "Bau-Session: Abnahme bereit (ohne Staging, lokal belegt). Bitte hier kommentieren: "
    "„passt“ → Komplett-Prüfung und Übungs-Live; oder Kritik.\n\nVorschlag: passt"
)
RUECKNAHME = (
    "Aufseher: Die automatische Annahme von 11:34 gibt nichts frei. Label checkpoint:human "
    "ist wieder gesetzt. Es geht erst weiter, wenn der Mensch selbst „passt“ oder Kritik kommentiert."
)


def _annahmen(welt: dict[str, Path]) -> list[str]:  # noqa: F811
    return [t for t in cp._texte(welt, "902") if t.startswith("Aufseher: Annahme nach")]


def _erste_annahme_und_ruecknahme(welt: dict[str, Path]) -> None:  # noqa: F811
    """11:34 Annahme, 12:08 Rücknahme mit wieder gesetztem Label — wie im Probe-Lauf."""
    cp._checkpoint_stellen(welt)
    cp._kommentar(welt, "902", "bau-bot", ABNAHME, 200)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert len(_annahmen(welt)) == 1, cp._texte(welt, "902")
    cp._kommentar(welt, "902", "bau-bot", RUECKNAHME, 120)
    _gh_setzen(welt, "902", labels=[cp.CHECKPOINT])


def test_keine_zweite_annahme_nach_ruecknahme_ohne_aufseher_zustand(
    welt: dict[str, Path],  # noqa: F811
) -> None:
    _erste_annahme_und_ruecknahme(welt)
    _bau_log_zurueckdatieren(welt, 77)
    # Neustart des Aufsehers: sein Gedächtnis (erledigt) ist weg, nur das Ticket weiß es noch.
    shutil.rmtree(welt["zustand"], ignore_errors=True)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert len(_annahmen(welt)) == 1, cp._texte(welt, "902")
    assert "Annahme steht schon am Ticket" in ergebnis.stdout, _text(ergebnis)
    assert cp._labels(welt, "902") == [cp.CHECKPOINT]


def _bau_log_zurueckdatieren(welt: dict[str, Path], minuten: float) -> None:  # noqa: F811
    """Die Uhr läuft weiter: alle Bau-Log-Zeilen von #902 liegen ``minuten`` früher."""
    datei = welt["repo"] / ".to-spawn" / "bau_log" / "902.jsonl"
    zeilen = [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]
    for z in zeilen:
        zeit = datetime.fromisoformat(str(z["ts"]))
        z["ts"] = (zeit - timedelta(minutes=minuten)).isoformat()
    datei.write_text("".join(json.dumps(z, ensure_ascii=False) + "\n" for z in zeilen), encoding="utf-8")


def test_keine_zweite_annahme_77_min_nach_der_ersten(welt: dict[str, Path]) -> None:  # noqa: F811
    """Genau der Probe-Ablauf: die eigene Annahme (11:34) schreibt eine Bau-Log-Zeile
    ``entscheidung`` — 77 min später (12:51) gilt sie als jüngster „Vorschlag“ mit neuer Frist."""
    _erste_annahme_und_ruecknahme(welt)
    _bau_log_zurueckdatieren(welt, 77)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert len(_annahmen(welt)) == 1, cp._texte(welt, "902")
    assert "Annahme steht schon am Ticket" in ergebnis.stdout, _text(ergebnis)
    assert cp._labels(welt, "902") == [cp.CHECKPOINT]


def test_regel_erkennt_nur_annahme_des_aufsehers() -> None:
    def k(body: str) -> dict[str, object]:
        return {"body": body, "created_at": "2026-11-24T11:34:19Z", "author_association": "OWNER"}

    assert capo.annahme_schon_gesetzt([k("Aufseher: Annahme nach 60 min nach Doktrin — x")])
    assert capo.annahme_schon_gesetzt([k("Wächter: Annahme nach 60 min nach Doktrin — x")])
    assert not capo.annahme_schon_gesetzt([k(RUECKNAHME)])
    assert not capo.annahme_schon_gesetzt([k("Bau-Session: Annahme nach 60 min wäre falsch")])
    assert not capo.annahme_schon_gesetzt([k("Annahme nach 60 min bitte")])  # Mensch
    assert not capo.annahme_schon_gesetzt([])
