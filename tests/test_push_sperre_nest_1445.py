"""#1445: ``nest sandbox`` richtet im Klon die master-Push-Sperre aus #1442 ein.

Weg-Test ohne Attrappen: echtes Bare-Repo als ``origin``, echter Klon, echte CLI
``to_spawn.py nest sandbox`` (der Weg, den ``bau.py`` vor jedem Session-Start geht),
echter Installer und echter Hook des Projekt-Repos, echte ``git push``-Aufrufe.
Der Modus ``tickets`` steht wie im Betrieb in der ``.env`` der Schlange; der Hook
liest sie über ``SCHLANGE_ENV_DATEI``. Telegram geht über ``WAECHTER_MELDUNG_DATEI``
in eine Datei statt ins Netz.

Die beiden Hook-Dateien unter ``tests/hilfen/push_sperre_1442/`` sind Testdaten:
Stand ``Shavy72/duoplus-management`` origin/master c711a62 (``scripts/hooks/``).
Der Skill selbst kopiert nichts, er ruft nur den Installer im Klon auf.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_bau_ohne_prompt_431 import _lade_bau
from test_nest_210 import _cli, _git, _neues_repo, _umgebung
from test_rolle_statt_name_323 import NAME, NAMEN, NAMEN_DATEI

from to_spawn import nest

HILFEN = Path(__file__).parent / "hilfen" / "push_sperre_1442"


def _welt(tmp_path: Path, mit_installer: bool = True, installer: str | None = None) -> tuple[Path, Path]:
    """Bare-origin mit (oder ohne) Hook-Dateien, daneben ein Hauptklon. Rückgabe (klon, heim).

    ``installer``: eigener Installer-Text statt der Projekt-Kopie (Fehlerzweige).
    """
    quelle = _neues_repo(tmp_path / "quelle")
    if mit_installer:
        hooks = quelle / "scripts" / "hooks"
        hooks.mkdir(parents=True)
        for name in ("install_git_hooks.sh", "pre-push-master-sperre.sh"):
            shutil.copy(HILFEN / name, hooks / name)
        if installer is not None:
            (hooks / "install_git_hooks.sh").write_text(installer, encoding="utf-8")
        _git(quelle, "add", "-A")
        _git(quelle, "commit", "-m", "Push-Sperre (#1442)")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(quelle), str(origin))
    klon = tmp_path / "klon"
    _git(tmp_path, "clone", "-q", str(origin), str(klon))
    _git(klon, "config", "user.email", "test@example.invalid")
    _git(klon, "config", "user.name", "Test")
    heim = tmp_path / "heim"
    heim.mkdir()
    return klon, heim


def _nest_sandbox(tmp_path: Path, klon: Path, heim: Path) -> subprocess.CompletedProcess[str]:
    return _cli(
        "sandbox",
        "1445",
        "--repo",
        str(klon),
        cwd=klon,
        env=_umgebung(heim, BAU_WT_DIR=str(tmp_path / "wt")),
    )


def _push(wt: Path, tmp_path: Path, ziel: str) -> subprocess.CompletedProcess[str]:
    env_datei = tmp_path / "schlange.env"
    env_datei.write_text("SCHLANGE_MODUS=tickets\n", encoding="utf-8")
    umgebung = _umgebung(
        tmp_path / "heim",
        SCHLANGE_ENV_DATEI=str(env_datei),
        WAECHTER_MELDUNG_DATEI=str(tmp_path / "meldungen.jsonl"),
    )
    for name in ("MASTER_PUSH_ERLAUBT", "DEPLOY_SCHLANGE_MERGE", "SCHLANGE_MELDUNG_DATEI"):
        umgebung.pop(name, None)
    return subprocess.run(
        ["git", "push", "origin", f"HEAD:{ziel}"],
        cwd=str(wt),
        env=umgebung,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_frisches_nest_sperrt_master_push_im_modus_tickets(tmp_path: Path) -> None:
    klon, heim = _welt(tmp_path)
    ergebnis = _nest_sandbox(tmp_path, klon, heim)
    assert ergebnis.returncode == 0, ergebnis.stderr
    wt = Path(ergebnis.stdout.strip()).parent.parent  # <worktree>/.to-spawn/sandbox.json
    (wt / "arbeit.txt").write_text("neu\n", encoding="utf-8")
    _git(wt, "add", "arbeit.txt")
    _git(wt, "commit", "-m", "Arbeit")

    auf_master = _push(wt, tmp_path, "refs/heads/master")
    assert auf_master.returncode != 0, f"Push auf master ging durch:\n{auf_master.stderr}"
    assert "Modus tickets" in auf_master.stderr
    assert "Arbeit" not in _git(klon, "log", "-1", "--format=%s", "origin/master")

    auf_ticket = _push(wt, tmp_path, "refs/heads/ticket/1445-probe")
    assert auf_ticket.returncode == 0, auf_ticket.stderr


def test_klon_ohne_installer_startet_trotzdem(tmp_path: Path) -> None:
    """Alter Projekt-Stand ohne Installer: Sandbox-Datei entsteht, klare Log-Zeile, kein Abbruch."""
    klon, heim = _welt(tmp_path, mit_installer=False)
    ergebnis = _nest_sandbox(tmp_path, klon, heim)
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert Path(ergebnis.stdout.strip()).is_file()
    assert "Push-Sperre" in ergebnis.stderr and "fehlt" in ergebnis.stderr


# --- Fix-Runde 1: echter Start-Weg, Quelle origin, Fehlerzweige -----------------------


def _arbeit_committen(ordner: Path) -> None:
    (ordner / "arbeit.txt").write_text("neu\n", encoding="utf-8")
    _git(ordner, "add", "arbeit.txt")
    _git(ordner, "commit", "-m", "Arbeit")


def test_bau_start_mit_sandbox_aus_sperrt_master_push(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B1: ``bau.main`` mit ``sandbox.modus "aus"`` (Ist aller Klone) richtet die Sperre ein.

    ``main()``, ``nest.sandbox_start`` und der Installer laufen echt; gestellt sind nur
    GitHub (Manifest, Blocker), die Speicher-Sperre und der Claude-Start selbst.
    """
    klon, _heim = _welt(tmp_path)
    (klon / ".to-spawn").mkdir()
    (klon / ".to-spawn" / "config.json").write_text('{"sandbox": {"modus": "aus"}}', encoding="utf-8")
    bau = _lade_bau(monkeypatch, klon)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    monkeypatch.setattr(bau, "gh_repo_ermitteln", lambda _repo: "test/push-sperre")
    monkeypatch.setattr(bau.config, "sicherstellen", lambda _repo: None)
    monkeypatch.setattr(bau, "load_default", lambda *_a, **_k: {"prompt_template": "T {ticket}"})
    monkeypatch.setattr(bau, "find_manifest", lambda _t: ({"spec": "9"}, {"title": "Titel"}))
    monkeypatch.setattr(bau, "build_prompt", lambda *_a, **_k: "PROMPT")
    monkeypatch.setattr(bau, "known_skill_names", lambda: set())
    monkeypatch.setattr(bau, "mcp_catalog", dict)
    monkeypatch.setattr(bau.context_mode, "server_definition", lambda _d: {})
    monkeypatch.setattr(bau, "blocker_schranke", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "auf_speicher_warten", lambda *_a, **_k: None)
    monkeypatch.setattr(bau.vertrauen, "still_sicherstellen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "umzug_anfragen_aufraeumen", lambda _t: tmp_path / "anfrage.json")
    monkeypatch.setattr(bau, "session_id_setzen", lambda *_a, **_k: None)
    monkeypatch.setattr(bau, "bau_log_umgebung", lambda *_a, **_k: {})
    praefixe: list[list[str]] = []
    monkeypatch.setattr(bau, "starte_session", lambda cmd, _u: (praefixe.append(cmd), (0, None))[1])
    monkeypatch.setattr(sys, "argv", ["bau.py", "1445", "--sofort", "--staffel-max", "1"])
    umwelt = dict(os.environ)
    try:
        assert bau.main() == 0
    finally:
        os.environ.clear()
        os.environ.update(umwelt)
    assert praefixe and praefixe[0][0] != "srt", "Sandbox war an — Test prüft den Weg ohne Sandbox"

    _arbeit_committen(klon)
    auf_master = _push(klon, tmp_path, "refs/heads/master")
    assert auf_master.returncode != 0, f"Push auf master ging durch:\n{auf_master.stderr}"
    assert "Modus tickets" in auf_master.stderr


def test_installer_kommt_aus_origin_nicht_aus_dem_arbeitsordner(tmp_path: Path) -> None:
    """B2: Ein im Arbeitsordner veränderter Installer läuft nie, es zählt ``origin/master``."""
    klon, _heim = _welt(tmp_path)
    marke = tmp_path / "boese-gelaufen"
    (klon / "scripts" / "hooks" / "install_git_hooks.sh").write_text(f"touch {marke}\nexit 0\n", encoding="utf-8")
    assert nest.git_hooks_einrichten(klon) is True
    assert not marke.exists(), "Installer aus dem Arbeitsordner ausgeführt"
    hook = Path(_git(klon, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()) / "hooks" / "pre-push"
    assert "pre-push-master-sperre.sh" in hook.read_text(encoding="utf-8")


def test_installer_exit_ungleich_null_warnt_ohne_abbruch(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    klon, _heim = _welt(tmp_path, installer="echo kaputt >&2\nexit 3\n")
    with caplog.at_level(logging.WARNING, logger=nest.log.name):
        assert nest.git_hooks_einrichten(klon) is False
    assert "endete mit 3" in caplog.text and "kaputt" in caplog.text


def test_installer_timeout_warnt_ohne_abbruch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    klon, _heim = _welt(tmp_path, installer="sleep 5\n")
    monkeypatch.setattr(nest, "GIT_HOOKS_TIMEOUT_S", 1)
    with caplog.at_level(logging.WARNING, logger=nest.log.name):
        assert nest.git_hooks_einrichten(klon) is False
    assert "Push-Sperre nicht eingerichtet" in caplog.text and "timed out" in caplog.text


def test_oserror_beim_schreiben_bricht_start_nicht_ab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Fix-Runde 2: Platte voll/Rechte beim Ablegen der Hook-Dateien → Warnung, ``False``, kein Wurf."""
    klon, _heim = _welt(tmp_path)

    def _platte_voll(_self: Path, _daten: bytes) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", _platte_voll)
    with caplog.at_level(logging.WARNING, logger=nest.log.name):
        assert nest.git_hooks_einrichten(klon) is False
    assert "Push-Sperre nicht eingerichtet" in caplog.text and "No space left" in caplog.text


PROJEKT_KLON = Path.home() / "duoplus-leit"


@pytest.mark.parametrize("name", ["install_git_hooks.sh", "pre-push-master-sperre.sh"])
def test_testdaten_gleich_projekt_origin(name: str) -> None:
    """B4: Testdaten-Kopie byte-gleich mit ``origin/master`` des Projekt-Klons (nur lesen, kein fetch)."""
    if not (PROJEKT_KLON / ".git").exists():
        pytest.skip(f"Projekt-Klon {PROJEKT_KLON} nicht da — Abgleich nur auf dem Bau-Server")
    original = subprocess.run(
        ["git", "-C", str(PROJEKT_KLON), "show", f"origin/master:scripts/hooks/{name}"],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if original.returncode != 0:
        pytest.skip(f"origin/master im Projekt-Klon hat scripts/hooks/{name} nicht")
    kopie = (HILFEN / name).read_bytes()
    if kopie == original.stdout:
        return
    if not NAMEN:
        pytest.skip(f"Kopie weicht ab, Rollenwort-Prüfung braucht die private Namensliste: {NAMEN_DATEI}")
    # #325: Kommentarzeilen mit festem Personennamen stehen in der Kopie mit Rollenwort
    # (öffentliches Repo). Nur genau diese Zeilen dürfen abweichen — und müssen dann namensfrei sein.
    soll = original.stdout.decode("utf-8").splitlines(keepends=True)
    ist = kopie.decode("utf-8").splitlines(keepends=True)
    assert len(ist) == len(soll), f"tests/hilfen/push_sperre_1442/{name} weicht vom Projekt ab — neu kopieren"
    for nr, (s, i) in enumerate(zip(soll, ist, strict=True), 1):
        if s == i:
            continue
        # Eng: Text vor und nach dem Namen der Vorlagenzeile muss in der Kopie stehen; nur der Name wird ersetzt.
        treffer = NAME.search(s)
        anonym = bool(
            treffer
            and s.lstrip().startswith("#")
            and not NAME.search(i)
            and i.startswith(s[: treffer.start()])
            and i.endswith(s[treffer.end() :])
        )
        assert anonym, (
            f"tests/hilfen/push_sperre_1442/{name}:{nr} weicht vom Projekt ab — neu kopieren "
            "(nur Kommentarzeilen mit Personennamen dürfen per Rollenwort abweichen)"
        )
