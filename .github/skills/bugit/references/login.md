# Login

1. Run `M auth <ip> <tracker>`.
2. Read the exit code.

   | Exit | Do this |
   | --- | --- |
   | 0 | Done. Go on. |
   | 4, tracker jira | Tell the user to run `<skill-dir>/scripts/credentials.sh --tracker jira`. **STOP.** Then run `configure.sh <ip>`. Go to step 1. |
   | 4, tracker launchpad | Show `login_url`. Tell the user to open it and approve. **STOP.** Then go to step 1. |
   | other | Show the error. **STOP.** |

`local` needs no login.

Next: `triage.md`.
