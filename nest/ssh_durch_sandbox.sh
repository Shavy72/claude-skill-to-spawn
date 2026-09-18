#!/usr/bin/env bash
# ssh_durch_sandbox.sh — ProxyCommand für ssh INNERHALB der Sandbox (srt).
#
# In srt gibt es kein eigenes Netz, nur den Proxy der Sandbox. srt nennt ihn in
# $HTTPS_PROXY (http://<auth>@localhost:<port>). Dieses Skript reicht die
# ssh-Verbindung per socat durch diesen Proxy. nest_server.sh trägt es in ~/.ssh/config ein:
#   Match exec "test -n \"$HTTPS_PROXY$https_proxy\""
#       ProxyCommand ~/.claude/skills/to-spawn/nest/ssh_durch_sandbox.sh %h %p
# Das Ziel (host:port) muss in der Sandbox erlaubt sein (sandbox.netz_zusatz).
# Die Proxy-Anmeldung wird nie ausgegeben.
set -euo pipefail

ziel_host="${1:?Aufruf: ssh_durch_sandbox.sh <host> <port>}"
ziel_port="${2:-22}"
proxy="${HTTPS_PROXY:-${https_proxy:-}}"
if [ -z "$proxy" ]; then
  echo "ssh_durch_sandbox: kein HTTPS_PROXY — läuft nicht in der Sandbox" >&2
  exit 2
fi

rest="${proxy#*://}"
rest="${rest%%/*}"
anmeldung=""
case "$rest" in
  *@*) anmeldung="${rest%@*}"; rest="${rest##*@}" ;;
esac
proxy_host="${rest%:*}"
proxy_port="${rest##*:}"
if [ -z "$proxy_host" ] || [ -z "$proxy_port" ] || [ "$proxy_host" = "$rest" ]; then
  echo "ssh_durch_sandbox: HTTPS_PROXY ohne Host:Port — Abbruch" >&2
  exit 2
fi

ziel="PROXY:${proxy_host}:${ziel_host}:${ziel_port},proxyport=${proxy_port}"
[ -n "$anmeldung" ] && ziel="${ziel},proxyauth=${anmeldung}"
exec socat - "$ziel"
