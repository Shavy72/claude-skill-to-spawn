"""Mess- und Auswerteskript für Etappe 3b-5 (#105): M12-Status im App-Repo und Bremse bei 2 Gates.

Zweck:
1. Bewertet den M12-Status im App-Repo anhand der 3b-0-Entscheidung (#98):
   - Bei Wechsel auf Variante 3 (schnellerer Voll-Lauf, keine Test-Auswahl) gilt laut
     Leit-Entscheidung 21.11. 21:05: „nicht anwendbar, weil keine Auswahl läuft“; der
     Pflicht-Nachweis für M12 liegt im Probe-Repo (#104, zweite unbekannte Fehlerreihe).
   - Bei Kombination wäre eine eigene Fehlerreihe (>= 20 Fehler) im App-Repo nötig.
2. Misst reproduzierbar die **Bremse bei 2 gleichzeitigen Gates** (je ``-n 4``) gegen ein
   Einzel-Gate (``-n 4``) am selben Stand (``nice -n 15`` auf Linux, nur bei 1-Minuten-Last
   ``< 12,0``, Live-Abbruch ab Last ``>= 14,0``) und entscheidet gegen die 3b-6-Schwelle
   ``bremse_faktor <= 1,5`` (zweiter Gate-Platz sinnvoll oder nicht).

Sicherheit:
- Fasst niemals das Quell-Repo schreibend an: für Repo-Messungen entstehen zwei getrennte,
  isolierte Arbeitskopien per ``git clone --shared --no-tags --no-checkout`` im Temp-Ordner,
  die in ``try/finally`` vollständig gelöscht werden.
- Startet Test-Prozesse ausschließlich über ``to_spawn.starter.starte`` mit
  ``to_spawn.prozessbaum.fensterlos()`` und nutzt bestehende Mess-Helfer aus
  ``to_spawn.speicher`` sowie ``messung.pruefzeit_last_290``.
- Keine Personennamen, keine Benutzerpfade, keine Geheimnisse in Ausgabe oder Bericht.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

REPO_WURZEL = Path(__file__).resolve().parents[1]
if str(REPO_WURZEL) not in sys.path:
    sys.path.insert(0, str(REPO_WURZEL))

from messung.pruefzeit_last_290 import (
    LastZuHochFehler,
    SpeicherStand,
    erzeuge_probe_suite,
    prozessbaum_rss_mib,
    pruefe_last_grenze,
    speicher_stand,
)
from to_spawn import prozessbaum, speicher, starter

__all__ = [
    "ABBRUCH_LAST",
    "MAX_START_LAST",
    "SCHWELLE_ZWEITER_PLATZ",
    "BremseAuswertung",
    "GateLauf",
    "LastZuHochFehler",
    "M12AppStatus",
    "ParallelRunde",
    "SpeicherStand",
    "baue_gate_befehl",
    "bereite_isolierte_kopien",
    "bericht_markdown",
    "bewerte_m12_app_repo",
    "fahre_bremse_messung",
    "main",
    "messe_einzel_gate",
    "messe_zwei_gates_parallel",
    "pruefe_start_last",
    "waehle_test_pfade",
    "werte_bremse_aus",
]

log = logging.getLogger("messung.bremse_3b5")

#: Harte Start-Grenze für die 1-Minuten-Last auf dem Linux-Bau-Server (#105).
MAX_START_LAST: float = 12.0
#: Live-Abbruch-Grenze während eines laufenden Messlaufs (Schnitt 3b).
ABBRUCH_LAST: float = 14.0
#: Schwelle aus Vergleich 6.3 / Schnitt 3b-6: zweiter Gate-Platz nur bei Bremse <= 1,5.
SCHWELLE_ZWEITER_PLATZ: float = 1.5
#: Arbeiter-Zahl je Gate laut Issue #105 / Vergleich 6.3.
STANDARD_ARBEITER: int = 4
#: Linux-Prozess-Priorität laut Schnitt 3b-5.
STANDARD_NICE: int = 15
#: Abtast-Intervall für RSS-, Speicher- und Last-Messung (Sekunden).
ABTAST_INTERVALL_S: float = 0.25
#: Eingefrorener Regel-Stand aus dem Probe-Pflichtnachweis (#104).
STANDARD_REGELN_STAND: str = "regeln@fd7e49f"
#: Standard-Dateien, die bei isolierten Sparse-Kopien ausgespart werden (spart Plattenplatz).
STANDARD_OHNE_ORDNER: tuple[str, ...] = ("docs", "archive")


@dataclass(frozen=True)
class M12AppStatus:
    """Ehrliche Bewertung von M12 im App-Repo nach 3b-0-Entscheidung."""

    anwendbar: bool
    entscheidung_3b0: str
    eingebaute_fehler: int
    urteil: str
    begruendung: str
    pflicht_nachweis_probe_issue: int


@dataclass(frozen=True)
class GateLauf:
    """Messwerte eines einzelnen Gate-Prozesses (allein oder als Teil einer Parallel-Runde)."""

    modus: Literal["einzel", "parallel"]
    runde: int
    gate_nr: int
    arbeiter: int
    verteilung: str
    dauer_s: float
    rss_spitze_mib: float
    ram_frei_min_mib: int
    swap_vorher_mib: int
    swap_spitze_mib: int
    last_vorher: float
    last_spitze: float
    exit_code: int
    abgebrochen: bool = False
    abbruch_grund: str = ""

    @property
    def swap_delta_mib(self) -> int:
        return self.swap_spitze_mib - self.swap_vorher_mib


@dataclass(frozen=True)
class ParallelRunde:
    """Messwerte zweier gleichzeitig laufender Gates (je ``-n 4``) in getrennten Kopien."""

    runde: int
    arbeiter: int
    verteilung: str
    gesamt_wand_s: float
    gates: tuple[GateLauf, GateLauf]
    summe_rss_spitze_mib: float
    ram_frei_min_mib: int
    swap_vorher_mib: int
    swap_spitze_mib: int
    last_vorher: float
    last_spitze: float
    abgebrochen: bool = False
    abbruch_grund: str = ""

    @property
    def swap_delta_mib(self) -> int:
        return self.swap_spitze_mib - self.swap_vorher_mib


@dataclass(frozen=True)
class BremseAuswertung:
    """Verdichtete Kennzahlen der Bremse-Messung und Entscheidung für 3b-6."""

    arbeiter: int
    verteilung: str
    runden_einzel: int
    runden_parallel: int
    einzel_median_s: float
    parallel_gate_mittel_s: float
    parallel_gate_max_s: float
    bremse_faktor: float
    bremse_faktor_max: float
    schwelle_zweiter_platz: float
    zweiter_platz_empfohlen: bool
    einzel_rss_max_mib: float
    ram_je_worker_mib: float
    summe_rss_parallel_max_mib: float
    ram_frei_min_mib: int
    swap_delta_max_mib: int
    last_spitze_max: float


def bewerte_m12_app_repo(
    *,
    entscheidung_3b0: str,
    r1_r3_quote: float,
    ungefangene_fehlgriffe: Sequence[str],
    regeln_stand: str = STANDARD_REGELN_STAND,
) -> M12AppStatus:
    """Leitet den M12-Status im App-Repo aus dem 3b-0-Ergebnis (#98) ab."""
    normal = entscheidung_3b0.strip().lower()
    if normal == "variante_3":
        shas = ", ".join(ungefangene_fehlgriffe) if ungefangene_fehlgriffe else "keine"
        begruendung = (
            f"3b-0 (#98) hat wegen R1/R3-Quote {_komma(r1_r3_quote * 100, 1)} % (>= 50 %) und "
            f"{len(ungefangene_fehlgriffe)} unbefangenen Fehlgriffen ({shas}) den Wechsel auf "
            f"Variante 3 (schnellerer Voll-Lauf, keine Auswahl) entschieden. Laut Leit-Entscheidung "
            f"21.11. 21:05 gilt M12 im App-Repo als nicht anwendbar; der Pflicht-Nachweis für M12 "
            f"ist im Probe-Repo (#104, 26/26 Fehler bei {regeln_stand}, 0 übersehen) erbracht."
        )
        return M12AppStatus(
            anwendbar=False,
            entscheidung_3b0="variante_3",
            eingebaute_fehler=0,
            urteil="nicht anwendbar, weil keine Auswahl läuft",
            begruendung=begruendung,
            pflicht_nachweis_probe_issue=104,
        )
    if normal == "kombination":
        return M12AppStatus(
            anwendbar=True,
            entscheidung_3b0="kombination",
            eingebaute_fehler=20,
            urteil="erforderlich (>= 20 eingebaute Fehler gegen eingefrorene Regeln)",
            begruendung=(
                f"3b-0 bleibt bei Kombination; vor Freigabe sind >= 20 eingebaute Fehler im isolierten "
                f"Staging-Klon gegen {regeln_stand} mit 0 übersehenen Fehlern nachzuweisen."
            ),
            pflicht_nachweis_probe_issue=104,
        )
    raise ValueError(f"Unbekannte 3b-0-Entscheidung: {entscheidung_3b0!r} (erwartet 'variante_3' oder 'kombination')")


def pruefe_start_last(
    *,
    max_last: float = MAX_START_LAST,
    loadavg: Path | None = None,
) -> float:
    """Prüft vor einem Messlauf, ob die 1-Minuten-Last unter ``max_last`` liegt."""
    return pruefe_last_grenze(max_last=max_last, loadavg=loadavg)


def baue_gate_befehl(
    *,
    python_exe: str,
    arbeiter: int = STANDARD_ARBEITER,
    verteilung: str = "worksteal",
    basetemp: Path,
    test_pfade: Sequence[str] = (),
    nice_wert: int = STANDARD_NICE,
) -> list[str]:
    """Baut die Befehlsliste für einen isolierten Gate-Prüflauf (ohne Shell)."""
    befehl: list[str] = []
    if os.name == "posix" and nice_wert > 0:
        nice_bin = shutil.which("nice")
        if nice_bin:
            befehl.extend([nice_bin, "-n", str(nice_wert)])
    befehl.extend(
        [
            python_exe,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--basetemp={basetemp}",
        ]
    )
    if arbeiter > 1:
        befehl.extend(["-n", str(arbeiter), "--dist", verteilung])
    befehl.extend(test_pfade)
    return befehl


def _gate_umgebung(basetemp: Path) -> dict[str, str]:
    """Isolierte Umgebung je Gate-Prozess (eigenes Temp, kein Bytecode, keine globalen Sperren)."""
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    tmp_ordner = basetemp / "tmp"
    tmp_ordner.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = str(tmp_ordner)
    env["TEMP"] = str(tmp_ordner)
    env["TMP"] = str(tmp_ordner)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Globale App-Sperren deaktivieren, damit 2 Gates gezielt gleichzeitig gemessen werden
    # und keine Sperrdateien außerhalb des Mess-Temp-Ordners entstehen.
    env["TEST_PLAETZE"] = "aus"
    env["PYTEST_DECKEL"] = "aus"
    return env


def _beende_prozess(proc: Any) -> None:
    """Beendet einen laufenden Hilfsprozess samt Wartephase sauber."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except OSError:
            return


def _aktuelle_last_oder(ersatz: float) -> float:
    wert = speicher.last_1m()
    return wert if wert is not None else ersatz


def messe_einzel_gate(
    arbeits_dir: Path,
    *,
    python_exe: str,
    arbeiter: int = STANDARD_ARBEITER,
    verteilung: str = "worksteal",
    runde: int = 1,
    max_start_last: float = MAX_START_LAST,
    abbruch_last: float = ABBRUCH_LAST,
    test_pfade: Sequence[str] = (),
    nice_wert: int = STANDARD_NICE,
    timeout_s: float = 3000.0,
) -> GateLauf:
    """Fährt genau ein Gate (``-n arbeiter``) und misst Dauer, RSS-Spitze, RAM, Swap und Last."""
    last_vorher = pruefe_start_last(max_last=max_start_last)
    stand_vorher = speicher_stand()
    with tempfile.TemporaryDirectory(prefix="gate_einzel_bt_", dir=arbeits_dir.parent) as bt_str:
        basetemp = Path(bt_str)
        befehl = baue_gate_befehl(
            python_exe=python_exe,
            arbeiter=arbeiter,
            verteilung=verteilung,
            basetemp=basetemp,
            test_pfade=test_pfade,
            nice_wert=nice_wert,
        )
        env = _gate_umgebung(basetemp)

        rss_spitze = 0.0
        ram_frei_min = stand_vorher.frei_mib
        swap_spitze = stand_vorher.swap_belegt_mib
        last_spitze = last_vorher
        abgebrochen = False
        abbruch_grund = ""

        start = time.monotonic()
        proc = starter.starte(
            befehl,
            cwd=arbeits_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            notnagel=None,
            **prozessbaum.fensterlos(),
        )
        try:
            while proc.poll() is None:
                rss_spitze = max(rss_spitze, prozessbaum_rss_mib(proc.pid))
                stand = speicher_stand()
                ram_frei_min = min(ram_frei_min, stand.frei_mib)
                swap_spitze = max(swap_spitze, stand.swap_belegt_mib)
                aktuelle_last = _aktuelle_last_oder(last_spitze)
                last_spitze = max(last_spitze, aktuelle_last)
                if aktuelle_last >= abbruch_last:
                    abgebrochen = True
                    abbruch_grund = f"unter Last abgebrochen ({aktuelle_last:.2f} >= {abbruch_last:.2f})"
                    log.warning("Einzel-Gate Runde %d: %s", runde, abbruch_grund)
                    _beende_prozess(proc)
                    break
                if time.monotonic() - start >= timeout_s:
                    abgebrochen = True
                    abbruch_grund = f"Zeitgrenze überschritten ({timeout_s:.0f} s)"
                    log.warning("Einzel-Gate Runde %d: %s", runde, abbruch_grund)
                    _beende_prozess(proc)
                    break
                time.sleep(ABTAST_INTERVALL_S)
        finally:
            _beende_prozess(proc)

        dauer_s = round(time.monotonic() - start, 2)
        exit_code = int(proc.returncode if proc.returncode is not None else -15)

    return GateLauf(
        modus="einzel",
        runde=runde,
        gate_nr=1,
        arbeiter=arbeiter,
        verteilung=verteilung,
        dauer_s=dauer_s,
        rss_spitze_mib=round(rss_spitze, 1),
        ram_frei_min_mib=ram_frei_min,
        swap_vorher_mib=stand_vorher.swap_belegt_mib,
        swap_spitze_mib=swap_spitze,
        last_vorher=round(last_vorher, 2),
        last_spitze=round(last_spitze, 2),
        exit_code=exit_code,
        abgebrochen=abgebrochen,
        abbruch_grund=abbruch_grund,
    )


def messe_zwei_gates_parallel(
    arbeits_dirs: tuple[Path, Path],
    *,
    python_exe: str,
    arbeiter: int = STANDARD_ARBEITER,
    verteilung: str = "worksteal",
    runde: int = 1,
    max_start_last: float = MAX_START_LAST,
    abbruch_last: float = ABBRUCH_LAST,
    test_pfade: Sequence[str] = (),
    nice_wert: int = STANDARD_NICE,
    timeout_s: float = 3000.0,
) -> ParallelRunde:
    """Startet genau 2 Gates (je ``-n arbeiter``) gleichzeitig in zwei getrennten Ordnern."""
    last_vorher = pruefe_start_last(max_last=max_start_last)
    stand_vorher = speicher_stand()
    dir1, dir2 = arbeits_dirs
    with (
        tempfile.TemporaryDirectory(prefix="gate_par1_bt_", dir=dir1.parent) as bt1_str,
        tempfile.TemporaryDirectory(prefix="gate_par2_bt_", dir=dir2.parent) as bt2_str,
    ):
        basetemps = (Path(bt1_str), Path(bt2_str))
        befehle = tuple(
            baue_gate_befehl(
                python_exe=python_exe,
                arbeiter=arbeiter,
                verteilung=verteilung,
                basetemp=bt,
                test_pfade=test_pfade,
                nice_wert=nice_wert,
            )
            for bt in basetemps
        )
        umgebungen = tuple(_gate_umgebung(bt) for bt in basetemps)

        rss_spitzen = [0.0, 0.0]
        summe_rss_spitze = 0.0
        ram_frei_min = stand_vorher.frei_mib
        swap_spitze = stand_vorher.swap_belegt_mib
        last_spitze = last_vorher
        abgebrochen = False
        abbruch_grund = ""
        fertig_zeiten: list[float | None] = [None, None]
        prozesse: list[Any] = []

        start = time.monotonic()
        try:
            for idx in range(2):
                proc = starter.starte(
                    befehle[idx],
                    cwd=arbeits_dirs[idx],
                    env=umgebungen[idx],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    notnagel=None,
                    **prozessbaum.fensterlos(),
                )
                prozesse.append(proc)

            while any(t is None for t in fertig_zeiten):
                jetzt = time.monotonic()
                summe_jetzt = 0.0
                for idx, proc in enumerate(prozesse):
                    if fertig_zeiten[idx] is not None:
                        continue
                    if proc.poll() is not None:
                        fertig_zeiten[idx] = round(jetzt - start, 2)
                        continue
                    rss = prozessbaum_rss_mib(proc.pid)
                    rss_spitzen[idx] = max(rss_spitzen[idx], rss)
                    summe_jetzt += rss
                summe_rss_spitze = max(summe_rss_spitze, summe_jetzt)

                stand = speicher_stand()
                ram_frei_min = min(ram_frei_min, stand.frei_mib)
                swap_spitze = max(swap_spitze, stand.swap_belegt_mib)
                aktuelle_last = _aktuelle_last_oder(last_spitze)
                last_spitze = max(last_spitze, aktuelle_last)

                if aktuelle_last >= abbruch_last:
                    abgebrochen = True
                    abbruch_grund = f"unter Last abgebrochen ({aktuelle_last:.2f} >= {abbruch_last:.2f})"
                    log.warning("Parallel-Runde %d: %s", runde, abbruch_grund)
                    for proc in prozesse:
                        _beende_prozess(proc)
                    break
                if jetzt - start >= timeout_s:
                    abgebrochen = True
                    abbruch_grund = f"Zeitgrenze überschritten ({timeout_s:.0f} s)"
                    log.warning("Parallel-Runde %d: %s", runde, abbruch_grund)
                    for proc in prozesse:
                        _beende_prozess(proc)
                    break
                if any(t is None for t in fertig_zeiten):
                    time.sleep(ABTAST_INTERVALL_S)
        finally:
            for proc in prozesse:
                _beende_prozess(proc)

        gesamt_wand_s = round(time.monotonic() - start, 2)
        gate_laeufe: list[GateLauf] = []
        for idx, proc in enumerate(prozesse):
            dauer = fertig_zeiten[idx] if fertig_zeiten[idx] is not None else gesamt_wand_s
            code = int(proc.returncode if proc.returncode is not None else -15)
            gate_laeufe.append(
                GateLauf(
                    modus="parallel",
                    runde=runde,
                    gate_nr=idx + 1,
                    arbeiter=arbeiter,
                    verteilung=verteilung,
                    dauer_s=dauer,
                    rss_spitze_mib=round(rss_spitzen[idx], 1),
                    ram_frei_min_mib=ram_frei_min,
                    swap_vorher_mib=stand_vorher.swap_belegt_mib,
                    swap_spitze_mib=swap_spitze,
                    last_vorher=round(last_vorher, 2),
                    last_spitze=round(last_spitze, 2),
                    exit_code=code,
                    abgebrochen=abgebrochen,
                    abbruch_grund=abbruch_grund,
                )
            )

    return ParallelRunde(
        runde=runde,
        arbeiter=arbeiter,
        verteilung=verteilung,
        gesamt_wand_s=gesamt_wand_s,
        gates=(gate_laeufe[0], gate_laeufe[1]),
        summe_rss_spitze_mib=round(summe_rss_spitze, 1),
        ram_frei_min_mib=ram_frei_min,
        swap_vorher_mib=stand_vorher.swap_belegt_mib,
        swap_spitze_mib=swap_spitze,
        last_vorher=round(last_vorher, 2),
        last_spitze=round(last_spitze, 2),
        abgebrochen=abgebrochen,
        abbruch_grund=abbruch_grund,
    )


def _git_lauf(args: Sequence[str], *, cwd: Path | None = None) -> str:
    r = subprocess.run(
        ["git", "--no-optional-locks", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **prozessbaum.fensterlos(),
    )
    if r.returncode != 0:
        meldung = (r.stderr or r.stdout).strip()[:300]
        raise RuntimeError(f"git {' '.join(args[:3])} fehlgeschlagen (rc={r.returncode}): {meldung}")
    return r.stdout


def bereite_isolierte_kopien(
    quell_repo: Path,
    ziel_wurzel: Path,
    *,
    ohne_ordner: Sequence[str] = STANDARD_OHNE_ORDNER,
) -> tuple[Path, Path]:
    """Erstellt zwei getrennte, isolierte Sparse-Kopien eines Repos ohne das Quell-Repo zu ändern."""
    kopien: list[Path] = []
    muster = ["/*", *(f"!/{o.strip('/')}/" for o in ohne_ordner if o.strip("/"))]
    for idx in (1, 2):
        ziel = ziel_wurzel / f"gate_{idx}"
        _git_lauf(["clone", "--shared", "--no-tags", "--no-checkout", "-q", str(quell_repo), str(ziel)])
        if ohne_ordner:
            _git_lauf(["-C", str(ziel), "sparse-checkout", "init", "--no-cone"])
            _git_lauf(["-C", str(ziel), "sparse-checkout", "set", *muster])
        _git_lauf(["-C", str(ziel), "checkout", "-q", "HEAD"])
        kopien.append(ziel)
    return kopien[0], kopien[1]


def waehle_test_pfade(
    repo_dir: Path,
    *,
    anzahl: int = 0,
    vorwahl: Sequence[str] = (),
) -> tuple[str, ...]:
    """Wählt reproduzierbar Testdateien aus ``repo_dir/tests`` (gleichmäßig verteilt bei ``anzahl > 0``)."""
    if vorwahl:
        return tuple(p.replace("\\", "/") for p in vorwahl)
    if anzahl <= 0:
        return ()
    tests_dir = repo_dir / "tests"
    alle = sorted(p.relative_to(repo_dir).as_posix() for p in tests_dir.rglob("test_*.py"))
    if not alle:
        raise ValueError(f"Keine test_*.py unter {tests_dir.name} gefunden")
    if anzahl >= len(alle):
        return tuple(alle)
    schritt = len(alle) / anzahl
    return tuple(alle[int(i * schritt)] for i in range(anzahl))


def werte_bremse_aus(
    einzel_laeufe: Sequence[GateLauf],
    parallel_runden: Sequence[ParallelRunde],
    *,
    schwelle: float = SCHWELLE_ZWEITER_PLATZ,
) -> BremseAuswertung:
    """Berechnet Bremse-Faktor, RAM je Worker und Empfehlung gegen die 3b-6-Schwelle."""
    if not einzel_laeufe:
        raise ValueError("Keine Einzel-Gate-Läufe vorhanden")
    gueltige_einzel = [g for g in einzel_laeufe if not g.abgebrochen and g.dauer_s > 0]
    if not gueltige_einzel:
        raise ValueError("Keine gültigen (nicht abgebrochenen) Einzel-Gate-Läufe vorhanden")

    gueltige_parallel = [
        p for p in parallel_runden if not p.abgebrochen and all(not g.abgebrochen and g.dauer_s > 0 for g in p.gates)
    ]
    if not gueltige_parallel:
        raise ValueError("Keine gültigen (nicht abgebrochenen) Parallel-Runden vorhanden")

    einzel_median_s = float(statistics.median(g.dauer_s for g in gueltige_einzel))
    parallel_einzel_dauern = [g.dauer_s for p in gueltige_parallel for g in p.gates]
    parallel_gate_mittel_s = float(statistics.mean(parallel_einzel_dauern))
    parallel_gate_max_s = float(max(p.gesamt_wand_s for p in gueltige_parallel))

    bremse_faktor = round(parallel_gate_mittel_s / einzel_median_s, 3)
    bremse_faktor_max = round(parallel_gate_max_s / einzel_median_s, 3)
    arbeiter = gueltige_einzel[0].arbeiter
    verteilung = gueltige_einzel[0].verteilung

    einzel_rss_max = max(g.rss_spitze_mib for g in gueltige_einzel)
    ram_je_worker = round(einzel_rss_max / max(1, arbeiter), 1)
    summe_rss_parallel_max = max(p.summe_rss_spitze_mib for p in gueltige_parallel)
    ram_frei_min = min(
        min(g.ram_frei_min_mib for g in gueltige_einzel),
        min(p.ram_frei_min_mib for p in gueltige_parallel),
    )
    swap_delta_max = max(
        max(g.swap_delta_mib for g in gueltige_einzel),
        max(p.swap_delta_mib for p in gueltige_parallel),
    )
    last_spitze_max = max(
        max(g.last_spitze for g in gueltige_einzel),
        max(p.last_spitze for p in gueltige_parallel),
    )

    return BremseAuswertung(
        arbeiter=arbeiter,
        verteilung=verteilung,
        runden_einzel=len(gueltige_einzel),
        runden_parallel=len(gueltige_parallel),
        einzel_median_s=round(einzel_median_s, 2),
        parallel_gate_mittel_s=round(parallel_gate_mittel_s, 2),
        parallel_gate_max_s=round(parallel_gate_max_s, 2),
        bremse_faktor=bremse_faktor,
        bremse_faktor_max=bremse_faktor_max,
        schwelle_zweiter_platz=schwelle,
        zweiter_platz_empfohlen=bremse_faktor <= schwelle,
        einzel_rss_max_mib=round(einzel_rss_max, 1),
        ram_je_worker_mib=ram_je_worker,
        summe_rss_parallel_max_mib=round(summe_rss_parallel_max, 1),
        ram_frei_min_mib=ram_frei_min,
        swap_delta_max_mib=swap_delta_max,
        last_spitze_max=round(last_spitze_max, 2),
    )


def _komma(wert: float, stellen: int = 2) -> str:
    return f"{wert:.{stellen}f}".replace(".", ",")


def bericht_markdown(
    m12: M12AppStatus,
    ausw: BremseAuswertung,
    einzel_laeufe: Sequence[GateLauf],
    parallel_runden: Sequence[ParallelRunde],
    *,
    beschreibung: str = "",
) -> str:
    """Erzeugt einen datenschutzsauberen Markdown-Bericht (ohne Benutzerpfade/Personennamen)."""
    empfehlung = (
        f"JA — Bremse {_komma(ausw.bremse_faktor)}× <= {_komma(ausw.schwelle_zweiter_platz, 1)}× (2. Gate-Platz tragfähig)"
        if ausw.zweiter_platz_empfohlen
        else f"NEIN — Bremse {_komma(ausw.bremse_faktor)}× > {_komma(ausw.schwelle_zweiter_platz, 1)}× (kein 2. Gate-Platz)"
    )
    zeilen = [
        "# Etappe 3b-5 (#105): M12-Status im App-Repo und Bremse bei 2 gleichzeitigen Gates",
        "",
        "## 1. M12 im App-Repo (nach 3b-0-Entscheidung #98)",
        f"- **3b-0-Entscheidung:** `{m12.entscheidung_3b0}`",
        f"- **Urteil M12 App-Repo:** {m12.urteil}",
        f"- **Eingebaute Fehler im App-Repo:** {m12.eingebaute_fehler}",
        f"- **Begründung:** {m12.begruendung}",
        "",
        "## 2. Bremse bei 2 gleichzeitigen Gates (`-n 4`)",
    ]
    if beschreibung:
        zeilen.append(f"- **Messaufbau:** {beschreibung}")
    zeilen.extend(
        [
            "",
            "| Kennzahl | Wert |",
            "|---|---|",
            f"| Konfiguration | `-n {ausw.arbeiter} --dist {ausw.verteilung}` (`nice -n {STANDARD_NICE}`) |",
            f"| Einzel-Gate Median ({ausw.runden_einzel} Runden) | **{_komma(ausw.einzel_median_s)} s** |",
            f"| 2 Gates gleichzeitig, Gate-Mittel ({ausw.runden_parallel} Runden) | **{_komma(ausw.parallel_gate_mittel_s)} s** (Max-Wand {_komma(ausw.parallel_gate_max_s)} s) |",
            f"| **Bremse-Faktor (gemessen statt Annahme 3,0×)** | **{_komma(ausw.bremse_faktor)}×** (Spitze {_komma(ausw.bremse_faktor_max)}×) |",
            f"| **Entscheidung 2. Gate-Platz (Schwelle <= {_komma(ausw.schwelle_zweiter_platz, 1)}×)** | **{empfehlung}** |",
            f"| RAM-Spitze 1 Gate / je Worker | {_komma(ausw.einzel_rss_max_mib, 1)} MiB / ~{_komma(ausw.ram_je_worker_mib, 1)} MiB |",
            f"| RAM-Spitze 2 Gates gleichzeitig (Summe RSS) | {_komma(ausw.summe_rss_parallel_max_mib, 1)} MiB |",
            f"| RAM frei min / Swap-Delta max | {ausw.ram_frei_min_mib} MiB / {ausw.swap_delta_max_mib:+d} MiB |",
            f"| 1-Minuten-Last Spitze | {_komma(ausw.last_spitze_max)} |",
            "",
            "### Einzelläufe",
            "",
            "| Lauf | Modus | Dauer (s) | RSS Spitze (MiB) | RAM frei min (MiB) | Swap-Delta (MiB) | Last vorher→Spitze | Exit |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for g in einzel_laeufe:
        zeilen.append(
            f"| R{g.runde} G1 | einzel | {_komma(g.dauer_s)} | {_komma(g.rss_spitze_mib, 1)} | "
            f"{g.ram_frei_min_mib} | {g.swap_delta_mib:+d} | "
            f"{_komma(g.last_vorher)}→{_komma(g.last_spitze)} | {g.exit_code} |"
        )
    for p in parallel_runden:
        g1, g2 = p.gates
        zeilen.append(
            f"| R{p.runde} G1+G2 | parallel | {_komma(g1.dauer_s)} / {_komma(g2.dauer_s)} (Wand {_komma(p.gesamt_wand_s)}) | "
            f"{_komma(p.summe_rss_spitze_mib, 1)} ({_komma(g1.rss_spitze_mib, 1)}+{_komma(g2.rss_spitze_mib, 1)}) | "
            f"{p.ram_frei_min_mib} | {p.swap_delta_mib:+d} | "
            f"{_komma(p.last_vorher)}→{_komma(p.last_spitze)} | {g1.exit_code}/{g2.exit_code} |"
        )
    return "\n".join(zeilen)


def fahre_bremse_messung(
    *,
    repo: Path | None = None,
    python_exe: str = sys.executable,
    arbeiter: int = STANDARD_ARBEITER,
    verteilung: str = "worksteal",
    runden: int = 2,
    datei_anzahl: int = 60,
    test_pfade: Sequence[str] = (),
    max_start_last: float = MAX_START_LAST,
    abbruch_last: float = ABBRUCH_LAST,
    nice_wert: int = STANDARD_NICE,
    arbeits_basis: Path | None = None,
    mit_aufwaermen: bool = True,
) -> tuple[list[GateLauf], list[ParallelRunde], BremseAuswertung, str]:
    """Führt eine vollständige, isolierte Bremse-Messung (1 vs. 2 gleichzeitige Gates) durch."""
    basis_str = str(arbeits_basis) if arbeits_basis is not None else None
    if arbeits_basis is not None:
        arbeits_basis.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bremse_3b5_", dir=basis_str) as tmp_str:
        tmp_path = Path(tmp_str)
        if repo is not None:
            kopie1, kopie2 = bereite_isolierte_kopien(repo, tmp_path)
            gewaehlte = waehle_test_pfade(kopie1, anzahl=datei_anzahl, vorwahl=test_pfade)
            beschreibung = (
                f"Isolierte Sparse-Kopien des App-Repos ({len(gewaehlte) or 'alle'} Testdateien, "
                f"-n {arbeiter}, --dist {verteilung}, nice -n {nice_wert})"
            )
        else:
            kopie1 = tmp_path / "gate_1"
            kopie2 = tmp_path / "gate_2"
            erzeuge_probe_suite(kopie1)
            erzeuge_probe_suite(kopie2)
            kopie1 = kopie1 / "suite"
            kopie2 = kopie2 / "suite"
            gewaehlte = ()
            beschreibung = (
                f"Isolierte Probe-Suiten (je 24 Module / 288 Tests, -n {arbeiter}, "
                f"--dist {verteilung}, nice -n {nice_wert})"
            )

        if mit_aufwaermen:
            log.info("Aufwärmlauf (Dateisystem-/Import-Cache) …")
            _ = messe_einzel_gate(
                kopie1,
                python_exe=python_exe,
                arbeiter=arbeiter,
                verteilung=verteilung,
                runde=0,
                max_start_last=max_start_last,
                abbruch_last=abbruch_last,
                test_pfade=gewaehlte,
                nice_wert=nice_wert,
            )
            time.sleep(2.0)

        einzel_laeufe: list[GateLauf] = []
        parallel_runden: list[ParallelRunde] = []
        for r in range(1, runden + 1):
            log.info("Runde %d/%d: Einzel-Gate (-n %d, --dist %s) …", r, runden, arbeiter, verteilung)
            einzel = messe_einzel_gate(
                kopie1,
                python_exe=python_exe,
                arbeiter=arbeiter,
                verteilung=verteilung,
                runde=r,
                max_start_last=max_start_last,
                abbruch_last=abbruch_last,
                test_pfade=gewaehlte,
                nice_wert=nice_wert,
            )
            einzel_laeufe.append(einzel)
            log.info(
                "Einzel R%d: %.2f s, RSS=%.1f MiB, RAM-frei-min=%d MiB, Last=%.2f->%.2f, rc=%d",
                r,
                einzel.dauer_s,
                einzel.rss_spitze_mib,
                einzel.ram_frei_min_mib,
                einzel.last_vorher,
                einzel.last_spitze,
                einzel.exit_code,
            )
            time.sleep(2.0)

            log.info("Runde %d/%d: 2 Gates gleichzeitig (je -n %d, --dist %s) …", r, runden, arbeiter, verteilung)
            par = messe_zwei_gates_parallel(
                (kopie1, kopie2),
                python_exe=python_exe,
                arbeiter=arbeiter,
                verteilung=verteilung,
                runde=r,
                max_start_last=max_start_last,
                abbruch_last=abbruch_last,
                test_pfade=gewaehlte,
                nice_wert=nice_wert,
            )
            parallel_runden.append(par)
            log.info(
                "Parallel R%d: G1=%.2f s / G2=%.2f s (Wand %.2f s), Summe-RSS=%.1f MiB, Last=%.2f->%.2f, rc=%d/%d",
                r,
                par.gates[0].dauer_s,
                par.gates[1].dauer_s,
                par.gesamt_wand_s,
                par.summe_rss_spitze_mib,
                par.last_vorher,
                par.last_spitze,
                par.gates[0].exit_code,
                par.gates[1].exit_code,
            )
            time.sleep(2.0)

    ausw = werte_bremse_aus(einzel_laeufe, parallel_runden)
    return einzel_laeufe, parallel_runden, ausw, beschreibung


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Etappe 3b-5 (#105): M12-Status im App-Repo und Bremse bei 2 gleichzeitigen Gates (-n 4)."
    )
    parser.add_argument("--repo", type=Path, default=None, help="Quell-Klon eines App-Repos (nur lesend geklont)")
    parser.add_argument("--python", default=sys.executable, help="Python-Interpreter mit pytest + pytest-xdist")
    parser.add_argument("--arbeiter", type=int, default=STANDARD_ARBEITER, help="xdist-Worker je Gate (Standard: 4)")
    parser.add_argument(
        "--dist",
        choices=("worksteal", "loadfile"),
        default="worksteal",
        help="xdist-Verteilung (Standard: worksteal)",
    )
    parser.add_argument("--runden", type=int, default=2, help="Anzahl abwechselnder Messrunden (Standard: 2)")
    parser.add_argument(
        "--datei-anzahl",
        type=int,
        default=60,
        help="Anzahl gleichmäßig gewählter Testdateien im App-Repo (0 = alle)",
    )
    parser.add_argument("--max-last", type=float, default=MAX_START_LAST, help="Start-Sperre 1-Minuten-Last")
    parser.add_argument("--abbruch-last", type=float, default=ABBRUCH_LAST, help="Live-Abbruch 1-Minuten-Last")
    parser.add_argument("--nice", type=int, default=STANDARD_NICE, help="nice-Wert auf Linux (Standard: 15)")
    parser.add_argument("--arbeits-basis", type=Path, default=None, help="Basis-Ordner für temporäre Arbeitskopien")
    parser.add_argument("--ohne-aufwaermen", action="store_true", help="Keinen Aufwärmlauf vor Runde 1 fahren")
    parser.add_argument("--json-aus", type=Path, default=None, help="Optionale lokale JSON-Ausgabe (nicht committen)")
    args = parser.parse_args(argv)

    m12 = bewerte_m12_app_repo(
        entscheidung_3b0="variante_3",
        r1_r3_quote=179 / 302,
        ungefangene_fehlgriffe=("1ed5b52", "61e877f", "0cba2e3"),
    )
    einzel, parallel, ausw, beschreibung = fahre_bremse_messung(
        repo=args.repo,
        python_exe=args.python,
        arbeiter=args.arbeiter,
        verteilung=args.dist,
        runden=args.runden,
        datei_anzahl=args.datei_anzahl,
        max_start_last=args.max_last,
        abbruch_last=args.abbruch_last,
        nice_wert=args.nice,
        arbeits_basis=args.arbeits_basis,
        mit_aufwaermen=not args.ohne_aufwaermen,
    )

    if args.json_aus is not None:
        args.json_aus.parent.mkdir(parents=True, exist_ok=True)
        daten: dict[str, Any] = {
            "m12": asdict(m12),
            "auswertung": asdict(ausw),
            "einzel": [asdict(g) for g in einzel],
            "parallel": [asdict(p) for p in parallel],
            "beschreibung": beschreibung,
        }
        args.json_aus.write_text(json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    sys.stdout.write(bericht_markdown(m12, ausw, einzel, parallel, beschreibung=beschreibung) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
