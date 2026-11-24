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
        w = _wache()
        datei = w.logging_einrichten(tmp_path, 541, auf_terminal=False)
        w.terminal_log_abschalten()
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


def _sauber(fn):
    root = logging.getLogger()
    alt = (root.handlers[:], root.level)
    try:
        fn()
    finally:
        for h in root.handlers[:]:
            root.removeHandler(h)
            h.close()
        for h in alt[0]:
            root.addHandler(h)
        root.setLevel(alt[1])


def test_fehler_vor_dem_abschalten_auf_stderr_und_in_datei(tmp_path, capsys):
    def lauf():
        w = _wache()
        datei = w.logging_einrichten(tmp_path, 541, auf_terminal=False)
        logging.getLogger("wache").error("Start verweigert")
        assert "Start verweigert" in capsys.readouterr().err
        assert "Start verweigert" in datei.read_text(encoding="utf-8")

    _sauber(lauf)


def test_nach_dem_abschalten_bleibt_nur_der_datei_handler(tmp_path):
    def lauf():
        w = _wache()
        w.logging_einrichten(tmp_path, 541, auf_terminal=False)
        assert _terminal_handler()
        w.terminal_log_abschalten()
        assert _terminal_handler() == []
        assert any(
            isinstance(h, logging.FileHandler) for h in logging.getLogger().handlers
        )

    _sauber(lauf)


def test_auf_terminal_nur_stderr_keine_datei(tmp_path):
    def lauf():
        assert _wache().logging_einrichten(tmp_path, 541, auf_terminal=True) is None
        assert _terminal_handler()
        assert not (tmp_path / ".to-spawn").exists()

    _sauber(lauf)


def test_main_schaltet_terminal_log_vor_fahre_ab(tmp_path, monkeypatch):
    def lauf():
        w = _wache()
        gesehen = {}

        def fahre(**kw):
            gesehen["terminal"] = _terminal_handler()
            gesehen["datei"] = [
                h
                for h in logging.getLogger().handlers
                if isinstance(h, logging.FileHandler)
            ]
            return 0

        monkeypatch.setattr(w.waechter_lauf, "fahre", fahre)
        monkeypatch.setattr(w.sys, "argv", ["wache.py", "541"])
        for name in ("repo_slug_oder_abbruch",):
            monkeypatch.setattr(w, name, lambda: "x/y")
        monkeypatch.setattr(w, "REPO_ORDNER", tmp_path)
        monkeypatch.setattr(w.startklar, "gate", lambda *a, **k: 0)
        monkeypatch.setattr(w.context_mode, "pruefen", lambda **k: "ctx")
        monkeypatch.setattr(w.nest, "git_hooks_einrichten", lambda *a, **k: None)
        monkeypatch.setattr(w.vertrauen, "still_sicherstellen", lambda *a, **k: None)
        monkeypatch.setattr(w, "auf_speicher_warten", lambda *a, **k: None)
        monkeypatch.setattr(w.terminal_maus, "maus_ruhig", lambda: None)
        monkeypatch.setattr(w.starter, "finde", lambda n: "claude")
        monkeypatch.setattr(w.os, "environ", dict(w.os.environ))
        w.main()
        assert gesehen["terminal"] == []
        assert gesehen["datei"]

    _sauber(lauf)
