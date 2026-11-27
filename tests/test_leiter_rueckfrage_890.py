"""Rot-/Grün-Tests für Issue #890: Rückfrage-Dialog ohne Briefkasten-Frage eskalieren."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

from to_spawn import aufpasser, aufseher_stand, eigener_handoff, leiter, respawn

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
