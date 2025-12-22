import typer
from typing_extensions import Annotated

from bugit_v2.models.dut_info import DutInfo
from bugit_v2.utils import is_prod, is_snap
from bugit_v2.utils.constants import DUT_INFO_DIR
from bugit_v2.utils.validations import ensure_all_directories_exist, is_cid

app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=not is_prod(),
    pretty_exceptions_show_locals=not is_prod(),
    no_args_is_help=True,
    add_completion=not is_snap(),  # the built-in ones doesn't work in snap
)


def strip(value: str | None) -> str | None:
    return value and value.strip()


def cid_check(value: str | None) -> str | None:
    if value is None:
        return None
    if not is_cid(value):
        raise typer.BadParameter(
            f"Invalid CID: '{value}'. "
            + "CID should look like 202408-12345 "
            + "(6 digits, dash, then 5 digits)",
        )
    return value.strip()


def alnum_check(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.isalnum():
        raise typer.BadParameter(
            f"Invalid project: '{value}'. "
            + "Project name should be an alphanumeric string."
        )
    return value.strip()


def assignee_str_check(value: str | None) -> str | None:
    if value is None:
        return None
    # pydantic will check for email
    if value.startswith("lp:"):
        raise typer.BadParameter('Assignee should not start with "lp:"')
    return value.strip()


@app.command(
    help="Persist DUT info like CID, SKU, platform tags to let bugit reuse it in bug reports and info collectors"
)
def main(
    cid: Annotated[
        str | None,
        typer.Option(
            "-c",
            "--cid",
            help=(
                "Canonical ID (CID) of the device under test. "
                + 'This is used to pre-fill the "CID" field in the editor'
            ),
            file_okay=False,
            dir_okay=False,
            callback=cid_check,
        ),
    ] = None,
    sku: Annotated[
        str | None,
        typer.Option(
            "-k",
            "--sku",
            help="Stock Keeping Unit (SKU) string of the device under test. "
            + 'This is used to pre-fill the "SKU" field in the editor',
            file_okay=False,
            dir_okay=False,
            callback=strip,
        ),
    ] = None,
    project: Annotated[
        str | None,
        typer.Option(
            "-p",
            "--project",
            help="Project name (case sensitive) like STELLA, SOMERVILLE. "
            + 'This is used to pre-fill the "Project" field in the editor',
            file_okay=False,
            dir_okay=False,
            callback=alnum_check,
        ),
    ] = None,
    jira_assignee: Annotated[
        str | None,
        typer.Option(
            "-ja",
            "--jira-assignee",
            help="Assignee ID. For Jira it's the assignee's email. ",
            file_okay=False,
            dir_okay=False,
            callback=assignee_str_check,
        ),
    ] = None,
    lp_assignee: Annotated[
        str | None,
        typer.Option(
            "-la",
            "--lp-assignee",
            help='Assignee ID. For Launchpad it\'s LP ID, without the "lp:" part',
            file_okay=False,
            dir_okay=False,
            callback=assignee_str_check,
        ),
    ] = None,
    platform_tags: Annotated[
        list[str],
        typer.Option(
            "-pt",
            "--platform-tags",
            help='Platform Tags. They will appear under "Components" on Jira. '
            + 'This is used to pre-fill the "Platform Tags" field in the editor',
            file_okay=False,
            dir_okay=False,
        ),
    ] = [],  # pyright: ignore[reportCallInDefaultInitializer]
    tags: Annotated[
        list[str],
        typer.Option(
            "-t",
            "--tags",
            help="Additional tags on Jira. "
            + 'This is used to pre-fill the "Tags" field in the editor',
            file_okay=False,
            dir_okay=False,
        ),
    ] = [],  # pyright: ignore[reportCallInDefaultInitializer]):
):
    ensure_all_directories_exist()
    with open(DUT_INFO_DIR / "dut_info.json", "w") as f:
        f.write(
            DutInfo(
                cid=cid,
                sku=sku,
                project=project,
                platform_tags=platform_tags,
                jira_assignee=jira_assignee,
                lp_assignee=lp_assignee,
            ).model_dump_json()
        )


if __name__ == "__main__":
    app()
