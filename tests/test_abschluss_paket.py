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
from rundschau_beispiel import rundschau_args

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


@pytest.mark.xfail(strict=True, reason="überholt durch #885: tagsüber keine Mail je Spec (Entscheidung 27.11.)")
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


@pytest.mark.xfail(strict=True, reason="überholt durch #885: tagsüber keine Mail je Spec (Entscheidung 27.11.)")
def test_abschluss_paket_abnahme_mail_nummeriert_mit_allem(repo: Path, tmp_path: Path) -> None:
    postfach = _mail_konfig(repo, tmp_path)
    _mit_abnahme_ticket(repo)
    ergebnis = _lauf("abschluss_paket.py", repo, *_volle_angaben(), env=_env(tmp_path))
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    mails = _mails(postfach)
    assert [m["betreff"] for m in mails] == [f"Spec {SPEC} bereit zur Abnahme — alles zum Durchschauen"]
    assert mails[0]["art"] == "spec_fertig"
    text = mails[0]["text"]
    reihenfolge = [
        "1. **Rundschau**",
        "2. **Belegseiten**",
        "3. **Test-Übersicht**",
        "4. **Direkt-Links**",
        "5. **Zugang**",
        "## Was ist neu",
        "## Was du tun musst",
    ]
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


@pytest.mark.xfail(strict=True, reason="überholt durch #885: tagsüber keine Mail je Spec (Entscheidung 27.11.)")
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
    [
        "Ohne Gleichheitszeichen https://stage.test",
        "Titel=stage.test/ohne-schema",
        "Titel=ftp://stage.test",
        "=https://x.test",
    ],
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
    args = (
        [a for a in _LINKS]
        if schalter != "--stage"
        else ["--rundschau", "https://r.test/1", "--tests", "https://t.test/2"]
    )
    ergebnis = _lauf("abschluss_paket.py", repo, *args, schalter, wert, env=_env(tmp_path))
    assert ergebnis.returncode == 2, ergebnis.stdout + ergebnis.stderr
    assert "Passwort" in ergebnis.stderr
    assert wert.split()[-1] not in ergebnis.stdout + ergebnis.stderr  # das Geheimnis nie wiederholen
    assert not (repo / "docs" / "agents" / f"abschluss_{SPEC}.md").exists()
    assert not (tmp_path / "abschluss").exists() and not postfach.exists()


@pytest.mark.xfail(strict=True, reason="überholt durch #885: tagsüber keine Mail je Spec (Entscheidung 27.11.)")
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


@pytest.mark.xfail(strict=True, reason="überholt durch #885: tagsüber keine Mail je Spec (Entscheidung 27.11.)")
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
    for nr, titel in [
        ("901", "Knopf zeigt den Status"),
        ("902", "Handy schaltet sich aus"),
        ("903", "Live-Beweis am echten Handy"),
        ("904", "Ohne Beleg"),
    ]:
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


@pytest.mark.xfail(strict=True, reason="überholt durch #885: tagsüber keine Mail je Spec (Entscheidung 27.11.)")
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


def _deck(befehl: str, repo: Path, args: tuple[str, ...]) -> list[str]:
    """#529: Die Abnahme verlangt ein gültiges Rundschau-Deck — echt aus den Entscheidungen des Repos."""
    if befehl != "ablegen" or "--dry-run" in args or "--stand" in args:
        return []
    return rundschau_args(repo, SPEC)


def _unter(befehl: str, repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SKRIPTE / "abschluss_paket.py"),
            befehl,
            str(SPEC),
            "--repo",
            str(repo),
            *args,
            *_deck(befehl, repo, args),
        ],
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


def _git_stand(repo: Path) -> None:
    (repo / ".git").rmdir()
    for befehl in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", *befehl], cwd=repo, check=True, capture_output=True)


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
    _git_stand(repo)  # Belegseiten liest capo.belegseiten aus dem Git-Stand (git ls-tree)
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


@pytest.mark.xfail(strict=True, reason="überholt durch #885: tagsüber keine Mail je Spec (Entscheidung 27.11.)")
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


@pytest.mark.xfail(strict=True, reason="überholt durch #885: tagsüber keine Mail je Spec (Entscheidung 27.11.)")
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


# --- #586 Fixrunde: nachsehen stürzt nie ab, Ablage je Stand, früher Doppel-Schutz -------------------


@pytest.mark.xfail(strict=True, reason="überholt durch #885: tagsüber keine Mail je Spec (Entscheidung 27.11.)")
def test_nachsehen_kaputter_marker_mail_geht_trotzdem(repo: Path, tmp_path: Path) -> None:
    postfach = _abgelegt(repo, tmp_path)
    _marker(repo)
    (repo / "docs" / "agents" / f"rueckblick_{SPEC}.md").write_bytes(b"\xff\xfe kaputt \xc3")
    lauf = _unter("nachsehen", repo, "--jetzt", "2026-10-06T10:05:00+02:00", env=_env(tmp_path))
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    mails = _mails(postfach)
    assert len(mails) == 1
    assert f"Rückblick fehlgeschlagen: Rückblick-Marker docs/agents/rueckblick_{SPEC}.md unlesbar:" in mails[0]["text"]


def test_ablage_je_stand_live_ueberschreibt_staging_nicht(repo: Path, tmp_path: Path) -> None:
    _abgelegt(repo, tmp_path)
    staging = (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").read_text(encoding="utf-8")
    live = _unter("ablegen", repo, *_LINKS, "--stand", "live", "--spec-fertig", _FERTIG, env=_env(tmp_path))
    assert live.returncode == 0, live.stdout + live.stderr
    assert (repo / ".to-spawn" / f"abschluss_{SPEC}_paket.json").read_text(encoding="utf-8") == staging
    assert (
        json.loads((repo / ".to-spawn" / f"abschluss_{SPEC}_live_paket.json").read_text())["paket"]["stand"] == "live"
    )
    _marker(repo)
    probe = _unter(
        "nachsehen", repo, "--stand", "live", "--jetzt", "2026-10-06T10:05:00+02:00", "--dry-run", env=_env(tmp_path)
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert f"Spec {SPEC} live — alles zum Durchschauen" in probe.stdout


@pytest.mark.xfail(strict=True, reason="überholt durch #885: tagsüber keine Mail je Spec (Entscheidung 27.11.)")
def test_nachsehen_nach_versand_ohne_marker_exit_0_keine_zweite_mail(repo: Path, tmp_path: Path) -> None:
    postfach = _abgelegt(repo, tmp_path)
    _marker(repo)
    erst = _unter("nachsehen", repo, "--jetzt", "2026-10-06T10:05:00+02:00", env=_env(tmp_path))
    assert erst.returncode == 0, erst.stdout + erst.stderr
    (repo / "docs" / "agents" / f"rueckblick_{SPEC}.md").unlink()
    (repo / "docs" / "agents" / f"thermo_{SPEC}.md").unlink()
    nochmal = _unter("nachsehen", repo, "--jetzt", "2026-10-06T10:06:00+02:00", env=_env(tmp_path))
    assert nochmal.returncode == 0, nochmal.stdout + nochmal.stderr
    assert len(_mails(postfach)) == 1


def test_nachsehen_wirkungskreis_leer(repo: Path, tmp_path: Path) -> None:
    _abgelegt(repo, tmp_path)
    _marker(repo)
    _wirkungskreis(repo, 0, "")
    text = _unter("nachsehen", repo, "--jetzt", "2026-10-06T10:05:00+02:00", "--dry-run", env=_env(tmp_path)).stdout
    assert "Wirkungskreis nicht ermittelt: keine Ausgabe" in text


# --- #366 Teil 2: Test-Übersicht zählt den aktuellen Stand, zeigt Herkunft je Karte ---------------


def _commit_am(repo: Path, datum: str) -> None:
    """Alles committen mit fester Commit-Zeit (der Beleg entstand an ``datum``)."""
    zeit = {"GIT_AUTHOR_DATE": datum, "GIT_COMMITTER_DATE": datum}
    for befehl in (
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", datum],
    ):
        subprocess.run(
            ["git", *befehl],
            cwd=repo,
            check=True,
            capture_output=True,
            env={**os.environ, **zeit},
        )


def test_uebersicht_zaehlt_neuesten_lauf_nicht_groessten(tmp_path: Path) -> None:
    """Älterer, breiterer Lauf (rot) darf den neueren grünen Lauf nicht verdecken (#366).

    Nach einem Checkout tragen alle Belegdateien dieselbe Änderungszeit; was zählt, ist,
    wann der Beleg entstand (Commit-Zeit).
    """
    r = tmp_path / "projekt"
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    tickets = {
        "901": {"title": "Alt gegen neu"},
        "902": {"title": "Zwei Zahlen in einer Zeile"},
        "903": {"title": "Nur Text"},
    }
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(
        json.dumps({"spec": SPEC, "tickets": tickets}), encoding="utf-8"
    )
    vh = r / "docs" / "verify-hard"
    vh.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=r, check=True, capture_output=True)
    (vh / "901_breit_vorher.txt").write_text("pytest tests/\n2 failed, 38 passed in 9s\n", encoding="utf-8")
    _commit_am(r, "2026-11-01T10:00:00+01:00")
    (vh / "901_gruen.txt").write_text("pytest tests/test_knopf.py\n12 passed in 2s\n", encoding="utf-8")
    (vh / "902.md").write_text(
        "# Belegseite #902\nErgebnis: 54 passed (vor Fix). Nach Fix: 71 passed in 4s\n",
        encoding="utf-8",
    )
    (vh / "903.md").write_text("# Belegseite #903\nNoch nichts gelaufen.\n", encoding="utf-8")
    _commit_am(r, "2026-11-02T11:30:00+01:00")
    for datei in vh.iterdir():  # Checkout: alle Dateien gleich alt
        os.utime(datei, (1_700_000_000, 1_700_000_000))
    ziel = tmp_path / "u.html"
    ergebnis = _lauf("test_uebersicht.py", r, "--aus", str(ziel))
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    seite = ziel.read_text(encoding="utf-8")
    # #366 W1: der neuere grüne Teil-Lauf deckt den älteren breiten roten Lauf nicht ab → offen.
    assert 'class="karte offen"><h2>Ticket #901' in seite
    assert "(12 von 12 Tests grün)" in seite and "38 von 40" not in seite
    assert "docs/verify-hard/901_gruen.txt</code> · Stand 2026-11-02" in seite
    assert "(71 von 71 Tests grün)" in seite
    karte_903 = seite.split("Ticket #903", 1)[1].split("</section>", 1)[0]
    assert "offen – kein Testlauf gefunden" in karte_903
    assert "83 von 83 Tests grün" in seite


def test_uebersicht_ohne_jeden_lauf_keine_zahl(repo: Path, tmp_path: Path) -> None:
    """Kein einziger auswertbarer Lauf → keine Zahl wie „0 von 4“, sondern „offen“ (#366)."""
    vh = repo / "docs" / "verify-hard"
    for datei in [*vh.rglob("*")]:
        if datei.is_file():
            datei.unlink()
    ziel = tmp_path / "u.html"
    assert _lauf("test_uebersicht.py", repo, "--aus", str(ziel)).returncode == 0
    kopf = ziel.read_text(encoding="utf-8").split("<h1>", 1)[1].split("</h1>", 1)[0]
    assert kopf == "⚠️ offen – kein Testlauf gefunden — 4 ohne Beleg"


def _belege_mod():  # noqa: ANN202 - Modul aus dem Skill
    sys.path.insert(0, str(SKILL))
    from to_spawn import belege

    return belege


def test_uebersicht_neuer_teil_lauf_verdeckt_roten_lauf_nicht(tmp_path: Path) -> None:
    """Neuer grüner Teil-Lauf, älterer breiter Lauf rot → offen, Karte nennt beide Dateien (#366 W1).

    Umgekehrt (neuer Lauf rot) bleibt rot.
    """
    r = tmp_path / "projekt"
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    tickets = {"901": {"title": "Teil-Lauf"}, "902": {"title": "Neu rot"}}
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(
        json.dumps({"spec": SPEC, "tickets": tickets}), encoding="utf-8"
    )
    vh = r / "docs" / "verify-hard"
    vh.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=r, check=True, capture_output=True)
    (vh / "901_breit.txt").write_text("3 failed, 37 passed in 9s\n", encoding="utf-8")
    (vh / "902_breit.txt").write_text("40 passed in 9s\n", encoding="utf-8")
    _commit_am(r, "2026-11-01T10:00:00+01:00")
    (vh / "901_teil.txt").write_text("5 passed in 1s\n", encoding="utf-8")
    (vh / "902_teil.txt").write_text("1 failed, 4 passed in 1s\n", encoding="utf-8")
    _commit_am(r, "2026-11-02T11:30:00+01:00")
    ziel = tmp_path / "u.html"
    assert _lauf("test_uebersicht.py", r, "--aus", str(ziel)).returncode == 0
    seite = ziel.read_text(encoding="utf-8")
    karte_901 = seite.split("Ticket #901", 1)[1].split("</section>", 1)[0]
    assert seite.count('class="karte offen"><h2>Ticket #901') == 1
    assert "901_teil.txt" in karte_901 and "901_breit.txt" in karte_901
    assert "deckt" in karte_901 and "kein Beleg gefunden" not in karte_901
    assert 'class="karte rot"><h2>Ticket #902' in seite
    b = _belege_mod().auswerten(vh, "901")
    assert b.status == "offen" and b.verdeckt is not None and b.verdeckt.datei == "901_breit.txt"


def test_belege_ohne_git_zaehlt_aenderungszeit(tmp_path: Path) -> None:
    """Außerhalb eines Git-Ordners entscheidet die Änderungszeit, welcher Lauf der neueste ist (#366)."""
    vh = tmp_path / "verify-hard"
    vh.mkdir()
    (vh / "905_a.txt").write_text("40 passed in 9s\n", encoding="utf-8")
    (vh / "905_b.txt").write_text("1 failed, 4 passed in 1s\n", encoding="utf-8")
    os.utime(vh / "905_a.txt", (1_700_000_000, 1_700_000_000))
    os.utime(vh / "905_b.txt", (1_700_000_500, 1_700_000_500))
    b = _belege_mod().auswerten(vh, "905")
    assert b.lauf is not None and b.lauf.datei == "905_b.txt" and b.lauf.zeit == 1_700_000_500
    assert b.status == "rot"


def test_belege_behobener_roter_lauf_blockiert_nicht(tmp_path: Path) -> None:
    """Breit rot → breit grün → klein grün: der rote Lauf ist schon abgelöst → grün (#366 P1).

    Breit rot → klein grün bleibt offen, der Hinweis nennt die nötige Testzahl.
    """
    vh = tmp_path / "verify-hard"
    vh.mkdir()
    laeufe = {
        "906_a.txt": "3 failed, 37 passed in 9s\n",
        "906_b.txt": "40 passed in 9s\n",
        "906_c.txt": "5 passed in 1s\n",
        "907_a.txt": "3 failed, 37 passed in 9s\n",
        "907_c.txt": "5 passed in 1s\n",
    }
    for i, (name, text) in enumerate(laeufe.items()):
        (vh / name).write_text(text, encoding="utf-8")
        zeit = 1_700_000_000 + 100 * i
        os.utime(vh / name, (zeit, zeit))
    b = _belege_mod().auswerten(vh, "906")
    assert b.lauf is not None and b.lauf.datei == "906_c.txt"
    assert b.status == "gruen" and b.verdeckt is None and b.hinweis == ""
    b = _belege_mod().auswerten(vh, "907")
    assert b.status == "offen" and b.verdeckt is not None and b.verdeckt.datei == "907_a.txt"
    assert b.hinweis == "grüner Testlauf mit mindestens 40 Tests (so viele wie der rote Lauf 907_a.txt)"


def test_uebersicht_abnahme_rot_und_kopfzeile_ungeklaert(tmp_path: Path) -> None:
    """Karte rot durch Abnahme-Zeile zeigt „rot“, nicht „offen“; Kopfzeile nennt „ungeklärt“ (#366 N1/N4)."""
    r = tmp_path / "projekt"
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    tickets = {"911": {"title": "Abnahme rot"}, "912": {"title": "Ungeklärt"}}
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(
        json.dumps({"spec": SPEC, "tickets": tickets}), encoding="utf-8"
    )
    vh = r / "docs" / "verify-hard"
    vh.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=r, check=True, capture_output=True)
    for n in ("911", "912"):
        (vh / f"{n}_breit.txt").write_text("3 failed, 37 passed in 9s\n", encoding="utf-8")
    _commit_am(r, "2026-11-01T10:00:00+01:00")
    for n in ("911", "912"):
        (vh / f"{n}_teil.txt").write_text("5 passed in 1s\n", encoding="utf-8")
    (vh / "911.md").write_text("# Belegseite #911\n**ABNAHME: ROT (Knopf fehlt)**\n", encoding="utf-8")
    _commit_am(r, "2026-11-02T11:30:00+01:00")
    ziel = tmp_path / "u.html"
    assert _lauf("test_uebersicht.py", r, "--aus", str(ziel)).returncode == 0
    seite = ziel.read_text(encoding="utf-8")
    karte_911 = seite.split("Ticket #911", 1)[1].split("</section>", 1)[0]
    assert 'class="karte rot"><h2>Ticket #911' in seite
    assert "❌ rot" in karte_911 and "⚠️ offen" not in karte_911
    karte_912 = seite.split("Ticket #912", 1)[1].split("</section>", 1)[0]
    assert "mindestens 40 Tests (so viele wie der rote Lauf 912_breit.txt)" in karte_912
    kopf = seite.split("<h1>", 1)[1].split("</h1>", 1)[0]
    assert kopf == (
        "❌ 10 von 10 Tests grün — 1 Ticket rot — 1 ungeklärt (neuer Teil-Lauf deckt alten roten Lauf nicht ab)"
    )


def test_uebersicht_kopfzeile_nur_per_abnahme_gruen(tmp_path: Path) -> None:
    """Alle Tickets nur über die ABNAHME-Zeile grün → Kopfzeile sagt das, keine Testzahl (#366 N4)."""
    r = tmp_path / "projekt"
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    tickets = {"921": {"title": "A"}, "922": {"title": "B"}}
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(
        json.dumps({"spec": SPEC, "tickets": tickets}), encoding="utf-8"
    )
    vh = r / "docs" / "verify-hard"
    vh.mkdir(parents=True)
    for n in tickets:
        (vh / f"{n}.md").write_text(f"# Belegseite #{n}\n**ABNAHME: ABGENOMMEN (Live)**\n", encoding="utf-8")
    ziel = tmp_path / "u.html"
    assert _lauf("test_uebersicht.py", r, "--aus", str(ziel)).returncode == 0
    kopf = ziel.read_text(encoding="utf-8").split("<h1>", 1)[1].split("</h1>", 1)[0]
    assert kopf == "✅ 2 von 2 Tickets per Abnahme grün (kein Testlauf)"


def test_uebersicht_nur_abnahme_eindeutig(repo: Path, tmp_path: Path) -> None:
    """Grün nur über die ABNAHME-Zeile: keine Marke „grün“ plus „offen“, Herkunft = Abnahme-Zeile (#366)."""
    vh = repo / "docs" / "verify-hard"
    for datei in [*vh.rglob("*")]:
        if datei.is_file():
            datei.unlink()
    (vh / "903.md").write_text("# Belegseite #903\n**ABNAHME: ABGENOMMEN (Live)**\n", encoding="utf-8")
    ziel = tmp_path / "u.html"
    assert _lauf("test_uebersicht.py", repo, "--aus", str(ziel)).returncode == 0
    seite = ziel.read_text(encoding="utf-8")
    karte_903 = seite.split("Ticket #903", 1)[1].split("</section>", 1)[0]
    assert 'karte gruen"><h2>Ticket #903' in seite
    assert "offen" not in karte_903
    assert "Abnahme-Zeile in <code>docs/verify-hard/903.md</code>" in karte_903


# --- #472: unittest-/node-Belege unter docs/verify-hard/*_gruen.txt + gesuchter Pfad -------------


def test_uebersicht_erkennt_unittest_gruen_belege_472(tmp_path: Path) -> None:
    """Grüne unittest-Belege unter docs/verify-hard/*_gruen.txt werden angezeigt (#472)."""
    r = tmp_path / "projekt"
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    tickets = {
        "12": {"title": "Gesund-Abruf"},
        "13": {"title": "Version"},
        "14": {"title": "Mit Übersprungen"},
        "15": {"title": "Roter Lauf"},
    }
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(
        json.dumps({"spec": SPEC, "tickets": tickets}), encoding="utf-8"
    )
    vh = r / "docs" / "verify-hard"
    vh.mkdir(parents=True)
    (vh / "12_rot.txt").write_text("Ran 3 tests in 1.018s\n\nFAILED (errors=1)\nEXIT 1\n", encoding="utf-8")
    (vh / "12_gruen.txt").write_text(
        "$ python -m unittest discover -s tests -t .\n...\n"
        "----------------------------------------------------------------------\n"
        "Ran 3 tests in 1.019s\n\nOK\nEXIT 0\n",
        encoding="utf-8",
    )
    (vh / "13_gruen.txt").write_text("Ran 6 tests in 1.024s\nOK\n", encoding="utf-8")
    (vh / "14_gruen.txt").write_text("Ran 5 tests in 0.420s\n\nOK (skipped=1)\n", encoding="utf-8")
    (vh / "15_gruen.txt").write_text("Ran 5 tests in 0.500s\n\nFAILED (failures=2, errors=1)\n", encoding="utf-8")
    ziel = tmp_path / "u.html"
    ergebnis = _lauf("test_uebersicht.py", r, "--aus", str(ziel))
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    seite = ziel.read_text(encoding="utf-8")
    # 3 (#12) + 6 (#13) + 4 (#14: 5 minus 1 skipped) + 2 (#15: 5 minus 3 failed) = 15 grün von 18.
    assert "❌ 15 von 18 Tests grün — 1 Ticket rot" in seite
    assert 'class="karte gruen"><h2>Ticket #12' in seite
    assert "python -m unittest discover -s tests -t ." in seite
    assert "Ran 3 tests in 1.019s — OK" in seite
    assert 'class="karte gruen"><h2>Ticket #13' in seite
    assert "(6 von 6 Tests grün)" in seite
    assert 'class="karte gruen"><h2>Ticket #14' in seite
    assert "(4 von 4 Tests grün)" in seite
    assert 'class="karte rot"><h2>Ticket #15' in seite
    assert "(2 von 5 Tests grün)" in seite
    assert "kein Testlauf gefunden" not in seite


def test_belege_unittest_grenzfaelle_und_node_test_472(tmp_path: Path) -> None:
    """Abgebrochener/leerer unittest-Lauf zählt nicht als grün; node --test wird erkannt (#472)."""
    vh = tmp_path / "verify-hard"
    vh.mkdir()
    (vh / "21_gruen.txt").write_text("Ran 0 tests in 0.000s\n\nNO TESTS RAN\n", encoding="utf-8")
    (vh / "22_gruen.txt").write_text("Ran 4 tests in 0.100s\n", encoding="utf-8")
    (vh / "23_gruen.txt").write_text("Ran 2 tests in 0.050s\n\nOK (skipped=2)\n", encoding="utf-8")
    (vh / "24_gruen.txt").write_text(
        "$ node --test tests/app.test.js\nℹ tests 8\nℹ suites 2\nℹ pass 8\nℹ fail 0\n",
        encoding="utf-8",
    )
    belege = _belege_mod()
    for nr in ("21", "22", "23"):
        b = belege.auswerten(vh, nr)
        assert b.lauf is None and b.status == "offen", f"Ticket #{nr} darf nicht grün sein: {b}"
    b24 = belege.auswerten(vh, "24")
    assert b24.status == "gruen" and b24.lauf is not None
    assert (b24.lauf.passed, b24.lauf.failed) == (8, 0)
    assert b24.lauf.befehl == "node --test tests/app.test.js"


def test_uebersicht_ohne_beleg_nennt_gesuchten_pfad_472(repo: Path, tmp_path: Path) -> None:
    """Fehlt wirklich jeder Beleg, nennt die Meldung den gesuchten Pfad in Seite und Log (#472)."""
    vh = repo / "docs" / "verify-hard"
    for datei in [*vh.rglob("*")]:
        if datei.is_file():
            datei.unlink()
    ziel = tmp_path / "u.html"
    ergebnis = _lauf("test_uebersicht.py", repo, "--aus", str(ziel))
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    seite = ziel.read_text(encoding="utf-8")
    kopf = seite.split("<h1>", 1)[1].split("</h1>", 1)[0]
    assert kopf == "⚠️ offen – kein Testlauf gefunden — 4 ohne Beleg"
    erwarteter_pfad = str(vh.resolve())
    assert erwarteter_pfad in seite
    assert "docs/verify-hard/&lt;N&gt;_gruen.txt" in seite
    assert erwarteter_pfad in ergebnis.stderr
    karte_901 = seite.split("Ticket #901", 1)[1].split("</section>", 1)[0]
    assert "docs/verify-hard/901_gruen.txt" in karte_901
    assert erwarteter_pfad in karte_901


def test_uebersicht_folgt_konfiguriertem_belege_ordner_472(tmp_path: Path) -> None:
    """Konfigurierter regularien.belege_ordner wird gelesen und als Suchpfad angezeigt (#472)."""
    r = tmp_path / "projekt"
    (r / ".to-spawn").mkdir(parents=True)
    (r / ".to-spawn" / "config.json").write_text(
        json.dumps({"regularien": {"belege_ordner": "belege/pruef"}}), encoding="utf-8"
    )
    (r / "docs" / "agents" / "manifests").mkdir(parents=True)
    (r / "docs" / "agents" / "manifests" / f"spec-{SPEC}.json").write_text(
        json.dumps({"spec": SPEC, "tickets": {"50": {"title": "Eigener Ordner"}, "51": {"title": "Fehlt"}}}),
        encoding="utf-8",
    )
    ordner = r / "belege" / "pruef"
    ordner.mkdir(parents=True)
    (ordner / "50_gruen.txt").write_text("Ran 4 tests in 0.200s\n\nOK\n", encoding="utf-8")
    ziel = tmp_path / "u.html"
    ergebnis = _lauf("test_uebersicht.py", r, "--aus", str(ziel))
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    seite = ziel.read_text(encoding="utf-8")
    assert "⚠️ 4 von 4 Tests grün — 1 ohne Beleg" in seite
    assert "belege/pruef/50_gruen.txt" in seite
    karte_51 = seite.split("Ticket #51", 1)[1].split("</section>", 1)[0]
    assert "belege/pruef/51_gruen.txt" in karte_51
    assert str(ordner.resolve()) in karte_51
