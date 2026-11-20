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
done
exit $rc
