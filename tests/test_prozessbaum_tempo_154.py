"""Skill-Issue #154: die Baum-Abfrage unter Windows startet keinen Prozess mehr.

Vorher startete jeder Aufruf PowerShell und fragte WMI ``Win32_Process`` ab (Median
1382 ms). Gemessen wird der Median über 20 Aufrufe von ``baum()`` — ein einzelner
Ausreißer auf einem ausgelasteten PC kippt den Median nicht. Die Schwelle 50 ms liegt
gut viermal über dem Kernel-Weg (~11 ms bei ~770 Prozessen) und weit unter jedem
Prozess-Start (PowerShell allein ~700 ms): sie trennt die beiden Wege, ohne bei Last zu wackeln.
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
def test_baum_abfrage_median_unter_schwelle() -> None:
    dauern_ms = []
    for _ in range(AUFRUFE):
        start = time.perf_counter()
        prozessbaum.baum(1)
        dauern_ms.append((time.perf_counter() - start) * 1000)
    median = statistics.median(dauern_ms)
    assert median < SCHWELLE_MS, f"Median {median:.1f} ms (min {min(dauern_ms):.1f}, max {max(dauern_ms):.1f})"
