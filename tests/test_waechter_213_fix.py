"""Fixrunde #213 (Prüfpanel 18.09.): keine Fehlalarme mehr, Laufdatei-Delta, Zeitgrenzen, Sperre.

Nachgebildet sind die Fälle aus dem Prüfpanel: Scope-Betreff ``feat(#219): …``,
Tickets „nicht geplant“, reine Doku-Tickets, zweiter Tick nach einem
Wieder-Öffnen, erster Tick mit längst geschlossenen Tickets, Label ``waechter:ok``,
gescheitertes ``git fetch``, Tickets frisch zu (< 15 min), ``deploy_phase`` rot
nur in der gitignorierten Laufdatei, Mutanten-Kopien unter ``docs/``, VPS unlesbar, Checkpoint-Label, ``rate_limit`` ohne Limit-Text.

Echt laufen Git, ``skripte/capo.py``, ``skripte/wache.py`` und die Zustandsdateien;
gestellt sind wie in ``test_waechter_213.py`` nur ``gh``, ``ssh``, Mail und ``claude``.
Anders als dort läuft capo hier OHNE ``TO_SPAWN_WAECHTER_SOFORT`` — also mit
Karenz und Ausgangsstand wie im Betrieb.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_waechter_213 import (  # noqa: F401  (welt = Fixture)
    SPEC,
    _ausfuehrbar,
    _beleg,
    _capo,
    _commit,
    _git,
    _gh_setzen,
    _gh_zustand,
    _iso,
    _kommentare,
    _konfig,
    _log_zeile,
    _mails,
    _text,
    _wache,
    _wache_welt,
    _aufrufe,
    welt,
)


# --- Hilfen ------------------------------------------------------------------


def _zustand_datei(welt: dict[str, Path]) -> Path:
    return welt["zustand"] / f"test_wegwerf_{SPEC}.json"


def _waechter_seit(welt: dict[str, Path], stunden: float = 1.0) -> None:
    """Zustand so setzen, als liefe der Wächter schon ``stunden`` (erster Tick lange her)."""
    datei = _zustand_datei(welt)
    daten = json.loads(datei.read_text(encoding="utf-8")) if datei.is_file() else {}
    beginn = datetime.now(timezone.utc) - timedelta(hours=stunden)
    daten["erster_tick"] = beginn.isoformat(timespec="seconds")
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(json.dumps(daten), encoding="utf-8")


def _frisch_zu(
    welt: dict[str, Path], nummer: str = "901", minuten: float = 20, **felder: object
) -> None:
    """Wächter läuft seit 1 h, Ticket wurde vor ``minuten`` geschlossen (nach der Karenz)."""
    _waechter_seit(welt)
    _gh_setzen(
        welt,
        nummer,
        state="closed",
        closed_at=_iso(timedelta(minutes=minuten)),
        **felder,
    )


def _zustand(welt: dict[str, Path], nummer: str = "901") -> str:
    return _gh_zustand(welt)["issues"][nummer]["state"]


# --- E1: Scope-Betreff zählt ---------------------------------------------------


def test_scope_betreff_zaehlt_als_ticket_commit(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat(#901): Belegbilder als WebP [skip ci]", _beleg())
    _frisch_zu(welt)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _zustand(welt) == "closed"
    assert _kommentare(welt, "901") == []
    assert "commit_ohne_nummer" not in ergebnis.stdout


def test_ohne_nummer_im_betreff_bleibt_verstoss(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: Belegbilder als WebP [skip ci]", _beleg())
    _frisch_zu(welt)
    _capo(welt)
    assert _zustand(welt) == "open"
    assert _kommentare(welt, "901")[0].startswith("Wächter: commit_ohne_nummer")


# --- 1: nicht geplant / Duplikat --------------------------------------------------


@pytest.mark.parametrize("grund", ["not_planned", "duplicate"])
def test_nicht_geplant_keine_regeln(welt: dict[str, Path], grund: str) -> None:
    _commit(welt["repo"], "chore: irgendwas", {})
    _frisch_zu(welt, state_reason=grund)
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _zustand(welt) == "closed"
    assert _kommentare(welt, "901") == []
    assert grund in ergebnis.stdout


# --- E4: reines Doku-Ticket ---------------------------------------------------------


def test_doku_ticket_braucht_keine_belegseite(welt: dict[str, Path]) -> None:
    _commit(
        welt["repo"],
        "docs: Anleitung Wächter (#901) [skip ci]",
        {"docs/anleitung_waechter.md": "# Anleitung\n", "HINWEIS.md": "x\n"},
    )
    _frisch_zu(welt)
    ergebnis = _capo(welt)
    assert _kommentare(welt, "901") == [], _text(ergebnis)
    assert "beweis_fehlt" not in ergebnis.stdout


def test_code_ticket_ohne_beleg_bleibt_verstoss(welt: dict[str, Path]) -> None:
    _commit(
        welt["repo"],
        "feat: Knopf (#901)",
        {"docs/anleitung.md": "x\n", "web/knopf.py": "x = 1\n"},
    )
    _frisch_zu(welt)
    _capo(welt)
    assert any(k.startswith("Wächter: beweis_fehlt") for k in _kommentare(welt, "901"))


# --- E2: je Schließen genau einmal wieder öffnen ---------------------------------------
# Entscheidung 21.09. (#213, verify-hard Lauf 3): Die Einmal-Sperre je Ticket+Regel ist
# weg, Spec Zeile 13 kennt keine Ausnahme. Der alte Fall
# test_zweites_schliessen_nach_reopen_oeffnet_nicht_nochmal prüfte die Sperre und gilt
# nicht mehr; das neue Soll (zweites Schließen öffnet wieder) steht in
# tests/test_waechter_213_spec_streng.py. Hier bleibt der Teil, der weiter gilt:
# derselbe geschlossene Stand wird nur einmal wieder geöffnet, auch über mehrere Ticks.


@pytest.mark.skip(
    reason="Verhalten per Entscheidung 21.09. umgekehrt (#213): jedes erneute Schließen "
    "öffnet wieder — Soll in tests/test_waechter_213_spec_streng.py"
)
def test_zweites_schliessen_nach_reopen_oeffnet_nicht_nochmal(
    welt: dict[str, Path],
) -> None:
    _commit(welt["repo"], "feat: ohne Nummer", _beleg())
    _frisch_zu(welt, minuten=40)
    _capo(welt)
    assert _zustand(welt) == "open"
    assert len(_kommentare(welt, "901")) == 1
    # Die Session schließt erneut, ohne den Verstoß zu beheben.
    _gh_setzen(welt, "901", state="closed", closed_at=_iso(timedelta(minutes=20)))
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _zustand(welt) == "closed"
    kommentare = _kommentare(welt, "901")
    assert len(kommentare) == 2 and "nicht noch einmal" in kommentare[1]
    assert "schon einmal wieder geöffnet" in ergebnis.stdout
    _capo(welt)
    assert _zustand(welt) == "closed"
    assert len(_kommentare(welt, "901")) == 2


def test_zweiter_tick_nach_reopen_oeffnet_nicht_nochmal(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: ohne Nummer", _beleg())
    _frisch_zu(welt, minuten=40)
    _capo(welt)
    assert _zustand(welt) == "open"
    assert len(_kommentare(welt, "901")) == 1
    # Zweiter Tick, ohne dass jemand erneut geschlossen hat.
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _zustand(welt) == "open"
    assert len(_kommentare(welt, "901")) == 1, _text(ergebnis)


def test_label_waechter_ok_ueberspringt_regeln(welt: dict[str, Path]) -> None:
    """Label hebt seit 21.09. nur das Wieder-Öffnen auf, nicht das Erkennen (#213)."""
    _commit(welt["repo"], "feat: ohne Nummer", {})
    _frisch_zu(welt, labels=["waechter:ok"])
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert _zustand(welt) == "closed"
    assert _kommentare(welt, "901") == []
    assert "waechter:ok" in ergebnis.stdout
    assert "VERSTOSS commit_ohne_nummer" in ergebnis.stdout


# --- E3: erster Tick = Ausgangsstand ------------------------------------------------


def test_erster_tick_oeffnet_alte_tickets_nicht(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: ohne Nummer", {})
    _gh_setzen(welt, "901", closed_at=_iso(timedelta(days=2)))
    erste = _capo(welt)
    assert erste.returncode == 0, _text(erste)
    assert _zustand(welt) == "closed"
    assert _kommentare(welt, "901") == []
    assert "alt:" in erste.stdout and "commit_ohne_nummer" in erste.stdout
    assert "würde wieder öffnen" not in _capo(welt, "--dry-run").stdout
    _capo(welt)
    assert _zustand(welt) == "closed"
    # Neues Schließ-Ereignis nach dem ersten Tick wird wie üblich geprüft.
    _waechter_seit(welt)
    _gh_setzen(welt, "901", closed_at=_iso(timedelta(minutes=20)))
    _capo(welt)
    assert _zustand(welt) == "open"


def test_erster_tick_probe_schlaegt_kein_wieder_oeffnen_vor(
    welt: dict[str, Path],
) -> None:
    _commit(welt["repo"], "feat: ohne Nummer", {})
    _gh_setzen(welt, "901", closed_at=_iso(timedelta(hours=6)))
    ergebnis = _capo(welt, "--dry-run")
    assert "würde wieder öffnen" not in ergebnis.stdout, _text(ergebnis)
    assert "alt:" in ergebnis.stdout


# --- 5: fetch-Fehler + Karenz --------------------------------------------------------


def test_fetch_fehler_setzt_regeln_aus(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: ohne Nummer", {})
    _frisch_zu(welt)
    _git(welt["repo"], "remote", "set-url", "origin", str(welt["tmp"] / "weg.git"))
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 1, _text(ergebnis)
    assert "FEHLER: fetch — Regeln ausgesetzt" in ergebnis.stdout
    assert _zustand(welt) == "closed"
    assert _kommentare(welt, "901") == []


def test_karenz_frisch_geschlossen_prueft_spaeter(welt: dict[str, Path]) -> None:
    _commit(welt["repo"], "feat: ohne Nummer", {})
    _commit(welt["repo"], "feat: B (#902)", _beleg("902"))
    _waechter_seit(welt)
    _gh_setzen(welt, "902", state="closed", closed_at=_iso(timedelta(minutes=30)))
    _gh_setzen(welt, "901", closed_at=_iso(timedelta(minutes=5)))
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert "prüfe später" in ergebnis.stdout
    assert _zustand(welt) == "closed" and _kommentare(welt, "901") == []
    assert "SPEC FERTIG" not in ergebnis.stdout


# --- 6: Laufdatei-Delta -------------------------------------------------------------


def _ignoriert_laufdateien(welt: dict[str, Path]) -> None:
    _commit(
        welt["repo"],
        "chore: gitignore",
        {".gitignore": ".to-spawn/*\n!.to-spawn/config.json\n"},
    )


def test_gate_rot_nur_in_gitignorierter_laufdatei_im_worktree(
    welt: dict[str, Path],
) -> None:
    _ignoriert_laufdateien(welt)
    wt = welt["wt"] / "wt-902"
    subprocess.run(
        ["git", "clone", "-q", str(welt["tmp"] / "fern.git"), str(wt)], check=True
    )
    lauf = wt / ".to-spawn" / "bau_log" / "902.jsonl"
    lauf.parent.mkdir(parents=True)
    zeile = _log_zeile(
        "deploy_phase", lauf="7", phase="ende", ergebnis="rot", grund="pytest rot"
    )
    lauf.write_text(zeile, encoding="utf-8")
    assert _git(wt, "check-ignore", ".to-spawn/bau_log/902.jsonl")  # wirklich ignoriert
    erste = _capo(welt)
    assert erste.returncode == 0, _text(erste)
    assert "deploy_phase" in erste.stdout
    assert [m["art"] for m in _mails(welt)] == ["gate_rot"]
    # Die Session überträgt die Zeile später ins versionierte Log → kein zweites Mal.
    _commit(welt["repo"], "docs: Log (#902)", {"docs/agents/bau_log/902.jsonl": zeile})
    zweite = _capo(welt)
    assert "deploy_phase" not in zweite.stdout, _text(zweite)
    assert [m["art"] for m in _mails(welt)] == ["gate_rot"]


def test_blockiert_in_laufdatei_des_repo_ordners(welt: dict[str, Path]) -> None:
    lauf = welt["repo"] / ".to-spawn" / "bau_log" / "902.jsonl"
    lauf.parent.mkdir(parents=True, exist_ok=True)
    lauf.write_text(_log_zeile("blockiert", grund="Handy aus"), encoding="utf-8")
    ergebnis = _capo(welt)
    assert "blockiert" in ergebnis.stdout, _text(ergebnis)
    assert [m["art"] for m in _mails(welt)] == ["live_beweis_blockiert"]
    with lauf.open("a", encoding="utf-8") as fh:
        fh.write(_log_zeile("entscheidung", frage="Farbe", wahl="blau", grund="x"))
    zweite = _capo(welt).stdout
    assert "Farbe · blau" in zweite and "Handy aus" not in zweite


def test_kaputte_log_zeile_wird_gewarnt(
    welt: dict[str, Path], caplog: pytest.LogCaptureFixture
) -> None:
    from to_spawn import capo

    _commit(welt["repo"], "docs: Log", {"docs/agents/bau_log/902.jsonl": "{kaputt\n"})
    with caplog.at_level(logging.WARNING, logger="to_spawn.capo"):
        zeilen = capo.log_vom_ref(welt["repo"], "origin/master", 902)
    assert zeilen == [{"typ": "kaputt"}]
    assert "Kaputte Bau-Log-Zeile" in caplog.text


# --- 7 + 14: Session tot — Mail unabhängig vom Kommentar, Fehler = Exit 1 ---------------


def _verwaist(welt: dict[str, Path], **felder: object) -> None:
    _commit(welt["repo"], "feat: Bauteil (#901)", _beleg())
    _gh_setzen(
        welt, "902", assignees=["bau"], updated_at=_iso(timedelta(hours=5)), **felder
    )


def test_mail_session_tot_trotz_kommentar_fehler(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _verwaist(welt)
    monkeypatch.setenv("GH_STUB_FEHLER", "comment")
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 1, _text(ergebnis)
    assert "FEHLER: kommentieren gescheitert" in ergebnis.stdout
    assert [m["art"] for m in _mails(welt)] == ["session_tot"]


def test_mail_fehler_ist_fehlerzeile_und_wird_wiederholt(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _verwaist(welt)
    monkeypatch.setenv("MAIL_FAKE_EXIT", "1")
    erste = _capo(welt)
    assert erste.returncode == 1, _text(erste)
    assert "FEHLER: Mail session_tot" in erste.stdout
    assert len(_kommentare(welt, "902")) == 1
    monkeypatch.setenv("MAIL_FAKE_EXIT", "0")
    zweite = _capo(welt)
    assert zweite.returncode == 0, _text(zweite)
    assert "Mail session_tot verschickt" in zweite.stdout
    _capo(welt)
    assert len(_mails(welt)) == 2  # ein Fehlversuch + ein Versand, dann Ruhe
    assert len(_kommentare(welt, "902")) == 1


# --- 8 + E5: nur echte Testdateien, Trailer erlaubt Entfernen ----------------------------


def test_mutanten_kopie_unter_docs_ist_kein_test(welt: dict[str, Path]) -> None:
    mutante = "docs/verify-hard/x/mutants/test_a.py"
    _commit(welt["repo"], "test: Mutante", {mutante: "def test_m():\n    assert 0\n"})
    _commit(welt["repo"], "chore: Mutanten weg (#901)", {mutante: None, **_beleg()})
    _frisch_zu(welt)
    ergebnis = _capo(welt)
    assert _kommentare(welt, "901") == [], _text(ergebnis)
    assert "test_ersetzt" not in ergebnis.stdout


@pytest.mark.skip(
    reason="Ausnahme per Entscheidung 21.09. entfernt (#213): Tests werden nie entfernt — "
    "Soll in tests/test_waechter_213_spec_streng.py"
)
def test_trailer_test_entfernt_erlaubt(welt: dict[str, Path]) -> None:
    _commit(
        welt["repo"], "test: alt", {"tests/test_a.py": "def test_alt():\n    pass\n"}
    )
    _commit(
        welt["repo"],
        "refactor: Umbau (#901)\n\nTest-entfernt: test_alt prüfte die alte Tabelle, die es nicht mehr gibt",
        {"tests/test_a.py": "def test_neu():\n    pass\n", **_beleg()},
    )
    _frisch_zu(welt)
    ergebnis = _capo(welt)
    assert _kommentare(welt, "901") == [], _text(ergebnis)


# Entscheidung 21.09. (#213): test_trailer_test_entfernt_erlaubt prüfte die Ausnahme
# „Commit-Trailer Test-entfernt: erlaubt das Entfernen“. Die Ausnahme ist weg (Spec
# Zeile 18: Tests nur ergänzt); das neue Soll steht in test_waechter_213_spec_streng.py.


def test_ohne_trailer_bleibt_test_ersetzt(welt: dict[str, Path]) -> None:
    _commit(
        welt["repo"], "test: alt", {"tests/test_a.py": "def test_alt():\n    pass\n"}
    )
    _commit(
        welt["repo"],
        "refactor: Umbau (#901)\n\nTest entfernt, weil alt",
        {"tests/test_a.py": "def test_neu():\n    pass\n", **_beleg()},
    )
    _frisch_zu(welt)
    _capo(welt)
    assert any(k.startswith("Wächter: test_ersetzt") for k in _kommentare(welt, "901"))


# --- 9: VPS unlesbar ------------------------------------------------------------------


def test_vps_unlesbar_kein_spec_fertig(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _konfig(welt, vps={"ssh": "vps-test", "pfad": "/opt/x", "deploy_pfade": ["web/"]})
    _commit(welt["repo"], "feat: A (#901)", _beleg("901"))
    _commit(welt["repo"], "feat: B (#902)", _beleg("902"))
    _frisch_zu(welt, "901")
    _frisch_zu(welt, "902")
    binaer = welt["tmp"] / "bin"
    binaer.mkdir(exist_ok=True)
    _ausfuehrbar(binaer / "ssh", "#!/bin/sh\necho 'Verbindung weg' >&2\nexit 255\n")
    import os

    monkeypatch.setenv("PATH", f"{binaer}{os.pathsep}{os.environ['PATH']}")
    ergebnis = _capo(welt)
    assert ergebnis.returncode == 1, _text(ergebnis)
    assert "FEHLER: VPS-HEAD nicht lesbar" in ergebnis.stdout
    assert "SPEC FERTIG" not in ergebnis.stdout
    assert _mails(welt) == []


# --- 10: verwaist nicht bei Checkpoint oder blockiert ------------------------------------


def test_checkpoint_label_ist_nicht_verwaist(welt: dict[str, Path]) -> None:
    _verwaist(welt, labels=["checkpoint:human"])
    ergebnis = _capo(welt)
    assert "session_verwaist" not in ergebnis.stdout, _text(ergebnis)
    assert _kommentare(welt, "902") == [] and _mails(welt) == []


def test_juengste_zeile_blockiert_ist_nicht_verwaist(welt: dict[str, Path]) -> None:
    alt = timedelta(hours=5)
    zeile = json.loads(_log_zeile("blockiert", grund="wartet auf David"))
    zeile["ts"] = (datetime.now(timezone.utc) - alt).isoformat(timespec="seconds")
    _commit(
        welt["repo"],
        "docs: Log",
        {"docs/agents/bau_log/902.jsonl": json.dumps(zeile) + "\n"},
        alter=alt,
    )
    _verwaist(welt)
    ergebnis = _capo(welt)
    assert "session_verwaist" not in ergebnis.stdout, _text(ergebnis)
    assert _kommentare(welt, "902") == []


# --- 15: Sperre gegen Parallelläufe --------------------------------------------------


def test_zweiter_capo_lauf_wartet_nicht_endlos(
    welt: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from to_spawn import capo

    monkeypatch.setenv("TO_SPAWN_CAPO_SPERRE_S", "0")
    datei = _zustand_datei(welt)
    with capo.sperre(datei, warten_s=0) as frei:
        assert frei
        ergebnis = _capo(welt)
    assert ergebnis.returncode == 1, _text(ergebnis)
    assert "läuft schon" in ergebnis.stdout
    danach = _capo(welt)
    assert danach.returncode == 0, _text(danach)


# --- P1: git mit Zeitgrenze -------------------------------------------------------------


def test_git_hat_zeitgrenze(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from to_spawn import capo

    grenzen: list[float | None] = []

    def fake_run(*args: object, **kwargs: object) -> object:
        grenzen.append(kwargs.get("timeout"))  # type: ignore[arg-type]
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(capo.subprocess, "run", fake_run)
    assert capo._git(tmp_path, "fetch", "-q", "origin")[0] != 0
    assert capo._git(tmp_path, "status")[0] != 0
    assert grenzen == [60, 30]


# --- 11 + 12 + 13 + P4/P5: Aufsicht der Wächter-Session ------------------------------------


def test_rate_limit_ohne_limit_text_ist_kein_limit() -> None:
    from to_spawn import waechter_lauf

    zeile = {
        "type": "assistant",
        "isApiErrorMessage": True,
        "error": "rate_limit",
        "message": {
            "content": [
                {"type": "text", "text": "API Error: Rate limit reached for requests"}
            ]
        },
    }
    assert waechter_lauf.ist_limit_zeile(zeile) is False


def test_aufsicht_liest_nach_halt_noch_einmal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from test_waechter_213 import LIMIT_ZEILE
    from to_spawn import waechter_lauf

    datei = tmp_path / "t.jsonl"
    datei.write_text("kein json\n" + json.dumps(LIMIT_ZEILE) + "\n", encoding="utf-8")
    funde: list[str] = []
    aufsicht = waechter_lauf.Aufsicht(datei, 0, 0.05, funde.append)
    aufsicht.halt.set()  # Session schon zu Ende, bevor die Aufsicht einmal lief
    with caplog.at_level(logging.DEBUG, logger="to_spawn.waechter_lauf"):
        aufsicht.start()
        aufsicht.join(timeout=5)
    assert funde and "session limit" in funde[0]
    assert "unlesbar" in caplog.text


def test_aufsicht_warnt_wenn_transkript_fehlt(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from to_spawn import waechter_lauf

    aufsicht = waechter_lauf.Aufsicht(
        tmp_path / "fehlt.jsonl", 0, 0.02, lambda _t: None, warte_s=0.05
    )
    with caplog.at_level(logging.WARNING, logger="to_spawn.waechter_lauf"):
        aufsicht.start()
        time.sleep(0.3)
        aufsicht.halt.set()
        aufsicht.join(timeout=5)
    assert caplog.text.count("Transkript") == 1


def test_wechsel_schreibt_versioniertes_log(welt: dict[str, Path]) -> None:
    ergebnis = _wache(welt["repo"], env=_wache_welt(welt))
    assert ergebnis.returncode == 0, _text(ergebnis)
    fest = welt["repo"] / "docs" / "agents" / "bau_log" / f"{SPEC}.jsonl"
    zeilen = [json.loads(z) for z in fest.read_text(encoding="utf-8").splitlines()]
    assert "waechter_modell" in [z["typ"] for z in zeilen]


def test_wechsel_scheitert_nie_am_log(welt: dict[str, Path]) -> None:
    # Versioniertes Log nicht schreibbar (Ordner statt Datei) → Neustart trotzdem.
    (welt["repo"] / "docs" / "agents" / "bau_log" / f"{SPEC}.jsonl").mkdir(parents=True)
    ergebnis = _wache(welt["repo"], env=_wache_welt(welt))
    assert ergebnis.returncode == 0, _text(ergebnis)
    assert len(_aufrufe(welt)) == 2
    assert [m["art"] for m in _mails(welt)] == ["waechter_ausweich"]
    assert "Bau-Log" in ergebnis.stderr
