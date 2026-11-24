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
    if args[:2] != ["issue", "create"]:
        return e2._stub_main(args)
    pfad = Path(os.environ["E2_GH_WELT"])
    welt = json.loads(pfad.read_text(encoding="utf-8"))
    nummer = T0 + len(welt.setdefault("angelegt", []))
    titel = args[args.index("--title") + 1]
    welt["issues"][str(nummer)] = e2._issue(nummer, titel)
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


VOLL = {"staging": {"url": "https://staging.example"}, "staging_start": "make staging"}


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


def test_bewusst_ohne_staging_kein_staging_zwang(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(
        tmp_path, konfig={"staging": {"ohne": True}}, python=True
    )
    lauf = _cli(repo, umgebung, "regularien", str(e2.SPEC))
    assert "angelegt" not in e2._welt(weltdatei), lauf.stdout


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


def test_spaeter_angehaengtes_ticket_wartet_auf_t0(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    _cli(repo, umgebung, "regularien", str(e2.SPEC))
    lauf = e2._anhaengen(repo, umgebung, *e2.VOLL)
    assert T0 in e2._welt(weltdatei)["kanten"][str(e2.NEU)], lauf.stdout + lauf.stderr


if __name__ == "__main__":
    sys.exit(_stub_main(sys.argv[1:]))
