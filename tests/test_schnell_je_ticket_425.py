"""Je Ticket nur die schnelle Prüfung (#425 Teil B, Vorgabe des Auftraggebers 24.11.).

Echt laufen: git, pytest und die Prüf-Tür in frischen Probe-Repos; der Ticket-Weg geht durch
``einreichen._pruefung`` (dieselbe Stelle, die ``to_spawn.py einreichen`` nutzt). Keine Attrappe.

Geprüft wird: Beim Einreichen eines Tickets läuft nie der volle Testlauf (Stufe 4), auch wenn die
Auswahl nichts Sicheres liefert — das steht dann im Protokoll. Ist ``pruefung.schnell``
eingerichtet, läuft genau dieser Befehl. Art „komplett“ läuft voll. Die Dauer steht im Protokoll.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from to_spawn import einreichen, pruef


def _git(ordner: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=ordner,
        check=True,
        capture_output=True,
    )


def _probe(ordner: Path, konfig: dict | None = None) -> Path:
    """Python-Repo mit einem Test, Tag ``base``; danach ein Commit mit ungetestetem Code.

    Der neue Code wird von keinem Test berührt: Die Auswahl findet 0 Tests (bzw. ist gar nicht
    möglich) — genau der Fall, in dem die Leiter heute auf den vollen Lauf zurückfällt.
    """
    ordner.mkdir()
    (ordner / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n", encoding="utf-8")
    (ordner / "rechnen.py").write_text("def addiere(a, b):\n    return a + b\n", encoding="utf-8")
    (ordner / "tests").mkdir()
    (ordner / "tests" / "test_rechnen.py").write_text(
        textwrap.dedent("""\
            import sys, pathlib
            sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
            from rechnen import addiere

            def test_addiere():
                assert addiere(1, 2) == 3
            """),
        encoding="utf-8",
    )
    if konfig is not None:
        (ordner / ".to-spawn").mkdir()
        (ordner / ".to-spawn" / "config.json").write_text(json.dumps(konfig), encoding="utf-8")
    _git(ordner, "init", "-q")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "base")
    _git(ordner, "tag", "base")
    (ordner / "neu.py").write_text("def neu():\n    return 1\n", encoding="utf-8")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "ticket")
    return ordner


def _letzte_zeile(repo: Path) -> dict:
    zeilen = (repo / ".to-spawn" / "pruef" / "protokoll.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(zeilen[-1])


def _voll_lief(zeile: dict) -> bool:
    return any(stufe["name"] == "voll" and stufe["lief"] for stufe in zeile["stufen"])


def test_ticket_pruefung_startet_nie_den_vollen_lauf(tmp_path: Path) -> None:
    repo = _probe(tmp_path / "p")
    assert einreichen._pruefung(repo, "base") == ""
    zeile = _letzte_zeile(repo)
    assert not _voll_lief(zeile), f"Ticket-Prüfung lief voll: {zeile['stufen']}"
    assert zeile.get("art") == "schnell"
    ausgelassen = [s for s in zeile["stufen"] if not s["lief"] and "schnell" in s["grund"]]
    assert ausgelassen, f"kein Protokoll-Eintrag, warum nichts lief: {zeile['stufen']}"


def test_eingerichteter_schnell_befehl_laeuft_genau_so(tmp_path: Path) -> None:
    marke = tmp_path / "marke.txt"
    befehl = f"{Path(sys.executable).as_posix()} -c \"open(r'{marke.as_posix()}', 'a').write('x')\""
    repo = _probe(tmp_path / "p", {"pruefung": {"schnell": befehl}})
    urteil = pruef.pruefen(repo, "base", "HEAD", art="schnell")
    assert urteil.exit == 0, urteil.zeile
    assert marke.read_text(encoding="utf-8") == "x"
    assert [s["befehl"] for s in urteil.zeile["stufen"]] == [befehl]
    assert not _voll_lief(urteil.zeile)


def test_roter_schnell_befehl_ist_rot(tmp_path: Path) -> None:
    befehl = f'{Path(sys.executable).as_posix()} -c "raise SystemExit(3)"'
    repo = _probe(tmp_path / "p", {"pruefung": {"schnell": befehl}})
    urteil = pruef.pruefen(repo, "base", "HEAD", art="schnell")
    assert urteil.exit == 1, urteil.zeile


def test_komplett_laeuft_voll(tmp_path: Path) -> None:
    repo = _probe(tmp_path / "p")
    urteil = pruef.pruefen(repo, "base", "HEAD", art="komplett")
    assert urteil.exit == 0, urteil.zeile
    assert _voll_lief(urteil.zeile)
    assert urteil.zeile["art"] == "komplett"


def test_dauer_der_schnellen_pruefung_steht_in_protokoll_und_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo = _probe(tmp_path / "p")
    with caplog.at_level(logging.INFO, logger="to_spawn.pruef"):
        urteil = pruef.pruefen(repo, "base", "HEAD", art="schnell")
    assert urteil.zeile["gesamt_s"] >= 0
    assert _letzte_zeile(repo)["art"] == "schnell"
    assert any("Dauer der schnellen Prüfung" in r.getMessage() for r in caplog.records)
