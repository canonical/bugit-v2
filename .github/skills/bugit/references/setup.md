# Setup: `/bugit <ip>`

1. If `<ip>` is missing, run `<skill-dir>/scripts/ask_form.py text ip "DUT ip"` and pass its
   JSON as `requestedSchema`.
2. Check that `~/.config/bugit/credentials` exists.
3. If it is missing, tell the user: `Run <skill-dir>/scripts/credentials.sh in your terminal, then tell me.` **STOP.**
4. Run `python3 --version`. If it fails or is older than 3.10, tell the user to install Python 3.10 or newer. **STOP.**
5. Run `<skill-dir>/scripts/configure.sh --status <ip>`. Read `missing` and `jira_credentials_cached`.
6. Run `<skill-dir>/scripts/ask_form.py setup --missing <names> --tracker local` and pass its
   JSON as `requestedSchema` (ONE form). Use the table.

   | Field | Format | Example |
   | --- | --- | --- |
   | project | letters, digits | somerville |
   | jira_assignee | email | a@b.com |
   | lp_assignee | Launchpad ID only | hanhsuan |
   | cid | 6 digits, `-`, 5 digits | 202001-27667 |
   | sku | letters, digits, `-`, `_` | sku1 |
   | platform_tags | comma list | tag-a,tag-b |
   | tracker | local, jira or launchpad | local |
7. If `tracker` is jira and `jira_credentials_cached` is false, tell the user to run `credentials.sh` with the Jira email and token. **STOP.**
8. Run this. Leave out any value you do not have.
   ```sh
   BUGIT_PROJECT=<project> BUGIT_JIRA_ASSIGNEE=<email> BUGIT_LP_ASSIGNEE=<lpid> \
     BUGIT_CID=<cid> BUGIT_SKU=<sku> BUGIT_PLATFORM_TAGS=<tags> \
     <skill-dir>/scripts/configure.sh <ip>
   ```
9. Read the exit code.

   | Exit | Do this |
   | --- | --- |
   | 0 | Tell the user: `Only the API key protects the server.` Go on. |
   | 1 | Tell the user to install `sshpass` or `jq`. **STOP.** |
   | 2 | Ask to install the bugit snap. **STOP.** |
   | 3 or 4 | Show the error. **STOP.** |
   | 5 | Show the message. Ask again only for that value. Go to step 8. |

Next: `login.md`.
