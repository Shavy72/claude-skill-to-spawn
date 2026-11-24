"""#473: Aufseher-Vorlage startet ``capo.py`` und ``aufraeumen.mjs`` je als eigenen, ausgeschriebenen Bash-Aufruf.

Hintergrund: Der Git-Sperr-Hook liest eine Bash-Zeile mit ``$``-Variable und Nachbar-Befehl
als ganzen Code und sperrt dann den Aufruf (Vorfall Probe-Lauf 24.11.). Darum darf in der
Vorlage kein Aufruf dieser beiden Werkzeuge mit Variable, Umleitung, Pipe, Verkettung oder
vorangestellter Umgebungs-Zuweisung stehen, und die Vorlage verlangt den Einzel-Aufruf.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parent.parent
WACHE = SKILL / "skripte" / "wache.py"
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import waechter_lauf

# Die eine Regel: Werkzeug-Aufruf im Code-Span ohne $, |, >, &&, ; und ohne VAR=-Vorsatz.
WERKZEUG = re.compile(r"capo\.py|aufraeumen\.mjs")
VERBOTEN = re.compile(r"\$|\||>|&&|;|^\s*[A-Za-z_][A-Za-z0-9_]*=")


@pytest.fixture(scope="module")
def wache() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wache_473", WACHE)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _verstoesse(text: str) -> list[str]:
    spans = re.findall(r"`([^`\n]+)`", text)
    return [s for s in spans if WERKZEUG.search(s) and VERBOTEN.search(s)]


def test_vorlage_ohne_verkettete_werkzeug_aufrufe(wache: ModuleType) -> None:
    text = wache.prompt_bauen(900, "x/y", 1800, {"ssh_ziel": "bau-server", "server_repo": "/home/bau/r"})
    spans = re.findall(r"`([^`\n]+)`", text)
    for werkzeug in ("capo.py", "aufraeumen.mjs"):
        assert any(werkzeug in s for s in spans), f"Vorlage nennt keinen Aufruf von {werkzeug} mehr"
    assert _verstoesse(text) == []


def test_weiter_text_capo_aufruf_ausgeschrieben() -> None:
    assert not VERBOTEN.search(waechter_lauf.capo_aufruf(900))


def test_vorlage_verlangt_eigenen_bash_aufruf(wache: ModuleType) -> None:
    text = wache.prompt_bauen(900, "x/y", 1800, {"ssh_ziel": "bau-server", "server_repo": "/home/bau/r"})
    assert "je als eigenen Bash-Aufruf" in text


def test_regel_erkennt_verkettung() -> None:
    probe = "`S=/x; python3 $S/skripte/capo.py 11 2>&1 | tail` und `node aufraeumen.mjs --spec 11 && tmux ls`"
    assert len(_verstoesse(probe)) == 2
    assert _verstoesse("`python /s/skripte/capo.py 11 --katalog`") == []
