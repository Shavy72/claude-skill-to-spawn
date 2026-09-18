#!/usr/bin/env bash
# ssh_durch_sandbox.sh — ProxyCommand für ssh INNERHALB der Sandbox (srt).
#
# Reicht an ssh_durch_sandbox.py weiter (HTTP-CONNECT über den Sandbox-Proxy aus
# $HTTPS_PROXY; die Proxy-Anmeldung steht nie in argv). nest_server.sh trägt ein:
#   Match exec "test -n \"$TO_SPAWN_SANDBOX\""
#       ProxyCommand ~/.claude/skills/to-spawn/nest/ssh_durch_sandbox.sh %h %p
# TO_SPAWN_SANDBOX=1 setzt der srt-Präfix von bau.py. Das Ziel (host:port) muss in der
# Sandbox erlaubt sein (.to-spawn/config.json → sandbox.netz_zusatz).
set -euo pipefail
exec python3 "$(dirname "${BASH_SOURCE[0]}")/ssh_durch_sandbox.py" "$@"
