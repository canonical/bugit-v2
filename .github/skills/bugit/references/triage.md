# Triage

Ask the user four times: A, A2, B, C. Remember `tracker`, `draft_id`.

## Question A
T1. Run `M failures <ip>`. `choices` lists each failed test as
`submission | test case name | suggested severity` (`n/a` if none).
T2. Run `<skill-dir>/scripts/configure.sh --status <ip>` and read `missing`.
Ask with `ask_user`: a multi-select of ALL `choices` lines verbatim, none preselected,
and a text field per `missing` name. **STOP.**

T3. If `missing` values were given, run `setup.md` step 8 with them.

## Question A2
Ask with `ask_user`: a `severity` choice (highest = Critical, high, medium, low, lowest),
preselecting the highest `suggested_severity` of the chosen tests. **STOP.**

## Pipeline
All chosen tests go into ONE bug.

T4. Run this with `initial_wait` 600, quoting every chosen line.
```sh
M triage <ip> --tracker <tracker> --severity <severity> "<label 1>" "<label 2>"
```
It collects logs, makes the draft, prints warnings, `errors`/`logs`, then the draft box.
T5. If the exit code is not 0, read `triage-errors.md`.
T6. **Mandatory.** State each failure's root cause, not the exception text, then run
`M edit <ip> <draft_id> '{"title": "...", "summary": "...", "actual_result": "..."}'`.
Skip only if `errors`/`logs` are empty.

T7. ALWAYS show the FULL draft: paste the whole box verbatim into YOUR OWN message,
every section, in a `text` block. List the warnings, then write exactly:
`Draft above. Choose approve, edit or reject.`

## Question B
Ask with `ask_user`: choice `approve`, `edit`, `reject`; text "What should change?". **STOP.**

| Answer | Do this |
| --- | --- |
| approve | Go to Question C. |
| edit | Run `M edit <ip> <draft_id> '<JSON of only the changed fields>'`. Go to T7. |
| reject | Stop. File nothing. |

## Question C
Read `attach.md`.

Edit fields: `title`, `summary`, `steps_to_reproduce`, `expected_result`, `actual_result`,
`failure_rate`, `severity`.

