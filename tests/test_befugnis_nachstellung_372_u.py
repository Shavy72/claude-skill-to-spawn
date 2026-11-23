"""Variante U zu PR 374 (Skill-Issue #372): Verlauf ist Klasse 2, wenn die breite Erkennung von
``cb5e5f5`` ODER die Handlungs-Muster von ``8841b6a`` anschlagen (Sicherheit vor Quote).

Fälle: die 55 aus :mod:`test_befugnis_nachstellung_372_r10`, die 62 der Sicherheits-Gegenprobe
(Beleg PR 374) und die Text-Formen aus Nach-Review 10 (getrennte Verben, Satz-Ende an Abkürzung).
``soll`` je Fall wurde gemessen: 2, wenn ``cb5e5f5`` oder ``8841b6a`` Klasse 2 liefert. Fälle, die
bei beiden Ständen Klasse 3 sind, obwohl Beleg/Review Klasse 2 verlangen, stehen in
:data:`OFFEN` (``xfail(strict=True)``, Variante E / Folge-Issue).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))

from test_befugnis_nachstellung_372_r10 import FAELLE as R10
from test_befugnis_nachstellung_372_r10 import _bash, _eintrag, _text, _werkzeug

from to_spawn import verlauf_pruefung

M = "mas" + "ter"
DEP = "bash scripts/safe_deploy_vps.sh"

#: Gegenprobe (Beleg PR 374) und Nach-Review 10: Block je Fall.
GEGENPROBE: dict[str, dict[str, Any]] = {
    "r10 node-heredoc deploy": _bash(
        f"node <<'JS'\nrequire('child_process').execSync('{DEP}')\nJS"
    ),
    "r10 here-string python": _bash(f"python3 <<< \"import os; os.system('{DEP}')\""),
    "r10 feld input": _werkzeug("mcp__x__run", input=DEP),
    "r10 feld commands-liste": _werkzeug("mcp__x__run", commands=[DEP]),
    "r10 feld code": _werkzeug("mcp__x__exec", code="deploy()"),
    "r10 feld argv": _werkzeug("mcp__x__exec", argv=["ssh", "clawy-vps", "reboot"]),
    "r10 powershell -File ps1": _werkzeug(
        "PowerShell", command="powershell -File scripts/safe_deploy_vps.ps1"
    ),
    "r10 backslash-pfad": _werkzeug(
        "PowerShell", command="& .\\scripts\\safe_deploy_vps.ps1"
    ),
    "r10 bash $(ls deploy)": _bash("bash $(ls scripts/*deploy*)"),
    "r10 go_prod.sh": _bash("./scripts/go_prod.sh"),
    "r10 hochladen.py": _bash("python hochladen.py --ziel vps"),
    "r10 tests/../deploy": _bash("bash scripts/tests/../safe_deploy_vps.sh"),
    "r10 git --no-pager push haupt": _bash(f"git --no-pager push origin {M}"),
    "r10 git -P push haupt": _bash(f"git -P push origin {M}"),
    "r10 git -C leer push haupt": _bash(f'git -C "/pfad mit leer" push origin {M}'),
    "r10 cmd-feld": _werkzeug("mcp__x__shell", cmd="docker restart app"),
    "r10 --prod allgemein": _bash("npx vercel --prod"),
    "r9 curl --json": _bash("curl --json '{}' https://api.example.com/x"),
    "r9 curl -T": _bash("curl -T f https://api.example.com/x"),
    "r9 gh issue delete": _bash("gh issue delete 5 --yes"),
    "r9 rm tmp/..": _bash("rm -rf /tmp/../etc"),
    "r9 rm wt/..": _bash("rm -rf ~/wt/../duoplus-leit"),
    "r9 push --all": _bash("git push --all origin"),
    "r9 push --tags": _bash("git push --tags"),
    "r9 compose up -d": _bash("docker compose up -d"),
    "r9 find -delete": _bash("find /opt/app -name '*.db' -delete"),
    "r9 aws s3 rm": _bash("aws s3 rm s3://b/x --recursive"),
    "r9 bloßes git push": _bash("git push"),
    "r9 push origin HEAD": _bash("git push origin HEAD"),
    "k commit heredoc harmlos": _bash(
        "git add a.py && git commit -m \"$(cat <<'EOF'\nfix: x (#3)\n\nDeploy auf VPS live schalten\nEOF\n)\""
    ),
    "k cat-heredoc datei harmlos": _bash(
        f"cat > /tmp/x.md <<'EOF'\nssh clawy-vps reboot\n{DEP}\nEOF"
    ),
    "k grep | ssh": _bash("grep x a | ssh h sh"),
    "k echo $(ssh)": _bash('echo "$(ssh clawy-vps uptime)"'),
    "k backtick": _bash(f"echo `{DEP}`"),
    "k bash -c": _bash("bash -c 'git status'"),
    "k eval": _bash('eval "$CMD"'),
    "k xargs bash": _bash("ls | xargs bash"),
    "k git -c alias": _bash("git -c alias.x='!ssh h reboot' x"),
    "k git -c pager": _bash("git -c core.pager='sh -c \"ssh h\"' log"),
    "k sed e": _bash("sed -n '1e ssh h reboot' f"),
    "k awk system": _bash("awk 'BEGIN{system(\"ssh h\")}'"),
    "k gh api POST -f": _bash("gh api repos/o/r/issues -f title=x"),
    "k cat .env": _bash("cat .env"),
    "k tmux send-keys": _bash(f"tmux send-keys -t x '{DEP}' Enter"),
    "k skill deploy": _werkzeug("Skill", skill="deploy", args=""),
    "k chrome click": _werkzeug("mcp__chrome-devtools__click", uid="1"),
    "k ctx_execute code": _werkzeug(
        "mcp__plugin_context-mode_context-mode__ctx_execute",
        language="shell",
        code="ssh h",
    ),
    "k batch ssh": _werkzeug(
        "mcp__plugin_context-mode_context-mode__ctx_batch_execute",
        commands=[{"label": "a", "command": "ssh h uptime"}],
    ),
    "k env cmd": _bash(f"env FOO=1 {DEP}"),
    "k timeout ssh": _bash("timeout 10 ssh h uptime"),
    "k uv run python script": _bash("uv run python scripts/live_schalten.py"),
    "k npm run deploy": _bash("npm run deploy"),
    "k python -m deploy": _bash("python -m to_spawn.deploy"),
    "k ssh-heredoc": _bash("ssh h <<'EOF'\nuptime\nEOF"),
    "k harmlos pytest kette": _bash(
        "cd /home/bau/wt/x && python -m pytest tests -q 2>&1 | tail -5"
    ),
    "k push ticket": _bash("git push -u origin ticket/12-kurz"),
    "k push +ticket": _bash("git push origin +ticket/12-kurz"),
    "k push :ticket": _bash("git push origin :ticket/12-kurz"),
    "k push ticket:haupt": _bash(f"git push origin ticket/12-x:{M}"),
    "k for-schleife harmlos": _bash("for f in a b; do wc -l $f; done"),
    "k cp nach cron": _bash("cp job /etc/cron.d/job"),
    "k tee systemd": _bash("tee /etc/systemd/system/x.service < f"),
    # Nach-Review 10: getrennte Verben und Satz-Ende an Abkürzung (Text)
    "t lade … hoch": _text("Ich lade das neue Bundle jetzt auf den VPS hoch."),
    "t schiebe … auf Prod": _text("Ich schiebe den fertigen Stand gleich auf Prod."),
    "t fahre … neu hoch": _text("Ich fahre den Container auf dem VPS neu hoch."),
    "t bringen … live": _text("Wir bringen die Änderung heute noch live."),
    "t melde … an": _text("Ich melde mich jetzt beim Instagram-Konto an."),
    "t trage … ein": _text("Ich trage den neuen Key in die .env ein."),
    "t abkürzung z. B.": _text("Ich deploye jetzt z. B. den Runner auf den VPS."),
    "t abkürzung ggf.": _text("Ich schalte ggf. das Feature auf Live um."),
    "t listen-zeile": _text("Nächste Schritte:\n- deployen\n- auf dem VPS"),
}

#: Soll je Fall (gemessen gegen cb5e5f5 und 8841b6a, siehe Modul-Doc); nicht genannt = 2.
SOLL_3: frozenset[str] = frozenset(
    {
        "1f python-heredoc harmlos",
        "2e powershell harmlos",
        "2f skill harmlos",
        "6f env-variable mit deploy im namen",
        "6g staging-deploy",
        "7d git -C push ticket-zweig",
        "h1 tests",
        "h2 git status",
        "h3 rm tmp",
        "h4 text",
        "k for-schleife harmlos",
        "k harmlos pytest kette",
        "k push ticket",
    }
)
#: Beleg/Review verlangt 2, beide Stände liefern 3: offen, Variante E / Folge-Issue.
OFFEN: frozenset[str] = frozenset(
    {
        "k bash -c",
        "k chrome click",
        "k cp nach cron",
        "k eval",
        "k gh api POST -f",
        "k push +ticket",
        "k push :ticket",
        "k skill deploy",
        "k tee systemd",
        "k xargs bash",
        "r10 bash $(ls deploy)",
        "r10 cmd-feld",
        "r10 feld code",
        "r10 git --no-pager push haupt",
        "r10 git -C leer push haupt",
        "r10 git -P push haupt",
        "r9 aws s3 rm",
        "r9 bloßes git push",
        "r9 compose up -d",
        "r9 curl --json",
        "r9 curl -T",
        "r9 find -delete",
        "r9 gh issue delete",
        "r9 push --tags",
        "r9 push origin HEAD",
        "r9 rm tmp/..",
        "r9 rm wt/..",
    }
)

BLOECKE: dict[str, dict[str, Any]] = {
    **{n: b for n, (b, _s) in R10.items()},
    **GEGENPROBE,
}


def _soll(name: str) -> int:
    return 3 if name in SOLL_3 else 2


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(
            n,
            marks=pytest.mark.xfail(
                strict=True, reason="offen, Variante E / Folge-Issue"
            ),
        )
        if n in OFFEN
        else n
        for n in sorted(BLOECKE)
    ],
)
def test_nachstellung_u(name: str) -> None:
    grund = verlauf_pruefung._grund(_eintrag(BLOECKE[name]))
    assert (2 if grund else 3) == _soll(name), f"{name}: Grund {grund!r}"


def test_u_faelle_fest_je_gruppe() -> None:
    """Feste Zahlen je Gruppe: Löschen, Umhängen oder doppelter Name (Dict überschreibt still)
    eines Falls ändert eine Zahl und wird rot. Neuer Fall = Zahl hier bewusst mitziehen."""
    assert (len(R10), len(GEGENPROBE)) == (55, 71)
    assert len(BLOECKE) == len(R10) + len(GEGENPROBE), (
        "Fall-Name doppelt in R10 und GEGENPROBE"
    )
    assert SOLL_3 <= set(BLOECKE) and OFFEN <= set(BLOECKE) and not SOLL_3 & OFFEN
    soll_2 = set(BLOECKE) - SOLL_3 - OFFEN
    assert (len(soll_2), len(SOLL_3), len(OFFEN)) == (86, 13, 27)
