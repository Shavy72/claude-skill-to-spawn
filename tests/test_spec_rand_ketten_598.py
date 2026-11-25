"""Weg-Tests #598: Zwei getrennte Abnahme-Ketten in einer Spec bekommen nur ihre eigenen Kanten.

Echt laufen: CLI (``to_spawn.py anhaengen``), Manifest, Git-Repo, Manifest-Prüfung.
Gestellt ist nur die ``gh``-CLI über den Ersatz aus ``test_spec_rand_e2``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_spec_rand_e2 as e2

SPEC = e2.SPEC
A1, A2, ABN_A = 101, 102, 103
B1, B2, ABN_B = 201, 202, 203


def _zwei_ketten_welt(
    *,
    extra_issues: dict[str, dict[str, Any]] | None = None,
    unter: list[int] | None = None,
    kanten: dict[str, list[int]] | None = None,
) -> dict[str, Any]:
    issues: dict[str, Any] = {
        str(SPEC): e2._issue(SPEC, "Spec: Zwei Ketten"),
        str(A1): e2._issue(A1, "Teil A: Schritt 1"),
        str(A2): e2._issue(A2, "Teil A: Schritt 2", body=f"## Blocked by\n#{A1}"),
        str(ABN_A): e2._issue(
            ABN_A,
            "Abnahme Teil A",
            labels=["checkpoint:human"],
            body=f"## Blocked by\n#{A1}\n#{A2}",
        ),
        str(B1): e2._issue(B1, "Teil B: Schritt 1"),
        str(B2): e2._issue(B2, "Teil B: Schritt 2", body=f"## Blocked by\n#{B1}"),
        str(ABN_B): e2._issue(
            ABN_B,
            "Abnahme Teil B",
            labels=["checkpoint:human"],
            body=f"## Blocked by\n#{B1}\n#{B2}",
        ),
    }
    if extra_issues:
        issues.update(extra_issues)
    return {
        "issues": issues,
        "unter": {str(SPEC): list(unter or [])},
        "kanten": dict(kanten or {}),
        "posts": [],
    }


def _aufbau_leer(
    tmp_path: Path,
    welt: dict[str, Any],
    *,
    tickets: dict[str, Any] | None = None,
) -> tuple[Path, Path, dict[str, str]]:
    repo, weltdatei, umgebung = e2._aufbau(tmp_path, welt)
    e2._manifest(repo).write_text(
        json.dumps(
            {"spec": SPEC, "feature": "zwei-ketten", "tickets": tickets or {}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return repo, weltdatei, umgebung


def _anh(
    repo: Path,
    umgebung: dict[str, str],
    ticket: int,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(e2.CLI), "anhaengen", str(SPEC), str(ticket), *extra],
        check=False,
        cwd=str(repo),
        env=umgebung,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _ketten_manifest_und_welt(
    extra_issues: dict[str, dict[str, Any]] | None = None,
    *,
    transitiv: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fertig angehängte Spec mit zwei Ketten A (101→102→103) und B (201→202→203)."""
    kanten = {
        str(A2): [A1],
        str(ABN_A): [A2] if transitiv else [A1, A2],
        str(B2): [B1],
        str(ABN_B): [B2] if transitiv else [B1, B2],
    }
    welt = _zwei_ketten_welt(
        extra_issues=extra_issues,
        unter=[A1, A2, ABN_A, B1, B2, ABN_B],
        kanten=kanten,
    )
    if transitiv:
        welt["issues"][str(ABN_A)]["body"] = f"## Blocked by\n#{A2}"
        welt["issues"][str(ABN_B)]["body"] = f"## Blocked by\n#{B2}"
    tickets = {
        str(A1): {"title": "Teil A: Schritt 1", "schaetzung_k": 40, "umfang": "A1."},
        str(A2): {"title": "Teil A: Schritt 2", "schaetzung_k": 40, "umfang": "A2."},
        str(ABN_A): {"title": "Abnahme Teil A", "schaetzung_k": 20, "umfang": "Abnahme A."},
        str(B1): {"title": "Teil B: Schritt 1", "schaetzung_k": 40, "umfang": "B1."},
        str(B2): {"title": "Teil B: Schritt 2", "schaetzung_k": 40, "umfang": "B2."},
        str(ABN_B): {"title": "Abnahme Teil B", "schaetzung_k": 20, "umfang": "Abnahme B."},
    }
    return welt, tickets


def test_zwei_ketten_nacheinander_jede_abnahme_nur_eigene_kette(tmp_path: Path) -> None:
    """#598: Kette A komplett anhängen, dann Kette B — Abnahme A bekommt keine B-Kanten."""
    repo, weltdatei, umgebung = _aufbau_leer(tmp_path, _zwei_ketten_welt())
    schritte = [
        (A1, ("--schaetzung", "40", "--umfang", "A1.")),
        (A2, ("--blockiert-von", str(A1), "--schaetzung", "40", "--umfang", "A2.")),
        (ABN_A, ("--blockiert-von", f"{A1},{A2}", "--schaetzung", "20", "--umfang", "Abnahme A.")),
        (B1, ("--schaetzung", "40", "--umfang", "B1.")),
        (B2, ("--blockiert-von", str(B1), "--schaetzung", "40", "--umfang", "B2.")),
        (ABN_B, ("--blockiert-von", f"{B1},{B2}", "--schaetzung", "20", "--umfang", "Abnahme B.")),
    ]
    for ticket, args in schritte:
        lauf = _anh(repo, umgebung, ticket, *args)
        assert lauf.returncode in (0, 3), f"#{ticket}: {lauf.stdout}{lauf.stderr}"
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "alles erfüllt" in lauf.stdout, lauf.stdout

    kanten = e2._welt(weltdatei)["kanten"]
    assert sorted(kanten[str(ABN_A)]) == [A1, A2]
    assert sorted(kanten[str(ABN_B)]) == [B1, B2]
    assert kanten[str(A2)] == [A1]
    assert kanten[str(B2)] == [B1]


def test_zwei_ketten_bau_zuerst_dann_abnahmen_und_idempotent(tmp_path: Path) -> None:
    """#598: Erst alle Bau-Tickets, dann beide Abnahmen; zweiter Lauf setzt keine Fremd-Kante."""
    repo, weltdatei, umgebung = _aufbau_leer(tmp_path, _zwei_ketten_welt())
    schritte = [
        (A1, ("--schaetzung", "40", "--umfang", "A1.")),
        (A2, ("--blockiert-von", str(A1), "--schaetzung", "40", "--umfang", "A2.")),
        (B1, ("--schaetzung", "40", "--umfang", "B1.")),
        (B2, ("--blockiert-von", str(B1), "--schaetzung", "40", "--umfang", "B2.")),
        (ABN_A, ("--blockiert-von", f"{A1},{A2}", "--schaetzung", "20", "--umfang", "Abnahme A.")),
        (ABN_B, ("--blockiert-von", f"{B1},{B2}", "--schaetzung", "20", "--umfang", "Abnahme B.")),
    ]
    for ticket, args in schritte:
        lauf = _anh(repo, umgebung, ticket, *args)
        assert lauf.returncode in (0, 3), f"#{ticket}: {lauf.stdout}{lauf.stderr}"
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr

    posts_vorher = len(e2._welt(weltdatei)["posts"])
    wieder = _anh(repo, umgebung, A1)
    assert wieder.returncode == 0, wieder.stdout + wieder.stderr
    assert "nichts zu tun" in wieder.stdout, wieder.stdout
    assert len(e2._welt(weltdatei)["posts"]) == posts_vorher

    kanten = e2._welt(weltdatei)["kanten"]
    assert sorted(kanten[str(ABN_A)]) == [A1, A2]
    assert sorted(kanten[str(ABN_B)]) == [B1, B2]


def test_nachtrag_in_einer_kette_haengt_nur_an_eigener_abnahme(tmp_path: Path) -> None:
    """#598: Nachtrag in Kette A bzw. B landet nur vor seiner Abnahme (auch transitiv)."""
    a3, b3 = 104, 204
    welt, tickets = _ketten_manifest_und_welt(
        extra_issues={
            str(a3): e2._issue(a3, "Nachtrag A3", body=f"## Blocked by\n#{A1}"),
            str(b3): e2._issue(b3, "Nachtrag B3", body=f"## Blocked by\n#{B2}"),
        },
        transitiv=True,
    )
    repo, weltdatei, umgebung = _aufbau_leer(tmp_path, welt, tickets=tickets)

    lauf_a = _anh(repo, umgebung, a3, "--blockiert-von", str(A1), "--schaetzung", "30", "--umfang", "A3.")
    assert lauf_a.returncode == 0, lauf_a.stdout + lauf_a.stderr
    lauf_b = _anh(repo, umgebung, b3, "--blockiert-von", str(B2), "--schaetzung", "30", "--umfang", "B3.")
    assert lauf_b.returncode == 0, lauf_b.stdout + lauf_b.stderr

    kanten = e2._welt(weltdatei)["kanten"]
    assert a3 in kanten[str(ABN_A)] and a3 not in kanten[str(ABN_B)]
    assert b3 in kanten[str(ABN_B)] and b3 not in kanten[str(ABN_A)]


def test_gelbes_folge_ticket_haengt_nur_an_abnahme_seiner_kette(tmp_path: Path) -> None:
    """#598: Folge-Ticket mit ``gelb_von`` aus Kette A landet nur vor Abnahme A."""
    folge = 105
    welt, tickets = _ketten_manifest_und_welt(
        extra_issues={str(folge): e2._issue(folge, f"Gelb aus #{A2}")},
    )
    tickets[str(folge)] = {
        "title": f"Gelb aus #{A2}",
        "schaetzung_k": 30,
        "umfang": "Randfall in Kette A.",
        "gelb_von": A2,
    }
    repo, weltdatei, umgebung = _aufbau_leer(tmp_path, welt, tickets=tickets)

    lauf = _anh(repo, umgebung, folge)
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr

    kanten = e2._welt(weltdatei)["kanten"]
    assert folge in kanten[str(ABN_A)] and folge not in kanten[str(ABN_B)]


def test_einrichtungs_ticket_t0_verschmilzt_ketten_nicht(tmp_path: Path) -> None:
    """#598 + #410: Gemeinsames Einrichtungs-Ticket T0 koppelt Kette A und Kette B nicht zusammen."""
    t0, a3 = 950, 104
    welt, tickets = _ketten_manifest_und_welt(
        extra_issues={
            str(t0): e2._issue(t0, "Einrichtung", labels=["einrichtung"]),
            str(a3): e2._issue(a3, "Nachtrag A3", body=f"## Blocked by\n#{A2}"),
        },
    )
    welt["unter"][str(SPEC)].append(t0)
    for nr in (A1, A2, ABN_A, B1, B2, ABN_B):
        welt["kanten"].setdefault(str(nr), []).append(t0)
    tickets[str(t0)] = {
        "title": "Einrichtung",
        "schaetzung_k": 60,
        "umfang": "Prüfung einrichten.",
        "einrichtung": True,
    }
    repo, weltdatei, umgebung = _aufbau_leer(tmp_path, welt, tickets=tickets)

    lauf = _anh(repo, umgebung, a3, "--blockiert-von", str(A2), "--schaetzung", "30", "--umfang", "A3.")
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr

    kanten = e2._welt(weltdatei)["kanten"]
    assert t0 in kanten[str(a3)]
    assert a3 in kanten[str(ABN_A)] and a3 not in kanten[str(ABN_B)]


def test_mehrdeutiger_nachtrag_ohne_blocker_bricht_klar_ab_und_mit_abnahme_klappt(
    tmp_path: Path,
) -> None:
    """#598: Ohne Blocker bei zwei offenen Abnahmen bricht ``anhaengen`` klar ab; ``--abnahme`` löst."""
    neu, c1, c2 = 301, 302, 303
    welt, tickets = _ketten_manifest_und_welt(
        extra_issues={
            str(neu): e2._issue(neu, "Unabhängiger Nachtrag"),
            str(c1): e2._issue(c1, "Kette C: Schritt 1"),
            str(c2): e2._issue(c2, "Kette C: Schritt 2", body=f"## Blocked by\n#{c1}"),
        },
    )
    repo, weltdatei, umgebung = _aufbau_leer(tmp_path, welt, tickets=tickets)
    manifest_vorher = e2._manifest(repo).read_bytes()

    abbruch = _anh(repo, umgebung, neu, "--schaetzung", "30", "--umfang", "Ohne Blocker.")
    assert abbruch.returncode == 1, abbruch.stdout + abbruch.stderr
    assert f"#{ABN_A}" in abbruch.stdout and f"#{ABN_B}" in abbruch.stdout, abbruch.stdout
    assert "--abnahme" in abbruch.stdout, abbruch.stdout
    assert e2._welt(weltdatei)["posts"] == []
    assert e2._manifest(repo).read_bytes() == manifest_vorher

    mit_abn = _anh(
        repo,
        umgebung,
        neu,
        "--abnahme",
        str(ABN_B),
        "--schaetzung",
        "30",
        "--umfang",
        "Ohne Blocker.",
    )
    assert mit_abn.returncode == 0, mit_abn.stdout + mit_abn.stderr
    kanten = e2._welt(weltdatei)["kanten"]
    assert neu in kanten[str(ABN_B)] and neu not in kanten[str(ABN_A)]

    # Dritte Kette starten mit ``--abnahme keine``; Folge-Ticket c2 setzt Kette C automatisch fort.
    start_c = _anh(repo, umgebung, c1, "--abnahme", "keine", "--schaetzung", "30", "--umfang", "C1.")
    assert start_c.returncode == 0, start_c.stdout + start_c.stderr
    weiter_c = _anh(
        repo,
        umgebung,
        c2,
        "--blockiert-von",
        str(c1),
        "--schaetzung",
        "30",
        "--umfang",
        "C2.",
    )
    assert weiter_c.returncode == 0, weiter_c.stdout + weiter_c.stderr
    kanten = e2._welt(weltdatei)["kanten"]
    assert c1 not in kanten[str(ABN_A)] and c1 not in kanten[str(ABN_B)]
    assert c2 not in kanten[str(ABN_A)] and c2 not in kanten[str(ABN_B)]


def test_nachtrag_ueber_beide_ketten_bricht_klar_ab(tmp_path: Path) -> None:
    """#598: Blocker aus beiden Ketten → klarer Abbruch vor jedem Schreibzugriff."""
    bruecke = 304
    welt, tickets = _ketten_manifest_und_welt(
        extra_issues={
            str(bruecke): e2._issue(bruecke, "Brücke", body=f"## Blocked by\n#{A2}\n#{B2}"),
        },
    )
    repo, weltdatei, umgebung = _aufbau_leer(tmp_path, welt, tickets=tickets)
    manifest_vorher = e2._manifest(repo).read_bytes()

    lauf = _anh(
        repo,
        umgebung,
        bruecke,
        "--blockiert-von",
        f"{A2},{B2}",
        "--schaetzung",
        "30",
        "--umfang",
        "Verbindet A und B.",
    )
    assert lauf.returncode == 1, lauf.stdout + lauf.stderr
    assert f"#{ABN_A}" in lauf.stdout and f"#{ABN_B}" in lauf.stdout, lauf.stdout
    assert "--abnahme" in lauf.stdout, lauf.stdout
    assert e2._welt(weltdatei)["posts"] == []
    assert e2._manifest(repo).read_bytes() == manifest_vorher


def test_abnahme_a_geschlossen_nachtrag_a_landet_nicht_an_abnahme_b(tmp_path: Path) -> None:
    """#598: Ist Abnahme A schon zu, landet ein Nachtrag aus Kette A nie an Abnahme B."""
    a3 = 104
    welt, tickets = _ketten_manifest_und_welt(
        extra_issues={str(a3): e2._issue(a3, "Nachtrag A3", body=f"## Blocked by\n#{A2}")},
    )
    welt["issues"][str(ABN_A)]["state"] = "closed"
    repo, weltdatei, umgebung = _aufbau_leer(tmp_path, welt, tickets=tickets)

    lauf = _anh(repo, umgebung, a3, "--blockiert-von", str(A2), "--schaetzung", "30", "--umfang", "A3.")
    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    kanten = e2._welt(weltdatei)["kanten"]
    assert a3 not in kanten[str(ABN_B)]


def test_ungueltiges_abnahme_argument_bricht_ohne_schreiben_ab(tmp_path: Path) -> None:
    """#598: Ungültiges ``--abnahme`` (kein Abnahme-Ticket, zu, gemischt, auf Abnahme) schreibt nichts."""
    neu = 305
    welt, tickets = _ketten_manifest_und_welt(
        extra_issues={str(neu): e2._issue(neu, "Nachtrag")},
    )
    welt["issues"][str(ABN_A)]["state"] = "closed"
    repo, weltdatei, umgebung = _aufbau_leer(tmp_path, welt, tickets=tickets)
    manifest_vorher = e2._manifest(repo).read_bytes()

    for ticket, abn_arg, erwartet in (
        (neu, str(A2), "kein Abnahme-Ticket"),
        (neu, str(ABN_A), "schon geschlossen"),
        (neu, f"keine,{ABN_B}", "nicht mit Ticket-Nummern mischen"),
        (neu, "999", "nicht im Manifest"),
        (ABN_B, str(ABN_B), "selbst Abnahme-Ticket"),
    ):
        lauf = _anh(
            repo,
            umgebung,
            ticket,
            "--abnahme",
            abn_arg,
            "--schaetzung",
            "30",
            "--umfang",
            "Test.",
        )
        assert lauf.returncode == 1, (abn_arg, lauf.stdout + lauf.stderr)
        assert erwartet in lauf.stdout, (abn_arg, lauf.stdout)
    assert e2._welt(weltdatei)["posts"] == []
    assert e2._manifest(repo).read_bytes() == manifest_vorher

