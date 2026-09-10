#!/usr/bin/env python3
"""Check projects.json is well formed, and that every status URL is same-origin.

Same-origin is the property the whole design rests on: a status URL that left
codesweep.ai would need CORS on the other end and would spend the GitHub API's
anonymous quota. See SPEC.md.
"""

import json
import sys
import urllib.parse


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "projects.json"
    with open(path) as fh:
        data = json.load(fh)

    if data.get("schema") != 1:
        sys.exit(f"{path}: unexpected schema {data.get('schema')!r}, wanted 1")

    projects = data.get("projects")
    if not projects:
        sys.exit(f"{path}: no projects listed")

    seen = set()
    for entry in projects:
        for key in ("name", "status"):
            if key not in entry:
                sys.exit(f"{path}: an entry has no {key}")
        name, status = entry["name"], entry["status"]
        if name in seen:
            sys.exit(f"{path}: {name} is listed twice")
        seen.add(name)
        if urllib.parse.urlparse(status).scheme:
            sys.exit(f"{path}: {name} points at {status}, which is not same-origin")
        if not status.endswith(".json"):
            sys.exit(f"{path}: {name} points at {status}, which is not a .json")

    print(f"data: {len(seen)} projects, all relative")


if __name__ == "__main__":
    main()
