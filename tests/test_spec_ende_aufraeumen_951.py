"""#951: Spec-Ende — Spec schließen und automatisch aufräumen (beide Varianten).

Prüft alle vier Anforderungen aus Issue #951 (Probe-Lauf 7, Lauf 3, Skill-#426, B11):
1. Nach SPEC FERTIG + Live schließt das Spec-Issue in beiden Varianten (-mit und -ohne).
2. Danach wird automatisch aufgeräumt: saubere Ticket-Worktrees weg, gemergte ``ticket/*``-Zweige
   (lokal + remote) weg, tmux-Sitzung der Spec zu (Live-Server bleiben unberührt), und keine
   Lock-Reste wie ``docs/agents/FEHLERKATALOG_spawn.md.lock`` ungespeichert im Klon.
3. Worktree-Pfad immer mit Repo-Namen (``~/wt/<repo>/wt-<N>`` / ``C:/dev/<repo>/wt-<N>``, nie flach).
4. Abnahme-Ticket (ohne Code) gilt als fertig, wenn „passt“ und Live grün sind; keine Bau-Runde
   nur für Commit ``(#N)`` oder Belegdatei.
"""

# ruff: noqa: F811 — Fixtures kommen aus test_waechter_213 / test_aufraeumen_schluss_1370.
from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from test_aufraeumen_schluss_1370 import _zustand, hm  # noqa: F401
from test_komplett_abnahme_853 import _abnahme_hinzufuegen
from test_komplett_je_spec_425 import _befehl
from test_komplett_live_tuer_456 import _live_befehl
from test_waechter_213 import (  # noqa: F401
    SPEC,
    _beleg,
    _commit,
    _gh_setzen,
    _gh_zustand,
    _iso,
    _konfig,
    welt,
)

from to_spawn import befund, capo, config, hausmeister, tmux_aufruf, vorfall

GH = "test/wegwerf"
STAGING = {"url": "http://127.0.0.1:8001/", "ohne": False}
OHNE_STAGING = {"url": "", "ohne": True}


def _git(ort: Path, *args: str) -> str:
    lauf = subprocess.run(
        ["git", "-C", str(ort), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return lauf.stdout.strip()


def _kommentar(welt: dict[str, Path], ticket: str, body: str) -> None:
    daten = _gh_zustand(welt)
    liste = daten.setdefault("kommentare", {}).setdefault(ticket, [])
    liste.append(
        {
            "id": len(liste) + 1,
            "autor": "nutzer",
            "body": body,
            "created_at": _iso(),
            "author_association": "OWNER",
        }
    )
    welt["gh"].write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")


def _ticket_worktree_mit_merge(repo: Path, wt_basis: Path, ticket: int, zweig: str) -> Path:
    """Legt einen echten Ticket-Worktree an, pusht den Zweig und mergt ihn nach origin/main."""
    wt = wt_basis / f"wt-{ticket}"
    _git(repo, "worktree", "add", "-q", "-b", zweig, str(wt))
    (wt / f"feature_{ticket}.py").write_text(f"WERT = {ticket}\n", encoding="utf-8")
    for rel, text in _beleg(str(ticket)).items():
        ziel = wt / rel
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(text, encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", f"feat: Ticket {ticket} (#{ticket})")
    _git(wt, "push", "-q", "origin", zweig)
    # In den Hauptzweig mergen und pushen — Worktree bleibt sauber stehen, Zweig bleibt lokal + remote.
    _git(repo, "merge", "-q", "--no-ff", zweig, "-m", f"merge {zweig} (#{ticket})")
    _git(repo, "push", "-q", "origin", "HEAD")
    return wt


def _probe_spec_vorbereiten(
    welt: dict[str, Path],
    *,
    mit_staging: bool,
) -> tuple[Path, Path, list[Path]]:
    """Richtet eine Probe-Spec (mit oder ohne Staging) samt Worktrees, Zweigen und Abnahme #903 ein."""
    repo = welt["repo"]
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    alt = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
    if ".to-spawn/*" not in alt:
        exclude.write_text(alt + "\n.to-spawn/*\n!.to-spawn/config.json\n", encoding="utf-8")

    zaehl = welt["tmp"] / "komplett.txt"
    live_zaehl = welt["tmp"] / "live.txt"
    _konfig(
        welt,
        staging=STAGING if mit_staging else OHNE_STAGING,
        pruefung={"schnell": "true", "komplett": _befehl(zaehl)},
        live={"befehl": _live_befehl(live_zaehl), "url": "http://127.0.0.1:18513/"},
    )
    wt_basis = welt["wt"] / repo.name
    wt_basis.mkdir(parents=True, exist_ok=True)
    wt1 = _ticket_worktree_mit_merge(repo, wt_basis, 901, "ticket/901-eins")
    wt2 = _ticket_worktree_mit_merge(repo, wt_basis, 902, "ticket/902-zwei")
    _gh_setzen(welt, "901", state="closed", closed_at=_iso())
    _gh_setzen(welt, "902", state="closed", closed_at=_iso())
    _abnahme_hinzufuegen(welt, nummer=903)
    manifest_text = json.dumps(
        {"spec": int(SPEC), "tickets": {str(t): {"title": f"Ticket {t}"} for t in (901, 902, 903)}},
        ensure_ascii=False,
        indent=2,
    )
    _commit(
        repo,
        "docs: Manifest + Abschluss-Marker",
        {
            f"docs/agents/manifests/spec-{SPEC}.json": manifest_text + "\n",
            f"docs/agents/rueckblick_{SPEC}.md": "Rückblick: keine Vorschläge\n",
            f"docs/agents/thermo_{SPEC}.md": "# Thermo\n\n- nichts auffällig\n",
        },
    )
    return zaehl, live_zaehl, [wt1, wt2]


# --- 3. Worktree-Pfad immer mit Repo-Namen --------------------------------------


@pytest.mark.parametrize(
    ("plattform", "erwartet_praefix"),
    [
        ("linux", lambda heim, name: f"{heim.as_posix()}/wt/{name}/wt-7"),
        ("win32", lambda _heim, name: f"C:/dev/{name}/wt-7"),
    ],
)
def test_worktree_pfad_immer_mit_repo_namen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plattform: str,
    erwartet_praefix,
) -> None:
    """Ohne worktree_basis/BAU_WT_DIR liegt der Worktree immer unter <basis>/<repo>/wt-<N>."""
    heim = tmp_path / "heim"
    heim.mkdir()
    repo = tmp_path / "probe-mit"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("USERPROFILE", str(heim))
    monkeypatch.delenv("BAU_WT_DIR", raising=False)
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.setattr(sys, "platform", plattform)

    pfad = config.worktree_pfad(7, repo)
    assert pfad == erwartet_praefix(heim, "probe-mit"), pfad
    assert "/wt/wt-7" not in pfad and pfad != "C:/dev/wt-7"


# --- 2b. Kein Lock-Rest FEHLERKATALOG_spawn.md.lock im Klon ---------------------


def test_katalog_pflegen_hinterlaesst_keine_lock_datei_im_klon(tmp_path: Path) -> None:
    """katalog_pflegen darf weder mit noch ohne Katalog eine .lock-Datei im Repo liegen lassen."""
    repo = tmp_path / "klon"
    repo.mkdir()
    _git(repo, "init", "-q")
    fall = vorfall.Vorfall(
        klasse="prozess",
        symptom="Stillstand",
        ursache="Test",
        loesung="Beheben",
    )

    # Fall A: Repo ohne FEHLERKATALOG_spawn.md (wie Probe-Repo)
    capo.katalog_pflegen(repo, {}, [fall])
    locks = list(repo.rglob("*.lock"))
    assert locks == [], f"Lock-Reste im Klon ohne Katalog: {locks}"
    assert _git(repo, "status", "--porcelain") == ""

    # Fall B: Repo mit vorhandenem FEHLERKATALOG_spawn.md
    katalog = vorfall.katalog_pfad(repo, {})
    katalog.parent.mkdir(parents=True, exist_ok=True)
    katalog.write_text(
        "### 3.4 Prozess\n\n| Nr | Symptom | Ursache | Lösung | Schutz | Beispiel |\n|---|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    capo.katalog_pflegen(repo, {}, [fall])
    locks_nachher = list(repo.rglob("*.lock"))
    assert locks_nachher == [], f"Lock-Reste im Klon mit Katalog: {locks_nachher}"


# --- 4. Abnahme-Ticket ohne Code: kein Reopen / keine Bau-Runde -----------------


def test_geschlossenes_abnahme_ticket_ohne_code_loest_keine_bau_runde_aus(
    welt: dict[str, Path],
    hm: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Geschlossenes Abnahme-Ticket (#903) ohne Commit/Beleg erzeugt keine Verstöße und keine Bau-Runde."""
    _probe_spec_vorbereiten(welt, mit_staging=True)
    _gh_setzen(welt, "903", state="closed", closed_at=_iso(timedelta(minutes=20)))
    bau_starts: list[int] = []
    monkeypatch.setattr(capo, "_folge_runde", lambda *_a, **_k: bau_starts.append(1) or [])
    monkeypatch.setenv("TO_SPAWN_REPO", str(welt["repo"]))
    monkeypatch.chdir(welt["repo"])

    erg = capo.tick(
        welt["repo"],
        int(SPEC),
        GH,
        config.lade(welt["repo"]),
        wt_basis=str(welt["wt"] / welt["repo"].name),
    )
    assert not erg.verstoesse, erg.verstoesse
    assert bau_starts == [], "Für ein Abnahme-Ticket ohne Code darf keine Bau-Runde starten"


# --- 1 & 2. Weg-Test über das Spec-Ende einer Probe-Spec (-mit und -ohne) -------


@pytest.mark.parametrize("mit_staging", [True, False], ids=["mit-staging", "ohne-staging"])
def test_weg_spec_ende_schliesst_und_raeumt_auf(
    welt: dict[str, Path],
    hm: Path,
    monkeypatch: pytest.MonkeyPatch,
    mit_staging: bool,
) -> None:
    """Weg-Test #951: nach „passt“ + Live schließt Spec-Issue und räumt Worktrees, Zweige + tmux auf."""
    _zaehl, _live_zaehl, worktrees = _probe_spec_vorbereiten(welt, mit_staging=mit_staging)
    repo = welt["repo"]
    tmux_zu: list[tuple[Path, int]] = []
    monkeypatch.setattr(tmux_aufruf, "spec_session_schliessen", lambda r, s: tmux_zu.append((r, s)))

    def _gh_schliessen(nummer: int | str, gh_repo: str, text: str, befehl: str = "comment") -> bool:
        if befehl == "close":
            _gh_setzen(welt, str(nummer), state="closed", closed_at=_iso())
        return True

    def _abruf(nummer: int) -> dict[str, object] | None:
        daten = _gh_zustand(welt)["issues"].get(str(nummer))
        if not isinstance(daten, dict):
            return None
        return {"state": str(daten.get("state", "open")).upper(), "labels": daten.get("labels") or []}

    monkeypatch.setattr(befund, "kommentiere", _gh_schliessen)
    monkeypatch.setattr(befund, "_abruf_github", lambda _repo: _abruf)
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    monkeypatch.chdir(repo)

    # Nutzer kommentiert „passt — abgenommen.“ am Abnahme-Ticket #903.
    _kommentar(welt, "903", "passt — abgenommen.")

    konfig = config.lade(repo)
    wt_basis = str(welt["wt"] / repo.name)
    erg1 = capo.tick(repo, int(SPEC), GH, konfig, wt_basis=wt_basis)
    assert erg1.exit_code == 0, "\n".join(erg1.zeilen)

    # Falls im ersten Takt erst Live lief, schließt spätestens der nächste Takt die Kette ab.
    if not _zustand(welt).get("spec_geschlossen"):
        erg2 = capo.tick(repo, int(SPEC), GH, konfig, wt_basis=wt_basis)
        assert erg2.exit_code == 0, "\n".join(erg2.zeilen)

    # 1. Spec-Issue und Abnahme-Ticket sind zu; Live ist gelaufen.
    gh_daten = _gh_zustand(welt)
    assert capo.live_gelaufen(repo, int(SPEC), GH)
    assert gh_daten["issues"]["903"]["state"] == "closed", "Abnahme-Ticket muss nach passt + Live zu sein"
    assert gh_daten["issues"][SPEC]["state"] == "closed", "Spec-Issue muss in beiden Varianten zu sein"
    assert _zustand(welt).get("spec_geschlossen")

    # 2. 0 Ticket-Worktrees übrig.
    for wt in worktrees:
        assert not wt.exists(), f"Ticket-Worktree nicht entfernt: {wt}"
    wt_liste = [z for z in _git(repo, "worktree", "list", "--porcelain").splitlines() if z.startswith("worktree ")]
    assert len(wt_liste) == 1, f"Es darf nur noch das Hauptrepo als Worktree bleiben: {wt_liste}"

    # 3. 0 gemergte ticket/*-Zweige lokal und remote.
    lokale_zweige = [
        z for z in _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/ticket/*").splitlines() if z
    ]
    assert lokale_zweige == [], f"Lokale ticket/*-Zweige nicht gelöscht: {lokale_zweige}"
    remote_zweige = [z for z in _git(repo, "ls-remote", "--heads", "origin", "refs/heads/ticket/*").splitlines() if z]
    assert remote_zweige == [], f"Remote ticket/*-Zweige nicht gelöscht: {remote_zweige}"

    # 4. tmux-Sitzung der Spec wurde geschlossen.
    assert tmux_zu == [(repo, int(SPEC))], tmux_zu

    # 5. Keine ungespeicherten .lock-Reste im Klon (wie docs/agents/FEHLERKATALOG_spawn.md.lock).
    assert list((repo / "docs").rglob("*.lock")) == []
    assert not any(z.endswith(".lock") for z in _git(repo, "status", "--porcelain").splitlines())


def test_ohne_staging_mit_live_befehl_schliesst_erst_nach_live(
    welt: dict[str, Path],
    hm: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Variante -ohne mit live.befehl: vor Live bleibt Spec offen, nach Live schließt sie."""
    _probe_spec_vorbereiten(welt, mit_staging=False)
    repo = welt["repo"]
    _gh_setzen(welt, "903", state="closed", closed_at=_iso(timedelta(minutes=20)))
    monkeypatch.setattr(tmux_aufruf, "spec_session_schliessen", lambda *_a: None)
    monkeypatch.setattr(
        befund,
        "kommentiere",
        lambda nr, _r, _t, befehl="comment": (
            (_gh_setzen(welt, str(nr), state="closed", closed_at=_iso()) or True) if befehl == "close" else True
        ),
    )
    monkeypatch.setattr(
        befund,
        "_abruf_github",
        lambda _repo: lambda nr: {"state": str(_gh_zustand(welt)["issues"][str(nr)]["state"]).upper(), "labels": []},
    )
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    monkeypatch.chdir(repo)

    konfig = config.lade(repo)
    wt_basis = str(welt["wt"] / repo.name)
    erg_vor = capo.tick(repo, int(SPEC), GH, konfig, wt_basis=wt_basis)
    assert erg_vor.exit_code == 0, "\n".join(erg_vor.zeilen)
    assert not _zustand(welt).get("spec_geschlossen"), "Ohne Live darf Spec mit live.befehl nicht schließen"

    assert capo.live_merken(repo, int(SPEC), GH)
    erg_nach = capo.tick(repo, int(SPEC), GH, konfig, wt_basis=wt_basis)
    assert erg_nach.exit_code == 0, "\n".join(erg_nach.zeilen)
    assert _zustand(welt).get("spec_geschlossen"), "Nach Live muss Spec in Variante -ohne schließen"
    assert _gh_zustand(welt)["issues"][SPEC]["state"] == "closed"


def test_spec_aufraeumen_schuetzt_schmutzigen_worktree_und_ungemergten_zweig(welt: dict[str, Path]) -> None:
    """Schutzregel #951: schmutzige Ticket-Worktrees und ungemergte ticket/*-Zweige bleiben stehen."""
    repo = welt["repo"]
    wt_basis = welt["wt"] / repo.name
    wt_basis.mkdir(parents=True, exist_ok=True)

    # Sauberer, gemergter Worktree → muss weg.
    wt_sauber = _ticket_worktree_mit_merge(repo, wt_basis, 910, "ticket/910-sauber")

    # Schmutziger Worktree (ungespeicherte Datei) → muss bleiben.
    wt_schmutzig = _ticket_worktree_mit_merge(repo, wt_basis, 911, "ticket/911-schmutzig")
    (wt_schmutzig / "ungespeichert.txt").write_text("noch arbeit\n", encoding="utf-8")

    # Ungemergter Zweig (lokal + remote) → muss bleiben.
    _git(repo, "branch", "ticket/912-ungemergt")
    wt_ungemergt = wt_basis / "wt-912"
    _git(repo, "worktree", "add", "-q", str(wt_ungemergt), "ticket/912-ungemergt")
    (wt_ungemergt / "offen.py").write_text("X = 1\n", encoding="utf-8")
    _git(wt_ungemergt, "add", "offen.py")
    _git(wt_ungemergt, "commit", "-q", "-m", "wip: ungemergt (#912)")
    _git(wt_ungemergt, "push", "-q", "origin", "ticket/912-ungemergt")

    lock_rest = repo / "docs" / "agents" / "FEHLERKATALOG_spawn.md.lock"
    lock_rest.parent.mkdir(parents=True, exist_ok=True)
    lock_rest.write_text("", encoding="utf-8")

    hausmeister.spec_aufraeumen(repo, int(SPEC), loeschen=True)

    assert not lock_rest.exists(), "Ungespeicherter .lock-Rest unter docs/ muss entfernt werden"

    assert not wt_sauber.exists(), "Sauberer gemergter Worktree muss entfernt werden"
    assert wt_schmutzig.is_dir(), "Schmutziger Worktree darf nicht entfernt werden"
    assert wt_ungemergt.is_dir(), "Worktree mit ungemergtem Commit darf nicht entfernt werden"

    lokale = set(_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/ticket/*").splitlines())
    assert "ticket/910-sauber" not in lokale
    assert "ticket/911-schmutzig" in lokale  # noch ausgecheckt im schmutzigen Worktree
    assert "ticket/912-ungemergt" in lokale

    remote = _git(repo, "ls-remote", "--heads", "origin", "refs/heads/ticket/*")
    assert "ticket/910-sauber" not in remote
    assert "ticket/912-ungemergt" in remote
