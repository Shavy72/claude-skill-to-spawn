"""Tests für das Abschluss-Paket der Wache: Test-Übersicht (HTML) + Abschluss-Paket (3 Links).

Echt laufen beide CLIs gegen ein Repo aus echten Dateien (Manifest, Belegseiten, Konfig).
Gestellt ist nur der externe Mail-Befehl (schreibt die JSON-Nutzlast in eine Datei).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SKRIPTE = SKILL / "skripte"
SPEC = 900


def _lauf(skript: str, repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKRIPTE / skript), str(SPEC), "--repo", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})},
        timeout=120,
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "projekt"
    (r / ".git").mkdir(parents=True)
    manifest = {
        "spec": SPEC,
        "tickets": {
            "901": {"title": "Knopf zeigt den Status"},
            "902": {"title": "Handy schaltet sich aus"},
            "903": {"title": "Live-Beweis am echten Handy"},
            "904": {"title": "Ohne Beleg"},
        },
    }
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(json.dumps(manifest), encoding="utf-8")
    vh = r / "docs" / "verify-hard"
    (vh / "902").mkdir(parents=True)
    # 901: Rot-Beweis (gewollt rot) wird ignoriert, grüner Lauf zählt.
    (vh / "901_rot.txt").write_text("FAILED tests/x.py::a\n3 failed in 1.0s\n", encoding="utf-8")
    (vh / "901_gruen.txt").write_text(
        "$ python -m pytest tests/test_knopf.py -q\n...\n== 20 passed in 2.1s ==\n", encoding="utf-8"
    )
    # 902: grüner Name, aber ein Test scheitert → rot.
    (vh / "902" / "gruen_lauf.txt").write_text(
        "pytest tests/test_aus.py\n1 failed, 6 passed, 2 warnings in 3s\n", encoding="utf-8"
    )
    # 903: nur Markdown mit Abnahme + X/Y + Klickweg.
    (vh / "903.md").write_text(
        "# Belegseite #903\n**ABNAHME: ABGENOMMEN (Live)**\n| SPEC | BELEGT | 5/5 Abnahme-Kriterien belegt |\n"
        "Klickweg als manu (Rolle cmo): Knopf gedrückt.\n",
        encoding="utf-8",
    )
    # Fremdes Ticket mit ähnlicher Nummer darf nicht mitzählen.
    (vh / "9010_gruen.txt").write_text("99 failed\n", encoding="utf-8")
    return r


def test_uebersicht_zaehlt_und_erkennt_rot(repo: Path, tmp_path: Path) -> None:
    ziel = tmp_path / "uebersicht.html"
    ergebnis = _lauf("test_uebersicht.py", repo, "--aus", str(ziel))
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    seite = ziel.read_text(encoding="utf-8")
    # 20 (901) + 7 (902: 6 grün, 1 rot) + 5 (903: 5/5) = 31 Tests, 1 rot.
    assert "❌ 31 von 32 Tests grün — 1 Ticket rot — 1 ohne Beleg" in seite
    assert 'class="karte rot"><h2>Ticket #902' in seite
    assert 'class="karte gruen"><h2>Ticket #901' in seite
    assert "python -m pytest tests/test_knopf.py -q" in seite and "20 passed in 2.1s" in seite
    assert "Ticket #904" in seite and "kein Beleg gefunden" in seite
    assert seite.count("ja — jemand hat es am echten System durchgeklickt") == 1  # nur 903
    assert "prefers-color-scheme:dark" in seite and 'name="viewport"' in seite


def test_uebersicht_alles_gruen(repo: Path, tmp_path: Path) -> None:
    vh = repo / "docs" / "verify-hard"
    (vh / "902" / "gruen_lauf.txt").write_text("7 passed in 3s\n", encoding="utf-8")
    (vh / "904.md").write_text("12 passed nach Fix\n", encoding="utf-8")
    ziel = tmp_path / "u.html"
    assert _lauf("test_uebersicht.py", repo, "--aus", str(ziel)).returncode == 0
    assert "✅ 44 von 44 Tests grün" in ziel.read_text(encoding="utf-8")


def _mail_konfig(repo: Path, tmp_path: Path) -> Path:
    postfach = tmp_path / "post.jsonl"
    mailer = tmp_path / "mailer.py"
    mailer.write_text(
        "import sys\nopen(sys.argv[1], 'a', encoding='utf-8').write(sys.stdin.read() + '\\n')\n", encoding="utf-8"
    )
    (repo / ".to-spawn").mkdir()
    konfig = {"mail": {"ziel": "", "nur_kritisch": True, "befehl": [sys.executable, str(mailer), str(postfach)]}}
    (repo / ".to-spawn" / "config.json").write_text(json.dumps(konfig), encoding="utf-8")
    return postfach


def test_abschluss_paket_schreibt_datei_offen_json_und_mailt(repo: Path, tmp_path: Path) -> None:
    postfach = _mail_konfig(repo, tmp_path)
    ordner = tmp_path / "abschluss"
    env = {"TO_SPAWN_ABSCHLUSS_ORDNER": str(ordner), "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "w")}
    links = ["--stage", "https://stage.test", "--rundschau", "https://r.test/1", "--tests", "https://t.test/2"]
    ergebnis = _lauf("abschluss_paket.py", repo, *links, "--neu", "Der Knopf zeigt jetzt den Status.", env=env)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    md = (repo / "docs" / "agents" / f"abschluss_{SPEC}.md").read_text(encoding="utf-8")
    assert "https://stage.test" in md and "https://r.test/1" in md and "https://t.test/2" in md
    assert "- Der Knopf zeigt jetzt den Status." in md and "- Neu: Knopf zeigt den Status." in md
    offen = json.loads((ordner / "offen" / f"projekt_{SPEC}.json").read_text(encoding="utf-8"))
    assert offen["spec"] == SPEC and offen["repo"] == str(repo.resolve()) and offen["tests"] == "https://t.test/2"
    mails = [json.loads(z) for z in postfach.read_text(encoding="utf-8").splitlines() if z.strip()]
    assert [m["betreff"] for m in mails] == [f"Spec {SPEC} fertig — 3 Links"]


def test_abschluss_paket_dry_run_ohne_seiteneffekte(repo: Path, tmp_path: Path) -> None:
    postfach = _mail_konfig(repo, tmp_path)
    ordner = tmp_path / "abschluss"
    env = {"TO_SPAWN_ABSCHLUSS_ORDNER": str(ordner), "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "w")}
    ergebnis = _lauf(
        "abschluss_paket.py", repo, "--stage", "s", "--rundschau", "r", "--tests", "t", "--dry-run", env=env
    )
    assert ergebnis.returncode == 0, ergebnis.stderr
    assert "[Probe] Mail „Spec 900 fertig — 3 Links“: ja" in ergebnis.stdout
    assert not (repo / "docs" / "agents" / f"abschluss_{SPEC}.md").exists()
    assert not ordner.exists() and not postfach.exists()


# --- #366: Abschluss-Paket vor Live (Belegseiten, Direkt-Links, Zugang, Stand) ---------------------

_LINKS = ["--stage", "https://stage.test", "--rundschau", "https://r.test/1", "--tests", "https://t.test/2"]


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        "TO_SPAWN_ABSCHLUSS_ORDNER": str(tmp_path / "abschluss"),
        "TO_SPAWN_WAECHTER_ORDNER": str(tmp_path / "w"),
    }


def _mails(postfach: Path) -> list[dict[str, str]]:
    if not postfach.exists():
        return []
    return [json.loads(z) for z in postfach.read_text(encoding="utf-8").splitlines() if z.strip()]


def _mit_abnahme_ticket(repo: Path) -> None:
    datei = repo / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json"
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["tickets"]["905"] = {"title": "Abnahme am echten Handy durch David"}
    datei.write_text(json.dumps(daten), encoding="utf-8")


def _volle_angaben() -> list[str]:
    return [
        *_LINKS,
        "--belege",
        "https://b.test/3",
        "--direkt",
        "Sound-Pop-up (als Cedrik)=https://stage.test/suite/sound?rolle=supervisor",
        "--direkt",
        "Knopf-Status=https://stage.test/dashboard#status",
        "--basic-auth-nutzer",
        "stage",
        "--app-rolle",
        "Manuel (cmo)",
        "--app-rolle",
        "Cedrik (supervisor)",
        "--bitwarden",
        "Stage Basic-Auth",
        "--neu",
        "Der Knopf zeigt jetzt den Status.",
    ]


def test_abschluss_paket_abnahme_mail_nummeriert_mit_allem(repo: Path, tmp_path: Path) -> None:
    postfach = _mail_konfig(repo, tmp_path)
    _mit_abnahme_ticket(repo)
    ergebnis = _lauf("abschluss_paket.py", repo, *_volle_angaben(), env=_env(tmp_path))
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    mails = _mails(postfach)
    assert [m["betreff"] for m in mails] == [f"Spec {SPEC} bereit zur Abnahme — alles zum Durchschauen"]
    assert mails[0]["art"] == "spec_fertig"
    text = mails[0]["text"]
    reihenfolge = ["1. **Rundschau**", "2. **Belegseiten**", "3. **Test-Übersicht**", "4. **Direkt-Links**",
                   "5. **Zugang**", "## Was ist neu", "## Was du tun musst"]
    stellen = [text.find(t) for t in reihenfolge]
    assert -1 not in stellen and stellen == sorted(stellen), text
    assert "https://r.test/1" in text and "https://b.test/3" in text and "https://t.test/2" in text
    assert "Sound-Pop-up (als Cedrik): https://stage.test/suite/sound?rolle=supervisor" in text
    assert "Knopf-Status: https://stage.test/dashboard#status" in text
    assert "Stage-App: https://stage.test" in text
    assert "Anmelde-Name (Basic-Auth): stage" in text
    assert "App-Rolle: Manuel (cmo)" in text and "App-Rolle: Cedrik (supervisor)" in text
    assert "Passwort: Bitwarden-Eintrag Stage Basic-Auth" in text
    # Standard-Sätze „Was du tun musst“: Zettel + Abnahme-Ticket aus dem Manifest.
    assert "Zettel" in text and "#905" in text
    md = (repo / "docs" / "agents" / f"abschluss_{SPEC}.md").read_text(encoding="utf-8")
    assert md == text
    offen = json.loads((tmp_path / "abschluss" / "offen" / f"projekt_{SPEC}.json").read_text(encoding="utf-8"))
    assert offen["belege"] == "https://b.test/3" and offen["stand"] == "abnahme"
    assert offen["direkt"] == [
        {"titel": "Sound-Pop-up (als Cedrik)", "url": "https://stage.test/suite/sound?rolle=supervisor"},
        {"titel": "Knopf-Status", "url": "https://stage.test/dashboard#status"},
    ]
    assert offen["zugang"] == {
        "basic_auth_nutzer": "stage",
        "app_rollen": ["Manuel (cmo)", "Cedrik (supervisor)"],
        "bitwarden": "Stage Basic-Auth",
    }
    assert "passw" not in json.dumps(offen).lower()


def test_abschluss_paket_tun_saetze_bis_drei(repo: Path, tmp_path: Path) -> None:
    postfach = _mail_konfig(repo, tmp_path)
    tun = ["--tun", "Eins lesen.", "--tun", "Zwei klicken.", "--tun", "Drei freigeben.", "--tun", "Vier zu viel."]
    ergebnis = _lauf("abschluss_paket.py", repo, *_LINKS, "--belege", "https://b.test/3", *tun, env=_env(tmp_path))
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    text = _mails(postfach)[0]["text"]
    teil = text.split("## Was du tun musst", 1)[1]
    assert "Eins lesen." in teil and "Drei freigeben." in teil and "Vier zu viel." not in teil
    assert "Zettel" not in teil


@pytest.mark.parametrize(
    "falsch",
    ["Ohne Gleichheitszeichen https://stage.test", "Titel=stage.test/ohne-schema", "Titel=ftp://stage.test", "=https://x.test"],
)
def test_abschluss_paket_direkt_falsch_exit_2(repo: Path, tmp_path: Path, falsch: str) -> None:
    postfach = _mail_konfig(repo, tmp_path)
    ergebnis = _lauf("abschluss_paket.py", repo, *_LINKS, "--direkt", falsch, env=_env(tmp_path))
    assert ergebnis.returncode == 2, ergebnis.stdout + ergebnis.stderr
    assert "--direkt" in ergebnis.stderr and "Titel" in ergebnis.stderr
    assert not (repo / "docs" / "agents" / f"abschluss_{SPEC}.md").exists()
    assert not (tmp_path / "abschluss").exists() and not postfach.exists()


@pytest.mark.parametrize(
    ("schalter", "wert"),
    [
        ("--bitwarden", "Stage passwort=geheim123"),
        ("--app-rolle", "Manuel (cmo) password: hunter2"),
        ("--basic-auth-nutzer", "stage pw=abc"),
        ("--direkt", "Suite=https://stage:geheim@stage.test/suite"),
        ("--stage", "https://nutzer:pass@stage.test"),
        ("--tun", "Einloggen mit Passwort: abc123"),
        ("--neu", "Neu: PW=xyz"),
        ("--bitwarden", "Zugangspasswort: geheim123"),
        ("--tun", "Adminkennwort=abc"),
        ("--neu", "meinPW=xyz"),
        ("--direkt", "Suite=stage:geheim@stage.test/suite"),
        ("--stage", "nutzer:pass@stage.test"),
    ],
)
def test_abschluss_paket_passwort_schutz_exit_2(repo: Path, tmp_path: Path, schalter: str, wert: str) -> None:
    postfach = _mail_konfig(repo, tmp_path)
    args = [a for a in _LINKS] if schalter != "--stage" else ["--rundschau", "https://r.test/1", "--tests", "https://t.test/2"]
    ergebnis = _lauf("abschluss_paket.py", repo, *args, schalter, wert, env=_env(tmp_path))
    assert ergebnis.returncode == 2, ergebnis.stdout + ergebnis.stderr
    assert "Passwort" in ergebnis.stderr
    assert wert.split()[-1] not in ergebnis.stdout + ergebnis.stderr  # das Geheimnis nie wiederholen
    assert not (repo / "docs" / "agents" / f"abschluss_{SPEC}.md").exists()
    assert not (tmp_path / "abschluss").exists() and not postfach.exists()


def test_abschluss_paket_live_eigene_datei_eigener_schluessel(repo: Path, tmp_path: Path) -> None:
    postfach = _mail_konfig(repo, tmp_path)  # nur_kritisch: true
    env = _env(tmp_path)
    abnahme = _lauf("abschluss_paket.py", repo, *_LINKS, "--belege", "https://b.test/3", env=env)
    assert abnahme.returncode == 0, abnahme.stdout + abnahme.stderr
    live = _lauf("abschluss_paket.py", repo, *_LINKS, "--belege", "https://b.test/3", "--stand", "live", env=env)
    assert live.returncode == 0, live.stdout + live.stderr
    assert [m["betreff"] for m in _mails(postfach)] == [
        f"Spec {SPEC} bereit zur Abnahme — alles zum Durchschauen",
        f"Spec {SPEC} live — alles zum Durchschauen",
    ]
    assert all(m["art"] == "spec_fertig" for m in _mails(postfach))
    assert (repo / "docs" / "agents" / f"abschluss_{SPEC}.md").exists()
    assert (repo / "docs" / "agents" / f"abschluss_{SPEC}_live.md").exists()
    offen = json.loads((tmp_path / "abschluss" / "offen" / f"projekt_{SPEC}.json").read_text(encoding="utf-8"))
    assert offen["stand"] == "live"
    # Zweiter Live-Lauf: Schlüssel abschluss_<S>_live schon gesendet → keine zweite Mail.
    nochmal = _lauf("abschluss_paket.py", repo, *_LINKS, "--belege", "https://b.test/3", "--stand", "live", env=env)
    assert nochmal.returncode == 1
    assert len(_mails(postfach)) == 2


def test_spec_fertig_immer_trotz_nur_kritisch() -> None:
    sys.path.insert(0, str(SKILL))
    from to_spawn import melder

    assert "spec_fertig" in melder.IMMER
    assert melder.darf_raus("spec_fertig", {"mail": {"nur_kritisch": True}})
    assert not melder.darf_raus("irgendwas_leises", {"mail": {"nur_kritisch": True}})


# --- #366: Belegseiten-Übersicht ------------------------------------------------------------------


def test_belege_uebersicht_ohne_github(repo: Path, tmp_path: Path) -> None:
    ziel = tmp_path / "belege.html"
    ergebnis = _lauf("belege_uebersicht.py", repo, "--aus", str(ziel), "--ohne-github")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    assert str(ziel) in ergebnis.stdout
    seite = ziel.read_text(encoding="utf-8")
    for nr, titel in [("901", "Knopf zeigt den Status"), ("902", "Handy schaltet sich aus"),
                      ("903", "Live-Beweis am echten Handy"), ("904", "Ohne Beleg")]:
        assert f"Ticket #{nr}" in seite and titel in seite
    assert seite.count("Status: unbekannt") == 4
    assert "**ABNAHME: ABGENOMMEN (Live)**" in seite  # Beleg-Zeile 903 (erste Zeile mit ABNAHME)
    assert "kein Beleg gefunden" in seite  # 904
    assert "901_gruen.txt" in seite and "901_rot.txt" in seite and "902/gruen_lauf.txt" in seite
    assert "9010_gruen.txt" not in seite  # fremdes Ticket zählt nicht für 901
    assert "prefers-color-scheme:dark" in seite and 'name="viewport"' in seite and 'lang="de"' in seite
    assert "Belegseiten" in seite


def test_belege_uebersicht_status_aus_github(repo: Path, tmp_path: Path) -> None:
    stub = tmp_path / "gh_stub.py"
    stub.write_text(
        "import json, sys\n"
        "nr = sys.argv[3]\n"
        "print(json.dumps({'state': 'CLOSED' if nr in ('901', '903') else 'OPEN'}))\n",
        encoding="utf-8",
    )
    ziel = tmp_path / "belege.html"
    ergebnis = _lauf("belege_uebersicht.py", repo, "--aus", str(ziel), env={"TO_SPAWN_GH_STUB": str(stub)})
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    seite = ziel.read_text(encoding="utf-8")
    assert seite.count("Status: zu") == 2 and seite.count("Status: offen") == 2
    assert "unbekannt" not in seite


def test_belege_uebersicht_manifest_fehlt_exit_2(tmp_path: Path) -> None:
    leer = tmp_path / "leer"
    leer.mkdir()
    ergebnis = _lauf("belege_uebersicht.py", leer, "--ohne-github", "--aus", str(tmp_path / "x.html"))
    assert ergebnis.returncode == 2


# --- #366: Auslöser in den Wächter-Prompts vorgezogen --------------------------------------------


def _prompts() -> dict[str, str]:
    import importlib.util

    sys.path.insert(0, str(SKILL))
    from to_spawn import waechter_takt

    modul_spec = importlib.util.spec_from_file_location("wache_366", SKRIPTE / "wache.py")
    assert modul_spec and modul_spec.loader
    wache = importlib.util.module_from_spec(modul_spec)
    modul_spec.loader.exec_module(wache)
    return {"wache": wache.PROMPT, "takt": waechter_takt.PROMPT}


@pytest.mark.parametrize("name", ["wache", "takt"])
def test_prompt_abschluss_schon_bei_abnahme(name: str) -> None:
    prompt = _prompts()[name]
    assert "bereit zur Abnahme" in prompt
    assert "--stand abnahme" in prompt and "--stand live" in prompt
    assert "skripte/belege_uebersicht.py {S}" in prompt and "skripte/test_uebersicht.py {S}" in prompt
    assert "--belege" in prompt and "--direkt" in prompt
    assert "--basic-auth-nutzer" in prompt and "--app-rolle" in prompt and "--bitwarden" in prompt
    assert "nie Passwort" in prompt


# --- #366 Fixrunde: Prüfpanel-Befunde --------------------------------------------------------------


def test_belege_uebersicht_ticket_kein_dict_exit_2(tmp_path: Path) -> None:
    r = tmp_path / "kaputt"
    (r / ".git").mkdir(parents=True)
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(
        json.dumps({"spec": SPEC, "tickets": {"901": "kaputt"}}), encoding="utf-8"
    )
    ergebnis = _lauf("belege_uebersicht.py", r, "--ohne-github", "--aus", str(tmp_path / "x.html"))
    assert ergebnis.returncode == 2, ergebnis.stdout + ergebnis.stderr
    assert "nicht lesbar" in ergebnis.stderr
    assert "Traceback" not in ergebnis.stderr


def _beleg_zeile_aus(repo: Path, tmp_path: Path, nummer: str) -> str:
    ziel = tmp_path / "belege.html"
    ergebnis = _lauf("belege_uebersicht.py", repo, "--aus", str(ziel), "--ohne-github")
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    seite = ziel.read_text(encoding="utf-8")
    karte = seite.split(f"Ticket #{nummer}", 1)[1]
    return karte.split("<dt>Beleg-Zeile</dt><dd><code>", 1)[1].split("</code>", 1)[0]


def test_belege_uebersicht_tabelle_vor_abnahme(repo: Path, tmp_path: Path) -> None:
    (repo / "docs" / "verify-hard" / "901.md").write_text(
        "# Belegseite #901\n| Achse | Beleg |\n|---|---|\n| SPEC | 5/5 belegt |\n\n**ABNAHME: ABGENOMMEN**\n",
        encoding="utf-8",
    )
    zeile = _beleg_zeile_aus(repo, tmp_path, "901")
    assert "ABNAHME" in zeile and "| Achse | Beleg |" not in zeile


def test_belege_uebersicht_tabellenkopf_nie_beleg_zeile(repo: Path, tmp_path: Path) -> None:
    (repo / "docs" / "verify-hard" / "901.md").write_text(
        "# Belegseite #901\n| Was | Beleg |\n| --- | :---: |\n| Tests | grün |\n| Beweis | Beleg: 20 passed |\n",
        encoding="utf-8",
    )
    zeile = _beleg_zeile_aus(repo, tmp_path, "901")
    assert zeile == "| Beweis | Beleg: 20 passed |", zeile


def test_abschluss_paket_live_standard_tun_saetze(repo: Path, tmp_path: Path) -> None:
    postfach = _mail_konfig(repo, tmp_path)
    _mit_abnahme_ticket(repo)
    live = _lauf(
        "abschluss_paket.py", repo, *_LINKS, "--belege", "https://b.test/3", "--stand", "live", env=_env(tmp_path)
    )
    assert live.returncode == 0, live.stdout + live.stderr
    teil = _mails(postfach)[0]["text"].split("## Was du tun musst", 1)[1]
    assert "Die echte App kurz über die Direkt-Links durchklicken." in teil
    assert "Abnahme-Ticket #905 schließen." in teil
    assert "Zettel" not in teil


# --- #586: eine Mail je Spec — ablegen + nachsehen ------------------------------------------------

_FERTIG = "2026-10-06T10:00:00+02:00"
_RUECKBLICK = "Rückblick: keine Vorschläge\n\nDatenlage: 4 Tickets, 0 Vorfälle, 2 Bau-Logs.\n"
_THERMO = "# Thermo 900\n\n- to_spawn/capo.py wächst (2143 Zeilen)\n"


def _unter(befehl: str, repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SKRIPTE / "abschluss_paket.py"), befehl, str(SPEC), "--repo", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})},
        timeout=120,
        check=False,
    )


def _wirkungskreis(repo: Path, exit_code: int, text: str) -> None:
    skript = repo / "scripts" / "wirkungskreis.py"
    skript.parent.mkdir(parents=True, exist_ok=True)
    skript.write_text(f"import sys\nsys.stdout.write({text!r})\nsys.exit({exit_code})\n", encoding="utf-8")


def _marker(repo: Path, rueckblick: bool = True, thermo: bool = True) -> None:
    ordner = repo / "docs" / "agents"
    if rueckblick:
        (ordner / f"rueckblick_{SPEC}.md").write_text(_RUECKBLICK, encoding="utf-8")
    if thermo:
        (ordner / f"thermo_{SPEC}.md").write_text(_THERMO, encoding="utf-8")


def _abgelegt(repo: Path, tmp_path: Path) -> Path:
    postfach = _mail_konfig(repo, tmp_path)
    bau_log = repo / ".to-spawn" / "bau_log"
    bau_log.mkdir(parents=True)
    zeilen = [
        {"ts": "2026-10-06T08:00:00+02:00", "typ": "zusammenfassung", "ticket": "901", "umfang": "alter Stand"},
        {"ts": "2026-10-06T09:00:00+02:00", "typ": "zusammenfassung", "ticket": "901", "umfang": "Knopf zeigt Status"},
    ]
    (bau_log / "901.jsonl").write_text("".join(json.dumps(z) + "\n" for z in zeilen), encoding="utf-8")
    _wirkungskreis(repo, 0, "Wirkungskreis: web/app.py, web/static/knopf.js\n")
    ablage = _unter("ablegen", repo, *_LINKS, "--spec-fertig", _FERTIG, env=_env(tmp_path))
    assert ablage.returncode == 0, ablage.stdout + ablage.stderr
    assert (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").is_file()
    assert _mails(postfach) == []  # ablegen mailt nie
    return postfach


def test_nachsehen_probe_drei_teile_in_reihenfolge(repo: Path, tmp_path: Path) -> None:
    postfach = _abgelegt(repo, tmp_path)
    _marker(repo)
    probe = _unter("nachsehen", repo, "--jetzt", "2026-10-06T10:05:00+02:00", "--dry-run", env=_env(tmp_path))
    assert probe.returncode == 0, probe.stdout + probe.stderr
    text = probe.stdout
    spec, system, code = (text.index(k) for k in ("## Spec-Teil", "## System-Teil", "## Code-Befunde"))
    assert spec < system < code
    assert text.index("Spec 900 fertig — 3 Links") < system
    assert _mails(postfach) == [] and not (repo / "docs" / "agents" / f"abschluss_{SPEC}.md").exists()


def test_nachsehen_je_ticket_umfang_belegseite_wirkungskreis(repo: Path, tmp_path: Path) -> None:
    _abgelegt(repo, tmp_path)
    _marker(repo)
    text = _unter("nachsehen", repo, "--jetzt", "2026-10-06T10:05:00+02:00", "--dry-run", env=_env(tmp_path)).stdout
    teil = text.split("## Spec-Teil", 1)[1].split("## System-Teil", 1)[0]
    assert "#901 Knopf zeigt den Status — Knopf zeigt Status" in teil  # jüngste zusammenfassung
    assert "#902 Handy schaltet sich aus — kein Bau-Log" in teil
    assert "docs/verify-hard/901_gruen.txt" in teil and "docs/verify-hard/903.md" in teil
    assert "docs/verify-hard/9010" not in teil  # fremde Nummer zählt nicht
    assert "#904 Ohne Beleg — kein Bau-Log · Vorher/Nachher: Belegseite fehlt" in teil
    assert "Wirkungskreis: web/app.py, web/static/knopf.js" in teil.split("Wirkungskreis", 1)[1]
    _wirkungskreis(repo, 2, "nicht ermittelbar: kein Merge-Commit\n")
    text = _unter("nachsehen", repo, "--jetzt", "2026-10-06T10:05:00+02:00", "--dry-run", env=_env(tmp_path)).stdout
    assert "nicht ermittelbar: kein Merge-Commit" in text


def test_nachsehen_rueckblick_marker_unveraendert(repo: Path, tmp_path: Path) -> None:
    _abgelegt(repo, tmp_path)
    _marker(repo)
    text = _unter("nachsehen", repo, "--jetzt", "2026-10-06T10:05:00+02:00", "--dry-run", env=_env(tmp_path)).stdout
    system = text.split("## System-Teil", 1)[1].split("## Code-Befunde", 1)[0]
    assert _RUECKBLICK in system
    assert f"Abhaken per Chat: „Rückblick {SPEC}: 1 ja, 2 nein“" in system
    assert _THERMO in text.split("## Code-Befunde", 1)[1]


def test_nachsehen_marker_fehlt_wartet_dann_vermerk(repo: Path, tmp_path: Path) -> None:
    postfach = _abgelegt(repo, tmp_path)
    _marker(repo, rueckblick=False)
    frueh = _unter("nachsehen", repo, "--jetzt", "2026-10-06T11:59:00+02:00", env=_env(tmp_path))
    assert frueh.returncode == 3, frueh.stdout + frueh.stderr
    assert _mails(postfach) == [] and not (repo / "docs" / "agents" / f"abschluss_{SPEC}.md").exists()
    spaet = _unter("nachsehen", repo, "--jetzt", "2026-10-06T12:01:00+02:00", env=_env(tmp_path))
    assert spaet.returncode == 0, spaet.stdout + spaet.stderr
    mails = _mails(postfach)
    assert [m["betreff"] for m in mails] == [f"Spec {SPEC} fertig — 3 Links"] and mails[0]["art"] == "spec_fertig"
    system = mails[0]["text"].split("## System-Teil", 1)[1].split("## Code-Befunde", 1)[0]
    assert (
        f"Rückblick fehlgeschlagen: Rückblick-Marker docs/agents/rueckblick_{SPEC}.md fehlt "
        "120 Minuten nach SPEC FERTIG" in system
    )
    assert _THERMO in mails[0]["text"]
    assert (repo / "docs" / "agents" / f"abschluss_{SPEC}.md").is_file()


def test_nachsehen_zweimal_genau_eine_mail(repo: Path, tmp_path: Path) -> None:
    postfach = _abgelegt(repo, tmp_path)
    _marker(repo)
    for _ in range(2):
        lauf = _unter("nachsehen", repo, "--jetzt", "2026-10-06T10:05:00+02:00", env=_env(tmp_path))
        assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert len(_mails(postfach)) == 1


def test_nachsehen_ohne_ablage_exit_2(repo: Path, tmp_path: Path) -> None:
    lauf = _unter("nachsehen", repo, env=_env(tmp_path))
    assert lauf.returncode == 2 and "Ablage" in lauf.stderr


def test_ablegen_zweimal_erster_spec_fertig_gewinnt(repo: Path, tmp_path: Path) -> None:
    _abgelegt(repo, tmp_path)
    nochmal = _unter("ablegen", repo, *_LINKS, "--spec-fertig", "2026-10-06T13:00:00+02:00", env=_env(tmp_path))
    assert nochmal.returncode == 0, nochmal.stdout + nochmal.stderr
    _marker(repo, thermo=False)
    # 121 min nach dem ERSTEN Zeitpunkt → Vermerk statt Warten.
    lauf = _unter("nachsehen", repo, "--jetzt", "2026-10-06T12:01:00+02:00", "--dry-run", env=_env(tmp_path))
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert f"Rückblick fehlgeschlagen: Thermo-Marker docs/agents/thermo_{SPEC}.md fehlt" in lauf.stdout
