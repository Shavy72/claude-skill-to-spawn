"""Hilfsstarter für pytest: schreibt die exakten Knoten-Kennungen je Testlauf (#149).

Läuft direkt im Ziel-Interpreter des geprüften Repos (nur Python-Standardbibliothek + ``pytest``),
ohne ``to_spawn`` zu importieren. Das eingebundene Controller-Plugin sammelt ``report.nodeid`` aus
``pytest_runtest_logreport`` — seriell wie unter ``pytest-xdist`` — und schreibt den Beleg neben
den ``--junitxml``-Bericht (``<xml>.beleg.json``). So lassen sich auch kollidierende Pfade
(``tests/a.b/`` vs. ``tests/a/b/``) und importierte Testfunktionen eindeutig auseinanderhalten.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_BERICHT_PREFIX = "--junitxml="


class _BelegPlugin:
    """Controller-Plugin: merkt sich je ``nodeid`` alle Phasen-Ausgänge und ob ein Abbruch vorlag."""

    def __init__(self, ziel: Path) -> None:
        self._ziel = ziel
        self._phasen: dict[str, dict[str, set[str]]] = {}
        self._xfail: set[str] = set()
        self._abgebrochen = False

    def pytest_runtest_logreport(self, report: Any) -> None:
        nid = getattr(report, "nodeid", "")
        if not nid:
            self._abgebrochen = True
            return
        phasen = self._phasen.setdefault(nid, {})
        phasen.setdefault(report.when, set()).add(report.outcome)
        if getattr(report, "wasxfail", None):
            self._xfail.add(nid)

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        bestanden = sorted(
            nid
            for nid, ph in self._phasen.items()
            if nid not in self._xfail
            and set(ph) == {"setup", "call", "teardown"}
            and ph["setup"] == {"passed"}
            and ph["call"] == {"passed"}
            and ph["teardown"] == {"passed"}
        )
        daten = {
            "alle": sorted(self._phasen),
            "bestanden": bestanden,
            "abgebrochen": self._abgebrochen or bool(getattr(session, "shouldstop", False)),
        }
        self._ziel.parent.mkdir(parents=True, exist_ok=True)
        self._ziel.write_text(json.dumps(daten), encoding="utf-8")


def _xml_pfad(args: list[str]) -> Path | None:
    """Letzter ``--junitxml``-Pfad aus ``args``; ``None``, wenn keiner übergeben wurde."""
    pfad: str | None = None
    for idx, teil in enumerate(args):
        if teil.startswith(_BERICHT_PREFIX):
            pfad = teil[len(_BERICHT_PREFIX) :]
        elif teil == "--junitxml" and idx + 1 < len(args):
            pfad = args[idx + 1]
    return Path(pfad) if pfad else None


def main() -> int:
    # Wie ``python -m pytest``: aktuelles Arbeitsverzeichnis auf ``sys.path[0]``, nicht den
    # Skript-Ordner (sonst könnten Namen aus ``to_spawn/pruef/`` Repo-Module verdecken).
    if sys.path:
        sys.path[0] = ""
    else:
        sys.path.insert(0, "")
    args = sys.argv[1:]
    xml = _xml_pfad(args)
    plugins: list[object] = [_BelegPlugin(Path(f"{xml}.beleg.json"))] if xml is not None else []
    return int(pytest.main(args, plugins=plugins))


if __name__ == "__main__":
    raise SystemExit(main())
