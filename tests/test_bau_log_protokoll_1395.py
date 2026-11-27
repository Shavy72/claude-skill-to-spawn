"""Weg-Tests für das Ereignis-Protokoll (#902, aus App #1395).

Prüft die gesperrte Schreib-Tür ``bau_log.schreibe(typ, **felder)``:
- Pflichtfeld-Prüfung (Zeit, Ticket, wer, was, Session, Commit, Grund, Kontextstand).
- Lokaler Puffer unter ``~/.local/state/to-spawn/protokoll`` (nie im Worktree).
- Gebündelter Git-Plumbing-Push auf den verwaisten Zweig ``protokoll`` ohne
  Eingriff in Arbeitsbaum, Index oder fremde Dateien auf ``protokoll``.
- Offline-Puffer ohne Fehler an den Aufrufer und gebündelter Wiederanlauf.
- Zwei echte parallele Prozesse gegen ein lokales Bare-Repo (getrennte Klone
  mit echten Push-Konflikten sowie gemeinsamer lokaler Puffer mit Datei-Sperre).
- Glossar-Eintrag „Ereignis-Protokoll“ in ``docs/WIEDEREINSTIEG.md``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from to_spawn import bau_log

SKILL = Path(__file__).resolve().parent.parent


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


def _protokoll_zeilen(bare: Path, ticket: str | int) -> list[dict[str, Any]]:
    """Liest ``bau_log/<ticket>.jsonl`` direkt vom Zweig ``protokoll`` im Bare-Repo."""
    erg = _git(bare, "show", f"refs/heads/protokoll:bau_log/{ticket}.jsonl", check=False)
    if erg.returncode != 0:
        return []
    return [json.loads(z) for z in erg.stdout.splitlines() if z.strip()]


def _protokoll_commits(bare: Path) -> list[str]:
    erg = _git(bare, "rev-list", "refs/heads/protokoll", check=False)
    if erg.returncode != 0:
        return []
    return [z.strip() for z in erg.stdout.splitlines() if z.strip()]


def _gueltige_felder(**ueberschreiben: Any) -> dict[str, Any]:
    felder: dict[str, Any] = {
        "ticket": 902,
        "wer": "bau",
        "was": "Session gestartet",
        "session": "sess-902-a",
        "commit": "283d283",
        "grund": "Ticket-Start",
        "kontextstand": 0,
    }
    felder.update(ueberschreiben)
    return felder


# --- 1. Pflichtfeld-Prüfung --------------------------------------------------


@pytest.mark.parametrize("fehlend", ["ticket", "wer", "was", "session", "commit", "grund", "kontextstand"])
def test_pflichtfeld_fehlend_wird_abgewiesen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fehlend: str) -> None:
    bare, klon = _bare_und_klon(tmp_path)
    monkeypatch.chdir(klon)
    felder = _gueltige_felder()
    felder.pop(fehlend)

    with pytest.raises(ValueError, match=fehlend):
        bau_log.schreibe("start", **felder)

    puffer = bau_log.protokoll_puffer_ordner()
    assert not list(puffer.glob("*.jsonl")) if puffer.exists() else True
    assert _protokoll_commits(bare) == []


@pytest.mark.parametrize(
    ("feld", "wert"),
    [
        ("zeit", ""),
        ("zeit", None),
        ("zeit", "kein-iso-zeitstempel"),
        ("ticket", ""),
        ("ticket", None),
        ("ticket", True),
        ("ticket", "../evil"),
        ("wer", "   "),
        ("was", ""),
        ("session", None),
        ("commit", ""),
        ("grund", "  "),
        ("kontextstand", None),
        ("kontextstand", ""),
        ("kontextstand", False),
    ],
)
def test_pflichtfeld_ungueltig_wird_abgewiesen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, feld: str, wert: Any
) -> None:
    bare, klon = _bare_und_klon(tmp_path)
    monkeypatch.chdir(klon)
    felder = _gueltige_felder(**{feld: wert})

    with pytest.raises(ValueError):
        bau_log.schreibe("start", **felder)

    assert _protokoll_commits(bare) == []


def test_schreibe_leerer_typ_abgewiesen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bare, klon = _bare_und_klon(tmp_path)
    monkeypatch.chdir(klon)
    with pytest.raises(ValueError):
        bau_log.schreibe("   ", **_gueltige_felder())


def test_schreibe_enthaelt_alle_pflichtfelder_und_akzeptiert_kontext_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, klon = _bare_und_klon(tmp_path)
    monkeypatch.chdir(klon)

    zeile = bau_log.schreibe("start", **_gueltige_felder(kontextstand=0, ausloeser="manuell"))

    for feld in ("zeit", "ticket", "wer", "was", "session", "commit", "grund", "kontextstand"):
        assert feld in zeile
    assert zeile["typ"] == "start"
    assert zeile["ticket"] == "902"
    assert zeile["kontextstand"] == 0
    assert zeile["ausloeser"] == "manuell"

    remote = _protokoll_zeilen(bare, 902)
    assert len(remote) == 1
    assert remote[0] == zeile


# --- 2. Puffer-Ort (~/.local/state/to-spawn/protokoll, nie im Worktree) ------


def test_puffer_liegt_unter_local_state_und_nie_im_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_heim: Path
) -> None:
    bare, klon = _bare_und_klon(tmp_path)
    monkeypatch.chdir(klon)

    erwartet = test_heim / ".local" / "state" / "to-spawn" / "protokoll"
    assert bau_log.protokoll_puffer_ordner() == erwartet

    # Auch wenn der Puffer explizit in den Worktree gelegt werden soll -> Abweisung.
    monkeypatch.setenv("TO_SPAWN_PROTOKOLL_PUFFER", str(klon / ".to-spawn" / "protokoll"))
    with pytest.raises(ValueError, match="Arbeitsordner"):
        bau_log.schreibe("start", **_gueltige_felder())
    monkeypatch.delenv("TO_SPAWN_PROTOKOLL_PUFFER")

    # Regulärer Lauf lässt den Worktree komplett sauber.
    bau_log.schreibe("start", **_gueltige_felder())
    assert _git(klon, "status", "--porcelain").stdout == ""
    assert not (klon / ".to-spawn").exists()
    assert not (klon / "docs" / "agents" / "bau_log").exists()
    assert len(_protokoll_zeilen(bare, 902)) == 1


# --- 3. Plumbing ohne Eingriff in Arbeitsbaum/Index & gebündelter Push -------


def test_plumbing_laesst_arbeitsbaum_index_und_fremddateien_unberuehrt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, klon = _bare_und_klon(tmp_path)
    monkeypatch.chdir(klon)

    # Vorhandene Datei auf protokoll anlegen (z. B. Monats-Zusammenfassung oder anderes Ticket).
    bau_log.schreibe("start", **_gueltige_felder(ticket=100, was="Alt-Ereignis"))
    # Arbeitsbaum und Index im Klon bewusst verschmutzen.
    (klon / "README.md").write_text("Geändert im Arbeitsbaum\n", encoding="utf-8")
    (klon / "staged.txt").write_text("gestaged\n", encoding="utf-8")
    _git(klon, "add", "staged.txt")
    (klon / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    kopf_vorher = _git(klon, "rev-parse", "HEAD").stdout.strip()
    zweig_vorher = _git(klon, "branch", "--show-current").stdout.strip()
    diff_cached_vorher = _git(klon, "diff", "--cached").stdout

    bau_log.schreibe("uebergabe", **_gueltige_felder(ticket=902, was="Übergabe an Staffel 2", kontextstand=185.4))

    assert _git(klon, "rev-parse", "HEAD").stdout.strip() == kopf_vorher
    assert _git(klon, "branch", "--show-current").stdout.strip() == zweig_vorher
    assert _git(klon, "diff", "--cached").stdout == diff_cached_vorher
    assert (klon / "README.md").read_text(encoding="utf-8") == "Geändert im Arbeitsbaum\n"
    assert (klon / "untracked.txt").read_text(encoding="utf-8") == "untracked\n"
    assert len(_protokoll_zeilen(bare, 100)) == 1
    assert len(_protokoll_zeilen(bare, 902)) == 1


def test_gebundelter_push_schiebt_mehrere_zeilen_in_einem_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, klon = _bare_und_klon(tmp_path)
    monkeypatch.chdir(klon)

    bau_log.schreibe(
        "start",
        **_gueltige_felder(ticket=902, zeit="2026-11-27T04:00:01+01:00", was="Eins"),
        sofort_push=False,
    )
    bau_log.schreibe(
        "uebergabe",
        **_gueltige_felder(ticket=902, zeit="2026-11-27T04:00:02+01:00", was="Zwei"),
        sofort_push=False,
    )
    bau_log.schreibe(
        "hindernis",
        **_gueltige_felder(ticket=903, zeit="2026-11-27T04:00:03+01:00", was="Drei"),
        sofort_push=False,
    )

    # Noch nichts gepusht, alles liegt im Puffer.
    assert _protokoll_commits(bare) == []
    puffer = bau_log.protokoll_puffer_ordner()
    assert (puffer / "902.jsonl").is_file()
    assert (puffer / "903.jsonl").is_file()

    # Ein einziger Push (über spuele) bündelt alle Tickets in genau einem Commit.
    assert bau_log.spuele() is True
    assert len(_protokoll_commits(bare)) == 1
    assert [z["was"] for z in _protokoll_zeilen(bare, 902)] == ["Eins", "Zwei"]
    assert [z["was"] for z in _protokoll_zeilen(bare, 903)] == ["Drei"]
    assert list(puffer.glob("*.jsonl")) == []


# --- 4. Offline-Puffer und Wiederanlauf ohne Fehler an Aufrufer --------------


def test_offline_puffer_und_wiederanlauf_ohne_fehler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_heim: Path
) -> None:
    bare, klon = _bare_und_klon(tmp_path)
    monkeypatch.chdir(klon)
    puffer = test_heim / ".local" / "state" / "to-spawn" / "protokoll"

    # Netz weg simulieren: origin zeigt auf nicht erreichbaren Ort.
    kaputt = tmp_path / "offline-nicht-da.git"
    _git(klon, "remote", "set-url", "origin", str(kaputt))

    z1 = bau_log.schreibe("start", **_gueltige_felder(zeit="2026-11-27T04:10:01+01:00", was="Offline 1"))
    z2 = bau_log.schreibe("hindernis", **_gueltige_felder(zeit="2026-11-27T04:10:02+01:00", was="Offline 2"))
    assert z1["was"] == "Offline 1"
    assert z2["was"] == "Offline 2"

    # Kein Fehler an den Aufrufer; Zeilen liegen im lokalen Puffer, Worktree sauber.
    assert (puffer / "902.jsonl").is_file()
    assert len((puffer / "902.jsonl").read_text(encoding="utf-8").splitlines()) == 2
    assert _git(klon, "status", "--porcelain").stdout == ""
    assert _protokoll_commits(bare) == []

    # Netz zurück: nächster Aufruf schiebt alle gepufferten Zeilen + die neue gebündelt nach.
    _git(klon, "remote", "set-url", "origin", str(bare))
    bau_log.schreibe("schliessen", **_gueltige_felder(zeit="2026-11-27T04:10:03+01:00", was="Wieder online"))

    assert len(_protokoll_commits(bare)) == 1
    remote = _protokoll_zeilen(bare, 902)
    assert [z["was"] for z in remote] == ["Offline 1", "Offline 2", "Wieder online"]
    assert list(puffer.glob("*.jsonl")) == []


# --- 5. Zwei echte parallele Prozesse gegen lokales Bare-Repo ----------------


_ARBEITER_SKRIPT = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, os.environ["TEST_SKILL"])
from to_spawn import bau_log

name = sys.argv[1]
anzahl = int(sys.argv[2])
start_datei = Path(sys.argv[3])
basis_sek = int(sys.argv[4])

while not start_datei.exists():
    time.sleep(0.01)

for i in range(anzahl):
    # Verschränkte Zeitstempel beider Prozesse prüfen echte Zeit-Sortierung.
    sek = i * 2 + basis_sek
    zeit = f"2026-11-27T04:20:{sek:02d}+01:00"
    bau_log.schreibe(
        "uebergabe",
        ticket=902,
        zeit=zeit,
        wer=name,
        was=f"{name}-{i:02d}",
        session=f"sess-{name}",
        commit="283d283",
        grund="Parallel-Test",
        kontextstand=10 + i,
    )
"""


def _starte_zwei_prozesse(
    tmp_path: Path,
    klon_a: Path,
    klon_b: Path,
    heim_a: Path,
    heim_b: Path,
    anzahl: int = 8,
) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    skript = tmp_path / "arbeiter.py"
    skript.write_text(_ARBEITER_SKRIPT, encoding="utf-8")
    start_datei = tmp_path / "los.flag"

    def _env(heim: Path) -> dict[str, str]:
        heim.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "HOME": str(heim), "USERPROFILE": str(heim), "TEST_SKILL": str(SKILL)}
        for k in list(env):
            if k.startswith("TO_SPAWN_"):
                env.pop(k, None)
        return env

    proc_a = subprocess.Popen(
        [sys.executable, str(skript), "prozess_a", str(anzahl), str(start_datei), "0"],
        cwd=str(klon_a),
        env=_env(heim_a),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    proc_b = subprocess.Popen(
        [sys.executable, str(skript), "prozess_b", str(anzahl), str(start_datei), "1"],
        cwd=str(klon_b),
        env=_env(heim_b),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    start_datei.write_text("los", encoding="utf-8")

    out_a, err_a = proc_a.communicate(timeout=90)
    out_b, err_b = proc_b.communicate(timeout=90)
    return (
        subprocess.CompletedProcess(proc_a.args, proc_a.returncode, out_a, err_a),
        subprocess.CompletedProcess(proc_b.args, proc_b.returncode, out_b, err_b),
    )


def test_weg_zwei_parallele_prozesse_getrennte_klone_konflikt_retry(tmp_path: Path) -> None:
    """Zwei echte Prozesse in getrennten Klonen und Puffern pushen gleichzeitig (echte Konflikte)."""
    bare, klon_a = _bare_und_klon(tmp_path, "klon_a")
    _, klon_b = _bare_und_klon(tmp_path, "klon_b")
    anzahl = 8

    erg_a, erg_b = _starte_zwei_prozesse(
        tmp_path, klon_a, klon_b, tmp_path / "heim_a", tmp_path / "heim_b", anzahl=anzahl
    )
    assert erg_a.returncode == 0, erg_a.stdout + erg_a.stderr
    assert erg_b.returncode == 0, erg_b.stdout + erg_b.stderr

    zeilen = _protokoll_zeilen(bare, 902)
    assert len(zeilen) == anzahl * 2
    erwartet = [f"{wer}-{i:02d}" for i in range(anzahl) for wer in ("prozess_a", "prozess_b")]
    assert [z["was"] for z in zeilen] == erwartet
    zeiten = [z["zeit"] for z in zeilen]
    assert zeiten == sorted(zeiten)
    assert _git(klon_a, "status", "--porcelain").stdout == ""
    assert _git(klon_b, "status", "--porcelain").stdout == ""


def test_weg_zwei_parallele_prozesse_gemeinsamer_puffer_mit_sperre(tmp_path: Path) -> None:
    """Zwei echte Prozesse auf derselben Maschine teilen sich Sperre und Puffer-Ordner."""
    bare, klon = _bare_und_klon(tmp_path, "klon")
    gemeinsames_heim = tmp_path / "gemeinsam_heim"
    anzahl = 6

    erg_a, erg_b = _starte_zwei_prozesse(tmp_path, klon, klon, gemeinsames_heim, gemeinsames_heim, anzahl=anzahl)
    assert erg_a.returncode == 0, erg_a.stdout + erg_a.stderr
    assert erg_b.returncode == 0, erg_b.stdout + erg_b.stderr

    zeilen = _protokoll_zeilen(bare, 902)
    assert len(zeilen) == anzahl * 2
    erwartet = [f"{wer}-{i:02d}" for i in range(anzahl) for wer in ("prozess_a", "prozess_b")]
    assert [z["was"] for z in zeilen] == erwartet
    assert _git(klon, "status", "--porcelain").stdout == ""
    puffer = gemeinsames_heim / ".local" / "state" / "to-spawn" / "protokoll"
    assert list(puffer.glob("*.jsonl")) == []


# --- 6. Glossar --------------------------------------------------------------


def test_glossar_enthaelt_ereignis_protokoll() -> None:
    text = (SKILL / "docs" / "WIEDEREINSTIEG.md").read_text(encoding="utf-8")
    assert "## 6. Glossar" in text
    glossar = text.split("## 6. Glossar", 1)[1]
    assert "Ereignis-Protokoll" in glossar
    assert "protokoll" in glossar
    assert ".local/state/to-spawn/protokoll" in glossar
