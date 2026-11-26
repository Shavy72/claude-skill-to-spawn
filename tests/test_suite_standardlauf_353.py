"""#353 (Linux-Teil): Standard-Lauf der Suite grün (Login-Marker, Wiedereinstieg-Check) und schnell.

Prüft:
- Tests mit echtem ``claude``-Login sind über ``@pytest.mark.braucht_claude_login`` /
  ``braucht("claude-login")`` sauber aus dem Standard-Lauf ausgenommen (nie gelöscht,
  per ``TO_SPAWN_MIT_ECHTEM_CLAUDE=1`` gezielt zuschaltbar, auf GitHub-Runnern immer aus).
- Der Wiedereinstieg-Check kippt nicht bei jedem fremden Merge auf ``origin/main`` rot;
  er prüft die Konsistenz von ``docs/WIEDEREINSTIEG.md`` deterministisch, während
  ``to_spawn.py sichern --pruefen`` einen veralteten Live-Stand als Hinweis meldet.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from conftest import braucht_fehlt
from to_spawn import konsole
from to_spawn import uebergabe_sichern as us

SKILL = Path(__file__).resolve().parent.parent


def test_braucht_claude_login_im_standardlauf_ausgeschlossen_und_mit_freigabe_aktiv() -> None:
    """#353: Einzige Regel in ``conftest.braucht_fehlt`` für Tests mit echtem ``claude``-Login."""
    standard = braucht_fehlt("claude-login", "linux", finde=lambda _: "/usr/bin/claude", umgebung={})
    assert standard and "TO_SPAWN_MIT_ECHTEM_CLAUDE" in standard

    mit_freigabe = braucht_fehlt(
        "claude-login",
        "linux",
        finde=lambda _: "/usr/bin/claude",
        umgebung={"TO_SPAWN_MIT_ECHTEM_CLAUDE": "1"},
    )
    assert mit_freigabe is None

    abgewaehlt = braucht_fehlt(
        "claude-login",
        "linux",
        finde=lambda _: "/usr/bin/claude",
        umgebung={"TO_SPAWN_MIT_ECHTEM_CLAUDE": "1", "TO_SPAWN_OHNE_ECHTEN_WEG": "1"},
    )
    assert abgewaehlt and "TO_SPAWN_OHNE_ECHTEN_WEG" in abgewaehlt

    auf_gh = braucht_fehlt(
        "claude-login",
        "linux",
        finde=lambda _: "/usr/bin/claude",
        umgebung={"TO_SPAWN_MIT_ECHTEM_CLAUDE": "1", "GITHUB_ACTIONS": "true"},
    )
    assert auf_gh and "GitHub" in auf_gh

    ohne_bin = braucht_fehlt(
        "claude-login",
        "linux",
        finde=lambda _: None,
        umgebung={"TO_SPAWN_MIT_ECHTEM_CLAUDE": "1"},
    )
    assert ohne_bin and "claude" in ohne_bin


def test_echte_claude_login_tests_im_standardlauf_uebersprungen_nicht_geloescht() -> None:
    """#353: Die echten ``claude -p``-Tests bleiben erhalten und werden ohne Freigabe übersprungen."""
    env = {k: v for k, v in os.environ.items() if k not in ("TO_SPAWN_MIT_ECHTEM_CLAUDE", "TO_SPAWN_OHNE_ECHTEN_WEG")}
    lauf = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-rs",
            "tests/test_frage_sperre_321_weg.py",
            "tests/test_context_mode_237.py::test_weg_echte_session_hat_start_mjs_und_ruft_ctx_werkzeug",
        ],
        cwd=SKILL,
        env=env,
        capture_output=True,
        text=True,
        **konsole.UNTERPROZESS_TEXT,
        timeout=60,
        check=False,
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "3 skipped" in ausgabe, ausgabe
    assert "braucht_claude_login" in ausgabe, ausgabe


def test_echte_claude_login_tests_ohne_claude_im_path_zeigen_marker_grund() -> None:
    """#353: Auch ohne ``claude`` im PATH (z. B. GitHub-Runner) greift ``braucht_claude_login`` vor ``skipif``."""
    pfade = [
        p
        for p in os.environ.get("PATH", "").split(os.pathsep)
        if p and not any((Path(p) / name).exists() for name in ("claude", "claude.exe", "claude.cmd"))
    ]
    env = {k: v for k, v in os.environ.items() if k not in ("TO_SPAWN_MIT_ECHTEM_CLAUDE", "TO_SPAWN_OHNE_ECHTEN_WEG")}
    env["PATH"] = os.pathsep.join(pfade)
    env["GITHUB_ACTIONS"] = "true"
    lauf = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-rs",
            "tests/test_frage_sperre_321_weg.py",
            "tests/test_context_mode_237.py::test_weg_echte_session_hat_start_mjs_und_ruft_ctx_werkzeug",
        ],
        cwd=SKILL,
        env=env,
        capture_output=True,
        text=True,
        **konsole.UNTERPROZESS_TEXT,
        timeout=60,
        check=False,
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "3 skipped" in ausgabe, ausgabe
    assert "braucht_claude_login:" in ausgabe, ausgabe
    assert "claude nicht installiert" not in ausgabe, ausgabe
    assert "claude nicht auf dem PATH" not in ausgabe, ausgabe


def test_wiedereinstieg_stand_referenz_und_live_hinweis(tmp_path: Path) -> None:
    """#353: ``stand_referenz_commit`` liest den zitierten main-Stand; veralteter Live-Stand ergibt Hinweis."""
    text = (
        "## 3. Stand\n\n"
        "- **2026-11-25 19:30 (Europe/Berlin, main 7005f9c):** Paket gemergt.\n"
        "- **2026-11-24 10:00 (Europe/Berlin, main 0741c91):** Vorher.\n\n"
        "## 4. Weiter\n"
    )
    assert us.stand_referenz_commit(text) == "7005f9c"
    assert us.stand_referenz_commit("## 3. Stand\n\n- **2026-11-25 19:30:** ohne sha\n") is None

    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "WIEDEREINSTIEG.md").write_text(text, encoding="utf-8")

    frisch = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
    assert us.wiedereinstieg_stand_hinweis(repo, referenz=frisch) is None

    veraltet = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
    hinweis = us.wiedereinstieg_stand_hinweis(repo, referenz=veraltet)
    assert hinweis is not None and "WIEDEREINSTIEG.md" in hinweis and "Stand" in hinweis


def test_wiedereinstieg_check_bleibt_bei_spaeterem_origin_main_merge_gruen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#353: Ein fremder Merge auf ``origin/main`` 5 Tage später darf den Standard-Test nicht rot machen."""
    import test_uebergabe_sichern_145 as mod

    zukunft = datetime.now(tz=UTC) + timedelta(days=5)
    monkeypatch.setattr(us, "juengster_main_stand", lambda _repo: zukunft)
    monkeypatch.delenv("TO_SPAWN_PRUEFE_STAND_FRISCHE", raising=False)
    mod.test_wiedereinstieg_stand_nicht_aelter_als_juengster_merge()


def test_standard_worker_skaliert_von_vier_bis_acht_und_respektiert_env() -> None:
    """#353: Standard-Worker-Zahl sinkt nie unter 4, nutzt bis 8 Kerne und erlaubt Env-Vorgabe."""
    from to_spawn import install_pruefung

    assert install_pruefung.standard_worker(kerne=2, umgebung={}) == "4"
    assert install_pruefung.standard_worker(kerne=4, umgebung={}) == "4"
    assert install_pruefung.standard_worker(kerne=6, umgebung={}) == "6"
    assert install_pruefung.standard_worker(kerne=8, umgebung={}) == "8"
    assert install_pruefung.standard_worker(kerne=16, umgebung={}) == "8"
    assert install_pruefung.standard_worker(kerne=8, umgebung={"TO_SPAWN_SUITE_WORKER": "6"}) == "6"
    assert install_pruefung.standard_worker(kerne=8, umgebung={"TO_SPAWN_SUITE_WORKER": "kaputt"}) == "8"


def test_wurzel_sammlung_teilt_einzelnen_collect_only_lauf_im_selben_basetemp(tmp_path: Path) -> None:
    """#353: Gemeinsame Sammel-Hilfe ruft ``pytest --collect-only`` je Testlauf höchstens einmal auf."""
    import conftest

    aufrufe: list[int] = []

    def fake_lauf(_tmp: Path) -> tuple[int, str, str]:
        aufrufe.append(1)
        return 0, "tests/test_a.py::test_ok\n1 test collected in 0.01s\n", ""

    cache_datei = tmp_path / "_wurzel_sammlung.json"
    erste = conftest.wurzel_sammlung(tmp_path, cache_pfad=cache_datei, ausfuehren=fake_lauf)
    zweite = conftest.wurzel_sammlung(tmp_path, cache_pfad=cache_datei, ausfuehren=fake_lauf)
    assert len(aufrufe) == 1
    assert erste.returncode == 0 and zweite.returncode == 0
    assert erste.stdout == zweite.stdout and "1 test collected" in zweite.stdout


def test_komplett_im_hintergrund_erkennt_gehaltene_sperre_statt_exit_1_wackler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#353 Wackler unter -n 8: endet ein paralleler Hintergrund-Start an einer bereits gehaltenen
    ``komplett-<S>.lock``, liefert ``_im_hintergrund`` ``laeuft=True, ok=True`` statt Exit-1-Fehler."""
    from to_spawn import komplett_lauf, sperre, starter
    from to_spawn.pruefplan import Moment

    klon = tmp_path / "klon"
    klon.mkdir()
    sperr_datei = komplett_lauf._datei(klon, 777).with_suffix(".lock")
    sperr_datei.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(komplett_lauf, "_kopf", lambda _k: "abc1234")

    class _SofortExit1:
        pid = 999999

        def wait(self, timeout: float | None = None) -> int:
            return 1

    monkeypatch.setattr(starter, "starte", lambda *_a, **_kw: _SofortExit1())

    with sperre.halte(sperr_datei) as gehalten:
        assert gehalten
        erg = komplett_lauf._im_hintergrund(klon, 777, Moment.ALLE_AUF_STAGING, "abc1234")
    assert erg.laeuft is True and erg.ok is True, erg
    assert "läuft schon" in erg.grund, erg.grund
