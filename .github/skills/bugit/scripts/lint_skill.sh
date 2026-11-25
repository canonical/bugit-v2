#!/usr/bin/env bash
# Check that the skill text is short and plain enough for small language models.
# Usage: lint_skill.sh [skill-dir]
set -u
dir="${1:-$(dirname "${BASH_SOURCE[0]}")/..}"
max_words=300 max_line=160 rc=0
for f in "$dir"/SKILL.md "$dir"/references/*.md; do
  words=$(wc -w <"$f")
  [ "$words" -le "$max_words" ] || { echo "$f: $words words (max $max_words)"; rc=1; }
  long=$(awk -v m="$max_line" 'length($0) > m {n++} END {print n+0}' "$f")
  [ "$long" -eq 0 ] || { echo "$f: $long lines longer than $max_line"; rc=1; }
  # Every ask_user form must come from ask_form.py so requestedSchema stays a JSON object.
  if grep -q 'ask_user' "$f"; then
    grep -q 'ask_form.py' "$f" || { echo "$f: ask_user without ask_form.py"; rc=1; }
  fi
  grep -qE "(ask_user|requestedSchema)[^A-Za-z]*['\"]\{" "$f" &&
    { echo "$f: schema written as a quoted string"; rc=1; }
  # ask_user already waits for the answer; a STOP after it makes models end the turn early.
  stop=$(awk '/requestedSchema/ {s=NR} /\*\*STOP/ && s && NR-s<=2 {print NR; exit}' "$f")
  [ -z "$stop" ] || { echo "$f:$stop: **STOP** right after an ask_user form"; rc=1; }
done
exit $rc
