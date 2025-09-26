# 0.2.5

## Added

- Autosave functionality: Now the editor saves your progress as you type. It utilizes the [debounce](https://medium.com/@jamischarles/what-is-debouncing-2505c0648ff1) pattern to ensure performance while editing and only saves the progress to disk when the user hasn't been typing for 1 second.
- Recovery from autosave: If autosave files exist on the system, bugit will show a recovery screen asking the user whether they want to use any of the previous saved files.
- Added a clear button for the log selection list


# 0.2.4

## Added

- CLI arguments to pre-fill values like CID, SKU, tags, etc. Bugit will hold on to these values and fill them in at the bug report screen. **Note that any human-entered value has higher precedence than CLI values.** So if a submission failed and bugit returned to the editor, the CLI values will be overridden by the values entered by the user.
- Log collector progress watchers. Now there's a progress message every 30 seconds for very slow log collectors
- Experimental reopen editor implemented, locked until 0.2.5 for now
- Upgraded to textual 6.1
- New header style
- Not specifying any subcommand will now show a help page instead of just an error
- Fixed the issue of Checkbox version not appearing in the header
- Fixed an issue where the nvidia log collector is not getting all the information

# 0.2.3

- Added the "No Session" and "No Job" options
- Removed oem-getlogs and sosreport collectors since running them in a snap environment produces inaccurate logs
- Added a journalctl collector that has been manually verified to work on 25.04 and older
- Fixed an issue where all the content in the description was lost after returning from the error prompt in the submission progress screen [#34](https://github.com/canonical/bugit-v2/issues/34)
- Moved to core24 and python 3.12
- Fixed bad path for the nvidia-bug-report.sh command

# 0.2.2

- Added bash completion to the snap
- Added a `bugit.dump-standard-info` command to quickly dump a bugit-style info block in either json or human readable format
- Fixed [#21](https://github.com/canonical/bugit-v2/issues/21)
