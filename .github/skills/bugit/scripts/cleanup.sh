#!/usr/bin/env bash
# Remove bugit MCP entries from the Copilot CLI MCP config.
# Usage: [SSHPASS=<password>] cleanup.sh [--unbind] [--purge-config] [--user <user>] [ip ...]
# SSHPASS is read from the credentials.sh file (BUGIT_CRED_FILE) when not in the environment.
# No ip = every `bugit-*` entry. --unbind also reverts the DUT's mcp.bind-address
# and stops the MCP service over SSH (needs SSHPASS). --purge-config (implies
# --unbind) also runs `bugit.dut-info clear` and removes the cached Jira credentials. The API key is never printed.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
load_credentials

CONFIG="${COPILOT_MCP_CONFIG:-$HOME/.copilot/mcp-config.json}"
unbind=0 purge=0 user=ubuntu ips=()
while [ $# -gt 0 ]; do
  case "$1" in
    --unbind) unbind=1 ;;
    --purge-config) unbind=1 purge=1 ;;
    --user) user="${2:?--user needs a value}"; shift ;;
    -*) echo "unknown option $1" >&2; exit 1 ;;
    *) ips+=("$1") ;;
  esac
  shift
done

command -v jq >/dev/null || { echo "jq missing: sudo apt install jq" >&2; exit 1; }
if [ ! -f "$CONFIG" ]; then
  jq -n --arg c "$CONFIG" '{removed:[], config:$c}'; exit 0
fi

if [ ${#ips[@]} -eq 0 ]; then
  names="$(jq -r '.mcpServers // {} | keys[] | select(startswith("bugit-"))' "$CONFIG")"
else
  names="$(printf 'bugit-%s\n' "${ips[@]}" | jq -R . | jq -rs --slurpfile c "$CONFIG" \
    '.[] | select(. as $n | $c[0].mcpServers // {} | has($n))')"
fi

if [ -z "$names" ]; then
  jq -n --arg c "$CONFIG" '{removed:[], config:$c}'; exit 0
fi

if [ "$unbind" = 1 ]; then
  check_credentials local || exit 6
  export SSHPASS
  command -v sshpass >/dev/null || { echo "sshpass missing: sudo apt install sshpass" >&2; exit 1; }
  unset_cmd="snap unset bugit mcp.bind-address"
  if [ "$purge" = 1 ]; then
    unset_cmd="$unset_cmd; bugit.dut-info clear; sudo -S -p '' rm -f /tmp/snap-private-tmp/snap.bugit/tmp/jira_submitter-credentials.json"
  fi
  while IFS= read -r n; do
    ip="${n#bugit-}"
    if printf '%s\n' "$SSHPASS" | sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
      "$user@$ip" "sudo -S -p '' $unset_cmd; sudo -S -p '' snap stop --disable bugit.mcp" >/dev/null 2>&1; then
      echo "unbound $ip" >&2
    else
      echo "warning: could not unbind $ip" >&2
    fi
  done <<< "$names"
fi

tmp="$(mktemp)"
jq --argjson r "$(printf '%s\n' "$names" | jq -R . | jq -s .)" \
  '.mcpServers |= with_entries(select(.key as $k | $r | index($k) | not))' "$CONFIG" > "$tmp"
chmod 600 "$tmp" && mv "$tmp" "$CONFIG"

printf '%s\n' "$names" | jq -R . | jq -s --arg c "$CONFIG" '{removed:., config:$c}'
