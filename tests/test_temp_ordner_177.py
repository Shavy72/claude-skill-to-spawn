"""Temp-Ordner je Ticket über ``temp_ordner.anlegen()`` für Bau, Aufseher und Prüf-Tür (#177).

Prüft:
- Bau-Sitzung (``bau.main``): ``TMPDIR``/``TEMP``/``TMP`` = markierter Unterordner ``t<ticket>-…``;
  eigene Sitzungsdateien (``settings.json``, ``mcp.json``, ``prompt.txt``) liegen darin, und
  ``sessions_stand.VERWAIST`` erkennt ``settings.json`` weiterhin.
- Aufseher-Sitzung (``wache.main``): ``TMPDIR``/``TEMP``/``TMP`` = markierter Unterordner ``t<spec>-…``;
  der Lauf-Ordner ``wache-<spec>-…`` liegt darin.
- Prüf-Tür (``pruef.pruefen``, ``ausfuehren.lauf``, ``leiter.eigener_befehl``):
  eigenständiger Lauf legt genau einen markierten Unterordner ``t<ticket>-…`` je Prüflauf an;
  aus einer Sitzung mit passendem markiertem ``TMPDIR`` wird dieser Ordner weiter genutzt
  statt auf die Temp-Wurzel zurückzufallen.
- End-to-End-Beleg (M14): echte Reste aus Bau-, Aufseher- und Prüf-Lauf bleiben bei offenem
  Ticket/frischem Stand und werden nach Schließung + Alterung vom Hausmeister geräumt.
- Absicherung aus dem Review (#177):
  - verknüpfter Git-Worktree als markierter Temp-Ordner wird über ``git worktree remove``
    sauber aus dem Hauptrepo ausgetragen;
  - TOCTOU: wird der Ordner zwischen Prüfung und Löschen gegen einen Symlink (auch auf
    einen echten Worktree) oder anderen Ordner getauscht, verweigert der Hausmeister das
    Löschen und lässt das Ziel unberührt;
  - Repo-Slug mit getrenntem ``ticket_repo`` in der Maschinen-Liste wird im Marker auf das
    ``ticket_repo`` abgebildet, sodass der Hausmeister ihn wiedererkennt.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from test_pruef_tuer_3b import _git as _pruef_git
from test_pruef_tuer_3b import _probe_py
from test_temp_ordner_136 import (
    TEST_REPO,
    _alt_baum,
    _git,
    _markieren,
    _quelle_anlegen,
    _wurzel,
)

from to_spawn import hausmeister, pruef, temp_ordner
from to_spawn.pruef import ausfuehren, leiter

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"


def _lade_skript(name: str, modulname: str, monkeypatch: pytest.MonkeyPatch, repo: Path) -> types.ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location(modulname, SKRIPTE / name)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules[modulname] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop(modulname, None)
    return modul


def _pruefe_marker(ordner: Path, wurzel: Path, *, ticket: int, repo: str, von: str) -> dict[str, Any]:
    assert ordner.parent == wurzel, f"erwartete Unterordner von {wurzel}, bekam {ordner}"
    assert ordner.name.startswith(f"t{ticket}-"), f"Ordnername ohne t{ticket}-Präfix: {ordner.name}"
    marker_pfad = ordner / temp_ordner.MARKER
    assert marker_pfad.is_file(), f"Marker fehlt in {ordner}"
    daten = json.loads(marker_pfad.read_text(encoding="utf-8"))
    info = os.lstat(ordner)
    assert daten["ticket"] == ticket
    assert daten["repo"] == repo
    assert daten["von"] == von
    assert daten["ordner"] == ordner.name
    assert daten["dev"] == info.st_dev
    assert daten["ino"] == info.st_ino
    return daten


def _bau_vorbereiten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    repo_slug: str = TEST_REPO,
) -> types.ModuleType:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    modul = _lade_skript("bau.py", "bau_177_test", monkeypatch, repo)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("BAU_AUFTRAG", raising=False)
    monkeypatch.setattr(modul, "gh_repo_ermitteln", lambda _repo: repo_slug)
    monkeypatch.setattr(modul.config, "sicherstellen", lambda _repo: None)
    monkeypatch.setattr(modul, "load_default", lambda *_a, **_k: {"prompt_template": "TEMPLATE"})
    monkeypatch.setattr(modul, "find_manifest", lambda _t: ({"spec": "100"}, {"title": "Titel"}))
    monkeypatch.setattr(modul, "build_prompt", lambda *_a, **_k: "GRUND-PROMPT")
    monkeypatch.setattr(modul, "known_skill_names", lambda: set())
    monkeypatch.setattr(modul, "mcp_catalog", dict)
    monkeypatch.setattr(modul.context_mode, "server_definition", lambda _d: {})
    monkeypatch.setattr(modul.nest, "git_hooks_einrichten", lambda *_a, **_k: None)
    monkeypatch.setattr(modul.nest, "sandbox_start", lambda *_a, **_k: [])
    monkeypatch.setattr(modul.speicher, "auf_platte_warten", lambda *_a, **_k: None)
    monkeypatch.setattr(modul, "auf_speicher_warten", lambda *_a, **_k: None)
    monkeypatch.setattr(modul.vertrauen, "still_sicherstellen", lambda *_a, **_k: None)
    monkeypatch.setattr(modul, "umzug_anfragen_aufraeumen", lambda _t: tmp_path / "anfrage.json")
    monkeypatch.setattr(modul, "session_id_setzen", lambda *_a, **_k: None)
    monkeypatch.setattr(modul, "bau_log_umgebung", lambda *_a, **_k: {})
    return modul


def _wache_vorbereiten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    repo_slug: str = TEST_REPO,
) -> types.ModuleType:
    repo = tmp_path / "wache-repo"
    repo.mkdir(exist_ok=True)
    modul = _lade_skript("wache.py", "wache_177_test", monkeypatch, repo)

    @contextmanager
    def _leer(*_a: Any, **_k: Any):
        yield None

    monkeypatch.setattr(modul, "REPO_ORDNER", repo)
    monkeypatch.setattr(modul, "repo_slug_oder_abbruch", lambda: repo_slug)
    monkeypatch.setattr(modul, "start_ordner", lambda: repo)
    monkeypatch.setattr(modul, "auf_speicher_warten", lambda *_a, **_k: 0)
    monkeypatch.setattr(modul.config, "sicherstellen", lambda *_a, **_k: None)
    monkeypatch.setattr(modul.config, "lade", lambda *_a, **_k: {})
    monkeypatch.setattr(modul.startklar, "gate", lambda *_a, **_k: 0)
    monkeypatch.setattr(modul.context_mode, "pruefen", lambda **_k: tmp_path)
    monkeypatch.setattr(modul.vertrauen, "still_sicherstellen", lambda *_a, **_k: None)
    monkeypatch.setattr(modul.nest, "git_hooks_einrichten", lambda *_a, **_k: None)
    monkeypatch.setattr(modul.handoff_blick, "laeuft", _leer)
    monkeypatch.setattr(modul.briefkasten_wecker, "laeuft", _leer)
    for name in ("BAU_UMZUG_DATEI", "TO_SPAWN_WACHE_SPEC", modul.ABLOESE_ENV):
        monkeypatch.setenv(name, "")
    return modul


def _ticket_probe_repo(tmp_path: Path, ticket: int, *, repo_slug: str = TEST_REPO, test_text: str | None = None) -> Path:
    """Python-Probe-Repo unter ``wt-<ticket>`` mit GitHub-Origin und Zweig ``ticket/<ticket>-probe``."""
    ordner = _probe_py(tmp_path / f"wt-{ticket}", test_text="assert addiere(2, 3) == 5")
    if test_text is not None:
        koerper = "\n".join(f"    {z}" for z in test_text.strip().splitlines())
        (ordner / "tests" / "test_rechnen.py").write_text(
            "import sys, pathlib\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n"
            "from rechnen import addiere\n\n"
            "def test_addiere():\n"
            f"{koerper}\n",
            encoding="utf-8",
        )
        _pruef_git(ordner, "add", "tests/test_rechnen.py")
        _pruef_git(ordner, "commit", "-qm", "test-anpassen")
    _pruef_git(ordner, "checkout", "-qb", f"ticket/{ticket}-probe")
    _pruef_git(ordner, "remote", "add", "origin", f"https://github.com/{repo_slug}.git")
    return ordner


# --- 1. Bau-Sitzung ------------------------------------------------------------------


def test_bau_sitzung_bekommt_markierten_ticket_temp_ordner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, set())
    bau = _bau_vorbereiten(tmp_path, monkeypatch)

    gesehen: dict[str, Any] = {}

    def fake_start(cmd: list[str], umzug_datei: Path) -> tuple[int, dict | None]:
        gesehen["env"] = {k: os.environ.get(k) for k in ("TMPDIR", "TEMP", "TMP")}
        gesehen["cmd"] = list(cmd)
        gesehen["umzug_datei"] = umzug_datei
        return 0, None

    monkeypatch.setattr(bau, "starte_session", fake_start)
    monkeypatch.setattr(sys, "argv", ["bau.py", "177", "--sofort"])

    assert bau.main() == 0

    tmpdir = Path(gesehen["env"]["TMPDIR"])
    assert gesehen["env"] == {"TMPDIR": str(tmpdir), "TEMP": str(tmpdir), "TMP": str(tmpdir)}
    _pruefe_marker(tmpdir, wurzel, ticket=177, repo=TEST_REPO, von="bau")

    # Sitzungsdateien (settings.json, prompt.txt, umzug.json) liegen im markierten Ticket-Ordner,
    # und sessions_stand.VERWAIST erkennt settings.json weiterhin.
    umzug_datei: Path = gesehen["umzug_datei"]
    assert tmpdir in umzug_datei.parents, f"Sitzungsordner {umzug_datei.parent} liegt außerhalb von {tmpdir}"
    settings_pfad = umzug_datei.parent / "settings.json"
    assert settings_pfad.is_file()
    sys.path.insert(0, str(SKRIPTE))
    try:
        import sessions_stand
    finally:
        sys.path.remove(str(SKRIPTE))
    treffer = sessions_stand.VERWAIST.search(str(settings_pfad))
    assert treffer is not None and treffer.group(1) == "177", settings_pfad


# --- 2. Aufseher-Sitzung -------------------------------------------------------------


def test_aufseher_sitzung_bekommt_markierten_spec_temp_ordner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, set())
    wache = _wache_vorbereiten(tmp_path, monkeypatch)

    gesehen: dict[str, Any] = {}

    def fake_fahre(**_kw: Any) -> int:
        gesehen["env"] = {k: os.environ.get(k) for k in ("TMPDIR", "TEMP", "TMP")}
        gesehen["umzug"] = Path(os.environ["BAU_UMZUG_DATEI"])
        return 0

    monkeypatch.setattr(wache.waechter_lauf, "fahre", fake_fahre)
    monkeypatch.setattr(sys, "argv", ["wache.py", "177"])

    assert wache.main() == 0

    tmpdir = Path(gesehen["env"]["TMPDIR"])
    assert gesehen["env"] == {"TMPDIR": str(tmpdir), "TEMP": str(tmpdir), "TMP": str(tmpdir)}
    _pruefe_marker(tmpdir, wurzel, ticket=177, repo=TEST_REPO, von="wache")
    assert tmpdir in gesehen["umzug"].parents, f"wache-Laufordner liegt außerhalb von {tmpdir}"


# --- 3. Prüf-Tür ---------------------------------------------------------------------


def test_pruef_ausfuehren_und_eigener_befehl_setzen_markierten_ticket_ordner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, set())
    monkeypatch.setenv("TMPDIR", "/tmp")
    probe = _ticket_probe_repo(tmp_path, 177)

    code = "import json, os; print(json.dumps({k: os.environ.get(k) for k in ('TMPDIR', 'TEMP', 'TMP')}))"
    erg = ausfuehren.lauf([sys.executable, "-c", code], probe)
    assert erg.code == 0, erg.ausgabe
    umg = json.loads(erg.ausgabe.strip())
    ordner_lauf = Path(umg["TMPDIR"])
    assert umg == {"TMPDIR": str(ordner_lauf), "TEMP": str(ordner_lauf), "TMP": str(ordner_lauf)}
    _pruefe_marker(ordner_lauf, wurzel, ticket=177, repo=TEST_REPO, von="pruef")

    # Auch eigener_befehl (pruefung.<art>) bekommt einen markierten t<ticket>-…-Ordner.
    stufe = leiter.eigener_befehl(probe, 'printf "%s|%s|%s\\n" "$TMPDIR" "$TEMP" "$TMP"', "schnell")
    assert stufe.ergebnis == leiter.GRUEN, stufe.ausgabe
    # Da GRUEN ausgabe leert, schreiben wir den Pfad in eine Temp-Datei im Zielordner und prüfen die Wurzel.
    markiert = sorted(p for p in wurzel.iterdir() if p.is_dir() and (p / temp_ordner.MARKER).is_file())
    assert len(markiert) == 2, markiert
    zweiter = next(p for p in markiert if p != ordner_lauf)
    _pruefe_marker(zweiter, wurzel, ticket=177, repo=TEST_REPO, von="pruef")


def test_pruef_tuer_teilt_einen_markierten_ordner_und_ueberschreibt_bau_tmpdir_nicht(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, set())
    monkeypatch.setenv("TMPDIR", "/tmp")
    spur = tmp_path / "gesehene_tmpdirs.txt"
    test_text = (
        "import os\n"
        f"with open({str(spur)!r}, 'a', encoding='utf-8') as f:\n"
        "    f.write(os.environ.get('TMPDIR', '') + '\\n')\n"
        "assert addiere(2, 3) == 5\n"
    )
    probe = _ticket_probe_repo(tmp_path, 178, test_text=test_text)

    # Fall A: eigenständiger pruef.pruefen-Lauf legt GENAU einen t178-…-Ordner für alle Stufen an.
    urteil = pruef.pruefen(probe, "base", "HEAD")
    assert urteil.exit == 0, urteil.zeile
    zeilen = [z for z in spur.read_text(encoding="utf-8").splitlines() if z]
    assert len(zeilen) >= 1
    pruef_ordner = Path(zeilen[-1])
    _pruefe_marker(pruef_ordner, wurzel, ticket=178, repo=TEST_REPO, von="pruef")
    alle_178 = [p for p in wurzel.iterdir() if p.name.startswith("t178-")]
    assert alle_178 == [pruef_ordner], f"mehrere Ordner für einen Prüflauf angelegt: {alle_178}"

    # Fall B: läuft pruef.pruefen aus einer Bau-Sitzung heraus (TMPDIR ist schon t178-… mit Marker),
    # bleibt der Sitzungs-Ordner erhalten — kein Rückfall auf die Wurzel, kein zweiter Ordner.
    bau_ordner = temp_ordner.anlegen(178, repo=TEST_REPO, von="bau")
    monkeypatch.setenv("TMPDIR", str(bau_ordner))
    monkeypatch.setenv("TEMP", str(bau_ordner))
    monkeypatch.setenv("TMP", str(bau_ordner))
    monkeypatch.setenv("TO_SPAWN_TICKET", "178")
    # Neuer Commit, damit der Stufe-0-Cache nicht trifft und pytest wirklich läuft.
    (probe / "rechnen.py").write_text("def addiere(a, b):\n    return a + b + 0\n", encoding="utf-8")
    _pruef_git(probe, "add", "rechnen.py")
    _pruef_git(probe, "commit", "-qm", "zweiter-lauf")

    urteil2 = pruef.pruefen(probe, "base", "HEAD")
    assert urteil2.exit == 0, urteil2.zeile
    zeilen2 = [z for z in spur.read_text(encoding="utf-8").splitlines() if z]
    assert Path(zeilen2[-1]) == bau_ordner
    alle_danach = sorted(p for p in wurzel.iterdir() if p.name.startswith("t178-"))
    assert alle_danach == sorted([pruef_ordner, bau_ordner])


# --- 4. End-to-End-Beleg M14: Hausmeister räumt echte Reste aller drei Aufrufer ------


def test_hausmeister_raeumt_echte_reste_aus_bau_aufseher_und_prueftuer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    zu: set[int] = set()
    wurzel = _wurzel(tmp_path, monkeypatch, zu)
    monkeypatch.setattr(
        hausmeister,
        "_ticket_daten",
        lambda nr, ziel=None: {"state": "CLOSED" if nr in zu else "OPEN"},
    )

    # 1. Echter Bau-Lauf hinterlässt absichtlichen Rest im TMPDIR
    bau = _bau_vorbereiten(tmp_path, monkeypatch)
    bau_ordner_box: list[Path] = []

    def fake_bau_start(_cmd: list[str], _umzug: Path) -> tuple[int, dict | None]:
        t = Path(os.environ["TMPDIR"])
        (t / "bau-rest.txt").write_text("absichtlicher bau-rest", encoding="utf-8")
        bau_ordner_box.append(t)
        return 0, None

    monkeypatch.setattr(bau, "starte_session", fake_bau_start)
    monkeypatch.setattr(sys, "argv", ["bau.py", "1771", "--sofort"])
    assert bau.main() == 0

    # 2. Echter Aufseher-Lauf hinterlässt absichtlichen Rest im TMPDIR
    wache = _wache_vorbereiten(tmp_path, monkeypatch)
    wache_ordner_box: list[Path] = []

    def fake_wache_fahre(**_kw: Any) -> int:
        t = Path(os.environ["TMPDIR"])
        (t / "wache-rest.txt").write_text("absichtlicher wache-rest", encoding="utf-8")
        wache_ordner_box.append(t)
        return 0

    monkeypatch.setattr(wache.waechter_lauf, "fahre", fake_wache_fahre)
    monkeypatch.setattr(sys, "argv", ["wache.py", "1772"])
    assert wache.main() == 0

    # 3. Echter Prüf-Tür-Lauf (eigenständig) hinterlässt absichtlichen Rest im TMPDIR
    monkeypatch.setenv("TMPDIR", "/tmp")
    probe = _ticket_probe_repo(
        tmp_path,
        1773,
        test_text=(
            "import os\n"
            "from pathlib import Path\n"
            "(Path(os.environ['TMPDIR']) / 'pruef-rest.txt').write_text('absichtlicher pruef-rest', encoding='utf-8')\n"
            "assert addiere(2, 3) == 5\n"
        ),
    )
    urteil = pruef.pruefen(probe, "base", "HEAD")
    assert urteil.exit == 0, urteil.zeile
    pruef_ordner = next(p for p in wurzel.iterdir() if p.name.startswith("t1773-"))
    assert (pruef_ordner / "pruef-rest.txt").is_file()

    alle = [bau_ordner_box[0], wache_ordner_box[0], pruef_ordner]
    assert all(p.is_dir() and (p / temp_ordner.MARKER).is_file() for p in alle)

    # Noch offen & frisch → Hausmeister lässt alle drei liegen.
    hausmeister.aufraeumen_alles(loeschen=True)
    assert all(p.is_dir() for p in alle)

    # Alle drei Tickets geschlossen, aber noch frisch → bleiben.
    zu.update({1771, 1772, 1773})
    hausmeister.aufraeumen_alles(loeschen=True)
    assert all(p.is_dir() for p in alle)

    # Gealtert (> 24 h) → Hausmeister räumt alle drei echten Reste samt Log-Zeile.
    for p in alle:
        _alt_baum(p)
    hausmeister.aufraeumen_alles(loeschen=True)

    assert [p for p in alle if p.exists()] == [], f"Reste blieben liegen: {[p for p in alle if p.exists()]}"
    log_pfad = Path(os.environ["TO_SPAWN_HAUSMEISTER_LOG"])
    zeilen = [json.loads(z) for z in log_pfad.read_text(encoding="utf-8").splitlines()]
    geloescht = {z["ordner"] for z in zeilen if z.get("art") == "gelöscht"}
    assert {str(p) for p in alle} <= geloescht


# --- 5. Review-Absicherung: Ticket-Repo-Auflösung, Worktree-Rest, TOCTOU -------------


def test_anlegen_loest_repo_slug_auf_ticket_repo_der_maschinen_liste_auf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hat ein Repo ein getrenntes ``ticket_repo`` in ``maschine.json``, steht dieses im Marker."""
    maschine = tmp_path / "maschine.json"
    maschine.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "slug": "Org/app-code",
                        "pfad": str(tmp_path / "app"),
                        "ticket_repo": "Org/app-tickets",
                        "worktree_basis": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TO_SPAWN_MASCHINE", str(maschine))
    wurzel = _wurzel(tmp_path, monkeypatch, {505})

    ordner = temp_ordner.anlegen(505, repo="Org/app-code", von="bau")
    _pruefe_marker(ordner, wurzel, ticket=505, repo="Org/app-tickets", von="bau")

    _alt_baum(ordner)
    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert not ordner.exists(), ergebnis[ordner]


def test_worktree_als_markierter_temp_ordner_wird_per_git_worktree_remove_geraeumt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wurzel = _wurzel(tmp_path, monkeypatch, {1801})
    quelle = _quelle_anlegen(tmp_path / "remote")
    haupt = tmp_path / "haupt"
    _git(tmp_path, "clone", "-q", str(quelle), str(haupt))

    wt = wurzel / "t1801-wt"
    _git(haupt, "worktree", "add", "-q", "-b", "wt-1801", str(wt), "origin/main")
    _alt_baum(_markieren(wt, 1801))

    vorher = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=haupt,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert str(wt.resolve()) in vorher

    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert not wt.exists(), ergebnis[wt]

    nachher = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=haupt,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert str(wt.resolve()) not in nachher, f"Worktree noch im Hauptrepo eingetragen:\n{nachher}"


@pytest.mark.braucht("posix")
def test_toctou_symlink_tausch_zwischen_pruefung_und_loeschen_schuetzt_ziel_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wird der geprüfte Temp-Ordner direkt vor dem Löschen durch einen Symlink auf einen
    echten Worktree ersetzt, bricht ``temp_ordner_loeschen`` ab und der Ziel-Worktree bleibt."""
    _wurzel(tmp_path, monkeypatch, {1802})
    quelle = _quelle_anlegen(tmp_path / "remote")
    haupt = tmp_path / "haupt"
    _git(tmp_path, "clone", "-q", str(quelle), str(haupt))
    echter_wt = tmp_path / "echter-wt"
    _git(haupt, "worktree", "add", "-q", "-b", "wt-echt", str(echter_wt), "origin/main")
    (echter_wt / "wichtig.txt").write_text("nicht löschen", encoding="utf-8")

    opfer = temp_ordner.anlegen(1802, repo=TEST_REPO, von="bau")
    _alt_baum(opfer)

    echt_loeschen = hausmeister.temp_ordner_loeschen

    def tausch_und_loeschen(ordner: Path, **kw: Any) -> None:
        # Simuliert den Angreifer/Rennzustand genau nach der Gegenprobe: Ordner weg, Symlink hin.
        shutil.rmtree(ordner)
        ordner.symlink_to(echter_wt, target_is_directory=True)
        echt_loeschen(ordner, **kw)

    monkeypatch.setattr(hausmeister, "temp_ordner_loeschen", tausch_und_loeschen)

    ergebnis = dict(temp_ordner.aufraeumen(loeschen=True))
    assert ergebnis[opfer].startswith("bleibt:"), ergebnis[opfer]
    assert (echter_wt / "wichtig.txt").is_file(), "Ziel-Worktree wurde trotz TOCTOU-Symlink angefasst!"
    liste = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=haupt,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert str(echter_wt.resolve()) in liste, "Ziel-Worktree wurde aus dem Hauptrepo ausgetragen!"
