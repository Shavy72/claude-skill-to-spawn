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


def _probe(ordner: Path, konfig: dict | None = None, datei: str = "neu.py") -> Path:
    """Python-Repo mit einem Test, Tag ``base``; danach ein Commit mit ungetestetem Code.

    Der neue Code wird von keinem Test berührt: Die Auswahl findet 0 Tests (bzw. ist gar nicht
    möglich) — genau der Fall, in dem die Leiter heute auf den vollen Lauf zurückfällt. ``datei`` =
    die im Ticket-Commit neue Datei (``README.md`` = Ticket ohne Code-Datei).
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
    (ordner / datei).write_text("def neu():\n    return 1\n", encoding="utf-8")
    _git(ordner, "add", "-A")
    _git(ordner, "commit", "-qm", "ticket")
    return ordner


def _letzte_zeile(repo: Path) -> dict:
    zeilen = (repo / ".to-spawn" / "pruef" / "protokoll.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(zeilen[-1])


def _voll_lief(zeile: dict) -> bool:
    return any(stufe["name"] == "voll" and stufe["lief"] for stufe in zeile["stufen"])


#: Eingerichtetes Repo ohne eigenen Schnell-Befehl: Ticket-Prüfung geht durch die Leiter.
_NUR_KOMPLETT = {"pruefung": {"schnell": "", "komplett": "true"}}


def test_ticket_pruefung_startet_nie_den_vollen_lauf(tmp_path: Path) -> None:
    repo = _probe(tmp_path / "p", _NUR_KOMPLETT)
    # Fix-Runde 1: Code geändert, kein Test lief → Gate-Fehler statt still Grün (siehe unten).
    with pytest.raises(einreichen._Rot):
        einreichen._pruefung(repo, "base")
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


# Fix-Runde 1 (#425): „schnell“ mit geänderter Code-Datei und 0 gelaufenen Tests ist nie Grün.
_KEINE_AUSWAHL = "schnell: keine Test-Auswahl möglich"
#: Fix-Runde 2: Der Gate-Fehler nennt die Handlung, die ihn behebt.
_HANDLUNG = "→ Schlüssel pruefung.schnell in .to-spawn/config.json eintragen"


def test_schnell_mit_code_ohne_gelaufenen_test_ist_gate_fehler(tmp_path: Path) -> None:
    repo = _probe(tmp_path / "p")
    urteil = pruef.pruefen(repo, "base", "HEAD", art="schnell")
    assert urteil.exit == 2, urteil.zeile
    assert _KEINE_AUSWAHL in urteil.zeile["fehler_kurz"], urteil.zeile
    assert _HANDLUNG in urteil.zeile["fehler_kurz"], urteil.zeile
    assert not _voll_lief(urteil.zeile), f"Rückfall auf den vollen Lauf: {urteil.zeile['stufen']}"


def test_einreichen_nennt_den_grund_in_ergebnis_und_protokoll(tmp_path: Path) -> None:
    repo = _probe(tmp_path / "p", _NUR_KOMPLETT)
    with pytest.raises(einreichen._Rot) as rot:
        einreichen._pruefung(repo, "base")
    assert _KEINE_AUSWAHL in rot.value.text
    zeile = _letzte_zeile(repo)
    assert zeile["urteil"] == "gate-fehler"
    assert _KEINE_AUSWAHL in zeile["fehler_kurz"]
    assert any(_KEINE_AUSWAHL in stufe["grund"] for stufe in zeile["stufen"]), zeile["stufen"]


def test_schnell_nach_rotem_lauf_mit_code_ist_gate_fehler(tmp_path: Path) -> None:
    repo = _probe(tmp_path / "p")
    ordner = repo / ".to-spawn" / "pruef"
    ordner.mkdir(parents=True)
    (ordner / "protokoll.jsonl").write_text(json.dumps({"urteil": "rot"}) + "\n", encoding="utf-8")
    urteil = pruef.pruefen(repo, "base", "HEAD", art="schnell")
    assert urteil.exit == 2, urteil.zeile
    assert "LF" in urteil.zeile["fehler_kurz"], urteil.zeile
    assert not _voll_lief(urteil.zeile)


def test_schnell_ohne_code_datei_bleibt_gruen(tmp_path: Path) -> None:
    repo = _probe(tmp_path / "p", datei="README.md")
    urteil = pruef.pruefen(repo, "base", "HEAD", art="schnell")
    assert urteil.exit == 0, urteil.zeile
    assert not _voll_lief(urteil.zeile)


# Übergangsregel (#425, Zusatz Leit-Session): Repo ohne eingerichtete Gruppe ``pruefung`` läuft wie
# vor Teil B; laufende Specs in solchen Repos brechen nicht am neuen Gate-Fehler.
_NICHT_EINGERICHTET = "Prüfung nicht eingerichtet (pruefung.schnell fehlt)"


def test_ohne_gruppe_pruefung_alter_ablauf_mit_warnung(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    repo = _probe(tmp_path / "p")
    with caplog.at_level(logging.WARNING):
        assert einreichen._pruefung(repo, "base") == ""
    zeile = _letzte_zeile(repo)
    assert zeile["urteil"] == "grün", zeile
    assert "art" not in zeile, "nicht eingerichtet: Leiter ohne art wie vor Teil B"
    assert _NICHT_EINGERICHTET in zeile.get("warnung", ""), zeile
    treffer = [r for r in caplog.records if _NICHT_EINGERICHTET in r.getMessage()]
    assert len(treffer) == 1, [r.getMessage() for r in caplog.records]


def test_nur_komplett_ohne_auswahl_mit_code_ist_gate_fehler(tmp_path: Path) -> None:
    repo = _probe(tmp_path / "p", _NUR_KOMPLETT)
    with pytest.raises(einreichen._Rot) as rot:
        einreichen._pruefung(repo, "base")
    assert "Exit 2" in rot.value.text and _KEINE_AUSWAHL in rot.value.text
    zeile = _letzte_zeile(repo)
    assert zeile["art"] == "schnell" and "warnung" not in zeile
    assert not _voll_lief(zeile)


# Fix-Runde 2 (#425): Unbekannte Änderung (git diff gescheitert, ``pfade is None``) zählt wie eine
# Code-Änderung — nie still Grün ohne gelaufenen Test.
def test_regel_unbekannte_aenderung_ist_gate_fehler() -> None:
    from to_spawn.pruef import regeln

    grund = regeln.schnell_ohne_test(None, "python", "R0: Änderung unklar")
    assert _KEINE_AUSWAHL in grund and _HANDLUNG in grund, grund


def test_schnell_mit_unbekannter_aenderung_ist_gate_fehler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn.pruef import sofort

    repo = _probe(tmp_path / "p", datei="README.md")
    # Rand-Attrappe nur für den git-Fehlschlag: geänderte Dateien unbekannt.
    monkeypatch.setattr(sofort, "geaendert", lambda _repo, _basis: None)
    urteil = pruef.pruefen(repo, "base", "HEAD", art="schnell")
    assert urteil.exit == 2, urteil.zeile
    assert _KEINE_AUSWAHL in urteil.zeile["fehler_kurz"], urteil.zeile
    assert not _voll_lief(urteil.zeile), f"Rückfall auf den vollen Lauf: {urteil.zeile['stufen']}"
