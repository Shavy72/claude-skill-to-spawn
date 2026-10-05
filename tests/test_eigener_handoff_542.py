"""#542: Folge-Session startet automatisch ab eigenem, committetem Handoff.

Weg-Tests mit echtem git-Repo (Worktree) und echtem Bau-Log in ``tmp_path``. Attrappen
nur für tmux (Bildschirm/Session-Status) und den eigentlichen Start
(``spawn.neustart``) — alles dazwischen (Handoff-Suche, Bau-Log, Leitstand, Sperre,
Deploy-Wache des Aufpassers) läuft echt.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import (
    aufpasser,
    aufseher_stand,
    bau_log,
    leiter,
    leitstand,
    spawn,
)

SPEC = 9542
TICKET = 9543
JETZT = time.time()
MINUTE = 60.0
HANDOFF = f"docs/handoffs/HANDOFF_2026-10-05_{TICKET}.md"


def _git(ordner: Path, *args: str, zeit: float | None = None) -> None:
    env = dict(os.environ)
    if zeit is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"@{int(zeit)} +0000"
    subprocess.run(
        ["git", "-C", str(ordner), *args], check=True, capture_output=True, env=env
    )


def _repo(ordner: Path) -> Path:
    ordner.mkdir(parents=True)
    _git(ordner, "init", "-q")
    _git(ordner, "config", "user.email", "t@t")
    _git(ordner, "config", "user.name", "t")
    (ordner / "README.md").write_text("x\n", encoding="utf-8")
    _git(ordner, "add", "README.md")
    _git(ordner, "commit", "-q", "-m", "start", zeit=JETZT - 7200)
    return ordner


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Hauptbaum + Ticket-Worktree (``BAU_WT_DIR``), Leitstand in tmp, Start belegt."""
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path))
    haupt = _repo(tmp_path / "haupt")
    wt = _repo(tmp_path / f"wt-{TICKET}")
    gestartet: list[dict[str, Any]] = []

    def neustart(repo: Path, spec: int, ticket: int, konfig: dict, **kw: Any) -> int:
        gestartet.append({"repo": repo, "spec": spec, "ticket": ticket, **kw})
        return 0

    monkeypatch.setattr(spawn, "neustart", neustart)
    _session_start(wt, JETZT - 60 * MINUTE)
    return {"haupt": haupt, "wt": wt, "gestartet": gestartet}


def _session_start(wt: Path, zeit: float) -> None:
    ts = datetime.fromtimestamp(zeit, tz=timezone.utc).isoformat()
    datei = bau_log.lauf_pfad(wt, TICKET)
    datei.parent.mkdir(parents=True, exist_ok=True)
    with datei.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps({"ts": ts, "typ": "session_start", "ticket": str(TICKET)}) + "\n"
        )


def _handoff(wt: Path, zeit: float, *, committen: bool = True) -> None:
    datei = wt / HANDOFF
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text("# Handoff\nWeiter mit Schritt 3.\n", encoding="utf-8")
    if committen:
        _git(wt, "add", HANDOFF)
        _git(wt, "commit", "-q", "-m", "docs: Handoff", zeit=zeit)


# --- Aufpasser ---------------------------------------------------------------------


def _aufpasser(
    tmp: Path, deploy: list[str] | None = None
) -> tuple[aufpasser.Aufpasser, list[str]]:
    a = aufpasser.Aufpasser(
        aufpasser.Einstellungen(
            zustand=tmp / "stand.json", tmux_socket="nie-benutzt", jetzt=lambda: JETZT
        )
    )
    meldungen: list[str] = []
    a.pane_text = lambda ziel: (
        "✻ Baked for 40s\n\nHandoff committet, ich höre hier auf.\n"
    )  # type: ignore[method-assign]
    a.session = lambda f: aufpasser.SessionInfo("sid", None, "idle", 4242)  # type: ignore[method-assign]
    a.deploy_laeuft = lambda muster=None: list(deploy or [])  # type: ignore[method-assign]
    a.melden = lambda spec, repo, fenster, ereignis, text: meldungen.append(
        f"{ereignis}: {text}"
    )  # type: ignore[method-assign]
    a.muster = aufpasser.DEPLOY_MUSTER
    return a, meldungen


def _fenster() -> aufpasser.Fenster:
    return aufpasser.Fenster(
        f"spec-{SPEC}", str(SPEC), "3", f"bau {TICKET}", "/x", 4242
    )


def test_a_aufpasser_frischer_handoff_idle_startet_neu(
    welt: dict, tmp_path: Path
) -> None:
    """Rot vor #542: Session sitzt mit committetem Handoff still — niemand startet neu."""
    _handoff(welt["wt"], JETZT - 5 * MINUTE)
    a, meldungen = _aufpasser(tmp_path)
    a.fenster_pruefen(_fenster(), welt["haupt"], {TICKET})
    assert welt["gestartet"] == [
        {
            "repo": welt["haupt"],
            "spec": SPEC,
            "ticket": TICKET,
            "handoff": HANDOFF,
            "beenden": True,
        }
    ]
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    assert (leitstand.leiter_respawn(TICKET) or 0) >= JETZT
    assert any("handoff" in m.lower() for m in meldungen)
    # Derselbe Handoff zählt nie zweimal (Neustart-Zeit ist gemerkt).
    a.fenster_pruefen(_fenster(), welt["haupt"], {TICKET})
    assert len(welt["gestartet"]) == 1


def test_b_aufpasser_nur_still_ohne_handoff_altes_verhalten(
    welt: dict, tmp_path: Path
) -> None:
    a, meldungen = _aufpasser(tmp_path)
    a.fenster_pruefen(_fenster(), welt["haupt"], {TICKET})
    assert welt["gestartet"] == [] and meldungen == []


def test_c_aufpasser_handoff_aelter_als_session_start_nichts(
    welt: dict, tmp_path: Path
) -> None:
    _handoff(welt["wt"], JETZT - 90 * MINUTE)  # gehört zur Vorsession
    a, _ = _aufpasser(tmp_path)
    a.fenster_pruefen(_fenster(), welt["haupt"], {TICKET})
    assert welt["gestartet"] == []


def test_d_aufpasser_deploy_laeuft_nichts(welt: dict, tmp_path: Path) -> None:
    _handoff(welt["wt"], JETZT - 5 * MINUTE)
    a, _ = _aufpasser(tmp_path, deploy=["4711 bash scripts/safe_deploy_vps.sh"])
    a.fenster_pruefen(_fenster(), welt["haupt"], {TICKET})
    assert welt["gestartet"] == []


def test_e_aufpasser_handoff_nicht_committet_nichts(welt: dict, tmp_path: Path) -> None:
    _handoff(welt["wt"], JETZT - 5 * MINUTE, committen=False)
    a, _ = _aufpasser(tmp_path)
    a.fenster_pruefen(_fenster(), welt["haupt"], {TICKET})
    assert welt["gestartet"] == []
    # Committet, danach weiter bearbeitet (uncommittet) → auch nichts.
    _handoff(welt["wt"], JETZT - 5 * MINUTE)
    (welt["wt"] / HANDOFF).write_text("# Handoff\nnoch in Arbeit\n", encoding="utf-8")
    a.fenster_pruefen(_fenster(), welt["haupt"], {TICKET})
    assert welt["gestartet"] == []


def test_aufpasser_handoff_erst_eine_minute_alt_wartet(
    welt: dict, tmp_path: Path
) -> None:
    _handoff(welt["wt"], JETZT - 1 * MINUTE)
    a, _ = _aufpasser(tmp_path)
    a.fenster_pruefen(_fenster(), welt["haupt"], {TICKET})
    assert welt["gestartet"] == []


def test_aufpasser_leiter_loest_selbst_ab_nichts(welt: dict, tmp_path: Path) -> None:
    """Stufe 2 = die Leiter hat den Handoff angefordert und löst selbst per respawn ab."""
    _handoff(welt["wt"], JETZT - 5 * MINUTE)
    leitstand.setze_leiter_stufe(TICKET, 2, JETZT - 6 * MINUTE)
    a, _ = _aufpasser(tmp_path)
    a.fenster_pruefen(_fenster(), welt["haupt"], {TICKET})
    assert welt["gestartet"] == []


# --- Leiter ------------------------------------------------------------------------


class EchteHandoffUmwelt:
    """Leiter-Umwelt: Fenster still (Attrappe tmux), Handoff-Suche + Neustart echt."""

    def __init__(self, haupt: Path, wt: Path) -> None:
        self.haupt, self.wt = haupt, wt

    def jetzt(self) -> float:
        return JETZT

    def lage(self, spec: int, ticket: int, seit: float | None) -> leiter.Lage:
        from to_spawn import eigener_handoff

        start = bau_log.letzter_session_start(self.wt, ticket)
        return leiter.Lage(
            offen=True,
            fenster=aufseher_stand.STILL,
            still_min=0,
            kontext_k=None,
            prompt_datei=False,
            ziel="=spec-9542:3",
            letzter_start=start,
            eigener_handoff=eigener_handoff.finde(
                self.wt, ticket, eigener_handoff.grenze(start, ticket)
            ),
        )

    def handoff_k(self) -> float:
        return 250.0

    def worktree(self, ticket: int) -> Path:
        return self.wt

    def werkzeug(self) -> Any:
        class Tmux:
            def tippen(self, ziel: str, text: str) -> None:
                raise AssertionError(f"nie tippen: {text}")

        return Tmux()

    def folge_ab_handoff(self, spec: int, ticket: int, treffer: Any) -> int:
        from to_spawn import eigener_handoff

        return eigener_handoff.folge_starten(
            self.haupt, spec, ticket, treffer, sperren=False
        )


def test_a_leiter_frischer_handoff_startet_neu(welt: dict) -> None:
    _handoff(welt["wt"], JETZT - 5 * MINUTE)
    erg = leiter.eingreifen(
        welt["haupt"],
        SPEC,
        TICKET,
        umwelt=EchteHandoffUmwelt(welt["haupt"], welt["wt"]),
    )
    assert erg.exit == 0, erg.zeile
    assert "Handoff" in erg.zeile
    assert [g["handoff"] for g in welt["gestartet"]] == [HANDOFF]
    assert leitstand.leiter_eintrag(TICKET) == (0, None)
    # Zweiter Lauf: Handoff ist verbraucht.
    erg = leiter.eingreifen(
        welt["haupt"],
        SPEC,
        TICKET,
        umwelt=EchteHandoffUmwelt(welt["haupt"], welt["wt"]),
    )
    assert len(welt["gestartet"]) == 1, erg.zeile


def test_b_leiter_ohne_handoff_altes_verhalten(welt: dict) -> None:
    erg = leiter.eingreifen(
        welt["haupt"],
        SPEC,
        TICKET,
        umwelt=EchteHandoffUmwelt(welt["haupt"], welt["wt"]),
    )
    assert erg.exit == 0 and welt["gestartet"] == []
    assert "still 0 min" in erg.zeile


def test_c_leiter_handoff_aelter_als_start_nichts(welt: dict) -> None:
    _handoff(welt["wt"], JETZT - 90 * MINUTE)
    leiter.eingreifen(
        welt["haupt"],
        SPEC,
        TICKET,
        umwelt=EchteHandoffUmwelt(welt["haupt"], welt["wt"]),
    )
    assert welt["gestartet"] == []


def test_entscheide_folge_nur_stufe_0_und_1() -> None:
    from to_spawn import eigener_handoff

    treffer = eigener_handoff.Treffer(HANDOFF, JETZT - 5 * MINUTE)
    lage = leiter.Lage(
        True, aufseher_stand.STILL, 0, None, False, eigener_handoff=treffer
    )
    for stufe in (0, 1):
        assert (
            leiter.entscheide(lage, leiter.Gemerkt(stufe, None), JETZT, 250.0).aktion
            == "folge_handoff"
        )
    assert (
        leiter.entscheide(lage, leiter.Gemerkt(2, JETZT), JETZT, 250.0).aktion
        != "folge_handoff"
    )
    arbeitet = leiter.Lage(
        True, aufseher_stand.ARBEITET, None, None, False, eigener_handoff=treffer
    )
    assert (
        leiter.entscheide(arbeitet, leiter.Gemerkt(0, None), JETZT, 250.0).aktion
        == "nichts"
    )
    jung = leiter.Lage(
        True,
        aufseher_stand.STILL,
        0,
        None,
        False,
        eigener_handoff=eigener_handoff.Treffer(HANDOFF, JETZT - 30),
    )
    assert (
        leiter.entscheide(jung, leiter.Gemerkt(0, None), JETZT, 250.0).aktion
        == "nichts"
    )


# --- wache: Neustart im richtigen Ordner (Punkt 4) ---------------------------------


def _wache() -> Any:
    modul_spec = importlib.util.spec_from_file_location(
        "wache_542", SKILL / "skripte" / "wache.py"
    )
    assert modul_spec and modul_spec.loader
    modul = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(modul)
    return modul


@pytest.mark.parametrize("name", ["TO_SPAWN_REPO", "REPO"])
def test_f_wache_nimmt_repo_aus_umgebung_statt_cwd(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "repo")
    anderswo = tmp_path / "anderswo"
    anderswo.mkdir()
    monkeypatch.chdir(anderswo)
    monkeypatch.setenv(name, str(repo))
    wache = _wache()
    assert wache.start_ordner() == repo.resolve()
    assert wache.REPO_ORDNER == repo.resolve()


def test_f_wache_ohne_umgebung_bleibt_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("REPO", raising=False)
    monkeypatch.chdir(tmp_path)
    assert _wache().start_ordner() == tmp_path.resolve()


def test_f_wache_repo_ohne_ordner_wird_ignoriert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``REPO`` kann auch ein Slug sein (``owner/name``) — dann zählt der aktuelle Ordner."""
    monkeypatch.setenv("REPO", "owner/gibt-es-nicht")
    monkeypatch.chdir(tmp_path)
    assert _wache().start_ordner() == tmp_path.resolve()


@pytest.mark.parametrize("name", ["TO_SPAWN_REPO", "REPO"])
def test_f_wache_cwd_im_git_repo_schlaegt_umgebung(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Läuft der Aufseher in einem Repo/Worktree, zählt dieser Ordner (#450 F1) — ``REPO``
    der Shell (z. B. Hauptbaum auf dem Bau-Server) darf ihn nicht umlenken."""
    hier = _repo(tmp_path / "hier")
    fremd = _repo(tmp_path / "fremd")
    monkeypatch.chdir(hier)
    monkeypatch.setenv(name, str(fremd))
    assert _wache().start_ordner() == hier.resolve()
