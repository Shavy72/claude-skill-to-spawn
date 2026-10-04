"""Aufseher-Stand #430: eine Kurz-Zeile je Ticket + Stand-Datei je Tick (E19, E21, E25).

Weg-Tests über die echte Sammel-Funktion. Gefaked sind nur die Außenquellen:
tmux (Fensterliste, Bildschirm-Text) und GitHub (Sub-Issues der Spec). Das Bau-Log
liegt echt als Laufdatei im Test-Repo, die Stand-Datei echt im Test-Ordner.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser, aufseher_stand

SPEC = 77
JETZT = 1_800_000_000.0
LINIE = "─" * 80


def bildschirm(ausgabe: str, uhr: str = "2h06m") -> str:
    """Pane-Text wie Claude Code ihn zeigt: Ausgabe, Eingabefeld, Statuszeile mit Uhr."""
    return "\n".join(
        [
            ausgabe,
            "",
            LINIE,
            "❯ ",
            LINIE,
            f"  Opus 5.5 (1M context) · 91.7k (9.0%) · {uhr}",
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
            "",
        ]
    )


class FakeWelt:
    """tmux + GitHub von außen; alles andere ist echt."""

    def __init__(self) -> None:
        self.fenster: dict[
            int, tuple[float, str]
        ] = {}  # Ticket → (activity, Pane-Text)
        self.tickets: dict[int, dict[str, Any]] = {}
        self.uhr = JETZT

    def ticket(self, n: int, *, zu: bool = False, kommentare: int = 0) -> None:
        self.tickets[n] = {
            "number": n,
            "state": "closed" if zu else "open",
            "title": f"Ticket {n}",
            "comments": kommentare,
        }

    def tmux(self, args: list[str]) -> str | None:
        if args[0] == "list-windows":
            if args[2] != f"=spec-{SPEC}":
                return None
            zeilen = [f"0\twache {SPEC}\t{int(self.uhr)}"]
            for i, (n, (aktiv, _)) in enumerate(sorted(self.fenster.items()), start=1):
                zeilen.append(f"{i}\tbau {n}\t{int(aktiv)}")
            return "\n".join(zeilen)
        if args[0] == "capture-pane":
            index = int(args[args.index("-t") + 1].rsplit(":", 1)[1])
            n = sorted(self.fenster)[index - 1]
            return self.fenster[n][1]
        raise AssertionError(f"unerwarteter tmux-Aufruf {args}")

    def kinder(self, spec: int) -> list[dict[str, Any]] | None:
        assert spec == SPEC
        return [dict(t) for t in self.tickets.values()]

    def quellen(self, repo: Path) -> aufseher_stand.Quellen:
        return aufseher_stand.Quellen(
            repo=repo, kinder=self.kinder, tmux=self.tmux, jetzt=lambda: self.uhr
        )


def bau_log(repo: Path, n: int, *zeilen: dict[str, Any]) -> None:
    datei = repo / ".to-spawn" / "bau_log" / f"{n}.jsonl"
    datei.parent.mkdir(parents=True, exist_ok=True)
    with datei.open("a", encoding="utf-8") as fh:
        for z in zeilen:
            fh.write(json.dumps({"ticket": str(n), **z}, ensure_ascii=False) + "\n")


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "repo"
    (r / ".git").mkdir(parents=True)
    # Worktree-Basis ins Leere, damit kein echter Worktree des Rechners gelesen wird.
    (r / ".to-spawn").mkdir()
    (r / ".to-spawn" / "config.json").write_text(
        json.dumps({"worktree_basis": str(tmp_path / "wts")}), encoding="utf-8"
    )
    monkeypatch.setenv("TO_SPAWN_REPO", str(r))
    return r


@pytest.fixture()
def ordner(tmp_path: Path) -> Path:
    return tmp_path / "stand"


def zeile_fuer(text: str, n: int) -> str:
    treffer = [z for z in text.splitlines() if z.startswith(f"#{n} ")]
    assert len(treffer) == 1, text
    return treffer[0]


# --- Kurz-Zeile je Fenster-Lage -------------------------------------------------


def test_arbeitet(repo: Path, ordner: Path) -> None:
    w = FakeWelt()
    w.ticket(431)
    w.fenster[431] = (
        JETZT - 5,
        bildschirm("● Schreibe Test\n✻ Baking… (esc to interrupt)"),
    )
    bau_log(
        repo,
        431,
        {
            "ts": "2026-10-04T10:00:00+00:00",
            "typ": "session_start",
            "text": "Session gestartet.",
        },
        {
            "ts": "2026-10-04T10:30:00+00:00",
            "typ": "session_ende",
            "kontext": {"spitze": 112_500},
            "text": "Tests grün, baue jetzt die CLI und danach den Skill-Text samt Hinweis auf die Stand-Datei",
        },
    )
    text = aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner)
    z = zeile_fuer(text, 431)
    assert z.startswith("#431 offen · arbeitet · ")
    assert "Kontext 112,5k" in z
    assert "Phase Session-Ende" in z
    assert "„Tests grün, baue jetzt die CLI" in z
    assert "…“" in z  # gekürzt
    assert "esc to interrupt" not in text


def test_still_mit_tickender_uhr(repo: Path, ordner: Path) -> None:
    """Nur die Uhr der Statuszeile tickt → still, Minuten zählen weiter."""
    w = FakeWelt()
    w.ticket(432)
    w.fenster[432] = (JETZT - 40 * 60, bildschirm("✻ Baked for 40s", uhr="2h06m"))
    z = zeile_fuer(aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner), 432)
    assert " · still 40 min · " in z
    assert "Kontext —" in z and "Phase —" in z

    # 5 min später: Uhr hat getickt, tmux hält das Fenster deshalb für „aktiv“.
    w.uhr = JETZT + 5 * 60
    w.fenster[432] = (w.uhr, bildschirm("✻ Baked for 40s", uhr="2h11m"))
    z = zeile_fuer(aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner), 432)
    assert " · still 45 min · " in z


def test_rueckfrage(repo: Path, ordner: Path) -> None:
    w = FakeWelt()
    w.ticket(433)
    marker = aufpasser.RUECKFRAGE_MARKER[0]
    w.fenster[433] = (
        JETZT - 12 * 60,
        bildschirm(f"Bash(rm -rf build)\n{marker}\n❯ 1. Yes"),
    )
    z = zeile_fuer(aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner), 433)
    assert " · Rückfrage 12 min · " in z
    assert marker not in z


def test_ticket_zu_und_kein_fenster(repo: Path, ordner: Path) -> None:
    w = FakeWelt()
    w.ticket(434, zu=True)
    w.ticket(435)
    text = aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner, alle=True)
    assert zeile_fuer(text, 434).startswith("#434 zu · kein Fenster · ")
    assert zeile_fuer(text, 435).startswith("#435 offen · kein Fenster · ")
    # Ohne --alle fällt ein geschlossenes Ticket ohne Fenster weg, der Kopf zählt es.
    kurz = aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner)
    assert "#434 " not in kurz
    assert "1 zu" in kurz.splitlines()[0]


def test_zehn_tickets_hoechstens_elf_zeilen(repo: Path, ordner: Path) -> None:
    w = FakeWelt()
    roh = []
    for n in range(500, 510):
        w.ticket(n, kommentare=3)
        pane = bildschirm(f"Rohzeile-{n} aus dem Pane\n● Lese Datei x.py")
        roh.append(f"Rohzeile-{n}")
        w.fenster[n] = (JETZT - 60 * (n - 499), pane)
        bau_log(
            repo,
            n,
            {"ts": "2026-10-04T10:00:00+00:00", "typ": "auftrag", "text": "x" * 500},
        )
    text = aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner)
    zeilen = text.splitlines()
    assert len(zeilen) <= 11
    assert sum(1 for z in zeilen if z.startswith("#5")) == 10
    assert all(len(z) <= 200 for z in zeilen)
    assert not any(r in text for r in roh)
    kopf = zeilen[0]
    assert f"Spec #{SPEC}" in kopf and "10 offen" in kopf and "10 still" in kopf


def test_quelle_fehlt_nur_strich_und_warnung(
    repo: Path, ordner: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Kaputtes Bau-Log eines Tickets: Strich + Warnung, kein Absturz."""
    w = FakeWelt()
    w.ticket(436)
    w.ticket(437)
    kaputt = repo / ".to-spawn" / "bau_log" / "436.jsonl"
    kaputt.mkdir(parents=True)  # Ordner statt Datei → OSError beim Lesen
    with caplog.at_level("WARNING"):
        text = aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner)
    assert "Kontext —" in zeile_fuer(text, 436)
    assert zeile_fuer(text, 437)
    assert any("436" in r.getMessage() for r in caplog.records)


# --- Stand-Datei ----------------------------------------------------------------


def test_stand_datei_noop_und_letzter_stand(repo: Path, ordner: Path) -> None:
    w = FakeWelt()
    w.ticket(440, kommentare=2)
    w.fenster[440] = (JETZT - 40 * 60, bildschirm("fertig"))
    aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner)
    w.uhr += 600  # nur Minuten laufen weiter
    zweiter = aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner)

    datei = ordner / f"stand-{SPEC}.jsonl"
    zeilen = [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines()]
    assert len(zeilen) == 2
    assert zeilen[0]["noop"] is False and zeilen[0]["zeilen"]
    assert zeilen[1]["noop"] is True and "zeilen" not in zeilen[1]
    assert zeilen[0]["fingerabdruck"] == zeilen[1]["fingerabdruck"]
    assert "noop" in zweiter.splitlines()[0]
    assert "#440 offen · still 50 min" in zweiter

    # Zwei neue Kommentare → keine noop-Zeile, Zahl steht in der Kurz-Zeile.
    w.tickets[440]["comments"] = 4
    dritter = aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner)
    assert "2 neue Kommentare" in zeile_fuer(dritter, 440)
    # Fenster wechselt auf „arbeitet“ → Änderung.
    w.fenster[440] = (w.uhr, bildschirm("● Lese\n(esc to interrupt)"))
    aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner)
    zeilen = [json.loads(z) for z in datei.read_text(encoding="utf-8").splitlines()]
    assert [z["noop"] for z in zeilen] == [False, True, False, False]

    letzter = aufseher_stand.letzter_stand(SPEC, ordner=ordner)
    assert letzter is not None
    assert letzter == zeilen[-1]
    assert any("arbeitet" in z for z in letzter["zeilen"])
    # Ein weiterer noop-Aufruf ändert den Startstand des Nachfolgers nicht.
    aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner)
    assert aufseher_stand.letzter_stand(SPEC, ordner=ordner) == letzter


def test_letzter_stand_ohne_datei(ordner: Path) -> None:
    assert aufseher_stand.letzter_stand(SPEC, ordner=ordner) is None


def test_erster_aufruf_kommentare_strich(repo: Path, ordner: Path) -> None:
    w = FakeWelt()
    w.ticket(441, kommentare=5)
    z = zeile_fuer(aufseher_stand.stand(SPEC, w.quellen(repo), ordner=ordner), 441)
    assert z.endswith("— neue Kommentare")


def test_stand_ordner_per_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TO_SPAWN_AUFSEHER_STAND", str(tmp_path / "x"))
    assert aufseher_stand.stand_ordner() == tmp_path / "x"
    monkeypatch.delenv("TO_SPAWN_AUFSEHER_STAND")
    assert aufseher_stand.stand_ordner() == (
        Path.home() / ".local" / "state" / "to-spawn" / "aufseher"
    )


# --- CLI über echte Programm-Nähte (gh-/tmux-Ersatz per Umgebung) ---------------

TMUX_STUB = textwrap.dedent(
    """
    import json, os, sys
    welt = json.loads(open(os.environ["FAKE_WELT"], encoding="utf-8").read())
    args = sys.argv[1:]
    if args[0] == "list-windows":
        print(welt["fenster"])
    elif args[0] == "capture-pane":
        print(welt["panes"][args[args.index("-t") + 1]])
    else:
        sys.exit(1)
    """
)
GH_STUB = textwrap.dedent(
    """
    import json, os, sys
    welt = json.loads(open(os.environ["FAKE_WELT"], encoding="utf-8").read())
    schluessel = " ".join(sys.argv[1:])
    if schluessel not in welt["gh"]:
        sys.exit(1)
    print(json.dumps(welt["gh"][schluessel]))
    """
)


def _cli_welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "tmux_stub.py").write_text(TMUX_STUB, encoding="utf-8")
    (tmp_path / "gh_stub.py").write_text(GH_STUB, encoding="utf-8")
    welt = {
        "fenster": f"0\twache {SPEC}\t{int(JETZT)}\n1\tbau 450\t{int(JETZT)}",
        "panes": {f"=spec-{SPEC}:1": bildschirm("● Lese\n(esc to interrupt)")},
        "gh": {
            f"api repos/o/r/issues/{SPEC}/sub_issues?per_page=100": [
                {"number": 450, "state": "open", "title": "A", "comments": 1},
                {"number": 451, "state": "closed", "title": "B", "comments": 0},
            ]
        },
    }
    datei = tmp_path / "welt.json"
    datei.write_text(json.dumps(welt), encoding="utf-8")
    monkeypatch.setenv("FAKE_WELT", str(datei))
    monkeypatch.setenv(
        "TO_SPAWN_TMUX", json.dumps([sys.executable, str(tmp_path / "tmux_stub.py")])
    )
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(tmp_path / "gh_stub.py"))
    return tmp_path / "stand"


def test_cli_aufseher_stand(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ordner = _cli_welt(tmp_path, monkeypatch)
    import importlib.util

    spec = importlib.util.spec_from_file_location("to_spawn_cli", SKILL / "to_spawn.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    code = cli.main(
        [
            "aufseher-stand",
            str(SPEC),
            "--gh-repo",
            "o/r",
            "--stand-ordner",
            str(ordner),
            "--alle",
        ]
    )
    raus = capsys.readouterr().out
    assert code == 0, raus
    assert "#450 offen · arbeitet · " in raus
    assert "#451 zu · kein Fenster · " in raus
    assert (ordner / f"stand-{SPEC}.jsonl").is_file()


def test_cli_ohne_gh_fehler(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gh fehlt komplett → klarer Fehler, Exit ≠ 0 (eigener Prozess, PATH ohne gh)."""
    leer = tmp_path / "leer"
    leer.mkdir()
    umgebung = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_GH_STUB"}
    umgebung["PATH"] = str(leer)
    fertig = subprocess.run(
        [
            sys.executable,
            str(SKILL / "to_spawn.py"),
            "aufseher-stand",
            str(SPEC),
            "--gh-repo",
            "o/r",
            "--stand-ordner",
            str(tmp_path / "stand"),
        ],
        capture_output=True,
        text=True,
        env=umgebung,
        cwd=repo,
        check=False,
    )
    assert fertig.returncode != 0
    assert "gh" in (fertig.stdout + fertig.stderr)
    assert "FEHLER" in (fertig.stdout + fertig.stderr)


# --- Skill-Text -------------------------------------------------------------------


def test_wache_prompt_nennt_aufseher_stand() -> None:
    sys.path.insert(0, str(SKILL / "skripte"))
    import wache

    assert "to_spawn.py aufseher-stand {S}" in wache.PROMPT
    tick = wache.PROMPT.split("## Jeder Tick", 1)[1].split("## Befehle", 1)[0]
    assert "aufseher-stand" in tick.splitlines()[1]


def test_skill_md_nennt_aufseher_stand() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "aufseher-stand" in text
    assert "stand.sh" in text
