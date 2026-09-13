"""License policy, supply-chain signals and service-level dates for resolved records.

None of this changes a record's status, which says how current it is. Each adds
evidence beside it: whether its license suits how the project uses it, what its
upstream's health looks like, and how long the gap it has has been open.
"""

import fnmatch
import re
from datetime import timedelta

from .sources import iso, parse_time

VERDICT_ORDER = ("allowed", "aggregate", "not-shipped", "unknown", "review", "denied")

# Scopes whose code does not reach what a project ships: tooling, CI, a
# development-only package. A license there binds nobody downstream.
NOT_SHIPPED = ("dev", "ci", "tool", "toolchain", "engines", "optional")


def spdx_ids(expression):
    return [t for t in re.findall(r"[A-Za-z0-9.+-]+", expression or "") if t not in ("AND", "OR", "WITH")]


def evaluate(expression, classify):
    """Grade an SPDX expression: AND takes the worst term, OR the best, WITH the pair or its license."""
    tokens = re.findall(r"\(|\)|[A-Za-z0-9.+:-]+", expression or "")
    pos = 0

    def worst(a, b):
        return a if VERDICT_ORDER.index(a) >= VERDICT_ORDER.index(b) else b

    def best(a, b):
        return a if VERDICT_ORDER.index(a) <= VERDICT_ORDER.index(b) else b

    def atom():
        nonlocal pos
        if pos >= len(tokens):
            return "unknown"
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            value = either()
            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1
            return value
        if pos < len(tokens) and tokens[pos] == "WITH":
            exception = tokens[pos + 1] if pos + 1 < len(tokens) else ""
            pos += 2
            # An exception can change what a license asks of the code that
            # links it, so a policy that names the pair decides it.
            graded = classify(f"{tok} WITH {exception}")
            return graded if graded != "unknown" else classify(tok)
        return classify(tok)

    def both():
        nonlocal pos
        value = atom()
        while pos < len(tokens) and tokens[pos] == "AND":
            pos += 1
            value = worst(value, atom())
        return value

    def either():
        nonlocal pos
        value = both()
        while pos < len(tokens) and tokens[pos] == "OR":
            pos += 1
            value = best(value, both())
        return value

    return either() if tokens else "unknown"


def shipped(dep):
    """Whether a record's code reaches what its project ships, as far as the collector can tell."""
    only_side_modfile = dep["ecosystem"] == "go" and all(
        re.search(r"(^|/)go\.[\w-]+\.mod$", s.get("path", "")) and not s.get("path", "").endswith("/go.mod")
        and s.get("path") != "go.mod" for s in dep.get("sources") or [{}])
    return dep.get("scope") not in NOT_SHIPPED and not dep.get("dev") and dep.get("in_build") is not False \
        and not only_side_modfile


def license_verdict(dep, policy, categories=None):
    """The license evidence for one record, or None when nothing is known to grade.

    The verdict depends on the policy, the declared license and whether the
    code ships, and on nothing fetched. `categories` maps a license id to its
    ScanCode LicenseDB category, shown beside a license the policy does not
    name as a hint for which list it belongs in.
    """
    if dep.get("internal"):
        return None  # the org's own code, under the org's own license
    expression = " AND ".join(f"({x})" if " " in x else x for x in dep.get("licenses") or [])
    ships = shipped(dep)
    aggregate = dep["ecosystem"] in tuple(policy.get("aggregate", ("package", "image", "native", "runtime")))

    def classify(license_id):
        # An exact entry is checked before a wildcard, so the policy can name
        # one license inside a family it otherwise denies. A wildcard naming an
        # exception matches only that pair, so GPL-* never swallows a pair.
        pair = " WITH " in license_id
        for matches in (lambda key: license_id in policy.get(key, []),
                        lambda key: any(fnmatch.fnmatchcase(license_id, p) for p in policy.get(key, []) if (" WITH " in p) == pair)):
            for key in ("deny", "review", "allow"):
                if matches(key):
                    return {"deny": "denied", "review": "review", "allow": "allowed"}[key]
        return "unknown"

    if not expression:
        if dep["ecosystem"] in ("go", "npm", "pypi", "package") and dep.get("version") is not None or dep["ecosystem"] == "package":
            return {"expression": None, "verdict": "unknown" if ships and not aggregate else "not-shipped" if not ships else "aggregate"}
        return None
    graded = evaluate(expression, classify)
    if not ships:
        verdict = "not-shipped"
    elif aggregate:
        # A separate program an image redistributes: its terms travel with it
        # and bind nothing in the project, unless the policy names it outright.
        verdict = "denied" if any(fnmatch.fnmatchcase(i, p) for i in spdx_ids(expression)
                                  for p in policy.get("deny_aggregate", [])) else "aggregate"
    else:
        verdict = graded
    out = {"expression": expression, "verdict": verdict}

    def unlisted(ids):
        return {i: (categories or {}).get(i, "") for i in ids if classify(i) == "unknown" and i not in _NO_LICENSE}

    if verdict == "unknown":
        names = unlisted(spdx_ids(expression))
        if names:
            out["unlisted"] = names
    if ships and not aggregate:
        # A scan can find code under another license than the one declared: a
        # bundled font, a vendored file. An expression that grades worse than
        # the declared one, as a whole, names what the policy flags or does not
        # name. ScanCode's own LicenseRef-scancode-* keys include loose matches,
        # such as a copyright line read as a proprietary license, so only SPDX
        # ids count.
        found = set()
        for expr in dep.get("licenses_found") or []:
            named = " ".join(t if t in ("AND", "OR", "WITH", "(", ")") or not t.startswith("LicenseRef-") else "NOASSERTION"
                             for t in re.findall(r"\(|\)|[A-Za-z0-9.+:-]+", expr))
            worse = evaluate(named, classify)
            if VERDICT_ORDER.index(worse) > VERDICT_ORDER.index(verdict):
                found.update(i for i in spdx_ids(named) if i not in _NO_LICENSE and classify(i) in ("unknown", "review", "denied"))
        if found:
            grades = [classify(i) for i in found]
            out["found"] = sorted(found)
            out["found_url"] = dep.get("license_url")
            out["found_verdict"] = max(grades, key=VERDICT_ORDER.index)
            names = unlisted(found)
            if names:
                out["unlisted"] = dict(out.get("unlisted", {}), **names)
    if dep.get("license_score") is not None:
        out["score"] = dep["license_score"]
    return out


# SPDX's words for a license nobody stated, which no policy grades.
_NO_LICENSE = ("NOASSERTION", "NONE")


def signals(dep, repo_info, now):
    """Supply-chain evidence from public sources: what a careful reviewer would want to know."""
    out = []
    if dep.get("deprecated") and dep.get("status") != "deprecated":
        out.append({"kind": "deprecated", "text": f"deprecated: {dep['deprecated']}"})
    if dep.get("install_script") and not dep.get("internal"):
        out.append({"kind": "install-script", "text": "runs a script when npm installs it"})
    sc = (repo_info or {}).get("scorecard") or {}
    checks = sc.get("checks") or {}
    latest = parse_time((dep.get("upstream") or {}).get("latest_date"))
    age = (now - latest).days if latest and not dep.get("internal") and dep["ecosystem"] in ("npm", "go", "pypi", "native") else 0
    stale = age > 365
    if age > 730 and checks.get("Maintained") == 0:
        # Chromium asks that an upstream has released within two years; Scorecard
        # finding no activity either is what separates abandoned from finished.
        out.append({"kind": "abandoned", "text": f"no release since {latest.date()} and no repository activity in 90 days"})
    elif stale:
        out.append({"kind": "stale", "text": f"no release since {latest.date()}"})
    elif checks.get("Maintained") == 0 and not dep.get("internal"):
        out.append({"kind": "quiet", "text": "no repository activity in 90 days (OpenSSF Scorecard)"})
    risky = [c for c in ("Dangerous-Workflow", "Binary-Artifacts", "Code-Review") if checks.get(c) == 0]
    if risky:
        out.append({"kind": "scorecard", "text": "OpenSSF Scorecard fails " + ", ".join(risky) +
                    (f" (overall {sc['score']}/10)" if sc.get("score") is not None else "")})
    if dep["ecosystem"] == "actions" and not dep.get("pinned_sha") and not dep.get("internal") \
            and not dep["name"].startswith(("actions/", "github/")):
        out.append({"kind": "unpinned-action", "text": "a third-party action pinned by a tag its owner can move"})
    return out


def since(dep, first_seen, key, now):
    """When the gap a record has began: an advisory's date, an end of life, a newer release.

    Where nothing public dates it, the collector's own first sighting does,
    carried forward from the previously published file.
    """
    status = dep.get("status")
    dates = []
    if status == "vulnerable":
        dates = [parse_time(v.get("published")) for v in dep.get("vulnerabilities") or []]
    elif status in ("eol", "eol-soon") and dep.get("lifecycle"):
        dates = [parse_time(dep["lifecycle"].get("eol"))]
    elif status in ("major", "minor", "patch"):
        dates = [parse_time((dep.get("upstream") or {}).get("latest_date"))]
    dates = [d for d in dates if d and d <= now]
    if dates:
        return min(dates)
    return parse_time(first_seen.get(key)) or now


def sla(dep, start, policy, now):
    days = (policy or {}).get(dep.get("level"))
    if not days:
        return None
    due = start + timedelta(days=days)
    left = (due - now).total_seconds() / 86400
    state = "breached" if left < 0 else "due-soon" if left <= max(3, days * 0.25) else "within"
    return {"since": iso(start), "due": iso(due), "days": days, "state": state}


REASONS = ("fix_started", "inaccurate", "no_bandwidth", "not_used", "tolerable_risk")


def accepted(project, dep, rules, now):
    """The acceptance a record falls under, if any: a decision made in deps-config.json.

    A rule names a dependency, optionally a project, version and finding, with a
    reason from Dependabot's dismissal vocabulary and a date it lapses on. A
    lapsed rule no longer hides anything, and says so.
    """
    for rule in rules:
        if rule.get("name") != dep["name"]:
            continue
        if rule.get("project", "*") not in ("*", project):
            continue
        if rule.get("version") and rule["version"] != dep.get("version"):
            continue
        finding = rule.get("finding")
        ids = {v["id"] for v in dep.get("vulnerabilities") or []} | {a for v in dep.get("vulnerabilities") or [] for a in v.get("aliases", [])}
        if finding and finding not in ids and finding != dep.get("status") and finding != "license":
            continue
        until = parse_time(rule.get("until"))
        return {"reason": rule.get("reason") if rule.get("reason") in REASONS else "tolerable_risk",
                "note": rule.get("note"), "until": iso(until) if until else None,
                "finding": finding, "lapsed": bool(until and until < now)}
    return None
