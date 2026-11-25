#!/usr/bin/env bash
# Store the SSH password and, for the jira tracker, the Jira email/token in a private file,
# so they are never typed into or echoed by the chat. Run it yourself in a terminal:
#   ~/.copilot/skills/bugit/scripts/credentials.sh [--tracker local|jira|launchpad] [--check | --clear]
# Only --tracker jira asks for the Jira email and token; other trackers keep the saved ones.
# --check saves nothing: exit 0 if the tracker's secrets are saved, else exit 6 and print
# the command to run.
# Input is hidden. The file is created with mode 600 and read by configure.sh/cleanup.sh.
set -euo pipefail
umask 077
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
FILE="$BUGIT_CRED_FILE"

usage() { echo "usage: $0 [--tracker local|jira|launchpad] [--check | --clear]" >&2; exit 1; }
TRACKER=local action=save
while [ $# -gt 0 ]; do
  case "$1" in
    --tracker) TRACKER="${2:-}"; shift; [ $# -eq 0 ] || shift ;;
    --check) action=check; shift ;;
    --clear) action=clear; shift ;;
    *) usage ;;
  esac
done
case "$TRACKER" in local | jira | launchpad) ;; *) usage ;; esac

if [ "$action" = clear ]; then
  rm -f "$FILE"
  echo "removed $FILE"
  exit 0
fi
if [ "$action" = check ]; then
  load_credentials || exit 6
  check_credentials "$TRACKER" || exit 6
  echo "credentials for $TRACKER are saved in $FILE"
  exit 0
fi

[ -t 0 ] || { echo "run this in an interactive terminal (input is hidden)" >&2; exit 1; }
mkdir -p "$(dirname "$FILE")"
read -r -s -p "SSH password of the DUT: " ssh_pass; echo
# Other trackers keep the Jira email/token that are already saved.
jira_email="$(sed -n 's/^BUGIT_JIRA_EMAIL=//p' "$FILE" 2>/dev/null || true)"
jira_token="$(sed -n 's/^BUGIT_JIRA_TOKEN=//p' "$FILE" 2>/dev/null || true)"
if [ "$TRACKER" = jira ]; then
  read -r -p "Jira email: " jira_email
  read -r -s -p "Jira API token: " jira_token; echo
  [ -n "$jira_email" ] && [ -n "$jira_token" ] || { echo "Jira email and token are required" >&2; exit 1; }
fi
case "$ssh_pass$jira_email$jira_token" in *$'\n'*) echo "newlines are not allowed" >&2; exit 1 ;; esac
{
  printf 'SSHPASS=%s\n' "$ssh_pass"
  [ -z "$jira_email" ] || printf 'BUGIT_JIRA_EMAIL=%s\nBUGIT_JIRA_TOKEN=%s\n' "$jira_email" "$jira_token"
} >"$FILE"
chmod 600 "$FILE"
echo "saved to $FILE (mode 600). Delete it with: $0 --clear"
