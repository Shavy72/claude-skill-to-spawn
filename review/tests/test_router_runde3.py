"""Router Runde 3: Secrets in reinen Datenzeilen (Tupel/Listen) bleiben sichtbar."""

from __future__ import annotations

import sys
from pathlib import Path

REVIEW = Path(__file__).resolve().parent.parent
if str(REVIEW) not in sys.path:
    sys.path.insert(0, str(REVIEW))

from _gemeinsam import SIGNALE, DateiDiff, signal_trifft  # noqa: E402


def treffer(pfad: str, hinzu: list[str], tmp_path: Path) -> set[str]:
    d = DateiDiff(pfad=pfad, zeilen=len(hinzu), hinzu=hinzu)
    return {s.id for s in SIGNALE if signal_trifft(s, tmp_path, d)}


def test_passwort_tupel_ist_sicherheit(tmp_path: Path) -> None:
    ids = treffer("app/config.py", ['CREDS = ("password", "hunter2")'], tmp_path)
    assert "sicherheit" in ids


def test_authorization_header_liste_ist_sicherheit(tmp_path: Path) -> None:
    zeile = 'HEADERS = [("Authorization", "Bearer sk-abc123")]'
    assert "sicherheit" in treffer("app/config.py", [zeile], tmp_path)


def test_katalog_ohne_geheimwort_bleibt_still(tmp_path: Path) -> None:
    ids = treffer("review/katalog.py", ['_X = ("migration", "boot")'], tmp_path)
    assert "datenbank" not in ids
    assert "kosten" not in ids
