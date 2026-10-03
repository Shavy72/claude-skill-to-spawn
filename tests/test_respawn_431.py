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

from to_spawn import respawn
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
        bildschirm_reagiert: bool = True,
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

    def jetzt(self) -> float:
        return self.zeit

    def schlafen(self, s: float) -> None:
        self.zeit += s

    # --- Handlungen -------------------------------------------------------
    def fenster_starten(self, sitzung: str, name: str, cwd: str, befehl: str) -> str:
        self.aufrufe.append(("fenster_starten", sitzung, name, cwd, befehl))
        self.fenster.append(FensterInfo(sitzung, name, NEU_ZIEL, 9999))
        self.schirme[NEU_ZIEL] = "Claude Code\n❯ \n? for shortcuts"
        return NEU_ZIEL

    def tippen(self, ziel: str, text: str) -> None:
        self.aufrufe.append(("tippen", ziel, text))
        if ziel == ALT_ZIEL and "HANDOFF_" in text:
            self._dateien_anlegen()
        if ziel == NEU_ZIEL and text == START_TEXT and self.bildschirm_reagiert:
            self.schirme[NEU_ZIEL] += "\n● Lese Handoff …"

    def fenster_umbenennen(self, ziel: str, name: str) -> None:
        self.aufrufe.append(("fenster_umbenennen", ziel, name))

    def fenster_schliessen(self, ziel: str) -> None:
        self.aufrufe.append(("fenster_schliessen", ziel))

    def alte_session_beenden(self, pane_pid: int) -> bool:
        self.aufrufe.append(("alte_session_beenden", pane_pid))
        return True

    # --- Hilfen -----------------------------------------------------------
    def _dateien_anlegen(self) -> None:
        tag = time.strftime("%Y-%m-%d", time.localtime(self.zeit))
        ordner = self.wt / "docs" / "handoffs"
        ordner.mkdir(parents=True, exist_ok=True)
        stempel = self.zeit + 5
        if self.handoff_anlegen:
            pfad = ordner / f"HANDOFF_{tag}_{TICKET}.md"
            pfad.write_text("# Handoff\nStand …\n", encoding="utf-8")
            os.utime(pfad, (stempel, stempel))
        if self.prompt_anlegen:
            pfad = ordner / f"START_{tag}_{TICKET}.txt"
            pfad.write_text(START_TEXT + "\n", encoding="utf-8")
            os.utime(pfad, (stempel, stempel))

    def namen(self) -> list[str]:
        return [
            a[0]
            for a in self.aufrufe
            if a[0] not in ("fenster_liste", "bau_prozesse", "bildschirm")
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
    from to_spawn import config

    return respawn.abloesen(
        repo, SPEC, TICKET, config.lade(repo), werkzeug=fake, warte_max=warte_max
    )


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
    assert ("fenster_umbenennen", NEU_ZIEL, f"bau {TICKET}") in fake.aufrufe
    start = next(a for a in fake.aufrufe if a[0] == "fenster_starten")
    assert (
        start[1] == "spec-399"
        and start[2] == f"bau {TICKET} neu"
        and start[3] == str(wt)
    )
    assert erg.zeile.startswith(f"respawn #{TICKET}: ok")
    assert "\n" not in erg.zeile


def test_startbefehl_ohne_prompt_mit_model_und_effort(
    umgebung: tuple[Path, Path],
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    assert _lauf(repo, fake).exit == 0
    befehl = next(a for a in fake.aufrufe if a[0] == "fenster_starten")[4]
    assert START_TEXT not in befehl
    assert "--model claude-opus-5-5" in befehl
    assert "--effort medium" in befehl
    assert f"TO_SPAWN_TICKET={TICKET}" in befehl and f"TO_SPAWN_SPEC={SPEC}" in befehl
    assert f"BAU_TICKET={TICKET}" in befehl and f"TO_SPAWN_LOG_REPO={wt}" in befehl
    assert f"TO_SPAWN_LOG_RUECKFALL={repo}" in befehl
    # Nach ``claude`` folgen nur Schalter mit Wert — kein freies Prompt-Argument.
    nach_claude = befehl.split(" claude ", 1)[1].split()
    assert nach_claude == ["--model", "claude-opus-5-5", "--effort", "medium"]


def test_startbefehl_nimmt_werte_aus_konfig(umgebung: tuple[Path, Path]) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    from to_spawn import config

    konfig = config.lade(repo)
    konfig["modelle"] = {**konfig["modelle"], "ticket": "claude-test-1"}
    konfig["effort"] = {**konfig["effort"], "ticket": "high"}
    assert (
        respawn.abloesen(repo, SPEC, TICKET, konfig, werkzeug=fake, warte_max=600).exit
        == 0
    )
    befehl = next(a for a in fake.aufrufe if a[0] == "fenster_starten")[4]
    assert befehl.endswith("claude --model claude-test-1 --effort high")


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
    from to_spawn import config

    erg = respawn.abloesen(
        repo, SPEC, TICKET, config.lade(repo), werkzeug=fake, dry_run=True
    )
    assert erg.exit == 0
    assert fake.namen() == []
    assert "\n" not in erg.zeile and "--model" in erg.zeile


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
