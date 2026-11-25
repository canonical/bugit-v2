#!/usr/bin/env bash
# Configure the bugit MCP server on a DUT and register it in the Copilot CLI MCP config.
# Usage: [SSHPASS=<password>] [BUGIT_PROJECT=.. BUGIT_JIRA_ASSIGNEE=.. BUGIT_LP_ASSIGNEE=..
#          BUGIT_CID=.. BUGIT_SKU=.. BUGIT_PLATFORM_TAGS=tag1,tag2 \
#          BUGIT_JIRA_EMAIL=.. BUGIT_JIRA_TOKEN=..] \
#          configure.sh [--status] <ip> [user]
# The BUGIT_* values that are set are stored by bugit on the DUT (bugit.dut-info).
# SSHPASS and the Jira email/token are read from the file made by credentials.sh
# (BUGIT_CRED_FILE, mode 600) when not in the environment, so they never go through the chat.
# Invalid values are rejected up front (exit 5) with the expected format; missing
# credentials exit 6 with the credentials.sh command to run.
# --status only reports which settings the DUT already has (secrets redacted).
# Prints a JSON summary (the API key and tokens are never printed).
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
load_credentials || exit 6

status_only=0
TRACKER=""
if [ "${1:-}" = "--status" ]; then
  status_only=1; shift
  if [ "${1:-}" = "--tracker" ]; then TRACKER="${2:-}"; shift 2 || true; fi
fi
IP="${1:?usage: configure.sh [--status [--tracker <t>]] <ip> [user]}"
USER_NAME="${2:-ubuntu}"
check_credentials "${TRACKER:-local}" || exit 6
export SSHPASS
CONFIG="${COPILOT_MCP_CONFIG:-$HOME/.copilot/mcp-config.json}"
NAME="bugit-$IP"

command -v sshpass >/dev/null || { echo "sshpass missing: sudo apt install sshpass" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq missing: sudo apt install jq" >&2; exit 1; }

dut() { sshpass -e ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "$USER_NAME@$IP" "$@"; }
# sudo with the SSH password on stdin; works for passwordless sudo too
dut_sudo() { printf '%s\n' "$SSHPASS" | dut "sudo -S -p '' $*"; }

dut "snap list bugit >/dev/null 2>&1" || {
  echo "bugit snap not installed on $IP. Install: sudo snap install bugit --edge --devmode" >&2
  exit 2
}

# Settings live in bugit itself: `bugit.dut-info` (run as the DUT user) holds CID, SKU,
# project and assignees; Jira credentials go to the cache file bugit already uses
# ({"email","token"}), inside the snap's private /tmp, where the MCP daemon reads them.
JIRA_CREDS=/tmp/snap-private-tmp/snap.bugit/tmp/jira_submitter-credentials.json
q() { printf '%q' "$1"; }

if [ "$status_only" = 1 ]; then
  info="$(dut "bugit.dut-info show --json 2>/dev/null" || true)"
  [ -n "$info" ] || info='{}'
  jira=false
  dut_sudo "test -s $JIRA_CREDS" >/dev/null 2>&1 && jira=true
  # the assignee of the other tracker is not needed
  jq -n --arg ip "$IP" --arg t "$TRACKER" --argjson i "$info" --argjson j "$jira" \
    '{dut:$ip, dut_info:$i, jira_credentials_cached:$j,
      missing:([["cid","sku","project","jira_assignee","lp_assignee","platform_tags"][]
        | select(($t != "jira" or . != "lp_assignee") and ($t != "launchpad" or . != "jira_assignee"))
        | select(($i[.] // null) as $v | $v == null or $v == "" or $v == [])])}'
  exit 0
fi

applied=()
args=()
add() { # env var, dut-info option, label
  local val="${!1:-}"
  [ -n "$val" ] || return 0
  args+=("$2 $(q "$val")")
  applied+=("$3")
}
# Reject bad values here with a clear message instead of bugit.dut-info's raw validation dump
bad() { echo "$1" >&2; exit 5; }
[ -z "${BUGIT_LP_ASSIGNEE:-}" ] || [[ "$BUGIT_LP_ASSIGNEE" =~ ^[A-Za-z0-9]+$ ]] \
  || bad "Launchpad assignee '$BUGIT_LP_ASSIGNEE' is invalid: use only the Launchpad ID (letters and digits), without \"lp:\", \"~\" or a URL. Example: hanhsuan"
[ -z "${BUGIT_CID:-}" ] || [[ "$BUGIT_CID" =~ ^[0-9]{6}-[0-9]{5}$ ]] \
  || bad "CID '$BUGIT_CID' is invalid: expected 6 digits, a dash and 5 digits. Example: 202001-27667"
[ -z "${BUGIT_PROJECT:-}" ] || [[ "$BUGIT_PROJECT" =~ ^[A-Za-z0-9]+$ ]] \
  || bad "Project '$BUGIT_PROJECT' is invalid: use letters and digits only, no spaces. Example: somerville"
[ -z "${BUGIT_SKU:-}" ] || [[ "$BUGIT_SKU" =~ ^[A-Za-z0-9_-]+$ ]] \
  || bad "SKU '$BUGIT_SKU' is invalid: use letters, digits, '-' and '_' only"
[ -z "${BUGIT_JIRA_ASSIGNEE:-}" ] || [[ "$BUGIT_JIRA_ASSIGNEE" =~ ^[^@[:space:]]+@[^@[:space:]]+$ ]] \
  || bad "Jira assignee '$BUGIT_JIRA_ASSIGNEE' is invalid: use the assignee's email address"
_pt="${BUGIT_PLATFORM_TAGS:-}"
for t in ${_pt//,/ }; do
  [[ "$t" =~ ^[A-Za-z0-9-]+$ ]] \
    || bad "Platform tag '$t' is invalid: use letters, digits and '-' only (no spaces or '_'). Example: Test-platform"
done

add BUGIT_CID --cid cid
add BUGIT_SKU --sku sku
add BUGIT_PROJECT --project project
add BUGIT_JIRA_ASSIGNEE --jira-assignee jira_assignee
add BUGIT_LP_ASSIGNEE --lp-assignee lp_assignee
if [ -n "${BUGIT_PLATFORM_TAGS:-}" ]; then
  IFS=',' read -ra tags <<<"$BUGIT_PLATFORM_TAGS"
  for t in "${tags[@]}"; do
    t="${t//[[:space:]]/}"
    [ -z "$t" ] || args+=("--platform-tags $(q "$t")")
  done
  applied+=(platform_tags)
fi
if [ ${#args[@]} -gt 0 ]; then
  dut "bugit.dut-info set ${args[*]}" >&2 || { echo "bugit.dut-info rejected a value (details above); fix it and run again" >&2; exit 5; }
fi

# The token travels over stdin only, never on a command line
if [ -n "${BUGIT_JIRA_EMAIL:-}" ] && [ -n "${BUGIT_JIRA_TOKEN:-}" ]; then
  tmpf="$(dut 'umask 077; mktemp')"
  jq -nc --arg e "$BUGIT_JIRA_EMAIL" --arg t "$BUGIT_JIRA_TOKEN" '{email:$e, token:$t}' | dut "cat > $tmpf"
  dut_sudo "sh -c 'mkdir -p $(dirname $JIRA_CREDS) && install -m 600 -o root $tmpf $JIRA_CREDS; rc=\$?; rm -f $tmpf; exit \$rc'" >/dev/null \
    || { echo "could not store the Jira credentials on $IP" >&2; exit 5; }
  applied+=(jira_credentials)
fi

bind="$(dut "snap get bugit mcp.bind-address 2>/dev/null" || true)"
if [ -z "$bind" ] || [ "$bind" = "127.0.0.1" ]; then
  dut_sudo "snap set bugit mcp.bind-address=0.0.0.0"
  bind=0.0.0.0
fi

# The configure hook generates a key when none is set; trigger it if missing.
key="$(dut "snap get bugit mcp.api-key 2>/dev/null" || true)"
if [ -z "$key" ]; then
  key="$(dut_sudo "cat /var/snap/bugit/current/mcp-api-key" 2>/dev/null || true)"
fi
[ -n "$key" ] || { echo "could not read mcp api key on $IP" >&2; exit 3; }

# Port from the daemon's socket (default 8000) rather than assuming it
port="$(dut "grep -rhoE 'listen-stream: *[0-9.:]*[0-9]+' /snap/bugit/current/meta/snap.yaml 2>/dev/null | grep -oE '[0-9]+\$' | head -1" || true)"
port="${port:-8000}"

dut_sudo "snap start --enable bugit.mcp" >/dev/null 2>&1 || true

probe() { # path -> http status (SSE streams, so cap time and ignore curl timeout code)
  curl -s -o /dev/null -m 5 -w '%{http_code}' -H "Authorization: Bearer $key" \
    -H 'Accept: text/event-stream, application/json' "http://$IP:$port$1" || true
}

transport="" path=""
for cand in "sse:/sse" "http:/mcp"; do
  t="${cand%%:*}" p="${cand#*:}"
  code="$(probe "$p")"
  if [ "$code" = "200" ] || [ "$code" = "400" ] || [ "$code" = "405" ] || [ "$code" = "406" ]; then
    transport="$t" path="$p"; break
  fi
done
[ -n "$transport" ] || { echo "no MCP endpoint responded on $IP:$port (/sse, /mcp)" >&2; exit 4; }

url="http://$IP:$port$path"
mkdir -p "$(dirname "$CONFIG")"
[ -f "$CONFIG" ] || echo '{"mcpServers":{}}' > "$CONFIG"
tmp="$(mktemp)"
jq --arg n "$NAME" --arg t "$transport" --arg u "$url" --arg k "Bearer $key" \
  '.mcpServers[$n] = {type:$t, url:$u, headers:{Authorization:$k}, tools:["*"]}' "$CONFIG" > "$tmp"
chmod 600 "$tmp" && mv "$tmp" "$CONFIG"

jq -n --arg n "$NAME" --arg t "$transport" --arg u "$url" --arg c "$CONFIG" \
  --argjson a "$(printf '%s\n' "${applied[@]:-}" | jq -R . | jq -sc 'map(select(. != ""))')" \
  '{server:$n, transport:$t, url:$u, config:$c, key:"<redacted>", configured:$a}'
