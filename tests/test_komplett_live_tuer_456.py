"""#456 (Folge #425 Teil C): Live-Schritt an die Komplett-Prüfung gebunden, Probelauf nur lesend.

Echt laufen: Wegwerf-Git-Repos (``welt`` aus ``test_waechter_213`` bzw. ``klon`` aus
``test_komplett_je_spec_425``), echte Merker-Dateien, ein echter kleiner Prüf-Befehl mit Zähldatei,
ein echter Live-Befehl mit eigener Zähldatei, der capo-Takt und ``to_spawn.py`` als Prozess.
Werkzeug-Fehler wird über die Tür ``bash_wahl`` eingespeist (``TO_SPAWN_TEST_BASH_FEHLT=1``) —
wirkt unter Linux wie Windows, wo ``bash_wahl`` Git-Bash sonst an festen Orten fände (#646).

Teile:
1. ``to_spawn.py live <S>`` — der Live-Befehl (``live.befehl``) startet nur, wenn die
   Komplett-Prüfung „vor Live“ grün ist; rot, Werkzeug-Fehler → kein Live.
2. ``capo --dry-run`` — startet keine Komplett-Prüfung, liest nur den Merker; rot/fehlend →
   kein „SPEC FERTIG“.
3. Lücken aus #425: Werkzeug-Fehler, Kopf nicht lesbar, Merker nicht schreibbar, fehlender Schlüssel.
"""

# ruff: noqa: F811 — ``welt`` kommt als Fixture aus test_waechter_213 und wird als Parameter genannt.
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from test_komplett_je_spec_425 import SPEC as KLON_SPEC
from test_komplett_je_spec_425 import _befehl, _konfig as _klon_konfig, _laeufe, _rot, klon, zaehl  # noqa: F401
from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _capo,
    _commit,
    _gh_setzen,
    _iso,
    _konfig,
    _text,
    welt,
)

from to_spawn import bash_wahl, komplett_lauf
from to_spawn.pruefplan import Moment

SKILL = Path(__file__).resolve().parents[1]
STAGING = {"url": "http://127.0.0.1:8001/"}


def _live_befehl(live_zaehl: Path) -> str:
    """Echter Live-Befehl: eine Zeile je Lauf in ``live_zaehl``."""
    code = f"import pathlib;pathlib.Path({str(live_zaehl)!r}).open('a').write('live\\n')"
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def _einrichten(welt: dict[str, Path], *, staging: bool, live: bool = True, pruefung: bool = True) -> tuple[Path, Path]:
    zaehl, live_zaehl = welt["tmp"] / "zaehl.txt", welt["tmp"] / "live.txt"
    zusatz: dict[str, object] = {"staging": STAGING if staging else {"ohne": True}}
    if pruefung:
        zusatz["pruefung"] = {"schnell": "true", "komplett": _befehl(zaehl)}
    if live:
        zusatz["live"] = {"befehl": _live_befehl(live_zaehl), "url": "http://127.0.0.1:18513/"}
    _konfig(welt, **zusatz)
    return zaehl, live_zaehl


def _alle_zu(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: B (#902)", {})
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())


def _live(repo: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "live", SPEC],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "TO_SPAWN_REPO": str(repo), **(env or {})},
        timeout=90,
        check=False,
    )


# --- 1. Live-Schritt an der Tür ------------------------------------------------------------------


def test_live_ohne_staging_rot_startet_nicht_dann_gruen_einmal(welt: dict[str, Path]) -> None:
    zaehl, live_zaehl = _einrichten(welt, staging=False)
    _rot(zaehl)
    rot = _live(welt["repo"])
    assert rot.returncode == 1, _text(rot)
    assert "Live-Schritt nicht gestartet" in rot.stdout and "rot" in rot.stdout, _text(rot)
    assert _laeufe(live_zaehl) == 0
    zaehl.with_name("exit.txt").unlink()
    gruen = _live(welt["repo"])
    assert gruen.returncode == 0, _text(gruen)
    assert "Komplett-Prüfung grün" in gruen.stdout, _text(gruen)
    assert _laeufe(live_zaehl) == 1
    nochmal = _live(welt["repo"])
    assert nochmal.returncode == 0 and "schon grün" in nochmal.stdout, _text(nochmal)
    assert _laeufe(zaehl) == 2  # rot + grün, der zweite Live-Schritt prüft nicht erneut
    assert _laeufe(live_zaehl) == 2


def test_live_werkzeug_fehler_startet_nicht(welt: dict[str, Path]) -> None:
    zaehl, live_zaehl = _einrichten(welt, staging=False)
    erg = _live(welt["repo"], {bash_wahl.TEST_FEHLT_ENV: "1"})
    assert erg.returncode == 1, _text(erg)
    assert "Live-Schritt nicht gestartet" in erg.stdout and "Werkzeug" in erg.stdout, _text(erg)
    assert _laeufe(zaehl) == 0 and _laeufe(live_zaehl) == 0


def test_live_mit_staging_nach_gruenem_takt_ohne_neuen_lauf(welt: dict[str, Path]) -> None:
    zaehl, live_zaehl = _einrichten(welt, staging=True)
    _alle_zu(welt)
    takt = _capo(welt)
    assert "Komplett-Prüfung grün" in takt.stdout, _text(takt)
    erg = _live(welt["repo"])
    assert erg.returncode == 0 and "schon grün" in erg.stdout, _text(erg)
    assert _laeufe(zaehl) == 1 and _laeufe(live_zaehl) == 1


def test_live_nicht_eingerichtet_alter_ablauf(welt: dict[str, Path]) -> None:
    _, live_zaehl = _einrichten(welt, staging=False, pruefung=False)
    erg = _live(welt["repo"])
    assert erg.returncode == 0 and "nicht eingerichtet" in erg.stdout, _text(erg)
    assert _laeufe(live_zaehl) == 1


def test_live_befehl_fehlt_exit_2_nach_gruener_pruefung(welt: dict[str, Path]) -> None:
    zaehl, live_zaehl = _einrichten(welt, staging=False, live=False)
    erg = _live(welt["repo"])
    assert erg.returncode == 2, _text(erg)
    assert "live.befehl" in erg.stdout, _text(erg)
    assert _laeufe(zaehl) == 1 and _laeufe(live_zaehl) == 0


def test_live_befehl_rot_exit_1(welt: dict[str, Path]) -> None:
    _einrichten(welt, staging=False, live=False)
    daten = json.loads((welt["repo"] / ".to-spawn" / "config.json").read_text(encoding="utf-8"))
    daten["live"] = {"befehl": "exit 7"}
    (welt["repo"] / ".to-spawn" / "config.json").write_text(json.dumps(daten), encoding="utf-8")
    erg = _live(welt["repo"])
    assert erg.returncode == 1 and "Exit 7" in erg.stdout, _text(erg)


# --- 2. Probelauf liest nur ---------------------------------------------------------------------


def test_probelauf_startet_keine_komplett_pruefung(welt: dict[str, Path]) -> None:
    zaehl, _ = _einrichten(welt, staging=True)
    _alle_zu(welt)
    probe = _capo(welt, "--dry-run")
    assert _laeufe(zaehl) == 0, _text(probe)
    assert "SPEC FERTIG" not in probe.stdout, _text(probe)
    assert "[Probe]" in probe.stdout and "Komplett-Prüfung" in probe.stdout, _text(probe)
    assert not komplett_lauf._datei(welt["repo"], int(SPEC)).exists()


def test_probelauf_rot_merker_kein_spec_fertig(welt: dict[str, Path]) -> None:
    zaehl, _ = _einrichten(welt, staging=True)
    _alle_zu(welt)
    _rot(zaehl)
    _capo(welt)
    assert _laeufe(zaehl) == 1
    probe = _capo(welt, "--dry-run")
    assert "SPEC FERTIG" not in probe.stdout, _text(probe)
    assert "rot" in probe.stdout, _text(probe)
    assert _laeufe(zaehl) == 1


def test_probelauf_gruener_merker_zeigt_spec_fertig(welt: dict[str, Path]) -> None:
    zaehl, _ = _einrichten(welt, staging=True)
    _alle_zu(welt)
    _capo(welt)
    probe = _capo(welt, "--dry-run")
    assert "SPEC FERTIG" in probe.stdout and "[Probe]" in probe.stdout, _text(probe)
    assert _laeufe(zaehl) == 1


def test_probelauf_merker_ohne_schluessel_kein_spec_fertig(welt: dict[str, Path]) -> None:
    zaehl, _ = _einrichten(welt, staging=True)
    _alle_zu(welt)
    kopf = subprocess.run(
        ["git", "-C", str(welt["repo"]), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    datei = komplett_lauf._datei(welt["repo"], int(SPEC))
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps({"spec": int(SPEC), "exit": 0, "kopf": kopf}), encoding="utf-8")
    probe = _capo(welt, "--dry-run")
    assert "SPEC FERTIG" not in probe.stdout, _text(probe)
    assert _laeufe(zaehl) == 0


# --- 3. Lücken aus #425 (Tür direkt, echtes Wegwerf-Repo) ----------------------------------------


def test_werkzeug_fehler_kein_ok_und_nie_als_geprueft(
    klon: Path, zaehl: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    konfig = _klon_konfig(zaehl, staging=False)
    monkeypatch.setenv(bash_wahl.TEST_FEHLT_ENV, "1")
    erg = komplett_lauf.komplett_einmal(klon, KLON_SPEC, Moment.VOR_LIVE, konfig)
    assert (erg.gelaufen, erg.ok) == (True, False), erg
    assert "Werkzeug-Fehler" in erg.grund and "bash" in erg.grund, erg.grund
    merker = komplett_lauf.lies_merker(klon, KLON_SPEC)
    assert merker is not None and merker.exit == 2 and "bash" in merker.grund
    assert _laeufe(zaehl) == 0
    monkeypatch.delenv(bash_wahl.TEST_FEHLT_ENV)
    # Gate-Fehler zählt nie als geprüft — auch im Takt nicht: nächster Aufruf prüft echt.
    takt = komplett_lauf.takt_kind(klon, KLON_SPEC, Moment.VOR_LIVE, konfig)
    assert (takt.gelaufen, takt.ok) == (True, True), takt
    assert _laeufe(zaehl) == 1


def test_kopf_nicht_lesbar_kein_lauf_kein_merker(tmp_path: Path, zaehl: Path) -> None:
    leer = tmp_path / "leer"
    leer.mkdir()
    subprocess.run(["git", "init", "-q", str(leer)], check=True, capture_output=True)
    konfig = _klon_konfig(zaehl, staging=False)
    erg = komplett_lauf.komplett_einmal(leer, KLON_SPEC, Moment.VOR_LIVE, konfig)
    assert (erg.gelaufen, erg.ok) == (False, False), erg
    assert "Handlung" in erg.grund, erg.grund
    assert _laeufe(zaehl) == 0
    assert komplett_lauf.lies_merker(leer, KLON_SPEC) is None


def test_merker_nicht_schreibbar_ok_false(klon: Path, zaehl: Path) -> None:
    konfig = _klon_konfig(zaehl, staging=False)
    # Ein Ordner an der Stelle der Merker-Datei: os.replace scheitert echt.
    komplett_lauf._datei(klon, KLON_SPEC).mkdir(parents=True)
    erg = komplett_lauf.komplett_einmal(klon, KLON_SPEC, Moment.VOR_LIVE, konfig)
    assert (erg.gelaufen, erg.ok) == (True, False), erg
    assert "nicht schreibbar" in erg.grund and "Handlung" in erg.grund, erg.grund
    assert _laeufe(zaehl) == 1


def test_fehlender_schluessel_gilt_als_nicht_gelaufen(klon: Path, zaehl: Path) -> None:
    konfig = _klon_konfig(zaehl, staging=False)
    kopf = subprocess.run(
        ["git", "-C", str(klon), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    datei = komplett_lauf._datei(klon, KLON_SPEC)
    datei.parent.mkdir(parents=True)
    datei.write_text(json.dumps({"spec": KLON_SPEC, "exit": 0, "kopf": kopf}), encoding="utf-8")
    assert komplett_lauf.lies_merker(klon, KLON_SPEC) is None
    erg = komplett_lauf.komplett_einmal(klon, KLON_SPEC, Moment.VOR_LIVE, konfig)
    assert (erg.gelaufen, erg.ok) == (True, True), erg
    assert _laeufe(zaehl) == 1
    merker = komplett_lauf.lies_merker(klon, KLON_SPEC)
    assert merker is not None and merker.dauer_s >= 0 and merker.moment == "vor_live"
