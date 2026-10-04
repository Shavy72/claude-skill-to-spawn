"""#431 Prüfpanel Runde 2 — Befunde 2–9, 11, 12 und Offen A/B (``respawn``).

Baut auf dem Fake aus ``test_respawn_431.py`` auf (gestellt ist nur die Außenwelt).
Echte ``TmuxWerkzeug``-Logik wird über ``subprocess.run`` bzw. ``_tmux`` gestellt.
"""

from __future__ import annotations

import inspect
import logging
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_respawn_431 import (  # noqa: E402
    ALT_ZIEL,
    NEU_ZIEL,
    SPEC,
    TICKET,
    FakeWerkzeug,
    _weiter_getippt,
    umgebung,  # noqa: F401  (Fixture)
)

from to_spawn import respawn  # noqa: E402


def _lauf(repo: Path, fake: FakeWerkzeug, warte_max: float = 600) -> respawn.Ergebnis:
    """Ruft die Tür — mit ``konfig`` nur, solange die alte Signatur es verlangt."""
    extra: list[Any] = []
    if "konfig" in inspect.signature(respawn.abloesen).parameters:
        from to_spawn import config

        extra.append(config.lade(repo))
    return respawn.abloesen(
        repo, SPEC, TICKET, *extra, werkzeug=fake, warte_max=warte_max
    )


def _run_wirft(fehler: BaseException) -> Any:
    def run(*args: Any, **kwargs: Any) -> Any:
        raise fehler

    return run


# --- Befund 2: tippen() nicht atomar ------------------------------------------------


def test_b2_handoff_auftrag_tippen_scheitert_zeile_ehrlich(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    """Paste ok, Enter scheitert → Auftrag kann im Eingabefeld stehen: nie „unangetastet“."""
    repo, wt = umgebung
    fake = FakeWerkzeug(
        wt, fehler_bei={f"tippen:{ALT_ZIEL}:Ablösung dieser": RuntimeError("enter")}
    )
    erg = _lauf(repo, fake)
    assert erg.exit == 1, erg.zeile
    assert "unangetastet" not in erg.zeile
    assert _weiter_getippt(fake)


class _TmuxEnterKaputt(respawn.TmuxWerkzeug):
    """``send-keys Enter`` scheitert, alles andere klappt."""

    def __init__(self) -> None:
        self.aufrufe: list[tuple[str, ...]] = []

    def _tmux(self, *argumente: str, eingabe: str | None = None) -> str:
        self.aufrufe.append(argumente)
        if argumente[0] == "send-keys" and argumente[-1] == "Enter":
            raise respawn.TmuxFehler("send-keys", "Enter kaputt")
        return ""

    def schlafen(self, s: float) -> None:
        return None


def test_b2_tippen_enter_scheitert_leert_eingabe() -> None:
    t = _TmuxEnterKaputt()
    with pytest.raises(respawn.TmuxFehler):
        t.tippen("=spec-1:@2", "Auftrag")
    assert ("send-keys", "-t", "=spec-1:@2", "C-u") in t.aufrufe


# --- Befund 3: stderr von tmux geht verloren -----------------------------------------


def test_b3_tmux_fehler_traegt_stderr_und_unterbefehl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fehler = subprocess.CalledProcessError(1, ["tmux"], "", "boom: kein Fenster @7")
    monkeypatch.setattr(respawn.subprocess, "run", _run_wirft(fehler))
    with pytest.raises(RuntimeError) as info:
        respawn.TmuxWerkzeug()._tmux("rename-window", "-t", "=s:@7", "x")
    assert "boom: kein Fenster @7" in str(info.value)
    assert "rename-window" in str(info.value)
    assert info.value.__cause__ is fehler


# --- Befund 4: bildschirm() schluckt Fehler ------------------------------------------


def test_b4_bildschirm_fenster_weg_wirft(monkeypatch: pytest.MonkeyPatch) -> None:
    fehler = subprocess.CalledProcessError(1, ["tmux"], "", "can't find window: @9")
    monkeypatch.setattr(respawn.subprocess, "run", _run_wirft(fehler))
    with pytest.raises(respawn.FensterWeg):
        respawn.TmuxWerkzeug().bildschirm("=spec-1:@9")


@pytest.mark.parametrize(
    "fehler",
    [
        subprocess.CalledProcessError(1, ["tmux"], "", "server exited unexpectedly"),
        subprocess.TimeoutExpired(["tmux"], 30),
    ],
)
def test_b4_bildschirm_anderer_fehler_leer_mit_warnung(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    fehler: BaseException,
) -> None:
    monkeypatch.setattr(respawn.subprocess, "run", _run_wirft(fehler))
    with caplog.at_level(logging.WARNING, logger=respawn.log.name):
        assert respawn.TmuxWerkzeug().bildschirm("=spec-1:@9") == ""
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_b4_neues_fenster_weg_bricht_sofort_ab(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    """Neue Session beendet sich selbst → kein 120-s-Warten auf „bereit“."""
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    original = fake.bildschirm

    def bildschirm(ziel: str) -> str:
        if ziel == NEU_ZIEL:
            raise respawn.FensterWeg("capture-pane", "can't find window: @9")
        return original(ziel)

    fake.bildschirm = bildschirm  # type: ignore[method-assign]
    start = fake.zeit
    erg = _lauf(repo, fake)
    assert erg.exit == 1, erg.zeile
    assert "beendet sich selbst" in erg.zeile
    assert fake.zeit - start < respawn.BEREIT_MAX_S
    assert not any(a[0] == "alte_session_beenden" for a in fake.aufrufe)
    assert _weiter_getippt(fake)


# --- Befund 5: Schritt e zählt Marker gegen „vorher“ ---------------------------------


def test_b5_eingeklappter_paste_mit_markern_im_prompt_zaehlt_als_arbeit(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    """Prompt mit Markerzeichen erscheint eingeklappt — Arbeit trotzdem erkannt."""
    repo, wt = umgebung
    prompt = "● Schritt 1\n● Schritt 2\n● Schritt 3\n✻ Hinweis\n"
    fake = FakeWerkzeug(wt, prompt_text=prompt)
    erg = _lauf(repo, fake)
    assert erg.exit == 0, erg.zeile
    assert any(a[0] == "alte_session_beenden" for a in fake.aufrufe)


def test_b5_arbeitet_nur_zustandsmarker() -> None:
    assert respawn._arbeitet("✻ Lese Handoff… (esc to interrupt)")
    assert respawn._arbeitet("text\n✶ Thinking…\n❯ ")
    # Begrüßung und abgeschlossene Werkzeug-Aufrufe sind keine laufende Arbeit.
    assert not respawn._arbeitet("✻ Welcome to Claude Code!\n⏺ Read(x)\n● fertig\n❯ ")
    assert not respawn._arbeitet("✻ Worked for 3m 12s\n❯ ")
    # Echo in der Eingabezeile zählt nicht.
    assert not respawn._arbeitet("❯ bitte esc to interrupt drücken")


# --- Befund 6: Schritt d prüft nicht, ob der Commit fertig ist -----------------------


def test_b6_beenden_erst_wenn_alte_ruhig(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    original = fake.bildschirm
    beginn = fake.zeit
    beenden_zeit: list[float] = []

    def bildschirm(ziel: str) -> str:
        if ziel == ALT_ZIEL and fake.zeit - beginn < 40:
            return "✻ Committing… (esc to interrupt)"
        return original(ziel)

    def beenden(pane_pid: int) -> str:
        beenden_zeit.append(fake.zeit)
        fake.aufrufe.append(("alte_session_beenden", pane_pid))
        return respawn.BEENDET

    fake.bildschirm = bildschirm  # type: ignore[method-assign]
    fake.alte_session_beenden = beenden  # type: ignore[method-assign]
    erg = _lauf(repo, fake)
    assert erg.exit == 0, erg.zeile
    assert beenden_zeit and beenden_zeit[0] - beginn >= 40


def test_b6_handoff_nicht_committet_steht_in_zeile(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    fake.committet_ergebnis = False
    erg = _lauf(repo, fake)
    assert "nicht committet" in erg.zeile, erg.zeile
    assert any(a[0] == "committet" for a in fake.aufrufe)


# --- Befund 7: Signale räumen nicht auf ----------------------------------------------


class _TestSignal(BaseException):
    pass


def test_b7_sigterm_raeumt_auf_und_gibt_eine_zeile(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(wt)
    original = fake.tippen

    def tippen(ziel: str, text: str) -> None:
        original(ziel, text)
        if text == respawn.REMOTE_CONTROL:
            signal.raise_signal(signal.SIGTERM)

    def test_handler(signum: int, frame: Any) -> None:
        raise _TestSignal()

    fake.tippen = tippen  # type: ignore[method-assign]
    vorher = signal.signal(signal.SIGTERM, test_handler)
    try:
        erg = _lauf(repo, fake)
        assert signal.getsignal(signal.SIGTERM) is test_handler
    finally:
        signal.signal(signal.SIGTERM, vorher)
    assert erg.exit == 1, erg.zeile
    assert "SIGTERM" in erg.zeile
    assert ("fenster_schliessen", NEU_ZIEL) in fake.aufrufe
    assert _weiter_getippt(fake)


def test_b7_keyboardinterrupt_raeumt_auf_und_reicht_weiter(
    umgebung: tuple[Path, Path],  # noqa: F811
) -> None:
    repo, wt = umgebung
    fake = FakeWerkzeug(
        wt, fehler_bei={f"tippen:{NEU_ZIEL}:/remote-control": KeyboardInterrupt()}
    )
    with pytest.raises(KeyboardInterrupt):
        _lauf(repo, fake)
    assert ("fenster_schliessen", NEU_ZIEL) in fake.aufrufe
    assert _weiter_getippt(fake)


# --- Befund 8: fenster_schliessen ohne TimeoutExpired ---------------------------------


def test_b8_fenster_schliessen_timeout_wirft_nicht(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        respawn.subprocess, "run", _run_wirft(subprocess.TimeoutExpired(["tmux"], 30))
    )
    respawn.TmuxWerkzeug().fenster_schliessen("=spec-1:@1")


# --- Befund 9: _ist_claude kennt die npm-Installation nicht --------------------------


def _proc_tabelle(
    monkeypatch: pytest.MonkeyPatch, tabelle: dict[str, bytes | BaseException]
) -> None:
    def proc(pid: int, datei: str) -> bytes:
        wert = tabelle[datei]
        if isinstance(wert, BaseException):
            raise wert
        return wert

    monkeypatch.setattr(respawn, "_proc", proc)


@pytest.mark.parametrize(
    ("comm", "cmdline", "erwartet"),
    [
        (b"claude\n", b"claude\0--model\0x\0", True),
        (b"node\n", b"node\0/usr/local/bin/claude\0--effort\0high\0", True),
        (
            b"node\n",
            b"node\0/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js\0",
            True,
        ),
        (b"node\n", b"node\0/home/x/.claude/plugins/context-mode/start.mjs\0", False),
        (b"bash\n", b"bash\0-lc\0claude\0", False),
    ],
)
def test_b9_ist_claude_erkennt_npm_node(
    monkeypatch: pytest.MonkeyPatch, comm: bytes, cmdline: bytes, erwartet: bool
) -> None:
    _proc_tabelle(monkeypatch, {"comm": comm, "cmdline": cmdline})
    assert respawn._ist_claude(4711) is erwartet


def test_b9_unlesbar_zaehlt_als_lebend_weg_nicht(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _proc_tabelle(monkeypatch, {"comm": PermissionError(), "cmdline": b""})
    assert respawn._ist_claude(4711) is True
    _proc_tabelle(monkeypatch, {"comm": FileNotFoundError(), "cmdline": b""})
    assert respawn._ist_claude(4711) is False


# --- Befund 11: echter LEBT-Pfad -----------------------------------------------------


class _Uhr(respawn.TmuxWerkzeug):
    def __init__(self) -> None:
        self.zeit = 0.0

    def jetzt(self) -> float:
        return self.zeit

    def schlafen(self, s: float) -> None:
        self.zeit += s


def test_b11_alte_session_ueberlebt_sigkill_heisst_lebt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gesendet: list[tuple[int, int]] = []
    monkeypatch.setattr(respawn, "_nachkommen", lambda pid: [200, 300])
    monkeypatch.setattr(respawn, "_ist_claude", lambda pid: pid == 300)
    monkeypatch.setattr(respawn, "_lebt", lambda pid: True)
    monkeypatch.setattr(respawn.os, "kill", lambda pid, sig: gesendet.append((pid, sig)))
    w = _Uhr()
    assert w.alte_session_beenden(100) == respawn.LEBT
    assert (300, signal.SIGTERM) in gesendet and (300, signal.SIGKILL) in gesendet
    assert w.zeit >= respawn.BEENDEN_MAX_S + 5 * respawn.BEENDEN_TAKT_S


# --- Befund 12 + Offen A: Typen, schmale except, kein konfig ---------------------------


def test_b12_typen_und_schmale_ausnahmen() -> None:
    quelle = Path(respawn.__file__).read_text(encoding="utf-8")
    assert respawn.__annotations__.get("REMOTE_MARKER") == "tuple[str, ...]"
    assert (
        inspect.signature(respawn._signal).parameters["sig"].annotation
        == "signal.Signals"
    )
    assert "dict[str, Any]" not in quelle
    # Nur die Tür selbst fängt breit (sie gibt nie eine Ausnahme weiter).
    assert quelle.count("except Exception") == 1


def test_offen_a_kein_konfig_mehr() -> None:
    assert "konfig" not in inspect.signature(respawn.abloesen).parameters
    import dataclasses

    assert "konfig" not in {f.name for f in dataclasses.fields(respawn.Auftrag)}


# --- Offen B: Staffel darf im alten Fenster keine Folge-Runde starten ----------------


def test_offen_b_handoff_auftrag_verbietet_staffel_marker() -> None:
    text = respawn._handoff_auftrag("docs/handoffs/H.md", "docs/handoffs/S.txt")
    assert "Staffel: weiter" in text
    assert "Keine Zeile" in text
