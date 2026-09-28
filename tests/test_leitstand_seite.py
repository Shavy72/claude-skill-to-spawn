"""Bau-Leitstand-Seite (``skripte/leitstand.py``): Rechenteil für die Live-Seite einer Spec.

Reine Rechenlogik direkt (Zustand je Ticket, Dedupe, seed-Dokumente, URL-Ablage).
Der Weg über die Kommandozeile läuft als echter Prozess gegen ein Wegwerf-Repo;
GitHub ist ein externer Dienst und kommt aus einer echten Aufzeichnung
(``hilfen/leitstand_seite/gh_issues_376.json``, einmal mit ``gh issue view`` geholt)
über den vorhandenen ``gh_stub.py``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

SKILL = Path(__file__).resolve().parents[1]
SKRIPTE = SKILL / "skripte"
HILFEN = Path(__file__).resolve().parent / "hilfen"
AUFZEICHNUNG = HILFEN / "leitstand_seite"
OHNE_FENSTER = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _lade(name: str, datei: Path) -> ModuleType:
    if str(SKILL) not in sys.path:
        sys.path.insert(0, str(SKILL))
    spec = importlib.util.spec_from_file_location(name, datei)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    spec.loader.exec_module(modul)
    return modul


@pytest.fixture(scope="module")
def ls() -> ModuleType:
    return _lade("leitstand_seite_test", SKRIPTE / "leitstand.py")


def _github_aus_aufzeichnung() -> dict[str, dict]:
    roh = json.loads((AUFZEICHNUNG / "gh_issues_376.json").read_text(encoding="utf-8"))
    daten = {n: {**d, "labels": [{"name": x} for x in d["labels"]]} for n, d in roh.items()}
    daten["378"]["_kanten"] = [{"number": 377, "state": "open"}]
    daten["377"]["_kanten"] = []
    return daten


def _manifest() -> dict:
    return json.loads((AUFZEICHNUNG / "spec-376.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- seed (reine Rechnung)


def test_seed_docs_aus_manifest_und_aufzeichnung(ls: ModuleType) -> None:
    docs = ls.seed_docs(
        "376",
        _manifest(),
        _github_aus_aufzeichnung(),
        ziel="local",
        gestartet="2026-09-27T17:39:00+02:00",
        faktor=4.8,
        ssh_ziel="bau-server",
    )
    nach_id = {f"{s}/{d}": doc for s, d, doc in docs}
    assert list(nach_id) == ["meta/stand", "tickets/377", "tickets/378", "tickets/386"]
    meta = nach_id["meta/stand"]
    assert meta["spec"] == 376
    assert meta["titel"] == "Verwerfen in der Reelstraße"
    assert meta["ziel"] == "local" and meta["faktor"] == 4.8
    assert meta["gestartet"] == "2026-09-27T17:39:00+02:00"
    assert "naehte" not in meta, "Manifest ohne naehte → Feld fehlt"
    assert meta["beobachten"][0] == {"titel": "Stand als Tabelle", "befehl": "sessions 376"}
    assert any(h["titel"] == "Abnahme" and "#386" in h["text"] for h in meta["hinweise"])
    assert any(h["titel"] == "Aus der Planung" and "Playwright" in h["text"] for h in meta["hinweise"])

    t377, t378, t386 = nach_id["tickets/377"], nach_id["tickets/378"], nach_id["tickets/386"]
    assert t377["titel"] == "Rollen „obere Etage“ + Grund-Chip-Katalog"
    assert t377["art"] == "bau" and t386["art"] == "checkpoint"
    assert t377["blocked_by"] == [] and t378["blocked_by"] == [377]
    assert [t377["reihe"], t378["reihe"], t386["reihe"]] == [1, 2, 3]
    # Messwert Spec 376: 25k × 4,8 ÷ 3 k/min = 40 min, 35k → 56 min.
    assert (t377["erwartet_min"], t378["erwartet_min"]) == (40, 56)
    assert t377["liefert"].startswith("Rollen-Konstante obere Etage")
    assert t377["kurz"].startswith("Eine zentrale Rollenliste „obere Etage“")
    assert "403" in t377["beweis"]
    assert "naht" not in t377, "Manifest ohne naht → Feld fehlt"
    alles = json.dumps(docs, ensure_ascii=False)
    assert "ä" in alles and "ß" in alles and "\\u00" not in alles


def test_seed_docs_naht_und_naehte_aus_manifest(ls: ModuleType) -> None:
    manifest = _manifest()
    manifest["naehte"] = [{"nr": 1, "name": "Oberfläche", "kurz": "Klick-Logik im Browser-Prüfstand."}]
    manifest["tickets"]["378"]["naht"] = 1
    docs = {f"{s}/{d}": doc for s, d, doc in ls.seed_docs(
        "376", manifest, {}, ziel="server", gestartet="x", faktor=None, ssh_ziel="netcup"
    )}  # fmt: skip
    assert docs["meta/stand"]["naehte"][0]["name"] == "Oberfläche"
    assert docs["tickets/378"]["naht"] == 1
    assert "faktor" not in docs["meta/stand"]
    # Ohne GitHub: Titel aus Manifest, Faktor 1 → 35 ÷ 3 = 12 min.
    assert docs["tickets/378"]["erwartet_min"] == 12
    assert docs["meta/stand"]["titel"] == "reelstrasse-verwerfen"
    befehle = [b["befehl"] for b in docs["meta/stand"]["beobachten"]]
    assert befehle == ["ssh netcup sessions 376", "ssh -t netcup tmux attach -t spec-376"]


@pytest.mark.parametrize(
    ("titel", "soll"),
    [
        ("Spec: Verwerfen in der Reelstraße – Mülleimer, Quarantäne", "Verwerfen in der Reelstraße"),
        ("[Spec] Übergabe-Seite", "Übergabe-Seite"),
        ("Leitstand für Bau-Sessions", "Leitstand für Bau-Sessions"),
    ],
)
def test_spec_titel(ls: ModuleType, titel: str, soll: str) -> None:
    assert ls.spec_titel(titel) == soll


def test_ziel_normal(ls: ModuleType) -> None:
    assert ls.ziel_normal("lokal", {}) == "local"
    assert ls.ziel_normal(None, {"ziel_default": "srv"}) == "server"
    assert ls.ziel_normal(None, {"ziel_default": "local"}) == "local"


# ---------------------------------------------------------------- Zustand + Dedupe (reine Rechnung)


def _zeile(typ: str, minuten: int, **extra: object) -> dict:
    ts = (datetime(2026, 9, 27, 18, 0).astimezone() + timedelta(minutes=minuten)).isoformat()
    return {"typ": typ, "ts": ts, **extra}


def test_ticket_stand_reihenfolge(ls: ModuleType) -> None:
    leer = ls.leerer_zustand()
    assert ls.ticket_stand("1", "läuft", {"state": "CLOSED"}, [], [], leer) == ("fertig", None)
    assert ls.ticket_stand("1", "VERWAIST pid 9", {}, [], [], leer) == ("fehler", "Session verwaist")
    rot = [_zeile("deploy_phase", 1, phase="gate", status="rot")]
    assert ls.ticket_stand("1", "läuft", {}, rot, [], leer)[0] == "fehler"
    david = [ls.als_zeit(_zeile("x", 5)["ts"])]
    assert ls.ticket_stand("1", "läuft", {}, [_zeile("session_start", 0)], david, leer) == (
        "braucht_david",
        "Wartet auf David",
    )
    assert ls.ticket_stand("1", "läuft", {}, [_zeile("handoff", 2)], [], leer) == (
        "läuft",
        "Übergabe an neue Session",
    )
    assert ls.ticket_stand("1", "aus", {}, [], [], {**leer, "start": {"1": "x"}}) == (
        "wartet",
        "Session beendet, Ticket offen",
    )


def test_berechne_und_dedupe(ls: ModuleType) -> None:
    zeilen = {
        "377": ([_zeile("session_start", 0), _zeile("blockiert", 3, grund="Test-Handy belegt")], 42.0),
        "378": ([], None),
    }
    issues = {"376": {}, "377": {"state": "OPEN"}, "378": {"state": "OPEN"}}
    titel = {"377": "Rollen", "378": "Mülleimer"}
    zustand = ls.leerer_zustand()
    live, ereignisse, merk = ls.berechne("376", titel, {"377": "läuft"}, issues, zeilen, zustand)
    assert live["tickets"]["377"]["zustand"] == "läuft"
    assert live["tickets"]["377"]["phase"].startswith("Blockiert: Test-Handy")
    assert live["tickets"]["378"] == {"zustand": "wartet", "ist_k": None}
    assert "377" in merk["start"] and "378" not in merk["start"]
    arten = sorted(e.art for e in ereignisse)
    assert arten == ["info", "problem"]

    jetzt = datetime.now().astimezone()
    docs = ls.neue_dokumente(zustand, live, ereignisse, jetzt)
    assert [s for s, _, _ in docs] == ["live", "log", "log"]
    # Alles gesendet + gleicher Inhalt + Herzschlag frisch → nichts Neues.
    gesendet = {
        **zustand,
        "gesendet": [d for s, d, _ in docs if s == "log"],
        "live_inhalt": json.dumps(live, sort_keys=True, ensure_ascii=False),
        "live_zeit": jetzt.isoformat(),
    }
    assert ls.neue_dokumente(gesendet, live, ereignisse, jetzt + timedelta(seconds=60)) == []
    # Herzschlag nach 10 Min: nur live.
    spaeter = ls.neue_dokumente(gesendet, live, ereignisse, jetzt + timedelta(seconds=ls.HERZSCHLAG_S))
    assert [s for s, _, _ in spaeter] == ["live"]
    # Ereignis-ids deterministisch.
    assert [e.id for e in ereignisse] == [e.id for e in ls.berechne("376", titel, {"377": "läuft"}, issues, zeilen, zustand)[1]]


def test_david_nicht_doppelt(ls: ModuleType) -> None:
    aus_log = ls.aus_bau_log("377", [_zeile("mensch_noetig", 0)])
    kommentar = ls.Ereignis("gh|1", aus_log[0].zeit, "david", "x", "y", 377)
    assert ls.ohne_doppelte_david(aus_log, [kommentar]) == []
    assert ls.ohne_doppelte_david(aus_log, []) == aus_log


def test_repariert_doppelt_kodierte_umlaute(ls: ModuleType) -> None:
    assert ls.kurz("lÃ¤uft", 20) == "läuft"


# ---------------------------------------------------------------- Weg über die Kommandozeile


def _wegwerf_repo(tmp_path: Path, manifest: dict) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs" / "agents" / "manifests").mkdir(parents=True)
    (repo / "docs" / "agents" / "manifests" / "spec-376.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    for befehl in (["git", "init", "-q"], ["git", "remote", "add", "origin", "https://github.com/test/repo.git"]):
        subprocess.run(befehl, cwd=repo, check=True, capture_output=True, creationflags=OHNE_FENSTER)
    return repo


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TO_SPAWN_", "GH_STUB_"))}
    env.update(
        TO_SPAWN_REPO=str(repo),
        TO_SPAWN_GH_STUB=str(HILFEN / "gh_stub.py"),
        GH_STUB_DATEN=str(AUFZEICHNUNG / "gh_issues_376.json"),
        GH_STUB_BLOCKER="378:377",
        PYTHONIOENCODING="utf-8",
    )
    return subprocess.run(  # noqa: S603 — fester Befehl
        [sys.executable, str(SKRIPTE / "leitstand.py"), "376", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
        creationflags=OHNE_FENSTER,
    )


def test_cli_seed_bestaetigen_dedupe(tmp_path: Path) -> None:
    repo = _wegwerf_repo(tmp_path, _manifest())
    lauf = _cli(repo, "seed", "--ziel", "server")
    assert lauf.returncode == 0, lauf.stderr
    writes_datei = Path(lauf.stdout.strip())
    assert writes_datei == (repo / ".to-spawn" / "leitstand" / "writes-376.json")
    writes = json.loads(writes_datei.read_text(encoding="utf-8"))
    assert [f"{w['collection']}/{w['doc_id']}" for w in writes] == [
        "meta/stand",
        "tickets/377",
        "tickets/378",
        "tickets/386",
    ]
    assert all(w["op"] == "set" and Path(w["file_path"]).is_file() for w in writes)
    meta = json.loads(Path(writes[0]["file_path"]).read_text(encoding="utf-8"))
    assert meta["ziel"] == "server" and meta["titel"] == "Verwerfen in der Reelstraße"
    t378 = json.loads(Path(writes[2]["file_path"]).read_text(encoding="utf-8"))
    assert t378["blocked_by"] == [377] and t378["titel"] == "Mülleimer + Pop-up-Gerüst in der Reelstraße"

    # Ohne Bestätigung kommt dasselbe wieder (nichts verloren).
    assert _cli(repo, "seed").returncode == 0
    assert _cli(repo, "bestaetigen").returncode == 0
    assert not writes_datei.exists()
    zustand = json.loads((repo / ".to-spawn" / "leitstand" / "zustand-376.json").read_text(encoding="utf-8"))
    assert zustand["seed_gesendet"] == ["meta/stand", "tickets/377", "tickets/378", "tickets/386"]
    assert zustand["gestartet"]
    # Nach Bestätigung: nichts Neues → Exit 3.
    nochmal = _cli(repo, "seed")
    assert nochmal.returncode == 3, nochmal.stderr
    assert nochmal.stdout.strip() == ""
    assert _cli(repo, "bestaetigen").returncode == 1, "nichts offen → Fehler statt stiller Erfolg"


def test_cli_url_speichern_lesen(tmp_path: Path) -> None:
    repo = _wegwerf_repo(tmp_path, _manifest())
    assert _cli(repo, "url").returncode == 4
    assert _cli(repo, "url", "--setzen", "https://example.com/x").returncode == 2
    url = "https://claude.ai/artifact/Dmu7nZQH8E5XzCdFXq3TRi"
    gesetzt = _cli(repo, "url", "--setzen", url)
    assert gesetzt.returncode == 0 and gesetzt.stdout.strip() == url
    gelesen = _cli(repo, "url")
    assert gelesen.returncode == 0 and gelesen.stdout.strip() == url
    seite = json.loads((repo / ".to-spawn" / "leitstand" / "seite-376.json").read_text(encoding="utf-8"))
    assert seite["url"] == url and seite["spec"] == "376"


def test_bestaetigen_takt_merkt_ids(ls: ModuleType, tmp_path: Path) -> None:
    ablage = ls.Ablage(tmp_path, "376")
    docs = [("live", "1-a", {"zeit": "z", "tickets": {}}), ("log", "2-b", {"art": "info"})]
    offen = {
        "art": "takt",
        "start": {"377": "s"},
        "ende": {},
        "log_ids": ["2-b"],
        "live_inhalt": "{}",
        "live_zeit": "z",
    }
    assert len(ls.lege_writes_ab(ablage, docs, offen)) == 2
    assert ls.bestaetigen(ablage) == 0
    zustand = ls.lade_zustand(ablage.zustand_datei)
    assert zustand["gesendet"] == ["2-b"] and zustand["start"] == {"377": "s"} and zustand["live_zeit"] == "z"
    assert not ablage.writes_datei.exists() and not ablage.offen_datei.exists()


# ---------------------------------------------------------------- ohne Fenster


@pytest.mark.skipif(sys.platform != "win32", reason="nur Windows-Konsolen")
def test_prozesse_lesen_startet_powershell_ohne_fenster(monkeypatch: pytest.MonkeyPatch) -> None:
    """Echter PowerShell-Aufruf; der Spion reicht nur durch und merkt sich die Startoptionen."""
    ss = _lade("sessions_stand_ohne_fenster", SKRIPTE / "sessions_stand.py")
    echt = subprocess.run
    gesehen: list[dict] = []

    def spion(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        gesehen.append(dict(kwargs))
        return echt(*args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(ss.subprocess, "run", spion)
    prozesse = ss.prozesse_lesen()
    assert prozesse, "Prozessliste leer"
    assert gesehen and int(gesehen[0].get("creationflags") or 0) & subprocess.CREATE_NO_WINDOW


def test_leitstand_startet_kinder_ohne_fenster(ls: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    echt = subprocess.run
    gesehen: list[dict] = []

    def spion(*args: object, **kwargs: object) -> subprocess.CompletedProcess:
        gesehen.append(dict(kwargs))
        return echt(*args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(ls.subprocess, "run", spion)
    ls.starte([sys.executable, "-c", "print(1)"], tmp_path)
    assert gesehen[0]["creationflags"] == ls.OHNE_FENSTER
    if sys.platform == "win32":
        assert ls.OHNE_FENSTER & subprocess.CREATE_NO_WINDOW
    shutil.rmtree(tmp_path, ignore_errors=True)
