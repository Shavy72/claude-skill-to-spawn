"""#541: Das Aufseher-Log geht in eine Datei, kein Terminal-Handler am Root-Logger."""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

WACHE = Path(__file__).resolve().parent.parent / "skripte" / "wache.py"


def _wache():
    sys.path.insert(0, str(WACHE.parent.parent))
    spec = importlib.util.spec_from_file_location("wache_541", WACHE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _terminal_handler():
    return [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        and getattr(h, "stream", None)
        in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__)
    ]


def test_log_geht_in_datei_nicht_aufs_terminal(tmp_path):
    root = logging.getLogger()
    alt = (root.handlers[:], root.level)
    try:
        datei = _wache().logging_einrichten(tmp_path, 541, auf_terminal=False)
        assert _terminal_handler() == []
        logging.getLogger("wache").info("Zeile für die Datei")
        for h in root.handlers:
            h.flush()
        assert datei == tmp_path / ".to-spawn" / "wache_541.log"
        assert "Zeile für die Datei" in datei.read_text(encoding="utf-8")
    finally:
        for h in root.handlers[:]:
            root.removeHandler(h)
            h.close()
        for h in alt[0]:
            root.addHandler(h)
        root.setLevel(alt[1])
