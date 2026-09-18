"""#208 Setup-Wizard Teil 1: Terminal, Modell und Effort je Rolle.

Echt laufen: Dialog, Speichern (atomar), CLI als Prozess mit gepiptem stdin in
einem temporären Git-Repo. Gestellt ist nur die Programmsuche (``finde``) und
die Startprüfung, damit die Plattform-Optionen auf jedem Rechner gleich sind.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"

sys.path.insert(0, str(SKILL))

from to_spawn import config, setup

FLAGGSCHIFF = "claude-fable-5-1"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    _git(arbeit, "init")
    return arbeit


def _konfig_datei(repo: Path) -> Path:
    return repo / ".to-spawn" / "config.json"


def _schreibe_konfig(repo: Path, daten: object) -> Path:
    datei = _konfig_datei(repo)
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")
    return datei


def _lies(repo: Path) -> dict:
    return json.loads(_konfig_datei(repo).read_text(encoding="utf-8"))


def _cli(repo: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(repo),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def _alles_da(programm: str) -> str | None:
    return f"/usr/bin/{programm}"


def _nichts_da(programm: str) -> str | None:
    return None


def _startet(programm: str, pfad: str) -> str | None:
    return None


def _antworten(*zeilen: str) -> Callable[[str], str]:
    """Eingabe-Funktion wie ``input``: liefert Zeilen, danach EOFError."""
    rest = list(zeilen)

    def eingabe(frage: str) -> str:
        if not rest:
            raise EOFError
        return rest.pop(0)

    return eingabe


# --- 1. Terminal-Optionen je Plattform --------------------------------------


def test_windows_optionen_wt_standard_und_geplante(repo: Path) -> None:
    optionen = setup.terminal_optionen("win32", finde=_alles_da, pruefe_start=_startet)
    schluessel = [o.schluessel for o in optionen]
    assert schluessel[0] == "wt"
    assert "tmux" not in schluessel
    assert {"pane", "wezterm"} <= set(schluessel)
    assert "herdr" not in schluessel and "cmux" not in schluessel
    wt = optionen[0]
    assert wt.status == "bereit" and wt.waehlbar
    assert wt.vorteil and "Windows" in wt.vorteil
    for option in optionen[1:]:
        assert option.status == "geplant" and not option.waehlbar
        assert option.vorteil
    assert setup.standard_terminal({}, "win32") == "wt"


def test_linux_optionen_tmux_herdr(repo: Path) -> None:
    optionen = setup.terminal_optionen("linux", finde=_alles_da, pruefe_start=_startet)
    schluessel = [o.schluessel for o in optionen]
    assert schluessel[0] == "tmux"
    assert "herdr" in schluessel and "cmux" not in schluessel and "wt" not in schluessel
    assert optionen[0].status == "bereit"
    assert "Laptop" in optionen[0].vorteil
    assert [o.schluessel for o in optionen if o.waehlbar] == ["tmux"]
    # Konfig-Vorgabe "wt" passt auf Linux nicht → Plattform-Standard tmux.
    assert setup.standard_terminal({"terminal": "wt"}, "linux") == "tmux"


def test_macos_optionen_tmux_cmux() -> None:
    optionen = setup.terminal_optionen("darwin", finde=_alles_da, pruefe_start=_startet)
    schluessel = [o.schluessel for o in optionen]
    assert schluessel[0] == "tmux"
    assert "cmux" in schluessel and "herdr" not in schluessel
    assert {"pane", "wezterm"} <= set(schluessel)


def test_nicht_installiert_ohne_exception() -> None:
    optionen = setup.terminal_optionen("linux", finde=_nichts_da, pruefe_start=_startet)
    tmux = optionen[0]
    assert tmux.status == "nicht installiert"
    assert tmux.waehlbar
    assert tmux.grund


def test_startet_nicht_wird_nicht_installiert_mit_grund() -> None:
    def kaputt(programm: str, pfad: str) -> str | None:
        return "tmux -V endet mit Exit 1"

    optionen = setup.terminal_optionen("linux", finde=_alles_da, pruefe_start=kaputt)
    assert optionen[0].status == "nicht installiert"
    assert "Exit 1" in optionen[0].grund


def test_echte_startpruefung_faengt_fehler(tmp_path: Path) -> None:
    kaputt = tmp_path / "tmux"
    kaputt.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    kaputt.chmod(0o755)
    grund = setup.pruefe_start("tmux", str(kaputt))
    assert grund
    assert setup.pruefe_start("tmux", str(tmp_path / "gibt-es-nicht"))


# --- 2. Dialog ------------------------------------------------------------------


def test_dialog_nur_enter_nimmt_standards() -> None:
    ausgabe = io.StringIO()
    ergebnis = setup.fuehre_dialog(
        config.DEFAULTS,
        _antworten("", "", "", "", "", ""),
        ausgabe,
        plattform="linux",
        finde=_alles_da,
        pruefe_start=_startet,
    )
    assert ergebnis == {
        "terminal": "tmux",
        "modelle": {
            "ticket": "claude-opus-5",
            "ticket_leicht": "claude-sonnet-5",
            "waechter": FLAGGSCHIFF,
        },
        "effort": {"ticket": "medium", "ticket_leicht": "low", "waechter": "low"},
    }
    text = ausgabe.getvalue()
    assert "[Standard]" in text
    assert "(läuft auf dem Server weiter" in text
    assert "Fable 5.1" in text
    assert "Remote Control" in text


def test_dialog_eof_nimmt_standards_ohne_absturz() -> None:
    ergebnis = setup.fuehre_dialog(
        config.DEFAULTS,
        _antworten(),
        io.StringIO(),
        plattform="win32",
        finde=_alles_da,
        pruefe_start=_startet,
    )
    assert ergebnis["terminal"] == "wt"
    assert ergebnis["modelle"]["ticket"] == "claude-opus-5"
    assert ergebnis["effort"]["waechter"] == "low"


def test_dialog_ungueltige_eingabe_fragt_nach() -> None:
    ausgabe = io.StringIO()
    # Terminal: Enter · Ticket-Modell: "99" ungültig, dann "4" = Haiku · Rest Enter.
    ergebnis = setup.fuehre_dialog(
        config.DEFAULTS,
        _antworten("", "99", "4", "", "", "", ""),
        ausgabe,
        plattform="linux",
        finde=_alles_da,
        pruefe_start=_startet,
    )
    assert ergebnis["modelle"]["ticket"] == "claude-haiku-4-5-20251001"
    assert "ungültig" in ausgabe.getvalue().lower()


def test_dialog_drei_fehlversuche_nehmen_standard() -> None:
    ergebnis = setup.fuehre_dialog(
        config.DEFAULTS,
        _antworten("", "x", "y", "z", "", "", "", ""),
        io.StringIO(),
        plattform="linux",
        finde=_alles_da,
        pruefe_start=_startet,
    )
    assert ergebnis["modelle"]["ticket"] == "claude-opus-5"


def test_dialog_geplantes_terminal_nicht_waehlbar() -> None:
    ausgabe = io.StringIO()
    # Nummer 2 wäre Herdr (geplant) → ungültig, danach Enter.
    ergebnis = setup.fuehre_dialog(
        config.DEFAULTS,
        _antworten("2", ""),
        ausgabe,
        plattform="linux",
        finde=_alles_da,
        pruefe_start=_startet,
    )
    assert ergebnis["terminal"] == "tmux"


def test_dialog_warnt_wenn_standard_nicht_bereit() -> None:
    ausgabe = io.StringIO()
    setup.fuehre_dialog(
        config.DEFAULTS,
        _antworten(),
        ausgabe,
        plattform="linux",
        finde=_nichts_da,
        pruefe_start=_startet,
    )
    assert "Achtung" in ausgabe.getvalue()


def test_dialog_waechter_fragt_nur_effort() -> None:
    fragen: list[str] = []
    rest = ["", "", "", "", "", "max"]

    def eingabe(frage: str) -> str:
        fragen.append(frage)
        return rest.pop(0)

    konfig = config._mische(
        config.DEFAULTS, {"modelle": {"waechter": "claude-haiku-4-5-20251001"}}
    )
    ergebnis = setup.fuehre_dialog(
        konfig,
        eingabe,
        io.StringIO(),
        plattform="linux",
        finde=_alles_da,
        pruefe_start=_startet,
    )
    assert len(fragen) == 6
    assert ergebnis["modelle"]["waechter"] == FLAGGSCHIFF
    assert ergebnis["effort"]["waechter"] == "max"


# --- 3. Speichern ---------------------------------------------------------------


def test_speichere_erzwingt_flaggschiff_fuer_waechter(repo: Path) -> None:
    _schreibe_konfig(repo, {"modelle": {"waechter": "claude-sonnet-5"}})
    setup.speichere(repo, {"modelle": {"waechter": "claude-haiku-4-5-20251001"}})
    assert _lies(repo)["modelle"]["waechter"] == FLAGGSCHIFF


def test_speichere_behaelt_fremde_felder(repo: Path) -> None:
    _schreibe_konfig(
        repo,
        {
            "deploy_befehl": "make deploy",
            "eigenes_feld": {"x": 1},
            "modelle": {"ticket": "claude-opus-5", "sonder": "abc"},
        },
    )
    setup.speichere(
        repo,
        {
            "terminal": "tmux",
            "modelle": {"ticket": "claude-sonnet-5"},
            "effort": {"ticket": "high"},
        },
        plattform="linux",
    )
    daten = _lies(repo)
    assert daten["deploy_befehl"] == "make deploy"
    assert daten["eigenes_feld"] == {"x": 1}
    assert daten["modelle"] == {
        "ticket": "claude-sonnet-5",
        "sonder": "abc",
        "waechter": FLAGGSCHIFF,
    }
    assert daten["effort"] == {"ticket": "high"}
    assert daten["terminal"] == {"win32": "wt", "linux": "tmux", "darwin": "tmux"}
    # Nichts aus den Vorgaben hineingemischt.
    assert "staffel" not in daten


def test_speichere_ohne_datei_nimmt_vorgaben(repo: Path) -> None:
    datei = setup.speichere(repo, {"terminal": "tmux"}, plattform="linux")
    assert datei == _konfig_datei(repo)
    daten = _lies(repo)
    assert daten["terminal"] == config.DEFAULTS["terminal"]
    assert daten["staffel"] == config.DEFAULTS["staffel"]
    assert daten["modelle"]["waechter"] == FLAGGSCHIFF


def test_speichere_ueberschreibt_unlesbare_datei_nie(repo: Path) -> None:
    datei = _konfig_datei(repo)
    datei.parent.mkdir(parents=True)
    datei.write_text("{ kaputt", encoding="utf-8")
    with pytest.raises(setup.KonfigUnlesbar):
        setup.speichere(repo, {"terminal": "tmux"})
    assert datei.read_text(encoding="utf-8") == "{ kaputt"


def test_speichere_schreibt_atomar_ohne_reste(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _schreibe_konfig(repo, {"terminal": "wt"})
    aufrufe: list[tuple[str, str]] = []
    echt = setup.os.replace

    def merke(quelle: str, ziel: str) -> None:
        aufrufe.append((str(quelle), str(ziel)))
        echt(quelle, ziel)

    monkeypatch.setattr(setup.os, "replace", merke)
    setup.speichere(repo, {"terminal": "tmux"})
    assert len(aufrufe) == 1
    quelle, ziel = aufrufe[0]
    assert Path(quelle).parent == _konfig_datei(repo).parent
    assert Path(ziel) == _konfig_datei(repo)
    assert sorted(p.name for p in _konfig_datei(repo).parent.iterdir()) == [
        "config.json"
    ]


# --- 4. Weg-Tests: CLI als Prozess ---------------------------------------------


def test_weg_nur_enter_ergibt_standards(repo: Path) -> None:
    ergebnis = _cli(repo, "setup", "--dialog", "--plattform", "linux", stdin="\n" * 6)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    daten = _lies(repo)
    assert daten["terminal"] == config.DEFAULTS["terminal"]
    assert daten["modelle"] == config.DEFAULTS["modelle"]
    assert daten["effort"] == config.DEFAULTS["effort"]
    assert daten["staffel"] == config.DEFAULTS["staffel"]
    assert (
        "Laufende Sessions bleiben unberührt; neue Starts lesen die Werte."
        in ergebnis.stdout
    )
    assert str(_konfig_datei(repo)) in ergebnis.stdout


def test_weg_gezielte_antworten_und_fremdes_feld(repo: Path) -> None:
    _schreibe_konfig(
        repo, {"terminal": "wt", "fremd": {"bleibt": [1, 2]}, "ssh_ziel": "anderer"}
    )
    # Terminal 1 (tmux) · Ticket 3 (Sonnet 5) · Effort 3 (high) · leicht 4 (Haiku) ·
    # Effort 1 (low) · Wächter-Effort "xhigh".
    ergebnis = _cli(
        repo,
        "setup",
        "--dialog",
        "--plattform",
        "linux",
        stdin="1\n3\n3\n4\n1\nxhigh\n",
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    daten = _lies(repo)
    assert daten == {
        "terminal": {"win32": "wt", "linux": "tmux", "darwin": "tmux"},
        "fremd": {"bleibt": [1, 2]},
        "ssh_ziel": "anderer",
        "modelle": {
            "ticket": "claude-sonnet-5",
            "ticket_leicht": "claude-haiku-4-5-20251001",
            "waechter": FLAGGSCHIFF,
        },
        "effort": {"ticket": "high", "ticket_leicht": "low", "waechter": "xhigh"},
    }


def test_weg_zweiter_aufruf_mit_flags_aendert_nur_genanntes(repo: Path) -> None:
    erst = _cli(repo, "setup", "--plattform", "linux", "--standard")
    assert erst.returncode == 0, erst.stdout + erst.stderr
    vorher = _lies(repo)
    zweit = _cli(
        repo,
        "setup",
        "--plattform",
        "linux",
        "--effort-ticket",
        "max",
        "--modell-waechter",
        "claude-sonnet-5",
    )
    assert zweit.returncode == 0, zweit.stdout + zweit.stderr
    nachher = _lies(repo)
    erwartet = json.loads(json.dumps(vorher))
    erwartet["effort"]["ticket"] = "max"
    assert nachher == erwartet
    assert nachher["modelle"]["waechter"] == FLAGGSCHIFF


def test_weg_ungueltiges_flag_exit_2_nichts_geschrieben(repo: Path) -> None:
    ergebnis = _cli(repo, "setup", "--plattform", "linux", "--effort-ticket", "turbo")
    assert ergebnis.returncode == 2
    assert not _konfig_datei(repo).exists()
    ergebnis = _cli(repo, "setup", "--plattform", "linux", "--terminal", "wt")
    assert ergebnis.returncode == 2
    assert not _konfig_datei(repo).exists()


def test_weg_zeigen_schreibt_nichts(repo: Path) -> None:
    ergebnis = _cli(repo, "setup", "--plattform", "darwin", "--zeigen")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert not (repo / ".to-spawn").exists()
    text = ergebnis.stdout
    for teil in (
        "tmux",
        "cmux",
        "WezTerm",
        "Pane",
        "Opus 5",
        "claude-haiku-4-5-20251001",
        "xhigh",
    ):
        assert teil in text
    assert "Remote Control" in text


def test_weg_unlesbare_konfig_exit_ungleich_0(repo: Path) -> None:
    datei = _konfig_datei(repo)
    datei.parent.mkdir(parents=True)
    datei.write_text("[1, 2", encoding="utf-8")
    ergebnis = _cli(repo, "setup", "--plattform", "linux", "--standard")
    assert ergebnis.returncode != 0
    assert datei.read_text(encoding="utf-8") == "[1, 2"
    assert "unlesbar" in (ergebnis.stdout + ergebnis.stderr).lower()


# --- 5. Erster Start in spawn ----------------------------------------------------


def test_erster_start_mit_tty_fuehrt_dialog(repo: Path) -> None:
    ausgabe = io.StringIO()
    setup.erster_start(
        repo,
        ist_tty=True,
        eingabe=_antworten("", "2"),
        ausgabe=ausgabe,
        plattform="linux",
        finde=_alles_da,
        pruefe_start=_startet,
    )
    daten = _lies(repo)
    assert daten["modelle"]["ticket"] == "claude-opus-5"
    assert daten["terminal"]["linux"] == "tmux"


def test_erster_start_ohne_tty_legt_vorgaben_an_mit_hinweis(repo: Path) -> None:
    ausgabe = io.StringIO()
    setup.erster_start(repo, ist_tty=False, eingabe=_antworten(), ausgabe=ausgabe)
    assert _lies(repo) == json.loads(json.dumps(config.DEFAULTS))
    assert "Erstes Mal in diesem Repo" in ausgabe.getvalue()
    # Zweiter Start: Datei da → kein Hinweis, nichts verändert.
    ausgabe2 = io.StringIO()
    setup.erster_start(repo, ist_tty=True, eingabe=_antworten(), ausgabe=ausgabe2)
    assert ausgabe2.getvalue() == ""


# --- 6. Fixrunde Prüfpanel ------------------------------------------------------


def test_defaults_terminal_ist_objekt_je_plattform() -> None:
    assert config.DEFAULTS["terminal"] == {
        "win32": "wt",
        "linux": "tmux",
        "darwin": "tmux",
    }


def test_terminal_fuer_liest_objekt_und_alten_text() -> None:
    objekt = {"terminal": {"win32": "wt", "linux": "tmux", "darwin": "eigen"}}
    assert config.terminal_fuer(objekt, "darwin") == "eigen"
    assert config.terminal_fuer(objekt, "win32") == "wt"
    assert config.terminal_fuer({"terminal": "wt"}, "win32") == "wt"
    assert config.terminal_fuer({"terminal": "wt"}, "linux") == "tmux"
    assert config.terminal_fuer({"terminal": "tmux"}, "win32") == "wt"
    assert config.terminal_fuer({}, "darwin") == "tmux"
    assert config.terminal_fuer({"terminal": {"linux": 5}}, "linux") == "tmux"
    assert config.terminal_fuer(config.DEFAULTS) in ("wt", "tmux")


def test_speichere_aendert_nur_eigene_plattform(repo: Path) -> None:
    _schreibe_konfig(
        repo, {"terminal": {"win32": "wt", "linux": "tmux", "darwin": "eigen"}}
    )
    setup.speichere(repo, {"terminal": "wt"}, plattform="win32")
    assert _lies(repo)["terminal"] == {
        "win32": "wt",
        "linux": "tmux",
        "darwin": "eigen",
    }


def test_speichere_wandelt_alten_text_um(repo: Path) -> None:
    _schreibe_konfig(repo, {"terminal": "tmux"})
    setup.speichere(repo, {"terminal": "wt"}, plattform="win32")
    assert _lies(repo)["terminal"] == {"win32": "wt", "linux": "tmux", "darwin": "tmux"}


def test_weg_standard_plus_flags_flags_gewinnen(repo: Path) -> None:
    ergebnis = _cli(
        repo, "setup", "--plattform", "linux", "--standard", "--effort-ticket", "max"
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    daten = _lies(repo)
    assert daten["effort"]["ticket"] == "max"
    assert daten["effort"]["ticket_leicht"] == "low"
    assert daten["modelle"]["ticket"] == "claude-opus-5"


def test_weg_zeigen_mit_setz_flags_exit_2(repo: Path) -> None:
    ergebnis = _cli(
        repo, "setup", "--plattform", "linux", "--zeigen", "--effort-ticket", "max"
    )
    assert ergebnis.returncode == 2
    assert "--zeigen schreibt nichts" in ergebnis.stdout + ergebnis.stderr
    assert not (repo / ".to-spawn").exists()


def test_weg_felder_kein_objekt_werden_normalisiert(repo: Path) -> None:
    _schreibe_konfig(repo, {"effort": "kaputt", "modelle": 5, "fremd": 1})
    ergebnis = _cli(
        repo, "setup", "--plattform", "linux", "--modell-ticket", "claude-sonnet-5"
    )
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    daten = _lies(repo)
    assert daten["modelle"] == {"ticket": "claude-sonnet-5", "waechter": FLAGGSCHIFF}
    assert daten["effort"] == {}
    assert daten["fremd"] == 1
    assert "kein Objekt" in ergebnis.stderr
    assert "Effort medium" in ergebnis.stdout


def test_weg_ohne_terminal_ohne_flags_exit_2(repo: Path) -> None:
    ergebnis = _cli(repo, "setup", "--plattform", "linux", stdin="\n" * 6)
    assert ergebnis.returncode == 2
    assert (
        "Kein Terminal: `--standard` oder Werte als Flags angeben (Optionen: `--zeigen`)."
        in ergebnis.stdout + ergebnis.stderr
    )
    assert not (repo / ".to-spawn").exists()


def test_speichere_uebernimmt_rechte(repo: Path) -> None:
    datei = _schreibe_konfig(repo, {"terminal": "wt"})
    datei.chmod(0o640)
    setup.speichere(repo, {"effort": {"ticket": "high"}}, plattform="linux")
    assert datei.stat().st_mode & 0o777 == 0o640


def test_speichere_neue_datei_hat_644(repo: Path) -> None:
    datei = setup.speichere(repo, {"terminal": "tmux"}, plattform="linux")
    assert datei.stat().st_mode & 0o777 == 0o644


def test_weg_zeigen_unlesbare_konfig_exit_1(repo: Path) -> None:
    datei = _konfig_datei(repo)
    datei.parent.mkdir(parents=True)
    datei.write_text("{ kaputt", encoding="utf-8")
    ergebnis = _cli(repo, "setup", "--plattform", "linux", "--zeigen")
    assert ergebnis.returncode == 1
    assert "unlesbar" in (ergebnis.stdout + ergebnis.stderr).lower()


def test_weg_dialog_unlesbare_konfig_fragt_nicht(repo: Path) -> None:
    datei = _konfig_datei(repo)
    datei.parent.mkdir(parents=True)
    datei.write_text("{ kaputt", encoding="utf-8")
    ergebnis = _cli(repo, "setup", "--dialog", "--plattform", "linux", stdin="\n" * 6)
    assert ergebnis.returncode == 1
    assert "Auswahl" not in ergebnis.stdout
    assert datei.read_text(encoding="utf-8") == "{ kaputt"


def test_weg_zeigen_trennt_datei_und_standard(repo: Path) -> None:
    _schreibe_konfig(repo, {"effort": {"ticket": "high"}})
    ergebnis = _cli(repo, "setup", "--plattform", "linux", "--zeigen")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    text = ergebnis.stdout
    assert "In der Datei:" in text
    datei_teil = text.split("In der Datei:")[1].split("Standard")[0]
    assert "high" in datei_teil
    assert "fehlt" in datei_teil
