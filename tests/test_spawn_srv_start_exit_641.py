"""#641 Teil 2: ``spawn_srv.sh`` — Exit ≠ 0 nach Anlauf meldet „nicht gestartet“ und weckt Aufseher.

Prüft das Verhalten von ``skripte/spawn_srv.sh`` auf dem Bau-Server (Linux):
1. Schreibt ein gestartetes Fenster ``bau <N>`` eine neue Zeile ``bau.py #<N> Exit <rc>`` mit
   ``rc ≠ 0`` in ``.to-spawn/fenster_<N>.log``, meldet ``spawn_srv.sh`` „nicht gestartet“ statt
   „gestartet“, endet mit Exit 8 (``spawn_exit.EXIT_START_FEHLGESCHLAGEN``) und wirft sofort eine
   Klasse-3-Frage in den Briefkasten, über die ``briefkasten_wecker.Wecker`` den Aufseher weckt.
2. Alte Exit-Zeilen von vor dem Start und neue ``Exit 0``-Zeilen lösen keinen Fehlalarm aus.
3. Gemischter Lauf: scheitert ein Ticket und startet ein zweites, wird jedes ehrlich gemeldet.
4. Echter ``tmux``-Lauf (isolierter Socket ``-L``) mit echtem ``bau.py`` und fremdem Worktree
   (Weigerung #499, Exit 9): das Fenster-Log entsteht durch ``spawn_srv.sh`` selbst, der
   Fehlstart wird erkannt, als „nicht gestartet“ gemeldet und im Briefkasten eingeworfen.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import test_spec_rand_e2 as e2
from test_spawn_srv_anlauf_sperre_604 import _umgebung_mit_speicher

from to_spawn import bash_wahl, briefkasten, briefkasten_wecker, capo, spawn_exit

SKILL = Path(__file__).resolve().parents[1]
SPAWN_SH = SKILL / "skripte" / "spawn_srv.sh"


def _tmux_stub_mit_log(
    tmp_path: Path,
    repo: Path,
    *,
    fehler_tickets: dict[str, tuple[int, str]] | None = None,
    exit_null_tickets: set[str] | None = None,
) -> Path:
    """``tmux``-Attrappe: schreibt bei ``new-session``/``new-window`` optional Exit-Zeilen ins Log."""
    fehler = fehler_tickets or {}
    nullen = exit_null_tickets or set()
    fenster = tmp_path / "bin" / "fenster.txt"
    aufrufe = tmp_path / "bin" / "tmux_aufrufe.txt"
    skript_teile = [
        "#!/usr/bin/env bash",
        f'printf "%s\\n" "$*" >> {str(aufrufe)!r}',
        'name=""; prev=""',
        'for a in "$@"; do [ "$prev" = "-n" ] && name="$a"; prev="$a"; done',
        'case "$1" in',
        f"  has-session) [ -s {str(fenster)!r} ] ;;",
        f"  list-sessions) cat {str(tmp_path / 'bin' / 'sitzungen.txt')!r} ;;",
        f"  list-windows) [ -s {str(fenster)!r} ] && cat {str(fenster)!r} ;;",
        "  new-session|new-window)",
        '    case "$name" in',
    ]
    for nr, (code, kontext) in fehler.items():
        log_datei = capo.fenster_log(repo, int(nr))
        skript_teile.extend(
            [
                f'      "bau {nr}")',
                f"        mkdir -p {str(log_datei.parent)!r}",
                f'        printf "%s\\n2026-11-25T02:11:04+01:00 bau.py #{nr} Exit {code}\\n" {kontext!r} >> {str(log_datei)!r}',
                "        ;;",
            ]
        )
    for nr in sorted(nullen):
        log_datei = capo.fenster_log(repo, int(nr))
        skript_teile.extend(
            [
                f'      "bau {nr}")',
                f"        mkdir -p {str(log_datei.parent)!r}",
                f'        printf "2026-11-25T02:11:04+01:00 bau.py #{nr} Exit 0\\n" >> {str(log_datei)!r}',
                f'        printf "%s\\n" "$name" >> {str(fenster)!r}',
                "        ;;",
            ]
        )
    skript_teile.extend(
        [
            "      *)",
            f'        printf "%s\\n" "$name" >> {str(fenster)!r}',
            "        ;;",
            "    esac",
            "    ;;",
            "esac",
            "exit 0",
        ]
    )
    tmux = tmp_path / "bin" / "tmux"
    tmux.write_text("\n".join(skript_teile) + "\n", encoding="utf-8")
    tmux.chmod(0o755)
    return aufrufe


class _WeckerWerkzeug:
    """Minimales Tmux-Werkzeug für ``briefkasten_wecker.Wecker``: zeichnet getippte Weckrufe auf."""

    def __init__(self) -> None:
        self.getippt: list[tuple[str, str]] = []
        self._zeit = 1000.0

    def pane_lage(self, _ziel: str) -> tuple[int, float]:
        return 4242, 0.0

    def claude_laeuft(self, _pid: int) -> bool:
        return True

    def bildschirm(self, _ziel: str, *, farben: bool = False) -> str:
        linie = "─" * 40
        return f"● Aufseher wartet.\n\n{linie}\n❯ \n{linie}\n  ⏵⏵ auto mode\n"

    def jetzt(self) -> float:
        return self._zeit

    def tippen(self, ziel: str, text: str) -> None:
        self.getippt.append((ziel, text))


def _zeilen_mit_praefix(ausgabe: str, praefix: str) -> list[str]:
    return [z for z in ausgabe.splitlines() if z.startswith(praefix)]


@pytest.mark.braucht("bash", "posix")
def test_exit_ungleich_null_nach_start_meldet_nicht_gestartet_und_weckt_aufseher(
    tmp_path: Path,
) -> None:
    """Exit 9 im Fenster-Log → „nicht gestartet“ statt „gestartet“, Exit 8, Aufseher über Briefkasten geweckt."""
    repo, umgebung, _proc, _sessions = _umgebung_mit_speicher(tmp_path)
    _tmux_stub_mit_log(
        tmp_path,
        repo,
        fehler_tickets={
            "901": (
                9,
                "ERROR WEIGERUNG (#499): bau 901: Ordner /tmp/wt-901 ist schon Worktree eines anderen Repos",
            )
        },
    )
    # Auch mit fremder TO_SPAWN_TICKET-Umgebungsvariable muss der Briefkasten-Einwurf gelingen.
    umgebung["TO_SPAWN_TICKET"] = "999"
    # Wecker kennt den Ausgangsstand (leer) vor dem Fehlstart.
    werkzeug: Any = _WeckerWerkzeug()
    wecker = briefkasten_wecker.Wecker(e2.SPEC, repo, "%7", werkzeug=werkzeug)

    lauf = bash_wahl.lauf(
        [str(SPAWN_SH), str(e2.SPEC), "--tickets", "901", "--ohne-wache"],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    ausgabe = lauf.stdout + lauf.stderr

    assert lauf.returncode == spawn_exit.EXIT_START_FEHLGESCHLAGEN, ausgabe
    assert not _zeilen_mit_praefix(ausgabe, "gestartet: bau 901"), ausgabe
    assert _zeilen_mit_praefix(lauf.stderr, "nicht gestartet: bau 901"), ausgabe
    assert "bau.py #901 Exit 9" in lauf.stderr, ausgabe

    offene = briefkasten.offene(["901"], hauptbaum=repo)
    assert len(offene) == 1, offene
    assert offene[0]["ticket"] == "901"
    assert offene[0]["klasse"] == "3"
    assert offene[0]["id"]
    assert "Exit 9" in offene[0]["frage"]

    # Der Briefkasten-Wecker des Aufsehers erkennt die neue Frage sofort und weckt das Aufseher-Pane.
    wecker._briefkasten_blick()
    assert len(werkzeug.getippt) == 1, werkzeug.getippt
    ziel, text = werkzeug.getippt[0]
    assert ziel == "%7"
    assert f"#901 id {offene[0]['id']}" in text, text
    assert spawn_exit.EXIT_START_FEHLGESCHLAGEN == 8
    assert spawn_exit.beschreibung(spawn_exit.EXIT_START_FEHLGESCHLAGEN)


@pytest.mark.braucht("bash", "posix")
def test_alter_exit_vor_start_wird_nicht_als_neuer_fehler_gemeldet(tmp_path: Path) -> None:
    """Alte ``bau.py #N Exit 9``-Zeilen von vor dem Start lösen keinen Fehlalarm aus."""
    repo, umgebung, _proc, _sessions = _umgebung_mit_speicher(tmp_path)
    log_datei = capo.fenster_log(repo, 901)
    log_datei.parent.mkdir(parents=True, exist_ok=True)
    log_datei.write_text("2026-11-24T01:00:00+01:00 bau.py #901 Exit 9\n", encoding="utf-8")
    _tmux_stub_mit_log(tmp_path, repo)
    umgebung["TO_SPAWN_ANLAUF_PRUEF_S"] = "0"

    lauf = bash_wahl.lauf(
        [str(SPAWN_SH), str(e2.SPEC), "--tickets", "901", "--ohne-wache"],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    ausgabe = lauf.stdout + lauf.stderr

    assert lauf.returncode == 0, ausgabe
    assert _zeilen_mit_praefix(ausgabe, "gestartet: bau 901"), ausgabe
    assert "nicht gestartet: bau 901" not in ausgabe, ausgabe
    assert briefkasten.offene(["901"], hauptbaum=repo) == []


@pytest.mark.braucht("bash", "posix")
def test_exit_null_im_fenster_log_ist_kein_fehler(tmp_path: Path) -> None:
    """Eine neue ``bau.py #N Exit 0``-Zeile gilt nicht als Fehlstart."""
    repo, umgebung, _proc, _sessions = _umgebung_mit_speicher(tmp_path)
    _tmux_stub_mit_log(tmp_path, repo, exit_null_tickets={"901"})
    umgebung["TO_SPAWN_ANLAUF_PRUEF_S"] = "0"

    lauf = bash_wahl.lauf(
        [str(SPAWN_SH), str(e2.SPEC), "--tickets", "901", "--ohne-wache"],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    ausgabe = lauf.stdout + lauf.stderr

    assert lauf.returncode == 0, ausgabe
    assert _zeilen_mit_praefix(ausgabe, "gestartet: bau 901"), ausgabe
    assert "nicht gestartet: bau 901" not in ausgabe, ausgabe
    assert briefkasten.offene(["901"], hauptbaum=repo) == []


@pytest.mark.braucht("bash", "posix")
def test_gemischt_ein_ticket_scheitert_anderes_startet(tmp_path: Path) -> None:
    """Scheitert #901 mit Exit 9 während das zweite Ticket anläuft, wird nur #901 als nicht gestartet gemeldet."""
    zweit = str(e2.CHECKPOINT)
    repo, umgebung, _proc, _sessions = _umgebung_mit_speicher(tmp_path)
    _tmux_stub_mit_log(
        tmp_path,
        repo,
        fehler_tickets={"901": (9, "ERROR WEIGERUNG (#499): fremder Worktree")},
    )
    umgebung["TO_SPAWN_ANLAUF_PRUEF_S"] = "0"

    lauf = bash_wahl.lauf(
        [str(SPAWN_SH), str(e2.SPEC), "--tickets", f"901,{zweit}", "--ohne-wache"],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    ausgabe = lauf.stdout + lauf.stderr

    assert lauf.returncode == spawn_exit.EXIT_START_FEHLGESCHLAGEN, ausgabe
    assert not _zeilen_mit_praefix(ausgabe, "gestartet: bau 901"), ausgabe
    assert _zeilen_mit_praefix(lauf.stderr, "nicht gestartet: bau 901"), ausgabe
    assert _zeilen_mit_praefix(ausgabe, f"gestartet: bau {zweit}"), ausgabe
    assert f"nicht gestartet: bau {zweit}" not in ausgabe, ausgabe
    assert len(briefkasten.offene(["901"], hauptbaum=repo)) == 1
    assert briefkasten.offene([zweit], hauptbaum=repo) == []


@pytest.fixture()
def tmux_socket() -> Iterator[str]:
    """Eigener tmux-Server nur für diesen Test; am Ende geschlossen."""
    name = f"t641-{uuid.uuid4().hex[:8]}"
    yield name
    subprocess.run(["tmux", "-L", name, "kill-server"], check=False, capture_output=True)


def _git(ort: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(ort), *args], check=True, capture_output=True, text=True)


@pytest.mark.braucht("bash", "posix")
@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux fehlt")
def test_echter_tmux_bau_exit_9_schreibt_log_meldet_nicht_gestartet_und_weckt(tmp_path: Path, tmux_socket: str) -> None:
    """Echter ``tmux`` + echtes ``bau.py`` + fremder Worktree (Weigerung #499, Exit 9)."""
    echtes_tmux = shutil.which("tmux")
    assert echtes_tmux is not None
    repo, umgebung, _proc, _sessions = _umgebung_mit_speicher(tmp_path)
    # Fremdes Repo belegt denselben konfigurierten Worktree-Ordner wt-901 → bau.py 901 endet mit Exit 9 (#499).
    fremd_bare = tmp_path / "fremd.git"
    subprocess.run(["git", "init", "-q", "--bare", str(fremd_bare)], check=True)
    fremd = tmp_path / "fremd-repo"
    subprocess.run(["git", "clone", "-q", str(fremd_bare), str(fremd)], check=True, capture_output=True)
    _git(fremd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "start")
    wt_fremd = tmp_path / "wt" / "wt-901"
    wt_fremd.parent.mkdir(parents=True, exist_ok=True)
    _git(fremd, "worktree", "add", "-q", "-b", "ticket/901-fremd", str(wt_fremd))

    # Echte tmux-Hülle mit isoliertem Socket.
    huelle = tmp_path / "bin" / "tmux"
    huelle.write_text(
        f'#!/usr/bin/env bash\nexec {echtes_tmux!r} -L {tmux_socket!r} "$@"\n',
        encoding="utf-8",
    )
    huelle.chmod(0o755)
    umgebung["TO_SPAWN_ANLAUF_PRUEF_S"] = "3"

    lauf = bash_wahl.lauf(
        [str(SPAWN_SH), str(e2.SPEC), "--tickets", "901", "--ohne-wache"],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    ausgabe = lauf.stdout + lauf.stderr

    log_datei = capo.fenster_log(repo, 901)
    assert log_datei.is_file(), f"Fenster-Log fehlt — Ausgabe:\n{ausgabe}"
    log_text = log_datei.read_text(encoding="utf-8", errors="replace")
    assert "bau.py #901 Exit 9" in log_text, log_text
    assert lauf.returncode == spawn_exit.EXIT_START_FEHLGESCHLAGEN, ausgabe
    assert not _zeilen_mit_praefix(ausgabe, "gestartet: bau 901"), ausgabe
    assert _zeilen_mit_praefix(lauf.stderr, "nicht gestartet: bau 901"), ausgabe
    offene = briefkasten.offene(["901"], hauptbaum=repo)
    assert len(offene) == 1 and offene[0]["klasse"] == "3", offene
