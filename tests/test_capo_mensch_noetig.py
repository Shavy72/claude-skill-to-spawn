"""Tests: capo liest „Mensch nötig“-Einträge des Review-Stop-Hooks.

Echt laufen: Git-Repo mit Bare-Origin, die CLI ``skripte/capo.py``, die echte
Zustandsdatei und eine echte ``mensch_noetig.jsonl`` (Env ``REVIEW_MENSCH_NOETIG``).
Gestellt ist nur ``gh`` (Ersatz aus #213).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus test_waechter_213 und wird als Parameter genannt.
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _capo,
    _gh_zustand,
    _kommentare,
    _text,
    welt,
)


@pytest.fixture()
def datei(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> Path:
    pfad = welt["tmp"] / "review" / "mensch_noetig.jsonl"
    pfad.parent.mkdir()
    monkeypatch.setenv("REVIEW_MENSCH_NOETIG", str(pfad))
    monkeypatch.delenv("GH_STUB_FEHLER", raising=False)
    # #465: Einträge zählen nur aus dem eigenen Lauf (Hauptbaum oder eigener Worktree).
    monkeypatch.setattr(sys.modules[__name__], "_REPO", str(welt["repo"]))
    return pfad


_REPO = "C:/x"


def _eintrag(
    spec: str | None = SPEC,
    ticket: str | None = "902",
    fp: str = "abcdef0123456789",
    zeit: str = "2026-09-24T22:00:00+0200",
    repo: str | None = None,
) -> str:
    return json.dumps(
        {
            "zeit": zeit,
            "session_id": "sitzung-1",
            "repo": repo or _REPO,
            "fingerprint": fp,
            "fix_runde": 3,
            "spec": spec,
            "ticket": ticket,
        }
    )


def _schreibe(pfad: Path, *zeilen: str) -> None:
    pfad.write_text("".join(z + "\n" for z in zeilen), encoding="utf-8")


def _mensch_kommentare(welt: dict[str, Path], nummer: str) -> list[str]:
    return [k for k in _kommentare(welt, nummer) if "Mensch nötig" in k]


def _zustand(welt: dict[str, Path]) -> dict:
    pfad = welt["zustand"] / f"test_wegwerf_{SPEC}.json"
    return json.loads(pfad.read_text(encoding="utf-8")) if pfad.is_file() else {}


def test_neuer_eintrag_zeile_und_ein_kommentar(welt: dict[str, Path], datei: Path) -> None:
    _schreibe(datei, _eintrag())
    erste = _capo(welt)
    assert (
        "MENSCH NÖTIG: #902 Review-Beleg nach 3 Fixrunden noch rot (fp abcdef01) — "
        "Kette läuft weiter, der Mensch prüft." in _text(erste)
    )
    kommentare = _mensch_kommentare(welt, "902")
    assert len(kommentare) == 1
    assert kommentare[0].startswith("Aufseher:")
    assert _gh_zustand(welt)["issues"]["902"]["state"] == "open"
    zweite = _capo(welt)
    assert "MENSCH NÖTIG" not in _text(zweite)
    assert len(_mensch_kommentare(welt, "902")) == 1


def test_fremde_spec_und_kaputte_zeile(welt: dict[str, Path], datei: Path) -> None:
    _schreibe(
        datei,
        _eintrag(spec="123", ticket="555"),
        "{kaputt",
        "",
        _eintrag(fp="1111222233334444"),
    )
    ergebnis = _capo(welt)
    assert "#555" not in _text(ergebnis)
    assert "(fp 11112222)" in _text(ergebnis)
    assert _mensch_kommentare(welt, "555") == []
    assert len(_mensch_kommentare(welt, "902")) == 1


def test_gh_fehler_wird_nicht_gemerkt(welt: dict[str, Path], datei: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _schreibe(datei, _eintrag())
    monkeypatch.setenv("GH_STUB_FEHLER", "comment")
    erste = _capo(welt)
    assert "MENSCH NÖTIG" in _text(erste)
    assert _mensch_kommentare(welt, "902") == []
    assert not _zustand(welt).get("mensch_noetig_gesehen")
    monkeypatch.delenv("GH_STUB_FEHLER")
    _capo(welt)
    assert len(_mensch_kommentare(welt, "902")) == 1


def test_ohne_ticket_kommentar_auf_spec(welt: dict[str, Path], datei: Path) -> None:
    _schreibe(datei, _eintrag(ticket=None))
    ergebnis = _capo(welt)
    assert f"MENSCH NÖTIG: #{SPEC} " in _text(ergebnis)
    assert len(_mensch_kommentare(welt, SPEC)) == 1


def test_dry_run_kein_kommentar_kein_zustand(welt: dict[str, Path], datei: Path) -> None:
    _schreibe(datei, _eintrag())
    ergebnis = _capo(welt, "--dry-run")
    assert "MENSCH NÖTIG: #902" in _text(ergebnis)
    assert _mensch_kommentare(welt, "902") == []
    assert not _zustand(welt).get("mensch_noetig_gesehen")


def test_review_offen_eigener_text(welt: dict[str, Path], datei: Path) -> None:
    """Modus hinweis: Review angefordert, nie belegt → nicht „Beleg rot“ melden (Runde 5)."""
    zeile = json.loads(_eintrag())
    zeile["grund"] = "review_offen"
    _schreibe(datei, json.dumps(zeile))
    text = _text(_capo(welt))
    assert "MENSCH NÖTIG: #902 Review angefordert, aber kein Beleg geschrieben" in text
    assert "rot" not in _mensch_kommentare(welt, "902")[0]


def test_gleicher_stand_mehrere_zeilen_ein_kommentar(welt: dict[str, Path], datei: Path) -> None:
    """Review-Befund: scharf schreibt je Stop eine Zeile (neue Zeit) → trotzdem nur 1 Kommentar."""
    _schreibe(datei, _eintrag(zeit="2026-09-24T22:00:00+0200"), _eintrag(zeit="2026-09-24T22:05:00+0200"))
    _capo(welt)
    _schreibe(datei, _eintrag(zeit="2026-09-24T22:00:00+0200"), _eintrag(zeit="2026-09-24T22:05:00+0200"),
              _eintrag(zeit="2026-09-24T22:10:00+0200"))
    _capo(welt)
    assert len(_mensch_kommentare(welt, "902")) == 1


def test_fremdes_repo_gleiche_spec_kein_kommentar_465(
    welt: dict[str, Path], datei: Path
) -> None:
    """#465: Probe-Lauf 2 bekam am blockierten #5 den Eintrag eines anderen Repos (gleiche
    Spec 1, gleiches Ticket 5, Worktree ``to-spawn-probe-srv-1/wt-5``) — fremd, nie melden."""
    fremd = welt["tmp"] / "anderes-repo" / "wt-902"
    zeile = json.loads(_eintrag(repo=fremd.as_posix()))
    zeile["grund"] = "review_offen"
    _schreibe(datei, json.dumps(zeile))
    ergebnis = _capo(welt)
    assert "MENSCH NÖTIG" not in _text(ergebnis)
    assert _mensch_kommentare(welt, "902") == []


def test_eigener_worktree_wird_gemeldet_465(welt: dict[str, Path], datei: Path) -> None:
    """Gegenprobe: Eintrag aus dem eigenen Worktree ``<wt-basis>/wt-902`` zählt."""
    _schreibe(datei, _eintrag(repo=(welt["wt"] / "wt-902").as_posix()))
    assert "MENSCH NÖTIG: #902 " in _text(_capo(welt))
    assert len(_mensch_kommentare(welt, "902")) == 1
