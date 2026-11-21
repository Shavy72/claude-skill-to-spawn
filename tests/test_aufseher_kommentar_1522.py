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
}


class FakeGh:
    def __init__(self) -> None:
        self.aufrufe: list[list[str]] = []

    def lauf(self, args: list[str], cwd: Path | None = None) -> tuple[int, str]:
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
    assert "regel_0" in kommentar  # der erste Grund bleibt lesbar
    assert "+3 Zeilen gekürzt" in kommentar


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


def test_kurzer_kommentar_bleibt_unveraendert() -> None:
    text = f"{befund.KOPF} Mensch nötig — x.\n{befund.KOPF} Die Kette läuft weiter."
    assert befund.kuerze(text) == text


def test_leerzeilen_zaehlen_nicht_und_fallen_weg() -> None:
    assert befund.kuerze("eins\n\n\nzwei\n") == "eins\nzwei"


def _comment_stellen() -> list[tuple[str, str, int]]:
    """(Datei, umschließende Top-Level-Funktion, Zeile) jedes ``"comment"``/``"--comment"``-Literals."""
    funde: list[tuple[str, str, int]] = []
    for ordner in ("to_spawn", "skripte"):
        for pfad in sorted((WURZEL / ordner).glob("*.py")):
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
                if isinstance(kind, ast.Constant) and isinstance(kind.value, str):
                    wort = kind.value
                    if wort in ("comment", "--comment"):
                        funde.append((rel, besitzer.get(id(kind), ""), kind.lineno))
    return funde


def test_waechter_kein_kommentar_am_modul_vorbei() -> None:
    fremd = [f for f in _comment_stellen() if (f[0], f[1]) not in ERLAUBT]
    assert not fremd, (
        "GitHub-Kommentar am Modul vorbei — Aufseher-Kommentare nur über "
        f"befund.kommentiere (kürzt auf 2 Zeilen, #1522): {fremd}"
    )
