"""Duplikat-Prüfung: sh -c-Wrapper zählt nicht als zweiter bau.py (Spec 68, #74)."""

import pytest

from to_spawn import prozessbaum
from to_spawn.respawn import _bau_pid_fuer


def test_shell_wrapper_zaehlt_nicht() -> None:
    zeile = "702827 sh -c ./.venv/bin/python scripts/bau.py 74 --sofort; exec bash"
    assert _bau_pid_fuer(zeile, 74) is False


def test_python_prozess_zaehlt() -> None:
    zeile = "702829 ./.venv/bin/python scripts/bau.py 74 --sofort"
    assert _bau_pid_fuer(zeile, 74) is True


def test_anderes_ticket_zaehlt_nicht() -> None:
    assert _bau_pid_fuer("1 python scripts/bau.py 740 --sofort", 74) is False


@pytest.mark.parametrize(
    ("zeile", "erwartet"),
    [
        ("702827 sh -c ./.venv/bin/python scripts/bau.py 74 --sofort; exec bash", True),
        ("/bin/bash -lc 'python scripts/bau.py 74'", True),
        ("702829 ./.venv/bin/python scripts/bau.py 74 --sofort", False),
        ("bash", False),
        ("", False),
    ],
)
def test_wrapper_erkennung_lebt_in_prozessbaum(zeile: str, erwartet: bool) -> None:
    assert prozessbaum.ist_wrapper_zeile(zeile) is erwartet
