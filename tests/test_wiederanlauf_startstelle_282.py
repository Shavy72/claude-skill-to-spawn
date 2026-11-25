"""#282: Startstelle + ``bau.py`` nutzen :mod:`to_spawn.wiederanlauf` + offene Reste.

Deckt die noch offenen Teile von Issue #282 ab:
1. Abnahme 1–4: ``startstelle._pruefen`` fragt :func:`wiederanlauf.feststellen`; ``bau.py``
   setzt den Stand-Hinweis als Vorsatz-Block vor den Start-Text, überspringt bei
   ``art == "abschluss"`` das Blocker-Warten, bricht bei ``art == "nichts"`` mit Exit 0
   und „nichts zu tun“ ohne Claude-Start ab und erneuert den Hinweis für Ablösung
   (``--sofort --ohne-prompt``) sowie Staffel-Runde 2.
2. Lokale Zweige (noch nicht auf ``origin``) und Belege im Ticket-Worktree ``wt-<N>``
   werden von :func:`wiederanlauf.feststellen` erkannt.
3. Bedingungs-/Offen-Sätze und offene Checkboxen zählen in :mod:`to_spawn.belege` nicht
   fälschlich als „Klickweg entfällt“.
4. Leiter-Randfall: Stufe 2 (Handoff angefordert) geht beim Zurücksetzen von
   :data:`leiter.ABSCHLUSS_GEMELDET` nicht verloren.
"""

# ruff: noqa: F811 — Fixtures aus Nachbar-Tests werden als Parameter genutzt.
from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
sys.path.insert(0, str(SKILL))

import test_leiter_432 as leiter_test
from test_leiter_abschluss_282 import ABSCHLUSS
from test_wiederanlauf_282 import (
    MIT_KLICKWEG,
    OHNE_KLICKWEG,
    _arbeit,
    _beleg,
    _git,
    _issue,
    welt,  # noqa: F401
)

from to_spawn import (
    aufseher_stand,
    belege,
    leiter,
    leitstand,
    startstelle,
    wiederanlauf,
)


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path, name: str) -> types.ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location(name, SKRIPTE / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[name] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(name, None)
    return modul


def _bau_umgebung(
    welt: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    *,
    blocker_gerufen: list[str] | None = None,
) -> tuple[types.ModuleType, list[list[str]], dict[str, str]]:
    """Echtes ``bau.main`` + echte ``startstelle`` + echtes git/wiederanlauf; gestellt nur gh + Claude."""
    klon = welt["klon"]
    monkeypatch.delenv(startstelle.TEST_OFFEN_ENV, raising=False)
    monkeypatch.setenv(startstelle.SPERR_ORDNER_ENV, str(tmp_path / "sperren"))
    monkeypatch.setenv(startstelle.STAND_ABSTAND_ENV, "0")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)

    bau = _lade_bau(monkeypatch, klon, name)
    vorlage = tmp_path / f"vorlage-{name}.json"
    vorlage.write_text(
        json.dumps({"prompt_template": "SCHRITT 1 BLOCKER für #{N} ({TITLE}). {ANNAHME_GRENZE}"}),
        encoding="utf-8",
    )
    bau.VORLAGE = vorlage
    monkeypatch.setattr(bau, "gh_repo_ermitteln", lambda _repo: "test-org/test-repo")
    monkeypatch.setattr(bau.config, "sicherstellen", lambda _repo: None)
    monkeypatch.setattr(bau, "find_manifest", lambda _t: ({"spec": "9"}, {"title": "Wiederanlauf-Probe"}))
    monkeypatch.setattr(bau, "known_skill_names", lambda: set())
    monkeypatch.setattr(bau, "mcp_catalog", dict)
    monkeypatch.setattr(bau.context_mode, "server_definition", lambda _d: {})
    monkeypatch.setattr(bau.nest, "sandbox_start", lambda *_a, **_k: [])
    monkeypatch.setattr(bau.nest, "git_hooks_einrichten", lambda _r: None)
    monkeypatch.setattr(bau, "auf_speicher_warten", lambda *_a, **_k: None)
    monkeypatch.setattr(bau.speicher, "auf_platte_warten", lambda **_k: None)
    monkeypatch.setattr(bau.vertrauen, "still_sicherstellen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "session_id_setzen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "bau_log_umgebung", lambda *_a, **_k: {})
    monkeypatch.setattr(bau, "staffel_uebergabe", lambda _d: None)
    monkeypatch.setattr(bau, "umzug_anfragen_aufraeumen", lambda _t: tmp_path / f"anfrage-{name}.json")
    monkeypatch.setattr(bau.terminal_maus, "maus_ruhig", lambda: None)

    def _blocker(t: str, _args: object) -> None:
        if blocker_gerufen is not None:
            blocker_gerufen.append(t)

    monkeypatch.setattr(bau, "blocker_schranke", _blocker)

    befehle: list[list[str]] = []
    dateien: dict[str, str] = {}

    def _starte_session(cmd: list[str], umzug_datei: Path) -> tuple[int, None]:
        befehle.append(list(cmd))
        for p in sorted(umzug_datei.parent.glob("prompt*.txt")):
            dateien[p.name] = p.read_text(encoding="utf-8")
        return 0, None

    monkeypatch.setattr(bau, "starte_session", _starte_session)
    return bau, befehle, dateien


# --- Abnahme 2: staging:fertig + fehlender Klickweg → Abschluss, kein Blocker-Warten ------


def test_abnahme2_abschluss_startet_ohne_blocker_warten_mit_hinweis(
    welt: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arbeit(welt["klon"])
    _beleg(welt["klon"], OHNE_KLICKWEG)
    _issue(welt, labels=["staging:fertig"])
    blocker: list[str] = []
    bau, befehle, dateien = _bau_umgebung(welt, tmp_path, monkeypatch, "bau_282_abn2", blocker_gerufen=blocker)

    monkeypatch.setattr(sys, "argv", ["bau.py", "7"])
    assert bau.main() == 0
    assert blocker == [], "Abschluss-Start darf nicht auf Blocker warten"
    assert len(befehle) == 1
    prompt_text = dateien["prompt-runde1.txt"]
    assert prompt_text == befehle[0][-1]
    assert "Wiederanlauf-Stand" in prompt_text
    assert "Klickweg-Beleg" in prompt_text
    assert "nicht neu bauen" in prompt_text
    assert "Überspringe Schritt 1" in prompt_text


# --- Abnahme 3: vollständig fertig → Exit 0, „nichts zu tun“, kein Claude-Start -----------


def test_abnahme3_vollstaendig_fertig_meldet_nichts_zu_tun_und_startet_nicht(
    welt: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _arbeit(welt["klon"])
    _beleg(welt["klon"], MIT_KLICKWEG)
    _issue(welt, labels=["staging:fertig"])
    blocker: list[str] = []
    bau, befehle, _dateien = _bau_umgebung(welt, tmp_path, monkeypatch, "bau_282_abn3", blocker_gerufen=blocker)

    monkeypatch.setattr(sys, "argv", ["bau.py", "7"])
    assert bau.main() == 0
    ausgabe = capsys.readouterr().out
    assert "nichts zu tun" in ausgabe
    assert befehle == [], "Bei vollständig fertigem Ticket darf keine Session starten"
    assert blocker == []


# --- Abnahme 1: offenes Ticket mit Vorarbeit → Fortsetzen-Hinweis im Prompt ---------------


def test_abnahme1_offenes_ticket_mit_zweig_traegt_fortsetzen_hinweis(
    welt: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arbeit(welt["klon"])
    _issue(welt, assignees=["bot-a"])
    blocker: list[str] = []
    bau, befehle, dateien = _bau_umgebung(welt, tmp_path, monkeypatch, "bau_282_abn1", blocker_gerufen=blocker)

    monkeypatch.setattr(sys, "argv", ["bau.py", "7"])
    assert bau.main() == 0
    assert blocker == ["7"], "Offenes Ticket durchläuft weiter die Blocker-Schranke"
    assert len(befehle) == 1
    prompt_text = dateien["prompt-runde1.txt"]
    assert "Wiederanlauf-Stand" in prompt_text
    assert "ticket/7-x" in prompt_text
    assert "bot-a" in prompt_text
    assert "Nicht von vorn beginnen" in prompt_text


# --- Abnahme 4: Ablösung (--sofort --ohne-prompt) + Staffel-Runde 2 -----------------------


def test_abnahme4_abloesung_und_staffel_runde2_tragen_hinweis(
    welt: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arbeit(welt["klon"])
    _beleg(welt["klon"], OHNE_KLICKWEG)
    _issue(welt, labels=["staging:fertig"])
    handoff = tmp_path / "handoff-7.md"
    handoff.write_text("Handoff nach Runde 1", encoding="utf-8")

    bau, befehle, dateien = _bau_umgebung(welt, tmp_path, monkeypatch, "bau_282_abn4")
    monkeypatch.setattr(bau, "staffel_ziel", lambda _u: handoff)
    monkeypatch.setattr(
        bau,
        "staffel_uebergabe",
        lambda _d: {"handoff": str(handoff)} if len(befehle) == 1 else None,
    )

    monkeypatch.setattr(sys, "argv", ["bau.py", "7", "--sofort", "--ohne-prompt"])
    assert bau.main() == 0
    assert len(befehle) == 2
    # Runde 1 (--ohne-prompt): prompt-runde1.txt bleibt leer, prompt.txt trägt den Hinweis.
    assert dateien["prompt-runde1.txt"] == ""
    assert "Klickweg-Beleg" in dateien["prompt.txt"]
    # Runde 2 (Staffel): Prompt-Argument und prompt-runde2.txt tragen genau einen Hinweis + Handoff.
    runde2 = dateien["prompt-runde2.txt"]
    assert runde2 == befehle[1][-1]
    assert "Handoff nach Runde 1" in runde2
    assert "Klickweg-Beleg" in runde2
    assert runde2.count("## Wiederanlauf-Stand") == 1


def test_abnahme4_abloesung_startet_nicht_bei_fertigem_ticket(
    welt: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _arbeit(welt["klon"])
    _beleg(welt["klon"], MIT_KLICKWEG)
    _issue(welt, labels=["staging:fertig"])
    bau, befehle, _dateien = _bau_umgebung(welt, tmp_path, monkeypatch, "bau_282_abn4_zu")

    monkeypatch.setattr(sys, "argv", ["bau.py", "7", "--sofort", "--ohne-prompt"])
    assert bau.main() == 0
    assert "nichts zu tun" in capsys.readouterr().out
    assert befehle == []


def test_abnahme4_staffel_runde2_aktualisiert_lage_ohne_doppelten_block(
    welt: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runde 1 startet offen (fortsetzen); vor Runde 2 wird das Ticket staging:fertig ohne Klickweg."""
    _arbeit(welt["klon"])
    _beleg(welt["klon"], OHNE_KLICKWEG)
    _issue(welt)  # Runde 1: offen
    handoff = tmp_path / "handoff-wechsel.md"
    handoff.write_text("Zwischenstand", encoding="utf-8")

    bau, befehle, dateien = _bau_umgebung(welt, tmp_path, monkeypatch, "bau_282_wechsel")
    monkeypatch.setattr(bau, "staffel_ziel", lambda _u: handoff)

    def _uebergabe(_d: Path) -> dict | None:
        if len(befehle) == 1:
            _issue(welt, labels=["staging:fertig"])  # Lage wechselt vor Runde 2
            return {"handoff": str(handoff)}
        return None

    monkeypatch.setattr(bau, "staffel_uebergabe", _uebergabe)

    monkeypatch.setattr(sys, "argv", ["bau.py", "7"])
    assert bau.main() == 0
    assert len(befehle) == 2
    assert "Nicht von vorn beginnen" in dateien["prompt-runde1.txt"]
    assert "Klickweg-Beleg" not in dateien["prompt-runde1.txt"]
    runde2 = dateien["prompt-runde2.txt"]
    assert "Klickweg-Beleg" in runde2
    assert "Überspringe Schritt 1" in runde2
    assert runde2.count("## Wiederanlauf-Stand") == 1


# --- Lokaler Zweig + Belege im Worktree (wiederanlauf.py) ---------------------------------


def test_lokaler_zweig_ohne_origin_push_ergibt_fortsetzen(welt: dict[str, Path]) -> None:
    """Noch nicht gepushter Zweig ``ticket/7-lokal`` ohne Commit mit (#7) zählt als ``fortsetzen``."""
    klon = welt["klon"]
    _git(klon, "checkout", "-q", "-b", "ticket/7-lokal")
    _git(klon, "checkout", "-q", "main")
    lage = wiederanlauf.feststellen(7, klon)
    assert lage is not None
    assert lage.art == "fortsetzen"
    assert "ticket/7-lokal" in lage.hinweis


def test_belege_im_ticket_worktree_werden_vom_haupt_repo_aus_gefunden(
    welt: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Liegen Belege nur im Ticket-Worktree ``wt-7``, erkennt ``feststellen(7, haupt_repo)`` sie."""
    klon = welt["klon"]
    _arbeit(klon)
    wt_basis = tmp_path / "wts"
    wt_basis.mkdir()
    monkeypatch.setenv("BAU_WT_DIR", str(wt_basis))
    wt = wt_basis / "wt-7"
    _git(klon, "worktree", "add", "-q", str(wt), "ticket/7-x")
    _beleg(wt, MIT_KLICKWEG)
    _issue(welt, labels=["staging:fertig"])

    lage = wiederanlauf.feststellen(7, klon)
    assert lage is not None
    assert lage.art == "nichts", lage


# --- „entfällt“-Erkennung: Bedingungs- und Offen-Sätze aus #282-Kommentar -----------------


@pytest.mark.parametrize(
    "text",
    [
        "Klickweg entfällt, wenn der Test grün ist\n",
        "Klickweg entfällt, wird nachgeholt\n",
        "Klickweg entfällt: unklar\n",
        "Ob Klickweg entfällt, ist unklar\n",
        "- [ ] Klickweg entfällt\n",
        "Klickweg entfällt - Live-Beweis offen\n",
    ],
)
def test_entfaellt_lehnt_bedingung_offen_und_checkbox_ab(tmp_path: Path, text: str) -> None:
    (tmp_path / "7.md").write_text(text, encoding="utf-8")
    assert belege.auswerten(tmp_path, "7").klickweg_entfaellt is False


# --- Leiter-Randfall: Stufe 2 bleibt beim Löschen von ABSCHLUSS_GEMELDET erhalten ---------


@pytest.mark.parametrize(
    ("stufe", "fenster", "still_min"),
    [
        ("fertig_gebaut", aufseher_stand.STILL, 20),
        ("offen", aufseher_stand.ARBEITET, 0),
    ],
    ids=["kurze_stille_fertig_gebaut", "wieder_offen_arbeitet"],
)
def test_leiter_stufe2_bleibt_beim_zuruecksetzen_der_abschluss_meldung_erhalten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stufe: str, fenster: str, still_min: int
) -> None:
    """Stufe 2 (Handoff angefordert) + ABSCHLUSS_GEMELDET: Merker löschen darf Stufe 2 nicht verwerfen."""
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    wt = tmp_path / "wt"
    (wt / "docs" / "handoffs").mkdir(parents=True)
    leitstand.setze_leiter_stufe(leiter_test.TICKET, 2, 100.0)
    leitstand.setze_notiz(leiter._FRAGE_NOTIZ.format(ticket=leiter_test.TICKET), leiter.ABSCHLUSS_GEMELDET)

    class Stufe2Umwelt(leiter_test.FakeUmwelt):
        def lage(self, spec: int, ticket: int, seit: float | None) -> leiter.Lage:
            return dataclasses.replace(super().lage(spec, ticket, seit), anlauf=ABSCHLUSS)

    u = Stufe2Umwelt(wt, still_min=still_min, stufe=stufe, fenster=fenster)
    erg = leiter_test._lauf(u)
    assert erg.exit == leiter.EXIT_OK, erg
    assert leitstand.leiter_eintrag(leiter_test.TICKET) == (2, 100.0)
    assert leiter.gemerkte_frage(leiter_test.TICKET) is None
