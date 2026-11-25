# Setup: `/bugit <ip>`

1. If `<ip>` is missing, run `<skill-dir>/scripts/ask_form.py text ip "DUT ip"` and pass its
   JSON as `requestedSchema`.
2. Run `<skill-dir>/scripts/ask_form.py tracker` and pass its JSON as `requestedSchema`. Remember
   `tracker`.
3. Run `<skill-dir>/scripts/credentials.sh --check --tracker <tracker>`.
4. Exit 6: paste its output to the user. **STOP.** Then go to step 3.
5. Run `python3 --version`. If it fails or is below 3.10, tell the user to install Python 3.10+. **STOP.**
6. Run `<skill-dir>/scripts/configure.sh --status --tracker <tracker> <ip>`. Read `missing`.
7. Run `<skill-dir>/scripts/ask_form.py setup --missing <names> --tracker <tracker>` and pass its
   JSON as `requestedSchema` (ONE form). Space-separate `<names>`, e.g. `--missing cid sku project`
   (not commas).

   | Field | Format | Example |
   | --- | --- | --- |
   | project | letters, digits | somerville |
   | jira_assignee | email | a@b.com |
   | lp_assignee | Launchpad ID only | hanhsuan |
   | cid | 6 digits, `-`, 5 digits | 202001-27667 |
   | sku | letters, digits, `-`, `_` | sku1 |
   | platform_tags | comma list | tag-a,tag-b |

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
   | 6 | Paste its output. **STOP.** Then go to step 3. |
   | 5 | Show the message. Ask again only for that value. Go to step 8. |

Next: `login.md`.
