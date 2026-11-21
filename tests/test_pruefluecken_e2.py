"""L1 Etappe 2 E2-7 (#91): zwei Prüf-Lücken aus dem Review von P6b.

1. Hausmeister-Wurzeln mit verstecktem Ordner *nach* dem ersten Glob-Zeichen
   (``~/x/wt-*/.ssh``) fallen weg wie die mit verstecktem Ordner im festen Teil.
   ``~/wt-*/.ssh`` fiel schon vorher weg (fester Teil = Home selbst), steht zur Absicherung mit drin.
2. Der Prüf-Test „keine festen Namen“ meldet den Tausch eines erlaubten Fundes gegen
   einen anderen, auch wenn die Zahl der Treffer gleich bleibt.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_feste_namen_l1 import AUSNAHMEN, _treffer, _verstoesse, _zeilen_treffer
from test_hausmeister_l1_konfig import _home, _maschine, ohne_naehte  # noqa: F401 — Fixtures

from to_spawn import hausmeister


@pytest.mark.parametrize(
    "wurzel", ["~/wt-*/.ssh", "~/x/wt-*/.ssh", "~/x/*/.config/*", "~/x/wt-*/../.ssh/*"]
)
def test_versteckter_ordner_nach_glob_abgelehnt(
    ohne_naehte: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    wurzel: str,
) -> None:
    heim = _home(monkeypatch, tmp_path)
    _maschine({"repos": [], "hausmeister": {"wurzeln": [wurzel, "~/bau/*"]}})
    assert hausmeister._wurzeln() == [str(heim / "bau" / "*")]


def test_tausch_in_ausnahme_bei_gleicher_zahl_gemeldet() -> None:
    schluessel = ("to_spawn/capo.py", "duoplus")
    assert schluessel in AUSNAHMEN
    erlaubt = len(
        _treffer()[schluessel]
    )  # heute erlaubte Zahl, unabhängig vom Format der Ausnahme
    funde: dict[tuple[str, str], list[str]] = {}
    _zeilen_treffer(
        schluessel[0],
        "\n".join(['REPO = "Shavy72/duoplus-management"'] * erlaubt),
        funde,
    )
    assert len(funde[schluessel]) == erlaubt
    assert _verstoesse({schluessel: funde[schluessel]}), (
        "Tausch bei gleicher Zahl blieb unbemerkt"
    )
