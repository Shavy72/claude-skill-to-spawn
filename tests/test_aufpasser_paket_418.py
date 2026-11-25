"""Paket p418 (#418, #419, #434): Aufpasser-Startreihenfolge, Fenster-Log und Hintergrund-Marker.

Deckt drei Fehler des Aufpassers ab:
- #418: Fehlt der Aufseher (``wache <S>``), startet er vor den Bau-Fenstern; Tickets mit
  offenem Blocker (Regel: ``gh.blocker_erledigt``) werden beim Nachstart übersprungen.
- #419: ``Aufpasser.bau_befehl`` nutzt ``capo.bau_startzeile``, sodass auch vom Aufpasser
  gestartete Bau-Fenster ``.to-spawn/fenster_<N>.log`` schreiben.
- #434: Eine im Verlauf stehengebliebene Zeile ``✻ Waiting for 1 background agent to finish``
  sperrt ein stilles Fenster weder in ``aufpasser.arbeitet`` noch im Briefkasten-Wecker oder
  im Aufseher-Stand; ein aktiver Hintergrund-Helfer in der Statuszeile bleibt „arbeitet“.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser, aufseher_stand, capo

LINIE = "─" * 80


def _claude_bild(verlauf: str, status: str = "", eingabe: str = "") -> str:
    """Echtes Claude-Code-Bild: Verlauf, Rahmen + Eingabefeld, Statuszeile darunter."""
    status_zeilen = [
        "  Opus 5.5 (1M context) · 91.7k (9.0%) · 1h12m",
        "  📁 repo-418 · ⎇ HEAD · 5h: 10% (reset 03:20) · 7d: 58%",
        status or "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    ]
    return "\n".join(
        [
            verlauf,
            "",
            LINIE,
            f"❯ {eingabe}",
            LINIE,
            *status_zeilen,
            "",
        ]
    )


def _mini_repo(pfad: Path) -> Path:
    pfad.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(pfad), check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/probe/app.git"],
        cwd=str(pfad),
        check=True,
    )
    (pfad / ".to-spawn").mkdir(exist_ok=True)
    (pfad / ".to-spawn" / "config.json").write_text("{}", encoding="utf-8")
    return pfad


# --- #418: Aufseher startet zuerst, blockierte Tickets werden übersprungen ---


def test_418_aufseher_startet_zuerst_und_blockierte_tickets_werden_uebersprungen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fehlen Aufseher und Bau-Fenster: erst ``wache``, dann nur freie Tickets;
    offene Blocker überspringen ohne Start und ohne Staffel-Pause."""
    repo = _mini_repo(tmp_path / "repo-418")
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "zustand", repo=repo))

    gestartet: list[str] = []
    pausen: list[float] = []
    blocker_map: dict[str, list[dict[str, Any]]] = {
        "6": [],
        "7": [{"number": 6, "state": "open", "labels": []}],
        "8": [
            {"number": 5, "state": "closed", "labels": []},
            {"number": 9, "state": "open", "labels": [{"name": "staging:fertig"}]},
        ],
    }

    monkeypatch.setattr(
        a,
        "offene_tickets",
        lambda _s, _r: {
            n: {"number": n, "state": "open", "state_reason": None, "labels": []}
            for n in (6, 7, 8)
        },
    )
    monkeypatch.setattr(a, "ticket_lesen", lambda n, _r: {"number": n, "state": "open", "labels": set()})
    monkeypatch.setattr(a, "belegte_tickets", lambda: set())
    monkeypatch.setattr(aufpasser.gh, "blocked_by", lambda _slug, ticket, cwd=None: blocker_map[ticket])
    monkeypatch.setattr(aufpasser.speicher, "platz_frei", lambda *_a, **_k: (True, "frei"))
    monkeypatch.setattr(aufpasser.speicher, "staffel_s", lambda _k: 20)
    monkeypatch.setattr(aufpasser.time, "sleep", lambda s: pausen.append(s))
    monkeypatch.setattr(a, "fenster_starten", lambda _s, name, _c, _cmd: gestartet.append(name))
    monkeypatch.setattr(a, "_start_nachweisen", lambda _s, _n: True)
    monkeypatch.setattr(a, "melden", lambda *_a, **_k: None)
    monkeypatch.setattr(a, "fenster_pruefen", lambda *_a, **_k: None)

    a.sitzung_pruefen("spec-418", "418", [], repo)

    assert gestartet == ["wache 418", "bau 6", "bau 8"]
    # Drei echte Starts ⇒ genau zwei Staffel-Pausen (keine vor dem Aufseher, keine fürs blockierte #7).
    assert pausen == [20, 20]


def test_418_blocker_abfrage_fehler_sperrt_nicht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scheitert ``gh.blocked_by`` (``None``), startet das Fenster weiter — ``bau.py`` prüft selbst."""
    repo = _mini_repo(tmp_path / "repo-418")
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "zustand", repo=repo))
    gestartet: list[str] = []

    monkeypatch.setattr(
        a,
        "offene_tickets",
        lambda _s, _r: {6: {"number": 6, "state": "open", "state_reason": None, "labels": []}},
    )
    monkeypatch.setattr(a, "ticket_lesen", lambda n, _r: {"number": n, "state": "open", "labels": set()})
    monkeypatch.setattr(a, "belegte_tickets", lambda: set())
    monkeypatch.setattr(aufpasser.gh, "blocked_by", lambda *_a, **_k: None)
    monkeypatch.setattr(aufpasser.speicher, "platz_frei", lambda *_a, **_k: (True, "frei"))
    monkeypatch.setattr(aufpasser.time, "sleep", lambda _s: None)
    monkeypatch.setattr(a, "fenster_starten", lambda _s, name, _c, _cmd: gestartet.append(name))
    monkeypatch.setattr(a, "_start_nachweisen", lambda _s, _n: True)
    monkeypatch.setattr(a, "melden", lambda *_a, **_k: None)
    monkeypatch.setattr(a, "fenster_pruefen", lambda *_a, **_k: None)

    a.sitzung_pruefen("spec-418", "418", [], repo)

    assert gestartet == ["wache 418", "bau 6"]


def test_418_echter_lauf_startet_aufseher_vor_bau_und_laesst_blockiertes_weg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weg-Test mit echtem tmux: fehlender Aufseher startet vor ``bau 9002``,
    blockiertes ``9003`` (Vorgänger #9002 offen) bekommt kein Fenster."""
    from test_aufpasser_236 import (
        HILFEN,
        SITZUNG,
        SOCKET,
        SPEC,
        fenster_namen,
        fenster_ziel,
        git,
        tmux,
    )

    from conftest import attrappe, braucht_fehlt

    if grund := braucht_fehlt("tmux"):
        pytest.skip(f"braucht tmux: {grund}")

    monkeypatch.setattr(aufpasser, "START_NACHWEIS_S", 1.0)
    monkeypatch.setattr(aufpasser.speicher, "staffel_s", lambda _k: 0)
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.setenv("HOME", str(heim))
    binaer = tmp_path / "bin"
    binaer.mkdir()
    reihenfolge = tmp_path / "starts.txt"
    start_skript = binaer / "protokoll.sh"
    start_skript.write_text(
        f'#!/bin/bash\necho "$1" >> "{reihenfolge}"\nexec sleep 3600\n',
        encoding="utf-8",
    )
    start_skript.chmod(0o755)
    attrappe(
        binaer,
        "gh",
        f"""
        import json, os, sys
        if len(sys.argv) >= 3 and sys.argv[1] == "api" and "issues/9003/dependencies/blocked_by" in sys.argv[2]:
            print(json.dumps([{{"number": 9002, "state": "open", "labels": []}}]))
            sys.exit(0)
        os.execv({sys.executable!r}, [{sys.executable!r}, {str(HILFEN / "gh_stub.py")!r}, *sys.argv[1:]])
        """,
    )
    echt_tmux = shutil.which("tmux") or "tmux"
    attrappe(binaer, "tmux", f"import os, sys\nos.execv({echt_tmux!r}, [{echt_tmux!r}, *sys.argv[1:]])")
    monkeypatch.setenv("PATH", f"{binaer}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TO_SPAWN_TEST_BIN", str(binaer))
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002|9003")

    repo = _mini_repo(tmp_path / "repo")
    (repo / "README.md").write_text("Probe\n", encoding="utf-8")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Anfang")

    tmux("new-session", "-d", "-s", SITZUNG, "-n", "halter", "-c", str(repo), "sleep 3600")
    try:
        code = aufpasser.main(
            [
                "--tmux-socket",
                SOCKET,
                "--zustand",
                str(tmp_path / "zustand"),
                "--deploy-muster",
                f"aufpasser-probe-niemals-{os.getpid()}",
                "--bau-vorlage",
                f"{start_skript} bau-{{n}}",
                "--wache-vorlage",
                f"{start_skript} wache-{{s}}",
            ]
        )
        assert code == 0
        time.sleep(0.5)
        namen = fenster_namen()
        assert f"wache {SPEC}" in namen
        assert "bau 9002" in namen
        assert "bau 9003" not in namen
        assert reihenfolge.read_text(encoding="utf-8").splitlines() == [
            f"wache-{SPEC}",
            "bau-9002",
        ]
        # Aufräumen: Ziel-Helfer bestätigt echte Fenster
        assert fenster_ziel(f"wache {SPEC}")
    finally:
        tmux("kill-server", check=False)


# --- #419: Aufpasser.bau_befehl nutzt capo.bau_startzeile (fenster_<N>.log) ---


def test_419_bau_befehl_nutzt_capo_bau_startzeile_mit_fenster_log(tmp_path: Path) -> None:
    """Ohne eigene ``bau_vorlage`` delegiert ``Aufpasser.bau_befehl`` an ``capo.bau_startzeile``
    (mit und ohne ``--resume``) — auch wenn ``scripts/bau.py`` im Repo liegt."""
    repo = _mini_repo(tmp_path / "repo")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "bau.py").write_text("# Weiterleitung\n", encoding="utf-8")
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "zustand", repo=repo))

    frisch = a.bau_befehl(repo, 7, None)
    assert frisch == capo.bau_startzeile(repo, 7, "")
    assert shlex.quote(str(capo.fenster_log(repo, 7))) in frisch

    fort = a.bau_befehl(repo, 7, "sess-abc-123")
    assert fort == capo.bau_startzeile(repo, 7, "", "--resume", "sess-abc-123")
    assert shlex.quote(str(capo.fenster_log(repo, 7))) in fort
    assert "--resume sess-abc-123" in fort


def test_419_bau_befehl_mit_expliziter_vorlage_bleibt_erhalten(tmp_path: Path) -> None:
    repo = _mini_repo(tmp_path / "repo")
    a = aufpasser.Aufpasser(
        aufpasser.Einstellungen(
            zustand=tmp_path / "zustand",
            repo=repo,
            bau_vorlage="echo {n}{resume}",
        )
    )
    assert a.bau_befehl(repo, 7, None) == "echo 7"
    assert a.bau_befehl(repo, 7, "abc") == "echo 7 --resume abc"


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="bash-Startzeile läuft nur unter Linux",
)
def test_419_aufpasser_startzeile_schreibt_echtes_fenster_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Echter ``bash -lc``-Lauf der Aufpasser-Startzeile schreibt stderr + Exit-Code
    nach ``.to-spawn/fenster_<N>.log``."""
    skill = tmp_path / "skill"
    (skill / "skripte").mkdir(parents=True)
    (skill / "skripte" / "bau.py").write_text(
        "import sys\nprint('Start: Probe-Fehler', file=sys.stderr)\nsys.exit(3)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(capo, "_SKILL", skill)

    repo = _mini_repo(tmp_path / "repo")
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "zustand", repo=repo))
    zeile = a.bau_befehl(repo, 7, "sid-99")
    home = tmp_path / "home"
    home.mkdir()
    lauf = subprocess.run(
        ["bash", "-lc", zeile],
        cwd=str(repo),
        env={"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert lauf.returncode == 3
    log_datei = capo.fenster_log(repo, 7)
    assert log_datei.is_file()
    inhalt = log_datei.read_text(encoding="utf-8")
    assert "Start: Probe-Fehler" in inhalt
    assert "bau.py #7 Exit 3" in inhalt


# --- #434: stehengebliebene „Waiting for 1 background agent“-Zeile im Verlauf ---


def test_434_stehengebliebene_background_agent_zeile_im_verlauf_ist_nicht_arbeit() -> None:
    """Echtes Bild aus #434: ``✻ Waiting for 1 background agent to finish`` im Verlauf,
    leeres Eingabefeld, ruhige Statuszeile → ``arbeitet`` ist False (auch für den Aufseher)."""
    bild = _claude_bild(
        "● Subagent gestartet.\n✻ Waiting for 1 background agent to finish\n● Fertig, warte auf Antwort."
    )
    assert aufpasser.arbeitet(bild) is False
    assert aufpasser.arbeitet(bild, status="idle") is False

    # Auch aufseher_stand darf das Fenster nicht als ARBEITET einstufen.
    q = aufseher_stand.Quellen(repo=SKILL, kinder=lambda _s: [], tmux=lambda _args: bild)
    zustand, *_ = aufseher_stand._fenster_lage(7, "=spec-1:1", 1000.0, q, aufseher_stand._Vorher(), 1600.0)
    assert zustand == aufseher_stand.STILL


def test_434_aktiver_background_agent_in_statuszeile_bleibt_arbeit() -> None:
    """Wirklich laufender Hintergrund-Helfer in der Statuszeile unter dem Eingabefeld bleibt „arbeitet“."""
    for status in (
        "  ⏵⏵ bypass permissions on (shift+tab to cycle) · 1 background agent (↓ to manage)",
        "  ⏵⏵ bypass permissions on · 2 background agents",
    ):
        bild = _claude_bild("● Subagent läuft.", status=status)
        assert aufpasser.arbeitet(bild) is True, status
        q = aufseher_stand.Quellen(
            repo=SKILL, kinder=lambda _s: [], tmux=lambda _args, text=bild: text
        )
        zustand, *_ = aufseher_stand._fenster_lage(7, "=spec-1:1", 1000.0, q, aufseher_stand._Vorher(), 1600.0)
        assert zustand == aufseher_stand.ARBEITET, status


def test_434_esc_to_interrupt_und_bild_ohne_eingabefeld_bleiben_arbeit() -> None:
    """Andere Arbeits-Marker im Verlauf und Ausschnitte ohne Eingabefeld bleiben unverändert."""
    bild_esc = _claude_bild("✻ Baking… (esc to interrupt)")
    assert aufpasser.arbeitet(bild_esc) is True
    assert aufpasser.arbeitet("…\nbackground agent\n…") is True


def test_434_briefkasten_wecker_weckt_trotz_stehengebliebener_background_agent_zeile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Briefkasten-Wecker weckt das stille Fenster trotz stehengebliebener Verlaufszeile."""
    import test_briefkasten_wecker_414 as basis
    from test_briefkasten_wecker_414 import (
        PANE,
        SPEC,
        _bild,
        _frage,
        _getippt,
        _wecker_modul,
    )

    gen = basis.welt.__wrapped__(tmp_path, monkeypatch)  # type: ignore[attr-defined]
    welt = next(gen)
    try:
        w = _wecker_modul().Wecker(SPEC, welt["haupt"], PANE)
        frage_id = _frage(welt)
        _bild(
            welt,
            _claude_bild(
                "✻ Waiting for 1 background agent to finish\n● Frage eingeworfen, warte."
            ),
        )
        w.einmal()
        w.einmal()
        getippt = _getippt(welt)
        assert [g["ziel"] for g in getippt] == [PANE], getippt
        assert frage_id in getippt[0]["text"]
    finally:
        try:
            next(gen)
        except StopIteration:
            pass
