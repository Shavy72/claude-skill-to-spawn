"""#885 (aus App #320): Morgen-Bericht und Mail-Regel (Entscheidung 27.11.).

Prüft ohne echten Mailversand (Attrappen-Befehl + isolierter Zustandsordner in ``tmp_path``):
1. Nachts 22:00–07:00 (``mail.nacht``): Mail nur bei Art ``server_tot``, sonst stumm —
   auch über Mitternachts-Randminuten und ohne Nachhol-Schwall am Morgen.
2. Tagsüber: keine Mail je Spec (``spec_fertig``), keine Aufpasser- oder Leitstand-Mail
   (nur ins Log); Kritisch-Mails und der eine ``morgen_bericht`` bleiben erlaubt.
3. Morgen-Bericht: 4 Teile (Fertig · Entscheidungen zum Kippen · Umwege · Wartet auf den
   Nutzer) mit Deckel (``BERICHT_MAX``) und Liste der Kritisch-Ausnahmen; genau 1 Mail.
4. Aufseher-Takt: ruht nach abgeschlossenem Versand (bzw. ohne Mail-Versand).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
PY = sys.executable

if str(SKILL) not in sys.path:
    sys.path.insert(0, str(SKILL))

UEBERSCHRIFTEN = (
    "## Fertig",
    "## Entscheidungen zum Kippen",
    "## Umwege",
    "## Wartet auf den Nutzer",
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        timeout=60,
    )


def _mails(postfach: Path) -> list[dict[str, Any]]:
    if not postfach.is_file():
        return []
    return [json.loads(z) for z in postfach.read_text(encoding="utf-8").splitlines() if z.strip()]


@pytest.fixture
def welt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Isoliertes Git-Repo mit Fake-Mail-Befehl und Zustandsordner in ``tmp_path``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# Test\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "chore: Start")

    postfach = tmp_path / "mails.jsonl"
    mailer = tmp_path / "mailer.py"
    mailer.write_text(
        "import sys\nopen(sys.argv[1], 'a', encoding='utf-8').write(sys.stdin.read().strip() + '\\n')\n",
        encoding="utf-8",
    )
    waechter_dir = tmp_path / "waechter"
    leitstand_dir = tmp_path / "leitstand"
    mensch_noetig_datei = tmp_path / "mensch_noetig.jsonl"
    monkeypatch.setenv("TO_SPAWN_WAECHTER_ORDNER", str(waechter_dir))
    monkeypatch.setenv("TO_SPAWN_LEITSTAND_ORDNER", str(leitstand_dir))
    monkeypatch.setenv("REVIEW_MENSCH_NOETIG", str(mensch_noetig_datei))
    monkeypatch.setenv("TO_SPAWN_ABSCHLUSS_ORDNER", str(tmp_path / "abschluss"))

    mail_befehl = [PY, str(mailer), str(postfach)]
    daten = {
        "repo": repo,
        "tmp": tmp_path,
        "postfach": postfach,
        "mail_befehl": mail_befehl,
        "waechter_dir": waechter_dir,
        "leitstand_dir": leitstand_dir,
        "mensch_noetig": mensch_noetig_datei,
    }
    _konfig(daten)
    return daten


def _konfig(welt: dict[str, Any], **mail: Any) -> dict[str, Any]:
    konfig = {
        "hauptzweig": "main",
        "mail": {
            "ziel": "",
            "nur_kritisch": True,
            "befehl": welt["mail_befehl"],
            "nacht": "22:00-07:00",
            **mail,
        },
    }
    ordner = welt["repo"] / ".to-spawn"
    ordner.mkdir(exist_ok=True)
    (ordner / "config.json").write_text(json.dumps(konfig), encoding="utf-8")
    return konfig


@pytest.mark.parametrize(
    ("fenster", "uhr", "erwartet_nacht"),
    [
        ("22:00-07:00", (21, 59), False),
        ("22:00-07:00", (22, 0), True),
        ("22:00-07:00", (0, 0), True),
        ("22:00-07:00", (6, 59), True),
        ("22:00-07:00", (7, 0), False),
        ("01:00-05:00", (0, 59), False),
        ("01:00-05:00", (1, 0), True),
        ("01:00-05:00", (5, 0), False),
        ("25:00-07:00", (3, 0), False),
        ("22:70-07:00", (3, 0), False),
        ("22:00", (23, 0), False),
        ("", (3, 0), False),
    ],
)
def test_a_nachtruhe_randminuten_und_nur_server_tot(fenster: str, uhr: tuple[int, int], erwartet_nacht: bool) -> None:
    from to_spawn import config, melder

    assert "nacht" in config.DEFAULTS["mail"]
    assert "server_tot" in melder.KRITISCH
    assert melder.NACHTS == frozenset({"server_tot"})

    jetzt = datetime(2026, 11, 27, *uhr).astimezone()
    konfig = {"mail": {"nur_kritisch": True, "nacht": fenster}}
    assert melder.ist_nacht(konfig, jetzt) is erwartet_nacht
    assert melder.darf_raus("server_tot", konfig, jetzt=jetzt) is True
    for art in ("gate_rot", "session_tot", "morgen_bericht", "checkpoint_offen"):
        assert melder.darf_raus(art, konfig, jetzt=jetzt) is (not erwartet_nacht), art
    # Spec-/Aufpasser-/Leitstand-Mails sind immer stumm (auch tagsüber).
    for art in ("spec_fertig", "leitstand_start", "aufpasser_weg", "aufpasser_da"):
        assert melder.darf_raus(art, konfig, jetzt=jetzt) is False, art


def test_b_nachts_stumm_ohne_nachhol_schwall(welt: dict[str, Any]) -> None:
    from to_spawn import melder

    repo = welt["repo"]
    konfig = _konfig(welt, nacht="22:00-07:00")
    drei = datetime(2026, 11, 27, 3, 0).astimezone()
    morgen = datetime(2026, 11, 27, 7, 5).astimezone()

    # Nachts: nur server_tot geht raus; gate_rot + morgen_bericht bleiben stumm.
    assert melder.melden(repo, "gate_rot", "Gate rot #1", "pytest rot", "k_nacht_1", konfig=konfig, jetzt=drei) is False
    assert (
        melder.melden(repo, "morgen_bericht", "Morgen-Bericht", "inhalt", "k_nacht_mb", konfig=konfig, jetzt=drei)
        is False
    )
    assert (
        melder.melden(repo, "server_tot", "Bau-Server tot", "keine Antwort", "k_srv", konfig=konfig, jetzt=drei) is True
    )
    assert [m["art"] for m in _mails(welt["postfach"])] == ["server_tot"]
    assert melder.schon_gesendet(repo, "k_nacht_1") is False

    # Morgens (07:05): derselbe Schlüssel kommt NICHT als Schwall nach!
    assert (
        melder.melden(repo, "gate_rot", "Gate rot #1", "pytest rot", "k_nacht_1", konfig=konfig, jetzt=morgen) is False
    )
    assert [m["art"] for m in _mails(welt["postfach"])] == ["server_tot"]

    # Ein neuer Vorfall tagsüber geht normal raus.
    assert melder.melden(repo, "gate_rot", "Gate rot #2", "neu", "k_tag_2", konfig=konfig, jetzt=morgen) is True
    assert [m["art"] for m in _mails(welt["postfach"])] == ["server_tot", "gate_rot"]


def test_c_tagsueber_keine_spec_aufpasser_leitstand_mails_nur_log(
    welt: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    from to_spawn import melder

    repo = welt["repo"]
    konfig = _konfig(welt, nacht="22:00-07:00")
    mittag = datetime(2026, 11, 27, 12, 0).astimezone()

    with caplog.at_level("INFO"):
        for art in ("spec_fertig", "leitstand_start", "aufpasser_weg", "aufpasser_da", "aufpasser_status"):
            assert (
                melder.melden(repo, art, f"Betreff {art}", "Text", f"k_{art}", konfig=konfig, jetzt=mittag) is False
            ), art
    assert _mails(welt["postfach"]) == []
    assert "nur im Log" in caplog.text

    # Kritisch-Mails und der eine Morgen-Bericht gehen tagsüber raus.
    assert (
        melder.melden(repo, "morgen_bericht", "Morgen-Bericht", "4 Teile", "mb_tag", konfig=konfig, jetzt=mittag)
        is True
    )
    assert (
        melder.melden(repo, "session_tot", "Session tot", "keine Spur", "st_tag", konfig=konfig, jetzt=mittag) is True
    )
    assert [m["art"] for m in _mails(welt["postfach"])] == ["morgen_bericht", "session_tot"]


def test_d_morgen_bericht_vier_teile_deckel_und_kritisch_liste(welt: dict[str, Any]) -> None:
    from to_spawn import capo, melder

    repo = welt["repo"]
    bau_dir = repo / "docs" / "agents" / "bau_log"
    bau_dir.mkdir(parents=True)
    (bau_dir / "101.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-11-27T02:10:00+01:00",
                "typ": "entscheidung",
                "ticket": "101",
                "frage": "Standard-Format?",
                "wahl": "JSON",
                "grund": "einfacher",
            }
        )
        + "\n"
        + json.dumps(
            {
                "ts": "2026-11-27T03:15:00+01:00",
                "typ": "vorfall",
                "ticket": "101",
                "symptom": "Gate rot",
                "loesung": "Test ergänzt",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "docs: Bau-Log (#101)")

    welt["mensch_noetig"].write_text(
        json.dumps({"session_id": "s1", "fingerprint": "f1", "spec": 500, "ticket": 102, "fix_runde": 3})
        + "\n"
        + json.dumps({"session_id": "s2", "fingerprint": "f2", "spec": 999, "ticket": 1, "fix_runde": 1})
        + "\n",
        encoding="utf-8",
    )

    liste: list[dict[str, Any]] = [{"number": n, "title": f"Ticket {n}", "state": "closed"} for n in range(101, 115)]
    liste.append({"number": 115, "title": "Abnahme prüfen", "state": "open", "labels": [{"name": "checkpoint:human"}]})

    pfad, text = capo.morgen_bericht(repo, "HEAD", 500, liste)
    assert pfad.is_file()
    stellen = [text.index(u) for u in UEBERSCHRIFTEN]
    assert stellen == sorted(stellen), text

    fertig, kippen, umwege, wartet = (text[a:b] for a, b in zip(stellen, [*stellen[1:], len(text)], strict=True))
    # Deckel: höchstens BERICHT_MAX Punkte + „… und N weitere“.
    fertig_punkte = [z for z in fertig.splitlines() if z.startswith("- ")]
    assert len(fertig_punkte) == capo.BERICHT_MAX + 1, fertig
    assert fertig_punkte[-1].startswith("- … und 2 weitere"), fertig_punkte[-1]
    assert "#101" in kippen and "JSON" in kippen, kippen
    assert "#101" in umwege and "Gate rot" in umwege, umwege
    assert "#115 Checkpoint:" in wartet, wartet
    assert "#102 Mensch nötig:" in wartet, wartet
    assert "#1 " not in wartet, wartet

    # Liste der Kritisch-Ausnahmen wird einmal im Morgen-Bericht gezeigt.
    for art in melder.kritisch_liste():
        assert art in text, f"Kritisch-Art {art} fehlt im Morgen-Bericht"


def test_e_genau_ein_morgen_bericht_per_mail_kein_doppelversand(welt: dict[str, Any]) -> None:
    from to_spawn import capo

    repo = welt["repo"]
    konfig = _konfig(welt, nacht="22:00-07:00")
    mittag = datetime(2026, 11, 27, 8, 0).astimezone()
    liste = [{"number": 101, "title": "Erster Schritt", "state": "closed"}]
    _pfad, text = capo.morgen_bericht(repo, None, 500, liste)

    assert capo.morgen_bericht_verschicken(repo, 500, text, konfig=konfig, jetzt=mittag) is True
    assert capo.morgen_bericht_verschicken(repo, 500, text, konfig=konfig, jetzt=mittag) is False
    mails = _mails(welt["postfach"])
    assert len(mails) == 1
    assert mails[0]["art"] == "morgen_bericht"
    for u in UEBERSCHRIFTEN:
        assert u in mails[0]["text"]


def test_f_abschluss_paket_und_leitstand_tagsueber_nur_log_exit_0(welt: dict[str, Any]) -> None:
    repo = welt["repo"]
    env = dict(os.environ)
    env.update(
        {
            "TO_SPAWN_WAECHTER_ORDNER": str(welt["waechter_dir"]),
            "TO_SPAWN_ABSCHLUSS_ORDNER": str(welt["tmp"] / "abschluss"),
            "TO_SPAWN_LEITSTAND_ORDNER": str(welt["leitstand_dir"]),
        }
    )
    # abschluss_paket.py: spec_fertig ist nur Log → Exit 0, keine Mail, Datei trotzdem geschrieben.
    lauf = subprocess.run(
        [
            PY,
            str(SKILL / "skripte" / "abschluss_paket.py"),
            "500",
            "--stage",
            "https://stage.test",
            "--rundschau",
            "https://r.test",
            "--tests",
            "https://t.test",
            "--repo",
            str(repo),
        ],
        cwd=str(SKILL),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert (repo / "docs" / "agents" / "abschluss_500.md").is_file()
    assert _mails(welt["postfach"]) == []

    # leitstand_fenster.start_mail: leitstand_start ist nur Log → Exit 0, keine Mail.
    sys.path.insert(0, str(SKILL / "skripte"))
    import leitstand_fenster

    ablage = leitstand_fenster.Ablage(repo, 500)
    leitstand_fenster.speichere_json(ablage.seite_datei, {"url": "https://leitstand.test/500"})
    assert leitstand_fenster.start_mail(ablage) == 0
    assert _mails(welt["postfach"]) == []
    assert json.loads(ablage.mail_datei.read_text(encoding="utf-8"))["status"] == "nur_log"


def test_g_waechter_takt_ist_ruhig_nach_abschluss(welt: dict[str, Any]) -> None:
    from to_spawn import capo, waechter_takt

    repo = welt["repo"]
    konfig = _konfig(welt, nacht="22:00-07:00")
    mittag = datetime(2026, 11, 27, 8, 0).astimezone()

    # Noch nicht fertig → nicht ruhig.
    assert waechter_takt.ist_ruhig(repo, 500, "owner/repo", konfig, {"fertig": False}) is False
    # Fertig, aber Mail eingerichtet und noch kein Abschluss/Morgen-Bericht raus → noch nicht ruhig.
    assert waechter_takt.ist_ruhig(repo, 500, "owner/repo", konfig, {"fertig": True}) is False

    # Nach versandtem Morgen-Bericht → ruhig!
    _pfad, text = capo.morgen_bericht(repo, None, 500, [{"number": 101, "title": "A", "state": "closed"}])
    assert capo.morgen_bericht_verschicken(repo, 500, text, konfig=konfig, gh_repo="owner/repo", jetzt=mittag) is True
    assert waechter_takt.ist_ruhig(repo, 500, "owner/repo", konfig, {"fertig": True}) is True

    # Ohne Mail-Befehl → fertig heißt sofort ruhig.
    konfig_ohne = _konfig(welt, befehl=[])
    assert waechter_takt.ist_ruhig(repo, 501, "owner/repo", konfig_ohne, {"fertig": True}) is True


def test_h_abschluss_paket_morgen_bericht_cli_genau_einmal(welt: dict[str, Any]) -> None:
    repo = welt["repo"]
    env = dict(os.environ)
    env.update(
        {
            "TO_SPAWN_WAECHTER_ORDNER": str(welt["waechter_dir"]),
            "TO_SPAWN_ABSCHLUSS_ORDNER": str(welt["tmp"] / "abschluss"),
            "TO_SPAWN_LEITSTAND_ORDNER": str(welt["leitstand_dir"]),
        }
    )
    cmd = [
        PY,
        str(SKILL / "skripte" / "abschluss_paket.py"),
        "morgen-bericht",
        "500",
        "--repo",
        str(repo),
        "--jetzt",
        "2026-11-27T08:00:00+01:00",
    ]
    erster = subprocess.run(
        cmd, cwd=str(SKILL), env=env, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False
    )
    zweiter = subprocess.run(
        cmd, cwd=str(SKILL), env=env, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False
    )
    assert erster.returncode == 0, erster.stdout + erster.stderr
    assert zweiter.returncode == 0, zweiter.stdout + zweiter.stderr
    mails = _mails(welt["postfach"])
    assert len(mails) == 1
    assert mails[0]["art"] == "morgen_bericht"
    for u in UEBERSCHRIFTEN:
        assert u in mails[0]["text"]


def test_i_morgen_bericht_laufdatei_laesst_arbeitsbaum_sauber(welt: dict[str, Any]) -> None:
    """#885 Fix-Runde 2: Morgen-Bericht als Laufdatei unter ``.to-spawn/``, ohne festen Bau-Log-Pfad."""
    from to_spawn import capo
    from to_spawn.pruef import cache, sofort

    repo = welt["repo"]
    liste: list[dict[str, Any]] = [{"number": n, "title": f"Ticket {n}", "state": "closed"} for n in range(101, 116)]
    pfad, text = capo.morgen_bericht(repo, "HEAD", 500, liste)

    rel = pfad.relative_to(repo).as_posix()
    assert rel == ".to-spawn/morgen_bericht_500.md"
    assert cache.ist_werkzeug_rest(repo, rel) is True
    # Config liegt im Test-Repo ungetrackt; der Morgen-Bericht selbst zählt nie als ungespeichert.
    assert rel not in sofort.ungespeichert(repo)
    assert "docs/agents/bau_log" not in text
    assert "(siehe Bau-Log)" in text
