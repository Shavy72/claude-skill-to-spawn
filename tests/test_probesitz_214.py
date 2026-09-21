"""Probesitz-Checkliste (#214): 7 Punkte, Zustand je Maschine, Ausgabe im Setup.

Unit-Tests ohne Netz und ohne ``claude``: alle Unterprozesse laufen über einen
gestellten ``laeufer`` (protokolliert argv, liefert feste Antworten). Der echte
Weg (``claude auth status``, ``srt``) steht in ``test_probesitz_214_weg.py``.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
SKRIPTE = SKILL / "skripte"
HILFEN = Path(__file__).resolve().parent / "hilfen"
sys.path.insert(0, str(SKILL))

import context_mode_attrappe  # noqa: E402  (tests/hilfen, Pfad setzt conftest)
from to_spawn import bau_log, config, melder, probesitz, setup  # noqa: E402

GH_WRAPPER = """#!/bin/sh
exec {py} {stub} "$@"
"""


# --- Bausteine -----------------------------------------------------------------


class Laeufer:
    """Gestellter Unterprozess-Läufer: ``antworten[Präfix] = (exit, stdout)``."""

    def __init__(self, antworten: dict[str, tuple[int, str]] | None = None) -> None:
        self.antworten = antworten or {}
        self.aufrufe: list[list[str]] = []

    def __call__(
        self, argv: list[str], **_: Any
    ) -> subprocess.CompletedProcess[str]:
        self.aufrufe.append(list(argv))
        text = " ".join(str(teil) for teil in argv)
        for praefix, (code, stdout) in self.antworten.items():
            if praefix in text:
                return subprocess.CompletedProcess(argv, code, stdout, "")
        return subprocess.CompletedProcess(argv, 0, "", "")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    _git(arbeit, "init", "-q")
    (arbeit / ".to-spawn").mkdir()
    (arbeit / ".to-spawn" / "config.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("TO_SPAWN_WAECHTER_ORDNER", str(tmp_path / "zustand"))
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.chdir(arbeit)
    return arbeit


def _konfig(repo: Path) -> dict[str, Any]:
    """Tiefe Kopie: ``config.lade`` teilt verschachtelte Vorgaben mit ``DEFAULTS``."""
    return copy.deepcopy(config.lade(repo))


def _which_alles(programm: str) -> str | None:
    return f"/usr/bin/{programm}"


def _which_nichts(_programm: str) -> str | None:
    return None


# --- Vertrag -------------------------------------------------------------------


def test_vertrag_punkte_und_konstanten() -> None:
    assert len(probesitz.PUNKTE) == 7
    assert probesitz.PUNKTE[0] == "Login trägt"
    assert probesitz.PUNKTE[6] == "Mail „Probesitz grün“"
    assert probesitz.ZUSTAND_UNTERORDNER == "probesitz"
    assert "probesitz_gruen" in melder.IMMER
    assert "staging" in config.DEFAULTS and config.DEFAULTS["staging"]["url"] == ""
    prompt = probesitz.WEGWERF_PROMPT.format(ticket=999)
    assert "probesitz-999" in prompt and "Runde 1" in prompt and "Runde 2" in prompt
    assert "`" not in prompt
    assert "Staffel: weiter" in prompt


# --- Zustand -------------------------------------------------------------------


def test_zustand_laden_speichern(repo: Path, tmp_path: Path) -> None:
    assert probesitz.lade_zustand(repo) == {}
    erg = probesitz.Ergebnis(1, probesitz.PUNKTE[0], True, "loggedIn true", beleg="max")
    zustand = probesitz.eintragen(probesitz.lade_zustand(repo), erg)
    probesitz.speichere_zustand(repo, zustand)
    datei = probesitz.zustand_datei(repo)
    assert datei.parent.name == "probesitz"
    assert datei.parent.parent == tmp_path / "zustand"
    neu = probesitz.lade_zustand(repo)
    assert neu["punkte"]["1"]["ok"] is True
    assert neu["punkte"]["1"]["grund"] == "loggedIn true"
    assert neu["punkte"]["1"]["ts"]
    assert neu["letzter_lauf"]


def test_zeige_zeilen_gruen_rot_offen() -> None:
    zustand: dict[str, Any] = {"punkte": {}, "letzter_lauf": "2026-09-21T10:00:00"}
    zustand = probesitz.eintragen(
        zustand, probesitz.Ergebnis(1, probesitz.PUNKTE[0], True, "loggedIn true")
    )
    zustand = probesitz.eintragen(
        zustand,
        probesitz.Ergebnis(
            3, probesitz.PUNKTE[2], False, "staging.url fehlt", "staging.url eintragen"
        ),
    )
    text = probesitz.zeige(zustand)
    zeilen = text.splitlines()
    assert zeilen[0] == "Probesitz (7 Punkte):"
    assert "  ✓ 1 Login trägt — loggedIn true" in zeilen
    assert (
        "  ✗ 3 Playwright gegen Staging — staging.url fehlt · fehlt noch: staging.url eintragen"
        in zeilen
    )
    assert "  · 4 Sandbox sperrt außerhalb Worktree — noch nie geprüft" in zeilen
    assert zeilen[-1].startswith("  Stand: ")


def test_zeige_ohne_lauf() -> None:
    text = probesitz.zeige({})
    assert text.splitlines()[-1] == "  Noch nie gelaufen: python to_spawn.py probesitz"
    assert text.count("noch nie geprüft") == 7


# --- Punkt 1 Login -------------------------------------------------------------


def test_p1_login_ok() -> None:
    laeufer = Laeufer({"auth status": (0, json.dumps({"loggedIn": True, "subscriptionType": "max"}))})
    erg = probesitz.pruefe_login(laeufer=laeufer, which=_which_alles)
    assert erg.ok and erg.nummer == 1
    assert "max" in erg.grund
    assert laeufer.aufrufe[0][1:] == ["auth", "status"]


def test_p1_login_fehlt() -> None:
    laeufer = Laeufer({"auth status": (0, json.dumps({"loggedIn": False}))})
    erg = probesitz.pruefe_login(laeufer=laeufer, which=_which_alles)
    assert not erg.ok
    assert erg.fehlt_noch == "claude login (Abo-Konto)"


def test_p1_claude_nicht_gefunden() -> None:
    laeufer = Laeufer()
    erg = probesitz.pruefe_login(laeufer=laeufer, which=_which_nichts)
    assert not erg.ok
    assert erg.grund == "claude nicht gefunden"
    assert "claude login" in erg.fehlt_noch
    assert laeufer.aufrufe == []


# --- Punkt 3 Staging -----------------------------------------------------------


def test_p3_staging_url_fehlt(repo: Path) -> None:
    erg = probesitz.pruefe_staging(config.lade(repo), repo)
    assert not erg.ok and erg.grund == "staging.url fehlt"
    assert "#211" in erg.fehlt_noch


def test_p3_playwright_fehlt(repo: Path) -> None:
    def kaputt(_url: str, _nutzer: str, _passwort: str) -> tuple[int, str]:
        raise ImportError("No module named 'playwright'")

    konfig = _konfig(repo)
    konfig["staging"]["url"] = "https://staging.example"
    erg = probesitz.pruefe_staging(konfig, repo, seite_laden=kaputt)
    assert not erg.ok
    assert "Playwright für diesen Python-Interpreter" in erg.fehlt_noch
    assert ".venv/bin/python -m pip install playwright" in erg.fehlt_noch


def test_p3_login_seite_200_mit_zugangsdatei(repo: Path, tmp_path: Path) -> None:
    zugang = tmp_path / "zugang.txt"
    zugang.write_text(
        "adresse=https://aus-datei.example\nnutzer=staging\npasswort=geheim123\n",
        encoding="utf-8",
    )
    gesehen: dict[str, str] = {}

    def laden(url: str, nutzer: str, passwort: str) -> tuple[int, str]:
        gesehen.update(url=url, nutzer=nutzer, passwort=passwort)
        return 200, "Anmelden"

    konfig = _konfig(repo)
    konfig["staging"]["zugang_datei"] = str(zugang)  # url leer → adresse aus der Datei
    erg = probesitz.pruefe_staging(konfig, repo, seite_laden=laden)
    assert erg.ok, erg
    assert gesehen == {
        "url": "https://aus-datei.example/login",
        "nutzer": "staging",
        "passwort": "geheim123",
    }
    assert "200" in erg.grund and "Anmelden" in erg.beleg
    assert "geheim123" not in erg.grund + erg.beleg + erg.fehlt_noch


def test_p3_status_ungleich_200(repo: Path) -> None:
    konfig = _konfig(repo)
    konfig["staging"]["url"] = "https://staging.example/"
    erg = probesitz.pruefe_staging(
        konfig, repo, seite_laden=lambda *_: (502, "Bad Gateway")
    )
    assert not erg.ok and "502" in erg.grund


def test_lies_zugang_beide_formate() -> None:
    assert probesitz.lies_zugang("user:pa:ss\n") == ("", "user", "pa:ss")
    assert probesitz.lies_zugang(
        "# Kommentar\nadresse = https://x\nnutzer=u\npasswort=p\n"
    ) == ("https://x", "u", "p")
    assert probesitz.lies_zugang("") == ("", "", "")


# --- Punkt 4 Sandbox -----------------------------------------------------------


def test_p4_srt_fehlt(repo: Path) -> None:
    laeufer = Laeufer()
    erg = probesitz.pruefe_sandbox(
        config.lade(repo), repo, laeufer=laeufer, which=_which_nichts
    )
    assert not erg.ok
    assert "srt + bubblewrap installieren" in erg.fehlt_noch
    assert laeufer.aufrufe == []


def _sperr_laeufer(aussen_exit: int, aussen_anlegen: bool) -> Laeufer:
    class Sperre(Laeufer):
        def __call__(self, argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
            self.aufrufe.append(list(argv))
            ziel = Path(argv[-1])
            if "aussen" in ziel.parent.name:
                if aussen_anlegen:
                    ziel.touch()
                return subprocess.CompletedProcess(argv, aussen_exit, "", "Read-only file system")
            ziel.touch()
            return subprocess.CompletedProcess(argv, 0, "", "")

    return Sperre()


def test_p4_sperrt_aussen(repo: Path, tmp_path: Path) -> None:
    heim = tmp_path / "heim"
    heim.mkdir()
    laeufer = _sperr_laeufer(aussen_exit=1, aussen_anlegen=False)
    erg = probesitz.pruefe_sandbox(
        config.lade(repo), repo, laeufer=laeufer, which=_which_alles, home=heim
    )
    assert erg.ok, erg
    assert "modus aus" in erg.grund  # Hinweis, kein ✗
    assert len(laeufer.aufrufe) == 2
    assert laeufer.aufrufe[0][0] == "/usr/bin/srt" and "--settings" in laeufer.aufrufe[0]
    assert "--" in laeufer.aufrufe[0]
    assert not list(heim.glob(".probesitz-*")), "Temp-Ordner werden aufgeräumt"


def test_p4_aussen_schreibbar_ist_rot(repo: Path, tmp_path: Path) -> None:
    heim = tmp_path / "heim"
    heim.mkdir()
    laeufer = _sperr_laeufer(aussen_exit=0, aussen_anlegen=True)
    erg = probesitz.pruefe_sandbox(
        config.lade(repo), repo, laeufer=laeufer, which=_which_alles, home=heim
    )
    assert not erg.ok
    assert "außerhalb" in erg.grund


def test_p4_innen_gesperrt_ist_rot(repo: Path, tmp_path: Path) -> None:
    heim = tmp_path / "heim"
    heim.mkdir()

    class AllesZu(Laeufer):
        def __call__(self, argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
            self.aufrufe.append(list(argv))
            return subprocess.CompletedProcess(argv, 1, "", "nein")

    erg = probesitz.pruefe_sandbox(
        config.lade(repo), repo, laeufer=AllesZu(), which=_which_alles, home=heim
    )
    assert not erg.ok and "innerhalb" in erg.grund


# --- Punkte 5/6 aus dem Bau-Log ---------------------------------------------------


def _log_schreiben(repo: Path, ticket: str, zeilen: list[dict[str, Any]]) -> None:
    datei = bau_log.lauf_pfad(repo, ticket)
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(
        "".join(json.dumps(z, ensure_ascii=False) + "\n" for z in zeilen), encoding="utf-8"
    )


def test_p5_p6_aus_bau_log_gruen(repo: Path) -> None:
    _log_schreiben(
        repo,
        "999",
        [
            {"ts": "2026-09-21T10:00:00+02:00", "typ": "session_start", "ticket": "999", "session_id": "a1", "staffel": 1},
            {"ts": "2026-09-21T10:01:00+02:00", "typ": "session_ende", "ticket": "999", "session_id": "a1", "staffel": 1, "modell": "claude-sonnet-5", "tokens": {"gesamt": 12345}},
            {"ts": "2026-09-21T10:01:01+02:00", "typ": "handoff", "ticket": "999", "session_id": "a1", "staffel": 1},
            {"ts": "2026-09-21T10:02:00+02:00", "typ": "session_start", "ticket": "999", "session_id": "b2", "staffel": 2},
            {"ts": "2026-09-21T10:03:00+02:00", "typ": "session_ende", "ticket": "999", "session_id": "b2", "staffel": 2, "tokens": {"gesamt": 500}},
        ],
    )
    p5, p6 = probesitz.werte_bau_log(bau_log.lese(repo, "999"))
    assert p5.ok and p5.nummer == 5
    assert "claude-sonnet-5" in p5.beleg and "12345" in p5.beleg
    assert p6.ok and p6.nummer == 6
    assert "a1" in p6.beleg and "b2" in p6.beleg


def test_p5_p6_ohne_token_und_ohne_staffel(repo: Path) -> None:
    _log_schreiben(
        repo,
        "998",
        [
            {"ts": "2026-09-21T10:00:00+02:00", "typ": "session_start", "ticket": "998", "session_id": "a1", "staffel": 1},
            {"ts": "2026-09-21T10:01:00+02:00", "typ": "session_ende", "ticket": "998", "session_id": "a1", "staffel": 1, "tokens": {"gesamt": 0}},
        ],
    )
    p5, p6 = probesitz.werte_bau_log(bau_log.lese(repo, "998"))
    assert not p5.ok and p5.fehlt_noch
    assert not p6.ok and "Staffel" in p6.fehlt_noch


def test_p5_p6_leeres_log() -> None:
    p5, p6 = probesitz.werte_bau_log([])
    assert not p5.ok and not p6.ok
    assert "keine Bau-Log-Zeile" in p5.grund


# --- Wegwerf-Lauf: Aufräumen auch bei Fehler ---------------------------------------


def test_wegwerf_gh_scheitert_haengt_5_und_6_an_2(repo: Path) -> None:
    laeufer = Laeufer({"issue create": (1, "")})
    ergebnisse = probesitz.wegwerf_lauf(
        repo,
        config.lade(repo),
        modell="claude-sonnet-5",
        laeufer=laeufer,
        which=_which_alles,
        session_starter=lambda *a, **k: pytest.fail("darf ohne Ticket nicht starten"),
    )
    assert set(ergebnisse) == {2, 5, 6}
    assert ergebnisse[2].grund == "gh nicht angemeldet" and ergebnisse[2].fehlt_noch == "gh auth login"
    assert ergebnisse[5].grund == "hängt an Punkt 2"
    assert ergebnisse[6].grund == "hängt an Punkt 2"


def test_wegwerf_raeumt_auch_bei_fehler_auf(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path / "wt"))
    laeufer = Laeufer({"issue create": (0, "https://github.com/x/y/issues/4711\n")})

    def kaputt(*_: Any, **__: Any) -> Any:
        raise RuntimeError("Session explodiert")

    ergebnisse = probesitz.wegwerf_lauf(
        repo,
        config.lade(repo),
        modell="claude-sonnet-5",
        laeufer=laeufer,
        which=_which_alles,
        session_starter=kaputt,
        worktree_anlegen=lambda *_: None,
    )
    assert not ergebnisse[2].ok and "Session explodiert" in ergebnisse[2].grund
    texte = [" ".join(a) for a in laeufer.aufrufe]
    assert any("issue close 4711" in t and "Probesitz beendet" in t for t in texte), texte
    assert any("push origin --delete probesitz-4711" in t for t in texte), texte
    assert any("worktree remove --force" in t and "wt-4711" in t for t in texte), texte
    assert any("branch -D" in t and "probesitz-4711" in t for t in texte), texte


# --- Punkt 7 Mail --------------------------------------------------------------


def _alles_gruen_bis_6() -> dict[str, Any]:
    zustand: dict[str, Any] = {}
    for nummer in range(1, 7):
        zustand = probesitz.eintragen(
            zustand, probesitz.Ergebnis(nummer, probesitz.PUNKTE[nummer - 1], True, "ok")
        )
    return zustand


def test_p7_blockiert_bei_offenen_punkten(repo: Path) -> None:
    zustand = probesitz.eintragen({}, probesitz.Ergebnis(1, probesitz.PUNKTE[0], True, "ok"))
    konfig = _konfig(repo)
    konfig["mail"]["befehl"] = ["true"]
    erg = probesitz.pruefe_mail(
        repo, konfig, zustand, melden=lambda *a, **k: pytest.fail("keine Mail bei Rot")
    )
    assert not erg.ok
    assert erg.grund.startswith("Punkte offen: 2, 3, 4, 5, 6")
    assert erg.fehlt_noch == "erst die offenen Punkte"


def test_p7_mail_befehl_fehlt(repo: Path) -> None:
    erg = probesitz.pruefe_mail(repo, config.lade(repo), _alles_gruen_bis_6())
    assert not erg.ok and "mail.befehl" in erg.fehlt_noch


def test_p7_ruft_melder_mit_probesitz_gruen(repo: Path) -> None:
    gesehen: dict[str, Any] = {}

    def melden(repo_: Path, art: str, betreff: str, text: str, schluessel: str, **k: Any) -> bool:
        gesehen.update(repo=repo_, art=art, betreff=betreff, text=text, schluessel=schluessel)
        return True

    konfig = _konfig(repo)
    konfig["mail"]["befehl"] = ["true"]
    erg = probesitz.pruefe_mail(repo, konfig, _alles_gruen_bis_6(), melden=melden)
    assert erg.ok, erg
    assert gesehen["art"] == "probesitz_gruen"
    assert gesehen["betreff"] == "Probesitz grün"
    assert gesehen["schluessel"].startswith("probesitz:")
    assert gesehen["text"].count("\n") >= 6  # 7 Zeilen Zusammenfassung


def test_p7_melder_gibt_false(repo: Path) -> None:
    konfig = _konfig(repo)
    konfig["mail"]["befehl"] = ["false"]
    erg = probesitz.pruefe_mail(repo, konfig, _alles_gruen_bis_6(), melden=lambda *a, **k: False)
    assert not erg.ok and "mail.befehl" in erg.grund


def test_p7_darf_bei_nur_kritisch_raus(repo: Path) -> None:
    konfig = _konfig(repo)
    assert konfig["mail"]["nur_kritisch"] is True
    assert melder.darf_raus("probesitz_gruen", konfig)


# --- laufen: Auswahl + Zustand nach jedem Punkt --------------------------------------


def test_laufen_einzelne_punkte_speichern_zustand(repo: Path) -> None:
    laeufer = Laeufer({"auth status": (0, json.dumps({"loggedIn": True}))})
    ergebnisse = probesitz.laufen(
        repo, config.lade(repo), punkte={1, 3}, laeufer=laeufer, which=_which_alles
    )
    assert [e.nummer for e in ergebnisse] == [1, 3]
    zustand = probesitz.lade_zustand(repo)
    assert zustand["punkte"]["1"]["ok"] is True
    assert zustand["punkte"]["3"]["ok"] is False
    assert "2" not in zustand["punkte"]


# --- Setup zeigt den Block ------------------------------------------------------


def test_setup_zeige_enthaelt_probesitz(repo: Path) -> None:
    text = setup.zeige(config.lade(repo), repo / config.KONFIG_PFAD, "linux")
    assert "Probesitz (7 Punkte):" in text
    assert "Noch nie gelaufen: python to_spawn.py probesitz" in text


# --- CLI -----------------------------------------------------------------------


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def test_cli_probesitz_zeigen_ohne_zustand_exit_1(repo: Path) -> None:
    ergebnis = _cli(repo, "probesitz", "--zeigen")
    assert ergebnis.returncode == 1, ergebnis.stdout + ergebnis.stderr
    assert "Probesitz (7 Punkte):" in ergebnis.stdout
    assert ergebnis.stdout.count("noch nie geprüft") == 7


def test_cli_setup_zeigen_enthaelt_probesitz(repo: Path) -> None:
    ergebnis = _cli(repo, "setup", "--zeigen", "--plattform", "linux")
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert "Probesitz (7 Punkte):" in ergebnis.stdout


# --- bau.py --probesitz ----------------------------------------------------------


@pytest.fixture()
def heim(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    context_mode_attrappe.plugin_anlegen(home)
    binaer = home / "bin"
    binaer.mkdir()
    (binaer / "gh").write_text(
        GH_WRAPPER.format(py=sys.executable, stub=HILFEN / "gh_stub.py"), encoding="utf-8"
    )
    (binaer / "gh").chmod(0o755)
    (binaer / "claude").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (binaer / "claude").chmod(0o755)
    return home


def _bau(repo: Path, heim: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_REPO"}
    env["HOME"] = str(heim)
    env["PATH"] = f"{heim / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["TMPDIR"] = str(heim / "tmp")
    (heim / "tmp").mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(SKRIPTE / "bau.py"), "999", "--probesitz", *args],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def test_bau_probesitz_print_prompt_runde_1(repo: Path, heim: Path) -> None:
    ergebnis = _bau(repo, heim, "--print-prompt")
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert "Runde 1" in ergebnis.stdout and "probesitz-999" in ergebnis.stdout


def test_bau_probesitz_dry_run_zeigt_p(repo: Path, heim: Path) -> None:
    ergebnis = _bau(repo, heim, "--dry-run")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    befehl = ergebnis.stdout.split("Befehl:", 1)[1]
    teile = befehl.split()
    assert teile[1] == "-p", befehl
    assert "--strict-mcp-config" in teile
