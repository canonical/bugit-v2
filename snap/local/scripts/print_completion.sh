#!/bin/sh
# Prints the shell completion script for the bugit apps.
# bash completion is automatically provided by snapd, but sourcing the script
# directly avoids the 'snap run' overhead on every <TAB>.

dir="$SNAP/src/bugit_v2/completions"

case "$1" in
bash) exec cat "$dir/bugit.bash" ;;
zsh) exec cat "$dir/_bugit" ;;
fish) exec cat "$dir/bugit.fish" ;;
*)
    cat >&2 <<USAGE
Usage: bugit.completion <bash|zsh|fish>

  bash: source <(bugit.completion bash)    # optional, snapd already does this
  zsh:  bugit.completion zsh > ~/.zfunc/_bugit  # with ~/.zfunc in \$fpath
        or: source <(bugit.completion zsh)      # after compinit in ~/.zshrc
  fish: bugit.completion fish > ~/.config/fish/conf.d/bugit.fish
USAGE
    exit 1
    ;;
esac
