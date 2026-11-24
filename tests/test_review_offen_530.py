"""Tests #530: „Mensch nötig — kein Beleg“ erst, wenn der Review nicht mehr läuft; Rücknahme;
gleicher Aufseher-Kommentar binnen Sekunden nur einmal.

Echt laufen: Git-Repo mit Bare-Origin, die CLI ``skripte/capo.py``, die echte Zustandsdatei,
eine echte ``mensch_noetig.jsonl`` und ein echter Beleg in ``<repo>/.review``. Gestellt ist
nur ``gh`` (Ersatz aus #213 bzw. ``gh.lauf`` schreibt mit).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus test_waechter_213 und wird als Parameter genannt.
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from test_aufseher_kommentar_1522 import FakeGh, fake_gh  # noqa: F401  (fake_gh = Fixture)
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _capo,
    _kommentare,
    _text,
    welt,
)

from to_spawn import befund, mensch_noetig

FP = "5e60624d9e00aaaabbbbcccc"


@pytest.fixture()
def datei(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> Path:
    pfad = welt["tmp"] / "review" / "mensch_noetig.jsonl"
    pfad.parent.mkdir()
    monkeypatch.setenv("REVIEW_MENSCH_NOETIG", str(pfad))
    monkeypatch.delenv("GH_STUB_FEHLER", raising=False)
    return pfad


def _review_offen(welt: dict[str, Path], datei: Path, vor_min: float) -> None:
    zeit = (datetime.now().astimezone() - timedelta(minutes=vor_min)).strftime("%Y-%m-%dT%H:%M:%S%z")
    zeile = {
        "zeit": zeit,
        "session_id": "sitzung-530",
        "repo": str(welt["repo"]),
        "fingerprint": FP,
        "fix_runde": 0,
        "grund": "review_offen",
        "spec": SPEC,
        "ticket": "902",
    }
    datei.write_text(json.dumps(zeile) + "\n", encoding="utf-8")


def _beleg_schreiben(welt: dict[str, Path]) -> None:
    ordner = welt["repo"] / ".review"
    ordner.mkdir(exist_ok=True)
    beleg = {"sha": "45a8750", "moment": "sitzung", "fingerprint": FP, "befunde": []}
    (ordner / f"beleg-45a8750-sitzung-{FP[:12]}.json").write_text(json.dumps(beleg), encoding="utf-8")


def _mensch(welt: dict[str, Path]) -> list[str]:
    return [k for k in _kommentare(welt, "902") if "Mensch nötig" in k]


def test_review_laeuft_noch_kein_mensch_noetig(welt: dict[str, Path], datei: Path) -> None:
    """Rot-Beweis #530: Eintrag 1 min alt, Beleg fehlt, Review läuft noch → kein Ruf."""
    _review_offen(welt, datei, vor_min=1)
    ergebnis = _capo(welt)
    assert "MENSCH NÖTIG" not in _text(ergebnis), _text(ergebnis)
    assert "Review läuft noch" in _text(ergebnis), _text(ergebnis)
    assert _mensch(welt) == []


def test_review_beendet_ohne_beleg_mensch_noetig_wie_bisher(welt: dict[str, Path], datei: Path) -> None:
    """Wartezeit abgelaufen, kein Beleg → „Mensch nötig“ wie bisher, genau ein Kommentar."""
    _review_offen(welt, datei, vor_min=mensch_noetig.REVIEW_WARTEZEIT_MIN + 1)
    ergebnis = _capo(welt)
    assert "MENSCH NÖTIG: #902 Review angefordert, aber kein Beleg geschrieben" in _text(ergebnis)
    assert len(_mensch(welt)) == 1


def test_beleg_schon_da_kein_ruf(welt: dict[str, Path], datei: Path) -> None:
    """Review nachweislich fertig (Beleg zum Fingerabdruck liegt) → kein Ruf, auch nach der Wartezeit."""
    _review_offen(welt, datei, vor_min=mensch_noetig.REVIEW_WARTEZEIT_MIN + 1)
    _beleg_schreiben(welt)
    ergebnis = _capo(welt)
    assert "MENSCH NÖTIG" not in _text(ergebnis), _text(ergebnis)
    assert _mensch(welt) == []


def test_beleg_nach_ruf_aufseher_nimmt_ruf_zurueck(welt: dict[str, Path], datei: Path) -> None:
    """Beleg kommt nach dem Ruf → ein Satz Rücknahme am Ticket, beim nächsten Lauf keiner mehr."""
    _review_offen(welt, datei, vor_min=mensch_noetig.REVIEW_WARTEZEIT_MIN + 1)
    _capo(welt)
    assert len(_mensch(welt)) == 1
    _beleg_schreiben(welt)
    _capo(welt)
    _capo(welt)
    ruecknahmen = [k for k in _kommentare(welt, "902") if "zurückgenommen" in k]
    assert len(ruecknahmen) == 1, _kommentare(welt, "902")
    assert ruecknahmen[0].startswith("Aufseher:")
    assert "fp 5e60624d" in ruecknahmen[0]


def test_gleicher_kommentar_direkt_hintereinander_nur_einmal(fake_gh: FakeGh) -> None:
    """Zweimal derselbe Text an dasselbe Ticket binnen Sekunden → ein Kommentar."""
    text = f"{befund.KOPF} Gleicher Text 530."
    assert befund.kommentiere(530, "test/wegwerf", text) is True
    assert befund.kommentiere(530, "test/wegwerf", text) is True
    assert len(fake_gh.aufrufe) == 1


def test_gewollte_wiederholung_spaeter_und_anderes_ticket_gehen_raus(
    fake_gh: FakeGh, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Sperre schluckt keine gewollte Wiederholung: anderes Ticket sofort, gleiches später."""
    uhr = [1000.0]
    monkeypatch.setattr(befund, "_uhr", lambda: uhr[0])
    text = f"{befund.KOPF} Wiederholung 530."
    befund.kommentiere(531, "test/wegwerf", text)
    befund.kommentiere(532, "test/wegwerf", text)
    uhr[0] += befund.DOPPELT_SEKUNDEN + 1
    befund.kommentiere(531, "test/wegwerf", text)
    assert [a[2] for a in fake_gh.aufrufe] == ["531", "532", "531"]


def _rufe(welt: dict[str, Path]) -> list[dict]:
    dateien = list(welt["zustand"].glob(f"*_{SPEC}.json"))
    zustand = json.loads(dateien[0].read_text(encoding="utf-8")) if dateien else {}
    return zustand.get("mensch_noetig_rufe") or []


@pytest.mark.parametrize("zeit", [None, "", "kaputt", "2026-11-24T10:00:00"])
def test_review_offen_ohne_lesbare_zeit_ruft_sofort(zeit: object) -> None:
    """Fehlende/unlesbare Zeit (auch ohne Zeitzone) → ``melden``: im Zweifel rufen, nie still warten."""
    eintrag = {"grund": "review_offen", "fingerprint": FP, "zeit": zeit}
    assert mensch_noetig.review_stand(eintrag, datetime.now().astimezone()) == "melden"


def test_review_offen_zeit_in_zukunft_ruft_sofort() -> None:
    """Uhr gesprungen: Eintrag liegt in der Zukunft → sofort rufen, nicht warten, bis die Uhr aufholt."""
    jetzt = datetime.now().astimezone()
    zukunft = (jetzt + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S%z")
    eintrag = {"grund": "review_offen", "fingerprint": FP, "zeit": zukunft}
    assert mensch_noetig.review_stand(eintrag, jetzt) == "melden"


def test_ruecknahme_dry_run_sendet_nichts_und_ruf_bleibt(welt: dict[str, Path], datei: Path) -> None:
    """dry_run: Rücknahme nur angezeigt, kein Kommentar, gemerkter Ruf bleibt stehen."""
    _review_offen(welt, datei, vor_min=mensch_noetig.REVIEW_WARTEZEIT_MIN + 1)
    _capo(welt)
    assert len(_rufe(welt)) == 1
    _beleg_schreiben(welt)
    ergebnis = _capo(welt, "--dry-run")
    assert "Ruf nach dem Menschen zurückgenommen" in _text(ergebnis), _text(ergebnis)
    assert not [k for k in _kommentare(welt, "902") if "zurückgenommen" in k]
    assert len(_rufe(welt)) == 1


def test_ruecknahme_gh_fehler_bricht_nicht_ab_und_ruf_bleibt(
    welt: dict[str, Path], datei: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gh-Fehler bei der Rücknahme: Aufseher läuft durch, Ruf gilt nicht als zurückgenommen; später klappt es."""
    _review_offen(welt, datei, vor_min=mensch_noetig.REVIEW_WARTEZEIT_MIN + 1)
    normal = _capo(welt)
    _beleg_schreiben(welt)
    monkeypatch.setenv("GH_STUB_FEHLER", "comment")
    ergebnis = _capo(welt)
    assert ergebnis.returncode == normal.returncode, _text(ergebnis)
    assert "Traceback" not in _text(ergebnis), _text(ergebnis)
    assert not [k for k in _kommentare(welt, "902") if "zurückgenommen" in k]
    assert len(_rufe(welt)) == 1
    monkeypatch.delenv("GH_STUB_FEHLER")
    _capo(welt)
    assert len([k for k in _kommentare(welt, "902") if "zurückgenommen" in k]) == 1
    assert _rufe(welt) == []


def test_merkliste_verwirft_alte_eintraege_beim_schreiben(fake_gh: FakeGh, monkeypatch: pytest.MonkeyPatch) -> None:
    """``_gesendet`` wächst im Dauerlauf nicht: Einträge jenseits des Zeitfensters fliegen beim Schreiben raus."""
    uhr = [1000.0]
    monkeypatch.setattr(befund, "_uhr", lambda: uhr[0])
    monkeypatch.setattr(befund, "_gesendet", {})
    for n in range(100):
        befund.kommentiere(600 + n, "test/wegwerf", f"{befund.KOPF} Merkliste 530.")
        uhr[0] += befund.DOPPELT_SEKUNDEN + 1
    assert len(befund._gesendet) == 1
    assert len(fake_gh.aufrufe) == 100
