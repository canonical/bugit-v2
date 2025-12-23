"""
Generates the "Additional Information" section from the bug report
"""

import json
from typing import Annotated

import typer
from rich import print as rich_print

from bugit_v2.dut_utils.info_getters import get_standard_info
from bugit_v2.models.dut_info import get_saved_dut_info
from bugit_v2.utils import is_prod, is_snap
from bugit_v2.utils.validations import sudo_devmode_check

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
    no_timeout: Annotated[
        bool, typer.Option("-nt", "--no-timeout", help="Ignore all timeouts")
    ] = False,
):
    sudo_devmode_check()
    if no_timeout:
        info = get_standard_info(None)
    else:
        info = get_standard_info()
    saved_dut_info = get_saved_dut_info()

    if saved_dut_info:
        if saved_dut_info.cid:
            info["CID"] = saved_dut_info.cid
        if saved_dut_info.sku:
            info["SKU"] = saved_dut_info.sku

    if print_json:
        out = {}
        for key in info:
            out[
                "_".join(
                    word.strip().lower()
                    for word in key.split()
                    if word.strip()
                )
            ] = info[key]

        print(json.dumps(out))
    else:
        for key, v in info.items():
            rich_print(f"[yellow]{key}[/]: [bold white]{v}")


if __name__ == "__main__":
    app(
        prog_name=(
            "bugit.dump-standard-info"
            if is_snap()
            else "bugit-dump-standard-info"
        )
    )
