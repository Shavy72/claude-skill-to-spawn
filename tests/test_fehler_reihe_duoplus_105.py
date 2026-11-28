"""Rot-/Grün-Tests für Paket 3b-5 (#105): M12-Status im App-Repo und Bremse bei 2 Gates (-n 4).

Prüft:
- ehrliche M12-Status-Bewertung je nach 3b-0-Entscheidung (bei Variante 3:
  „nicht anwendbar, weil keine Auswahl läuft“, Verweis auf Probe-Nachweis #104),
- Auswertung der Bremse-Messung (1 Gate vs. 2 gleichzeitige Gates je ``-n 4``,
  Faktor-Berechnung, Schwelle ``<= 1,5`` für 2. Gate-Platz in 3b-6, RAM je Worker),
- Last-Wächter (Sperre ab Last >= 12 vor Start, Live-Abbruch ab Last >= 14),
- Befehlsbau (``-n 4``, ``--dist``, ``--basetemp``, ``nice -n 15`` nur auf Linux),
- Isolierte Parallel-Ausführung und Aufräumen über ``to_spawn.starter.starte``
  sowie Datenschutz im Markdown-Bericht (keine Benutzerpfade).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

WURZEL = Path(__file__).resolve().parents[1]
if str(WURZEL) not in sys.path:
    sys.path.insert(0, str(WURZEL))


def test_m12_status_variante_3_nicht_anwendbar() -> None:
    """Bei 3b-0-Entscheidung Variante 3 meldet 3b-5 ehrlich 'nicht anwendbar, weil keine Auswahl läuft'."""
    from messung import fehler_reihe_duoplus as m105

    status = m105.bewerte_m12_app_repo(
        entscheidung_3b0="variante_3",
        r1_r3_quote=0.593,
        ungefangene_fehlgriffe=("1ed5b52", "61e877f", "0cba2e3"),
    )
    assert status.anwendbar is False
    assert status.eingebaute_fehler == 0
    assert "nicht anwendbar, weil keine Auswahl läuft" in status.urteil
    assert status.pflicht_nachweis_probe_issue == 104
    assert "regeln@fd7e49f" in status.begruendung

    kombi = m105.bewerte_m12_app_repo(
        entscheidung_3b0="kombination",
        r1_r3_quote=0.25,
        ungefangene_fehlgriffe=(),
    )
    assert kombi.anwendbar is True
    assert kombi.eingebaute_fehler >= 20


def test_bremse_auswertung_faktor_und_schwelle_1_5() -> None:
    """Bremse-Faktor = Parallel-Gate-Dauer / Einzel-Gate-Dauer; Schwelle 1,5 entscheidet 2. Gate-Platz."""
    from messung import fehler_reihe_duoplus as m105

    einzel = [
        m105.GateLauf(
            modus="einzel",
            runde=1,
            gate_nr=1,
            arbeiter=4,
            verteilung="worksteal",
            dauer_s=100.0,
            rss_spitze_mib=1600.0,
            ram_frei_min_mib=6000,
            swap_vorher_mib=1000,
            swap_spitze_mib=1020,
            last_vorher=1.2,
            last_spitze=4.5,
            exit_code=0,
        )
    ]
    parallel_schnell = [
        m105.ParallelRunde(
            runde=1,
            arbeiter=4,
            verteilung="worksteal",
            gesamt_wand_s=138.0,
            gates=(
                m105.GateLauf(
                    modus="parallel",
                    runde=1,
                    gate_nr=1,
                    arbeiter=4,
                    verteilung="worksteal",
                    dauer_s=130.0,
                    rss_spitze_mib=1620.0,
                    ram_frei_min_mib=4200,
                    swap_vorher_mib=1000,
                    swap_spitze_mib=1050,
                    last_vorher=1.5,
                    last_spitze=8.4,
                    exit_code=0,
                ),
                m105.GateLauf(
                    modus="parallel",
                    runde=1,
                    gate_nr=2,
                    arbeiter=4,
                    verteilung="worksteal",
                    dauer_s=138.0,
                    rss_spitze_mib=1580.0,
                    ram_frei_min_mib=4200,
                    swap_vorher_mib=1000,
                    swap_spitze_mib=1050,
                    last_vorher=1.5,
                    last_spitze=8.4,
                    exit_code=0,
                ),
            ),
            summe_rss_spitze_mib=3200.0,
            ram_frei_min_mib=4200,
            swap_vorher_mib=1000,
            swap_spitze_mib=1050,
            last_vorher=1.5,
            last_spitze=8.4,
        )
    ]
    ausw = m105.werte_bremse_aus(einzel, parallel_schnell)
    assert ausw.einzel_median_s == pytest.approx(100.0)
    assert ausw.parallel_gate_mittel_s == pytest.approx(134.0)
    assert ausw.bremse_faktor == pytest.approx(1.34)
    assert ausw.bremse_faktor_max == pytest.approx(1.38)
    assert ausw.zweiter_platz_empfohlen is True
    assert ausw.ram_je_worker_mib == pytest.approx(400.0)
    assert ausw.summe_rss_parallel_max_mib == pytest.approx(3200.0)

    # Gegenprobe: Bremse > 1,5 → kein zweiter Gate-Platz empfohlen
    parallel_langsam = [
        m105.ParallelRunde(
            runde=1,
            arbeiter=4,
            verteilung="worksteal",
            gesamt_wand_s=185.0,
            gates=(
                m105.GateLauf(
                    modus="parallel",
                    runde=1,
                    gate_nr=1,
                    arbeiter=4,
                    verteilung="worksteal",
                    dauer_s=175.0,
                    rss_spitze_mib=1700.0,
                    ram_frei_min_mib=2800,
                    swap_vorher_mib=1000,
                    swap_spitze_mib=1400,
                    last_vorher=2.0,
                    last_spitze=11.1,
                    exit_code=0,
                ),
                m105.GateLauf(
                    modus="parallel",
                    runde=1,
                    gate_nr=2,
                    arbeiter=4,
                    verteilung="worksteal",
                    dauer_s=185.0,
                    rss_spitze_mib=1720.0,
                    ram_frei_min_mib=2800,
                    swap_vorher_mib=1000,
                    swap_spitze_mib=1400,
                    last_vorher=2.0,
                    last_spitze=11.1,
                    exit_code=0,
                ),
            ),
            summe_rss_spitze_mib=3420.0,
            ram_frei_min_mib=2800,
            swap_vorher_mib=1000,
            swap_spitze_mib=1400,
            last_vorher=2.0,
            last_spitze=11.1,
        )
    ]
    ausw_langsam = m105.werte_bremse_aus(einzel, parallel_langsam)
    assert ausw_langsam.bremse_faktor == pytest.approx(1.80)
    assert ausw_langsam.zweiter_platz_empfohlen is False


def test_bremse_auswertung_fehlerhaertung_und_abbruch() -> None:
    """Leere Listen, abgebrochene Läufe und nicht-positive Zeiten werden sauber abgewiesen."""
    from messung import fehler_reihe_duoplus as m105

    with pytest.raises(ValueError, match="Einzel"):
        m105.werte_bremse_aus([], [])

    abgebrochen = m105.GateLauf(
        modus="einzel",
        runde=1,
        gate_nr=1,
        arbeiter=4,
        verteilung="worksteal",
        dauer_s=0.0,
        rss_spitze_mib=0.0,
        ram_frei_min_mib=4000,
        swap_vorher_mib=0,
        swap_spitze_mib=0,
        last_vorher=11.0,
        last_spitze=14.8,
        exit_code=-15,
        abgebrochen=True,
        abbruch_grund="unter Last abgebrochen",
    )
    with pytest.raises(ValueError, match="gültige"):
        m105.werte_bremse_aus([abgebrochen], [])


def test_last_waechter_sperrt_ab_12_und_bricht_lauf_ab_14(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Start-Sperre greift bei Last >= 12; Live-Abbruch greift, wenn Last während des Laufs >= 14 steigt."""
    from messung import fehler_reihe_duoplus as m105
    from messung.pruefzeit_last_290 import LastZuHochFehler

    hoch = tmp_path / "loadavg_hoch"
    hoch.write_text("12.35 9.10 7.40 4/500 1234\n", encoding="utf-8")
    with pytest.raises(LastZuHochFehler):
        m105.pruefe_start_last(max_last=12.0, loadavg=hoch)

    # Live-Abbruch während eines laufenden Prozesses simulieren
    class _FakeProc:
        pid = 999999

        def __init__(self) -> None:
            self.beendet = False
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.beendet = True
            self.returncode = -15

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode if self.returncode is not None else -15

        def kill(self) -> None:
            self.beendet = True
            self.returncode = -9

    fake = _FakeProc()
    last_werte = iter([3.0, 14.5])
    monkeypatch.setattr(m105.speicher, "last_1m", lambda *_a, **_k: next(last_werte, 14.5))
    monkeypatch.setattr(m105, "prozessbaum_rss_mib", lambda _pid: 250.0)
    monkeypatch.setattr(
        m105,
        "speicher_stand",
        lambda: m105.SpeicherStand(16000, 6000, 10000, 16000, 2000),
    )
    monkeypatch.setattr(m105.starter, "starte", lambda *_a, **_k: fake)
    monkeypatch.setattr(m105.time, "sleep", lambda _s: None)

    lauf = m105.messe_einzel_gate(
        tmp_path,
        python_exe=sys.executable,
        arbeiter=4,
        verteilung="worksteal",
        runde=1,
        max_start_last=12.0,
        abbruch_last=14.0,
        test_pfade=(),
    )
    assert fake.beendet is True
    assert lauf.abgebrochen is True
    assert "unter Last abgebrochen" in lauf.abbruch_grund


def test_befehlsbau_nice_und_plattformneutral(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Auf Linux mit nice wird nice -n 15 vorangestellt; ohne nice oder auf Windows nicht."""
    from messung import fehler_reihe_duoplus as m105

    monkeypatch.setattr(m105.os, "name", "posix")
    monkeypatch.setattr(m105.shutil, "which", lambda name: "/usr/bin/nice" if name == "nice" else None)
    cmd = m105.baue_gate_befehl(
        python_exe="/opt/venv/bin/python",
        arbeiter=4,
        verteilung="worksteal",
        basetemp=tmp_path / "bt",
        test_pfade=("tests/test_a.py",),
        nice_wert=15,
    )
    assert cmd[:3] == ["/usr/bin/nice", "-n", "15"]
    assert "-n" in cmd[3:] and "4" in cmd[3:]
    assert "--dist" in cmd and "worksteal" in cmd
    assert "tests/test_a.py" in cmd

    monkeypatch.setattr(m105.os, "name", "nt")
    cmd_win = m105.baue_gate_befehl(
        python_exe="C:/venv/python.exe",
        arbeiter=4,
        verteilung="loadfile",
        basetemp=tmp_path / "bt",
        test_pfade=(),
        nice_wert=15,
    )
    assert cmd_win[0] == "C:/venv/python.exe"
    assert "nice" not in cmd_win[0]


def test_bericht_markdown_ohne_benutzerpfade_und_vollstaendig() -> None:
    """Markdown-Bericht nennt M12-Urteil, Bremse-Faktor, Schwelle 1,5 und keine Benutzerpfade."""
    from messung import fehler_reihe_duoplus as m105

    m12 = m105.bewerte_m12_app_repo(
        entscheidung_3b0="variante_3",
        r1_r3_quote=0.593,
        ungefangene_fehlgriffe=("1ed5b52", "61e877f", "0cba2e3"),
    )
    einzel = [
        m105.GateLauf(
            modus="einzel",
            runde=1,
            gate_nr=1,
            arbeiter=4,
            verteilung="worksteal",
            dauer_s=50.0,
            rss_spitze_mib=1200.0,
            ram_frei_min_mib=7000,
            swap_vorher_mib=1000,
            swap_spitze_mib=1000,
            last_vorher=1.0,
            last_spitze=4.0,
            exit_code=0,
        )
    ]
    parallel = [
        m105.ParallelRunde(
            runde=1,
            arbeiter=4,
            verteilung="worksteal",
            gesamt_wand_s=62.0,
            gates=(
                m105.GateLauf(
                    modus="parallel",
                    runde=1,
                    gate_nr=1,
                    arbeiter=4,
                    verteilung="worksteal",
                    dauer_s=60.0,
                    rss_spitze_mib=1210.0,
                    ram_frei_min_mib=5500,
                    swap_vorher_mib=1000,
                    swap_spitze_mib=1000,
                    last_vorher=1.2,
                    last_spitze=7.8,
                    exit_code=0,
                ),
                m105.GateLauf(
                    modus="parallel",
                    runde=1,
                    gate_nr=2,
                    arbeiter=4,
                    verteilung="worksteal",
                    dauer_s=62.0,
                    rss_spitze_mib=1190.0,
                    ram_frei_min_mib=5500,
                    swap_vorher_mib=1000,
                    swap_spitze_mib=1000,
                    last_vorher=1.2,
                    last_spitze=7.8,
                    exit_code=0,
                ),
            ),
            summe_rss_spitze_mib=2400.0,
            ram_frei_min_mib=5500,
            swap_vorher_mib=1000,
            swap_spitze_mib=1000,
            last_vorher=1.2,
            last_spitze=7.8,
        )
    ]
    ausw = m105.werte_bremse_aus(einzel, parallel)
    md = m105.bericht_markdown(m12, ausw, einzel, parallel, beschreibung="Probe-Messung")
    assert "nicht anwendbar, weil keine Auswahl läuft" in md
    assert "1,22×" in md or "1.22×" in md
    assert "1,5" in md or "1.5" in md
    assert "/home/" not in md
    assert "C:\\Users\\" not in md
    assert "C:/Users/" not in md


def test_parallel_gates_starten_beide_und_raeumen_sauber_auf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``messe_zwei_gates_parallel`` startet 2 getrennte Prozesse über ``starter.starte`` und sammelt beide ein."""
    from messung import fehler_reihe_duoplus as m105

    gestartet: list[dict[str, Any]] = []

    class _FakeProc:
        def __init__(self, pid: int, dauer_schritte: int) -> None:
            self.pid = pid
            self._rest = dauer_schritte
            self.returncode: int | None = None

        def poll(self) -> int | None:
            if self._rest > 0:
                self._rest -= 1
                return None
            self.returncode = 0
            return 0

        def terminate(self) -> None:
            self.returncode = -15

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode if self.returncode is not None else 0

        def kill(self) -> None:
            self.returncode = -9

    zaehler = iter([(1001, 2), (1002, 3)])

    def _fake_starte(cmd: Any, **kwargs: Any) -> _FakeProc:
        pid, schritte = next(zaehler)
        gestartet.append({"cmd": list(cmd), "cwd": kwargs.get("cwd"), "env": kwargs.get("env")})
        return _FakeProc(pid, schritte)

    monkeypatch.setattr(m105.speicher, "last_1m", lambda *_a, **_k: 2.5)
    monkeypatch.setattr(m105, "prozessbaum_rss_mib", lambda pid: 500.0 if pid == 1001 else 520.0)
    monkeypatch.setattr(
        m105,
        "speicher_stand",
        lambda: m105.SpeicherStand(16000, 7500, 8500, 16000, 1000),
    )
    monkeypatch.setattr(m105.starter, "starte", _fake_starte)
    monkeypatch.setattr(m105.time, "sleep", lambda _s: None)

    g1 = tmp_path / "g1"
    g2 = tmp_path / "g2"
    g1.mkdir()
    g2.mkdir()

    runde = m105.messe_zwei_gates_parallel(
        (g1, g2),
        python_exe=sys.executable,
        arbeiter=4,
        verteilung="worksteal",
        runde=1,
        max_start_last=12.0,
        abbruch_last=14.0,
        test_pfade=("tests/test_x.py",),
    )
    assert len(gestartet) == 2
    assert gestartet[0]["cwd"] == g1
    assert gestartet[1]["cwd"] == g2
    assert len(runde.gates) == 2
    assert all(g.exit_code == 0 and not g.abgebrochen for g in runde.gates)
    assert runde.summe_rss_spitze_mib == pytest.approx(1020.0)
