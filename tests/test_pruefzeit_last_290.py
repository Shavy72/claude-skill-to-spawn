"""Unit- und Befund-Tests für die Last-/Prüfzeit-Messreihe (#290, L7-Vorstufe)."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest
from test_review_quelle_329 import NAMEN, name_muster

from to_spawn.pruef import sofort

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

BEFUND_DATEI = REPO / "docs" / "verify-hard" / "pruefzeit_last_290.md"
HEIM_PFAD_MUSTER = re.compile(r"/home/[a-zA-Z0-9._-]+|/root/|[A-Za-z]:[\\/]+Users[\\/]", re.IGNORECASE)


def _lade_modul():
    from messung import pruefzeit_last_290

    return pruefzeit_last_290


def test_last_waechter_verweigert_ab_schwelle(tmp_path: Path) -> None:
    """Bei 1-Minuten-Last >= 12 darf kein Messlauf starten."""
    mod = _lade_modul()
    ok_datei = tmp_path / "loadavg_ok"
    ok_datei.write_text("4.25 3.80 2.90 2/412 12345\n", encoding="utf-8")
    assert mod.pruefe_last_grenze(max_last=12.0, loadavg=ok_datei) == pytest.approx(4.25)

    voll_datei = tmp_path / "loadavg_voll"
    voll_datei.write_text("12.00 9.50 7.10 14/412 12345\n", encoding="utf-8")
    with pytest.raises(mod.LastZuHochFehler):
        mod.pruefe_last_grenze(max_last=12.0, loadavg=voll_datei)


def test_swap_und_speicher_aus_meminfo(tmp_path: Path) -> None:
    """Liest RAM- und Swap-Werte aus einer ``/proc/meminfo``-Datei in MiB."""
    mod = _lade_modul()
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       16384000 kB\n"
        "MemFree:         1024000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "SwapTotal:      16384000 kB\n"
        "SwapFree:       12288000 kB\n",
        encoding="utf-8",
    )
    stand = mod.speicher_stand(meminfo=meminfo)
    assert stand.gesamt_mib == 16000
    assert stand.frei_mib == 8000
    assert stand.belegt_mib == 8000
    assert stand.swap_gesamt_mib == 16000
    assert stand.swap_belegt_mib == 4000


def test_prozessbaum_rss_summiert_eltern_und_kinder(tmp_path: Path) -> None:
    """Summiert ``VmRSS`` nur über Wurzelprozess und dessen Nachkommen."""
    mod = _lade_modul()
    eintraege = {
        100: (1, 10240),  # Wurzel: 10 MiB
        101: (100, 20480),  # Kind 1: 20 MiB
        102: (100, 30720),  # Kind 2: 30 MiB
        103: (101, 5120),  # Enkel: 5 MiB
        999: (1, 102400),  # Fremdprozess: darf nicht mitgezählt werden
    }
    for pid, (ppid, rss_kib) in eintraege.items():
        ordner = tmp_path / str(pid)
        ordner.mkdir()
        (ordner / "status").write_text(
            f"Name:\tpython3\nPPid:\t{ppid}\nVmRSS:\t{rss_kib} kB\n",
            encoding="utf-8",
        )
    assert mod.prozessbaum_rss_mib(100, proc_dir=tmp_path) == pytest.approx(65.0)
    assert mod.prozessbaum_rss_mib(777, proc_dir=tmp_path) == pytest.approx(0.0)


def test_erzeuge_probe_suite_erstellt_gueltige_testdateien(tmp_path: Path) -> None:
    """Die generierte Probe-Suite enthält syntaktisch gültige pytest-Dateien inkl. Nachzügler."""
    mod = _lade_modul()
    suite_dir = mod.erzeuge_probe_suite(tmp_path, dateien=6, tests_je_datei=4, nachzuegler=2)
    test_dateien = sorted(suite_dir.glob("test_mod_*.py"))
    assert len(test_dateien) == 6
    for datei in test_dateien:
        inhalt = datei.read_text(encoding="utf-8")
        ast.parse(inhalt, filename=str(datei))
        assert "def test_fall_01" in inhalt


def test_markdown_tabelle_und_schwellen_vorschlag() -> None:
    """Formatiert die Ergebnistabelle und leitet einen konservativen Schwellen-Vorschlag ab."""
    mod = _lade_modul()
    punkte = [
        mod.Messpunkt(
            sessions=s,
            arbeiter=w,
            verteilung="loadfile",
            dauer_s=round(12.0 / min(w, 4) * (1.0 + 0.15 * s), 2),
            pruef_rss_spitze_mib=round(60.0 + 45.0 * w, 1),
            bau_rss_spitze_mib=round(450.0 * s, 1),
            ram_frei_min_mib=max(1500, 8000 - 500 * s - 80 * w),
            swap_vorher_mib=4000,
            swap_spitze_mib=4000 + 20 * s,
            last_vorher=1.2 + 0.8 * s,
            last_spitze=1.5 + 1.1 * s + 0.6 * w,
            exit_code=0,
        )
        for s in (0, 1, 2, 3)
        for w in (1, 2, 4, 8)
    ]
    tabelle = mod.tabelle_markdown(punkte)
    for s in (0, 1, 2, 3):
        assert f"| {s} |" in tabelle
    for w in (1, 2, 4, 8):
        assert f"| {w} |" in tabelle
    assert "Dauer (s)" in tabelle
    assert "Prüf-RSS Spitze (MiB)" in tabelle

    vorschlag = mod.schwellen_vorschlag(punkte, kerne=8, ram_gesamt_mib=15996)
    assert 1 <= vorschlag.arbeiter_ruhig <= 8
    assert 1 <= vorschlag.arbeiter_unter_last <= vorschlag.arbeiter_ruhig
    assert 1 <= vorschlag.max_sessions_gleichzeitig <= 4


def test_befund_dokument_vollstaendig_und_sauber() -> None:
    """Das Befund-Dokument zu #290 deckt alle Abnahmepunkte ab und enthält keine sensiblen Daten."""
    assert BEFUND_DATEI.is_file(), f"Befunddatei fehlt: {BEFUND_DATEI}"
    text = BEFUND_DATEI.read_text(encoding="utf-8")

    # Abnahme 1: Messreihe 0, 1, 2, 3 Bau-Sessions × Arbeiter-Zahl (Dauer & Speicher-Spitze)
    for s in (0, 1, 2, 3):
        assert f"| {s} |" in text, f"Messzeilen für {s} Bau-Sessions fehlen"
    for w in (1, 2, 4, 8):
        assert f"| {w} |" in text, f"Messzeilen für {w} Arbeiter fehlen"
    assert "Speicher" in text or "RSS" in text

    # Abnahme 2: Ursache der 1346 s gegen 388 s benannt und belegt
    assert "1346" in text and "388" in text
    assert "account_db.py" in text

    # Abnahme 3: Ursache der drei 10-Sekunden-Fehler benannt und belegt
    assert "ticket-gate-fehler" in text
    assert "rollen_sicht_check" in text
    assert "kennungen_aus_log" in text

    # Abnahme 4: Vorschlag für Schwellen (nur Vorschlag, Bau ist L7)
    assert "Vorschlag" in text
    assert "L7" in text

    # Hygiene: keine Benutzerpfade, keine Geheimnisse, keine Personennamen
    assert not HEIM_PFAD_MUSTER.search(text), f"Benutzerpfad gefunden: {HEIM_PFAD_MUSTER.findall(text)}"
    assert sofort.pruefe_beleg_text(str(BEFUND_DATEI.relative_to(REPO)), text) == []
    if NAMEN:
        name_re = name_muster(NAMEN)
        treffer = [f"Z.{nr}" for nr, zeile in enumerate(text.splitlines(), 1) if name_re.search(zeile)]
        assert treffer == [], f"Personenname im Befund-Dokument: {treffer}"


def test_prozess_starts_laufen_ueber_start_tuer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Beide Prozess-Starts im Messskript nutzen ``to_spawn.starter.starte`` und sperren echte Starts im Test."""
    from to_spawn import starter

    mod = _lade_modul()
    with pytest.raises(starter.StartGesperrt):
        mod._starte_bau_last_sessions(1, python_exe=sys.executable, ram_je_session_mib=1, hilfs_dir=tmp_path)

    monkeypatch.setattr(mod, "_warte_unter_last", lambda _max_last: 1.0)
    monkeypatch.setattr(
        mod,
        "speicher_stand",
        lambda: mod.SpeicherStand(
            gesamt_mib=16000, frei_mib=8000, belegt_mib=8000, swap_gesamt_mib=0, swap_belegt_mib=0
        ),
    )
    with pytest.raises(starter.StartGesperrt):
        mod.messe_einzellauf(
            tmp_path,
            python_exe=sys.executable,
            sessions=0,
            arbeiter=1,
            verteilung="seriell",
            bau_prozesse=(),
        )
