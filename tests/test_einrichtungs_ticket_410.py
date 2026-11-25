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
    if (
        args[:1] == ["api"]
        and (stelle := welt.get("api_scheitert_einmal"))
        and args[1].endswith(stelle)
    ):
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


def _aufbau(
    tmp_path: Path, *, konfig: dict[str, Any] | None = None, python: bool = False
) -> tuple[Path, Path, dict[str, str]]:
    repo, weltdatei, umgebung = e2._aufbau(tmp_path)
    umgebung["TO_SPAWN_GH_STUB"] = str(Path(__file__).resolve())
    if konfig is not None:
        (repo / ".to-spawn").mkdir()
        (repo / ".to-spawn" / "config.json").write_text(
            json.dumps(konfig), encoding="utf-8"
        )
    if python:
        (repo / "pyproject.toml").write_text(
            "[project]\nname = 'x'\n", encoding="utf-8"
        )
    return repo, weltdatei, umgebung


def _cli(
    repo: Path, umgebung: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
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
    repo, weltdatei, umgebung = _aufbau(
        tmp_path, konfig={"staging": {"ohne": True}, **BEFEHLE}, python=True
    )
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
    repo, weltdatei, umgebung = _aufbau(
        tmp_path, konfig={"worktree_basis": wt_basis.as_posix()}
    )
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
    assert ".to-spawn" in (repo / ".gitignore").read_text(encoding="utf-8")


def test_t0_nicht_lesbar_meldet_warnung(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    _cli(repo, umgebung, "regularien", str(e2.SPEC))
    _welt_setzen(weltdatei, api_scheitert_einmal=f"issues/{T0}")
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    ausgabe = lauf.stdout + lauf.stderr
    assert lauf.returncode == 0, ausgabe
    assert f"Einrichtungs-Ticket #{T0} nicht lesbar" in ausgabe, ausgabe


def _fake_tmux(ordner: Path) -> Path:
    """Attrappe für tmux im PATH: zeichnet auf, startet nichts (keine Session da)."""
    ordner.mkdir(parents=True, exist_ok=True)
    aufrufe = ordner / "tmux_aufrufe.txt"
    tmux = ordner / "tmux"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {str(aufrufe)!r}\n'
        'case "$1" in has-session|list-windows) exit 1 ;; esac\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(0o755)
    return aufrufe


def _spawn_srv_weg(
    tmp_path: Path, *args: str
) -> tuple[subprocess.CompletedProcess[str], Path, Path, bytes, Path]:
    from to_spawn import bash_wahl

    repo, weltdatei, umgebung = _aufbau(
        tmp_path,
        konfig={"hauptzweig": "main", "worktree_basis": (tmp_path / "wt").as_posix()},
    )
    aufrufe = _fake_tmux(tmp_path / "bin")
    umgebung.update(
        {
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{umgebung['PATH']}",
            "TO_SPAWN_REPO": str(repo),
            "TO_SPAWN_TEST_BIN": str(tmp_path / "bin"),
            # git fetch auf die Attrappen-URL bleibt lokal: nur file-Protokoll erlaubt.
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
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
        lauf, repo, weltdatei, vorher, aufrufe = _spawn_srv_weg(
            tmp_path / str(i), *args
        )
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
            assert "bau 901" not in gestartet, gestartet
        if args == ("--tickets", "901"):
            # Start-Nachweis (#438): das Fenster bau 901 startet tatsächlich.
            assert "-n bau 901 " in gestartet, gestartet
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


if __name__ == "__main__":
    sys.exit(_stub_main(sys.argv[1:]))
