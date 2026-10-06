"""Weg-Test für ``rueckblick.py planen`` (#581): echtes Mini-Repo mit git-Worktree, Aufruf als Unterprozess.

Kein Mock: git echt, SSH echt gegen einen nicht auflösbaren Host. Der Spec-330-Test liest die
echten Bau-Logs (unverändert kopiert nach ``tests/hilfen/rueckblick_spec330``).
"""

from __future__ import annotations

import json
import os
import time
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

SKRIPT = Path(__file__).resolve().parent.parent / "skripte" / "rueckblick.py"
SPEC330 = Path(__file__).resolve().parent / "hilfen" / "rueckblick_spec330"
SPEC = 900
FREI = "Gleicher freier Text bei zwei Tickets"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", timeout=60, check=True
    ).stdout.strip()


def _lauf(repo: Path, spec: int, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKRIPT), "planen", str(spec), "--repo", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=120,
        check=False,
    )


def _zeilen(pfad: Path, zeilen: list[dict]) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with pfad.open("a", encoding="utf-8") as f:
        for z in zeilen:
            f.write(json.dumps(z, ensure_ascii=False) + "\n")


def _vorfall(ticket: str, regel: str, ts: str) -> dict:
    return {"ts": ts, "typ": "vorfall", "ticket": ticket, "symptom": "frei", "regel": regel, "quelle": "capo"}


def _zus(ticket: str, schwer: str, ts: str) -> dict:
    return {"ts": ts, "typ": "zusammenfassung", "ticket": ticket, "umfang": "u", "schwierigkeiten": schwer}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Mini-Repo: 901 Log im Repo (+ gleiche Zeile in der Laufdatei), 902 nur in Laufdatei eines Worktrees, 903 ohne."""
    r = tmp_path / "projekt"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    manifest = r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"spec": SPEC, "feature": "x", "tickets": ["901", "902", "903"]}), encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "docs: Manifest")
    z901 = [
        _vorfall("901", "session_verwaist", "2026-09-25T08:00:00+02:00"),
        _vorfall("901", "gate_rot", "2026-09-25T09:00:00+02:00"),
        _zus("901", FREI, "2026-09-25T10:00:00+02:00"),
        _zus("901", "Zweite Schwierigkeit 901", "2026-09-25T11:00:00+02:00"),
    ]
    _zeilen(r / "docs" / "agents" / "bau_log" / "901.jsonl", z901)
    _zeilen(r / ".to-spawn" / "bau_log" / "901.jsonl", z901[:1])
    wt = tmp_path / "wt-902"
    _git(r, "worktree", "add", "-q", "-b", "ticket/902", str(wt))
    _zeilen(
        wt / ".to-spawn" / "bau_log" / "902.jsonl",
        [_vorfall("902", "session_verwaist", "2026-09-26T08:00:00+02:00"), _zus("902", FREI, "2026-09-26T09:00:00+02:00")],
    )
    return r


def _auftrag(lauf: subprocess.CompletedProcess[str]) -> str:
    pfad = next(z.split("Auftrag: ", 1)[1] for z in lauf.stdout.splitlines() if z.startswith("Auftrag: "))
    return Path(pfad.strip()).read_text(encoding="utf-8")


def _abschnitt(text: str, kopf: str) -> str:
    rest = text.split(kopf, 1)[1]
    return rest.split("\n## ", 1)[0]


def test_datenlage_fehlendes_rot_kein_null_fehler(repo: Path) -> None:
    lauf = _lauf(repo, SPEC, "--ohne-bau-server")
    assert lauf.returncode == 0, lauf.stderr
    erste = lauf.stdout.splitlines()[0]
    assert erste == "Datenlage: 2 von 3 Tickets mit Bau-Log"
    assert "🔴 #903: kein Bau-Log" in lauf.stdout
    assert "0 Fehler" not in lauf.stdout
    auftrag = _auftrag(lauf)
    assert auftrag.splitlines()[0] == erste
    assert "🔴 #903: kein Bau-Log" in auftrag
    assert "0 Fehler" not in auftrag
    assert "ausgelassen (--ohne-bau-server)" in auftrag
    lauf_json = json.loads((repo / ".to-spawn" / "rueckblick" / str(SPEC) / "lauf.json").read_text(encoding="utf-8"))
    assert lauf_json["spec"] == SPEC and lauf_json["ohne_log"] == ["903"]


def test_zaehlung_feste_namen_oben_freie_texte_ungezaehlt(repo: Path) -> None:
    auftrag = _auftrag(_lauf(repo, SPEC, "--ohne-bau-server"))
    zaehlung = _abschnitt(auftrag, "## Zählung feste Namen")
    assert "- session_verwaist: 2 Tickets (#901, #902)" in zaehlung
    assert "- gate_rot: 1 Ticket (#901)" in zaehlung
    assert zaehlung.index("session_verwaist") < zaehlung.index("gate_rot")
    assert FREI not in zaehlung
    schwer = _abschnitt(auftrag, "## Schwierigkeiten je Ticket")
    assert FREI in schwer and "Zweite Schwierigkeit 901" in schwer
    # Doppelte Zeile (Repo + Laufdatei) zählt einmal, freier Text erscheint je Ticket genau einmal.
    assert auftrag.count(FREI) == 2


@pytest.mark.skipif(shutil.which("ssh") is None, reason="kein ssh auf diesem Rechner")
def test_bau_server_nicht_erreichbar(repo: Path) -> None:
    konfig = repo / ".to-spawn" / "config.json"
    konfig.write_text(
        json.dumps({"ssh_ziel": "rueckblick-test.invalid", "server_repo": "/gibt/es/nicht/rueckblick"}),
        encoding="utf-8",
    )
    lauf = _lauf(repo, SPEC)
    assert lauf.returncode == 0, lauf.stderr
    assert lauf.stdout.splitlines()[0] == "Datenlage: 2 von 3 Tickets mit Bau-Log"
    assert "nicht erreichbar (" in lauf.stdout
    assert "nicht erreichbar (" in _auftrag(lauf)


def test_spec330_echte_logs_session_verwaist_oben(tmp_path: Path) -> None:
    r = tmp_path / "repo330"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    shutil.copy(SPEC330 / "spec-330.json", r / "docs" / "agents" / "manifests" / "spec-330.json")
    shutil.copytree(SPEC330 / "bau_log", r / "docs" / "agents" / "bau_log")
    lauf = _lauf(r, 330, "--ohne-bau-server")
    assert lauf.returncode == 0, lauf.stderr
    assert lauf.stdout.splitlines()[0] == "Datenlage: 9 von 9 Tickets mit Bau-Log"
    zaehlung = _abschnitt(_auftrag(lauf), "## Zählung feste Namen")
    erste = next(z for z in zaehlung.splitlines() if z.startswith("- "))
    # Echte Daten: session_verwaist steht in 7 Logs (auch 332 und 336, je 26.09. 14:07/18:16).
    assert erste == "- session_verwaist: 7 Tickets (#331, #332, #333, #334, #335, #336, #338)"
    # E4 nennt mindestens diese fünf Tickets.
    assert all(f"#{n}" in erste for n in (331, 333, 334, 335, 338))


def test_auftragstext_inhalt(repo: Path) -> None:
    auftrag = _auftrag(_lauf(repo, SPEC, "--ohne-bau-server"))
    assert "mp-retro" in auftrag
    assert "Erkannt wird er schon, wie verhindern wir ihn vorher?" in auftrag
    wege = ["Prüf-Skript", "Review-Auftrag", "Kontext-Paket", "CLAUDE.md"]
    stellen = [auftrag.index(w) for w in wege]
    assert stellen == sorted(stellen)
    assert "review-dirigent" in auftrag and "to-tickets" in auftrag
    assert "Weg 4 nur mit Begründung" in auftrag
    assert "nicht angeschlossen" in auftrag and "kaputt" in auftrag
    assert "Ursache beheben" in auftrag
    assert "2–3 teuersten" in auftrag
    assert "ergebnis.json" in auftrag


def test_marker_schon_erledigt(repo: Path) -> None:
    marker = repo / "docs" / "agents" / f"rueckblick_{SPEC}.md"
    marker.write_text("fertig\n", encoding="utf-8")
    lauf = _lauf(repo, SPEC, "--ohne-bau-server")
    assert lauf.returncode == 4
    assert "Schon erledigt" in lauf.stdout


def test_sperre_kein_doppelstart(repo: Path) -> None:
    ordner = repo / ".to-spawn" / "rueckblick" / str(SPEC)
    ordner.mkdir(parents=True)
    start = datetime.now().astimezone().isoformat(timespec="seconds")
    (ordner / "lauf.json").write_text(json.dumps({"start": start, "spec": SPEC}), encoding="utf-8")
    lauf = _lauf(repo, SPEC, "--ohne-bau-server")
    assert lauf.returncode == 4
    assert "läuft schon" in lauf.stdout
    assert not (ordner / "auftrag.md").exists()


UNVOLL = "🔴 Datenlage unvollständig"


def _konfig(repo: Path, **werte: str) -> None:
    pfad = repo / ".to-spawn" / "config.json"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps(werte), encoding="utf-8")


def _manifest(repo: Path, tickets: object) -> None:
    pfad = repo / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    pfad.write_text(json.dumps({"spec": SPEC, "tickets": tickets}), encoding="utf-8")


def test_bau_server_liest_geschwister_klone_samt_worktrees(repo: Path, tmp_path: Path) -> None:
    """Befund 1: Log liegt nur im Worktree eines Spec-Klons (``duoplus-551``) neben dem server_repo."""
    server = tmp_path / "duoplus-management"
    _git(tmp_path, "clone", "-q", str(repo), str(server))
    klon = tmp_path / "duoplus-551"
    _git(tmp_path, "clone", "-q", str(repo), str(klon))
    (tmp_path / "anderes-552").mkdir()  # gleiches Muster, kein Klon: wird übergangen
    wt = tmp_path / "wt-klon"
    _git(klon, "worktree", "add", "-q", "-b", "ticket/903", str(wt))
    _zeilen(wt / ".to-spawn" / "bau_log" / "903.jsonl", [_vorfall("903", "gate_rot", "2026-09-27T08:00:00+02:00")])
    _konfig(repo, server_repo=str(server))
    lauf = _lauf(repo, SPEC)
    assert lauf.returncode == 0, lauf.stderr
    assert lauf.stdout.splitlines()[0] == "Datenlage: 3 von 3 Tickets mit Bau-Log"
    assert "Bau-Server: " in lauf.stdout and "duoplus-551" in lauf.stdout
    assert "anderes-552" not in lauf.stdout


def test_ort_fehlt_rot_und_datenlage_unvollstaendig(repo: Path) -> None:
    """Befund 2: fehlender Log-Ordner = „fehlt“ 🔴, Warnung im Kopf und im Zählabschnitt."""
    shutil.rmtree(repo / "docs" / "agents" / "bau_log")
    lauf = _lauf(repo, SPEC, "--ohne-bau-server")
    assert lauf.returncode == 0, lauf.stderr
    kopf = lauf.stdout.split("Auftrag: ")[0]
    assert "🔴 Repo " in kopf and ": fehlt" in kopf
    assert "🔴 Bau-Server: ausgelassen" in kopf
    assert UNVOLL in kopf
    assert UNVOLL in _abschnitt(_auftrag(lauf), "## Zählung feste Namen")


def test_ausnahme_loescht_sperre_exit1(repo: Path) -> None:
    """Befunde 3+8: Fehler beim Schreiben → Exit 1, keine Sperre bleibt liegen."""
    ordner = repo / ".to-spawn" / "rueckblick" / str(SPEC)
    (ordner / "auftrag.md").mkdir(parents=True)
    lauf = _lauf(repo, SPEC, "--ohne-bau-server")
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert "Traceback" not in lauf.stderr
    assert not (ordner / "lauf.json").exists()


def test_kaputte_zeile_verworfen_unlesbare_datei_rot(repo: Path) -> None:
    """Befund 4: kaputte JSON-Zeile gezählt, ungültiges UTF-8 = Ort „nicht lesbar“ 🔴, kein Absturz."""
    with (repo / "docs" / "agents" / "bau_log" / "901.jsonl").open("a", encoding="utf-8") as f:
        f.write("{kaputt\n")
    (repo / ".to-spawn" / "bau_log" / "902.jsonl").write_bytes(b"\xff\xfe kein utf8\n")
    lauf = _lauf(repo, SPEC, "--ohne-bau-server")
    assert lauf.returncode == 0, lauf.stderr
    kopf = lauf.stdout.split("Auftrag: ")[0]
    assert "1 Zeilen verworfen" in kopf
    assert "nicht lesbar" in kopf
    assert UNVOLL in kopf


def test_manifest_fehlt_exit1(tmp_path: Path) -> None:
    lauf = _lauf(tmp_path, SPEC, "--ohne-bau-server")
    assert lauf.returncode == 1
    assert "Manifest" in lauf.stderr


@pytest.mark.parametrize("tickets", [[], {}, [{"nr": 901}], [1.5]])
def test_manifest_leer_oder_kaputt_exit1(repo: Path, tickets: object) -> None:
    """Befund 8: leeres Manifest oder Ticketelemente weder int noch str → Fehler."""
    _manifest(repo, tickets)
    lauf = _lauf(repo, SPEC, "--ohne-bau-server")
    assert lauf.returncode == 1, lauf.stdout
    assert "Manifest" in lauf.stderr


def test_abgelaufene_sperre_blockiert_nicht(repo: Path) -> None:
    ordner = repo / ".to-spawn" / "rueckblick" / str(SPEC)
    ordner.mkdir(parents=True)
    sperre = ordner / "lauf.json"
    sperre.write_text(json.dumps({"start": "2026-01-01T00:00:00+00:00", "spec": SPEC}), encoding="utf-8")
    alt = time.time() - 121 * 60
    os.utime(sperre, (alt, alt))
    lauf = _lauf(repo, SPEC, "--ohne-bau-server")
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert (ordner / "auftrag.md").exists()


def _modul():  # noqa: ANN202 — Modul des Skripts, für Tests an den Grenzen (Fern-Antwort, Sperre, Klone)
    import importlib.util

    for pfad in (SKRIPT.parent, SKRIPT.parent.parent):
        if str(pfad) not in sys.path:
            sys.path.insert(0, str(pfad))
    spec = importlib.util.spec_from_file_location("rueckblick_test", SKRIPT)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def test_vertrag_fern_logs_unterbefehl_gleich_lokal(repo: Path) -> None:
    """Runde 3, Punkt 1: stdout von ``logs <S>`` (echter Unterprozess) → ``_fern_antwort`` = lokale Logs."""
    rb = _modul()
    lauf = subprocess.run(
        [sys.executable, str(SKRIPT), "logs", str(SPEC), "--repo", str(repo)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=120,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stderr
    fern, orte = rb._fern_antwort("Bau-Server x", lauf.stdout)
    lokal, lokal_orte = rb.server_logs(repo.resolve(), ["901", "902", "903"])
    assert fern == lokal
    assert fern["901"] and fern["902"] and not fern["903"]
    assert orte[0] == ("Bau-Server x: per SSH gelesen", False)
    assert orte[1:] == [(f"Bau-Server: {text}", rot) for text, rot in lokal_orte]


@pytest.mark.parametrize(
    "stdout",
    [
        "{kaputt",
        "",
        "null",
        '[{"tickets": {}, "orte": []}]',
        '{"tickets": ["901"], "orte": []}',
        '{"tickets": {"901": "zeile"}, "orte": []}',
        '{"tickets": {}, "orte": [{"text": 1, "rot": true}]}',
        '{"tickets": {}, "orte": [{"text": "x", "rot": "ja"}]}',
        '{"orte": []}',
    ],
)
def test_fern_antwort_kaputt_oder_typfalsch_rot(stdout: str) -> None:
    """Runde 3, Punkt 1: kaputtes/typfalsches JSON = genau ein roter Ort, keine Ausnahme, keine Logs."""
    logs, orte = _modul()._fern_antwort("Bau-Server x", stdout)
    assert logs == {}
    assert len(orte) == 1 and orte[0][1] is True
    assert orte[0][0].startswith("Bau-Server x: Antwort unbrauchbar (")


def test_fern_antwort_zeilen_falscher_typ_rot() -> None:
    """Runde 3, Punkt 1: Zeilen, die kein JSON-Objekt-Text sind, werden gezählt und rot gemeldet."""
    stdout = json.dumps({"tickets": {"901": [1, {"a": 1}, "[1]", '{"typ": "vorfall"}']}, "orte": []})
    logs, orte = _modul()._fern_antwort("Bau-Server x", stdout)
    assert logs == {"901": {'{"typ": "vorfall"}'}}
    assert ("Bau-Server x: Antwort, 3 Zeilen verworfen", True) in orte


def test_sperre_bei_schreibfehler_wieder_weg(tmp_path: Path) -> None:
    """Runde 3, Punkt 2: Schreibfehler nach dem Anlegen der Sperre → Sperre weg, Fehler fliegt weiter."""
    sperre = tmp_path / "lauf.json"
    with pytest.raises(TypeError):
        _modul()._sperre_anlegen(sperre, {"start": object()})  # nicht serialisierbar = Schreibfehler
    assert not sperre.exists()


def test_server_ohne_origin_rot(tmp_path: Path) -> None:
    """Runde 3, Punkt 3: server_repo ohne origin → Spec-Klone nicht suchbar = roter Ort, nicht still."""
    server = tmp_path / "server"
    server.mkdir()
    _git(server, "init", "-q", "-b", "master")
    _, orte = _modul().server_logs(server, ["901"])
    rot = [text for text, r in orte if r and text.startswith("Spec-Klone neben ") and "kein origin" in text]
    assert rot, orte


def test_server_nachbarordner_nicht_lesbar_rot(repo: Path, tmp_path: Path) -> None:
    """Runde 3, Punkt 3: OSError beim Auflisten neben server_repo → roter Ort statt Absturz."""
    eltern = tmp_path / "gesperrt"
    eltern.mkdir()
    server = eltern / "duoplus-management"
    _git(tmp_path, "clone", "-q", str(repo), str(server))
    _konfig(repo, server_repo=str(server))
    eltern.chmod(0o300)  # betretbar, aber nicht auflistbar
    try:
        if os.access(eltern, os.R_OK):
            pytest.skip("Rechte greifen nicht (root)")
        lauf = _lauf(repo, SPEC)
    finally:
        eltern.chmod(0o700)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    kopf = lauf.stdout.split("Auftrag: ")[0]
    assert "🔴 Bau-Server: Spec-Klone neben " in kopf, kopf
    assert UNVOLL in kopf
