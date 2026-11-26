"""#425 Teil C2: Komplett-Prüfung im Takt (alle auf Staging) und als Tür vor Live.

Echt laufen: der capo-Takt (CLI ``skripte/capo.py``) im Wegwerf-Repo aus ``test_waechter_213``,
der Unterbefehl ``to_spawn.py komplett`` und ein echter kleiner Prüf-Befehl, der jede Ausführung
in eine Zähldatei schreibt. Gestellt ist nur ``gh`` (Ersatz aus #213).

Ausroll-Frage: ein Repo ohne Gruppe ``pruefung`` (laufende Spec auf dem Bau-Server) verhält sich
im Takt exakt wie vorher — kein Lauf, keine Komplett-Zeile, SPEC FERTIG wie bisher.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus test_waechter_213 und wird als Parameter genannt.
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import attrappe
from test_komplett_je_spec_425 import _befehl, _laeufe, _rot
from test_probe_vorlagen_426 import ANLEGEN, ROT, _abruf, _freie_ports
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _capo,
    _commit,
    _gh_setzen,
    _git,
    _iso,
    _konfig,
    _text,
    welt,
)

from to_spawn import bash_wahl, capo, config, einreichen, komplett_lauf, prozessbaum
from to_spawn.pruefplan import Moment

log = logging.getLogger(__name__)
SKILL = Path(__file__).resolve().parents[1]
STAGING = {"url": "http://127.0.0.1:8001/"}
#: Vorgabe #425 Abnahme: schnelle Prüfung je Ticket höchstens 3 Minuten.
TICKET_PRUEFUNG_GRENZE_S = 180.0


def _eingerichtet(welt: dict[str, Path], zaehl: Path, *, staging: bool = True) -> None:
    _konfig(
        welt,
        pruefung={"schnell": "true", "komplett": _befehl(zaehl)},
        staging=STAGING if staging else {"ohne": True},
    )


def _alle_zu(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: B (#902)", {})
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())


def _zaehl(welt: dict[str, Path]) -> Path:
    return welt["tmp"] / "zaehl.txt"


# --- (a) Staging-Moment im Takt -----------------------------------------------------------------


def test_takt_alle_auf_staging_laeuft_einmal_gruen(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl)
    _alle_zu(welt)
    erster = _capo(welt)
    zweiter = _capo(welt)
    assert "Komplett-Prüfung grün" in erster.stdout, _text(erster)
    assert "SPEC FERTIG" in erster.stdout, _text(erster)
    assert _laeufe(zaehl) == 1, _text(zweiter)
    assert "Komplett-Prüfung" not in zweiter.stdout, _text(zweiter)


def test_takt_rot_haelt_spec_fertig_zurueck(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl)
    _alle_zu(welt)
    _rot(zaehl)
    rot = _capo(welt)
    assert "Komplett-Prüfung rot" in rot.stdout, _text(rot)
    assert "Handlung" in rot.stdout, _text(rot)
    assert "SPEC FERTIG" not in rot.stdout, _text(rot)
    assert rot.returncode == 1, _text(rot)
    zaehl.with_name("exit.txt").unlink()
    # #455: rot je Kopf läuft im Takt nicht neu — nach dem Fix von Hand anstoßen.
    gemerkt = _capo(welt)
    assert "schon rot" in gemerkt.stdout and "SPEC FERTIG" not in gemerkt.stdout, _text(gemerkt)
    assert _laeufe(zaehl) == 1
    assert _tuer(welt["repo"], "--moment", "alle-auf-staging").returncode == 0
    gruen = _capo(welt)
    assert "SPEC FERTIG" in gruen.stdout, _text(gruen)
    assert _laeufe(zaehl) == 2


def test_takt_mit_offenem_ticket_kein_lauf(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl)
    ergebnis = _capo(welt)
    assert _laeufe(zaehl) == 0, _text(ergebnis)
    assert "Komplett-Prüfung" not in ergebnis.stdout


def test_takt_ohne_staging_kein_lauf_spec_fertig(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl, staging=False)
    _alle_zu(welt)
    ergebnis = _capo(welt)
    assert _laeufe(zaehl) == 0, _text(ergebnis)
    assert "SPEC FERTIG" in ergebnis.stdout, _text(ergebnis)
    assert "Komplett-Prüfung" not in ergebnis.stdout


def test_ausroll_repo_ohne_einrichtung_takt_wie_vorher(welt: dict[str, Path]) -> None:
    """Laufende Spec ohne ``pruefung``: kein Lauf, keine neue Zeile, kein Fehler."""
    _alle_zu(welt)
    ergebnis = _capo(welt)
    assert "SPEC FERTIG" in ergebnis.stdout, _text(ergebnis)
    assert "Komplett" not in _text(ergebnis), _text(ergebnis)
    assert "FEHLER" not in ergebnis.stdout, _text(ergebnis)
    assert not (welt["repo"] / ".to-spawn" / "pruef" / f"komplett-{SPEC}.json").exists()


def test_ausnahme_bricht_takt_nicht_ab(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _eingerichtet(welt, _zaehl(welt))
    _alle_zu(welt)

    def kaputt(*_a: object, **_k: object) -> komplett_lauf.Ergebnis:
        raise RuntimeError("kaputt")

    monkeypatch.setattr(komplett_lauf, "komplett_einmal", kaputt)
    erg = capo.tick(
        welt["repo"],
        int(SPEC),
        "test/wegwerf",
        config.lade(welt["repo"]),
        wt_basis=str(welt["wt"]),
    )
    text = "\n".join(erg.zeilen)
    assert "FEHLER: Komplett-Prüfung" in text and "kaputt" in text, text
    assert not erg.fertig
    assert erg.exit_code == 1


def test_takt_laeuft_schon_meldet_und_haelt_fertig(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    _eingerichtet(welt, _zaehl(welt))
    _alle_zu(welt)
    belegt = komplett_lauf.Ergebnis(False, True, "Komplett-Prüfung läuft schon", None, "", laeuft=True)
    monkeypatch.setattr(komplett_lauf, "komplett_einmal", lambda *_a, **_k: belegt)
    erg = capo.tick(
        welt["repo"],
        int(SPEC),
        "test/wegwerf",
        config.lade(welt["repo"]),
        wt_basis=str(welt["wt"]),
    )
    text = "\n".join(erg.zeilen)
    assert "läuft schon" in text and "FEHLER" not in text, text
    assert not erg.fertig


# --- (b) Tür vor Live ---------------------------------------------------------------------------


def _tuer(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "komplett", SPEC, *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "TO_SPAWN_REPO": str(repo)},
        timeout=60,
        check=False,
    )


def test_tuer_vor_live_gruen_rot_und_einmal(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl, staging=False)
    _rot(zaehl)
    rot = _tuer(welt["repo"], "--moment", "vor-live")
    assert rot.returncode == 1, _text(rot)
    assert "rot" in rot.stdout and len(rot.stdout.strip().splitlines()) == 1, _text(rot)
    zaehl.with_name("exit.txt").unlink()
    gruen = _tuer(welt["repo"], "--moment", "vor-live")
    nochmal = _tuer(welt["repo"], "--moment", "vor-live")
    assert gruen.returncode == 0 and "grün" in gruen.stdout, _text(gruen)
    assert nochmal.returncode == 0 and "schon grün" in nochmal.stdout, _text(nochmal)
    assert _laeufe(zaehl) == 2


def test_tuer_unbekannter_moment_exit_2(welt: dict[str, Path]) -> None:
    erg = _tuer(welt["repo"], "--moment", "irgendwann")
    assert erg.returncode == 2, _text(erg)


def test_tuer_alle_auf_staging_und_nicht_eingerichtet(welt: dict[str, Path]) -> None:
    ohne = _tuer(welt["repo"], "--moment", "vor-live")
    assert ohne.returncode == 1 and "pruefung.komplett" in ohne.stdout, _text(ohne)
    zaehl = _zaehl(welt)
    _eingerichtet(welt, zaehl)
    erg = _tuer(welt["repo"], "--moment", "alle-auf-staging")
    assert erg.returncode == 0 and "grün" in erg.stdout, _text(erg)
    assert _laeufe(zaehl) == 1


# --- (d) Sperre gegen Doppellauf ----------------------------------------------------------------


def _langsamer_lauf(klon: Path, zaehl: Path) -> subprocess.Popen[str]:
    """Zweiter Prozess: Komplett-Prüfung, deren Befehl nach dem Zählen 5 s schläft."""
    code = (
        "import sys, pathlib, json\n"
        "from to_spawn import komplett_lauf, config\n"
        "from to_spawn.pruefplan import Moment\n"
        "klon = pathlib.Path(sys.argv[1])\n"
        "erg = komplett_lauf.komplett_einmal(klon, int(sys.argv[2]), Moment.VOR_LIVE, config.lade(klon))\n"
        "print(json.dumps([erg.gelaufen, erg.ok]))\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code, str(klon), SPEC],
        cwd=str(SKILL),
        stdout=subprocess.PIPE,
        text=True,
        **prozessbaum.ohne_fenster(),
    )


def _warte_auf(zaehl: Path) -> None:
    ende = time.monotonic() + 20
    while _laeufe(zaehl) == 0:
        assert time.monotonic() < ende, "Prüf-Befehl startete nicht"
        time.sleep(0.05)


def _langsam_eingerichtet(welt: dict[str, Path], zaehl: Path) -> None:
    befehl = _befehl(zaehl) + " && sleep 5"
    _konfig(welt, pruefung={"schnell": "true", "komplett": befehl}, staging={"ohne": True})


def test_sperre_zweiter_aufruf_waehrend_lauf(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _langsam_eingerichtet(welt, zaehl)
    erster = _langsamer_lauf(welt["repo"], zaehl)
    try:
        _warte_auf(zaehl)
        zweit = komplett_lauf.komplett_einmal(welt["repo"], int(SPEC), Moment.VOR_LIVE, config.lade(welt["repo"]))
        assert (zweit.gelaufen, zweit.ok, zweit.laeuft) == (False, True, True)
        assert "läuft schon" in zweit.grund
    finally:
        aus, _ = erster.communicate(timeout=30)
    assert json.loads(aus) == [True, True]
    assert _laeufe(zaehl) == 1


def _hart_beenden(prozess: subprocess.Popen[str]) -> None:
    """Beendet ``prozess`` samt Nachfahren hart über ``prozessbaum`` (Linux + Windows, #647).

    Unter Windows fehlt ``signal.SIGKILL``; jedes Signal beendet dort per ``TerminateProcess``.
    ``prozessbaum.baum`` liefert die Wurzel zuerst — so stirbt der Halter der Sperre hart,
    bevor Kindprozesse (``bash``/``sleep``) enden, und auch ein Windows-venv-Kindprozess
    samt Nachfahren wird mit beendet.
    """
    hart = getattr(signal, "SIGKILL", signal.SIGTERM)
    pids = prozessbaum.baum(prozess.pid)
    prozessbaum.beenden(pids, ((hart, 5.0),), warten_auf=pids[1:])
    prozess.wait(timeout=10)
    if prozess.stdout is not None:
        prozess.stdout.close()


def test_sperre_abgestuerzter_lauf_sperrt_nicht(welt: dict[str, Path]) -> None:
    zaehl = _zaehl(welt)
    _langsam_eingerichtet(welt, zaehl)
    erster = _langsamer_lauf(welt["repo"], zaehl)
    try:
        _warte_auf(zaehl)
    finally:
        _hart_beenden(erster)
    _konfig(
        welt,
        pruefung={"schnell": "true", "komplett": _befehl(zaehl)},
        staging={"ohne": True},
    )
    erg = komplett_lauf.komplett_einmal(welt["repo"], int(SPEC), Moment.VOR_LIVE, config.lade(welt["repo"]))
    assert (erg.gelaufen, erg.ok) == (True, True), erg.grund
    assert _laeufe(zaehl) == 2


def test_sperre_abgestuerzter_lauf_ohne_sigkill(welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """#647: Unter Windows fehlt ``signal.SIGKILL`` — Abbruch läuft trotzdem über ``prozessbaum``."""
    monkeypatch.delattr(signal, "SIGKILL", raising=False)
    test_sperre_abgestuerzter_lauf_sperrt_nicht(welt)


# --- (e) Gemeinsamer Abnahme-Beleg mit Messung für beide Repo-Arten (#425) ----------------------


def _python_im_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stellt sicher, dass ``python`` in bash auf genau diesen Interpreter zeigt (Linux + Windows)."""
    py_ordner = Path(sys.executable).parent
    py_name = "python.exe" if sys.platform == "win32" else "python"
    if (py_ordner / py_name).is_file():
        ziel_ordner = py_ordner
    else:
        ziel_ordner = tmp_path / "py_bin"
        attrappe(
            ziel_ordner,
            "python",
            f"""
            import os, subprocess, sys
            if os.name == "nt":
                raise SystemExit(subprocess.run([{sys.executable!r}, *sys.argv[1:]], check=False).returncode)
            os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
            """,
        )
    monkeypatch.setenv("PATH", f"{ziel_ordner}{os.pathsep}{os.environ.get('PATH', '')}")


def _probe_repo_mit_origin(tmp_path: Path, variante: str) -> Path:
    """Legt eine echte Probe-Vorlage per ``probe/anlegen.sh`` an und bindet ein Bare-Origin (``main``) an."""
    try:
        bash_wahl.bash()
    except FileNotFoundError as fehler:
        pytest.skip(f"anlegen.sh braucht bash: {fehler}")
    app_port, staging_port, live_port = _freie_ports(3)
    repo = tmp_path / f"probe-{variante}"
    anlegen = bash_wahl.lauf(
        [
            str(ANLEGEN),
            str(repo),
            variante,
            "--app-port",
            str(app_port),
            "--staging-port",
            str(staging_port),
            "--live-port",
            str(live_port),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert anlegen.returncode == 0, _text(anlegen)
    fern = tmp_path / f"fern-{variante}.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(fern)],
        check=True,
        capture_output=True,
        **prozessbaum.fensterlos(),
    )
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "remote", "add", "origin", str(fern))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def _ticket_commit(repo: Path, betreff: str, dateien: dict[str, str]) -> str:
    """Dateien schreiben und lokal committen (noch nicht gepusht — Stand für ``einreichen._pruefung``)."""
    for name, inhalt in dateien.items():
        pfad = repo / name
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(inhalt, encoding="utf-8")
        _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", betreff)
    return _git(repo, "rev-parse", "HEAD")


def _live_aufruf(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "live", SPEC],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "TO_SPAWN_REPO": str(repo)},
        timeout=90,
        check=False,
        **prozessbaum.fensterlos(),
    )


def _protokoll_zeilen(repo: Path) -> list[dict[str, object]]:
    datei = repo / ".to-spawn" / "pruef" / "protokoll.jsonl"
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]


@pytest.mark.parametrize("variante", ("mit-staging", "ohne-staging"))
def test_abnahme_beide_repo_arten_mit_messung(
    welt: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    variante: str,
) -> None:
    """#425 Abnahme: beide Probe-Varianten end-to-end mit Messung (Ticket ≤ 3 min, Komplett genau 1×).

    Echt laufen: ``probe/anlegen.sh``, ``einreichen._pruefung`` für zwei Tickets (echte Code- +
    Test-Änderungen), Bau-Ticket-Sperre, roter Werkzeug-Test (darf nichts aufhalten), Staging-Start
    + HTTP-Probe (bei ``mit-staging``), ``capo``-Takt, ``to_spawn.py live`` mit echtem Live-Start +
    HTTP-Probe und Aufräumen. Gestellt ist nur ``gh`` (``gh_stub_213``) und ``tmux``.
    """
    _python_im_path(welt["tmp"], monkeypatch)
    repo = _probe_repo_mit_origin(welt["tmp"], variante)
    konfig = config.lade(repo)
    assert config.hat_staging(konfig) is (variante == "mit-staging")
    assert (repo / ROT).is_file(), "absichtlich roter Werkzeug-Test muss im Probe-Repo liegen"

    probe_welt = {**welt, "repo": repo}
    _gh_setzen(welt, "901", state="open")
    _gh_setzen(welt, "902", state="open")

    ticket_dauern: list[float] = []
    staging_gestartet = False
    live_gestartet = False
    try:
        # 1. Zwei Tickets bauen: je Ticket nur die schnelle Prüfung (≤ 3 min), noch keine Komplett-Prüfung.
        tickets = [
            (
                "901",
                "feat: Helfer eins (#901)",
                {
                    "app/helfer_eins.py": "def verdopple(n: int) -> int:\n    return n * 2\n",
                    "tests/test_helfer_eins.py": (
                        "import unittest\n"
                        "from app.helfer_eins import verdopple\n\n"
                        "class HelferEins(unittest.TestCase):\n"
                        "    def test_verdopple(self) -> None:\n"
                        "        self.assertEqual(verdopple(3), 6)\n"
                    ),
                    "docs/verify-hard/901_beleg.md": "Ticket 901 grün.\n",
                },
            ),
            (
                "902",
                "feat: Helfer zwei (#902)",
                {
                    "app/helfer_zwei.py": "def halbiere(n: int) -> int:\n    return n // 2\n",
                    "tests/test_helfer_zwei.py": (
                        "import unittest\n"
                        "from app.helfer_zwei import halbiere\n\n"
                        "class HelferZwei(unittest.TestCase):\n"
                        "    def test_halbiere(self) -> None:\n"
                        "        self.assertEqual(halbiere(8), 4)\n"
                    ),
                    "docs/verify-hard/902_beleg.md": "Ticket 902 grün.\n",
                },
            ),
        ]
        for nr, betreff, dateien in tickets:
            _ticket_commit(repo, betreff, dateien)
            start = time.monotonic()
            hinweis = einreichen._pruefung(repo, "origin/main")
            dauer = time.monotonic() - start
            assert hinweis == ""
            assert dauer <= TICKET_PRUEFUNG_GRENZE_S, f"Ticket #{nr} ({variante}): {dauer:.2f} s > 180 s"
            ticket_dauern.append(dauer)
            _git(repo, "push", "-q", "origin", "HEAD:main")
            _gh_setzen(welt, nr, state="closed", closed_at=_iso(timedelta(minutes=20)))
            if nr == "901":
                # Zwischenstand: Ticket 902 noch offen → keine Komplett-Prüfung, kein SPEC FERTIG.
                zwischen = _capo(probe_welt)
                assert "Komplett-Prüfung" not in zwischen.stdout, _text(zwischen)
                assert "SPEC FERTIG" not in zwischen.stdout, _text(zwischen)
                assert komplett_lauf.lies_merker(repo, int(SPEC)) is None

        # Bau-Ticket-Sperre: innerhalb eines Tickets startet nie die Komplett-Prüfung.
        with monkeypatch.context() as ticket_env:
            ticket_env.setenv(komplett_lauf.BAU_TICKET_VARIABLE, "902")
            gesperrt = komplett_lauf.komplett_einmal(repo, int(SPEC), Moment.VOR_LIVE, konfig)
            assert (gesperrt.gelaufen, gesperrt.ok) == (False, False)

        # 2. Alle Tickets fertig: Staging-App (falls vorhanden) startet Komplett-Prüfung im Takt;
        #    Repo ohne Staging prüft erst an der Live-Tür.
        if variante == "mit-staging":
            staging_lauf = bash_wahl.lauf(
                ["-c", konfig["staging_start"]],
                cwd=repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            staging_gestartet = True
            assert staging_lauf.returncode == 0, _text(staging_lauf)
            staging_antwort = _abruf(konfig["staging"]["url"])
            assert staging_antwort is not None and "(staging)" in staging_antwort, staging_antwort

            takt = _capo(probe_welt)
            assert "Komplett-Prüfung grün" in takt.stdout, _text(takt)
            assert "SPEC FERTIG" in takt.stdout, _text(takt)
            merker = komplett_lauf.lies_merker(repo, int(SPEC))
            assert merker is not None and merker.gruen and merker.moment == Moment.ALLE_AUF_STAGING.value

            # Zweiter Takt startet keine weitere Komplett-Prüfung.
            zweiter_takt = _capo(probe_welt)
            assert "Komplett-Prüfung" not in zweiter_takt.stdout, _text(zweiter_takt)

            # Live-Tür übernimmt den grünen Takt-Merker (kein zweiter Komplett-Lauf) und rollt live aus.
            live = _live_aufruf(repo)
            live_gestartet = True
            assert live.returncode == 0, _text(live)
            assert "schon grün" in live.stdout, _text(live)
        else:
            takt = _capo(probe_welt)
            assert "SPEC FERTIG" in takt.stdout, _text(takt)
            assert "Komplett-Prüfung" not in takt.stdout, _text(takt)
            assert komplett_lauf.lies_merker(repo, int(SPEC)) is None

            # Abschluss-Übersicht aus dem Fertig-Takt einchecken (wie Rundschau/Entscheidungen vor Live).
            entscheidungen = capo.entscheidungs_datei(repo, int(SPEC))
            assert entscheidungen.is_file()
            _git(repo, "add", str(entscheidungen.relative_to(repo)))
            _git(repo, "commit", "-q", "-m", f"docs: Entscheidungen Spec #{SPEC}")
            _git(repo, "push", "-q", "origin", "HEAD:main")

            # Live-Tür führt die Komplett-Prüfung genau einmal vor Live aus und rollt dann live aus.
            live = _live_aufruf(repo)
            live_gestartet = True
            assert live.returncode == 0, _text(live)
            assert "Komplett-Prüfung grün" in live.stdout, _text(live)
            merker = komplett_lauf.lies_merker(repo, int(SPEC))
            assert merker is not None and merker.gruen and merker.moment == Moment.VOR_LIVE.value

            # Erneute Abfrage vor Live prüft nicht noch einmal.
            nochmal = _tuer(repo, "--moment", "vor-live")
            assert nochmal.returncode == 0 and "schon grün" in nochmal.stdout, _text(nochmal)

        live_antwort = _abruf(konfig["live"]["url"])
        assert live_antwort is not None and "(live)" in live_antwort, live_antwort

        # 3. Gemeinsamer Mess- und Zähl-Beleg aus dem echten Prüf-Protokoll.
        zeilen = _protokoll_zeilen(repo)
        schnell = [z for z in zeilen if z.get("art") == "schnell"]
        komplett = [z for z in zeilen if z.get("art") == "komplett"]
        assert len(schnell) == 2, zeilen
        assert all(z.get("urteil") == "grün" for z in schnell), schnell
        assert all(float(z["gesamt_s"]) <= TICKET_PRUEFUNG_GRENZE_S for z in schnell), schnell  # type: ignore[arg-type]
        assert len(komplett) == 1, zeilen
        assert komplett[0].get("urteil") == "grün", komplett
        erwarteter_moment = Moment.ALLE_AUF_STAGING.value if variante == "mit-staging" else Moment.VOR_LIVE.value
        assert komplett[0].get("moment") == erwarteter_moment, komplett
        assert float(komplett[0]["gesamt_s"]) >= 0, komplett  # type: ignore[arg-type]
        log.info(
            "Abnahme #425 (%s): Tickets schnell=%s s (Protokoll %s s), komplett=1x in %.2f s (%s)",
            variante,
            [round(d, 2) for d in ticket_dauern],
            [round(float(z["gesamt_s"]), 2) for z in schnell],  # type: ignore[arg-type]
            float(komplett[0]["gesamt_s"]),  # type: ignore[arg-type]
            erwarteter_moment,
        )
    finally:
        if live_gestartet:
            weg_live = bash_wahl.lauf(
                ["-c", konfig["live"]["aufraeumen"]],
                cwd=repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            assert weg_live.returncode == 0, _text(weg_live)
            assert _abruf(konfig["live"]["url"]) is None
        if staging_gestartet:
            weg_staging = bash_wahl.lauf(
                ["-c", "python skripte/ausrollen.py staging --aufraeumen"],
                cwd=repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            assert weg_staging.returncode == 0, _text(weg_staging)
            assert _abruf(konfig["staging"]["url"]) is None
