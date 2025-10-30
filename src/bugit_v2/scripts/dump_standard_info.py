"""
Generates the "Additional Information" section from the bug report
"""

import json
from typing import Annotated

import typer
from rich import print as rich_print

from bugit_v2.dut_utils.info_getters import get_standard_info
from bugit_v2.utils import is_prod, is_snap
from bugit_v2.utils.validations import before_entry_check

app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=not is_prod(),
    pretty_exceptions_show_locals=not is_prod(),
    no_args_is_help=True,
    add_completion=not is_snap(),  # the built-in ones doesn't work in snap
)


@app.command(
    help="(sudo required) Print the info in a human-friendly format. Pipe the output to 'cat' to remove colors.",
)
def main(
    print_json: Annotated[
        bool, typer.Option("--json", help="Print in JSON format")
    ] = False,
):
    before_entry_check()
    info = get_standard_info()
    if print_json:
        print(json.dumps(info))
    else:
        for k, v in info.items():
            rich_print(f"[yellow]{k}[/]: [bold white]{v}")


if __name__ == "__main__":
    app(prog_name="dump_standard_info")
