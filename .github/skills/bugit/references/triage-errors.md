# Triage: if something goes wrong
| What happened | Do this |
| --- | --- |
| Exit 1 | Show the error. Fix the command (labels, JSON) and run it again. |
| Exit 2 | Show the error. If the DUT is unreachable, run `setup.md` again. **STOP.** |
| Exit 3 | Tell the user to update the bugit snap on the DUT. **STOP.** |
| Exit 4 | Follow `login.md`, then run T4 again. |
| `WARNING: No error output found` | Read the log: `M call <ip> tail_log '{"job_id": "..", "filename": ".."}'`. Fix `actual_result` with `M edit`. |
