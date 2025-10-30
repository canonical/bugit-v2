import json
from pathlib import Path

import typer
from rich import print as rich_print
from typing_extensions import Annotated

from bugit_v2.checkbox_utils import Session, get_valid_sessions
from bugit_v2.utils import is_prod, is_snap

SESSION_ROOT_DIR = Path("/var/tmp/checkbox-ng/sessions")


app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=not is_prod(),
    pretty_exceptions_show_locals=not is_prod(),
    no_args_is_help=True,
    add_completion=not is_snap(),  # the built-in ones doesn't work in snap
)


@app.command(
    help="Print the info in a human-friendly format. Pipe the output to 'cat' to remove colors.",
)
def main(
    print_json: Annotated[
        bool, typer.Option("--json", help="Print in JSON format")
    ] = False,
):
    valid_sessions = get_valid_sessions()

    if len(valid_sessions) == 0:
        rich_print("[red]No sessions were found on this device")
        exit()

    if print_json:
        d: list[dict[str, str]] = []
        for session_path in valid_sessions:
            session = Session(session_path)
            d.append(
                {
                    "session_path": str(session_path),
                    "test_plan": session.testplan_id,
                }
            )
        print(json.dumps(d))
    else:
        for idx, session_path in enumerate(valid_sessions):
            rich_print(
                f"[yellow]Session directory[/]: [bold white]{session_path}"
            )
            session = Session(session_path)
            rich_print(
                f"[yellow]Test Plan[/]: [bold white]{session.testplan_id}"
            )
            if idx != len(valid_sessions) - 1:
                # print a separator if not the last one
                print()


if __name__ == "__main__":
    app()
