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
const src = require("fs").readFileSync(process.argv[2], "utf8");
const a = src.indexOf("  function setzeQuelle");
const b = src.indexOf("  // Log-Anzeige baut");
const c = src.indexOf("  function uebernehme(");
const d = src.indexOf("  // Abo auf die neuesten");
const e = src.indexOf("  (async () => {");
const f = src.lastIndexOf("})();\n})();");
const els = {};
const $ = (id) => els[id] || (els[id] = {
  textContent: "", _c: new Set(),
  classList: {
    toggle(n, on) { on ? els[id]._c.add(n) : els[id]._c.delete(n); },
    add(n) { els[id]._c.add(n); }, remove(n) { els[id]._c.delete(n); },
  },
});
const window = { claude: { use: async () => ({
  doc() { throw new Error("kaputt"); }, collection() { throw new Error("kaputt"); },
}) } };
const body = "let quelle, meta, statTickets, liveAktuell, logEintraege, gerendert = false, hatRuntime = true;\n" +
  "const FALLBACK = {meta:{}, tickets:[], live:null, log:[]}; const normTicket = (t)=>t; const mische = ()=>{}; const ts = ()=>0;\n" +
  "let liveMeta, liveTickets, liveDocs, liveLog, sicherheit = null;\n" +
  src.slice(a, b) + "function zeigeBeispiel(q, text){ setzeQuelle(q, text); }\n" +
  "function logNeuZeichnen(){}\n" +
  src.slice(c, d) + src.slice(e, f + 5) + "\nreturn new Promise(r => globalThis.setTimeout(r, 50));";
new Function("$", "window", "setTimeout", "clearTimeout", body)($, window, () => 0, () => {})
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
