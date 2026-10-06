"""#598 Nachfix: Kette nur echt melden, Gate-Läufe sperren nur das eigene Fenster.

(4) Nach ``/exit`` ohne Kette bleibt nur eine Shell: das Fenster wird gleich
    geschlossen, gemeldet wird einmal — ohne „Kette läuft weiter“.
(5) ``deploy_art`` stuft ein: Gate-/Prüfläufe (``safe_deploy_vps.sh --nur-gate``,
    ``deploy_schlange.py``, ``pytest``) sperren nur das eigene Fenster, echter
    Deploy weiter den ganzen Server.
(7) Vor ``/exit`` wird das Eingabefeld geleert (``C-u``).

Nur Linux mit tmux (wie ``test_aufpasser_236``).
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus dem Erst-Test und wird als Parameter genannt.
from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_aufpasser_236 import (  # noqa: F401 — ``welt`` ist ein Fixture
    SITZUNG,
    SOCKET,
    SPEC,
    Welt,
    fenster_namen,
    fenster_ziel,
    tmux,
    welt,
)
from test_aufpasser_exit_592 import _fenster_nach_pause, _lauf
from test_aufpasser_platz_592 import _beenden, _still_seit_20_min

from to_spawn import aufpasser

#: Eigener Ordnername: das Muster von ``test_aufpasser_platz_592`` (``aufp592probe``)
#: sieht die Deploy-Attrappen hier nicht.
PROBE = "aufp598probe"


@pytest.fixture()
def zu_9003(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> Iterator[Welt]:
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9003")
    monkeypatch.setenv("GH_STUB_ZU", "9003")
    welt.worktree(9003, schmutzig=False)
    yield welt


def _geschlossen(welt: Welt) -> list[str]:
    return [k for k in welt.kommentare() if "„bau 9003“ geschlossen (Ticket zu)." in k]


# --- (5) deploy_art: eine Stelle entscheidet -------------------------------


@pytest.mark.parametrize(
    ("argv", "art"),
    [
        (["bash", "scripts/safe_deploy_vps.sh", "--nur-gate"], "pruef"),
        (["bash", "-lc", "bash scripts/safe_deploy_vps.sh --nur-gate"], "pruef"),
        (["python3", "scripts/deploy_schlange.py"], "pruef"),
        (["/usr/bin/python3", "-m", "pytest", "tests", "-q"], "pruef"),
        (["bash", "scripts/safe_deploy_vps.sh", "--skip-ci"], "live"),
        (["bash", "scripts/safe_deploy_vps.sh", "--rebuild-runner"], "live"),
        (["bash", "scripts/safe_deploy_vps.sh", "--to", "abc1234"], "live"),
        (["bash", "scripts/staging/staging_deploy.sh"], "live"),
        (
            [
                "ssh",
                "clawy-vps",
                "bash /opt/duoplus-management/scripts/server_tuer.sh abc",
            ],
            "live",
        ),
        (["ssh", "clawy-vps", "git rev-parse HEAD"], None),
        (
            ["claude", "--model", "x", "y", "Ticket: bash scripts/safe_deploy_vps.sh"],
            None,
        ),
        ([], None),
    ],
)
def test_deploy_art(argv: list[str], art: str | None) -> None:
    assert aufpasser.deploy_art(argv) == art


def _lauf_echtes_muster(welt: Welt) -> int:
    """Wie ``test_aufpasser_platz_592``, aber am Test-Ordner verankert: parallele
    Tests mit eigenen Deploy-Attrappen sehen einander nicht."""
    return aufpasser.main(
        [
            "--tmux-socket",
            SOCKET,
            "--zustand",
            str(welt.zustand),
            "--deploy-muster",
            rf"{welt.tmp.name}/{PROBE}[^ ]*/({aufpasser.DEPLOY_MUSTER})",
            "--bau-vorlage",
            welt.vorlage,
            "--hang-min",
            "90",
        ]
    )


def _deploy_attrappe(welt: Welt, name: str) -> Path:
    skript = welt.tmp / PROBE / "scripts" / name
    skript.parent.mkdir(parents=True, exist_ok=True)
    skript.write_text("#!/bin/bash\nsleep 3600\n", encoding="utf-8")
    skript.chmod(0o755)
    return skript


@pytest.mark.parametrize(
    "aufruf",
    [("safe_deploy_vps.sh", "--nur-gate"), ("deploy_schlange.py",)],
    ids=["nur-gate", "deploy-schlange"],
)
def test_fremder_pruef_lauf_sperrt_nicht(
    zu_9003: Welt, aufruf: tuple[str, ...]
) -> None:
    welt = zu_9003
    skript = _deploy_attrappe(welt, aufruf[0])
    fremd = subprocess.Popen(
        ["bash", str(skript), *aufruf[1:]],
        cwd=str(welt.tmp / PROBE),
        start_new_session=True,
    )
    try:
        welt.fenster_still(9003, "sleep 3600")
        _still_seit_20_min(welt)
        assert _lauf_echtes_muster(welt) == 0
        assert "bau 9003" not in _fenster_nach_pause(), welt.log()
        assert len(_geschlossen(welt)) == 1, welt.log()
    finally:
        _beenden(fremd)


def test_pruef_lauf_im_fensterbaum_haelt_fenster(zu_9003: Welt) -> None:
    welt = zu_9003
    skript = _deploy_attrappe(welt, "safe_deploy_vps.sh")
    welt.fenster_still(9003, f"bash {skript} --nur-gate")
    _still_seit_20_min(welt)
    assert _lauf_echtes_muster(welt) == 0
    assert "bau 9003" in _fenster_nach_pause()
    assert "Gate läuft in diesem Fenster" in welt.log(), welt.log()
    assert welt.kommentare() == []


def test_echter_deploy_sperrt_serverweit(zu_9003: Welt) -> None:
    welt = zu_9003
    skript = _deploy_attrappe(welt, "safe_deploy_vps.sh")
    fremd = subprocess.Popen(
        ["bash", str(skript), "--skip-ci"],
        cwd=str(welt.tmp / PROBE),
        start_new_session=True,
    )
    try:
        welt.fenster_still(9003, "sleep 3600")
        _still_seit_20_min(welt)
        assert _lauf_echtes_muster(welt) == 0
        assert "bau 9003" in _fenster_nach_pause()
        assert "nichts angefasst" in welt.log(), welt.log()
        assert welt.kommentare() == []
    finally:
        _beenden(fremd)


# --- (4) Kette nur echt melden ----------------------------------------------


def test_ohne_kette_nur_shell_fenster_zu_einmal_gemeldet(zu_9003: Welt) -> None:
    welt = zu_9003
    sid = uuid.uuid4()
    welt.fenster_still(
        9003,
        f'bash -c "FAKE_CLAUDE_EXIT_BEI_EINGABE=1 {welt.bin}/claude --session-id {sid} x; exec bash --norc -i"',
    )
    assert _lauf(welt, exit_warten_s=30) == 0
    assert "bau 9003" not in _fenster_nach_pause(), welt.log()
    treffer = _geschlossen(welt)
    assert len(treffer) == 1 and "Kette läuft weiter" not in treffer[0], (
        welt.kommentare()
    )
    # Zweiter Lauf (15 min später): nichts mehr zu schließen, keine zweite Meldung.
    assert (
        aufpasser.lauf_mit_einstellungen(
            aufpasser.Einstellungen(
                zustand=welt.zustand,
                tmux_socket=SOCKET,
                hang_min=90,
                bau_vorlage=welt.vorlage,
                deploy_muster="aufpasser-probe-niemals",
            )
        )
        == 0
    )
    assert len(_geschlossen(welt)) == 1, welt.kommentare()


def test_mit_kette_meldung_kette_laeuft_weiter(zu_9003: Welt) -> None:
    welt = zu_9003
    sid = uuid.uuid4()
    welt.fenster_still(
        9003,
        f'bash -c "FAKE_CLAUDE_EXIT_BEI_EINGABE=1 {welt.bin}/claude --session-id {sid} x; sleep 3600"',
    )
    assert _lauf(welt, exit_warten_s=30) == 0
    assert "bau 9003" in _fenster_nach_pause(), welt.log()
    treffer = _geschlossen(welt)
    assert len(treffer) == 1 and "Kette läuft weiter" in treffer[0], welt.kommentare()


# --- (7) Eingabefeld vor /exit leeren ---------------------------------------


def test_eingabefeld_wird_vor_exit_geleert(zu_9003: Welt) -> None:
    """Halber Text im Eingabefeld: ohne ``C-u`` käme „halber Text/exit“ an."""
    welt = zu_9003
    sid = uuid.uuid4()
    welt.fenster_still(
        9003,
        f'bash -c "FAKE_CLAUDE_EXIT_BEI_EINGABE=1 {welt.bin}/claude --session-id {sid} x; sleep 3600"',
    )
    tmux("send-keys", "-t", f"={SITZUNG}:bau 9003", "-l", "halber Text")
    assert _lauf(welt, exit_warten_s=5) == 0
    treffer = _geschlossen(welt)
    assert len(treffer) == 1 and "Per /exit beendet" in treffer[0], (
        welt.kommentare(),
        welt.log(),
    )


# --- Review A: Schließen trifft das eigene Fenster, nie den neuen Index ---------


def test_kill_window_nach_exit_trifft_nicht_fremdes_fenster_am_alten_index(
    zu_9003: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nach ``/exit`` ist das Fenster oft schon von selbst zu; tmux vergibt seinen
    Index an das nächste ``new-window``. ``kill-window`` darf dieses fremde Fenster
    nicht treffen — Ziel ist die ``window_id``, nicht der Index."""
    welt = zu_9003
    sid = uuid.uuid4()
    welt.fenster_still(
        9003,
        f'bash -c "FAKE_CLAUDE_EXIT_BEI_EINGABE=1 {welt.bin}/claude --session-id {sid} x"',
    )
    alt = fenster_ziel("bau 9003")

    def fremdes_fenster_am_alten_index(self: aufpasser.Aufpasser, *_: object) -> bool:
        for _ in range(50):
            if "bau 9003" not in fenster_namen():
                break
            time.sleep(0.1)
        tmux("new-window", "-d", "-t", alt, "-n", "fremd", "sleep 3600")
        return False

    monkeypatch.setattr(
        aufpasser.Aufpasser, "_kette_lebt", fremdes_fenster_am_alten_index
    )
    assert _lauf(welt, exit_warten_s=30) == 0
    assert "fremd" in _fenster_nach_pause(), welt.log()
    assert len(_geschlossen(welt)) == 1, welt.kommentare()
