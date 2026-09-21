"""Aufpasser #236: Cron-Skript, das je tmux-Sitzung ``spec-<S>`` die Fenster
``bau <N>`` / ``wache <S>`` beaufsichtigt — fehlende starten, geschlossene
Tickets schließen, stille Fenster über die Sicherheitskette wecken.

Weg-Tests mit ECHTEM tmux (eigener Socket ``aufpasser-test-<pid>``, nie der
Standard-Server), echtem git (temp Bare-Origin + Worktree) und dem Dateisystem.
Gestellt sind nur ``gh`` (externer Dienst, ``tests/hilfen/gh_stub.py``) und
``claude`` (Schlaf-Attrappe ``tests/hilfen/fake_claude_schlaeft.sh``).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
HILFEN = SKILL / "tests" / "hilfen"
SKRIPTE = SKILL / "skripte"
sys.path.insert(0, str(SKILL))

from to_spawn import aufpasser
from to_spawn.waechter_lauf import transkript_ordner

SOCKET = f"aufpasser-test-{os.getpid()}"
#: Deploy-Muster, das nie trifft — sonst würde ein echter Deploy/Gate auf dem
#: Bau-Server (oder pytest selbst) alle Tests still abschalten.
MUSTER_LEER = f"aufpasser-probe-niemals-{os.getpid()}"
SPEC = "9001"
SITZUNG = f"spec-{SPEC}"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

GH_WRAPPER = """#!/bin/sh
exec {py} {stub} "$@"
"""

BAU_PROTOKOLL = """#!/bin/bash
# Protokoll-Skript statt bau.py: schreibt seine Argumente weg und schläft.
echo "$@" >> "{datei}"
exec sleep 3600
"""

BAU_CLAUDE = """#!/bin/bash
# Start-Attrappe wie bau.py: Argumente wegschreiben, dann die Claude-Attrappe starten.
echo "$@" >> "{datei}"
exec "{claude}" "$@"
"""

CRONTAB_FAKE = """#!/bin/bash
# Gefälschtes crontab: -l liest die Datei, sonst wird stdin hineingeschrieben.
if [ "$1" = "-l" ]; then
  [ -f "$CRONTAB_DATEI" ] && cat "$CRONTAB_DATEI"
  exit 0
fi
cat > "$CRONTAB_DATEI"
"""


def tmux(*args: str, check: bool = True) -> str:
    """tmux NUR auf dem eigenen Test-Socket."""
    ergebnis = subprocess.run(
        ["tmux", "-L", SOCKET, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if check and ergebnis.returncode != 0:
        raise AssertionError(f"tmux {' '.join(args)}: {ergebnis.stderr}")
    return ergebnis.stdout


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout


def fenster_namen() -> list[str]:
    return tmux("list-windows", "-t", f"={SITZUNG}", "-F", "#{window_name}").split(
        "\n"
    )[:-1]


def fenster_ziel(name: str) -> str:
    for zeile in tmux(
        "list-windows", "-t", f"={SITZUNG}", "-F", "#{window_index}\t#{window_name}"
    ).splitlines():
        idx, nm = zeile.split("\t")
        if nm == name:
            return f"={SITZUNG}:{idx}"
    raise AssertionError(f"Fenster {name!r} fehlt: {fenster_namen()}")


def pane_text(name: str) -> str:
    return tmux("capture-pane", "-p", "-t", fenster_ziel(name))


@dataclass
class Welt:
    tmp: Path
    heim: Path
    bin: Path
    repo: Path
    origin: Path
    zustand: Path
    protokoll_gh: Path
    protokoll_bau: Path

    @property
    def vorlage(self) -> str:
        return f"{self.bin}/bau_protokoll.sh {{n}}{{resume}}"

    def kommentare(self) -> list[str]:
        if not self.protokoll_gh.is_file():
            return []
        return [
            z
            for z in self.protokoll_gh.read_text(encoding="utf-8").splitlines()
            if z.startswith("issue comment")
        ]

    def stand(self) -> dict:
        datei = self.zustand / "stand.json"
        return json.loads(datei.read_text(encoding="utf-8")) if datei.is_file() else {}

    def stand_setzen(self, name: str, **felder: object) -> None:
        daten = self.stand()
        fenster = daten.setdefault("fenster", {})
        eintrag = fenster.setdefault(f"{SITZUNG}/{name}", {})
        eintrag.update(felder)
        self.zustand.mkdir(parents=True, exist_ok=True)
        (self.zustand / "stand.json").write_text(json.dumps(daten), encoding="utf-8")

    def eintrag(self, name: str) -> dict:
        return self.stand().get("fenster", {}).get(f"{SITZUNG}/{name}", {})

    def log(self) -> str:
        datei = self.zustand / "aufpasser.log"
        return datei.read_text(encoding="utf-8") if datei.is_file() else ""

    def worktree(self, ticket: int, schmutzig: bool = True) -> Path:
        wt = self.tmp / "wt" / f"wt-{ticket}"
        git(self.repo, "worktree", "add", "-q", "-b", f"arbeit-{ticket}", str(wt))
        if schmutzig:
            (wt / "neu.txt").write_text("ungesicherte Arbeit\n", encoding="utf-8")
        return wt

    def fenster_fake_claude(self, ticket: int, sid: str) -> None:
        tmux(
            "new-window",
            "-d",
            "-t",
            f"={SITZUNG}",
            "-n",
            f"bau {ticket}",
            "-c",
            str(self.repo),
            f"{self.bin}/claude --session-id {sid} irgendwas",
        )
        time.sleep(0.5)

    def fenster_still(self, ticket: int, befehl: str = "cat") -> None:
        tmux(
            "new-window",
            "-d",
            "-t",
            f"={SITZUNG}",
            "-n",
            f"bau {ticket}",
            "-c",
            str(self.repo),
            befehl,
        )
        time.sleep(0.5)

    def vorlage_mit_claude(self) -> str:
        """Start-Attrappe, die wie bau.py die Claude-Attrappe startet (Fix F5:
        der Nachweis nach dem Fortsetzen braucht eine echte Session-JSON)."""
        datei = self.bin / "bau_claude.sh"
        datei.write_text(
            BAU_CLAUDE.format(datei=self.protokoll_bau, claude=self.bin / "claude"),
            encoding="utf-8",
        )
        datei.chmod(0o755)
        return f"{datei} {{n}}{{resume}}"

    def hash_von(self, name: str) -> str:
        return hashlib.sha1(pane_text(name).encode()).hexdigest()

    def lauf(self, *extra: str, hang_min: int = 90) -> int:
        # geändert wegen Fix F15: hang_min nie unter 61, Stille kommt aus ``seit``.
        return aufpasser.main(
            [
                "--tmux-socket",
                SOCKET,
                "--zustand",
                str(self.zustand),
                "--deploy-muster",
                MUSTER_LEER,
                "--bau-vorlage",
                self.vorlage,
                "--hang-min",
                str(hang_min),
                *extra,
            ]
        )


@pytest.fixture()
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Welt]:
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.setenv("HOME", str(heim))
    binaer = tmp_path / "bin"
    binaer.mkdir()
    (binaer / "gh").write_text(
        GH_WRAPPER.format(py=sys.executable, stub=HILFEN / "gh_stub.py"),
        encoding="utf-8",
    )
    (binaer / "gh").chmod(0o755)
    shutil.copy2(HILFEN / "fake_claude_schlaeft.sh", binaer / "claude")
    (binaer / "claude").chmod(0o755)
    protokoll_bau = tmp_path / "bau_argv.txt"
    (binaer / "bau_protokoll.sh").write_text(
        BAU_PROTOKOLL.format(datei=protokoll_bau), encoding="utf-8"
    )
    (binaer / "bau_protokoll.sh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{binaer}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("TMUX", raising=False)
    protokoll_gh = tmp_path / "gh_protokoll.txt"
    monkeypatch.setenv("GH_STUB_PROTOKOLL", str(protokoll_gh))
    # Vorgabe: nur 9002 ist Unter-Ticket; Tests mit 9003 setzen es selbst.
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002")
    monkeypatch.delenv("GH_STUB_ZU", raising=False)
    monkeypatch.setenv("BAU_WT_DIR", str(tmp_path / "wt"))
    (tmp_path / "wt").mkdir()

    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "master")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text("{}", encoding="utf-8")
    (repo / "README.md").write_text("Probe\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Anfang")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "master")

    tmux(
        "new-session",
        "-d",
        "-s",
        SITZUNG,
        "-n",
        f"wache {SPEC}",
        "-c",
        str(repo),
        "sleep 3600",
    )
    try:
        yield Welt(
            tmp=tmp_path,
            heim=heim,
            bin=binaer,
            repo=repo,
            origin=origin,
            zustand=tmp_path / "zustand",
            protokoll_gh=protokoll_gh,
            protokoll_bau=protokoll_bau,
        )
    finally:
        _pane_prozesse_beenden()
        tmux("kill-server", check=False)
        Path(f"/tmp/tmux-{os.getuid()}/{SOCKET}").unlink(missing_ok=True)


def _pane_prozesse_beenden() -> None:
    """Prozessbäume aller Panes beenden — ``kill-server`` lässt ``sleep``-Enkel sonst leben."""
    for zeile in tmux("list-panes", "-a", "-F", "#{pane_pid}", check=False).split():
        for pid in reversed(aufpasser.prozess_baum(int(zeile))):
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass


def _sha7_von_origin(welt: Welt, ticket: int) -> str:
    return git(welt.origin, "rev-parse", f"refs/heads/sicherung/{ticket}").strip()[:7]


def _origin_hat_branch(welt: Welt, ticket: int) -> bool:
    return bool(
        subprocess.run(
            ["git", "rev-parse", "--verify", "-q", f"refs/heads/sicherung/{ticket}"],
            cwd=str(welt.origin),
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


# --- 1. fehlendes Fenster wird gestartet -----------------------------------


def test_fehlendes_fenster_wird_gestartet(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002|9003")
    assert fenster_namen() == [f"wache {SPEC}"]
    assert welt.lauf(hang_min=90) == 0
    time.sleep(1)
    assert sorted(fenster_namen()) == ["bau 9002", "bau 9003", f"wache {SPEC}"]
    argv = welt.protokoll_bau.read_text(encoding="utf-8").splitlines()
    assert sorted(argv) == ["9002", "9003"]
    kommentare = welt.kommentare()
    assert any("#9002 hatte keine Session, gestartet." in k for k in kommentare), (
        kommentare
    )
    assert any("#9003 hatte keine Session, gestartet." in k for k in kommentare), (
        kommentare
    )
    assert "gestartet" in welt.log()


# --- 2. stilles Fenster wird angestupst ------------------------------------


def test_stilles_fenster_wird_angestupst(welt: Welt) -> None:
    welt.fenster_still(9002, "cat")
    vorher = pane_text("bau 9002")
    welt.stand_setzen(
        "bau 9002",
        hash=hashlib.sha1(vorher.encode()).hexdigest(),
        seit=time.time() - 100 * 60,
        stufe=0,
    )
    assert welt.lauf(hang_min=90) == 0
    time.sleep(1)
    nachher = pane_text("bau 9002")
    assert "Aufpasser: Du stehst seit 100 min still" in nachher, nachher
    assert welt.eintrag("bau 9002")["stufe"] == 1
    assert any(
        "„bau 9002“ stand 100 min still, angestupst." in k for k in welt.kommentare()
    )


def test_frisches_fenster_gilt_nicht_als_still(welt: Welt) -> None:
    """Hash unverändert < hang_min ⇒ nichts anfassen (Zeitlogik, nicht nur Stufe)."""
    welt.fenster_still(9002, "cat")
    vorher = pane_text("bau 9002")
    assert welt.lauf(hang_min=90) == 0
    time.sleep(0.5)
    assert pane_text("bau 9002") == vorher
    assert welt.eintrag("bau 9002")["stufe"] == 0
    assert welt.kommentare() == []


# --- 3. arbeitendes Fenster bleibt unberührt -------------------------------


def test_arbeitendes_fenster_bleibt_unberuehrt(welt: Welt) -> None:
    welt.fenster_still(9002, "printf 'esc to interrupt\\n'; sleep 3600")
    vorher = pane_text("bau 9002")
    assert "esc to interrupt" in vorher
    welt.stand_setzen(
        "bau 9002",
        hash=hashlib.sha1(vorher.encode()).hexdigest(),
        seit=time.time() - 100 * 60,
        stufe=0,
    )
    assert welt.lauf(hang_min=90) == 0
    time.sleep(0.5)
    assert pane_text("bau 9002") == vorher
    assert welt.eintrag("bau 9002").get("stufe", 0) == 0
    assert welt.kommentare() == []


# --- 4. Deploy-Prozess ⇒ nichts angefasst ----------------------------------


def test_deploy_prozess_nichts_angefasst(welt: Welt) -> None:
    skript = welt.tmp / "safe_deploy_vps.sh"
    skript.write_text("#!/bin/bash\nsleep 3600\n", encoding="utf-8")
    skript.chmod(0o755)
    deploy = subprocess.Popen([str(skript)], start_new_session=True)
    try:
        welt.fenster_still(9002, "cat")
        vorher = pane_text("bau 9002")
        # geändert wegen Fix F15: Stille über ``seit``+Hash statt ``--hang-min 0``.
        welt.stand_setzen(
            "bau 9002",
            seit=time.time() - 100 * 60,
            hash=hashlib.sha1(vorher.encode()).hexdigest(),
        )
        code = aufpasser.main(
            [
                "--tmux-socket",
                SOCKET,
                "--zustand",
                str(welt.zustand),
                "--deploy-muster",
                r"safe_deploy_vps\.sh",
                "--bau-vorlage",
                welt.vorlage,
            ]
        )
        assert code == 0
        time.sleep(0.5)
        assert sorted(fenster_namen()) == ["bau 9002", f"wache {SPEC}"]
        assert pane_text("bau 9002") == vorher
        assert not welt.protokoll_bau.exists()
        assert welt.kommentare() == []
        assert "Deploy/Gate läuft" in welt.log()
        # Zustand trotzdem fortgeschrieben (Hash bekannt).
        assert welt.eintrag("bau 9002").get("hash")
    finally:
        os.killpg(deploy.pid, 15)  # Skript samt ``sleep``-Kind
        deploy.wait()


def test_deploy_wache_zaehlt_nur_programm_nicht_prompt(tmp_path: Path) -> None:
    """Der Prompt einer Claude-Session nennt ``safe_deploy_vps.sh`` — das ist kein Deploy."""
    binaer = tmp_path / "bin"
    binaer.mkdir()
    shutil.copy2(HILFEN / "fake_claude_schlaeft.sh", binaer / "claude")
    skript = tmp_path / "safe_deploy_vps.sh"
    skript.write_text("#!/bin/bash\nsleep 3600\n", encoding="utf-8")
    skript.chmod(0o755)
    e = aufpasser.Einstellungen(
        zustand=tmp_path / "zustand", deploy_muster=r"safe_deploy_vps\.sh|pytest"
    )
    claude = subprocess.Popen(
        [
            str(binaer / "claude"),
            "--model",
            "x",
            "Ticket: bash scripts/safe_deploy_vps.sh",
        ],
        start_new_session=True,
    )
    try:
        time.sleep(0.5)
        # pytest selbst läuft gerade — auch das zählt hier nicht: argv[:3] von pytest ist
        # ``python -m pytest`` und trifft; deshalb gegen ein eigenes Muster prüfen.
        e.deploy_muster = r"safe_deploy_vps\.sh"
        assert aufpasser.Aufpasser(e).deploy_laeuft() == []
        deploy = subprocess.Popen([str(skript)], start_new_session=True)
        try:
            time.sleep(0.5)
            treffer = aufpasser.Aufpasser(e).deploy_laeuft()
            assert treffer and "safe_deploy_vps.sh" in treffer[0], treffer
        finally:
            os.killpg(deploy.pid, 15)
            deploy.wait()
    finally:
        os.killpg(claude.pid, 15)
        claude.wait()


# --- 5. Sicherung vor Resume, gleiche Gesprächs-ID ---------------------------


def test_sicherung_vor_resume_gleiche_id(welt: Welt) -> None:
    """geändert wegen Fix F5/F15: Stille über ``seit``+Hash statt ``--hang-min 0``;
    Start-Attrappe startet die Claude-Attrappe (Nachweis derselben ID)."""
    wt = welt.worktree(9002, schmutzig=True)
    kopf_vorher = git(wt, "rev-parse", "HEAD").strip()
    sid = str(uuid.uuid4())
    welt.fenster_fake_claude(9002, sid)
    welt.stand_setzen(
        "bau 9002",
        stufe=1,
        seit=time.time() - 100 * 60,
        eingriff=time.time() - 100 * 60,
        hash=welt.hash_von("bau 9002"),
    )
    assert welt.lauf("--bau-vorlage", welt.vorlage_mit_claude()) == 0
    time.sleep(1)
    # (a) Origin hat sicherung/9002 mit neu.txt
    assert _origin_hat_branch(welt, 9002)
    baum = git(welt.origin, "ls-tree", "-r", "--name-only", "refs/heads/sicherung/9002")
    assert "neu.txt" in baum.split()
    # (b) Worktree unverändert schmutzig, gleicher Branch, kein stash/reset
    assert "?? neu.txt" in git(wt, "status", "--porcelain")
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "arbeit-9002"
    assert git(wt, "rev-parse", "HEAD").strip() == kopf_vorher
    assert (wt / "neu.txt").read_text(encoding="utf-8") == "ungesicherte Arbeit\n"
    assert git(wt, "stash", "list") == ""
    # (c) Fenster neu bestückt (respawn, Fix F4) mit --resume <sid>
    assert fenster_namen().count("bau 9002") == 1
    argv = welt.protokoll_bau.read_text(encoding="utf-8").splitlines()
    assert argv == [f"9002 --resume {sid}"]
    # (d) Stufe 2
    assert welt.eintrag("bau 9002")["stufe"] == 2
    # (e) Kommentar nennt den Sicherungs-Branch und die ID
    sha7 = _sha7_von_origin(welt, 9002)
    treffer = [k for k in welt.kommentare() if "sicherung/9002" in k]
    assert treffer, welt.kommentare()
    assert sha7 in treffer[0] and sid[:8] in treffer[0]


# --- 6. sauberer Worktree ⇒ nichts zu sichern, trotzdem Resume --------------


def test_sauberer_worktree_nichts_zu_sichern_trotzdem_resume(welt: Welt) -> None:
    """geändert wegen Fix F5/F15 (siehe Test 5)."""
    welt.worktree(9002, schmutzig=False)
    sid = str(uuid.uuid4())
    welt.fenster_fake_claude(9002, sid)
    welt.stand_setzen(
        "bau 9002",
        stufe=1,
        seit=time.time() - 100 * 60,
        eingriff=time.time() - 100 * 60,
        hash=welt.hash_von("bau 9002"),
    )
    assert welt.lauf("--bau-vorlage", welt.vorlage_mit_claude()) == 0
    time.sleep(1)
    assert not _origin_hat_branch(welt, 9002)
    argv = welt.protokoll_bau.read_text(encoding="utf-8").splitlines()
    assert argv == [f"9002 --resume {sid}"]
    assert welt.eintrag("bau 9002")["stufe"] == 2
    treffer = [k for k in welt.kommentare() if "nichts zu sichern" in k]
    assert treffer and sid[:8] in treffer[0], welt.kommentare()


# --- 7. zweimal erfolglos ⇒ nichts mehr ändern -----------------------------


def test_zweimal_erfolglos_nichts_mehr_aendern(welt: Welt) -> None:
    """geändert wegen Fix F15: Stille über ``seit``+Hash statt ``--hang-min 0``."""
    welt.worktree(9002, schmutzig=True)
    sid = str(uuid.uuid4())
    welt.fenster_fake_claude(9002, sid)
    vorher = pane_text("bau 9002")
    welt.stand_setzen(
        "bau 9002",
        stufe=2,
        seit=time.time() - 100 * 60,
        eingriff=time.time() - 100 * 60,
        hash=hashlib.sha1(vorher.encode()).hexdigest(),
    )
    assert welt.lauf() == 0
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert sorted(fenster_namen()) == ["bau 9002", f"wache {SPEC}"]
    assert pane_text("bau 9002") == vorher
    assert not welt.protokoll_bau.exists()
    assert not _origin_hat_branch(welt, 9002)
    treffer = [k for k in welt.kommentare() if "braucht David" in k and "bau 9002" in k]
    assert len(treffer) == 1, welt.kommentare()
    assert "„bau 9002“ reagiert nach Anstupsen und Fortsetzen nicht" in treffer[0]
    assert welt.eintrag("bau 9002")["stufe"] == 3


# --- 8. Rücknahme: kein Kill ohne Sicherung --------------------------------


def test_kill_window_nur_in_fenster_schliessen() -> None:
    quelle = (SKILL / "to_spawn" / "aufpasser.py").read_text(encoding="utf-8")
    assert quelle.count("kill-window") == 1
    baum = ast.parse(quelle)
    funktionen = {
        knoten.name: knoten
        for knoten in ast.walk(baum)
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_fenster_schliessen" in funktionen
    konstanten = [
        k.value
        for k in ast.walk(funktionen["_fenster_schliessen"])
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    ]
    assert any("kill-window" in k for k in konstanten)
    # Freigabe(...) wird nur in den zwei Freigabe-Fabriken gebaut.
    erzeuger: set[str] = set()
    for name, fn in funktionen.items():
        for aufruf in ast.walk(fn):
            if (
                isinstance(aufruf, ast.Call)
                and isinstance(aufruf.func, ast.Name)
                and aufruf.func.id == "Freigabe"
            ):
                erzeuger.add(name)
    assert erzeuger == {"freigabe_ticket_zu", "freigabe_gesichert"}, erzeuger
    assert quelle.count("Freigabe(") == 2


def test_push_scheitert_kein_kill(welt: Welt) -> None:
    welt.worktree(9002, schmutzig=True)
    git(welt.repo, "remote", "set-url", "origin", str(welt.tmp / "gibt-es-nicht.git"))
    sid = str(uuid.uuid4())
    welt.fenster_fake_claude(9002, sid)
    # geändert wegen Fix F15: Stille über ``seit``+Hash statt ``--hang-min 0``.
    welt.stand_setzen(
        "bau 9002",
        stufe=1,
        seit=time.time() - 100 * 60,
        eingriff=time.time() - 100 * 60,
        hash=welt.hash_von("bau 9002"),
    )
    assert welt.lauf() == 0
    time.sleep(0.5)
    assert fenster_namen().count("bau 9002") == 1
    assert not welt.protokoll_bau.exists()
    treffer = [
        k for k in welt.kommentare() if "Sicherung fehlgeschlagen — braucht David" in k
    ]
    assert len(treffer) == 1, welt.kommentare()
    assert welt.eintrag("bau 9002")["stufe"] == 3
    # Das Fenster ist noch das alte: der Fake-Claude mit derselben ID läuft darin.
    assert sid in aufpasser.prozess_argv_text(welt_pane_pid("bau 9002"))


def welt_pane_pid(name: str) -> int:
    return int(
        tmux("display-message", "-p", "-t", fenster_ziel(name), "#{pane_pid}").strip()
    )


# --- 9. geschlossenes Ticket ⇒ Fenster zu ---------------------------------


def test_geschlossenes_ticket_fenster_zu(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9002|9003")
    monkeypatch.setenv("GH_STUB_ZU", "9003")
    welt.worktree(9003, schmutzig=True)
    welt.fenster_still(9003, "sleep 3600")
    welt.fenster_still(9002, "sleep 3600")
    # geändert wegen Fix F11: Schließen erst nach 15 min unverändertem Text.
    welt.stand_setzen(
        "bau 9003", seit=time.time() - 20 * 60, hash=welt.hash_von("bau 9003")
    )
    assert welt.lauf(hang_min=90) == 0
    time.sleep(0.5)
    assert sorted(fenster_namen()) == ["bau 9002", f"wache {SPEC}"]
    assert any("„bau 9003“ geschlossen (Ticket zu)." in k for k in welt.kommentare())
    assert _origin_hat_branch(welt, 9003)
    baum = git(welt.origin, "ls-tree", "-r", "--name-only", "refs/heads/sicherung/9003")
    assert "neu.txt" in baum.split()
    assert f"{SITZUNG}/bau 9003" not in welt.stand().get("fenster", {})


def test_geschlossenes_ticket_arbeitend_bleibt(
    welt: Welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_STUB_UNTER", f"{SPEC}:9003")
    monkeypatch.setenv("GH_STUB_ZU", "9003")
    welt.fenster_still(9003, "printf 'esc to interrupt\\n'; sleep 3600")
    assert welt.lauf(hang_min=90) == 0
    time.sleep(0.5)
    assert "bau 9003" in fenster_namen()
    assert welt.kommentare() == []


# --- 10. Cron-Einrichtung -------------------------------------------------


def test_cron_einrichten_ersetzt_alte_zeile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    binaer = tmp_path / "bin"
    binaer.mkdir()
    (binaer / "crontab").write_text(CRONTAB_FAKE, encoding="utf-8")
    (binaer / "crontab").chmod(0o755)
    monkeypatch.setenv("PATH", f"{binaer}{os.pathsep}{os.environ['PATH']}")
    datei = tmp_path / "crontab.txt"
    monkeypatch.setenv("CRONTAB_DATEI", str(datei))
    datei.write_text(
        'MAILTO=""\n'
        "0 3 * * * /usr/bin/backup.sh\n"
        "*/15 * * * * /usr/bin/python3 /home/x/aufpasser/aufpasser.py >> /home/x/aufpasser/cron.out 2>&1  # to-spawn aufpasser\n",
        encoding="utf-8",
    )
    zustand = tmp_path / "zustand"
    assert aufpasser.main(["--zustand", str(zustand), "--cron-einrichten"]) == 0
    ausgabe = capsys.readouterr().out.strip().splitlines()
    assert len(ausgabe) == 1, ausgabe
    zeilen = datei.read_text(encoding="utf-8").splitlines()
    aufpasser_zeilen = [z for z in zeilen if "# to-spawn aufpasser" in z]
    assert len(aufpasser_zeilen) == 1
    assert "/home/x/aufpasser" not in aufpasser_zeilen[0]
    assert str(SKRIPTE / "aufpasser.py") in aufpasser_zeilen[0]
    assert aufpasser_zeilen[0].startswith("*/15 * * * * ")
    assert f"{zustand}/cron.out" in aufpasser_zeilen[0]
    assert "0 3 * * * /usr/bin/backup.sh" in zeilen
    assert 'MAILTO=""' in zeilen
    sicherungen = list(zustand.glob("crontab.vorher.*"))
    assert len(sicherungen) == 1
    assert "/home/x/aufpasser" in sicherungen[0].read_text(encoding="utf-8")
    # zweiter Aufruf ändert nichts
    stand = datei.read_text(encoding="utf-8")
    assert aufpasser.main(["--zustand", str(zustand), "--cron-einrichten"]) == 0
    assert datei.read_text(encoding="utf-8") == stand


def test_cron_einrichten_leere_crontab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binaer = tmp_path / "bin"
    binaer.mkdir()
    (binaer / "crontab").write_text(CRONTAB_FAKE, encoding="utf-8")
    (binaer / "crontab").chmod(0o755)
    monkeypatch.setenv("PATH", f"{binaer}{os.pathsep}{os.environ['PATH']}")
    datei = tmp_path / "crontab.txt"
    monkeypatch.setenv("CRONTAB_DATEI", str(datei))
    zustand = tmp_path / "zustand"
    assert aufpasser.main(["--zustand", str(zustand), "--cron-einrichten"]) == 0
    zeilen = datei.read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 1 and zeilen[0].endswith("# to-spawn aufpasser")


# --- Starter + Trockenlauf ------------------------------------------------


def test_trockenlauf_ueber_starter_und_cli(welt: Welt) -> None:
    for befehl in (
        [sys.executable, str(SKRIPTE / "aufpasser.py")],
        [sys.executable, str(SKILL / "to_spawn.py"), "aufpasser"],
    ):
        ergebnis = subprocess.run(
            [
                *befehl,
                "--trocken",
                "--tmux-socket",
                SOCKET,
                "--zustand",
                str(welt.zustand),
                "--deploy-muster",
                MUSTER_LEER,
                "--bau-vorlage",
                welt.vorlage,
            ],
            cwd=str(welt.repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=60,
        )
        assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
        assert (
            "[trocken]" in ergebnis.stdout
            and "#9002 hatte keine Session" in ergebnis.stdout
        )
    assert fenster_namen() == [f"wache {SPEC}"]
    assert welt.kommentare() == []
    assert not (welt.zustand / "stand.json").exists()


# --- 11. bau.py: --session-id / --resume ------------------------------------

FAKE_CLAUDE_ARGV = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
Path(os.environ["FAKE_ARGV"]).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
sys.exit(0)
"""


@pytest.fixture()
def repo_bau(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    arbeit = tmp_path / "repo"
    arbeit.mkdir()
    git(arbeit, "init", "-q")
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


def _bau(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    umgebung = {k: v for k, v in os.environ.items() if k != "TO_SPAWN_REPO"}
    umgebung.update(env or {})
    return subprocess.run(
        [sys.executable, str(SKRIPTE / "bau.py"), "901", *args],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def test_bau_dry_run_zeigt_session_id(repo_bau: Path) -> None:
    ergebnis = _bau(repo_bau, "--dry-run")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    treffer = re.search(r"--session-id (\S+)", ergebnis.stdout)
    assert treffer, ergebnis.stdout
    assert UUID_RE.fullmatch(treffer.group(1)), treffer.group(1)


def test_bau_dry_run_resume_ohne_session_id(repo_bau: Path) -> None:
    ergebnis = _bau(repo_bau, "--dry-run", "--resume", "abc")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert "--resume abc" in ergebnis.stdout
    assert "--session-id" not in ergebnis.stdout


def test_bau_resume_wartet_nicht_auf_blocker(repo_bau: Path, tmp_path: Path) -> None:
    binaer = tmp_path / "bin"
    binaer.mkdir()
    (binaer / "claude").write_text(FAKE_CLAUDE_ARGV, encoding="utf-8")
    (binaer / "claude").chmod(0o755)
    (binaer / "gh").write_text(
        GH_WRAPPER.format(py=sys.executable, stub=HILFEN / "gh_stub.py"),
        encoding="utf-8",
    )
    (binaer / "gh").chmod(0o755)
    beweis = tmp_path / "argv.json"
    temp = tmp_path / "tmp"
    temp.mkdir()
    # geändert wegen Fix F5: ``--resume`` verlangt das Transkript unter
    # ``~/.claude/projects/<cwd-slug>/<id>.jsonl`` — Temp-HOME mit dieser Datei,
    # die Plugins (context-mode ist Pflicht) bleiben per Symlink die echten.
    heim = tmp_path / "heim"
    (heim / ".claude").mkdir(parents=True)
    echte_plugins = Path.home() / ".claude" / "plugins"
    if echte_plugins.exists():
        (heim / ".claude" / "plugins").symlink_to(echte_plugins)
    transkripte = transkript_ordner(repo_bau, heim)
    transkripte.mkdir(parents=True)
    (transkripte / "abc.jsonl").write_text("{}\n", encoding="utf-8")
    env = {
        "HOME": str(heim),
        "PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}",
        "FAKE_ARGV": str(beweis),
        "TMPDIR": str(temp),
        # Blocker 950 ist laut Stub „closed“, hat aber keinen Commit auf origin/master
        # → ohne --resume würde bau.py hier warten (Takt ≥ 60 s).
        "GH_STUB_BLOCKER": "901:950",
    }
    start = time.monotonic()
    ergebnis = _bau(repo_bau, "--resume", "abc", env=env)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert time.monotonic() - start < 50
    argv = json.loads(beweis.read_text(encoding="utf-8"))
    assert "--resume" in argv and argv[argv.index("--resume") + 1] == "abc"
    assert "--session-id" not in argv
    assert "Aufpasser: Weiter mit Ticket #901" in argv[-1]
    assert "nächste Prüfung" not in ergebnis.stdout + ergebnis.stderr
    sid_dateien = list(temp.rglob("session-id.txt"))
    assert sid_dateien and sid_dateien[0].read_text(encoding="utf-8").strip() == "abc"


def test_bau_neue_session_schreibt_id(repo_bau: Path, tmp_path: Path) -> None:
    binaer = tmp_path / "bin"
    binaer.mkdir()
    (binaer / "claude").write_text(FAKE_CLAUDE_ARGV, encoding="utf-8")
    (binaer / "claude").chmod(0o755)
    beweis = tmp_path / "argv.json"
    temp = tmp_path / "tmp"
    temp.mkdir()
    env = {
        "PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}",
        "FAKE_ARGV": str(beweis),
        "TMPDIR": str(temp),
    }
    ergebnis = _bau(repo_bau, "--sofort", env=env)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    argv = json.loads(beweis.read_text(encoding="utf-8"))
    sid = argv[argv.index("--session-id") + 1]
    assert UUID_RE.fullmatch(sid)
    sid_dateien = list(temp.rglob("session-id.txt"))
    assert sid_dateien and sid_dateien[0].read_text(encoding="utf-8").strip() == sid


# --- 12. Bausteine ---------------------------------------------------------


def test_arbeitet_marker() -> None:
    """Geändert wegen Live-Befund 21.09. (L1): „nächste Prüfung“ zählt nur noch als
    echte bau.py-Warte-Zeile, nicht als freie Prosa."""
    for marker in (
        "esc to interrupt",
        "background agent",
        "Ticket #9 wartet (#8 offen) — nächste Prüfung in 10 min [08:00]",
        "usage limit",
        "limit reset",
        "Do you want to proceed?",
        "Esc to cancel",
    ):
        assert aufpasser.arbeitet(f"…\n{marker}\n…")
    assert not aufpasser.arbeitet("nur Text\n\n")


def test_session_id_aus_argv() -> None:
    assert (
        aufpasser.session_id_aus_argv(["claude", "--session-id", "abc", "x"]) == "abc"
    )
    assert (
        aufpasser.session_id_aus_argv(["/usr/bin/claude", "--resume", "def"]) == "def"
    )
    assert (
        aufpasser.session_id_aus_argv(["python", "bau.py", "--resume", "def"]) is None
    )
    assert aufpasser.session_id_aus_argv(["claude", "prompt"]) is None


def test_keine_id_ohne_session_json_und_argv(tmp_path: Path) -> None:
    """geändert wegen Fix F1: der Transkript-Fallback (neueste ``.jsonl``) ist weg —
    er lieferte nachweislich die ID einer fremden Session (alle Bau-Fenster teilen
    sich das cwd). Ohne Session-JSON und ohne argv-ID gibt es keine ID."""
    binaer = tmp_path / "bin"
    binaer.mkdir()
    shutil.copy2(HILFEN / "fake_claude_schlaeft.sh", binaer / "claude")
    arbeit = tmp_path / "arbeit"
    arbeit.mkdir()
    heim = tmp_path / "heim"
    ordner = transkript_ordner(arbeit, heim)
    ordner.mkdir(parents=True)
    umgebung = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    umgebung.update(HOME=str(heim), FAKE_CLAUDE_OHNE_JSON="1")
    prozess = subprocess.Popen(
        [str(binaer / "claude"), "nur ein Prompt"], cwd=str(arbeit), env=umgebung
    )
    try:
        time.sleep(1.2)
        (ordner / f"{uuid.uuid4()}.jsonl").write_text("{}\n", encoding="utf-8")
        assert aufpasser.session_finden(prozess.pid, "@0", "%0", heim) is None
        assert not list((heim / ".claude").glob("sessions/*.json"))
    finally:
        for pid in reversed(aufpasser.prozess_baum(prozess.pid)):
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
        prozess.wait(timeout=10)
