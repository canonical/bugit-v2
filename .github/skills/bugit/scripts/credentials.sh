#!/usr/bin/env bash
# Store the SSH password and (optionally) the Jira email/token in a private file, so they
# are never typed into or echoed by the chat. Run it yourself in a terminal:
#   ~/.copilot/skills/bugit/scripts/credentials.sh [--clear]
# Input is hidden. The file is created with mode 600 and read by configure.sh/cleanup.sh.
set -euo pipefail
umask 077
FILE="${BUGIT_CRED_FILE:-$HOME/.config/bugit/credentials}"

if [ "${1:-}" = "--clear" ]; then
  rm -f "$FILE"
  echo "removed $FILE"
  exit 0
fi

[ -t 0 ] || { echo "run this in an interactive terminal (input is hidden)" >&2; exit 1; }
mkdir -p "$(dirname "$FILE")"
read -r -s -p "SSH password of the DUT: " ssh_pass; echo
read -r -p "Jira email (empty to skip): " jira_email
jira_token=""
if [ -n "$jira_email" ]; then
  read -r -s -p "Jira API token: " jira_token; echo
fi
case "$ssh_pass$jira_email$jira_token" in *$'\n'*) echo "newlines are not allowed" >&2; exit 1 ;; esac
{
  printf 'SSHPASS=%s\n' "$ssh_pass"
  [ -z "$jira_email" ] || printf 'BUGIT_JIRA_EMAIL=%s\nBUGIT_JIRA_TOKEN=%s\n' "$jira_email" "$jira_token"
} >"$FILE"
chmod 600 "$FILE"
echo "saved to $FILE (mode 600). Delete it with: $0 --clear"
