from bugit_v2.mcp_server.bugs import Draft
from bugit_v2.mcp_server.report import (
    SECTION_NAMES,
    format_preview,
    render_description,
)


def test_section_order_matches_bugit_editor():
    assert SECTION_NAMES == (
        "Summary",
        "Steps to Reproduce",
        "Expected Result",
        "Actual Result",
        "Failure Rate",
        "Affected Test Cases",
        "Additional Information",
    )


def test_render_matches_bugit_template_layout():
    text = render_description(
        {"Summary": "s", "Actual Result": "boom"},
        test_case="com.x::acpi/oem_osi",
        cid="202401-1",
        sku="sku9",
        machine_info={"Image": "img", "GPU": "nv"},
        extra_info="note",
    )
    assert text == (
        "[Summary]\ns\n\n"
        "[Steps to Reproduce]\n\n"
        "[Expected Result]\n\n"
        "[Actual Result]\nboom\n\n"
        "[Failure Rate]\n\n"
        "[Affected Test Cases]\ncom.x::acpi/oem_osi\n\n"
        "[Additional Information]\nCID: 202401-1\nSKU: sku9\nImage: img\nGPU: nv\nnote\n"
    )


def test_render_without_context_keeps_empty_cid_sku():
    text = render_description({}, test_case=None, cid=None, sku=None, machine_info={})
    assert "[Affected Test Cases]\n\n" in text
    assert text.endswith("[Additional Information]\nCID: \nSKU: \n")


def make_draft(**kw) -> Draft:
    base = dict(
        draft_id="d",
        title="T",
        description="[Summary]\nbody\n",
        tags=["a"],
        logs_job_id=None,
        tracker="launchpad",
        project="somerville",
        created_at=0.0,
    )
    return Draft(**{**base, **kw})


def test_preview_shows_what_will_be_submitted_for_launchpad():
    out = format_preview(
        make_draft(severity="highest", issue_file_time="immediate", platform_tags=["numbat"]),
        {"stdout": "out", "stderr": "", "comments": ""},
    )
    assert "Tracker: launchpad" in out and "Project: somerville" in out
    assert "Severity: Critical (LP) / Highest (Jira)" in out
    assert "Platform tags: numbat" in out
    assert "Title: T" in out
    assert "[Summary]\nbody" in out
    assert "[Stage]\nRight after it happened" in out
    assert "[Job Output]\n" in out and "Job stdout\n----\nout" in out


def test_preview_jira_uses_noformat_blocks():
    out = format_preview(make_draft(tracker="jira"), {"stdout": "out"})
    assert "*Job stdout*\n{noformat}out{noformat}" in out


def test_preview_without_job_output_has_no_block():
    assert "[Job Output]" not in format_preview(make_draft(), None)


def test_preview_truncates_long_job_output():
    out = format_preview(make_draft(), {"stdout": "x" * 10000})
    assert "truncated" in out and len(out) < 6000
