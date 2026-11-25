"""#455: Komplett-Prüfung — rot je Kopf nur einmal, Zeitgrenze, Takt nicht blockieren.

Echte Abläufe ohne Attrappen: tmp-Git-Repo als Klon, echter kleiner Prüf-Befehl (Zähldatei aus
``test_komplett_je_spec_425``), echte bash mit Unterprozessen für die Zeitgrenze und ein echter
Hintergrund-Prozess ``to_spawn.py komplett`` für den Takt.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Self

import pytest
from test_komplett_je_spec_425 import SPEC, _befehl, _commit, _git, _laeufe, _rot

from to_spawn import bash_wahl, config, komplett_lauf, prozessbaum
from to_spawn.pruef import leiter, protokoll
from to_spawn.pruefplan import Moment


def _klon(tmp_path: Path, pruefung: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Klon mit eingecheckter Konfig (der Hintergrund-Lauf liest sie selbst aus der Datei)."""
    repo = tmp_path / "klon"
    repo.mkdir()
    _git(repo, "init", "-q")
    eigen = {
        "staging": {"url": "http://127.0.0.1:8001/"},
        "pruefung": {"schnell": "true", **pruefung},
    }
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(json.dumps(eigen), encoding="utf-8")
    (repo / ".gitignore").write_text("\n".join(config.GITIGNORE_BLOCK) + "\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", ".to-spawn/config.json")
    _commit(repo, "a.txt")
    return repo, config.lade(repo)


def _warte_merker(klon: Path, kopf: str, frist_s: float = 60.0) -> komplett_lauf.Merker:
    ende = time.monotonic() + frist_s
    while time.monotonic() < ende:
        merker = komplett_lauf.lies_merker(klon, SPEC)
        if merker is not None and merker.kopf == kopf:
            return merker
        time.sleep(0.2)
    raise AssertionError(f"kein Merker für {kopf[:7]} nach {frist_s} s")


def _protokoll(klon: Path) -> list[dict[str, Any]]:
    datei = klon / protokoll.ORDNER / protokoll.DATEI
    if not datei.exists():
        return []
    return [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines() if z.strip()]


# --- rot je Kopf nur einmal ---------------------------------------------------------------------


def test_takt_rot_je_kopf_nur_einmal_neu_bei_neuem_kopf_oder_von_hand(
    tmp_path: Path,
) -> None:
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    _rot(zaehl)
    erst = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert (erst.gelaufen, erst.ok) == (True, False), erst
    zweit = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert (zweit.gelaufen, zweit.ok) == (False, False), zweit
    assert "schon rot" in zweit.grund and "Handlung" in zweit.grund, zweit.grund
    assert _laeufe(zaehl) == 1
    # Von Hand (Tür, ohne takt) läuft rot neu.
    hand = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (hand.gelaufen, hand.ok) == (True, False)
    assert _laeufe(zaehl) == 2
    # Neuer Kopf läuft im Takt einmal neu.
    zaehl.with_name("exit.txt").unlink()
    _commit(klon, "b.txt")
    neu = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert (neu.gelaufen, neu.ok) == (True, True), neu
    assert _laeufe(zaehl) == 3


# --- Takt nicht blockieren ----------------------------------------------------------------------


def test_takt_blockiert_nicht_und_startet_nie_doppelt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    zaehl = tmp_path / "zaehl.txt"
    langsam = f"sleep 4; {_befehl(zaehl)}"
    klon, konfig = _klon(tmp_path, {"komplett": langsam})
    monkeypatch.setattr(komplett_lauf, "TAKT_WARTEN_S", 0.5)
    kopf = _git(klon, "rev-parse", "HEAD")
    start = time.monotonic()
    erst = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert time.monotonic() - start < 3.5, "Takt wartet auf die ganze Prüfung"
    assert erst.laeuft and erst.ok and not erst.gelaufen, erst
    zweit = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert zweit.laeuft, zweit
    merker = _warte_merker(klon, kopf)
    assert merker.gruen
    nach = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert (nach.gelaufen, nach.ok, nach.laeuft) == (False, True, False), nach
    assert _laeufe(zaehl) == 1


# --- Zeitgrenze ---------------------------------------------------------------------------------


def test_zeitgrenze_beendet_ganze_prozessgruppe(tmp_path: Path) -> None:
    pid_datei = tmp_path / "enkel.pid"
    # Unter Windows (Git-Bash) liefert /proc/$p/winpid die echte Windows-PID statt der MSYS-PID.
    befehl = f'sleep 60 & p=$!; w=$(cat /proc/$p/winpid 2>/dev/null); echo "${{w:-$p}}" > {shlex.quote(str(pid_datei))}; wait'
    start = time.monotonic()
    stufe = leiter.eigener_befehl(tmp_path, befehl, "komplett", zeitgrenze_s=1.0)
    assert time.monotonic() - start < 15, "Ausgabe blieb offen — Enkel hält die Pipe"
    assert stufe.ergebnis == leiter.ROT, stufe
    assert "Zeitgrenze" in stufe.grund and "Zeitgrenze" in stufe.ausgabe, stufe
    enkel = int(pid_datei.read_text(encoding="utf-8").strip())
    ende = time.monotonic() + 5
    while prozessbaum.lebt(enkel) and time.monotonic() < ende:
        time.sleep(0.1)
    assert not prozessbaum.lebt(enkel) or "Z" in _zustand(enkel), "Enkel lebt nach Zeitgrenze weiter"


def _zustand(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
    except OSError:
        return ""


def test_zeitgrenze_aus_konfig_ist_rot_mit_grund_und_gemerkt(tmp_path: Path) -> None:
    klon, konfig = _klon(tmp_path, {"komplett": "sleep 60", "komplett_zeitgrenze_min": 0.02})
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (erg.gelaufen, erg.ok) == (True, False), erg
    assert "Zeitgrenze" in erg.grund, erg.grund
    merker = komplett_lauf.lies_merker(klon, SPEC)
    assert merker is not None and merker.exit == leiter.ROT and merker.grund == "Zeitgrenze"


def test_zeitgrenze_vorgabe_und_kaputter_wert() -> None:
    assert config.komplett_zeitgrenze_s({}) == config.KOMPLETT_ZEITGRENZE_MIN * 60
    assert config.komplett_zeitgrenze_s({"pruefung": {"komplett_zeitgrenze_min": "viel"}}) == (
        config.KOMPLETT_ZEITGRENZE_MIN * 60
    )
    assert config.komplett_zeitgrenze_s({"pruefung": {"komplett_zeitgrenze_min": -1}}) == (
        config.KOMPLETT_ZEITGRENZE_MIN * 60
    )
    assert config.komplett_zeitgrenze_s({"pruefung": {"komplett_zeitgrenze_min": 5}}) == 300


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Prozessgruppen-Abbruch unter Windows nur dort prüfbar",
)
def test_zeitgrenze_windows_beendet_baum(tmp_path: Path) -> None:
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        bash_wahl.lauf(
            ["-c", "sleep 60 & wait"],
            cwd=tmp_path,
            zeitgrenze_s=1.0,
            stdout=subprocess.PIPE,
            text=True,
        )
    assert time.monotonic() - start < 15


# --- Stand: schmutziger Baum, hinter origin ----------------------------------------------------


def test_ungespeicherte_aenderung_kein_lauf_kein_merker(tmp_path: Path) -> None:
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    (klon / "a.txt").write_text("geändert\n", encoding="utf-8")
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (erg.gelaufen, erg.ok) == (False, False), erg
    assert "ungespeichert" in erg.grund and "Handlung" in erg.grund, erg.grund
    assert _laeufe(zaehl) == 0
    assert komplett_lauf.lies_merker(klon, SPEC) is None


def test_notnagel_wenn_nachziehen_scheitert_kein_lauf(tmp_path: Path) -> None:
    # Nur der Notnagel _stand_halt: die conftest-Naht macht das Nachziehen zum No-op (echt: test_takt_zieht_…).
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    nackt = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(nackt))
    zweig = _git(klon, "rev-parse", "--abbrev-ref", "HEAD")
    _git(klon, "remote", "add", "origin", str(nackt))
    _git(klon, "push", "-q", "-u", "origin", zweig)
    anderer = tmp_path / "anderer"
    _git(tmp_path, "clone", "-q", str(nackt), str(anderer))
    _commit(anderer, "neu.txt")
    _git(anderer, "push", "-q", "origin", zweig)
    _git(klon, "fetch", "-q", "origin")
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (erg.gelaufen, erg.ok) == (False, False), erg
    assert "origin" in erg.grund and "Handlung" in erg.grund, erg.grund
    assert _laeufe(zaehl) == 0


# --- Protokoll ----------------------------------------------------------------------------------


def test_lauf_steht_im_pruef_protokoll(tmp_path: Path) -> None:
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    kopf = _git(klon, "rev-parse", "HEAD")
    komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, konfig)
    zeilen = [z for z in _protokoll(klon) if z.get("art") == "komplett"]
    assert len(zeilen) == 1, _protokoll(klon)
    assert zeilen[0]["ziel"] == kopf and zeilen[0]["urteil"] == "grün"
    assert zeilen[0]["stufen"][0]["name"] == "komplett"


# --- Review a62e67a: Takt zieht nach, gestorbener Hintergrund-Lauf ------------------------------


def _mit_origin(tmp_path: Path, klon: Path) -> tuple[Path, str]:
    """Klon an ein nacktes origin hängen; ``anderer`` = zweiter Klon, der neue Commits pusht."""
    nackt = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(nackt))
    zweig = _git(klon, "rev-parse", "--abbrev-ref", "HEAD")
    _git(klon, "remote", "add", "origin", str(nackt))
    _git(klon, "push", "-q", "-u", "origin", zweig)
    _git(nackt, "symbolic-ref", "HEAD", f"refs/heads/{zweig}")
    anderer = tmp_path / "anderer"
    _git(tmp_path, "clone", "-q", str(nackt), str(anderer))
    return anderer, zweig


def _neu_auf_origin(anderer: Path, zweig: str, klon: Path, name: str) -> str:
    """Neuer Commit auf origin, im Klon nur ``git fetch`` (wie der capo-Takt, ohne merge)."""
    kopf = _commit(anderer, name)
    _git(anderer, "push", "-q", "origin", zweig)
    _git(klon, "fetch", "-q", "origin")
    return kopf


@pytest.fixture
def echtes_nachziehen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test-Naht aus conftest aus: ``arbeitsstand.sicherstellen`` arbeitet mit echtem git."""
    from to_spawn import arbeitsstand

    monkeypatch.delenv(arbeitsstand.TEST_OK_ENV, raising=False)


def test_takt_zieht_klon_hinter_origin_nach_und_prueft_neuen_kopf(tmp_path: Path, echtes_nachziehen: None) -> None:
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    anderer, zweig = _mit_origin(tmp_path, klon)
    neu = _neu_auf_origin(anderer, zweig, klon, "neu.txt")
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert _git(klon, "rev-parse", "HEAD") == neu, "Klon nicht nachgezogen"
    merker = _warte_merker(klon, neu)
    assert merker.gruen, (erg, merker)
    assert _laeufe(zaehl) == 1


def test_takt_nach_rot_neuer_kopf_auf_origin_wird_nachgezogen_und_neu_geprueft(
    tmp_path: Path, echtes_nachziehen: None
) -> None:
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    anderer, zweig = _mit_origin(tmp_path, klon)
    _rot(zaehl)
    erst = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert (erst.gelaufen, erst.ok) == (True, False), erst
    zaehl.with_name("exit.txt").unlink()
    neu = _neu_auf_origin(anderer, zweig, klon, "fix.txt")
    zweit = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert _git(klon, "rev-parse", "HEAD") == neu, zweit
    assert _warte_merker(klon, neu).gruen, zweit
    assert _laeufe(zaehl) == 2


def test_nachziehen_unmoeglich_haelt_mit_handlung(tmp_path: Path, echtes_nachziehen: None) -> None:
    """Abgewichen (eigener Commit + neuer auf origin): kein Lauf, klare Zeile, HEAD bleibt."""
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    anderer, zweig = _mit_origin(tmp_path, klon)
    eigen = _commit(klon, "eigen.txt")
    _neu_auf_origin(anderer, zweig, klon, "fremd.txt")
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert (erg.gelaufen, erg.ok) == (False, False), erg
    assert "nicht vorspulbar" in erg.grund and "Tun:" in erg.grund, erg.grund
    assert _git(klon, "rev-parse", "HEAD") == eigen
    assert _laeufe(zaehl) == 0
    assert komplett_lauf.lies_merker(klon, SPEC) is None


def test_von_hand_zieht_auch_nach(tmp_path: Path, echtes_nachziehen: None) -> None:
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    anderer, zweig = _mit_origin(tmp_path, klon)
    neu = _neu_auf_origin(anderer, zweig, klon, "neu.txt")
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.VOR_LIVE, konfig)
    assert (erg.gelaufen, erg.ok, erg.kopf) == (True, True, neu), erg


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl-Sperre im Kind nur unter Linux")
def test_gestorbener_hintergrund_lauf_startet_im_naechsten_takt_neu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kind hält die Sperre, stirbt ohne Merker → Sperre frei, nächster Takt startet neu."""
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    kopf = _git(klon, "rev-parse", "HEAD")
    monkeypatch.setattr(komplett_lauf, "TAKT_WARTEN_S", 0.3)
    sperr = komplett_lauf._datei(klon, SPEC).with_suffix(".lock")
    echt = komplett_lauf.starter.starte
    # Kind-Skript im Attrappen-Ordner: nur von dort lässt der Starter unter Test Python zu.
    stirbt = Path(os.environ[komplett_lauf.starter.TEST_BIN_ENV]) / "stirbt.py"
    stirbt.write_text(
        "import fcntl,sys,time\nf=open(sys.argv[1],'a');fcntl.flock(f,fcntl.LOCK_EX)\ntime.sleep(1.5);sys.exit(9)\n",
        encoding="utf-8",
    )

    def sterbend(argv: list[str], **kw: Any) -> Any:
        return echt([sys.executable, str(stirbt), str(sperr)], **kw)

    monkeypatch.setattr(komplett_lauf.starter, "starte", sterbend)
    erst = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert erst.laeuft, erst
    time.sleep(0.5)
    belegt = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert belegt.laeuft and "läuft schon" in belegt.grund, belegt
    time.sleep(2.0)  # Kind ist tot, kein Merker, Sperre frei
    assert komplett_lauf.lies_merker(klon, SPEC) is None
    monkeypatch.setattr(komplett_lauf.starter, "starte", echt)
    neu = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert _warte_merker(klon, kopf).gruen, neu
    assert _laeufe(zaehl) == 1


# --- Review b1bf884: Sperre vor Nachziehen, Nachziehen nur bei Bedarf, setsid-Enkel ---------------


def _zaehle_nachziehen(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """``arbeitsstand.sicherstellen`` mitzählen (Original läuft weiter)."""
    from to_spawn import arbeitsstand

    aufrufe: list[Path] = []
    original = arbeitsstand.sicherstellen

    def zaehlend(repo: Path, *, nachziehen: bool = True) -> Any:
        aufrufe.append(repo)
        return original(repo, nachziehen=nachziehen)

    monkeypatch.setattr(arbeitsstand, "sicherstellen", zaehlend)
    return aufrufe


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl-Sperre nur unter Linux")
def test_belegte_sperre_zieht_nicht_nach_und_meldet_laeuft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Läuft eine Prüfung (Sperre belegt), darf der Takt den Arbeitsbaum nicht verschieben."""
    from to_spawn import sperre

    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    aufrufe = _zaehle_nachziehen(monkeypatch)
    sperr_datei = komplett_lauf._datei(klon, SPEC).with_suffix(".lock")
    with sperre.halte(sperr_datei) as frei:
        assert frei
        erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert erg.laeuft and erg.ok and not erg.gelaufen, erg
    assert aufrufe == [], "Nachziehen trotz belegter Sperre"
    assert _laeufe(zaehl) == 0


def test_takt_mit_gruenem_merker_zieht_nicht_nach(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Grüner Merker für den Kopf und Kopf gleich @{u}: kein Nachziehen im Takt; neuer Kopf auf origin schon."""
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    anderer, zweig = _mit_origin(tmp_path, klon)
    hand = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig)
    assert (hand.gelaufen, hand.ok) == (True, True), hand
    aufrufe = _zaehle_nachziehen(monkeypatch)
    takt = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert (takt.gelaufen, takt.ok) == (False, True), takt
    assert aufrufe == [], "Nachziehen trotz gültigem Merker"
    _neu_auf_origin(anderer, zweig, klon, "neu.txt")
    komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert aufrufe == [klon], "Kopf hinter @{u}, trotzdem nicht nachgezogen"


@pytest.mark.skipif(sys.platform == "win32", reason="setsid nur unter Linux")
def test_zeitgrenze_haelt_auch_bei_setsid_enkel(tmp_path: Path) -> None:
    """Enkel mit eigenem setsid verlässt die Prozessgruppe und hält die Pipe: trotzdem Zeitgrenze."""
    pid_datei = tmp_path / "enkel.pid"
    befehl = f"setsid sh -c 'echo $$ > {shlex.quote(str(pid_datei))}; exec sleep 25' & echo bisher; wait"
    start = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            bash_wahl.lauf(
                ["-c", befehl],
                cwd=tmp_path,
                zeitgrenze_s=1.0,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        assert time.monotonic() - start < 15, "setsid-Enkel hält die Pipe offen"
    finally:
        if pid_datei.exists():
            try:
                os.kill(int(pid_datei.read_text(encoding="utf-8").strip()), 9)
            except (OSError, ValueError):
                pass


# --- #468: Kopfwechsel im Hintergrund-Lauf -------------------------------------------------------


def test_hintergrund_kopfwechsel_vor_kind_pruefung_keine_falschmeldung(
    tmp_path: Path, echtes_nachziehen: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#468: Neuer Kopf kommt an, bevor das Kind prüft — Kind prüft den neuen Kopf, keine Falschmeldung."""
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    anderer, zweig = _mit_origin(tmp_path, klon)
    echt = komplett_lauf.starter.starte
    neuer_kopf: list[str] = []

    def mit_kopfwechsel(argv: list[str], **kw: Any) -> Any:
        if not neuer_kopf:
            neuer_kopf.append(_neu_auf_origin(anderer, zweig, klon, "waehrenddessen.txt"))
        return echt(argv, **kw)

    monkeypatch.setattr(komplett_lauf.starter, "starte", mit_kopfwechsel)
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert len(neuer_kopf) == 1
    assert "ohne Ergebnis" not in erg.grund, erg
    assert (erg.gelaufen, erg.ok, erg.laeuft, erg.kopf) == (True, True, False, neuer_kopf[0]), erg
    assert "grün" in erg.grund, erg.grund
    assert _laeufe(zaehl) == 1


def test_hintergrund_kopfwechsel_nach_kind_pruefung_keine_falschmeldung(
    tmp_path: Path,
) -> None:
    """#468: Kopf wechselt nach Start des Prüf-Befehls — kein „ohne Ergebnis“, aber noch nicht frei."""
    zaehl = tmp_path / "zaehl.txt"
    # Der Prüf-Befehl zählt sich und erzeugt während des Laufs einen neuen Commit im Klon,
    # sodass der Merker auf den geprüften alten Kopf zeigt, HEAD danach aber schon weiter ist.
    befehl = (
        f"{_befehl(zaehl)} && "
        "printf 'neu\\n' > nach.txt && "
        "git add nach.txt && "
        "git -c user.name=Bau -c user.email=bau@example.invalid commit -q -m nach"
    )
    klon, konfig = _klon(tmp_path, {"komplett": befehl})
    alt = _git(klon, "rev-parse", "HEAD")
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    neu = _git(klon, "rev-parse", "HEAD")
    assert neu != alt
    assert "ohne Ergebnis" not in erg.grund, erg
    assert erg.ok and erg.laeuft and not erg.frei, erg
    assert erg.kopf == neu, erg
    assert _laeufe(zaehl) == 1


def test_hintergrund_echt_ohne_ergebnis_meldet_fehler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#468: Endet der Hintergrund-Lauf wirklich ohne Merker, bleibt die Fehlermeldung erhalten."""
    zaehl = tmp_path / "zaehl.txt"
    klon, konfig = _klon(tmp_path, {"komplett": _befehl(zaehl)})
    echt = komplett_lauf.starter.starte
    absturz = Path(os.environ[komplett_lauf.starter.TEST_BIN_ENV]) / "absturz_468.py"
    absturz.write_text("import sys\nprint('kaputt')\nsys.exit(9)\n", encoding="utf-8")

    def abgestuerzt(argv: list[str], **kw: Any) -> Any:
        return echt([sys.executable, str(absturz)], **kw)

    monkeypatch.setattr(komplett_lauf.starter, "starte", abgestuerzt)
    erg = komplett_lauf.komplett_einmal(klon, SPEC, Moment.ALLE_AUF_STAGING, konfig, takt=True)
    assert (erg.gelaufen, erg.ok, erg.laeuft) == (False, False, False), erg
    assert "ohne Ergebnis beendet (Exit 9)" in erg.grund and "kaputt" in erg.grund, erg.grund
    assert komplett_lauf.lies_merker(klon, SPEC) is None


# --- #645: Prozessbaum-Abbruch unter Windows über prozessbaum + Job-Objekt -----------------------


def test_sitzungsjob_beenden_und_aufnehmen_einmalig(monkeypatch: pytest.MonkeyPatch) -> None:
    """#645: ``sitzungsjob.gebunden`` liefert einen Job; ``aufnehmen`` und ``beenden`` arbeiten sauber ohne Doppel-Close."""
    from to_spawn import sitzungsjob

    ereignisse: list[tuple[str, Any]] = []

    class FakeKernel:
        def CreateJobObjectW(self, _attr: Any, _name: Any) -> int:
            ereignisse.append(("CreateJobObjectW", None))
            return 7001

        def SetInformationJobObject(self, job: int, klasse: int, _info: Any, _groesse: int) -> int:
            ereignisse.append(("SetInformationJobObject", (job, klasse)))
            return 1

        def OpenProcess(self, _rechte: int, _erben: bool, pid: int) -> int:
            ereignisse.append(("OpenProcess", pid))
            return 8000 + pid

        def IsProcessInJob(self, _proz: int, _job: int, ergebnis: Any) -> int:
            ergebnis._obj.value = 0
            return 1

        def AssignProcessToJobObject(self, job: int, proz: int) -> int:
            ereignisse.append(("AssignProcessToJobObject", (job, proz)))
            return 1

        def TerminateJobObject(self, job: int, code: int) -> int:
            ereignisse.append(("TerminateJobObject", (job, code)))
            return 1

        def CloseHandle(self, griff: int) -> int:
            ereignisse.append(("CloseHandle", griff))
            return 1

    class FakeProzess:
        pid = 42

        def poll(self) -> int | None:
            return None

    monkeypatch.setattr(sitzungsjob, "_WINDOWS", True)
    monkeypatch.setattr(sitzungsjob, "_kernel", lambda: FakeKernel())

    with sitzungsjob.gebunden(FakeProzess()) as job:
        assert job is not None and job.aktiv
        assert job.aufnehmen(99) is True
        assert job.beenden() is True
        assert not job.aktiv
        # Zweiter Aufruf und Blockende dürfen den Griff nicht ein zweites Mal schließen.
        assert job.beenden() is False

    assert ereignisse.count(("TerminateJobObject", (7001, 1))) == 1
    assert ereignisse.count(("CloseHandle", 7001)) == 1
    assert ("AssignProcessToJobObject", (7001, 8099)) in ereignisse


def test_gruppe_beenden_delegiert_an_prozessbaum_und_nutzt_job_vor_baum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#645: Unter Windows beendet ``_gruppe_beenden`` über ``prozessbaum.gruppe_beenden`` zuerst den Job, dann Kinder vor Wurzel."""
    ereignisse: list[tuple[str, Any]] = []

    class FakeJob:
        aktiv = True

        def beenden(self) -> bool:
            ereignisse.append(("job.beenden", None))
            self.aktiv = False
            return True

    class FakeProzess:
        pid = 100

        def poll(self) -> int | None:
            return 1

        def kill(self) -> None:
            ereignisse.append(("prozess.kill", self.pid))

    monkeypatch.setattr(prozessbaum, "_WINDOWS", True)
    monkeypatch.setattr(prozessbaum, "baum", lambda pid: [pid, pid + 1, pid + 2])
    monkeypatch.setattr(
        prozessbaum,
        "beenden",
        lambda pids, stufen, **_kw: ereignisse.append(("beenden", list(pids))) or True,
    )

    bash_wahl._gruppe_beenden(FakeProzess(), job=FakeJob())  # type: ignore[arg-type]
    assert ereignisse == [
        ("job.beenden", None),
        ("beenden", [102, 101, 100]),
    ]

    # Ohne Job (Fallback) wird der Baum ebenfalls über prozessbaum.beenden (Kinder zuerst) abgeräumt.
    ereignisse.clear()
    bash_wahl._gruppe_beenden(FakeProzess())  # type: ignore[arg-type]
    assert ereignisse == [("beenden", [102, 101, 100])]


def test_lauf_bindet_job_und_beendet_ihn_vor_nachlauf_communicate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#645: ``bash_wahl.lauf`` bindet die Gruppe sofort (inkl. früher Kinder) und beendet den Job vor dem Nachlauf-``communicate``."""
    ereignisse: list[str] = []

    class FakeJob:
        aktiv = True

        def aufnehmen(self, pid: int) -> bool:
            ereignisse.append(f"aufnehmen:{pid}")
            return True

        def beenden(self) -> bool:
            ereignisse.append("job.beenden")
            self.aktiv = False
            return True

        def schliessen(self) -> None:
            ereignisse.append("job.schliessen")

    class FakePopen:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            self.pid = 500
            self.args = ["bash", "-c", "sleep 60 & wait"]
            self.stdout = None
            self.stderr = None
            self.returncode: int | None = None
            self._aufrufe = 0

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_a: object) -> None:
            pass

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            ereignisse.append("prozess.kill")
            self.returncode = 1

        def communicate(self, *_a: Any, timeout: float | None = None) -> tuple[str, str]:
            self._aufrufe += 1
            ereignisse.append(f"communicate:{self._aufrufe}")
            if self._aufrufe == 1:
                raise subprocess.TimeoutExpired(self.args, timeout or 1.0)
            assert "job.beenden" in ereignisse, "Job muss vor Nachlauf-communicate beendet sein"
            self.returncode = 1
            return ("teil", "")

    from contextlib import contextmanager

    from to_spawn import sitzungsjob

    @contextmanager
    def fake_gebunden(_proz: Any) -> Any:
        ereignisse.append("job.gebunden")
        job = FakeJob()
        try:
            yield job
        finally:
            job.schliessen()

    monkeypatch.setattr(bash_wahl.sys, "platform", "win32")
    monkeypatch.setattr(bash_wahl.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(bash_wahl, "bash", lambda: "C:/Program Files/Git/bin/bash.exe")
    monkeypatch.setattr(bash_wahl.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(prozessbaum, "_WINDOWS", True)
    monkeypatch.setattr(prozessbaum, "baum", lambda pid: [pid, pid + 1])
    monkeypatch.setattr(prozessbaum, "beenden", lambda *_a, **_kw: True)
    monkeypatch.setattr(sitzungsjob, "gebunden", fake_gebunden)

    with pytest.raises(subprocess.TimeoutExpired) as exc:
        bash_wahl.lauf(["-c", "sleep 60 & wait"], cwd=tmp_path, zeitgrenze_s=1.0, stdout=subprocess.PIPE, text=True)

    assert exc.value.output == "teil"
    assert ereignisse.index("job.gebunden") < ereignisse.index("aufnehmen:501") < ereignisse.index("communicate:1")
    assert ereignisse.index("communicate:1") < ereignisse.index("job.beenden") < ereignisse.index("communicate:2")


@pytest.mark.skipif(sys.platform == "win32", reason="killpg-Rückfall auf Prozessbaum unter Linux")
def test_gruppe_beenden_linux_beendet_prozessgruppe_und_hat_baum_rueckfall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#645: ``prozessbaum.gruppe_beenden`` beendet unter Linux die Prozessgruppe und fällt bei ``OSError`` auf ``baum``+``beenden`` zurück."""
    pid_datei = tmp_path / "enkel_645.pid"
    proz = subprocess.Popen(
        [bash_wahl.bash(), "-c", f"sleep 60 & echo $! > {shlex.quote(str(pid_datei))}; wait"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    enkel = 0
    try:
        ende = time.monotonic() + 5
        while (not pid_datei.exists() or not pid_datei.read_text(encoding="utf-8").strip()) and time.monotonic() < ende:
            time.sleep(0.05)
        enkel = int(pid_datei.read_text(encoding="utf-8").strip())
        assert prozessbaum.lebt(enkel)
        prozessbaum.gruppe_beenden(proz)
        proz.wait(timeout=5)
        ende = time.monotonic() + 5
        while prozessbaum.lebt(enkel) and time.monotonic() < ende:
            time.sleep(0.05)
        assert not prozessbaum.lebt(enkel) or "Z" in _zustand(enkel)
    finally:
        if proz.poll() is None:
            proz.kill()
            proz.wait(timeout=5)
        if enkel and prozessbaum.lebt(enkel):
            try:
                os.kill(enkel, 9)
            except OSError:
                pass

    # Rückfall, wenn os.killpg scheitert (OSError != ProcessLookupError).
    beendet: list[list[int]] = []

    class FakeProz:
        pid = 321

        def poll(self) -> int | None:
            return 0

        def kill(self) -> None:
            pass

    monkeypatch.setattr(prozessbaum.os, "killpg", lambda *_a: (_ for _ in ()).throw(PermissionError("gesperrt")))
    monkeypatch.setattr(prozessbaum, "baum", lambda pid: [pid, pid + 1])
    monkeypatch.setattr(prozessbaum, "beenden", lambda pids, *_a, **_kw: beendet.append(list(pids)) or True)
    prozessbaum.gruppe_beenden(FakeProz())  # type: ignore[arg-type]
    assert beendet == [[322, 321]]
