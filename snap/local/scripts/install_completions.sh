#!/bin/sh
# Usage: install_completions.sh <install|remove>
#
# snapd only provides bash completion (through the `completer` key), so this
# puts small zsh/fish loaders in the host's default completion directories.
# The loaders source the scripts from /snap/<name>/current so they are always
# in sync with the installed revision.
#
# This must run in the host's mount namespace, see the snap hooks:
#   nsenter -t 1 -m -- /bin/sh "$SNAP/install_completions.sh" install

set -e

MARKER="# Installed by the $SNAP_INSTANCE_NAME snap, removed with the snap"
SRC_DIR="/snap/$SNAP_INSTANCE_NAME/current/src/bugit_v2/completions"
ZSH_DIR=/usr/local/share/zsh/site-functions
FISH_DIR=/usr/local/share/fish/vendor_completions.d

remove() {
    for f in "$ZSH_DIR/_bugit" "$FISH_DIR"/bugit*.fish; do
        if [ -f "$f" ] && grep -qF "$MARKER" "$f"; then
            rm -f "$f"
        fi
    done
    # only cleans up the fish dirs if they are empty
    rmdir "$FISH_DIR" /usr/local/share/fish 2>/dev/null || true
}

install() {
    # the first line of the zsh script is "#compdef <all command names>"
    compdef_line=$(head -n 1 "$SNAP/src/bugit_v2/completions/_bugit")
    names=${compdef_line#\#compdef }

    mkdir -p "$ZSH_DIR" "$FISH_DIR"

    # zsh autoloads this file as the body of _bugit. Sourcing the real script
    # redefines _bugit, then the new definition handles this first completion
    cat >"$ZSH_DIR/_bugit" <<EOF
$compdef_line
$MARKER
[[ -f $SRC_DIR/_bugit ]] || return 1
source $SRC_DIR/_bugit
_bugit "\$@"
EOF

    # fish autoloads completions by command name, so one loader per command
    for name in $names; do
        cat >"$FISH_DIR/$name.fish" <<EOF
$MARKER
if not functions -q __bugit_path; and test -f $SRC_DIR/bugit.fish
    source $SRC_DIR/bugit.fish
end
EOF
    done
    chmod 644 "$ZSH_DIR/_bugit" "$FISH_DIR"/bugit*.fish
}

case "$1" in
install)
    # drop loaders of commands that no longer exist
    remove
    install
    ;;
remove) remove ;;
*)
    echo "Usage: $0 <install|remove>" >&2
    exit 1
    ;;
esac
