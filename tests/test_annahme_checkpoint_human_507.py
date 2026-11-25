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


# --- #511: Reste aus dem Review von 509 ----------------------------------------


def test_eigene_annahme_zeile_ist_kein_vorschlag_und_keine_frage(
    welt: dict[str, Path],  # noqa: F811
) -> None:
    """Wurzel (#511): die Bau-Log-Zeile, die die Annahme selbst schreibt, ist keine Spur der
    Session — sonst gilt sie nach der Frist als neuer Vorschlag mit neuer Frist."""
    _erste_annahme_und_ruecknahme(welt)
    zeilen = cp._bau_log(welt, "902")
    assert [z for z in zeilen if z.get("typ") == "entscheidung"], zeilen
    assert capo.checkpoint_vorschlag(zeilen, []) is None
    assert capo.checkpoint_frage_zeit(zeilen, []) is None


def test_mensch_zitiert_aufseher_annahme_sperrt_nicht(
    welt: dict[str, Path],  # noqa: F811
) -> None:
    """Ein Zitat des Aufseher-Texts (``> Aufseher: Annahme nach …``) stammt vom Menschen —
    es ist keine Annahme am Ticket, die erste echte Annahme kommt trotzdem."""
    cp._checkpoint_stellen(welt)
    zitat = "> Aufseher: Annahme nach 60 min nach Doktrin — der Mensch kann kippen.\n\nWas heißt das hier?"
    cp._kommentar(welt, "902", "david", zitat, 300)
    cp._kommentar(welt, "902", "bau-bot", ABNAHME, 200)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "Annahme steht schon am Ticket" not in ergebnis.stdout, _text(ergebnis)
    assert len(_annahmen(welt)) == 1, cp._texte(welt, "902")
    assert not capo.annahme_schon_gesetzt([{"body": zitat}])


def test_erste_annahme_nach_60_min_kommt(welt: dict[str, Path]) -> None:  # noqa: F811
    """Positiv-Fall als eigener Test: ohne frühere Annahme kommt nach 61 min genau eine."""
    cp._checkpoint_stellen(welt)
    cp._kommentar(welt, "902", "bau-bot", ABNAHME, 61)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "#902 Checkpoint-Annahme nach 61 min" in ergebnis.stdout, _text(ergebnis)
    assert "Annahme steht schon am Ticket" not in ergebnis.stdout, _text(ergebnis)
    assert len(_annahmen(welt)) == 1, cp._texte(welt, "902")
    assert cp._labels(welt, "902") == []


def test_meldezeile_annahme_steht_nur_einmal(welt: dict[str, Path]) -> None:  # noqa: F811
    """Die Zeile „Annahme steht schon am Ticket“ kommt einmal, nicht bei jedem Lauf (#511)."""
    _erste_annahme_und_ruecknahme(welt)
    _bau_log_zurueckdatieren(welt, 77)
    erster = _capo(welt)
    assert "Annahme steht schon am Ticket" in erster.stdout, _text(erster)
    zweiter = _capo(welt)
    assert zweiter.returncode == 0, _text(zweiter)
    assert "Annahme steht schon am Ticket" not in zweiter.stdout, _text(zweiter)
    assert len(_annahmen(welt)) == 1, cp._texte(welt, "902")
