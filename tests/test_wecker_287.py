"""#287: Externer Wecker für Leit-/Aufsichts-Sessions außerhalb der Session (Linux/tmux).

Deckt ab:
1. Schnittstelle ``SystemWecker`` + Linux-Werkzeugkasten (Cron-Einrichtung idempotent,
   alte crontab gesichert; Windows-Kasten sauber getrennt und als offen markiert).
2. Nachgestellter Wochen-Limit-Fall im echten tmux-Pane: Weckruf scheitert am Limit,
   wird mit Grund geloggt; während des Limits kein Spam; nach dem Reset (spätestens
   M Minuten danach) weckt der externe Wecker die Session ohne Eingriff weiter, obwohl
   die alte Limit-Meldung noch im Verlauf steht.
3. Einbindung in ``to_spawn.py wecker`` (CLI) und ``aufpasser`` (als Sicherheitsnetz
   nach der Deploy-Wache).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

SKILL = Path(__file__).resolve().parent.parent
BERLIN = ZoneInfo("Europe/Berlin")
OHNE_TMUX = sys.platform == "win32" or shutil.which("tmux") is None


def _tmux(socket: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", "-L", socket, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
        timeout=20,
    )


def _fake_session_skript(ordner: Path, limit_flag: Path, spur: Path) -> Path:
    """Interaktive Test-Session im echten tmux-Pane, die das Verhalten von Claude Code
    vor und nach dem Wochen-Limit-Reset nachstellt."""
    skript = ordner / "fake_leit_session.py"
    skript.write_text(
        "\n".join(
            [
                "import sys",
                "from pathlib import Path",
                f"FLAG = Path({str(limit_flag)!r})",
                f"SPUR = Path({str(spur)!r})",
                "RAHMEN = '─' * 30",
                "def prompt():",
                "    print(RAHMEN, flush=True)",
                "    print('❯ ', end='', flush=True)",
                "    print('\\n' + RAHMEN, flush=True)",
                "    print('Leit-Session bereit', flush=True)",
                "print('Leit-Session gestartet', flush=True)",
                "prompt()",
                "for roh in sys.stdin:",
                "    zeile = roh.strip()",
                "    if not zeile:",
                "        continue",
                "    if FLAG.exists():",
                "        meldung = FLAG.read_text(encoding='utf-8').strip()",
                "        with SPUR.open('a', encoding='utf-8') as f:",
                "            f.write(f'LIMIT:{zeile}\\n')",
                "        print(f'❯ {zeile}', flush=True)",
                "        print(meldung, flush=True)",
                "        prompt()",
                "    else:",
                "        with SPUR.open('a', encoding='utf-8') as f:",
                "            f.write(f'WEITER:{zeile}\\n')",
                "        print(f'❯ {zeile}', flush=True)",
                "        print(f'Weiterarbeit aufgenommen: {zeile}', flush=True)",
                "        prompt()",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return skript


def _warte_auf_text(socket: str, ziel: str, erwartet: str, sekunden: float = 10.0) -> str:
    ende = time.monotonic() + sekunden
    text = ""
    while time.monotonic() < ende:
        res = _tmux(socket, "capture-pane", "-p", "-t", ziel, check=False)
        text = res.stdout
        if erwartet in text:
            return text
        time.sleep(0.1)
    return text


def test_limit_zeile_und_reset_aus_beleg_287() -> None:
    """Erkennt sowohl die echte Wochen-Limit-Anzeige aus #287 als auch reguläre Limit-Zeilen."""
    from to_spawn import wecker

    jetzt = datetime(2026, 11, 22, 22, 7, tzinfo=BERLIN)
    beleg = "95% of your weekly limit · resets 11pm"
    assert wecker.limit_zeile(f"Verlauf\n{beleg}\n❯ ") == beleg
    ziel = wecker.reset_zeitpunkt_aus_bild(beleg, jetzt)
    assert ziel is not None
    assert ziel.astimezone(BERLIN) == datetime(2026, 11, 22, 23, 0, tzinfo=BERLIN)

    klassisch = "You've hit your weekly limit · resets 11pm (Europe/Berlin)"
    assert wecker.limit_zeile(klassisch) == klassisch
    ziel2 = wecker.reset_zeitpunkt_aus_bild(klassisch, jetzt)
    assert ziel2 is not None
    assert ziel2.astimezone(BERLIN) == datetime(2026, 11, 22, 23, 0, tzinfo=BERLIN)


def test_cron_zeilen_idempotent_und_fenster_getrennt() -> None:
    """Linux-Cron-Zeile wird idempotent gesetzt; fremde Zeilen bleiben erhalten."""
    from to_spawn import wecker

    zustand = Path("/tmp/wecker-test")
    befehl = wecker.cron_befehl(zustand, takt_min=5, python="/usr/bin/python3")
    assert "*/5 * * * *" in befehl
    assert "wecker lauf" in befehl

    basis = ["0 3 * * * echo fremd"]
    einmal = wecker.cron_zeilen(basis, befehl)
    zweimal = wecker.cron_zeilen(einmal, befehl)
    assert einmal == zweimal
    assert sum(wecker.CRON_MARKE in z for z in zweimal) == 1
    assert "0 3 * * * echo fremd" in zweimal

    # Windows-Werkzeugkasten ist sauber getrennt und nennt #287 als offen.
    win = wecker.system_wecker("win32")
    with pytest.raises(NotImplementedError, match="Windows"):
        win.cron_einrichten(zustand, takt_min=5, trocken=True)


def test_cli_setzen_liste_loeschen(tmp_path: Path) -> None:
    """CLI ``to_spawn.py wecker setzen/liste/loeschen`` verwaltet Weckaufträge atomar."""
    zustand = tmp_path / "wecker-zustand"
    env = {**os.environ, "TO_SPAWN_WECKER_ORDNER": str(zustand), "PYTHONIOENCODING": "utf-8"}

    def cli(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SKILL / "to_spawn.py"), "wecker", *args],
            cwd=str(SKILL),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )

    setzen = cli(
        "setzen",
        "leit-bau",
        "--ziel",
        "=leit:0",
        "--text",
        "Weckruf: Stand prüfen",
        "--takt-min",
        "10",
    )
    assert setzen.returncode == 0, setzen.stdout + setzen.stderr

    liste = cli("liste")
    assert liste.returncode == 0, liste.stdout + liste.stderr
    assert "leit-bau" in liste.stdout and "=leit:0" in liste.stdout

    loeschen = cli("loeschen", "leit-bau")
    assert loeschen.returncode == 0, loeschen.stdout + loeschen.stderr

    liste2 = cli("liste")
    assert liste2.returncode == 0
    assert "leit-bau" not in liste2.stdout


@pytest.mark.skipif(OHNE_TMUX, reason="Echter tmux-Weg-Test läuft nur unter Linux mit tmux")
def test_echter_tmux_weg_limit_scheitert_und_nach_reset_weiter(tmp_path: Path) -> None:
    """Abnahme 2 + 3 (#287) im echten tmux-Pane:
    1. Weckruf vor Reset scheitert am simulierten Wochen-Limit → im Log mit Grund.
    2. Vor dem Reset wartet der Wecker ab (kein Spam).
    3. Nach dem Reset (t + M min) weckt der Wecker die Session ohne Eingriff weiter,
       obwohl die alte Limit-Meldung noch im Pane steht.
    """
    from to_spawn import wecker

    socket = f"wecker287-{os.getpid()}-{int(time.time())}"
    limit_flag = tmp_path / "limit.flag"
    spur = tmp_path / "spur.txt"
    zustand = tmp_path / "wecker-zustand"
    limit_flag.write_text("95% of your weekly limit · resets 11pm\n", encoding="utf-8")
    skript = _fake_session_skript(tmp_path, limit_flag, spur)

    _tmux(
        socket,
        "new-session",
        "-d",
        "-s",
        "leit",
        "-n",
        "aufsicht",
        f"{sys.executable} {skript}",
    )
    try:
        ziel = "=leit:aufsicht"
        assert "Leit-Session bereit" in _warte_auf_text(socket, ziel, "Leit-Session bereit")

        wecker.auftrag_setzen(
            zustand,
            name="leit-aufsicht",
            ziel=ziel,
            text="Weckruf: Stand pruefen und weiterbauen",
            takt_min=5,
            tmux_socket=socket,
        )

        # 1) 22:07 — Weckruf trifft auf das Wochen-Limit.
        t_vor_reset = datetime(2026, 11, 22, 22, 7, tzinfo=BERLIN).timestamp()
        erg1 = wecker.durchgang(
            zustand,
            jetzt=t_vor_reset,
            nachpruef_s=0.4,
            puffer_s=120.0,
        )
        assert len(erg1) == 1
        assert erg1[0].ereignis == "gescheitert", erg1[0]
        assert "weekly limit" in erg1[0].grund.lower(), erg1[0].grund

        # Gescheiterter Weckruf steht strukturiert im Log mit Grund (Abnahme 3).
        log_zeilen = [
            json.loads(z)
            for z in (zustand / "wecker.jsonl").read_text(encoding="utf-8").splitlines()
            if z.strip()
        ]
        assert any(
            z["name"] == "leit-aufsicht"
            and z["ereignis"] == "gescheitert"
            and "weekly limit" in z["grund"].lower()
            for z in log_zeilen
        ), log_zeilen

        # 2) 22:25 — Limit-Reset (23:00 + 2 min Puffer) noch nicht vorbei → kein erneutes Tippen.
        t_waehrend = datetime(2026, 11, 22, 22, 25, tzinfo=BERLIN).timestamp()
        spur_vorher = spur.read_text(encoding="utf-8")
        erg2 = wecker.durchgang(
            zustand,
            jetzt=t_waehrend,
            nachpruef_s=0.4,
            puffer_s=120.0,
        )
        assert erg2[0].ereignis == "wartet_auf_reset", erg2[0]
        assert spur.read_text(encoding="utf-8") == spur_vorher

        # 3) 23:00 Reset erfolgt; 23:05 (≤ 5 min nach Reset) läuft der nächste Cron-Takt.
        limit_flag.unlink()
        t_nach_reset = datetime(2026, 11, 22, 23, 5, tzinfo=BERLIN).timestamp()
        erg3 = wecker.durchgang(
            zustand,
            jetzt=t_nach_reset,
            nachpruef_s=0.4,
            puffer_s=120.0,
        )
        assert erg3[0].ereignis == "geweckt", erg3[0]
        assert "Weiterarbeit aufgenommen" in _warte_auf_text(
            socket, ziel, "Weiterarbeit aufgenommen"
        )
        assert "WEITER:Weckruf: Stand pruefen und weiterbauen" in spur.read_text(encoding="utf-8")
    finally:
        _tmux(socket, "kill-server", check=False)


def test_aufpasser_stoesst_registrierten_wecker_an(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Aufpasser-Lauf ruft den externen Wecker mit auf (nach der Deploy-Wache)."""
    from to_spawn import aufpasser, wecker

    wecker_zustand = tmp_path / "wecker"
    aufgerufen: list[tuple[Path, str | None, bool]] = []

    def merk_durchgang(
        zustand: Path,
        *,
        tmux_socket: str | None = None,
        trocken: bool = False,
        jetzt: float | None = None,
        **kw: Any,
    ) -> list[Any]:
        aufgerufen.append((zustand, tmux_socket, trocken))
        return []

    wecker.auftrag_setzen(
        wecker_zustand,
        name="leit",
        ziel="=leit:0",
        text="Weckruf",
        takt_min=5,
    )
    monkeypatch.setattr(wecker, "durchgang", merk_durchgang)
    # Hausmeister leer halten, damit der Test nur die Aufpasser→Wecker-Naht prüft.
    leer = tmp_path / "leer"
    leer.mkdir()
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", "")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", "")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", str(leer))
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_LOG", str(tmp_path / "hausmeister.jsonl"))

    e = aufpasser.Einstellungen(
        zustand=tmp_path / "aufpasser",
        tmux_socket=f"wecker-anbindung-{os.getpid()}",
        deploy_muster=f"niemals287-{os.getpid()}",
        wecker_zustand=wecker_zustand,
        trocken=True,
    )
    assert aufpasser.Aufpasser(e).lauf() == 0
    assert aufgerufen == [(wecker_zustand, e.tmux_socket, True)]


def test_neue_limit_zeile_trennt_altes_scrollback_von_frischem_limit() -> None:
    """Scrollt das Pane nach oben und bricht der Weckruf um, zählt eine alte Limit-Zeile
    im Scrollback nicht als neuer Fehlschlag; eine zweite Limit-Zeile unten aber schon."""
    from to_spawn import wecker

    rahmen = "─" * 30
    vorher = (
        f"alte Zeile 1\n95% of your weekly limit · resets 11pm\n"
        f"{rahmen}\n❯ \n{rahmen}\nStatus alt"
    )
    # Nach Reset: Pane scrollt um 1 Zeile nach oben, langer Weckruf bricht um, Antwort ok.
    nachher_ok = (
        f"95% of your weekly limit · resets 11pm\n"
        f"❯ Langer Weckruf der im schmalen\nPane umgebrochen wird\n"
        f"Weiterarbeit aufgenommen\n{rahmen}\n❯ \n{rahmen}\nStatus neu"
    )
    wecktext = "Langer Weckruf der im schmalen Pane umgebrochen wird"
    assert wecker.neue_limit_zeile(vorher, nachher_ok, wecktext) is None

    # Gegenprobe: Limit greift erneut (gleicher Text ein zweites Mal unten).
    nachher_limit = (
        f"95% of your weekly limit · resets 11pm\n"
        f"❯ Langer Weckruf der im schmalen\nPane umgebrochen wird\n"
        f"95% of your weekly limit · resets 11pm\n{rahmen}\n❯ \n{rahmen}"
    )
    assert (
        wecker.neue_limit_zeile(vorher, nachher_limit, wecktext)
        == "95% of your weekly limit · resets 11pm"
    )


def test_limit_ohne_reset_uhrzeit_versucht_nach_takt_erneut(tmp_path: Path) -> None:
    """Ohne Reset-Uhrzeit im Limit-Text wird der Fehlschlag geloggt und nach ``takt_min``
    erneut geweckt; nach Aufhebung des Limits läuft die Session weiter."""
    from to_spawn import wecker

    rahmen = "─" * 30
    leer_bild = f"{rahmen}\n❯ \n{rahmen}\n"

    class Attrappe:
        def __init__(self) -> None:
            self.limit_aktiv = True
            self.bild = leer_bild
            self.getippt: list[str] = []

        def pane_lesen(self, ziel: str, *, tmux_socket: str | None = None) -> wecker.PaneStand:
            return wecker.PaneStand(
                existiert=True, session_laeuft=True, farbig=self.bild, text=self.bild
            )

        def tippen(self, ziel: str, text: str, *, tmux_socket: str | None = None) -> None:
            self.getippt.append(text)
            if self.limit_aktiv:
                self.bild = f"❯ {text}\nYou've hit your weekly limit\n{leer_bild}"
            else:
                self.bild = f"❯ {text}\nWeiter geht es\n{leer_bild}"

        def cron_einrichten(
            self, zustand: Path, *, takt_min: int = 5, trocken: bool = False, python: str = ""
        ) -> str:
            return ""

    zustand = tmp_path / "wecker"
    wecker.auftrag_setzen(zustand, name="leit", ziel="=leit:0", text="Weckruf", takt_min=5)
    kasten = Attrappe()

    erg1 = wecker.durchgang(zustand, werkzeug=kasten, jetzt=1000.0, nachpruef_s=0.0)
    assert erg1[0].ereignis == "gescheitert"
    assert "weekly limit" in erg1[0].grund.lower()

    # Nach 2 min noch nicht wieder fällig.
    erg2 = wecker.durchgang(zustand, werkzeug=kasten, jetzt=1120.0, nachpruef_s=0.0)
    assert erg2[0].ereignis == "nicht_faellig"
    assert len(kasten.getippt) == 1

    # Nach 5 min ist das Limit vorbei → erneuter Versuch gelingt.
    kasten.limit_aktiv = False
    erg3 = wecker.durchgang(zustand, werkzeug=kasten, jetzt=1305.0, nachpruef_s=0.0)
    assert erg3[0].ereignis == "geweckt"
    assert len(kasten.getippt) == 2

