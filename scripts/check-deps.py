#!/usr/bin/env python3
"""Check a generated deps.json against what SPEC.md describes."""

import json
import re
import sys

REQUIRED = ("schema", "generated", "org", "projects", "lifecycle", "history", "sources")
# Empty lists and nulls are left out of the file, except these, whose absence
# would read as "not looked at" rather than "nothing there".
REQUIRED_DEP = ("ecosystem", "name", "version", "scope", "sources", "status", "level")
ECOSYSTEMS = {"go", "npm", "pypi", "actions", "runtime", "image", "package", "native"}
STATUSES = {"vulnerable", "eol", "eol-soon", "behind", "major", "deprecated", "minor", "patch",
            "current", "floating", "unknown", "untracked", "held"}
LEVELS = {"idle", "good", "info", "warning", "serious", "critical"}
REACHABLE = {"called", "imported", "required", "not-in-build"}
VERDICTS = {"allowed", "review", "denied", "unknown", "not-shipped", "aggregate"}
SIGNALS = {"deprecated", "abandoned", "stale", "quiet", "scorecard", "install-script", "unpinned-action"}
SLA_STATES = {"within", "due-soon", "breached"}
REASONS = {"fix_started", "inaccurate", "no_bandwidth", "not_used", "tolerable_risk"}
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def fail(path, msg):
    sys.exit(f"{path}: {msg}")


TIERS = {"fix", "plan", "routine"}
KINDS = {"lock", "rebuild", "sync", "eol", "actions", "devtools", "routine", "license", "unlisted", "replace", "dep"}
TYPES = {"security", "eol", "license", "supply", "sync", "update"}


def check_actions(path, where, acts, projects):
    """Actions as SPEC.md describes them: a known kind and tier, and items that name real records."""
    ids = {a.get("id") for a in acts}
    for a in acts:
        label = f"{where}: action {a.get('id')!r}"
        if any(r not in ids for r in a.get("requires") or []):
            fail(path, f"{label}: requires an action that is not in the same list")
        if any(not o.get("text") for o in a.get("options") or []):
            fail(path, f"{label}: an option needs a text")
        if a.get("kind") not in KINDS or a.get("tier") not in TIERS or a.get("type") not in TYPES:
            fail(path, f"{label}: unknown kind, tier or type")
        if not a.get("title") or not a.get("items"):
            fail(path, f"{label}: an action needs a title and items")
        for it in a["items"]:
            deps = (projects.get(it.get("project")) or {}).get("dependencies") or []
            if not isinstance(it.get("record"), int) or not 0 <= it["record"] < len(deps):
                fail(path, f"{label}: item points at no record ({it.get('project')}, {it.get('record')})")
            if it.get("tier") not in TIERS:
                fail(path, f"{label}: item has unknown tier {it.get('tier')!r}")


def check_actions_file(path):
    """The actions file: every action carries steps an agent can follow and changes it can check."""
    with open(path) as fh:
        doc = json.load(fh)
    if doc.get("schema") != 1 or not TIME.match(doc.get("generated", "")):
        fail(path, "an actions file needs schema 1 and a UTC generated time")
    ids, count = set(), 0
    for p in doc.get("projects", []):
        if p.get("error"):
            continue
        for a in p.get("actions", []):
            count += 1
            label = f"project {p.get('name')!r}: action {a.get('id')!r}"
            if a.get("id") in ids:
                fail(path, f"{label}: ids must be unique")
            ids.add(a.get("id"))
            if a.get("tier") not in TIERS or not a.get("changes"):
                fail(path, f"{label}: needs a tier and changes")
            earlier = [x["id"] for x in p["actions"][:p["actions"].index(a)]]
            if any(r not in earlier for r in a.get("requires") or []):
                fail(path, f"{label}: an action it requires must come before it")
            for s in a.get("steps") or []:
                if sum(k in s for k in ("run", "edit", "do")) != 1:
                    fail(path, f"{label}: a step is exactly one of run, edit or do, got {s!r}")
            for c in a["changes"]:
                if not c.get("files") or not c.get("done_when"):
                    fail(path, f"{label}: change {c.get('name')!r} needs files and done_when")
    print(f"test: {path} matches SPEC.md ({count} actions)")


def check_evidence(path, label, d):
    """The fields beside a verdict, as SPEC.md's evidence section lists them."""
    for v in d.get("vulnerabilities") or []:
        if "reachable" in v and v["reachable"] not in REACHABLE:
            fail(path, f"{label}: {v.get('id')} has unknown reachability {v['reachable']!r}")
        if "exploited" in v and not DAY.match(str(v["exploited"])):
            fail(path, f"{label}: {v.get('id')} exploited is not a day, got {v['exploited']!r}")
        epss = v.get("epss")
        if epss is not None and not all(0 <= epss.get(k, -1) <= 1 for k in ("probability", "percentile")):
            fail(path, f"{label}: {v.get('id')} epss needs a probability and a percentile between 0 and 1")
    if "purl" in d and not str(d["purl"]).startswith("pkg:"):
        fail(path, f"{label}: purl is not a package URL, got {d['purl']!r}")
    if "reachability" in d and d["reachability"] not in REACHABLE:
        fail(path, f"{label}: unknown reachability {d['reachability']!r}")
    lic = d.get("license")
    if lic is not None and lic.get("verdict") not in VERDICTS:
        fail(path, f"{label}: unknown license verdict {lic.get('verdict')!r}")
    if lic and "found" in lic and (not lic["found"] or lic.get("found_verdict") not in ("unknown", "review", "denied")):
        fail(path, f"{label}: license.found needs ids and a found_verdict of unknown, review or denied")
    if lic and "score" in lic and not 0 <= lic["score"] <= 100:
        fail(path, f"{label}: license.score is out of range, got {lic['score']!r}")
    for sig in d.get("signals") or []:
        if sig.get("kind") not in SIGNALS or not sig.get("text"):
            fail(path, f"{label}: a signal needs a known kind and a text, got {sig!r}")
    for owner, value in (("", d), ("license.", lic or {})):
        if "opened" in value and not TIME.match(value["opened"]):
            fail(path, f"{label}: {owner}opened is not a UTC time")
        window = value.get("sla")
        if window is not None and (window.get("state") not in SLA_STATES or not TIME.match(window.get("due", ""))):
            fail(path, f"{label}: {owner}sla needs a known state and a UTC due time, got {window!r}")
    acc = d.get("accepted")
    if acc is not None and acc.get("reason") not in REASONS:
        fail(path, f"{label}: accepted with unknown reason {acc.get('reason')!r}")


def main():
    path = sys.argv[1]
    if path.endswith("actions.json"):
        check_actions_file(path)
        return
    with open(path) as fh:
        data = json.load(fh)

    if data.get("schema") != 1:
        fail(path, f"unexpected schema {data.get('schema')!r}, wanted 1")
    for key in REQUIRED:
        if key not in data:
            fail(path, f"missing {key}")
    if not TIME.match(data["generated"]):
        fail(path, f"generated is not a UTC time: {data['generated']!r}")

    count = 0
    for p in data["projects"]:
        where = f"project {p.get('name')!r}"
        if p.get("error"):
            continue
        for key in ("name", "repo", "dependencies", "summary", "manifests"):
            if key not in p:
                fail(path, f"{where}: missing {key}")
        for d in p["dependencies"]:
            count += 1
            label = f"{where}: {d.get('ecosystem')}:{d.get('name')}"
            for key in REQUIRED_DEP:
                if key not in d:
                    fail(path, f"{label}: missing {key}")
            if d["ecosystem"] not in ECOSYSTEMS:
                fail(path, f"{label}: unknown ecosystem")
            if d["status"] not in STATUSES:
                fail(path, f"{label}: unknown status {d['status']!r}")
            if d["level"] not in LEVELS:
                fail(path, f"{label}: unknown level {d['level']!r}")
            if not d["sources"] or "path" not in d["sources"][0]:
                fail(path, f"{label}: a record says where it was declared")
            if d["status"] == "vulnerable" and not d.get("vulnerabilities"):
                fail(path, f"{label}: vulnerable with no vulnerabilities")
            # SPEC.md: an indirect record needs attention only when it is vulnerable.
            if d["scope"] in ("indirect", "transitive") and d["status"] != "vulnerable" \
                    and d["level"] not in ("idle", "good"):
                fail(path, f"{label}: an indirect record that is not vulnerable carries level {d['level']}")
            for key in ("latest_date", "version_date"):
                value = (d.get("upstream") or {}).get(key)
                if value is not None and not TIME.match(value):
                    fail(path, f"{label}: upstream.{key} is not a UTC time")
            check_evidence(path, label, d)

    for src in data.get("data_sources") or []:
        if not all(src.get(k) for k in ("id", "name", "url", "gives")):
            fail(path, f"data source {src.get('id')!r} needs an id, a name, a url and what it gives")
    by_name = {p["name"]: p for p in data["projects"]}
    check_actions(path, "org", data.get("actions") or [], by_name)
    for p in data["projects"]:
        check_actions(path, f"project {p['name']!r}", p.get("actions") or [], by_name)
        for a in p.get("actions") or []:
            if any(it["project"] != p["name"] for it in a["items"]):
                fail(path, f"project {p['name']!r}: action {a.get('id')!r} has items from another project")

    names = ", ".join(p["name"] for p in data["projects"])
    print(f"test: {path} matches SPEC.md ({count} records across {names})")


if __name__ == "__main__":
    main()
