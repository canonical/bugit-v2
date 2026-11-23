import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / ".github/skills/bugit/scripts/ask_form.py"
LINT = ROOT / ".github/skills/bugit/scripts/lint_skill.sh"
SKILL_DIR = ROOT / ".github/skills/bugit"

_spec = importlib.util.spec_from_file_location("ask_form", SCRIPT)
assert _spec and _spec.loader
ask_form = importlib.util.module_from_spec(_spec)
sys.modules["ask_form"] = ask_form
_spec.loader.exec_module(ask_form)


def run(argv: list[str], capsys) -> dict[str, Any]:
    assert ask_form.main(argv) == 0
    printed = capsys.readouterr().out
    parsed = json.loads(printed)
    assert isinstance(parsed, dict), "ask_form.py must print an object, not a string"
    return parsed


def test_cleanup_form_is_two_booleans(capsys):
    properties = run(["cleanup"], capsys)["properties"]
    assert set(properties) == {"unbind", "purge"}
    assert all(field["type"] == "boolean" for field in properties.values())
    assert properties["unbind"]["default"] is True
    assert properties["purge"]["default"] is False


def test_text_form_has_one_string_field(capsys):
    properties = run(["text", "ip", "DUT IP address"], capsys)["properties"]
    assert properties == {"ip": {"type": "string", "title": "DUT IP address"}}


def test_unknown_setting_is_an_error(capsys):
    assert ask_form.main(["setup", "--missing", "banana"]) == 1
    assert "banana" in capsys.readouterr().err


def test_failures_lists_every_choice_verbatim(capsys):
    lines = ["session:new | ns::gfx/a | high", "session:old | ns::net/b | n/a"]
    properties = run(
        ["failures", "--line", lines[0], "--line", lines[1], "--missing", "project"],
        capsys,
    )["properties"]
    assert properties["tests"]["items"]["enum"] == lines
    assert properties["project"]["type"] == "string"


def test_failures_without_lines_is_an_error(capsys):
    assert ask_form.main(["failures"]) == 1
    assert "--line" in capsys.readouterr().err


def test_severity_preselects_the_suggestion(capsys):
    severity = run(["severity", "--suggest", "Critical"], capsys)["properties"][
        "severity"
    ]
    assert severity["default"] == "Critical"
    assert [option["const"] for option in severity["oneOf"]] == ask_form.SEVERITIES


def test_severity_rejects_an_unknown_value():
    with pytest.raises(SystemExit):
        ask_form.main(["severity", "--suggest", "mega"])


def test_approve_form_offers_the_three_decisions(capsys):
    properties = run(["approve"], capsys)["properties"]
    assert properties["decision"]["default"] == "approve"
    assert "changes" in properties


def test_attach_derives_the_file_list_from_the_groups(capsys):
    properties = run(
        ["attach", "--group", "checkbox: a.log, b.log", "--group", "oem: c.log"],
        capsys,
    )["properties"]
    assert properties["groups"]["items"]["enum"] == ["checkbox", "oem"]
    assert properties["files"]["items"]["enum"] == ["a.log", "b.log", "c.log"]


def test_check_unwraps_a_quoted_string_schema(capsys):
    drafted = '"{\\"properties\\": {\\"unbind\\": {\\"type\\": \\"boolean\\"}}}"'
    assert ask_form.main(["--check", drafted]) == 0
    out, err = capsys.readouterr()
    schema = json.loads(out)
    assert schema["properties"]["unbind"]["type"] == "boolean"
    assert "quoted string" in err


def test_check_accepts_a_good_schema(capsys):
    good = '{"properties": {"severity": {"type": "string"}}}'
    assert ask_form.main(["--check", good]) == 0
    assert json.loads(capsys.readouterr().out) == json.loads(good)


@pytest.mark.parametrize(
    "drafted",
    ["[1, 2]", '"not json at all"', '{"properties": {"a": {"type": "text"}}}', "42"],
)
def test_check_rejects_a_bad_schema(capsys, drafted):
    assert ask_form.main(["--check", drafted]) == 1


def test_check_rejects_an_unknown_field_type(capsys):
    assert ask_form.main(["--check", '{"properties": {"a": {"type": "text"}}}']) == 1
    _, err = capsys.readouterr()
    assert "unknown type" in err


def test_lint_passes_on_the_shipped_skill():
    result = subprocess.run(
        ["bash", str(LINT), str(SKILL_DIR)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout


def _skill(tmp_path, body: str) -> Path:
    skill = tmp_path / "skill"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# bugit\n\nAsk with `ask_user` for the ip.\n")
    (skill / "references/ask.md").write_text(body)
    return skill


def test_lint_flags_ask_user_without_ask_form(tmp_path):
    skill = _skill(tmp_path, "Ask with `ask_user`: two yes/no fields.\n")
    result = subprocess.run(
        ["bash", str(LINT), str(skill)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1
    assert "ask_user without ask_form.py" in result.stdout


def test_lint_flags_a_quoted_schema(tmp_path):
    skill = _skill(
        tmp_path,
        'Run ask_form.py and pass ask_user \'{"properties": {"a": {}}}\'.\n',
    )
    result = subprocess.run(
        ["bash", str(LINT), str(skill)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1
    assert "quoted string" in result.stdout
