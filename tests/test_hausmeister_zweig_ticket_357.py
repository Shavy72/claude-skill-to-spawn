"""#357: eine Stelle für „Zweigname + origin → Ticket-Repo + Nummer“ (:func:`hausmeister.zweig_zu_ticket`).

Muster, Datums-Sperre und Zuordnung origin → Ticket-Repo liegen dahinter; ``_ticket_daten``
fragt nur das Repo ab, das diese Stelle liefert. Gestellt ist nur die Maschinen-Liste und ``gh``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from to_spawn import hausmeister

APP = "Shavy72/duoplus-management"
SKILL = "Shavy72/claude-skill-to-spawn"


@pytest.fixture(autouse=True)
def maschine(monkeypatch: pytest.MonkeyPatch) -> None:
    eintraege = [
        {"slug": APP, "ticket_repo": APP, "pfad": ""},
        {"slug": SKILL, "ticket_repo": SKILL, "pfad": ""},
    ]
    monkeypatch.setattr(hausmeister, "_maschinen_repos", lambda: eintraege)


@pytest.mark.parametrize(
    ("zweig", "slug", "ziel"),
    [
        ("ticket/1525-x", APP, (APP, 1525)),
        ("l4/176-x", APP, (APP, 176)),
        ("l4/176-x", SKILL, (SKILL, 176)),
        ("skill-103", APP, None),
        ("l1/2026-11-21-stand", SKILL, None),
        ("ticket/2026-13-x", APP, None),
        ("wip/993675", APP, None),
        ("ticket/1525-x", "", None),
        ("fix/1406", SKILL, (SKILL, 1406)),
        ("ticket/1525-x", "Shavy72/fremd", None),
    ],
)
def test_zweig_zu_ticket_je_form(zweig: str, slug: str, ziel: tuple[str, int] | None) -> None:
    assert hausmeister.zweig_zu_ticket(zweig, slug) == ziel


def test_app_nummer_im_skill_repo_meint_das_skill_repo() -> None:
    """Regel aus #357: origin entscheidet, auch wenn die Nummer eigentlich ein App-Ticket ist."""
    assert hausmeister.zweig_zu_ticket("ticket/1525-x", SKILL) == (SKILL, 1525)


def test_ticket_daten_fragt_nur_das_gelieferte_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    gefragt: list[tuple[int, str | None]] = []
    monkeypatch.setattr(hausmeister, "_gh_ticket", lambda nr, repo: gefragt.append((nr, repo)) or {"state": "OPEN"})
    for zweig, slug in [("ticket/5-x", SKILL), ("l4/176-x", APP), ("ticket/5-x", "Shavy72/fremd"), ("main", APP)]:
        hausmeister._ticket_daten(5, hausmeister.zweig_zu_ticket(zweig, slug))
    assert gefragt == [(5, SKILL), (176, APP)]


def test_zweig_fertig_nicht_zuordenbar_fragt_gh_nie(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    ort = SimpleNamespace(remote="https://github.com/Shavy72/fremd", cwd=Path("."), haupt="origin/HEAD")
    monkeypatch.setattr(hausmeister, "_zweig_ort", lambda repo, o: ort)
    monkeypatch.setattr(hausmeister, "_zweig_stand", lambda o, z: "a" * 40)
    monkeypatch.setattr(hausmeister, "_zweig_fehlende", lambda o, s: [])
    monkeypatch.setattr(hausmeister, "_gh_ticket", lambda nr, repo: pytest.fail(f"gh gefragt: {repo}"))
    fertig, _, grund, _ = hausmeister.zweig_fertig(ort.remote, "ticket/5-x", holen=False)
    assert fertig is False and "zählt nicht" in grund


def test_ordnername_und_zweig_widersprechen_sich_bleibt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hausmeister, "_git_oder_none", lambda echt, *a: "ticket/7-x")
    with pytest.raises(hausmeister._Bleibt, match="widersprechen sich"):
        hausmeister._ticket_nummer(Path("wt-8"), Path("wt-8"))
