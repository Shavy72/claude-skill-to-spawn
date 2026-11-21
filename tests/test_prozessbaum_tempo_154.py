"""Skill-Issue #154: die Baum-Abfrage unter Windows startet keinen Prozess mehr.

Vorher startete jeder Aufruf PowerShell und fragte WMI ``Win32_Process`` ab (Median
1382 ms). Geprüft wird das untere Quartil von 20 Aufrufen von ``baum()`` gegen 50 ms.

Warum unteres Quartil statt Median: Last macht einzelne Aufrufe langsamer, nie schneller
als möglich. Auf einem voll ausgelasteten PC dürfen also bis zu 15 von 20 Aufrufen
ausreißen, der Test bleibt grün. Ein Prozess-Start kostet aber immer über 500 ms
(PowerShell allein ~700 ms); kehrt er zurück, liegen alle 20 Aufrufe darüber, und der Test
wird sicher rot. Das Quartil statt des Minimums verlangt zusätzlich, dass der schnelle Weg
der Normalfall ist (5 von 20) und nicht ein Glückstreffer. 50 ms liegen gut viermal über
dem Kernel-Weg (~11 ms bei ~770 Prozessen).
"""

from __future__ import annotations

import statistics
import sys
import time

import pytest

from to_spawn import prozessbaum

AUFRUFE = 20
SCHWELLE_MS = 50.0


@pytest.mark.skipif(sys.platform != "win32", reason="misst den Windows-Weg")
def test_baum_abfrage_unteres_quartil_unter_schwelle() -> None:
    dauern_ms = []
    for _ in range(AUFRUFE):
        start = time.perf_counter()
        prozessbaum.baum(1)
        dauern_ms.append((time.perf_counter() - start) * 1000)
    quartil = statistics.quantiles(dauern_ms, n=4)[0]
    assert quartil < SCHWELLE_MS, (
        f"unteres Quartil {quartil:.1f} ms (min {min(dauern_ms):.1f}, Median {statistics.median(dauern_ms):.1f}, "
        f"max {max(dauern_ms):.1f})"
    )
