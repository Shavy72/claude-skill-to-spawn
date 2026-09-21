"""Wächter streng nach Spec #213 (Entscheidung 21.09., verify-hard Lauf 3).

Lauf 3 hat drei Ausnahmen als Scope-Creep gemeldet. Die Spec (Zeile 13) verlangt
ohne Ausnahme: „Regelverstöße … werden real erkannt und das betroffene Ticket
wieder geöffnet“, Zeile 18: „Tests nur ergänzt, keiner ersetzt“. Daraus folgt:

* Jedes erneute Schließen mit demselben Verstoß öffnet wieder — keine Einmal-Sperre.
* Label ``waechter:ok`` verhindert nur das Wieder-Öffnen, nicht das Erkennen:
  der Verstoß steht trotzdem im Tick-Bericht.
* Der Commit-Trailer ``Test-entfernt:`` entschuldigt keine entfernte Testfunktion.

Echt laufen Git, ``skripte/capo.py`` und die Zustandsdateien; gestellt ist nur ``gh``.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    _beleg,
    _capo,
    _commit,
    _gh_setzen,
    _iso,
    _kommentare,
    _text,
    welt,
)
from test_waechter_213_fix import _frisch_zu, _zustand, _zustand_datei


def test_zweites_schliessen_mit_demselben_verstoss_oeffnet_wieder(
    welt: dict[str, Path],
) -> None:
    _commit(welt["repo"], "feat: ohne Nummer", _beleg())
    _frisch_zu(welt, minuten=40)
    _capo(welt)
    assert _zustand(welt) == "open"
    assert len(_kommentare(welt, "901")) == 1

    # Jemand schließt erneut, ohne den Verstoß zu beheben.
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(timedelta(minutes=20)))
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _zustand(welt) == "open", _text(ergebnis)
    assert len(_kommentare(welt, "901")) == 2
    assert all(
        k.startswith("Wächter: commit_ohne_nummer") for k in _kommentare(welt, "901")
    )


def test_label_waechter_ok_meldet_den_verstoss_trotzdem(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: ohne Nummer", {})
    _frisch_zu(welt, labels=["waechter:ok"])
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _zustand(welt) == "closed"
    assert _kommentare(welt, "901") == []
    assert "commit_ohne_nummer" in ergebnis.stdout, _text(ergebnis)
    assert "waechter:ok" in ergebnis.stdout


def test_trailer_test_entfernt_entschuldigt_nichts(welt: dict[str, Path]) -> None:
    _commit(
        welt["repo"], "test: alt", {"tests/test_a.py": "def test_alt():\n    pass\n"}
    )
    _commit(
        welt["repo"],
        "refactor: Umbau (#901)\n\nTest-entfernt: test_alt prüfte die alte Tabelle",
        {"tests/test_a.py": "def test_neu():\n    pass\n", **_beleg()},
    )
    _frisch_zu(welt)
    _capo(welt)
    assert any(k.startswith("Wächter: test_ersetzt") for k in _kommentare(welt, "901"))
    assert _zustand(welt) == "open"


def test_label_waechter_ok_blockiert_spec_fertig_nicht(welt: dict[str, Path]) -> None:
    """Ein bewusst freigegebenes Ticket darf „Spec fertig“ nicht ewig verhindern (#213)."""
    # #901 ist freigegeben trotz Verstoß, #902 sauber gebaut und zu.
    _commit(welt["repo"], "feat: B (#902)", _beleg("902"))
    _frisch_zu(welt, labels=["waechter:ok"])
    _frisch_zu(welt, nummer="902")
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "commit_ohne_nummer" in ergebnis.stdout
    assert "SPEC FERTIG" in ergebnis.stdout, _text(ergebnis)


def test_zustand_merkt_sich_keine_wieder_geoeffnet_liste(welt: dict[str, Path]) -> None:
    """Die Einmal-Sperre ist weg — ihr Zustandsfeld darf nicht weiterwachsen (#213)."""
    _commit(welt["repo"], "feat: ohne Nummer", _beleg())
    _frisch_zu(welt, minuten=40)
    _capo(welt)
    daten = json.loads(_zustand_datei(welt).read_text(encoding="utf-8"))
    assert "wieder_geoeffnet" not in daten, sorted(daten)
