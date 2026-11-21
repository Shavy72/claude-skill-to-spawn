"""Weg-Tests E2-2: Tür ``anhaengen`` hängt ein Issue an eine laufende Spec.

Echt laufen: CLI (``to_spawn.py anhaengen``), Manifest, Git-Repo, Manifest-Prüfung.
Gestellt ist nur die ``gh``-CLI (GitHub ist ein externer Dienst): Diese Datei ist
zugleich das Ersatz-Skript (``TO_SPAWN_GH_STUB`` zeigt auf sie). Es antwortet mit
aufgezeichneten GitHub-Antworten (Form der REST-API: ``id``, ``number``, ``state``,
``labels``, ``repository_url``) aus einer Welt-Datei und schreibt jeden POST dorthin
zurück — so sieht ein zweiter Aufruf, was der erste angelegt hat.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
SLUG = "wegwerf/repo"
API = "https://api.github.com/repos"
SPEC = 900
CHECKPOINT = 909
NEU = 905
ANDERES_REPO = 906


def _issue(
    nummer: int,
    titel: str,
    *,
    labels: list[str] | None = None,
    body: str = "",
    state: str = "open",
    slug: str = SLUG,
) -> dict[str, Any]:
    """Aufgezeichnete Form von ``GET repos/<slug>/issues/<N>`` (gekürzt)."""
    return {
        "id": 3_000_000_000 + nummer,
        "node_id": f"I_kwDO{nummer}",
        "number": nummer,
        "title": titel,
        "state": state,
        "labels": [{"id": 1, "name": name} for name in labels or []],
        "assignees": [],
        "body": body,
        "repository_url": f"{API}/{slug}",
        "html_url": f"https://github.com/{slug}/issues/{nummer}",
    }


def _welt_anfang() -> dict[str, Any]:
    return {
        "issues": {
            str(SPEC): _issue(SPEC, "Spec: Wegwerf"),
            "901": _issue(901, "Kern bauen"),
            str(CHECKPOINT): _issue(
                CHECKPOINT,
                "Abnahme",
                labels=["checkpoint:human"],
                body="## Blocked by\n#901",
            ),
            str(NEU): _issue(NEU, "Nachtrag: Rand", body="## Blocked by\n#901"),
            # Umgezogenes Issue: GitHub leitet um, die Antwort trägt das fremde Repo.
            str(ANDERES_REPO): _issue(ANDERES_REPO, "Fremd", slug="fremd/anders"),
        },
        "unter": {str(SPEC): [901, CHECKPOINT]},
        "kanten": {str(CHECKPOINT): [901]},
        "posts": [],
    }


# --- Ersatz-Skript für gh (läuft als eigener Prozess) -----------------------------


def _stub_main(args: list[str]) -> int:
    pfad = Path(os.environ["E2_GH_WELT"])
    welt = json.loads(pfad.read_text(encoding="utf-8"))
    issues: dict[str, Any] = welt["issues"]

    def kurz(nummer: int) -> dict[str, Any]:
        daten = issues[str(nummer)]
        return {
            k: daten[k] for k in ("id", "number", "title", "state", "repository_url")
        }

    if args[:2] == ["issue", "view"]:
        daten = issues.get(args[2])
        if daten is None:
            print("GraphQL: Could not resolve to an issue", file=sys.stderr)
            return 1
        felder = args[args.index("--json") + 1].split(",")
        antwort = {f: daten[f] for f in felder if f in daten}
        antwort["state"] = daten["state"].upper()
        print(json.dumps(antwort))
        return 0
    if args[:1] != ["api"]:
        print(f"Stub kennt den Aufruf nicht: {args}", file=sys.stderr)
        return 2
    if args[1:3] == ["-X", "POST"]:
        ziel, feld = args[3], args[5]
        welt["posts"].append(" ".join(args[3:]))
        name, _, wert = feld.partition("=")
        quelle = next(n for n, d in issues.items() if d["id"] == int(wert))
        if treffer := re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\d+)/sub_issues", ziel):
            assert name == "sub_issue_id"
            welt["unter"].setdefault(treffer.group(1), []).append(int(quelle))
        elif treffer := re.fullmatch(
            r"repos/[^/]+/[^/]+/issues/(\d+)/dependencies/blocked_by", ziel
        ):
            assert name == "issue_id"
            welt["kanten"].setdefault(treffer.group(1), []).append(int(quelle))
        else:
            return 2
        pfad.write_text(json.dumps(welt), encoding="utf-8")
        print(json.dumps(kurz(int(quelle))))
        return 0
    ziel = args[1]
    if treffer := re.fullmatch(r"repos/([^/]+/[^/]+)/issues/(\d+)", ziel):
        daten = issues.get(treffer.group(2))
        if daten is None or treffer.group(1) != SLUG:
            print('{"message":"Not Found","status":"404"}')
            return 1
        print(json.dumps(daten))
        return 0
    if treffer := re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\d+)/sub_issues", ziel):
        liste = welt["unter"].get(treffer.group(1), [])
        if "--jq" in args:
            print("\n".join(str(n) for n in liste))
        else:
            print(json.dumps([kurz(n) for n in liste]))
        return 0
    if treffer := re.fullmatch(
        r"repos/[^/]+/[^/]+/issues/(\d+)/dependencies/blocked_by", ziel
    ):
        print(json.dumps([kurz(n) for n in welt["kanten"].get(treffer.group(1), [])]))
        return 0
    print(f"Stub kennt den Pfad nicht: {ziel}", file=sys.stderr)
    return 2


# --- Tests -------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def _aufbau(
    tmp_path: Path, welt: dict[str, Any] | None = None
) -> tuple[Path, Path, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "remote", "add", "origin", f"https://github.com/{SLUG}.git")
    ordner = repo / "docs" / "agents" / "manifests"
    ordner.mkdir(parents=True)
    tickets = {
        "901": {"title": "Kern bauen", "schaetzung_k": 80, "umfang": "Kern bauen."},
        str(CHECKPOINT): {
            "title": "Abnahme",
            "schaetzung_k": 20,
            "umfang": "David prüft.",
        },
    }
    (ordner / f"spec-{SPEC}.json").write_text(
        json.dumps(
            {"spec": SPEC, "feature": "wegwerf", "tickets": tickets}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    weltdatei = tmp_path / "welt.json"
    weltdatei.write_text(json.dumps(welt or _welt_anfang()), encoding="utf-8")
    umgebung = {
        k: v for k, v in os.environ.items() if not k.startswith(("GH_STUB", "TO_SPAWN"))
    }
    umgebung.update(
        {
            "TO_SPAWN_GH_STUB": str(Path(__file__).resolve()),
            "E2_GH_WELT": str(weltdatei),
        }
    )
    return repo, weltdatei, umgebung


def _anhaengen(
    repo: Path, umgebung: dict[str, str], *extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "anhaengen", str(SPEC), str(NEU), *extra],
        check=False,
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


VOLL = (
    "--blockiert-von",
    "901",
    "--schaetzung",
    "40",
    "--umfang",
    "Rand nachziehen.",
    "--datei",
    "to_spawn/spec_rand.py:1-40",
)


def _manifest(repo: Path) -> Path:
    return repo / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"


def _welt(datei: Path) -> dict[str, Any]:
    return json.loads(datei.read_text(encoding="utf-8"))


def test_anhaengen_setzt_kanten_manifest_und_unter_issue(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    lauf = _anhaengen(repo, umgebung, *VOLL)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    welt = _welt(weltdatei)
    neu_id = 3_000_000_000 + NEU
    assert (
        f"repos/{SLUG}/issues/{SPEC}/sub_issues -F sub_issue_id={neu_id}"
        in welt["posts"]
    )
    assert NEU in welt["unter"][str(SPEC)]
    assert welt["kanten"][str(NEU)] == [901]
    # Kante vor das Abnahme-Ticket: checkpoint:human wartet jetzt auch auf den Nachtrag.
    assert NEU in welt["kanten"][str(CHECKPOINT)]
    # Unter-Issue zuletzt: erst damit sieht der Aufpasser das Ticket.
    assert "sub_issues" in welt["posts"][-1]
    eintrag = json.loads(_manifest(repo).read_text(encoding="utf-8"))["tickets"][
        str(NEU)
    ]
    assert eintrag == {
        "title": "Nachtrag: Rand",
        "schaetzung_k": 40,
        "umfang": "Rand nachziehen.",
        "files": ["to_spawn/spec_rand.py:1-40"],
    }
    assert "alles erfüllt" in lauf.stdout, lauf.stdout


def test_zweiter_aufruf_aendert_nichts(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    assert _anhaengen(repo, umgebung, *VOLL).returncode == 0
    posts_vorher = len(_welt(weltdatei)["posts"])
    bytes_vorher = _manifest(repo).read_bytes()
    lauf = _anhaengen(repo, umgebung, *VOLL)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert len(_welt(weltdatei)["posts"]) == posts_vorher
    assert _manifest(repo).read_bytes() == bytes_vorher
    assert "nichts zu tun" in lauf.stdout


def test_geschlossene_spec_bricht_ohne_schreiben_ab(tmp_path: Path) -> None:
    welt = _welt_anfang()
    welt["issues"][str(SPEC)]["state"] = "closed"
    repo, weltdatei, umgebung = _aufbau(tmp_path, welt)
    vorher = _manifest(repo).read_bytes()
    lauf = _anhaengen(repo, umgebung, *VOLL)
    assert lauf.returncode != 0
    assert "geschlossen" in lauf.stdout
    assert _welt(weltdatei)["posts"] == []
    assert _manifest(repo).read_bytes() == vorher


def test_issue_im_falschen_repo_bricht_ohne_schreiben_ab(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    vorher = _manifest(repo).read_bytes()
    lauf = subprocess.run(
        [sys.executable, str(CLI), "anhaengen", str(SPEC), str(ANDERES_REPO), *VOLL],
        check=False,
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert lauf.returncode != 0
    assert "fremd/anders" in lauf.stdout
    assert _welt(weltdatei)["posts"] == []
    assert _manifest(repo).read_bytes() == vorher


def test_trocken_zeigt_schritte_ohne_schreiben(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    vorher = _manifest(repo).read_bytes()
    lauf = _anhaengen(repo, umgebung, *VOLL, "--trocken")
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert _welt(weltdatei)["posts"] == []
    assert _manifest(repo).read_bytes() == vorher
    for erwartet in ("Unter-Issue", f"#{CHECKPOINT}", "Manifest"):
        assert erwartet in lauf.stdout, lauf.stdout


def test_neuer_eintrag_ohne_schaetzung_bricht_ab(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    lauf = _anhaengen(repo, umgebung, "--blockiert-von", "901")
    assert lauf.returncode != 0
    assert "--schaetzung" in lauf.stdout
    assert _welt(weltdatei)["posts"] == []


if __name__ == "__main__":
    sys.exit(_stub_main(sys.argv[1:]))
