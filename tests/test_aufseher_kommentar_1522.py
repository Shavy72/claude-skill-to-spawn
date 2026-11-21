"""Tests für #1522 — Aufseher-Kommentare werden hart auf 2 Zeilen gekürzt.

Anlass: Fehlerkatalog S13 — lange Aufseher-Kommentare füllen den Kontext der
Bau-Session; bisher stand die Grenze nur als Merkzettel im Gedächtnis.

Echt laufen: ``befund`` und ``capo``. Gestellt ist nur GitHub (``gh.lauf`` schreibt mit)
und der Abschluss-Anstoß (``capo._abschluss_versuch`` liefert einen langen Fehlergrund).
Der Wächter-Test liest den Quelltext: niemand schreibt am Modul vorbei einen Kommentar.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from to_spawn import befund, capo, gh

GH_REPO = "test/wegwerf"
WURZEL = Path(__file__).resolve().parent.parent

#: Wer außer der einen Schreib-Stelle ``gh … comment`` benutzen darf, und warum.
ERLAUBT = {
    ("to_spawn/befund.py", "kommentiere"): "die eine Schreib-Stelle (#1522)",
    ("to_spawn/aufpasser.py", "Aufpasser.melden"): "Aufpasser-Rolle, eigener Kopf",
    ("to_spawn/probesitz.py", "_aufraeumen"): "Probesitz-Wegwerf-Ticket, kein Aufseher",
    ("to_spawn/nest.py", ""): "Rechte-Liste ``Bash(gh issue comment:*)``, schreibt nichts",
}
#: Ordner, die der Prüf-Test nicht liest (Tests stellen gh nach, der Rest ist kein Quelltext).
AUSGENOMMEN = {"tests", ".git", ".venv", "venv", "__pycache__", ".pytest_cache"}


class FakeGh:
    def __init__(self) -> None:
        self.aufrufe: list[list[str]] = []

    def lauf(
        self, args: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> tuple[int, str]:
        self.aufrufe.append(list(args))
        return 0, ""


@pytest.fixture()
def fake_gh(monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    fake = FakeGh()
    monkeypatch.setattr(gh, "lauf", fake.lauf)
    return fake


def _zeilen(text: str) -> list[str]:
    return [z for z in text.splitlines() if z.strip()]


def test_rot_beweis_reopen_mit_fuenf_gruenden_hat_hoechstens_zwei_zeilen(
    fake_gh: FakeGh,
) -> None:
    gruende = [(f"regel_{i}", f"Grund Nummer {i}") for i in range(5)]
    zeilen = befund.wieder_oeffnen(415, GH_REPO, gruende, dry_run=False)
    assert zeilen == ["#415 wieder geöffnet"]
    (aufruf,) = fake_gh.aufrufe
    assert aufruf[:2] == ["issue", "reopen"]
    kommentar = aufruf[aufruf.index("--comment") + 1]
    assert len(_zeilen(kommentar)) <= 2, kommentar
    for i in range(5):  # Fix-Runde 1: keine Regel geht verloren (capo trägt alle als erledigt ein)
        assert f"regel_{i}" in kommentar, kommentar
        assert f"Grund Nummer {i}" in kommentar, kommentar


def test_rot_beweis_capo_abschluss_alarm_mit_langem_grund(fake_gh: FakeGh, monkeypatch: pytest.MonkeyPatch) -> None:
    grund = "\n".join(f"Traceback-Zeile {i}" for i in range(5))
    monkeypatch.setattr(capo, "_abschluss_versuch", lambda *a, **k: ([], grund))
    zustand = {"abschluss_fehlversuche": capo.ABSCHLUSS_ALARM_AB - 1}
    capo.abschluss_anstossen(Path("."), 900, GH_REPO, {}, zustand, dry_run=False)
    (aufruf,) = [a for a in fake_gh.aufrufe if a[:2] == ["issue", "comment"]]
    text = aufruf[aufruf.index("--body") + 1]
    assert len(_zeilen(text)) <= 2, text
    assert text.startswith(befund.KOPF)
    assert zustand.get("abschluss_alarm")


def test_reopen_mit_vielen_langen_gruenden_nennt_jede_regel_in_zeile_eins(
    fake_gh: FakeGh,
) -> None:
    gruende = [(f"regel_{i}", "x" * 400 + "\nzweite Zeile") for i in range(8)]
    befund.wieder_oeffnen(415, GH_REPO, gruende, dry_run=False)
    kommentar = fake_gh.aufrufe[0][fake_gh.aufrufe[0].index("--comment") + 1]
    zeilen = _zeilen(kommentar)
    assert len(zeilen) == 2, kommentar
    assert all(f"regel_{i}" in zeilen[0] for i in range(8)), zeilen[0]
    assert all(len(z) <= befund.KOMMENTAR_MAX_ZEICHEN for z in zeilen)


def test_kommentiere_kuerzt_fuenf_zeilen_direkt(fake_gh: FakeGh) -> None:
    text = "\n".join(f"{befund.KOPF} Zeile {i}" for i in range(5))
    assert befund.kommentiere(7, GH_REPO, text) is True
    (aufruf,) = fake_gh.aufrufe
    assert aufruf[:6] == ["issue", "comment", "7", "--repo", GH_REPO, "--body"]
    assert aufruf[6] == f"{befund.KOPF} Zeile 0\n{befund.KOPF} Zeile 1 … +3 Zeilen gekürzt"


def test_kommentiere_unbekannter_befehl_bricht(fake_gh: FakeGh) -> None:
    with pytest.raises(ValueError):
        befund.kommentiere(7, GH_REPO, "x", befehl="edit")
    assert fake_gh.aufrufe == []


@pytest.mark.parametrize(
    ("text", "soll"),
    [
        ("", ""),
        ("\n \n\t\n", ""),
        ("eins\nzwei", "eins\nzwei"),
        ("eins\nzwei\ndrei", "eins\nzwei … +1 Zeilen gekürzt"),
        ("eins\r\nzwei\r\ndrei\r\n", "eins\nzwei … +1 Zeilen gekürzt"),
    ],
)
def test_kuerze_grenzfaelle(text: str, soll: str) -> None:
    assert befund.kuerze(text) == soll


def test_kuerze_begrenzt_zeilenlaenge() -> None:
    zeile = befund.kuerze("y" * (befund.KOMMENTAR_MAX_ZEICHEN + 50))
    assert len(zeile) == befund.KOMMENTAR_MAX_ZEICHEN and zeile.endswith("…")


def test_kurzer_kommentar_bleibt_unveraendert() -> None:
    text = f"{befund.KOPF} Mensch nötig — x.\n{befund.KOPF} Die Kette läuft weiter."
    assert befund.kuerze(text) == text


def test_leerzeilen_zaehlen_nicht_und_fallen_weg() -> None:
    assert befund.kuerze("eins\n\n\nzwei\n") == "eins\nzwei"


def _schreibt_kommentar(wort: str) -> bool:
    return wort in ("comment", "--comment") or "issue comment" in wort


def _api_kommentar_schreiben(aufruf: ast.Call) -> bool:
    """``gh api …/comments`` mit POST bzw. ``body=`` (Lesen der Kommentare ist erlaubt)."""
    worte = [
        k.value for k in ast.walk(aufruf) if isinstance(k, ast.Constant) and isinstance(k.value, str)
    ]
    return any("/comments" in w for w in worte) and any(
        w == "POST" or w.startswith("body=") for w in worte
    )


def _comment_stellen() -> list[tuple[str, str, int]]:
    """(Datei, umschließende Funktion, Zeile) jeder Stelle, die einen GitHub-Kommentar schreibt."""
    funde: list[tuple[str, str, int]] = []
    for pfad in sorted(WURZEL.rglob("*.py")):
        if AUSGENOMMEN & set(pfad.relative_to(WURZEL).parts):
            continue
        baum = ast.parse(pfad.read_text(encoding="utf-8"))
        rel = pfad.relative_to(WURZEL).as_posix()
        besitzer: dict[int, str] = {}
        funktionen = [(k, getattr(k, "name", "")) for k in baum.body]
        for klasse in baum.body:
            if isinstance(klasse, ast.ClassDef):
                funktionen += [
                    (k, f"{klasse.name}.{getattr(k, 'name', '')}") for k in klasse.body
                ]
        for knoten, name in funktionen:
            if isinstance(knoten, ast.FunctionDef | ast.AsyncFunctionDef):
                for kind in ast.walk(knoten):
                    besitzer[id(kind)] = name
        for kind in ast.walk(baum):
            wort = kind.value if isinstance(kind, ast.Constant) else None
            if (isinstance(wort, str) and _schreibt_kommentar(wort)) or (
                isinstance(kind, ast.Call) and _api_kommentar_schreiben(kind)
            ):
                funde.append((rel, besitzer.get(id(kind), ""), kind.lineno))
    return funde


def test_waechter_kein_kommentar_am_modul_vorbei() -> None:
    fremd = [f for f in _comment_stellen() if (f[0], f[1]) not in ERLAUBT]
    assert not fremd, (
        "GitHub-Kommentar am Modul vorbei — Aufseher-Kommentare nur über "
        f"befund.kommentiere (kürzt auf 2 Zeilen, #1522): {fremd}"
    )


def test_waechter_findet_die_schreib_stelle_und_jede_ausnahme_lebt() -> None:
    gefunden = {(f[0], f[1]) for f in _comment_stellen()}
    assert ("to_spawn/befund.py", "kommentiere") in gefunden  # Positiv-Check: der Scan sieht etwas
    tot = [e for e in ERLAUBT if e not in gefunden]
    assert not tot, f"ERLAUBT-Eintrag ohne Fundstelle — streichen: {tot}"


def test_waechter_erkennt_api_post_auf_comments() -> None:
    quelle = 'gh.lauf(["api", f"repos/{r}/issues/{n}/comments", "-X", "POST", "-f", f"body={t}"])'
    (aufruf,) = [k for k in ast.walk(ast.parse(quelle)) if isinstance(k, ast.Call)][:1]
    assert _api_kommentar_schreiben(aufruf)
    lesen = ast.parse('gh.lauf(["api", f"repos/{r}/issues/{n}/comments?per_page=100"])')
    assert not _api_kommentar_schreiben(next(k for k in ast.walk(lesen) if isinstance(k, ast.Call)))
