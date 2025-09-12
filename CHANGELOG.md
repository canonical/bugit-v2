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
