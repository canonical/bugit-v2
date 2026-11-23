# Cleanup

Removes `bugit-<ip>` entries from `~/.copilot/mcp-config.json`. Nothing else is deleted.

1. List the `bugit-*` names that will be removed. Never show a key.
2. Run `<skill-dir>/scripts/ask_form.py cleanup` and pass its JSON as `requestedSchema`.
   - `unbind`: stop the server on the DUT.
   - `purge`: clear the saved settings and Jira login on the DUT.
3. **STOP.** Wait for the answer.
4. Run `<skill-dir>/scripts/cleanup.sh` with these flags:
   - `--unbind` if `unbind` is yes.
   - `--purge-config` if `purge` is yes.
   - then the `<ip>`, or nothing for all.
5. Show the JSON it prints.
6. Ask if the user wants to delete the saved credentials. If yes, run `<skill-dir>/scripts/credentials.sh --clear`.
7. Tell the user: `Run /mcp reload.`

Never ask for the SSH password. The scripts read it from `~/.config/bugit/credentials`.
