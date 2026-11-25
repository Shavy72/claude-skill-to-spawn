"""Abgleich-Befehl für ~/.claude zwischen zwei Rechnern mit Liste „gewollt verschieden“ (#561).

Prüft die Abnahme-Kriterien aus Issue #561:
1. Zwei Verzeichnisse mit einem Unterschied → genau dieser wird gemeldet (Standard: nur lesen).
2. Unterschied steht in „gewollt verschieden“ → keine Meldung.
3. Angleichen nur auf ausdrücklichen Schalter, je Datei mit Sicherung, ``settings.json``
   nur atomar und nach Gültigkeits-Prüfung; bei ungültiger ``settings.json`` bleibt alles unverändert.
4. Keine Geheimnisse in der Ausgabe (Schlüssel aus ``env`` nur als Name, nie als Wert).
5. Läuft auf Linux und Windows (Pfad-Normalisierung ``\\`` → ``/``, keine POSIX-Sonderbefehle).
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))


def _lade_cli() -> Any:
    spec = importlib.util.spec_from_file_location("to_spawn_cli_561", SKILL / "to_spawn.py")
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _schreibe_json(pfad: Path, daten: dict[str, Any]) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps(daten, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _schreibe_text(pfad: Path, inhalt: str) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(inhalt, encoding="utf-8")


def test_ein_unterschied_in_datei_wird_genau_gemeldet(tmp_path: Path) -> None:
    """Abnahme 1a: Zwei Verzeichnisse mit einem Datei-Unterschied → genau dieser wird gemeldet."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    _schreibe_json(quelle / "settings.json", {"env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3"}})
    _schreibe_json(ziel / "settings.json", {"env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3"}})
    _schreibe_text(quelle / "hooks" / "filter-test-output.sh", "# Hook-Skript\necho neu\n")
    _schreibe_text(ziel / "hooks" / "filter-test-output.sh", "# Hook-Skript\necho alt\n")

    erg = abgleich.vergleichen(quelle, ziel)
    assert not erg.gleich
    assert [a.kennung for a in erg.abweichungen] == ["hooks/filter-test-output.sh"]
    assert erg.abweichungen[0].art == "abweichung"
    # Nur lesen: Ziel unverändert, keine Sicherung angelegt.
    assert (ziel / "hooks" / "filter-test-output.sh").read_text(encoding="utf-8") == "# Hook-Skript\necho alt\n"
    assert list((ziel / "hooks").glob("*.bak-*")) == []


def test_ein_unterschied_in_settings_schluessel_wird_genau_gemeldet(tmp_path: Path) -> None:
    """Abnahme 1b: Fehlender Schlüssel wie ``CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`` wird exakt gemeldet."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    _schreibe_json(
        quelle / "settings.json",
        {
            "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3"},
            "hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "filter-test-output.sh"}]}]},
        },
    )
    _schreibe_json(
        ziel / "settings.json",
        {
            "hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "filter-test-output.sh"}]}]},
        },
    )

    erg = abgleich.vergleichen(quelle, ziel)
    assert not erg.gleich
    assert [a.kennung for a in erg.abweichungen] == [
        "settings.json:env.CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"
    ]
    assert erg.abweichungen[0].art == "fehlt_ziel"


def test_unterschied_in_gewollt_verschieden_keine_meldung(tmp_path: Path) -> None:
    """Abnahme 2: Einträge in der festen Liste „gewollt verschieden“ lösen keine Meldung aus."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    _schreibe_json(
        quelle / "settings.json",
        {
            "env": {
                "HOME": "C:\\Users\\nutzer",
                "CLAUDE_CONFIG_DIR": "C:\\Users\\nutzer\\.claude",
                "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3",
            },
            "permissions": {"allow": ["Bash(git status:*)"]},
            "skillsWhitelist": ["alle"],
        },
    )
    _schreibe_json(
        ziel / "settings.json",
        {
            "env": {
                "HOME": "/home/bau",
                "CLAUDE_CONFIG_DIR": "/home/bau/.claude",
                "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3",
            },
            "permissions": {"allow": ["Bash(git diff:*)"], "additionalDirectories": ["/home/bau/wt"]},
            "skillsWhitelist": ["to-spawn"],
        },
    )

    erg = abgleich.vergleichen(quelle, ziel)
    assert erg.gleich
    assert erg.abweichungen == ()
    assert len(erg.ausgenommen) >= 3


def test_benutzerdefinierte_ausnahme_unterdrueckt_datei_mit_windows_trennern(tmp_path: Path) -> None:
    """Windows-Pfadtrenner (``\\``) in Ausnahmen und Dateilisten werden einheitlich normalisiert."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    _schreibe_text(quelle / "hooks" / "smart-zone" / "kontext-stand.mjs", "export const v = 2;\n")
    _schreibe_text(ziel / "hooks" / "smart-zone" / "kontext-stand.mjs", "export const v = 1;\n")

    erg = abgleich.vergleichen(
        quelle,
        ziel,
        gewollt_verschieden=(*abgleich.GEWOLLT_VERSCHIEDEN, r"hooks\smart-zone\kontext-stand.mjs"),
    )
    assert erg.gleich
    assert erg.abweichungen == ()


def test_keine_geheimnisse_aus_env_in_ausgabe_und_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Soll 4: Werte unter ``env`` erscheinen niemals in Text-, JSON-Ausgabe oder Logs."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    geheim_pc = "SUPER_GEHEIMER_API_TOKEN_PC_987654"
    geheim_srv = "ANDERER_GEHEIMER_SCHLUESSEL_SRV_123456"
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    _schreibe_json(
        quelle / "settings.json",
        {
            "env": {
                "ANTHROPIC_API_KEY": geheim_pc,
                "ONLY_ON_PC_SECRET": "NUR_PC_GEHEIM_XYZ",
            }
        },
    )
    _schreibe_json(
        ziel / "settings.json",
        {
            "env": {
                "ANTHROPIC_API_KEY": geheim_srv,
            }
        },
    )

    with caplog.at_level(logging.DEBUG, logger="to_spawn.abgleich"):
        erg = abgleich.vergleichen(quelle, ziel)
        text = erg.text()
        als_json = json.dumps(erg.als_dict(), ensure_ascii=False)

    assert [a.kennung for a in erg.abweichungen] == [
        "settings.json:env.ANTHROPIC_API_KEY",
        "settings.json:env.ONLY_ON_PC_SECRET",
    ]
    for geheim in (geheim_pc, geheim_srv, "NUR_PC_GEHEIM_XYZ"):
        assert geheim not in text
        assert geheim not in als_json
        assert geheim not in caplog.text
    assert "ANTHROPIC_API_KEY" in text
    assert "ONLY_ON_PC_SECRET" in text


def test_angleichen_schreibt_sicherung_und_bewahrt_gewollt_verschiedenes(tmp_path: Path) -> None:
    """Abnahme 3a: Angleichen sichert jede geänderte Datei, übernimmt Unterschiede und schützt Ausnahmen."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    _schreibe_json(
        quelle / "settings.json",
        {
            "env": {
                "HOME": "C:\\Users\\nutzer",
                "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3",
            },
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "filter-test-output.sh"}]}
                ]
            },
            "permissions": {"allow": ["Bash(pc-only:*)"]},
        },
    )
    _schreibe_json(
        ziel / "settings.json",
        {
            "env": {"HOME": "/home/bau"},
            "permissions": {"allow": ["Bash(server-only:*)"]},
            "skillsWhitelist": ["to-spawn"],
        },
    )
    _schreibe_text(quelle / "hooks" / "filter-test-output.sh", "# Hook-Skript\necho aktuell\n")
    _schreibe_text(ziel / "hooks" / "filter-test-output.sh", "# Hook-Skript\necho alt\n")
    _schreibe_text(quelle / "hooks" / "smart-zone" / "kopien-check.mjs", "// neu\n")

    erg = abgleich.angleichen(quelle, ziel, zeit="20261125-071500")

    assert set(erg.aktualisiert) == {
        "settings.json",
        "hooks/filter-test-output.sh",
        "hooks/smart-zone/kopien-check.mjs",
    }
    # Sicherungen für vorher bestehende Dateien angelegt mit altem Inhalt.
    settings_bak = ziel / "settings.json.bak-20261125-071500"
    hook_bak = ziel / "hooks" / "filter-test-output.sh.bak-20261125-071500"
    assert settings_bak.is_file()
    assert hook_bak.is_file()
    assert hook_bak.read_text(encoding="utf-8") == "# Hook-Skript\necho alt\n"
    assert json.loads(settings_bak.read_text(encoding="utf-8"))["env"] == {"HOME": "/home/bau"}

    # Ziel ist jetzt angeglichen, aber „gewollt verschieden“ blieb unberührt!
    ziel_settings = json.loads((ziel / "settings.json").read_text(encoding="utf-8"))
    assert ziel_settings["env"]["CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH"] == "3"
    assert ziel_settings["env"]["HOME"] == "/home/bau"
    assert ziel_settings["permissions"] == {"allow": ["Bash(server-only:*)"]}
    assert ziel_settings["skillsWhitelist"] == ["to-spawn"]
    assert "PostToolUse" in ziel_settings["hooks"]
    assert (ziel / "hooks" / "filter-test-output.sh").read_text(encoding="utf-8") == "# Hook-Skript\necho aktuell\n"
    assert (ziel / "hooks" / "smart-zone" / "kopien-check.mjs").read_text(encoding="utf-8") == "// neu\n"

    # Folgevergleich meldet keine Unterschiede mehr.
    danach = abgleich.vergleichen(quelle, ziel)
    assert danach.gleich


@pytest.mark.parametrize(
    ("kaputt_seite", "kaputt_inhalt"),
    [
        ("quelle", "{ungueltig"),
        ("ziel", "{ungueltig"),
        ("quelle", "[]"),
        ("ziel", '{"hooks": null}'),
        ("quelle", '{"env": "kein-objekt"}'),
    ],
)
def test_angleichen_laesst_bei_ungueltiger_settings_json_alles_unveraendert(
    tmp_path: Path, kaputt_seite: str, kaputt_inhalt: str
) -> None:
    """Abnahme 3b: Ungültige ``settings.json`` (Quelle oder Ziel) → Abbruch, keine Datei/Sicherung geändert."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    _schreibe_json(quelle / "settings.json", {"env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3"}})
    _schreibe_json(ziel / "settings.json", {"env": {"HOME": "/home/bau"}})
    _schreibe_text(quelle / "hooks" / "filter-test-output.sh", "# Hook-Skript\necho neu\n")
    _schreibe_text(ziel / "hooks" / "filter-test-output.sh", "# Hook-Skript\necho alt\n")

    (quelle if kaputt_seite == "quelle" else ziel).joinpath("settings.json").write_text(
        kaputt_inhalt, encoding="utf-8"
    )
    vorher_settings = (ziel / "settings.json").read_bytes()
    vorher_hook = (ziel / "hooks" / "filter-test-output.sh").read_bytes()

    with pytest.raises(abgleich.AbgleichFehler):
        abgleich.angleichen(quelle, ziel, zeit="20261125-071600")

    # Weder settings.json noch andere Dateien noch Sicherungen wurden angefasst.
    assert (ziel / "settings.json").read_bytes() == vorher_settings
    assert (ziel / "hooks" / "filter-test-output.sh").read_bytes() == vorher_hook
    assert list(ziel.rglob("*.bak-*")) == []


def test_angleichen_nutzt_atomares_schreiben_fuer_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``settings.json`` wird über :func:`to_spawn.manifest.schreibe_atomar` geschrieben."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    manifest = importlib.import_module("to_spawn.manifest")
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    _schreibe_json(quelle / "settings.json", {"env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3"}})
    _schreibe_json(ziel / "settings.json", {})

    aufrufe: list[Path] = []
    echt = manifest.schreibe_atomar

    def spion(datei: Path, daten: dict[str, Any]) -> None:
        aufrufe.append(datei)
        echt(datei, daten)

    monkeypatch.setattr(manifest, "schreibe_atomar", spion)
    abgleich.angleichen(quelle, ziel, zeit="20261125-071700")
    assert aufrufe == [ziel / "settings.json"]


def test_boese_dateipfade_abgelehnt(tmp_path: Path) -> None:
    """Pfad-Ausbrüche (``..``, absolute Pfade, Laufwerksbuchstaben) werden abgelehnt."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    quelle.mkdir()
    ziel.mkdir()
    for boese in ("../geheim.txt", "/etc/passwd", "C:/Windows/win.ini", "hooks/../../x"):
        with pytest.raises(abgleich.AbgleichFehler):
            abgleich.vergleichen(quelle, ziel, dateien=(boese,))


def test_cli_nur_lesen_standard_und_angleichen_schalter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI ``to_spawn.py abgleich``: Standard nur lesen (Exit 3 bei Abweichung), ``--angleichen`` ändert."""
    cli = _lade_cli()
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    geheim = "CLI_GEHEIM_WERT_42"
    _schreibe_json(
        quelle / "settings.json",
        {"env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3", "API_SECRET": geheim}},
    )
    _schreibe_json(ziel / "settings.json", {"env": {"HOME": "/home/bau"}})

    # Standard: nur lesen → Exit 3, kein Geheimnis in stdout/stderr, Ziel unverändert.
    code = cli.main(["abgleich", str(quelle), str(ziel)])
    aus = capsys.readouterr()
    assert code == 3
    assert "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH" in aus.out
    assert "API_SECRET" in aus.out
    assert geheim not in aus.out
    assert geheim not in aus.err
    assert json.loads((ziel / "settings.json").read_text(encoding="utf-8")) == {"env": {"HOME": "/home/bau"}}

    # Mit ausdrücklichem Schalter --angleichen → Exit 0, Sicherung da, Folge-Check Exit 0.
    code_sync = cli.main(["abgleich", str(quelle), str(ziel), "--angleichen"])
    aus_sync = capsys.readouterr()
    assert code_sync == 0
    assert geheim not in aus_sync.out
    assert list(ziel.glob("settings.json.bak-*"))

    assert cli.main(["abgleich", str(quelle), str(ziel)]) == 0
    capsys.readouterr()

    # Liste „gewollt verschieden“ über CLI abrufbar.
    assert cli.main(["abgleich", "--liste-ausnahmen"]) == 0
    liste = capsys.readouterr().out
    assert "settings.json:permissions" in liste
    assert "settings.json:skillsWhitelist" in liste


def test_cli_echter_prozess_aufruf(tmp_path: Path) -> None:
    """Echter Prozess-Aufruf von ``to_spawn.py abgleich`` läuft plattformunabhängig."""
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    _schreibe_json(quelle / "settings.json", {"env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3"}})
    _schreibe_json(ziel / "settings.json", {"env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "3"}})
    lauf = subprocess.run(
        [sys.executable, str(SKILL / "to_spawn.py"), "abgleich", str(quelle), str(ziel)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stderr


def test_ausnahmen_datei_json_und_text_sowie_cli_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--ausnahmen-datei`` (JSON und Text) und ``--json`` arbeiten ohne Geheimnisse."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    cli = _lade_cli()
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    _schreibe_json(
        quelle / "settings.json",
        {"env": {"EIGENES_ENV": "GEHEIM_ABC"}, "modell": "a"},
    )
    _schreibe_json(
        ziel / "settings.json",
        {"env": {"EIGENES_ENV": "GEHEIM_XYZ"}, "modell": "b"},
    )

    aus_json = tmp_path / "ausnahmen.json"
    _schreibe_json(aus_json, {"gewollt_verschieden": ["settings.json:env.EIGENES_ENV"]})
    code = cli.main(
        ["abgleich", str(quelle), str(ziel), "--ausnahmen-datei", str(aus_json), "--json"]
    )
    aus = capsys.readouterr()
    assert code == 3
    daten = json.loads(aus.out)
    assert daten["gleich"] is False
    assert [a["kennung"] for a in daten["abweichungen"]] == ["settings.json:modell"]
    assert "GEHEIM_ABC" not in aus.out and "GEHEIM_XYZ" not in aus.out

    aus_txt = tmp_path / "ausnahmen.txt"
    _schreibe_text(aus_txt, "# Kommentar\nsettings.json:env.EIGENES_ENV\nsettings.json:modell # gewollt\n")
    assert len(abgleich.lade_ausnahmen(aus_txt)) == 2
    assert cli.main(["abgleich", str(quelle), str(ziel), "--ausnahmen-datei", str(aus_txt)]) == 0
    capsys.readouterr()


def test_symlink_ausbruch_wird_abgelehnt(tmp_path: Path) -> None:
    """Ein Symlink in Quelle/Ziel, der aus dem Ordner zeigt, wird abgelehnt."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    fremd = tmp_path / "fremd.sh"
    fremd.write_text("geheim\n", encoding="utf-8")
    (quelle / "hooks").mkdir(parents=True)
    ziel.mkdir(parents=True)
    try:
        (quelle / "hooks" / "filter-test-output.sh").symlink_to(fremd)
    except OSError:
        pytest.skip("Symlink nicht anlegbar (Windows ohne Recht)")
    with pytest.raises(abgleich.AbgleichFehler):
        abgleich.vergleichen(quelle, ziel)


def test_angleichen_sichert_mehrfach_ohne_sicherung_zu_ueberschreiben(tmp_path: Path) -> None:
    """Zwei Läufe mit gleichem Zeitstempel überschreiben die erste Sicherung nicht."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    _schreibe_text(quelle / "hooks" / "filter-test-output.sh", "v2\n")
    _schreibe_text(ziel / "hooks" / "filter-test-output.sh", "v1\n")
    erg1 = abgleich.angleichen(quelle, ziel, zeit="20261125-072000")
    _schreibe_text(quelle / "hooks" / "filter-test-output.sh", "v3\n")
    erg2 = abgleich.angleichen(quelle, ziel, zeit="20261125-072000")
    assert erg1.sicherungen[0] != erg2.sicherungen[0]
    assert erg1.sicherungen[0].read_text(encoding="utf-8") == "v1\n"
    assert erg2.sicherungen[0].read_text(encoding="utf-8") == "v2\n"


def test_angleichen_loescht_keine_ziel_einzeldateien_oder_schluessel(tmp_path: Path) -> None:
    """Was nur im Ziel existiert, wird beim Angleichen nicht gelöscht, sondern übersprungen."""
    abgleich = importlib.import_module("to_spawn.abgleich")
    quelle = tmp_path / "pc"
    ziel = tmp_path / "server"
    _schreibe_json(quelle / "settings.json", {"a": 1})
    _schreibe_json(ziel / "settings.json", {"a": 1, "nur_server": True})
    _schreibe_text(ziel / "hooks" / "filter-test-output.sh", "# Hook-Skript\n")

    erg = abgleich.angleichen(quelle, ziel, zeit="20261125-072100")
    assert erg.aktualisiert == ()
    assert {a.kennung for a in erg.uebersprungen} == {
        "settings.json:nur_server",
        "hooks/filter-test-output.sh",
    }
    assert (ziel / "hooks" / "filter-test-output.sh").is_file()
    assert json.loads((ziel / "settings.json").read_text(encoding="utf-8")) == {"a": 1, "nur_server": True}

