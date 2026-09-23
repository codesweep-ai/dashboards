#!/usr/bin/env python3
"""Check a generated ci-status.json against what SPEC.md describes."""

import json
import os
import sys

REQUIRED = ("schema", "generated", "window", "repo", "workflows")
REQUIRED_REPO = ("name", "full_name", "url", "branch")


def main():
    path = sys.argv[1]
    with open(path) as fh:
        data = json.load(fh)

    if data.get("schema") != 1:
        sys.exit(f"{path}: unexpected schema {data.get('schema')!r}, wanted 1")
    for key in REQUIRED:
        if key not in data:
            sys.exit(f"{path}: missing {key}")
    for key in REQUIRED_REPO:
        if key not in data["repo"]:
            sys.exit(f"{path}: missing repo.{key}")

    # SPEC.md: the full SHA of the newest passing push build of ci, or null. A
    # sibling's `make repin` reads it with sed, so the spelling matters.
    b = data.get("built")
    if b is not None and not (isinstance(b, str) and len(b) == 40 and all(c in "0123456789abcdef" for c in b)):
        sys.exit(f"{path}: built is {b!r}, wanted a full lower-case SHA or null")

    names = [w["name"] for w in data["workflows"]]
    # SPEC.md: the run writing the file is in flight while it reads the API, and
    # reporting it would put a workflow that never finishes on the card.
    # A run record carries no id, but its url ends with one.
    run_id = os.environ.get("GITHUB_RUN_ID")
    if run_id:
        for wf in data["workflows"]:
            for run in wf["runs"]:
                if (run.get("url") or "").rstrip("/").endswith(f"/runs/{run_id}"):
                    sys.exit(f"{path}: the writing run reported itself in {wf['name']}")

    print(f"test: {path} matches SPEC.md ({', '.join(names) or 'no workflows'})")


if __name__ == "__main__":
    main()
