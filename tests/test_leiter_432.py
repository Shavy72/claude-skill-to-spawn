"""Tests für die Eingriffs-Leiter (#432, Spec #399: E15, E16, E11).

Gestellt ist nur die Außenwelt über die Naht ``umwelt`` (Lage, Uhr, tmux-Tippen,
Handoff-Grenze). Leitstand (``TO_SPAWN_LEITSTAND_ORDNER`` = tmp_path) und die
Prompt-Datei-Prüfung (echte Dateien im Worktree) laufen echt. ``respawn.abloesen``
wird nur in den Stufe-3-Tests durch einen Spion ersetzt — die Leiter darf keinen
eigenen Neustart-Weg haben.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufseher_stand, leiter, leitstand, respawn

SPEC = 9399
TICKET = 9432
ZIEL = "=spec-9399:3"
MINUTE = 60.0


class FakeWerkzeug:
    """Zeichnet jedes Tippen auf (``tippen`` = Text, dann Enter getrennt)."""

    def __init__(self, *, wirft: bool = False) -> None:
        self.getippt: list[tuple[str, str]] = []
        self.wirft = wirft

    def tippen(self, ziel: str, text: str) -> None:
        if self.wirft:
            raise RuntimeError("tmux weg")
        self.getippt.append((ziel, text))


class FakeUmwelt:
    """Lage nach Vorgabe; Prompt-Datei über die echte respawn-Prüfung im tmp-Worktree."""

    def __init__(
        self,
        wt: Path,
        *,
        fenster: str = aufseher_stand.STILL,
        still_min: int | None = 0,
        offen: bool | None = True,
        kontext_k: float | None = None,
        handoff_k: float = 250.0,
        werkzeug: FakeWerkzeug | None = None,
    ) -> None:
        self.wt = wt
        self.fenster = fenster
        self.still_min = still_min
        self.offen = offen
        self.kontext_k = kontext_k
        self.grenze = handoff_k
        self.uhr = time.time()
        self.wz = werkzeug or FakeWerkzeug()

    def jetzt(self) -> float:
        return self.uhr

    def lage(self, spec: int, ticket: int, seit: float | None) -> leiter.Lage:
        prompt = seit is not None and respawn.start_prompt_da(self.wt, ticket, seit)
        return leiter.Lage(
            offen=self.offen,
            fenster=self.fenster,
            still_min=self.still_min,
            kontext_k=self.kontext_k,
            prompt_datei=prompt,
            ziel=ZIEL,
        )

    def handoff_k(self) -> float:
        return self.grenze

    def worktree(self, ticket: int) -> Path:
        return self.wt

    def werkzeug(self) -> Any:
        return self.wz


@pytest.fixture
def ordner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    wt = tmp_path / "wt"
    (wt / "docs" / "handoffs").mkdir(parents=True)
    return wt


def _lauf(u: FakeUmwelt, **kw: Any) -> leiter.Ergebnis:
    erg = leiter.eingreifen(SKILL, SPEC, TICKET, umwelt=u, **kw)
    assert "\n" not in erg.zeile and erg.zeile.startswith(f"leiter #{TICKET}:")
    assert "  " not in erg.zeile
    return erg


def _start_datei(wt: Path, mtime: float) -> Path:
    tag = time.strftime("%Y-%m-%d", time.localtime(mtime))
    pfad = wt / "docs" / "handoffs" / f"START_{tag}_{TICKET}.txt"
    pfad.write_text("Weiter mit dem Ticket.", encoding="utf-8")
    os.utime(pfad, (mtime, mtime))
    return pfad


# --- Schwelle Stufe 0 → 1: 20 Minuten still ------------------------------------------


def test_still_19_min_nichts(ordner: Path) -> None:
    u = FakeUmwelt(ordner, still_min=19)
    erg = _lauf(u)
    assert erg.exit == 0
    assert u.wz.getippt == []
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    assert "19" in erg.zeile


def test_still_20_min_anstupsen(ordner: Path) -> None:
    u = FakeUmwelt(ordner, still_min=20)
    erg = _lauf(u)
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET) == (1, u.uhr)
    assert len(u.wz.getippt) == 1
    ziel, text = u.wz.getippt[0]
    assert ziel == ZIEL
    assert text == leiter.MINDSET_TEXT.format(min=20)
    assert "Staging" in text and "Smart Zone" in text
    assert erg.zeile.startswith(f"leiter #{TICKET}: Stufe 1 angestupst")


def test_doppel_eingriff_nur_ein_stupser(ordner: Path) -> None:
    u = FakeUmwelt(ordner, still_min=25)
    _lauf(u)
    u.uhr += 60
    _lauf(u)
    assert len(u.wz.getippt) == 1


# --- Schwelle Stufe 1 → 2: 15 Minuten nach dem Stupser -------------------------------


def _nach_stupser(wt: Path, minuten: float) -> FakeUmwelt:
    u = FakeUmwelt(wt, still_min=20)
    _lauf(u)
    u.wz.getippt.clear()
    u.uhr += minuten * MINUTE
    u.still_min = int(20 + minuten)
    return u


def test_stufe1_14_min_nichts(ordner: Path) -> None:
    u = _nach_stupser(ordner, 14)
    erg = _lauf(u)
    assert erg.exit == 0
    assert u.wz.getippt == []
    assert leitstand.leiter_eintrag(TICKET)[0] == 1


def test_stufe1_15_min_handoff(ordner: Path) -> None:
    u = _nach_stupser(ordner, 15)
    erg = _lauf(u)
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET) == (2, u.uhr)
    assert len(u.wz.getippt) == 1
    text = u.wz.getippt[0][1]
    assert text == respawn.handoff_auftrag_fuer(ordner, TICKET, u.uhr)
    tag = time.strftime("%Y-%m-%d", time.localtime(u.uhr))
    assert f"START_{tag}_{TICKET}.txt" in text
    assert erg.zeile.startswith(f"leiter #{TICKET}: Stufe 2")


# --- Handoff-Grenze --------------------------------------------------------------------


def test_handoff_grenze_erreicht(ordner: Path) -> None:
    u = FakeUmwelt(ordner, still_min=1, kontext_k=250.0)
    erg = _lauf(u)
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET)[0] == 2
    assert len(u.wz.getippt) == 1


def test_handoff_grenze_knapp_darunter(ordner: Path) -> None:
    u = FakeUmwelt(ordner, still_min=1, kontext_k=249.9)
    _lauf(u)
    assert u.wz.getippt == []
    assert leitstand.leiter_eintrag(TICKET)[0] == 0


# --- Prompt-Datei → Stufe 3 = respawn ---------------------------------------------------


class Spion:
    def __init__(self, exit_code: int = 0) -> None:
        self.aufrufe: list[tuple[Any, ...]] = []
        self.exit_code = exit_code

    def __call__(
        self, repo: Path, spec: int, ticket: int, **kw: Any
    ) -> respawn.Ergebnis:
        self.aufrufe.append((repo, spec, ticket, kw))
        return respawn.Ergebnis(self.exit_code, f"respawn #{ticket}: Testzeile")


def _stufe2(wt: Path) -> FakeUmwelt:
    u = FakeUmwelt(wt, still_min=1, kontext_k=260.0)
    _lauf(u)
    assert leitstand.leiter_eintrag(TICKET)[0] == 2
    u.wz.getippt.clear()
    u.uhr += 5 * MINUTE
    return u


def test_prompt_datei_frisch_respawn(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spion = Spion()
    monkeypatch.setattr(respawn, "abloesen", spion)
    u = _stufe2(ordner)
    _start_datei(ordner, u.uhr - MINUTE)
    erg = _lauf(u)
    assert len(spion.aufrufe) == 1
    assert spion.aufrufe[0][1:3] == (SPEC, TICKET)
    assert spion.aufrufe[0][3]["werkzeug"] is u.wz
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET) == (0, None)


def test_prompt_datei_respawn_scheitert_stufe2_bleibt(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spion = Spion(exit_code=2)
    monkeypatch.setattr(respawn, "abloesen", spion)
    u = _stufe2(ordner)
    _start_datei(ordner, u.uhr - MINUTE)
    erg = _lauf(u)
    assert erg.exit == 2
    assert leitstand.leiter_eintrag(TICKET)[0] == 2
    assert "respawn #" in erg.zeile


def test_prompt_datei_fehlt_nichts(
    ordner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spion = Spion()
    monkeypatch.setattr(respawn, "abloesen", spion)
    u = _stufe2(ordner)
    erg = _lauf(u)
    assert spion.aufrufe == []
    assert u.wz.getippt == []
    assert erg.exit == 0
    assert leitstand.leiter_eintrag(TICKET)[0] == 2


def test_prompt_datei_alt_nichts(ordner: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spion = Spion()
    monkeypatch.setattr(respawn, "abloesen", spion)
    u = _stufe2(ordner)
    seit = leitstand.leiter_eintrag(TICKET)[1]
    assert seit is not None
    _start_datei(ordner, seit - 10 * MINUTE)
    _lauf(u)
    assert spion.aufrufe == []


def test_start_prompt_da_direkt(ordner: Path) -> None:
    jetzt = time.time()
    assert not respawn.start_prompt_da(ordner, TICKET, jetzt)
    _start_datei(ordner, jetzt - 10 * MINUTE)
    assert not respawn.start_prompt_da(ordner, TICKET, jetzt)
    _start_datei(ordner, jetzt + 1)
    assert respawn.start_prompt_da(ordner, TICKET, jetzt)


def test_leiter_hat_keinen_eigenen_startweg() -> None:
    quelle = (SKILL / "to_spawn" / "leiter.py").read_text(encoding="utf-8")
    for verboten in ("bau_startzeile", "fenster_starten", "new-window", "neustart"):
        assert verboten not in quelle, verboten
    assert "respawn.abloesen(" in quelle


# --- Ticket zu → /exit -------------------------------------------------------------------


def test_ticket_zu_exit_einmal(ordner: Path) -> None:
    u = FakeUmwelt(ordner, still_min=3, offen=False)
    erg = _lauf(u)
    assert erg.exit == 0
    assert u.wz.getippt == [(ZIEL, "/exit")]
    assert leitstand.leiter_eintrag(TICKET)[0] == 4
    u.uhr += 5 * MINUTE
    _lauf(u)
    assert u.wz.getippt == [(ZIEL, "/exit")]


# --- Arbeitende Session / Rückfrage: nie tippen ------------------------------------------


@pytest.mark.parametrize(
    "einstellung",
    [
        {"still_min": 90},
        {"still_min": None, "kontext_k": 280.0},
        {"still_min": 30, "offen": False},
    ],
)
def test_arbeitende_session_nie_angestupst(
    ordner: Path, einstellung: dict[str, Any]
) -> None:
    u = FakeUmwelt(ordner, fenster=aufseher_stand.ARBEITET, **einstellung)
    erg = _lauf(u)
    assert erg.exit == 0
    assert u.wz.getippt == []
    assert leitstand.leiter_eintrag(TICKET)[0] == 0


def test_rueckfrage_nie_getippt(ordner: Path) -> None:
    u = FakeUmwelt(
        ordner, fenster=aufseher_stand.RUECKFRAGE, still_min=60, kontext_k=300
    )
    _lauf(u)
    assert u.wz.getippt == []


@pytest.mark.parametrize(
    "fenster", [aufseher_stand.KEIN_FENSTER, aufseher_stand.FENSTER_UNBEKANNT]
)
def test_kein_fenster_nichts(ordner: Path, fenster: str) -> None:
    u = FakeUmwelt(ordner, fenster=fenster, still_min=60, offen=False)
    _lauf(u)
    assert u.wz.getippt == []


def test_stufe1_arbeitet_zurueck_auf_0(ordner: Path) -> None:
    u = _nach_stupser(ordner, 3)
    u.fenster = aufseher_stand.ARBEITET
    _lauf(u)
    assert u.wz.getippt == []
    assert leitstand.leiter_eintrag(TICKET) == (0, None)


def test_stufe2_arbeitet_bleibt(ordner: Path) -> None:
    u = _stufe2(ordner)
    u.fenster = aufseher_stand.ARBEITET
    _lauf(u)
    assert leitstand.leiter_eintrag(TICKET)[0] == 2


# --- Reine Entscheidung ------------------------------------------------------------------


def test_entscheide_rein() -> None:
    lage = leiter.Lage(True, aufseher_stand.STILL, 20, None, False)
    assert (
        leiter.entscheide(lage, leiter.Gemerkt(0, None), 0.0, 250).aktion == "anstupsen"
    )
    lage19 = leiter.Lage(True, aufseher_stand.STILL, 19, None, False)
    assert (
        leiter.entscheide(lage19, leiter.Gemerkt(0, None), 0.0, 250).aktion == "nichts"
    )
    assert leiter.STUPS_MIN == 20 and leiter.NACH_STUPS_MIN == 15


# --- Dry-Run, Fehler ----------------------------------------------------------------------


def test_dry_run_tippt_und_merkt_nichts(ordner: Path) -> None:
    u = FakeUmwelt(ordner, still_min=30)
    erg = _lauf(u, dry_run=True)
    assert erg.exit == 0
    assert u.wz.getippt == []
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    assert "anstupsen" in erg.zeile


def test_tmux_wirft_exit1_eine_zeile(ordner: Path) -> None:
    u = FakeUmwelt(ordner, still_min=30, werkzeug=FakeWerkzeug(wirft=True))
    erg = _lauf(u)
    assert erg.exit == 1
    assert leitstand.leiter_eintrag(TICKET)[0] == 0


def test_leitstand_kaputt_exit1_eine_zeile(ordner: Path) -> None:
    datei = Path(os.environ["TO_SPAWN_LEITSTAND_ORDNER"])
    datei.mkdir(parents=True, exist_ok=True)
    (datei / "zustand.json").write_text("{kaputt", encoding="utf-8")
    u = FakeUmwelt(ordner, still_min=30)
    erg = _lauf(u)
    assert erg.exit == 1
    assert u.wz.getippt == []


# --- Leitstand-Format ---------------------------------------------------------------------


def test_leitstand_altes_und_neues_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path))
    leitstand.setze_leiter_stufe(1, 2)
    assert leitstand.leiter_stufe(1) == 2
    assert leitstand.leiter_eintrag(1) == (2, None)
    leitstand.setze_leiter_stufe(1, 1, 123.5)
    assert leitstand.leiter_stufe(1) == 1
    assert leitstand.leiter_eintrag(1) == (1, 123.5)
    # Altes Format: nackte Zahl je Ticket.
    leitstand.aendere_zustand(
        lambda z: z.setdefault("leiter_stufe", {}).__setitem__("7", 4)
    )
    assert leitstand.leiter_stufe(7) == 4
    assert leitstand.leiter_eintrag(7) == (4, None)
    roh = json.loads((tmp_path / "zustand.json").read_text(encoding="utf-8"))
    assert roh["leiter_stufe"]["1"] == {"stufe": 1, "seit": 123.5}


# --- Handoff-Grenze aus der SSOT ----------------------------------------------------------


def test_handoff_grenze_aus_smart_zone(tmp_path: Path) -> None:
    datei = tmp_path / "smart-zone.json"
    datei.write_text(json.dumps({"haupt": {"handoff_k": 222}}), encoding="utf-8")
    assert leiter.handoff_grenze(datei) == 222.0
    assert leiter.handoff_grenze(tmp_path / "fehlt.json") == leiter.HANDOFF_K_VORGABE
    (tmp_path / "kaputt.json").write_text("{", encoding="utf-8")
    assert leiter.handoff_grenze(tmp_path / "kaputt.json") == leiter.HANDOFF_K_VORGABE


# --- CLI ------------------------------------------------------------------------------------


def _cli_modul() -> Any:
    spec = importlib.util.spec_from_file_location(
        "to_spawn_cli_432", SKILL / "to_spawn.py"
    )
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_cli_dry_run_genau_eine_zeile(
    ordner: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    u = FakeUmwelt(ordner, still_min=30)
    monkeypatch.setattr(leiter, "echte_umwelt", lambda repo, gh_repo: u)
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    code = _cli_modul().main(
        ["leiter", str(SPEC), str(TICKET), "--dry-run", "--gh-repo", "a/b"]
    )
    raus = capsys.readouterr().out
    assert code == 0
    assert raus.endswith("\n") and raus.count("\n") == 1, repr(raus)
    assert raus.startswith(f"leiter #{TICKET}:")
    assert u.wz.getippt == []


def test_cli_umwelt_fehler_exit1_eine_zeile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def kaputt(repo: Path, gh_repo: str) -> Any:
        raise aufseher_stand.GhFehlt("gh nicht gefunden")

    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "ls"))
    monkeypatch.setattr(leiter, "echte_umwelt", kaputt)
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    code = _cli_modul().main(["leiter", str(SPEC), str(TICKET), "--gh-repo", "a/b"])
    raus = capsys.readouterr().out
    assert code == 1
    assert raus.count("\n") == 1 and "gh nicht gefunden" in raus
