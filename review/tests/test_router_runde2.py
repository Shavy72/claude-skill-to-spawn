"""Router Runde 2: Pfad-Wortgrenzen und Literale/Kommentare im Zeilen-Match."""

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


def test_pfad_teilwort_ist_kein_sicherheit(tmp_path: Path) -> None:
    assert "sicherheit" not in treffer("web/kontrolle.py", ["x = 1"], tmp_path)


def test_pfad_ganzes_wort_ist_sicherheit(tmp_path: Path) -> None:
    assert "sicherheit" in treffer("web/auth/login.py", ["x = 1"], tmp_path)
    assert "sicherheit" in treffer("web/user_role.py", ["x = 1"], tmp_path)


def test_pfad_env_datei_ist_sicherheit(tmp_path: Path) -> None:
    assert "sicherheit" in treffer("config/.env", ["A=1"], tmp_path)


def test_pfad_kosten_teilwort_nicht(tmp_path: Path) -> None:
    assert "kosten" not in treffer("web/reboot_log.py", ["x = 1"], tmp_path)


def test_string_literal_katalog_feuert_nicht(tmp_path: Path) -> None:
    ids = treffer("review/katalog.py", ['_X = ("migration", "boot")'], tmp_path)
    assert "datenbank" not in ids
    assert "kosten" not in ids


def test_kommentar_feuert_nicht(tmp_path: Path) -> None:
    ids = treffer("app.py", ["x = 1  # boot und migration", "y = 2 // proxy"], tmp_path)
    assert "datenbank" not in ids
    assert "kosten" not in ids


def test_echter_aufruf_ist_datenbank(tmp_path: Path) -> None:
    assert "datenbank" in treffer("app.py", ["account_db.set_status(x)"], tmp_path)


def test_env_literal_greift_weiter_auf_rohzeile(tmp_path: Path) -> None:
    assert "sicherheit" in treffer("app.py", ['load_dotenv(".env")'], tmp_path)


def test_fehlerpfad_or_leerstring_bleibt(tmp_path: Path) -> None:
    assert "fehlerpfad" in treffer("app.py", ['name = wert or ""'], tmp_path)
