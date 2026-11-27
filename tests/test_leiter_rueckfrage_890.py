"""Rot-/Grün-Tests für Issue #890: Rückfrage-Eskalation im echten Weg und Nachfolger ab Handoff."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from to_spawn import (
    aufpasser,
    aufseher_stand,
    bau_log,
    briefkasten,
    eigener_handoff,
    leiter,
    leitstand,
    prozessbaum,
    respawn,
    spawn,
    ticket_zustand,
)

SKILL = Path(__file__).resolve().parent.parent
SPEC = 890
TICKET = 8901


class _FakeWerkzeug:
    """Minimales tmux-Werkzeug-Fake: zeichnet jedes Tippen auf."""

    def __init__(self) -> None:
        self.getippt: list[tuple[str, str]] = []

    def tippen(self, ziel: str, text: str) -> None:
        self.getippt.append((ziel, text))


class _FakeUmwelt:
    """Umwelt-Fake für End-to-End-Tests von :func:`leiter.eingreifen`."""

    def __init__(self, wt: Path, *, still_min: int | None, frage: str = "") -> None:
        self._wt = wt
        self._still_min = still_min
        self._frage = frage
        self.wz = _FakeWerkzeug()

    def jetzt(self) -> float:
        return 1_700_000_000.0

    def lage(self, spec: int, ticket: int, seit: float | None) -> leiter.Lage:
        return leiter.Lage(
            stufe="offen",
            fenster=aufseher_stand.RUECKFRAGE,
            still_min=self._still_min,
            kontext_k=100.0,
            prompt_datei=False,
            ziel=f"bau:{ticket}",
            frage=self._frage,
        )

    def handoff_k(self) -> float:
        return 250.0

    def worktree(self, ticket: int) -> Path:
        return self._wt

    def werkzeug(self) -> respawn.Werkzeug:
        return self.wz  # type: ignore[return-value]

    def folge_ab_handoff(self, spec: int, ticket: int, treffer: eigener_handoff.Treffer) -> int:
        return 0

    def frage_einwerfen(self, spec: int, ticket: int, frage: str) -> str:
        return "q-1"


def _lage(still_min: int | None, frage: str = "") -> leiter.Lage:
    return leiter.Lage(
        stufe="offen",
        fenster=aufseher_stand.RUECKFRAGE,
        still_min=still_min,
        kontext_k=120.0,
        prompt_datei=False,
        frage=frage,
    )


def _wache_prompt() -> str:
    datei = SKILL / "skripte" / "wache.py"
    spec = importlib.util.spec_from_file_location("wache_rueckfrage_890", datei)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    text: str = modul.prompt_bauen(SPEC, "o/r", 1800, {})
    return text


def _abschnitt(text: str, titel: str) -> str:
    kopf = f"## {titel}"
    assert kopf in text, titel
    rest = text.split(kopf, 1)[1]
    return rest.split("\n## ", 1)[0]


def test_rueckfrage_ohne_briefkasten_ab_grenze_meldet() -> None:
    schritt = leiter.entscheide(
        _lage(aufpasser.RUECKFRAGE_MIN),
        leiter.Gemerkt(0, None),
        time.time(),
        250.0,
    )
    assert schritt.aktion == "melden"
    assert "braucht den Menschen" in schritt.grund
    assert f"{aufpasser.RUECKFRAGE_MIN} min" in schritt.grund


def test_rueckfrage_ohne_briefkasten_unter_grenze_bleibt_nichts() -> None:
    schritt = leiter.entscheide(
        _lage(aufpasser.RUECKFRAGE_MIN - 1),
        leiter.Gemerkt(0, None),
        time.time(),
        250.0,
    )
    assert schritt.aktion == "nichts"


def test_rueckfrage_ohne_still_min_bleibt_nichts() -> None:
    schritt = leiter.entscheide(_lage(None), leiter.Gemerkt(0, None), time.time(), 250.0)
    assert schritt.aktion == "nichts"


def test_rueckfrage_mit_briefkasten_frage_bleibt_entscheider() -> None:
    schritt = leiter.entscheide(
        _lage(30, frage="Soll ich Variante A oder B bauen?"),
        leiter.Gemerkt(0, None),
        time.time(),
        250.0,
    )
    assert schritt.aktion == "entscheider"


def test_eingreifen_rueckfrage_20_min_meldet_ohne_tippen(tmp_path: Path) -> None:
    u = _FakeUmwelt(tmp_path, still_min=20)
    erg = leiter.eingreifen(SKILL, SPEC, TICKET, umwelt=u)
    assert erg.exit == leiter.EXIT_FEHLER, erg.zeile
    assert "braucht den Menschen" in erg.zeile
    assert "20 min" in erg.zeile
    assert u.wz.getippt == []


def test_wache_prompt_ruft_leiter_fuer_rueckfrage_fenster() -> None:
    abschnitt = _abschnitt(_wache_prompt(), "Rückfragen-Fenster (Freigabe-Dialoge)")
    assert "Befehl C" in abschnitt, abschnitt
    assert "braucht den Menschen" in abschnitt, abschnitt
    assert "harmlos" in abschnitt.lower(), abschnitt
    # Grenze nicht doppelt im Prompt: sie lebt nur im Aufpasser.
    assert f"{aufpasser.RUECKFRAGE_MIN} min" not in abschnitt


# --- Lücke 1 (#890): Rückfrage-Eskalation im echten Weg (_EchteUmwelt + fenster_blick) ---


_ECHTES_RECHTE_PANE = (
    "● Bash(git push origin ticket/8901)\n"
    "\n"
    "Do you want to proceed?\n"
    "❯ 1. Yes\n"
    "  2. Yes, and don't ask again for git push commands\n"
    "  3. No\n"
    "\n"
    "Esc to cancel\n"
)


def test_rueckfrage_bereits_weitergegeben_eskaliert_ab_grenze() -> None:
    frage = "Bash(git push origin ticket/8901) Do you want to proceed?"
    gemerkt = leiter.Gemerkt(0, None, leiter.frage_schluessel(frage))
    unter = leiter.entscheide(_lage(aufpasser.RUECKFRAGE_MIN - 1, frage=frage), gemerkt, time.time(), 250.0)
    assert unter.aktion == "nichts"
    ab = leiter.entscheide(_lage(aufpasser.RUECKFRAGE_MIN, frage=frage), gemerkt, time.time(), 250.0)
    assert ab.aktion == "melden"
    assert "braucht den Menschen" in ab.grund
    assert f"{aufpasser.RUECKFRAGE_MIN} min" in ab.grund


def test_echter_weg_rechte_dialog_eskaliert_ab_rueckfrage_min(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Echter Weg über ``_EchteUmwelt`` + ``aufseher_stand.Quellen`` mit realem Rechte-Dialog (#890)."""
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    monkeypatch.setenv("TO_SPAWN_AUFSEHER_STAND", str(tmp_path / "stand"))
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path))
    monkeypatch.delenv("TO_SPAWN_TICKET", raising=False)

    jetzt = 1_764_200_000.0
    still_min = 33
    assert still_min >= aufpasser.RUECKFRAGE_MIN

    def fake_tmux(args: list[str]) -> str | None:
        if args[:1] == ["list-windows"]:
            return f"@4\tbau {TICKET}\t{jetzt - still_min * 60}\t{tmp_path}\n"
        if args[:1] == ["display-message"]:
            return None
        if args[:1] == ["capture-pane"]:
            return _ECHTES_RECHTE_PANE
        return None

    quellen = aufseher_stand.Quellen(
        repo=tmp_path,
        kinder=lambda _s: [{"number": TICKET, "state": "open", "comments": 0, "labels": []}],
        tmux=fake_tmux,
        jetzt=lambda: jetzt,
    )
    umwelt = leiter._EchteUmwelt(tmp_path, quellen)
    wz = _FakeWerkzeug()
    umwelt._werkzeug = wz  # type: ignore[assignment]

    # Takt 1: echter Dialog wird als Frage erkannt und einmalig weitergegeben, nie getippt.
    takt1 = leiter.eingreifen(tmp_path, SPEC, TICKET, umwelt=umwelt)
    assert takt1.exit == leiter.EXIT_OK, takt1.zeile
    assert "Entscheider-Auftrag" in takt1.zeile, takt1.zeile
    assert len(briefkasten.offene([TICKET], hauptbaum=tmp_path)) == 1
    assert wz.getippt == []

    # Takt 2: Dialog unverändert ab RUECKFRAGE_MIN → muss mit Exit 1 eskalieren, nie tippen.
    takt2 = leiter.eingreifen(tmp_path, SPEC, TICKET, umwelt=umwelt)
    assert takt2.exit == leiter.EXIT_FEHLER, takt2.zeile
    assert "braucht den Menschen" in takt2.zeile, takt2.zeile
    assert f"{still_min} min" in takt2.zeile, takt2.zeile
    assert wz.getippt == []


# --- Lücken 2-4 (#890): Nachfolger ab eigenem Handoff (KEIN_FENSTER, Stufe 2, stufe=None) ---


_JETZT = 1_764_200_000.0
_MINUTE = 60.0
_HANDOFF = f"docs/handoffs/HANDOFF_2026-11-27_{TICKET}.md"


def _git(ordner: Path, *args: str, zeit: float | None = None) -> None:
    env = dict(os.environ)
    if zeit is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{int(zeit)} +0000"
    subprocess.run(
        ["git", "-C", str(ordner), *args],
        check=True,
        capture_output=True,
        env=env,
        **prozessbaum.fensterlos(),
    )


def _repo(ordner: Path) -> Path:
    ordner.mkdir(parents=True)
    _git(ordner, "init", "-q")
    _git(ordner, "config", "user.email", "t@t")
    _git(ordner, "config", "user.name", "t")
    (ordner / "README.md").write_text("x\n", encoding="utf-8")
    _git(ordner, "add", "README.md")
    _git(ordner, "commit", "-q", "-m", "start", zeit=_JETZT - 7200)
    return ordner


def _handoff_committen(wt: Path, zeit: float) -> None:
    datei = wt / _HANDOFF
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("# Handoff\nDie nächste Session macht weiter.\n", encoding="utf-8")
    _git(wt, "add", _HANDOFF)
    _git(wt, "commit", "-q", "-m", "docs: Handoff", zeit=zeit)


@pytest.fixture
def handoff_welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Hauptbaum + Ticket-Worktree, Leitstand in tmp, Session-Start vor 60 min."""
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path))
    haupt = _repo(tmp_path / "haupt")
    wt = _repo(tmp_path / f"wt-{TICKET}")
    gestartet: list[dict[str, Any]] = []

    def neustart(repo: Path, spec: int, ticket: int, konfig: dict[str, Any], **kw: Any) -> int:
        gestartet.append({"ticket": ticket, **kw})
        return 0

    monkeypatch.setattr(spawn, "neustart", neustart)
    ts = datetime.fromtimestamp(_JETZT - 60 * _MINUTE, tz=UTC).isoformat()
    log = bau_log.lauf_pfad(wt, TICKET)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"ts": ts, "typ": "session_start", "ticket": str(TICKET)}) + "\n",
        encoding="utf-8",
    )
    return {"haupt": haupt, "wt": wt, "gestartet": gestartet}


class _HandoffUmwelt:
    """Leiter-Umwelt mit echter Handoff-Suche und echtem Neustart-Weg."""

    def __init__(
        self,
        welt: dict[str, Any],
        *,
        fenster: str,
        still_min: int | None = None,
        stufe: ticket_zustand.Stufe | None = "offen",
        prompt_datei: bool = False,
    ) -> None:
        self.haupt: Path = welt["haupt"]
        self.wt: Path = welt["wt"]
        self.fenster = fenster
        self.still_min = still_min
        self.stufe = stufe
        self.prompt_datei = prompt_datei
        self.wz = _FakeWerkzeug()

    def jetzt(self) -> float:
        return _JETZT

    def lage(self, spec: int, ticket: int, seit: float | None) -> leiter.Lage:
        start = bau_log.letzter_session_start(self.wt, ticket)
        return leiter.Lage(
            stufe=self.stufe,
            fenster=self.fenster,
            still_min=self.still_min,
            kontext_k=None,
            prompt_datei=self.prompt_datei,
            ziel=None if self.fenster == aufseher_stand.KEIN_FENSTER else "=s:3",
            letzter_start=start,
            eigener_handoff=eigener_handoff.finde(self.wt, ticket, eigener_handoff.grenze(start, ticket)),
        )

    def handoff_k(self) -> float:
        return 250.0

    def worktree(self, ticket: int) -> Path:
        return self.wt

    def werkzeug(self) -> respawn.Werkzeug:
        return self.wz  # type: ignore[return-value]

    def folge_ab_handoff(self, spec: int, ticket: int, treffer: eigener_handoff.Treffer) -> int:
        return eigener_handoff.folge_starten(self.haupt, spec, ticket, treffer, sperren=False)

    def frage_einwerfen(self, spec: int, ticket: int, frage: str) -> str:
        return "q-1"


def _handoff_takt(welt: dict[str, Any], **kw: Any) -> leiter.Ergebnis:
    return leiter.eingreifen(welt["haupt"], SPEC, TICKET, umwelt=_HandoffUmwelt(welt, **kw))


def test_kein_fenster_mit_faelligem_handoff_startet_genau_einmal(handoff_welt: dict[str, Any]) -> None:
    _handoff_committen(handoff_welt["wt"], _JETZT - 10 * _MINUTE)
    erg1 = _handoff_takt(handoff_welt, fenster=aufseher_stand.KEIN_FENSTER)
    assert erg1.exit == leiter.EXIT_OK, erg1.zeile
    assert [g["handoff"] for g in handoff_welt["gestartet"]] == [_HANDOFF]
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    erg2 = _handoff_takt(handoff_welt, fenster=aufseher_stand.KEIN_FENSTER)
    assert erg2.exit == leiter.EXIT_OK, erg2.zeile
    assert len(handoff_welt["gestartet"]) == 1


@pytest.mark.parametrize("fenster", [aufseher_stand.STILL, aufseher_stand.KEIN_FENSTER])
def test_stufe2_ohne_start_datei_handoff_nach_anforderung_startet(handoff_welt: dict[str, Any], fenster: str) -> None:
    leitstand.setze_leiter_stufe(TICKET, 2, _JETZT - 15 * _MINUTE)
    _handoff_committen(handoff_welt["wt"], _JETZT - 10 * _MINUTE)
    erg = _handoff_takt(
        handoff_welt,
        fenster=fenster,
        still_min=25 if fenster == aufseher_stand.STILL else None,
        prompt_datei=False,
    )
    assert erg.exit == leiter.EXIT_OK, erg.zeile
    assert [g["handoff"] for g in handoff_welt["gestartet"]] == [_HANDOFF]
    assert leitstand.leiter_eintrag(TICKET) == (0, None)


@pytest.mark.parametrize("fenster", [aufseher_stand.STILL, aufseher_stand.KEIN_FENSTER])
def test_stufe2_ohne_start_datei_handoff_vor_anforderung_startet_nicht(
    handoff_welt: dict[str, Any], fenster: str
) -> None:
    leitstand.setze_leiter_stufe(TICKET, 2, _JETZT - 15 * _MINUTE)
    _handoff_committen(handoff_welt["wt"], _JETZT - 20 * _MINUTE)
    erg = _handoff_takt(
        handoff_welt,
        fenster=fenster,
        still_min=25 if fenster == aufseher_stand.STILL else None,
        prompt_datei=False,
    )
    assert erg.exit == leiter.EXIT_OK, erg.zeile
    assert handoff_welt["gestartet"] == []
    assert leitstand.leiter_eintrag(TICKET)[0] == 2


@pytest.mark.parametrize("mit_handoff", [False, True])
def test_stufe2_mit_start_datei_bleibt_respawn(mit_handoff: bool) -> None:
    treffer = eigener_handoff.Treffer(_HANDOFF, _JETZT - 10 * _MINUTE) if mit_handoff else None
    lage = leiter.Lage(
        stufe="offen",
        fenster=aufseher_stand.STILL,
        still_min=25,
        kontext_k=None,
        prompt_datei=True,
        eigener_handoff=treffer,
    )
    schritt = leiter.entscheide(lage, leiter.Gemerkt(2, _JETZT - 15 * _MINUTE), _JETZT, 250.0)
    assert schritt.aktion == "respawn"


@pytest.mark.parametrize("fenster", [aufseher_stand.STILL, aufseher_stand.KEIN_FENSTER])
@pytest.mark.parametrize("leiter_stufe", [0, 2])
def test_unbekannter_ticket_status_startet_nie_folge_handoff(
    handoff_welt: dict[str, Any], fenster: str, leiter_stufe: int
) -> None:
    if leiter_stufe:
        leitstand.setze_leiter_stufe(TICKET, leiter_stufe, _JETZT - 30 * _MINUTE)
    _handoff_committen(handoff_welt["wt"], _JETZT - 10 * _MINUTE)
    erg = _handoff_takt(
        handoff_welt,
        fenster=fenster,
        still_min=25 if fenster == aufseher_stand.STILL else None,
        stufe=None,
    )
    assert handoff_welt["gestartet"] == [], erg.zeile


@pytest.mark.parametrize("fenster", [aufseher_stand.STILL, aufseher_stand.KEIN_FENSTER])
def test_geschlossenes_ticket_startet_nie_folge_handoff(handoff_welt: dict[str, Any], fenster: str) -> None:
    _handoff_committen(handoff_welt["wt"], _JETZT - 10 * _MINUTE)
    erg = _handoff_takt(
        handoff_welt,
        fenster=fenster,
        still_min=25 if fenster == aufseher_stand.STILL else None,
        stufe="zu",
    )
    assert handoff_welt["gestartet"] == [], erg.zeile


def test_kein_fenster_folge_start_scheitert_meldet_und_versucht_erneut(
    handoff_welt: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    versuche: list[int] = []

    def scheitert(repo: Path, spec: int, ticket: int, konfig: dict[str, Any], **kw: Any) -> int:
        versuche.append(ticket)
        return 3

    monkeypatch.setattr(spawn, "neustart", scheitert)
    _handoff_committen(handoff_welt["wt"], _JETZT - 10 * _MINUTE)
    erg1 = _handoff_takt(handoff_welt, fenster=aufseher_stand.KEIN_FENSTER)
    assert erg1.exit == leiter.EXIT_FEHLER and "gescheitert" in erg1.zeile, erg1.zeile
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    erg2 = _handoff_takt(handoff_welt, fenster=aufseher_stand.KEIN_FENSTER)
    assert len(versuche) == 2 and erg2.exit == leiter.EXIT_FEHLER, erg2.zeile
