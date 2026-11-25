# Shared helpers, sourced by configure.sh and cleanup.sh.
# Secrets never travel through the chat: the user stores them with credentials.sh in a
# 0600 file, and the scripts read them from there (values already in the environment win).
BUGIT_CRED_FILE="${BUGIT_CRED_FILE:-$HOME/.config/bugit/credentials}"

load_credentials() {
  [ -f "$BUGIT_CRED_FILE" ] || return 0
  if [ "$(stat -c %a "$BUGIT_CRED_FILE")" != 600 ]; then
    echo "refusing to read $BUGIT_CRED_FILE: mode must be 600 (chmod 600 it)" >&2
    return 6
  fi
  local k v
  while IFS='=' read -r k v; do
    case "$k" in
      SSHPASS | BUGIT_JIRA_EMAIL | BUGIT_JIRA_TOKEN)
        [ -n "${!k:-}" ] || export "$k=$v"
        ;;
    esac
  done <"$BUGIT_CRED_FILE"
}

BUGIT_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Return 6 and print the exact command to run when a secret the tracker needs is missing.
check_credentials() {
  local tracker="${1:-local}" missing=()
  [ -n "${SSHPASS:-}" ] || missing+=("SSH password")
  if [ "$tracker" = jira ]; then
    [ -n "${BUGIT_JIRA_EMAIL:-}" ] || missing+=("Jira email")
    [ -n "${BUGIT_JIRA_TOKEN:-}" ] || missing+=("Jira API token")
  fi
  [ ${#missing[@]} -gt 0 ] || return 0
  printf 'missing credentials in %s: %s\n' "$BUGIT_CRED_FILE" "$(IFS=,; echo "${missing[*]}" | sed 's/,/, /g')" >&2
  echo "Run $BUGIT_SCRIPTS/credentials.sh --tracker $tracker in your terminal, then tell me." >&2
  return 6
}
