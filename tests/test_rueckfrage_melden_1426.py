"""#1426 (Katalog I4): Rechte-Dialog im stillen Fenster wird im Aufseher-Takt gemeldet.

Ein „Do you want to proceed?“ hielt eine Session ~10 h an. Der Aufseher-Takt (Leiter)
sah zwar „Rückfrage“, gab aber immer „nichts“ zurück. Soll: ab
``aufpasser.RUECKFRAGE_MIN`` Minuten meldet die Leiter (Exit 1, Zeile „braucht
David“), getippt wird weiter nie.

Echt: Fenster-Erkennung ``aufseher_stand._fenster_lage`` (Marker des Aufpassers),
Leiter-Entscheidung und Leitstand. Attrappe nur: ``tmux capture-pane`` und die Lage-Naht.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from to_spawn import aufpasser, aufseher_stand, leiter, leitstand

SKILL = Path("/nicht/benutzt")
SPEC = 1279
TICKET = 1301
ZIEL = "=spec-1279:@7"
MINUTE = 60.0
DIALOG = (
    "● Bash(git push origin ticket/1301)\n"
    "  Do you want to proceed?\n"
    "  ❯ 1. Yes\n"
    "    2. Yes, and don't ask again\n"
    "    3. No, and tell Claude what to do differently (esc)\n"
)


class Werkzeug:
    def __init__(self) -> None:
        self.getippt: list[tuple[str, str]] = []

    def tippen(self, ziel: str, text: str) -> None:
        self.getippt.append((ziel, text))


class Umwelt:
    """Lage aus der echten Fenster-Erkennung über ein tmux-Fake."""

    def __init__(self, wt: Path, pane: str, still_min: float) -> None:
        self.wt = wt
        self.pane = pane
        self.uhr = time.time()
        self.aktiv = self.uhr - still_min * MINUTE
        self.wz = Werkzeug()

    def jetzt(self) -> float:
        return self.uhr

    def lage(self, spec: int, ticket: int, seit: float | None) -> leiter.Lage:
        q = aufseher_stand.Quellen(
            repo=self.wt,
            kinder=lambda _s: [],
            tmux=lambda _args: self.pane,
            jetzt=self.jetzt,
        )
        fenster, minuten, _ = aufseher_stand._fenster_lage(
            ticket, ZIEL, self.aktiv, q, aufseher_stand._Vorher(), self.uhr
        )
        return leiter.Lage(
            offen=True,
            fenster=fenster,
            still_min=minuten,
            kontext_k=None,
            prompt_datei=False,
            ziel=ZIEL,
        )

    def handoff_k(self) -> float:
        return 250.0

    def worktree(self, ticket: int) -> Path:
        return self.wt

    def werkzeug(self) -> Any:
        return self.wz


@pytest.fixture
def wt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    ordner = tmp_path / "wt"
    (ordner / "docs" / "handoffs").mkdir(parents=True)
    return ordner


def test_dialog_im_stillen_fenster_wird_gemeldet(wt: Path) -> None:
    u = Umwelt(wt, DIALOG, still_min=600)
    erg = leiter.eingreifen(SKILL, SPEC, TICKET, umwelt=u)
    assert erg.exit == leiter.EXIT_FEHLER, erg.zeile
    assert "Rückfrage" in erg.zeile and "braucht David" in erg.zeile
    assert "600 min" in erg.zeile
    assert u.wz.getippt == []
    assert leitstand.leiter_eintrag(TICKET)[0] == 0


def test_dialog_frisch_noch_nicht_gemeldet(wt: Path) -> None:
    u = Umwelt(wt, DIALOG, still_min=aufpasser.RUECKFRAGE_MIN - 1)
    erg = leiter.eingreifen(SKILL, SPEC, TICKET, umwelt=u)
    assert erg.exit == leiter.EXIT_OK, erg.zeile
    assert u.wz.getippt == []


def test_grenze_aus_dem_aufpasser() -> None:
    lage = leiter.Lage(
        offen=True,
        fenster=aufseher_stand.RUECKFRAGE,
        still_min=aufpasser.RUECKFRAGE_MIN,
        kontext_k=None,
        prompt_datei=False,
    )
    schritt = leiter.entscheide(lage, leiter.Gemerkt(0, None), time.time(), 250.0)
    assert schritt.aktion == "melden"


# --- Fix-Runde 1: Aufrufkette Aufseher-Takt → Leiter für Rückfrage-Fenster -----------
# Der Aufseher-Takt ist eine Claude-Session; ihr Eingang ist der Prompt aus
# ``skripte/wache.py``. Die Leiter (Befehl C) läuft nur dort, wo der Prompt sie nennt —
# ``handoff_blick`` ruft sie nur für stille Fenster. Ohne Verweis im Abschnitt
# „Rückfragen-Fenster“ wäre der Zweig „braucht David“ aus ac9f9d9 ein toter Pfad.


def _wache_prompt() -> str:
    import importlib.util

    datei = Path(__file__).resolve().parent.parent / "skripte" / "wache.py"
    spec = importlib.util.spec_from_file_location("wache_rueckfrage_1426", datei)
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


def test_aufseher_ruft_leiter_auch_fuer_rueckfrage_fenster() -> None:
    abschnitt = _abschnitt(_wache_prompt(), "Rückfragen-Fenster (Freigabe-Dialoge)")
    assert "Befehl C" in abschnitt, abschnitt
    assert "braucht David" in abschnitt, abschnitt
    # Grenze nicht doppelt im Prompt: sie lebt nur im Aufpasser.
    assert f"{aufpasser.RUECKFRAGE_MIN} min" not in abschnitt


def test_kette_rueckfrage_20_min_endet_bei_braucht_david(wt: Path) -> None:
    """Fenster „Rückfrage seit 20 min“: Prompt schickt zur Leiter, Leiter meldet."""
    assert "Befehl C" in _abschnitt(
        _wache_prompt(), "Rückfragen-Fenster (Freigabe-Dialoge)"
    )
    u = Umwelt(wt, DIALOG, still_min=20)
    erg = leiter.eingreifen(SKILL, SPEC, TICKET, umwelt=u)
    assert erg.exit == leiter.EXIT_FEHLER, erg.zeile
    assert "braucht David" in erg.zeile and "20 min" in erg.zeile
    assert u.wz.getippt == []
