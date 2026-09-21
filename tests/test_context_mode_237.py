"""context-mode läuft in jeder to-spawn-Session (duoplus-management#237).

Befund 19.09.2026 auf dem Bau-Server: ``bau.py`` startet mit ``--strict-mcp-config``
und leerer ``mcp.json`` — das Plugin-MCP ``context-mode`` fällt weg, die
Plugin-Hooks raten trotzdem zu ``ctx_execute``, das Werkzeug fehlt.

Echt laufen: Plugin-Suche im Dateisystem, ``bau.py``/``wache.py`` als Prozess,
und der Weg-Test startet eine echte ``claude``-Session (kein gestelltes Programm).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import signal
import threading
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
sys.path.insert(0, str(SKILL))

import context_mode_attrappe  # noqa: E402  (tests/hilfen, Pfad setzt conftest)
from to_spawn import context_mode, setup  # noqa: E402

SERVER = "plugin_context-mode_context-mode"


# --- Bausteine -----------------------------------------------------------------


def _plugin_anlegen(claude_dir: Path, version: str = "1.0.169", registrieren: bool = True) -> Path:
    """Gestelltes Plugin unter ``<claude_dir>/plugins`` — ein Helfer für alle Tests (hilfen/)."""
    return context_mode_attrappe.plugin_anlegen(claude_dir.parent, version, registrieren)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    _git(arbeit, "init")
    manifeste = arbeit / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    (manifeste / "spec-900.json").write_text(
        json.dumps(
            {
                "spec": 900,
                "tickets": {
                    "901": {
                        "title": "Wegwerf",
                        "schaetzung_k": 120,
                        "umfang": "Kern bauen.",
                        "mcp": [],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    shutil.copy2(SKILL / "repo-scripts" / "_default.json", manifeste / "_default.json")
    monkeypatch.delenv("TO_SPAWN_REPO", raising=False)
    monkeypatch.chdir(arbeit)
    return arbeit


@pytest.fixture()
def heim(tmp_path: Path) -> Path:
    """Eigenes HOME je Test — Plugin-Suche läuft gegen ``<HOME>/.claude``."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    return home


def _probelauf(
    repo: Path, name: str, nummer: str, home: Path
) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in {"TO_SPAWN_REPO"}}
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["TMPDIR"] = str(home / "tmp")
    (home / "tmp").mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(SKRIPTE / f"{name}.py"), nummer, "--dry-run"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def _temp_ordner(ausgabe: str) -> Path:
    zeile = next(z for z in ausgabe.splitlines() if "Temp:" in z)
    return Path(zeile.split("Temp:", 1)[1].strip())


# --- Modul: Plugin finden -------------------------------------------------------


def test_server_aus_der_plugin_registry(heim: Path) -> None:
    """Pfad kommt aus ``installed_plugins.json`` — nicht aus einer festen Versionsnummer."""
    wurzel = _plugin_anlegen(heim / ".claude", "1.0.169")
    server = context_mode.server_definition(heim / ".claude")
    assert list(server) == [SERVER]
    assert server[SERVER]["command"] == "node"
    assert server[SERVER]["args"] == [str(wurzel / "start.mjs")]
    assert "${CLAUDE_PLUGIN_ROOT}" not in json.dumps(server)


def test_ohne_registry_neueste_version_im_cache(heim: Path) -> None:
    """Registry fehlt/kaputt → neueste Version im Cache, die eine start.mjs trägt."""
    _plugin_anlegen(heim / ".claude", "1.0.169", registrieren=False)
    neu = _plugin_anlegen(heim / ".claude", "1.0.170", registrieren=False)
    leer = heim / ".claude" / "plugins" / "cache" / "context-mode" / "context-mode" / "1.0.171"
    leer.mkdir(parents=True)  # halb installiert, keine start.mjs → zählt nicht
    server = context_mode.server_definition(heim / ".claude")
    assert server[SERVER]["args"] == [str(neu / "start.mjs")]


def test_registry_zeigt_auf_geloeschten_ordner(heim: Path) -> None:
    """Registry-Eintrag ohne Dateien (Plugin entfernt) → Cache-Suche, sonst Fehler."""
    _plugin_anlegen(heim / ".claude", "1.0.169")
    shutil.rmtree(heim / ".claude" / "plugins" / "cache")
    with pytest.raises(context_mode.ContextModeFehlt) as fehler:
        context_mode.server_definition(heim / ".claude")
    assert "context-mode" in str(fehler.value)
    assert "claude plugin install" in str(fehler.value)


def test_fehlt_komplett_laut(heim: Path) -> None:
    with pytest.raises(context_mode.ContextModeFehlt) as fehler:
        context_mode.server_definition(heim / ".claude")
    assert "context-mode" in str(fehler.value)


def test_hinweis_zeilen(heim: Path) -> None:
    """Setup-Zeile: bereit mit Pfad, sonst laut FEHLT mit Install-Befehl."""
    assert "FEHLT" in context_mode.hinweis(heim / ".claude")
    wurzel = _plugin_anlegen(heim / ".claude", "1.0.169")
    zeile = context_mode.hinweis(heim / ".claude")
    assert "bereit" in zeile and str(wurzel) in zeile


def test_setup_zeigt_context_mode(heim: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``to_spawn.py setup --zeige`` trägt die Zeile — Setup meldet das Plugin laut."""
    monkeypatch.setattr(context_mode, "CLAUDE_DIR", heim / ".claude")
    text = setup.zeige({}, heim / "config.json", "linux")
    assert any("context-mode" in z and "FEHLT" in z for z in text.splitlines()), text
    _plugin_anlegen(heim / ".claude", "1.0.169")
    text = setup.zeige({}, heim / "config.json", "linux")
    assert any("context-mode" in z and "bereit" in z for z in text.splitlines()), text


# --- bau.py / wache.py als Prozess ----------------------------------------------


def test_bau_mcp_json_traegt_context_mode_trotz_leerem_manifest(repo: Path, heim: Path) -> None:
    """Manifest ``mcp: []`` + ``core_mcp: []`` → context-mode steht trotzdem drin (Pflicht)."""
    wurzel = _plugin_anlegen(heim / ".claude", "1.0.169")
    ergebnis = _probelauf(repo, "bau", "901", heim)
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    mcp = json.loads((_temp_ordner(ausgabe) / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"][SERVER]["args"] == [str(wurzel / "start.mjs")]
    assert "--strict-mcp-config" in ausgabe  # Sperre bleibt, der Server geht trotzdem mit
    assert SERVER in ausgabe  # Zusammenfassung „MCPs:“ nennt ihn


def test_bau_bricht_ohne_plugin_laut_ab(repo: Path, heim: Path) -> None:
    ergebnis = _probelauf(repo, "bau", "901", heim)
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode != 0, ausgabe
    assert "context-mode" in ausgabe and "claude plugin install" in ausgabe, ausgabe


def test_wache_bricht_ohne_plugin_laut_ab(repo: Path, heim: Path) -> None:
    ergebnis = _probelauf(repo, "wache", "900", heim)
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode != 0, ausgabe
    assert "context-mode" in ausgabe, ausgabe


def test_wache_startet_mit_plugin(repo: Path, heim: Path) -> None:
    _plugin_anlegen(heim / ".claude", "1.0.169")
    ergebnis = _probelauf(repo, "wache", "900", heim)
    ausgabe = ergebnis.stdout + ergebnis.stderr
    assert ergebnis.returncode == 0, ausgabe
    assert "context-mode" in ausgabe, ausgabe  # Startzeile nennt den Pflicht-Server


# --- Weg-Test: echte Session ---------------------------------------------------


def _start_mjs_nachkommen(pid: int) -> list[int]:
    """PIDs aller Nachkommen von ``pid``, die ``start.mjs`` fahren (Linux, /proc)."""
    if not Path("/proc").is_dir():
        return []
    kinder: dict[int, list[int]] = {}
    treffer: list[int] = []
    for eintrag in Path("/proc").iterdir():
        if not eintrag.name.isdigit():
            continue
        try:
            stat = (eintrag / "stat").read_text(encoding="utf-8", errors="replace")
            cmd = (eintrag / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        kinder.setdefault(ppid, []).append(int(eintrag.name))
        if "start.mjs" in cmd:
            treffer.append(int(eintrag.name))
    offen, nachkommen = [pid], set()
    while offen:
        p = offen.pop()
        for k in kinder.get(p, []):
            if k not in nachkommen:
                nachkommen.add(k)
                offen.append(k)
    return [p for p in treffer if p in nachkommen]


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude nicht auf dem PATH")
@pytest.mark.skipif(
    os.environ.get("TO_SPAWN_OHNE_ECHTEN_WEG") == "1",
    reason="TO_SPAWN_OHNE_ECHTEN_WEG=1 — echter claude-Lauf bewusst abgewählt",
)
def test_weg_echte_session_hat_start_mjs_und_ruft_ctx_werkzeug(tmp_path: Path) -> None:
    """Echte ``claude``-Session mit der mcp.json, die bau.py schreibt: start.mjs als
    Kindprozess + belegter ``ctx_execute``-Aufruf (tool_use + tool_result ohne Fehler,
    Rechenergebnis in der Antwort). Kostet einen Haiku-Lauf (~1 Cent, ~20 s)."""
    server = context_mode.server_definition()
    mcp = tmp_path / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": server}), encoding="utf-8")
    einstellungen = tmp_path / "settings.json"
    einstellungen.write_text(json.dumps({"skillOverrides": {}}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_CHILD_SESSION"}
    werkzeug = f"mcp__{SERVER}__ctx_execute"
    prozess = subprocess.Popen(
        [
            "claude", "-p", "--model", "claude-haiku-4-5-20251001",
            "--settings", str(einstellungen), "--mcp-config", str(mcp), "--strict-mcp-config",
            "--no-chrome", "--output-format", "stream-json", "--verbose",
            (
                f"Rufe das Werkzeug {werkzeug} auf mit language \"python\" und "
                "code \"print(6*7*1000+237)\". Antworte nur mit der Ausgabe des Werkzeugs."
            ),
        ],
        cwd=str(tmp_path), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,  # eigene Prozessgruppe: bei Timeout stirbt auch start.mjs
    )
    gesehen: list[int] = []

    def beobachten() -> None:
        while prozess.poll() is None:
            gesehen.extend(p for p in _start_mjs_nachkommen(prozess.pid) if p not in gesehen)
            time.sleep(0.5)

    wache = threading.Thread(target=beobachten, daemon=True)
    wache.start()
    try:
        out, err = prozess.communicate(timeout=180)
    except subprocess.TimeoutExpired:
        if hasattr(os, "killpg"):
            os.killpg(prozess.pid, signal.SIGKILL)
        else:
            prozess.kill()
        prozess.wait(timeout=10)
        pytest.fail("claude antwortete nicht in 180 s")
    wache.join()  # Schleife endet mit dem Prozess — danach liest nur noch dieser Thread
    assert prozess.returncode == 0, err[-2000:]

    ereignisse = [json.loads(z) for z in out.splitlines() if z.startswith("{")]
    aufrufe = [
        block
        for e in ereignisse
        if e.get("type") == "assistant"
        for block in (e.get("message") or {}).get("content") or []
        if block.get("type") == "tool_use" and block.get("name") == werkzeug
    ]
    assert aufrufe, f"kein tool_use {werkzeug} im Lauf: {[e.get('type') for e in ereignisse]}"
    ids = {a["id"] for a in aufrufe}
    ergebnisse = [
        block
        for e in ereignisse
        if e.get("type") == "user"
        for block in (e.get("message") or {}).get("content") or []
        if block.get("type") == "tool_result" and block.get("tool_use_id") in ids
    ]
    assert ergebnisse, "tool_use ohne tool_result"
    assert not any(r.get("is_error") for r in ergebnisse), ergebnisse
    assert "42237" in json.dumps(ergebnisse), ergebnisse  # Rechenergebnis aus dem Sandkasten
    ende = next(e for e in ereignisse if e.get("type") == "result")
    assert ende.get("is_error") is False, ende
    assert "42237" in ende.get("result", ""), ende
    if Path("/proc").is_dir():
        assert gesehen, "kein start.mjs-Kindprozess unter der Session gesehen"


# --- sessions_stand: Spalte ctx -------------------------------------------------


def test_sessions_stand_zeigt_context_mode_je_session() -> None:
    """``sessions`` trägt je Session ✓/✗, ob ein start.mjs-Kind läuft (Beleg für Probesitz #214)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("sessions_stand_237", SKRIPTE / "sessions_stand.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul  # dataclass-Auswertung braucht das Modul in sys.modules
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(spec.name, None)
    P = modul.Prozess
    alle = [
        P(10, 1, "python", "python skripte/bau.py 901", None),
        P(11, 10, "claude", "claude --settings x-bau/901-20260919-000000/settings.json", None),
        P(12, 11, "node", "node /x/context-mode/1.0.169/start.mjs", None),
        P(20, 1, "python", "python skripte/bau.py 902", None),
        P(21, 20, "claude", "claude --settings x-bau/902-20260919-000000/settings.json", None),
    ]
    eintraege = {
        "901": modul.Eintrag("901", "ticket", "mit"),
        "902": modul.Eintrag("902", "ticket", "ohne"),
    }
    modul.fremdes_repo = lambda pid: False
    modul.zuordnen(eintraege, alle)
    assert eintraege["901"].ctx == "✓"
    assert eintraege["902"].ctx == "✗"
    text = modul.tabelle(eintraege, alle_zeigen=True)
    assert "ctx" in text.splitlines()[0]


# --- Fixrunde Prüfpanel 21.09.2026 -----------------------------------------------


def test_veralteter_pfad_in_plugin_json_zaehlt_nicht(heim: Path) -> None:
    """Nach einem Plugin-Update stehen in plugin.json absolute Pfade der Vorversion —
    die mcp.json zeigt trotzdem auf die geprüfte Wurzel (Befund silent-failure-hunter)."""
    wurzel = _plugin_anlegen(heim / ".claude", "1.0.170")
    (wurzel / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "context-mode": {
                        "command": "/usr/bin/node",
                        "args": [str(wurzel.parent / "1.0.169" / "start.mjs")],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    server = context_mode.server_definition(heim / ".claude")
    assert server[SERVER]["args"] == [str(wurzel / "start.mjs")]
    assert Path(server[SERVER]["args"][0]).is_file()


def test_streng_registry_unlesbar(heim: Path) -> None:
    """Registry mit NUL-Bytes (11.–17.09.2026): Claude lädt kein Plugin → Wächter darf nicht starten."""
    _plugin_anlegen(heim / ".claude", "1.0.169")
    (heim / ".claude" / "plugins" / "installed_plugins.json").write_bytes(b"\0\0\0")
    assert context_mode.server_definition(heim / ".claude")  # bau.py: Dateien reichen
    with pytest.raises(context_mode.ContextModeFehlt, match="kein JSON"):
        context_mode.pruefen(heim / ".claude", streng=True)
    assert "FEHLT" in context_mode.hinweis(heim / ".claude")


def test_streng_enabled_plugins_false(heim: Path) -> None:
    _plugin_anlegen(heim / ".claude", "1.0.169")
    (heim / ".claude" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"context-mode@context-mode": False}}), encoding="utf-8"
    )
    with pytest.raises(context_mode.ContextModeFehlt, match="abgeschaltet"):
        context_mode.pruefen(heim / ".claude", streng=True)
    assert context_mode.pruefen(heim / ".claude")  # ohne streng: bau.py trägt den Server selbst ein


def test_wache_bricht_bei_abgeschaltetem_plugin_ab(repo: Path, heim: Path) -> None:
    _plugin_anlegen(heim / ".claude", "1.0.169")
    (heim / ".claude" / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"context-mode@context-mode": False}}), encoding="utf-8"
    )
    ergebnis = _probelauf(repo, "wache", "900", heim)
    assert ergebnis.returncode != 0
    assert "abgeschaltet" in ergebnis.stdout + ergebnis.stderr


def test_claude_config_dir_wird_beachtet(heim: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``CLAUDE_CONFIG_DIR`` gilt für Claude Code — und damit auch für die Plugin-Suche."""
    import importlib

    anderswo = heim / "anderswo" / ".claude"  # ≠ <HOME>/.claude
    wurzel = _plugin_anlegen(anderswo, "1.0.169")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(anderswo))
    modul = importlib.reload(context_mode)
    try:
        assert modul.CLAUDE_DIR == anderswo
        assert modul.plugin_wurzel() == wurzel
    finally:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR")
        importlib.reload(context_mode)


def test_sessions_stand_hook_sessionstart_zaehlt_nicht() -> None:
    """``hooks/sessionstart.mjs`` (Plugin-Hook) ist kein MCP-Server — Spalte ctx bleibt ✗."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("sessions_stand_237b", SKRIPTE / "sessions_stand.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(spec.name, None)
    P = modul.Prozess
    alle = [
        P(11, 1, "claude", "claude --settings x", None),
        P(12, 11, "node", '"/usr/bin/node" "/x/context-mode/1.0.169/hooks/sessionstart.mjs"', None),
    ]
    assert modul.context_mode_zustand(11, alle) == "✗"
    alle.append(P(13, 11, "node", '"/usr/bin/node" "C:\\x\\context-mode\\1.0.169\\start.mjs"', None))
    assert modul.context_mode_zustand(11, alle) == "✓"
