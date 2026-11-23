# Unreleased

## Added

- MCP playbook/`bugit` skill: the user selects the failed tests first, then the severity (preselected from the chosen tests)
- `bugit-v2 mcp`: headless MCP server (SSE, Bearer API key) that lets an AI agent triage Checkbox failures, collect and grep logs (PII-redacted) and file bugs after explicit human approval
- MCP drafts follow the bugit-v2 report format and fields; new read-only `get_draft` tool previews the final report
- `local` MCP tracker that files reports to a directory on the device without a login, plus a copy-paste quick start prompt in the README
- Socket-activated `bugit.mcp` snap daemon, configured with `snap set bugit mcp.bind-address=... mcp.api-key=...`

- MCP: `list_failed_tests` tool (failures with test plan and time); `draft_bug` accepts `cid`/`sku`; the playbook now asks the user only to select a failure and to approve the draft
- MCP: new `update_draft` tool to correct a draft after `get_draft`; the playbook now offers approve / edit / reject
- MCP: `draft_bug` defaults project, assignee, platform tags and tags from `bugit.dut-info`, and the daemon (root) now finds the dut-info saved by the DUT user
- `bugit` skill: credentials are stored with `credentials.sh` (hidden prompt, mode 600) instead of the chat; `BUGIT_PLATFORM_TAGS` in `configure.sh`

- MCP/`bugit` skill: `list_failed_tests` returns `cert_status` and `suggested_severity` (blocker: highest, non-blocker: high, as in the TUI) so the severity is chosen before drafting (`triage --severity`); `list_collected_logs` tags each file with its collector and `attach` prints the full grouped list; the full draft is always shown
- Log attachments are chosen right after the draft is approved: the playbook asks for them as a separate step, `confirm_submission` elicits the selection when supported, and the `bugit` skill gets `bugit_mcp.py attach` (submit is blocked until logs are chosen)
- MCP: `draft_bug` logs a warning when no saved dut-info is found (CID/SKU stay blank); the daemon reads the dut-info saved by the DUT user
- `bugit` skill: `configure.sh` rejects an invalid Launchpad assignee, CID, SKU, project, Jira assignee or platform tag up front with a clear message and an example

- MCP: new `triage_failures` tool finds the error of every selected test and drafts one bug in a single call (proposed title/summary/actual result/failure rate, overridable); the playbook prefers it over `grep_log` + `draft_bug`
- MCP: a draft lists every selected test under Affected Test Cases
- `bugit` skill: new `scripts/bugit_mcp.py` (plain `python3` 3.10+, no dependencies) drives the triage from the shell (`failures`, `triage`, `edit`, `submit --approved`, `auth`, `call`); `/mcp reload` is no longer needed
- MCP: removed the duplicate search (`search_bugs` tool and tracker search); triage no longer needs a tracker query
- MCP: `triage_failures` now also returns `logs` (redacted, truncated stdout/stderr per selected test); `triage_failures`/the playbook now require the calling agent to analyze `errors`/`logs` and write a real root-cause `title`/`summary`/`actual_result` before showing the draft, instead of restating the raw exception text
- `bugit` skill: `M triage` prints `errors` and `logs` for every selected test before the draft box; `triage.md` makes the root-cause rewrite step mandatory
- MCP: `list_failed_tests` now groups failures by submission (`submission_id`/`testplan_id`/`modified` with a nested `tests` list) instead of a flat list
- `bugit` skill: `M failures` prints tests grouped by submission; `M triage` warns (but still files one bug) when the chosen tests span more than one submission; `triage.md`/`SKILL.md` make it explicit that the draft must be pasted verbatim into the agent's own chat response, not just referenced

## Fixed

- `bugit` skill: every question now comes from `scripts/ask_form.py`, which prints the `ask_user` `requestedSchema` as a JSON object, so a hand-written schema no longer fails with `Expected object, received string`; `lint_skill.sh` flags a question that asks `ask_user` without it


# 0.2.8

## Added

- Experimental oem-getlogs log collector

## Fixed

- Problem with the certification status getter using too much memory
- Hanging checkbox subprocesses not getting cleaned up
- Tab completion
- Issue where the `long-job-stdout` collector always runs even if the job stdout is not long


# 0.2.7

## Added

- Subcommand `visual-config` to save user color theme preference
- Subcommand `dut-info` to set DUT info reusable by the main bugit program
- Ability to give up log collection if there's any running log collector
  - Use the `Give Up` button during submission to cancel all running collectors and immediately start uploading whatever was collected
- Ability to detect certification status of the current test case
- Asynchronous execution of all subprocesses


## Fixed

- Autosave not working when -s is specified
- Missing snap-debug.sh file in pipx version
- list-sessions crashing when there's an invalid session
- "Return to Editor" button not immediately returning to the editor
- Unable to open bugs when stdout/stderr of the job output is way too long.
  - Logs are now put into files if they are longer than 10k characters
- Issue where empty log files were rejected by jira

## Changed

- Autosave timer has been changed to 0.5s to save more frequently
- Upgraded textual to v7
- Word wrapping in submission progress screen's log window

# 0.2.6

## Added

- Ability to open bugs based on a checkbox submission tarball. Use the `-s` option to specify a submission tar and bugit will let you choose from the failed jobs in that file instead of reading /var/tmp/checkbox-ng/sessions
- Boosted snap version start time by 1.89x by pre-compiling pycache
- Standard info now shows checkbox type (deb/snap)
- Job selection now includes crashed jobs
- `--version` flag
- New logo during loading screens

## Fixed

- Relative timestamp showing weird minute values
- Missing checkbox session when recovering from an autosave file
- Autosave files were lost after a reboot
- NVIDIA log collector crashing the entire app when an NVIDIA card is present but the driver utils were not installed on the system


# 0.2.5

## Added

- Autosave functionality: Now the editor saves your progress as you type. It utilizes the [debounce](https://medium.com/@jamischarles/what-is-debouncing-2505c0648ff1) pattern to ensure performance while editing and only saves the progress to disk when the user hasn't been typing for 1 second.
- Recovery from autosave: If autosave files exist on the system, bugit will show a recovery screen asking the user whether they want to use any of the previous saved files.
- Added a clear button for the log selection list
- New commands bugit.list-sessions and bugit.dump-standard-info
  - Both supports plain json output with `--json`
- Fixed incorrect method of getting NVIDIA driver info
- Fixed the issue where 1 upload failure in the LP submitter will cause everything else to not be submitted

# 0.2.4

## Added

- CLI arguments to pre-fill values like CID, SKU, tags, etc. Bugit will hold on to these values and fill them in at the bug report screen. **Note that any human-entered value has higher precedence than CLI values.** So if a submission failed and bugit returned to the editor, the CLI values will be overridden by the values entered by the user.
- Log collector progress watchers. Now there's a progress message every 30 seconds for very slow log collectors
- Experimental reopen editor implemented, locked until 0.2.5 for now
- Upgraded to textual 6.1
- New header style
- Not specifying any subcommand will now show a help page instead of just an error

## Fixed
- Checkbox version not appearing in the header
- Nvidia log collector not getting all the information (lots of missing command or .so files)
- Missing bug status chooser in the editor when using th launchpad submitted

# 0.2.3

- Added the "No Session" and "No Job" options
- Removed oem-getlogs and sosreport collectors since running them in a snap environment produces inaccurate logs
- Added a journalctl collector that has been manually verified to work on 25.04 and older
- Fixed an issue where all the content in the description was lost after returning from the error prompt in the submission progress screen [#34](https://github.com/canonical/bugit-v2/issues/34)
- Moved to core24 and python 3.12
- Fixed bad path for the nvidia-bug-report.sh command

# 0.2.2

- Added bash completion to the snap
- Added a `bugit.dump-standard-info` command to quickly dump a bugit-style info block in either json or human readable format
- Fixed [#21](https://github.com/canonical/bugit-v2/issues/21)
