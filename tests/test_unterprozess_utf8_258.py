"""Wächter (#258): ``text=True``-Unterprozess-Aufrufe lesen UTF-8, nie die Windows-Locale."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from to_spawn import eigener_handoff, konsole

WURZEL = Path(__file__).resolve().parent.parent

# Alle Produkt-Dateien unter to_spawn/ und skripte/ sind auf konsole/UTF-8 migriert (#258).
NOCH_NICHT_MIGRIERT: set[str] = set()


KONSTANTEN = {
    "UNTERPROZESS_TEXT",
    "UNTERPROZESS_ANZEIGE",
    "UNTERPROZESS_DATEN",
    "UNTERPROZESS_PFADE",
}


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


def _produkt_dateien() -> list[Path]:
    dateien = [
        *sorted((WURZEL / "to_spawn").rglob("*.py")),
        *sorted((WURZEL / "skripte").rglob("*.py")),
    ]
    einstieg = WURZEL / "to_spawn.py"
    if einstieg.is_file():
        dateien.append(einstieg)
    return dateien


def test_text_true_ohne_utf8_nur_in_allowlist() -> None:
    schlecht = {}
    for datei in _produkt_dateien():
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


def test_waechter_erlaubt_unterprozess_pfade(tmp_path: Path) -> None:
    quelle = tmp_path / "pfade.py"
    quelle.write_text(
        "import subprocess\n"
        "subprocess.run(['git', 'status'], text=True, **konsole.UNTERPROZESS_PFADE)\n",
        encoding="utf-8",
    )
    assert _ohne_utf8(quelle) == []


def test_rest_module_nicht_utf8_ohne_absturz(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#258 Rest-Migration: ``lauf_daten``-Stellen stürzen bei Nicht-UTF-8 nicht ab."""
    from to_spawn import belege, komplett_lauf, nest

    datei = tmp_path / "beleg.txt"
    datei.write_text("ok", encoding="utf-8")
    erwartet_mtime = datei.stat().st_mtime

    monkeypatch.setattr(subprocess, "run", _kaputt_lauf)
    assert belege.zeitpunkt(datei) == pytest.approx(erwartet_mtime)
    assert komplett_lauf._git(tmp_path, "rev-parse", "HEAD") is None
    with pytest.raises(nest.NestFehler, match="UTF-8"):
        nest._bws_geheimnisse("bws", None, "geheim-tok", {})


def _lade_skript(name: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    pfad = WURZEL / "skripte" / f"{name}.py"
    modul_name = f"_test_utf8_258_{name}"
    spec = importlib.util.spec_from_file_location(modul_name, pfad)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, modul_name, modul)
    spec.loader.exec_module(modul)
    return modul


def test_migrierte_aufrufe_uebergeben_utf8_lesart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#258: Alle nachgezogenen Unterprozess-Stellen nutzen UTF-8 über ``konsole``."""
    from to_spawn import (
        hausmeister_aufgabe,
        nest,
        pc_fenster,
        prozessbaum,
        respawn,
        setup,
        starter,
        startklar,
    )

    gesehen: list[dict[str, Any]] = []

    def _merke_run(befehl: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        gesehen.append(dict(kwargs))
        return subprocess.CompletedProcess(befehl, 0, "", "")

    def _merke_starte(befehl: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        gesehen.append(dict(kwargs))
        return subprocess.CompletedProcess(befehl, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _merke_run)
    monkeypatch.setattr(starter, "starte", _merke_starte)
    lebt_werte = iter([True, False])
    monkeypatch.setattr(prozessbaum, "lebt", lambda _pid: next(lebt_werte, False))

    hausmeister_aufgabe._schtasks("/Query")
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "replace"

    nest._git(tmp_path, "status")
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "surrogateescape"

    pc_fenster.baum_beenden(999999)
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "replace"

    pc_fenster.tab_starten(tmp_path, 258, "auftrag")
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "replace"

    werkzeug = respawn.TmuxWerkzeug()
    werkzeug.bau_prozesse()
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "replace"

    werkzeug._git(tmp_path, "status")
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "surrogateescape"

    setup.pruefe_start("tmux", "tmux")
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "replace"

    startklar._git_lauf(tmp_path, "status")
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "surrogateescape"

    startklar._ausfuehren(["echo", "ok"], 5)
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "replace"

    startklar._fehlende_pakete(Path(sys.executable), ["os"])
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "replace"

    bau_skript = _lade_skript("bau", monkeypatch)
    bau_skript.repo_aus_origin(repo=tmp_path)
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "replace"

    wache_skript = _lade_skript("wache", monkeypatch)
    wache_skript.repo_aus_origin()
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "replace"

    sessions_skript = _lade_skript("sessions_stand", monkeypatch)
    sessions_skript.prozesse_linux()
    assert gesehen[-1]["encoding"] == "utf-8"
    assert gesehen[-1]["errors"] == "replace"
