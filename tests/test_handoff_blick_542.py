"""#542: Handoff-Blick — Erkennung per Autor-Zeit, ``aktuell`` / ``pruefe`` getrennt.

Gleiche Welt wie ``test_eigener_handoff_542.py``: echtes git-Repo (Worktree) und echtes
Bau-Log in ``tmp_path``. Der Rebase läuft als echter ``git rebase`` — nur
``GIT_COMMITTER_DATE`` ist gesetzt, die Autor-Zeit bleibt die des Original-Commits.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem Erst-Test und wird als Parameter genannt.
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from test_eigener_handoff_542 import (  # noqa: F401 — ``welt`` ist ein Fixture
    HANDOFF,
    JETZT,
    MINUTE,
    SPEC,
    TICKET,
    _git,
    _handoff,
    welt,
)

from to_spawn import aufseher_stand, eigener_handoff, handoff_blick, prozessbaum


def _zeiten(wt: Path) -> tuple[int, int]:
    """(Autor-Zeit, Committer-Zeit) des letzten Commits, der den Handoff berührt."""
    roh = subprocess.run(
        ["git", "-C", str(wt), "log", "-1", "--format=%at %ct", "--", HANDOFF],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    return int(roh[0]), int(roh[1])


def _rebase_auf_neue_basis(wt: Path, committer_zeit: float) -> None:
    """Echter ``git rebase`` des Handoff-Commits auf einen neuen Basis-Commit.

    Nur ``GIT_COMMITTER_DATE`` wird gesetzt — wie bei einem Rebase in der Bau-Session:
    die Committer-Zeit springt auf „jetzt“, die Autor-Zeit bleibt.
    """
    zweig = subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git(wt, "checkout", "-q", "-b", "basis", "HEAD~1")
    (wt / "basis.txt").write_text("neue Basis\n", encoding="utf-8")
    _git(wt, "add", "basis.txt")
    _git(wt, "commit", "-q", "-m", "chore: neue Basis", zeit=committer_zeit)
    _git(wt, "checkout", "-q", zweig)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE")
    }
    env["GIT_COMMITTER_DATE"] = f"@{int(committer_zeit)} +0000"
    subprocess.run(
        ["git", "-C", str(wt), "rebase", "-q", "basis"],
        check=True,
        capture_output=True,
        env=env,
    )


def test_rebase_alter_handoff_zaehlt_nicht(welt: dict) -> None:
    """Rot vor Fix: Rebase setzt die Committer-Zeit neu — alter Handoff sah frisch aus."""
    _handoff(welt["wt"], JETZT - 90 * MINUTE)  # vor dem Sitzungsstart (JETZT - 60 min)
    _rebase_auf_neue_basis(welt["wt"], JETZT - 5 * MINUTE)
    autor, committer = _zeiten(welt["wt"])
    assert autor == int(JETZT - 90 * MINUTE)  # Rebase lässt die Autor-Zeit gleich
    assert committer == int(JETZT - 5 * MINUTE)  # … und setzt die Committer-Zeit neu
    assert eigener_handoff.pruefe(welt["haupt"], TICKET, JETZT) is None
    assert eigener_handoff.aktuell(welt["haupt"], TICKET) is None


def test_rebase_frischer_handoff_zaehlt_weiter(welt: dict) -> None:
    """Ein Handoff aus dieser Sitzung bleibt nach dem Rebase gültig (Autor-Zeit zählt)."""
    _handoff(welt["wt"], JETZT - 10 * MINUTE)
    _rebase_auf_neue_basis(welt["wt"], JETZT - 1 * MINUTE)
    treffer = eigener_handoff.pruefe(welt["haupt"], TICKET, JETZT)
    assert treffer == eigener_handoff.Treffer(HANDOFF, float(int(JETZT - 10 * MINUTE)))


def test_aktuell_vor_faelligkeit_pruefe_erst_ab_faellig_ab(welt: dict) -> None:
    """``aktuell`` liefert den Handoff schon vor der Fälligkeit, ``pruefe`` erst ab
    ``faellig_ab`` (= Handoff-Zeit + ``STILL_MIN`` Minuten)."""
    _handoff(welt["wt"], JETZT - 1 * MINUTE)
    treffer = eigener_handoff.aktuell(welt["haupt"], TICKET)
    assert treffer == eigener_handoff.Treffer(HANDOFF, float(int(JETZT - MINUTE)))
    assert treffer.faellig_ab == treffer.commit_zeit + eigener_handoff.STILL_MIN * 60
    assert eigener_handoff.pruefe(welt["haupt"], TICKET, JETZT) is None
    assert eigener_handoff.pruefe(welt["haupt"], TICKET, treffer.faellig_ab - 1) is None
    assert eigener_handoff.pruefe(welt["haupt"], TICKET, treffer.faellig_ab) == treffer


def test_aktuell_ohne_handoff_none(welt: dict) -> None:
    assert eigener_handoff.aktuell(welt["haupt"], TICKET) is None


# --- Blick-Faden (Bau-2a): Leiter binnen 2,5 min nach Fälligkeit ---------------------
#
# Echt: git-Repo + Worktree, Bau-Log, Leitstand, Manifest-Leser, Handoff-Erkennung
# (``eigener_handoff.aktuell``) und ``aufseher_stand.ticket_lage``. Attrappen nur für
# GitHub (Sub-Issues), tmux (Fenster-Liste + Bildschirm) und den Prozess-Start der Leiter.

STILL_BILD = "● Fertig, Handoff committet.\n\n❯ \n"
ARBEITS_BILD = "✻ Arbeite… (esc to interrupt)\n"
RUECKFRAGE_BILD = "Do you want to proceed?\n❯ 1. Yes\n"


class Aussen:
    """Attrappe für GitHub + tmux: zählt Aufrufe, Bildschirm und Ticket-Zustand setzbar."""

    def __init__(self) -> None:
        self.bild = STILL_BILD
        self.state = "open"
        self.gh_aufrufe = 0

    def kinder(self, spec: int) -> list[dict[str, Any]]:
        self.gh_aufrufe += 1
        return [{"number": TICKET, "state": self.state, "comments": 0}]

    def tmux(self, args: list[str]) -> str | None:
        if args[0] == "list-windows":
            return f"1\tbau {TICKET}\t{int(JETZT)}\n"
        if args[0] == "capture-pane":
            return self.bild
        return None


class ProbeNaht(handoff_blick.EchteNaht):
    """Echte Naht; nur der Prozess-Start der Leiter ist ersetzt (merkt den Aufruf)."""

    def __init__(self, haupt: Path, aussen: Aussen, code: int = 0) -> None:
        q = aufseher_stand.Quellen(
            repo=haupt,
            kinder=lambda spec: aussen.kinder(spec),
            tmux=aussen.tmux,
            jetzt=lambda: JETZT,
        )
        super().__init__(haupt, haupt, "test-org/test-repo", quellen=q)
        self.code = code
        self.leiter_aufrufe: list[tuple[int, int]] = []

    def leiter(self, spec: int, ticket: int) -> handoff_blick.LeiterLauf:
        self.leiter_aufrufe.append((spec, ticket))
        return handoff_blick.LeiterLauf(self.code, f"#{ticket}: Probe-Leiter")


def _manifest(haupt: Path) -> None:
    datei = haupt / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    datei.parent.mkdir(parents=True, exist_ok=True)
    daten = {"spec": SPEC, "tickets": {str(TICKET): {"titel": "Probe"}}}
    datei.write_text(json.dumps(daten), encoding="utf-8")


@pytest.fixture
def blick_welt(
    welt: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    """``welt`` + Manifest der Spec + Aufseher-Stand in tmp + Probe-Naht."""
    monkeypatch.setenv(aufseher_stand.STAND_ENV, str(tmp_path / "stand"))
    _manifest(welt["haupt"])
    aussen = Aussen()
    return {**welt, "aussen": aussen, "naht": ProbeNaht(welt["haupt"], aussen)}


def test_blick_rebase_alter_handoff_startet_keine_leiter(blick_welt: dict) -> None:
    """Alter Handoff (vor dem Sitzungsstart) + echter Rebase → keine Leiter, kein gh."""
    _handoff(blick_welt["wt"], JETZT - 90 * MINUTE)
    _rebase_auf_neue_basis(blick_welt["wt"], JETZT - 5 * MINUTE)
    blick = handoff_blick.Blick(SPEC, blick_welt["naht"])
    assert blick.einmal(JETZT) == handoff_blick.STANDARD_S
    assert blick_welt["naht"].leiter_aufrufe == []
    assert blick_welt["aussen"].gh_aufrufe == 0


def test_blick_simulierte_uhr_leiter_spaetestens_180s_nach_faelligkeit(
    blick_welt: dict,
) -> None:
    """Handoff-Commit 40 s nach einem Durchgang → Leiter ≤ 180 s nach Fälligkeit."""
    naht = blick_welt["naht"]
    blick = handoff_blick.Blick(SPEC, naht)
    jetzt = JETZT
    warte = blick.einmal(jetzt)  # Durchgang ohne Handoff
    assert warte == handoff_blick.STANDARD_S
    _handoff(blick_welt["wt"], jetzt + 40)  # Session committet 40 s später
    faellig_ab = float(int(jetzt + 40)) + eigener_handoff.STILL_MIN * 60
    aufruf_zeit: float | None = None
    for _ in range(20):
        assert 0 < warte <= handoff_blick.STANDARD_S
        jetzt += warte
        warte = blick.einmal(jetzt)
        if naht.leiter_aufrufe:
            aufruf_zeit = jetzt
            break
    assert naht.leiter_aufrufe == [(SPEC, TICKET)]
    assert aufruf_zeit is not None
    assert faellig_ab <= aufruf_zeit <= faellig_ab + 180


@pytest.mark.parametrize(
    ("bild", "state"),
    [
        (ARBEITS_BILD, "open"),  # Session arbeitet
        (RUECKFRAGE_BILD, "open"),  # offene Rückfrage
        (STILL_BILD, "closed"),  # Ticket zu
    ],
    ids=["arbeitet", "rueckfrage", "ticket_zu"],
)
def test_blick_faelliger_handoff_ohne_still_und_offen_keine_leiter(
    blick_welt: dict, bild: str, state: str
) -> None:
    blick_welt["aussen"].bild = bild
    blick_welt["aussen"].state = state
    _handoff(blick_welt["wt"], JETZT - 5 * MINUTE)
    blick = handoff_blick.Blick(SPEC, blick_welt["naht"])
    assert blick.einmal(JETZT) == handoff_blick.STANDARD_S
    assert blick_welt["aussen"].gh_aufrufe == 1  # fällig → Lage gefragt
    assert blick_welt["naht"].leiter_aufrufe == []


def test_blick_ohne_handoff_fragt_keine_lage(blick_welt: dict) -> None:
    blick = handoff_blick.Blick(SPEC, blick_welt["naht"])
    assert blick.einmal(JETZT) == handoff_blick.STANDARD_S
    assert blick_welt["aussen"].gh_aufrufe == 0
    assert blick_welt["naht"].leiter_aufrufe == []


def test_blick_nicht_faellig_fragt_keine_lage_und_wartet_bis_faelligkeit(
    blick_welt: dict,
) -> None:
    _handoff(blick_welt["wt"], JETZT - 30)
    blick = handoff_blick.Blick(SPEC, blick_welt["naht"])
    warte = blick.einmal(JETZT)
    faellig_ab = float(int(JETZT - 30)) + eigener_handoff.STILL_MIN * 60
    assert warte == pytest.approx(faellig_ab - JETZT)
    assert 0 < warte < handoff_blick.STANDARD_S
    assert blick_welt["aussen"].gh_aufrufe == 0
    assert blick_welt["naht"].leiter_aufrufe == []


def test_blick_gescheiterte_leiter_nur_einmal_versucht_und_gemeldet(
    blick_welt: dict, caplog: pytest.LogCaptureFixture
) -> None:
    naht = blick_welt["naht"]
    naht.code = 1
    _handoff(blick_welt["wt"], JETZT - 5 * MINUTE)
    blick = handoff_blick.Blick(SPEC, naht)
    with caplog.at_level(logging.DEBUG, logger=handoff_blick.log.name):
        for schritt in range(3):
            assert blick.einmal(JETZT + schritt * 150) == handoff_blick.STANDARD_S
    assert naht.leiter_aufrufe == [(SPEC, TICKET)]
    fehler = _laut(caplog)
    assert len(fehler) == 1
    assert str(TICKET) in fehler[0].getMessage()


def _laut(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Warnungen und Schlimmeres aus dem Blick-Modul (andere Module zählen nicht)."""
    return [
        r
        for r in caplog.records
        if r.name == handoff_blick.log.name and r.levelno >= logging.WARNING
    ]


class KaputteNaht:
    """Naht, deren Ticket-Liste bei jedem Aufruf wirft (Manifest weg …)."""

    def __init__(self) -> None:
        self.aufrufe = 0

    def tickets(self, spec: int) -> list[int]:
        self.aufrufe += 1
        raise RuntimeError("Manifest unlesbar")

    def aktuell(self, ticket: int) -> eigener_handoff.Treffer | None:
        raise AssertionError("nie erreicht")

    def lage(self, spec: int, ticket: int) -> aufseher_stand.TicketLage | None:
        raise AssertionError("nie erreicht")

    def leiter(self, spec: int, ticket: int) -> handoff_blick.LeiterLauf:
        raise AssertionError("nie erreicht")


def test_blick_einmal_wirft_nie(caplog: pytest.LogCaptureFixture) -> None:
    naht = KaputteNaht()
    blick = handoff_blick.Blick(SPEC, naht)
    with caplog.at_level(logging.DEBUG, logger=handoff_blick.log.name):
        assert blick.einmal(JETZT) == handoff_blick.STANDARD_S
        assert blick.einmal(JETZT + 150) == handoff_blick.STANDARD_S
    assert naht.aufrufe == 2
    fehler = _laut(caplog)
    assert len(fehler) == 1  # gleicher Fehler nur einmal laut


def test_blick_lage_wirft_gibt_standard_zurueck(blick_welt: dict) -> None:
    """gh fehlt (Ausnahme aus der echten Lage-Abfrage) → Standard-Wartezeit, keine Leiter."""

    def gh_kaputt(spec: int) -> list[dict[str, Any]]:
        raise aufseher_stand.GhFehlt("gh nicht gefunden")

    blick_welt["aussen"].kinder = gh_kaputt
    _handoff(blick_welt["wt"], JETZT - 5 * MINUTE)
    blick = handoff_blick.Blick(SPEC, blick_welt["naht"])
    assert blick.einmal(JETZT) == handoff_blick.STANDARD_S
    assert blick_welt["naht"].leiter_aufrufe == []


class ZaehlNaht(KaputteNaht):
    """Naht ohne Tickets, meldet jeden Durchgang über ein Event."""

    def __init__(self) -> None:
        super().__init__()
        self.durchgang = threading.Event()

    def tickets(self, spec: int) -> list[int]:
        self.aufrufe += 1
        self.durchgang.set()
        return []


def _blick_faeden(spec: int) -> list[threading.Thread]:
    return [
        f
        for f in threading.enumerate()
        if f.name == f"handoff-blick-{spec}" and f.is_alive()
    ]


def test_laeuft_startet_und_stoppt_faden_sauber() -> None:
    naht = ZaehlNaht()
    beginn = time.monotonic()
    with handoff_blick.laeuft(SPEC, naht) as blick:
        assert isinstance(blick, handoff_blick.Blick)
        assert naht.durchgang.wait(5), "Faden hat keinen Durchgang gemacht"
        faeden = _blick_faeden(SPEC)
        assert len(faeden) == 1 and faeden[0].daemon
    # Standard-Wartezeit 150 s — das Stopp-Signal weckt sofort statt auszuschlafen.
    assert time.monotonic() - beginn < 5
    assert _blick_faeden(SPEC) == []
    assert naht.aufrufe == 1


def test_echte_naht_leiter_kind_ohne_to_spawn_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Befehl C als eigener Prozess: ``to_spawn.py leiter <S> <N>``, cwd=ordner,
    stdin=DEVNULL, ohne Fenster, Kind-Env ohne ``TO_SPAWN_REPO`` — die eigene bleibt."""
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path / "fremd"))
    gesehen: dict[str, Any] = {}

    def run(args: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        gesehen["args"], gesehen["kw"] = args, kw
        kw["stdout"].write(f"#{TICKET}: Stufe 3 — Folge ab Handoff\n".encode())
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(handoff_blick.subprocess, "run", run)
    ordner = tmp_path / "ordner"
    naht = handoff_blick.EchteNaht(tmp_path / "haupt", ordner, "o/r")
    lauf = naht.leiter(SPEC, TICKET)
    assert lauf == handoff_blick.LeiterLauf(0, f"#{TICKET}: Stufe 3 — Folge ab Handoff")
    args, kw = gesehen["args"], gesehen["kw"]
    assert args[0] == sys.executable
    assert Path(args[1]).name == "to_spawn.py" and Path(args[1]).is_file()
    assert args[2:] == ["leiter", str(SPEC), str(TICKET), "--gh-repo", "o/r"]
    assert kw["cwd"] == str(ordner)
    assert kw["stdin"] is subprocess.DEVNULL
    assert "TO_SPAWN_REPO" not in kw["env"]
    for schluessel, wert in prozessbaum.ohne_fenster().items():
        assert kw[schluessel] == wert
    assert os.environ["TO_SPAWN_REPO"] == str(tmp_path / "fremd")


def test_echte_naht_leiter_echter_prozess_meldet_fehler(welt: dict) -> None:
    """Weg-Test: echter Kind-Prozess ``to_spawn.py leiter`` im Ordner ohne GitHub-Origin
    → Exit 1 mit der einen Leiter-Zeile (Befehlszeile passt zum echten Parser)."""
    naht = handoff_blick.EchteNaht(welt["haupt"], welt["haupt"], "")
    lauf = naht.leiter(SPEC, TICKET)
    assert lauf.code == 1
    assert f"#{TICKET}" in lauf.zeile
    assert "GitHub-Repo unbekannt" in lauf.zeile


def test_wache_main_startet_blick_vor_fahre_und_stoppt_danach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``wache.main`` umschließt alle Aufseher-Runden mit dem Handoff-Blick (#542):
    Blick an → ``fahre`` (auch nach einer Ablösung) → Blick aus. Die Naht ist die echte
    ``EchteNaht`` mit Repo-Wurzel, Aufseher-Ordner und GitHub-Slug aus ``wache``."""
    import importlib.util
    from contextlib import contextmanager

    quelle = Path(__file__).resolve().parent.parent / "skripte" / "wache.py"
    modul_spec = importlib.util.spec_from_file_location("wache_blick_542", quelle)
    assert modul_spec is not None and modul_spec.loader is not None
    wache = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)

    reihe: list[str] = []
    naehte: list[Any] = []

    @contextmanager
    def laeuft(spec: int, naht: Any, **_kw: Any):  # noqa: ANN202
        assert spec == SPEC
        naehte.append(naht)
        reihe.append("blick_an")
        try:
            yield None
        finally:
            reihe.append("blick_aus")

    def fahre(**kw: Any) -> int:
        reihe.append("fahre")
        if reihe.count("fahre") == 1:  # erste Runde löst sich ab → zweite Runde
            (tmp_path / "h.md").write_text("Handoff\n", encoding="utf-8")
            Path(os.environ[wache.ABLOESE_ENV]).write_text(
                json.dumps({"handoff": str(tmp_path / "h.md")}), encoding="utf-8"
            )
        return 0

    monkeypatch.setattr(handoff_blick, "laeuft", laeuft)
    monkeypatch.setattr(wache.waechter_lauf, "fahre", fahre)
    monkeypatch.setattr(wache, "repo_slug_oder_abbruch", lambda: "o/r")
    monkeypatch.setattr(wache, "start_ordner", lambda: tmp_path)
    monkeypatch.setattr(wache, "auf_speicher_warten", lambda *a, **k: 0)
    monkeypatch.setattr(wache, "abloese_prompt", lambda *a, **k: "weiter")
    monkeypatch.setattr(wache.config, "sicherstellen", lambda *a, **k: None)
    monkeypatch.setattr(wache.config, "lade", lambda *a, **k: {})
    monkeypatch.setattr(wache.startklar, "gate", lambda *a, **k: 0)
    monkeypatch.setattr(wache.context_mode, "pruefen", lambda **k: tmp_path)
    monkeypatch.setattr(wache.vertrauen, "still_sicherstellen", lambda *a, **k: None)
    monkeypatch.setattr(wache.tempfile, "mkdtemp", lambda **k: str(tmp_path))
    # main() setzt/entfernt Umgebung — vorher registrieren, damit sie zurückgesetzt wird.
    for name in (
        "CLAUDE_CODE_FORCE_SESSION_PERSISTENCE",
        "BAU_UMZUG_DATEI",
        "TO_SPAWN_WACHE_SPEC",
        wache.ABLOESE_ENV,
    ):
        monkeypatch.setenv(name, "")
    monkeypatch.delenv("CLAUDE_CODE_CHILD_SESSION", raising=False)
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.setattr(sys, "argv", ["wache.py", str(SPEC)])

    assert wache.main() == 0
    assert reihe == ["blick_an", "fahre", "fahre", "blick_aus"]
    assert len(naehte) == 1 and isinstance(naehte[0], handoff_blick.EchteNaht)
    assert naehte[0].repo == wache.REPO_ORDNER
    assert naehte[0].ordner == tmp_path
    assert naehte[0].gh_repo == "o/r"
