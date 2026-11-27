"""Weg-Tests für Protokoll-Pflege, Hindernis-Tür und 15-Minuten-Auslastungszeile (#904, aus App #1399).

Prüft gegen ein lokales Bare-Repo:
- Ereignis-Typ ``hindernis_gemeldet`` (Typ in ``bau_log.TYPEN``/``EREIGNIS_TYPEN`` + Tür
  ``bau_log.hindernis_gemeldet`` über ``bau_log.schreibe``).
- Idempotenter 90-Tage-Pflege-Lauf (``bau_log.pflegen``): Zeilen älter als 90 Tage werden
  zu Monats-Zusammenfassungen unter ``bau_log/monate/<YYYY-MM>.jsonl`` verdichtet, jüngere
  Zeilen bleiben unverändert, ein zweiter Lauf erzeugt keinen weiteren Commit, und
  nachrückende Alt-Zeilen werden sauber in bestehende Monate zusammengeführt.
- Auslastungs-Messung in ``to_spawn.speicher.auslastung_messen`` (freie Platte über
  ``hausmeister``, Speicher über ``speicher``, Sessions ohne Ticket).
- 15-Minuten-Takt in ``to_spawn.aufpasser.Aufpasser`` schreibt eine Auslastungszeile
  über ``bau_log.schreibe()`` (idempotent je 15-Minuten-Takt, im Trockenlauf stumm),
  abrufbar über die Lese-Tür ``bau_log.lese`` / ``bau_log.lese_auslastung``.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from to_spawn import aufpasser, bau_log, speicher

JETZT_DT = datetime(2026, 11, 27, 12, 0, 0, tzinfo=UTC)
JETZT_TS = JETZT_DT.timestamp()


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=check,
    )


def _bare_und_klon(tmp_path: Path, name: str = "klon") -> tuple[Path, Path]:
    """Legt ein lokales Bare-Repo und einen Klon mit Start-Commit auf ``main`` an."""
    bare = tmp_path / "origin.git"
    if not bare.exists():
        bare.mkdir(parents=True)
        _git(bare, "init", "--bare", "-b", "main")
    klon = tmp_path / name
    _git(tmp_path, "clone", str(bare), str(klon))
    _git(klon, "config", "user.name", "Test")
    _git(klon, "config", "user.email", "test@example.invalid")
    if _git(klon, "rev-parse", "--verify", "HEAD", check=False).returncode != 0:
        (klon / "README.md").write_text("Start\n", encoding="utf-8")
        _git(klon, "add", "README.md")
        _git(klon, "commit", "-m", "Start")
        _git(klon, "push", "-u", "origin", "main")
    return bare, klon


def _protokoll_commits(bare: Path) -> list[str]:
    erg = _git(bare, "rev-list", "refs/heads/protokoll", check=False)
    if erg.returncode != 0:
        return []
    return [z.strip() for z in erg.stdout.splitlines() if z.strip()]


def _protokoll_datei_zeilen(bare: Path, pfad_im_zweig: str) -> list[dict[str, Any]]:
    erg = _git(bare, "show", f"refs/heads/protokoll:{pfad_im_zweig}", check=False)
    if erg.returncode != 0:
        return []
    return [json.loads(z) for z in erg.stdout.splitlines() if z.strip()]


def _ereignis(
    klon: Path,
    *,
    typ: str = "start",
    ticket: str | int = 904,
    zeit: datetime,
    was: str = "Ereignis",
    wer: str = "bau",
    session: str = "sess-1",
    commit: str = "abc1234",
    grund: str = "Testlauf",
    kontextstand: float = 42.0,
    **extra: Any,
) -> dict[str, Any]:
    return bau_log.schreibe(
        typ,
        repo=klon,
        ticket=ticket,
        zeit=zeit.isoformat(),
        wer=wer,
        was=was,
        session=session,
        commit=commit,
        grund=grund,
        kontextstand=kontextstand,
        **extra,
    )


def _fake_meminfo(pfad: Path, *, frei_mib: int, gesamt_mib: int = 16384) -> Path:
    pfad.write_text(
        f"MemTotal:       {gesamt_mib * 1024} kB\nMemAvailable:   {frei_mib * 1024} kB\n",
        encoding="utf-8",
    )
    return pfad


def _fake_proc(ordner: Path, pids_status: list[tuple[int, str | None]], sessions_ordner: Path) -> Path:
    ordner.mkdir(parents=True, exist_ok=True)
    sessions_ordner.mkdir(parents=True, exist_ok=True)
    for pid, status in pids_status:
        p = ordner / str(pid)
        p.mkdir(parents=True, exist_ok=True)
        (p / "cmdline").write_bytes(b"/usr/local/bin/claude\x00--resume\x00abc\x00")
        (p / "environ").write_bytes(b"PATH=/usr/bin\x00")
        if status is not None:
            (sessions_ordner / f"{pid}.json").write_text(
                json.dumps({"pid": pid, "status": status}),
                encoding="utf-8",
            )
    return ordner


# --- 1. Ereignis-Typ hindernis_gemeldet (nur Tür + Typ) ----------------------


def test_hindernis_gemeldet_typ_und_tuer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bare, klon_a = _bare_und_klon(tmp_path, "klon_a")
    _, klon_b = _bare_und_klon(tmp_path, "klon_b")
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer_a"))

    assert bau_log.HINDERNIS_GEMELDET == "hindernis_gemeldet"
    assert bau_log.HINDERNIS_GEMELDET in bau_log.EREIGNIS_TYPEN
    assert bau_log.HINDERNIS_GEMELDET in bau_log.TYPEN

    with caplog.at_level("WARNING"):
        zeile = bau_log.hindernis_gemeldet(
            repo=klon_a,
            ticket=904,
            wer="hausmeister",
            was="Platte unter 10 % frei",
            session="hausmeister",
            commit="abc1234",
            grund="platte_knapp",
            kontextstand=0,
            art="platte_knapp",
        )

    assert "Unbekannter Zeilen-Typ" not in caplog.text
    assert zeile["typ"] == "hindernis_gemeldet"
    assert zeile["art"] == "platte_knapp"

    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer_b"))
    gelesen = bau_log.lese(klon_b, 904)
    assert len(gelesen) == 1
    assert gelesen[0]["typ"] == "hindernis_gemeldet"
    assert gelesen[0]["grund"] == "platte_knapp"
    assert len(_protokoll_datei_zeilen(bare, "bau_log/904.jsonl")) == 1


# --- 2. 90-Tage-Pflege-Lauf und Monats-Zusammenfassung -----------------------


def test_weg_pflege_verdichtet_aelter_als_90_tage_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bare, klon_a = _bare_und_klon(tmp_path, "klon_a")
    _, klon_b = _bare_und_klon(tmp_path, "klon_b")
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer_a"))

    alt_juli_1 = datetime(2026, 7, 10, 8, 0, 0, tzinfo=UTC)
    alt_juli_2 = datetime(2026, 7, 20, 9, 30, 0, tzinfo=UTC)
    alt_aug = datetime(2026, 8, 15, 14, 0, 0, tzinfo=UTC)
    jung_nov = JETZT_DT - timedelta(days=10)

    # Ticket 101: nur alte Zeilen (Juli) -> Datei wird nach Verdichtung entfernt.
    _ereignis(klon_a, typ="start", ticket=101, zeit=alt_juli_1, was="Alt 101 Start")
    _ereignis(klon_a, typ="schliessen", ticket=101, zeit=alt_juli_2, was="Alt 101 Zu")
    # Ticket 102: gemischt (August alt + November jung) -> nur August wird verdichtet.
    _ereignis(klon_a, typ="uebergabe", ticket=102, zeit=alt_aug, was="Alt 102 Übergabe")
    _ereignis(klon_a, typ="start", ticket=102, zeit=jung_nov, was="Jung 102 Start")
    # Auslastung: eine alte (August) und eine junge (November) Zeile.
    bau_log.auslastung_schreiben(
        klon_a,
        platte_frei_prozent=22.5,
        speicher_frei_mib=4096,
        speicher_gesamt_mib=16384,
        sessions=3,
        sessions_gesamt=5,
        zeit=alt_aug.isoformat(),
    )
    bau_log.auslastung_schreiben(
        klon_a,
        platte_frei_prozent=41.0,
        speicher_frei_mib=8192,
        speicher_gesamt_mib=16384,
        sessions=2,
        sessions_gesamt=4,
        zeit=jung_nov.isoformat(),
    )

    commits_vorher = len(_protokoll_commits(bare))

    # Erster Pflege-Lauf: verdichtet alle 4 Zeilen älter als 90 Tage.
    erg1 = bau_log.pflegen(klon_a, tage=90, jetzt=JETZT_DT)
    assert erg1.ok is True
    assert erg1.geaendert is True
    assert erg1.verdichtet == 4
    assert erg1.monate == ("2026-07", "2026-08")
    assert len(_protokoll_commits(bare)) == commits_vorher + 1
    assert _git(klon_a, "status", "--porcelain").stdout == ""

    # Ticket 101 (nur alt) ist weg, Ticket 102 und Auslastung behalten ihre jungen Zeilen.
    assert _protokoll_datei_zeilen(bare, "bau_log/101.jsonl") == []
    assert [z["was"] for z in _protokoll_datei_zeilen(bare, "bau_log/102.jsonl")] == ["Jung 102 Start"]
    auslastung_rest = _protokoll_datei_zeilen(bare, "bau_log/auslastung.jsonl")
    assert len(auslastung_rest) == 1
    assert auslastung_rest[0]["platte_frei_prozent"] == 41.0

    # Über zweiten Klon sind die Monats-Zusammenfassungen per Lese-Tür abrufbar.
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer_b"))
    monate = bau_log.monats_zusammenfassungen(klon_b)
    assert [m["monat"] for m in monate] == ["2026-07", "2026-08"]
    juli, august = monate
    assert juli["typ"] == "monats_zusammenfassung"
    assert juli["zeilen_gesamt"] == 2
    assert juli["nach_typ"] == {"schliessen": 1, "start": 1}
    assert juli["tickets"] == ["101"]
    assert august["zeilen_gesamt"] == 2
    assert august["nach_typ"] == {"auslastung": 1, "uebergabe": 1}
    assert august["tickets"] == ["102"]
    assert august["auslastung"]["messungen"] == 1
    assert august["auslastung"]["platte_frei_prozent_min"] == 22.5
    assert august["auslastung"]["speicher_frei_mib_min"] == 4096
    assert august["auslastung"]["sessions_max"] == 3

    # Zweiter Pflege-Lauf direkt danach: streng idempotent (kein neuer Commit, 0 verdichtet).
    commits_nach_1 = len(_protokoll_commits(bare))
    erg2 = bau_log.pflegen(klon_b, tage=90, jetzt=JETZT_DT)
    assert erg2.ok is True
    assert erg2.geaendert is False
    assert erg2.verdichtet == 0
    assert len(_protokoll_commits(bare)) == commits_nach_1

    # Nachrückende Alt-Zeile im selben Monat (August) wird sauber in 2026-08 eingerechnet.
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer_a"))
    _ereignis(
        klon_a, typ="hindernis_gemeldet", ticket=103, zeit=datetime(2026, 8, 25, 10, 0, 0, tzinfo=UTC), was="Spät"
    )
    erg3 = bau_log.pflegen(klon_a, tage=90, jetzt=JETZT_DT)
    assert erg3.ok is True
    assert erg3.geaendert is True
    assert erg3.verdichtet == 1
    assert erg3.monate == ("2026-08",)
    august_neu = bau_log.monats_zusammenfassungen(klon_b, monat="2026-08")
    assert len(august_neu) == 1
    assert august_neu[0]["zeilen_gesamt"] == 3
    assert august_neu[0]["nach_typ"] == {"auslastung": 1, "hindernis_gemeldet": 1, "uebergabe": 1}
    assert august_neu[0]["tickets"] == ["102", "103"]


# --- 3. Auslastungs-Messung (speicher + hausmeister) -------------------------


def test_speicher_auslastung_messen_nutzt_speicher_und_hausmeister(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    meminfo = _fake_meminfo(tmp_path / "meminfo", frei_mib=5120, gesamt_mib=16384)
    sessions_dir = tmp_path / "sessions"
    proc_dir = _fake_proc(tmp_path / "proc", [(1001, "busy"), (1002, "idle"), (1003, None)], sessions_dir)

    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PROC", str(proc_dir))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_SESSIONS", str(sessions_dir))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "0.345")

    stand = speicher.auslastung_messen()
    assert stand["platte_frei_prozent"] == 34.5
    assert stand["speicher_frei_mib"] == 5120
    assert stand["speicher_gesamt_mib"] == 16384
    # 1001 (busy) + 1003 (ohne Status) = 2 arbeitend; mit 1002 (idle) = 3 gesamt.
    assert stand["sessions"] == 2
    assert stand["sessions_gesamt"] == 3


# --- 4. Aufpasser 15-Minuten-Takt schreibt Auslastungszeile ------------------


def test_weg_aufpasser_schreibt_auslastungszeile_alle_15_minuten_abrufbar_ueber_lese_tuer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, klon_a = _bare_und_klon(tmp_path, "klon_a")
    _, klon_b = _bare_und_klon(tmp_path, "klon_b")
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer_a"))

    meminfo = _fake_meminfo(tmp_path / "meminfo", frei_mib=6144, gesamt_mib=16384)
    sessions_dir = tmp_path / "sessions"
    proc_dir = _fake_proc(tmp_path / "proc", [(2001, "busy"), (2002, "idle")], sessions_dir)
    monkeypatch.setenv("TO_SPAWN_SPEICHER_MEMINFO", str(meminfo))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_PROC", str(proc_dir))
    monkeypatch.setenv("TO_SPAWN_SPEICHER_SESSIONS", str(sessions_dir))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "0.52")

    zeit_wert = [JETZT_TS]

    def _mache_aufpasser(*, trocken: bool = False) -> aufpasser.Aufpasser:
        e = aufpasser.Einstellungen(
            zustand=tmp_path / "aufpasser_zustand",
            repo=klon_a,
            trocken=trocken,
            jetzt=lambda: zeit_wert[0],
        )
        ap = aufpasser.Aufpasser(e)
        ap.sitzungen = list  # type: ignore[method-assign]
        ap._deploy_wache = lambda _repos: []  # type: ignore[method-assign]
        ap._hausmeister_lauf = lambda: None  # type: ignore[method-assign]
        ap._wecker_lauf = lambda: None  # type: ignore[method-assign]
        ap.klone_nachziehen = lambda: None  # type: ignore[method-assign]
        return ap

    # Trockenlauf schreibt nichts.
    assert _mache_aufpasser(trocken=True).lauf() == 0
    assert _protokoll_commits(bare) == []

    # Erster echter Takt -> schreibt 1 Auslastungszeile ohne Ticket-Nummer.
    assert _mache_aufpasser().lauf() == 0
    # Zweiter Aufruf im selben 15-Minuten-Takt -> idempotent, keine Doppelzeile.
    zeit_wert[0] = JETZT_TS + 60
    assert _mache_aufpasser().lauf() == 0

    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer_b"))
    zeilen_1 = bau_log.lese_auslastung(klon_b)
    assert len(zeilen_1) == 1
    z1 = zeilen_1[0]
    assert z1["typ"] == "auslastung"
    assert z1["ticket"] == bau_log.AUSLASTUNG_TICKET
    assert z1["wer"] == "aufpasser"
    assert z1["platte_frei_prozent"] == 52.0
    assert z1["speicher_frei_mib"] == 6144
    assert z1["sessions"] == 1
    assert z1["sessions_gesamt"] == 2
    # Auch direkt über bau_log.lese abrufbar; verschmutzt alle_tickets() nicht.
    assert bau_log.lese(klon_b, bau_log.AUSLASTUNG_TICKET) == zeilen_1
    assert bau_log.alle_tickets(klon_b) == []

    # Nächster 15-Minuten-Takt (+900 s) -> schreibt zweite Auslastungszeile.
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer_a"))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_PLATTE", "0.48")
    zeit_wert[0] = JETZT_TS + 900
    assert _mache_aufpasser().lauf() == 0

    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer_b"))
    zeilen_2 = bau_log.lese_auslastung(klon_b)
    assert len(zeilen_2) == 2
    assert [z["platte_frei_prozent"] for z in zeilen_2] == [52.0, 48.0]
    assert _git(klon_a, "status", "--porcelain").stdout == ""
