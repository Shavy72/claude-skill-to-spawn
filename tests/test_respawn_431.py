"""Tests für ``respawn`` (#431): Bau-Session nach fester SOP a–e ablösen.

Gestellt ist nur die Außenwelt (tmux, Prozesse, Uhr) — über die Naht ``werkzeug``.
Das Fake zeichnet jeden Aufruf in ``aufrufe`` auf; die Zeit läuft simuliert über
``jetzt()``/``schlafen()``. Handoff und Start-Prompt legt das Fake im Ticket-Worktree
(tmp_path) an, sobald das alte Fenster den Handoff-Auftrag getippt bekommt.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import prozessbaum, respawn
from to_spawn.respawn import FensterInfo

SPEC = 399
TICKET = 431
ALT_ZIEL = "=spec-399:@1"
NEU_ZIEL = "=spec-399:@9"
START_TEXT = "Weiter mit Ticket #431 laut Handoff — Start-Prompt-Inhalt."


class FakeWerkzeug:
    """Zeichnet jeden Aufruf auf; simuliert Zeit, Dateien und Bildschirme."""

    def __init__(
        self,
        wt: Path,
        *,
        fenster: list[FensterInfo] | None = None,
        prozesse: list[str] | None = None,
        handoff_anlegen: bool = True,
        prompt_anlegen: bool = True,
        alter_handoff: bool = False,
        bildschirm_reagiert: bool | str = True,
        remote_reaktion: str = "sofort",
        beenden_ergebnis: str = "beendet",
        tag_versatz: int = 0,
        prompt_text: str = START_TEXT + "\n",
        fehler_bei: dict[str, BaseException] | None = None,
        schliessen_wirkt: bool = True,
    ) -> None:
        self.wt = wt
        self.aufrufe: list[tuple[Any, ...]] = []
        self.zeit = time.time()
        self.fenster = (
            fenster
            if fenster is not None
            else [
                FensterInfo("spec-399", f"bau {TICKET}", ALT_ZIEL, 1111),
                FensterInfo("spec-399", "wache 399", "=spec-399:@2", 2222),
            ]
        )
        self.prozesse = (
            prozesse
            if prozesse is not None
            else [
                f"4242 python3 /home/bau/.claude/skills/to-spawn/skripte/bau.py {TICKET}"
            ]
        )
        self.handoff_anlegen = handoff_anlegen
        self.prompt_anlegen = prompt_anlegen
        self.alter_handoff = alter_handoff
        self.bildschirm_reagiert = bildschirm_reagiert
        self.schirme: dict[str, str] = {ALT_ZIEL: "alte Session arbeitet"}
        # sofort | nach_enter | nie — wann „/remote-control“ auf dem Schirm bestätigt ist
        self.remote_reaktion = remote_reaktion
        self.beenden_ergebnis = beenden_ergebnis
        self.tag_versatz = tag_versatz
        self.prompt_text = prompt_text
        # Methodenname oder „tippen:<ziel>:<text-anfang 15>“ → Ausnahme, einmal geworfen
        self.fehler_bei = fehler_bei or {}
        self.schliessen_wirkt = schliessen_wirkt
        self.nach_schlafen: list[Any] = []
        # Antwort von ``committet`` (Handoff + Start-Prompt im Git, #431 Runde 2 Befund 6)
        self.committet_ergebnis = True
        # Läuft Claude unter der alten Pane? (#431 Runde 5 Befund 6)
        self.claude_da = True

    def _fehler(self, schluessel: str) -> None:
        if schluessel in self.fehler_bei:
            raise self.fehler_bei.pop(schluessel)

    # --- Abfragen ---------------------------------------------------------
    def fenster_liste(self) -> list[FensterInfo]:
        self.aufrufe.append(("fenster_liste",))
        return list(self.fenster)

    def bau_prozesse(self) -> list[str]:
        self.aufrufe.append(("bau_prozesse",))
        return list(self.prozesse)

    def bildschirm(self, ziel: str) -> str:
        self.aufrufe.append(("bildschirm", ziel))
        return self.schirme.get(ziel, "")

    def committet(self, wt: Path, pfade: list[Path]) -> bool:
        self.aufrufe.append(("committet", wt, tuple(pfade)))
        return self.committet_ergebnis

    def claude_laeuft(self, pane_pid: int) -> bool:
        self.aufrufe.append(("claude_laeuft", pane_pid))
        return self.claude_da

    def jetzt(self) -> float:
        return self.zeit

    def schlafen(self, s: float) -> None:
        self.zeit += s
        haken, self.nach_schlafen = self.nach_schlafen, []
        for h in haken:
            h()

    # --- Handlungen -------------------------------------------------------
    def fenster_starten(self, sitzung: str, name: str, cwd: str, befehl: str) -> str:
        self.aufrufe.append(("fenster_starten", sitzung, name, cwd, befehl))
        self.fenster.append(FensterInfo(sitzung, name, NEU_ZIEL, 9999))
        self.schirme[NEU_ZIEL] = "Claude Code\n❯ \n? for shortcuts"
        return NEU_ZIEL

    def tippen(self, ziel: str, text: str) -> None:
        self.aufrufe.append(("tippen", ziel, text))
        self._fehler("tippen")
        self._fehler(f"tippen:{ziel}:{text[:15]}")
        if ziel == ALT_ZIEL and "HANDOFF_" in text:
            self._dateien_anlegen()
        if ziel == NEU_ZIEL and text == respawn.REMOTE_CONTROL:
            self.schirme[NEU_ZIEL] += "\n❯ /remote-control"
            if self.remote_reaktion == "sofort":
                self._remote_an()
        if ziel == NEU_ZIEL and text == self.prompt_text.strip() and text:
            if self.bildschirm_reagiert == "echo":
                self.schirme[NEU_ZIEL] += f"\n❯ {text}"
            elif self.bildschirm_reagiert:
                self.schirme[NEU_ZIEL] += "\n✻ Lese Handoff… (esc to interrupt)"

    def _remote_an(self) -> None:
        self.schirme[NEU_ZIEL] = (
            "Claude Code\n  ⎿ Remote Control active\n❯ \n? for shortcuts"
        )

    def taste(self, ziel: str, taste: str) -> None:
        self.aufrufe.append(("taste", ziel, taste))
        if (
            ziel == NEU_ZIEL
            and taste == "Enter"
            and self.remote_reaktion == "nach_enter"
        ):
            self._remote_an()

    def fenster_umbenennen(self, ziel: str, name: str) -> None:
        self.aufrufe.append(("fenster_umbenennen", ziel, name))
        self._fehler("fenster_umbenennen")

    def fenster_schliessen(self, ziel: str) -> None:
        self.aufrufe.append(("fenster_schliessen", ziel))
        if self.schliessen_wirkt:
            self.fenster = [f for f in self.fenster if f.ziel != ziel]

    def alte_session_beenden(self, pane_pid: int) -> str:
        self.aufrufe.append(("alte_session_beenden", pane_pid))
        self._fehler("alte_session_beenden")
        return self.beenden_ergebnis

    # --- Hilfen -----------------------------------------------------------
    def _dateien_anlegen(self) -> None:
        tag = time.strftime(
            "%Y-%m-%d", time.localtime(self.zeit + self.tag_versatz * 86400)
        )
        ordner = self.wt / "docs" / "handoffs"
        ordner.mkdir(parents=True, exist_ok=True)
        stempel = self.zeit + 5
        if self.handoff_anlegen:
            pfad = ordner / f"HANDOFF_{tag}_{TICKET}.md"
            pfad.write_text("# Handoff\nStand …\n", encoding="utf-8")
            os.utime(pfad, (stempel, stempel))
        if self.prompt_anlegen:
            pfad = ordner / f"START_{tag}_{TICKET}.txt"
            pfad.write_text(self.prompt_text, encoding="utf-8")
            os.utime(pfad, (stempel, stempel))

    def namen(self) -> list[str]:
        return [
            a[0]
            for a in self.aufrufe
            if a[0]
            not in (
                "fenster_liste",
                "bau_prozesse",
                "bildschirm",
                "committet",
                "claude_laeuft",
            )
        ]

    def getippt(self, ziel: str | None = None) -> list[str]:
        return [
            a[2]
            for a in self.aufrufe
            if a[0] == "tippen" and (ziel is None or a[1] == ziel)
        ]


@pytest.fixture()
def umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    wt_basis = tmp_path / "wt"
    monkeypatch.setenv("BAU_WT_DIR", str(wt_basis))
    wt = wt_basis / f"wt-{TICKET}"
    wt.mkdir(parents=True)
    return repo, wt


def _lauf(repo: Path, fake: FakeWerkzeug, warte_max: float = 600) -> respawn.Ergebnis:
    return respawn.abloesen(repo, SPEC, TICKET, werkzeug=fake, warte_max=warte_max)


# --- Erfolgsweg -----------------------------------------------------------


def test_reihenfolge_a_bis_e(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    erg = _lauf(repo, fake)
    assert erg.exit == 0, erg.zeile
    namen = fake.namen()
    # a) Handoff-Auftrag ins alte Fenster, b) neues Fenster, c) /remote-control, e) Prompt, dann beenden + umbenennen
    assert namen == [
        "tippen",
        "fenster_starten",
        "tippen",
        "tippen",
        "alte_session_beenden",
        "fenster_schliessen",
        "fenster_umbenennen",
    ]
    getippt = [(a[1], a[2]) for a in fake.aufrufe if a[0] == "tippen"]
    assert (
        getippt[0][0] == ALT_ZIEL
        and "HANDOFF_" in getippt[0][1]
        and "START_" in getippt[0][1]
    )
    assert getippt[1] == (NEU_ZIEL, "/remote-control")
    assert getippt[2] == (NEU_ZIEL, START_TEXT)
    assert ("alte_session_beenden", 1111) in fake.aufrufe
    # Altes Fenster zu, bevor das neue „bau N“ heißt — nie zwei Fenster gleichen Namens,
    # und bau.py im alten Fenster kann keine Folge-Runde mehr starten.
    assert ("fenster_schliessen", ALT_ZIEL) in fake.aufrufe
    assert ("fenster_umbenennen", NEU_ZIEL, f"bau {TICKET}") in fake.aufrufe
    start = next(a for a in fake.aufrufe if a[0] == "fenster_starten")
    assert (
        start[1] == "spec-399"
        and start[2] == f"bau {TICKET} neu"
        and start[3] == str(repo)
    )
    assert erg.zeile.startswith(f"respawn #{TICKET}: ok")
    assert "\n" not in erg.zeile


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-Server: bash quotet Windows-Pfade anders")
def test_startbefehl_ohne_prompt_ueber_bau(
    umgebung: tuple[Path, Path],
) -> None:
    # #431 Befund 1: kein nacktes ``claude`` mehr — bau.py liefert die ganze Konfiguration
    # (Beleg im Detail: tests/test_respawn_431_konfig.py).
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    assert _lauf(repo, fake).exit == 0
    befehl = next(a for a in fake.aufrufe if a[0] == "fenster_starten")[4]
    assert START_TEXT not in befehl
    assert befehl.startswith("bash -lc ")
    assert befehl.endswith(f"bau {TICKET} --sofort --ohne-prompt'")
    assert f"REPO={repo}" in befehl


def test_startbefehl_unabhaengig_von_modell_konfig(umgebung: tuple[Path, Path]) -> None:
    # Modell/Effort liest bau.py selbst aus der Repo-Konfig — respawn reicht nichts durch.
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    assert respawn.abloesen(repo, SPEC, TICKET, werkzeug=fake, warte_max=600).exit == 0
    befehl = next(a for a in fake.aufrufe if a[0] == "fenster_starten")[4]
    assert "claude-test-1" not in befehl and "--model" not in befehl


# --- Schritt d: Handoff / Start-Prompt fehlt ---------------------------------


def _pruefe_exit2(fake: FakeWerkzeug, erg: respawn.Ergebnis) -> None:
    assert erg.exit == 2, erg.zeile
    assert START_TEXT not in fake.getippt()
    assert fake.getippt(NEU_ZIEL) == ["/remote-control"]
    assert not any(a[0] == "alte_session_beenden" for a in fake.aufrufe)
    assert ("fenster_schliessen", NEU_ZIEL) in fake.aufrufe
    assert not any(a[0] == "fenster_umbenennen" for a in fake.aufrufe)
    assert "\n" not in erg.zeile


def test_handoff_fehlt_exit2(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, handoff_anlegen=False)
    _pruefe_exit2(fake, _lauf(repo, fake, warte_max=300))


def test_prompt_fehlt_exit2(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, prompt_anlegen=False)
    _pruefe_exit2(fake, _lauf(repo, fake, warte_max=300))


def test_alter_handoff_zaehlt_nicht(umgebung: tuple[Path, Path]) -> None:
    """G5: Handoff von vor ``seit`` (z. B. vom Vormittag) gilt nicht."""
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, handoff_anlegen=False)
    tag = time.strftime("%Y-%m-%d", time.localtime(fake.zeit))
    ordner = wt / "docs" / "handoffs"
    ordner.mkdir(parents=True)
    alt = ordner / f"HANDOFF_{tag}_{TICKET}.md"
    alt.write_text("# alter Handoff\n", encoding="utf-8")
    os.utime(alt, (fake.zeit - 3600, fake.zeit - 3600))
    _pruefe_exit2(fake, _lauf(repo, fake, warte_max=300))


def test_leerer_handoff_zaehlt_nicht(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    original = fake._dateien_anlegen

    def leer() -> None:
        original()
        for pfad in (wt / "docs" / "handoffs").glob("HANDOFF_*"):
            pfad.write_text("", encoding="utf-8")
            os.utime(pfad, (fake.zeit + 5, fake.zeit + 5))

    fake._dateien_anlegen = leer  # type: ignore[method-assign]
    _pruefe_exit2(fake, _lauf(repo, fake, warte_max=300))


# --- Duplikat (A5) -------------------------------------------------------------


def _pruefe_exit3(fake: FakeWerkzeug, erg: respawn.Ergebnis) -> None:
    assert erg.exit == 3, erg.zeile
    assert fake.namen() == []  # nichts getippt, nichts gestartet
    assert "\n" not in erg.zeile


def test_duplikat_neu_fenster_schon_da(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    fake.fenster.append(
        FensterInfo("spec-399", f"bau {TICKET} neu", "=spec-399:@5", 5555)
    )
    _pruefe_exit3(fake, _lauf(repo, fake))


def test_duplikat_zwei_bau_fenster(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    fake.fenster.append(FensterInfo("spec-400", f"bau {TICKET}", "=spec-400:@1", 5555))
    _pruefe_exit3(fake, _lauf(repo, fake))


def test_duplikat_zwei_bau_prozesse(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(
        wt,
        prozesse=[
            f"4242 python3 skripte/bau.py {TICKET}",
            f"4343 python3 scripts/bau.py {TICKET} --sofort",
            "4444 python3 scripts/bau.py 4310",
        ],
    )
    _pruefe_exit3(fake, _lauf(repo, fake))


def test_alte_session_fehlt(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(
        wt, fenster=[FensterInfo("spec-399", "wache 399", "=spec-399:@2", 2222)]
    )
    _pruefe_exit3(fake, _lauf(repo, fake))


def test_anderes_ticket_stoert_nicht(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(
        wt, prozesse=[f"1 python3 bau.py {TICKET}", "2 python3 bau.py 4310"]
    )
    fake.fenster.append(FensterInfo("spec-399", "bau 4310", "=spec-399:@7", 7777))
    assert _lauf(repo, fake).exit == 0


# --- Bildschirm-Prüfung (G4) -------------------------------------------------------


def test_bildschirm_unveraendert_exit1(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, bildschirm_reagiert=False)
    erg = _lauf(repo, fake)
    assert erg.exit == 1, erg.zeile
    assert not any(a[0] == "alte_session_beenden" for a in fake.aufrufe)
    assert not any(a[0] == "fenster_umbenennen" for a in fake.aufrufe)
    assert "\n" not in erg.zeile


def test_neues_fenster_nie_bereit_exit1(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    original = fake.fenster_starten

    def ohne_marker(sitzung: str, name: str, cwd: str, befehl: str) -> str:
        ziel = original(sitzung, name, cwd, befehl)
        fake.schirme[ziel] = "Lade …"
        return ziel

    fake.fenster_starten = ohne_marker  # type: ignore[method-assign]
    erg = _lauf(repo, fake)
    assert erg.exit == 1, erg.zeile
    assert "/remote-control" not in fake.getippt()
    assert ("fenster_schliessen", NEU_ZIEL) in fake.aufrufe
    assert not any(a[0] == "alte_session_beenden" for a in fake.aufrufe)
    assert "\n" not in erg.zeile


def test_dry_run_tut_nichts(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    erg = respawn.abloesen(repo, SPEC, TICKET, werkzeug=fake, dry_run=True)
    assert erg.exit == 0
    assert fake.namen() == []
    assert "\n" not in erg.zeile and "--ohne-prompt" in erg.zeile


# --- CLI ---------------------------------------------------------------------------


def _cli_modul() -> Any:
    spec = importlib.util.spec_from_file_location(
        "to_spawn_cli_431", SKILL / "to_spawn.py"
    )
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


@pytest.mark.parametrize(
    ("einstellung", "erwartet"),
    [({}, 0), ({"handoff_anlegen": False}, 2), ({"prozesse": []}, 0)],
)
def test_cli_eine_zeile(
    umgebung: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    einstellung: dict[str, Any],
    erwartet: int,
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, **einstellung)
    monkeypatch.setattr(respawn, "TmuxWerkzeug", lambda: fake)
    monkeypatch.chdir(repo)
    cli = _cli_modul()
    code = cli.main(["respawn", str(SPEC), str(TICKET), "--warte-max", "120"])
    raus = capsys.readouterr().out
    assert code == erwartet
    assert raus.endswith("\n") and raus.count("\n") == 1, repr(raus)
    assert raus.startswith(f"respawn #{TICKET}")


def test_cli_duplikat_eine_zeile(
    umgebung: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, fenster=[])
    monkeypatch.setattr(respawn, "TmuxWerkzeug", lambda: fake)
    monkeypatch.chdir(repo)
    code = _cli_modul().main(["respawn", str(SPEC), str(TICKET), "--dry-run"])
    raus = capsys.readouterr().out
    assert code == 3
    assert raus.count("\n") == 1


class _TmuxAufzeichnung(respawn.TmuxWerkzeug):
    """Echte ``tippen``-Logik, nur der tmux-Aufruf wird aufgezeichnet."""

    def __init__(self) -> None:
        self.aufrufe: list[tuple[tuple[str, ...], str | None]] = []

    def _tmux(self, *argumente: str, eingabe: str | None = None) -> str:
        self.aufrufe.append((argumente, eingabe))
        return ""


def test_tippen_mehrzeilig_als_paste_dann_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zeilenumbrüche per send-keys wären Enter — der Prompt ginge stückweise ab."""
    monkeypatch.setattr(respawn.time, "sleep", lambda s: None)
    t = _TmuxAufzeichnung()
    t.tippen("=spec-1:@2", "Zeile eins\nZeile zwei")
    befehle = [a[0][0] for a in t.aufrufe]
    assert befehle == ["load-buffer", "paste-buffer", "send-keys"]
    assert t.aufrufe[0][1] == "Zeile eins\nZeile zwei"
    assert "-p" in t.aufrufe[1][0]  # Bracketed Paste: TUI sieht einen Block
    assert t.aufrufe[2][0][-1] == "Enter"
    assert not any("-l" in a[0] for a in t.aufrufe)


def test_trust_dialog_ist_nicht_bereit() -> None:
    """Der Vertrauens-Dialog zeigt auch „❯“ — dort darf kein /remote-control landen."""
    dialog = (
        " Quick safety check: Is this a project you created or one you trust?\n"
        " ❯ No, exit\n   Yes, I trust this folder\n Enter to confirm · Esc to cancel"
    )
    assert not respawn._bereit(dialog)
    assert respawn._bereit('❯ Try "fix typecheck errors"\n  ⏵⏵ bypass permissions on')


# --- Fixrunde 2: Echtlauf 1 + Prüfbefunde Runde 1 ------------------------------------


def _weiter_getippt(fake: FakeWerkzeug) -> bool:
    return any(t == respawn.WEITER_AUFTRAG for t in fake.getippt(ALT_ZIEL))


class _ProzessWerkzeug(respawn.TmuxWerkzeug):
    """Echte ``alte_session_beenden``-Logik mit simulierter Uhr."""

    def __init__(self) -> None:
        self.zeit = 0.0

    def jetzt(self) -> float:
        return self.zeit

    def schlafen(self, s: float) -> None:
        self.zeit += s


def _prozesse(
    monkeypatch: pytest.MonkeyPatch,
    baum: dict[int, list[int]],
    claude: set[int],
    stirbt_bei: str = "SIGTERM",
) -> list[tuple[int, int]]:
    """Stellt Prozessbaum + Signale; gibt die Liste gesendeter Signale zurück."""
    gesendet: list[tuple[int, int]] = []
    tot: set[int] = set()

    def nachkommen(pid: int) -> list[int]:
        raus: list[int] = []
        offen = [pid]
        while offen:
            for kind in baum.get(offen.pop(), []):
                raus.append(kind)
                offen.append(kind)
        return raus

    def kill(pid: int, sig: int) -> None:
        gesendet.append((pid, sig))
        if sig == getattr(respawn.signal, stirbt_bei):
            tot.add(pid)

    # Prozessbaum-Wissen liegt seit #431 Runde 4 in ``to_spawn.prozessbaum``.
    monkeypatch.setattr(prozessbaum, "baum", lambda pid: [pid, *nachkommen(pid)])
    monkeypatch.setattr(prozessbaum, "ist_claude", lambda pid: pid in claude)
    monkeypatch.setattr(prozessbaum, "lebt", lambda pid: pid not in tot)
    monkeypatch.setattr(respawn.os, "kill", kill)
    return gesendet


def test_echtlauf_pane_pid_selbst_ist_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """Echtlauf 1: Fenster direkt mit ``claude`` gestartet → Pane-PID IST claude."""
    gesendet = _prozesse(monkeypatch, {}, {100})
    assert _ProzessWerkzeug().alte_session_beenden(100) == respawn.BEENDET
    assert (100, respawn.signal.SIGTERM) in gesendet


def test_beenden_ganzer_baum_bau_py_vor_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """Befund 8: bau.py darf nach dem Ende von claude keine Folge-Runde starten."""
    gesendet = _prozesse(monkeypatch, {100: [200], 200: [300]}, {300})
    assert _ProzessWerkzeug().alte_session_beenden(100) == respawn.BEENDET
    pids = [pid for pid, _ in gesendet]
    assert 200 in pids and 300 in pids
    assert pids.index(200) < pids.index(300)


def test_beenden_kein_claude_heisst_schon_weg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Befund 5: kein claude-Prozess mehr = alte Session hat sich selbst beendet."""
    _prozesse(monkeypatch, {100: [200]}, set())
    assert _ProzessWerkzeug().alte_session_beenden(100) == respawn.SCHON_WEG


@pytest.mark.skipif(sys.platform == "win32", reason="signal.SIGKILL fehlt auf Windows")
def test_beenden_sigkill_nach_frist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Befund 5: nach BEENDEN_MAX_S ohne Ende → SIGKILL."""
    gesendet = _prozesse(monkeypatch, {100: [300]}, {300}, stirbt_bei="SIGKILL")
    w = _ProzessWerkzeug()
    assert w.alte_session_beenden(100) == respawn.BEENDET
    assert (300, respawn.signal.SIGKILL) in gesendet
    assert w.zeit >= respawn.BEENDEN_MAX_S


def test_nachkommen_pgrep_fehler_wirft(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Befund 5: Baum nicht ermittelbar ist ein Fehler, nicht „keine Kinder“.

    Seit #431 Runde 4 liest ``prozessbaum.baum`` ``/proc`` statt ``pgrep -P``.
    """
    monkeypatch.setattr(prozessbaum, "_PROC", tmp_path / "fehlt")
    with pytest.raises(OSError):
        _ProzessWerkzeug().alte_session_beenden(100)


def test_kern_alte_schon_weg_ist_erfolg(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, beenden_ergebnis=respawn.SCHON_WEG)
    erg = _lauf(repo, fake)
    assert erg.exit == 0, erg.zeile
    assert ("fenster_umbenennen", NEU_ZIEL, f"bau {TICKET}") in fake.aufrufe


def test_kern_alte_lebt_sagt_zwei_sessions(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, beenden_ergebnis=respawn.LEBT)
    erg = _lauf(repo, fake)
    assert erg.exit == 1, erg.zeile
    assert "zwei Sessions" in erg.zeile
    assert not any(a[0] == "fenster_umbenennen" for a in fake.aufrufe)


# --- Echtlauf 1 c): /remote-control muss bestätigt sein -----------------------------


def test_remote_control_nie_bestaetigt_exit1(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, remote_reaktion="nie")
    erg = _lauf(repo, fake)
    assert erg.exit == 1, erg.zeile
    assert "Remote Control" in erg.zeile
    assert START_TEXT not in fake.getippt()
    assert ("fenster_schliessen", NEU_ZIEL) in fake.aufrufe
    assert ("taste", NEU_ZIEL, "Enter") in fake.aufrufe  # ein Nachschub-Enter
    assert _weiter_getippt(fake)
    assert not any(a[0] == "alte_session_beenden" for a in fake.aufrufe)


def test_remote_control_nach_zweitem_enter_ok(umgebung: tuple[Path, Path]) -> None:
    """Slash-Menü schluckt das erste Enter → ein Nachschub-Enter bestätigt."""
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, remote_reaktion="nach_enter")
    erg = _lauf(repo, fake)
    assert erg.exit == 0, erg.zeile
    assert ("taste", NEU_ZIEL, "Enter") in fake.aufrufe


def test_remote_control_erst_nach_ruhe(umgebung: tuple[Path, Path]) -> None:
    """Nicht im ersten Moment tippen, in dem „❯“ erscheint."""
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    zeiten: dict[str, float] = {}
    tippen, starten = fake.tippen, fake.fenster_starten

    def starten_mit_zeit(sitzung: str, name: str, cwd: str, befehl: str) -> str:
        zeiten["start"] = fake.zeit
        return starten(sitzung, name, cwd, befehl)

    def tippen_mit_zeit(ziel: str, text: str) -> None:
        if text == respawn.REMOTE_CONTROL:
            zeiten["remote"] = fake.zeit
        tippen(ziel, text)

    fake.fenster_starten = starten_mit_zeit  # type: ignore[method-assign]
    fake.tippen = tippen_mit_zeit  # type: ignore[method-assign]
    assert _lauf(repo, fake).exit == 0
    assert zeiten["remote"] - zeiten["start"] >= respawn.EINGABE_RUHE_S


def test_remote_control_bestaetigt_erkennung() -> None:
    assert not respawn._remote_bestaetigt(
        "❯ /remote-control\n  /remote-control  Start Remote Control"
    )
    assert respawn._remote_bestaetigt("  ⎿ Remote Control active\n❯ \n? for shortcuts")
    assert not respawn._remote_bestaetigt("❯ \n? for shortcuts")


# --- Befund 1/2/4: Abbruch nach Schritt a — ehrlich und aufgeräumt ------------------


def test_ausnahme_nach_neuem_fenster_schliesst_es(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(
        wt,
        fehler_bei={f"tippen:{NEU_ZIEL}:{START_TEXT[:15]}": RuntimeError("tmux weg")},
    )
    erg = _lauf(repo, fake)
    assert erg.exit == 1, erg.zeile
    assert ("fenster_schliessen", NEU_ZIEL) in fake.aufrufe
    assert _weiter_getippt(fake)
    assert "arbeitet weiter" in erg.zeile and "tmux weg" in erg.zeile
    assert not any(a[0] == "alte_session_beenden" for a in fake.aufrufe)
    assert "\n" not in erg.zeile


def test_abbruch_nach_a_alte_bekommt_weiter(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, handoff_anlegen=False)
    erg = _lauf(repo, fake, warte_max=300)
    assert erg.exit == 2
    assert _weiter_getippt(fake)
    assert "arbeitet weiter" in erg.zeile


def test_abbruch_weiter_tippen_scheitert_ehrliche_zeile(
    umgebung: tuple[Path, Path],
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(
        wt,
        handoff_anlegen=False,
        fehler_bei={
            f"tippen:{ALT_ZIEL}:{respawn.WEITER_AUFTRAG[:15]}": RuntimeError("weg")
        },
    )
    erg = _lauf(repo, fake, warte_max=300)
    assert erg.exit == 2
    assert "arbeitet weiter" not in erg.zeile
    assert "Handarbeit" in erg.zeile


def test_bildschirm_nur_echo_zaehlt_nicht(umgebung: tuple[Path, Path]) -> None:
    """Befund 3: das eingefügte Prompt-Echo ist kein Arbeitszeichen."""
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, bildschirm_reagiert="echo")
    erg = _lauf(repo, fake)
    assert erg.exit == 1, erg.zeile
    assert not any(a[0] == "alte_session_beenden" for a in fake.aufrufe)


def test_g4_scheitert_neues_fenster_zu(umgebung: tuple[Path, Path]) -> None:
    """Befund 4: keine zwei Sessions nach gescheitertem G4."""
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, bildschirm_reagiert=False)
    erg = _lauf(repo, fake)
    assert erg.exit == 1
    assert ("fenster_schliessen", NEU_ZIEL) in fake.aufrufe
    assert _weiter_getippt(fake)


def test_neues_fenster_laesst_sich_nicht_schliessen(
    umgebung: tuple[Path, Path],
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, bildschirm_reagiert=False, schliessen_wirkt=False)
    erg = _lauf(repo, fake)
    assert erg.exit == 1
    assert "zwei Sessions" in erg.zeile and "Handarbeit" in erg.zeile


# --- Befund 6: Aufräumen nach dem Beenden ------------------------------------------


def test_umbenennen_scheitert_zeile_nennt_zustand(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, fehler_bei={"fenster_umbenennen": RuntimeError("rename")})
    erg = _lauf(repo, fake)
    assert erg.exit == 1, erg.zeile
    assert f"bau {TICKET} neu" in erg.zeile and "umbenennen" in erg.zeile.lower()
    assert not _weiter_getippt(fake)


def test_altes_fenster_bleibt_offen_zeile_nennt_es(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, schliessen_wirkt=False)
    erg = _lauf(repo, fake)
    assert erg.exit == 1, erg.zeile
    assert "altes Fenster" in erg.zeile


# --- Befund 7/12: fenster_liste ---------------------------------------------------


class _TmuxFehler(respawn.TmuxWerkzeug):
    def __init__(self, stderr: str) -> None:
        self.stderr = stderr

    def _tmux(self, *argumente: str, eingabe: str | None = None) -> str:
        raise respawn.TmuxFehler("list-panes", self.stderr)


def test_fenster_liste_ohne_server_leer() -> None:
    assert (
        _TmuxFehler("no server running on /tmp/tmux-1000/default").fenster_liste() == []
    )


def test_fenster_liste_anderer_fehler_wirft() -> None:
    with pytest.raises(respawn.TmuxFehler):
        _TmuxFehler("unknown option -- Z").fenster_liste()


def test_fenster_liste_nimmt_aktives_pane() -> None:
    """Befund 12: bei pane-base-index 1 gibt es kein Pane 0."""

    class Roh(respawn.TmuxWerkzeug):
        def _tmux(self, *argumente: str, eingabe: str | None = None) -> str:
            return "spec-1\tbau 7\t@3\t555\t1\nspec-1\tbau 7\t@3\t556\t0\n"

    fenster = Roh().fenster_liste()
    assert fenster == [FensterInfo("spec-1", "bau 7", "=spec-1:@3", 555)]


# --- Befund 13/14: Dateien finden, stabil und nicht leer --------------------------


def test_handoff_mit_anderem_datum_wird_gefunden(umgebung: tuple[Path, Path]) -> None:
    """Befund 13: über Mitternacht schreibt die alte Session mit neuem Datum."""
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, tag_versatz=1)
    erg = _lauf(repo, fake)
    assert erg.exit == 0, erg.zeile
    assert START_TEXT in fake.getippt(NEU_ZIEL)


def test_leerer_start_prompt_exit2(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt, prompt_text="  \n\n")
    erg = _lauf(repo, fake, warte_max=300)
    assert erg.exit == 2, erg.zeile
    assert fake.getippt(NEU_ZIEL) == ["/remote-control"]


def test_start_prompt_erst_bei_stabiler_groesse(umgebung: tuple[Path, Path]) -> None:
    """Befund 14: halb geschriebene Datei nicht tippen."""
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    voll = START_TEXT + " Teil zwei."

    def anhaengen() -> None:
        if not fake.getippt(NEU_ZIEL):
            fake.nach_schlafen.append(anhaengen)
            return
        pfad = next((wt / "docs" / "handoffs").glob("START_*"))
        pfad.write_text(voll + "\n", encoding="utf-8")
        os.utime(pfad, (fake.zeit, fake.zeit))
        fake.prompt_text = voll + "\n"

    fake.nach_schlafen.append(anhaengen)
    erg = _lauf(repo, fake)
    assert erg.exit == 0, erg.zeile
    assert fake.getippt(NEU_ZIEL)[-1] == voll


# --- Befund 15/9/10 ------------------------------------------------------------------


def test_unerwartete_ausnahme_eine_zeile(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(
        wt, fehler_bei={"alte_session_beenden": AttributeError("kaputt")}
    )
    erg = _lauf(repo, fake)
    assert erg.exit == 1
    assert "\n" not in erg.zeile and "kaputt" in erg.zeile
    assert "Handarbeit" in erg.zeile
    # Neue Session hat schon gearbeitet — nicht blind schließen.
    assert ("fenster_schliessen", NEU_ZIEL) not in fake.aufrufe


def test_auftrag_ist_frozen_dataclass(umgebung: tuple[Path, Path]) -> None:
    import dataclasses

    repo, _ = umgebung
    auftrag = respawn.Auftrag(repo, SPEC, TICKET, 600.0, False)
    assert (
        auftrag.name_alt == f"bau {TICKET}" and auftrag.name_neu == f"bau {TICKET} neu"
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        auftrag.ticket = 1  # type: ignore[misc]


def test_tippen_nutzt_naht_schlafen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Befund 10: keine direkte Uhr — die Naht ``schlafen`` gilt auch hier."""

    def verboten(s: float) -> None:
        raise AssertionError("time.sleep direkt benutzt")

    monkeypatch.setattr(respawn.time, "sleep", verboten)
    t = _TmuxAufzeichnung()
    geschlafen: list[float] = []
    t.schlafen = geschlafen.append  # type: ignore[method-assign]
    t.tippen("=spec-1:@2", "x")
    assert geschlafen == [respawn.TIPP_PAUSE_S]


def test_remote_bestaetigt_echter_bildschirm_claude_2_1() -> None:
    """Echtlauf 2: Claude Code 2.1 zeigt „/remote-control is active“, nicht „Remote Control“."""
    echt = (
        " ▝▝   ▝▝   ~/wt/wt-9431 · /rc\n"
        "❯ /remote-control\n"
        "  /remote-control is active · Continue here, on your phone, or at\n"
        "  https://claude.ai/code/session_x\n"
        "────\n"
        "❯ \n"
        "────\n"
    )
    assert respawn._remote_bestaetigt(echt)


def test_remote_slash_menue_echter_bildschirm_zaehlt_nicht() -> None:
    """Offenes Slash-Menü (echte Beschreibung) ist keine Bestätigung."""
    menue = (
        "  /remote-control                 Control this session from your phone or\n"
        "                                  claude.ai/code\n"
        "────\n"
        "❯ /remote-control\n"
        "────\n"
    )
    assert not respawn._remote_bestaetigt(menue)


def test_handoff_auftrag_verlangt_ueberschreiben_alter_dateien() -> None:
    """Echtlauf 3: Dateien aus früherer Runde lagen schon da, Session schrieb nichts neu."""
    text = respawn._handoff_auftrag("docs/handoffs/H.md", "docs/handoffs/S.txt")
    assert "veraltet" in text
    assert "überschreib" in text
