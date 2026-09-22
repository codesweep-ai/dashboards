"""Resolve each declared dependency against its upstream, and say what that means.

For every record this adds what the newest release is and when it shipped, the
release cycle it belongs to and when that cycle ends, the known vulnerabilities
at the pinned version, and, for a pin on a sibling repository, how many commits
it trails. It then gives the record one status and one level, by the rules in
SPEC.md, so the page renders verdicts rather than re-deriving them.
"""

import math
import re
import urllib.parse
from datetime import timedelta

from . import versions
from .http import FetchError
from .sources import iso, parse_time

LEVELS = ("idle", "good", "info", "warning", "serious", "critical")
EOL_SOON_DAYS = 90

# The endoflife.date product a runtime or OS image is tracked under, how many
# version components name one of its cycles, and whether a newer cycle only
# counts once it is a long-term-support release.
PRODUCTS = {
    "go": ("go", 2, False),
    "node": ("nodejs", 1, True),
    "python": ("python", 2, False),
    "java": ("eclipse-temurin", 1, True),
    "fedora": ("fedora", 1, False),
    "debian": ("debian", 1, False),
    "ubuntu": ("ubuntu", 2, True),
    "alpine": ("alpine", 2, False),
    "linux": ("linux", 2, False),
    "windows": ("windows-server", 1, False),
    "macos": ("macos", 1, False),
    "maven": ("apache-maven", 2, False),
}

SEVERITY_ORDER = ("unrated", "low", "moderate", "high", "critical")
_BODHI_SEVERITY = {"urgent": "critical", "high": "high", "medium": "moderate", "low": "low"}

# A package whose name is its source package's plus one of these suffixes comes
# from that source package: openssl-devel from openssl, gcc-c++ from gcc.
_SUBPACKAGE = re.compile(r"^(devel|libs|utils|tools|common|cli|clients|server|c\+\+|headers|static|"
                         r"doc|extra|core|gconv-extra|lib\w*)$")


def level_rank(level):
    return LEVELS.index(level) if level in LEVELS else 0


def days_between(a, b):
    a, b = parse_time(a), parse_time(b)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 86400


class Resolver:
    def __init__(self, sources, org, repos, now):
        self.src = sources
        self.org = org
        self.repos = repos
        self.now = now

    # --- one record -------------------------------------------------------------

    def resolve(self, dep):
        handler = getattr(self, "_ds_" + (dep.get("datasource") or "none").replace("-", "_"), None)
        if dep.get("scope") in ("indirect", "transitive"):
            # Machine-maintained, and work only when an advisory applies, which
            # OSV answers in one batch. Looking up each one's newest release
            # would be most of a run's requests for a column nobody acts on.
            handler = None
        try:
            if handler:
                handler(dep)
        except FetchError as exc:
            dep["error"] = f"{exc.reason} ({_host(exc.url)})"
        except Exception as exc:  # a malformed upstream answer must not sink the run
            dep["error"] = f"{type(exc).__name__}: {exc}"
        return dep

    def _set_upstream(self, dep, latest=None, latest_date=None, version_date=None, line_latest=None,
                      url=None, effective=None):
        up = dep.setdefault("upstream", {})
        up.update({k: v for k, v in {
            "latest": latest, "latest_date": iso(latest_date), "version_date": iso(version_date),
            "line_latest": line_latest if line_latest and line_latest != latest else None,
            "url": url, "effective": effective if effective and effective != dep.get("version") else None,
        }.items() if v is not None})
        compare = effective or dep.get("version")
        if compare and latest:
            dep["behind"] = versions.distance(compare, latest)
        if dep.get("behind") and up.get("latest_date") and up.get("version_date"):
            span = days_between(up["version_date"], up["latest_date"])
            if span is not None and span > 0:
                dep["libyears"] = round(span / 365.25, 2)

    # --- Go modules -----------------------------------------------------------------

    def _ds_goproxy(self, dep):
        module, version = dep["package"], dep.get("version")
        if dep.get("internal") and versions.pseudo(version):
            repo = module.split("/")[2] if module.count("/") >= 2 else None
            return self._internal_commit(dep, repo, version)
        latest = self.src.go_latest(module)
        if not latest:
            dep["error"] = "not on proxy.golang.org"
            return
        self._set_upstream(dep, latest=latest["version"], latest_date=latest["time"],
                           version_date=self.src.go_time(module, version) if version else None,
                           url=f"https://pkg.go.dev/{module}")
        parsed = versions.parse(latest["version"])
        if dep["scope"] in ("direct", "tool") and parsed and parsed.major >= 1:
            nxt = self.src.go_next_major(module, parsed.major)
            if nxt:
                base = re.sub(r"/v\d+$", "", module)
                dep["upstream"]["next_major"] = f"{base}/v{versions.parse(nxt).major}@{nxt}"
        dep["upstream_repo"] = _github_repo(module)

    # --- npm and PyPI -----------------------------------------------------------------

    def _ds_npm(self, dep):
        name, version = dep["package"], dep.get("version")
        latest = self.src.npm_latest(name)
        if not latest:
            dep["error"] = "not on registry.npmjs.org"
            return
        if dep.get("internal") and version and versions.pseudo(version):
            repo = _repo_from_url(latest.get("repository"), self.org) or _guess_repo(name)
            self._internal_commit(dep, repo, version)
            lag = dep.get("lag") or {}
            if lag.get("commits"):
                # The version to install is the one built from the head commit. A
                # dist-tag names whichever build was tagged last, which can be older.
                built = [v for v in self.src.npm_versions(name) or []
                         if versions.pseudo(v) and lag["head"].startswith(versions.pseudo(v)[1])]
                if built:
                    lag["version"] = built[-1]
            return
        info = self.src.npm_version(name, version) if version else {}
        top = self.src.npm_version(name, latest["version"]) if latest["version"] else {}
        self._set_upstream(dep, latest=latest["version"], latest_date=top.get("published"),
                           version_date=info.get("published"),
                           url=f"https://www.npmjs.com/package/{name}")
        if info.get("deprecated"):
            dep["deprecated"] = info.get("deprecated_reason") or "deprecated by its publisher"
        dep["upstream_repo"] = _github_repo(latest.get("repository") or "")

    def _ds_pypi(self, dep):
        found = self.src.pypi(dep["package"])
        if not found:
            dep["error"] = "not on pypi.org"
            return
        self._set_upstream(dep, latest=found["version"], latest_date=found["time"],
                           url=f"https://pypi.org/project/{dep['package']}/")

    # --- GitHub releases: native binaries and actions --------------------------------

    def _ds_github(self, dep):
        repo, version = dep["package"], dep.get("version") or ""
        dep["upstream_repo"] = f"github.com/{repo}"
        if dep["ecosystem"] == "actions" and dep.get("pinned_sha"):
            if dep.get("internal"):
                return self._internal_commit(dep, repo.split("/")[1], version,
                                             paths=[dep["subpath"]] if dep.get("subpath") else ())
            tags = self.src.github_tags(repo)
            named = sorted((t for t, sha in tags.items() if sha == version), key=len)
            if not named:
                dep["error"] = "the pinned commit carries no tag"
                return
            dep["upstream"] = {"pinned_tag": named[0]}
            version = named[0]
        prefix = dep.get("tag_prefix") or ""
        releases = self.src.github_releases(repo)
        cands = {}
        for r in releases:
            if r["prerelease"] or (prefix and not r["tag"].startswith(prefix)):
                continue
            v = versions.strip_prefix(r["tag"], prefix)
            parsed = versions.parse(v.split("+")[0])
            if parsed is None or parsed.is_prerelease:
                continue
            cands.setdefault(parsed.raw, r)
        pin_line = versions.parse(versions.strip_prefix(version, prefix)) if version else None
        covered = pin_line is None or any(versions.parse(c).nums[:min(2, len(pin_line.nums))] ==
                                          pin_line.nums[:min(2, len(pin_line.nums))] for c in cands)
        # The feed lists only recent releases, so a pin on an older line reads
        # the tags too: they carry no dates, but they carry every version.
        if not cands or not covered:
            for tag in self.src.github_tags(repo):
                if prefix and not tag.startswith(prefix):
                    continue
                v = versions.strip_prefix(tag, prefix)
                parsed = versions.parse(v)
                if parsed and not parsed.is_prerelease and not versions.looks_prerelease(tag):
                    cands.setdefault(parsed.raw, {"tag": tag, "published": None,
                                                  "url": f"https://github.com/{repo}/releases/tag/{tag}"})
        if not cands:
            dep["error"] = "no releases or version tags"
            return
        pin = versions.strip_prefix(version, prefix) if version else None
        latest = versions.newest(cands)
        effective = None
        p = versions.parse(pin)
        if p is not None and len(p.nums) < 3:
            # `v7` names whatever v7.x.y is newest, so that is what runs.
            effective = versions.newest([c for c in cands if versions.parse(c).nums[:len(p.nums)] == p.nums])
        line = versions.newest_in_line(cands, effective or pin)
        vd = cands.get(effective or pin, {}).get("published") if (effective or pin) else None
        self._set_upstream(dep, latest=latest, latest_date=cands[latest].get("published"), version_date=vd,
                           line_latest=line, url=f"https://github.com/{repo}/releases", effective=effective)
        if dep["ecosystem"] == "actions" and pin:
            dep["behind"] = versions.distance(effective or pin, latest)
        if repo == "firecracker-microvm/firecracker" and pin:
            self._firecracker_lifecycle(dep, versions.cycle_of(pin, 2))

    def _firecracker_lifecycle(self, dep, cycle):
        """Firecracker's own support table: a line is supported at least until a date."""
        policy = self.src.firecracker_policy()
        row = policy["releases"].get(cycle)
        if not row:
            return
        today = self.now.date()
        # Until a newer line ends it, a line's end is only known as a floor.
        end = _date(row["eol"] or row["min_support"])
        phase = "active"
        if row["eol"] and end <= today:
            phase = "eol"
        elif end and (end - today).days <= EOL_SOON_DAYS:
            phase = "eol-soon"
        dep["lifecycle"] = {"product": "firecracker", "cycle": cycle, "release": row["release"],
                            "support": None, "eol": row["eol"] or row["min_support"],
                            "eol_is_floor": not row["eol"], "lts": False, "latest": row["latest"],
                            "phase": phase, "url": policy["url"]}

    # --- toolchains and runtimes -------------------------------------------------------

    def _lifecycle(self, dep, key, cycle, lts_only=None):
        product, width, prefer_lts = PRODUCTS[key]
        prefer_lts = prefer_lts if lts_only is None else lts_only
        return self._lifecycle_rows(dep, product, self.src.cycles(product), cycle, prefer_lts)

    def _lifecycle_rows(self, dep, product, rows, cycle, prefer_lts=False):
        if not rows:
            return None, None
        today = self.now.date()
        released = [r for r in rows if _date(r.get("releaseDate")) and _date(r["releaseDate"]) <= today]
        target = None
        for r in released:
            if prefer_lts:
                lts = r.get("lts")
                if lts is True or (isinstance(lts, str) and _date(lts) and _date(lts) <= today):
                    target = r
                    break
            else:
                target = r
                break
        row = next((r for r in rows if str(r.get("cycle")) == str(cycle)), None) if cycle else None
        if row:
            eol = row.get("eol")
            eol_date = _date(eol) if isinstance(eol, str) else None
            support = _date(row.get("support")) if isinstance(row.get("support"), str) else None
            phase = "active"
            if eol is True or (eol_date and eol_date <= today):
                phase = "eol"
            elif eol_date and (eol_date - today).days <= EOL_SOON_DAYS:
                phase = "eol-soon"
            elif support and support <= today:
                phase = "maintenance"
            dep["lifecycle"] = {
                "product": product, "cycle": str(row.get("cycle")), "release": row.get("releaseDate"),
                "support": row.get("support") if isinstance(row.get("support"), str) else None,
                "eol": eol if isinstance(eol, str) else (None if eol is False else eol),
                "lts": bool(row.get("lts")), "latest": row.get("latest"), "phase": phase,
                "url": f"https://endoflife.date/{product}",
            }
        elif cycle:
            dep["lifecycle"] = {"product": product, "cycle": str(cycle), "phase": "unknown",
                                "url": f"https://endoflife.date/{product}"}
        return target, row

    def _ds_golang(self, dep):
        version = dep.get("version")
        target, row = self._lifecycle(dep, "go", versions.cycle_of(version, 2))
        if not target:
            dep["error"] = "no Go release data"
            return
        latest = target["latest"]
        line = row.get("latest") if row else None
        effective = line if dep.get("floating") else None
        self._set_upstream(dep, latest=latest, latest_date=self.src.go_toolchain_time(latest),
                           version_date=self.src.go_toolchain_time(effective or version) if version else None,
                           line_latest=line, url="https://go.dev/doc/devel/release", effective=effective)

    def _ds_node(self, dep):
        version = dep.get("version")
        cycle = dep.get("cycle") or versions.cycle_of(version, 1)
        target, row = self._lifecycle(dep, "node", cycle)
        if not target:
            dep["error"] = "no Node.js release data"
            return
        dates = {r["version"].lstrip("v"): r.get("date") for r in self.src.node_releases()}
        line = row.get("latest") if row else None
        if version is None:
            dep.setdefault("upstream", {})["latest"] = target["latest"]
            return
        effective = versions.newest([v for v in dates if versions.parse(v) and
                                     versions.parse(v).nums[:len(versions.parse(version).nums)] ==
                                     versions.parse(version).nums]) if dep.get("floating") else None
        self._set_upstream(dep, latest=target["latest"], latest_date=dates.get(target["latest"]),
                           version_date=dates.get(effective or version), line_latest=line,
                           url="https://nodejs.org/en/about/previous-releases", effective=effective)

    def _ds_python(self, dep):
        version = dep.get("version")
        target, row = self._lifecycle(dep, "python", versions.cycle_of(version, 2))
        if not target:
            dep["error"] = "no Python release data"
            return
        line = row.get("latest") if row else None
        self._set_upstream(dep, latest=target["latest"], latest_date=target.get("latestReleaseDate"),
                           version_date=row.get("latestReleaseDate") if row and line == version else None,
                           line_latest=line, url="https://www.python.org/downloads/",
                           effective=line if dep.get("floating") else None)

    def _ds_temurin(self, dep):
        version = dep.get("version")
        target, row = self._lifecycle(dep, "java", versions.cycle_of(version, 1))
        if not target:
            dep["error"] = "no Temurin release data"
            return
        strip = lambda s: (s or "").split("+")[0] or None  # noqa: E731 - "25.0.4.1+1" is 25.0.4.1
        self._set_upstream(dep, latest=strip(target["latest"]), latest_date=target.get("latestReleaseDate"),
                           version_date=row.get("latestReleaseDate") if row and strip(row.get("latest")) == version else None,
                           line_latest=strip(row.get("latest")) if row else None,
                           url="https://adoptium.net/temurin/releases/")

    def _ds_maven(self, dep):
        found = [v for v in self.src.maven_versions(dep["package"]) if not versions.looks_prerelease(v)]
        if not found:
            dep["error"] = "no Maven Central versions"
            return
        self._set_upstream(dep, latest=versions.newest(found),
                           line_latest=versions.newest_in_line(found, dep.get("version")),
                           url="https://maven.apache.org/docs/history.html")
        self._lifecycle(dep, "maven", versions.cycle_of(dep.get("version"), 2))

    # --- images and runners ----------------------------------------------------------

    def _ds_oci(self, dep):
        name, tag = dep["package"], dep.get("version")
        repo = name.split("/", 1)[1] if "/" in name else name
        base = repo.rsplit("/", 1)[-1]
        stamp = versions.pseudo(tag) if tag else None
        if dep.get("internal") and stamp:
            tags = self.src.oci_tags(name)
            builds = sorted({versions.pseudo(t)[0] for t in tags
                             if versions.pseudo(t) and re.fullmatch(r"v0\.0\.0-\d{14}-[0-9a-f]{12}", t)})
            newer = [b for b in builds if b > stamp[0]]
            # ghcr.io/<org>/sandbox-slim-agents is published by the sandbox repository,
            # under the namespace the image is named in, which a fork does not rename.
            namespace = repo.split("/", 1)[0] if "/" in repo else self.org
            dep["provider"] = base.split("-")[0]
            dep["upstream"] = {"latest_date": iso(builds[-1]) if builds else None,
                               "version_date": iso(stamp[0]),
                               "url": f"https://github.com/{namespace}/{base.split('-')[0]}/pkgs/container/{base}"}
            if builds:
                newest_tag = next((t for t in tags if versions.pseudo(t) and versions.pseudo(t)[0] == builds[-1]
                                   and re.fullmatch(r"v0\.0\.0-\d{14}-[0-9a-f]{12}", t)), None)
                dep["upstream"]["latest"] = newest_tag
            dep["lag"] = {"builds": len(newer),
                          "days": round((builds[-1] - stamp[0]).total_seconds() / 86400, 1) if newer else 0}
            return
        os_key, cycle = _image_os(repo, tag)
        if os_key:
            target, row = self._lifecycle(dep, os_key, cycle)
            if target:
                latest_cycle = str(target.get("cycle"))
                up = dep.setdefault("upstream", {})
                up["latest"] = latest_cycle
                up["url"] = f"https://endoflife.date/{PRODUCTS[os_key][0]}"
                if cycle and versions.parse(cycle) and versions.parse(latest_cycle):
                    if versions.parse(cycle) < versions.parse(latest_cycle):
                        dep["behind"] = "major"
            return
        if dep.get("floating"):
            return
        tags = self.src.oci_tags(name)
        suffix = tag.split("-", 1)[1] if "-" in tag and versions.parse(tag.split("-", 1)[0]) else ""
        cands = [t for t in tags if (t.split("-", 1)[1] if "-" in t else "") == suffix
                 and versions.parse(t.split("-", 1)[0])]
        latest = versions.newest([c.split("-", 1)[0] for c in cands])
        if latest:
            self._set_upstream(dep, latest=latest + (f"-{suffix}" if suffix else ""))
            dep["behind"] = versions.distance(tag.split("-", 1)[0], latest)

    def _ds_runner(self, dep):
        if dep.get("self_hosted"):
            dep["error"] = "a self-hosted runner declares no OS version, so its host is not visible here"
            return
        label = dep.get("version")
        info = self.src.runner_images().get(label)
        if not info:
            dep["error"] = f"{label} is not a label in GitHub's runner-images table"
            return
        dep["runner"] = info
        dep["label"] = f"{info['image']} ({label})"
        up = dep.setdefault("upstream", {})
        up["effective"] = info["version"]
        up["url"] = "https://github.com/actions/runner-images"
        if info["deprecated"]:
            dep["deprecated"] = f"GitHub has deprecated the {info['image']} image"
        if info["os"] not in PRODUCTS:
            return
        target, _ = self._lifecycle(dep, info["os"], info["version"])
        if target:
            up["latest"] = str(target.get("cycle"))
            # A `-latest` label follows GitHub, so only a pinned one can trail.
            if not dep.get("floating"):
                pinned, newest = versions.parse(info["version"]), versions.parse(up["latest"])
                if pinned and newest and pinned < newest:
                    dep["behind"] = "major"

    def _ds_rpm(self, dep):
        """What a floating package resolves to in its release today, and its source package."""
        if not dep.get("release"):
            return
        found = self.src.fedora_package(dep["release"], dep["name"])
        if not found:
            dep["note"] = f"not a Fedora {dep['release']} package name; dnf resolves it through a provide"
            return
        # Which source package builds it: a sibling the name extends, a name with
        # a subpackage suffix removed, then the language prefixes Fedora uses.
        name = dep["name"]
        cands = [c for c in sorted(found["co_packages"], key=len) if name.startswith(c + "-")]
        stripped = re.sub(r"-(devel|libs|cli|common|utils|tools|server|clients|c\+\+|headers|static|doc|extra|core)$", "", name)
        dep["source_candidates"] = list(dict.fromkeys(cands + [stripped, name, "rust-" + name, "python-" + name, "golang-" + name]))
        dep["nvra"] = found["nvra"]
        dep["upstream"] = {"effective": found["version"], "url": f"https://packages.fedoraproject.org/search?query={name}"}

    def _ds_fedora_kernel(self, dep):
        version = dep.get("version") or ""
        v, release = versions.nvr(version)
        fc = re.search(r"fc(\d+)", release or "")
        if not v or not fc:
            dep["error"] = "not a Fedora kernel version-release"
            return
        self._lifecycle(dep, "linux", ".".join(str(n) for n in v.nums[:2]))
        nvr, pushed = self.src.bodhi_latest(fc.group(1), "kernel")
        if not nvr:
            dep["error"] = f"no stable kernel in Fedora {fc.group(1)}"
            return
        latest_v, _ = versions.nvr(nvr)
        dep["upstream"] = {"latest": nvr, "latest_date": iso(pushed),
                           "url": f"https://bodhi.fedoraproject.org/updates/?packages=kernel&releases=F{fc.group(1)}"}
        dep["behind"] = versions.distance(v.raw, latest_v.raw) if latest_v else None
        if dep.get("compat") == "firecracker-guest":
            dep["compat"] = self._firecracker_guest(v, latest_v, nvr, fc.group(1))

    def _firecracker_guest(self, version, fedora_latest, fedora_nvr, release):
        """A guest kernel against the lines Firecracker validates, and the line to move to when it is not one.

        Firecracker's table lists each guest line with the first Firecracker
        release that runs it and a date its support is guaranteed until. The date
        is a floor, as the release table's is, so a listed line stays validated
        past it. The line to move to is the listed one guaranteed longest whose
        upstream line has not ended: in practice the newest long-term line.
        """
        policy = self.src.firecracker_policy()
        guests = policy["guest_kernels"]
        line = ".".join(str(n) for n in version.nums[:2])
        today = self.now.date()
        cycles = {str(r.get("cycle")): r for r in self.src.cycles("linux") or []}

        def ended(cycle):
            eol = (cycles.get(cycle) or {}).get("eol")
            return eol is True or bool(isinstance(eol, str) and _date(eol) and _date(eol) <= today)

        listed = sorted(guests, key=versions.parse)
        live = [g for g in listed if not ended(g)]
        target = max(live, key=lambda g: (guests[g]["min_support"], versions.parse(g)), default=None)
        compat = {"with": "Firecracker", "line": line, "validated": listed, "ok": line in guests,
                  "guaranteed": {g: guests[g]["min_support"] for g in listed},
                  "requires": {g: guests[g]["min_firecracker"] for g in listed},
                  "url": policy["kernel_url"]}
        if target:
            row = cycles.get(target) or {}
            compat["target"] = {"line": target, "lts": bool(row.get("lts")),
                                "eol": row.get("eol") if isinstance(row.get("eol"), str) else None,
                                "latest": row.get("latest"), "guaranteed": guests[target]["min_support"],
                                "min_firecracker": guests[target]["min_firecracker"]}
            # The lines released after the target, and why none of them is the answer.
            later = sorted((c for c in cycles if versions.parse(c) and versions.parse(target) < versions.parse(c)
                            and _date(cycles[c].get("releaseDate")) and _date(cycles[c]["releaseDate"]) <= today),
                           key=versions.parse)
            compat["newer"] = {"ended": [c for c in later if ended(c)],
                               "not_validated": [c for c in later if not ended(c) and c not in guests]}
        if fedora_latest:
            # Fedora keeps one kernel line current per release, and rebases it,
            # so its newest stable build names the only line it maintains.
            fedora_line = ".".join(str(n) for n in fedora_latest.nums[:2])
            compat["distribution"] = {"name": f"Fedora {release}", "latest": fedora_nvr, "line": fedora_line,
                                      "has_target": bool(target) and fedora_line == target}
        return compat

    def link_compat(self, projects):
        """Name the Firecracker pin a guest kernel's move depends on, and whether it is new enough.

        Firecracker's table says which release first runs each guest line, and
        the project pins its Firecracker elsewhere, so this looks across records.
        """
        for p in projects:
            fc = next((d for d in p["dependencies"] if d.get("package") == "firecracker-microvm/firecracker"
                       or d["name"] == "github.com/firecracker-microvm/firecracker"), None)
            for d in p["dependencies"]:
                compat = d.get("compat") or {}
                target = compat.get("target") or {}
                if compat.get("with") != "Firecracker" or compat.get("ok") or not target or not fc or not fc.get("version"):
                    continue
                pinned, needs = versions.parse(fc["version"]), versions.parse(target["min_firecracker"])
                compat["firecracker"] = {"name": fc["name"], "pinned": fc["version"], "needs": target["min_firecracker"],
                                         "ok": bool(pinned and needs and not pinned < needs)}

    # --- sibling repositories -------------------------------------------------------------

    def _internal_commit(self, dep, repo_name, version, paths=()):
        """How far a pin on another project's commit trails that project's default branch."""
        stamp = versions.pseudo(version)
        sha = stamp[1] if stamp else version
        repo = self.repos.get(repo_name)
        dep["provider"] = repo_name
        if repo is None:
            dep["error"] = f"{repo_name} is not a project this page reads"
            return
        behind = repo.behind(sha)
        if behind is None:
            dep["error"] = f"commit {sha[:12]} is not in {repo_name}'s history"
            return
        pinned_at = repo.commit_time(sha)
        # The whole head commit: an action pin moves to it, and a pin names a commit in full.
        lag = {"commits": behind, "days": 0.0, "head": repo.sha, "pinned": sha[:12]}
        if paths:
            lag["commits_touching"] = repo.behind(sha, paths)
            lag["paths"] = list(paths)
        if behind and pinned_at and repo.committed:
            lag["days"] = round((repo.committed - pinned_at).total_seconds() / 86400, 1)
        if not repo.on_branch(sha):
            lag["off_branch"] = True
        dep["lag"] = lag
        dep["upstream"] = {"latest_date": iso(repo.committed), "version_date": iso(pinned_at),
                           "url": f"{repo.url}/compare/{sha[:12]}...{repo.branch or 'HEAD'}"}
        if behind and pinned_at and repo.committed:
            dep["libyears"] = round((repo.committed - pinned_at).total_seconds() / 86400 / 365.25, 2)

    # --- vulnerabilities, across every project at once ------------------------------------

    def vulnerabilities(self, projects):
        """Attach OSV findings to every record and lockfile package they affect."""
        queries, owners = [], []
        for p in projects:
            for d in p["dependencies"]:
                q = _osv_query(d)
                if q:
                    queries.append(q)
                    owners.append((p, d))
            for inst in p.get("installed", []):
                queries.append(("npm", inst["name"], inst["version"]))
                owners.append((p, inst))
        if not queries:
            return
        unique = sorted(set(queries))
        ids_for = dict(zip(unique, self.src.osv_batch(unique)))
        details = {}
        for vid in sorted({i for ids in ids_for.values() for i in ids}):
            try:
                details[vid] = self.src.osv_vuln(vid)
            except FetchError:
                details[vid] = {"id": vid}
        for q, (p, owner) in zip(queries, owners):
            ids = ids_for.get(q) or []
            if not ids:
                continue
            found = _merge_advisories([details[i] for i in ids], q)
            if "ecosystem" in owner:
                owner["vulnerabilities"] = found
                continue
            # A lockfile package: attach to its record, or add one.
            direct = next((d for d in p["dependencies"] if d["ecosystem"] == "npm" and d["name"] == owner["name"]
                           and d.get("version") == owner["version"]), None)
            if direct is not None:
                direct["vulnerabilities"] = found
                continue
            p["dependencies"].append({
                "ecosystem": "npm", "name": owner["name"], "version": owner["version"],
                "scope": "transitive", "internal": False, "dev": owner["dev"],
                "sources": [{"path": owner["lockfile"], "line": None}], "datasource": "npm",
                "package": owner["name"], "vulnerabilities": found,
            })

    def known_exploited(self, projects):
        """Date every advisory CISA lists as exploited in the wild, by any CVE it aliases."""
        vulns = _all_advisories(projects)
        if not vulns:
            return
        kev = self.src.kev()
        for v in vulns:
            added = sorted(kev[c] for c in _cves(v) if kev.get(c))
            if added:
                v["exploited"] = added[0]

    def exploit_prediction(self, projects):
        """FIRST's EPSS for every advisory with a CVE: how likely an exploit is within 30 days."""
        vulns = _all_advisories(projects)
        cves = sorted({c for v in vulns for c in _cves(v)})
        if not cves:
            return
        scores = self.src.epss(cves)
        for v in vulns:
            found = [scores[c] for c in _cves(v) if c in scores]
            if found:
                probability, percentile = max(found)
                v["epss"] = {"probability": round(probability, 4), "percentile": round(percentile, 4)}

    def check_fixes(self, projects, rounds=3):
        """Make sure the version a record is told to move to has no advisory of its own.

        The release that fixes a record's advisories is the highest first-fixed
        version among them. A later advisory can affect that release too, so it
        is looked up in turn, and the fix steps past it while one is known.
        """
        todo = {}
        for p in projects:
            for d in p["dependencies"]:
                q = _osv_query(d)
                fixes = [v["fixed"] for v in d.get("vulnerabilities") or [] if v.get("fixed")]
                if q and fixes:
                    fix = max(fixes, key=lambda f: versions.parse(f) or versions.parse("0"))
                    todo.setdefault((q[0], q[1], fix), []).append(d)
        candidate = {k: k for k in todo}
        hits = {}
        pending = set(todo)
        for _ in range(rounds):
            if not pending:
                break
            queries = sorted({candidate[k] for k in pending})
            ids_for = dict(zip(queries, self.src.osv_batch(queries)))
            still = set()
            for k in pending:
                q = candidate[k]
                ids = ids_for.get(q) or []
                if not ids:
                    continue
                hits.setdefault(k, []).extend(i for i in ids if i not in hits.get(k, []))
                later = set()
                for vid in ids:
                    try:
                        later.update(_fixed(self.src.osv_vuln(vid), q[0], q[1], q[2]))
                    except FetchError:
                        continue
                if later:
                    candidate[k] = (q[0], q[1], max(later, key=lambda f: versions.parse(f) or versions.parse("0")))
                    still.add(k)
                else:
                    candidate[k] = None
            pending = still
        for k, recs in todo.items():
            if k not in hits:
                continue
            clean = candidate[k] if candidate[k] and k not in pending else None
            for d in recs:
                d["fix_advisories"] = sorted(hits[k])
                d["clean_fix"] = clean[2] if clean else None

    def cleared_by_moves(self, projects):
        """Name the declared dependencies whose own move drops a vulnerable lockfile copy.

        Runs once records are classified, since it reads where each declared
        dependency moves. A copy is cleared when every declared dependency that
        installs it moves, and what deps.dev resolves for each at its new version
        asks for no affected copy: none at all, or only fixed releases under a
        range the pinned copy does not satisfy, so npm cannot keep it.
        """
        graphs = {}
        for p in projects:
            direct = {(d["name"], d.get("lockfile")): d for d in p["dependencies"]
                      if d["ecosystem"] == "npm" and d.get("lockfile") and d["scope"] not in ("indirect", "transitive")}
            for r in p["dependencies"]:
                if r.get("scope") != "transitive" or r.get("status") != "vulnerable" or not r.get("via") or not r.get("fix"):
                    continue
                moves = []
                for name in r["via"]:
                    to = _planned_move(direct.get((name, r["sources"][0]["path"])))
                    if to and (name, to) not in graphs:
                        graphs[(name, to)] = self.src.npm_graph(name, to)
                    if not to or not _drops(graphs[(name, to)], r):
                        moves = None
                        break
                    moves.append({"name": name, "version": to})
                if moves:
                    r["cleared_by"] = moves

    def enrich_found_licenses(self, projects, shipped):
        """The licenses ClearlyDefined's scans found in each shipped package's files, and its license score."""
        coords = {}
        for p in projects:
            for d in p["dependencies"]:
                c = _clearlydefined_coordinate(d)
                if c and shipped(d):
                    coords.setdefault(c, []).append(d)
        if not coords:
            return
        found = self.src.clearlydefined(sorted(coords))
        for c, recs in coords.items():
            info = found.get(c)
            if not info:
                continue
            for d in recs:
                d["licenses_found"] = info["discovered"]
                d["license_url"] = f"https://clearlydefined.io/definitions/{c}"
                if info.get("score") is not None:
                    d["license_score"] = info["score"]

    def enrich_lifecycles(self, projects):
        """Release cycles for anything endoflife.date tracks by package identifier.

        Its index maps purls to products: pkg:npm/react is React, whose 18 line
        has its own support dates. A record that already has a cycle keeps it.
        """
        try:
            index = self.src.eol_products_by_purl()
        except FetchError:
            return
        for p in projects:
            for d in p["dependencies"]:
                if d.get("lifecycle") or not d.get("version") or d.get("scope") in ("indirect", "transitive"):
                    continue
                product = next((index[k] for k in _purls(d) if k in index), None)
                if not product:
                    continue
                try:
                    rows = self.src.cycles(product)
                except FetchError:
                    continue
                v = versions.parse((d.get("upstream") or {}).get("effective") or d["version"])
                names = {str(r.get("cycle")) for r in rows}
                cycle = next((c for c in (".".join(str(n) for n in v.nums[:w]) for w in (2, 1)) if c in names), None) if v else None
                if cycle:
                    self._lifecycle_rows(d, product, rows, cycle)

    def enrich_packages(self, projects, known=None):
        """Licenses, deprecation and provenance for every npm, Go and PyPI record.

        What deps.dev says of an exact version does not change, except whether
        it is deprecated, so `known` answers for an indirect record the previous
        file already described. A direct record is always asked, for deprecation.
        """
        systems = {"npm": "NPM", "go": "GO", "pypi": "PYPI"}
        keys, reuse = {}, {}
        for p in projects:
            for d in p["dependencies"]:
                eco = "npm" if d.get("datasource") == "npm" else d["ecosystem"]
                if eco in systems and d.get("version") and not d.get("lag"):
                    key = (systems[eco], d.get("package") or d["name"], d["version"])
                    if d.get("scope") in ("indirect", "transitive") and key in (known or {}):
                        reuse.setdefault(key, []).append(d)
                    else:
                        keys.setdefault(key, []).append(d)
        for key, recs in reuse.items():
            for d in recs:
                d.update({k: v for k, v in known[key].items() if v})
        if not keys:
            return
        found = self.src.depsdev_versions(sorted(keys))
        for k, recs in keys.items():
            info = found.get(k)
            if not info:
                continue
            for d in recs:
                if info["licenses"]:
                    d["licenses"] = info["licenses"]
                if info["deprecated"] and not d.get("deprecated"):
                    d["deprecated"] = info["deprecated"]
                if info["provenance"]:
                    d["provenance"] = True
                up = d.setdefault("upstream", {})
                if info["published"] and not up.get("version_date"):
                    up["version_date"] = iso(info["published"])
                if not d.get("upstream_repo") and info.get("source_repo"):
                    d["upstream_repo"] = _github_repo(info["source_repo"])

    def enrich_repos(self, projects):
        """OpenSSF Scorecard and a license for the repositories direct dependencies come from."""
        repos = {}
        for p in projects:
            for d in p["dependencies"]:
                if d.get("internal") or d.get("scope") in ("indirect", "transitive"):
                    continue
                repo = d.get("upstream_repo")
                if repo and repo.startswith("github.com/"):
                    repos.setdefault(repo.lower(), []).append(d)
        from concurrent.futures import ThreadPoolExecutor

        def one(repo):
            try:
                return repo, self.src.depsdev_project(repo)
            except FetchError:
                return repo, None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = dict(pool.map(one, sorted(repos)))
        for repo, recs in repos.items():
            info = results.get(repo)
            if not info:
                continue
            for d in recs:
                d["repo_signals"] = {k: v for k, v in info.items() if k in ("scorecard", "stars")}
                if not d.get("licenses") and info.get("license") and d["ecosystem"] in ("native", "actions"):
                    d["licenses"] = [info["license"]]

    def enrich_fedora_licenses(self, projects, known=None):
        """The License tag of each Fedora package's source package.

        A build's source package and spec do not change, so `known` answers for a
        package whose version the previous file already described.
        """
        from concurrent.futures import ThreadPoolExecutor
        pairs = {}
        for p in projects:
            for d in p["dependencies"]:
                if d["ecosystem"] == "package" and d.get("source_candidates") and d.get("release"):
                    seen = (known or {}).get((d["release"], d["name"], (d.get("upstream") or {}).get("effective")))
                    if seen:
                        d.pop("source_candidates", None)
                        d.pop("nvra", None)
                        d.update({k: v for k, v in seen.items() if v})
                        continue
                    pairs.setdefault((d["release"], tuple(d["source_candidates"])), []).append(d)

        nvras = {}
        for (release, cands), recs in pairs.items():
            nvras[(release, cands)] = recs[0].get("nvra")

        def one(pair):
            # Koji names the source package outright; the name rules are the fallback.
            known = self.src.koji_source(nvras[pair]) if nvras.get(pair) else None
            cands = ((known,) if known else ()) + pair[1]
            try:
                return pair, self.src.fedora_license(pair[0], list(dict.fromkeys(cands)))
            except FetchError:
                return pair, (known, None)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for pair, (source, expression) in pool.map(one, sorted(pairs)):
                for d in pairs[pair]:
                    d.pop("source_candidates", None)
                    d.pop("nvra", None)
                    if source:
                        d["source_package"] = source
                    if expression:
                        d["licenses"] = [expression]

    def snapshot(self, project, repo, snap):
        """Fedora security updates shipped since a project's packages were last resolved."""
        text = repo.read(snap["built"]["path"]) or ""
        m = re.search(snap["built"]["match"], text, re.MULTILINE)
        built = versions._stamp(m.group(1)) if m else None
        pkgs = [d for d in project["dependencies"]
                if d["ecosystem"] == "package" and d["sources"][0]["path"] == snap["packages"]]
        if not built or not pkgs:
            project.setdefault("unmatched", []).append({
                "path": snap["built"]["path"], "match": snap["built"]["match"], "name": "package snapshot",
                "reason": "pattern did not match" if not built else f"no packages installed by {snap['packages']}"})
            return
        release = next((d.get("release") for d in pkgs if d.get("release")), None)
        info = {"built": iso(built), "from": snap["built"]["path"], "packages": snap["packages"],
                "release": release, "updates": 0, "matched": 0}
        project["snapshot"] = info
        for d in pkgs:
            d["snapshot"] = iso(built)
        if not release:
            return
        updates = self.src.bodhi_security_since(release, built)
        info["updates"] = len(updates)
        names = {d["name"]: d for d in pkgs}
        touched = set()
        for u in updates:
            sources = {b.rsplit("-", 2)[0] for b in u["builds"] if b.count("-") >= 2}
            for binary, d in names.items():
                # mdapi names the source package when it knows the binary; the
                # suffix rule covers the rest.
                known = d.get("source_package")
                if (known and known in sources) or (not known and any(
                        binary == s or (binary.startswith(s + "-") and _SUBPACKAGE.match(binary[len(s) + 1:]))
                        for s in sources)):
                    touched.add(u["id"])
                    d.setdefault("vulnerabilities", []).append({
                        "id": u["id"], "aliases": [], "severity": _BODHI_SEVERITY.get(u["severity"], "unrated"),
                        "summary": u["summary"], "fixed": ", ".join(u["builds"]), "published": iso(u["pushed"]),
                        "url": u["url"]})
        info["matched"] = len(touched)

    # --- the verdict ---------------------------------------------------------------------

    def classify(self, dep):
        """Give a record its one status and level. SPEC.md lists the rules in this order."""
        vulns = dep.get("vulnerabilities") or []
        if vulns and dep["ecosystem"] != "package":
            # Each advisory names the first release that fixes it, so the
            # release that fixes them all is the highest of those.
            fixes = [v["fixed"] for v in vulns if v.get("fixed")]
            if fixes:
                dep["fix"] = max(fixes, key=lambda f: versions.parse(f) or versions.parse("0"))
                if len(fixes) < len(vulns):
                    dep["fix_partial"] = True
            # The first release that fixes these can carry advisories of its
            # own; check_fixes found the release past them, or that none is known.
            if "clean_fix" in dep:
                clean = dep.pop("clean_fix")
                if clean:
                    dep["fix"] = clean
                else:
                    dep["fix_partial"] = True
        elif vulns:
            dep["fix"] = ", ".join(sorted({v["fixed"] for v in vulns if v.get("fixed")}))
        phase = (dep.get("lifecycle") or {}).get("phase")
        lag = dep.get("lag") or {}
        relaxed = dep.get("scope") in ("dev", "ci", "optional") or dep.get("dev")
        if vulns:
            worst = max((SEVERITY_ORDER.index(v.get("severity", "unrated")) for v in vulns), default=0)
            status = "vulnerable"
            # An advisory CISA lists as exploited in the wild is being used
            # against real systems, whatever its score and wherever it runs.
            exploited = any(v.get("exploited") for v in vulns)
            level = "critical" if exploited or (worst >= SEVERITY_ORDER.index("high") and not relaxed) else "serious"
            # govulncheck read the code and found no call to what any advisory
            # names: the version is in the graph, the hole is not in the path.
            if dep.get("reachability") and dep["reachability"] != "called":
                level = "serious" if exploited else "warning"
        elif phase == "eol":
            # An engines floor is a promise about what may run, not what does,
            # and a dev tool past its end of life ships nothing to anyone.
            status, level = "eol", "warning" if dep.get("scope") == "engines" else "serious" if relaxed else "critical"
        elif phase == "eol-soon":
            status, level = "eol-soon", "info" if dep.get("scope") == "engines" else "serious"
        elif lag.get("commits_touching", lag.get("commits")) or lag.get("builds"):
            # An action pinned by commit is behind only if its own directory moved.
            status = "behind"
            days = lag.get("days") or 0
            level = "serious" if days > 60 else "warning" if days > 14 else "info"
        elif dep.get("behind") == "major":
            # Chromium's third-party freshness policy: a major that has been out
            # a year is overdue, not merely available.
            status, level = "major", "serious" if self._released_days(dep) >= 365 else "warning"
        elif dep.get("deprecated"):
            status, level = "deprecated", "warning"
        elif dep.get("behind") in ("minor", "patch"):
            # The same policy's other window: a minor or patch out three months.
            status, level = dep["behind"], "warning" if self._released_days(dep) >= 90 else "info"
        elif dep.get("error"):
            status, level = "unknown", "idle"
        elif "lag" in dep or (dep.get("upstream") or {}).get("latest") or phase in ("active", "maintenance"):
            status, level = ("floating", "idle") if dep.get("floating") and not dep.get("upstream", {}).get("latest") else ("current", "good")
        elif dep.get("floating"):
            status, level = "floating", "idle"
        else:
            status, level = "unknown", "idle"
        if status == "current" and dep.get("floating") and dep.get("version") in (None, "latest"):
            status, level = "floating", "idle"
        if dep.get("scope") in ("indirect", "transitive") and status != "vulnerable":
            # Its tool moves it, so it is shown and never queued, and its newest
            # release is not looked up.
            status, level = "untracked", "idle"
        dep["status"], dep["level"] = status, level
        return dep

    def _released_days(self, dep):
        """How long the newest release has been out, in days; 0 when unknown."""
        latest = parse_time((dep.get("upstream") or {}).get("latest_date"))
        return (self.now - latest).total_seconds() / 86400 if latest else 0


# --- helpers ---------------------------------------------------------------------------


def _date(s):
    dt = parse_time(s) if isinstance(s, str) else None
    return dt.date() if dt else None


def _host(url):
    return re.sub(r"^https?://([^/]+).*$", r"\1", url or "")


def _github_repo(text):
    m = re.search(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/|$)", text or "")
    return f"github.com/{m.group(1)}/{m.group(2)}" if m else None


def _repo_from_url(url, org):
    m = re.search(rf"github\.com[/:]{re.escape(org)}/([\w.-]+?)(?:\.git)?$", url or "")
    return m.group(1) if m else None


def _guess_repo(name):
    # @codesweep-ai/ledger is published from ledger, @codesweep-ai/ui from ui.
    # cs- is the old naming, still on npm until those packages are unpublished.
    return name.split("/")[-1].removeprefix("cs-")


def _image_os(repo, tag):
    """Which OS release an image tag is, when it is one: ("fedora", "44")."""
    base = repo.rsplit("/", 1)[-1]
    m = re.search(r"-debian(\d+)$", base)
    if m:
        return "debian", m.group(1)
    if base in ("fedora", "debian", "ubuntu", "alpine") and tag:
        width = PRODUCTS[base][1]
        cyc = re.match(r"^(\d+(?:\.\d+){0,%d})" % (width - 1), tag)
        return (base, cyc.group(1)) if cyc else (base, None)
    return None, None


def _purls(d):
    """The package identifiers endoflife.date could know a record by, without versions."""
    eco, name = d["ecosystem"], d.get("package") or d["name"]
    out = []
    if d.get("datasource") == "npm":
        out.append("pkg:npm/" + urllib.parse.quote(name, safe="/").lower())
    if eco == "go":
        out.append(f"pkg:golang/{name}".lower())
    if eco == "pypi":
        out.append(f"pkg:pypi/{name}".lower())
    if eco in ("native", "actions") and d.get("datasource") == "github" and d.get("package"):
        out.append(f"pkg:github/{d['package']}".lower())
    if eco == "image" and d.get("datasource") == "oci" and "/" in name:
        out.append(f"pkg:docker/{name.split('/', 1)[1]}".lower())
    return out


def _all_advisories(projects):
    return [v for p in projects for d in p["dependencies"] for v in d.get("vulnerabilities") or []]


def _cves(v):
    return [i for i in [v.get("id"), *(v.get("aliases") or [])] if i and i.startswith("CVE-")]


def _clearlydefined_coordinate(d):
    """A record's ClearlyDefined coordinate: type/provider/namespace/name/revision."""
    v = d.get("version")
    if not v or d.get("internal") or d.get("lag"):
        return None
    if d["ecosystem"] == "go":
        namespace, _, name = d["name"].rpartition("/")
        return f"go/golang/{urllib.parse.quote(namespace, safe='').lower() or '-'}/{name}/{v}"
    if d.get("datasource") == "npm" and d["ecosystem"] in ("npm", "native"):
        pkg = d.get("package") or d["name"]
        namespace, name = pkg.split("/", 1) if pkg.startswith("@") and "/" in pkg else ("-", pkg)
        return f"npm/npmjs/{namespace}/{name}/{v}"
    if d["ecosystem"] == "pypi":
        return f"pypi/pypi/-/{d.get('package') or d['name']}/{v}"
    return None


def _osv_query(d):
    v = d.get("version")
    if not v:
        return None
    if d["ecosystem"] == "go" and not d.get("internal"):
        return ("Go", d["name"], v)
    if d["ecosystem"] == "runtime" and d["name"] == "go" and len(v.split(".")) >= 3:
        return ("Go", "stdlib", v)
    if d["ecosystem"] == "pypi":
        return ("PyPI", d["package"], v)
    if d.get("datasource") == "npm" and d["ecosystem"] in ("npm", "native") and not d.get("lockfile"):
        return ("npm", d["package"], v)
    return None


def _merge_advisories(records, query):
    """One entry per issue: a GO- id and the GHSA- it aliases are the same hole."""
    eco, name, version = query
    groups = []
    for rec in records:
        ids = {rec.get("id")} | set(rec.get("aliases") or [])
        hit = next((g for g in groups if g["ids"] & ids), None)
        if hit:
            hit["ids"] |= ids
            hit["records"].append(rec)
        else:
            groups.append({"ids": ids, "records": [rec]})
    out = []
    for g in groups:
        recs = g["records"]
        primary = next((r for r in recs if str(r.get("id", "")).startswith("GHSA-")), recs[0])
        severity = max((_severity(r) for r in recs), key=SEVERITY_ORDER.index)
        fixed = sorted({f for r in recs for f in _fixed(r, eco, name, version)},
                       key=lambda s: versions.parse(s) or versions.parse("0"))
        out.append({
            "id": primary.get("id"),
            "aliases": sorted(i for i in g["ids"] if i and i != primary.get("id")),
            "severity": severity,
            "summary": next((r.get("summary") for r in recs if r.get("summary")), "")[:200],
            "fixed": fixed[0] if fixed else None,
            "published": iso(primary.get("published")),
            "url": f"https://osv.dev/vulnerability/{primary.get('id')}",
        })
    out.sort(key=lambda v: -SEVERITY_ORDER.index(v["severity"]))
    return out


def _severity(rec):
    s = str((rec.get("database_specific") or {}).get("severity") or "").lower()
    if s in SEVERITY_ORDER:
        return s
    if s == "medium":
        return "moderate"
    for sev in rec.get("severity") or []:
        score = cvss3_score(sev.get("score", "")) if str(sev.get("type", "")).startswith("CVSS_V3") else None
        if score is not None:
            return ("critical" if score >= 9 else "high" if score >= 7 else "moderate" if score >= 4
                    else "low" if score > 0 else "unrated")
    return "unrated"


def _fixed(rec, eco, name, version):
    pinned = versions.parse(version.lstrip("v") if eco == "Go" and name == "stdlib" else version)
    for aff in rec.get("affected") or []:
        pkg = aff.get("package") or {}
        if pkg.get("name") != name or pkg.get("ecosystem") != eco:
            continue
        for rng in aff.get("ranges") or []:
            for ev in rng.get("events") or []:
                f = ev.get("fixed")
                pf = versions.parse(f)
                if f and pf and (pinned is None or pinned < pf):
                    yield f


_CVSS3 = {
    "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}, "AC": {"L": 0.77, "H": 0.44},
    "UI": {"N": 0.85, "R": 0.62}, "C": {"H": 0.56, "L": 0.22, "N": 0}, "I": {"H": 0.56, "L": 0.22, "N": 0},
    "A": {"H": 0.56, "L": 0.22, "N": 0},
}


def cvss3_score(vector):
    """The CVSS v3.x base score a vector string describes, or None."""
    parts = dict(p.split(":", 1) for p in vector.split("/")[1:] if ":" in p)
    try:
        scope_changed = parts["S"] == "C"
        pr = {"N": 0.85, "L": 0.68 if scope_changed else 0.62, "H": 0.5 if scope_changed else 0.27}[parts["PR"]]
        iss = 1 - (1 - _CVSS3["C"][parts["C"]]) * (1 - _CVSS3["I"][parts["I"]]) * (1 - _CVSS3["A"][parts["A"]])
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15 if scope_changed else 6.42 * iss
        expl = 8.22 * _CVSS3["AV"][parts["AV"]] * _CVSS3["AC"][parts["AC"]] * pr * _CVSS3["UI"][parts["UI"]]
    except KeyError:
        return None
    if impact <= 0:
        return 0.0
    raw = min(1.08 * (impact + expl), 10) if scope_changed else min(impact + expl, 10)
    return math.ceil(raw * 10 - 1e-9) / 10


def attention(dep):
    """True when a record belongs in the upgrade queue and counts toward its project's state."""
    if dep.get("scope") in ("indirect", "transitive"):
        return dep.get("status") == "vulnerable"
    return level_rank(dep.get("level")) >= level_rank("info")


def _planned_move(d):
    """The version a declared record's own action moves it to, when the record is work at all."""
    if not d or (d.get("accepted") and not d["accepted"].get("lapsed")) or not attention(d):
        return None
    if d.get("status") == "vulnerable":
        return d.get("fix")
    if d.get("status") in ("major", "minor", "patch"):
        return (d.get("upstream") or {}).get("latest")
    return None


def _drops(graph, r):
    """Whether a resolved graph leaves no copy of r's package that r's fix does not reach."""
    if graph is None:
        return False
    fixed = versions.parse(r["fix"])
    for version, asked in (graph.get(r["name"]) or {}).items():
        got = versions.parse(version)
        if fixed is None or got is None or got < fixed:
            return False
        # npm keeps the copy it has when that copy satisfies the range.
        if any(versions.satisfies(r["version"], q) is not False for q in asked):
            return False
    return True


def lag_days(dep):
    return (dep.get("lag") or {}).get("days")


def known_facts(previous):
    """What the previous file says about exact versions, which a new run can reuse without asking again.

    Returns (packages, fedora): deps.dev's facts keyed by (system, name, version),
    and each Fedora build's source package and license keyed by (release, name, version).
    """
    systems = {"npm": "NPM", "go": "GO", "pypi": "PYPI"}
    packages, fedora = {}, {}
    if not previous or previous.get("schema") != 1:
        return packages, fedora
    for p in previous.get("projects") or []:
        for d in p.get("dependencies") or []:
            eco = "npm" if d.get("datasource") == "npm" else d.get("ecosystem")
            if eco in systems and d.get("version") and not d.get("lag"):
                packages[(systems[eco], d.get("package") or d["name"], d["version"])] = {
                    "licenses": d.get("licenses"), "provenance": d.get("provenance"), "upstream_repo": d.get("upstream_repo")}
            elif eco == "package" and d.get("release") and (d.get("upstream") or {}).get("effective") and d.get("source_package"):
                fedora[(d["release"], d["name"], d["upstream"]["effective"])] = {
                    "source_package": d["source_package"], "licenses": d.get("licenses")}
    return packages, fedora


__all__ = ["Resolver", "known_facts", "attention", "level_rank", "LEVELS", "timedelta"]
