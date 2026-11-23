"""Beleg-Gültigkeit für Dateiteilmenge B ⊆ A (VERTRAG.md §5)."""

from __future__ import annotations

import sys
from pathlib import Path

REVIEW = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REVIEW))

from review_beleg import beleg_gueltig_pruefen, datei_hash, datei_hashes_berechnen  # noqa: E402
from _gemeinsam import DateiDiff  # noqa: E402

SHA = "abc1234"
FP_A = "fp-fuer-a-b"
FP_B = "fp-nur-b-anders-als-a"


def _beleg(dateien: list[str], datei_hashes: dict[str, str] | None, fingerprint: str = FP_A) -> dict:
    beleg = {"sha": SHA, "fingerprint": fingerprint, "dateien": dateien}
    if datei_hashes is not None:
        beleg["datei_hashes"] = datei_hashes
    return beleg


def test_datei_hash_stabil_und_inhaltsabhaengig() -> None:
    d1 = DateiDiff("x.py", 1, ["+a"], ["+a"])
    d2 = DateiDiff("x.py", 1, ["+a"], ["+a"])
    d3 = DateiDiff("x.py", 1, ["+b"], ["+b"])
    assert datei_hash(d1) == datei_hash(d2)
    assert datei_hash(d1) != datei_hash(d3)


def test_gleicher_fingerprint_ist_gueltig() -> None:
    beleg = _beleg(["a.py", "b.py"], {"a.py": "h1", "b.py": "h2"})
    assert beleg_gueltig_pruefen(beleg, SHA, FP_A, ["a.py", "b.py"], {"a.py": "h1", "b.py": "h2"})


def test_teilmenge_mit_gleichen_hashes_ist_gueltig() -> None:
    """B={a.py} ⊂ A={a.py,b.py}, anderer Gesamt-fingerprint, aber a.py-Hash gleich → gültig."""
    beleg = _beleg(["a.py", "b.py"], {"a.py": "h1", "b.py": "h2"}, fingerprint=FP_A)
    aktuelle_hashes = {"a.py": "h1"}
    assert beleg_gueltig_pruefen(beleg, SHA, FP_B, ["a.py"], aktuelle_hashes)


def test_teilmenge_mit_abweichendem_hash_ist_ungueltig() -> None:
    beleg = _beleg(["a.py", "b.py"], {"a.py": "h1", "b.py": "h2"}, fingerprint=FP_A)
    aktuelle_hashes = {"a.py": "h1-geaendert"}
    assert not beleg_gueltig_pruefen(beleg, SHA, FP_B, ["a.py"], aktuelle_hashes)


def test_datei_ausserhalb_des_belegs_ist_ungueltig() -> None:
    beleg = _beleg(["a.py"], {"a.py": "h1"}, fingerprint=FP_A)
    assert not beleg_gueltig_pruefen(beleg, SHA, FP_B, ["a.py", "c.py"], {"a.py": "h1", "c.py": "hx"})


def test_andere_sha_ist_ungueltig_trotz_gleicher_hashes() -> None:
    beleg = _beleg(["a.py"], {"a.py": "h1"}, fingerprint=FP_A)
    assert not beleg_gueltig_pruefen(beleg, "andere-sha", FP_B, ["a.py"], {"a.py": "h1"})


def test_alter_beleg_ohne_datei_hashes_faellt_auf_fingerprint_zurueck() -> None:
    """Abwärtskompatibel: kein datei_hashes-Feld → Teilmenge nie gültig, nur exakter fingerprint."""
    beleg = _beleg(["a.py", "b.py"], datei_hashes=None, fingerprint=FP_A)
    assert beleg_gueltig_pruefen(beleg, SHA, FP_A, ["a.py", "b.py"], {"a.py": "h1", "b.py": "h2"})
    assert not beleg_gueltig_pruefen(beleg, SHA, FP_B, ["a.py"], {"a.py": "h1"})


def test_datei_hashes_berechnen_baut_dict_je_datei() -> None:
    diffs = {"a.py": DateiDiff("a.py", 1, ["+a"], ["+a"]), "b.py": DateiDiff("b.py", 1, ["+b"], ["+b"])}
    ergebnis = datei_hashes_berechnen(["a.py", "b.py"], diffs)
    assert set(ergebnis) == {"a.py", "b.py"}
    assert ergebnis["a.py"] == datei_hash(diffs["a.py"])
