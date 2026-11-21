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
from test_hausmeister_1368 import git, welt  # noqa: F401 — ``welt`` ist ein Fixture (#1368)

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
    assert hausmeister._eigene_repos() == {
        "shavy72/duoplus-management",
        "shavy72/claude-skill-to-spawn",
    }
    assert hausmeister._hauptrepo_namen() == (
        "duoplus-management",
        "claude-skill-to-spawn",
    )
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


def test_maschinen_eintrag_greift(ohne_naehte: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # Fix-Runde 1: worktree_basis nur unter Home
    app = tmp_path / "meine-app"
    (app / ".git").mkdir(parents=True)
    basis = tmp_path / "baustellen"
    _maschine(
        {
            "repos": [
                {
                    "slug": "Ich/Meine-App",
                    "pfad": str(app),
                    "worktree_basis": str(basis),
                }
            ],
            "hausmeister": {"wurzeln": ["~/bau/*"], "ordner_praefixe": ["app"]},
        }
    )
    assert hausmeister._ticket_repo() is None  # Fix-Runde 1: ohne ticket_repo nie den slug raten
    assert hausmeister._eigene_repos() == {"ich/meine-app"}
    # Schutzliste wächst, schrumpft nie
    assert hausmeister._hauptrepo_namen() == (
        "duoplus-management",
        "claude-skill-to-spawn",
        "meine-app",
    )
    assert "meine-app" in hausmeister._geschuetzte_ordner()
    assert hausmeister._hauptrepos() == [app]
    assert hausmeister._wurzeln() == [os.path.expanduser("~/bau/*"), str(basis / "*")]
    assert hausmeister._sicherung_remotes() == ["https://github.com/Ich/Meine-App"]
    assert hausmeister._nummer("app-123", hausmeister._name_muster()) == 123
    assert hausmeister._nummer("wt-123", hausmeister._name_muster()) is None


def test_env_naehte_gewinnen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _maschine(
        {
            "repos": [{"slug": "Ich/App", "pfad": str(tmp_path)}],
            "hausmeister": {"wurzeln": ["~/bau/*"]},
        }
    )
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
    assert hausmeister._hauptrepo_namen() == (
        "duoplus-management",
        "claude-skill-to-spawn",
    )
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


# --- Fix-Runde 1 (#66) -------------------------------------------------------------


def _home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    heim = tmp_path / "heim"
    heim.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(heim))
    return heim


@pytest.mark.parametrize(
    ("repos", "erwartet"),
    [
        # ein Repo ohne ticket_repo: nie den slug raten ⇒ nicht abfragbar
        ([{"slug": "Ich/App"}], None),
        ([{"slug": "Ich/App", "ticket_repo": "  "}], None),
        # ticket_repo != slug zählt
        ([{"slug": "Ich/App", "ticket_repo": "Ich/Tickets"}], "Ich/Tickets"),
        # gleiches ticket_repo bei zwei Repos ⇒ eindeutig
        (
            [
                {"slug": "A/a", "ticket_repo": "T/t"},
                {"slug": "B/b", "ticket_repo": "T/t"},
            ],
            "T/t",
        ),
        # leeres ticket_repo nur, wenn der slug selbst das Ticket-Repo der anderen ist (Vorgabe)
        ([{"slug": "T/t"}, {"slug": "B/b", "ticket_repo": "T/t"}], "T/t"),
        ([{"slug": "X/x"}, {"slug": "B/b", "ticket_repo": "T/t"}], None),
        # leerer slug ohne ticket_repo: unklar ⇒ nicht abfragbar
        ([{"slug": ""}, {"slug": "B/b", "ticket_repo": "T/t"}], None),
    ],
)
def test_ticket_repo_nie_aus_slug(ohne_naehte: None, repos: list[dict], erwartet: str | None) -> None:
    _maschine({"repos": repos})
    assert hausmeister._ticket_repo() == erwartet


def test_gh_aufruf_mit_ticket_repo_und_keiner_bei_none(ohne_naehte: None, monkeypatch: pytest.MonkeyPatch) -> None:
    aufrufe: list[list[str]] = []
    monkeypatch.setattr(
        hausmeister.gh,
        "json_lauf",
        lambda args, **kw: aufrufe.append(args) or {"state": "OPEN"},
    )
    _maschine({"repos": [{"slug": "Ich/App", "ticket_repo": "Ich/Tickets"}]})
    assert hausmeister._ticket_daten(7) == {"state": "OPEN"}
    assert aufrufe == [["issue", "view", "7", "-R", "Ich/Tickets", "--json", "state,closedAt"]]
    _maschine({"repos": [{"slug": "Ich/App"}]})
    assert hausmeister._ticket_daten(7) is None
    assert len(aufrufe) == 1


def test_repos_eintrag_kein_objekt_faellt_weg(ohne_naehte: None) -> None:
    _maschine({"repos": ["Ich/App", 3, {"slug": "Ich/App", "ticket_repo": "Ich/App"}]})
    assert hausmeister._eigene_repos() == {"ich/app"}
    assert hausmeister._ticket_repo() == "Ich/App"


def test_hauptrepos_leere_naht_heisst_keine(ohne_naehte: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / ".git").mkdir(parents=True)
    _maschine({"repos": [{"slug": "Ich/App", "pfad": str(app)}]})
    monkeypatch.setenv("TO_SPAWN_HAUSMEISTER_HAUPTREPOS", "")
    # Naht gewinnt auch leer: keine Hauptrepos ⇒ der Zweig-Lauf fasst nichts an (weniger löschen)
    assert hausmeister._hauptrepos() == []


@pytest.mark.parametrize("praefixe", [[""], ["wt", ""]])
def test_leere_praefixe_kein_allesfresser(ohne_naehte: None, praefixe: list[str]) -> None:
    _maschine({"hausmeister": {"ordner_praefixe": praefixe}})
    muster = hausmeister._name_muster()
    assert hausmeister._nummer("-1234", muster) is None
    assert hausmeister._nummer("x-1234", muster) is None
    assert hausmeister._nummer("wt-1234", muster) == (1234 if "wt" in praefixe else None)


def _basis_wurzeln(basis: str) -> list[str]:
    _maschine(
        {
            "repos": [{"slug": "A/a", "worktree_basis": basis}],
            "hausmeister": {"wurzeln": []},
        }
    )
    return hausmeister._wurzeln()


def test_basis_home_unterordner_gilt(ohne_naehte: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    heim = _home(monkeypatch, tmp_path)
    assert _basis_wurzeln("~/wt/app") == [str(heim / "wt" / "app" / "*")]


@pytest.mark.parametrize("basis", ["", "  ", "~", "/", "rel/x", "~/..", "/tmp/wt", "{tmp}/draussen"])
def test_basis_nicht_unter_home_keine_wurzel(
    ohne_naehte: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, basis: str
) -> None:
    _home(monkeypatch, tmp_path)
    assert _basis_wurzeln(basis.format(tmp=tmp_path)) == []


def test_basis_symlink_aus_home_heraus_keine_wurzel(
    ohne_naehte: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    heim = _home(monkeypatch, tmp_path)
    (tmp_path / "draussen").mkdir()
    (heim / "link").symlink_to(tmp_path / "draussen")
    (heim / "selbst").symlink_to(heim)
    assert _basis_wurzeln("~/link") == []
    assert _basis_wurzeln("~/selbst") == []


def test_basis_glob_zeichen_escaped(ohne_naehte: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    heim = _home(monkeypatch, tmp_path)
    (heim / "wt[1]" / "wt-5").mkdir(parents=True)
    (heim / "wt1" / "wt-6").mkdir(parents=True)
    [muster] = _basis_wurzeln("~/wt[1]")
    assert hausmeister.glob.glob(muster) == [str(heim / "wt[1]" / "wt-5")]


@pytest.mark.parametrize("wurzel", ["~/*", "~", "/tmp/*", "/*", "~/../*", "rel/*", "{tmp}/x/*", "~/link/*"])
def test_wurzel_aus_liste_nur_unter_home(
    ohne_naehte: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, wurzel: str
) -> None:
    heim = _home(monkeypatch, tmp_path)
    (tmp_path / "draussen").mkdir()
    (heim / "link").symlink_to(tmp_path / "draussen")
    _maschine(
        {
            "repos": [],
            "hausmeister": {"wurzeln": [wurzel.format(tmp=tmp_path), "~/bau/*"]},
        }
    )
    assert hausmeister._wurzeln() == [str(heim / "bau" / "*")]


def test_vorgabe_wurzeln_bleiben_auch_in_der_liste(ohne_naehte: None) -> None:
    # C:/dev/wt-* liegt nicht unter Home, ist aber Vorgabe ⇒ bleibt (Verhalten gleich)
    _maschine({"repos": [], "hausmeister": {"wurzeln": ALT_WURZELN}})
    assert hausmeister._wurzeln() == [os.path.expanduser(m) for m in ALT_WURZELN]


# --- Weg-Test durch _regeln/aufraeumen (echtes Git, gestellt nur gh) ---------------


@pytest.fixture
def welt_liste(welt, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):  # noqa: F811
    """Welt aus #1368, aber Wurzeln aus der Maschinen-Liste statt Env-Naht; eigener tmux-Server."""
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path))
    for name in ("WURZELN", "ZWEIG_REPOS", "HAUPTREPOS"):
        monkeypatch.delenv(f"TO_SPAWN_HAUSMEISTER_{name}", raising=False)
    return welt


def _liste(welt, slug: str, *, pfad: Path | None = None) -> None:  # noqa: F811
    _maschine(
        {
            "repos": [{"slug": slug, "pfad": str(pfad or welt.haupt), "ticket_repo": slug}],
            "hausmeister": {"wurzeln": [f"{welt.heim}/wt/*"]},
        }
    )


def test_weg_maschinen_liste_entscheidet(welt_liste) -> None:
    w = welt_liste
    wt = w.worktree(11)
    w.ticket(11, "CLOSED", 48)
    # Gegenprobe: eigenes Repo aus der Liste, Wurzel aus der Liste ⇒ löschbar (trocken)
    _liste(w, "Shavy72/duoplus-management")
    r = w.aufraeumen(wt)
    assert (r.art, r.ticket) == ("gelöscht", 11), r.grund
    # fremdes origin (Liste kennt nur Ich/App) ⇒ bleibt
    _liste(w, "Ich/App")
    r = w.aufraeumen(wt)
    assert r.art == "bleibt" and r.grund.startswith("fremdes Repo"), r.grund
    # Hauptrepo aus der Liste (Ordnername) ⇒ geschützt
    _liste(w, "Shavy72/duoplus-management", pfad=w.tmp / "irgendwo" / wt.name)
    r = w.aufraeumen(wt)
    assert (r.art, r.grund) == ("bleibt", "geschützter Ordner")
    assert wt.is_dir()


def test_weg_geschuetzter_ordner_bleibt(welt_liste) -> None:
    w = welt_liste
    _liste(w, "Shavy72/duoplus-management")
    klon = w.heim / "wt" / "obsidian"
    git(
        w.tmp,
        "clone",
        "-q",
        "https://github.com/Shavy72/duoplus-management",
        str(klon),
    )
    r = w.aufraeumen(klon)
    assert (r.art, r.grund) == ("bleibt", "geschützter Ordner")
