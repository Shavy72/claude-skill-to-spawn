"""Weg-Tests für #812: Eingriffs-Leiter findet ``bau <N>`` in ``spec-<kurz>-<S>`` und ``spec-<S>``.

Prüft den vollständigen Weg über :func:`leiter.eingreifen`, :class:`leiter._EchteUmwelt`
und :func:`aufseher_stand.ticket_lage` mit echten Git-Repos und echtem Leitstand in
``tmp_path`` — nur tmux-Antworten, GitHub-Sub-Issues und das Tipp-Werkzeug sind Attrappen,
damit der Test unter Linux und Windows identisch läuft.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

import test_aufseher_stand_430 as stand_test

from to_spawn import (
    aufseher_stand,
    leiter,
    leitstand,
    prozessbaum,
    respawn,
    tmux_aufruf,
)

SPEC = 41
JETZT = stand_test.JETZT


class _FakeWerkzeug:
    """Zeichnet getippte Befehle auf; gilt auch unter Windows als tippfähig (tmux-Weg)."""

    def __init__(self) -> None:
        self.getippt: list[tuple[str, str]] = []

    def tippen(self, ziel: str, text: str) -> None:
        self.getippt.append((ziel, text))


class _SitzungsWelt:
    """Fake-tmux mit mehreren Sessions (inkl. ``#{session_path}``) und Fake-GitHub."""

    def __init__(self) -> None:
        # Sitzungsname → (session_path, [(window_id, ticket, activity, pane_text), ...])
        self.sitzungen: dict[str, tuple[Path, list[tuple[str, int, float, str]]]] = {}
        self.tickets: dict[int, dict[str, Any]] = {}
        self.uhr = JETZT

    def ticket(self, nummer: int, *, zu: bool = False) -> None:
        self.tickets[nummer] = {
            "number": nummer,
            "state": "closed" if zu else "open",
            "title": f"Ticket {nummer}",
            "comments": 0,
        }

    def fenster_hinzufuegen(
        self,
        sitzung: str,
        pfad: Path,
        window_id: str,
        ticket: int,
        *,
        still_min: int,
        bild: str | None = None,
    ) -> None:
        text = bild if bild is not None else stand_test.bildschirm("✻ Baked for 40s")
        eintrag = (window_id, ticket, self.uhr - still_min * 60, text)
        if sitzung not in self.sitzungen:
            self.sitzungen[sitzung] = (pfad, [eintrag])
        else:
            self.sitzungen[sitzung][1].append(eintrag)

    def tmux(self, args: list[str]) -> str | None:
        if args[0] == "list-windows":
            ziel = args[args.index("-t") + 1].lstrip("=")
            if ziel not in self.sitzungen:
                return None
            pfad, eintraege = self.sitzungen[ziel]
            zeilen = [f"@0\twache {SPEC}\t{int(self.uhr)}\t{pfad}"]
            for wid, ticket, aktiv, _ in eintraege:
                zeilen.append(f"{wid}\tbau {ticket}\t{int(aktiv)}\t{pfad}")
            return "\n".join(zeilen)
        if args[0] == "capture-pane":
            ziel = args[args.index("-t") + 1].lstrip("=")
            sitzung, _, wid = ziel.partition(":")
            if sitzung not in self.sitzungen:
                return None
            for e_wid, _, _, text in self.sitzungen[sitzung][1]:
                if e_wid == wid:
                    return text
            return None
        if args[0] == "display-message":
            return None
        raise AssertionError(f"unerwarteter tmux-Aufruf: {args}")

    def kinder(self, spec: int) -> list[dict[str, Any]]:
        assert spec == SPEC
        return [dict(t) for t in self.tickets.values()]

    def quellen(self, repo: Path) -> aufseher_stand.Quellen:
        return aufseher_stand.Quellen(
            repo=repo,
            kinder=self.kinder,
            tmux=self.tmux,
            jetzt=lambda: self.uhr,
        )


def _git_repo(ordner: Path, origin: str, wt_basis: Path) -> Path:
    ordner.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(ordner)], check=True, **prozessbaum.fensterlos())
    subprocess.run(
        ["git", "-C", str(ordner), "remote", "add", "origin", origin],
        check=True,
        **prozessbaum.fensterlos(),
    )
    (ordner / ".to-spawn").mkdir(exist_ok=True)
    (ordner / ".to-spawn" / "config.json").write_text(
        json.dumps({"worktree_basis": str(wt_basis)}),
        encoding="utf-8",
    )
    return ordner


@pytest.fixture()
def umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, _SitzungsWelt, _FakeWerkzeug]:
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(tmp_path / "leitstand"))
    monkeypatch.setenv("TO_SPAWN_AUFSEHER_STAND", str(tmp_path / "stand"))
    repo = _git_repo(
        tmp_path / "to-spawn-probe-4-mit",
        "https://github.com/test/to-spawn-probe-4-mit.git",
        tmp_path / "wts",
    )
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    wz = _FakeWerkzeug()
    monkeypatch.setattr(respawn, "werkzeug_fuer_rechner", lambda: wz)
    return repo, _SitzungsWelt(), wz


def test_leiter_exit_findet_bau_fenster_in_repo_sitzung(
    umgebung: tuple[Path, _SitzungsWelt, _FakeWerkzeug],
) -> None:
    """#812: Session heißt ``spec-<kurz>-<S>`` (ohne altes ``spec-<S>``) → Leiter tippt ``/exit``."""
    repo, welt, wz = umgebung
    sitzung = tmux_aufruf.sitzung_name(repo, SPEC)
    assert sitzung == f"spec-to-spawn-probe-4-mit-{SPEC}"

    welt.ticket(42, zu=True)
    welt.fenster_hinzufuegen(sitzung, repo, "@1", 42, still_min=5)

    u = leiter._EchteUmwelt(repo, welt.quellen(repo))
    erg = leiter.eingreifen(repo, SPEC, 42, umwelt=u)

    assert erg.exit == 0, erg.zeile
    assert wz.getippt == [(f"={sitzung}:@1", leiter.EXIT_TEXT)]
    assert leitstand.leiter_eintrag(42)[0] == 4
    assert "/exit getippt" in erg.zeile


def test_leiter_anstupsen_findet_bau_fenster_in_repo_sitzung(
    umgebung: tuple[Path, _SitzungsWelt, _FakeWerkzeug],
) -> None:
    """#812: Auch Anstupsen bei offenem stillem Ticket trifft ``spec-<kurz>-<S>`` samt Beleg."""
    repo, welt, wz = umgebung
    sitzung = tmux_aufruf.sitzung_name(repo, SPEC)

    welt.ticket(43, zu=False)
    welt.fenster_hinzufuegen(sitzung, repo, "@2", 43, still_min=25)

    u = leiter._EchteUmwelt(repo, welt.quellen(repo))
    erg = leiter.eingreifen(repo, SPEC, 43, umwelt=u)

    assert erg.exit == 0, erg.zeile
    assert len(wz.getippt) == 1
    assert wz.getippt[0][0] == f"={sitzung}:@2"
    assert leitstand.leiter_eintrag(43)[0] == 1
    assert "Beleg #43" in erg.zeile


def test_leiter_exit_findet_bau_fenster_in_alter_sitzung(
    umgebung: tuple[Path, _SitzungsWelt, _FakeWerkzeug],
) -> None:
    """#812: Bestehende eigene Alt-Session ``spec-<S>`` funktioniert weiterhin."""
    repo, welt, wz = umgebung
    sitzung_alt = tmux_aufruf.sitzung_alt(SPEC)
    assert sitzung_alt == f"spec-{SPEC}"

    welt.ticket(44, zu=True)
    welt.fenster_hinzufuegen(sitzung_alt, repo, "@3", 44, still_min=5)

    u = leiter._EchteUmwelt(repo, welt.quellen(repo))
    erg = leiter.eingreifen(repo, SPEC, 44, umwelt=u)

    assert erg.exit == 0, erg.zeile
    assert wz.getippt == [(f"={sitzung_alt}:@3", leiter.EXIT_TEXT)]
    assert leitstand.leiter_eintrag(44)[0] == 4


def test_leiter_ignoriert_fremde_alte_sitzung_und_trifft_eigene_repo_sitzung(
    umgebung: tuple[Path, _SitzungsWelt, _FakeWerkzeug],
    tmp_path: Path,
) -> None:
    """Fremdes ``spec-<S>`` darf nicht stören — Leiter trifft das eigene ``spec-<kurz>-<S>``."""
    repo, welt, wz = umgebung
    fremd = _git_repo(
        tmp_path / "anderes-repo",
        "https://github.com/fremd/anderes-repo.git",
        tmp_path / "wts-fremd",
    )
    sitzung_eigen = tmux_aufruf.sitzung_name(repo, SPEC)
    sitzung_alt = tmux_aufruf.sitzung_alt(SPEC)

    welt.ticket(42, zu=True)
    welt.fenster_hinzufuegen(sitzung_alt, fremd, "@9", 42, still_min=10)
    welt.fenster_hinzufuegen(sitzung_eigen, repo, "@7", 42, still_min=5)

    u = leiter._EchteUmwelt(repo, welt.quellen(repo))
    erg = leiter.eingreifen(repo, SPEC, 42, umwelt=u)

    assert erg.exit == 0, erg.zeile
    assert wz.getippt == [(f"={sitzung_eigen}:@7", leiter.EXIT_TEXT)]


def test_ticket_lage_liefert_kein_ziel_aus_fremder_alter_sitzung(
    umgebung: tuple[Path, _SitzungsWelt, _FakeWerkzeug],
    tmp_path: Path,
) -> None:
    """Ohne eigene Session darf ``ticket_lage`` kein Ziel aus fremdem ``spec-<S>`` liefern."""
    repo, welt, _wz = umgebung
    fremd = _git_repo(
        tmp_path / "anderes-repo",
        "https://github.com/fremd/anderes-repo.git",
        tmp_path / "wts-fremd",
    )
    welt.ticket(42, zu=True)
    welt.fenster_hinzufuegen(tmux_aufruf.sitzung_alt(SPEC), fremd, "@9", 42, still_min=10)

    blick = aufseher_stand.ticket_lage(SPEC, 42, welt.quellen(repo))

    assert blick.lage is not None
    assert blick.lage.fenster == aufseher_stand.FENSTER_UNBEKANNT
    assert blick.ziel is None
