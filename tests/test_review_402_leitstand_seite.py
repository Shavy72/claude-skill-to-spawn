"""Issue #402 A4: Nach Fehler beim Abonnieren muss die Störung sichtbar bleiben.

Führt die echten Funktionen aus leitstand/seite.html per node mit minimalem DOM-Stub aus.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SEITE = Path(__file__).resolve().parent.parent / "leitstand" / "seite.html"

HARNESS = r"""
// Zeilenenden vereinheitlichen: ein Windows-Checkout (core.autocrlf) liefert CRLF.
const src = require("fs").readFileSync(process.argv[2], "utf8").replace(/\r\n/g, "\n");
const a = src.indexOf("  /* ---------- Daten ---------- */");
const b = src.indexOf("  // Log-Anzeige baut");
const c = src.indexOf("  function uebernehme(");
const d = src.indexOf("  // Abo auf die neuesten");
const e = src.indexOf("  (async () => {");
const f = src.lastIndexOf("})();\n})();");
const marker = {a, b, c, d, e, f};
for (const k in marker) if (marker[k] < 0) { console.error("Schnittmarke fehlt in seite.html: " + k); process.exit(2); }
if (!(a < b && b < c && c < d && d < e && e < f)) { console.error("Schnittmarken in falscher Reihenfolge"); process.exit(2); }
const modus = process.argv[3] || "sync";
const els = {};
const $ = (id) => els[id] || (els[id] = {
  textContent: "", _c: new Set(),
  classList: {
    toggle(n, on) { on ? els[id]._c.add(n) : els[id]._c.delete(n); },
    add(n) { els[id]._c.add(n); }, remove(n) { els[id]._c.delete(n); },
  },
});
const spaet = (fn) => Promise.resolve().then(fn);
const quelleMock = (ok) => ({
  orderBy() { return this; }, limit() { return this; },
  onSnapshot(cb, err) { spaet(() => ok ? cb({ docs: [], exists: false }) : err(new Error("async kaputt"))); },
});
const db = modus === "sync"
  ? { doc() { throw new Error("kaputt"); }, collection() { throw new Error("kaputt"); } }
  : { doc: () => quelleMock(false), collection: () => quelleMock(modus === "erholt") };
const window = { claude: { use: async () => db } };
const timer = new Map(); let tid = 0;
const setT = (fn) => { timer.set(++tid, fn); return tid; };
const clearT = (id) => { timer.delete(id); };
const body = "let quelle, meta, statTickets, liveAktuell, logEintraege, gerendert = false, hatRuntime = true;\n" +
  "const FALLBACK = {meta:{}, tickets:[], live:null, log:[]}; const normTicket = (t)=>t; const mische = ()=>{}; const ts = ()=>0;\n" +
  "let liveMeta, liveTickets, liveDocs, liveLog, sicherheit = null;\n" +
  src.slice(a, b) + "function zeigeBeispiel(q, text){ setzeQuelle(q, text); }\n" +
  "function logNeuZeichnen(){}\n" +
  src.slice(c, e) + src.slice(e, f + 5) + "\nreturn new Promise(r => globalThis.setTimeout(r, 50));";
new Function("$", "window", "setTimeout", "clearTimeout", body)($, window, setT, clearT)
  .then(() => { for (const fn of [...timer.values()]) fn(); })
  .then(() => console.log(JSON.stringify({
    text: els["h-live-text"].textContent, stoerung: els["h-live"]._c.has("stoerung"),
  })));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_stoerung_bleibt_nach_abo_fehler_sichtbar(tmp_path: Path) -> None:
    js = tmp_path / "harness.js"
    js.write_text(HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(js), str(SEITE)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["stoerung"] is True
    assert "gestört" in out["text"]


def _lauf(tmp_path: Path, modus: str) -> dict:
    js = tmp_path / "harness.js"
    js.write_text(HARNESS, encoding="utf-8")
    res = subprocess.run(
        ["node", str(js), str(SEITE), modus], capture_output=True, text=True, encoding="utf-8", timeout=60
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_stoerung_bleibt_nach_async_fehler_und_sicherheits_timer(tmp_path: Path) -> None:
    """Async onSnapshot-Fehler, danach feuert der Sicherheits-Timer: Störung darf nicht überschrieben werden."""
    out = _lauf(tmp_path, "async")
    assert out["stoerung"] is True
    assert "gestört" in out["text"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node fehlt")
def test_stoerung_endet_wenn_abo_nach_fehler_wieder_liefert(tmp_path: Path) -> None:
    out = _lauf(tmp_path, "erholt")
    assert out["stoerung"] is False
    assert "gestört" not in out["text"]


# --- #226: HTML immer LF (identische Bytes unter Windows und Linux) ----------

WURZEL = Path(__file__).resolve().parent.parent


def test_seite_html_hat_nur_lf() -> None:
    """#226: ``leitstand/seite.html`` liegt unter Windows und Linux ohne CR (``\\r``) vor."""
    roh = SEITE.read_bytes()
    assert b"\n" in roh
    assert b"\r" not in roh, "leitstand/seite.html enthält CR-Bytes (CRLF statt LF)"


def test_gitattributes_erzwingt_lf_fuer_html(tmp_path: Path) -> None:
    """#226: ``.gitattributes`` legt ``*.html text eol=lf`` fest und hält ``core.autocrlf=true`` ab."""
    attr_datei = WURZEL / ".gitattributes"
    if not attr_datei.is_file():
        pytest.skip("installierte Kopie ohne .gitattributes")

    if (WURZEL / ".git").exists():
        check = subprocess.run(
            ["git", "check-attr", "text", "eol", "--", "leitstand/seite.html"],
            cwd=str(WURZEL),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=True,
        )
        zeilen = check.stdout.splitlines()
        assert any(z.endswith(": text: set") for z in zeilen), check.stdout
        assert any(z.endswith(": eol: lf") for z in zeilen), check.stdout

    # Echter Git-Checkout mit Windows-Vorgabe core.autocrlf=true: muss trotzdem LF liefern.
    repo = tmp_path / "repo"
    repo.mkdir()
    git_cfg = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True, timeout=30)
    (repo / ".gitattributes").write_bytes(attr_datei.read_bytes())
    html = repo / "probe.html"
    html.write_bytes(b"<!doctype html>\n<html>\n<body>ok</body>\n</html>\n")
    subprocess.run(["git", "add", ".gitattributes", "probe.html"], cwd=str(repo), check=True, timeout=30)
    subprocess.run(["git", *git_cfg, "commit", "-q", "-m", "init"], cwd=str(repo), check=True, timeout=30)
    subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=str(repo), check=True, timeout=30)
    html.unlink()
    subprocess.run(["git", "checkout", "--", "probe.html"], cwd=str(repo), check=True, timeout=30)
    assert b"\r" not in html.read_bytes(), "Checkout mit core.autocrlf=true erzeugte CRLF in HTML-Datei"
