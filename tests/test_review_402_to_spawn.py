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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kommentare: list[dict[str, Any]],
    login: Any,
    zeilen: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Probelauf des Checkpoints mit gestelltem GitHub (Kommentare + ``gh api user``)."""
    monkeypatch.setattr(capo, "issue_kommentare", lambda _repo, _n: kommentare)

    def json_lauf(args: list[str], cwd: Path | None = None) -> Any:
        if args[:2] == ["api", "user"]:
            return login
        if args[:1] == ["api"] and args[1].endswith("/dependencies/blocked_by"):
            return []  # #451: Checkpoint prüft vorher die Blocker — hier keine
        return None

    monkeypatch.setattr(gh, "json_lauf", json_lauf)
    monkeypatch.setattr(capo, "_SESSION_LOGIN", {}, raising=False)  # Zwischenspeicher je Test leer
    return capo._checkpoint(
        tmp_path,
        {"waechter": {"checkpoint_frist_min": 60}},
        "probe/repo",
        900,
        902,
        zeilen or [],
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
    # Angepasst (Nachschau R1): Login trennt nicht mehr, der Session-Kommentar trägt die Marke.
    zeilen = _checkpoint(monkeypatch, tmp_path, [_kommentar("bau-bot", SESSION_VORSCHLAG, 90)], {"login": "bau-bot"})
    assert any("würde Vorschlag annehmen: sofort aus." in z for z in zeilen), zeilen


def test_checkpoint_ohne_bekannten_login_nimmt_keinen_kommentar_vorschlag_an(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    zeilen = _checkpoint(monkeypatch, tmp_path, [_kommentar("bau-bot", VORSCHLAG, 90)], None)
    assert not any("würde Vorschlag annehmen" in z for z in zeilen), zeilen


def test_checkpoint_vorschlag_filtert_fremde_autoren() -> None:
    # Angepasst (Nachschau R1): kein session_login mehr — „fremd“ heißt jetzt: ohne Marke.
    kommentare = [_kommentar("bau-bot", f"{MARKE} Vorschlag: eigener", 90), _kommentar("fremder", "Vorschlag: fremd", 30)]
    vorschlag = capo.checkpoint_vorschlag([], kommentare)
    assert vorschlag is not None
    assert (vorschlag.wahl, vorschlag.autor) == ("eigener", "bau-bot")


# --- Befund 8, Nachschau R1/R2: gleiches gh-Konto für Session und David ----------
# Session und David kommentieren mit demselben Login (nest_push.sh kopiert Davids
# gh-Token). Nur die Marke capo.SESSION_KOPF trennt Session-Kommentare von David.

KONTO = "Shavy72"
MARKE = "Bau-Session:"
SESSION_VORSCHLAG = f"{MARKE} Frage: Soll der Abschalter sofort greifen?\n\nVorschlag: sofort aus."


def test_marke_ist_die_konstante_aus_capo() -> None:
    assert capo.SESSION_KOPF == MARKE


def test_r1_davids_nein_vom_gleichen_konto_verhindert_annahme(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    kommentare = [
        _kommentar(KONTO, SESSION_VORSCHLAG, 90),
        _kommentar(KONTO, "Nein, erst am Morgen abschalten.", 10),
    ]
    zeilen = _checkpoint(monkeypatch, tmp_path, kommentare, {"login": KONTO})
    assert not any("würde Vorschlag annehmen" in z for z in zeilen), zeilen
    assert any("hat geantwortet" in z for z in zeilen), zeilen


def test_r1_davids_eigener_vorschlag_ohne_marke_wird_nicht_angenommen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    zeilen = _checkpoint(monkeypatch, tmp_path, [_kommentar(KONTO, VORSCHLAG, 90)], {"login": KONTO})
    assert not any("würde Vorschlag annehmen" in z for z in zeilen), zeilen


def test_r1_session_vorschlag_mit_marke_wird_angenommen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    zeilen = _checkpoint(monkeypatch, tmp_path, [_kommentar(KONTO, SESSION_VORSCHLAG, 90)], {"login": KONTO})
    assert any("würde Vorschlag annehmen: sofort aus." in z for z in zeilen), zeilen


def test_r2_session_fortschritt_mit_marke_ist_keine_antwort_auf_bau_log_vorschlag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ts = (JETZT - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    bau_log = [{"typ": "entscheidung", "frage": "Abschalter?", "wahl": "sofort aus", "ts": ts}]
    kommentare = [_kommentar(KONTO, f"{MARKE} Zwischenstand: Tests laufen.", 30)]
    zeilen = _checkpoint(monkeypatch, tmp_path, kommentare, {"login": KONTO}, zeilen=bau_log)
    assert not any("hat geantwortet" in z for z in zeilen), zeilen
    assert any("würde Vorschlag annehmen: sofort aus" in z for z in zeilen), zeilen


def test_r2_davids_antwort_ohne_marke_auf_bau_log_vorschlag_zaehlt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ts = (JETZT - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    bau_log = [{"typ": "entscheidung", "frage": "Abschalter?", "wahl": "sofort aus", "ts": ts}]
    kommentare = [_kommentar(KONTO, "Nein.", 30)]
    zeilen = _checkpoint(monkeypatch, tmp_path, kommentare, {"login": KONTO}, zeilen=bau_log)
    assert any(f"{KONTO} hat geantwortet" in z for z in zeilen), zeilen


def test_bau_prompt_gibt_der_session_die_marke_vor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Die Marke lebt nur in capo; die Vorlage holt sie über {SESSION_KOPF}."""
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location("fremdrepo_257_hilfen", SKILL / "tests" / "test_fremdrepo_257.py")
    assert spec and spec.loader
    hilfen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hilfen)
    _git, _lade_bau = hilfen._git, hilfen._lade_bau

    vorlage = json.loads((SKILL / "repo-scripts" / "_default.json").read_text(encoding="utf-8"))["prompt_template"]
    assert "{SESSION_KOPF}" in vorlage and "Vorschlag: <eigene Wahl>" in vorlage
    repo = tmp_path / "projrepo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", "https://github.com/acme/produkt.git")
    ergebnis = _lade_bau(monkeypatch, repo).build_prompt(vorlage, "5", "1", "Titel", "Kontext", konfig={})
    assert "{SESSION_KOPF}" not in ergebnis
    assert f"„{capo.SESSION_KOPF}“" in ergebnis


# --- Befund 9: fehlende Fixrunde ------------------------------------------------


def test_mensch_noetig_ohne_fix_runde_zeigt_fragezeichen() -> None:
    assert capo.mensch_noetig_was({"grund": "beleg_rot"}) == "Review-Beleg nach ? Fixrunden noch rot"


def test_mensch_noetig_mit_fix_runde_und_review_offen() -> None:
    assert capo.mensch_noetig_was({"fix_runde": 3}) == "Review-Beleg nach 3 Fixrunden noch rot"
    assert capo.mensch_noetig_was({"grund": "review_offen"}) == "Review angefordert, aber kein Beleg geschrieben"


# --- Fix-Runde 2, S1: nur Kommentare von Repo-Beteiligten zählen ----------------
# Ein Dritter (öffentliches Repo) darf mit der Marke weder einen Vorschlag setzen
# noch ``seit`` hinter Davids Antwort schieben. GitHub liefert ``author_association``
# an jedem Kommentar; hier läuft der echte ``issue_kommentare`` über gestelltes gh.


def _gh_kommentar(autor: str, text: str, vor_min: float, rolle: str) -> dict[str, Any]:
    return {**_kommentar(autor, text, vor_min), "author_association": rolle}


def _checkpoint_ueber_gh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kommentare: list[dict[str, Any]]
) -> list[str]:
    """Wie ``_checkpoint``, aber die Kommentare kommen durch ``capo.issue_kommentare``."""

    def json_lauf(args: list[str], cwd: Path | None = None) -> Any:
        if args[:2] == ["api", "user"]:
            return {"login": KONTO}
        if args[:1] == ["api"] and "/comments" in args[1]:
            return kommentare
        if args[:1] == ["api"] and args[1].endswith("/dependencies/blocked_by"):
            return []  # #451: Checkpoint prüft vorher die Blocker — hier keine
        return None

    monkeypatch.setattr(gh, "json_lauf", json_lauf)
    monkeypatch.setattr(capo, "_SESSION_LOGIN", {}, raising=False)
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


@pytest.mark.parametrize("rolle", ["NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR"])
def test_s1_dritter_mit_marke_und_vorschlag_wird_nicht_angenommen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rolle: str
) -> None:
    kommentare = [_gh_kommentar("fremder", SESSION_VORSCHLAG, 90, rolle)]
    zeilen = _checkpoint_ueber_gh(monkeypatch, tmp_path, kommentare)
    assert not any("würde Vorschlag annehmen" in z for z in zeilen), zeilen


def test_s1_dritter_marken_kommentar_verschiebt_davids_antwort_nicht(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kommentare = [
        _gh_kommentar(KONTO, SESSION_VORSCHLAG, 150, "OWNER"),
        _gh_kommentar(KONTO, "Nein, erst am Morgen abschalten.", 100, "OWNER"),
        _gh_kommentar("fremder", f"{MARKE} Frage: x?\n\nVorschlag: sofort aus.", 30, "NONE"),
    ]
    zeilen = _checkpoint_ueber_gh(monkeypatch, tmp_path, kommentare)
    assert not any("würde Vorschlag annehmen" in z for z in zeilen), zeilen
    assert any(f"von {KONTO}" in z and "als Antwort gewertet" in z for z in zeilen), zeilen


@pytest.mark.parametrize("rolle", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_s1_session_vorschlag_von_beteiligtem_wird_angenommen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rolle: str
) -> None:
    zeilen = _checkpoint_ueber_gh(monkeypatch, tmp_path, [_gh_kommentar(KONTO, SESSION_VORSCHLAG, 90, rolle)])
    assert any("würde Vorschlag annehmen: sofort aus." in z for z in zeilen), zeilen


def test_s1_kommentar_ohne_author_association_zaehlt_nicht(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    zeilen = _checkpoint_ueber_gh(monkeypatch, tmp_path, [_kommentar(KONTO, SESSION_VORSCHLAG, 90)])
    assert not any("würde Vorschlag annehmen" in z for z in zeilen), zeilen


# --- Fix-Runde 2, G3: Meldung sagt, was wirklich erkannt wurde ------------------


def test_g3_antwort_meldung_nennt_kommentar_ohne_marke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    kommentare = [
        _kommentar(KONTO, SESSION_VORSCHLAG, 90),
        _kommentar(KONTO, "Probesitz beendet: alles grün.", 10),
    ]
    zeilen = _checkpoint(monkeypatch, tmp_path, kommentare, {"login": KONTO})
    assert any(
        f"Kommentar ohne Session-Marke von {KONTO}" in z and "Vorschlag nicht übernommen" in z for z in zeilen
    ), zeilen
