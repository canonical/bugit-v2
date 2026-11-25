---
name: bugit
description: Set up the bugit MCP server on a DUT and file a bug for a failed Checkbox test. Use for /bugit <ip>, /bugit cleanup, or filing a bug.
argument-hint: <ip address> | cleanup [ip]
---

# bugit

`<skill-dir>` is the folder of this file: `~/.copilot/skills/bugit` or `.github/skills/bugit`.
`M` means `<skill-dir>/scripts/bugit_mcp.py`. It talks to the DUT; you do not call MCP tools.

## Which file to read
| User says | Read, in order |
| --- | --- |
| `/bugit <ip>` | `references/setup.md`, `references/login.md`, `references/triage.md` |
| `/bugit cleanup` | `references/cleanup.md` |
| file a bug | `references/triage.md`, then `references/attach.md` |

## Rules
1. Do one step at a time. Never skip a step.
2. `ask_user` waits for the answer itself: use it and go on in the same turn.
   **STOP** means end your turn and wait; do it only where a step says **STOP**.
3. Never ask for or print a password or token.
4. Never guess a value. Ask with `ask_user`.
5. `ask_user` needs `requestedSchema` as a JSON object, never a quoted string. Run
   `<skill-dir>/scripts/ask_form.py <form>` and paste what it prints. Right: the parameter value
   starts with `{"properties":` . Wrong: it starts with `"` or `'`, or has `\"` inside. Do not
   wrap, escape, or reformat it. If `ask_user` reports
   `Expected object, received string`, run `ask_form.py --check '<your schema>'`.
6. Run `M submit` only after the user says approve and the logs are chosen.
7. If a command fails, show the error and stop.
8. If you tell the user to look at tool output, paste it into your own response; never say
   "see above".
