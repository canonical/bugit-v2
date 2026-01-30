#!/usr/bin/env bash

_bugit_v2_completion() {
    local IFS=$'
'
    COMPREPLY=( $(env COMP_WORDS="${COMP_WORDS[*]}" COMP_CWORD=$COMP_CWORD _BUGIT.BUGIT_V2_COMPLETE=complete_bash python3 $SNAP/src/bugit_v2/app.py) )
    return 0
}

complete -o default -F _bugit_v2_completion bugit.bugit-v2
