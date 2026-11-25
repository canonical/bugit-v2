#!/usr/bin/env python3
"""Print the exact `ask_user` requestedSchema for each bugit question.

The Copilot CLI `ask_user` tool needs `requestedSchema` as a JSON object. Writing it by
hand is the usual cause of `Invalid input: "requestedSchema": Expected object, received
string`. Run this and paste what it prints as the `requestedSchema` argument.

Usage:
  ask_form.py cleanup
  ask_form.py text <field> [title]
  ask_form.py tracker
  ask_form.py setup [--missing project cid] [--tracker local]
  ask_form.py failures [--line "sub | test | high"]... [--missing project cid]
  ask_form.py severity [--suggest high]      (asks severity and stage)
  ask_form.py approve
  ask_form.py attach [--group collector]...
  ask_form.py --check '<the schema you drafted>'

Needs only python3 >= 3.10 (standard library). Exit 0 prints a schema object, 1 bad usage.
"""

import argparse
import json
import sys
from typing import Any

SEVERITIES = ["highest", "high", "medium", "low", "lowest"]
TRACKERS = ["local", "jira", "launchpad"]
# issue_file_time values and what the draft calls them under Stage
STAGES = {
    "immediate": "Right after it happened",
    "after_reboot": "Device froze, reported after a reboot",
    "later": "At a later stage",
}
DECISIONS = ["approve", "edit", "reject"]
TYPES = {"string", "boolean", "array", "number", "integer"}

SETUP_FIELDS: dict[str, dict[str, Any]] = {
    "project": {
        "type": "string",
        "title": "Project",
        "description": "letters and digits, e.g. somerville",
    },
    "jira_assignee": {
        "type": "string",
        "title": "Jira assignee",
        "description": "email, e.g. a@b.com",
    },
    "lp_assignee": {
        "type": "string",
        "title": "Launchpad assignee",
        "description": "Launchpad ID only, e.g. hanhsuan",
    },
    "cid": {
        "type": "string",
        "title": "CID",
        "description": "6 digits, -, 5 digits, e.g. 202001-27667",
    },
    "sku": {
        "type": "string",
        "title": "SKU",
        "description": "letters, digits, - or _, e.g. sku1",
    },
    "platform_tags": {
        "type": "string",
        "title": "Platform tags",
        "description": "comma list, e.g. tag-a,tag-b",
    },
}


def boolean(name: str, title: str, description: str, default: bool) -> dict[str, Any]:
    return {
        "type": "boolean",
        "title": title,
        "description": description,
        "default": default,
    }


def choice(
    name: str, title: str, options: list[str], default: str | None = None
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "type": "string",
        "title": title,
        "description": "Pick one.",
        "oneOf": [{"const": o, "title": o} for o in options],
    }
    if default in options:
        field["default"] = default
    return field


def multi(name: str, title: str, description: str, options: list[str]) -> dict[str, Any]:
    return {
        "type": "array",
        "title": title,
        "description": description,
        "items": {"type": "string", "enum": options},
    }


def form(message: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {"message": message, "requestedSchema": {"properties": properties}}


def form_text(name: str, title: str) -> dict[str, Any]:
    return form(
        f"What is the {title.lower()}?",
        {name: {"type": "string", "title": title}},
    )


def form_cleanup() -> dict[str, Any]:
    return form(
        "Cleanup: unbind the MCP server on the DUT and purge its saved settings?",
        {
            "unbind": boolean(
                "unbind",
                "Unbind: stop the server on the DUT",
                "Stop the bugit MCP server and revert mcp.bind-address.",
                True,
            ),
            "purge": boolean(
                "purge",
                "Purge: clear saved settings and Jira login on the DUT",
                "Remove bugit configuration and the cached Jira credentials.",
                False,
            ),
        },
    )


def form_missing(missing: list[str]) -> dict[str, Any]:
    # Accept either space-separated args (`--missing a b c`) or a single
    # comma-separated string (`--missing a,b,c`), since callers sometimes pass
    # the `missing` JSON array from `configure.sh` joined with commas.
    names = [part for item in missing for part in item.split(",") if part]
    properties: dict[str, Any] = {}
    for name in names:
        if name not in SETUP_FIELDS:
            raise ValueError(
                f"unknown setting '{name}': use one of {', '.join(SETUP_FIELDS)}"
            )
        properties[name] = SETUP_FIELDS[name]
    return properties


def form_tracker() -> dict[str, Any]:
    return form(
        "Which bug tracker will you file to?",
        {"tracker": choice("tracker", "Tracker", TRACKERS, "local")},
    )


def form_setup(missing: list[str], tracker: str) -> dict[str, Any]:
    skip = {"jira": "lp_assignee", "launchpad": "jira_assignee"}.get(tracker)
    names = [part for item in missing for part in item.split(",") if part]
    properties = form_missing([m for m in names if m != skip])
    return form("Fill in the missing bugit settings.", properties)


def form_failures(lines: list[str], missing: list[str]) -> dict[str, Any]:
    if not lines:
        raise ValueError(
            "no failure lines: pass --line for every choice from `M failures`"
        )
    properties = {
        "tests": multi(
            "tests", "Failed tests", "Select every line you want to file.", lines
        )
    }
    properties.update(form_missing(missing))
    return form("Which failed tests should this bug cover?", properties)


def form_severity(suggest: str | None) -> dict[str, Any]:
    return form(
        "What severity and stage should this bug have?",
        {
            "severity": choice("severity", "Severity", SEVERITIES, suggest),
            "stage": {
                "type": "string",
                "title": "Stage",
                "description": "When was the issue filed?",
                "oneOf": [{"const": k, "title": v} for k, v in STAGES.items()],
                "default": "immediate",
            },
        },
    )


def form_approve() -> dict[str, Any]:
    return form(
        "Draft above. Choose approve, edit or reject.",
        {
            "decision": choice("decision", "Decision", DECISIONS, "approve"),
            "changes": {
                "type": "string",
                "title": "What should change?",
                "description": "Only needed when you choose edit.",
            },
        },
    )


def form_attach(groups: list[str]) -> dict[str, Any]:
    """Groups only: a run can collect hundreds of files, too many for one list."""
    names: list[str] = []
    for group in groups:
        label = group.partition(":")[0].strip()
        if not label:
            raise ValueError(f"bad group '{group}': use \"<group>\"")
        if label not in names:
            names.append(label)
    if not names:
        raise ValueError(
            "no log groups: pass --group for every group printed by `M attach`"
        )
    return form(
        "Which log groups should be attached?",
        {
            "groups": multi(
                "groups",
                "Log groups",
                "A chosen group attaches all of its files.",
                names,
            ),
        },
    )


def check_schema(drafted: str) -> dict[str, Any]:
    """Return the schema as an object, unwrapping a quoted string if needed."""
    value: Any = json.loads(drafted)
    if isinstance(value, str):
        print(
            "warning: the schema was a quoted string; pass the object itself",
            file=sys.stderr,
        )
        value = json.loads(value)
    if not isinstance(value, dict):
        raise TypeError("requestedSchema must be an object, not a list or a value")
    schema = value.get("requestedSchema", value)
    if not isinstance(schema, dict):
        raise TypeError("requestedSchema must be an object")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise ValueError("requestedSchema needs at least one field in properties")
    for name, field in properties.items():
        if not isinstance(field, dict):
            raise TypeError(f"field '{name}' must be an object")
        if field.get("type") not in TYPES:
            raise ValueError(f"field '{name}' has an unknown type {field.get('type')!r}")
    return schema


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        metavar="JSON",
        help="validate a schema you drafted and print it as an object",
    )
    sub = parser.add_subparsers(dest="form")

    sub.add_parser("cleanup", help="unbind/purge yes/no form")
    sub.add_parser("tracker", help="tracker choice")

    p = sub.add_parser("text", help="one free-text field, e.g. the DUT ip")
    p.add_argument("name", help="field name")
    p.add_argument("title", nargs="?", default="", help="label shown to the user")

    p = sub.add_parser(
        "setup", help="one text field per missing setting for the tracker"
    )
    p.add_argument(
        "--missing", nargs="*", default=[], help="names from `configure.sh --status`"
    )
    p.add_argument("--tracker", default="local", choices=TRACKERS)

    p = sub.add_parser(
        "failures", help="multi-select of failed tests, plus missing settings"
    )
    p.add_argument(
        "--line", action="append", default=[], help="one choice line, verbatim"
    )
    p.add_argument("--missing", nargs="*", default=[])

    p = sub.add_parser("severity", help="severity and stage choice")
    p.add_argument(
        "--suggest", choices=SEVERITIES, help="highest suggested_severity of the tests"
    )

    sub.add_parser("approve", help="approve/edit/reject form")

    p = sub.add_parser("attach", help="multi-select of log groups")
    p.add_argument("--group", action="append", default=[], help="a log group name")

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.check:
            schema = check_schema(args.check)
        elif args.form == "cleanup":
            schema = form_cleanup()["requestedSchema"]
        elif args.form == "text":
            schema = form_text(args.name, args.title or args.name)["requestedSchema"]
        elif args.form == "tracker":
            schema = form_tracker()["requestedSchema"]
        elif args.form == "setup":
            schema = form_setup(args.missing, args.tracker)["requestedSchema"]
        elif args.form == "failures":
            schema = form_failures(args.line, args.missing)["requestedSchema"]
        elif args.form == "severity":
            schema = form_severity(args.suggest)["requestedSchema"]
        elif args.form == "approve":
            schema = form_approve()["requestedSchema"]
        elif args.form == "attach":
            schema = form_attach(args.group)["requestedSchema"]
        else:
            build_parser().print_help(sys.stderr)
            return 1
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(schema, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
