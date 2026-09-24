#!/usr/bin/env python3
"""Check a generated ci-status.json against what SPEC.md describes."""

import json
import os
import sys

REQUIRED = ("schema", "generated", "window", "repo", "workflows")
REQUIRED_REPO = ("name", "full_name", "url", "branch")


def is_sha(v):
    return isinstance(v, str) and len(v) == 40 and all(c in "0123456789abcdef" for c in v)


def check_versions(path, where, v):
    ok = (isinstance(v, dict) and (v.get("go") is None or isinstance(v.get("go"), str))
          and all(isinstance(v.get(k), dict) and all(isinstance(x, str) for x in v[k].values())
                  for k in ("images", "npm")))
    if not ok:
        sys.exit(f"{path}: {where} has versions {v!r}, wanted go, images and npm")


def main():
    path = sys.argv[1]
    with open(path) as fh:
        data = json.load(fh)

    if data.get("schema") != 2:
        sys.exit(f"{path}: unexpected schema {data.get('schema')!r}, wanted 2")
    for key in REQUIRED:
        if key not in data:
            sys.exit(f"{path}: missing {key}")
    for key in REQUIRED_REPO:
        if key not in data["repo"]:
            sys.exit(f"{path}: missing repo.{key}")

    # SPEC.md: the newest passing builds, newest first and each once, and the
    # versions each commit was published under. A sibling's `make repin` reads the
    # first commit in built with sed, so the spelling matters.
    built = data.get("built")
    if not isinstance(built, list) or len(built) > 10:
        sys.exit(f"{path}: built is {built!r}, wanted a list of at most 10 builds")
    for b in built:
        if not isinstance(b, dict) or not is_sha(b.get("commit")):
            sys.exit(f"{path}: built holds {b!r}, wanted an object with a full lower-case commit")
        check_versions(path, "built", b.get("versions"))
    commits = [b["commit"] for b in built]
    if len(set(commits)) != len(commits):
        sys.exit(f"{path}: built names a commit more than once")
    for wf in data["workflows"]:
        for run in wf["runs"]:
            if run.get("commit") and not is_sha(run["commit"]):
                sys.exit(f"{path}: a run of {wf['name']} has commit {run['commit']!r}")
            if "versions" in run:
                check_versions(path, f"a run of {wf['name']}", run["versions"])

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
