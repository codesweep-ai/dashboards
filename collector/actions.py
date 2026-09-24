"""Turn records into actions: the changes to make, grouped, ordered and described.

A record says what is true of one dependency. An action says what to do about
it: one change, across every record it moves, with the steps that make it.
The page renders actions and an agent follows them, so both read the same
decisions from the file rather than re-deriving them.
"""

import posixpath
import re
from datetime import datetime

from .resolve import level_rank

TIERS = ("fix", "plan", "routine")
TIER_UP = {"routine": "plan", "plan": "fix", "fix": "fix"}
INDIRECT = ("indirect", "transitive")

PRODUCT = {
    "go": "Go", "nodejs": "Node.js", "python": "Python", "eclipse-temurin": "Java (Temurin)", "apache-maven": "Maven",
    "fedora": "Fedora", "debian": "Debian", "ubuntu": "Ubuntu", "alpine": "Alpine", "linux": "Linux kernel",
    "windows-server": "Windows Server", "macos": "macOS", "firecracker": "Firecracker", "react": "React",
    "eslint": "ESLint", "nvm": "nvm",
}
ECO_LABEL = {
    "runtime": "Toolchains", "image": "Images & runners", "native": "Native & binaries", "package": "System packages",
    "go": "Go modules", "npm": "npm packages", "pypi": "Python packages", "actions": "GitHub Actions",
}
SIGNAL_ORDER = ("deprecated", "stale", "quiet", "scorecard", "install-script", "unpinned-action")
SIGNAL_ONE = {"deprecated": " is deprecated: ", "stale": " has had ", "quiet": " has ", "scorecard": ": ",
              "install-script": " ", "unpinned-action": " is "}
SIGNAL_MANY = {
    "deprecated": "are deprecated", "stale": "have no release in a year",
    "quiet": "show no repository activity in 90 days (OpenSSF Scorecard)",
    "scorecard": "fail OpenSSF Scorecard checks", "install-script": "run a script when npm installs them",
    "unpinned-action": "are third-party actions pinned by a tag their owner can move",
}


# --- small helpers -------------------------------------------------------------------


def _uniq(xs):
    return list(dict.fromkeys(xs))


def _plural(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def slug(s):
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", str(s).lower()))[:90]


def name(d):
    return d.get("label") or d["name"]


def src_path(d):
    return ((d.get("sources") or [{}])[0] or {}).get("path") or ""


def dir_of(path):
    return "." if "/" not in path else path.rsplit("/", 1)[0]


def in_dir(path, cmd):
    return cmd if dir_of(path) == "." else f"cd {dir_of(path)} && {cmd}"


def _ts(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def pin_commit(lag):
    """The commit a pin on a sibling moves to: the sibling's last passing build, as its
    status file names it. A sibling that names none holds the pin, so nothing moves it."""
    return lag.get("built")


def target(d):
    """The version a record's newest release, or its fix, names."""
    up = d.get("upstream") or {}
    if (d.get("lag") or {}).get("head"):
        return d["lag"].get("version") or (pin_commit(d["lag"]) or "")[:7] or None
    if d.get("status") == "vulnerable" and d.get("fix") and not up.get("latest"):
        return d["fix"]
    return up.get("latest") or d.get("fix")


def move_to(d, items=()):
    """The version one change moves a record to. A security fix keeps the rest of its family on their line."""
    compat = d.get("compat") or {}
    if compat and not compat.get("ok") and (compat.get("target") or {}).get("line"):
        return compat["target"]["line"] + " line"
    if d.get("status") == "vulnerable" and d.get("fix"):
        return d["fix"]
    hosts = [i for i in items if i["dep"].get("status") == "vulnerable" and i["dep"].get("fix") and not i["reason"]]
    if hosts and d["ecosystem"] == "npm":
        # A sibling released in lockstep with its host moves to the host's fix.
        twin = next((i for i in hosts if i["dep"].get("version") == d.get("version")), None)
        return twin["dep"]["fix"] if twin else (d.get("upstream") or {}).get("line_latest")
    return target(d)


def accepted_now(d):
    return bool(d.get("accepted")) and not d["accepted"].get("lapsed")


def attention(d):
    """Whether a record is work: never while accepted, and for an indirect record only when vulnerable."""
    if accepted_now(d):
        return False
    if d.get("scope") in INDIRECT:
        return d.get("status") == "vulnerable"
    return level_rank(d.get("level")) >= level_rank("info")


def exploited(d):
    return any(v.get("exploited") for v in d.get("vulnerabilities") or [])


def epss(d):
    return max([0] + [(v.get("epss") or {}).get("probability") or 0 for v in d.get("vulnerabilities") or []])


def tier_of(d, reason=None):
    """When an item's work belongs: urgency only. A passed fix-by date moves it up one rung."""
    window = d.get("sla")
    if reason == "license":
        t = "plan" if d["license"]["verdict"] == "denied" else "routine"
        window = d["license"].get("sla")
    elif reason in ("abandoned",):
        t = "plan"
    elif reason == "unlisted":
        t = "routine"
    elif d.get("status") == "behind":
        t = "plan" if level_rank(d.get("level")) >= level_rank("serious") else "routine"
    else:
        t = {"critical": "fix", "serious": "fix", "warning": "plan", "info": "routine"}.get(d.get("level"), "routine")
    return TIER_UP[t] if window and window.get("state") == "breached" else t


def family_of(p, d):
    """Which dependencies one change moves together: react with react-dom and its types."""
    repo = d.get("upstream_repo") or ""
    if d["ecosystem"] == "npm" and d["name"].startswith("@types/"):
        base = d["name"][7:].replace("__", "/", 1)
        base = re.sub(r"^([^/]+)/(.+)$", r"@\1/\2", base)
        owner = next((x for x in p["dependencies"] if x["ecosystem"] == "npm" and x["name"] == base), None)
        return owner["upstream_repo"] if owner and owner.get("upstream_repo") else "npm:" + base
    if repo and "definitelytyped" not in repo.lower() and d["ecosystem"] in ("npm", "go"):
        return re.sub(r"/v\d+$", "", repo)
    return f"{d['ecosystem']}:{d['name']}"


def action_key(p, d):
    if d.get("status") == "vulnerable" and d.get("scope") == "transitive":
        return f"lock|{p['name']}|{src_path(d)}"
    if d.get("status") == "vulnerable" and d["ecosystem"] == "package":
        return f"rebuild|{p['name']}|{src_path(d)}"
    if d.get("status") == "behind":
        return f"sync|{d.get('provider') or ''}|{d['ecosystem']}|{d['name']}"
    if d.get("status") in ("eol", "eol-soon") and d.get("lifecycle"):
        lc = d["lifecycle"]
        return f"eol|{lc['product']}|{lc['cycle']}" + ("|engines" if d.get("scope") == "engines" else "")
    if d["ecosystem"] == "actions":
        return "actions"
    return "dep|" + family_of(p, d)


# --- steps: the edit or command that makes one record's change -------------------------

GO_MODFILE = re.compile(r"(^|/)go(\.[\w-]+)?\.mod$")


def _modfile_flag(path):
    """`-modfile=go.golangci.mod ` for a module file other than go.mod: go's commands read go.mod unless told."""
    base = path.rsplit("/", 1)[-1]
    return "" if base == "go.mod" else f"-modfile={base} "


def _set_at(d, s, v):
    """The edit that sets a version where no command maintains it, such as a Containerfile ARG."""
    what = s.get("arg") or d.get("arg")
    return {"edit": s["path"], "line": s.get("line"),
            "text": f"set {what or 'the version'} to {v}" + (", with any checksum beside it" if what else ""),
            "from": d.get("version"), "to": v}


def _go_get(d, s, v):
    """`go get` in one module file: the commands it names as tools, or else the module, at `v`.

    `-tool` with the module path would add the module as a second tool line.
    A module file beside go.mod holds one tool's requirements and none of the
    directory's packages, which tidy would pull in, so only go.mod is tidied.
    """
    tools = s.get("tools")
    what = " ".join(f"{x}@{v}" for x in tools) if tools else f"{d['name']}@{v}"
    flag = _modfile_flag(s["path"])
    return {"run": f"go get {flag}{'-tool ' if tools else ''}{what}" + ("" if flag else " && go mod tidy"),
            "cwd": dir_of(s["path"])}


def _each_place(d, sources, in_modfile, v):
    """A step for every place a Go pin is written: `in_modfile(source)` in a module file, an edit anywhere else."""
    steps = []
    for s in sources:
        step = in_modfile(s) if GO_MODFILE.search(s["path"]) else _set_at(d, s, v)
        if step not in steps:
            steps.append(step)
    return steps


def _npm_places(d, sources, cmd, v, via=None):
    """`cmd` in each directory whose manifest or lockfile holds the package, and an edit anywhere else it is written.

    `via` is a script, by its path in the project, that the command runs under."""
    steps = []
    for s in sources:
        manifest = s["path"].rsplit("/", 1)[-1] in ("package.json", "package-lock.json")
        cwd = dir_of(s["path"])
        run = f"{posixpath.relpath(via, cwd)} {cmd}" if via else cmd
        step = {"run": run, "cwd": cwd} if manifest else _set_at(d, s, v)
        if step not in steps:
            steps.append(step)
    return steps


def steps_for(d, to=None):
    """What makes one record's change, then any steps deps-config.json says the move needs after it."""
    own = _own_steps(d, to)
    return own + [s for s in d.get("after") or [] if s not in own] if own else own


def _own_steps(d, to=None):
    """What makes one record's change, as structured steps: a command to run, an edit to make, or a task to do."""
    t = to if to is not None else target(d)
    path = src_path(d)
    sources = [s for s in d.get("sources") or [] if s.get("path")] or [{"path": path, "line": None}]

    # An edit is made at every place the pin is written, and a command runs
    # once in every directory holding a manifest that declares it.
    def edit(text, **kw):
        return [dict({"edit": s["path"], "line": s.get("line"), "text": text}, **kw) for s in sources]

    def run(cmd, cwd=None):
        dirs = [cwd] if cwd is not None else _uniq(dir_of(s["path"]) for s in sources)
        return [{"run": cmd, "cwd": x} for x in dirs]
    if d.get("status") == "behind":
        head = pin_commit(d.get("lag") or {})
        if d["ecosystem"] == "go" and head:
            # The commit itself, in this pin alone: a repin target moves every sibling to main.
            return _each_place(d, sources, lambda s: _go_get(d, s, head), head)
        if d["ecosystem"] == "npm" and head:
            built = d["lag"].get("version")
            # A version only the build's image holds installs through cs-npmrevs
            # alone, so a project without its script waits for npmjs.com.
            if not built or (d["lag"].get("image_only") and not d.get("installer")):
                return [{"do": f"Install the build of {d['name']} made from commit {head[:12]} once the registry holds it: "
                               f"`npm view {d['name']} versions --json` lists them."}]
            return _npm_places(d, sources, f"npm install --save-exact {'-D ' if d.get('scope') == 'dev' else ''}{d['name']}@{built}",
                               built, d.get("installer"))
        if d["ecosystem"] == "actions":
            return edit(f"uses: {d['name']}@{head or '<newest commit>'}", **{"from": (d.get("lag") or {}).get("pinned"), "to": head})
        if d["ecosystem"] == "image":
            return edit(f"{d.get('variable') or 'the image reference'}={t or '<newest tag>'}", **{"from": d.get("version"), "to": t})
    if d.get("scope") == "engines":
        return edit("raise engines.node", **{"from": d.get("constraint") or d.get("version")})
    if d["ecosystem"] == "runtime" and d["name"] == "go" and t and any(GO_MODFILE.search(s["path"]) for s in sources):
        directive = f"-toolchain=go{t}" if d.get("scope") == "toolchain" else f"-go={t}"
        return _each_place(d, sources, lambda s: {"run": f"go mod edit {_modfile_flag(s['path'])}{directive}",
                                                  "cwd": dir_of(s["path"])}, t)
    compat = d.get("compat") or {}
    if compat and not compat.get("ok"):
        return compat_steps(d, compat, edit)
    if d.get("declared") or d.get("arg"):
        what = d.get("arg") or "the version"
        return edit(f"set {what} to {t or 'the newest release'}" + (", with any checksum beside it" if d.get("arg") else ""),
                    **{"from": d.get("version"), "to": t})
    if d["ecosystem"] == "go" and t:
        version = d.get("fix") or t
        return _each_place(d, sources, lambda s: _go_get(d, s, version), version)
    if d["ecosystem"] == "npm" and t:
        version = d["fix"] if d.get("fix") and d.get("status") == "vulnerable" else t
        return _npm_places(d, sources, f"npm install {'-D ' if d.get('scope') == 'dev' else ''}{d['name']}@{version}", version)
    if d["ecosystem"] == "pypi" and t:
        return run(f"pip install {d['name']}=={t}")
    if d["ecosystem"] == "actions" and t:
        return edit(f"uses: {d['name']}@v{str(t).split('.')[0]}", **{"from": d.get("version"), "to": f"v{str(t).split('.')[0]}"})
    if d["ecosystem"] == "image" and d.get("datasource") == "runner":
        return edit("runs-on: <a newer label>", **{"from": d.get("version"), "to": t})
    if t:
        return edit(f"move to {t}", **{"from": d.get("version"), "to": t})
    return []


def how_text(steps, places=1):
    """One line for the page: the first step, or its general form when a card spans several places."""
    if not steps:
        return None
    s = steps[0]
    if "run" in s:
        if places > 1:
            return s["run"] + "   in each place listed"
        return s["run"] if s.get("cwd", ".") == "." else f"cd {s['cwd']} && {s['run']}"
    if "edit" in s:
        text = s["text"]
        suffix = ", with any checksum beside it"
        tail = suffix if text.endswith(suffix) else ""
        text = text[: -len(suffix)] if tail else text
        if places > 1:
            return text + tail + "   in each place listed"
        where = s["edit"] + (f":{s['line']}" if s.get("line") else "")
        return f"{text}   at {where}{tail}"
    return s.get("do")


def compat_text(c):
    """Why a pin fails its compatibility check, in the few words a card has room for."""
    listed = ", ".join(c.get("validated") or []) or "none"
    target = c.get("target") or {}
    bits = [f"{c.get('with')} validates {listed}"]
    if target:
        bits.append(f"{target['line']} is {'LTS' if target.get('lts') else 'supported'}" +
                    (f" to {target['eol']}" if target.get("eol") else ""))
    dist = c.get("distribution") or {}
    if target and dist and not dist.get("has_target"):
        bits.append(f"{dist['name']} ships only {dist['line']}")
    return " · ".join(bits)


def compat_steps(d, compat, edit):
    """The steps that move a pin onto a compatible line: what it depends on, where the build comes from, the edit."""
    target = compat.get("target") or {}
    dist = compat.get("distribution") or {}
    fc = compat.get("firecracker") or {}
    steps = []
    if fc and not fc.get("ok"):
        steps.append({"do": f"Upgrade {compat['with']} to {fc['needs']}+ (pinned {fc['pinned']})"})
    if not target:
        return steps + edit(f"set the version to a line {compat['with']} validates", **{"from": d.get("version")})
    if dist.get("has_target"):
        return steps + edit(f"set the version to {dist['latest']}", **{"from": d.get("version"), "to": dist["latest"]})
    steps.append({"do": f"Pick a {target['line']} kernel from the options"})
    return steps + edit(f"set the guest kernel to it", **{"from": d.get("version")})


def compat_options(compat):
    """The choices a person makes when no build on the compatible line comes from where the pin does."""
    target = compat.get("target") or {}
    dist = compat.get("distribution") or {}
    if compat.get("ok") or not target or not dist or dist.get("has_target"):
        return None
    t, w = target["line"], compat["with"]
    latest = target.get("latest") or t
    options = [{"recommended": True, "url": compat.get("url"),
                "text": f"{t}{' LTS' if target.get('lts') else ''} microVM kernel: Amazon Linux microvm-kernel-{t}, "
                        f"or kernel.org {latest} with {w}'s guest config"}]
    if dist.get("latest"):
        options.append({"text": f"{dist['name']} kernel {dist['latest']}: patched, not validated by {w}"})
    options.append({"text": "Keep it until the next base image, as an accepted exception"})
    return options


def product_name(lc):
    return f"{PRODUCT.get(lc['product'], lc['product'])} {lc['cycle']}"


def _sla_rank(w):
    return 0 if not w else 3 if w.get("state") == "breached" else 2 if w.get("state") == "due-soon" else 1


# --- building and describing ----------------------------------------------------------


def build(projects):
    """Every action across `projects`, most urgent first. Items point at records by index."""
    groups = {}
    order = []

    def add(key, p, i, d, reason=None):
        if key not in groups:
            groups[key] = {"key": key, "items": []}
            order.append(key)
        groups[key]["items"].append({"project": p, "index": i, "dep": d, "reason": reason, "tier": tier_of(d, reason)})

    for p in projects:
        for i, d in enumerate(p["dependencies"]):
            if attention(d):
                add(action_key(p, d), p, i, d)
            verdict = (d.get("license") or {}).get("verdict")
            if verdict in ("denied", "review") and not accepted_now(d):
                add(f"license|{verdict}|{d['name']}", p, i, d, "license")
            elif verdict == "unknown" and d.get("scope") not in INDIRECT and not accepted_now(d):
                add(f"unlisted|{d['name']}", p, i, d, "unlisted")
            if d.get("scope") not in INDIRECT and not accepted_now(d) and any(
                    s.get("kind") == "abandoned" for s in d.get("signals") or []):
                add(f"replace|{d['name']}", p, i, d, "abandoned")

    out = []
    for key in list(order):
        a = groups[key]
        if not a["items"]:
            continue
        a["tier"] = min((it["tier"] for it in a["items"]), key=TIERS.index)
        first = a["items"][0]
        # Majors in one project's dev tooling are one afternoon in that project.
        dev_only = key.startswith("dep|") and a["tier"] == "plan" and all(
            it["dep"].get("scope") == "dev" and it["dep"].get("status") != "vulnerable" and it["project"] is first["project"]
            and src_path(it["dep"]) == src_path(first["dep"]) for it in a["items"])
        # A family whose every item is routine folds into one card per kind.
        fold = (f"devtools|{first['project']['name']}|{src_path(first['dep'])}" if dev_only
                else f"routine|{first['dep']['ecosystem']}" if a["tier"] == "routine" and key.startswith("dep|") else None)
        if fold:
            if fold not in groups:
                groups[fold] = {"key": fold, "items": []}
            f = groups[fold]
            f["items"] += a["items"]
            f["tier"] = "plan" if dev_only else "routine"
            a["items"] = []
            if f not in out:
                out.append(f)
            continue
        if a not in out:
            out.append(a)
    fold_cleared(out)
    out = [describe(a) for a in out if a["items"]]
    link_prerequisites(out)
    out.sort(key=_sort_key)
    return out


def fold_cleared(groups):
    """Move a lockfile item into the upgrade that clears it, so a refresh with nothing left to do is not listed.

    resolve.py names, in a record's `cleared_by`, the declared dependencies
    whose move drops every affected copy, and the version it read each at. The
    item joins the one action that makes every one of those moves, at those
    versions, and keeps its advisories and its done_when there.
    """
    for g in groups:
        if not g["key"].startswith("lock|"):
            continue
        for it in list(g["items"]):
            wanted = it["dep"].get("cleared_by") or []
            homes = [_mover(groups, it, m) for m in wanted]
            if not homes or None in homes or any(h is not homes[0] for h in homes):
                continue
            g["items"].remove(it)
            homes[0]["items"].append(dict(it, cleared_by=[m["name"] for m in wanted]))
            homes[0]["tier"] = min(homes[0]["tier"], it["tier"], key=TIERS.index)


def _mover(groups, it, m):
    """The upgrade action that moves `m["name"]` to `m["version"]` in the lockfile `it` sits in."""
    for h in groups:
        if h["key"].startswith("dep|") and any(
                x["project"] is it["project"] and not x["reason"] and not x.get("cleared_by")
                and x["dep"]["name"] == m["name"] and x["dep"].get("lockfile") == src_path(it["dep"])
                and move_to(x["dep"], h["items"]) == m["version"] for x in h["items"]):
            return h
    return None


def link_prerequisites(actions):
    """Point an action at the ones to make first: a guest kernel's move at its Firecracker upgrade.

    The kernel's action already says so in its steps. This names the other
    action, and tells that one what depends on it.
    """
    owner = {}
    for a in actions:
        for it in a["items"]:
            if not it["reason"]:
                owner.setdefault((id(it["project"]), it["dep"]["name"]), a)
    for a in actions:
        for it in a["items"]:
            compat = it["dep"].get("compat") or {}
            fc = compat.get("firecracker") or {}
            other = owner.get((id(it["project"]), fc.get("name"))) if fc and not fc.get("ok") else None
            if not other or other is a:
                continue
            if other["key"] not in a.setdefault("requires", []):
                a["requires"].append(other["key"])
            note = f"the {(compat.get('target') or {}).get('line')} guest kernel needs {fc['needs']}+"
            if note not in other["evidence"]:
                other["evidence"].append(note)


def _sort_key(a):
    due = _ts((a.get("sla") or {}).get("due")) if a.get("sla") else 0
    opened = _ts(a.get("opened"))
    return (TIERS.index(a["tier"]), -_sla_rank(a.get("sla")), due or 0, -level_rank(a["level"]),
            -int(a["exploited"]), -a["epss"], opened if opened is not None else float("inf"), -len(a["projects"]), a["title"].casefold())


def describe(a):
    items = a["items"]
    # An item another move clears rides along: it names nothing and has no step.
    items.sort(key=lambda it: (bool(it.get("cleared_by")), -level_rank(it["dep"].get("level")), it["project"]["name"]))
    own = [it for it in items if not it.get("cleared_by")]
    first, p0 = items[0]["dep"], items[0]["project"]
    kind = a["key"].split("|")[0]
    a["kind"] = kind
    a["id"] = "action-" + slug(a["key"])
    a["level"] = max((it["dep"].get("level") or "idle" for it in items), key=level_rank)
    a["status"] = first.get("status")
    a["projects"] = _uniq(it["project"]["name"] for it in items)
    slas = [(it["dep"].get("license") or {}).get("sla") if it["reason"] == "license" else it["dep"].get("sla") for it in items]
    slas = sorted((w for w in slas if w), key=lambda w: (-_sla_rank(w), _ts(w.get("due")) or 0))
    a["sla"] = slas[0] if slas else None
    opened = sorted(filter(None, ((it["dep"].get("license") or {}).get("opened") if it["reason"] == "license"
                                  else it["dep"].get("opened") for it in items)))
    a["opened"] = opened[0] if opened else None
    a["exploited"] = any(not it["reason"] and exploited(it["dep"]) for it in items)
    a["epss"] = max(0 if it["reason"] else epss(it["dep"]) for it in items)
    a["ends"] = (first.get("lifecycle") or {}).get("eol") if all(
        it["dep"].get("status") == "eol-soon" and not it["reason"] for it in items) else None
    for it in items:
        it["to"] = None if it["reason"] else move_to(it["dep"], own)
        it["steps"] = [] if it["reason"] or it.get("cleared_by") else steps_for(it["dep"], it["to"] if kind not in ("eol", "sync") else None)

    names = _uniq(name(it["dep"]) for it in own)
    fam = sorted(names, key=len)[0]
    tgt = next((target(it["dep"]) for it in own if target(it["dep"])), None)
    where = f"in {a['projects'][0]}" if len(a["projects"]) == 1 else f"in {len(a['projects'])} projects"
    advisories = _uniq(v["id"] for it in items for v in it["dep"].get("vulnerabilities") or [])
    called = _uniq(v["id"] for it in items for v in it["dep"].get("vulnerabilities") or [] if v.get("reachable") == "called")
    analysed = any(it["dep"].get("reachability") for it in items)
    a["type"] = ("security" if kind in ("lock", "rebuild") or any(it["dep"].get("status") == "vulnerable" and not it["reason"] for it in items)
                 else "eol" if kind == "eol" else "license" if kind in ("license", "unlisted")
                 else "supply" if kind == "replace" or first.get("status") == "deprecated"
                 else "sync" if kind == "sync" else "update")
    fixes = ""
    if advisories:
        fixes = "Fixes " + _plural(len(advisories), "advisory", "advisories")
        if analysed:
            fixes += f" ({len(called)} called)" if called else " (none called)"
    places = len(_uniq(f"{it['project']['name']}:{src_path(it['dep'])}" for it in own))
    a["released"] = None
    how, steps = None, None

    if kind == "lock":
        d = dir_of(src_path(first))
        a["title"] = f"Refresh the lockfile in {p0['name']}" + (f"/{d}" if d != "." else "")
        worst = next((v.get("severity") for v in ((it["dep"].get("vulnerabilities") or [{}])[0] for it in items) if v.get("severity")), "unrated")
        a["result"] = fixes
        a["why"] = f"{_plural(len(items), 'installed package')}, worst {worst}" + (", all dev only" if all(it["dep"].get("dev") for it in items) else "")
        steps = [{"run": "npm audit fix", "cwd": d}]
    elif kind == "devtools":
        wd = p0["name"] + (f"/{dir_of(src_path(first))}" if dir_of(src_path(first)) != "." else "")
        a["title"] = f"Upgrade {len(items)} dev tools in {wd}" if len(items) > 1 else f"Upgrade {fam} to {tgt} in {wd}"
        a["result"] = f"Moves {_plural(len(items), 'dev dependency', 'dev dependencies')} to their newest major"
        a["why"] = "none of them ships"
        steps = [{"run": "npm install -D " + " ".join(f"{it['dep']['name']}@{target(it['dep'])}" for it in items),
                  "cwd": dir_of(src_path(first))}]
    elif kind == "rebuild":
        a["title"] = f"Rebuild {p0['name']}'s base image"
        a["result"] = "Picks up " + _plural(len(items), "Fedora security update")
        a["why"] = "shipped after the image was built" + (f" on {first['snapshot'][:10]}" if first.get("snapshot") else "")
        steps = [{"do": f"Rebuild the image that {src_path(first)} describes, then point the pinned tag at the new build."}]
    elif kind == "sync":
        a["title"] = f"Repin {fam} {where}"
        lags = [it["dep"].get("lag") or {} for it in items]
        most = max((l["commits_touching"] if l.get("commits_touching") is not None else (l.get("commits") or l.get("builds") or 0)) for l in lags)
        a["result"] = "Picks up up to " + _plural(most, "newer build" if lags[0].get("builds") is not None else "commit") + \
            (f" from {first['provider']}" if first.get("provider") else "")
        a["why"] = ""
    elif kind == "eol":
        lc = first["lifecycle"]
        a["title"] = (f"Raise the Node.js engines floor past {lc['cycle']} {where}" if first.get("scope") == "engines"
                      else ("Move off " if first.get("status") == "eol" else "Plan the move off ") + f"{product_name(lc)} {where}")
        a["result"] = product_name(lc) + (" ended " if first.get("status") == "eol" else " is guaranteed only until " if lc.get("eol_is_floor")
                                          else " ends ") + str(lc.get("eol") or "")[:10]
        compat = first.get("compat") or {}
        a["why"] = compat_text(compat) if compat and not compat.get("ok") else ""
        a["options"] = compat_options(compat) if compat else None
    elif kind == "actions":
        a["title"] = f"Update {_plural(len(_uniq(it['dep']['name'] for it in items)), 'GitHub Action')} {where}"
        a["result"] = "One commit per project moves every uses: line"
        a["why"] = ""
    elif kind == "routine":
        label = ECO_LABEL[first["ecosystem"]]
        n = len(_uniq(it["dep"]["name"] for it in items))
        a["title"] = f"Bump {_plural(n, re.sub(r's$', '', label).lower(), label.lower())} {where}"
        a["result"] = "Newer minor and patch releases"
        a["why"] = "none of them urgent"
    elif kind == "license":
        expr = "; ".join(_uniq(it["dep"]["license"].get("expression") or "no license found" for it in items))
        denied = first["license"]["verdict"] == "denied"
        a["title"] = ("Replace " if denied else "Review the license of ") + f"{fam} {where}"
        a["result"] = ("Removes a license the policy denies: " if denied else "Checks a license the policy flags for review: ") + expr
        a["why"] = "shipped in what the project builds"
        steps = [{"do": "find an alternative, or record an exception in deps-config.json with its reason" if denied
                  else "read the license terms, then allow it in deps-config.json or replace the package"}]
    elif kind == "unlisted":
        ids = _uniq(i for it in items for i in (it["dep"]["license"].get("unlisted") or {}))
        hints = {i: c for it in items for i, c in (it["dep"]["license"].get("unlisted") or {}).items() if c}
        if ids:
            a["title"] = f"Add {', '.join(ids)} to the license policy"
            a["result"] = f"Grades the license of {fam} {where}, which no entry in the policy names"
            a["why"] = "; ".join(f"ScanCode LicenseDB calls {i} {c}" for i, c in hints.items())
            steps = [{"do": f"add {', '.join(ids)} to allow, review or deny under licenses in deps-config.json"}]
        else:
            a["title"] = f"Find the license of {fam} {where}"
            a["result"] = "Grades a shipped dependency that states no license"
            a["why"] = ""
            steps = [{"do": "find the license in the package's source, and record it in deps-config.json or replace the package"}]
    elif kind == "replace":
        a["title"] = f"Replace {fam} {where}"
        a["result"] = "Moves off a package with no maintainer in sight"
        a["why"] = next((s["text"] for s in first.get("signals") or [] if s.get("kind") == "abandoned"), "")
        steps = [{"do": "find a maintained alternative, or vendor it and own it"}]
    else:
        vulnerable = [it for it in own if it["dep"].get("status") == "vulnerable"]
        if vulnerable:
            fix = next((it["dep"]["fix"] for it in vulnerable if it["dep"].get("fix")), None)
            a["title"] = f"Upgrade {fam}" + (f" to {fix}" if fix else "") + f" {where}"
            a["result"] = fixes
            a["why"] = ""
        elif first.get("status") == "major":
            a["title"] = f"Upgrade {fam}" + (f" to {tgt}" if tgt else "") + f" {where}"
            a["result"] = f"Moves {len(names)} packages that release together" if len(names) > 1 else "Moves to the newest major"
            a["why"] = ""
            if (first.get("upstream") or {}).get("latest_date"):
                a["released"] = {"version": tgt, "date": first["upstream"]["latest_date"]}
        elif first.get("status") == "deprecated":
            a["title"] = f"Replace {fam} {where}"
            a["result"] = "Moves off a deprecated package"
            a["why"] = first.get("deprecated") or ""
        else:
            a["title"] = f"Upgrade {fam}" + (f" to {tgt}" if tgt else "") + f" {where}"
            a["result"] = "A newer " + ("patch" if first.get("status") == "patch" else "minor") + " release"
            a["why"] = ""
            if (first.get("upstream") or {}).get("latest_date"):
                a["released"] = {"version": None, "date": first["upstream"]["latest_date"]}
        if len(names) > 1 and all(it["dep"]["ecosystem"] == "npm" for it in own):
            steps = _npm_install_all(own)

    if steps is None and kind in ("actions", "routine"):
        # Several different changes on one card: its table says what each moves
        # to, and each item carries its own steps.
        how = None
        a["steps"] = None
    elif steps is None:
        # Each item's own step, and one line for the card: the first, in its
        # general form when the change repeats across places.
        how = how_text(items[0]["steps"], places)
        a["steps"] = None
    else:
        a["steps"] = steps
        how = how_text(steps, 1 if kind in ("lock", "devtools") else places)
    a["how"] = how
    a["evidence"] = evidence_of(a)
    return a


def _npm_install_all(items):
    """Packages that release together install in one command, dev ones with -D."""
    def specs(dev):
        return _uniq(f"{it['dep']['name']}@{move_to(it['dep'], items)}" for it in items
                     if (it["dep"].get("scope") == "dev") == dev and move_to(it["dep"], items))
    prod, dev = specs(False), specs(True)
    cmd = " && ".join(x for x in (("npm install " + " ".join(prod)) if prod else "", ("npm install -D " + " ".join(dev)) if dev else "") if x)
    return [{"run": cmd, "cwd": dir_of(src_path(items[0]["dep"]))}]


def _pct(x):
    return f"{round(x * 100)}%" if x >= 0.1 else f"{x * 100:.1f}%" if x >= 0.01 else "<1%"


def evidence_of(a):
    """What else is true of the dependencies on a card: the reasons a reviewer would want before acting."""
    items, bits = a["items"], []
    if a["exploited"]:
        bits.append("exploited in the wild (CISA KEV)")
    elif a["epss"] >= 0.01:
        bits.append(f"EPSS {_pct(a['epss'])} chance of exploitation within 30 days")
    found = _uniq(i for it in items for i in (it["dep"].get("license") or {}).get("found") or [])
    if found:
        bits.append("its files also carry " + ", ".join(found))
    if a["kind"] not in ("license", "unlisted"):
        lic = _uniq(v for v in ((it["dep"].get("license") or {}).get("verdict") for it in items) if v in ("denied", "review"))
        if lic:
            bits.append("license " + " and ".join(lic))
    if any((it["dep"].get("vulnerabilities") or []) and it["dep"].get("reachability") and it["dep"]["reachability"] != "called" for it in items):
        bits.append("govulncheck finds no call to the vulnerable code")
    kinds = {}
    for it in items:
        for s in it["dep"].get("signals") or []:
            kinds.setdefault(s["kind"], {"text": s["text"], "names": []})["names"].append(name(it["dep"]))
    several = len(_uniq(it["dep"]["name"] for it in items)) > 1
    for k in SIGNAL_ORDER:
        sig = kinds.get(k)
        if not sig or (a["kind"] == "replace" and k == "stale"):
            continue
        who = _uniq(sig["names"])
        if not several:
            bits.append(sig["text"])
            continue
        who_text = ", ".join(who[:3]) + f" and {len(who) - 3} more" if len(who) > 3 else ", ".join(who)
        bits.append(who_text + (" " + SIGNAL_MANY[k] if len(who) > 1 else SIGNAL_ONE[k] + re.sub(r"^deprecated: ", "", sig["text"])))
    if any((it["dep"].get("accepted") or {}).get("lapsed") for it in items):
        bits.append("an accepted exception has lapsed")
    return bits


# --- what goes in the files -----------------------------------------------------------


def for_page(actions):
    """Actions as deps.json carries them: items point at records by project and index."""
    out = []
    for a in actions:
        out.append({k: a[k] for k in ("id", "key", "kind", "tier", "level", "type", "status", "title", "result", "why", "released",
                                      "how", "evidence", "projects", "opened", "ends", "sla", "exploited", "epss", "options") if a.get(k) is not None}
                   | {"epss": round(a["epss"], 4),
                      "requires": ["action-" + slug(k) for k in a.get("requires") or []] or None,
                      # An action that depends on another or needs a choice shows every step, not one line.
                      "steps": (a.get("steps") or a["items"][0]["steps"]) if a.get("options") or a.get("requires") else None,
                      "items": [{"project": it["project"]["name"], "record": it["index"], "reason": it["reason"],
                                 "tier": it["tier"], "to": it["to"]} for it in a["items"]]})
    return out


def annotate_records(projects):
    """The tier and the step each record needing attention carries, for the page's tables and details."""
    for p in projects:
        for d in p["dependencies"]:
            if attention(d):
                d["tier"] = tier_of(d)
                d["how"] = how_text(steps_for(d))


REASON_WORDS = {"license": "license", "unlisted": "license", "abandoned": "abandoned"}


def agent_document(data, site, spec_url, repository=None):
    """The actions file: every project's actions, with the changes, steps and checks an agent needs.

    `repository` is the one holding deps-config.json, where a decline is proposed:
    this repository, or a fork's copy of it.
    """
    base = (site or "").rstrip("/")
    repository = repository or f"{data.get('owner') or data['org']}/dashboards"
    projects = []
    for p in data["projects"]:
        if p.get("error"):
            projects.append({"name": p["name"], "error": p["error"]})
            continue
        acts = []
        for a in p.get("_actions", []):
            changes, steps = [], []
            for it in a["items"]:
                d = it["dep"]
                changes.append({k: v for k, v in {
                    "ecosystem": d["ecosystem"], "name": d["name"], "label": d.get("label"), "purl": d.get("purl"),
                    "scope": d.get("scope"), "dev": d.get("dev"), "status": d.get("status"), "level": d.get("level"),
                    "reason": it["reason"], "from": d.get("version"), "to": it["to"],
                    "files": [f"{s['path']}:{s['line']}" if s.get("line") else s["path"] for s in d.get("sources") or []],
                    "advisories": [{k2: v.get(k2) for k2 in ("id", "aliases", "severity", "summary", "fixed", "url",
                                                            "reachable", "exploited", "epss") if v.get(k2) is not None}
                                   for v in d.get("vulnerabilities") or []] or None,
                    "fix_advisories": d.get("fix_advisories"),
                    "license": d.get("license") if it["reason"] in ("license", "unlisted") else None,
                    "lifecycle": {k2: d["lifecycle"].get(k2) for k2 in ("product", "cycle", "eol", "phase", "url")} if d.get("lifecycle") else None,
                    "compat": d.get("compat"),
                    "signals": d.get("signals") if it["reason"] == "abandoned" else None,
                    "lag": d.get("lag"),
                    "cleared_by": it.get("cleared_by"),
                    "done_when": _done_when(d, it),
                }.items() if v not in (None, [], {})})
                steps += [s for s in it["steps"] if s not in steps]
            if a.get("steps"):
                steps = a["steps"] + [s for s in steps if s not in a["steps"] and "run" not in s]
            acts.append({k: v for k, v in {
                "id": agent_id(p["name"], a["key"]),
                "tier": a["tier"], "level": a["level"], "type": a["type"], "title": a["title"],
                "result": a["result"], "why": a["why"] or None, "evidence": a["evidence"] or None,
                "opened": a.get("opened"), "ends": a.get("ends"), "due": (a.get("sla") or {}).get("due"),
                "exploited": a["exploited"] or None, "epss": round(a["epss"], 4) or None,
                "requires": [agent_id(p["name"], k) for k in a.get("requires") or []] or None,
                "options": a.get("options"),
                "steps": steps or None, "changes": changes,
                "page": f"{base}/deps?project={p['name']}#{a['id']}",
                "decline": _decline(p, a, repository),
            }.items() if v not in (None, [], {})})
        acts = _prerequisites_first(acts)
        counts = {t: sum(1 for a in acts if a["tier"] == t) for t in TIERS}
        projects.append({"name": p["name"],
                         "repo": {"url": p["repo"]["url"], "clone": p["repo"]["url"] + ".git", "branch": p["repo"].get("branch"),
                                  "sha": p["repo"].get("sha")},
                         "counts": counts, "actions": acts})
    return {
        "schema": 1,
        "generated": data["generated"],
        "org": data["org"],
        "owner": data.get("owner") or data["org"],
        "about": ("What to change in each project's dependencies, most urgent first. Each action is one change: "
                  "its steps make it, its changes say what moves where, and each change says when it is done."),
        "workflow": [
            "Work in a clone of the project, on a branch off the commit named in repo.sha or newer.",
            "Take actions in order: every fix action, then plan, then routine. One action is one commit.",
            "An action's requires names the actions to make before it, and they come before it in this list.",
            "An action with options needs a person's choice: propose the recommended one, and make none of them unasked.",
            "Follow each step: run a command in its cwd, make an edit at its file and line, or do the task it names.",
            "Run the project's own gate before committing, as its AGENTS.md or CONTRIBUTING.md describes.",
            "An action is done when every change meets its done_when. The next build of this file drops it.",
            "A step's exit status is not its check: npm audit fix exits 0 whether or not it changed anything. "
            "Read each done_when in the files the steps leave.",
            f"When a change cannot be made, say why, and propose the decline entry for deps-config.json in {repository}.",
        ],
        "tiers": {"fix": "act now: a vulnerability, an end of life, or a fix-by date already passed",
                  "plan": "schedule: a major behind, a denied license, an abandoned package, an advisory no code calls",
                  "routine": "batch: minor and patch releases, sibling pins, licenses to name in the policy"},
        "links": {"page": f"{base}/deps", "data": f"{base}/deps.json", "sbom": f"{base}/deps.cdx.json",
                  "feed": f"{base}/deps-feed.xml", "spec": spec_url},
        "data_sources": [{k: s[k] for k in ("name", "url", "gives", "license")} for s in data.get("data_sources") or []],
        "projects": projects,
    }


def agent_id(project, key):
    kind, _, rest = key.partition("|")
    return f"{project}:{kind}:{slug(rest or key)}"


def _prerequisites_first(acts):
    """Keep the order of urgency, except that an action follows every action it requires."""
    by_id = {a["id"]: a for a in acts}
    out, placed = [], set()

    def place(a):
        if a["id"] in placed:
            return
        placed.add(a["id"])
        for r in a.get("requires") or []:
            if r in by_id:
                place(by_id[r])
        out.append(a)
    for a in acts:
        place(a)
    return out


def _done_when(d, it):
    if it["reason"] in ("license", "unlisted"):
        return "the license policy grades it allowed, or the dependency is gone"
    if it["reason"] == "abandoned":
        return "the dependency is gone"
    status = d.get("status")
    compat = d.get("compat") or {}
    if compat and not compat.get("ok"):
        target = compat.get("target") or {}
        fc = compat.get("firecracker") or {}
        return (f"on a line {compat['with']} validates" + (f" ({target['line']})" if target else "") +
                (f", with {compat['with']} {fc['needs']}+" if fc and not fc.get("ok") else ""))
    if status == "vulnerable":
        return _cleared(d)
    if status in ("eol", "eol-soon"):
        return "the pinned release line is supported past 90 days from now"
    if status == "behind":
        return "the pin names the newest commit or build"
    if status == "major":
        return f"the pin is on the newest major: {it['to']}" if it["to"] else "the pin is on the newest major"
    if status in ("minor", "patch"):
        return f"the pin is {it['to']} or newer" if it["to"] else "the pin is the newest release"
    if status == "deprecated":
        return "the dependency is gone, or a release its publisher has not deprecated is pinned"
    return "the next build no longer lists this change"


def _cleared(d):
    """A vulnerable change is done when no copy the advisories affect is left, whatever version gets there.

    The registry moves between this file and the step, so a version would be
    stale: the refresh that lands above it is right, and a later release can
    carry an advisory of its own.
    """
    ids = _uniq(v["id"] for v in d.get("vulnerabilities") or [])
    if not ids:
        return "no advisory affects the pinned version"
    named = ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + " or " + ids[-1]
    path = src_path(d)
    if d["ecosystem"] == "npm":
        lock = d.get("lockfile") or (path if path.endswith("package-lock.json") else None)
        if lock:
            return f"{lock} carries no copy of {d['name']} that {named} affects"
    if d["ecosystem"] == "go":
        files = _uniq(s["path"] for s in d.get("sources") or [] if GO_MODFILE.search(s["path"]))
        if files:
            return f"{' and '.join(files)} require{'s' if len(files) == 1 else ''} no version of {d['name']} that {named} affects"
    if d["ecosystem"] == "package":
        return f"the image {path} describes installs no build of {d['name']} that {named} affects"
    return f"{path} pins no version of {d['name']} that {named} affects"


def _decline(p, a, repository):
    first = a["items"][0]
    d = first["dep"]
    finding = "license" if first["reason"] in ("license", "unlisted") else \
        (d.get("vulnerabilities") or [{}])[0].get("id") if d.get("status") == "vulnerable" else d.get("status")
    entry = {"project": p["name"], "name": d["name"], "finding": finding,
             "reason": "tolerable_risk", "note": "why this is left as it is", "until": "YYYY-MM-DD"}
    if d.get("version"):
        entry["version"] = d["version"]
    return {"file": "deps-config.json", "repo": repository, "accepted": entry,
            "reasons": ["fix_started", "inaccurate", "no_bandwidth", "not_used", "tolerable_risk"]}
