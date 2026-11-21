"""Weg-Tests E2-4 (App #1388, E25/E56): Folge-Tickets nur über ``befund --spec``,
„Spec schließen“ nur über ``to_spawn.py spec-schliessen`` mit Sperre.

Echt laufen: CLI (``to_spawn.py befund``/``spec-schliessen``/``hook-spec-sperre``),
Manifest, Git-Repo, ``spec_rand.anhaengen``, der Hook-Befehl aus ``bau.staffel_hooks()``.
Gestellt ist nur die ``gh``-CLI (GitHub = externer Dienst): diese Datei ist zugleich
das Ersatz-Skript (``TO_SPAWN_GH_STUB``). Es antwortet in Form der REST-API aus einer
Welt-Datei und schreibt jedes Anlegen/Schließen/POST dorthin zurück.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
CLI = SKILL / "to_spawn.py"
SLUG = "wegwerf/repo"
API = "https://api.github.com/repos"
SPEC = 900
TICKET = 901
ANDERES = 902


def _issue(nummer: int, titel: str, *, state: str = "open") -> dict[str, Any]:
    return {
        "id": 3_000_000_000 + nummer,
        "number": nummer,
        "title": titel,
        "state": state,
        "state_reason": None,
        "labels": [],
        "body": "",
        "repository_url": f"{API}/{SLUG}",
    }


# --- Ersatz-Skript für gh (läuft als eigener Prozess) -----------------------------


def _stub_main(args: list[str]) -> int:
    pfad = Path(os.environ["E24_GH_WELT"])
    welt = json.loads(pfad.read_text(encoding="utf-8"))
    issues: dict[str, Any] = welt["issues"]

    def speichern() -> None:
        pfad.write_text(json.dumps(welt), encoding="utf-8")

    if args[:2] == ["issue", "create"]:
        neu = max(int(n) for n in issues) + 1
        titel = args[args.index("--title") + 1]
        issues[str(neu)] = _issue(neu, titel) | {"body": args[args.index("--body") + 1]}
        speichern()
        print(f"https://github.com/{SLUG}/issues/{neu}")
        return 0
    if args[:2] == ["issue", "close"]:
        issues[args[2]]["state"] = "closed"
        issues[args[2]]["state_reason"] = "completed"
        welt.setdefault("geschlossen", []).append(int(args[2]))
        speichern()
        return 0
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
    # Fehler-Schalter: GitHub antwortet mit Fehler (Netz weg, 5xx).
    if os.environ.get("E24_GH_API_FEHLER"):
        print("HTTP 502: Bad Gateway", file=sys.stderr)
        return 1
    if args[1:3] == ["-X", "POST"] and os.environ.get("E24_GH_POST_FEHLER"):
        print("HTTP 502: Bad Gateway", file=sys.stderr)
        return 1
    if args[1:3] == ["-X", "POST"]:
        ziel, feld = args[3], args[5]
        name, _, wert = feld.partition("=")
        quelle = next(n for n, d in issues.items() if d["id"] == int(wert))
        if treffer := re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\d+)/sub_issues", ziel):
            welt["unter"].setdefault(treffer.group(1), []).append(int(quelle))
        elif treffer := re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\d+)/dependencies/blocked_by", ziel):
            welt["kanten"].setdefault(treffer.group(1), []).append(int(quelle))
        else:
            return 2
        speichern()
        print(json.dumps({"number": int(quelle)}))
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
        print("\n".join(str(n) for n in welt["unter"].get(treffer.group(1), [])))
        return 0
    if treffer := re.fullmatch(r"repos/[^/]+/[^/]+/issues/(\d+)/dependencies/blocked_by", ziel):
        kanten = welt["kanten"].get(treffer.group(1), [])
        print(json.dumps([{"number": n} for n in kanten]))
        return 0
    print(f"Stub kennt den Pfad nicht: {ziel}", file=sys.stderr)
    return 2


# --- Aufbau -------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


def _aufbau(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "remote", "add", "origin", f"https://github.com/{SLUG}.git")
    ordner = repo / "docs" / "agents" / "manifests"
    ordner.mkdir(parents=True)
    tickets = {str(TICKET): {"title": "Kern", "schaetzung_k": 80, "umfang": "Kern bauen."}}
    (ordner / f"spec-{SPEC}.json").write_text(
        json.dumps({"spec": SPEC, "feature": "wegwerf", "tickets": tickets}),
        encoding="utf-8",
    )
    weltdatei = tmp_path / "welt.json"
    welt = {
        "issues": {
            str(SPEC): _issue(SPEC, "Spec: Wegwerf"),
            str(TICKET): _issue(TICKET, "Kern", state="closed"),
            str(ANDERES): _issue(ANDERES, "Fremdes Ticket"),
        },
        "unter": {str(SPEC): [TICKET]},
        "kanten": {},
    }
    weltdatei.write_text(json.dumps(welt), encoding="utf-8")
    umgebung = {k: v for k, v in os.environ.items() if not k.startswith(("GH_STUB", "TO_SPAWN"))}
    umgebung.update({"TO_SPAWN_GH_STUB": str(Path(__file__).resolve()), "E24_GH_WELT": str(weltdatei)})
    return repo, weltdatei, umgebung


def _cli(
    repo: Path, umgebung: dict[str, str], *args: str, eingabe: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        check=False,
        cwd=str(repo),
        env=umgebung,
        input=eingabe,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _welt(datei: Path) -> dict[str, Any]:
    return json.loads(datei.read_text(encoding="utf-8"))


def _manifest(repo: Path) -> dict[str, Any]:
    datei = repo / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    return json.loads(datei.read_text(encoding="utf-8"))["tickets"]


def _folge_anlegen(repo: Path, umgebung: dict[str, str], text: str) -> int:
    lauf = _cli(
        repo,
        umgebung,
        "befund",
        "--spec",
        str(SPEC),
        "--ticket",
        str(TICKET),
        "--stufe",
        "gelb",
        "--text",
        text,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    treffer = re.search(r"Folge-Ticket #(\d+)", lauf.stdout)
    assert treffer, lauf.stdout
    return int(treffer.group(1))


# --- Tests --------------------------------------------------------------------------


def test_folge_ticket_ueber_befund_landet_im_manifest_mit_herkunft(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    neu = _folge_anlegen(repo, umgebung, "Randfall Leerzeichen im Pfad")
    eintrag = _manifest(repo)[str(neu)]
    assert eintrag["gelb_von"] == TICKET
    assert eintrag["umfang"] == "Randfall Leerzeichen im Pfad"
    # spec_rand.anhaengen: Folge-Ticket hängt als Unter-Issue an der Spec.
    assert neu in _welt(weltdatei)["unter"][str(SPEC)]


def test_spec_schliessen_bricht_ab_mit_liste_bei_offenem_folge_ticket(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    neu = _folge_anlegen(repo, umgebung, "noch offen")
    lauf = _cli(repo, umgebung, "spec-schliessen", str(SPEC))
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert f"#{neu}" in lauf.stdout and "offen" in lauf.stdout
    assert _welt(weltdatei)["issues"][str(SPEC)]["state"] == "open"


def test_spec_schliessen_schliesst_wenn_alles_erledigt(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    gebaut = _folge_anlegen(repo, umgebung, "wird gebaut")
    gebuendelt = _folge_anlegen(repo, umgebung, "kommt ins Sammel-Issue")
    welt = _welt(weltdatei)
    welt["issues"][str(gebaut)]["state"] = "closed"
    welt["issues"][str(gebuendelt)]["labels"] = [{"name": "verschoben"}]
    weltdatei.write_text(json.dumps(welt), encoding="utf-8")
    lauf = _cli(repo, umgebung, "spec-schliessen", str(SPEC))
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert _welt(weltdatei)["geschlossen"] == [SPEC]


def test_spec_schliessen_ohne_manifest_schliesst_nichts(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    lauf = _cli(repo, umgebung, "spec-schliessen", str(ANDERES))
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert "geschlossen" not in _welt(weltdatei)


def _lade_bau(monkeypatch: pytest.MonkeyPatch, repo: Path) -> types.ModuleType:
    monkeypatch.setenv("TO_SPAWN_REPO", str(repo))
    spec = importlib.util.spec_from_file_location("bau_e24", SKILL / "skripte" / "bau.py")
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    sys.modules["bau_e24"] = modul
    try:
        spec.loader.exec_module(modul)
    finally:
        sys.modules.pop("bau_e24", None)
    return modul


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash fehlt (Claude Code startet Hooks in bash)")
def test_hook_aus_bau_session_sperrt_rohes_spec_schliessen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _, umgebung = _aufbau(tmp_path)
    bau = _lade_bau(monkeypatch, repo)
    pre = bau.staffel_hooks()["PreToolUse"]
    befehle = [h["command"] for b in pre if b["matcher"] == "Bash" for h in b["hooks"]]
    hook = next(b for b in befehle if "hook-spec-sperre" in b)

    def lauf(befehl: str) -> subprocess.CompletedProcess[str]:
        eingabe = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": befehl},
            "cwd": str(repo),
        }
        return subprocess.run(
            ["bash", "-c", hook],
            check=False,
            cwd=str(repo),
            env=umgebung,
            input=json.dumps(eingabe),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )

    for roh in (
        f"gh issue close {SPEC} --comment fertig",
        f"cd x && gh -R {SLUG} issue close '#{SPEC}'",
        f"gh issue close https://github.com/{SLUG}/issues/{SPEC}",
    ):
        abgelehnt = lauf(roh)
        assert abgelehnt.returncode == 2, (roh, abgelehnt.stdout, abgelehnt.stderr)
        assert f"spec-schliessen {SPEC}" in abgelehnt.stderr
    for frei in (
        f"gh issue close {ANDERES}",
        f'gh issue close {ANDERES} --comment "siehe #{SPEC}"',
        "git status",
    ):
        erlaubt = lauf(frei)
        assert erlaubt.returncode == 0, (frei, erlaubt.stdout, erlaubt.stderr)


def test_spec_schliessen_bricht_ab_bei_offenem_regulaerem_spec_ticket(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    welt = _welt(weltdatei)
    welt["issues"][str(TICKET)]["state"] = "open"
    weltdatei.write_text(json.dumps(welt), encoding="utf-8")
    lauf = _cli(repo, umgebung, "spec-schliessen", str(SPEC))
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert f"#{TICKET}" in lauf.stdout and "offen" in lauf.stdout
    assert "geschlossen" not in _welt(weltdatei)


def test_hook_sperrt_auch_issue_edit_und_api_state_closed(tmp_path: Path) -> None:
    repo, _, umgebung = _aufbau(tmp_path)

    def lauf(befehl: str) -> subprocess.CompletedProcess[str]:
        eingabe = {"tool_name": "Bash", "tool_input": {"command": befehl}, "cwd": str(repo)}
        return _cli(repo, umgebung, "hook-spec-sperre", eingabe=json.dumps(eingabe))

    for roh in (
        f"gh issue edit {SPEC} --state closed",
        f"gh issue edit {SPEC} --title neu --state=closed",
        f"gh api -X PATCH repos/{SLUG}/issues/{SPEC} -f state=closed",
        f"gh api repos/{SLUG}/issues/{SPEC} --method PATCH --field 'state=closed'",
    ):
        abgelehnt = lauf(roh)
        assert abgelehnt.returncode == 2, (roh, abgelehnt.stdout, abgelehnt.stderr)
        assert f"spec-schliessen {SPEC}" in abgelehnt.stderr
    for frei in (
        f"gh issue edit {SPEC} --add-label verschoben",
        f"gh api repos/{SLUG}/issues/{SPEC}",
        f"gh api repos/{SLUG}/issues/{SPEC}/comments -f body=state=closed",
        f"gh api -X PATCH repos/{SLUG}/issues/{ANDERES} -f state=closed",
    ):
        erlaubt = lauf(frei)
        assert erlaubt.returncode == 0, (frei, erlaubt.stdout, erlaubt.stderr)


def _hook(repo: Path, umgebung: dict[str, str], befehl: str) -> subprocess.CompletedProcess[str]:
    eingabe = {"tool_name": "Bash", "tool_input": {"command": befehl}, "cwd": str(repo)}
    return _cli(repo, umgebung, "hook-spec-sperre", eingabe=json.dumps(eingabe))


def test_hook_trennt_nur_ausserhalb_von_anfuehrungszeichen(tmp_path: Path) -> None:
    """Review PR 146: Trenner im Kommentar, Zeilen-Fortsetzung, Klammern umgingen die
    Sperre; eine Commit-Nachricht, die den Befehl nur nennt, wurde falsch gesperrt."""
    repo, _, umgebung = _aufbau(tmp_path)
    for roh in (
        f'gh issue close -c "fertig; alles grün" {SPEC}',
        f'gh issue close --comment="a & b" {SPEC}',
        f"gh issue close -c 'x | y' {SPEC}",
        f'gh issue close -c ";" {SPEC}',
        f"gh issue close \\\n  {SPEC}",
        f"(gh issue close {SPEC})",
        f"echo $(gh issue close {SPEC})",
        f"GH_PAGER= gh issue close {SPEC}",
        f"git status; gh issue close {SPEC}\necho fertig",
        # Urteil dirigent_88_fertig: Schalter mit Wert, Shell-Umweg, voller Pfad, --input.
        f"gh issue close --duplicate-of 12 {SPEC}",
        f'bash -c "gh issue close {SPEC}"',
        f"sh -c 'cd x && gh issue close {SPEC}'",
        f'eval "gh issue close {SPEC}"',
        f"/usr/bin/gh issue close {SPEC}",
        f"gh api repos/{SLUG}/issues/{SPEC} -X PATCH --input datei.json",
        f"echo '{{}}' | gh api repos/{SLUG}/issues/{SPEC} --method PATCH --input -",
    ):
        abgelehnt = _hook(repo, umgebung, roh)
        assert abgelehnt.returncode == 2, (roh, abgelehnt.stdout, abgelehnt.stderr)
        assert f"spec-schliessen {SPEC}" in abgelehnt.stderr
    for frei in (
        f'git commit -m "doku: gh issue close {SPEC} verboten"',
        f'echo "gh issue close {SPEC}"',
        f"cat > t.py <<'EOF'\nbefehl = \"gh issue close {SPEC}\"\nEOF",
        f'gh issue close {ANDERES} -c "nicht #{SPEC}; gh issue close {SPEC}"',
        f"echo gh issue close {SPEC}",
        f"gh issue close --duplicate-of {SPEC} {ANDERES}",
        f"gh api repos/{SLUG}/issues/{SPEC}/comments --input body.json",
    ):
        erlaubt = _hook(repo, umgebung, frei)
        assert erlaubt.returncode == 0, (frei, erlaubt.stdout, erlaubt.stderr)


def test_hook_sperrt_schluesselwoerter_praefix_schalter_shell_gruppe_und_gleich_form(tmp_path: Path) -> None:
    """Issue 175 (Rest aus PR 146): Befehlsanfang hinter Shell-Schlüsselwort, Klammerblock
    oder Präfix mit Schalter, ``-c`` in einer Schalter-Gruppe und ``--input=`` kamen durch."""
    repo, _, umgebung = _aufbau(tmp_path)
    for roh in (
        # die fünf Beispiele aus dem Issue
        f"if true; then gh issue close {SPEC}; fi",
        f"{{ gh issue close {SPEC}; }}",
        f"sudo -u bau gh issue close {SPEC}",
        f"bash -lc 'gh issue close {SPEC}'",
        f"gh api repos/{SLUG}/issues/{SPEC} -X PATCH --input=datei.json",
        # dieselben Klassen in anderer Schreibweise
        f"if gh issue close {SPEC}; then echo ok; fi",
        f"while true; do gh issue close {SPEC}; done",
        f"! gh issue close {SPEC}",
        f"env -u GH_PAGER gh issue close {SPEC}",
        f"sudo -E -u bau /usr/bin/gh issue close {SPEC}",
        f"bash -ec 'gh issue close {SPEC}'",
        f"bash -o pipefail -c 'gh issue close {SPEC}'",
        f"gh api repos/{SLUG}/issues/{SPEC} -X PATCH -fstate=closed",
        f"gh api repos/{SLUG}/issues/{SPEC} -X PATCH -F=state=closed",
    ):
        abgelehnt = _hook(repo, umgebung, roh)
        assert abgelehnt.returncode == 2, (roh, abgelehnt.stdout, abgelehnt.stderr)
        assert f"spec-schliessen {SPEC}" in abgelehnt.stderr


def test_hook_gegenprobe_175_harmlose_befehle_bleiben_frei(tmp_path: Path) -> None:
    """Gegenprobe zu Issue 175: das neue Zerlegen sperrt nichts, was nur ``gh`` nennt."""
    repo, _, umgebung = _aufbau(tmp_path)
    for frei in (
        f'if true; then echo "gh issue close {SPEC}"; fi',
        f"{{ echo gh issue close {SPEC}; }}",
        f"sudo -u bau echo gh issue close {SPEC}",
        f"bash -lc 'echo gh issue close {SPEC}'",
        f"bash -l skript.sh gh issue close {SPEC}",
        f"gh api repos/{SLUG}/issues/{SPEC}/comments --input=body.json",
        f"gh api repos/{SLUG}/issues/{SPEC} --jq=.state",
        f"gh api repos/{SLUG}/issues/{SPEC}/comments -fbody=state=closed",
        f"gh issue edit {SPEC} --body=state=closed",
        f"gh issue close {ANDERES} --comment=#{SPEC}",
        f"sudo -u bau gh issue close {ANDERES}",
    ):
        erlaubt = _hook(repo, umgebung, frei)
        assert erlaubt.returncode == 0, (frei, erlaubt.stdout, erlaubt.stderr)


def test_hook_laesst_bei_eigenem_fehler_durch(tmp_path: Path) -> None:
    """Fail-open: kaputte Eingabe oder fremder Ordner hält die Bau-Session nie an."""
    repo, _, umgebung = _aufbau(tmp_path)
    kaputt = _cli(repo, umgebung, "hook-spec-sperre", eingabe="{kein json")
    assert kaputt.returncode == 0, kaputt.stdout + kaputt.stderr
    ohne = _cli(repo, umgebung, "hook-spec-sperre", eingabe=json.dumps({"tool_name": "Bash", "cwd": str(repo)}))
    assert ohne.returncode == 0, ohne.stdout + ohne.stderr
    fremd = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": f"gh issue close {SPEC}"}, "cwd": str(tmp_path / "fehlt")}
    )
    aussen = _cli(repo, umgebung, "hook-spec-sperre", eingabe=fremd)
    assert aussen.returncode == 0, aussen.stdout + aussen.stderr


def test_spec_schliessen_schliesst_nicht_bei_gh_fehler(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    lauf = _cli(repo, umgebung | {"E24_GH_API_FEHLER": "1"}, "spec-schliessen", str(SPEC))
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert "geschlossen" not in _welt(weltdatei)


def test_spec_schliessen_schliesst_nicht_bei_kaputtem_manifest(tmp_path: Path) -> None:
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    (repo / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text("{kaputt", encoding="utf-8")
    lauf = _cli(repo, umgebung, "spec-schliessen", str(SPEC))
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert "geschlossen" not in _welt(weltdatei)


def test_befund_wiederholt_haengt_folge_ticket_nachtraeglich_an(tmp_path: Path) -> None:
    """Scheitert das Anhängen beim ersten ``befund --spec``, holt der zweite Aufruf es nach."""
    repo, weltdatei, umgebung = _aufbau(tmp_path)
    args = ("befund", "--spec", str(SPEC), "--ticket", str(TICKET), "--stufe", "gelb", "--text", "Randfall")
    erst = _cli(repo, umgebung | {"E24_GH_POST_FEHLER": "1"}, *args)
    assert erst.returncode != 0, erst.stdout + erst.stderr
    neu = int(re.search(r"Folge-Ticket #(\d+)", erst.stdout).group(1))  # type: ignore[union-attr]
    assert neu not in _welt(weltdatei)["unter"].get(str(SPEC), [])
    zweit = _cli(repo, umgebung, *args)
    assert zweit.returncode == 0, zweit.stdout + zweit.stderr
    assert neu in _welt(weltdatei)["unter"][str(SPEC)]
    assert len([n for n in _manifest(repo) if _manifest(repo)[n].get("gelb_von") == TICKET]) == 1


if __name__ == "__main__":
    sys.exit(_stub_main(sys.argv[1:]))
