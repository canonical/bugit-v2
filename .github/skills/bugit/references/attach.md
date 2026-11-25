# Attach logs (Question C)

1. Run `M attach <ip> <draft_id>`. It prints one line per log group, then a JSON form.
2. Paste every group line into your own message. Do not list single files.
3. Pass the printed JSON form, unchanged, as `requestedSchema` to `ask_user`. It comes from
   `ask_form.py attach`. Preselect nothing. A chosen group means all of its files.
4. Run `M attach <ip> <draft_id> --groups "<group>,<group>"` with the chosen group names,
   or `--none` if nothing was chosen. Never pass group names to `--select`.
5. Paste the full draft box as in `triage.md` T7.
   Chosen files are always uploaded as one `<group>.tar.xz` per group.
6. Run `M submit <ip> <draft_id> --approved`. Show the id or URL.

A Job Output (stdout, stderr or comments) of 3000 characters or more is too big for the
description. `submit` uploads it as its own `job_<name>.txt` attachment, and the draft box
shows a placeholder for it. Nothing to choose here; tell the user which files were attached.
