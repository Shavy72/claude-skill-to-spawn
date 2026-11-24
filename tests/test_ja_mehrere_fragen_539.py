"""``ja <N>`` bei mehreren offenen Fragen nennt je Frage Kennung, erste Zeile und den fertigen Befehl (#539).

Echt laufen: die CLI (``to_spawn.py frage``/``ja``/``nutzer-antwort``, ohne TTY) gegen ein tmp-HOME mit
eingetragener Ja-Sperre; der gemeldete Befehl wird wörtlich ausgeführt. Attrappen: keine.
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest
from test_live_ja_kurzer_befehl_531 import _cli

from to_spawn import briefkasten, ja_einrichten


@pytest.fixture
def mit_sperre(tmp_path: Path) -> Path:
    heim = tmp_path / "heim"
    heim.mkdir()
    assert ja_einrichten.sperre_eintragen(heim / ".claude", sys.executable) is True
    return heim


def _frage(repo: Path, heim: Path, text: str) -> str:
    lauf = _cli(
        repo,
        heim,
        "frage",
        "7",
        "--frage",
        text,
        "--vorschlag",
        "nein",
        "--klasse",
        "2",
    )
    assert lauf.returncode == 0, lauf.stderr
    return lauf.stdout.rsplit("id", 1)[1].strip()


def test_ja_bei_mehreren_fragen_nennt_kennung_erste_zeile_und_befehl(tmp_path: Path, mit_sperre: Path) -> None:
    erste = _frage(tmp_path, mit_sperre, "Live schalten?\nDetails stehen im Ticket.")
    zweite = _frage(tmp_path, mit_sperre, "Alten Zweig löschen?")
    lauf = _cli(tmp_path, mit_sperre, "ja", "7")
    assert lauf.returncode == 1
    meldung = lauf.stderr
    for fid, zeile in ((erste, "Live schalten?"), (zweite, "Alten Zweig löschen?")):
        assert fid in meldung and zeile in meldung
        assert f"nutzer-antwort 7 --id {fid}" in meldung
    assert "Details stehen im Ticket." not in meldung  # nur die erste Zeile der Frage

    befehle = re.findall(r"`! ([^`]*nutzer-antwort[^`]*)`", meldung)
    assert len(befehle) == 2, meldung
    gewaehlt = next(b for b in befehle if f"--id {erste}" in b)
    argv = shlex.split(gewaehlt)
    assert Path(argv[1]).name == "to_spawn.py" and Path(argv[1]).is_file()
    lauf = _cli(tmp_path, mit_sperre, *argv[2:])  # wörtlich der gemeldete Befehl (ohne Python + Skript)
    assert lauf.returncode == 0, lauf.stderr
    # lesen() meldet NUTZER, solange irgendeine Klasse-2-Frage offen ist — also über offene() prüfen
    assert [o["id"] for o in briefkasten.offene(["7"], hauptbaum=tmp_path)] == [zweite]
