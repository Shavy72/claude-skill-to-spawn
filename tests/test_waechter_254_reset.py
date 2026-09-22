"""Reset-Uhrzeit lesen und Pause berechnen (duoplus-management#254).

Vorbild ``test_waechter_213_limit_echt.py``: Grundlage ist die unveränderte
Limit-Zeile aus einem echten Claude-Code-Transkript
(``hilfen/limit_zeile_echt.jsonl``, Session-Limit 18.09.2026). Alle Varianten
werden aus genau dieser Zeile abgeleitet — gleiche Datei-Form, nur die
entscheidenden Felder ändern sich.
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import bau_log, melder, waechter_lauf  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")
ECHT = Path(__file__).resolve().parent / "hilfen" / "limit_zeile_echt.jsonl"


def _echte() -> dict[str, Any]:
    return json.loads(ECHT.read_bytes())


def _ohne_quota(text: str | None = None) -> dict[str, Any]:
    """Gleiche Zeile, aber ohne ``quotaLimits`` — nur der Text trägt die Uhrzeit."""
    zeile = copy.deepcopy(_echte())
    zeile.pop("quotaLimits", None)
    if text is not None:
        zeile["message"]["content"] = [{"type": "text", "text": text}]
    return zeile


# --- Reset-Zeitpunkt ---------------------------------------------------------


def test_echte_zeile_liefert_den_reset_zeitpunkt() -> None:
    """``quotaLimits.resetsAt`` ist die genaue Quelle — 19:40 Berlin am 18.09.2026."""
    ziel = waechter_lauf.reset_zeitpunkt(_echte())
    assert ziel is not None
    assert ziel.astimezone(BERLIN) == datetime(2026, 9, 18, 19, 40, tzinfo=BERLIN)


def test_ohne_quota_wird_die_uhrzeit_aus_dem_text_gelesen() -> None:
    jetzt = datetime(2026, 9, 18, 18, 0, tzinfo=BERLIN)
    ziel = waechter_lauf.reset_zeitpunkt(_ohne_quota(), jetzt=jetzt)
    assert ziel is not None
    assert ziel.astimezone(BERLIN) == datetime(2026, 9, 18, 19, 40, tzinfo=BERLIN)


def test_uhrzeit_ueber_mitternacht_meint_den_naechsten_tag() -> None:
    zeile = _ohne_quota("You've hit your session limit · resets 5:40am (Europe/Berlin)")
    jetzt = datetime(2026, 9, 18, 23, 50, tzinfo=BERLIN)
    ziel = waechter_lauf.reset_zeitpunkt(zeile, jetzt=jetzt)
    assert ziel is not None
    assert ziel.astimezone(BERLIN) == datetime(2026, 9, 19, 5, 40, tzinfo=BERLIN)


def test_uhrzeit_schon_vorbei_meint_den_naechsten_tag() -> None:
    jetzt = datetime(2026, 9, 18, 20, 5, tzinfo=BERLIN)
    ziel = waechter_lauf.reset_zeitpunkt(_ohne_quota(), jetzt=jetzt)
    assert ziel is not None
    assert ziel.astimezone(BERLIN) == datetime(2026, 9, 19, 19, 40, tzinfo=BERLIN)


def test_uhrzeit_ohne_minuten_wird_gelesen() -> None:
    zeile = _ohne_quota("You've reached your Fable limit · resets 9pm (Europe/Berlin)")
    jetzt = datetime(2026, 9, 18, 18, 0, tzinfo=BERLIN)
    ziel = waechter_lauf.reset_zeitpunkt(zeile, jetzt=jetzt)
    assert ziel is not None
    assert ziel.astimezone(BERLIN) == datetime(2026, 9, 18, 21, 0, tzinfo=BERLIN)


def test_uhrzeit_in_fremder_zeitzone_wird_umgerechnet() -> None:
    zeile = _ohne_quota("You've hit your session limit · resets 7:40pm (America/New_York)")
    jetzt = datetime(2026, 9, 18, 18, 0, tzinfo=BERLIN)
    ziel = waechter_lauf.reset_zeitpunkt(zeile, jetzt=jetzt)
    assert ziel is not None
    assert ziel.astimezone(ZoneInfo("America/New_York")) == datetime(
        2026, 9, 18, 19, 40, tzinfo=ZoneInfo("America/New_York")
    )


def test_ohne_uhrzeit_gibt_es_keinen_zeitpunkt() -> None:
    zeile = _ohne_quota("You've hit your session limit")
    assert waechter_lauf.reset_zeitpunkt(zeile) is None


def test_kaputtes_resets_at_faellt_auf_den_text_zurueck() -> None:
    zeile = copy.deepcopy(_echte())
    zeile["quotaLimits"]["resetsAt"] = "bald"
    jetzt = datetime(2026, 9, 18, 18, 0, tzinfo=BERLIN)
    ziel = waechter_lauf.reset_zeitpunkt(zeile, jetzt=jetzt)
    assert ziel is not None
    assert ziel.astimezone(BERLIN) == datetime(2026, 9, 18, 19, 40, tzinfo=BERLIN)


def test_unbekannte_zeitzone_nimmt_die_ortszeit() -> None:
    zeile = _ohne_quota("You've hit your session limit · resets 7:40pm (Mittelerde/Auenland)")
    jetzt = datetime(2026, 9, 18, 18, 0, tzinfo=BERLIN)
    ziel = waechter_lauf.reset_zeitpunkt(zeile, jetzt=jetzt)
    assert ziel is not None
    assert ziel.hour == 19 and ziel.minute == 40


# --- Wartezeit ---------------------------------------------------------------


def test_wartezeit_rechnet_den_puffer_dazu() -> None:
    jetzt = datetime(2026, 9, 18, 19, 0, tzinfo=BERLIN)
    ziel = datetime(2026, 9, 18, 19, 40, tzinfo=BERLIN)
    assert waechter_lauf.warte_sekunden(ziel, jetzt=jetzt, puffer=120.0) == 40 * 60 + 120


def test_wartezeit_ueber_der_obergrenze_ist_keine() -> None:
    """Wochen-Limit: Reset Tage weg — dann lieber Mail wie heute als Endlos-Warten."""
    jetzt = datetime(2026, 9, 18, 19, 0, tzinfo=BERLIN)
    ziel = jetzt + timedelta(days=3)
    assert waechter_lauf.warte_sekunden(ziel, jetzt=jetzt, hoechstens=6 * 3600.0) is None


def test_wartezeit_in_der_vergangenheit_ist_nur_der_puffer() -> None:
    """Reset gerade vorbei (innerhalb RESET_TOLERANZ_S) — weiter geht es nach dem Puffer.
    Länger zurück heißt „andere Runde“ und ergibt keine Pause (Fixrunde 1, F1)."""
    jetzt = datetime(2026, 9, 18, 19, 40, 30, tzinfo=BERLIN)
    ziel = datetime(2026, 9, 18, 19, 40, tzinfo=BERLIN)
    assert waechter_lauf.warte_sekunden(ziel, jetzt=jetzt, puffer=30.0) == 30.0


def test_warten_bricht_bei_abbruch_sofort_ab() -> None:
    assert waechter_lauf._warten(30.0, 0.05, lambda: True) is False


def test_warten_laeuft_ohne_abbruch_durch() -> None:
    assert waechter_lauf._warten(0.05, 0.01, None) is True


# --- Katalog -----------------------------------------------------------------


def test_bau_log_kennt_pause_und_weiter() -> None:
    assert "waechter_pause" in bau_log.TYPEN
    assert "waechter_weiter" in bau_log.TYPEN


def test_pause_meldung_ist_kritisch() -> None:
    """Der Mensch muss sehen, dass der Wächter pausiert — nicht nur im Log."""
    assert melder.darf_raus("waechter_pause", {"mail": {"nur_kritisch": True}}) is True
