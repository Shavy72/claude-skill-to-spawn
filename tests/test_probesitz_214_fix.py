"""Probesitz (#214) — Fixrunde nach Prüfpanel: Exit-Code, Aufräumen, Rechte, Lauf-Kennung.

Je Befund ein Test; die Tests der ersten Runde (``test_probesitz_214.py``) bleiben
unverändert und müssen weiter grün sein.
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
SKRIPTE = SKILL / "skripte"
HILFEN = Path(__file__).resolve().parent / "hilfen"
sys.path.insert(0, str(SKILL))

import context_mode_attrappe  # noqa: E402  (tests/hilfen, Pfad setzt conftest)
from to_spawn import config, probesitz  # noqa: E402


# --- Bausteine -----------------------------------------------------------------


class Laeufer:
    """Gestellter Unterprozess-Läufer: ``antworten[Präfix] = (exit, stdout, stderr)``."""

    def __init__(self, antworten: dict[str, tuple[int, str, str]] | None = None) -> None:
        self.antworten = antworten or {}
        self.aufrufe: list[list[str]] = []

    def __call__(self, argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        self.aufrufe.append(list(argv))
        text = " ".join(str(teil) for teil in argv)
        for praefix, (code, stdout, stderr) in self.antworten.items():
            if praefix in text:
                return subprocess.CompletedProcess(argv, code, stdout, stderr)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def texte(self) -> list[str]:
        return [" ".join(a) for a in self.aufrufe]


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(arbeit), check=True, capture_output=True)
    (arbeit / ".to-spawn").mkdir()
    (arbeit / ".to-spawn" / "config.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_WAECHTER_ORDNER", str(tmp_path / "zustand"))
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path / "wt"))
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.chdir(arbeit)
    return arbeit


def _which_alles(programm: str) -> str | None:
    return f"/usr/bin/{programm}"


def _konfig(repo: Path) -> dict[str, Any]:
    return copy.deepcopy(config.lade(repo))


def _lauf(exit_code: int, protokoll: Path, text: str = "") -> Any:
    protokoll.write_text(text, encoding="utf-8")

    def starter(*_: Any, **__: Any) -> probesitz.SessionLauf:
        return probesitz.SessionLauf(exit_code, "Session PID 1 · ctx ✓", False, str(protokoll))

    return starter


def _pid_lebt(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        # Zombie? waitpid ohne Blockieren — ein beendetes, nicht abgeholtes Kind zählt als tot.
        fertig, _ = os.waitpid(pid, os.WNOHANG)
        return fertig == 0
    except ChildProcessError:
        return True


# --- 1. bau.py-Exit ≠ 0 wird eigener Grund, Protokoll immer im Beleg -------------------


def test_f1_bau_exit_ungleich_null_eigener_grund(repo: Path, tmp_path: Path) -> None:
    laeufer = Laeufer({"issue create": (0, "https://github.com/x/y/issues/4711\n", "")})
    protokoll = tmp_path / "lauf.log"
    ergebnisse = probesitz.wegwerf_lauf(
        repo,
        _konfig(repo),
        laeufer=laeufer,
        which=_which_alles,
        session_starter=_lauf(2, protokoll, "ERROR Sandbox ist an, aber srt fehlt\n"),
        worktree_anlegen=lambda *_: None,
    )
    p2 = ergebnisse[2]
    assert not p2.ok
    assert "bau.py Exit 2" in p2.grund, p2
    assert str(protokoll) in p2.beleg


def test_f1_rechte_verweigert_wird_grund(repo: Path, tmp_path: Path) -> None:
    laeufer = Laeufer({"issue create": (0, "https://github.com/x/y/issues/4711\n", "")})
    protokoll = tmp_path / "lauf.log"
    ergebnisse = probesitz.wegwerf_lauf(
        repo,
        _konfig(repo),
        laeufer=laeufer,
        which=_which_alles,
        session_starter=_lauf(0, protokoll, "Claude requested permissions to use Bash, but you haven't granted it yet.\n"),
        worktree_anlegen=lambda *_: None,
    )
    p2 = ergebnisse[2]
    assert not p2.ok
    assert "permission" in p2.grund.lower(), p2
    assert str(protokoll) in p2.beleg


def test_f1_protokoll_auch_bei_exit_null_im_beleg(repo: Path, tmp_path: Path) -> None:
    laeufer = Laeufer(
        {
            "issue create": (0, "https://github.com/x/y/issues/4711\n", ""),
            "ls-remote": (0, "abc\trefs/heads/probesitz-4711\n", ""),
            "log ": (0, "abc chore(probesitz): Wegwerf-Commit Runde 1 (#4711) [skip ci]\n", ""),
        }
    )
    protokoll = tmp_path / "lauf.log"
    ergebnisse = probesitz.wegwerf_lauf(
        repo,
        _konfig(repo),
        laeufer=laeufer,
        which=_which_alles,
        session_starter=_lauf(0, protokoll, "FERTIG\n"),
        worktree_anlegen=lambda *_: None,
    )
    assert ergebnisse[2].ok, ergebnisse[2]
    assert str(protokoll) in ergebnisse[2].beleg


# --- 2./3./9. session_fahren beendet das Kind immer ---------------------------------------


KIND = (
    "import os, signal, sys, time\n"
    "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "time.sleep(120)\n"
)


def _kind_argv(pidfile: Path) -> list[str]:
    return [sys.executable, "-c", KIND, str(pidfile)]


def _pid_lesen(pidfile: Path) -> int:
    for _ in range(100):
        if pidfile.is_file() and pidfile.read_text().strip():
            return int(pidfile.read_text().strip())
        time.sleep(0.05)
    raise AssertionError("Kind hat seine PID nicht geschrieben")


def test_f2_ausnahme_im_beobachter_beendet_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probesitz, "BEOBACHTUNGS_TAKT_S", 0.2)
    pidfile = tmp_path / "pid"

    def beobachter(_ticket: str) -> str | None:
        _pid_lesen(pidfile)
        raise RuntimeError("Prozessliste kaputt")

    with pytest.raises(RuntimeError):
        probesitz.session_fahren(
            _kind_argv(pidfile), cwd=tmp_path, env=dict(os.environ), zeitlimit_s=60, beobachter=beobachter, ticket="1"
        )
    assert not _pid_lebt(_pid_lesen(pidfile)), "Kind läuft nach der Ausnahme weiter"


def test_f3_zeitlimit_toetet_kind_das_sigterm_ignoriert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probesitz, "BEOBACHTUNGS_TAKT_S", 0.2)
    monkeypatch.setattr(probesitz, "BEENDEN_FRIST_S", 1.0)
    pidfile = tmp_path / "pid"
    lauf = probesitz.session_fahren(
        _kind_argv(pidfile), cwd=tmp_path, env=dict(os.environ), zeitlimit_s=0.5, beobachter=lambda _t: None, ticket="1"
    )
    assert lauf.zeit_ueberschritten
    assert lauf.exit_code != 0
    assert not _pid_lebt(_pid_lesen(pidfile)), "Kind lebt nach dem Zeitlimit weiter"


def test_f9_windows_weg_ohne_killpg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probesitz, "BEOBACHTUNGS_TAKT_S", 0.2)
    monkeypatch.setattr(probesitz, "BEENDEN_FRIST_S", 1.0)
    monkeypatch.setattr(probesitz, "_posix", lambda: False)
    pidfile = tmp_path / "pid"
    lauf = probesitz.session_fahren(
        _kind_argv(pidfile), cwd=tmp_path, env=dict(os.environ), zeitlimit_s=0.5, beobachter=lambda _t: None, ticket="1"
    )
    assert lauf.zeit_ueberschritten
    assert not _pid_lebt(_pid_lesen(pidfile))


# --- 4. gh issue create: Grund aus der Antwort ------------------------------------------


def test_f4_exit_0_ohne_nummer_eigener_grund(repo: Path) -> None:
    laeufer = Laeufer({"issue create": (0, "Creating issue in x/y\n", "")})
    ergebnisse = probesitz.wegwerf_lauf(
        repo, _konfig(repo), laeufer=laeufer, which=_which_alles,
        session_starter=lambda *a, **k: pytest.fail("kein Start ohne Ticket"),
    )
    assert not ergebnisse[2].ok
    assert "Ticket-Nummer" in ergebnisse[2].grund, ergebnisse[2]
    assert ergebnisse[2].grund != "gh nicht angemeldet"
    assert not any("issue close" in t for t in laeufer.texte())


def test_f4_stderr_wird_grund(repo: Path) -> None:
    laeufer = Laeufer({"issue create": (1, "", "HTTP 401: Bad credentials (https://api.github.com/graphql)")})
    ergebnisse = probesitz.wegwerf_lauf(
        repo, _konfig(repo), laeufer=laeufer, which=_which_alles,
        session_starter=lambda *a, **k: pytest.fail("kein Start ohne Ticket"),
    )
    assert "401" in ergebnisse[2].grund, ergebnisse[2]
    assert ergebnisse[2].fehlt_noch == "gh auth login"


# --- 5. Sandbox: nur echte Sperre zählt als ✓ --------------------------------------------


def _sandbox_laeufer(aussen: tuple[int, str]) -> Laeufer:
    class Sperre(Laeufer):
        def __call__(self, argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
            self.aufrufe.append(list(argv))
            ziel = Path(argv[-1])
            if "aussen" in ziel.parent.name:
                return subprocess.CompletedProcess(argv, aussen[0], "", aussen[1])
            ziel.touch()
            return subprocess.CompletedProcess(argv, 0, "", "")

    return Sperre()


@pytest.mark.parametrize("exit_code,stderr", [(124, "Zeitlimit 120s überschritten"), (126, "bwrap: exec failed"), (127, "No such file")])
def test_f5_zeitlimit_und_fehlstart_sind_rot(repo: Path, tmp_path: Path, exit_code: int, stderr: str) -> None:
    heim = tmp_path / "heim"
    heim.mkdir()
    erg = probesitz.pruefe_sandbox(_konfig(repo), repo, laeufer=_sandbox_laeufer((exit_code, stderr)), which=_which_alles, home=heim)
    assert not erg.ok, erg
    assert str(exit_code) in erg.grund


def test_f5_exit_1_ohne_sperr_meldung_ist_rot(repo: Path, tmp_path: Path) -> None:
    heim = tmp_path / "heim"
    heim.mkdir()
    erg = probesitz.pruefe_sandbox(_konfig(repo), repo, laeufer=_sandbox_laeufer((1, "touch: Kaputt")), which=_which_alles, home=heim)
    assert not erg.ok, erg


def test_f5_permission_denied_ist_gruen(repo: Path, tmp_path: Path) -> None:
    heim = tmp_path / "heim"
    heim.mkdir()
    erg = probesitz.pruefe_sandbox(_konfig(repo), repo, laeufer=_sandbox_laeufer((1, "touch: cannot touch: Permission denied")), which=_which_alles, home=heim)
    assert erg.ok, erg


# --- 6. Lauf-Kennung: Punkt 7 nur aus demselben Lauf ------------------------------------


def _gruen_bis_6(lauf_id: str) -> dict[str, Any]:
    zustand: dict[str, Any] = {}
    for nummer in range(1, 7):
        zustand = probesitz.eintragen(zustand, probesitz.Ergebnis(nummer, probesitz.PUNKTE[nummer - 1], True, "ok"), lauf_id=lauf_id)
    return zustand


def test_f6_p7_verweigert_aeltere_punkte(repo: Path) -> None:
    konfig = _konfig(repo)
    konfig["mail"]["befehl"] = ["true"]
    erg = probesitz.pruefe_mail(repo, konfig, _gruen_bis_6("alt"), lauf_id="neu", melden=lambda *a, **k: pytest.fail("keine Mail"))
    assert not erg.ok
    assert erg.grund.startswith("Punkte aus älterem Lauf: 1, 2, 3, 4, 5, 6")
    assert erg.fehlt_noch == "vollständiger Lauf ohne --punkt"


def test_f6_p7_gleicher_lauf_geht_raus(repo: Path) -> None:
    konfig = _konfig(repo)
    konfig["mail"]["befehl"] = ["true"]
    gesehen: list[str] = []
    erg = probesitz.pruefe_mail(repo, konfig, _gruen_bis_6("gleich"), lauf_id="gleich", melden=lambda *a, **k: gesehen.append(a[1]) or True)
    assert erg.ok and gesehen == ["probesitz_gruen"]


def test_f6_laufen_punkt_7_allein_ist_rot(repo: Path) -> None:
    probesitz.speichere_zustand(repo, _gruen_bis_6("alt"))
    konfig = _konfig(repo)
    konfig["mail"]["befehl"] = ["true"]
    ergebnisse = probesitz.laufen(repo, konfig, punkte={7}, melden=lambda *a, **k: pytest.fail("keine Mail"))
    assert [e.nummer for e in ergebnisse] == [7]
    assert not ergebnisse[0].ok and "älterem Lauf" in ergebnisse[0].grund


def test_f6_zeige_zeigt_zeitstempel(repo: Path) -> None:
    zustand = probesitz.eintragen({}, probesitz.Ergebnis(1, probesitz.PUNKTE[0], True, "ok"), lauf_id="x")
    zeile = next(z for z in probesitz.zeige(zustand).splitlines() if " 1 Login trägt" in z)
    ts = zustand["punkte"]["1"]["ts"]  # ISO
    assert f"{ts[8:10]}.{ts[5:7]}. {ts[11:16]}" in zeile, zeile


# --- 7. Aufräum-Fehlschläge stehen im Beleg -----------------------------------------------


def test_f7_aufraeum_fehler_im_beleg_und_prune(repo: Path, tmp_path: Path) -> None:
    laeufer = Laeufer(
        {
            "issue create": (0, "https://github.com/x/y/issues/4711\n", ""),
            "issue close": (1, "", "GraphQL: Could not resolve to an Issue"),
            "branch -D": (1, "", "error: branch 'ticket-4711' not found"),
        }
    )
    ergebnisse = probesitz.wegwerf_lauf(
        repo, _konfig(repo), laeufer=laeufer, which=_which_alles,
        session_starter=_lauf(0, tmp_path / "lauf.log"), worktree_anlegen=lambda *_: None,
    )
    beleg = ergebnisse[2].beleg
    assert "Aufräumen" in beleg and "issue close" in beleg and "Could not resolve" in beleg, beleg
    texte = laeufer.texte()
    assert any("worktree prune" in t for t in texte), texte
    assert sum("branch -D" in t for t in texte) == 2, texte
    assert texte.index(next(t for t in texte if "worktree prune" in t)) < len(texte) - 1


# --- 8. bau.py --probesitz gibt Rechte mit ----------------------------------------------


@pytest.fixture()
def heim(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    context_mode_attrappe.plugin_anlegen(home)
    binaer = home / "bin"
    binaer.mkdir()
    (binaer / "claude").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (binaer / "claude").chmod(0o755)
    return home


def test_f8_dry_run_zeigt_allowed_tools(repo: Path, heim: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_REPO"}
    env["HOME"] = str(heim)
    env["PATH"] = f"{heim / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    ergebnis = subprocess.run(
        [sys.executable, str(SKRIPTE / "bau.py"), "999", "--probesitz", "--dry-run"],
        cwd=str(repo), env=env, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    befehl = ergebnis.stdout.split("Befehl:", 1)[1]
    assert "--allowedTools" in befehl and '"Bash(git *)"' in befehl and "Write" in befehl, befehl
    # Variadische Option: das nächste Flag muss vor dem Prompt kommen, sonst frisst sie ihn.
    nach = befehl.split("--allowedTools", 1)[1]
    assert "--settings" in nach


# --- 10./11. Prompt + CLI ------------------------------------------------------------------


def test_f10_runde_2_add_nur_diese_datei() -> None:
    prompt = probesitz.WEGWERF_PROMPT.format(ticket=1)
    assert prompt.count("git add nur diese Datei") == 2


def test_f11_punkt_ausserhalb_1_bis_7_ist_argparse_fehler(repo: Path) -> None:
    ergebnis = subprocess.run(
        [sys.executable, str(CLI), "probesitz", "--punkt", "9", "--zeigen"],
        cwd=str(repo), capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    assert ergebnis.returncode == 2
    assert "invalid choice" in ergebnis.stderr


def test_f11_docs_exit_2_nur_konfig() -> None:
    for datei in (SKILL / "SKILL.md", SKILL / "to_spawn" / "README.md"):
        text = datei.read_text(encoding="utf-8")
        assert "2 = Konfig/Repo unlesbar" in text, datei
