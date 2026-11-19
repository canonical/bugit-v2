---
name: bugit
description: Configure the bugit-v2 MCP server on a device under test (DUT) and prepare a bug draft for a failed Checkbox test. Use for "/bugit <ip>", "/bugit cleanup" (remove all bugit MCP entries) or when asked to triage or file a bug from a DUT's Checkbox results.
argument-hint: <ip address> | cleanup [ip]
---

# /bugit <ip>

Configure the bugit MCP server on the DUT at `<ip>`, then run the bug triage workflow.
Nothing about the server (transport, port, key) is assumed; it is discovered at runtime because it changes over time.

## 1. Inputs (install time: the ONLY place settings are collected)
- `<ip>` comes from the argument; ask if missing. SSH user defaults to `ubuntu`.
- **Credentials never go through the chat.** Do not ask for the SSH password or Jira token with `ask_user`, do not put them in a command line, and always refer to them as `********`. Instead, if `~/.config/bugit/credentials` does not exist, tell the user to run `<skill-dir>/scripts/credentials.sh` in their own terminal (hidden prompt, file mode 600; `--clear` deletes it) and wait for them to confirm. `configure.sh` and `cleanup.sh` read that file themselves (or `BUGIT_CRED_FILE`). Never `cat` it or print its values.
- Read what the DUT already has: `<skill-dir>/scripts/configure.sh --status <ip> [user]` (prints `bugit.dut-info show --json` and whether Jira credentials are cached; the token is never shown).
- In ONE `ask_user` form, ask only for what is missing: project, assignee (Jira email and/or Launchpad ID: the bare ID such as `hanhsuan`, no `lp:` or `~`), CID (`123456-12345`), SKU, platform tags (comma separated; letters, digits and `-` only, no spaces or `_`; `dut_info.platform_tags`), and the tracker you will use (`local`, `jira`, `launchpad`). For `jira` the token is NOT asked here: unless `jira_credentials_cached` is true, make sure `credentials.sh` was run with the Jira email and token (get one at https://id.atlassian.com/manage-profile/security/api-tokens). Empty answers stay unset.

## 2. Configure
Run the script from this skill's own directory (`~/.copilot/skills/bugit` or `.github/skills/bugit`), passing the collected values as environment variables (only the ones you have):

```sh
BUGIT_PROJECT=<project> BUGIT_JIRA_ASSIGNEE=<email> BUGIT_LP_ASSIGNEE=<lpid> \
  BUGIT_CID=<cid> BUGIT_SKU=<sku> BUGIT_PLATFORM_TAGS=<tag1,tag2> \
  <skill-dir>/scripts/configure.sh <ip> [user]
```
(`SSHPASS`, `BUGIT_JIRA_EMAIL` and `BUGIT_JIRA_TOKEN` come from the credentials file.)

The script over SSH: checks the `bugit` snap, stores the given values in bugit itself with `bugit.dut-info set` (CID, SKU, project, assignees, platform tags), stores the Jira email/token from the credentials file in bugit's own Jira credentials cache (inside the snap's /tmp, mode 600, sent over stdin, never printed; it is lost on reboot, so run `/bugit <ip>` again), sets `mcp.bind-address` if unset or loopback, reads the API key, finds the socket port, probes `/sse` then `/mcp`, and upserts a `bugit-<ip>` entry in `~/.copilot/mcp-config.json` (mode 600). It prints JSON with the key redacted and the list of settings stored.

Exit codes: 2 snap missing (offer to install, do not install without asking), 3 key unreadable, 4 no endpoint responded, 5 a setting was rejected by `bugit.dut-info` or could not be stored (re-ask only that value).
Tell the user that binding to 0.0.0.0 exposes the server on the network, protected only by the API key. To change a setting later, run the same command again with just the new values.

## 3. Load the server and log in
Ask the user to run `/mcp reload` (or restart the CLI) if the `bugit-<ip>` tools are not visible. Confirm with a tool search for `list_failed_tests`. Do not continue until the tools are available.

Then make sure the chosen tracker is logged in, so triage never has to stop for it:
- `local`: nothing to do.
- `jira`: call `check_auth_status('jira')`. If invalid, ask the user to re-run `credentials.sh` with the corrected email/token, re-run `configure.sh` and check again.
- `launchpad`: call `check_auth_status('launchpad')`. If not authenticated, call `get_login_url('launchpad')`, show the URL, tell the user to open it and approve, then poll `check_auth_status('launchpad')` until it is authenticated.

## 4. Triage
Run the server's `bug_triage_playbook` prompt and follow it. If prompts are unsupported, use the tools in order:
`list_failed_tests`, `start_log_collection`, `check_job_status` (until completed), `grep_log`/`tail_log`, `search_bugs`, `draft_bug`, `get_draft`, (`update_draft`, `get_draft` as needed), `confirm_submission`.

The workflow has exactly TWO user interactions and nothing else in between:

**Interaction 1: select** (single `ask_user` form)
- `list_failed_tests` returns every failure of every recent submission in one call. Show each as `[<testplan_id> @ <failed_at>] <test_id>` (test plan name and timestamp in front), labelled with its `submission_id`. Offer them as a multi-select.
- Before the form, run `configure.sh --status <ip>` and use `dut_info` for project, assignee, CID, SKU and platform tags. Put any that are unset in the SAME form; never ask separately. An unauthenticated tracker is handled in section 3, not here.
- Pass the chosen test's `submission_id` to `start_log_collection`.

**Pipeline** (no questions): for every chosen failure collect logs, investigate, `check_auth_status` + `search_bugs`, then `draft_bug` (pass `logs_job_id`; the server defaults project, assignee, CID, SKU and platform tags from the DUT's `bugit.dut-info`, so pass them only to override) and `get_draft`.
- A `search_bugs` match is only a warning: always continue to `draft_bug` and `get_draft`, then list the matches after the preview.
- Fill every draft section (summary, steps, expected, actual, failure rate) yourself from the test output and logs.

**Interaction 2: approve** (single `ask_user` form)
- Present each draft as in section 6, then ask once with a choice approve / edit / reject (per draft when there are several) and a free-text field "What is wrong or should change?" so the user can describe any correction.
- Approve: call `confirm_submission` (irreversible) and show the returned URL/id.
- Edit (or any text in the free-text field): apply it with `update_draft(draft_id, <only the changed fields>)`, call `get_draft`, show it as in section 6 and ask the same form again. Repeat until approve or reject.
- Reject: stop.
- If the client also supports MCP elicitation, `confirm_submission` shows its own prompt; that is the same approval, do not ask a third time.

## 5. Cleanup (`/bugit cleanup [<ip>]`)
Removes the `bugit-<ip>` MCP entries (all of them, or only `<ip>`) from `~/.copilot/mcp-config.json`. It does not delete this skill or any DUT data.
1. Show which `bugit-*` entries will be removed (names only, never the key).
2. Ask ONCE with `ask_user` whether to also unbind the DUT(s) (unsets `mcp.bind-address`, stops/disables `bugit.mcp`) and whether to also purge the stored settings (`bugit.dut-info clear` and the cached Jira credentials). If yes, the SSH password is read from the credentials file (see section 1); never ask for it in chat.
3. Run `<skill-dir>/scripts/cleanup.sh [--unbind] [--purge-config] [--user <user>] [<ip> ...]`. It prints JSON listing removed entries. Offer `credentials.sh --clear` to delete the stored credentials file.
4. Tell the user to run `/mcp reload` so the tools disappear.

## 6. Presenting the draft
Show each `get_draft` result for humans, with its text unchanged, inside a fenced `text` block drawn as a box:
- Header `╔═ Draft <draft_id> ═...╗`, then the metadata lines (Tracker, Project, Severity, Stage, ...), then Title.
- Each section ([Summary], [Steps to Reproduce], [Expected Result], [Actual Result], [Failure Rate], [Affected Test Cases], [Additional Information], [Stage], [Job Output]) is separated by a `╟─...─╢` rule; every line starts with `║ `.
- Wrap lines at about 100 columns; close with `╚═...═╝`.
- With several drafts, show one box per draft, then a one-line list of the draft_ids and any `search_bugs` matches. The approval question (section 4, interaction 2) comes right after this.
