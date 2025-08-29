# Bugit V2

This is a new UI for [bugit](https://launchpad.net/bugit) implemented with the [textual](https://textual.textualize.io/) library.

[Features](#features) | [Installation](#installation) | [Development](#development) | [Limitations](#limitations)

> [!CAUTION]
> This project is VERY EXPERIMENTAL as of Aug 29,2025. Jira related logic has been tested but not the LP paths.  
<img width="2000" height="2144" alt="image" src="https://github.com/user-attachments/assets/7901ef4a-7398-49dd-992d-dfefb448fe6a" />

## Features

- Complete mouse support provided by textual. Now you can click, drag and scroll like a real app!
- "Real" text editor. The bug description box is now a full-fledged editor that allows familiar keyboard shortcuts like Ctrl+C Ctrl+V Ctrl+Z Ctrl+Shift+Z, etc. To see all the bindings, click the `^p palette` button or use Control+P to bring up the command palette and click Help
- Fancy colors! Textual comes with a lot of themes and provides us with a nice framework to theme it ourselves.
- Works through SSH. If you are dealing with ubuntu server/core, textual will still give you a pretty UI as long as the terminal you are viewing it from is a graphical terminal like gnome-terminal

To see more about textual itself, [check out their docs](https://textual.textualize.io/)

## Installation

### pipx

(works on 22.04+, requires python3.10)

Install pipx first:

```
sudo apt install pipx
```

Make sure you have access rights to this repository, then do:

```sh
pipx install git+https://github.com/canonical/bugit-v2.git
```

This should give you a new command called `bugit-v2`. If pipx is installed for the first time, it will prompt you about the app not being in `$PATH`. To fix this permanently, add `$HOME/.local/bin` to your $PATH.

Typically we need sudo for the log collectors. To run with sudo:

```
sudo -E env PATH="$PATH" APPORT_LAUNCHPAD_INSTANCE=production JIRA_SERVER=<jira_server_url> PROD=1 BUGIT_APP_NAME=bugit-v2 bugit-v2 jira
```

where jira_server_url is the base URL of your jira server, it should start with `https` and end with `atlassian.net`.


To uninstall, `pipx uninstall bugit-v2`

### snap

For now you have to manually build the snap. Clone the repo and `snapcraft clean && snapcraft pack`. Once snapcraft produces a `.snap` file, use `sudo snap install ./bugit-v2_0.1_amd64.snap --dangerous --classic` (replace the filename with the real one) to install it. Then finally run the app with `sudo bugit jira`

To uninstall `sudo snap remove bugit-v2`

## SSH Colors

If you ssh into a ubuntu machine and run bugit-v2, it might give you completely
different colors than running locally. This can be fixed by specifying
`COLORTERM=truecolor` in the ssh session. You shouldn't have to do this when using
the snap version.

## Development

### Dependencies

Dependencies are managed by uv. You can install uv by `pipx install uv` or use the official installer from uv's website.

### Get started

```bash
git clone git@github.com:canonical/bugit-v2.git
cd bugit-v2
uv sync
source .venv/bin/activate
python3 src/bugit_v2/app.py
```

Optionally install pre commit hooks:

```bash
# inside the virtual env and project root
pre-commit install
```

This will run some basic formatting checks before allowing a commit. If you don't want this git behavior, `pre-commit run --all-files` will just run the checks.

If you are using vscode's git panel, it might show something like this when the hooks didn't pass:

![image](https://github.com/user-attachments/assets/1633d7e4-f8d4-4ffa-9386-9622b17ba8af)

This basically says the automatic style fixes were not included. Do another `git add .` and you should be able to commit. If it still doesn't work, then some of the checks actually failed. Do `pre-commit run --all-files` manually and check the output.

### Type Checks

All the tools should pass `mypy`'s checks. Run `mypy .` at the project root with the virtual environment enabled. In vscode, there's the [mypy extension](https://marketplace.visualstudio.com/items?itemName=matangover.mypy) that lets you catch these errors in the editor.

### Debugging

Since the app runs inside the terminal it covers up all the normal stdout and stderr outputs. Textual provides the `textual console` command to allows us to inspect what's going on in the app. To use this:

```sh
uv sync
source .venv/bin/activate
textual console
```

Then in another terminal run the app:

```sh
uv sync
source .venv/bin/activate
textual run --dev src/bugit_v2/app.py
```

And the debug console should start printing events and `print()` messages:

```
(bugit-v2-py3.13) ❯ textual console -x EVENT

▌Textual Development Console v5.0.1
▌Run a Textual app with textual run --dev my_app.py to connect.
▌Press Ctrl+C to quit.
────────────────────────── Client '127.0.0.1' connected ──────────────────────────
[14:01:13] SYSTEM                                                      app.py:3148
Connected to devtools ( ws://127.0.0.1:8081 )
[14:01:13] SYSTEM                                                      app.py:3172
---
[14:01:13] SYSTEM                                                      app.py:3173
loop=<_UnixSelectorEventLoop running=True closed=False debug=False>
[14:01:13] SYSTEM                                                      app.py:3174
features=frozenset({'devtools', 'debug'})
[14:01:13] SYSTEM                                                      app.py:3206
STARTED FileMonitor(set())
[14:01:13] INFO                                                        app.py:3275
driver=<LinuxDriver BugitApp(title='BugitApp', classes={'-dark-mode'},
pseudo_classes={'dark', 'focus'})>
[14:01:13] SYSTEM                                                      app.py:3319
ready in 91 milliseconds
```


## Limitations

This app only looks pretty when it's used through a graphical terminal such as:
- gnome-terminal
- kitty
- ptyxis
- alacritty
- ghostty
- ...

If it's run in a virtual terminal (like the one you get from ctrl+alt+f4), it will still work but none of the styles/special symbols would appear. In that case use the original bugit or run the app through SSH.
