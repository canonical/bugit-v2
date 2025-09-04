"""
Generates the "Additional Information" section from the bug report
"""

import json

from rich import print as rich_print
from typer import Typer

from bugit_v2.dut_utils.info_getters import get_standard_info
from bugit_v2.utils import is_prod
from bugit_v2.utils.validations import before_entry_check

app = Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=not is_prod(),
    pretty_exceptions_show_locals=not is_prod(),
)


@app.command("json")
def print_json():
    info = get_standard_info()
    print(json.dumps(info))


@app.command("pretty")
def print_text():
    info = get_standard_info()
    for k, v in info.items():
        rich_print(f"[yellow]{k}[/]: [bold white]{v}")


if __name__ == "__main__":
    before_entry_check()
    app()
