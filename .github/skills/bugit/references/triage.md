# Triage

Ask the user only twice: A and B. Remember `tracker`, the chosen labels and `draft_id`.

## Question A
T1. Run `M failures <ip>`. It groups failed-test labels by submission.
T2. Run `<skill-dir>/scripts/configure.sh --status <ip>`. Read `missing`.
Ask with `ask_user`: a multi-select of the labels, as printed, plus one text field per `missing` name.
**STOP.**

T3. If the user gave `missing` values, run `setup.md` step 8 with them.

## Pipeline
Nothing asked here. All chosen tests go into ONE bug; `M triage` warns if they span
submissions.

T4. Run this with `initial_wait` 600. Quote every chosen label.
```sh
M triage <ip> --tracker <tracker> "<label 1>" "<label 2>"
```
It collects logs, finds each error, makes the draft, and prints any cross-submission warning,
`errors`/`logs` (raw stdout/stderr), then the draft box.
T5. Read the exit code; if not 0, read `triage-errors.md`.
T6. **Mandatory.** From `errors`/`logs`, state each failure's root cause (not the exception
text), then run
`M edit <ip> <draft_id> '{"title": "...", "summary": "...", "actual_result": "..."}'`.
Skip only if `errors`/`logs` are empty for every test.

T7. Paste the box from the last output verbatim into YOUR OWN message, unchanged, in a `text`
block — never just say "see above". List the warnings, then write exactly:
`Draft above. Choose approve, edit or reject.`

## Question B
Ask with `ask_user`: choice `approve`, `edit`, `reject`; text field "What should change?".
**STOP.**

| Answer | Do this |
| --- | --- |
| approve | Run `M submit <ip> <draft_id> --approved`. Show the id or URL. |
| edit | Run `M edit <ip> <draft_id> '<JSON of only the changed fields>'`. Go to T7. |
| reject | Stop. File nothing. |

Fields for edit: `title`, `summary`, `steps_to_reproduce`, `expected_result`, `actual_result`,
`failure_rate`, `severity` (highest, high, medium, low, lowest), `attach_logs` (list of file names).
