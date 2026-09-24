"""Write deps.json: every project's dependencies, and how far behind each one is.

    python3 -m collector [--output deps.json] [--previous URL-or-path] [--only NAME,…]

Reads projects.json and deps-config.json from the working directory. A token in
GH_TOKEN or GITHUB_TOKEN is used for GitHub release lookups when present, and
nothing needs one: without it the collector reads github.com's release feeds.
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import actions, catalog, extract, gitrepo, policy, reach, sbom
from .http import Client, FetchError
from .resolve import LEVELS, Resolver, attention, known_facts, level_rank, newest_built
from .sources import Sources, iso

SCHEMA = 1
HISTORY_KEEP = 400


def load(path):
    with open(path) as fh:
        return json.load(fh)


def log(msg):
    print(f"deps: {msg}", file=sys.stderr, flush=True)


GITHUB_OWNER = re.compile(r"^[A-Za-z0-9._-]+$")
GITHUB_REPOSITORY = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def not_github(owner, repository):
    """Why `owner` or `repository` names nothing on GitHub, or None when both can.

    An owner taken from a remote that is not on GitHub reads as a path, and the
    clone of github.com/<it>/... then fails as a credential error, which points
    at the wrong cause.
    """
    if not GITHUB_OWNER.match(owner or ""):
        return f"{owner!r} is not a GitHub owner. Name one with --owner or $GITHUB_REPOSITORY_OWNER."
    if not GITHUB_REPOSITORY.match(repository):
        return f"$GITHUB_REPOSITORY is {repository!r}, which is not owner/name. Unset it, or name this repository."
    return None


def summarise(project):
    """The per-project rollup the page leads with. SPEC.md defines each count."""
    deps = project["dependencies"]
    counted = [d for d in deps if d["scope"] not in ("indirect", "transitive")]
    flagged = [d for d in deps if attention(d)]
    by_eco = {}
    for d in deps:
        e = by_eco.setdefault(d["ecosystem"], {"total": 0, "levels": {}})
        e["total"] += 1
        e["levels"][d["level"]] = e["levels"].get(d["level"], 0) + 1
    status = {}
    for d in deps:
        status[d["status"]] = status.get(d["status"], 0) + 1
    vulns = {v["id"] for d in deps for v in d.get("vulnerabilities") or []}
    worst = max((d["level"] for d in flagged), key=level_rank, default="good")
    licenses = {}
    for d in deps:
        v = (d.get("license") or {}).get("verdict")
        if v:
            licenses[v] = licenses.get(v, 0) + 1
    return {
        "licenses": licenses,
        "signals": sum(1 for d in counted if d.get("signals")),
        "sla": {s: sum(1 for d in deps if (d.get("sla") or {}).get("state") == s) for s in ("breached", "due-soon", "within")},
        "dependencies": len(counted),
        "indirect": len(deps) - len(counted),
        "attention": len(flagged),
        "by_status": status,
        "by_ecosystem": by_eco,
        "vulnerabilities": len(vulns),
        "libyears": round(sum(d.get("libyears") or 0 for d in counted), 2),
        "state": worst,
    }


def lifecycle_table(projects):
    """Every release cycle in use, once, with the projects that use it."""
    rows = {}
    for p in projects:
        for d in p["dependencies"]:
            lc = d.get("lifecycle")
            if not lc or not lc.get("cycle"):
                continue
            key = (lc["product"], lc["cycle"])
            row = rows.setdefault(key, dict(lc, projects=[], dependencies=[]))
            if p["name"] not in row["projects"]:
                row["projects"].append(p["name"])
            label = d.get("label") or d["name"]
            if label not in row["dependencies"]:
                row["dependencies"].append(label)
    order = {"eol": 0, "eol-soon": 1, "maintenance": 2, "active": 3, "unknown": 4}
    return sorted(rows.values(), key=lambda r: (order.get(r["phase"], 5), r.get("eol") or "9999", r["product"]))


def history(previous, projects, generated):
    """Carry the previous file's history forward and add today's totals, one row per day."""
    rows = [r for r in (previous or {}).get("history", []) if isinstance(r, dict) and r.get("date")]
    live = [p for p in projects if not p.get("error")]
    today = {
        "date": generated[:10],
        "dependencies": sum(p["summary"]["dependencies"] for p in live),
        "attention": sum(p["summary"]["attention"] for p in live),
        "vulnerabilities": len({v["id"] for p in live for d in p["dependencies"]
                                for v in d.get("vulnerabilities") or []}),
        "eol": sum(1 for p in live for d in p["dependencies"] if d["status"] == "eol"),
        "major": sum(1 for p in live for d in p["dependencies"] if d["status"] == "major"),
        "libyears": round(sum(p["summary"]["libyears"] for p in live), 2),
    }
    rows = [r for r in rows if r["date"] != today["date"]] + [today]
    return rows[-HISTORY_KEEP:]


def resolve_attention(d):
    return attention(d)


def generated_now(now):
    return iso(now)


def feed_actions(actions_list):
    """The actions the feed announces: everything to fix now, and every license the policy denies."""
    return [a for a in actions_list
            if a["tier"] == "fix" or (a["kind"] == "license" and a["key"].startswith("license|denied|"))]


def atom_feed(data, site, limit=100):
    """An Atom feed of the actions to fix now, newest first: the alert channel that needs no key.

    Each entry is one action, as the page shows it, so one change across several
    projects is one entry. Its id is the action's key, which stays the same for as
    long as that change is called for, so a reader announces it once. Its date is
    the run that first called for it, from `seen`, so a gap open for months that
    this run found first still reads as new.
    """
    from xml.sax.saxutils import escape, quoteattr
    base = (site or "").rstrip("/")
    page = base + "/deps"
    seen = data.get("seen") or {}
    entries = []
    for a in feed_actions(data.get("actions") or []):
        first = seen.get("action|" + a["key"]) or data["generated"]
        lines = [x for x in (a.get("result"), a.get("why")) if x]
        lines += a.get("evidence") or []
        titles = {x["id"]: x["title"] for x in data.get("actions") or []}
        lines += ["First: " + titles.get(r, r) for r in a.get("requires") or []]
        if a.get("how"):
            lines.append("How: " + a["how"])
        lines += [("Recommended: " if o.get("recommended") else "Or: ") + o["text"] for o in a.get("options") or []]
        lines.append("Projects: " + ", ".join(a["projects"]))
        entries.append((first, a, "\n".join(lines)))
    # Newest first, then in the page's order of urgency.
    order = {id(a): i for i, a in enumerate(data.get("actions") or [])}
    entries.sort(key=lambda e: (e[0], -order[id(e[1])]), reverse=True)
    items = []
    for first, a, summary in entries[:limit]:
        items.append(
            "<entry>"
            f"<id>urn:{escape(data.get('owner') or data['org'])}:deps:action:{escape(a['key'])}</id>"
            f"<title>{escape(a['title'])}</title>"
            f"<published>{escape(first)}</published><updated>{escape(first)}</updated>"
            f"<link rel=\"alternate\" type=\"text/html\" href={quoteattr(page + '?view=upgrades#' + a['id'])}/>"
            f"<category term={quoteattr(a['type'])}/><category term={quoteattr(a['tier'])}/>"
            f"<summary type=\"text\">{escape(summary)}</summary>"
            "</entry>")
    return ('<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">'
            f"<id>{escape(page)}</id><title>{escape(data['org'])} dependencies: fix now</title>"
            f"<updated>{escape(data['generated'])}</updated>"
            f"<author><name>{escape(data['org'])} dashboards</name><uri>{escape(base + '/')}</uri></author>"
            f"<link rel=\"alternate\" type=\"text/html\" href={quoteattr(page)}/>"
            f"<link rel=\"self\" type=\"application/atom+xml\" href={quoteattr(base + '/deps-feed.xml')}/>"
            + "".join(items) + "</feed>\n")


def _prune(value):
    if isinstance(value, dict):
        return {k: _prune(v) for k, v in value.items() if v is not None and v != [] and v != {}
                or k in ("version", "eol", "latest")}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


def read_previous(where, client):
    if not where:
        return None
    try:
        if urllib.parse.urlparse(where).scheme in ("http", "https"):
            return client.json(where, accept_missing=True)
        with open(where) as fh:
            return json.load(fh)
    except (OSError, ValueError, FetchError) as exc:
        log(f"no previous history from {where}: {exc}")
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m collector", description=__doc__.splitlines()[0])
    ap.add_argument("--projects", default="projects.json")
    ap.add_argument("--config", default="deps-config.json")
    ap.add_argument("--output", default="deps.json", help="where to write; - for stdout")
    ap.add_argument("--previous", help="an earlier deps.json (path or URL) whose history to carry forward")
    ap.add_argument("--only", help="comma-separated project names, for a quicker local run")
    ap.add_argument("--keep", help="clone into this directory and leave the clones there")
    ap.add_argument("--jobs", type=int, default=12)
    ap.add_argument("--feed", help="also write an Atom feed of what needs attention, here")
    ap.add_argument("--sbom", help="also write the file as a CycloneDX SBOM with VEX, here")
    ap.add_argument("--actions", help="also write every project's actions, for an agent to follow, here")
    ap.add_argument("--no-reachability", action="store_true", help="skip govulncheck, for a quicker local run")
    ap.add_argument("--owner", help="the GitHub owner whose repositories to read; $GITHUB_REPOSITORY_OWNER, else the org")
    ap.add_argument("--site", help="where this site is published; the config's site when the owner is the org")
    args = ap.parse_args(argv)

    index, config = load(args.projects), load(args.config)
    # Whose repositories to read is a different question from whose packages are
    # siblings. A fork keeps the org's module paths, npm scope and image names, so
    # `org` still decides what is internal. The fork's own owner and site decide
    # which repositories, status files and links the run reads.
    org = config["org"]
    owner = args.owner or os.environ.get("GITHUB_REPOSITORY_OWNER") or org
    site = args.site or (config.get("site") if owner == org else None)
    if not site:
        log(f"no site given for {owner}, so no status file is read and links are relative")
    repository = os.environ.get("GITHUB_REPOSITORY") or f"{owner}/dashboards"
    problem = not_github(owner, repository)
    if problem:
        ap.error(problem)
    names =[p["name"] for p in index["projects"]] + [n for n in config.get("include", [])
                                                     if n not in {p["name"] for p in index["projects"]}]
    status_paths = {p["name"]: p.get("status") for p in index["projects"]}
    if args.only:
        wanted = set(args.only.split(","))
        names = [n for n in names if n in wanted]

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None
    gitrepo.use_token(token)
    client = Client()
    src = Sources(client, token=token)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    workdir = args.keep or tempfile.mkdtemp(prefix="deps-")
    os.makedirs(workdir, exist_ok=True)

    pins = config.get("pins", [])
    snapshots = config.get("snapshots", [])

    def fetch(name):
        extra = [p["path"] for p in pins if p["project"] == name] + \
                [s["built"]["path"] for s in snapshots if s["project"] == name]
        dest = os.path.join(workdir, name)
        shutil.rmtree(dest, ignore_errors=True)
        url = f"https://github.com/{owner}/{name}"
        try:
            return name, gitrepo.clone(url, dest, extra), None
        except gitrepo.GitError as exc:
            return name, None, str(exc)

    log(f"reading {len(names)} repositories")
    with ThreadPoolExecutor(max_workers=6) as pool:
        cloned = list(pool.map(fetch, names))
    repos = {n: r for n, r, _ in cloned if r}

    # Every project's status file, before any pin is resolved: a pin on a sibling is
    # measured against the last commit that sibling built, which its file names.
    def read_status(name):
        try:
            return name, client.json(urllib.parse.urljoin(site, status_paths[name]), accept_missing=True) or {}
        except FetchError:
            return name, {}
    statuses = {}
    if site:
        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = dict(pool.map(read_status, [n for n in repos if status_paths.get(n)]))
    built = {n: b for n, s in statuses.items() if (b := newest_built(s))}

    resolver = Resolver(src, org, repos, now, built)
    projects = []
    for name, repo, err in cloned:
        project = {"name": name, "repo": {"full_name": f"{owner}/{name}", "url": f"https://github.com/{owner}/{name}"}}
        if err:
            log(f"{name}: {err}")
            project.update(error=err, dependencies=[], manifests=[])
            projects.append(project)
            continue
        project["repo"].update(branch=repo.branch, sha=repo.sha, committed=iso(repo.committed), description="")
        project["repo"]["description"] = ((statuses.get(name) or {}).get("repo") or {}).get("description") or ""
        found = extract.repository(repo, org, [p for p in pins if p["project"] == name],
                                   [a for a in config.get("after", []) if a["project"] == name])
        deps = found["dependencies"]
        for u in found["unplaced"]:
            deps.append({"ecosystem": "native", "name": u["arg"], "version": u["version"], "scope": "build",
                         "internal": False, "sources": [{"path": u["path"], "line": u["line"]}],
                         "error": "no upstream recognised for this pin; declare one in deps-config.json"})
        deps += extract.lockfile_records(deps, found["installed"], org)
        project.update(dependencies=deps, installed=found["installed"], manifests=found["manifests"],
                       unmatched=found["unmatched"])
        projects.append(project)
        log(f"{name}: {len(deps)} declared in {len(found['manifests'])} files")

    todo = [d for p in projects for d in p["dependencies"]]
    log(f"resolving {len(todo)} records")
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(resolver.resolve, todo))

    live = [p for p in projects if not p.get("error")]
    previous = read_previous(args.previous, client)
    known_packages, known_fedora = known_facts(previous)
    resolver.enrich_lifecycles(live)
    resolver.link_compat(live)
    for label, step in (("package metadata", lambda ps: resolver.enrich_packages(ps, known_packages)),
                        ("repository signals", resolver.enrich_repos),
                        ("Fedora licenses", lambda ps: resolver.enrich_fedora_licenses(ps, known_fedora))):
        log(f"reading {label}")
        try:
            step(live)
        except FetchError as exc:
            log(f"{label} not read: {exc}")
            for p in live:
                p.setdefault("notes", []).append(f"{label} not read: {exc.reason}")

    log("looking up vulnerabilities")
    try:
        resolver.vulnerabilities([p for p in projects if not p.get("error")])
    except FetchError as exc:
        log(f"vulnerability lookup failed: {exc}")
        for p in projects:
            p.setdefault("notes", []).append(f"vulnerabilities not checked: {exc.reason}")

    # govulncheck builds every Go project: it says which advisories the code
    # reaches, and which modules end up in what the project ships.
    needs = [p for p in live if "go.mod" in p.get("manifests", [])]
    if needs and not args.no_reachability:
        try:
            tool = reach.build_tool(workdir)
            for p in needs:
                log(f"{p['name']}: govulncheck")
                try:
                    gitrepo.full_checkout(repos[p["name"]])
                    levels, aliases, built = reach.scan(tool, repos[p["name"]].path)
                    reach.annotate(p, levels, aliases, built)
                    p["reachability"] = "govulncheck"
                except reach.Unavailable as exc:
                    if "no packages matched" in str(exc):
                        reach.annotate(p, {}, {}, set(), no_code=True)  # tooling only: nothing ships
                    else:
                        p.setdefault("notes", []).append(f"reachability not checked: {exc}")
                except (gitrepo.GitError, OSError) as exc:
                    p.setdefault("notes", []).append(f"reachability not checked: {exc}")
        except reach.Unavailable as exc:
            log(f"reachability skipped: {exc}")
            for p in needs:
                p.setdefault("notes", []).append(f"reachability not checked: {exc}")

    for snap in snapshots:
        p = next((p for p in projects if p["name"] == snap["project"] and not p.get("error")), None)
        if p:
            try:
                resolver.snapshot(p, repos[p["name"]], snap)
            except FetchError as exc:
                p.setdefault("notes", []).append(f"Fedora security updates not checked: {exc.reason}")

    # What is known about each advisory beyond its severity, and whether the
    # release that fixes it is clean; then the licenses scans found in what ships.
    categories = {}
    for label, step in (("known exploited vulnerabilities", resolver.known_exploited),
                        ("exploit predictions", resolver.exploit_prediction),
                        ("fixed releases", resolver.check_fixes),
                        ("licenses found in package files", lambda ps: resolver.enrich_found_licenses(ps, policy.shipped)),
                        ("license categories", lambda ps: categories.update(src.license_categories()))):
        log(f"reading {label}")
        try:
            step(live)
        except FetchError as exc:
            log(f"{label} not read: {exc}")
            for p in live:
                p.setdefault("notes", []).append(f"{label} not read: {exc.reason}")

    lic_policy = config.get("licenses", {})
    sla_policy = config.get("sla", {})
    seen_before = (previous or {}).get("seen", {})
    seen = {}
    for p in projects:
        repo_signal = {}
        installed = {(i["name"], i["version"]): i for i in p.get("installed", [])}
        p.pop("installed", None)
        for d in p["dependencies"]:
            if d["ecosystem"] == "npm" and installed.get((d["name"], d.get("version")), {}).get("install_script"):
                d["install_script"] = True
            resolver.classify(d)
            if d["scope"] == "transitive" and d["status"] != "vulnerable":
                d.pop("via", None)  # kept where it can explain a fix, and the file stays small
            accept = policy.accepted(p["name"], d, config.get("accepted", []), now)
            if accept:
                d["accepted"] = accept
            lic = policy.license_verdict(d, lic_policy, categories)
            d.pop("licenses_found", None)
            d.pop("license_score", None)
            d.pop("license_url", None)
            if lic:
                d["license"] = lic
            d["purl"] = sbom.purl(d)
            sig = policy.signals(d, d.get("repo_signals"), now)
            if sig:
                d["signals"] = sig
            # A security or compliance gap records when it opened: an advisory's
            # date, an end of life, the first run that saw a license. A fix-by
            # date is added only when deps-config.json sets one for the level.
            if resolve_attention(d) and d["status"] in ("vulnerable", "eol", "eol-soon"):
                key = "|".join([p["name"], d["ecosystem"], d["name"], d["status"]])
                seen[key] = seen_before.get(key) or generated_now(now)
                opened = policy.since(d, seen, key, now)
                d["opened"] = iso(opened)
                window = policy.sla(d, opened, sla_policy, now)
                if window:
                    d["sla"] = window
            if (d.get("license") or {}).get("verdict") in ("denied", "review"):
                key = "|".join([p["name"], d["ecosystem"], d["name"], "license"])
                seen[key] = seen_before.get(key) or generated_now(now)
                d["license"]["opened"] = seen[key]
                level = "serious" if d["license"]["verdict"] == "denied" else "warning"
                window = policy.sla(dict(d, level=level), policy.parse_time(seen[key]), sla_policy, now)
                if window:
                    d["license"]["sla"] = window
        p["dependencies"].sort(key=lambda d: (d["scope"] in ("indirect", "transitive"), -level_rank(d["level"]),
                                              d["ecosystem"], d["name"]))
        p["summary"] = summarise(p)

    # The work the records call for, decided once: the page renders it and an
    # agent follows it. Items point at records by index, so this runs after the sort.
    live = [p for p in projects if not p.get("error")]
    log("reading what each planned npm move installs")
    try:
        resolver.cleared_by_moves(live)
    except FetchError as exc:
        log(f"dependency graphs not read: {exc}")
        for p in live:
            p.setdefault("notes", []).append(f"dependency graphs not read: {exc.reason}")
    actions.annotate_records(live)
    for p in live:
        p["_actions"] = actions.build([p])
        p["actions"] = actions.for_page(p["_actions"])
        p["summary"]["tiers"] = {t: sum(1 for a in p["_actions"] if a["tier"] == t) for t in actions.TIERS}
    org_actions = actions.for_page(actions.build(live))
    # When a run first called for each action the feed announces, carried forward
    # like every other first sighting, so the feed dates an entry by when it was new.
    for a in feed_actions(org_actions):
        key = "action|" + a["key"]
        seen[key] = seen_before.get(key) or generated_now(now)

    generated = iso(now)
    out = {
        "schema": SCHEMA,
        "generated": generated,
        "org": org,
        "owner": owner,
        "levels": list(LEVELS),
        "projects": sorted(projects, key=lambda p: p["name"]),
        "lifecycle": lifecycle_table(projects),
        "actions": org_actions,
        "history": history(previous, projects, generated),
        "seen": seen,
        "data_sources": catalog.entries(owner, site, args.previous),
        "sources": client.report(),
        "authenticated": bool(token),
    }
    agent = actions.agent_document(out, site, f"https://github.com/{repository}/blob/main/SPEC.md#the-actions-file",
                                   repository)
    for p in projects:
        p.pop("_actions", None)
    # Compact, and without the fields a record leaves empty: the file carries
    # every indirect dependency, and the page downloads it on every visit.
    text = json.dumps(_prune(out), separators=(",", ":")) + "\n"
    if args.output == "-":
        sys.stdout.write(text)
    else:
        with open(args.output, "w") as fh:
            fh.write(text)
        print(args.output)

    if args.feed:
        with open(args.feed, "w") as fh:
            fh.write(atom_feed(out, site))
    if args.actions:
        with open(args.actions, "w") as fh:
            json.dump(_prune(agent), fh, indent=1)
            fh.write("\n")
    if args.sbom:
        with open(args.sbom, "w") as fh:
            json.dump(sbom.cyclonedx(_prune(out), site), fh, separators=(",", ":"))
            fh.write("\n")

    if not args.keep:
        shutil.rmtree(workdir, ignore_errors=True)
    if not repos:
        log("no repository could be read")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
