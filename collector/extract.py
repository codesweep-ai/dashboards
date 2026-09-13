"""Read what a project declares it depends on, from the files it declares it in.

Each extractor turns one kind of file into dependency records carrying the
version as written and where it was written. Nothing here touches the network:
resolving a record against its upstream is resolve.py's job, so every rule in
this file can be tested against a string.
"""

import json
import os
import re
import urllib.parse

from . import versions

# Directories whose manifests describe something other than the project: test
# inputs, recorded traffic, and copies of other repositories' files.
SKIP_DIRS = {"node_modules", "testdata", "cassettes", "examples", "vendor", ".git"}

ECOSYSTEMS = ("go", "npm", "pypi", "actions", "runtime", "image", "package", "native")

RUNTIME_LABELS = {
    "go": "Go toolchain",
    "node": "Node.js",
    "python": "Python",
    "java": "Java (Temurin)",
    "maven": "Maven",
}


def _dep(ecosystem, name, version, path, line, **kw):
    d = {
        "ecosystem": ecosystem,
        "name": name,
        "version": version,
        "sources": [{"path": path, "line": line}],
    }
    d.update(kw)
    d.setdefault("scope", "direct")
    d.setdefault("internal", False)
    return d


def _line_of(text, index):
    return text.count("\n", 0, index) + 1


def _skipped(path):
    return any(part in SKIP_DIRS for part in path.split("/")[:-1])


# --- Go ---------------------------------------------------------------------


def go_mod(text, path, org):
    """Records for a go.mod (or go.<name>.mod): the toolchain, modules and tools."""
    deps, tools = [], []
    block = None
    for i, raw in enumerate(text.splitlines(), start=1):
        indirect = "// indirect" in raw
        line = raw.split("//", 1)[0].strip()
        if not line:
            continue
        if block:
            if line == ")":
                block = None
                continue
            words = line.split()
        else:
            m = re.match(r"^(require|tool|replace|exclude|retract|godebug|ignore)\s*\($", line)
            if m:
                block = m.group(1)
                continue
            words = line.split()
            if not words:
                continue
            if words[0] in ("go", "toolchain") and len(words) == 2:
                v = words[1][2:] if words[1].startswith("go") else words[1]
                deps.append(_dep("runtime", "go", v, path, i, scope="toolchain" if words[0] == "toolchain" else "build",
                                 datasource="golang", label=RUNTIME_LABELS["go"],
                                 floating=len(v.split(".")) < 3))
                continue
            if words[0] in ("require", "tool"):
                block_kind, words = words[0], words[1:]
                _go_entry(block_kind, words, path, i, indirect, org, deps, tools)
            continue
        _go_entry(block, words, path, i, indirect, org, deps, tools)

    for d in deps:
        if d["ecosystem"] != "go":
            continue
        if any(t == d["name"] or t.startswith(d["name"] + "/") for t in tools):
            d["scope"] = "tool"
    return deps


def _go_entry(kind, words, path, line, indirect, org, deps, tools):
    if kind == "tool" and words:
        tools.append(words[0])
    elif kind == "require" and len(words) >= 2:
        mod, ver = words[0], words[1]
        deps.append(_dep("go", mod, ver, path, line,
                         scope="indirect" if indirect else "direct",
                         datasource="goproxy", package=mod,
                         internal=mod.startswith(f"github.com/{org}/")))


def module_root(pkg):
    """Best guess at the module a package path belongs to, for `go install x@v`."""
    parts = pkg.split("/")
    if parts[0] in ("github.com", "gitlab.com", "bitbucket.org", "golang.org", "go.googlesource.com"):
        root = parts[:3]
        if len(parts) > 3 and re.fullmatch(r"v\d+", parts[3]):
            root.append(parts[3])
        return "/".join(root)
    cut = [i for i, p in enumerate(parts) if p == "cmd"]
    return "/".join(parts[: cut[0]]) if cut else pkg


# --- npm --------------------------------------------------------------------

_NPM_SECTIONS = (("dependencies", "direct"), ("devDependencies", "dev"),
                 ("optionalDependencies", "optional"))


def npm(pkg_text, lock_text, path, lock_path, org):
    """Records for one package.json, resolved through its lockfile when there is one.

    Returns (direct records, every package the lockfile installs). The second
    list is what vulnerability lookups read, since a hole in a transitive
    package ships as surely as one in a direct dependency.
    """
    try:
        pkg = json.loads(pkg_text)
    except ValueError:
        return [], []
    lock = {}
    installed = []
    declared = set()
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        declared.update((pkg.get(section) or {}).keys())
    if lock_text:
        try:
            lock = json.loads(lock_text).get("packages", {})
        except ValueError:
            lock = {}
        for key, meta in lock.items():
            if not key or "node_modules/" not in key or meta.get("link"):
                continue
            name = key.rsplit("node_modules/", 1)[1]
            if meta.get("version"):
                # npm hoists what it can to the top level, so a top-level entry
                # is direct only when package.json names it.
                installed.append({"name": name, "version": meta["version"], "dev": bool(meta.get("dev")),
                                  "direct": key == f"node_modules/{name}" and name in declared,
                                  "install_script": bool(meta.get("hasInstallScript"))})

    deps = []
    for section, scope in _NPM_SECTIONS:
        entries = pkg.get(section) or {}
        at = pkg_text.find(f'"{section}"')
        for name, spec in entries.items():
            if not isinstance(spec, str):
                continue
            # Platform binaries a wrapper package publishes alongside itself are
            # placeholders in the tree, rewritten at publish time.
            if section == "optionalDependencies" and spec == "0.0.0":
                continue
            if re.match(r"^(file|link|workspace|git\+|git:|github:|https?:)", spec):
                continue
            idx = pkg_text.find(f'"{name}"', max(at, 0))
            resolved = (lock.get(f"node_modules/{name}") or {}).get("version")
            exact = re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", spec)
            version = resolved or (spec if exact else None)
            deps.append(_dep("npm", name, version, path, _line_of(pkg_text, idx) if idx >= 0 else 1,
                             scope=scope, datasource="npm", package=name,
                             install_script=bool((lock.get(f"node_modules/{name}") or {}).get("hasInstallScript")),
                             constraint=None if spec == version else spec,
                             floating=version is None,
                             internal=name.startswith(f"@{org}/"),
                             **({"lockfile": lock_path} if resolved else {})))
    node = (pkg.get("engines") or {}).get("node")
    if isinstance(node, str):
        floor = re.search(r"(\d+(?:\.\d+){0,2})", node)
        idx = pkg_text.find('"node"', max(pkg_text.find('"engines"'), 0))
        if floor:
            deps.append(_dep("runtime", "node", None, path, _line_of(pkg_text, idx) if idx >= 0 else 1,
                             scope="engines", datasource="node", label=RUNTIME_LABELS["node"],
                             constraint=node, floating=True, cycle=floor.group(1).split(".")[0]))
    return deps, installed


# --- GitHub Actions -----------------------------------------------------------

# A GitHub-hosted runner label: ubuntu-26.04, ubuntu-26.04-arm, macos-latest,
# macos-15-intel, windows-2025-vs2026. Which image each one names is read from
# GitHub's runner-images repository at resolve time, not guessed here.
_RUNNER = re.compile(r"^(ubuntu|macos|windows|xcode)-(latest|slim|\d[\w.]*)(?:-[a-z0-9]+)*$")
# Where a workflow names a runner: `runs-on:`, and the matrix keys that feed it.
_RUNNER_KEY = re.compile(r"^\s*(?:-\s*)?(runs-on|os|runner|platform):\s*(.+)$")


def workflow(text, path, org):
    deps = []
    for i, raw in enumerate(text.splitlines(), start=1):
        if raw.lstrip().startswith("#"):
            continue
        line = re.sub(r"\s+#.*$", "", raw)
        m = re.match(r"^\s*(?:-\s*)?uses:\s*['\"]?([^'\"\s]+)", line)
        if m:
            ref = m.group(1)
            if ref.startswith("docker://"):
                deps.append(_image(ref[len("docker://"):], path, i, org, scope="ci"))
            elif not ref.startswith("./") and "@" in ref:
                target, _, pin = ref.partition("@")
                parts = target.split("/")
                if len(parts) >= 2:
                    repo = "/".join(parts[:2])
                    sha = re.fullmatch(r"[0-9a-f]{40}", pin) is not None
                    deps.append(_dep("actions", target, pin, path, i, scope="ci",
                                     datasource="github", package=repo,
                                     subpath="/".join(parts[2:]) or None,
                                     pinned_sha=sha,
                                     floating=not sha and len(pin.lstrip("v").split(".")) < 3,
                                     internal=parts[0] == org))
            continue
        for key, runtime in (("go-version", "go"), ("node-version", "node"), ("python-version", "python")):
            m = re.search(rf"\b{key}:\s*['\"]?([0-9][^'\"\s]*)", line)
            if m:
                v = m.group(1).replace(".x", "")
                deps.append(_dep("runtime", runtime, v, path, i, scope="ci",
                                 datasource={"go": "golang", "node": "node", "python": "python"}[runtime],
                                 label=RUNTIME_LABELS[runtime], floating=len(v.split(".")) < 3))
        m = _RUNNER_KEY.match(line)
        if m and "${{" not in m.group(2):
            labels = re.findall(r"[A-Za-z0-9.-]+", m.group(2))
            if "self-hosted" in labels:
                rest = [lb for lb in labels if lb != "self-hosted"]
                os_name = next((o for o in ("macos", "linux", "windows") if o in [lb.lower() for lb in rest]), None)
                deps.append(_dep("image", "runner/self-hosted", " ".join(rest) or None, path, i, scope="ci",
                                 datasource="runner", label="self-hosted " + (" ".join(rest) or "runner"),
                                 self_hosted=True, os={"linux": "ubuntu"}.get(os_name, os_name), floating=True))
            for label in labels:
                r = _RUNNER.match(label)
                if r:
                    deps.append(_dep("image", f"runner/{r.group(1)}", label, path, i, scope="ci",
                                     datasource="runner", label=label,
                                     floating=r.group(2) in ("latest", "slim"), os=r.group(1)))
        m = re.match(r"^\s*distribution:\s*['\"]?(Ubuntu|Debian)-?(\d+(?:\.\d+)?)['\"]?\s*$", line)
        if m:
            # A WSL distribution an action installs, which a job then runs inside.
            deps.append(_dep("image", f"wsl/{m.group(1).lower()}", m.group(2), path, i, scope="ci",
                             datasource="oci", package=f"wsl/{m.group(1).lower()}",
                             label=f"WSL {m.group(1)} {m.group(2)}"))
        if not re.match(r"^\s*(?:-\s*)?(?:name|if|id|with|env):", line):
            deps.extend(commands(line, path, i, org, scope="ci"))
        m = re.match(r"^\s*(?:-\s*)?(?:image|container):\s*['\"]?([a-z0-9][^'\"\s]*:[^'\"\s]+)['\"]?\s*$", line)
        if m and "${{" not in m.group(1) and parse_image(m.group(1)):
            deps.append(_image(m.group(1), path, i, org, scope="ci"))
    return deps


# --- container images --------------------------------------------------------

_IMAGE_REF = re.compile(r"^(?:(?P<registry>[a-z0-9.-]+\.[a-z]{2,}(?::\d+)?|localhost(?::\d+)?)/)?"
                        r"(?P<repo>[a-z0-9._/-]+?)(?::(?P<tag>[\w][\w.-]{0,127}))?(?:@(?P<digest>sha256:[0-9a-f]{64}))?$")


def parse_image(ref):
    m = _IMAGE_REF.match(ref.strip())
    if not m:
        return None
    registry = m.group("registry") or "docker.io"
    repo = m.group("repo")
    if registry == "docker.io" and "/" not in repo:
        repo = f"library/{repo}"
    return {"registry": registry, "repository": repo, "tag": m.group("tag"), "digest": m.group("digest")}


def _image(ref, path, line, org, scope):
    img = parse_image(ref) or {"registry": "", "repository": ref, "tag": None, "digest": None}
    name = f"{img['registry']}/{img['repository']}"
    return _dep("image", name, img["tag"] or ("latest" if not img["digest"] else None), path, line,
                scope=scope, datasource="oci", package=name, digest=img["digest"],
                floating=not img["tag"] or img["tag"] == "latest",
                internal=img["registry"] == "ghcr.io" and img["repository"].startswith(f"{org}/"))


# --- Containerfiles ----------------------------------------------------------

_URL = re.compile(r"https?://[^\s\"'|;)]+")


def logical_lines(text):
    """Join backslash continuations, keeping the line each statement starts on."""
    out, buf, start = [], [], None
    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not buf and (not stripped or stripped.startswith("#")):
            continue
        if buf and stripped.startswith("#"):
            continue
        if start is None:
            start = i
        if stripped.endswith("\\"):
            buf.append(stripped[:-1])
            continue
        buf.append(stripped)
        out.append((start, " ".join(buf)))
        buf, start = [], None
    if buf:
        out.append((start, " ".join(buf)))
    return out


def upstream_from_url(url, var):
    """Which upstream a download URL built around `${var}` fetches from."""
    v = r"\$\{?" + re.escape(var) + r"\}?"
    m = re.search(r"github\.com/adoptium/temurin(\d+)-binaries/releases/download/", url)
    if m:
        return {"ecosystem": "runtime", "name": "java", "datasource": "temurin", "label": RUNTIME_LABELS["java"]}
    if re.search(r"(go\.dev|golang\.org|dl\.google\.com/go)/dl/go" + v, url) or re.search(r"/go" + v + r"\.linux", url):
        return {"ecosystem": "runtime", "name": "go", "datasource": "golang", "label": RUNTIME_LABELS["go"]}
    if re.search(r"nodejs\.org/dist/v" + v, url):
        return {"ecosystem": "runtime", "name": "node", "datasource": "node", "label": RUNTIME_LABELS["node"]}
    if re.search(r"apache\.org/.*maven/maven-\d+/" + v, url):
        return {"ecosystem": "runtime", "name": "maven", "datasource": "maven",
                "package": "org.apache.maven:apache-maven", "label": RUNTIME_LABELS["maven"]}
    m = re.search(r"github\.com/([\w.-]+)/([\w.-]+)/releases/download/([^/$]*)" + v, url)
    if m:
        return {"ecosystem": "native", "name": f"github.com/{m.group(1)}/{m.group(2)}", "datasource": "github",
                "package": f"{m.group(1)}/{m.group(2)}", "tag_prefix": m.group(3), "label": m.group(2)}
    m = re.search(r"raw\.githubusercontent\.com/([\w.-]+)/([\w.-]+)/([^/$]*)" + v, url)
    if m:
        return {"ecosystem": "native", "name": f"github.com/{m.group(1)}/{m.group(2)}", "datasource": "github",
                "package": f"{m.group(1)}/{m.group(2)}", "tag_prefix": m.group(3), "label": m.group(2)}
    return None


_RENOVATE_DATASOURCES = {
    "github-releases": ("native", "github"), "github-tags": ("native", "github"),
    "npm": ("native", "npm"), "go": ("go", "goproxy"), "pypi": ("pypi", "pypi"),
    "docker": ("image", "oci"), "node-version": ("runtime", "node"), "node": ("runtime", "node"),
    "java-version": ("runtime", "temurin"), "python-version": ("runtime", "python"),
    "golang-version": ("runtime", "golang"),
}


def renovate_annotations(text):
    """Renovate's inline convention: `# renovate: datasource=… depName=…` above a pin.

    Returns {line of the annotated statement: attributes}. Reading the same
    comments Renovate reads lets a project name a pin's upstream where the pin
    is, without running Renovate.
    """
    out, pending = {}, None
    for i, raw in enumerate(text.splitlines(), start=1):
        s = raw.strip()
        m = re.match(r"^(?:#|//)\s*renovate:\s*(.+)$", s)
        if m:
            pending = dict(re.findall(r"(\w+)=(\S+)", m.group(1)))
            continue
        if pending is not None and s and not s.startswith(("#", "//")):
            out[i] = pending
            pending = None
    return out


def _from_annotation(attrs, value, path, line, org):
    kind = _RENOVATE_DATASOURCES.get(attrs.get("datasource", ""))
    dep_name = attrs.get("depName") or attrs.get("packageName")
    if not kind or not dep_name:
        return None
    eco, ds = kind
    package = attrs.get("packageName") or dep_name
    extra = {}
    prefix = re.match(r"^\^([\w.-]*)\(\?<version>", attrs.get("extractVersion", ""))
    if prefix:
        extra["tag_prefix"] = prefix.group(1)
    if eco == "image":
        dep = _image(f"{package}:{value}", path, line, org, scope="build")
        dep["declared"] = True
        return dep
    if eco == "runtime":
        name = {"node": "node", "temurin": "java", "python": "python", "golang": "go"}[ds]
        return _dep("runtime", name, value, path, line, scope="build", datasource=ds,
                    label=RUNTIME_LABELS[name], declared=True)
    if ds == "github":
        return _dep(eco, f"github.com/{package}", value, path, line, scope="build", datasource=ds,
                    package=package, label=dep_name.rsplit("/", 1)[-1], declared=True, **extra)
    return _dep(eco, dep_name, value, path, line, scope="build", datasource=ds, package=package,
                declared=True, internal=package.startswith((f"github.com/{org}/", f"@{org}/")))


def containerfile(text, path, org):
    """Records for a Containerfile, and the ARG pins no rule could place.

    Returns (records, unplaced) where unplaced is a list of (arg, line) for
    `ARG *_VERSION=…` defaults whose upstream nothing here recognised.
    """
    deps, unplaced = [], []
    args, stages = {}, set()
    release = None
    lines = logical_lines(text)
    # Every URL in the file, with the shell variables its RUN assigned
    # substituted, so `base=https://…; curl "$base/$VER"` still reads as a URL.
    urls = []
    for _, stmt in lines:
        assigns = dict(re.findall(r"\b(\w+)=[\"']?(https?://[^\s\"';]+)", stmt))
        for u in _URL.findall(stmt):
            urls.append(u)
        for name, value in assigns.items():
            for tail in re.findall(r"\$\{?" + name + r"\}?(/[^\s\"'|;)]*)", stmt):
                urls.append(value + tail)

    notes = renovate_annotations(text)
    for lineno, stmt in lines:
        word, _, rest = stmt.partition(" ")
        word = word.upper()
        if word in ("ARG", "ENV") and lineno in notes:
            m = re.match(r"^(\w+)[= ]\s*[\"']?([^\s\"']+)", rest.strip())
            if m:
                args[m.group(1)] = m.group(2)
                dep = _from_annotation(notes[lineno], m.group(2), path, lineno, org)
                if dep:
                    deps.append(dep)
                    continue
        if word == "ARG":
            m = re.match(r"^(\w+)(?:=(\S*))?", rest.strip())
            if not m:
                continue
            name, value = m.group(1), (m.group(2) or "").strip("\"'")
            args[name] = value
            if not value or not re.search(r"_VERSION$", name) or "$" in value:
                continue
            found = None
            for u in urls:
                if re.search(r"\$\{?" + re.escape(name) + r"\}?", u):
                    found = upstream_from_url(u, name)
                    if found:
                        break
            if found:
                eco = found.pop("ecosystem")
                nm = found.pop("name")
                deps.append(_dep(eco, nm, value, path, lineno, scope="build", arg=name, **found))
            else:
                unplaced.append((name, value, lineno))
        elif word == "FROM":
            toks = [t for t in rest.split() if not t.startswith("--")]
            if not toks:
                continue
            ref = re.sub(r"\$\{?(\w+)\}?", lambda mm: args.get(mm.group(1)) or mm.group(0), toks[0])
            if len(toks) >= 3 and toks[1].upper() == "AS":
                stages.add(toks[2])
            if "$" in ref or ref == "scratch" or ref in stages:
                continue
            dep = _image(ref, path, lineno, org, scope="build")
            deps.append(dep)
            img = parse_image(ref) or {}
            if img.get("repository") in ("fedora", "library/fedora") or img.get("repository", "").endswith("/fedora"):
                release = img.get("tag")
        elif word == "RUN":
            deps.extend(commands(stmt[len("RUN"):], path, lineno, org, scope="build"))

    for d in deps:
        if d["ecosystem"] == "package" and release:
            d["release"] = release
    return deps, unplaced


def commands(text, path, lineno, org, scope):
    """Records for what a shell command installs: a Containerfile RUN, or a workflow step."""
    deps = []
    for cmd in re.split(r"&&|\|\||;|\|", text):
        words = cmd.split()
        if not words:
            continue
        # dnf / microdnf / yum install: the packages float with the release.
        for i, w in enumerate(words):
            if w in ("dnf", "microdnf", "yum") and "install" in words[i + 1:]:
                after = words[words.index("install", i) + 1:]
                for pkg in after:
                    if pkg.startswith("-") or "$" in pkg or "/" in pkg:
                        continue
                    deps.append(_dep("package", pkg, None, path, lineno, scope=scope,
                                     datasource="rpm", floating=True, manager=w))
                break
        m = re.search(r"\bnvm install v?(\d+\.\d+\.\d+)", cmd)
        if m:
            deps.append(_dep("runtime", "node", m.group(1), path, lineno, scope=scope,
                             datasource="node", label=RUNTIME_LABELS["node"]))
        m = re.search(r"\bpyenv install (?:-\S+ )*(\d+\.\d+\.\d+)", cmd)
        if m:
            deps.append(_dep("runtime", "python", m.group(1), path, lineno, scope=scope,
                             datasource="python", label=RUNTIME_LABELS["python"]))
        m = re.search(r"\bgo install ([\w./-]+)@(v[\w.+-]+)", cmd)
        if m:
            mod = module_root(m.group(1))
            deps.append(_dep("go", mod, m.group(2), path, lineno, scope=scope, datasource="goproxy",
                             package=mod, internal=mod.startswith(f"github.com/{org}/")))
        m = re.search(r"\b(?:npm (?:install|i|add)|npx)\s+(?:-g\s+|--global\s+|--yes\s+|-y\s+)*((?:@[\w.-]+/)?[\w.-]+)@(\d[\w.+-]*)", cmd)
        if m:
            deps.append(_dep("npm", m.group(1), m.group(2), path, lineno, scope=scope, datasource="npm",
                             package=m.group(1), internal=m.group(1).startswith(f"@{org}/")))
        m = re.search(r"\bpip3? install (.+)$", cmd)
        if m:
            for tok in m.group(1).split():
                if tok.startswith("-") or "/" in tok or "$" in tok or not re.match(r"^[A-Za-z0-9]", tok):
                    continue
                name, _, ver = tok.partition("==")
                deps.append(_dep("pypi", name.lower(), ver or None, path, lineno, scope=scope,
                                 datasource="pypi", package=name, floating=not ver))
        for u in _URL.findall(cmd):
            m = re.search(r"raw\.githubusercontent\.com/([\w.-]+)/([\w.-]+)/(v?\d+\.\d+\.\d+)/", u)
            if m:
                deps.append(_dep("native", f"github.com/{m.group(1)}/{m.group(2)}", m.group(3), path, lineno,
                                 scope=scope, datasource="github", package=f"{m.group(1)}/{m.group(2)}",
                                 label=m.group(2)))
    return deps


# --- env files and deployment manifests -------------------------------------


def env_file(text, path, org):
    deps = []
    for i, raw in enumerate(text.splitlines(), start=1):
        m = re.match(r"^\s*(?:export\s+)?(\w+)=[\"']?([a-z0-9.-]+\.[a-z]{2,}(?::\d+)?/[^\s\"']+:[\w.-]+)[\"']?\s*$", raw)
        if m:
            d = _image(m.group(2), path, i, org, scope="build")
            d["variable"] = m.group(1)
            deps.append(d)
    return deps


def manifest_yaml(text, path, org):
    deps = []
    for i, raw in enumerate(text.splitlines(), start=1):
        m = re.match(r"^\s*-?\s*image:\s*['\"]?([^'\"\s#]+)", raw)
        if m and "{{" not in m.group(1) and "$" not in m.group(1):
            deps.append(_image(m.group(1), path, i, org, scope="deploy"))
    return deps


# --- declared pins -----------------------------------------------------------


# AboutCode's .ABOUT format: a purl type, as the ecosystem and datasource a
# vendored copy is tracked under.
_ABOUT_TYPES = {"npm": ("native", "npm"), "github": ("native", "github"), "pypi": ("pypi", "pypi"),
                "golang": ("go", "goproxy")}


def about_file(text, path):
    """A record for vendored code an AboutCode .ABOUT file describes, or None.

    The file sits beside the copy and says what it is: `name`, `version`, and
    `package_url` or `download_url` for its upstream, with its license.
    """
    fields, key = {}, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m and not line[0].isspace():
            key = m.group(1).lower()
            fields[key] = m.group(2).strip().strip("'\"")
        elif key and line[0].isspace():
            fields[key] = (fields[key] + " " + line.strip()).strip()
    purl = fields.get("package_url") or ""
    m = re.match(r"^pkg:([a-z]+)/(.+?)@([^?#]+)", purl)
    license_expr = fields.get("spdx_license_expression") or fields.get("license_expression")
    extra = {"scope": "vendored", "declared": True, "label": fields.get("name")}
    if license_expr:
        extra["licenses"] = [license_expr]
    if m and m.group(1) in _ABOUT_TYPES:
        eco, ds = _ABOUT_TYPES[m.group(1)]
        name = urllib.parse.unquote(m.group(2))
        version = urllib.parse.unquote(m.group(3))
        if ds == "github":
            extra["package"] = name
            name = f"github.com/{name}"
        elif ds == "npm":
            extra["package"] = name
        return _dep(eco, name, version, path, 1, datasource=ds, **extra)
    version = fields.get("version")
    if not version:
        return None
    up = upstream_from_url((fields.get("download_url") or "").replace(version, "${V}"), "V")
    if up:
        up = dict(up)
        extra.update({k: v for k, v in up.items() if k not in ("ecosystem", "name", "label")})
        return _dep(up["ecosystem"], up["name"], version, path, 1, **extra)
    return _dep("native", fields.get("name") or os.path.basename(path)[:-6], version, path, 1,
                error="no upstream recognised: give the .ABOUT file a package_url", **extra)


def declared_pin(pin, text):
    """A record for one entry of deps-pins.json, or None when its pattern no longer matches."""
    try:
        m = re.search(pin["match"], text, re.MULTILINE)
    except re.error:
        return None
    if not m or not m.groups():
        return None
    extra = {k: pin[k] for k in ("package", "tag_prefix", "label", "note", "cycle", "compat", "internal") if k in pin}
    return _dep(pin.get("ecosystem", "native"), pin.get("name") or pin.get("package"), m.group(1),
                pin["path"], _line_of(text, m.start(1)), scope=pin.get("scope", "build"),
                datasource=pin["datasource"], declared=True, **extra)


# --- a whole repository -------------------------------------------------------


def repository(repo, org, pins=()):
    """Everything one checked-out repository declares.

    Returns a dict with `dependencies`, `installed` (npm lockfile contents, for
    vulnerability lookups), `manifests` (paths read), `unplaced` ARG pins and
    `unmatched` declared pins.
    """
    deps, installed, manifests, unplaced, unmatched = [], [], [], [], []
    files = repo.files()
    fileset = set(files)
    for rel in files:
        base = os.path.basename(rel)
        if base.endswith(".ABOUT") and "node_modules" not in rel.split("/"):
            # Vendored code is where an .ABOUT file lives, so vendor/ is read for it.
            text = repo.read(rel)
            rec = about_file(text or "", rel)
            if rec:
                deps.append(rec)
                manifests.append(rel)
            continue
        if _skipped(rel):
            continue
        text = None
        if re.fullmatch(r"go(\.[\w-]+)?\.mod", base):
            text = repo.read(rel)
            deps += go_mod(text, rel, org)
        elif base == "package.json":
            lock = os.path.join(os.path.dirname(rel), "package-lock.json") if os.path.dirname(rel) else "package-lock.json"
            text = repo.read(rel)
            found, inst = npm(text, repo.read(lock) if lock in fileset else None, rel, lock, org)
            deps += found
            for p in inst:
                p["lockfile"] = lock
            installed += inst
        elif rel.startswith(".github/workflows/") and base.endswith((".yml", ".yaml")):
            text = repo.read(rel)
            deps += workflow(text, rel, org)
        elif base in ("action.yml", "action.yaml"):
            text = repo.read(rel)
            deps += workflow(text, rel, org)
        elif base.startswith(("Containerfile", "Dockerfile")):
            text = repo.read(rel)
            found, loose = containerfile(text, rel, org)
            deps += found
            unplaced += [{"path": rel, "arg": a, "version": v, "line": ln} for a, v, ln in loose]
        elif base.endswith(".env"):
            text = repo.read(rel)
            deps += env_file(text, rel, org)
        elif base.endswith((".yml", ".yaml")) and (
                rel.split("/")[0] in ("deploy", "k8s", "manifests", "charts") or base.startswith(("docker-compose", "compose."))):
            text = repo.read(rel)
            deps += manifest_yaml(text, rel, org)
        if text is not None:
            manifests.append(rel)

    for pin in pins:
        text = repo.read(pin["path"])
        rec = declared_pin(pin, text) if text is not None else None
        if rec is None:
            unmatched.append({"path": pin["path"], "match": pin["match"], "name": pin.get("name"),
                              "reason": "file not found" if text is None else "pattern did not match"})
            continue
        deps.append(rec)
        if pin["path"] not in manifests:
            manifests.append(pin["path"])
        # A declared pin supersedes the ARG it names, so it is not also reported
        # as a pin nobody could place.
        unplaced = [u for u in unplaced if not (u["path"] == rec["sources"][0]["path"]
                                                and u["line"] == rec["sources"][0]["line"])]

    return {"dependencies": merge(deps), "installed": installed, "manifests": sorted(manifests),
            "unplaced": unplaced, "unmatched": unmatched}


_SCOPE_RANK = {"direct": 0, "tool": 1, "build": 2, "toolchain": 3, "dev": 4, "ci": 5, "deploy": 6,
               "engines": 7, "optional": 8, "vendored": 2, "indirect": 9, "transitive": 10}


def merge(deps):
    """Fold records naming the same dependency at the same version into one.

    The sources are kept, so a runner label used by twelve jobs is one record
    that points at all twelve lines. The most direct scope wins.
    """
    out = {}
    for d in deps:
        key = (d["ecosystem"], d["name"], d.get("version"), d.get("constraint"), d.get("subpath"))
        cur = out.get(key)
        if cur is None:
            out[key] = d
            continue
        for s in d["sources"]:
            if s not in cur["sources"]:
                cur["sources"].append(s)
        if _SCOPE_RANK.get(d["scope"], 99) < _SCOPE_RANK.get(cur["scope"], 99):
            cur["scope"] = d["scope"]
    return list(out.values())
