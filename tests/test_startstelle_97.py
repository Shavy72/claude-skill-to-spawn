"""#97 (L1 E2-13, App #1390): eine Start-Stelle mit Sperre, geschlossene Tickets starten nie.

Weg-Tests starten ``skripte/bau.py`` als echte Prozesse (``tests/hilfen/bau_lauf_97.py``);
der Ticket-Stand kommt aus der gh-Attrappe ``tests/hilfen/gh_stub.py`` (Antwort-Format
wie ``gh issue view --json state``). Dazu ein Prüf-Test: Claude startet nur hinter
``startstelle.betreten``.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
LAUF = SKILL / "tests" / "hilfen" / "bau_lauf_97.py"
GH_STUB = SKILL / "tests" / "hilfen" / "gh_stub.py"
sys.path.insert(0, str(SKILL))

from to_spawn import startstelle  # noqa: E402


@pytest.fixture()
def umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Echte Ticket-Prüfung (kein Test-Durchwinken), gh-Attrappe, Repo im tmp."""
    monkeypatch.delenv(startstelle.TEST_OFFEN_ENV, raising=False)
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(GH_STUB))
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    return dict(os.environ)


def _start(env: dict[str, str], beleg: Path, *argv: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(LAUF), str(beleg), *argv],
        env=env,
        cwd=str(beleg.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )


def _starts(beleg: Path) -> int:
    return len(beleg.read_text(encoding="utf-8").splitlines()) if beleg.is_file() else 0


def test_geschlossenes_ticket_startet_nie(umgebung: dict[str, str], tmp_path: Path) -> None:
    beleg = tmp_path / "beleg.txt"
    umgebung["GH_STUB_ZU"] = "9701"
    lauf = _start(umgebung, beleg, "9701", "--sofort")
    ausgabe, _ = lauf.communicate(timeout=60)
    assert _starts(beleg) == 0, ausgabe
    assert lauf.returncode == 0, ausgabe
    assert "geschlossen" in ausgabe


def test_gh_fehler_startet_nichts_und_meldet_laut(umgebung: dict[str, str], tmp_path: Path) -> None:
    beleg = tmp_path / "beleg.txt"
    umgebung["GH_STUB_KAPUTT"] = "9702"
    lauf = _start(umgebung, beleg, "9702", "--sofort")
    ausgabe, _ = lauf.communicate(timeout=60)
    assert _starts(beleg) == 0, ausgabe
    assert lauf.returncode == startstelle.EXIT_UNKLAR, ausgabe
    assert "nicht bestimmbar" in ausgabe


def test_zwei_gleichzeitige_starts_ergeben_eine_session(umgebung: dict[str, str], tmp_path: Path) -> None:
    beleg = tmp_path / "beleg.txt"
    laeufe = [_start(umgebung, beleg, "9703", "--sofort") for _ in range(2)]
    ausgaben = [lauf.communicate(timeout=60)[0] for lauf in laeufe]
    assert _starts(beleg) == 1, ausgaben
    assert sorted(lauf.returncode for lauf in laeufe) == [0, 0], ausgaben
    assert any("läuft schon" in a for a in ausgaben), ausgaben


def test_nach_ende_ist_die_sperre_frei(umgebung: dict[str, str], tmp_path: Path) -> None:
    """Tote Sperre = keine Sperre: nach dem ersten Lauf startet der zweite normal."""
    beleg = tmp_path / "beleg.txt"
    for _ in range(2):
        lauf = _start(umgebung, beleg, "9704", "--sofort")
        lauf.communicate(timeout=60)
    assert _starts(beleg) == 2


def test_abloesung_startet_neben_alter_aber_nur_einmal(umgebung: dict[str, str], tmp_path: Path) -> None:
    beleg = tmp_path / "beleg.txt"
    alt = _start(umgebung, beleg, "9705", "--sofort")
    for _ in range(100):  # warten, bis die alte Session läuft (hält die Sperre)
        if _starts(beleg):
            break
        subprocess.run([sys.executable, "-c", "import time; time.sleep(0.1)"], check=True)
    neu = [_start(umgebung, beleg, "9705", "--sofort", "--ohne-prompt") for _ in range(2)]
    ausgaben = [p.communicate(timeout=60)[0] for p in (alt, *neu)]
    assert _starts(beleg) == 2, ausgaben  # alt + genau eine Ablösung
    assert any("Ablösung läuft schon" in a for a in ausgaben), ausgaben


def test_staffel_runde_endet_bei_geschlossenem_ticket(tmp_path: Path) -> None:
    assert startstelle.weiter_erlaubt("1", pruefer=lambda _t, _c: False) is False
    assert startstelle.weiter_erlaubt("1", pruefer=lambda _t, _c: None) is False
    assert startstelle.weiter_erlaubt("1", pruefer=lambda _t, _c: True) is True


# --- Prüf-Test: eine Start-Stelle -------------------------------------------------------

#: Dateien, die Claude starten dürfen. Nur ``bau.py`` startet Ticket-Sessions; Aufseher
#: (``wache.py``) und Leitstand sind keine Ticket-Sessions. Neue Einträge nur mit Grund.
CLAUDE_STARTER = {"skripte/bau.py", "skripte/wache.py", "skripte/leitstand_fenster.py"}


def _claude_finder() -> set[str]:
    treffer: set[str] = set()
    for datei in [*SKILL.glob("to_spawn/**/*.py"), *SKILL.glob("skripte/**/*.py")]:
        baum = ast.parse(datei.read_text(encoding="utf-8"))
        for knoten in ast.walk(baum):
            if (
                isinstance(knoten, ast.Call)
                and isinstance(knoten.func, ast.Attribute)
                and knoten.func.attr == "finde"
                and knoten.args
                and isinstance(knoten.args[0], ast.Constant)
                and knoten.args[0].value == "claude"
            ):
                treffer.add(datei.relative_to(SKILL).as_posix())
    return treffer


def test_claude_startet_nur_an_bekannten_stellen() -> None:
    assert _claude_finder() == CLAUDE_STARTER


def test_bau_main_geht_durch_die_startstelle() -> None:
    baum = ast.parse((SKILL / "skripte" / "bau.py").read_text(encoding="utf-8"))
    main = next(k for k in baum.body if isinstance(k, ast.FunctionDef) and k.name == "main")
    aufrufe = [
        (k.lineno, k.func.attr)
        for k in ast.walk(main)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and isinstance(k.func.value, ast.Name)
        and k.func.value.id == "startstelle"
    ]
    starts = [
        k.lineno
        for k in ast.walk(main)
        if isinstance(k, ast.Call) and isinstance(k.func, ast.Name) and k.func.id == "starte_session"
    ]
    betreten = [z for z, name in aufrufe if name == "betreten"]
    weiter = [z for z, name in aufrufe if name in ("weiter_erlaubt", "weiter_entscheidung")]
    assert len(betreten) == 1 and starts, (aufrufe, starts)
    assert betreten[0] < min(starts)
    assert weiter and min(weiter) < min(starts), "Staffel-Runden prüfen den Ticket-Stand neu"


def test_shell_startwege_starten_claude_nie_direkt() -> None:
    """spawn_srv.sh / spawn_local.ps1 öffnen nur Fenster mit ``bau.py`` (→ Start-Stelle)."""
    for name in ("skripte/spawn_srv.sh", "spawn_local.ps1"):
        text = (SKILL / name).read_text(encoding="utf-8")
        assert "bau.py" in text, name
        for zeile in text.splitlines():
            code = zeile.split("#", 1)[0]
            assert not __import__("re").search(r"\bclaude(\.exe|\.cmd)?\s+--", code), (name, zeile)
