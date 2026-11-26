"""Weg-Tests #410: Einrichtungs-Ticket (T0) vor T1, wenn Prüfung oder Staging fehlen.

Echt laufen: CLI (``to_spawn.py regularien`` / ``anhaengen``), Konfig, Manifest,
Git-Repo. Gestellt ist nur die ``gh``-CLI (GitHub ist ein externer Dienst): Diese
Datei ist das Ersatz-Skript. Sie nimmt die aufgezeichnete Welt aus
``test_spec_rand_e2`` und ergänzt nur ``gh issue create`` (neues Issue in der Welt,
Antwort = Issue-URL wie echtes ``gh``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_spec_rand_e2 as e2

T0 = 950


def _stub_main(args: list[str]) -> int:
    pfad = Path(os.environ["E2_GH_WELT"])
    welt = json.loads(pfad.read_text(encoding="utf-8"))
    if args[:1] == ["api"] and "/labels/" in args[1]:
        # Label-Abfrage wie echtes ``gh``: vorhanden = Exit 0, sonst 404.
        name = args[1].rsplit("/", 1)[-1]
        return 0 if name in welt.get("labels", []) else 1
    if args[:2] == ["label", "create"]:
        welt.setdefault("labels", []).append(args[2])
        pfad.write_text(json.dumps(welt), encoding="utf-8")
        return 0
    if args[:2] == ["issue", "list"]:
        label = args[args.index("--label") + 1]
        treffer = [
            {"number": d["number"], "title": d["title"]}
            for d in welt["issues"].values()
            if d["state"] == "open" and label in [x["name"] for x in d["labels"]]
        ]
        print(json.dumps(treffer))
        return 0
    if args[:1] == ["api"] and (stelle := welt.get("api_scheitert_einmal")) and args[1].endswith(stelle):
        # Lesefehler nachstellen (gh api scheitert genau einmal, dann heilt die Welt).
        del welt["api_scheitert_einmal"]
        pfad.write_text(json.dumps(welt), encoding="utf-8")
        print("HTTP 502: Server Error", file=sys.stderr)
        return 1
    if args[:2] != ["issue", "create"]:
        return e2._stub_main(args)
    if welt.get("create_scheitert"):
        print("HTTP 502: Server Error", file=sys.stderr)
        return 1
    nummer = T0 + len(welt.setdefault("angelegt", []))
    titel = args[args.index("--title") + 1]
    labels = [args[i + 1] for i, a in enumerate(args) if a == "--label"]
    if any(x not in welt.get("labels", []) for x in labels):
        print("could not add label: not found", file=sys.stderr)
        return 1
    welt["issues"][str(nummer)] = e2._issue(nummer, titel, labels=labels)
    welt["angelegt"].append(titel)
    pfad.write_text(json.dumps(welt), encoding="utf-8")
    print(f"https://github.com/{e2.SLUG}/issues/{nummer}")
    return 0


# #353: Als gh-Stub gestartet sofort beenden, ohne pytest (und Plugins) zu importieren.
if __name__ == "__main__":
    sys.exit(_stub_main(sys.argv[1:]))

import pytest  # noqa: E402


def _aufbau(
    tmp_path: Path, *, konfig: dict[str, Any] | None = None, python: bool = False
) -> tuple[Path, Path, dict[str, str]]:
    repo, weltdatei, umgebung = e2._aufbau(tmp_path)
    umgebung["TO_SPAWN_GH_STUB"] = str(Path(__file__).resolve())
    if konfig is not None:
        (repo / ".to-spawn").mkdir()
        (repo / ".to-spawn" / "config.json").write_text(json.dumps(konfig), encoding="utf-8")
    if python:
        (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    return repo, weltdatei, umgebung


def _cli(repo: Path, umgebung: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(e2.CLI), *args],
        check=False,
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _manifest(repo: Path) -> dict[str, Any]:
    return json.loads(e2._manifest(repo).read_text(encoding="utf-8"))


#: Prüf- und Live-Befehle (#425 Teil D): ohne sie fordert das Einrichtungs-Ticket sie nach.
BEFEHLE = {
    "pruefung": {"schnell": "make pruefen-schnell", "komplett": "make pruefen-alles"},
    "live": {"befehl": "make live"},
}
VOLL = {
    "staging": {"url": "https://staging.example"},
    "staging_start": "make staging",
    **BEFEHLE,
}


def test_leere_einrichtung_legt_t0_vor_alle_tickets(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    welt = e2._welt(weltdatei)
    assert len(welt.get("angelegt", [])) == 1, ausgabe
    assert "pruefung" in welt["angelegt"][0] and "staging" in welt["angelegt"][0]
    eintrag = _manifest(repo)["tickets"][str(T0)]
    assert eintrag["einrichtung"] is True and eintrag["schaetzung_k"] > 0
    # T0 blockiert T1 und die Abnahme, hängt als Unter-Issue an der Spec.
    assert T0 in welt["kanten"]["901"]
    assert T0 in welt["kanten"][str(e2.CHECKPOINT)]
    assert T0 in welt["unter"][str(e2.SPEC)]
    assert f"Einrichtungs-Ticket #{T0} angelegt" in ausgabe


def test_zweiter_aufruf_legt_kein_zweites_t0_an(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    _cli(repo, umgebung, "regularien", str(e2.SPEC))
    posts = list(e2._welt(weltdatei)["posts"])
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    welt = e2._welt(weltdatei)
    assert len(welt["angelegt"]) == 1, lauf.stdout + lauf.stderr
    assert welt["posts"] == posts
    assert f"Einrichtungs-Ticket #{T0} steht" in lauf.stdout


def test_volle_einrichtung_bleibt_still(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path, konfig=VOLL, python=True)
    vorher = e2._manifest(repo).read_bytes()
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    assert "angelegt" not in e2._welt(weltdatei)
    assert e2._manifest(repo).read_bytes() == vorher
    assert "Einrichtung" not in lauf.stdout
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert f"Regularien Spec #{e2.SPEC}" in lauf.stdout


def test_bewusst_ohne_staging_kein_staging_zwang(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path, konfig={"staging": {"ohne": True}, **BEFEHLE}, python=True)
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    assert "angelegt" not in e2._welt(weltdatei), lauf.stdout
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert f"Regularien Spec #{e2.SPEC}" in lauf.stdout


def test_ohne_staging_aber_ohne_pruefung_nur_pruef_t0(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path, konfig={"staging": {"ohne": True}})
    _cli(repo, umgebung, "regularien", str(e2.SPEC))
    titel = e2._welt(weltdatei)["angelegt"]
    assert len(titel) == 1 and "pruefung" in titel[0] and "staging" not in titel[0]


def test_ohne_github_nur_hinweis(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC), "--ohne-github")
    assert "angelegt" not in e2._welt(weltdatei)
    assert "Einrichtung fehlt: pruefung, staging" in lauf.stdout
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert f"Regularien Spec #{e2.SPEC}" in lauf.stdout


def test_spaeter_angehaengtes_ticket_wartet_auf_t0(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    _cli(repo, umgebung, "regularien", str(e2.SPEC))
    lauf = e2._anhaengen(repo, umgebung, *e2.VOLL)
    assert T0 in e2._welt(weltdatei)["kanten"][str(e2.NEU)], lauf.stdout + lauf.stderr


# --- Fix-Runde 1 (Review PR #433) -------------------------------------------


def _welt_setzen(weltdatei: Path, **werte: Any) -> None:
    welt = e2._welt(weltdatei)
    welt.update(werte)
    weltdatei.write_text(json.dumps(welt), encoding="utf-8")


def _offenes_t0(weltdatei: Path, nummer: int, titel: str) -> None:
    welt = e2._welt(weltdatei)
    welt["issues"][str(nummer)] = e2._issue(nummer, titel, labels=["einrichtung"])
    welt.setdefault("labels", []).append("einrichtung")
    weltdatei.write_text(json.dumps(welt), encoding="utf-8")


def _nichts_angelegt(repo: Path, weltdatei: Path, vorher: bytes) -> None:
    welt = e2._welt(weltdatei)
    assert "angelegt" not in welt
    assert welt["posts"] == []
    assert e2._manifest(repo).read_bytes() == vorher


def test_probelauf_legt_nie_ein_issue_an(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    vorher = e2._manifest(repo).read_bytes()
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC), "--dry-run")
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    _nichts_angelegt(repo, weltdatei, vorher)
    assert "würde Einrichtungs-Ticket anlegen" in lauf.stdout
    assert f"Regularien Spec #{e2.SPEC}" in lauf.stdout


def test_starter_reichen_probelauf_und_nur_wache_als_dry_run_weiter() -> None:
    wurzel = e2.CLI.parent
    srv = (wurzel / "skripte" / "spawn_srv.sh").read_text(encoding="utf-8")
    # Fix-Runde 2: --nur-wache ist kein Probelauf mehr, sondern Teil-Start (Weg-Test unten).
    assert 'if [ "$DRY_RUN" -eq 1 ]; then PRUEF_ARGS+=(--dry-run); fi' in srv
    assert 'if [ "$NUR_WACHE" -eq 1 ]; then PRUEF_ARGS+=(--ohne-einrichtung); fi' in srv
    lokal = (wurzel / "spawn_local.ps1").read_text(encoding="utf-8")
    assert 'if ($DryRun) { $pruefArgs += "--dry-run" }' in lokal


def test_spec_mitten_im_bau_bau_log_legt_nichts_an(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    (repo / ".to-spawn" / "bau_log").mkdir(parents=True)
    (repo / ".to-spawn" / "bau_log" / "901.jsonl").write_text("{}\n", encoding="utf-8")
    vorher = e2._manifest(repo).read_bytes()
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    _nichts_angelegt(repo, weltdatei, vorher)
    assert "läuft schon" in lauf.stdout and "#901" in lauf.stdout
    assert f"Regularien Spec #{e2.SPEC}" in lauf.stdout


def test_spec_mit_geschlossenem_ticket_legt_nichts_an(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    welt = e2._welt(weltdatei)
    welt["issues"]["901"]["state"] = "closed"
    weltdatei.write_text(json.dumps(welt), encoding="utf-8")
    vorher = e2._manifest(repo).read_bytes()
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    _nichts_angelegt(repo, weltdatei, vorher)
    assert "läuft schon" in lauf.stdout


def test_ticket_stand_unklar_legt_nichts_an_exit_3(tmp_path: Path) -> None:
    """``gh api`` scheitert beim Ticket-Stand: nichts anlegen und Exit 3 (Review F1 zu 433, #438)."""
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    _welt_setzen(weltdatei, api_scheitert_einmal="issues/901")
    vorher = e2._manifest(repo).read_bytes()
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 3, ausgabe
    _nichts_angelegt(repo, weltdatei, vorher)
    assert "Stand unklar" in lauf.stdout and "#901" in lauf.stdout, ausgabe


def test_ticket_stand_unklar_weigert_mit_exit_3(tmp_path: Path) -> None:
    """Review F1 (433): ein einmaliger ``gh api``-Aussetzer darf T0 nicht still verlieren.

    Sonst startet der Bau ohne T0, danach greift „hat Bau-Log“ und T0 entsteht nie.
    """
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    _welt_setzen(weltdatei, api_scheitert_einmal="issues/901")
    vorher = e2._manifest(repo).read_bytes()
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 3, ausgabe
    _nichts_angelegt(repo, weltdatei, vorher)
    assert "läuft schon" not in lauf.stdout, ausgabe
    assert lauf.stdout.strip().splitlines()[-1].startswith("EINRICHTUNG FEHLT"), ausgabe
    assert "Stand unklar" in lauf.stdout and "#901" in lauf.stdout, ausgabe


def test_ticket_auswahl_legt_nichts_an(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    vorher = e2._manifest(repo).read_bytes()
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC), "--tickets", "901")
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    _nichts_angelegt(repo, weltdatei, vorher)
    assert "--tickets" in lauf.stdout and "nicht angelegt" in lauf.stdout


def test_anlegen_scheitert_meldet_exit_und_schlusszeile(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    _welt_setzen(weltdatei, create_scheitert=True)
    vorher = e2._manifest(repo).read_bytes()
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    assert lauf.returncode == 3, lauf.stdout + lauf.stderr
    assert lauf.stdout.strip().splitlines()[-1].startswith("EINRICHTUNG FEHLT")
    assert e2._manifest(repo).read_bytes() == vorher


def test_t0_traegt_label_einrichtung(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    labels = e2._welt(weltdatei)["issues"][str(T0)]["labels"]
    assert [x["name"] for x in labels] == ["einrichtung"]


def test_abbruch_nach_anlegen_nutzt_offenes_t0(tmp_path: Path) -> None:
    # Issue steht schon (Abbruch vor dem Manifest-Eintrag): kein zweites anlegen.
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    _offenes_t0(weltdatei, 960, f"Einrichtung für Spec #{e2.SPEC}: pruefung, staging")
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    welt = e2._welt(weltdatei)
    assert "angelegt" not in welt, ausgabe
    assert _manifest(repo)["tickets"]["960"]["einrichtung"] is True
    assert 960 in welt["kanten"]["901"]
    assert 960 in welt["unter"][str(e2.SPEC)]


def test_zwei_specs_im_repo_ein_t0(tmp_path: Path) -> None:
    # T0 gilt fürs Repo: Spec 900 hängt sich an das offene T0 einer anderen Spec.
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    _offenes_t0(weltdatei, 970, "Einrichtung für Spec #800: pruefung, staging")
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    welt = e2._welt(weltdatei)
    assert "angelegt" not in welt, ausgabe
    assert _manifest(repo)["tickets"]["970"]["einrichtung"] is True
    assert 970 in welt["kanten"]["901"]
    # GitHub erlaubt nur eine Eltern-Spec: das fremde T0 wird kein Unter-Issue von 900.
    assert 970 not in welt["unter"][str(e2.SPEC)]
    zweiter = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    assert zweiter.returncode == 0, zweiter.stdout + zweiter.stderr
    assert "angelegt" not in e2._welt(weltdatei)


def test_hat_staging_ist_die_eine_stelle() -> None:
    from to_spawn import config

    assert config.hat_staging({"staging": {"url": "https://s.example"}})
    assert config.hat_staging({"staging_start": "make staging"})
    assert not config.hat_staging({"staging": {"url": " "}, "staging_start": ""})
    assert not config.hat_staging({"staging": {"ohne": True, "url": "https://s"}})
    assert config.staging_bewusst_ohne({"staging": {"ohne": True}})
    assert not config.staging_bewusst_ohne({"staging": "kaputt"})


# --- Fix-Runde 2 (Review PR #433) -------------------------------------------


def test_bau_log_nur_im_worktree_legt_nichts_an(tmp_path: Path) -> None:
    # Die Hooks schreiben das Bau-Log zuerst in den Ticket-Worktree (bau_log.log_ort).
    from to_spawn import bau_log

    wt_basis = tmp_path / "wt"
    repo, weltdatei, umgebung = _aufbau(tmp_path, konfig={"worktree_basis": wt_basis.as_posix()})
    log = bau_log.lauf_pfad(wt_basis / "wt-901", 901)
    log.parent.mkdir(parents=True)
    log.write_text("{}\n", encoding="utf-8")
    vorher = e2._manifest(repo).read_bytes()
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    _nichts_angelegt(repo, weltdatei, vorher)
    assert "läuft schon" in lauf.stdout and "#901 hat Bau-Log" in lauf.stdout, ausgabe


def test_ohne_einrichtung_nur_hinweis_aber_konfig_angelegt(tmp_path: Path) -> None:
    # --nur-wache (kein Ticket-Fenster): nur Hinweis wie --tickets, aber kein Probelauf —
    # config.sicherstellen legt auf frischem Repo Konfig + .gitignore-Block an.
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    vorher = e2._manifest(repo).read_bytes()
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC), "--ohne-einrichtung")
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    _nichts_angelegt(repo, weltdatei, vorher)
    assert "nicht angelegt" in lauf.stdout and "[trocken]" not in lauf.stdout, ausgabe
    assert (repo / ".to-spawn" / "config.json").is_file()
    assert ".to-spawn" in (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert not (repo / ".gitignore").exists()


def test_t0_nicht_lesbar_meldet_warnung(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    _cli(repo, umgebung, "regularien", str(e2.SPEC))
    _welt_setzen(weltdatei, api_scheitert_einmal=f"issues/{T0}")
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert f"Einrichtungs-Ticket #{T0} nicht lesbar" in ausgabe, ausgabe


def _fake_tmux(ordner: Path) -> Path:
    """Attrappe für ``tmux`` im PATH (Linux + Windows): zeichnet auf, startet nichts (#731).

    Unter Windows ruft sowohl Git-Bash (``spawn_srv.sh``) als auch Python
    (``to_spawn.py sitzung``/``speicher``) ``tmux`` über ``PATH`` auf:
    - eine reine Bash-Datei scheitert in Python mit ``WinError 2``, worauf ``speicher.py``
      auf „zähle den ganzen Rechner“ zurückfällt;
    - eine endungslose Python-Datei *neben* ``tmux.exe`` im selben ``PATH``-Ordner wird
      von Git-Bash vor ``tmux.exe`` gefunden und ohne passenden Shebang als Shell-Skript
      ausgeführt (``import: command not found``).
    Darum liegt unter Windows nur ``tmux.exe`` (über ``windows_starter``) im ``PATH``-Ordner,
    das Python-Skript selbst in einem Unterordner außerhalb von ``PATH``.
    """
    from windows_attrappen import windows_starter

    from conftest import attrappe

    ordner.mkdir(parents=True, exist_ok=True)
    aufrufe = ordner / "tmux_aufrufe.txt"
    fenster = ordner / "tmux_fenster.txt"
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        f"aufrufe = Path({str(aufrufe)!r})\n"
        f"fenster = Path({str(fenster)!r})\n"
        "with aufrufe.open('a', encoding='utf-8') as f:\n"
        "    f.write(' '.join(args) + '\\n')\n"
        "cmd = args[0] if args else ''\n"
        "vorhanden = [z for z in fenster.read_text(encoding='utf-8').splitlines() if z] if fenster.exists() else []\n"
        "if cmd == 'has-session':\n"
        "    raise SystemExit(0 if vorhanden else 1)\n"
        "if cmd == 'list-windows':\n"
        "    if not vorhanden:\n"
        "        raise SystemExit(1)\n"
        "    print('\\n'.join(vorhanden))\n"
        "    raise SystemExit(0)\n"
        "if cmd in ('new-session', 'new-window') and '-n' in args:\n"
        "    idx = args.index('-n') + 1\n"
        "    if idx < len(args):\n"
        "        with fenster.open('a', encoding='utf-8') as f:\n"
        "            f.write(args[idx] + '\\n')\n"
    )
    if sys.platform == "win32":
        # Skript außerhalb des PATH-Ordners, im PATH liegt nur tmux.exe — so treffen
        # sowohl Git-Bash als auch Python (CreateProcess/PATHEXT) direkt die .exe.
        skript = attrappe(ordner / "_py", "tmux", code)
        windows_starter(skript).replace(ordner / "tmux.exe")
    else:
        attrappe(ordner, "tmux", code)
    return aufrufe


def _fake_powershell_leer(ordner: Path) -> None:
    """Attrappe für ``powershell`` im PATH: leere Prozessliste für ``sessions_stand.py`` auf Windows (#232).

    Unter Windows liest ``sessions_stand.py`` alle Prozesse per WMI ohne Arbeitsordner-Filter;
    parallele pytest-xdist-Worker mit ``bau.py 901``/``wache.py 900`` ließen sonst ``spawn_srv.sh``
    fälschlich „läuft bereits“ annehmen und Fenster überspringen.
    """
    from windows_attrappen import windows_starter

    from conftest import attrappe

    windows_starter(attrappe(ordner, "powershell", "print('[]')\n"))


def _fake_ps_leer(ordner: Path) -> None:
    """Attrappe für ``ps`` im PATH: leere Bau-/Aufseher-Liste für ``sessions_stand.py`` auf POSIX (#731).

    Spiegel zu :func:`_fake_powershell_leer`: fremde Prozesse mit unlesbarem Arbeitsordner
    (``ordner=None``) zählten sonst als eigene und ließen ``spawn_srv.sh`` Fenster überspringen.
    """
    if sys.platform == "win32":
        return
    from conftest import attrappe

    attrappe(
        ordner,
        "ps",
        "print('1 0 Mon Jan  1 00:00:00 2026 init /sbin/init')\n",
    )


def _spawn_srv_weg(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], Path, Path, bytes, Path]:
    from to_spawn import bash_wahl

    repo, weltdatei, umgebung = _aufbau(
        tmp_path,
        konfig={
            "hauptzweig": "main",
            "worktree_basis": (tmp_path / "wt").as_posix(),
            "speicher": {"staffel_s": 0},
        },
    )
    aufrufe = _fake_tmux(tmp_path / "bin")
    _fake_powershell_leer(tmp_path / "bin")
    _fake_ps_leer(tmp_path / "bin")
    umgebung.update(
        {
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{umgebung['PATH']}",
            "TO_SPAWN_REPO": str(repo),
            "TO_SPAWN_TEST_BIN": str(tmp_path / "bin"),
            # git fetch auf die Attrappen-URL bleibt lokal: nur file-Protokoll erlaubt.
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
            # #353: Fake-tmux startet kein echtes bau.py (schreibt kein fenster_<N>.log) → 2-s-Prüfwartezeit sparen.
            "TO_SPAWN_ANLAUF_PRUEF_S": "0",
        }
    )
    vorher = e2._manifest(repo).read_bytes()
    lauf = bash_wahl.lauf(
        [str(e2.CLI.parent / "skripte" / "spawn_srv.sh"), str(e2.SPEC), *args],
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    return lauf, repo, weltdatei, vorher, aufrufe


def test_weg_spawn_srv_nur_wache_tickets_probelauf_legen_kein_t0_an(
    tmp_path: Path,
) -> None:
    for i, args in enumerate((("--nur-wache",), ("--tickets", "901"), ("--dry-run",))):
        (tmp_path / str(i)).mkdir()
        lauf, repo, weltdatei, vorher, aufrufe = _spawn_srv_weg(tmp_path / str(i), *args)
        ausgabe = lauf.stdout + lauf.stderr
        assert "== Regularien ==" in ausgabe, ausgabe
        assert f"Regularien Spec #{e2.SPEC}" in ausgabe, ausgabe
        assert "Einrichtung fehlt: pruefung, staging" in ausgabe, ausgabe
        _nichts_angelegt(repo, weltdatei, vorher)
        assert lauf.returncode == 0, (args, ausgabe)
        gestartet = aufrufe.read_text(encoding="utf-8") if aufrufe.exists() else ""
        if args == ("--nur-wache",):
            assert "[trocken]" not in ausgabe, ausgabe
            assert f"-n wache {e2.SPEC} " in gestartet, (gestartet, ausgabe)
            assert "bau 901" not in gestartet, (gestartet, ausgabe)
        if args == ("--tickets", "901"):
            # Start-Nachweis (#438): das Fenster bau 901 startet tatsächlich.
            assert "-n bau 901 " in gestartet, (gestartet, ausgabe)
        if args == ("--dry-run",):
            assert "new-session" not in gestartet and "new-window" not in gestartet


def test_weg_spawn_srv_ganze_spec_legt_t0_an(tmp_path: Path) -> None:
    # Gegenprobe: derselbe Weg ohne Schalter legt das Einrichtungs-Ticket an und startet die Fenster.
    lauf, _repo, weltdatei, _vorher, aufrufe = _spawn_srv_weg(tmp_path)
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert len(e2._welt(weltdatei).get("angelegt", [])) == 1, ausgabe
    gestartet = aufrufe.read_text(encoding="utf-8") if aufrufe.exists() else ""
    assert "-n bau 901 " in gestartet, gestartet
    assert f"-n bau {T0} " in gestartet, gestartet


def test_spawn_srv_weg_isoliert_windows_prozessliste(tmp_path: Path) -> None:
    """``_spawn_srv_weg`` stellt ``powershell`` im PATH mit leerer Prozessliste (#232).

    Unter Windows liest ``sessions_stand.py`` sonst alle Prozesse der Maschine ohne
    Arbeitsordner-Filter; parallele xdist-Worker mit ``bau.py 901``/``wache.py 900``
    lassen ``spawn_srv.sh`` dann Fenster überspringen.
    """
    lauf, *_ = _spawn_srv_weg(tmp_path, "--dry-run")
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    ps = tmp_path / "bin" / ("powershell.exe" if sys.platform == "win32" else "powershell")
    assert ps.is_file(), f"PowerShell-Attrappe fehlt unter {ps}"
    probe = subprocess.run(
        [str(ps), "-NoProfile", "-Command", "Get-CimInstance Win32_Process"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    assert json.loads(probe.stdout) == []


def _fremde_claude_sessions(ordner: Path, anzahl: int) -> Path:
    """Attrappen-``/proc`` mit ``anzahl`` fremden Claude-Sessions (andere tmux-Sitzung, #731)."""
    ordner.mkdir(parents=True, exist_ok=True)
    for pid in range(2000, 2000 + anzahl):
        eintrag = ordner / str(pid)
        eintrag.mkdir()
        (eintrag / "cmdline").write_bytes(b"claude\0")
        (eintrag / "environ").write_bytes(b"TMUX=/tmp/fremd-tmux,1,99\0")
    return ordner


def test_spawn_srv_weg_mit_9_fremden_sessions_und_portabler_tmux_attrappe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#731: 9 fremde Claude-Sessions dürfen ``--nur-wache`` nicht blockieren; ``tmux`` muss auch für Python unter Windows laufen.

    Unter Windows scheiterte ``to_spawn.py speicher`` an der reinen Bash-Attrappe ``tmux``
    (WinError 2), fiel auf „zähle den ganzen Rechner“ zurück und ließ 9 fremde Sessions
    gegen ``max_sessions=6`` laufen statt gegen die Spec-Sitzung.
    """
    monkeypatch.setenv(
        "TO_SPAWN_SPEICHER_PROC",
        str(_fremde_claude_sessions(tmp_path / "proc9", 9)),
    )
    lauf_ordner = tmp_path / "lauf"
    lauf_ordner.mkdir()
    lauf, _repo, _weltdatei, _vorher, aufrufe = _spawn_srv_weg(lauf_ordner, "--nur-wache")
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "zähle den ganzen Rechner" not in ausgabe, ausgabe
    assert "schon 9 Claude-Sessions" not in ausgabe, ausgabe
    gestartet = aufrufe.read_text(encoding="utf-8") if aufrufe.exists() else ""
    assert f"-n wache {e2.SPEC} " in gestartet, (gestartet, ausgabe)
    assert "bau 901" not in gestartet, (gestartet, ausgabe)
    # Cross-Plattform-Nachweis: unter Windows liegt nur tmux.exe im PATH (kein endungsloses
    # tmux, das Git-Bash vor tmux.exe als Shell-Skript startet); auf POSIX ein Python-Skript
    # ohne Bash-Shebang (das Python unter Windows nicht starten könnte).
    if sys.platform == "win32":
        assert not (lauf_ordner / "bin" / "tmux").exists()
        assert not (lauf_ordner / "bin" / "tmux.cmd").exists()
        tmux_start = lauf_ordner / "bin" / "tmux.exe"
    else:
        tmux_start = lauf_ordner / "bin" / "tmux"
        assert not tmux_start.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
    assert tmux_start.is_file(), f"tmux-Attrappe fehlt unter {tmux_start}"
    probe = subprocess.run(
        [str(tmux_start), "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout == ""


def test_spawn_srv_weg_isoliert_fremde_wache_in_prozessliste(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#731: eine fremde ``wache.py 900`` im PATH-``ps``/``powershell`` darf ``wache 900`` nicht überspringen lassen."""
    from windows_attrappen import windows_starter

    from conftest import attrappe

    fremd_bin = tmp_path / "fremd-bin"
    if sys.platform == "win32":
        fremd_json = json.dumps(
            [
                {
                    "ProcessId": os.getpid(),
                    "ParentProcessId": 0,
                    "Name": "python.exe",
                    "CommandLine": f"python.exe skripte/wache.py {e2.SPEC}",
                    "Start": "",
                }
            ]
        )
        windows_starter(attrappe(fremd_bin, "powershell", f"print({fremd_json!r})\n"))
    else:
        # PID 1 (init) lebt, hat für normale Nutzer keinen lesbaren /proc/1/cwd → ordner_fremd=False.
        attrappe(
            fremd_bin,
            "ps",
            f"print('1 0 Mon Jan  1 00:00:00 2026 python3 python3 skripte/wache.py {e2.SPEC}')\n",
        )
    monkeypatch.setenv("PATH", f"{fremd_bin}{os.pathsep}{os.environ['PATH']}")

    lauf_ordner = tmp_path / "lauf"
    lauf_ordner.mkdir()
    lauf, _repo, _weltdatei, _vorher, aufrufe = _spawn_srv_weg(lauf_ordner, "--nur-wache")
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    gestartet = aufrufe.read_text(encoding="utf-8") if aufrufe.exists() else ""
    assert f"-n wache {e2.SPEC} " in gestartet, (gestartet, ausgabe)


def test_fake_tmux_windows_legt_nur_exe_in_path_ohne_endungslosen_schatten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#731 Fix 2: im PATH-Ordner darf unter Windows nur ``tmux.exe`` liegen.

    Liegt daneben ein endungsloses Python-Skript ``tmux`` ohne Shebang im selben
    ``PATH``-Ordner, findet Git-Bash es vor ``tmux.exe`` und führt es als Shell-Skript
    aus (``import: command not found``, Exit 2).
    """
    import windows_attrappen

    binaer = tmp_path / "bin"
    starter_ziele: list[Path] = []

    def _stub_starter(skript: Path) -> Path:
        starter_ziele.append(skript)
        exe = skript.with_name(skript.name + ".exe")
        exe.write_bytes(b"MZ-attrappe")
        return exe

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(windows_attrappen, "windows_starter", _stub_starter)

    _fake_tmux(binaer)

    assert (binaer / "tmux.exe").is_file()
    assert not (binaer / "tmux").exists(), (
        "endungsloses bin/tmux verdeckt tmux.exe in Git-Bash und scheitert als Shell-Skript"
    )
    assert not (binaer / "tmux.cmd").exists()
    assert len(starter_ziele) == 1 and starter_ziele[0].is_file()
    assert starter_ziele[0].parent != binaer


def test_fake_tmux_laeuft_ueber_path_aus_bash_und_python(tmp_path: Path) -> None:
    """#731 Fix 2: ``tmux`` aus :func:`_fake_tmux` läuft über ``PATH`` sowohl in bash als auch in Python."""
    import shutil

    from to_spawn import bash_wahl

    binaer = tmp_path / "bin"
    aufrufe = _fake_tmux(binaer)
    umgebung = {**os.environ, "PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}"}

    bash_lauf = bash_wahl.lauf(
        ["-c", 'command -v tmux >/dev/null && tmux new-session -d -s s -n "wache 900"'],
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert bash_lauf.returncode == 0, bash_lauf.stdout + bash_lauf.stderr

    gefunden = shutil.which("tmux", path=str(binaer))
    assert gefunden is not None
    py_lauf = subprocess.run(
        [gefunden, "list-sessions", "-F", "#{session_name}"],
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert py_lauf.returncode == 0, py_lauf.stderr
    assert py_lauf.stdout == ""
    protokoll = aufrufe.read_text(encoding="utf-8")
    assert "-n wache 900" in protokoll
    assert "list-sessions -F #{session_name}" in protokoll


def test_spawn_srv_weg_ohne_feste_wartezeiten_und_mit_anlauf_fenster_nachweis(tmp_path: Path) -> None:
    """#353: ``_spawn_srv_weg`` schläft keine 20-s-Staffel-Pausen, und ``_fake_tmux`` meldet gestartete Fenster
    bei ``list-windows``, damit ``anlauf_bereit`` beim ersten Takt endet statt 50× ins 10-s-Limit zu laufen."""
    import shutil

    binaer = tmp_path / "bin"
    _fake_tmux(binaer)
    tmux_bin = shutil.which("tmux", path=str(binaer))
    assert tmux_bin is not None
    umgebung = {**os.environ, "PATH": f"{binaer}{os.pathsep}{os.environ['PATH']}"}

    def _tmux(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [tmux_bin, *args],
            env=umgebung,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=30,
        )

    assert _tmux("has-session", "-t", "=spec-900").returncode == 1
    assert _tmux("list-windows", "-t", "=spec-900", "-F", "#W").returncode == 1

    assert _tmux("new-session", "-d", "-s", "spec-900", "-n", "wache 900").returncode == 0
    assert _tmux("new-window", "-t", "=spec-900", "-n", "bau 901").returncode == 0
    assert _tmux("has-session", "-t", "=spec-900").returncode == 0
    fenster = _tmux("list-windows", "-t", "=spec-900", "-F", "#W")
    assert fenster.returncode == 0, fenster.stderr
    assert fenster.stdout.splitlines() == ["wache 900", "bau 901"], fenster.stdout

    weg_ordner = tmp_path / "weg"
    weg_ordner.mkdir()
    lauf, *_ = _spawn_srv_weg(weg_ordner, "--dry-run")
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert "Staffel: 0 s" in ausgabe, ausgabe
