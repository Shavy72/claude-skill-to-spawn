"""Wächter (#258): ``text=True``-Unterprozess-Aufrufe lesen UTF-8, nie die Windows-Locale."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import eigener_handoff, konsole

WURZEL = Path(__file__).resolve().parent.parent

# Aufgeräumt ist, was Issue #258 nennt. ponytail: übrige Dateien stehen hier, bis sie migriert sind;
# Upgrade-Pfad: Datei auf ``**konsole.UNTERPROZESS_TEXT`` umstellen und hier streichen.
NOCH_NICHT_MIGRIERT = {
    "to_spawn/belege.py", "to_spawn/hausmeister_aufgabe.py", "to_spawn/komplett_lauf.py",
    "to_spawn/nest.py", "to_spawn/pc_fenster.py", "to_spawn/respawn.py", "to_spawn/setup.py",
    "to_spawn/startklar.py",
}


KONSTANTEN = {"UNTERPROZESS_TEXT", "UNTERPROZESS_ANZEIGE", "UNTERPROZESS_DATEN"}


def _konsole_konstante(knoten: ast.expr) -> bool:
    return (
        isinstance(knoten, ast.Attribute)
        and isinstance(knoten.value, ast.Name)
        and knoten.value.id == "konsole"
        and knoten.attr in KONSTANTEN
    )


def _gut(wert: ast.expr) -> bool:
    """``**konsole.<Konstante>`` oder ``**{**konsole.<Konstante>, ...}``."""
    if _konsole_konstante(wert):
        return True
    return isinstance(wert, ast.Dict) and any(
        k is None and _konsole_konstante(v) for k, v in zip(wert.keys, wert.values)
    )


def _ist_utf8(wert: ast.expr) -> bool:
    return isinstance(wert, ast.Constant) and str(wert.value).lower().replace("-", "") == "utf8"


def _ohne_utf8(datei: Path) -> list[int]:
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    zeilen = []
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue
        text = any(
            k.arg in ("text", "universal_newlines") and isinstance(k.value, ast.Constant) and k.value.value is True
            for k in knoten.keywords
        )
        if not text:
            continue
        encoding_ok = any(k.arg == "encoding" and _ist_utf8(k.value) for k in knoten.keywords)
        spread_ok = any(k.arg is None and _gut(k.value) for k in knoten.keywords)
        if not (encoding_ok or spread_ok):
            zeilen.append(knoten.lineno)
    return zeilen


def test_waechter_erkennt_schlechte_formen(tmp_path: Path) -> None:
    quelle = tmp_path / "x.py"
    quelle.write_text(
        "import subprocess\n"
        "subprocess.run(['a'], text=True)\n"
        "subprocess.run(['a'], universal_newlines=True)\n"
        "subprocess.run(['a'], text=True, encoding='cp1252')\n"
        "subprocess.run(['a'], text=True, **kw)\n"
        "subprocess.run(['a'], text=True, **konsole.ANDERES)\n"
        "subprocess.run(['a'], text=True, encoding='utf-8')\n"
        "subprocess.run(['a'], **konsole.UNTERPROZESS_DATEN, text=True)\n"
        "subprocess.run(['a'], text=True, **{**konsole.UNTERPROZESS_DATEN, 'errors': 'x'})\n",
        encoding="utf-8",
    )
    assert _ohne_utf8(quelle) == [2, 3, 4, 5, 6]


def test_helfer_liest_echte_umlaute_aus_echtem_unterprozess() -> None:
    code = r"import sys; sys.stdout.buffer.write('\u00e4\u00f6\u00fc \u00df \u20ac'.encode('utf-8'))"
    for lesart in (konsole.UNTERPROZESS_ANZEIGE, konsole.UNTERPROZESS_DATEN):
        lauf = subprocess.run([sys.executable, "-c", code], capture_output=True, check=True, **lesart)
        assert lauf.stdout.strip() == "äöü ß €"


def test_git_dateiname_mit_umlaut_ueber_eigener_handoff(tmp_path: Path) -> None:
    git = ["git", "-C", str(tmp_path)]
    subprocess.run([*git, "init", "-q"], check=True)
    (tmp_path / "größe.txt").write_text("x", encoding="utf-8")
    subprocess.run([*git, "add", "."], check=True)
    assert eigener_handoff._git(tmp_path, "-c", "core.quotepath=false", "ls-files").strip() == "größe.txt"


def test_text_true_ohne_utf8_nur_in_allowlist() -> None:
    schlecht = {}
    for datei in sorted((WURZEL / "to_spawn").rglob("*.py")):
        rel = datei.relative_to(WURZEL).as_posix()
        if rel in NOCH_NICHT_MIGRIERT:
            continue
        if zeilen := _ohne_utf8(datei):
            schlecht[rel] = zeilen
    assert not schlecht, f"text=True ohne **konsole.UNTERPROZESS_TEXT / encoding: {schlecht}"


def test_jeder_allowlist_eintrag_hat_noch_einen_verstoss() -> None:
    """Sonst wird die Datei grundlos übersprungen: Eintrag streichen."""
    erledigt = [rel for rel in sorted(NOCH_NICHT_MIGRIERT) if not _ohne_utf8(WURZEL / rel)]
    assert not erledigt, f"Eintrag streichen (kein Verstoß mehr): {erledigt}"


def _kaputt_lauf(befehl: list[str], **_k: object) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(befehl, 0, bytes([97, 98, 0x81, 99]), b"")


def test_nicht_utf8_ausgabe_ist_nicht_lesbar_kein_absturz(monkeypatch) -> None:
    from to_spawn import capo, docker_reste
    from to_spawn.pruef import cache

    monkeypatch.setattr(subprocess, "run", _kaputt_lauf)
    assert konsole.lauf_daten(["x"]) is None
    assert docker_reste._docker(("ps",))[0] is False
    assert capo.vps_kopf({"ssh": "h", "pfad": "p"}) is None
    assert cache._tree(Path("."), "HEAD") is None


def test_pruef_sha_nicht_utf8_ausgabe_liefert_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#608 (1): ``pruef._sha`` stürzt bei kaputter Nicht-UTF-8-Ausgabe nicht ab, sondern liefert ``None``."""
    from to_spawn import pruef

    monkeypatch.setattr(subprocess, "run", _kaputt_lauf)
    assert pruef._sha(tmp_path, "HEAD") is None

    def _kaputt_stderr(befehl: list[str], **_k: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(befehl, 1, b"", bytes([102, 0x81, 101]))

    monkeypatch.setattr(subprocess, "run", _kaputt_stderr)
    assert pruef._sha(tmp_path, "HEAD") is None


def test_lauf_daten_vereinheitlicht_crlf_zu_lf(monkeypatch: pytest.MonkeyPatch) -> None:
    """#608 (3): ``konsole.lauf_daten`` normiert CRLF/CR wie ``text=True`` auf LF (auch für Docker-Fehler)."""
    from to_spawn import docker_reste

    code = (
        "import sys; "
        "sys.stdout.buffer.write(b'zeile1\\r\\nzeile2\\r\\n'); "
        "sys.stderr.buffer.write(b'fehler1\\r\\nfehler2\\rende\\r\\n')"
    )
    echt = konsole.lauf_daten([sys.executable, "-c", code], check=False)
    assert echt is not None
    assert echt.stdout == "zeile1\nzeile2\n"
    assert echt.stderr == "fehler1\nfehler2\nende\n"

    def _docker_crlf(befehl: list[str], **_k: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(befehl, 1, b"", b"Fehler A\r\nFehler B\r\n")

    monkeypatch.setattr(subprocess, "run", _docker_crlf)
    ok, text = docker_reste._docker(("ps",))
    assert ok is False
    assert "\r" not in text
    assert "Fehler A\nFehler B" in text
