"""#191 Teil 1: Ticket-Stand unbekannt vor einer Staffel-Runde, gh hängt, tmux-Start-Weg.

1. ``weiter_entscheidung``: unbekannt → begrenzt erneut fragen, dann laut Exit 2 (nie
   still Exit 0 wie „geschlossen“).
2. ``gh.ticket_offen`` hat eine Zeitgrenze; hängt gh, endet ``bau.py`` laut statt stumm.
3. Prüf-Test „nur eine Start-Stelle“ erkennt auch ``tmux new-window … claude``.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
LAUF = SKILL / "tests" / "hilfen" / "bau_lauf_97.py"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

from to_spawn import gh, startstelle  # noqa: E402
from test_startstelle_97 import CLAUDE_STARTER  # noqa: E402

# --- 1. Stand unbekannt vor der Staffel-Runde ------------------------------------------


def _zaehler(*antworten: bool | None) -> tuple[list[int], startstelle.TicketPruefer]:
    aufrufe: list[int] = []

    def pruefer(_t: str, _c: Path | None) -> bool | None:
        aufrufe.append(1)
        return antworten[min(len(aufrufe), len(antworten)) - 1]

    return aufrufe, pruefer


def test_unbekannt_wird_wiederholt_dann_exit_2() -> None:
    aufrufe, pruefer = _zaehler(None)
    e = startstelle.weiter_entscheidung("1", pruefer=pruefer, abstand=0)
    assert not e.erlaubt
    assert e.exit_code == startstelle.EXIT_UNKLAR
    assert len(aufrufe) == startstelle.STAND_VERSUCHE == 3


def test_kurzer_aussetzer_wird_ueberbrueckt() -> None:
    aufrufe, pruefer = _zaehler(None, True)
    assert startstelle.weiter_entscheidung("1", pruefer=pruefer, abstand=0).erlaubt
    assert len(aufrufe) == 2


def test_geschlossen_bleibt_exit_0_ohne_wiederholung() -> None:
    aufrufe, pruefer = _zaehler(False)
    e = startstelle.weiter_entscheidung("1", pruefer=pruefer, abstand=0)
    assert (e.erlaubt, e.exit_code, len(aufrufe)) == (False, 0, 1)


@pytest.fixture()
def umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(startstelle.TEST_OFFEN_ENV, raising=False)
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv(startstelle.STAND_ABSTAND_ENV, "0")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    return dict(os.environ)


def _bau(env: dict[str, str], beleg: Path, ticket: str) -> tuple[int, str, float]:
    anfang = time.monotonic()
    fertig = subprocess.run(
        [sys.executable, str(LAUF), str(beleg), ticket, "--sofort"],
        env=env,
        cwd=str(beleg.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    return fertig.returncode, fertig.stdout + fertig.stderr, time.monotonic() - anfang


def test_bau_gh_aussetzer_vor_staffel_runde_endet_laut(umgebung: dict[str, str], tmp_path: Path) -> None:
    """Start-Tür sieht OPEN, danach fällt gh aus → Exit 2 + Meldung, kein stilles Exit 0."""
    zaehler = tmp_path / "gh_zaehler"
    stub = tmp_path / "gh_erst_offen.py"
    stub.write_text(
        "import pathlib, sys\n"
        f"z = pathlib.Path({str(zaehler)!r})\n"
        "n = int(z.read_text() or 0) + 1 if z.exists() else 1\n"
        "z.write_text(str(n))\n"
        "if n == 1:\n"
        '    print(\'{"state": "OPEN"}\')\n'
        "    sys.exit(0)\n"
        "print('HTTP 502', file=sys.stderr)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    umgebung["TO_SPAWN_GH_STUB"] = str(stub)
    beleg = tmp_path / "beleg.txt"
    code, ausgabe, _ = _bau(umgebung, beleg, "19101")
    assert not beleg.exists(), ausgabe
    assert code == startstelle.EXIT_UNKLAR, ausgabe
    assert "nicht bestimmbar" in ausgabe
    assert zaehler.read_text() == str(1 + startstelle.STAND_VERSUCHE)


# --- 2. gh hängt ----------------------------------------------------------------------


def test_gh_haengt_bau_endet_laut_und_gibt_sperre_frei(umgebung: dict[str, str], tmp_path: Path) -> None:
    stub = tmp_path / "gh_haengt.py"
    stub.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
    umgebung["TO_SPAWN_GH_STUB"] = str(stub)
    umgebung[gh.TICKET_STAND_TIMEOUT_ENV] = "1"
    umgebung[startstelle.SPERR_ORDNER_ENV] = str(tmp_path / "sperren")
    beleg = tmp_path / "beleg.txt"
    code, ausgabe, dauer = _bau(umgebung, beleg, "19102")
    assert code == startstelle.EXIT_UNKLAR, ausgabe
    assert "nicht bestimmbar" in ausgabe
    assert dauer < 60, dauer
    # Sperre frei: ein zweiter Lauf scheitert wieder am Stand, nicht an „läuft schon“.
    code, ausgabe, _ = _bau(umgebung, beleg, "19102")
    assert code == startstelle.EXIT_UNKLAR and "läuft schon" not in ausgabe, ausgabe


def test_ticket_stand_zeitgrenze_hat_einen_standardwert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(gh.TICKET_STAND_TIMEOUT_ENV, raising=False)
    assert gh.ticket_stand_timeout() == gh.TICKET_STAND_TIMEOUT_S > 0
    monkeypatch.setenv(gh.TICKET_STAND_TIMEOUT_ENV, "kaputt")
    assert gh.ticket_stand_timeout() == gh.TICKET_STAND_TIMEOUT_S


# --- 3. tmux-Fenster mit claude --------------------------------------------------------

#: Fenster-Befehle von tmux, die einen Prozess starten.
_TMUX_START = {"new-window", "respawn-window", "split-window", "new-session", "respawn-pane"}
_CLAUDE_BEFEHL = __import__("re").compile(r"(?<![\w./~-])claude(\.exe|\.cmd)?(?![\w./-])")


def _texte(knoten: ast.AST) -> list[str]:
    return [k.value for k in ast.walk(knoten) if isinstance(k, ast.Constant) and isinstance(k.value, str)]


def tmux_claude_fenster(quelle: str) -> list[int]:
    """Zeilen der Funktionen, die ein tmux-Fenster öffnen UND darin ``claude`` starten.

    Je Funktion (auch verteilt über Listen/f-Strings, wie ``capo.bau_startzeile``):
    ein tmux-Start-Befehl und ein Text, in dem ``claude`` als Befehl steht.
    """
    treffer = []
    for knoten in ast.walk(ast.parse(quelle)):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            texte = _texte(knoten)
            if _TMUX_START & set(texte) and any(_CLAUDE_BEFEHL.search(t) for t in texte):
                treffer.append(knoten.lineno)
    return treffer


ANGRIFF = """
def folgerunde(self, spec, ticket):
    tmux("new-window", "-t", f"=spec-{spec}", f"bash -lc \\"claude --ticket {ticket}\\"", start=True)
"""
ANGRIFF_LISTE = """
def folgerunde(spec):
    kopf = ["new-window", "-t", f"=spec-{spec}"]
    return [*_tmux_befehl(), *kopf, "bash -lc 'claude -p x'"]
"""
ERLAUBT = """
def fenster(self, befehl):
    self.tmux("new-window", "-d", "-n", "bau 7", f"bash -lc {befehl}")  # bau.py → Start-Stelle
    pfad = "~/.claude/skills/to-spawn/claude-skill"
"""


def test_tmux_claude_angriff_wird_erkannt() -> None:
    assert tmux_claude_fenster(ANGRIFF)
    assert tmux_claude_fenster(ANGRIFF_LISTE)
    assert not tmux_claude_fenster(ERLAUBT)


def test_kein_tmux_fenster_startet_claude_an_der_startstelle_vorbei() -> None:
    funde = {}
    for datei in [*SKILL.glob("to_spawn/**/*.py"), *SKILL.glob("skripte/**/*.py")]:
        name = datei.relative_to(SKILL).as_posix()
        zeilen = tmux_claude_fenster(datei.read_text(encoding="utf-8"))
        if zeilen and name not in CLAUDE_STARTER:
            funde[name] = zeilen
    assert not funde, f"tmux-Fenster mit claude an bau.py vorbei: {funde}"
