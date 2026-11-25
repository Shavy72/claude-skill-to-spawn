"""#460 Abnahme: fremde Sessions füllen den Rechner — ``spawn`` meldet „wartet auf Platz“, nicht „nichts gestartet“.

1. 12 arbeitende Claude-Sessions, davon nur 2 in der Spec-Sitzung (Obergrenze 6, Rechner-Grenze 12; 284: war 9):
   der Aufseher bekommt sein Fenster (er wartet darin selbst auf Platz), der Rest wartet auf ihn —
   Exit 0, Warte-Zeile nennt den Grund, kein „nicht gestartet“.
2. Gegenprobe: 9 eigene Sessions → Exit 5, Meldung nennt die volle Spec-Sitzung.

Attrappen wie in ``test_obergrenze_rechner_460_fix1_spawn`` (``/proc``, meminfo, tmux);
``spawn_srv.sh`` und ``to_spawn.py speicher`` sind echt.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import test_spec_rand_e2 as e2
from test_obergrenze_rechner_460_fix1_spawn import _spawn


@pytest.mark.braucht("bash", "posix")
def test_neun_sessions_zwei_eigene_wartet_auf_platz_exit_0(tmp_path: Path) -> None:
    lauf, gestartet = _spawn(tmp_path, max_sessions=6, belegt=10, belegt_eigen=2)
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "nicht gestartet" not in ausgabe and "nichts gestartet" not in ausgabe, ausgabe
    assert f"wache {e2.SPEC}" in gestartet and "bau 901" not in gestartet, gestartet
    warte = [z for z in ausgabe.splitlines() if z.startswith("wartet auf Platz: bau 901")]
    assert warte and "Rechner voll" in warte[0], ausgabe


@pytest.mark.braucht("bash", "posix")
def test_neun_eigene_sessions_exit_5_mit_stimmender_meldung(tmp_path: Path) -> None:
    lauf, gestartet = _spawn(tmp_path, max_sessions=6, belegt_eigen=9)
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 5, ausgabe
    assert f"nicht gestartet: wache {e2.SPEC}" in ausgabe, ausgabe
    assert f"schon 9 Claude-Sessions in spec-{e2.SPEC} (Obergrenze 6)" in ausgabe, ausgabe
    assert "wartet auf Platz" not in ausgabe and "new-" not in gestartet, (ausgabe, gestartet)
