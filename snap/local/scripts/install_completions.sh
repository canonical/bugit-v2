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
# snap specific name so it never collides with a "_bugit" from elsewhere
ZSH_LOADER="$ZSH_DIR/_${SNAP_INSTANCE_NAME}_snap"

# write_loader <path>: writes stdin to <path>, unless <path> already exists
# and wasn't created by this snap (e.g. from a package or a manual install)
write_loader() {
    if [ -e "$1" ] && ! grep -qF "$MARKER" "$1"; then
        echo "Not overwriting $1, it was not installed by this snap" >&2
        cat >/dev/null
        return 0
    fi
    cat >"$1.tmp"
    chmod 644 "$1.tmp"
    mv -f "$1.tmp" "$1"
}

remove() {
    # _bugit and bugit-v2.fish are loader names used by older revisions
    for f in "$ZSH_LOADER" "$ZSH_DIR/_bugit" "$FISH_DIR/$SNAP_NAME".*.fish \
        "$FISH_DIR/bugit-v2.fish"; do
        if [ -f "$f" ] && grep -qF "$MARKER" "$f"; then
            rm -f "$f"
        fi
    done
    # only cleans up the fish dirs if they are empty
    rmdir "$FISH_DIR" /usr/local/share/fish 2>/dev/null || true
}

install() {
    # the first line of the zsh script is "#compdef <all command names>",
    # only take the snap's own commands (not e.g. the pipx "bugit-v2")
    compdef_line=$(head -n 1 "$SNAP/src/bugit_v2/completions/_bugit")
    names=""
    for name in ${compdef_line#\#compdef }; do
        case "$name" in
        "$SNAP_NAME".*) names="$names $name" ;;
        esac
    done

    mkdir -p "$ZSH_DIR" "$FISH_DIR"

    # zsh autoloads this file as the body of the loader function. Sourcing
    # the real script defines _bugit (and compdefs it for later completions),
    # then _bugit handles this first completion. Inside a sourced file
    # $funcstack[1] is the file path, so the real script won't dispatch again
    write_loader "$ZSH_LOADER" <<EOF
#compdef$names
$MARKER
[[ -f $SRC_DIR/_bugit ]] || return 1
source $SRC_DIR/_bugit
_bugit "\$@"
EOF

    # fish autoloads completions by command name, so one loader per command
    for name in $names; do
        write_loader "$FISH_DIR/$name.fish" <<EOF
$MARKER
if not functions -q __bugit_path; and test -f $SRC_DIR/bugit.fish
    source $SRC_DIR/bugit.fish
end
EOF
    done
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
