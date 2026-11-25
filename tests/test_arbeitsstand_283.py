"""Start aus veraltetem Arbeitsordner (#283) — mit echtem git, keine Attrappen.

Aufbau je Test: lokales bare-Repo als ``origin``, ein Klon (der Arbeitsordner) und eine
zweite Kopie, die N Commits nachschiebt. Der Klon liegt danach N Commits hinter
``origin/main`` — wie der PC-Hauptordner im Fall (5839 Commits, alte ``_default.json``).
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from to_spawn import arbeitsstand, einrichten

SKILL = Path(__file__).resolve().parent.parent
VORLAGE = Path("docs/agents/manifests/_default.json")
N = 3


def _git(ort: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=ort,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def _commit(ort: Path, inhalt: str) -> None:
    datei = ort / VORLAGE
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(inhalt, encoding="utf-8")
    _git(ort, "add", ".")
    _git(ort, "commit", "-q", "-m", inhalt)


@pytest.fixture
def klon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Arbeitsordner, der ``N`` Commits hinter ``origin/main`` liegt (sauberer Baum)."""
    monkeypatch.delenv(arbeitsstand.TEST_OK_ENV, raising=False)
    return _klon_bauen(tmp_path, "app")


def _klon_bauen(tmp_path: Path, name: str) -> Path:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    quelle = tmp_path / "quelle"
    _git(tmp_path, "clone", "-q", str(remote), str(quelle))
    _commit(quelle, '{"prompt_template": "alt"}')
    _git(quelle, "push", "-q", "origin", "HEAD:main")
    ort = tmp_path / name
    _git(tmp_path, "clone", "-q", str(remote), str(ort))
    for i in range(N):
        _commit(quelle, f'{{"prompt_template": "neu {i} staging:fertig"}}')
    _git(quelle, "push", "-q", "origin", "HEAD:main")
    return ort


def test_sauber_zurueck_wird_nachgezogen(klon: Path) -> None:
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.NACHGEZOGEN, stand.text
    assert stand.ok and stand.hinter == N
    assert "staging:fertig" in (klon / VORLAGE).read_text(encoding="utf-8")
    assert _git(klon, "rev-list", "--count", "HEAD..origin/main") == "0"


def test_zurueck_mit_lokaler_aenderung_bricht_ab_mit_zahl_und_anweisung(
    klon: Path,
) -> None:
    (klon / VORLAGE).write_text('{"prompt_template": "von Hand"}', encoding="utf-8")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.ABBRUCH and not stand.ok
    assert f"{N} Commits hinter origin/main" in stand.text
    assert "Tun:" in stand.text and "pull --ff-only" in stand.text
    # Lokale Änderung unberührt, nichts vorgespult.
    assert (klon / VORLAGE).read_text(encoding="utf-8") == '{"prompt_template": "von Hand"}'
    assert _git(klon, "rev-list", "--count", "HEAD..origin/main") == str(N)


def test_abweichende_historie_bricht_ab(klon: Path) -> None:
    (klon / "eigen.txt").write_text("x", encoding="utf-8")
    _git(klon, "add", ".")
    _git(klon, "commit", "-q", "-m", "eigen")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.ABBRUCH
    assert f"{N} Commits hinter" in stand.text and "nicht vorspulbar" in stand.text


def test_aktuell_ist_still_ok(klon: Path) -> None:
    _git(klon, "pull", "-q", "--ff-only")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.AKTUELL and stand.ok and stand.hinter == 0


def test_fetch_fehler_gilt_nie_als_aktuell(klon: Path, tmp_path: Path) -> None:
    _git(klon, "remote", "set-url", "origin", str(tmp_path / "gibt-es-nicht.git"))
    stand = arbeitsstand.sicherstellen(klon)
    # Nachprüfung #283: nie still „aktuell“, aber Warnung statt Abbruch.
    assert stand.zustand == arbeitsstand.HINWEIS and "nicht prüfbar" in stand.text


def test_nur_pruefen_zieht_nicht_nach(klon: Path) -> None:
    stand = arbeitsstand.sicherstellen(klon, nachziehen=False)
    assert stand.zustand == arbeitsstand.VERALTET and not stand.ok
    assert _git(klon, "rev-list", "--count", "HEAD..origin/main") == str(N)


def test_nur_pruefen_hinter_und_voraus_ohne_ff_rat(klon: Path) -> None:
    """Hinter und eigene Commits voraus: kein ``pull --ff-only``-Rat (der würde scheitern)."""
    _git(klon, "commit", "-q", "--allow-empty", "-m", "eigen")
    stand = arbeitsstand.sicherstellen(klon, nachziehen=False)
    assert stand.zustand == arbeitsstand.VERALTET and "voraus: 1" in stand.text, stand.text
    assert "pull --ff-only" not in stand.text


def test_einrichten_pruefen_meldet_veralteten_arbeitsordner(klon: Path) -> None:
    ausgabe = io.StringIO()
    code = einrichten.einrichten(klon, pruefen=True, ausgabe=ausgabe)
    text = ausgabe.getvalue()
    assert code == einrichten.EXIT_ABWEICHUNG
    zeile = next(z for z in text.splitlines() if z.strip().startswith("arbeitsstand:"))
    assert f"{N} Commits hinter origin/main" in zeile, text


def test_bau_start_bei_veraltetem_ordner_nur_hinweis_zeile(klon: Path) -> None:
    """Echter Start-Weg ``skripte/bau.py <N>``: nicht nachziehbar → nur Hinweis-Zeile, Start läuft weiter.

    Nachprüfung #283: ``bau.py`` bricht wegen des Stands nie ab und zieht nie nach. Beleg für
    „läuft weiter“ ist die Folge-Meldung der nächsten Stelle (kein Manifest für Ticket #7).
    """
    vorher = _git(klon, "rev-parse", "HEAD")
    (klon / VORLAGE).write_text('{"prompt_template": "von Hand"}', encoding="utf-8")
    env = {**os.environ, "TO_SPAWN_REPO": str(klon), "TO_SPAWN_GH_REPO": "test-org/app"}
    fertig = subprocess.run(
        [sys.executable, str(SKILL / "skripte" / "bau.py"), "7"],
        cwd=klon,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    ausgabe = fertig.stdout + fertig.stderr
    hinweise = [z for z in ausgabe.splitlines() if f"{N} Commits hinter origin/main" in z]
    assert len(hinweise) == 1 and "Tun:" in hinweise[0], ausgabe
    assert "Kein Start:" not in ausgabe and "Kein Manifest führt Ticket #7" in ausgabe, ausgabe
    assert _git(klon, "rev-parse", "HEAD") == vorher


@pytest.mark.parametrize("weg", ["anderer_zweig", "losgeloest"])
def test_nicht_auf_hauptzweig_und_hinter_bricht_ab(klon: Path, weg: str) -> None:
    """Kante (a): anderer Zweig bzw. losgelöster HEAD und hinter → Hinweis mit Zahl, nichts bewegt.

    Nachprüfung #283: früher Abbruch — ein Bau-Klon steht regelmäßig auf einem Ticket-Zweig.
    """
    if weg == "anderer_zweig":
        _git(klon, "switch", "-q", "-c", "fix/irgendwas")
    else:
        _git(klon, "switch", "-q", "--detach")
    vorher = _git(klon, "rev-parse", "HEAD")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.HINWEIS and stand.ok
    assert f"{N} Commits hinter origin/main" in stand.text and "switch main" in stand.text
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_unversionierte_dateien_hindern_nachziehen_nicht(klon: Path) -> None:
    """Kante (e): unversionierte Datei bleibt liegen, Nachziehen klappt trotzdem."""
    notiz = klon / "notiz-unversioniert.txt"
    notiz.write_text("bleibt", encoding="utf-8")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.NACHGEZOGEN, stand.text
    assert notiz.read_text(encoding="utf-8") == "bleibt"
    assert _git(klon, "rev-parse", "HEAD") == _git(klon, "rev-parse", "origin/main")


def test_aktueller_klon_ohne_zweig_und_sauberkeits_zwang(klon: Path) -> None:
    """Bau-Server-Fall: hinter == 0 → aktuell, auch auf anderem Zweig mit lokaler Änderung."""
    _git(klon, "pull", "-q", "--ff-only")
    _git(klon, "switch", "-q", "-c", "arbeit")
    (klon / VORLAGE).write_text('{"prompt_template": "von Hand"}', encoding="utf-8")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.AKTUELL and stand.ok, stand.text


def test_zwei_starts_zugleich_scheitern_nicht_an_git_sperre(klon: Path) -> None:
    """Kante (f): zwei Prozesse rufen die Tür gleichzeitig im selben Ordner — beide ok, einer zieht nach."""
    code = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]);"
        "from to_spawn import arbeitsstand as a;"
        "s = a.sicherstellen(Path(sys.argv[2])); print(s.zustand); print(s.text)"
    )
    env = {k: v for k, v in os.environ.items() if k != arbeitsstand.TEST_OK_ENV}
    starts = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(SKILL), str(klon)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for _ in range(2)
    ]
    ergebnisse = [p.communicate(timeout=120) for p in starts]
    zustaende = sorted(aus.splitlines()[0] if aus else f"leer: {err}" for aus, err in ergebnisse)
    assert zustaende == [arbeitsstand.AKTUELL, arbeitsstand.NACHGEZOGEN], ergebnisse
    assert _git(klon, "rev-parse", "HEAD") == _git(klon, "rev-parse", "origin/main")


def test_bau_start_eigene_gitignore_ergaenzung_blockiert_nachziehen_nicht(
    klon: Path,
) -> None:
    """Getrackte ``.gitignore`` ohne to-spawn-Block, sauber, hinter origin: kein Abbruch.

    Nachprüfung #283: ``bau.py`` zieht nie nach (früher: HEAD == origin/main), nur Hinweis.
    """
    quelle = klon.parent / "quelle"
    (quelle / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _git(quelle, "add", ".")
    _git(quelle, "commit", "-q", "-m", "gitignore")
    _git(quelle, "push", "-q", "origin", "HEAD:main")
    _git(klon, "pull", "-q", "--ff-only", "origin", "main")
    _commit(quelle, '{"prompt_template": "noch neuer"}')
    _git(quelle, "push", "-q", "origin", "HEAD:main")
    env = {**os.environ, "TO_SPAWN_REPO": str(klon), "TO_SPAWN_GH_REPO": "test-org/app"}
    fertig = subprocess.run(
        [sys.executable, str(SKILL / "skripte" / "bau.py"), "7"],
        cwd=klon,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    ausgabe = fertig.stdout + fertig.stderr
    assert "lokal geändert" not in ausgabe and "Kein Start: Arbeitsordner" not in ausgabe, ausgabe
    assert _git(klon, "rev-parse", "HEAD") != _git(klon, "rev-parse", "origin/main")
    assert "Commits hinter origin/main" in ausgabe, ausgabe


# --- Nach der Prüfung (PR 385): Nachzieh-Regel „git entscheidet“, Meldungen, Start-Wege -------


def _quelle_schiebt(klon: Path, datei: str, inhalt: str) -> None:
    """Die zweite Kopie committet ``datei`` und schiebt sie nach ``origin/main``."""
    quelle = klon.parent / "quelle"
    ziel = quelle / datei
    ziel.parent.mkdir(parents=True, exist_ok=True)
    ziel.write_text(inhalt, encoding="utf-8")
    _git(quelle, "add", ".")
    _git(quelle, "commit", "-q", "-m", datei)
    _git(quelle, "push", "-q", "origin", "HEAD:main")


def _getrackte_gitignore(klon: Path) -> Path:
    """Klon mit getrackter ``.gitignore`` (aktuell), danach ``N`` neue Vorlage-Commits auf origin."""
    _quelle_schiebt(klon, ".gitignore", "node_modules/\n")
    _git(klon, "pull", "-q", "--ff-only", "origin", "main")
    for i in range(N):
        _quelle_schiebt(klon, str(VORLAGE), f'{{"prompt_template": "neuer {i}"}}')
    return klon / ".gitignore"


def _laeuft(befehl: list[str], ort: Path, **env: str) -> subprocess.CompletedProcess[str]:
    umgebung = {k: v for k, v in os.environ.items() if k not in (arbeitsstand.TEST_OK_ENV, "TO_SPAWN_REPO")}
    umgebung.update(env)
    return subprocess.run(
        befehl,
        cwd=ort,
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )


def test_unbeteiligte_lokale_aenderung_hindert_nachziehen_nicht(klon: Path) -> None:
    """(a) Realer Fall: früherer Start hängte einen Block an die getrackte ``.gitignore``."""
    gitignore = _getrackte_gitignore(klon)
    gitignore.write_text("node_modules/\n# to-spawn\n.to-spawn/\n", encoding="utf-8")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.NACHGEZOGEN and stand.hinter == N, stand.text
    assert gitignore.read_text(encoding="utf-8") == "node_modules/\n# to-spawn\n.to-spawn/\n"
    assert _git(klon, "rev-parse", "HEAD") == _git(klon, "rev-parse", "origin/main")


def test_kollidierende_lokale_aenderung_bricht_ab_und_nennt_datei(klon: Path) -> None:
    """(b) + Befund 2: origin ändert dieselbe Datei → Abbruch mit Zahl und Datei, kein „Commits voraus“."""
    (klon / VORLAGE).write_text('{"prompt_template": "von Hand"}', encoding="utf-8")
    vorher = _git(klon, "rev-parse", "HEAD")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.ABBRUCH and stand.hinter == N, stand.text
    assert f"{N} Commits hinter origin/main" in stand.text
    assert VORLAGE.as_posix() in stand.text and "eigene Commits voraus" not in stand.text, stand.text
    assert "Tun:" in stand.text
    assert (klon / VORLAGE).read_text(encoding="utf-8") == '{"prompt_template": "von Hand"}'
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_autostash_in_git_konfig_aendert_lokale_datei_nicht(klon: Path) -> None:
    """``merge.autoStash=true`` darf nie stashen/zurückspielen: Abbruch, Datei und HEAD unberührt."""
    _git(klon, "config", "merge.autoStash", "true")
    (klon / VORLAGE).write_text('{"prompt_template": "von Hand"}', encoding="utf-8")
    vorher = _git(klon, "rev-parse", "HEAD")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.ABBRUCH, stand.text
    assert (klon / VORLAGE).read_bytes() == b'{"prompt_template": "von Hand"}'
    assert _git(klon, "rev-parse", "HEAD") == vorher
    assert _git(klon, "stash", "list") == ""


def test_unversionierte_datei_die_origin_einfuehrt_bricht_ab(klon: Path) -> None:
    """(c) origin führt ``neu.txt`` ein, lokal liegt sie unversioniert → Abbruch, Datei bleibt."""
    _quelle_schiebt(klon, "neu.txt", "von origin")
    notiz = klon / "neu.txt"
    notiz.write_text("lokal", encoding="utf-8")
    vorher = _git(klon, "rev-parse", "HEAD")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.ABBRUCH, stand.text
    assert f"{N + 1} Commits hinter" in stand.text and "neu.txt" in stand.text, stand.text
    assert "eigene Commits voraus" not in stand.text
    assert notiz.read_text(encoding="utf-8") == "lokal"
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_pruefen_mit_lokaler_aenderung_meldet_veraltet_mit_zahl(klon: Path) -> None:
    (klon / VORLAGE).write_text('{"prompt_template": "von Hand"}', encoding="utf-8")
    stand = arbeitsstand.sicherstellen(klon, nachziehen=False)
    assert stand.zustand == arbeitsstand.VERALTET and stand.hinter == N, stand.text


def test_abweichende_historie_nennt_eigene_commits(klon: Path) -> None:
    (klon / "eigen.txt").write_text("x", encoding="utf-8")
    _git(klon, "add", ".")
    _git(klon, "commit", "-q", "-m", "eigen")
    stand = arbeitsstand.sicherstellen(klon)
    assert stand.zustand == arbeitsstand.ABBRUCH and "eigene Commits voraus: 1" in stand.text


def test_tun_befehle_mit_leerzeichen_im_pfad_kopierbar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Befund 5: Pfad mit Leerzeichen steht in den Tun-Befehlen in doppelten Anführungszeichen."""
    monkeypatch.delenv(arbeitsstand.TEST_OK_ENV, raising=False)
    ort = _klon_bauen(tmp_path, "mein app")
    veraltet = arbeitsstand.sicherstellen(ort, nachziehen=False)
    assert f'git -C "{ort}" pull --ff-only origin main' in veraltet.text, veraltet.text
    _git(ort, "switch", "-q", "-c", "nebenher")
    zweig = arbeitsstand.sicherstellen(ort)
    assert f'git -C "{ort}" switch main' in zweig.text, zweig.text


def test_pruefen_wartet_nur_kurz_auf_die_sperre(klon: Path) -> None:
    """Befund 7: hält ein Start die Sperre, gibt der Prüf-Modus nach ≤ 10 s auf (nicht 300 s)."""
    from to_spawn import sperre

    code = (
        "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]);"
        "from to_spawn import arbeitsstand as a;"
        "s = a.sicherstellen(Path(sys.argv[2]), nachziehen=False); print(s.zustand); print(s.text)"
    )
    with sperre.halte(arbeitsstand._sperr_datei(klon)) as gehalten:
        assert gehalten
        fertig = _laeuft([sys.executable, "-c", code, str(SKILL), str(klon)], klon)
    assert fertig.stdout.splitlines()[0] == arbeitsstand.ABBRUCH, fertig.stdout + fertig.stderr
    assert f"{arbeitsstand.SPERRE_WARTEN_PRUEFEN_S:.0f} s" in fertig.stdout


def test_bau_zieht_verlegten_manifest_ordner_nicht_nach(klon: Path) -> None:
    """Befund 3, Nachprüfung #283: origin verlegt ``pfade.manifeste`` — ``bau.py`` zieht nicht nach.

    Früher zog der Start nach und las den neuen Ort (kaputte Vorlage → Exit 2). Jetzt bleibt
    HEAD stehen, die alte gültige Vorlage gilt, nur eine Hinweis-Zeile nennt den Rückstand.
    """
    vorher = _git(klon, "rev-parse", "HEAD")
    _quelle_schiebt(klon, "neu/manifeste/_default.json", '{"kaputt": 1}')
    _quelle_schiebt(klon, ".to-spawn/config.json", '{"pfade": {"manifeste": "neu/manifeste"}}')
    fertig = _laeuft(
        [sys.executable, str(SKILL / "skripte" / "bau.py"), "7"],
        klon,
        TO_SPAWN_REPO=str(klon),
        TO_SPAWN_GH_REPO="test-org/app",
    )
    ausgabe = fertig.stdout + fertig.stderr
    assert "nachgezogen" not in ausgabe, ausgabe
    assert "Default-Manifest ohne prompt_template" not in ausgabe, ausgabe
    assert f"{N + 2} Commits hinter origin/main" in ausgabe, ausgabe
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_bau_probelauf_im_veralteten_ordner_warnt_nur(klon: Path) -> None:
    vorher = _git(klon, "rev-parse", "HEAD")
    fertig = _laeuft(
        [sys.executable, str(SKILL / "skripte" / "bau.py"), "7", "--dry-run"],
        klon,
        TO_SPAWN_REPO=str(klon),
        TO_SPAWN_GH_REPO="test-org/app",
    )
    ausgabe = fertig.stdout + fertig.stderr
    assert f"{N} Commits hinter origin/main" in ausgabe and "Kein Start" not in ausgabe, ausgabe
    assert _git(klon, "rev-parse", "HEAD") == vorher


def test_cli_arbeitsstand_zieht_nach_exit_0(klon: Path) -> None:
    fertig = _laeuft(
        [sys.executable, str(SKILL / "to_spawn.py"), "arbeitsstand"],
        klon,
        TO_SPAWN_REPO=str(klon),
    )
    assert fertig.returncode == 0, fertig.stdout + fertig.stderr
    assert fertig.stdout.startswith(f"Arbeitsstand: nachgezogen — Arbeitsordner {N} Commits")
    assert _git(klon, "rev-parse", "HEAD") == _git(klon, "rev-parse", "origin/main")


def test_cli_arbeitsstand_pruefen_exit_1_ohne_nachziehen(klon: Path) -> None:
    vorher = _git(klon, "rev-parse", "HEAD")
    fertig = _laeuft(
        [sys.executable, str(SKILL / "to_spawn.py"), "arbeitsstand", "--pruefen"],
        klon,
        TO_SPAWN_REPO=str(klon),
    )
    assert fertig.returncode == 1, fertig.stdout + fertig.stderr
    assert fertig.stderr.startswith("Arbeitsstand: veraltet — ") and f"{N} Commits hinter" in fertig.stderr
    assert _git(klon, "rev-parse", "HEAD") == vorher


def _abweichung_zeile(text: str) -> str:
    return next((z for z in text.splitlines() if z.startswith("Abweichung:")), "")


def test_einrichten_pruefen_abweichung_nennt_arbeitsstand_nur_wenn_veraltet(
    klon: Path,
) -> None:
    veraltet = io.StringIO()
    einrichten.einrichten(klon, pruefen=True, ausgabe=veraltet)
    assert "arbeitsstand" in _abweichung_zeile(veraltet.getvalue()), veraltet.getvalue()
    _git(klon, "pull", "-q", "--ff-only")
    aktuell = io.StringIO()
    einrichten.einrichten(klon, pruefen=True, ausgabe=aktuell)
    assert "arbeitsstand: aktuell" in aktuell.getvalue(), aktuell.getvalue()
    assert "arbeitsstand" not in _abweichung_zeile(aktuell.getvalue()), aktuell.getvalue()


PS = shutil.which("pwsh")
SPAWN_LOCAL = SKILL / "spawn_local.ps1"


@pytest.mark.skipif(PS is None, reason="pwsh fehlt")
def test_spawn_local_bricht_bei_kollidierender_aenderung_ab(klon: Path) -> None:
    """Echter Weg ``spawn_local.ps1`` (ohne -DryRun): Abbruch vor Regularien/wt, Exit ≠ 0, rote Zeile."""
    (klon / VORLAGE).write_text('{"prompt_template": "von Hand"}', encoding="utf-8")
    vorher = _git(klon, "rev-parse", "HEAD")
    fertig = _laeuft(
        [str(PS), "-NoProfile", "-File", str(SPAWN_LOCAL), "-Spec", "48"],
        klon,
        TO_SPAWN_REPO=str(klon),
    )
    ausgabe = fertig.stdout + fertig.stderr
    assert fertig.returncode != 0, ausgabe
    assert "nichts gestartet" in ausgabe and f"{N} Commits hinter origin/main" in ausgabe, ausgabe
    assert "Starte" not in ausgabe
    assert _git(klon, "rev-parse", "HEAD") == vorher


@pytest.mark.skipif(PS is None, reason="pwsh fehlt")
def test_spawn_local_ohne_python_endet_nicht_mit_exit_0(tmp_path: Path) -> None:
    """Befund 4: Interpreter fehlt → ``$rc`` bleibt nicht leer (``exit $null`` wäre Exit 0)."""
    namen = ("python.exe", "python3.exe", "python", "python3")
    pfad = os.pathsep.join(
        d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not any((Path(d) / n).exists() for n in namen)
    )
    fertig = _laeuft(
        [str(PS), "-NoProfile", "-File", str(SPAWN_LOCAL), "-Spec", "48"],
        tmp_path,
        PATH=pfad,
    )
    assert fertig.returncode != 0, fertig.stdout + fertig.stderr
