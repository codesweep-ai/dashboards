"""Every source of information the collector reads, and what it takes from each.

The page shows this list, the actions file carries it, and SPEC.md's table
describes the same entries in full; tests/test_resolve.py checks the table
names every one.

The three project sources depend on where a run reads, so `entries` fills in
their addresses: a fork reads its own owner's repositories and its own site.
"""

import urllib.parse

CATALOG = [
    # --- the projects themselves ---------------------------------------------------
    {"id": "github-git", "group": "projects", "name": "GitHub repositories", "url": "https://github.com",
     "gives": "Each project's manifests, commit history and source code, and the tags of actions with no releases",
     "license": "each repository's own", "hosts": ["github.com"]},
    {"id": "status-files", "group": "projects", "name": "Project status files", "url": "https://pages.github.com",
     "gives": "Each project's description", "license": "each project's own", "hosts": []},
    {"id": "previous", "group": "projects", "name": "The previous deps.json", "url": "https://pages.github.com",
     "gives": "Trend history, when each gap was first seen, and facts about versions already looked up",
     "license": "this site's own", "hosts": []},

    # --- versions and releases -----------------------------------------------------
    {"id": "goproxy", "group": "versions", "name": "Go module proxy", "url": "https://proxy.golang.org",
     "gives": "Go module versions and release dates, Go release dates, and the modules govulncheck builds",
     "license": "none stated", "hosts": ["proxy.golang.org", "sum.golang.org"]},
    {"id": "npm", "group": "versions", "name": "npm registry", "url": "https://registry.npmjs.org",
     "gives": "The newest version and source repository of each direct npm package",
     "license": "none stated", "hosts": ["registry.npmjs.org"]},
    {"id": "pypi", "group": "versions", "name": "PyPI", "url": "https://pypi.org",
     "gives": "The newest version of each Python package", "license": "none stated", "hosts": ["pypi.org"]},
    {"id": "maven", "group": "versions", "name": "Maven Central", "url": "https://repo1.maven.org",
     "gives": "Maven releases", "license": "none stated", "hosts": ["repo1.maven.org"]},
    {"id": "nodejs", "group": "versions", "name": "Node.js release index", "url": "https://nodejs.org/dist/index.json",
     "gives": "Node.js releases and their dates", "license": "none stated", "hosts": ["nodejs.org"]},
    {"id": "github-releases", "group": "versions", "name": "GitHub releases", "url": "https://docs.github.com/en/rest/releases",
     "gives": "Releases and their dates for GitHub Actions and for binaries downloaded from GitHub",
     "license": "each repository's own", "hosts": ["api.github.com"]},
    {"id": "registries", "group": "versions", "name": "Container registries",
     "url": "https://github.com/opencontainers/distribution-spec",
     "gives": "The tags of each container image, from Docker Hub, GitHub, Fedora and Google's registries",
     "license": "none stated", "hosts": ["ghcr.io", "auth.docker.io", "registry-1.docker.io", "registry.fedoraproject.org", "gcr.io"]},

    # --- support windows -----------------------------------------------------------
    {"id": "endoflife", "group": "support", "name": "endoflife.date", "url": "https://endoflife.date",
     "gives": "Support and end-of-life dates for each release line, and which packages belong to which product",
     "license": "MIT", "hosts": ["endoflife.date"]},
    {"id": "runner-images", "group": "support", "name": "GitHub runner images", "url": "https://github.com/actions/runner-images",
     "gives": "The image and OS version each hosted runner label runs, and which labels are deprecated",
     "license": "MIT", "hosts": ["raw.githubusercontent.com"]},
    {"id": "firecracker", "group": "support", "name": "Firecracker policies",
     "url": "https://github.com/firecracker-microvm/firecracker/blob/main/docs/RELEASE_POLICY.md",
     "gives": "Firecracker's supported release lines and dates, and the guest kernels each supports",
     "license": "Apache-2.0", "hosts": ["raw.githubusercontent.com"]},

    # --- security ------------------------------------------------------------------
    {"id": "osv", "group": "security", "name": "OSV", "url": "https://osv.dev",
     "gives": "Advisories affecting each Go, npm and PyPI version and the Go standard library, with severity and fixed releases",
     "license": "each source database's, such as CC-BY-4.0 for GitHub's", "hosts": ["api.osv.dev"]},
    {"id": "govulndb", "group": "security", "name": "Go vulnerability database, through govulncheck",
     "url": "https://pkg.go.dev/vuln/",
     "gives": "Whether a project's code calls what a Go advisory names, and which modules its build includes",
     "license": "CC-BY-4.0", "hosts": ["vuln.go.dev"]},
    {"id": "kev", "group": "security", "name": "CISA KEV catalog",
     "url": "https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
     "gives": "Which CVEs are exploited in the wild, and since when", "license": "CC0-1.0",
     "hosts": ["www.cisa.gov", "raw.githubusercontent.com"]},
    {"id": "epss", "group": "security", "name": "FIRST EPSS", "url": "https://www.first.org/epss/",
     "gives": "How likely each CVE is to be exploited within 30 days", "license": "free, with attribution requested",
     "hosts": ["api.first.org"]},
    {"id": "bodhi", "group": "security", "name": "Fedora Bodhi", "url": "https://bodhi.fedoraproject.org",
     "gives": "Fedora security updates pushed since an image was built, and each release's newest kernel build",
     "license": "none stated", "hosts": ["bodhi.fedoraproject.org"]},

    # --- packages and licenses -------------------------------------------------------
    {"id": "depsdev", "group": "licenses", "name": "deps.dev", "url": "https://deps.dev",
     "gives": "Each version's licenses, publish date, deprecation, provenance and repository, each repository's OpenSSF Scorecard, "
              "and what installing an npm version brings in",
     "license": "CC-BY-4.0", "hosts": ["api.deps.dev"]},
    {"id": "clearlydefined", "group": "licenses", "name": "ClearlyDefined", "url": "https://clearlydefined.io",
     "gives": "The licenses scans found in each shipped package's files, and a license score",
     "license": "CC0-1.0", "hosts": ["api.clearlydefined.io"]},
    {"id": "licensedb", "group": "licenses", "name": "ScanCode LicenseDB", "url": "https://scancode-licensedb.aboutcode.org",
     "gives": "The category of a license the policy does not name, as a hint", "license": "CC-BY-4.0",
     "hosts": ["scancode-licensedb.aboutcode.org"]},
    {"id": "mdapi", "group": "licenses", "name": "Fedora mdapi", "url": "https://mdapi.fedoraproject.org",
     "gives": "The version a Fedora package name resolves to in a release, and the packages built beside it",
     "license": "none stated", "hosts": ["mdapi.fedoraproject.org"]},
    {"id": "koji", "group": "licenses", "name": "Fedora Koji", "url": "https://koji.fedoraproject.org",
     "gives": "The source package each Fedora binary package was built from", "license": "none stated",
     "hosts": ["koji.fedoraproject.org"]},
    {"id": "distgit", "group": "licenses", "name": "Fedora dist-git", "url": "https://src.fedoraproject.org",
     "gives": "The license each Fedora source package's spec file states", "license": "each package's own",
     "hosts": ["src.fedoraproject.org"]},
]

GROUPS = {"projects": "The projects", "versions": "Versions and releases", "support": "Support windows",
          "security": "Security", "licenses": "Packages and licenses"}


def entries(owner, site=None, previous=None):
    """The catalog for one run, with the project sources at the addresses it read.

    With no site, no status file is read, so that entry is left out. The previous
    file is named by its URL when it came from one, and otherwise by where the
    site publishes it.
    """
    out = []
    for c in CATALOG:
        c = dict(c)
        if c["id"] == "github-git":
            c["url"] = f"https://github.com/{owner}"
        elif c["id"] == "status-files":
            if not site:
                continue
            c["url"] = urllib.parse.urljoin(site, "/")
            c["hosts"] = [urllib.parse.urlparse(site).hostname]
        elif c["id"] == "previous":
            url = previous if (previous or "").startswith("https://") else \
                (urllib.parse.urljoin(site, "deps.json") if site else None)
            if not url:
                continue
            c["url"] = url
        out.append(c)
    return out
