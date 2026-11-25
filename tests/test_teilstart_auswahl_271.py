"""Tests für #271: Teil-Start einer Spec (--tickets / --ohne-wache) bleibt begrenzt.

Abdeckung:
- Auswahl-Speicher je Spec und Rechner (``to_spawn.auswahl``) inkl. Zusammenführen,
  ``--ohne-wache``, ``--nur-wache``, Voll-Start-Reset, fremder Rechner und fail-closed.
- ``spawn_srv.sh`` speichert die Auswahl (nie bei ``--dry-run`` oder Nachstart).
- Weg-Test Aufpasser: Spec mit 5 offenen Tickets, Start mit 2 → genau 2 Bau-Fenster +
  Aufseher, kein Fenster und kein Schreibzugriff auf die anderen 3.
- Aufpasser: ``--ohne-wache`` und fremder Rechner starten keinen Aufseher; Aufseher startet
  im Spec-Klon (wo das Manifest liegt) und schreibt Start-Ausgabe/Exit in eine Log-Datei.
- ``spawn.neustart`` verweigert bei Ticket außerhalb der Auswahl (Exit ≠ 0, klare Meldung).
- ``aufseher_stand`` und ``sessions_stand`` zeigen nur die gewählten Tickets.
- ``capo.tick`` fasst nur die Auswahl an (kein ``session_verwaist``/Reopen an fremden Tickets)
  und meldet bei Teil-Auswahl kein ``SPEC FERTIG``.
- Stop-Hook (``hooks``) und ``waechter_takt``: bei ``--ohne-wache`` kein Takt; bei Teil-Start
  0 Kommentare und 0 Einträge für fremde Tickets.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
SPAWN_SRV = SKILL / "skripte" / "spawn_srv.sh"
SESSIONS_STAND_PFAD = SKILL / "skripte" / "sessions_stand.py"

if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

from to_spawn import (  # noqa: E402
    aufpasser,
    aufseher_stand,
    befund,
    capo,
    hooks,
    manifest,
    spawn,
    waechter_takt,
)


def _git(repo: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return erg.stdout.strip()


def _init_git_repo(ordner: Path, *, origin: str = "https://github.com/probe/repo.git") -> Path:
    ordner.mkdir(parents=True, exist_ok=True)
    _git(ordner, "init", "-q", "-b", "main")
    _git(ordner, "config", "user.email", "test@example.invalid")
    _git(ordner, "config", "user.name", "Test")
    _git(ordner, "remote", "add", "origin", origin)
    (ordner / ".to-spawn").mkdir(parents=True, exist_ok=True)
    (ordner / ".to-spawn" / "config.json").write_text("{}", encoding="utf-8")
    (ordner / "README.md").write_text("probe\n", encoding="utf-8")
    _git(ordner, "add", ".to-spawn/config.json", "README.md")
    _git(ordner, "commit", "-q", "-m", "chore: basis")
    return ordner


def _schreibe_manifest(repo: Path, spec: int, tickets: list[int]) -> Path:
    ordner = repo / "docs" / "agents" / "manifests"
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / f"spec-{spec}.json"
    pfad.write_text(
        json.dumps(
            {
                "spec": spec,
                "feature": "teilstart-probe",
                "tickets": {str(n): {"title": f"Ticket {n}", "schaetzung_k": 40, "umfang": "Probe."} for n in tickets},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return pfad


def _lade_sessions_stand(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    name = "sessions_stand_probe_271"
    spec = importlib.util.spec_from_file_location(name, SESSIONS_STAND_PFAD)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, modul)
    spec.loader.exec_module(modul)
    return modul


# --- 1. Auswahl-Speicher (eine Stelle: to_spawn.auswahl) ----------------------


def test_auswahl_speichern_lesen_zusammenfuehren_und_vollstart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import auswahl

    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-srv-1")
    repo = tmp_path / "repo"
    repo.mkdir()

    # Ohne gespeicherte Auswahl: Verhalten wie heute (alles erlaubt).
    assert auswahl.lesen(repo, 1330) is None
    assert auswahl.ticket_erlaubt(repo, 1330, 101) is True
    assert auswahl.wache_erlaubt(repo, 1330) is True

    # Teil-Start mit 2 Tickets (mit Wache).
    datei = auswahl.speichern(repo, 1330, [101, 102], ohne_wache=False)
    assert datei is not None and datei.is_file()
    stand = auswahl.lesen(repo, 1330)
    assert stand is not None
    assert stand.tickets == (101, 102)
    assert stand.ohne_wache is False
    assert stand.rechner == "bau-srv-1"
    assert auswahl.ticket_erlaubt(repo, 1330, 101) is True
    assert auswahl.ticket_erlaubt(repo, 1330, 103) is False
    assert auswahl.wache_erlaubt(repo, 1330) is True

    # Weiteres Ticket nachstarten → zusammengeführt.
    auswahl.speichern(repo, 1330, [103], ohne_wache=True)
    stand = auswahl.lesen(repo, 1330)
    assert stand is not None
    assert stand.tickets == (101, 102, 103)
    assert stand.ohne_wache is True
    assert auswahl.wache_erlaubt(repo, 1330) is False

    # --nur-wache schaltet Wache wieder frei, behält Ticket-Auswahl.
    auswahl.speichern(repo, 1330, None, nur_wache=True)
    stand = auswahl.lesen(repo, 1330)
    assert stand is not None
    assert stand.tickets == (101, 102, 103)
    assert stand.ohne_wache is False
    assert auswahl.wache_erlaubt(repo, 1330) is True

    # Fremder Rechner darf weder Tickets noch Wache anfassen.
    monkeypatch.setenv("TO_SPAWN_RECHNER", "anderer-rechner")
    assert auswahl.ticket_erlaubt(repo, 1330, 101) is False
    assert auswahl.wache_erlaubt(repo, 1330) is False

    # Voll-Start auf eigenem Rechner löscht die Auswahl wieder.
    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-srv-1")
    auswahl.speichern(repo, 1330, None, ohne_wache=False)
    assert auswahl.lesen(repo, 1330) is None
    assert auswahl.ticket_erlaubt(repo, 1330, 999) is True

    # Kaputte Datei → fail-closed (keine Tickets, keine Wache).
    kaputt = auswahl.pfad(repo, 1330)
    kaputt.parent.mkdir(parents=True, exist_ok=True)
    kaputt.write_text("{kaputt", encoding="utf-8")
    assert auswahl.ticket_erlaubt(repo, 1330, 101) is False
    assert auswahl.wache_erlaubt(repo, 1330) is False


# --- 2. Aufpasser: Weg-Test (5 Tickets, Start mit 2) + Nachträge 1 & 4 --------


def test_aufpasser_wegtest_fuenf_tickets_start_mit_zwei(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Weg-Test aus #271: Spec mit 5 offenen Tickets, Auswahl [101, 102], ein
    Aufpasser-Lauf → genau 2 Bau-Fenster + Aufseher, kein Schreibzugriff auf die anderen 3."""
    from to_spawn import auswahl

    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-srv-1")
    repo = _init_git_repo(tmp_path / "klon-1330")
    _schreibe_manifest(repo, 1330, [101, 102, 103, 104, 105])
    auswahl.speichern(repo, 1330, [101, 102], ohne_wache=False)

    e = aufpasser.Einstellungen(zustand=tmp_path / "zustand", repo=repo)
    a = aufpasser.Aufpasser(e)

    gestartet: list[tuple[str, str, Path]] = []
    kommentare: list[str] = []
    ticket_abfragen: list[int] = []

    monkeypatch.setattr(
        a,
        "offene_tickets",
        lambda _s, _r: {
            n: {"number": n, "state": "open", "state_reason": None, "labels": []} for n in (101, 102, 103, 104, 105)
        },
    )
    monkeypatch.setattr(
        a,
        "ticket_lesen",
        lambda n, _r: ticket_abfragen.append(n) or {"number": n, "state": "open", "labels": set()},
    )
    monkeypatch.setattr(a, "belegte_tickets", lambda: set())
    monkeypatch.setattr(aufpasser.gh, "blocked_by", lambda *_a, **_k: [])
    monkeypatch.setattr(aufpasser.speicher, "platz_frei", lambda *_a, **_k: (True, "frei"))
    monkeypatch.setattr(aufpasser.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        a,
        "fenster_starten",
        lambda sitz, name, cwd, _cmd: gestartet.append((sitz, name, cwd)),
    )
    monkeypatch.setattr(a, "_start_nachweisen", lambda _s, _n: True)
    monkeypatch.setattr(
        a,
        "melden",
        lambda _s, _r, fenster, ereignis, text: kommentare.append(f"{fenster}|{ereignis}|{text}"),
    )
    monkeypatch.setattr(a, "fenster_pruefen", lambda *_a, **_k: None)

    a.sitzung_pruefen("spec-1330", "1330", [], repo)

    namen = [name for _, name, _ in gestartet]
    # #418: Aufseher startet vor den Bau-Fenstern.
    assert namen == ["wache 1330", "bau 101", "bau 102"]
    assert all(f"10{k}" not in " ".join(kommentare) for k in (3, 4, 5))
    assert set(ticket_abfragen) == {101, 102}


def test_aufpasser_ohne_wache_und_fremder_rechner_kein_aufseher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nachtrag 1(b) & 4(2): --ohne-wache und fremder Rechner sperren den Aufseher-Start."""
    from to_spawn import auswahl

    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-srv-1")
    repo = _init_git_repo(tmp_path / "klon-1330")
    auswahl.speichern(repo, 1330, [101], ohne_wache=True)

    e = aufpasser.Einstellungen(zustand=tmp_path / "zustand", repo=repo)
    a = aufpasser.Aufpasser(e)
    gestartet: list[str] = []
    monkeypatch.setattr(
        a,
        "offene_tickets",
        lambda _s, _r: {
            101: {"number": 101, "state": "open", "state_reason": None, "labels": []},
            102: {"number": 102, "state": "open", "state_reason": None, "labels": []},
        },
    )
    monkeypatch.setattr(a, "ticket_lesen", lambda n, _r: {"number": n, "state": "open", "labels": set()})
    monkeypatch.setattr(a, "belegte_tickets", lambda: set())
    monkeypatch.setattr(aufpasser.gh, "blocked_by", lambda *_a, **_k: [])
    monkeypatch.setattr(aufpasser.speicher, "platz_frei", lambda *_a, **_k: (True, "frei"))
    monkeypatch.setattr(aufpasser.time, "sleep", lambda _s: None)
    monkeypatch.setattr(a, "fenster_starten", lambda _s, name, _c, _cmd: gestartet.append(name))
    monkeypatch.setattr(a, "_start_nachweisen", lambda _s, _n: True)
    monkeypatch.setattr(a, "melden", lambda *_a, **_k: None)
    monkeypatch.setattr(a, "fenster_pruefen", lambda *_a, **_k: None)

    assert a.start_erlaubt("1330", repo, None) is False
    assert a.start_erlaubt("1330", repo, 102) is False
    assert a.start_erlaubt("1330", repo, 101) is True

    a.sitzung_pruefen("spec-1330", "1330", [], repo)
    assert gestartet == ["bau 101"]

    # Fremder Rechner: weder Bau-Fenster noch Aufseher.
    gestartet.clear()
    monkeypatch.setenv("TO_SPAWN_RECHNER", "anderer-server")
    assert a.start_erlaubt("1330", repo, None) is False
    assert a.start_erlaubt("1330", repo, 101) is False
    a.sitzung_pruefen("spec-1330", "1330", [], repo)
    assert gestartet == []


def test_aufpasser_aufseher_im_spec_klon(tmp_path: Path) -> None:
    """Nachtrag 4 (1): Aufseher startet im Spec-Klon mit Manifest, nicht im Hauptrepo."""
    origin = "https://github.com/probe/app.git"
    haupt = _init_git_repo(tmp_path / "app-management", origin=origin)
    klon = _init_git_repo(tmp_path / "app-1330", origin=origin)
    _schreibe_manifest(klon, 1330, [1331])

    e = aufpasser.Einstellungen(zustand=tmp_path / "zustand", repo=haupt)
    a = aufpasser.Aufpasser(e)

    # Fenster zeigt auf Hauptrepo ohne Manifest → Spec-Klon wird gefunden.
    fenster = [aufpasser.Fenster("spec-1330", "1330", "0", "bau 1331", str(haupt), 0)]
    gewaehlt = a.spec_repo_ordner("1330", fenster, haupt)
    assert gewaehlt == klon


@pytest.mark.skipif(sys.platform == "win32", reason="Aufpasser/bash-Fenster läuft nur auf Linux")
def test_aufpasser_aufseher_start_log(tmp_path: Path) -> None:
    """Nachtrag 4 (3): Start-Ausgabe und Exit-Code des Aufseher-Fensters landen in wache_log."""
    klon = _init_git_repo(tmp_path / "app-1330")
    e_log = aufpasser.Einstellungen(
        zustand=tmp_path / "zustand",
        repo=klon,
        wache_vorlage="sh -c 'echo startklar-fehler-probe >&2; exit 2'",
    )
    a_log = aufpasser.Aufpasser(e_log)
    befehl = a_log.wache_befehl(klon, "1330", resume=None)
    lauf = subprocess.run(["bash", "-lc", befehl], capture_output=True, text=True, check=False)
    assert lauf.returncode == 2
    log_datei = aufpasser.wache_log(klon, "1330")
    assert log_datei.is_file()
    inhalt = log_datei.read_text(encoding="utf-8")
    assert "startklar-fehler-probe" in inhalt
    assert "Exit 2" in inhalt


# --- 3. neustart verweigert außerhalb der Auswahl -----------------------------


def test_neustart_verweigert_ausserhalb_auswahl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from to_spawn import auswahl

    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-srv-1")
    repo = _init_git_repo(tmp_path / "repo")
    _schreibe_manifest(repo, 1330, [101, 102, 103])
    auswahl.speichern(repo, 1330, [101, 102], ohne_wache=False)

    # Ticket 103 liegt außerhalb der Auswahl → Exit ≠ 0, klare Meldung.
    rc = spawn.neustart(repo, 1330, 103, {}, ziel="local", dry_run=True)
    err = capsys.readouterr().err
    assert rc == manifest.EXIT_WEIGERUNG
    assert "#103" in err
    assert "Auswahl" in err

    # Ticket 101 ist in der Auswahl → erlaubt.
    eintrag_aus = type("E", (), {"zustand": "aus", "pid": None, "session_pid": None})()
    monkeypatch.setattr(spawn, "ticket_eintrag", lambda _s, _t: eintrag_aus)
    monkeypatch.setattr(spawn, "_neustart_lokal_befehl", lambda *_a: (["echo", "ok"], False))
    rc_ok = spawn.neustart(repo, 1330, 101, {}, ziel="local", dry_run=True)
    assert rc_ok == 0


# --- 4. Aufseher-Stand & sessions_stand begrenzt ------------------------------


def test_aufseher_stand_begrenzt_auf_auswahl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import auswahl

    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-srv-1")
    repo = _init_git_repo(tmp_path / "repo")
    auswahl.speichern(repo, 1330, [101, 102], ohne_wache=False)

    kinder = [
        {"number": n, "state": "open", "state_reason": None, "labels": [], "comments": 0}
        for n in (101, 102, 103, 104, 105)
    ]
    q = aufseher_stand.Quellen(
        repo=repo,
        kinder=lambda _s: kinder,
        tmux=lambda _a: "",
        jetzt=lambda: 1_700_000_000.0,
    )
    text = aufseher_stand.stand(1330, q, ordner=tmp_path / "stand", alle=True)
    assert "#101 " in text
    assert "#102 " in text
    assert "#103 " not in text
    assert "#104 " not in text
    assert "#105 " not in text
    assert "2 offen" in text


def test_sessions_stand_begrenzt_auf_auswahl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import auswahl

    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-srv-1")
    repo = _init_git_repo(tmp_path / "repo")
    _schreibe_manifest(repo, 1330, [101, 102, 103, 104, 105])
    auswahl.speichern(repo, 1330, [101, 102], ohne_wache=True)

    ss = _lade_sessions_stand(repo, monkeypatch)
    eintraege = ss.manifeste_lesen("1330")
    assert set(eintraege) == {"101", "102"}


# --- 5. capo.tick fasst fremde Tickets nicht an -------------------------------


def test_capo_tick_fasst_nur_auswahl_an_und_schliesst_spec_nicht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from to_spawn import auswahl

    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-srv-1")
    zustand_ordner = tmp_path / "capo-zustand"
    monkeypatch.setenv("TO_SPAWN_WAECHTER_ORDNER", str(zustand_ordner))
    repo = _init_git_repo(tmp_path / "repo")
    # origin/main anlegen, damit haupt_ref funktioniert.
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")

    auswahl.speichern(repo, 1330, [101], ohne_wache=False)

    jetzt = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    alt = (jetzt - timedelta(hours=10)).isoformat()
    kinder = [
        # 101: unser Ticket, sauber geschlossen (erster Tick = Ausgangsstand)
        {
            "number": 101,
            "state": "closed",
            "state_reason": "completed",
            "closed_at": alt,
            "created_at": alt,
            "updated_at": alt,
            "labels": [],
            "assignees": [],
        },
        # 102: fremdes Ticket, offen mit Assignee ohne Spur → würde session_verwaist kommentieren
        {
            "number": 102,
            "state": "open",
            "state_reason": None,
            "created_at": alt,
            "updated_at": alt,
            "labels": [],
            "assignees": [{"login": "fremd"}],
        },
        # 103: fremdes Ticket, frisch geschlossen ohne Commit → würde wieder geöffnet
        {
            "number": 103,
            "state": "closed",
            "state_reason": "completed",
            "closed_at": (jetzt - timedelta(minutes=30)).isoformat(),
            "created_at": alt,
            "updated_at": alt,
            "labels": [],
            "assignees": [],
        },
    ]

    kommentare: list[tuple[Any, str]] = []
    wieder_geoeffnet: list[int] = []
    monkeypatch.setattr(capo, "_git", lambda _r, *a: (0, "abc1234") if a[0] != "fetch" else (0, ""))
    monkeypatch.setattr(capo, "haupt_ref", lambda _r: "origin/main")
    monkeypatch.setattr(capo, "kinder", lambda _g, _s: kinder)
    monkeypatch.setattr(capo, "alle_commits", lambda _r, _ref: [])
    monkeypatch.setattr(capo, "log_vom_ref", lambda *_a: [])
    monkeypatch.setattr(capo, "laufdateien", lambda *_a: [])
    monkeypatch.setattr(
        befund,
        "kommentiere",
        lambda n, _g, text: kommentare.append((n, text)) or True,
    )
    monkeypatch.setattr(
        capo,
        "_wieder_oeffnen",
        lambda n, *_a: wieder_geoeffnet.append(n) or [f"#{n} wieder geöffnet"],
    )

    erg = capo.tick(repo, 1330, "probe/repo", {"waechter": {"karenz_min": 0}}, jetzt=jetzt)
    assert (zustand_ordner / "probe_repo_1330.json").is_file()
    assert kommentare == []
    assert wieder_geoeffnet == []
    assert all("#102" not in z and "#103" not in z for z in erg.zeilen)
    # Teil-Auswahl darf nie SPEC FERTIG melden.
    assert erg.fertig is False


# --- 6. Stop-Hook & Aufseher-Takt (Nachträge 2 & 3) ---------------------------


def test_stop_hook_ohne_wache_startet_keinen_takt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nachtrag 2 & 3: Bei --ohne-wache löst der Stop-Hook keinen Takt aus."""
    from to_spawn import auswahl

    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-srv-1")
    monkeypatch.setenv("TO_SPAWN_SPEC", "1330")
    repo = _init_git_repo(tmp_path / "repo")
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    auswahl.speichern(repo, 1330, [101], ohne_wache=True)

    gestartet: list[tuple[int, Path, str]] = []
    monkeypatch.setattr(
        waechter_takt.starter,
        "starte",
        lambda *a, **k: gestartet.append((1330, repo, "cmd")),
    )
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))

    hooks._starte_takt(repo, "101")
    assert gestartet == []
    assert waechter_takt.starte_abgeloest(1330, repo, "session_ende #101") is False
    assert gestartet == []

    # Auch direkter Takt-Aufruf steigt bei --ohne-wache ohne capo/Claude aus.
    capo_aufrufe: list[int] = []
    monkeypatch.setattr(capo, "tick", lambda *_a, **_k: capo_aufrufe.append(1))
    assert waechter_takt.takt(repo, 1330, "probe/repo") == 0
    assert capo_aufrufe == []


def test_teilstart_takt_fasst_fremde_tickets_nicht_an(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Abnahme-Zusatz Nachtrag 3: Teil-Start mit einem Ticket [101], Turn-Ende →
    0 Kommentare und 0 Einträge für fremde Tickets."""
    from to_spawn import auswahl

    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-srv-1")
    monkeypatch.setenv("TO_SPAWN_WAECHTER_ORDNER", str(tmp_path / "capo-zustand"))
    repo = _init_git_repo(tmp_path / "repo")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    auswahl.speichern(repo, 1330, [101], ohne_wache=False)

    jetzt = datetime.now(timezone.utc)
    alt = (jetzt - timedelta(hours=10)).isoformat()
    kinder = [
        {
            "number": 101,
            "state": "open",
            "state_reason": None,
            "created_at": alt,
            "updated_at": alt,
            "labels": [],
            "assignees": [],
        },
        {
            "number": 102,
            "state": "open",
            "state_reason": None,
            "created_at": alt,
            "updated_at": alt,
            "labels": [],
            "assignees": [{"login": "fremd"}],
        },
    ]
    kommentare: list[Any] = []
    monkeypatch.setattr(capo, "_git", lambda _r, *a: (0, "abc1234") if a[0] != "fetch" else (0, ""))
    monkeypatch.setattr(capo, "haupt_ref", lambda _r: "origin/main")
    monkeypatch.setattr(capo, "kinder", lambda _g, _s: kinder)
    monkeypatch.setattr(capo, "alle_commits", lambda _r, _ref: [])
    monkeypatch.setattr(
        capo,
        "log_vom_ref",
        lambda _r, _ref, n: [{"ts": jetzt.isoformat(), "typ": "frage", "text": "Fremde Frage"}] if n == 102 else [],
    )
    monkeypatch.setattr(capo, "laufdateien", lambda *_a: [])
    monkeypatch.setattr(befund, "kommentiere", lambda n, _g, t: kommentare.append((n, t)) or True)
    monkeypatch.setattr(waechter_takt.briefkasten, "offene", lambda nummern, **_k: [])

    eintraege, _neu = waechter_takt.pruefe(repo, 1330, "probe/repo", {}, None, dry_run=False)
    assert kommentare == []
    assert eintraege == []


# --- 7. spawn_srv.sh speichert Auswahl (Dry-Run & Nachstart lassen in Ruhe) ---


@pytest.mark.skipif(sys.platform == "win32", reason="spawn_srv.sh läuft auf dem Bau-Server (Linux/bash)")
def test_spawn_srv_speichert_auswahl_und_beachtet_dry_run_und_nachstart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spawn_srv.sh speichert --tickets/--ohne-wache, nie bei --dry-run oder TO_SPAWN_NACHSTART=1."""
    from to_spawn import auswahl

    # Kleines Repo mit lokalem bare origin auf aktuellem Stand.
    fern = tmp_path / "fern.git"
    subprocess.run(["git", "init", "--bare", "-q", str(fern)], check=True)
    repo = _init_git_repo(tmp_path / "repo", origin=fern.as_posix())
    (repo / ".to-spawn" / "config.json").write_text('{"speicher": {"staffel_s": 0}}', encoding="utf-8")
    _schreibe_manifest(repo, 1330, [101, 102, 103])
    _git(repo, "add", ".to-spawn/config.json", "docs/agents/manifests/spec-1330.json")
    _git(repo, "commit", "-q", "-m", "chore: manifest")
    _git(repo, "push", "-q", "-u", "origin", "main")

    # Fake-tmux im PATH, damit kein echtes tmux-Fenster entsteht und anlauf_bereit sofort greift (#353).
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fenster_datei = fake_bin / "fenster.txt"
    fake_tmux = fake_bin / "tmux"
    fake_tmux.write_text(
        "#!/usr/bin/env bash\n"
        f"fenster={str(fenster_datei)!r}\n"
        'case "$1" in\n'
        '  has-session) [ -s "$fenster" ] ;;\n'
        '  list-windows) [ -s "$fenster" ] && cat "$fenster" ;;\n'
        "  new-session|new-window)\n"
        '    prev=""\n'
        '    for a in "$@"; do\n'
        '      if [ "$prev" = "-n" ]; then printf "%s\\n" "$a" >> "$fenster"; break; fi\n'
        '      prev="$a"\n'
        "    done\n"
        "    exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "TO_SPAWN_REPO": str(repo),
        "TO_SPAWN_RECHNER": "bau-srv-1",
    }

    def lauf(*args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SPAWN_SRV), "1330", "--ohne-regularien", *args],
            cwd=str(repo),
            env={**env, **(extra_env or {})},
            capture_output=True,
            text=True,
            check=False,
        )

    # 1. Trockenlauf legt keine Auswahl an.
    r_dry = lauf("--tickets", "101,102", "--ohne-wache", "--dry-run")
    assert r_dry.returncode == 0, r_dry.stdout + r_dry.stderr
    assert auswahl.lesen(repo, 1330) is None

    # 2. Echter Teil-Start mit --ohne-wache speichert Auswahl.
    r_teil = lauf("--tickets", "101,102", "--ohne-wache")
    assert r_teil.returncode == 0, r_teil.stdout + r_teil.stderr
    stand = auswahl.lesen(repo, 1330)
    assert stand is not None
    assert stand.tickets == (101, 102)
    assert stand.ohne_wache is True

    # 3. Nachstart aus wache.py verändert eine bestehende Auswahl nicht.
    r_nach = lauf("--tickets", "103", "--ohne-wache", extra_env={"TO_SPAWN_NACHSTART": "1"})
    assert r_nach.returncode == 0, r_nach.stdout + r_nach.stderr
    stand = auswahl.lesen(repo, 1330)
    assert stand is not None
    assert stand.tickets == (101, 102)

    # 4. Voll-Start (ohne --tickets/--ohne-wache) hebt die Teil-Auswahl auf.
    r_voll = lauf()
    assert r_voll.returncode == 0, r_voll.stdout + r_voll.stderr
    assert auswahl.lesen(repo, 1330) is None


def test_spec_repo_ordner_vertraegt_fenster_ohne_pfad_und_nicht_git_ordner(tmp_path: Path) -> None:
    """spec_repo_ordner darf bei Fenstern ohne ``pfad`` und Nicht-Git-Ordnern nie abstürzen (#271)."""
    basis = tmp_path / "kein-git"
    basis.mkdir()
    a = aufpasser.Aufpasser(aufpasser.Einstellungen(zustand=tmp_path / "zustand"))
    fenster = [SimpleNamespace(name="bau 101"), SimpleNamespace(name="bau 102", pfad="")]
    assert a.spec_repo_ordner("1330", fenster, basis) == basis  # type: ignore[arg-type]
    assert aufpasser._origin_kennung(basis) == ""
