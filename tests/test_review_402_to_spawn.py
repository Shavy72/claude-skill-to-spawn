"""Tests für Review-Befunde 2, 8 und 9 (#402) in ``to_spawn``.

2 — Der Takt-Lauf (``waechter_takt``) startet Claude mit derselben Denkstufe wie
    die Wache (``skripte/wache.py``): Quelle ist ``effort.waechter`` der Konfig.
8 — Ein Checkpoint-„Vorschlag:“ im Issue zählt nur, wenn ihn der gh-Login der
    Session geschrieben hat; fremde Autoren werden ignoriert.
9 — Alte „Mensch nötig“-Einträge ohne ``fix_runde`` ergeben „nach ? Fixrunden“,
    nie „nach None Fixrunden“.

GitHub ist ein externer Dienst: nur ``gh.json_lauf`` bzw. ``issue_kommentare``
werden gestellt, die Checkpoint-Logik läuft echt (Probelauf ohne Seiteneffekte).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import capo, gh, waechter_lauf, waechter_takt  # noqa: E402

JETZT = datetime(2026, 9, 28, 3, 0, tzinfo=timezone.utc)
VORSCHLAG = "Frage: Soll der Abschalter sofort greifen?\n\nVorschlag: sofort aus."


# --- Befund 2: Denkstufe im Takt-Lauf -----------------------------------------


def _effort_aus(cmd: list[str]) -> str | None:
    return cmd[cmd.index("--effort") + 1] if "--effort" in cmd else None


def test_takt_befehl_nimmt_effort_waechter_aus_konfig() -> None:
    cmd = waechter_takt.claude_befehl("claude", {"effort": {"waechter": "high"}}, 7)
    assert _effort_aus(cmd) == "high"
    assert cmd[-1] == "-p"


def test_takt_befehl_ohne_konfig_nimmt_dieselbe_vorgabe_wie_die_wache() -> None:
    cmd = waechter_takt.claude_befehl("claude", {}, 7)
    assert _effort_aus(cmd) == waechter_lauf.effort({}) == "medium"


# --- Befund 8: Checkpoint-Vorschlag nur vom Session-Login ----------------------


def _kommentar(autor: str, text: str, vor_min: float) -> dict[str, Any]:
    zeit = (JETZT - timedelta(minutes=vor_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"user": {"login": autor}, "body": text, "created_at": zeit}


def _checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kommentare: list[dict[str, Any]], login: Any
) -> list[str]:
    """Probelauf des Checkpoints mit gestelltem GitHub (Kommentare + ``gh api user``)."""
    monkeypatch.setattr(capo, "issue_kommentare", lambda _repo, _n: kommentare)

    def json_lauf(args: list[str], cwd: Path | None = None) -> Any:
        if args[:2] == ["api", "user"]:
            return login
        return None

    monkeypatch.setattr(gh, "json_lauf", json_lauf)
    monkeypatch.setattr(capo, "_SESSION_LOGIN", {}, raising=False)  # Zwischenspeicher je Test leer
    return capo._checkpoint(
        tmp_path,
        {"waechter": {"checkpoint_frist_min": 60}},
        "probe/repo",
        900,
        902,
        [],
        set(),
        checkpoint="checkpoint:human",
        jetzt=JETZT,
        dry_run=True,
        gelernt=[],
    )


def test_checkpoint_vorschlag_eines_fremden_autors_wird_nicht_angenommen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    zeilen = _checkpoint(monkeypatch, tmp_path, [_kommentar("fremder", VORSCHLAG, 90)], {"login": "bau-bot"})
    assert not any("würde Vorschlag annehmen" in z for z in zeilen), zeilen


def test_checkpoint_vorschlag_des_session_logins_wird_angenommen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    zeilen = _checkpoint(monkeypatch, tmp_path, [_kommentar("bau-bot", VORSCHLAG, 90)], {"login": "bau-bot"})
    assert any("würde Vorschlag annehmen: sofort aus." in z for z in zeilen), zeilen


def test_checkpoint_ohne_bekannten_login_nimmt_keinen_kommentar_vorschlag_an(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    zeilen = _checkpoint(monkeypatch, tmp_path, [_kommentar("bau-bot", VORSCHLAG, 90)], None)
    assert not any("würde Vorschlag annehmen" in z for z in zeilen), zeilen


def test_checkpoint_vorschlag_filtert_fremde_autoren() -> None:
    kommentare = [_kommentar("bau-bot", "Vorschlag: eigener", 90), _kommentar("fremder", "Vorschlag: fremd", 30)]
    vorschlag = capo.checkpoint_vorschlag([], kommentare, session_login="bau-bot")
    assert vorschlag is not None
    assert (vorschlag.wahl, vorschlag.autor) == ("eigener", "bau-bot")


# --- Befund 9: fehlende Fixrunde ------------------------------------------------


def test_mensch_noetig_ohne_fix_runde_zeigt_fragezeichen() -> None:
    assert capo.mensch_noetig_was({"grund": "beleg_rot"}) == "Review-Beleg nach ? Fixrunden noch rot"


def test_mensch_noetig_mit_fix_runde_und_review_offen() -> None:
    assert capo.mensch_noetig_was({"fix_runde": 3}) == "Review-Beleg nach 3 Fixrunden noch rot"
    assert capo.mensch_noetig_was({"grund": "review_offen"}) == "Review angefordert, aber kein Beleg geschrieben"
