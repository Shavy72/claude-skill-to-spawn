"""Tests für Etappe 3b-6 (#106): Vorprüfung vor der Schlange (Stufe 0–3), 2. Gate-Platz & M13.

Prüft echt:
- Rot-Beweis: Ticket mit Python-Syntax-Fehler wird in Stufe 1 abgewiesen, ohne einen Gate-Platz zu
  belegen (weder mit noch ohne ruff im PATH, und ohne auf einen belegten Gate-Platz zu warten).
- Stufen 0–3: Cache-Treffer, nur Doku, betroffene Tests (grün/rot) sowie Rückfall auf Gate (Stufe 4
  läuft nie in der Vorprüfung).
- Fremder Stand (``ziel != HEAD``): Prüfung im isolierten Temp-Worktree ohne den Hauptbaum zu ändern.
- 2. Gate-Platz nur bei Bremse <= 1,5 (aus #105) sowie aktivem RAM-, Last- (< 12) und Session-Schutz.
- 10 gleichzeitige Tickets durch den Gate-Pool: höchstens 2 parallele Plätze, Syntax-Fehler-Tickets
  belegen 0 Plätze.
- Ehrliche M13-Bewertung im Mess- und Auswerteskript (Erreichbarkeit bleibt bei gerissenen Schwellen
  explizit als unbelegt/nicht erreicht markiert).
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

CLI = SKILL / "to_spawn.py"


def _git(ordner: Path, *args: str) -> str:
    erg = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return erg.stdout.strip()


def _probe_repo(ordner: Path, *, mit_doku_konfig: bool = False) -> Path:
    """Frisches Python-Repo mit Code, Tests und Tag ``base``."""
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (ordner / "rechnen.py").write_text(
        "def addiere(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    (ordner / "tests").mkdir(exist_ok=True)
    (ordner / "tests" / "test_rechnen.py").write_text(
        textwrap.dedent(
            """\
            import pathlib
            import sys

            sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
            from rechnen import addiere


            def test_addiere() -> None:
                assert addiere(2, 3) == 5
            """
        ),
        encoding="utf-8",
    )
    (ordner / "README.md").write_text("# Probe\n", encoding="utf-8")
    if mit_doku_konfig:
        (ordner / ".to-spawn").mkdir(exist_ok=True)
        (ordner / ".to-spawn" / "config.json").write_text(
            json.dumps({"pruefen": {"doku_pfade": ["README.md", "docs/**"]}}, indent=2) + "\n",
            encoding="utf-8",
        )
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "base")
    _git(ordner, "tag", "base")
    return ordner


def _commit(ordner: Path, datei: str, inhalt: str, nachricht: str) -> str:
    ziel = ordner / datei
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(inhalt, encoding="utf-8")
    _git(ordner, "add", datei)
    _git(ordner, "commit", "-qm", nachricht)
    return _git(ordner, "rev-parse", "HEAD")


# --- 1. Rot-Beweis: Syntax-Fehler-Ticket abgewiesen ohne Gate-Platz --------------------------


def test_rot_beweis_syntax_fehler_ticket_abgewiesen_ohne_gate_platz(tmp_path: Path) -> None:
    """Rot-Beweis (#106): Syntax-Fehler wird in Stufe 1 abgewiesen und belegt keinen Gate-Platz."""
    from to_spawn.pruef import vorpruefung

    repo = _probe_repo(tmp_path / "repo")
    _commit(repo, "rechnen.py", "def addiere(a, b)\n    return a + b\n", "syntax kaputt")

    pool = vorpruefung.GatePool(
        tmp_path / "sperren",
        bremse_faktor=1.34,
        frei_mib_fn=lambda: 8192,
        last_1m_fn=lambda: 1.0,
        bau_sessions_fn=lambda: 0,
    )
    # Selbst wenn Gate-Platz 1 bereits belegt ist, darf das Syntax-Fehler-Ticket sofort abgewiesen
    # werden, ohne einen zweiten Platz zu belegen oder auf den ersten zu warten.
    with pool.belege_platz(warten_s=0.0) as erster_platz:
        assert erster_platz == 1
        with pool.einlass(repo, "base", "HEAD", warten_s=0.0) as einlass:
            assert einlass.zugelassen is False
            assert einlass.gate_platz_belegt is False
            assert einlass.platz_nr is None
            assert einlass.vorpruefung.exit == 1
            assert einlass.vorpruefung.zeile["urteil"] == "rot"
            assert einlass.vorpruefung.zeile["art"] == "vorpruefung"
            assert not any(s["name"] == "voll" and s["lief"] for s in einlass.vorpruefung.zeile["stufen"])

    assert pool.abgewiesen_vorpruefung == 1
    assert pool.belegungen == 1  # nur der manuell gehaltene erste Platz, nie das Syntax-Ticket


def test_rot_beweis_syntax_fehler_auch_ohne_ruff_abgewiesen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Auch wenn ruff im PATH fehlt, fängt das Python-Syntax-Netz den Syntax-Fehler vor dem Gate."""
    from to_spawn.pruef import sofort, vorpruefung

    repo = _probe_repo(tmp_path / "repo")
    _commit(repo, "rechnen.py", "def kaputt(:\n    pass\n", "syntax ohne ruff")
    monkeypatch.setattr(sofort, "_finde_werkzeug", lambda _repo, _name: None)

    pool = vorpruefung.GatePool(
        tmp_path / "sperren",
        bremse_faktor=1.34,
        frei_mib_fn=lambda: 8192,
        last_1m_fn=lambda: 1.0,
        bau_sessions_fn=lambda: 0,
    )
    with pool.einlass(repo, "base", "HEAD", warten_s=0.0) as einlass:
        assert einlass.zugelassen is False
        assert einlass.gate_platz_belegt is False
        assert einlass.vorpruefung.exit == 1
        assert (
            "Syntax" in einlass.vorpruefung.zeile["fehler_kurz"]
            or "syntax" in einlass.vorpruefung.zeile["fehler_kurz"].lower()
        )
    assert pool.belegungen == 0


# --- 2. Stufen 0–3 in der Vorprüfung (nie Stufe 4 voll) --------------------------------------


def test_vorpruefung_stufe_0_cache_und_stufe_2_doku(tmp_path: Path) -> None:
    """Stufe 2 (nur Doku) lässt das Ticket ohne Tests durch; Stufe 0 (Cache) trifft beim 2. Lauf."""
    from to_spawn.pruef import vorpruefung

    repo = _probe_repo(tmp_path / "repo", mit_doku_konfig=True)
    _commit(repo, "README.md", "# Probe\n\nNeue Zeile.\n", "nur doku")

    erst = vorpruefung.vorpruefen(repo, "base", "HEAD")
    assert erst.exit == 0
    assert erst.zugelassen is True
    assert erst.weiter_an_gate is False
    assert any(s["name"] == "doku" and s["grund"] == "nur Doku" for s in erst.zeile["stufen"])

    zweit = vorpruefung.vorpruefen(repo, "base", "HEAD")
    assert zweit.exit == 0
    assert zweit.zugelassen is True
    assert zweit.zeile["cache_treffer"] is True


def test_vorpruefung_stufe_3_auswahl_gruen_und_rot(tmp_path: Path) -> None:
    """Kleine Code-Änderung läuft nur betroffene Tests; Rot weist vor dem Gate ab."""
    from to_spawn.pruef import vorpruefung

    repo = _probe_repo(tmp_path / "repo")
    _commit(repo, "rechnen.py", "def addiere(a: int, b: int) -> int:\n    return a - b\n", "logik kaputt")

    pool = vorpruefung.GatePool(
        tmp_path / "sperren",
        bremse_faktor=1.34,
        frei_mib_fn=lambda: 8192,
        last_1m_fn=lambda: 1.0,
        bau_sessions_fn=lambda: 0,
    )
    with pool.einlass(repo, "base", "HEAD", warten_s=0.0) as rot_einlass:
        assert rot_einlass.zugelassen is False
        assert rot_einlass.gate_platz_belegt is False
        assert rot_einlass.vorpruefung.exit == 1
        assert any(s["name"] == "auswahl" and s["lief"] for s in rot_einlass.vorpruefung.zeile["stufen"])

    _commit(repo, "rechnen.py", "def addiere(a: int, b: int) -> int:\n    return a + b + 0\n", "logik heil")
    with pool.einlass(repo, "base", "HEAD", warten_s=0.0) as gruen_einlass:
        assert gruen_einlass.zugelassen is True
        assert gruen_einlass.gate_platz_belegt is True
        assert gruen_einlass.platz_nr == 1
        assert gruen_einlass.vorpruefung.exit == 0
        assert gruen_einlass.vorpruefung.weiter_an_gate is True


def test_vorpruefung_r3_delegiert_stufe_4_an_gate_ohne_volllauf(tmp_path: Path) -> None:
    """Bei R3 (z. B. requirements.txt) läuft Stufe 4 nie in der Vorprüfung — Übergabe ans Gate."""
    from to_spawn.pruef import vorpruefung

    repo = _probe_repo(tmp_path / "repo")
    _commit(repo, "requirements.txt", "pytest>=9.0\n", "abhaengigkeit")

    urteil = vorpruefung.vorpruefen(repo, "base", "HEAD")
    assert urteil.exit == 0
    assert urteil.zugelassen is True
    assert urteil.weiter_an_gate is True
    assert not any(s["name"] == "voll" and s["lief"] for s in urteil.zeile["stufen"])
    assert any("Gate" in s["grund"] for s in urteil.zeile["stufen"])


def test_vorpruefung_fremder_stand_im_isolierten_worktree(tmp_path: Path) -> None:
    """Fremder Commit (``ziel != HEAD``) wird im isolierten Temp-Worktree geprüft und aufgeräumt."""
    from to_spawn.pruef import vorpruefung

    repo = _probe_repo(tmp_path / "repo")
    _git(repo, "checkout", "-qb", "ticket/42-kaputt")
    kaputt_sha = _commit(repo, "rechnen.py", "def addiere(a, b:\n    return a + b\n", "kaputter zweig")
    _git(repo, "checkout", "-q", "base")
    kopf_vorher = _git(repo, "rev-parse", "HEAD")

    urteil = vorpruefung.vorpruefen(repo, "base", kaputt_sha)
    assert urteil.exit == 1
    assert urteil.zugelassen is False
    assert _git(repo, "rev-parse", "HEAD") == kopf_vorher
    # Keine verwaisten Worktrees liegen geblieben
    wt_liste = _git(repo, "worktree", "list", "--porcelain")
    assert wt_liste.count("worktree ") == 1


# --- 3. 2. Gate-Platz nur bei Bremse <= 1,5 + RAM-/Last-/Session-Schutz ----------------------


@pytest.mark.parametrize(
    ("bremse", "frei_mib", "last_1m", "sessions", "erwartet_plaetze"),
    [
        # Gemessener Wert #105 (1,34× / 1,29×) bei ruhiger Maschine -> 2 Plätze
        (1.34, 8192, 1.5, 0, 2),
        (1.29, 4096, 5.9, 2, 2),
        (1.50, 6000, 2.0, 1, 2),
        # Bremse > 1,5 oder unbelegt -> höchstens 1 Platz
        (1.51, 8192, 1.0, 0, 1),
        (3.00, 8192, 1.0, 0, 1),
        (None, 8192, 1.0, 0, 1),
        # Last/RAM/Sessions zu hoch für 2. Platz -> zurückgestuft auf 1 Platz
        (1.34, 8192, 6.0, 0, 1),
        (1.34, 3900, 1.0, 0, 1),
        (1.34, 8192, 1.0, 3, 1),
        # Harter RAM- oder Last-Schutz (< 2048 MiB oder Last >= 12) -> 0 Plätze
        (1.34, 1500, 1.0, 0, 0),
        (1.34, 8192, 12.0, 0, 0),
    ],
)
def test_erlaubte_gate_plaetze_regeln(
    bremse: float | None,
    frei_mib: int,
    last_1m: float,
    sessions: int,
    erwartet_plaetze: int,
) -> None:
    from to_spawn.pruef import vorpruefung

    kap = vorpruefung.erlaubte_gate_plaetze(
        bremse_faktor=bremse,
        frei_mib=frei_mib,
        last_1m=last_1m,
        bau_sessions=sessions,
    )
    assert kap.plaetze == erwartet_plaetze
    assert kap.grund


def test_zehn_tickets_gleichzeitig_ohne_ueberbuchung_und_syntax_ohne_platz(tmp_path: Path) -> None:
    """10 gleichzeitige Tickets: Syntax-Tickets abgewiesen (0 Plätze), gültige <= 2 parallel."""
    from to_spawn.pruef import vorpruefung

    repo = _probe_repo(tmp_path / "repo", mit_doku_konfig=True)
    shas: list[tuple[str, bool]] = []
    for nr in range(10):
        _git(repo, "checkout", "-q", "base")
        _git(repo, "checkout", "-qb", f"ticket/{100 + nr}-test")
        if nr in (1, 6):
            sha = _commit(repo, "rechnen.py", f"def kaputt_{nr}(:\n    pass\n", f"syntax {nr}")
            shas.append((sha, False))
        elif nr in (2, 7):
            sha = _commit(repo, "README.md", f"# Probe\n\nTicket {nr}\n", f"doku {nr}")
            shas.append((sha, True))
        else:
            sha = _commit(
                repo,
                "rechnen.py",
                f"def addiere(a: int, b: int) -> int:\n    return a + b + {nr} - {nr}\n",
                f"code {nr}",
            )
            shas.append((sha, True))
    _git(repo, "checkout", "-q", "base")

    pool = vorpruefung.GatePool(
        tmp_path / "sperren",
        bremse_faktor=1.34,
        frei_mib_fn=lambda: 8192,
        last_1m_fn=lambda: 1.0,
        bau_sessions_fn=lambda: 0,
    )
    aktiv = 0
    max_aktiv = 0
    lock = threading.Lock()
    ergebnisse: list[tuple[bool, bool, int | None]] = []

    def arbeiter(sha: str, erwartet_ok: bool) -> None:
        nonlocal aktiv, max_aktiv
        with pool.einlass(repo, "base", sha, warten_s=15.0) as einlass:
            if einlass.gate_platz_belegt:
                with lock:
                    aktiv += 1
                    max_aktiv = max(max_aktiv, aktiv)
                time.sleep(0.05)
                with lock:
                    aktiv -= 1
            with lock:
                ergebnisse.append((einlass.zugelassen, einlass.gate_platz_belegt, einlass.platz_nr))
            assert einlass.zugelassen is erwartet_ok

    threads = [threading.Thread(target=arbeiter, args=(sha, ok)) for sha, ok in shas]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive()

    assert len(ergebnisse) == 10
    assert sum(1 for ok, _, _ in ergebnisse if not ok) == 2
    assert sum(1 for ok, belegt, _ in ergebnisse if not ok and belegt) == 0
    assert max_aktiv <= 2
    assert pool.abgewiesen_vorpruefung == 2


# --- 4. CLI-Schalter --vorpruefung -----------------------------------------------------------


def test_cli_pruefen_mit_vorpruefung_flag(tmp_path: Path) -> None:
    repo = _probe_repo(tmp_path / "repo")
    _commit(repo, "rechnen.py", "def addiere(a, b)\n    return a + b\n", "cli syntax rot")

    erg = subprocess.run(
        [sys.executable, str(CLI), "pruefen", "base", "HEAD", "--vorpruefung"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert erg.returncode == 1, erg.stdout + erg.stderr
    zeilen = erg.stdout.strip().splitlines()
    assert len(zeilen) == 1
    daten = json.loads(zeilen[0])
    assert daten["urteil"] == "rot"
    assert daten["art"] == "vorpruefung"


# --- 5. Ehrliche M13-Auswertung im Mess-Skript -----------------------------------------------


def test_m13_bewertung_bleibt_ehrlich_bei_gerissenen_schwellen() -> None:
    from messung import m13_vorpruefung_messung as m13

    urteil = m13.bewerte_m13(
        median_s=180.0,
        p90_s=2100.0,
        ram_riss=False,
        stress_ueber_15m_prozent=70.0,
        last_spitze=9.5,
        erreichbarkeit_belegt=False,
    )
    assert urteil.erfuellt is False
    assert urteil.median_ok is False
    assert urteil.p90_ok is False
    assert urteil.warte_ok is False
    assert urteil.ram_ok is True
    assert urteil.last_ok is True
    assert "unbelegt" in urteil.hinweis.lower()

    gruen = m13.bewerte_m13(
        median_s=95.0,
        p90_s=1100.0,
        ram_riss=False,
        stress_ueber_15m_prozent=0.0,
        last_spitze=8.0,
        erreichbarkeit_belegt=True,
    )
    assert gruen.erfuellt is True
