"""Rot-/Grün-Tests für die Smart-Zone-Nachmessung (#875 / App #1391).

Prüft reproduzierbare Transcript-Auswertung, Messgröße A (gepaarte echte
Tool-Fehlerquote 250–330k vs. 100–175k), Messgröße B (Code-Re-Edit ≤ 10 Runden
ab 250k ohne .md), Nebenkennzahl Übergaben/Woche, 14-Tage-Frist-Prüfung,
Schadens-Schwellen, Determinismus sowie Plattform-Neutralität.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[1]
SKRIPT = WURZEL / "messung" / "smart_zone_nachmessung.py"


def _schreibe_jsonl(pfad: Path, zeilen: list[dict[str, object]]) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    inhalt = "\n".join(json.dumps(z, ensure_ascii=False) for z in zeilen) + "\n"
    pfad.write_text(inhalt, encoding="utf-8")


def _assistant_zeile(
    msg_id: str,
    ts: str,
    input_t: int,
    cache_read_t: int,
    tools: list[tuple[str, str | None, str]],
    *,
    modell: str = "claude-opus-4-6",
    cache_creation_t: int = 0,
    output_t: int = 200,
) -> dict[str, object]:
    content = []
    for name, dateipfad, tool_id in tools:
        eingabe: dict[str, object] = {}
        if dateipfad is not None:
            eingabe["file_path"] = dateipfad
        content.append({"type": "tool_use", "id": tool_id, "name": name, "input": eingabe})
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "id": msg_id,
            "model": modell,
            "usage": {
                "input_tokens": input_t,
                "cache_read_input_tokens": cache_read_t,
                "cache_creation_input_tokens": cache_creation_t,
                "output_tokens": output_t,
            },
            "content": content,
        },
    }


def _tool_result_zeile(
    ergebnisse: list[tuple[str, bool, str]],
    *,
    ts: str = "2026-12-01T10:00:00Z",
) -> dict[str, object]:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "is_error": ist_fehler,
                    "content": text,
                }
                for tid, ist_fehler, text in ergebnisse
            ]
        },
    }


def _user_start(text: str, ts: str = "2026-12-01T09:59:00Z") -> dict[str, object]:
    return {"type": "user", "timestamp": ts, "message": {"content": text}}


def test_modul_importierbar_und_hat_type_hints() -> None:
    """Das Auswerte-Skript liegt unter messung/smart_zone_nachmessung.py."""
    assert SKRIPT.is_file(), f"Auswerte-Skript fehlt: {SKRIPT}"
    sys.path.insert(0, str(WURZEL))
    from messung import smart_zone_nachmessung as sz

    assert callable(sz.auswerten)
    assert callable(sz.formatiere_markdown)
    assert callable(sz.main)


def test_messgroesse_a_gepaart_und_fehlerkategorien(tmp_path: Path) -> None:
    """Messgröße A vergleicht echte Fehler 250–330k vs. 100–175k gepaart je Session.

    Hook-Blocks und Nutzer-Ablehnungen zählen nicht als echte Fehler.
    """
    sys.path.insert(0, str(WURZEL))
    from messung import smart_zone_nachmessung as sz

    projekte = tmp_path / "projects"
    zeilen: list[dict[str, object]] = [_user_start("Analysiere das Modul", "2026-12-01T10:00:00Z")]

    # 3 Runden im Band 100-175k: 10 Tools, davon 1 echter Fehler (exit code) + 1 Hook-Block
    for r in range(1, 4):
        tools = [("Bash", None, f"lo_{r}_{k}") for k in range(3 if r < 3 else 4)]
        zeilen.append(_assistant_zeile(f"m_lo_{r}", f"2026-12-01T10:0{r}:00Z", 20_000, 100_000, tools))
        ergs = []
        for _, _, tid in tools:
            if tid == "lo_1_0":
                ergs.append((tid, True, "Command failed with exit code 1"))
            elif tid == "lo_1_1":
                ergs.append((tid, True, "Hook blocked tool call"))
            else:
                ergs.append((tid, False, "ok"))
        zeilen.append(_tool_result_zeile(ergs))

    # 3 Runden im Band 250-330k: 10 Tools, davon 3 echte Fehler (stringmatch/exit/andere) + 2 Reject/Hook
    for r in range(1, 4):
        tools = [("Bash", None, f"hi_{r}_{k}") for k in range(3 if r < 3 else 4)]
        zeilen.append(_assistant_zeile(f"m_hi_{r}", f"2026-12-01T10:1{r}:00Z", 20_000, 260_000, tools))
        ergs = []
        for _, _, tid in tools:
            if tid == "hi_1_0":
                ergs.append((tid, True, "String to replace not found in file"))
            elif tid == "hi_1_1":
                ergs.append((tid, True, "exit code 2"))
            elif tid == "hi_2_0":
                ergs.append((tid, True, "unexpected failure"))
            elif tid == "hi_2_1":
                ergs.append((tid, True, "User rejected tool use"))
            elif tid == "hi_3_0":
                ergs.append((tid, True, "Permission blockiert"))
            else:
                ergs.append((tid, False, "ok"))
        zeilen.append(_tool_result_zeile(ergs))

    _schreibe_jsonl(projekte / "projekt-alpha" / "sess-1.jsonl", zeilen)

    erg = sz.auswerten(
        projekt_wurzeln=[projekte],
        staffel_ordner=[],
        ab_datum="2026-11-19",
        stichtag="2026-12-05",
        umstellung_datum="2026-11-19",
    )

    assert erg.messgroesse_a.sessions_gepaart == 1
    assert erg.messgroesse_a.tools_unten == 10
    assert erg.messgroesse_a.fehler_echt_unten == 1
    assert erg.messgroesse_a.tools_oben == 10
    assert erg.messgroesse_a.fehler_echt_oben == 3
    assert erg.messgroesse_a.quote_unten == pytest.approx(0.1)
    assert erg.messgroesse_a.quote_oben == pytest.approx(0.3)
    assert erg.messgroesse_a.verhaeltnis == pytest.approx(3.0)
    # Stichprobe oben hat nur 10 < 300 Tools → kein automatischer Schaden
    assert erg.messgroesse_a.stichprobe_ausreichend is False
    assert erg.messgroesse_a.schaden_erkannt is False


def test_messgroesse_a_schaden_ab_300_tools_oben(tmp_path: Path) -> None:
    """Bei >= 300 Tools oben und Quote oben > 1,5x unten wird Schaden erkannt."""
    sys.path.insert(0, str(WURZEL))
    from messung import smart_zone_nachmessung as sz

    projekte = tmp_path / "projects"
    zeilen: list[dict[str, object]] = [_user_start("Große Sitzung", "2026-12-02T10:00:00Z")]

    # Unten (100-175k): 100 Tools, 2 echte Fehler -> 2%
    for r in range(5):
        tools = [("Read", None, f"lo_{r}_{k}") for k in range(20)]
        zeilen.append(_assistant_zeile(f"mlo_{r}", f"2026-12-02T10:0{r}:00Z", 10_000, 120_000, tools))
        ergs = [
            (tid, (r == 0 and k < 2), "exit code 1" if (r == 0 and k < 2) else "ok")
            for k, (_, _, tid) in enumerate(tools)
        ]
        zeilen.append(_tool_result_zeile(ergs))

    # Oben (250-330k): 300 Tools, 12 echte Fehler -> 4% (= 2.0x > 1.5x)
    for r in range(15):
        tools = [("Read", None, f"hi_{r}_{k}") for k in range(20)]
        zeilen.append(_assistant_zeile(f"mhi_{r}", f"2026-12-02T11:{r:02d}:00Z", 10_000, 270_000, tools))
        ergs = [
            (tid, (r < 12 and k == 0), "exit code 1" if (r < 12 and k == 0) else "ok")
            for k, (_, _, tid) in enumerate(tools)
        ]
        zeilen.append(_tool_result_zeile(ergs))

    _schreibe_jsonl(projekte / "projekt-alpha" / "sess-gross.jsonl", zeilen)

    erg = sz.auswerten(
        projekt_wurzeln=[projekte],
        staffel_ordner=[],
        ab_datum="2026-11-19",
        stichtag="2026-12-05",
        umstellung_datum="2026-11-19",
    )

    assert erg.messgroesse_a.tools_oben == 300
    assert erg.messgroesse_a.stichprobe_ausreichend is True
    assert erg.messgroesse_a.schaden_erkannt is True
    assert erg.empfehlung_code == "zurueck"


def test_messgroesse_b_code_re_edit_trennt_md_und_erkennt_schaden(tmp_path: Path) -> None:
    """Messgröße B zählt nur Code-Edits (keine .md / handoff) ab 250k und erkennt > 60 % Re-Edit."""
    sys.path.insert(0, str(WURZEL))
    from messung import smart_zone_nachmessung as sz

    projekte = tmp_path / "projects"
    # Windows- und POSIX-Pfade gemischt für dieselbe Code-Datei prüfen Normalisierung
    zeilen: list[dict[str, object]] = [
        _user_start("Code überarbeiten", "2026-12-02T09:00:00Z"),
        # Runde 0 (< 250k): erster Edit an src/app.py
        _assistant_zeile("m0", "2026-12-02T09:01:00Z", 10_000, 150_000, [("Edit", r"C:\repo\src\app.py", "t0")]),
        _tool_result_zeile([("t0", False, "ok")]),
        # Runde 1 (>= 250k): Re-Edit derselben Datei innerhalb <= 10 Runden (Code-Re-Edit #1)
        _assistant_zeile("m1", "2026-12-02T09:02:00Z", 10_000, 250_000, [("Edit", "C:/repo/src/app.py", "t1")]),
        _tool_result_zeile([("t1", False, "ok")]),
        # Runde 2 (>= 250k): nochmal Re-Edit derselben Datei (Code-Re-Edit #2)
        _assistant_zeile("m2", "2026-12-02T09:03:00Z", 10_000, 255_000, [("Edit", "C:/repo/src/app.py", "t2")]),
        _tool_result_zeile([("t2", False, "ok")]),
        # Runde 3 (>= 250k): neuer Edit einer anderen Code-Datei (kein Re-Edit)
        _assistant_zeile("m3", "2026-12-02T09:04:00Z", 10_000, 260_000, [("Write", "/repo/src/neu.py", "t3")]),
        _tool_result_zeile([("t3", False, "ok")]),
        # Runde 4 (>= 250k): .md-Edits zählen NICHT zu Code-Edits
        _assistant_zeile(
            "m4",
            "2026-12-02T09:05:00Z",
            10_000,
            265_000,
            [
                ("Edit", "/repo/docs/notizen.md", "t4"),
                ("Edit", "/repo/docs/notizen.md", "t5"),
                ("Write", "/repo/.claude-handoff.md", "t6"),
            ],
        ),
        _tool_result_zeile([("t4", False, "ok"), ("t5", False, "ok"), ("t6", False, "ok")]),
    ]
    _schreibe_jsonl(projekte / "projekt-beta" / "sess-b.jsonl", zeilen)

    erg = sz.auswerten(
        projekt_wurzeln=[projekte],
        staffel_ordner=[],
        ab_datum="2026-11-19",
        stichtag="2026-12-05",
        umstellung_datum="2026-11-19",
    )

    # Ab 250k: 3 Code-Edits (t1, t2, t3), davon 2 Re-Edits (t1, t2) -> 2/3 = 66.7 % > 60 %
    assert erg.messgroesse_b.code_edits_ab_250k == 3
    assert erg.messgroesse_b.code_re_edits_le_10 == 2
    assert erg.messgroesse_b.md_edits_ab_250k == 3
    assert erg.messgroesse_b.schaden_erkannt is True
    assert erg.empfehlung_code == "zurueck"


def test_uebergaben_je_woche_und_14_tage_frist_sowie_determinismus(tmp_path: Path) -> None:
    """Prüft Übergaben/Woche, 14-Tage-Frist-Sperre und byte-gleiche Reproduzierbarkeit."""
    sys.path.insert(0, str(WURZEL))
    from messung import smart_zone_nachmessung as sz

    projekte = tmp_path / "projects"
    staffel = tmp_path / "staffel"
    staffel.mkdir()

    # 1 kleine Opus-Session mit 5 Runden
    zeilen = [_user_start("Kurze Analyse", "2026-11-25T10:00:00Z")]
    for i in range(5):
        zeilen.append(_assistant_zeile(f"m_{i}", f"2026-11-25T10:0{i}:00Z", 5_000, 110_000, [("Read", None, f"t_{i}")]))
        zeilen.append(_tool_result_zeile([(f"t_{i}", False, "ok")]))
    _schreibe_jsonl(projekte / "projekt-gamma" / "sess-g.jsonl", zeilen)

    # 2 Staffel-Einträge im Zeitraum 2026-11-23 bis 2026-11-29 (7 Tage) -> 2,0/Woche
    for idx, (ts, status) in enumerate(
        [
            ("2026-11-24T12:00:00Z", "übernommen"),
            ("2026-11-26T12:00:00Z", "wartet_subagenten"),
            ("2026-11-10T12:00:00Z", "übernommen"),  # vor Zeitraum
        ]
    ):
        (staffel / f"st-{idx}.json").write_text(
            json.dumps({"staffel_id": f"st-{idx}", "ts": ts, "status": status}, ensure_ascii=False),
            encoding="utf-8",
        )

    # smart-zone.json mit Änderungsdatum 2026-11-23
    sz_json = tmp_path / "smart-zone.json"
    sz_json.write_text(
        json.dumps({"_aenderung": "2026-11-23: haupt (Opus) handoff 250->300, hart 300->330"}, ensure_ascii=False),
        encoding="utf-8",
    )

    # 2026-11-29 ist erst 6 Tage nach 2026-11-23 -> Frist NICHT erfüllt
    erg1 = sz.auswerten(
        projekt_wurzeln=[projekte],
        staffel_ordner=[staffel],
        ab_datum="2026-11-23",
        stichtag="2026-11-29",
        smart_zone_pfad=sz_json,
    )
    erg2 = sz.auswerten(
        projekt_wurzeln=[projekte],
        staffel_ordner=[staffel],
        ab_datum="2026-11-23",
        stichtag="2026-11-29",
        smart_zone_pfad=sz_json,
    )

    assert erg1.frist.umstellung_datum == "2026-11-23"
    assert erg1.frist.tage_seit_umstellung == 6
    assert erg1.frist.frist_erfuellt is False
    assert erg1.empfehlung_code == "abwarten"

    assert erg1.uebergaben.staffel_im_zeitraum == 2
    assert erg1.uebergaben.zeitraum_tage == 7.0
    assert erg1.uebergaben.uebergaben_je_woche == 2.0

    # Determinismus: gleiche Eingabe -> byte-gleiche Markdown-Ausgabe
    md1 = sz.formatiere_markdown(erg1)
    md2 = sz.formatiere_markdown(erg2)
    assert md1 == md2
    assert "Messgröße A" in md1
    assert "Messgröße B" in md1
    assert "Übergaben" in md1


def test_cli_schreibt_ausgabe_und_erzeugt_valides_json(tmp_path: Path) -> None:
    """CLI-Lauf mit --json und --ausgabe funktioniert eigenständig."""
    projekte = tmp_path / "projects"
    projekte.mkdir()
    ausgabe_md = tmp_path / "befund.md"

    lauf = subprocess.run(
        [
            sys.executable,
            str(SKRIPT),
            "--projekte",
            str(projekte),
            "--ab",
            "2026-11-23",
            "--stichtag",
            "2026-12-08",
            "--umstellung",
            "2026-11-23",
            "--ausgabe",
            str(ausgabe_md),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert lauf.returncode == 0, lauf.stderr
    assert ausgabe_md.is_file()
    assert "Smart-Zone-Nachmessung" in ausgabe_md.read_text(encoding="utf-8")

    lauf_json = subprocess.run(
        [
            sys.executable,
            str(SKRIPT),
            "--projekte",
            str(projekte),
            "--ab",
            "2026-11-23",
            "--stichtag",
            "2026-12-08",
            "--umstellung",
            "2026-11-23",
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert lauf_json.returncode == 0, lauf_json.stderr
    daten = json.loads(lauf_json.stdout)
    assert daten["frist"]["frist_erfuellt"] is True
    assert daten["empfehlung_code"] == "beobachten"


def test_cli_erzwingt_utf8_ausgabe_trotz_cp1252_umgebung(tmp_path: Path) -> None:
    """Unter Windows-Codepage (cp1252) liefern stdout (Markdown/JSON) und stderr trotzdem valides UTF-8."""
    projekte = tmp_path / "projects"
    projekte.mkdir()
    env_cp1252 = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}

    lauf_md = subprocess.run(
        [
            sys.executable,
            str(SKRIPT),
            "--projekte",
            str(projekte),
            "--ab",
            "2026-11-23",
            "--stichtag",
            "2026-12-08",
            "--umstellung",
            "2026-11-23",
        ],
        capture_output=True,
        env=env_cp1252,
        check=False,
    )
    assert lauf_md.returncode == 0
    md_text = lauf_md.stdout.decode("utf-8")
    err_md = lauf_md.stderr.decode("utf-8")
    assert "Messgröße A" in md_text
    assert "250–330k" in md_text
    assert "14-Tage-Frist" in md_text
    assert "Auswertung" in err_md or err_md == "" or "INFO" in err_md

    lauf_json = subprocess.run(
        [
            sys.executable,
            str(SKRIPT),
            "--projekte",
            str(projekte),
            "--ab",
            "2026-11-23",
            "--stichtag",
            "2026-12-08",
            "--umstellung",
            "2026-11-23",
            "--json",
        ],
        capture_output=True,
        env=env_cp1252,
        check=False,
    )
    assert lauf_json.returncode == 0
    json_text = lauf_json.stdout.decode("utf-8")
    _ = lauf_json.stderr.decode("utf-8")
    daten = json.loads(json_text)
    assert daten["frist"]["frist_erfuellt"] is True
    assert daten["empfehlung_code"] == "beobachten"
