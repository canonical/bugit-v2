import json
import sys
from pathlib import Path

from rich import print as rich_print
from typer import Typer

from bugit_v2.checkbox_utils import Session, get_valid_sessions
from bugit_v2.utils import is_prod, is_snap

SESSION_ROOT_DIR = Path("/var/tmp/checkbox-ng/sessions")


app = Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=not is_prod(),
    pretty_exceptions_show_locals=not is_prod(),
    no_args_is_help=True,
    add_completion=not is_snap(),  # the built-in ones doesn't work in snap
)


@app.command("json", help="Print the info in JSON format")
def print_json():
    valid_sessions = get_valid_sessions()

    if len(valid_sessions) == 0:
        rich_print(
            "[red]No sessions were found on this device", file=sys.stderr
        )
        exit()

    d: list[dict[str, str]] = []
    for session_path in valid_sessions:
        # rich_print(f"[yellow]Session directory[/]: [bold white]{session_path}")
        session = Session(session_path)
        # rich_print(f"[yellow]Test Plan[/]: [bold white]{session.testplan_id}")
        # print(z
        d.append(
            {
                "session_path": str(session_path),
                "test_plan": session.testplan_id,
            }
        )

    print(json.dumps(d))


@app.command(
    "pretty",
    help="Print the info in a human-friendly format. Pipe the output to 'cat' to remove colors.",
)
def print_text():
    valid_sessions = get_valid_sessions()

    if len(valid_sessions) == 0:
        rich_print("[red]No sessions were found on this device")
        exit()

    for session_path in valid_sessions:
        rich_print(f"[yellow]Session directory[/]: [bold white]{session_path}")
        session = Session(session_path)
        rich_print(f"[yellow]Test Plan[/]: [bold white]{session.testplan_id}")
        print()


if __name__ == "__main__":
    app()
