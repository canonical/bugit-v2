import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
from typing_extensions import Annotated
import typer

from bugit_v2.bug_report_submitters.local_file_submitter import SERIALIZED_REPORT_NAME
from bugit_v2.models.bug_report import SerializableBugReport
from bugit_v2.utils import is_prod, is_snap


app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=not is_prod(),
    pretty_exceptions_show_locals=not is_prod(),
    no_args_is_help=True,
    help="Submit the archive made by `bugit-v2 local`",
    add_completion=not is_snap(),  # the built-in ones doesn't work in snap
)


@app.command("jira", help="Submit to Jira")
def jira_main(
    file: Annotated[
        Path | None,
        typer.Argument(
            help=(
                "The .tar.gz file created by [u]bugit-v2 local[/]. "
                + "A jira bug report will be created based on the content of this archive."
            ),
            exists=True,
            dir_okay=False,
            file_okay=True,
            readable=True,
            resolve_path=True,
        ),
    ] = None,
):
    with (
        tarfile.open(file, "r:gz") as report_tar,
        TemporaryDirectory(delete=False) as temp_dir_str,
    ):
        report_tar.extractall(temp_dir_str)
        temp_dir = Path(temp_dir_str)

        if not (temp_dir / SERIALIZED_REPORT_NAME).exists():
            raise typer.Abort(
                f"{SERIALIZED_REPORT_NAME} doesn't exist in {file}, cannot continue"
            )

        with open(temp_dir / SERIALIZED_REPORT_NAME) as f:
            serialized_report = SerializableBugReport.model_validate(
                json.load(f), extra="allow"
            )
            if (
                serialized_report.checkbox_session
                and not (temp_dir / "checkbox_session.tar.gz").exists()
            ):
                raise typer.Abort(
                    f"The bug report requires a checkbox session, but it wasn't in {file}"
                )

            with tarfile.open(temp_dir / "checkbox_session.tar.gz", "r:gz") as cbs:
                cbs.extractall(temp_dir / "checkbox_session")

            serialized_report.checkbox_session = temp_dir / "checkbox_session"
            report = serialized_report.to_bug_report()
            print(report)


@app.command("lp", help="Submit to Launchpad")
def lp_main(
    file: Annotated[
        Path | None,
        typer.Argument(
            help=(
                "The .tar.gz file created by [u]bugit-v2 local[/]. "
                + "A launchpad bug report will be created based on the content of this archive."
            ),
            exists=True,
            dir_okay=False,
            file_okay=True,
            readable=True,
            resolve_path=True,
        ),
    ] = None,
):
    pass


if __name__ == "__main__":
    app(prog_name="bugit.submit" if is_snap() else "bugit-submit")
