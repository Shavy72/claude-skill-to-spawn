"""L1 Etappe 1 P1: Hausmeister liest Repos, Wurzeln und Ordner-Präfixe aus der Maschinen-Liste.

(a) ohne Schlüssel exakt die alten Werte, (b) Maschinen-Eintrag greift, (c) Env-Naht
gewinnt, (d) kaputte Liste ⇒ Vorgabe ohne Absturz. Oberste Regel: nie mehr löschen —
Schutzlisten schrumpfen nie, ein nicht eindeutiges Ticket-Repo heißt „nicht abfragbar“.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from to_spawn import hausmeister

ALT_WURZELN = ["~/wt/*", "~/wt-*", "~/duoplus-[0-9]*", "C:/dev/wt-*"]
ALT_REMOTES = [
    "https://github.com/Shavy72/duoplus-management",
    "https://github.com/Shavy72/claude-skill-to-spawn",
]


def _maschine(inhalt: object) -> None:
    datei = Path(os.environ["TO_SPAWN_MASCHINE"])
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(inhalt if isinstance(inhalt, str) else json.dumps(inhalt), encoding="utf-8")


@pytest.fixture
def ohne_naehte(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("WURZELN", "ZWEIG_REPOS", "HAUPTREPOS"):
        monkeypatch.delenv(f"TO_SPAWN_HAUSMEISTER_{name}", raising=False)


def _alte_werte_gelten() -> None:
    assert hausmeister._ticket_repo() == "Shavy72/duoplus-management"
    assert hausmeister._eigene_repos() == {"shavy72/duoplus-management", "shavy72/claude-skill-to-spawn"}
    assert hausmeister._hauptrepo_namen() == ("duoplus-management", "claude-skill-to-spawn")
    assert hausmeister._wurzeln() == [os.path.expanduser(m) for m in ALT_WURZELN]
    assert hausmeister._sicherung_remotes() == ALT_REMOTES
    assert [m.pattern for m in hausmeister._name_muster()] == [m.pattern for m in hausmeister._NAME_MUSTER]
    assert "(?:wt|skill|duoplus)-" in hausmeister._name_muster()[0].pattern


def test_ohne_maschinen_liste_alte_werte(ohne_naehte: None) -> None:
    _alte_werte_gelten()
    # Aliase für Aufrufer/Tests bleiben mit den alten Werten
    assert hausmeister.TICKET_REPO == "Shavy72/duoplus-management"
    assert hausmeister.WURZELN_STANDARD == tuple(ALT_WURZELN)
    assert list(hausmeister._SICHERUNG_REMOTES) == ALT_REMOTES
    assert "duoplus-management" in hausmeister.GESCHUETZTE_ORDNER


@pytest.mark.parametrize("kaputt", ["{kein json", "[]", json.dumps({"repos": "x", "hausmeister": 3})])
def test_kaputte_maschinen_liste_vorgabe(ohne_naehte: None, kaputt: str) -> None:
    _maschine(kaputt)
    _alte_werte_gelten()


def test_maschinen_eintrag_greift(ohne_naehte: None, tmp_path: Path) -> None:
    app = tmp_path / "meine-app"
    (app / ".git").mkdir(parents=True)
    basis = tmp_path / "baustellen"
    _maschine(
        {
            "repos": [{"slug": "Ich/Meine-App", "pfad": str(app), "worktree_basis": str(basis)}],
            "hausmeister": {"wurzeln": ["~/bau/*"], "ordner_praefixe": ["app"]},
        }
    )
    assert hausmeister._ticket_repo() == "Ich/Meine-App"
    assert hausmeister._eigene_repos() == {"ich/meine-app"}
    # Schutzliste wächst, schrumpft nie
    assert hausmeister._hauptrepo_namen() == ("duoplus-management", "claude-skill-to-spawn", "meine-app")
    assert "meine-app" in hausmeister._geschuetzte_ordner()
    assert hausmeister._hauptrepos() == [app]
    assert hausmeister._wurzeln() == [os.path.expanduser("~/bau/*"), str(basis / "*")]
    assert hausmeister._sicherung_remotes() == ["https://github.com/Ich/Meine-App"]
    assert hausmeister._nummer("app-123", hausmeister._name_muster()) == 123
    assert hausmeister._nummer("wt-123", hausmeister._name_muster()) is None


def test_env_naehte_gewinnen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _maschine({"repos": [{"slug": "Ich/App", "pfad": str(tmp_path)}], "hausmeister": {"wurzeln": ["~/bau/*"]}})
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_WURZELN", "/nur/hier/*")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_ZWEIG_REPOS", "https://example.org/x")
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", "/haupt")
    assert hausmeister._wurzeln() == ["/nur/hier/*"]
    assert hausmeister._sicherung_remotes() == ["https://example.org/x"]
    assert hausmeister._hauptrepos() == [Path("/haupt")]


def test_nie_mehr_loeschen(ohne_naehte: None, monkeypatch: pytest.MonkeyPatch) -> None:
    # leere Listen: nichts eigen, keine Wurzel, kein Präfix-Muster — Schutz bleibt
    _maschine({"repos": [], "hausmeister": {"wurzeln": [], "ordner_praefixe": []}})
    assert hausmeister._eigene_repos() == frozenset()
    assert hausmeister._ticket_repo() is None
    assert hausmeister._ticket_daten(1234) is None  # „nicht abfragbar“ ⇒ bleibt
    assert hausmeister._hauptrepo_namen() == ("duoplus-management", "claude-skill-to-spawn")
    assert hausmeister._wurzeln() == []
    assert hausmeister._nummer("wt-1234", hausmeister._name_muster()) is None
    # zwei verschiedene Ticket-Repos: nicht eindeutig ⇒ keine Abfrage
    _maschine({"repos": [{"slug": "A/a"}, {"slug": "B/b"}]})
    assert hausmeister._ticket_repo() is None
    # Basis = Home oder darüber, relative Basis: keine Wurzel
    _maschine({"repos": [{"slug": "A/a", "worktree_basis": b} for b in ("~", "/", "rel/x")]})
    assert hausmeister._wurzeln() == [os.path.expanduser(m) for m in ALT_WURZELN]
    # Präfix ist Text, kein Regex
    _maschine({"hausmeister": {"ordner_praefixe": [".*", 5]}})
    assert hausmeister._nummer("wt-1234", hausmeister._name_muster()) is None
    assert hausmeister._nummer(".*-1234", hausmeister._name_muster()) == 1234
