"""#578 (E8): Werkzeug-Fehler ins eigene Log plus Ticket im Skill-Repo statt Mail.

Prüft das Modul ``to_spawn.fehlermeldung`` (eine Funktion ``melde(...)``) sowie die
Anbindung unerwarteter Werkzeug-Ausnahmen in ``to_spawn.capo``:

1. Einheitlicher Log-Ort unter ``~/.claude/to-spawn/`` (gleicher Basisordner wie ``hindernis``).
2. Gleicher Fehler zweimal → ein offenes Ticket im Skill-Repo + Kommentar, kein Duplikat
   (Weg-Test gegen vollständige ``gh``-Aufzeichnung über ``TO_SPAWN_GH_STUB``).
3. Kein Mail-/Benachrichtigungs-Aufruf (``mail.befehl`` und ``melder.melden`` werden nie gerufen).
4. Ziel-Repo des Tickets ist immer das Skill-Repo, nie das App-Repo — auch wenn der Aufruf
   aus einem App-Repo mit eigenem ``origin`` und ``TO_SPAWN_REPO`` kommt.
5. Resilienz (wirft nie bei ``gh``-/Dateifehlern) und Einbindung in ``capo`` ohne Mail.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from conftest import attrappe, stub_pfad
from to_spawn import capo, config, fehlermeldung, hausmeister, hindernis, melder, prozessbaum

SKILL = Path(__file__).resolve().parents[1]


def _gh_aufzeichner(ordner: Path) -> tuple[Path, Path, Path]:
    """Stateful ``gh``-Attrappe für Weg-Tests: zeichnet jeden Aufruf auf und führt offene Tickets."""
    zustand_datei = ordner / "gh_zustand.json"
    aufruf_log = ordner / "gh_aufrufe.jsonl"
    zustand_datei.write_text(json.dumps({"naechste": 1, "issues": [], "labels": []}), encoding="utf-8")

    code = textwrap.dedent(
        f"""\
        import json
        import sys
        from pathlib import Path

        ZUSTAND = Path({str(zustand_datei)!r})
        AUFRUFE = Path({str(aufruf_log)!r})
        args = sys.argv[1:]
        with AUFRUFE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(args, ensure_ascii=False) + "\\n")

        daten = json.loads(ZUSTAND.read_text(encoding="utf-8"))

        def wert_nach(flag: str, vorgabe: str = "") -> str:
            if flag in args:
                idx = args.index(flag)
                if idx + 1 < len(args):
                    return args[idx + 1]
            return vorgabe

        def speichern() -> None:
            ZUSTAND.write_text(json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")

        if args[:1] == ["api"] and len(args) >= 2 and "/labels/" in args[1]:
            pfad = args[1]
            vorhanden = pfad in daten["labels"]
            sys.exit(0 if vorhanden else 1)

        if args[:2] == ["label", "create"]:
            label = args[2] if len(args) > 2 else ""
            repo = wert_nach("--repo")
            daten["labels"].append(f"repos/{{repo}}/labels/{{label}}")
            speichern()
            print(label)
            sys.exit(0)

        if args[:2] == ["issue", "list"]:
            repo = wert_nach("--repo")
            label = wert_nach("--label")
            state = wert_nach("--state", "open")
            treffer = [
                {{
                    "number": e["number"],
                    "title": e["title"],
                    "body": e["body"],
                    "url": e["url"],
                }}
                for e in daten["issues"]
                if e["repo"] == repo
                and (not state or state == "all" or e["state"] == state)
                and (not label or label in e["labels"])
            ]
            print(json.dumps(treffer, ensure_ascii=False))
            sys.exit(0)

        if args[:2] == ["issue", "create"]:
            repo = wert_nach("--repo")
            titel = wert_nach("--title")
            body = wert_nach("--body")
            label = wert_nach("--label")
            nr = int(daten["naechste"])
            daten["naechste"] = nr + 1
            url = f"https://github.com/{{repo}}/issues/{{nr}}"
            daten["issues"].append(
                {{
                    "number": nr,
                    "repo": repo,
                    "title": titel,
                    "body": body,
                    "labels": [label] if label else [],
                    "state": "open",
                    "url": url,
                    "comments": [],
                }}
            )
            speichern()
            print(url)
            sys.exit(0)

        if args[:2] == ["issue", "comment"] and len(args) >= 3:
            nr = int(args[2])
            repo = wert_nach("--repo")
            body = wert_nach("--body")
            for eintrag in daten["issues"]:
                if eintrag["number"] == nr and eintrag["repo"] == repo:
                    eintrag["comments"].append(body)
                    speichern()
                    print(f"{{eintrag['url']}}#issuecomment-{{len(eintrag['comments'])}}")
                    sys.exit(0)
            sys.stderr.write(f"Issue #{{nr}} in {{repo}} nicht gefunden\\n")
            sys.exit(1)

        sys.stderr.write(f"Unbekannter gh-Aufruf: {{args!r}}\\n")
        sys.exit(2)
        """
    )
    stub = stub_pfad(attrappe(ordner, "gh_fehler_stub", code))
    return stub, zustand_datei, aufruf_log


def _aufrufe(log_datei: Path) -> list[list[str]]:
    if not log_datei.is_file():
        return []
    return [json.loads(z) for z in log_datei.read_text(encoding="utf-8").splitlines() if z.strip()]


def _app_repo(tmp_path: Path, slug: str = "fremde-org/fremde-app") -> tuple[Path, Path]:
    """Legt ein echtes App-Git-Repo mit fremdem GitHub-Origin und ``mail.befehl``-Falle an."""
    repo = tmp_path / "fremde-app"
    repo.mkdir()
    mail_spur = tmp_path / "mail_wurde_gerufen.txt"
    mail_skript = tmp_path / "mail_falle.py"
    mail_skript.write_text(
        f"from pathlib import Path\nPath({str(mail_spur)!r}).write_text('MAIL_GERUFEN', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (repo / ".to-spawn").mkdir()
    konfig = {
        "mail": {
            "ziel": "alarm@example.test",
            "nur_kritisch": False,
            "befehl": [sys.executable, str(mail_skript)],
        }
    }
    (repo / ".to-spawn" / "config.json").write_text(json.dumps(konfig), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, **prozessbaum.fensterlos())
    subprocess.run(
        ["git", "remote", "add", "origin", f"https://github.com/{slug}.git"],
        cwd=repo,
        check=True,
        **prozessbaum.fensterlos(),
    )
    return repo, mail_spur


def test_log_ort_einheitlich_unter_claude_to_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Einheitlicher Log-Ort unter ``~/.claude/to-spawn/`` (wie ``hindernis``)."""
    heim = tmp_path / "heim"
    heim.mkdir()
    monkeypatch.delenv(fehlermeldung.FEHLER_LOG_ENV, raising=False)
    monkeypatch.delenv("TO_SPAWN_HINDERNIS_LOG", raising=False)
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("USERPROFILE", str(heim))

    pfad = fehlermeldung.log_pfad()
    assert pfad == heim / ".claude" / "to-spawn" / fehlermeldung.FEHLER_LOG_NAME
    assert pfad.parent == hindernis.daten_ordner()
    assert pfad.parent == hindernis._pfad().parent


def test_gleicher_fehler_zweimal_ein_ticket_plus_kommentar_kein_duplikat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Weg-Test: gleicher Fehler zweimal → ein Ticket + Kommentar, kein zweites Ticket; anderer Fehler → neues Ticket."""
    stub, zustand_datei, aufruf_log = _gh_aufzeichner(tmp_path)
    log_datei = tmp_path / "werkzeug_fehler.jsonl"
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    monkeypatch.setenv(fehlermeldung.FEHLER_LOG_ENV, str(log_datei))
    monkeypatch.delenv(fehlermeldung.OHNE_TICKET_ENV, raising=False)

    erst = fehlermeldung.melde(
        "bau-nachtlauf",
        "Bau-Nachtlauf rot",
        "pytest Exit 4 auf Commit 1111111",
    )
    zweit = fehlermeldung.melde(
        "bau-nachtlauf",
        "Bau-Nachtlauf rot",
        "pytest Exit 4 auf Commit 2222222",
    )
    anderer = fehlermeldung.melde(
        "skill-tests",
        "Anderer Werkzeug-Fehler",
        "AssertionError in test_x",
    )

    assert erst.log_geschrieben is True
    assert erst.aktion == "angelegt"
    assert erst.ticket == "#1"
    assert erst.ticket_url == f"https://github.com/{erst.ziel_repo}/issues/1"

    assert zweit.log_geschrieben is True
    assert zweit.aktion == "kommentiert"
    assert zweit.ticket == "#1"
    assert zweit.ticket_url == erst.ticket_url
    assert zweit.schluessel == erst.schluessel

    assert anderer.aktion == "angelegt"
    assert anderer.ticket == "#2"
    assert anderer.schluessel != erst.schluessel

    zustand = json.loads(zustand_datei.read_text(encoding="utf-8"))
    assert len(zustand["issues"]) == 2
    assert len(zustand["issues"][0]["comments"]) == 1
    assert "Commit 2222222" in zustand["issues"][0]["comments"][0]
    assert len(zustand["issues"][1]["comments"]) == 0

    aufrufe = _aufrufe(aufruf_log)
    create_aufrufe = [a for a in aufrufe if a[:2] == ["issue", "create"]]
    comment_aufrufe = [a for a in aufrufe if a[:2] == ["issue", "comment"]]
    assert len(create_aufrufe) == 2
    assert len(comment_aufrufe) == 1
    assert comment_aufrufe[0][2] == "1"

    zeilen = fehlermeldung.lesen(log_datei)
    assert len(zeilen) == 3
    assert [z["aktion"] for z in zeilen] == ["angelegt", "kommentiert", "angelegt"]
    assert [z["ticket"] for z in zeilen] == ["#1", "#1", "#2"]
    assert all(z["ticket_url"] and z["zeit"] and z["wer"] and z["schluessel"] for z in zeilen)


def test_ziel_repo_immer_skill_repo_nie_app_repo_und_keine_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aufruf aus einem App-Repo mit ``mail.befehl``: Ticket geht ins Skill-Repo, ``mail.befehl`` wird nie gerufen."""
    app_repo, mail_spur = _app_repo(tmp_path, slug="fremde-org/fremde-app")
    stub, zustand_datei, aufruf_log = _gh_aufzeichner(tmp_path)
    log_datei = tmp_path / "werkzeug_fehler.jsonl"

    monkeypatch.chdir(app_repo)
    monkeypatch.setenv("TO_SPAWN_REPO", str(app_repo))
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    monkeypatch.setenv(fehlermeldung.FEHLER_LOG_ENV, str(log_datei))
    monkeypatch.delenv(fehlermeldung.OHNE_TICKET_ENV, raising=False)

    def verboten_mail(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("melder.melden / mail.befehl darf für Werkzeug-Fehler nie gerufen werden")

    monkeypatch.setattr(melder, "melden", verboten_mail)

    erg = fehlermeldung.melde(
        "werkzeug.pruef",
        "Unerwartete Werkzeug-Ausnahme",
        ausnahme=RuntimeError("interner Fehler"),
        kontext={"app_repo": "fremde-org/fremde-app", "ticket": 99},
    )

    erwartetes_skill_repo = fehlermeldung.skill_repo()
    assert erwartetes_skill_repo in config.OEFFENTLICHE_SLUGS
    assert erg.ziel_repo == erwartetes_skill_repo
    assert erg.ziel_repo != "fremde-org/fremde-app"
    assert erg.aktion == "angelegt"
    assert erg.ticket == "#1"

    aufrufe = _aufrufe(aufruf_log)
    assert aufrufe, "gh wurde nicht aufgerufen"
    for a in aufrufe:
        if "--repo" in a:
            assert a[a.index("--repo") + 1] == erwartetes_skill_repo
        if a[:1] == ["api"] and len(a) >= 2:
            assert a[1].startswith(f"repos/{erwartetes_skill_repo}/"), a
            assert "fremde-org/fremde-app" not in a[1]

    zustand = json.loads(zustand_datei.read_text(encoding="utf-8"))
    assert [i["repo"] for i in zustand["issues"]] == [erwartetes_skill_repo]
    assert not mail_spur.exists(), "mail.befehl wurde fälschlich aufgerufen"


def test_skill_repo_aus_stand_json_in_installierter_kopie(tmp_path: Path) -> None:
    """Installierte Kopie ohne ``.git`` liest das Skill-Repo aus ``STAND.json`` (``quelle``)."""
    installiert = tmp_path / "skill-kopie"
    installiert.mkdir()
    (installiert / "STAND.json").write_text(
        json.dumps({"commit": "abcdef1234567890", "quelle": "https://github.com/org-a/skill-fork.git"}),
        encoding="utf-8",
    )
    assert fehlermeldung.skill_repo(installiert) == "org-a/skill-fork"

    ohne_stempel = tmp_path / "skill-leer"
    ohne_stempel.mkdir()
    assert fehlermeldung.skill_repo(ohne_stempel) == config.OEFFENTLICHE_SLUGS[0]


def test_keine_mail_oder_benachrichtigung_im_modul_quelltext() -> None:
    """Statischer Schutz: ``to_spawn/fehlermeldung.py`` ruft weder ``melder`` noch ``mail.befehl`` noch Mail/Telegram."""
    quelle = (SKILL / "to_spawn" / "fehlermeldung.py").read_text(encoding="utf-8")
    verboten = re.compile(
        r"\bmelder\b|mail\.befehl|to_spawn_mail|smtplib|sendmail|telegram|webhook",
        re.IGNORECASE,
    )
    assert not verboten.search(quelle), "Verbotener Mail-/Benachrichtigungs-Bezug in to_spawn/fehlermeldung.py"


def test_melde_wirft_nie_bei_unbeschreibbarem_log_und_gh_fehler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kaputtes Log-Ziel und fehlschlagendes ``gh`` werfen nie — Aufrufer läuft stabil weiter."""
    blockiert = tmp_path / "datei-statt-ordner"
    blockiert.write_text("x", encoding="utf-8")
    kaputtes_gh = stub_pfad(attrappe(tmp_path, "gh_kaputt", "import sys\nsys.exit(1)\n"))

    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(kaputtes_gh))
    monkeypatch.setenv(fehlermeldung.FEHLER_LOG_ENV, str(blockiert / "fehler.jsonl"))
    monkeypatch.delenv(fehlermeldung.OHNE_TICKET_ENV, raising=False)

    erg = fehlermeldung.melde("test", "Kaputter Lauf", "Details")
    assert erg.log_geschrieben is False
    assert erg.aktion == "fehler"
    assert erg.ticket is None
    assert fehlermeldung.lesen(blockiert / "fehler.jsonl") == []


def test_capo_werkzeug_ausnahme_meldet_ins_log_und_skill_repo_ohne_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unerwartete Werkzeug-Ausnahme in ``capo`` geht an ``fehlermeldung.melde`` (Skill-Repo + Log, nie Mail)."""
    app_repo, mail_spur = _app_repo(tmp_path, slug="fremde-org/app-x")
    stub, zustand_datei, aufruf_log = _gh_aufzeichner(tmp_path)
    log_datei = tmp_path / "werkzeug_fehler.jsonl"

    monkeypatch.chdir(app_repo)
    monkeypatch.setenv("TO_SPAWN_REPO", str(app_repo))
    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    monkeypatch.setenv(fehlermeldung.FEHLER_LOG_ENV, str(log_datei))
    monkeypatch.delenv(fehlermeldung.OHNE_TICKET_ENV, raising=False)

    def kaputt(**_: object) -> hausmeister.Rundgang:
        raise RuntimeError("unerwarteter Werkzeug-Absturz")

    monkeypatch.setattr(hausmeister, "rundgang", kaputt)
    monkeypatch.setattr(
        melder,
        "melden",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Mail darf nicht gerufen werden")),
    )

    jetzt = datetime.now(UTC)
    # Probelauf (dry_run=True): keine GitHub-Meldung, kein Ticket.
    probe = capo._hausmeister_schluss(578, {}, jetzt, dry_run=True)
    assert any("unerwarteter Fehler" in z for z in probe)
    assert _aufrufe(aufruf_log) == []

    # Echter Lauf zweimal → ein Ticket + ein Kommentar im Skill-Repo, zwei Log-Zeilen, keine Mail.
    zeilen1 = capo._hausmeister_schluss(578, {}, jetzt, dry_run=False)
    zeilen2 = capo._hausmeister_schluss(578, {}, jetzt, dry_run=False)
    assert any("FEHLER: Hausmeister" in z for z in zeilen1)
    assert any("FEHLER: Hausmeister" in z for z in zeilen2)

    zustand = json.loads(zustand_datei.read_text(encoding="utf-8"))
    assert len(zustand["issues"]) == 1
    assert zustand["issues"][0]["repo"] == fehlermeldung.skill_repo()
    assert len(zustand["issues"][0]["comments"]) == 1
    assert not mail_spur.exists()

    eintraege = fehlermeldung.lesen(log_datei)
    assert [e["aktion"] for e in eintraege] == ["angelegt", "kommentiert"]
    assert all(e["quelle"] == "capo.hausmeister" for e in eintraege)


def test_cli_modul_aufruf_schreibt_log_und_ticket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Direkter Aufruf ``python -m to_spawn.fehlermeldung`` funktioniert als Unterprozess (Linux + Windows)."""
    stub, zustand_datei, _aufruf_log = _gh_aufzeichner(tmp_path)
    log_datei = tmp_path / "cli_fehler.jsonl"
    umgebung = {
        **os.environ,
        "TO_SPAWN_TESTLAUF": "1",
        "TO_SPAWN_GH_STUB": str(stub),
        fehlermeldung.FEHLER_LOG_ENV: str(log_datei),
        "PYTHONPATH": str(SKILL),
        "PYTHONUTF8": "1",
    }
    umgebung.pop(fehlermeldung.OHNE_TICKET_ENV, None)

    lauf = subprocess.run(
        [
            sys.executable,
            "-m",
            "to_spawn.fehlermeldung",
            "--quelle",
            "bau-nachtlauf",
            "--titel",
            "Bau-Nachtlauf rot",
            "--details",
            "pytest Exit 4",
        ],
        cwd=SKILL,
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        **prozessbaum.fensterlos(),
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "#1" in lauf.stdout
    zustand = json.loads(zustand_datei.read_text(encoding="utf-8"))
    assert len(zustand["issues"]) == 1
    assert len(fehlermeldung.lesen(log_datei)) == 1


def test_expliziter_schluessel_und_geschlossenes_ticket_und_kaputte_logzeile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expliziter Schlüssel bündelt wechselnde Titel; nach Schließen entsteht ein neues Ticket; kaputte Logzeile übersprungen."""
    stub, zustand_datei, _aufruf_log = _gh_aufzeichner(tmp_path)
    log_datei = tmp_path / "werkzeug_fehler.jsonl"
    log_datei.write_text("{kaputtes-json\n", encoding="utf-8")

    monkeypatch.setenv("TO_SPAWN_GH_STUB", str(stub))
    monkeypatch.setenv(fehlermeldung.FEHLER_LOG_ENV, str(log_datei))
    monkeypatch.delenv(fehlermeldung.OHNE_TICKET_ENV, raising=False)

    e1 = fehlermeldung.melde("nachtlauf", "Nachtlauf rot (Tag 1)", "Exit 1", schluessel="nachtlauf-sammel")
    e2 = fehlermeldung.melde("nachtlauf", "Nachtlauf rot (Tag 2)", "Exit 2", schluessel="nachtlauf-sammel")
    assert e1.aktion == "angelegt" and e1.ticket == "#1"
    assert e2.aktion == "kommentiert" and e2.ticket == "#1"

    # Ticket #1 schließen → nächster Fehler desselben Schlüssels legt neues Ticket #2 an.
    zustand = json.loads(zustand_datei.read_text(encoding="utf-8"))
    zustand["issues"][0]["state"] = "closed"
    zustand_datei.write_text(json.dumps(zustand, ensure_ascii=False), encoding="utf-8")

    e3 = fehlermeldung.melde("nachtlauf", "Nachtlauf rot (Tag 3)", "Exit 3", schluessel="nachtlauf-sammel")
    assert e3.aktion == "angelegt" and e3.ticket == "#2"

    zeilen = fehlermeldung.lesen(log_datei)
    assert len(zeilen) == 3
    assert [z["ticket"] for z in zeilen] == ["#1", "#1", "#2"]
