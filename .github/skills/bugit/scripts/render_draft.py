#!/usr/bin/env python3
"""Draw the plain-text result of get_draft as a box.

Usage: render_draft.py <draft_id> < draft.txt
A rule is put before each "[Section]" line; text is wrapped at 100 columns.
"""
import re
import sys
import textwrap

WIDTH = 100
SECTION = re.compile(r"^\[[^\]]+\]")


def render(draft_id: str, text: str) -> str:
    head = f"╔═ Draft {draft_id} "
    out = [head + "═" * max(WIDTH + 2 - len(head), 1) + "╗"]
    for line in text.rstrip().splitlines():
        if SECTION.match(line):
            out.append("╟" + "─" * (WIDTH + 2) + "╢")
        for part in textwrap.wrap(line, WIDTH, replace_whitespace=False) or [""]:
            out.append("║ " + part)
    out.append("╚" + "═" * (WIDTH + 2) + "╝")
    return "\n".join(out)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(render(sys.argv[1], sys.stdin.read()))
