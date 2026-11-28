"""Kontrollierte Last- und Prüfzeit-Messreihe auf dem Linux-Bau-Server (#290, L7-Vorstufe).

Misst Dauer und Speicher-Spitze (Prozessbaum-RSS, ``MemAvailable``, Swap, 1-Minuten-Last)
eines reproduzierbaren pytest-Prüflaufs bei 0, 1, 2, 3 gleichzeitigen Bau-Sessions (Last-Nachbildung)
und verschiedenen Arbeiter-Zahlen (``-n 1`` seriell bis ``-n 8`` mit ``--dist loadfile`` sowie
Vergleich mit ``--dist worksteal``).

Sicherheit:
- Prüft vor jedem Messpunkt die 1-Minuten-Last (Abbruch bei Last >= 12,0).
- Fasst keine fremden Prozesse an; räumt eigene Last-Prozesse und Temp-Ordner garantiert auf.
- Nutzt bestehende Hilfsfunktionen aus ``to_spawn.speicher`` und ``to_spawn.starter``.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_WURZEL = Path(__file__).resolve().parents[1]
if str(REPO_WURZEL) not in sys.path:
    sys.path.insert(0, str(REPO_WURZEL))

from to_spawn import speicher, starter

log = logging.getLogger("messung.pruefzeit_last_290")

#: Harte Last-Obergrenze für Messläufe auf dem Bau-Server (#290).
MAX_LAST_STANDARD: float = 12.0
#: Realistische Arbeitsspeicher-Belegung je nachgebildeter Bau-Session (MiB).
#: Abgeleitet aus Live-Messung laufender Claude-Sessions + context-mode auf dem Bau-Server.
BAU_SESSION_RAM_MIB: int = 450
#: Abtast-Intervall für /proc-Speicher- und Last-Messung während eines Prüflaufs (Sekunden).
ABTAST_INTERVALL_S: float = 0.1
#: Standard-Matrix für die kontrollierte Messreihe (#290).
STANDARD_SESSIONS: tuple[int, ...] = (0, 1, 2, 3)
STANDARD_ARBEITER: tuple[int, ...] = (1, 2, 4, 6, 8)


class LastZuHochFehler(RuntimeError):
    """Wird geworfen, wenn die 1-Minuten-Last des Rechners die Messgrenze erreicht."""


@dataclass(frozen=True)
class SpeicherStand:
    """Momentaufnahme von RAM und Swap in MiB aus ``/proc/meminfo``."""

    gesamt_mib: int
    frei_mib: int
    belegt_mib: int
    swap_gesamt_mib: int
    swap_belegt_mib: int


@dataclass(frozen=True)
class Messpunkt:
    """Messwerte eines einzelnen kontrollierten Prüflaufs."""

    sessions: int
    arbeiter: int
    verteilung: str
    dauer_s: float
    pruef_rss_spitze_mib: float
    bau_rss_spitze_mib: float
    ram_frei_min_mib: int
    swap_vorher_mib: int
    swap_spitze_mib: int
    last_vorher: float
    last_spitze: float
    exit_code: int

    @property
    def swap_delta_mib(self) -> int:
        return self.swap_spitze_mib - self.swap_vorher_mib


@dataclass(frozen=True)
class SchwellenVorschlag:
    """Abgeleiteter Schwellen-Vorschlag für L7 (nur Vorschlag, noch kein Regel-Bau)."""

    arbeiter_ruhig: int
    arbeiter_unter_last: int
    max_sessions_gleichzeitig: int
    ram_reserve_mib: int
    last_drossel_ab: float
    last_pause_ab: float
    begruendung: tuple[str, ...]


def pruefe_last_grenze(
    *,
    max_last: float = MAX_LAST_STANDARD,
    loadavg: Path | None = None,
) -> float:
    """Liest die 1-Minuten-Last über ``to_spawn.speicher.last_1m`` und sperrt ab ``max_last``."""
    pfad = loadavg if loadavg is not None else Path("/proc/loadavg")
    wert = speicher.last_1m(pfad)
    if wert is None:
        raise LastZuHochFehler(f"1-Minuten-Last nicht lesbar ({pfad})")
    if wert >= max_last:
        raise LastZuHochFehler(f"1-Minuten-Last {wert:.2f} >= Grenze {max_last:.2f} — Messlauf abgebrochen")
    return wert


def _meminfo_werte_kib(meminfo: Path) -> dict[str, int]:
    """Liest ausgewählte ``<Schlüssel>: <Wert> kB``-Felder aus ``/proc/meminfo``."""
    werte: dict[str, int] = {}
    try:
        zeilen = meminfo.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as fehler:
        log.warning("meminfo %s nicht lesbar: %s", meminfo, fehler)
        return werte
    for zeile in zeilen:
        teile = zeile.split()
        if len(teile) >= 2 and teile[0].endswith(":"):
            schluessel = teile[0][:-1]
            try:
                werte[schluessel] = int(teile[1])
            except ValueError:
                continue
    return werte


def speicher_stand(*, meminfo: Path | None = None) -> SpeicherStand:
    """Liest RAM- und Swap-Stand in MiB unter Nutzung von ``to_spawn.speicher``."""
    pfad = meminfo if meminfo is not None else Path("/proc/meminfo")
    gesamt_kib = speicher.gesamt_kib(pfad) or 0
    frei_mib = speicher.frei_mib(pfad) or 0
    gesamt_mib = gesamt_kib // 1024
    belegt_mib = max(0, gesamt_mib - frei_mib)
    felder = _meminfo_werte_kib(pfad)
    swap_gesamt_mib = felder.get("SwapTotal", 0) // 1024
    swap_frei_mib = felder.get("SwapFree", 0) // 1024
    swap_belegt_mib = max(0, swap_gesamt_mib - swap_frei_mib)
    return SpeicherStand(
        gesamt_mib=gesamt_mib,
        frei_mib=frei_mib,
        belegt_mib=belegt_mib,
        swap_gesamt_mib=swap_gesamt_mib,
        swap_belegt_mib=swap_belegt_mib,
    )


def prozessbaum_rss_mib(wurzel_pid: int, *, proc_dir: Path | None = None) -> float:
    """Summiert ``VmRSS`` (MiB) des Prozesses ``wurzel_pid`` und aller direkten/indirekten Kinder."""
    basis = proc_dir if proc_dir is not None else Path("/proc")
    kinder: dict[int, list[int]] = {}
    rss_kib_je_pid: dict[int, int] = {}

    try:
        eintraege = list(basis.iterdir())
    except OSError:
        return 0.0

    for eintrag in eintraege:
        if not eintrag.name.isdigit():
            continue
        pid = int(eintrag.name)
        status_datei = eintrag / "status"
        try:
            inhalt = status_datei.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ppid: int | None = None
        rss_kib = 0
        for zeile in inhalt.splitlines():
            if zeile.startswith("PPid:"):
                teile = zeile.split()
                if len(teile) >= 2 and teile[1].isdigit():
                    ppid = int(teile[1])
            elif zeile.startswith("VmRSS:"):
                teile = zeile.split()
                if len(teile) >= 2 and teile[1].isdigit():
                    rss_kib = int(teile[1])
        rss_kib_je_pid[pid] = rss_kib
        if ppid is not None:
            kinder.setdefault(ppid, []).append(pid)

    if wurzel_pid not in rss_kib_je_pid:
        return 0.0

    besucht: set[int] = set()
    stapel: list[int] = [wurzel_pid]
    gesamt_kib = 0
    while stapel:
        aktuell = stapel.pop()
        if aktuell in besucht:
            continue
        besucht.add(aktuell)
        gesamt_kib += rss_kib_je_pid.get(aktuell, 0)
        stapel.extend(kinder.get(aktuell, ()))
    return round(gesamt_kib / 1024.0, 1)


def erzeuge_probe_suite(
    ziel_dir: Path,
    *,
    dateien: int = 24,
    tests_je_datei: int = 12,
    nachzuegler: int = 2,
    speicher_puffer_mib: int = 48,
    arbeit_runden: int = 36000,
) -> Path:
    """Erzeugt eine realistische, isolierte pytest-Probe-Suite mit schiefer Dateilaufzeit.

    Jede Testdatei hält eine modulweite Fixture (SQLite + Puffer) und führt CPU-, JSON- und
    SQLite-Prüfungen aus. Die ersten ``nachzuegler`` Dateien laufen deutlich länger, um die
    Wirkung von ``--dist loadfile`` gegenüber ``--dist worksteal`` real abzubilden.
    """
    suite_dir = ziel_dir / "suite"
    tests_dir = suite_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (suite_dir / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")

    conftest_code = textwrap.dedent(
        f"""\
        from __future__ import annotations

        import hashlib
        import json
        import sqlite3
        import pytest


        @pytest.fixture(scope="module")
        def modul_zustand():
            # Realistischer Arbeits-Speicher je Worker (SQLite + strukturierter Datenpuffer)
            puffer = bytearray({speicher_puffer_mib} * 1024 * 1024)
            for pos in range(0, len(puffer), 4096):
                puffer[pos] = 1
            conn = sqlite3.connect(":memory:")
            conn.execute("CREATE TABLE eintraege (id INTEGER PRIMARY KEY, schluessel TEXT, nutzlast TEXT)")
            conn.executemany(
                "INSERT INTO eintraege (schluessel, nutzlast) VALUES (?, ?)",
                [(f"k-{{i}}", json.dumps({{"nr": i, "wert": i * 3}})) for i in range(400)],
            )
            conn.commit()
            try:
                yield {{"conn": conn, "puffer_len": len(puffer), "puffer": puffer}}
            finally:
                conn.close()


        def arbeit_schritt(conn: sqlite3.Connection, kennung: int, runden: int) -> str:
            digest = f"seed-{{kennung}}".encode("utf-8")
            for r in range(runden):
                digest = hashlib.sha256(digest + str(r).encode("ascii")).digest()
            rows = conn.execute(
                "SELECT nutzlast FROM eintraege WHERE id BETWEEN ? AND ?",
                ((kennung % 300) + 1, (kennung % 300) + 40),
            ).fetchall()
            geparst = [json.loads(z[0])["wert"] for z in rows]
            assert len(geparst) == 40
            return digest.hex()
        """
    )
    (tests_dir / "conftest.py").write_text(conftest_code, encoding="utf-8")

    for idx in range(1, dateien + 1):
        ist_nachzuegler = idx <= nachzuegler
        runden = arbeit_runden * 4 if ist_nachzuegler else arbeit_runden
        zeilen = [
            f'"""Synthetisches Probe-Modul {idx:02d} ({"Nachzügler" if ist_nachzuegler else "Standard"})."""',
            "",
            "from __future__ import annotations",
            "",
            "from conftest import arbeit_schritt",
            "",
        ]
        for nr in range(1, tests_je_datei + 1):
            zeilen.extend(
                [
                    f"def test_fall_{nr:02d}(modul_zustand) -> None:",
                    "    assert modul_zustand['puffer_len'] > 0",
                    f"    ergebnis = arbeit_schritt(modul_zustand['conn'], {idx * 100 + nr}, {runden})",
                    "    assert len(ergebnis) == 64",
                    "",
                ]
            )
        (tests_dir / f"test_mod_{idx:02d}.py").write_text("\n".join(zeilen), encoding="utf-8")
    return tests_dir


_LAST_ARBEITER_CODE = textwrap.dedent(
    """\
    import ast
    import hashlib
    import json
    import sys
    import tempfile
    import time
    from pathlib import Path

    ram_mib = int(sys.argv[1])
    bereit_pfad = Path(sys.argv[2])

    # Speicher-Profil einer laufenden Bau-Session anlegen und aktiv anfassen (echtes RSS)
    bloecke = []
    for _ in range(ram_mib):
        b = bytearray(1024 * 1024)
        for off in range(0, len(b), 4096):
            b[off] = 7
        bloecke.append(b)

    bereit_pfad.write_text("ok", encoding="utf-8")

    muster_code = "def f(x):\\n    return [i * i for i in range(x)]\\n" * 20
    with tempfile.TemporaryDirectory(prefix="bau_last_290_") as tmp:
        probe = Path(tmp) / "stand.json"
        runde = 0
        while True:
            runde += 1
            ast.parse(muster_code)
            daten = {"runde": runde, "werte": [hashlib.sha256(f"{runde}-{i}".encode()).hexdigest() for i in range(80)]}
            probe.write_text(json.dumps(daten), encoding="utf-8")
            json.loads(probe.read_text(encoding="utf-8"))
            # Minimale Pause spiegelt aktive Bau-Phase (Test/Lint/Helfer, ~90 % Kern-Auslastung je Session)
            time.sleep(0.001)
            # Mehrere Seiten periodisch berühren, damit das Working Set aktiv im RAM bleibt
            for k in range(8):
                bloecke[(runde + k) % len(bloecke)][0] = runde % 251
    """
)


def _starte_bau_last_sessions(
    anzahl: int,
    *,
    python_exe: str,
    ram_je_session_mib: int,
    hilfs_dir: Path,
) -> list[subprocess.Popen[bytes]]:
    """Startet ``anzahl`` isolierte Bau-Session-Lastprozesse und wartet auf deren RSS-Bereitschaft."""
    prozesse: list[subprocess.Popen[bytes]] = []
    bereit_dateien: list[Path] = []
    try:
        for idx in range(anzahl):
            bereit = hilfs_dir / f"bau_bereit_{idx}.flag"
            if bereit.exists():
                bereit.unlink()
            bereit_dateien.append(bereit)
            proc = starter.starte(
                [python_exe, "-c", _LAST_ARBEITER_CODE, str(ram_je_session_mib), str(bereit)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                notnagel=None,
            )
            prozesse.append(proc)

        frist = time.monotonic() + 30.0
        while time.monotonic() < frist:
            if all(p.exists() for p in bereit_dateien):
                return prozesse
            if any(proc.poll() is not None for proc in prozesse):
                raise RuntimeError("Ein Bau-Lastprozess hat sich unerwartet vorzeitig beendet")
            time.sleep(0.1)
        raise TimeoutError("Bau-Lastprozesse wurden innerhalb von 30 s nicht bereit")
    except Exception:
        _beende_prozesse(prozesse)
        raise


def _beende_prozesse(prozesse: Sequence[subprocess.Popen[bytes]]) -> None:
    """Beendet alle gestarteten Hilfsprozesse sauber und wartet auf ihr Ende."""
    for proc in prozesse:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                continue
    for proc in prozesse:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except OSError:
                continue


def _warte_unter_last(max_last: float, *, max_warte_s: float = 30.0) -> float:
    """Prüft die Last-Grenze; wartet bei kurzzeitiger Spitze kurz ab, bricht sonst mit Fehler ab."""
    ende = time.monotonic() + max_warte_s
    while True:
        try:
            return pruefe_last_grenze(max_last=max_last)
        except LastZuHochFehler:
            if time.monotonic() >= ende:
                raise
            time.sleep(3.0)


def messe_einzellauf(
    suite_dir: Path,
    *,
    python_exe: str,
    sessions: int,
    arbeiter: int,
    verteilung: str,
    bau_prozesse: Sequence[subprocess.Popen[bytes]],
    max_last: float = MAX_LAST_STANDARD,
) -> Messpunkt:
    """Führt genau einen pytest-Prüflauf aus und misst Wandzeit, RSS-Spitze, RAM, Swap und Last."""
    last_vorher = _warte_unter_last(max_last)
    stand_vorher = speicher_stand()

    befehl = [python_exe, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if arbeiter > 1:
        befehl.extend(["-n", str(arbeiter), "--dist", verteilung])

    pruef_rss_spitze = 0.0
    bau_rss_spitze = sum(prozessbaum_rss_mib(p.pid) for p in bau_prozesse)
    ram_frei_min = stand_vorher.frei_mib
    swap_spitze = stand_vorher.swap_belegt_mib
    last_spitze = last_vorher

    start = time.monotonic()
    proc = starter.starte(
        befehl,
        cwd=suite_dir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        notnagel=None,
    )
    try:
        while proc.poll() is None:
            pruef_rss_spitze = max(pruef_rss_spitze, prozessbaum_rss_mib(proc.pid))
            if bau_prozesse:
                bau_rss = sum(prozessbaum_rss_mib(p.pid) for p in bau_prozesse)
                bau_rss_spitze = max(bau_rss_spitze, bau_rss)
            stand = speicher_stand()
            ram_frei_min = min(ram_frei_min, stand.frei_mib)
            swap_spitze = max(swap_spitze, stand.swap_belegt_mib)
            aktuelle_last = speicher.last_1m()
            if aktuelle_last is not None:
                last_spitze = max(last_spitze, aktuelle_last)
            time.sleep(ABTAST_INTERVALL_S)
    finally:
        if proc.poll() is None:
            _beende_prozesse([proc])

    dauer_s = round(time.monotonic() - start, 2)
    return Messpunkt(
        sessions=sessions,
        arbeiter=arbeiter,
        verteilung="seriell" if arbeiter == 1 else verteilung,
        dauer_s=dauer_s,
        pruef_rss_spitze_mib=round(pruef_rss_spitze, 1),
        bau_rss_spitze_mib=round(bau_rss_spitze, 1),
        ram_frei_min_mib=ram_frei_min,
        swap_vorher_mib=stand_vorher.swap_belegt_mib,
        swap_spitze_mib=swap_spitze,
        last_vorher=round(last_vorher, 2),
        last_spitze=round(last_spitze, 2),
        exit_code=int(proc.returncode or 0),
    )


def fahre_messreihe(
    *,
    python_exe: str = sys.executable,
    sessions_liste: Sequence[int] = STANDARD_SESSIONS,
    arbeiter_liste: Sequence[int] = STANDARD_ARBEITER,
    ram_je_session_mib: int = BAU_SESSION_RAM_MIB,
    max_last: float = MAX_LAST_STANDARD,
    mit_worksteal_vergleich: bool = True,
) -> list[Messpunkt]:
    """Fährt die kontrollierte Messmatrix (0..3 Sessions × Arbeiter-Zahlen) komplett auf Linux."""
    ergebnisse: list[Messpunkt] = []
    with tempfile.TemporaryDirectory(prefix="pruefzeit_290_") as tmp:
        tmp_path = Path(tmp)
        erzeuge_probe_suite(tmp_path)
        suite_dir = tmp_path / "suite"
        hilfs_dir = tmp_path / "hilfs"
        hilfs_dir.mkdir(parents=True, exist_ok=True)

        # Einmaliger kurzer Aufwärmlauf (primed Python-/pytest-Imports im Dateisystem-Cache)
        _ = messe_einzellauf(
            suite_dir,
            python_exe=python_exe,
            sessions=0,
            arbeiter=2,
            verteilung="loadfile",
            bau_prozesse=(),
            max_last=max_last,
        )
        time.sleep(1.5)

        for sessions in sessions_liste:
            bau_prozesse: list[subprocess.Popen[bytes]] = []
            try:
                if sessions > 0:
                    bau_prozesse = _starte_bau_last_sessions(
                        sessions,
                        python_exe=python_exe,
                        ram_je_session_mib=ram_je_session_mib,
                        hilfs_dir=hilfs_dir,
                    )
                    time.sleep(1.0)
                for arbeiter in arbeiter_liste:
                    punkt = messe_einzellauf(
                        suite_dir,
                        python_exe=python_exe,
                        sessions=sessions,
                        arbeiter=arbeiter,
                        verteilung="loadfile",
                        bau_prozesse=bau_prozesse,
                        max_last=max_last,
                    )
                    log.info(
                        "Messpunkt S=%d W=%d (%s): %.2f s, Prüf-RSS=%.1f MiB, RAM-frei-min=%d MiB, Last=%.2f->%.2f",
                        punkt.sessions,
                        punkt.arbeiter,
                        punkt.verteilung,
                        punkt.dauer_s,
                        punkt.pruef_rss_spitze_mib,
                        punkt.ram_frei_min_mib,
                        punkt.last_vorher,
                        punkt.last_spitze,
                    )
                    ergebnisse.append(punkt)
                    time.sleep(1.0)

                if mit_worksteal_vergleich and sessions in (0, 3):
                    for arbeiter in (4, 8):
                        if arbeiter in arbeiter_liste:
                            punkt = messe_einzellauf(
                                suite_dir,
                                python_exe=python_exe,
                                sessions=sessions,
                                arbeiter=arbeiter,
                                verteilung="worksteal",
                                bau_prozesse=bau_prozesse,
                                max_last=max_last,
                            )
                            ergebnisse.append(punkt)
                            time.sleep(1.0)
            finally:
                _beende_prozesse(bau_prozesse)
                time.sleep(2.0)

    return ergebnisse


def tabelle_markdown(punkte: Sequence[Messpunkt]) -> str:
    """Formatiert Messpunkte als Markdown-Tabelle (Dauer, Faktor, RSS-Spitze, RAM, Swap, Last)."""
    basis_je_arbeiter: dict[tuple[int, str], float] = {
        (p.arbeiter, p.verteilung): p.dauer_s for p in punkte if p.sessions == 0
    }
    zeilen = [
        (
            "| Bau-Sessions | Arbeiter | Verteilung | Dauer (s) | Faktor vs. 0 Sess. | Prüf-RSS Spitze (MiB) | "
            "Bau-RSS (MiB) | RAM frei min (MiB) | Swap vorher→Spitze (MiB) | Last vorher→Spitze | Exit |"
        ),
        "|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for p in punkte:
        basis = basis_je_arbeiter.get((p.arbeiter, p.verteilung))
        faktor = f"{p.dauer_s / basis:.2f}×" if basis and basis > 0 else "–"
        zeilen.append(
            f"| {p.sessions} | {p.arbeiter} | `{p.verteilung}` | {p.dauer_s:.2f} | {faktor} | "
            f"{p.pruef_rss_spitze_mib:.1f} | {p.bau_rss_spitze_mib:.1f} | {p.ram_frei_min_mib} | "
            f"{p.swap_vorher_mib}→{p.swap_spitze_mib} ({p.swap_delta_mib:+d}) | "
            f"{p.last_vorher:.2f}→{p.last_spitze:.2f} | {p.exit_code} |"
        )
    return "\n".join(zeilen)


def schwellen_vorschlag(
    punkte: Sequence[Messpunkt],
    *,
    kerne: int,
    ram_gesamt_mib: int,
) -> SchwellenVorschlag:
    """Leitet einen konservativen L7-Schwellen-Vorschlag aus den Messdaten ab (nur Vorschlag)."""
    loadfile_0 = [p for p in punkte if p.sessions == 0 and p.verteilung in ("seriell", "loadfile")]
    loadfile_3 = [p for p in punkte if p.sessions == 3 and p.verteilung in ("seriell", "loadfile")]

    best_ruhig = min(loadfile_0, key=lambda p: p.dauer_s).arbeiter if loadfile_0 else min(kerne, 6)
    # Unter Last: Arbeiter-Zahl so wählen, dass noch Kerne für aktive Bau-Sessions bleiben
    arbeiter_ruhig = max(2, min(kerne, best_ruhig))
    if loadfile_3:
        best_last = min(loadfile_3, key=lambda p: p.dauer_s).arbeiter
        arbeiter_unter_last = max(2, min(arbeiter_ruhig, best_last, max(2, kerne - 4)))
    else:
        arbeiter_unter_last = max(2, min(arbeiter_ruhig, kerne // 2))

    # Konservativer RAM-Ansatz: mind. 25 % bzw. 3 GiB Reserve bei 16-GiB-Server
    ram_reserve_mib = max(2048, min(4096, ram_gesamt_mib // 4))
    max_sessions = 3 if ram_gesamt_mib >= 15000 and kerne >= 8 else max(1, kerne // 4)
    return SchwellenVorschlag(
        arbeiter_ruhig=arbeiter_ruhig,
        arbeiter_unter_last=arbeiter_unter_last,
        max_sessions_gleichzeitig=max_sessions,
        ram_reserve_mib=ram_reserve_mib,
        last_drossel_ab=round(kerne * 0.75, 1),
        last_pause_ab=round(kerne * 1.25, 1),
        begruendung=(
            f"Bei {kerne} Kernen und {ram_gesamt_mib} MiB RAM liefert n={arbeiter_ruhig} ohne Last die kürzeste Dauer.",
            f"Neben aktiven Bau-Sessions schützt n={arbeiter_unter_last} vor Überbuchung (Last < {kerne * 1.25:.1f}) und Swap-Druck.",
            f"Höchstens {max_sessions} aktiv bauende Tickets gleichzeitig halten bei mind. {ram_reserve_mib} MiB freiem RAM Luft für das Gate.",
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Kontrollierte Prüfzeit-/Last-Messreihe auf dem Bau-Server (#290).")
    parser.add_argument("--python", default=sys.executable, help="Python-Interpreter mit pytest + xdist")
    parser.add_argument("--max-last", type=float, default=MAX_LAST_STANDARD, help="Abbruch-Schwelle 1-Minuten-Last")
    parser.add_argument("--ram-je-session", type=int, default=BAU_SESSION_RAM_MIB, help="RAM je Bau-Session (MiB)")
    parser.add_argument("--json-aus", type=Path, default=None, help="Optionale lokale JSON-Ausgabe (nicht committen)")
    args = parser.parse_args(argv)

    punkte = fahre_messreihe(
        python_exe=args.python,
        ram_je_session_mib=args.ram_je_session,
        max_last=args.max_last,
    )
    if args.json_aus is not None:
        args.json_aus.parent.mkdir(parents=True, exist_ok=True)
        args.json_aus.write_text(
            json.dumps([asdict(p) for p in punkte], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    sys.stdout.write(tabelle_markdown(punkte) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
