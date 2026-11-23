# Triage

Ask the user four times: A, A2, B, C. Remember `tracker`, `draft_id`.

## Question A
T1. Run `M failures <ip>`. `choices` lists each failed test as
`submission | test case name | suggested severity` (`n/a` if none).
T2. Run `<skill-dir>/scripts/configure.sh --status <ip>` and read `missing`.
Run `<skill-dir>/scripts/ask_form.py failures --line "<each choice>" --missing <names>` and
pass its JSON as `requestedSchema`. Nothing is preselected. **STOP.**

T3. If `missing` values were given, run `setup.md` step 8 with them.

## Question A2
Run `<skill-dir>/scripts/ask_form.py severity --suggest <highest suggested_severity>` and pass
its JSON as `requestedSchema`. **STOP.**

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
Run `<skill-dir>/scripts/ask_form.py approve` and pass its JSON as `requestedSchema`. **STOP.**

| Answer | Do this |
| --- | --- |
| approve | Go to Question C. |
| edit | Run `M edit <ip> <draft_id> '<JSON of only the changed fields>'`. Go to T7. |
| reject | Stop. File nothing. |

## Question C
Read `attach.md`.

Edit fields: `title`, `summary`, `steps_to_reproduce`, `expected_result`, `actual_result`,
`failure_rate`, `severity`.

