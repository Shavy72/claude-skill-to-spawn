"""#457 (Rest nach PR #486): eindeutiger tmux-Session-Name je Repo an genau einer Stelle.

Prüft alle nach PR #486 offenen Punkte aus Issue #457:
1. Session-Name je Repo aus genau einer Quelle (``to_spawn.tmux_aufruf`` / ``to_spawn.py sitzung``):
   zwei Repos mit derselben Spec-Nummer bekommen zwei getrennte Sessions und laufen parallel.
2. Übergangszeit: bestehende eigene Sessions im alten Namen ``spec-<S>`` werden weiter gefunden,
   fremde ``spec-<S>`` bleiben unberührt (eigenes Repo bekommt ``spec-<kurz>-<S>``).
3. ``aufseher-stand`` zeigt je Repo nur die eigenen Fenster — nie die eines anderen Repos.
4. ``origin``-Adresse wird unabhängig von ``https``/``ssh``/``.git``/Groß-Kleinschreibung normalisiert.
5. ``capo.py`` (Folge-Runde / ``_starte_folge_runde``) prüft den Besitzer der Ziel-Session und
   verweigert bei fremdem Repo; das fremde Fenster bleibt unangetastet.
6. ``aufpasser`` und ``hausmeister`` erkennen sowohl ``spec-<S>`` als auch ``spec-<kurz>-<S>``.
7. Nicht-Dry-Run-Nachweis: ein echter Start-Versuch gegen eine fremde Session gleichen Namens
   bricht mit Exit 9 ab und lässt deren Fenster nachweislich unberührt (behebt :131 aus PR #486).

Echt: ``spawn_srv.sh``, ``to_spawn.py``, ``tmux`` (eigener Server per ``-L``), ``git``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import test_spec_rand_e2 as e2
from test_einrichtungs_ticket_410 import _aufbau

from to_spawn import aufpasser, aufseher_stand, capo, tmux_aufruf

SPAWN_SH = e2.CLI.parent / "skripte" / "spawn_srv.sh"

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux fehlt")


@pytest.fixture()
def socket() -> Iterator[str]:
    """Eigener tmux-Server je Test; danach beendet (nur dieser Socket)."""
    name = f"t457r-{uuid.uuid4().hex[:8]}"
    yield name
    subprocess.run(["tmux", "-L", name, "kill-server"], check=False, capture_output=True)


def _tmux(socket: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["tmux", "-L", socket, *args], check=False, capture_output=True, text=True)


def _git_ordner(ordner: Path, origin: str | None) -> Path:
    ordner.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(ordner)], check=True)
    if origin:
        subprocess.run(["git", "-C", str(ordner), "remote", "add", "origin", origin], check=True)
    return ordner


def _sitzung_anlegen(socket: str, name: str, ordner: Path, fenster: str) -> None:
    lauf = _tmux(
        socket,
        "new-session",
        "-d",
        "-s",
        name,
        "-n",
        fenster,
        "-c",
        str(ordner),
        "sleep 300",
    )
    assert lauf.returncode == 0, lauf.stderr


def _tmux_huelle(bin_ordner: Path, socket: str) -> Path:
    bin_ordner.mkdir(parents=True, exist_ok=True)
    huelle = bin_ordner / "tmux"
    huelle.write_text(
        f'#!/usr/bin/env bash\nexec {shutil.which("tmux")!r} -L {socket!r} "$@"\n',
        encoding="utf-8",
    )
    huelle.chmod(0o755)
    return huelle


def _repo_aufbau(
    wurzel: Path,
    socket: str,
    *,
    origin: str | None = None,
    ordnername: str | None = None,
) -> tuple[Path, dict[str, str]]:
    """Ein lauffähiges Wegwerf-Repo für ``spawn_srv.sh`` mit eigenem ``tmux -L socket``."""
    wurzel.mkdir(parents=True, exist_ok=True)
    repo, _welt, umgebung = _aufbau(
        wurzel,
        konfig={
            "hauptzweig": "main",
            "worktree_basis": (wurzel / "wt").as_posix(),
            "speicher": {"max_sessions": 9, "min_frei_mib": 0, "staffel_s": 0},
            "leitstand": {"aktiv": False},
        },
    )
    if ordnername:
        ziel = repo.parent / ordnername
        repo.rename(ziel)
        repo = ziel
    if origin is not None:
        subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin", origin], check=True)
    bin_ordner = wurzel / "bin"
    _tmux_huelle(bin_ordner, socket)
    umgebung = {k: v for k, v in umgebung.items() if k != "TMUX"}
    umgebung.update(
        {
            "PATH": f"{bin_ordner}{os.pathsep}{umgebung['PATH']}",
            "TO_SPAWN_REPO": str(repo),
            "TO_SPAWN_TEST_BIN": str(bin_ordner),
            "TO_SPAWN_TMUX": json.dumps([shutil.which("tmux"), "-L", socket]),
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return repo, umgebung


def _spawn(
    repo: Path, umgebung: dict[str, str], *extra: str
) -> subprocess.CompletedProcess[str]:
    from to_spawn import bash_wahl

    return bash_wahl.lauf(
        [str(SPAWN_SH), str(e2.SPEC), *extra],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _cli_sitzung(repo: Path, umgebung: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(e2.CLI), "sitzung", str(e2.SPEC), *extra],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


# --- 1. Einzige Quelle + Parser ----------------------------------------------


def test_spec_aus_sitzung_erkennt_alt_und_neu() -> None:
    """Parser erkennt alte ``spec-<S>`` und neue ``spec-<kurz>-<S>`` (auch mit Ziffern/Bindestrich)."""
    assert tmux_aufruf.spec_aus_sitzung("spec-1") == "1"
    assert tmux_aufruf.spec_aus_sitzung("spec-1607") == "1607"
    assert tmux_aufruf.spec_aus_sitzung("spec-duoplus-management-42") == "42"
    assert tmux_aufruf.spec_aus_sitzung("spec-to-spawn-probe-srv-1-1") == "1"
    assert tmux_aufruf.spec_aus_sitzung("spec-to-spawn-probe-2-mit-11") == "11"
    assert tmux_aufruf.spec_aus_sitzung("leit") is None
    assert tmux_aufruf.spec_aus_sitzung("spec-ohne-zahl") is None


def test_sitzung_name_je_repo_eindeutig_und_klon_stabil(tmp_path: Path) -> None:
    """Zwei Repos liefern verschiedene Namen; zwei Klone (https vs ssh) denselben."""
    a_https = _git_ordner(tmp_path / "a-1", "https://github.com/org/to-spawn-probe-srv-1.git")
    a_ssh = _git_ordner(tmp_path / "a-klon", "git@github.com:ORG/To-Spawn-Probe-Srv-1.git")
    b = _git_ordner(tmp_path / "b-1", "https://github.com/org/to-spawn-probe-2-mit.git")

    name_a_https = tmux_aufruf.sitzung_name(a_https, 1)
    name_a_ssh = tmux_aufruf.sitzung_name(a_ssh, 1)
    name_b = tmux_aufruf.sitzung_name(b, 1)

    assert name_a_https == name_a_ssh == "spec-to-spawn-probe-srv-1-1"
    assert name_b == "spec-to-spawn-probe-2-mit-1"
    assert name_a_https != name_b


# --- 2. Zwei Repos mit gleicher Spec-Nummer gleichzeitig ---------------------


@pytest.mark.braucht("bash", "posix")
def test_zwei_repos_gleiche_spec_bekommen_getrennte_sessions(tmp_path: Path, socket: str) -> None:
    """Abnahme #457: zwei Repos mit gleicher Spec-Nummer starten parallel in zwei Sessions."""
    repo_a, env_a = _repo_aufbau(tmp_path / "a", socket, origin="https://github.com/test/probe-eins.git")
    repo_b, env_b = _repo_aufbau(tmp_path / "b", socket, origin="https://github.com/test/probe-zwei.git")

    cli_a = _cli_sitzung(repo_a, env_a)
    cli_b = _cli_sitzung(repo_b, env_b)
    assert cli_a.returncode == 0, cli_a.stderr
    assert cli_b.returncode == 0, cli_b.stderr
    sitz_a = cli_a.stdout.strip()
    sitz_b = cli_b.stdout.strip()
    assert sitz_a == f"spec-probe-eins-{e2.SPEC}"
    assert sitz_b == f"spec-probe-zwei-{e2.SPEC}"
    assert sitz_a != sitz_b

    # Repo A hat bereits eine laufende Session; Repo B startet trotzdem sauber mit eigener Session.
    _sitzung_anlegen(socket, sitz_a, repo_a, f"wache {e2.SPEC}")
    lauf_b = _spawn(repo_b, env_b, "--dry-run")
    ausgabe_b = lauf_b.stdout + lauf_b.stderr
    assert lauf_b.returncode == 0, ausgabe_b
    assert sitz_b in ausgabe_b and "Probelauf — nichts gestartet." in ausgabe_b, ausgabe_b


# --- 3. Übergangszeit: bestehende alte spec-<S> vs. fremde spec-<S> ----------


@pytest.mark.braucht("bash", "posix")
def test_bestehende_eigene_alte_session_wird_weiter_gefunden_auch_bei_https_ssh(
    tmp_path: Path, socket: str
) -> None:
    """Alte Session ``spec-<S>`` desselben Repos (HTTPS vs SSH) wird weiter gefunden."""
    repo, env = _repo_aufbau(tmp_path / "eigen", socket, origin="https://github.com/test/wegwerf.git")
    klon_ssh = _git_ordner(tmp_path / "klon-ssh", "git@github.com:Test/Wegwerf.git")
    _sitzung_anlegen(socket, f"spec-{e2.SPEC}", klon_ssh, f"wache {e2.SPEC}")

    cli = _cli_sitzung(repo, env)
    assert cli.returncode == 0, cli.stderr
    assert cli.stdout.strip() == f"spec-{e2.SPEC}"

    lauf = _spawn(repo, env, "--dry-run")
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert f"tmux-Session spec-{e2.SPEC}" in ausgabe, ausgabe


@pytest.mark.braucht("bash", "posix")
def test_fremde_alte_session_blockiert_anderes_repo_nicht(tmp_path: Path, socket: str) -> None:
    """Alte Session ``spec-<S>`` eines anderen Repos → eigenes Repo bekommt ``spec-<kurz>-<S>``."""
    fremd = _git_ordner(tmp_path / "fremd", "https://github.com/fremd/anderes-repo.git")
    _sitzung_anlegen(socket, f"spec-{e2.SPEC}", fremd, f"wache {e2.SPEC}")

    repo, env = _repo_aufbau(tmp_path / "eigen", socket, origin="https://github.com/test/wegwerf.git")
    cli = _cli_sitzung(repo, env)
    assert cli.returncode == 0, cli.stderr
    assert cli.stdout.strip() == f"spec-wegwerf-{e2.SPEC}"


# --- 4. Echter Kollisions-Schutz ohne --dry-run (behebt :131) ----------------


@pytest.mark.braucht("bash", "posix")
def test_kollision_gleicher_kurzname_anderes_repo_verweigert_echten_start(
    tmp_path: Path, socket: str
) -> None:
    """Zwei verschiedene Repos mit gleichem Kurznamen (``fremd/wegwerf`` vs ``test/wegwerf``):
    echter Start OHNE ``--dry-run`` bricht mit Exit 9 ab, fremde Session bleibt unberührt.
    Im Gegensatz zu ``--dry-run`` würde ein kaputter Schutz hier wirklich ein Fenster anlegen!"""
    fremd = _git_ordner(tmp_path / "fremd", "https://github.com/fremd/wegwerf.git")
    ziel_sitzung = f"spec-wegwerf-{e2.SPEC}"
    _sitzung_anlegen(socket, ziel_sitzung, fremd, f"wache {e2.SPEC}")
    vorher = _tmux(socket, "list-windows", "-t", f"={ziel_sitzung}", "-F", "#W").stdout.splitlines()

    repo, env = _repo_aufbau(tmp_path / "eigen", socket, origin="https://github.com/test/wegwerf.git")
    lauf = _spawn(repo, env, "--ohne-regularien", "--nur-wache")

    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 9, ausgabe
    assert ziel_sitzung in ausgabe and "fremd/wegwerf" in ausgabe and "#457" in ausgabe, ausgabe
    nachher = _tmux(socket, "list-windows", "-t", f"={ziel_sitzung}", "-F", "#W").stdout.splitlines()
    assert nachher == vorher


# --- 5. aufseher-stand zeigt je Repo nur die eigenen Fenster -----------------


def test_aufseher_stand_trennt_fenster_zweier_repos_gleicher_spec(
    tmp_path: Path, socket: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Abnahme #457: zwei Repos mit gleicher Spec-Nummer laufen parallel — ``bau_fenster``
    zeigt jedem Repo ausschließlich seine eigenen Fenster."""
    repo_a = _git_ordner(tmp_path / "repo-a", "https://github.com/test/probe-eins.git")
    repo_b = _git_ordner(tmp_path / "repo-b", "https://github.com/test/probe-zwei.git")
    sitz_a = tmux_aufruf.sitzung_name(repo_a, 1)
    sitz_b = tmux_aufruf.sitzung_name(repo_b, 1)
    _sitzung_anlegen(socket, sitz_a, repo_a, "bau 10")
    _sitzung_anlegen(socket, sitz_b, repo_b, "bau 20")

    monkeypatch.setenv("TO_SPAWN_TMUX", json.dumps([shutil.which("tmux"), "-L", socket]))

    fenster_a = aufseher_stand.bau_fenster(1, repo=repo_a)
    fenster_b = aufseher_stand.bau_fenster(1, repo=repo_b)

    assert fenster_a is not None and set(fenster_a) == {10}, fenster_a
    assert fenster_b is not None and set(fenster_b) == {20}, fenster_b
    assert fenster_a[10].startswith(f"={sitz_a}:")
    assert fenster_b[20].startswith(f"={sitz_b}:")


def test_aufseher_stand_ignoriert_fremde_alte_spec_session(
    tmp_path: Path, socket: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alte Session ``spec-1`` gehört Repo A; Repo B ohne eigene Session darf A's Fenster nicht sehen."""
    repo_a = _git_ordner(tmp_path / "repo-a", "https://github.com/test/probe-eins.git")
    repo_b = _git_ordner(tmp_path / "repo-b", "https://github.com/test/probe-zwei.git")
    _sitzung_anlegen(socket, "spec-1", repo_a, "bau 10")

    monkeypatch.setenv("TO_SPAWN_TMUX", json.dumps([shutil.which("tmux"), "-L", socket]))

    fenster_a = aufseher_stand.bau_fenster(1, repo=repo_a)
    fenster_b = aufseher_stand.bau_fenster(1, repo=repo_b)

    assert fenster_a is not None and set(fenster_a) == {10}, fenster_a
    assert fenster_b is None, fenster_b


# --- 6. capo.py Neustart / Folge-Runde mit Besitzer-Prüfung ------------------


def test_capo_starte_folge_runde_verweigert_fremde_session(
    tmp_path: Path, socket: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``capo._starte_folge_runde`` darf kein Fenster in die Session eines anderen Repos legen."""
    fremd = _git_ordner(tmp_path / "fremd", "https://github.com/fremd/app.git")
    eigen = _git_ordner(tmp_path / "eigen", "https://github.com/eigen/app.git")
    sitz = tmux_aufruf.sitzung_name(eigen, 7)
    _sitzung_anlegen(socket, sitz, fremd, "wache 7")
    vorher = _tmux(socket, "list-windows", "-t", f"={sitz}", "-F", "#W").stdout.splitlines()

    monkeypatch.setenv("TO_SPAWN_TMUX", json.dumps([shutil.which("tmux"), "-L", socket]))
    befehl = capo.folge_befehl(eigen, 7, 12, ["wache 7"], "Beleg fehlt", sitzung=sitz)
    fehler = capo._starte_folge_runde(eigen, 12, befehl, True, "Beleg fehlt")

    assert fehler and "#457" in fehler and "fremd/app" in fehler, fehler
    nachher = _tmux(socket, "list-windows", "-t", f"={sitz}", "-F", "#W").stdout.splitlines()
    assert nachher == vorher


def test_capo_folge_runde_verweigert_bei_kollision_ohne_fenster_anzulegen(
    tmp_path: Path, socket: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``capo._folge_runde`` prüft die Ziel-Session und meldet Weigerung statt fremde Fenster anzufassen."""
    fremd = _git_ordner(tmp_path / "fremd", "https://github.com/fremd/app.git")
    eigen = _git_ordner(tmp_path / "eigen", "https://github.com/eigen/app.git")
    sitz = tmux_aufruf.sitzung_name(eigen, 7)
    _sitzung_anlegen(socket, sitz, fremd, "wache 7")
    vorher = _tmux(socket, "list-windows", "-t", f"={sitz}", "-F", "#W").stdout.splitlines()

    monkeypatch.setenv("TO_SPAWN_TMUX", json.dumps([shutil.which("tmux"), "-L", socket]))
    folgerunden: dict[str, int] = {}
    zeilen = capo._folge_runde(
        eigen,
        {},
        "eigen/app",
        7,
        12,
        [],
        folgerunden,
        wt_zeit=None,
        jetzt=datetime.now(timezone.utc),
        dry_run=False,
        gelernt=[],
    )

    assert any("WEIGERUNG" in z and "#457" in z for z in zeilen), zeilen
    assert folgerunden == {}
    nachher = _tmux(socket, "list-windows", "-t", f"={sitz}", "-F", "#W").stdout.splitlines()
    assert nachher == vorher


# --- 7. Aufpasser + spec_session_schliessen ----------------------------------


def test_aufpasser_sitzungen_erkennt_alte_und_neue_namen(tmp_path: Path, socket: str) -> None:
    """Aufpasser listet sowohl ``spec-<S>`` als auch ``spec-<kurz>-<S>`` mit richtiger Spec-Nummer."""
    repo_a = _git_ordner(tmp_path / "a", "https://github.com/test/probe-eins.git")
    repo_b = _git_ordner(tmp_path / "b", "https://github.com/test/probe-zwei.git")
    _sitzung_anlegen(socket, "spec-1", repo_a, "wache 1")
    _sitzung_anlegen(socket, "spec-probe-zwei-1", repo_b, "wache 1")
    _sitzung_anlegen(socket, "anderes", repo_a, "shell")

    instanz = aufpasser.Aufpasser(
        aufpasser.Einstellungen(tmux_socket=socket, zustand=tmp_path / "zustand", trocken=True)
    )
    gefunden = dict(instanz.sitzungen())
    assert gefunden == {"spec-1": "1", "spec-probe-zwei-1": "1"}


def test_spec_session_schliessen_schliesst_neue_sitzung_nur_des_eigenen_repos(
    tmp_path: Path, socket: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``spec_session_schliessen`` räumt ``spec-<kurz>-<S>`` des eigenen Repos weg, fremdes bleibt."""
    repo_a = _git_ordner(tmp_path / "a", "https://github.com/test/probe-eins.git")
    repo_b = _git_ordner(tmp_path / "b", "https://github.com/test/probe-zwei.git")
    sitz_a = tmux_aufruf.sitzung_name(repo_a, 3)
    sitz_b = tmux_aufruf.sitzung_name(repo_b, 3)
    _sitzung_anlegen(socket, sitz_a, repo_a, "wache 3")
    _sitzung_anlegen(socket, sitz_b, repo_b, "wache 3")

    monkeypatch.setenv("TO_SPAWN_TMUX", json.dumps([shutil.which("tmux"), "-L", socket]))
    tmux_aufruf.spec_session_schliessen(repo_a, 3)

    offen = set(_tmux(socket, "list-sessions", "-F", "#{session_name}").stdout.split())
    assert sitz_a not in offen
    assert sitz_b in offen
