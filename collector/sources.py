"""The public, keyless sources a dependency's upstream is read from.

Each method answers one question about one upstream and returns plain data. A
source that cannot answer raises http.FetchError, and resolve.py turns that into
a dependency marked unresolved rather than into a guess.
"""

import os
import re
import subprocess
import threading
import xmlrpc.client
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from . import versions
from .http import FetchError

GOPROXY = "https://proxy.golang.org"
NPM = "https://registry.npmjs.org"
DEPSDEV = "https://api.deps.dev/v3"
OSV = "https://api.osv.dev/v1"
EOL = "https://endoflife.date/api"
BODHI = "https://bodhi.fedoraproject.org"
KEV = ("https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
       "https://raw.githubusercontent.com/cisagov/kev-data/main/known_exploited_vulnerabilities.json")
EPSS = "https://api.first.org/data/v1/epss"
LICENSEDB = "https://scancode-licensedb.aboutcode.org/index.json"
CLEARLYDEFINED = "https://api.clearlydefined.io/definitions"


class _TimeoutTransport(xmlrpc.client.SafeTransport):
    """An XML-RPC transport that gives up, as every other request here does, rather than hang a build."""

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = 30
        return conn


def _enc(s):
    return urllib.parse.quote(s, safe="")


def iso(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = parse_time(dt)
        if dt is None:
            return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    s = str(s).strip().replace(" ", "T")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        s += "T00:00:00"
    s = re.sub(r"\.\d+", "", s)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Sources:
    def __init__(self, client, token=None):
        self.http = client
        self.token = token

    # --- Go module proxy ------------------------------------------------------

    @staticmethod
    def _escape_module(path):
        return re.sub(r"[A-Z]", lambda m: "!" + m.group(0).lower(), path)

    def go_latest(self, module):
        data = self.http.json(f"{GOPROXY}/{self._escape_module(module)}/@latest", accept_missing=True)
        if not data:
            return None
        return {"version": data.get("Version"), "time": data.get("Time")}

    def go_versions(self, module):
        text = self.http.text(f"{GOPROXY}/{self._escape_module(module)}/@v/list", accept_missing=True)
        return [v for v in (text or "").split() if v]

    def go_time(self, module, version):
        data = self.http.json(f"{GOPROXY}/{self._escape_module(module)}/@v/{_enc(version)}.info",
                              accept_missing=True)
        return (data or {}).get("Time")

    def go_next_major(self, module, current_major):
        """The latest version under the next major's module path, if one exists."""
        base = re.sub(r"/v\d+$", "", module)
        nxt = max(current_major, 1) + 1
        try:
            found = self.go_latest(f"{base}/v{nxt}")
        except FetchError as exc:
            if exc.status in (404, 410):
                return None
            raise
        return found["version"] if found else None

    # --- the Go toolchain -------------------------------------------------------

    def go_toolchain_time(self, version):
        """When go<version> was released, from the toolchain module the proxy serves."""
        data = self.http.json(f"{GOPROXY}/golang.org/toolchain/@v/v0.0.1-go{version}.linux-amd64.info",
                              accept_missing=True)
        return (data or {}).get("Time")

    # --- npm ------------------------------------------------------------------

    def npm_latest(self, name):
        data = self.http.json(f"{NPM}/{_enc(name)}/latest", accept_missing=True)
        if not data:
            return None
        repo = data.get("repository")
        return {"version": data.get("version"),
                "repository": repo.get("url") if isinstance(repo, dict) else repo}

    def npm_dist_tags(self, name):
        data = self.http.json(f"{NPM}/{_enc(name)}",
                              headers={"Accept": "application/vnd.npm.install-v1+json"}, accept_missing=True)
        return (data or {}).get("dist-tags", {})

    def npm_versions(self, name):
        """Every version the registry holds, from the same document as the dist-tags."""
        data = self.http.json(f"{NPM}/{_enc(name)}",
                              headers={"Accept": "application/vnd.npm.install-v1+json"}, accept_missing=True)
        return list((data or {}).get("versions", {}))

    def npm_version(self, name, version):
        """publishedAt and deprecation for one version, from deps.dev."""
        # The same URL depsdev_versions asks, so the run's cache answers the second time.
        data = self.http.json(f"{DEPSDEV}/systems/NPM/packages/{_enc(name)}/versions/{_enc(version)}",
                              accept_missing=True)
        if not data:
            return {}
        return {"published": data.get("publishedAt"), "deprecated": bool(data.get("isDeprecated")),
                "deprecated_reason": data.get("deprecatedReason") or None}

    def depsdev_versions(self, keys):
        """Licenses, deprecation, provenance and publish dates for many versions.

        `keys` is a list of (system, name, version), system being NPM, GO or
        PYPI. Returns {key: {...}}; a version deps.dev does not know is absent.
        Each is one GetVersion call on deps.dev's stable v3 API, whose batch
        method exists only in an alpha that may change without notice.
        """
        from concurrent.futures import ThreadPoolExecutor

        def one(key):
            system, name, version = key
            v = self.http.json(f"{DEPSDEV}/systems/{system}/packages/{_enc(name)}/versions/{_enc(version)}",
                               accept_missing=True)
            if not v:
                return key, None
            source = next((l["url"] for l in v.get("links", []) if l.get("label") == "SOURCE_REPO"), None)
            return key, {
                "licenses": [d.get("spdx") or d.get("license") for d in v.get("licenseDetails", [])] or v.get("licenses") or [],
                "published": v.get("publishedAt"),
                "deprecated": v.get("deprecatedReason") or ("deprecated by its publisher" if v.get("isDeprecated") else None),
                "provenance": bool(v.get("slsaProvenances") or v.get("attestations")),
                "source_repo": source,
            }
        with ThreadPoolExecutor(max_workers=8) as pool:
            return {k: info for k, info in pool.map(one, keys) if info}

    def depsdev_project(self, repo):
        """A source repository's license and OpenSSF Scorecard, from deps.dev: github.com/owner/name."""
        data = self.http.json(f"{DEPSDEV}/projects/{_enc(repo)}", accept_missing=True)
        if not data:
            return None
        sc = data.get("scorecard") or {}
        return {
            "license": data.get("license") or None,
            "stars": data.get("starsCount"),
            "scorecard": {
                "score": sc.get("overallScore"),
                "date": (sc.get("date") or "")[:10] or None,
                "checks": {c["name"]: c.get("score") for c in sc.get("checks", [])},
            } if sc else None,
        }

    # --- PyPI -----------------------------------------------------------------

    def pypi(self, name):
        data = self.http.json(f"https://pypi.org/pypi/{_enc(name)}/json", accept_missing=True)
        if not data:
            return None
        v = data["info"]["version"]
        files = data.get("releases", {}).get(v) or [{}]
        return {"version": v, "time": files[0].get("upload_time_iso_8601")}

    # --- GitHub releases and tags ---------------------------------------------

    def _github_auth(self):
        """The build's own token for GitHub's hosts, where one is set: runner IPs share anonymous limits."""
        return {"Authorization": f"Bearer {self.token}"} if self.token else None

    def github_releases(self, repo):
        """Published releases, newest first: [{tag, published, prerelease, url}].

        With a token this is the REST API. Without one it is the Atom feed
        github.com serves every repository, which needs no key and spends no
        API quota but lists only the recent releases.
        """
        if self.token:
            data = self.http.json(f"https://api.github.com/repos/{repo}/releases?per_page=100",
                                  headers={"Authorization": f"Bearer {self.token}",
                                           "Accept": "application/vnd.github+json"},
                                  accept_missing=True) or []
            return [{"tag": r["tag_name"], "published": r.get("published_at"),
                     "prerelease": bool(r.get("prerelease")), "url": r.get("html_url")}
                    for r in data if not r.get("draft")]
        text = self.http.text(f"https://github.com/{repo}/releases.atom", accept_missing=True)
        if not text:
            return []
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = []
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise FetchError(f"https://github.com/{repo}/releases.atom", f"unreadable feed: {exc}")
        for entry in root.findall("a:entry", ns):
            link = entry.find("a:link", ns)
            href = link.get("href") if link is not None else ""
            tag = urllib.parse.unquote(href.rsplit("/tag/", 1)[-1]) if "/tag/" in href else None
            if not tag:
                continue
            out.append({"tag": tag, "published": (entry.findtext("a:updated", namespaces=ns)),
                        "prerelease": versions.looks_prerelease(tag), "url": href})
        return out

    def github_tags(self, repo):
        """Every tag and the commit it points at, over git: {tag: sha}."""
        from .gitrepo import git_env
        proc = subprocess.run(["git", "ls-remote", "--tags", f"https://github.com/{repo}"],
                              capture_output=True, text=True, timeout=60, env=git_env())
        if proc.returncode != 0:
            self.http._count(f"https://github.com/{repo}", True, "git ls-remote failed")
            raise FetchError(f"https://github.com/{repo}", "git ls-remote failed")
        self.http._count(f"https://github.com/{repo}", False)
        tags = {}
        for line in proc.stdout.splitlines():
            sha, _, ref = line.partition("\t")
            name = ref.removeprefix("refs/tags/")
            if name.endswith("^{}"):
                tags[name[:-3]] = sha  # the peeled commit wins over the tag object
            else:
                tags.setdefault(name, sha)
        return tags

    # --- release-cycle data -----------------------------------------------------

    def cycles(self, product):
        """endoflife.date's cycles for a product: [{cycle, releaseDate, eol, support, lts, latest, …}].

        Read from its v1 API. Each date field is the date when one is published,
        and otherwise whether that point has passed, as the v0 fields were.
        """
        data = self.http.json(f"{EOL}/v1/products/{_enc(product)}", accept_missing=True) or {}
        out = []
        for r in (data.get("result") or {}).get("releases") or []:
            latest = r.get("latest") or {}
            out.append({
                "cycle": r.get("name"), "codename": r.get("codename"), "releaseDate": r.get("releaseDate"),
                "eol": r.get("eolFrom") or bool(r.get("isEol")),
                "support": r.get("eoasFrom") or bool(r.get("isEoas")),
                "lts": r.get("ltsFrom") or bool(r.get("isLts")),
                "extendedSupport": r.get("eoesFrom") or bool(r.get("isEoes")),
                "latest": latest.get("name"), "latestReleaseDate": latest.get("date"),
            })
        return out

    def node_releases(self):
        return self.http.json("https://nodejs.org/dist/index.json") or []

    def maven_versions(self, coordinate):
        group, artifact = coordinate.split(":")
        url = f"https://repo1.maven.org/maven2/{group.replace('.', '/')}/{artifact}/maven-metadata.xml"
        text = self.http.text(url, accept_missing=True)
        if not text:
            return []
        return [v.text for v in ET.fromstring(text).iter("version") if v.text]

    def eol_products_by_purl(self):
        """endoflife.date's index of package identifiers: {purl without version: product}."""
        data = self.http.json(f"{EOL}/v1/identifiers/purl", accept_missing=True) or {}
        return {r["identifier"].split("@")[0].lower(): r["product"]["name"]
                for r in data.get("result", []) if r.get("identifier")}

    # --- GitHub-hosted runners ------------------------------------------------------

    def runner_images(self):
        """GitHub's table of hosted runner images: {label: {image, os, version, arch, deprecated}}.

        Read from the README of actions/runner-images, which is where GitHub
        publishes which image `macos-latest` currently means.
        """
        text = self.http.text("https://raw.githubusercontent.com/actions/runner-images/main/README.md", headers=self._github_auth())
        out = {}
        for row in (text or "").splitlines():
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            if len(cells) < 3 or not cells[2].startswith("`"):
                continue
            name = re.sub(r"\[?!\[.*$|<br>.*$", "", cells[0]).strip()
            m = re.match(r"^(Ubuntu|macOS|Windows Server|Windows|Xcode)\s+([\d.]+|Slim)", name)
            if not m:
                continue
            os_key = {"Ubuntu": "ubuntu", "macOS": "macos", "Windows Server": "windows", "Windows": "windows-client",
                      "Xcode": "xcode"}[m.group(1)]
            info = {"image": name, "os": os_key, "version": m.group(2), "arch": cells[1],
                    "deprecated": "deprecated" in cells[0].lower(), "preview": "preview" in cells[0].lower()}
            for label in re.findall(r"`([^`]+)`", cells[2]):
                out[label] = info
        return out

    # --- Firecracker's published support policy -------------------------------------

    def firecracker_policy(self):
        """Firecracker's release and guest-kernel support tables, from its docs."""
        base = "https://raw.githubusercontent.com/firecracker-microvm/firecracker/main/docs"
        releases, guests = {}, {}
        for row in (self.http.text(f"{base}/RELEASE_POLICY.md", headers=self._github_auth(), accept_missing=True) or "").splitlines():
            m = re.match(r"^\|\s*v(\d+\.\d+)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*v?([\d.]+)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*\|", row)
            if m:
                official = re.match(r"(\d{4}-\d{2}-\d{2})", m.group(5))
                releases[m.group(1)] = {"release": m.group(2), "latest": m.group(3), "min_support": m.group(4),
                                        "eol": official.group(1) if official else None}
        text = self.http.text(f"{base}/kernel-policy.md", headers=self._github_auth(), accept_missing=True) or ""
        table = text.split("Guest kernel", 1)[1] if "Guest kernel" in text else ""
        for row in table.splitlines():
            m = re.match(r"^\|\s*\w+\s*\|\s*v(\d+\.\d+)\s*\|\s*v?([\d.]+)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|", row)
            if m:
                guests[m.group(1)] = {"min_firecracker": m.group(2), "min_support": m.group(3)}
            elif guests and not row.strip().startswith("|"):
                break
        return {"releases": releases, "guest_kernels": guests,
                "url": "https://github.com/firecracker-microvm/firecracker/blob/main/docs/RELEASE_POLICY.md",
                "kernel_url": "https://github.com/firecracker-microvm/firecracker/blob/main/docs/kernel-policy.md"}

    # --- container registries ---------------------------------------------------

    def oci_tags(self, name):
        """Every tag of an image, from the registry's own API, anonymously."""
        registry, _, repo = name.partition("/")
        host = "registry-1.docker.io" if registry == "docker.io" else registry
        headers = {}
        if registry == "ghcr.io":
            tok = self.http.json(f"https://ghcr.io/token?scope=repository:{repo}:pull&service=ghcr.io")
            headers["Authorization"] = f"Bearer {tok['token']}"
        elif registry == "docker.io":
            tok = self.http.json("https://auth.docker.io/token?service=registry.docker.io"
                                 f"&scope=repository:{repo}:pull")
            headers["Authorization"] = f"Bearer {tok['token']}"
        data = self.http.json(f"https://{host}/v2/{repo}/tags/list?n=10000", headers=headers,
                              accept_missing=True)
        return (data or {}).get("tags") or []

    # --- Fedora -------------------------------------------------------------------

    def fedora_package(self, release, name):
        """The newest build of a binary package in Fedora `release`, and its source package.

        Asks mdapi for the updates repository first, then the release. A name dnf
        resolves through a provide (`vim` installs vim-enhanced) is not a package
        name, and mdapi has no reverse lookup, so it answers None.
        """
        for branch in (f"f{release}-updates", f"f{release}"):
            data = self.http.json(f"https://mdapi.fedoraproject.org/{branch}/pkg/{_enc(name)}", accept_missing=True)
            if data and data.get("version"):
                return {"version": f"{data['version']}-{data['release']}", "repo": data.get("repo"),
                        "co_packages": data.get("co-packages") or [],
                        "nvra": f"{name}-{data['version']}-{data['release']}.{data.get('arch') or 'x86_64'}"}
        return None

    _koji = threading.local()

    def koji_source(self, nvra):
        """The source package a Fedora build came from, from Koji's public XML-RPC hub."""
        url = "https://koji.fedoraproject.org/kojihub"
        from .http import GIVE_UP_AFTER
        if self.http._down.get("koji.fedoraproject.org", 0) >= GIVE_UP_AFTER:
            self.http._count(url, True, "not tried: the host failed every recent request")
            return None
        hub = getattr(self._koji, "hub", None)
        if hub is None:
            hub = self._koji.hub = xmlrpc.client.ServerProxy(url, allow_none=True, transport=_TimeoutTransport())
        rpm = build = None
        for attempt in range(2):
            try:
                rpm = hub.getRPM(nvra)
                build = hub.getBuild(rpm["build_id"]) if rpm else None
                break
            except (xmlrpc.client.Error, OSError) as exc:
                # Koji answers a 502 now and then; one more try, then the name rules decide.
                if attempt:
                    self.http._count(url, True, f"koji: {exc}")
                    with self.http._lock:
                        self.http._down["koji.fedoraproject.org"] = self.http._down.get("koji.fedoraproject.org", 0) + 1
                    return None
                hub = self._koji.hub = xmlrpc.client.ServerProxy(url, allow_none=True, transport=_TimeoutTransport())
        self.http._count(url, False)
        self.http._down["koji.fedoraproject.org"] = 0
        return (build or {}).get("package_name")

    def fedora_license(self, release, candidates):
        """The source package and its spec's License tag, an SPDX expression, from dist-git.

        A binary package does not name its source package, so candidates are
        tried in order until a spec answers: (source, expression) or (None, None).
        """
        for source in candidates:
            text = self.http.text(f"https://src.fedoraproject.org/rpms/{_enc(source)}/raw/f{release}/f/{_enc(source)}.spec",
                                  accept_missing=True)
            if not text:
                continue
            if text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
                # dist-git sits behind a bot filter whose challenge page answers 200.
                self.http._count("https://src.fedoraproject.org", True, "an HTML page instead of a spec file")
                raise FetchError(f"https://src.fedoraproject.org/rpms/{source}", "an HTML page instead of a spec file")
            m = re.search(r"^License:\s*(.+?)\s*$", text, re.MULTILINE)
            if not m:
                return source, None
            # A conditional such as %{?with_bundled_x: AND Apache-2.0} adds a
            # license only in an optional build, so it is left out.
            expression = re.sub(r"%\{\?[^}]*\}", "", m.group(1)).strip()
            return source, expression if "%" not in expression else None
        return None, None

    def bodhi_security_since(self, release, since):
        """Stable security updates pushed to Fedora `release` since a moment."""
        out, page = [], 1
        while True:
            q = urllib.parse.urlencode({"releases": f"F{release}", "type": "security", "status": "stable",
                                        "pushed_since": since.strftime("%Y-%m-%dT%H:%M:%S"),
                                        "rows_per_page": 100, "page": page})
            data = self.http.json(f"{BODHI}/updates/?{q}")
            for u in data.get("updates", []):
                out.append({
                    "id": u.get("alias"),
                    "severity": (u.get("severity") or "unspecified").lower(),
                    "pushed": u.get("date_pushed"),
                    "builds": [b.get("nvr") for b in u.get("builds", [])],
                    "url": f"{BODHI}/updates/{u.get('alias')}",
                    "summary": (u.get("title") or "").strip(),
                })
            if page >= int(data.get("pages") or 1):
                return out
            page += 1

    def bodhi_latest(self, release, package):
        """The newest stable build of a source package in Fedora `release`: (nvr, pushed)."""
        q = urllib.parse.urlencode({"packages": package, "releases": f"F{release}", "status": "stable",
                                    "rows_per_page": 10})
        data = self.http.json(f"{BODHI}/updates/?{q}")
        best = None
        for u in data.get("updates", []):
            for b in u.get("builds", []):
                name, _, vr = b.get("nvr", "").partition(f"{package}-")
                if name or not vr:
                    continue
                v, _ = versions.nvr(vr)
                if v and (best is None or best[0] < v):
                    best = (v, vr, u.get("date_pushed"))
        return (best[1], best[2]) if best else (None, None)

    # --- vulnerabilities ------------------------------------------------------------

    def osv_batch(self, queries):
        """The vulnerability ids affecting each (ecosystem, name, version) query."""
        out = []
        for i in range(0, len(queries), 500):
            chunk = queries[i:i + 500]
            body = {"queries": [{"package": {"ecosystem": e, "name": n}, "version": v} for e, n, v in chunk]}
            data = self.http.json(f"{OSV}/querybatch", data=body)
            for res in data.get("results", []):
                out.append([v["id"] for v in (res.get("vulns") or [])])
        return out

    def osv_vuln(self, vid):
        return self.http.json(f"{OSV}/vulns/{_enc(vid)}")

    def kev(self):
        """CISA's catalog of known exploited vulnerabilities: {CVE id: date it was added}.

        cisagov publishes the same file on GitHub, which answers when cisa.gov
        turns a build runner away.
        """
        last = None
        for url in KEV:
            try:
                data = self.http.json(url, headers=self._github_auth() if "githubusercontent" in url else None)
                return {v["cveID"]: v.get("dateAdded") for v in data.get("vulnerabilities", []) if v.get("cveID")}
            except FetchError as exc:
                last = exc
        raise last

    def epss(self, cves):
        """FIRST's exploit prediction for each CVE: {id: (probability, percentile)}. Unscored ids are absent."""
        out = {}
        for i in range(0, len(cves), 80):
            chunk = cves[i:i + 80]  # the API reads a cve= list of up to 2,000 characters
            data = self.http.json(f"{EPSS}?cve={','.join(chunk)}&limit={len(chunk)}") or {}
            for row in data.get("data", []):
                try:
                    out[row["cve"]] = (float(row["epss"]), float(row["percentile"]))
                except (KeyError, ValueError):
                    continue
        return out

    # --- licenses ---------------------------------------------------------------

    def license_categories(self):
        """ScanCode LicenseDB's category for every license it knows, keyed by SPDX id and LicenseRef.

        One static file: Permissive, Copyleft, Copyleft Limited, Proprietary Free
        and the rest, with each license's other SPDX ids folded in.
        """
        out = {}
        for lic in self.http.json(LICENSEDB) or []:
            category = lic.get("category")
            if not category:
                continue
            keys = [lic.get("spdx_license_key"), *(lic.get("other_spdx_license_keys") or []),
                    f"LicenseRef-scancode-{lic.get('license_key')}"]
            for k in keys:
                if k:
                    out.setdefault(k, category)
        return out

    def clearlydefined(self, coordinates):
        """What ClearlyDefined knows of each package: licenses declared, licenses its scans found, and a score.

        `coordinates` are type/provider/namespace/name/revision strings. File
        listings are left out of the reply, which keeps a batch small.
        """
        out = {}
        for i in range(0, len(coordinates), 100):
            chunk = coordinates[i:i + 100]
            data = self.http.json(f"{CLEARLYDEFINED}?expand=-files", data=chunk) or {}
            for coord, definition in data.items():
                lic = (definition or {}).get("licensed") or {}
                discovered = (((lic.get("facets") or {}).get("core") or {}).get("discovered") or {}).get("expressions") or []
                if not lic.get("declared") and not discovered:
                    continue
                out[coord] = {"declared": lic.get("declared"), "discovered": discovered,
                              "score": (lic.get("score") or {}).get("total")}
        return out
