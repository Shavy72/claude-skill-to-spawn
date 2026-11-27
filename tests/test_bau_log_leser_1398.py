"""Weg- und Grep-Tests für Ereignis-Protokoll-Leser und Pflicht-Ereignisse (#903, aus App #1398).

Prüft:
- ``bau_log.lese`` und ``bau_log.zusammenfassung`` lesen Ereignisse aus ``origin/protokoll``
  über einen zweiten Klon gegen ein lokales Bare-Repo (ohne lokale Bau-Log-Dateien).
- ``aufseher_stand.stand`` / ``_log_lage`` liest Kontextstand, Phase und Aussage aus
  ``origin/protokoll`` (ohne lokale ``.to-spawn/bau_log``- oder ``docs/agents/bau_log``-Dateien).
- Pflicht-Ereignisse (Start, Übergabe mit Auslöser/Kontextstand, Schließen, Wiedereröffnen
  mit Grund) werden in Aufpasser, Spawn und Aufseher ausschließlich über ``bau_log.schreibe()``
  geschrieben und sind idempotent gegen Doppelaufruf.
- Grep-Test sichert ab, dass kein Modul am ``bau_log.schreibe()``-Weg vorbei lokal in
  ``.to-spawn/bau_log`` oder ``docs/agents/bau_log`` schreibt.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from to_spawn import (
    aufpasser,
    aufseher_stand,
    bau_log,
    befund,
    capo,
    eigener_handoff,
    leiter,
    respawn,
    spawn,
)

SKILL = Path(__file__).resolve().parent.parent
SPEC = 900
TICKET = 903
JETZT = 1_800_000_000.0


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
    """Lokales Bare-Repo + Klon mit Start-Commit auf ``main``."""
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
        (klon / ".to-spawn").mkdir(exist_ok=True)
        (klon / ".to-spawn" / "config.json").write_text(
            json.dumps({"worktree_basis": str(tmp_path / "wts")}),
            encoding="utf-8",
        )
        _git(klon, "add", "-A")
        _git(klon, "commit", "-m", "Start")
        _git(klon, "push", "-u", "origin", "main")
    return bare, klon


def _protokoll_zeilen(bare: Path, ticket: str | int) -> list[dict[str, Any]]:
    erg = _git(bare, "show", f"refs/heads/protokoll:bau_log/{ticket}.jsonl", check=False)
    if erg.returncode != 0:
        return []
    return [json.loads(z) for z in erg.stdout.splitlines() if z.strip()]


def _keine_lokalen_bau_logs(repo: Path) -> None:
    assert not (repo / ".to-spawn" / "bau_log").exists()
    assert not (repo / "docs" / "agents" / "bau_log").exists()


# --- 1. Leser: bau_log.lese / zusammenfassung / alle_tickets aus origin/protokoll ---


def test_weg_leser_lesen_origin_protokoll_im_zweiten_klon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bare, klon_a = _bare_und_klon(tmp_path, "klon_a")
    _, klon_b = _bare_und_klon(tmp_path, "klon_b")
    puffer_a = tmp_path / "puffer_a"
    puffer_b = tmp_path / "puffer_b"

    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(puffer_a))
    bau_log.schreibe(
        "start",
        repo=klon_a,
        ticket=TICKET,
        zeit="2026-11-27T06:00:00+01:00",
        wer="spawn",
        was="Ticket gestartet",
        session="sess-a",
        commit="abc1234",
        grund="Spawn für Spec #900",
        kontextstand=0,
    )
    bau_log.schreibe(
        "uebergabe",
        repo=klon_a,
        ticket=TICKET,
        zeit="2026-11-27T06:30:00+01:00",
        wer="aufpasser",
        was="Übergabe an Staffel 2",
        session="sess-a",
        commit="def5678",
        grund="Smart Zone erreicht",
        kontextstand=142.5,
        ausloeser="eigener_handoff",
    )

    # Zweiter Klon mit eigenem leerem Puffer und ohne lokale Bau-Log-Dateien.
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(puffer_b))
    _keine_lokalen_bau_logs(klon_a)
    _keine_lokalen_bau_logs(klon_b)

    assert bau_log.hat_log(klon_b, TICKET) is True
    assert str(TICKET) in bau_log.alle_tickets(klon_b)
    zeilen = bau_log.lese(klon_b, TICKET)
    assert [z["typ"] for z in zeilen] == ["start", "uebergabe"]
    assert zeilen[1]["ausloeser"] == "eigener_handoff"
    assert zeilen[1]["kontextstand"] == 142.5

    z = bau_log.zusammenfassung(klon_b, TICKET)
    assert z["spitze_k"] == 142.5
    assert z["sessions"] == 1
    assert z["handoffs"] == 1
    assert bau_log.letzter_session_start(klon_b, TICKET) is not None
    assert bau_log.kontext_aktuell_k(klon_b, TICKET) == 142.5
    assert _git(klon_a, "status", "--porcelain").stdout == ""
    assert _git(klon_b, "status", "--porcelain").stdout == ""
    assert len(_protokoll_zeilen(bare, TICKET)) == 2


# --- 2. Leser: aufseher_stand liest aus origin/protokoll ---


def test_weg_aufseher_stand_liest_aus_origin_protokoll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, klon_a = _bare_und_klon(tmp_path, "klon_a")
    _, klon_b = _bare_und_klon(tmp_path, "klon_b")
    puffer_a = tmp_path / "puffer_a"
    puffer_b = tmp_path / "puffer_b"

    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(puffer_a))
    bau_log.schreibe(
        "start",
        repo=klon_a,
        ticket=TICKET,
        zeit="2026-11-27T06:00:00+01:00",
        wer="spawn",
        was="Bau gestartet",
        session="sess-1",
        commit="abc1234",
        grund="Spawn",
        kontextstand=0,
    )
    bau_log.schreibe(
        "uebergabe",
        repo=klon_a,
        ticket=TICKET,
        zeit="2026-11-27T06:15:00+01:00",
        wer="aufpasser",
        was="Übergabe an Staffel 2",
        session="sess-1",
        commit="def5678",
        grund="Eigener Handoff",
        kontextstand=155.3,
        ausloeser="eigener_handoff",
    )

    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(puffer_b))
    _keine_lokalen_bau_logs(klon_b)

    def fake_kinder(spec: int) -> list[dict[str, Any]]:
        assert spec == SPEC
        return [{"number": TICKET, "state": "open", "title": "Protokoll-Leser", "comments": 0}]

    def fake_tmux(args: list[str]) -> str | None:
        if args[0] == "list-windows":
            return f"0\twache {SPEC}\t{int(JETZT)}\n1\tbau {TICKET}\t{int(JETZT - 60)}"
        if args[0] == "capture-pane":
            return "arbeitet… (esc to interrupt)\n"
        return None

    q = aufseher_stand.Quellen(repo=klon_b, kinder=fake_kinder, tmux=fake_tmux, jetzt=lambda: JETZT)
    ordner = tmp_path / "stand"
    with caplog.at_level("WARNING"):
        text = aufseher_stand.stand(SPEC, q, ordner=ordner)

    zeile = next(z for z in text.splitlines() if z.startswith(f"#{TICKET} "))
    assert "Kontext 155,3k" in zeile, zeile
    assert "Phase Übergabe" in zeile, zeile
    assert "„Übergabe an Staffel 2“" in zeile, zeile
    assert "Kein Bau-Log" not in caplog.text

    # Wiedereröffnen in Klon A → Klon B sieht neue Phase sofort über origin/protokoll.
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(puffer_a))
    bau_log.schreibe(
        "wiedereroeffnen",
        repo=klon_a,
        ticket=TICKET,
        zeit="2026-11-27T06:20:00+01:00",
        wer="aufseher",
        was="Ticket wieder geöffnet",
        session="aufseher",
        commit="def5678",
        grund="Beleg fehlt",
        kontextstand=155.3,
    )
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(puffer_b))
    text2 = aufseher_stand.stand(SPEC, q, ordner=ordner)
    zeile2 = next(z for z in text2.splitlines() if z.startswith(f"#{TICKET} "))
    assert "Phase Wiedereröffnet" in zeile2, zeile2


# --- 3. Idempotenz direkt in bau_log.schreibe() ---


def test_schreibe_idempotent_gegen_doppelaufruf_ueber_zwei_klone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, klon_a = _bare_und_klon(tmp_path, "klon_a")
    _, klon_b = _bare_und_klon(tmp_path, "klon_b")

    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer_a"))
    z1 = bau_log.schreibe(
        "schliessen",
        repo=klon_a,
        ticket=TICKET,
        zeit="2026-11-27T06:00:01+01:00",
        wer="aufpasser",
        was="Fenster geschlossen",
        session="bau-903",
        commit="abc1234",
        grund="Ticket #903 zu",
        kontextstand=88.0,
        idempotenz_schluessel="schliessen:903:zu:abc1234",
    )
    # Zweiter Aufruf im anderen Klon mit anderer Zeit, aber gleichem Idempotenz-Schlüssel.
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer_b"))
    z2 = bau_log.schreibe(
        "schliessen",
        repo=klon_b,
        ticket=TICKET,
        zeit="2026-11-27T06:00:05+01:00",
        wer="aufpasser",
        was="Fenster geschlossen",
        session="bau-903",
        commit="abc1234",
        grund="Ticket #903 zu",
        kontextstand=88.0,
        idempotenz_schluessel="schliessen:903:zu:abc1234",
    )

    remote = _protokoll_zeilen(bare, TICKET)
    assert len(remote) == 1
    assert remote[0]["idempotenz_schluessel"] == "schliessen:903:zu:abc1234"
    assert z1["typ"] == "schliessen"
    assert z2["typ"] == "schliessen"


# --- 4. Pflicht-Ereignisse in Spawn (Start + Übergabe, idempotent) ---


def test_weg_spawn_neustart_schreibt_start_und_uebergabe_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, klon = _bare_und_klon(tmp_path, "klon")
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer"))
    monkeypatch.setattr(
        spawn, "ticket_eintrag", lambda _s, _t: SimpleNamespace(zustand="aus", pid=None, session_pid=None)
    )
    monkeypatch.setattr(spawn, "_neustart_lokal_befehl", lambda _r, _s, _t, _a: (["echo", "ok"], True))
    monkeypatch.setattr(spawn.capo, "_starte_folge_runde", lambda *_a, **_kw: "")
    monkeypatch.setattr(spawn.starter, "starte", lambda *_a, **_kw: SimpleNamespace(returncode=0))

    # Normaler Neustart -> Start-Ereignis (Doppelaufruf idempotent).
    assert spawn.neustart(klon, SPEC, TICKET, {}, ziel="local") == 0
    assert spawn.neustart(klon, SPEC, TICKET, {}, ziel="local") == 0

    zeilen = _protokoll_zeilen(bare, TICKET)
    starts = [z for z in zeilen if z["typ"] == "start"]
    assert len(starts) == 1, zeilen
    assert starts[0]["wer"] == "spawn"

    # Neustart mit Handoff -> Übergabe-Ereignis mit Auslöser + Kontextstand (Doppelaufruf idempotent).
    handoff_datei = "docs/handoffs/HANDOFF_2026-11-27_903.md"
    assert spawn.neustart(klon, SPEC, TICKET, {}, ziel="local", handoff=handoff_datei) == 0
    assert spawn.neustart(klon, SPEC, TICKET, {}, ziel="local", handoff=handoff_datei) == 0

    zeilen2 = _protokoll_zeilen(bare, TICKET)
    uebergaben = [z for z in zeilen2 if z["typ"] == "uebergabe"]
    assert len(uebergaben) == 1, zeilen2
    assert uebergaben[0]["wer"] == "spawn"
    assert uebergaben[0]["ausloeser"]
    assert "kontextstand" in uebergaben[0]
    _keine_lokalen_bau_logs(klon)


def test_weg_spawn_start_schreibt_start_ereignis_und_offline_leser_sieht_puffer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, klon = _bare_und_klon(tmp_path, "klon")
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer"))
    monkeypatch.setattr(spawn.manifest, "pruefe", lambda *_a, **_kw: SimpleNamespace(sauber=True, text=lambda: "ok"))
    monkeypatch.setattr(spawn, "baue_befehl", lambda *_a, **_kw: ["ssh", "srv", "ok"])
    monkeypatch.setattr(spawn.stand, "sichere_stand", lambda *_a, **_kw: 0)
    monkeypatch.setattr(spawn.starter, "starte", lambda *_a, **_kw: SimpleNamespace(returncode=0))

    assert spawn.spawn(klon, SPEC, {"server_repo": "/srv/repo"}, ziel="srv", tickets=[str(TICKET)]) == 0
    assert spawn.spawn(klon, SPEC, {"server_repo": "/srv/repo"}, ziel="srv", tickets=[str(TICKET)]) == 0
    remote = _protokoll_zeilen(bare, TICKET)
    assert len([z for z in remote if z["typ"] == "start" and z["wer"] == "spawn"]) == 1, remote

    # Offline-Fall: origin weg -> Ereignis liegt im Puffer und ist für lese() dennoch sofort sichtbar.
    _git(klon, "remote", "set-url", "origin", str(tmp_path / "offline-fehlt.git"))
    bau_log.schreibe(
        "hindernis",
        repo=klon,
        ticket=TICKET,
        wer="aufseher",
        was="Netz getrennt",
        session="aufseher",
        commit="abc1234",
        grund="Offline-Probe",
        kontextstand=12.5,
    )
    gelesen = bau_log.lese(klon, TICKET)
    assert any(z["typ"] == "hindernis" and z["was"] == "Netz getrennt" for z in gelesen), gelesen
    _keine_lokalen_bau_logs(klon)


# --- 5. Pflicht-Ereignisse in Aufpasser (Start, Übergabe, Schließen, idempotent) ---


def _fake_aufpasser(tmp_path: Path, repo: Path) -> aufpasser.Aufpasser:
    e = aufpasser.Einstellungen(zustand=tmp_path / "aufpasser_zustand", repo=repo, jetzt=lambda: JETZT)
    ap = aufpasser.Aufpasser(e)
    ap.melden = lambda *_a, **_kw: None  # type: ignore[method-assign]
    ap.vor_eingriff = lambda *_a, **_kw: True  # type: ignore[method-assign]
    ap.fenster_starten = lambda *_a, **_kw: None  # type: ignore[method-assign]
    ap._start_nachweisen = lambda *_a, **_kw: True  # type: ignore[method-assign]
    ap.sichern_worktree = lambda *_a, **_kw: "1234567890abcdef"  # type: ignore[method-assign]
    ap._fenster_schliessen = lambda *_a, **_kw: "fenster"  # type: ignore[method-assign]
    ap._fenster_fortsetzen = lambda *_a, **_kw: None  # type: ignore[method-assign]
    ap._fortsetzen_nachweisen = lambda *_a, **_kw: True  # type: ignore[method-assign]
    ap._pane_lesen = lambda *_a, **_kw: ("ok", "hash-neu")  # type: ignore[method-assign]
    return ap


def test_weg_aufpasser_schreibt_start_uebergabe_schliessen_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, klon = _bare_und_klon(tmp_path, "klon")
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer"))
    ap = _fake_aufpasser(tmp_path, klon)

    # 1) Start (fehlendes Fenster) — doppelt aufgerufen -> genau 1 Start-Ereignis.
    ap.fehlendes_fenster("spec-900", str(SPEC), klon, TICKET)
    ap.fehlendes_fenster("spec-900", str(SPEC), klon, TICKET)

    # 2) Übergabe ab eigenem Handoff — doppelt aufgerufen -> genau 1 Übergabe-Ereignis mit Auslöser/Kontextstand.
    treffer = eigener_handoff.Treffer("docs/handoffs/HANDOFF_2026-11-27_903.md", JETZT - 300)
    monkeypatch.setattr(eigener_handoff, "pruefe", lambda _r, _t, _j: treffer)
    monkeypatch.setattr(eigener_handoff, "folge_starten", lambda *_a, **_kw: 0)
    fenster = aufpasser.Fenster("spec-900", str(SPEC), "1", f"bau {TICKET}", str(klon), 0, "@1", "%1")
    assert ap.folge_ab_handoff(fenster, TICKET, klon, f"spec-900/bau {TICKET}", "h0") is True
    assert ap.folge_ab_handoff(fenster, TICKET, klon, f"spec-900/bau {TICKET}", "h0") is True

    # 3) Übergabe durch Fortsetzen nach Stillstand — doppelt aufgerufen -> genau 1 Ereignis.
    info = aufpasser.SessionInfo("sess-resume-903", klon, "idle", 12345)
    ap.sichern_und_fortsetzen(fenster, TICKET, klon, f"spec-900/bau {TICKET}", {}, "h0", info)
    ap.sichern_und_fortsetzen(fenster, TICKET, klon, f"spec-900/bau {TICKET}", {}, "h0", info)

    # 4) Schließen — doppelt aufgerufen -> genau 1 Schließen-Ereignis.
    ap.ticket_zu_schliessen(fenster, TICKET, klon, f"spec-900/bau {TICKET}", "h0", info, grund="Ticket #903 zu")
    ap.ticket_zu_schliessen(fenster, TICKET, klon, f"spec-900/bau {TICKET}", "h0", info, grund="Ticket #903 zu")

    zeilen = _protokoll_zeilen(bare, TICKET)
    assert len([z for z in zeilen if z["typ"] == "start"]) == 1, zeilen
    uebergaben = [z for z in zeilen if z["typ"] == "uebergabe"]
    assert len(uebergaben) == 2, zeilen
    assert {z["ausloeser"] for z in uebergaben} == {"eigener_handoff", "stillstand_fortsetzen"}
    assert all("kontextstand" in z for z in uebergaben)
    schliessen = [z for z in zeilen if z["typ"] == "schliessen"]
    assert len(schliessen) == 1, zeilen
    assert schliessen[0]["wer"] == "aufpasser"
    assert "zu" in schliessen[0]["grund"]
    _keine_lokalen_bau_logs(klon)


# --- 6. Pflicht-Ereignisse in Aufseher (Wiedereröffnen, Schließen, Übergabe) ---


def test_weg_aufseher_wiedereroeffnen_schliessen_uebergabe_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, klon = _bare_und_klon(tmp_path, "klon")
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(tmp_path / "puffer"))
    monkeypatch.setattr(befund, "kommentiere", lambda *_a, **_kw: True)

    # Wiedereröffnen mit Grund über capo._wieder_oeffnen / befund.wieder_oeffnen (idempotent).
    funde = [capo.Verstoss(TICKET, "beweis", "Beleg-Commit fehlt")]
    assert capo._wieder_oeffnen(TICKET, "org/repo", funde, False, repo=klon) == [f"#{TICKET} wieder geöffnet"]
    assert capo._wieder_oeffnen(TICKET, "org/repo", funde, False, repo=klon) == [f"#{TICKET} wieder geöffnet"]

    zeilen = _protokoll_zeilen(bare, TICKET)
    reopens = [z for z in zeilen if z["typ"] == "wiedereroeffnen"]
    assert len(reopens) == 1, zeilen
    assert reopens[0]["wer"] == "aufseher"
    assert "beweis" in reopens[0]["grund"] and "Beleg-Commit fehlt" in reopens[0]["grund"]

    # Spec schließen über befund.spec_schliessen (idempotent).
    monkeypatch.setattr(befund, "spec_offen", lambda *_a, **_kw: [])
    assert befund.spec_schliessen(klon, "org/repo", SPEC).ok is True
    assert befund.spec_schliessen(klon, "org/repo", SPEC).ok is True
    spec_zeilen = _protokoll_zeilen(bare, SPEC)
    assert len([z for z in spec_zeilen if z["typ"] == "schliessen"]) == 1, spec_zeilen

    # Leiter-Respawn (Aufseher-Übergabe mit Auslöser und Kontextstand, idempotent).
    monkeypatch.setattr(leiter.leitstand, "setze_leiter_stufe", lambda *_a, **_kw: None)
    monkeypatch.setattr(leiter.leitstand, "schliesse_leiter_respawn", lambda *_a, **_kw: None)
    monkeypatch.setattr(respawn, "abloesen", lambda *_a, **_kw: respawn.Ergebnis(respawn.EXIT_OK, "abgelöst"))
    fake_u = SimpleNamespace(werkzeug=lambda: None, jetzt=lambda: JETZT)
    gemerkt = leiter.Gemerkt(stufe=2, seit=JETZT - 120)
    assert leiter._respawn(fake_u, klon, SPEC, TICKET, gemerkt, JETZT).exit == leiter.EXIT_OK  # type: ignore[arg-type]
    assert leiter._respawn(fake_u, klon, SPEC, TICKET, gemerkt, JETZT).exit == leiter.EXIT_OK  # type: ignore[arg-type]

    zeilen_nach = _protokoll_zeilen(bare, TICKET)
    uebergaben = [z for z in zeilen_nach if z["typ"] == "uebergabe"]
    assert len(uebergaben) == 1, zeilen_nach
    assert uebergaben[0]["wer"] == "aufseher"
    assert uebergaben[0]["ausloeser"] == "leiter_respawn"
    assert "kontextstand" in uebergaben[0]
    _keine_lokalen_bau_logs(klon)


# --- 7. Grep-Test: Kein lokaler Schreibweg am bau_log.schreibe()-Tor vorbei ---


def test_grep_kein_direkter_lokaler_bau_log_schreibweg() -> None:
    """Kein Modul außerhalb von ``bau_log.py`` schreibt direkt in ``bau_log``-Dateien,
    und Aufpasser/Spawn/Aufseher-Pflichtereignisse nutzen ausschließlich ``bau_log.schreibe()``."""
    direkt_schreiben = re.compile(r"\.(?:open\([^)]*['\"][aw]|write_text\(|write_bytes\()")
    bau_log_pfad_ref = re.compile(r"(?:\.to-spawn/bau_log|docs/agents/bau_log|lauf_pfad\(|log_pfad\()")
    verstoesse: list[str] = []

    for ordner in (SKILL / "to_spawn", SKILL / "skripte"):
        for datei in sorted(ordner.rglob("*.py")):
            rel = datei.relative_to(SKILL).as_posix()
            if rel == "to_spawn/bau_log.py":
                continue
            text = datei.read_text(encoding="utf-8")
            for nr, zeile in enumerate(text.splitlines(), 1):
                if bau_log_pfad_ref.search(zeile) and direkt_schreiben.search(zeile):
                    verstoesse.append(f"{rel}:{nr}: {zeile.strip()}")
    assert verstoesse == [], "Direkter Schreibzugriff auf lokale Bau-Log-Dateien gefunden:\n" + "\n".join(verstoesse)

    # Pflicht-Ereignis-Module dürfen weder alte lokale Schreibfunktionen noch 3-Arg-schreibe nutzen.
    for rel in ("to_spawn/aufpasser.py", "to_spawn/spawn.py", "to_spawn/befund.py", "to_spawn/leiter.py"):
        pfad = SKILL / rel
        quelltext = pfad.read_text(encoding="utf-8")
        assert "bau_log.eintrag_schreiben" not in quelltext, rel
        assert "bau_log.spiegel_in_laufdatei" not in quelltext, rel
        baum = ast.parse(quelltext, filename=rel)
        schreibe_aufrufe = 0
        for knoten in ast.walk(baum):
            if (
                isinstance(knoten, ast.Call)
                and isinstance(knoten.func, ast.Attribute)
                and knoten.func.attr == "schreibe"
                and isinstance(knoten.func.value, ast.Name)
                and knoten.func.value.id == "bau_log"
            ):
                schreibe_aufrufe += 1
                assert len(knoten.args) <= 1, (
                    f"{rel}:{knoten.lineno}: alter 3-Argumente-Aufruf von bau_log.schreibe() gefunden"
                )
        assert schreibe_aufrufe >= 1, f"{rel} schreibt kein Pflicht-Ereignis über bau_log.schreibe()"

    # aufseher_stand.py greift nicht direkt auf lokale bau_log-Pfade zu.
    stand_text = (SKILL / "to_spawn" / "aufseher_stand.py").read_text(encoding="utf-8")
    assert ".to-spawn/bau_log" not in stand_text
    assert "docs/agents/bau_log" not in stand_text
    assert "bau_log.lauf_pfad" not in stand_text
    assert "bau_log.log_pfad" not in stand_text
