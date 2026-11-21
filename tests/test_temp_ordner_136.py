"""#136: Temp-Ordner auf der Platte statt RAM-Platte /tmp, Hausmeister räumt Temp-Reste.

Alle Ordner liegen in ``tmp_path`` (nachgebaute Wurzel); echte ``/tmp``/``~/wt`` nie.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from to_spawn import hausmeister, temp_ordner
from to_spawn.pruef import ausfuehren

SKILL = Path(__file__).resolve().parents[1]
ALT = time.time() - 3 * 24 * 3600


def test_pruef_lauf_hat_tmpdir_auf_konfig_ordner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ziel = tmp_path / "platte" / "tmp-bau"
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(ziel))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "ram"))
    code = "import os, tempfile; print(os.environ['TMPDIR']); print(tempfile.gettempdir())"
    lauf = ausfuehren.lauf([sys.executable, "-c", code], tmp_path)
    assert lauf.code == 0, lauf.ausgabe
    assert lauf.ausgabe.split() == [str(ziel), str(ziel)]
    assert ziel.is_dir()


def test_temp_ordner_aus_maschinen_liste_und_rueckfall(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TO_SPAWN_TEMP_ORDNER")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    maschine = tmp_path / "maschine.json"
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    assert temp_ordner.pfad() == tmp_path / "tmp-bau"  # Rückfall ohne Datei
    maschine.write_text(json.dumps({"temp_ordner": "~/platte/temp"}), encoding="utf-8")
    assert temp_ordner.umgebung() == {k: str(tmp_path / "platte" / "temp") for k in ("TMPDIR", "TEMP", "TMP")}


def test_bau_session_bekommt_temp_umgebung() -> None:
    # bau.py setzt die Umgebung der Claude-Session an einer Stelle; ganzer Start ist hier zu teuer.
    text = (SKILL / "skripte" / "bau.py").read_text(encoding="utf-8")
    assert "os.environ.update(temp_ordner.umgebung())" in text


def _rest(ordner: Path, *, alt: bool = True) -> Path:
    ordner.mkdir(parents=True)
    (ordner / "datei.txt").write_text("x", encoding="utf-8")
    if alt:
        for p in (ordner / "datei.txt", ordner):
            os.utime(p, (ALT, ALT))
    return ordner


@pytest.mark.skipif(sys.platform == "win32", reason="Prozess-Probe über /proc (Linux)")
def test_hausmeister_raeumt_temp_reste_geschlossener_tickets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    platte = tmp_path / "tmp-bau"
    altbestand = tmp_path / "ram"
    monkeypatch.setenv("TO_SPAWN_TEMP_ORDNER", str(platte))
    monkeypatch.setenv(
        "TO_SPAWN_TEMP_RESTE", os.pathsep.join([str(altbestand / "*"), str(altbestand / "claude-*" / "*")])
    )
    zu = {1335, 1499, 136, 1500, 1600}
    monkeypatch.setattr(hausmeister, "_ticket_daten", lambda nr, slug=None: {"state": "CLOSED" if nr in zu else "OPEN"})
    weg = [_rest(altbestand / "t1335"), _rest(altbestand / "claude-1000" / "mut1499"), _rest(platte / "tmp_136")]
    os.utime(altbestand / "claude-1000", (ALT, ALT))
    bleibt = [
        altbestand / "claude-1000",  # Such-Eltern, nie selbst ein Rest
        _rest(altbestand / "t1400"),  # Ticket offen
        _rest(altbestand / "t1500", alt=False),  # frisch
        _rest(altbestand / "tmpk3j2x9"),  # kein Ticket-Name
        _rest(altbestand / "t1600"),  # Prozess darin
    ]
    (altbestand / "t1700").symlink_to(altbestand / "t1600")  # Symlink nie

    hausmeister.aufraeumen_alles(loeschen=False)
    assert all(p.exists() for p in weg + bleibt), "Probelauf löscht nie"

    prozess = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=altbestand / "t1600")
    try:
        hausmeister.aufraeumen_alles(loeschen=True)
    finally:
        prozess.kill()
        prozess.wait()
    assert [p for p in weg if p.exists()] == []
    assert [p for p in bleibt if not p.exists()] == []
    assert (altbestand / "t1700").is_symlink()
    log = Path(os.environ["TO_SPAWN_HAUSMEISTER_LOG"]).read_text(encoding="utf-8")
    assert {str(p) for p in weg} <= {json.loads(z)["ordner"] for z in log.splitlines()}


def test_schutz_hook_blockt_hausmeister_weg_nicht(tmp_path: Path) -> None:
    hook = Path.home() / ".claude" / "hooks" / "block-dangerous.sh"
    bash = shutil.which("bash")
    if not hook.is_file() or bash is None:
        pytest.skip("Schutz-Hook block-dangerous.sh nicht installiert")
    befehl = "python3 ~/.claude/skills/to-spawn/to_spawn.py aufraeumen --alles --loeschen"
    erg = subprocess.run(
        [bash, str(hook)],
        input=json.dumps({"tool_input": {"command": befehl}}),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert erg.returncode == 0, erg.stdout + erg.stderr


#: Begründete Ausnahmen für ``/tmp`` im Skill-Code: Datei → Grund.
TMP_AUSNAHMEN = {
    "to_spawn/vorgaben.py": "Vorgabe-Muster des Temp-Altbestands (#136), überschreibbar per Maschinen-Liste",
    "to_spawn/nest.py": "Sandbox-Schreibrecht für /tmp (Werkzeuge schreiben dort), kein Arbeitsordner",
    "to_spawn/aufpasser.py": "Kommentar zur Gate-Erkennung (#1411)",
    "to_spawn/probesitz.py": "Kommentar: warum die Ordner nicht unter /tmp liegen",
    "to_spawn/hausmeister.py": "Docstring: /tmp/* fällt als Wurzel weg",
    "nest/skill_schutz.py": "Docstring-Beispiel einer Umleitung",
    "nest/nest_server.sh": "Einrichtung frischer Server (Installer-Download, tmux-Socket des Systems)",
}


def test_kein_fest_verdrahtetes_tmp_im_skill_code() -> None:
    funde: list[str] = []
    for ordner in ("to_spawn", "skripte", "repo-scripts", "leitstand", "aliase", "nest", "kette"):
        for datei in sorted((SKILL / ordner).rglob("*")):
            if datei.suffix not in {".py", ".sh", ".ps1"} or not datei.is_file():
                continue
            name = datei.relative_to(SKILL).as_posix()
            if name not in TMP_AUSNAHMEN and "/tmp" in datei.read_text(encoding="utf-8", errors="replace"):
                funde.append(name)
    assert funde == [], f"/tmp fest verdrahtet (temp_ordner.pfad() nutzen oder Ausnahme begründen): {funde}"
