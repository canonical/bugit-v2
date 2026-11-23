# Attach logs (Question C)

1. Run `M attach <ip> <draft_id>`.
2. Paste its COMPLETE output into your own message: every group and every file name.
   Never shorten it, filter it or show only a "suggested" list.
3. Run `<skill-dir>/scripts/ask_form.py attach --group "<group>: <file>, <file>"` for every
   group and pass its JSON as `requestedSchema`. Preselect nothing. A chosen group means all
   of its files.
   **STOP.**
4. Run `M attach <ip> <draft_id> --select "<a>,<b>"`, or `--none` if nothing was chosen.
5. Paste the full draft box as in `triage.md` T7.
6. Run `M submit <ip> <draft_id> --approved`. Show the id or URL.

A Job Output (stdout, stderr or comments) of 3000 characters or more is too big for the
description. `submit` uploads it as its own `job_<name>.txt` attachment, and the draft box
shows a placeholder for it. Nothing to choose here; tell the user which files were attached.
