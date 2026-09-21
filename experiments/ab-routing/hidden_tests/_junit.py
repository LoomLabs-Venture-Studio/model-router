"""Minimal JUnit XML reader shared by task1's black-box tests and score.py
(not a test module itself -- leading underscore keeps pytest from collecting it)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def read_junit_cases(junit_path: Path) -> list[dict]:
    """Returns one dict per <testcase>: {"name", "classname", "failed", "skipped"}.
    "failed" covers both <failure> and <error> children -- for our purposes an
    error (e.g. an unhandled exception) is exactly as much a non-pass as a
    failed assertion."""
    tree = ET.parse(junit_path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    cases = []
    for suite in suites:
        for tc in suite.findall("testcase"):
            cases.append({
                "name": tc.get("name"),
                "classname": tc.get("classname"),
                "failed": tc.find("failure") is not None or tc.find("error") is not None,
                "skipped": tc.find("skipped") is not None,
            })
    return cases


def case_by_name(cases: list[dict], name: str) -> dict | None:
    return next((c for c in cases if c["name"] == name), None)
