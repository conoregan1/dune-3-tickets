#!/usr/bin/env python3
"""Check the workflow files are actually valid, not merely parseable.

Brought over from weekend-flights, where an unquoted " #" inside a workflow
value made YAML silently truncate a ${{ }} expression. yaml.safe_load was
happy; GitHub rejected the file and the feature never ran. This looks at the
values that come out of the parse, and bash -n's every run block.

    python -m pip install pyyaml
    python tools/check_workflows.py
"""

from __future__ import annotations

import glob
import os

import yaml

# Expression fragments that must balance once YAML has had its way with them.
OPEN, CLOSE = "${{", "}}"


def walk(node, path=""):
    """Every string in the parsed document, with a breadcrumb to find it."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from walk(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def check(filename: str) -> list[str]:
    problems: list[str] = []
    raw = open(filename, encoding="utf-8").read()

    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return [f"will not parse at all: {exc}"]

    if not isinstance(doc, dict) or "jobs" not in doc:
        problems.append("no jobs block — GitHub will reject this")

    for path, text in walk(doc):
        if OPEN in text and text.count(OPEN) != text.count(CLOSE):
            problems.append(
                f"{path}: expression truncated by YAML -> {text!r}\n"
                "        quote the whole value; a '#' or ': ' inside an "
                "unquoted scalar cuts it short"
            )

    # A run block losing its shebang-level content is the same class of problem.
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps", []) or []:
            if "uses" not in step and not step.get("run"):
                problems.append(
                    f"step {step.get('name', '?')!r} has neither uses nor run"
                )
    return problems


def check_shell(filename: str) -> list[str]:
    """bash -n every run block.

    YAML validity says nothing about the shell inside it, and these blocks have
    grown loops and conditionals. A syntax error there only shows up as a failed
    run at 6am, which is the worst place to find it.
    """
    import re
    import subprocess
    import tempfile

    problems: list[str] = []
    doc = yaml.safe_load(open(filename, encoding="utf-8"))
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps", []) or []:
            script = step.get("run")
            if not script:
                continue
            # GitHub expressions are not shell; substitute before checking.
            cleaned = re.sub(r"\$\{\{[^}]*\}\}", "PLACEHOLDER", script)
            tmp = tempfile.NamedTemporaryFile(
                "w", suffix=".sh", delete=False, encoding="utf-8"
            )
            tmp.write(cleaned)
            tmp.close()
            result = subprocess.run(
                ["bash", "-n", tmp.name], capture_output=True, text=True
            )
            os.unlink(tmp.name)
            if result.returncode:
                problems.append(
                    f"step {step.get('name', '?')!r}: shell syntax error -> "
                    + result.stderr.strip().splitlines()[-1][:120]
                )
    return problems


def main() -> int:
    files = sorted(glob.glob(".github/workflows/*.yml"))
    if not files:
        print("No workflow files found.")
        return 1

    bad = 0
    for filename in files:
        problems = check(filename) + check_shell(filename)
        name = os.path.basename(filename)
        if problems:
            bad += 1
            print(f"{name}:")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print(f"{name}: ok")

    print()
    print(f"{len(files) - bad}/{len(files)} workflows valid")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
