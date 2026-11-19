# Shared helpers, sourced by configure.sh and cleanup.sh.
# Secrets never travel through the chat: the user stores them with credentials.sh in a
# 0600 file, and the scripts read them from there (values already in the environment win).
BUGIT_CRED_FILE="${BUGIT_CRED_FILE:-$HOME/.config/bugit/credentials}"

load_credentials() {
  [ -f "$BUGIT_CRED_FILE" ] || return 0
  if [ "$(stat -c %a "$BUGIT_CRED_FILE")" != 600 ]; then
    echo "refusing to read $BUGIT_CRED_FILE: mode must be 600 (chmod 600 it)" >&2
    return 1
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
