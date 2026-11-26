"""#191 Teil 1: Ticket-Stand unbekannt vor einer Staffel-Runde, gh hängt, tmux-Start-Weg.

1. ``weiter_entscheidung``: unbekannt → begrenzt erneut fragen, dann laut Exit 2 (nie
   still Exit 0 wie „geschlossen“).
2. ``ticket_zustand.lesen`` (``gh.ticket_stand_timeout``) hat eine Zeitgrenze; hängt gh, endet ``bau.py`` laut statt stumm.
3. Prüf-Test „nur eine Start-Stelle“ erkennt auch ``tmux new-window … claude``.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
LAUF = SKILL / "tests" / "hilfen" / "bau_lauf_97.py"
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tests"))

from to_spawn import gh, startstelle  # noqa: E402
from test_startstelle_97 import CLAUDE_STARTER  # noqa: E402

# --- 1. Stand unbekannt vor der Staffel-Runde ------------------------------------------


def _zaehler(*antworten: bool | None) -> tuple[list[int], startstelle.TicketPruefer]:
    aufrufe: list[int] = []

    def pruefer(_t: str, _c: Path | None) -> bool | None:
        aufrufe.append(1)
        return antworten[min(len(aufrufe), len(antworten)) - 1]

    return aufrufe, pruefer


def test_unbekannt_wird_wiederholt_dann_exit_2() -> None:
    aufrufe, pruefer = _zaehler(None)
    e = startstelle.weiter_entscheidung("1", pruefer=pruefer, abstand=0)
    assert not e.erlaubt
    assert e.exit_code == startstelle.EXIT_UNKLAR
    assert len(aufrufe) == startstelle.STAND_VERSUCHE == 3


def test_kurzer_aussetzer_wird_ueberbrueckt() -> None:
    aufrufe, pruefer = _zaehler(None, True)
    assert startstelle.weiter_entscheidung("1", pruefer=pruefer, abstand=0).erlaubt
    assert len(aufrufe) == 2


def test_geschlossen_bleibt_exit_0_ohne_wiederholung() -> None:
    aufrufe, pruefer = _zaehler(False)
    e = startstelle.weiter_entscheidung("1", pruefer=pruefer, abstand=0)
    assert (e.erlaubt, e.exit_code, len(aufrufe)) == (False, 0, 1)


@pytest.fixture()
def umgebung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.delenv(startstelle.TEST_OFFEN_ENV, raising=False)
    monkeypatch.setenv("TO_SPAWN_REPO", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv(startstelle.SPERR_ORDNER_ENV, str(tmp_path / "sperren"))
    monkeypatch.setenv(startstelle.STAND_ABSTAND_ENV, "0")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    return dict(os.environ)


def _bau(env: dict[str, str], beleg: Path, ticket: str) -> tuple[int, str, float]:
    anfang = time.monotonic()
    fertig = subprocess.run(
        [sys.executable, str(LAUF), str(beleg), ticket, "--sofort"],
        env=env,
        cwd=str(beleg.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    return fertig.returncode, fertig.stdout + fertig.stderr, time.monotonic() - anfang


def test_bau_gh_aussetzer_vor_staffel_runde_endet_laut(umgebung: dict[str, str], tmp_path: Path) -> None:
    """Start-Tür sieht OPEN, danach fällt gh aus → Exit 2 + Meldung, kein stilles Exit 0."""
    zaehler = tmp_path / "gh_zaehler"
    stub = tmp_path / "gh_erst_offen.py"
    stub.write_text(
        "import pathlib, sys\n"
        f"z = pathlib.Path({str(zaehler)!r})\n"
        "n = int(z.read_text() or 0) + 1 if z.exists() else 1\n"
        "z.write_text(str(n))\n"
        "if n == 1:\n"
        '    print(\'{"state": "OPEN"}\')\n'
        "    sys.exit(0)\n"
        "print('HTTP 502', file=sys.stderr)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    umgebung["TO_SPAWN_GH_STUB"] = str(stub)
    beleg = tmp_path / "beleg.txt"
    code, ausgabe, _ = _bau(umgebung, beleg, "19101")
    assert not beleg.exists(), ausgabe
    assert code == startstelle.EXIT_UNKLAR, ausgabe
    assert "nicht bestimmbar" in ausgabe
    assert zaehler.read_text() == str(1 + startstelle.STAND_VERSUCHE)


# --- 2. gh hängt ----------------------------------------------------------------------


def test_gh_haengt_bau_endet_laut_und_gibt_sperre_frei(umgebung: dict[str, str], tmp_path: Path) -> None:
    stub = tmp_path / "gh_haengt.py"
    stub.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
    umgebung["TO_SPAWN_GH_STUB"] = str(stub)
    umgebung[gh.TICKET_STAND_TIMEOUT_ENV] = "1"
    umgebung[startstelle.SPERR_ORDNER_ENV] = str(tmp_path / "sperren")
    beleg = tmp_path / "beleg.txt"
    code, ausgabe, dauer = _bau(umgebung, beleg, "19102")
    assert code == startstelle.EXIT_UNKLAR, ausgabe
    assert "nicht bestimmbar" in ausgabe
    assert dauer < 60, dauer
    # Sperre frei: ein zweiter Lauf scheitert wieder am Stand, nicht an „läuft schon“.
    code, ausgabe, _ = _bau(umgebung, beleg, "19102")
    assert code == startstelle.EXIT_UNKLAR and "läuft schon" not in ausgabe, ausgabe


def test_ticket_stand_zeitgrenze_hat_einen_standardwert(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(gh.TICKET_STAND_TIMEOUT_ENV, raising=False)
    assert gh.ticket_stand_timeout() == gh.TICKET_STAND_TIMEOUT_S > 0
    monkeypatch.setenv(gh.TICKET_STAND_TIMEOUT_ENV, "kaputt")
    assert gh.ticket_stand_timeout() == gh.TICKET_STAND_TIMEOUT_S


# --- 3. tmux-Fenster mit claude --------------------------------------------------------

#: Fenster-Befehle von tmux, die einen Prozess starten oder eintippen.
_TMUX_START = {"new-window", "respawn-window", "split-window", "new-session", "respawn-pane", "send-keys"}
_CLAUDE_BEFEHL = __import__("re").compile(r"(?:^|[\s'\"`;&|(/\\])claude(?:\.exe|\.cmd)?(?=$|[\s'\"`;&|)])")


def _docstring_ids(baum: ast.AST) -> set[int]:
    ids: set[int] = set()
    for knoten in ast.walk(baum):
        if (
            isinstance(knoten, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and knoten.body
            and isinstance(knoten.body[0], ast.Expr)
            and isinstance(knoten.body[0].value, ast.Constant)
            and isinstance(knoten.body[0].value.value, str)
        ):
            ids.add(id(knoten.body[0].value))
    return ids


def _texte(knoten: ast.AST, docstrings: set[int]) -> list[str]:
    return [
        k.value
        for k in ast.walk(knoten)
        if isinstance(k, ast.Constant) and isinstance(k.value, str) and id(k) not in docstrings
    ]


def _hat_tmux_start(texte: list[str]) -> bool:
    for t in texte:
        if t in _TMUX_START or (_TMUX_START & set(t.split())):
            return True
    return False


def tmux_claude_fenster(quelle: str) -> list[int]:
    """Zeilen der Funktionen, die ein tmux-Fenster öffnen/bespielen UND darin ``claude`` starten.

    Je Funktion (auch verteilt über Listen/f-Strings, Modul-Konstanten und aufgerufene
    Hilfsfunktionen im selben Modul): ein tmux-Start-/send-keys-Befehl und ein Text, in dem
    ``claude`` als Befehl oder Pfad (z. B. ``~/.local/bin/claude``) steht.
    """
    baum = ast.parse(quelle)
    docstrings = _docstring_ids(baum)
    konstanten: dict[str, list[str]] = {}
    for anweisung in baum.body:
        if isinstance(anweisung, ast.Assign):
            werte = _texte(anweisung.value, docstrings)
            for ziel in anweisung.targets:
                if isinstance(ziel, ast.Name):
                    konstanten.setdefault(ziel.id, []).extend(werte)
        elif isinstance(anweisung, ast.AnnAssign) and isinstance(anweisung.target, ast.Name) and anweisung.value:
            konstanten.setdefault(anweisung.target.id, []).extend(_texte(anweisung.value, docstrings))

    funktionen: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funktionen.setdefault(knoten.name, []).append(knoten)

    def _erreichbare_texte(knoten: ast.AST, besucht: set[int]) -> list[str]:
        if id(knoten) in besucht:
            return []
        besucht.add(id(knoten))
        gefunden = _texte(knoten, docstrings)
        for teil in ast.walk(knoten):
            if isinstance(teil, ast.Name) and teil.id in konstanten:
                gefunden.extend(konstanten[teil.id])
            elif isinstance(teil, ast.Call):
                zielname = (
                    teil.func.id
                    if isinstance(teil.func, ast.Name)
                    else teil.func.attr
                    if isinstance(teil.func, ast.Attribute)
                    else None
                )
                if zielname and zielname in funktionen:
                    for helfer in funktionen[zielname]:
                        gefunden.extend(_erreichbare_texte(helfer, besucht))
        return gefunden

    treffer: list[int] = []
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            eigen = _texte(knoten, docstrings)
            if _hat_tmux_start(eigen) and any(_CLAUDE_BEFEHL.search(t) for t in _erreichbare_texte(knoten, set())):
                treffer.append(knoten.lineno)
    return treffer


ANGRIFF = """
def folgerunde(self, spec, ticket):
    tmux("new-window", "-t", f"=spec-{spec}", f"bash -lc \\"claude --ticket {ticket}\\"", start=True)
"""
ANGRIFF_LISTE = """
def folgerunde(spec):
    kopf = ["new-window", "-t", f"=spec-{spec}"]
    return [*_tmux_befehl(), *kopf, "bash -lc 'claude -p x'"]
"""
ERLAUBT = """
def fenster(self, befehl):
    self.tmux("new-window", "-d", "-n", "bau 7", f"bash -lc {befehl}")  # bau.py → Start-Stelle
    pfad = "~/.claude/skills/to-spawn/claude-skill"
"""


def test_tmux_claude_angriff_wird_erkannt() -> None:
    assert tmux_claude_fenster(ANGRIFF)
    assert tmux_claude_fenster(ANGRIFF_LISTE)
    assert not tmux_claude_fenster(ERLAUBT)


def test_kein_tmux_fenster_startet_claude_an_der_startstelle_vorbei() -> None:
    funde = {}
    for datei in [*SKILL.glob("to_spawn/**/*.py"), *SKILL.glob("skripte/**/*.py")]:
        name = datei.relative_to(SKILL).as_posix()
        zeilen = tmux_claude_fenster(datei.read_text(encoding="utf-8"))
        if zeilen and name not in CLAUDE_STARTER:
            funde[name] = zeilen
    assert not funde, f"tmux-Fenster mit claude an bau.py vorbei: {funde}"


# --- 4. Offene Befunde aus PR #202 + Rest aus #191 ------------------------------------

ANGRIFF_TILDE_PFAD = """
def folgerunde(spec, ticket):
    tmux("new-window", "-t", f"=spec-{spec}", f"bash -lc '~/.local/bin/claude --ticket {ticket}'")
"""
ANGRIFF_HOME_PFAD = """
def folgerunde(spec, ticket):
    tmux("new-window", "-t", f"=spec-{spec}", f"bash -lc '$HOME/.local/bin/claude -p {ticket}'")
"""
ANGRIFF_SEND_KEYS = """
def folgerunde(ziel):
    tmux("send-keys", "-t", ziel, "claude -p hallo", "Enter")
"""
ANGRIFF_KONSTANTE = """
CLAUDE_ZEILE = "bash -lc 'claude --model sonnet'"
def folgerunde(spec):
    tmux("new-window", "-t", f"=spec-{spec}", CLAUDE_ZEILE)
"""
ANGRIFF_HILFSFUNKTION = """
def _befehl(ticket):
    return f"~/.local/bin/claude --ticket {ticket}"
def folgerunde(spec, ticket):
    tmux("new-window", "-t", f"=spec-{spec}", _befehl(ticket))
"""
ERLAUBT_FREMDES_LOG = """
def _anderes_log():
    log.warning("claude %s lebt noch", 42)
def fenster(befehl):
    tmux("new-window", "-d", "-n", "bau 7", f"bash -lc {befehl}")
    pfad = "~/.claude/skills/to-spawn/claude-skill"
    modell = "claude-opus-5-5"
"""


@pytest.mark.parametrize(
    ("name", "quelle"),
    [
        ("tilde-pfad", ANGRIFF_TILDE_PFAD),
        ("home-pfad", ANGRIFF_HOME_PFAD),
        ("send-keys", ANGRIFF_SEND_KEYS),
        ("modul-konstante", ANGRIFF_KONSTANTE),
        ("hilfsfunktion", ANGRIFF_HILFSFUNKTION),
    ],
)
def test_tmux_claude_scanner_erkennt_umgehungen(name: str, quelle: str) -> None:
    """PR #202 Befund 1: Pfad-Aufrufe, send-keys, Modul-Konstanten und Hilfsfunktionen fallen auf."""
    assert tmux_claude_fenster(quelle), f"Umgehung nicht erkannt: {name}"
    assert not tmux_claude_fenster(ERLAUBT_FREMDES_LOG)


def test_weiter_erlaubt_delegiert_an_weiter_entscheidung(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR #202 Befund 2: ``weiter_erlaubt`` nutzt dieselbe Wiederhol-Logik wie ``weiter_entscheidung``."""
    monkeypatch.setenv(startstelle.STAND_ABSTAND_ENV, "0")
    aufrufe, pruefer = _zaehler(None, True)
    assert startstelle.weiter_erlaubt("1", pruefer=pruefer) is True
    assert len(aufrufe) == 2


def test_spawn_local_gh_fehler_bricht_vor_tab_bau_ab() -> None:
    """#191: ``spawn_local.ps1`` öffnet bei komplettem gh-Fehler keine Tabs (auch kein wache/leitstand)."""
    import shutil

    skript = SKILL / "spawn_local.ps1"
    text = skript.read_text(encoding="utf-8")
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh:
        pruef = subprocess.run(
            [
                pwsh,
                "-NoProfile",
                "-Command",
                "$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($env:TO_SPAWN_PS_PFAD, [ref]$null, [ref]$e); if ($e) { $e | Out-String; exit 1 }",
            ],
            env={**os.environ, "TO_SPAWN_PS_PFAD": str(skript)},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        assert pruef.returncode == 0, pruef.stdout + pruef.stderr

    stelle_zu_bauen = text.index("$zuBauenRc = $LASTEXITCODE")
    stelle_cmds = text.index("$cmds = @()")
    abschnitt = text[stelle_zu_bauen:stelle_cmds]
    assert "$zuBauen.Count -eq 0" in abschnitt, "Abbruch bei komplettem gh-Fehler vor Tab-Bau fehlt"
    assert "exit 2" in abschnitt, "gh-Fehler muss vor Tab-Bau mit Exit 2 enden"


# --- 5. Pflichtfall 10.11.: 3 Sessions statt 13 über spawn_srv.sh + tmux-Attrappe -----


def _pflichtfall_welt(tmp_path: Path, *, kaputt_alle: bool = False) -> tuple[Path, Path, dict[str, str]]:
    """Wegwerf-Git-Repo mit 13-Ticket-Manifest (10 geschlossen, 3 offen) und tmux-Attrappe."""
    import json

    from to_spawn import bash_wahl

    _ = bash_wahl  # Verfügbarkeit prüfen
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/test-org/test-repo.git"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)

    spec = 1011
    tickets = [str(n) for n in range(1011, 1024)]  # 13 Tickets
    geschlossen = tickets[:10]  # 10 geschlossen (1011..1020)
    offen = tickets[10:]  # 3 offen (1021..1023)

    manifeste = repo / "docs" / "agents" / "manifests"
    manifeste.mkdir(parents=True)
    (manifeste / f"spec-{spec}.json").write_text(
        json.dumps(
            {
                "spec": spec,
                "feature": "Pflichtfall 10.11.",
                "tickets": {t: {"title": f"Ticket {t}"} for t in tickets},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (repo / ".to-spawn").mkdir()
    (repo / ".to-spawn" / "config.json").write_text(
        json.dumps(
            {
                "hauptzweig": "main",
                "worktree_basis": (tmp_path / "wt").as_posix(),
                "speicher": {"max_sessions": 20, "min_frei_mib": 0, "staffel_s": 0},
                "leitstand": {"aktiv": False},
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)

    beleg = tmp_path / "beleg.txt"
    fenster_datei = tmp_path / "fenster.txt"
    fenster_exits = tmp_path / "fenster_exits.txt"
    bin_ordner = tmp_path / "bin"
    bin_ordner.mkdir()

    # tmux-Attrappe: nimmt new-session/new-window von spawn_srv.sh entgegen und startet den
    # echten bau.py-Befehl über tests/hilfen/bau_lauf_97.py (echtes bau.main + startstelle.betreten).
    tmux_helfer = bin_ordner / "tmux_helfer.py"
    tmux_helfer.write_text(
        "import os, re, subprocess, sys\n"
        "from pathlib import Path\n"
        f"fenster = Path({str(fenster_datei)!r})\n"
        f"exits = Path({str(fenster_exits)!r})\n"
        f"beleg = Path({str(beleg)!r})\n"
        f"lauf = {str(LAUF)!r}\n"
        f"bau_echt = {str(SKILL / 'skripte' / 'bau.py')!r}\n"
        "args = sys.argv[1:]\n"
        "cmd = args[0] if args else ''\n"
        "if cmd == 'has-session':\n"
        "    sys.exit(0 if fenster.exists() and fenster.read_text(encoding='utf-8').strip() else 1)\n"
        "if cmd == 'display-message':\n"
        f"    print({str(repo)!r})\n"
        "    sys.exit(0)\n"
        "if cmd in ('list-windows', 'list-sessions'):\n"
        "    if fenster.exists():\n"
        "        print(fenster.read_text(encoding='utf-8').strip())\n"
        "    sys.exit(0)\n"
        "if cmd in ('new-session', 'new-window'):\n"
        "    name = args[args.index('-n') + 1] if '-n' in args else ''\n"
        "    with fenster.open('a', encoding='utf-8') as fh:\n"
        "        fh.write(name + '\\n')\n"
        "    befehl = args[-1]\n"
        "    m = re.search(r'bau\\.py\\s+(\\d+)', befehl)\n"
        "    if m:\n"
        "        ticket = m.group(1)\n"
        "        erg = subprocess.run(\n"
        "            [sys.executable, lauf, str(beleg), ticket],\n"
        "            cwd=str(beleg.parent),\n"
        "            env=os.environ,\n"
        "            capture_output=True,\n"
        "            text=True,\n"
        "            encoding='utf-8',\n"
        "            timeout=60,\n"
        "            check=False,\n"
        "        )\n"
        "        with exits.open('a', encoding='utf-8') as fh:\n"
        "            fh.write(f'{ticket}:{erg.returncode}\\n')\n"
        "    sys.exit(0)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    tmux = bin_ordner / "tmux"
    tmux.write_text(f'#!/usr/bin/env bash\nexec {sys.executable!r} {str(tmux_helfer)!r} "$@"\n', encoding="utf-8")
    tmux.chmod(0o755)

    env = dict(os.environ)
    env.pop(startstelle.TEST_OFFEN_ENV, None)
    env.pop("TMUX", None)
    env.pop("LOCALAPPDATA", None)
    env.pop("BAU_AUFTRAG", None)
    env.update(
        {
            "PATH": f"{bin_ordner}{os.pathsep}{env.get('PATH', '')}",
            "TO_SPAWN_REPO": str(repo),
            "TO_SPAWN_GH_STUB": str(SKILL / "tests" / "hilfen" / "gh_stub.py"),
            "TO_SPAWN_TEST_BIN": str(bin_ordner),
            startstelle.SPERR_ORDNER_ENV: str(tmp_path / "sperren"),
            startstelle.STAND_ABSTAND_ENV: "0",
            "BAU_LAUF_97_SCHLAF_S": "0",
            "TMPDIR": str(tmp_path),
            "GH_STUB_ZU": ",".join(geschlossen),
            "GH_STUB_KAPUTT": ",".join(tickets) if kaputt_alle else "",
            # #353: Fake-tmux startet kein echtes bau.py-Log → 2-s-Prüfwartezeit in spawn_srv.sh sparen.
            "TO_SPAWN_ANLAUF_PRUEF_S": "0",
        }
    )
    assert len(tickets) == 13 and len(offen) == 3
    return repo, beleg, env


@pytest.mark.braucht("bash", "posix")
def test_pflichtfall_10_11_drei_sessions_statt_dreizehn(tmp_path: Path) -> None:
    """#191 Pflichtfall: 13 Tickets (10 geschlossen, 3 offen) über ``spawn_srv.sh`` → genau 3 Sessions."""
    from to_spawn import bash_wahl

    repo, beleg, env = _pflichtfall_welt(tmp_path)
    lauf = bash_wahl.lauf(
        [str(SKILL / "skripte" / "spawn_srv.sh"), "1011", "--ohne-wache", "--ohne-regularien"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    zeilen = beleg.read_text(encoding="utf-8").splitlines() if beleg.exists() else []
    gestartet = [z.split()[2] for z in zeilen]
    assert gestartet == ["1021", "1022", "1023"], (gestartet, ausgabe)
    exits = dict(
        z.split(":", 1) for z in (tmp_path / "fenster_exits.txt").read_text(encoding="utf-8").splitlines() if ":" in z
    )
    assert len(exits) == 13
    assert all(code == "0" for code in exits.values()), exits


@pytest.mark.braucht("bash", "posix")
def test_pflichtfall_10_11_gh_ausfall_startet_null_sessions(tmp_path: Path) -> None:
    """#191 Gegenprobe: fällt gh für alle 13 Tickets aus, startet keine einzige Session (Exit 2 je Fenster)."""
    from to_spawn import bash_wahl

    repo, beleg, env = _pflichtfall_welt(tmp_path, kaputt_alle=True)
    lauf = bash_wahl.lauf(
        [str(SKILL / "skripte" / "spawn_srv.sh"), "1011", "--ohne-wache", "--ohne-regularien"],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert not beleg.exists(), beleg.read_text(encoding="utf-8")
    exits = [
        z.split(":", 1)[1]
        for z in (tmp_path / "fenster_exits.txt").read_text(encoding="utf-8").splitlines()
        if ":" in z
    ]
    assert len(exits) == 13 and all(c == str(startstelle.EXIT_UNKLAR) for c in exits), exits


# --- 6. Testlücken aus PR #193 Befund 5 (Ablöse-Übernahme, OSError, msvcrt-Zweig) -----


def test_abloesung_uebernimmt_haupt_sperre_nach_ende_der_alten_session(
    umgebung: dict[str, str], tmp_path: Path
) -> None:
    """PR #193 Befund 5: endet die alte Session, übernimmt die Ablösung die Haupt-Sperre."""
    umgebung["TO_SPAWN_GH_STUB"] = str(SKILL / "tests" / "hilfen" / "gh_stub.py")
    umgebung[startstelle.SPERR_ORDNER_ENV] = str(tmp_path / "sperren")
    beleg = tmp_path / "beleg.txt"
    frei_alt = tmp_path / "frei_alt"
    frei_neu = tmp_path / "frei_neu"

    def _starten(halte: Path, *extra: str) -> subprocess.Popen[str]:
        env = {**umgebung, "BAU_LAUF_97_HALTE_DATEI": str(halte)}
        return subprocess.Popen(
            [sys.executable, str(LAUF), str(beleg), "19110", "--sofort", *extra],
            env=env,
            cwd=str(tmp_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )

    def _warte_zeilen(anzahl: int) -> None:
        ende = time.monotonic() + 20.0
        while time.monotonic() < ende:
            if beleg.exists() and len(beleg.read_text(encoding="utf-8").splitlines()) >= anzahl:
                return
            time.sleep(0.05)
        raise AssertionError(f"Erwartet {anzahl} Starts in {beleg}")

    alt = _starten(frei_alt)
    neu: subprocess.Popen[str] | None = None
    try:
        _warte_zeilen(1)
        neu = _starten(frei_neu, "--ohne-prompt")
        _warte_zeilen(2)

        haupt_lock = startstelle.sperr_datei("19110", "test-org/test-repo")
        assert haupt_lock.parent == tmp_path / "sperren", haupt_lock
        abloese_lock = haupt_lock.with_name(haupt_lock.name + ".abloesung")
        from to_spawn import sperre

        with sperre.halte(haupt_lock) as haupt_frei:
            assert not haupt_frei, "Alte Session muss die Haupt-Sperre halten"
        with sperre.halte(abloese_lock) as abloese_frei:
            assert not abloese_frei, "Ablösung muss die Ablöse-Sperre halten"

        frei_alt.write_text("go", encoding="utf-8")
        alt.communicate(timeout=30)

        # Warten, bis der Hintergrund-Thread der Ablösung die Haupt-Sperre übernommen hat
        # (daran erkennbar, dass die Ablöse-Sperre wieder frei ist).
        ende = time.monotonic() + 10.0
        uebernommen = False
        while time.monotonic() < ende:
            with sperre.halte(abloese_lock) as frei:
                if frei:
                    uebernommen = True
                    break
            time.sleep(0.05)
        assert uebernommen, "Ablösung hat die Haupt-Sperre nach Ende der alten Session nicht übernommen"
        assert neu.poll() is None, "Ablösung darf vor Freigabe von frei_neu nicht beendet sein"

        # Jetzt muss ein normaler Start abgewiesen werden („läuft schon“), kein dritter Start.
        dritter = subprocess.run(
            [sys.executable, str(LAUF), str(beleg), "19110", "--sofort"],
            env={**umgebung, "BAU_LAUF_97_SCHLAF_S": "0"},
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        ausgabe = dritter.stdout + dritter.stderr
        assert dritter.returncode == 0, ausgabe
        assert "läuft schon" in ausgabe, ausgabe
        assert len(beleg.read_text(encoding="utf-8").splitlines()) == 2
    finally:
        frei_alt.write_text("go", encoding="utf-8")
        frei_neu.write_text("go", encoding="utf-8")
        if alt.poll() is None:
            alt.communicate(timeout=30)
        if neu is not None:
            neu.communicate(timeout=30)


def test_start_sperre_oserror_ist_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PR #193 Befund 5: unbenutzbarer Sperr-Ordner (Datei statt Ordner) → fail-closed, Exit 2."""
    blockiert = tmp_path / "datei_statt_ordner"
    blockiert.write_text("x", encoding="utf-8")
    monkeypatch.setenv(startstelle.SPERR_ORDNER_ENV, str(blockiert))
    e = startstelle.betreten("19111", "test-org/test-repo", pruefer=lambda _t, _c: True)
    assert not e.erlaubt
    assert e.exit_code == startstelle.EXIT_UNKLAR
    assert "nicht nutzbar" in e.grund


def test_startstelle_msvcrt_zweig_sperrt_und_gibt_nach_ende_frei(tmp_path: Path) -> None:
    """#191: msvcrt-Zweig der Sperre in ``startstelle.betreten`` (zwei Prozesse, Prozess-Ende gibt frei)."""
    if sys.platform != "win32":
        pytest.importorskip("fcntl", reason="msvcrt-Nachbau unter POSIX braucht fcntl")
    skript = (
        "import errno, os, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(SKILL)!r})\n"
        "from to_spawn import sperre, startstelle\n"
        "if sys.platform != 'win32':\n"
        "    import fcntl\n"
        "    class _M:\n"
        "        LK_NBLCK = 2\n"
        "        def locking(self, fd, m, n):\n"
        "            try:\n"
        "                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "            except OSError:\n"
        "                raise OSError(errno.EACCES, 'belegt') from None\n"
        "    sperre._fcntl, sperre._msvcrt = None, _M()\n"
        "e = startstelle.betreten('19112', 'test-org/test-repo', pruefer=lambda _t, _c: True)\n"
        "print('JA' if e.erlaubt else f'NEIN:{e.grund}', flush=True)\n"
        "if sys.argv[1] == 'halten' and e.erlaubt:\n"
        "    sys.stdin.readline()\n"
    )
    # UTF-8 explizit erzwingen: unter Windows nutzt Python für Pipes sonst cp1252 (Umlaute/Gedankenstrich).
    env = {
        **os.environ,
        startstelle.SPERR_ORDNER_ENV: str(tmp_path / "sperren"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    halter = subprocess.Popen(
        [sys.executable, "-c", skript, "halten"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        assert halter.stdout is not None
        assert halter.stdout.readline().strip() == "JA"
        zweiter = subprocess.run(
            [sys.executable, "-c", skript, "pruefen"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        assert zweiter.returncode == 0 and "läuft schon" in zweiter.stdout, zweiter.stdout + zweiter.stderr
    finally:
        halter.communicate("\n", timeout=30)
    assert halter.returncode == 0
    danach = subprocess.run(
        [sys.executable, "-c", skript, "pruefen"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert danach.stdout.strip() == "JA", danach.stdout + danach.stderr


# --- 7. Maschinenübergreifende Ticket-Sperre (Remote-Lease, Linux + Windows) ----------


def _zwei_rechner_welt(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Gemeinsames Bare-Remote + zwei unabhängige Klone (Rechner A = PC, Rechner B = Bau-Server)."""
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    rechner_a = tmp_path / "klon-pc"
    rechner_b = tmp_path / "klon-server"
    subprocess.run(["git", "clone", "-q", str(bare), str(rechner_a)], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(rechner_a),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(rechner_a), "push", "-q", "origin", "HEAD"], check=True, capture_output=True)
    subprocess.run(["git", "clone", "-q", str(bare), str(rechner_b)], check=True, capture_output=True)
    return bare, rechner_a, rechner_b


def _rechner_env(
    tmp_path: Path,
    repo: Path,
    rechner: str,
    *,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Prozess-Umgebung für einen Rechner mit eigenem Sperr-Ordner und aktiver Fern-Sperre."""
    env = dict(os.environ)
    env.pop(startstelle.TEST_OFFEN_ENV, None)
    env.pop(startstelle.TEST_FERN_SPERRE_AUS_ENV, None)
    env.pop("LOCALAPPDATA", None)
    env.pop("BAU_AUFTRAG", None)
    env.update(
        {
            "TO_SPAWN_REPO": str(repo),
            "TO_SPAWN_GH_STUB": str(SKILL / "tests" / "hilfen" / "gh_stub.py"),
            "TO_SPAWN_RECHNER": rechner,
            startstelle.SPERR_ORDNER_ENV: str(tmp_path / f"sperren-{rechner}"),
            startstelle.STAND_ABSTAND_ENV: "0",
            "TMPDIR": str(tmp_path / f"tmp-{rechner}"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    (tmp_path / f"tmp-{rechner}").mkdir(parents=True, exist_ok=True)
    if extra:
        env.update(extra)
    return env


def test_fern_sperre_zwei_rechner_sperren_sich_und_geben_bei_ende_frei(tmp_path: Path) -> None:
    """#191: Läuft ein Ticket auf Rechner A (PC), weist Rechner B (Bau-Server) ab; nach Ende startet B."""
    _bare, klon_pc, klon_srv = _zwei_rechner_welt(tmp_path)
    beleg = tmp_path / "beleg.txt"
    halte_pc = tmp_path / "halte_pc"

    env_pc = _rechner_env(tmp_path, klon_pc, "pc-win", extra={"BAU_LAUF_97_HALTE_DATEI": str(halte_pc)})
    env_srv = _rechner_env(tmp_path, klon_srv, "bau-server", extra={"BAU_LAUF_97_SCHLAF_S": "0"})

    pc = subprocess.Popen(
        [sys.executable, str(LAUF), str(beleg), "19120", "--sofort"],
        env=env_pc,
        cwd=str(klon_pc),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    try:
        ende = time.monotonic() + 20.0
        while time.monotonic() < ende:
            if beleg.exists() and beleg.read_text(encoding="utf-8").splitlines():
                break
            time.sleep(0.05)
        assert beleg.exists() and len(beleg.read_text(encoding="utf-8").splitlines()) == 1

        # Zweiter Rechner sieht aktive Fern-Sperre von pc-win -> kein zweiter Start.
        srv_block = subprocess.run(
            [sys.executable, str(LAUF), str(beleg), "19120", "--sofort"],
            env=env_srv,
            cwd=str(klon_srv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        ausgabe_block = srv_block.stdout + srv_block.stderr
        assert srv_block.returncode == 0, ausgabe_block
        assert "läuft schon" in ausgabe_block and "pc-win" in ausgabe_block, ausgabe_block
        assert len(beleg.read_text(encoding="utf-8").splitlines()) == 1
    finally:
        halte_pc.write_text("ende", encoding="utf-8")
        pc_aus, _ = pc.communicate(timeout=30)
    assert pc.returncode == 0, pc_aus

    # Nach sauberem Ende von PC ist die Remote-Ref frei -> Bau-Server darf starten.
    srv_frei = subprocess.run(
        [sys.executable, str(LAUF), str(beleg), "19120", "--sofort"],
        env=env_srv,
        cwd=str(klon_srv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert srv_frei.returncode == 0, srv_frei.stdout + srv_frei.stderr
    assert len(beleg.read_text(encoding="utf-8").splitlines()) == 2


def test_fern_sperre_gleichzeitiger_start_auf_zwei_rechnern_ergibt_eine_session(tmp_path: Path) -> None:
    """#191: Wettlauf zweier Rechner am selben Bare-Remote → atomares CAS lässt genau einen starten."""
    _bare, klon_pc, klon_srv = _zwei_rechner_welt(tmp_path)
    beleg = tmp_path / "beleg.txt"
    env_pc = _rechner_env(tmp_path, klon_pc, "pc-win", extra={"BAU_LAUF_97_SCHLAF_S": "1.5"})
    env_srv = _rechner_env(tmp_path, klon_srv, "bau-server", extra={"BAU_LAUF_97_SCHLAF_S": "1.5"})

    p_pc = subprocess.Popen(
        [sys.executable, str(LAUF), str(beleg), "19121", "--sofort"],
        env=env_pc,
        cwd=str(klon_pc),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    p_srv = subprocess.Popen(
        [sys.executable, str(LAUF), str(beleg), "19121", "--sofort"],
        env=env_srv,
        cwd=str(klon_srv),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    ausgaben = [p.communicate(timeout=60)[0] for p in (p_pc, p_srv)]
    assert sorted((p_pc.returncode, p_srv.returncode)) == [0, 0], ausgaben
    zeilen = beleg.read_text(encoding="utf-8").splitlines() if beleg.exists() else []
    assert len(zeilen) == 1, (zeilen, ausgaben)
    assert any("läuft schon" in a for a in ausgaben), ausgaben


def test_fern_sperre_remote_unerreichbar_ist_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#191: Ist das Remote nicht erreichbar, verweigert die Startstelle fail-closed mit Exit 2."""
    repo = tmp_path / "repo-kaputt"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(tmp_path / "fehlt.git")], cwd=repo, check=True)
    monkeypatch.delenv(startstelle.TEST_FERN_SPERRE_AUS_ENV, raising=False)
    monkeypatch.setenv("TO_SPAWN_RECHNER", "pc-win")
    monkeypatch.setenv(startstelle.SPERR_ORDNER_ENV, str(tmp_path / "sperren"))

    e = startstelle.betreten("19122", "test-org/test-repo", repo, pruefer=lambda _t, _c: True)
    assert not e.erlaubt
    assert e.exit_code == startstelle.EXIT_UNKLAR
    assert "fail-closed" in e.grund


def test_fern_sperre_kaputter_inhalt_ist_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#191: Unlesbarer/kaputter Lease-Commit auf dem Remote wird nie still überschrieben (Exit 2)."""
    _bare, klon_pc, klon_srv = _zwei_rechner_welt(tmp_path)
    ref = startstelle.fern_sperr_ref("19123")
    ok, sha, fehler = gh.commit_mit_nachricht(klon_pc, "kein-json{{{")
    assert ok, fehler
    ok_push, _konflikt, fehler_push = gh.remote_ref_cas_setzen(klon_pc, ref, sha, "")
    assert ok_push, fehler_push

    monkeypatch.delenv(startstelle.TEST_FERN_SPERRE_AUS_ENV, raising=False)
    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-server")
    monkeypatch.setenv(startstelle.SPERR_ORDNER_ENV, str(tmp_path / "sperren"))

    e = startstelle.betreten("19123", "test-org/test-repo", klon_srv, pruefer=lambda _t, _c: True)
    assert not e.erlaubt
    assert e.exit_code == startstelle.EXIT_UNKLAR
    assert "fail-closed" in e.grund
    # Kaputte Ref bleibt unangetastet auf dem Remote.
    ok_sha, fern_sha, _ = gh.remote_ref_sha(klon_srv, ref)
    assert ok_sha and fern_sha == sha


def test_fern_sperre_abgelaufene_lease_wird_uebernommen_und_alte_freigabe_loescht_sie_nicht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#191 Ablauf-Regel: Abgelaufene Lease eines anderen Rechners wird atomar übernommen; verspätete Freigabe schützt neue Lease."""
    import json

    _bare, klon_pc, klon_srv = _zwei_rechner_welt(tmp_path)
    ref = startstelle.fern_sperr_ref("19124")
    abgelaufen = {
        "schema": 1,
        "ticket": "19124",
        "rechner": "pc-win",
        "pid": 99999,
        "token": "alt-token",
        "beginn": 1_000_000.0,
        "erneuert": 1_000_000.0,
        "ablauf": 1_000_060.0,
        "ttl_s": 60.0,
    }
    ok, alt_sha, fehler = gh.commit_mit_nachricht(klon_pc, json.dumps(abgelaufen))
    assert ok, fehler
    ok_push, _konflikt, fehler_push = gh.remote_ref_cas_setzen(klon_pc, ref, alt_sha, "")
    assert ok_push, fehler_push

    monkeypatch.delenv(startstelle.TEST_FERN_SPERRE_AUS_ENV, raising=False)
    monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-server")
    monkeypatch.setenv(startstelle.SPERR_ORDNER_ENV, str(tmp_path / "sperren-srv"))

    try:
        e = startstelle.betreten("19124", "test-org/test-repo", klon_srv, pruefer=lambda _t, _c: True)
        assert e.erlaubt, e.grund
        ok_sha, neu_sha, _ = gh.remote_ref_sha(klon_srv, ref)
        assert ok_sha and neu_sha and neu_sha != alt_sha

        # Verspätete CAS-Freigabe des alten Halters (mit alt_sha) darf die neue Lease nicht löschen.
        ok_del, konflikt, _ = gh.remote_ref_cas_loeschen(klon_pc, ref, alt_sha)
        assert not ok_del and konflikt
        ok_sha2, danach_sha, _ = gh.remote_ref_sha(klon_srv, ref)
        assert ok_sha2 and danach_sha == neu_sha
    finally:
        startstelle.alle_fern_sperren_freigeben()


def test_fern_sperre_heartbeat_verlaengert_aktive_lease(tmp_path: Path) -> None:
    """#191: Laufende Session verlängert ihre Lease regelmäßig — über die Erst-TTL hinaus bleibt Rechner B gesperrt."""
    _bare, klon_pc, klon_srv = _zwei_rechner_welt(tmp_path)
    beleg = tmp_path / "beleg.txt"
    halte_pc = tmp_path / "halte_pc"
    ref = startstelle.fern_sperr_ref("19125")

    env_pc = _rechner_env(
        tmp_path,
        klon_pc,
        "pc-win",
        extra={
            "BAU_LAUF_97_HALTE_DATEI": str(halte_pc),
            startstelle.FERN_SPERRE_TTL_ENV: "1.5",
            startstelle.FERN_SPERRE_TAKT_ENV: "0.3",
        },
    )
    env_srv = _rechner_env(tmp_path, klon_srv, "bau-server", extra={"BAU_LAUF_97_SCHLAF_S": "0"})

    pc = subprocess.Popen(
        [sys.executable, str(LAUF), str(beleg), "19125", "--sofort"],
        env=env_pc,
        cwd=str(klon_pc),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    try:
        ende = time.monotonic() + 20.0
        while time.monotonic() < ende:
            if beleg.exists() and beleg.read_text(encoding="utf-8").splitlines():
                break
            time.sleep(0.05)
        ok_1, sha_1, _ = gh.remote_ref_sha(klon_srv, ref)
        assert ok_1 and sha_1

        # Länger warten als die Erst-TTL (1,5 s): Heartbeat muss den SHA fortgeschrieben haben.
        time.sleep(2.0)
        ok_2, sha_2, _ = gh.remote_ref_sha(klon_srv, ref)
        assert ok_2 and sha_2 and sha_2 != sha_1, "Heartbeat hat die Lease nicht verlängert"

        srv = subprocess.run(
            [sys.executable, str(LAUF), str(beleg), "19125", "--sofort"],
            env=env_srv,
            cwd=str(klon_srv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        assert srv.returncode == 0 and "läuft schon" in (srv.stdout + srv.stderr)
    finally:
        halte_pc.write_text("ende", encoding="utf-8")
        pc.communicate(timeout=30)


def test_fern_sperre_uebernahme_bei_umzug_und_weiter_pruefung(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#191 Übernahme-Regel: Explizite Übernahme (Umzug) löst aktive Lease ab; der alte Rechner stoppt vor der nächsten Staffel-Runde."""
    _bare, klon_pc, klon_srv = _zwei_rechner_welt(tmp_path)
    monkeypatch.delenv(startstelle.TEST_FERN_SPERRE_AUS_ENV, raising=False)
    ref = startstelle.fern_sperr_ref("19126")

    try:
        # Rechner A (PC) betritt das Ticket.
        monkeypatch.setenv("TO_SPAWN_RECHNER", "pc-win")
        monkeypatch.setenv(startstelle.SPERR_ORDNER_ENV, str(tmp_path / "sperren-pc"))
        e_pc = startstelle.betreten("19126", "test-org/test-repo", klon_pc, pruefer=lambda _t, _c: True)
        assert e_pc.erlaubt, e_pc.grund

        # Rechner B (Bau-Server) übernimmt ausdrücklich (wie ``bau.py --umzug``).
        monkeypatch.setenv("TO_SPAWN_RECHNER", "bau-server")
        monkeypatch.setenv(startstelle.SPERR_ORDNER_ENV, str(tmp_path / "sperren-srv"))
        e_srv = startstelle.betreten(
            "19126", "test-org/test-repo", klon_srv, uebernahme=True, pruefer=lambda _t, _c: True
        )
        assert e_srv.erlaubt, e_srv.grund

        # Rechner A prüft vor einer Staffel-Runde: Lease gehört jetzt bau-server → kein Weiterlaufen.
        monkeypatch.setenv("TO_SPAWN_RECHNER", "pc-win")
        w_pc = startstelle.weiter_entscheidung("19126", klon_pc, pruefer=lambda _t, _c: True, abstand=0)
        assert not w_pc.erlaubt and w_pc.exit_code == 0 and "bau-server" in w_pc.grund, w_pc
    finally:
        startstelle.alle_fern_sperren_freigeben()

    ok_ende, sha_ende, _ = gh.remote_ref_sha(klon_srv, ref)
    assert ok_ende and sha_ende == ""


def test_fern_sperre_test_schalter_wirkt_nicht_im_betrieb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#191: ``TO_SPAWN_TEST_FERN_SPERRE_AUS=1`` wird außerhalb von Tests ignoriert (fail-closed im Betrieb)."""
    repo = tmp_path / "repo-betrieb"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(tmp_path / "nicht-da.git")], cwd=repo, check=True)
    monkeypatch.delenv("TO_SPAWN_TESTLAUF", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv(startstelle.TEST_FERN_SPERRE_AUS_ENV, "1")
    monkeypatch.setenv(startstelle.SPERR_ORDNER_ENV, str(tmp_path / "sperren"))

    e = startstelle.betreten("19127", "test-org/test-repo", repo, pruefer=lambda _t, _c: True)
    assert not e.erlaubt
    assert e.exit_code == startstelle.EXIT_UNKLAR
    assert "fail-closed" in e.grund


def test_fern_sperre_gleicher_rechner_uebernimmt_verwaiste_lease_nach_absturz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#191: Stirbt ein Prozess hart auf demselben Rechner, darf derselbe Rechner seine verwaiste Lease sofort übernehmen."""
    import json

    _bare, klon_pc, _klon_srv = _zwei_rechner_welt(tmp_path)
    ref = startstelle.fern_sperr_ref("19128")
    jetzt = time.time()
    verwaist = {
        "schema": 1,
        "ticket": "19128",
        "rechner": "PC-WIN",  # Groß-/Kleinschreibung egal (auswahl.gleicher_rechner)
        "pid": 99998,
        "token": "abgestuerzt-token",
        "beginn": jetzt - 10.0,
        "erneuert": jetzt - 10.0,
        "ablauf": jetzt + 600.0,
        "ttl_s": 600.0,
    }
    ok, alt_sha, fehler = gh.commit_mit_nachricht(klon_pc, json.dumps(verwaist))
    assert ok, fehler
    ok_push, _konflikt, fehler_push = gh.remote_ref_cas_setzen(klon_pc, ref, alt_sha, "")
    assert ok_push, fehler_push

    monkeypatch.delenv(startstelle.TEST_FERN_SPERRE_AUS_ENV, raising=False)
    monkeypatch.setenv("TO_SPAWN_RECHNER", "pc-win")
    monkeypatch.setenv(startstelle.SPERR_ORDNER_ENV, str(tmp_path / "sperren-pc"))

    try:
        e = startstelle.betreten("19128", "test-org/test-repo", klon_pc, pruefer=lambda _t, _c: True)
        assert e.erlaubt, e.grund
        ok_sha, neu_sha, _ = gh.remote_ref_sha(klon_pc, ref)
        assert ok_sha and neu_sha and neu_sha != alt_sha
    finally:
        startstelle.alle_fern_sperren_freigeben()
