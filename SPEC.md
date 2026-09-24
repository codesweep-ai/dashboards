# Specification

What a project must publish for a dashboard to read it, and what a dashboard may
assume in return. This is the only contract between the two. A project that
satisfies it needs to know nothing about the pages, and a page needs to know
nothing about how a project builds.

It also describes the dependencies file, `deps.json`: the one file the
dependencies page reads. This site's own build writes it, so it asks nothing of a
project.

## The status file

Each project publishes one file to its own Pages site, at the root:

```
https://codesweep.ai/<project>/ci-status.json
```

It is written by `action/ci-status` during that project's Pages build, from that
project's own workflow runs. See [README.md](README.md) for how a project wires
it up.

### Shape

```jsonc
{
  "schema": 2,                       // integer, bumped on a breaking change
  "generated": "2026-09-10T07:00:00Z",  // when this file was written, UTC
  "window": 20,                      // the cap on runs summarised per workflow
  "repo": {
    "name": "lint",
    "full_name": "codesweep-ai/lint",
    "description": "…",              // may be empty
    "url": "https://github.com/codesweep-ai/lint",
    "branch": "main",                // the branch the runs are from
    "pushed_at": "2026-09-09T22:00:00Z"  // copied from the repository, may be null
  },
  "built": [                         // the newest passing push builds of ci, newest first
    {
      "commit": "70fa2864…",         // its full SHA
      "versions": { /* see below */ } // what it was published under
    }
  ],
  "workflows": [ /* see below */ ]
}
```

Each entry in `workflows` has this shape:

```jsonc
{
  "name": "ci",
  "path": ".github/workflows/ci.yml", // "" when the workflow is gone but its runs remain
  "declared": true,                   // active in .github/workflows
  "latest": { /* run, or null when it has never run */ },
  "runs": [ /* newest first, at most `window` of them */ ],
  "counted": 20,                      // entries in `runs` that reached a verdict
  "pass_rate": 85,                    // whole percent of `counted`, or null when counted is 0
  "failures": 3,                      // of `counted`
  "median_duration": 218,             // seconds across `counted`, or null
  "green_streak": 12,                 // passes at the tip of `counted`
  "last_failure": { /* run, or null — from all history, not only `runs` */ }
}
```

Each run:

```jsonc
{
  "state": "success",                 // conclusion, or status when still running
  "started": "2026-09-09T21:00:00Z",
  "duration": 218,                    // seconds, or null
  "title": "Bump dependencies",
  "sha": "70fa286",                   // the first 7 characters of the head SHA
  "event": "push",
  "attempt": 1,
  "actor": "octocat",
  "url": "https://github.com/…/actions/runs/123",
  "commit": "70fa2864…",              // the full head SHA
  "versions": { /* see below */ }     // what that commit was published under
}
```

The versions a commit was published under, in each `built` entry and in every run:

```jsonc
{
  "go": "v0.0.0-20260923200616-70fa28640a2b",   // Go's version of it, or null
  "images": {                                   // ghcr.io/<owner>/<repository>: tag
    "npm/lint": "0.0.0-20260923200616-70fa28640a2b"
  },
  "npm": {                                      // package on npmjs.com: version
    "@codesweep-ai/lint": "0.0.0-20260923200616-70fa28640a2b"
  }
}
```

Workflows are ordered `ci` first, then by name, and a page renders them in the
order it is given. The primary gate leads because it is the one a reader came
for.

### Rules

- **`schema` is the compatibility gate.** Add optional fields freely; a page must
  ignore what it does not recognise. Removing a field, or changing what one
  means, is a bump. `scripts/check-status.py` enforces the version. A page
  checks instead that the structure it needs is there, so a file it cannot read
  becomes a "no status" card rather than a broken page.
- **Times are UTC, `YYYY-MM-DDTHH:MM:SSZ`.** They carry no offset and no
  fractional seconds.
- **Durations are whole seconds**, or `null` when they cannot be computed.
- **`null` means "not applicable", never zero.** A workflow that has never run has
  `latest: null`, not a synthetic run; a rate over no runs is `null`, not `0`.
- **A verdict is a pass or a failure, and nothing else.** `success` passes.
  `failure`, `timed_out` and `startup_failure` fail. Nothing else is a verdict:
  not `cancelled`, `skipped`, `neutral`, `action_required` or `stale`, and not a
  run still in flight. Such a run appears in `runs` and stays out of `counted`,
  and so out of everything derived from it.
- **The window bounds the arithmetic, not the history.** `counted`, `pass_rate`,
  `failures`, `median_duration` and `green_streak` are computed over `runs`,
  which holds at most `window` entries. `latest` and `last_failure` are drawn
  from everything read, so a workflow may report `failures: 0` and still name a
  `last_failure` older than the window. That is deliberate: the window says how
  the workflow behaves lately, and `last_failure` says when it last broke.
- **`green_streak` counts verdicts.** It is the run of passes from the newest
  verdict backwards, so a cancelled or skipped run between two passes does not
  break it, and it never exceeds `counted`.
- **Only the default branch is summarised.** The runs read are those the API
  reports for the repository's default branch. A pull-request run is recorded
  against its source instead, so it falls outside.
- **At most the 100 most recent runs are read**, in one API page, before the
  window is applied per workflow. A repository that builds often enough to fill
  that page may report fewer than `window` runs for a rarely run workflow.
- **One API call, in the repository's own job.** The run history above is the
  only call to GitHub's API there. The workflows come from the checkout's
  `.github/workflows`, each named by its top-level `name:` or its path, so one
  switched off by hand is still listed. The module path comes from the
  checkout's `go.mod`, and the repository's details from the event that started
  the run. Whether a build is still on the branch is asked of git, which needs a
  checkout with the branch's history: `fetch-depth: 0` with `filter: blob:none`
  brings it without the file contents. Run anywhere else, the action asks the
  API for each of these instead.
- **`built` lists the commits a sibling may pin, newest first.** Each is a
  commit that a push to the branch started a run of `ci` for, and that run
  passed. The branch still holds it, and it is named once, however many times it
  was built. The list holds at most 10, found among the newest 30 such runs. It is
  empty when no run qualifies. A commit that changed nothing CI builds has no
  run, and a failed or unfinished build is passed over, so the first entry may
  be older than the branch's tip. `ci`'s runs are read from the page above. They
  are asked for on their own only when that page holds none that passed, as
  after a long stretch of pushes that change only the ledger.
- **A project that publishes images needs them before a commit is built.** When
  it declares a `publish images` workflow, the commit's version also has to be in
  every image repository the project publishes to. That is read from the
  registry's tag lists, with no call to GitHub. The workflow pushes that version tag last: an npm
  package's wrapper after its platform packages, and a sandbox image's list once
  both architectures are in. A version pruned since drops its commit out of the
  list. The run itself cannot say which commit it published: GitHub lists a run
  started by `ci` finishing under whatever commit is the branch's head by then.
  The `publish images` commit status the workflow posts on the commit is for a
  reader of GitHub, and the file does not read it.
- **A lookup that fails does not shorten the list.** Where GitHub's API fails for
  any reason but the commit's being unknown, the file is not written, rather than
  written without a build it should name. A registry that does not answer lists
  no image repository, and so names no build.
- **A sibling pins the first entry.** Its `make repin` reads the first `commit`
  inside `built` with `sed`, so each stays on a line of its own. A sibling that
  pushed a commit and pins it once built waits for the commit to appear
  anywhere in the list, so a newer build does not hide it.
- **`versions` says what a commit was published under**, which is what a pin on
  it names. `go` is the version Go's module proxy gives the commit of the module
  the repository's `go.mod` declares. It is what `go get` records and what a
  binary built from the commit reports, and `null` for a project that is no Go
  module. `images` and `npm` hold every published version whose name ends with
  the commit, as a dev version's does. That ending is its first twelve
  characters after a hyphen, or its first seven after a dot. The places are named after the project:
  images `npm/<name>`, `<name>` and `<name>-slim` under its owner on ghcr.io,
  and the npm package `@<owner>/<name>`. A place that does not exist, or has
  nothing for the commit, is left out, and a registry that does not answer
  leaves its entries out too. None of these lookups goes through GitHub's API.
- **The Pages build GitHub generates is never reported.** It is not CI, and not
  the repository's to report.
- **The run writing the file is never reported.** It is in flight while it reads
  the API, and would otherwise put a workflow that never finishes on the card.
  Its workflow's finished runs are still reported: whatever publishes the status
  can fail, and that failure is worth seeing.

## The index

`projects.json` in this repository says where the status files are:

```jsonc
{
  "schema": 1,
  "title": "codesweep-ai CI",         // optional, used in the document title
  "projects": [
    { "name": "lint", "status": "../lint/ci-status.json" }
  ]
}
```

- **`status` is a relative path**, resolved against `projects.json`, ending in
  `.json`. It carries no scheme, which is what keeps it same-origin, for the
  reason below.
- **`name` is unique**, and is what a project is called on the page when its file
  cannot be read and the file's own `repo.name` is therefore unavailable.
- **Unrecognised fields are ignored**, here as in the status file. `projects.json`
  carries a `comment` that nothing reads.

`scripts/check-projects.py` enforces the first two.

## The dependencies file

The dependencies page reads one file, published beside it:

```
https://codesweep.ai/dashboards/deps.json
```

No project writes it. This site's own build writes it with `python3 -m collector`, on every push and once
a day. The collection is its own workflow, `dashboard-deps.yml`, which `pages.yml` calls before it
assembles and deploys the site. The collector reads each project's default branch over plain git, and asks public registries about
every dependency it finds there. It needs no API key. [README.md](README.md) says why it runs on a
schedule when nothing else here does.

### What the collector reads

| Kind | Declared in | Upstream read from |
|---|---|---|
| Go modules | `go.mod`, `go.<name>.mod`, `go install x@v` | proxy.golang.org |
| npm packages | `package.json`, resolved through `package-lock.json` | registry.npmjs.org, with release dates from deps.dev |
| Python packages | `pip install` | pypi.org |
| GitHub Actions | `uses:` in workflows and in `action.yml`, and installs in `run:` steps | GitHub releases, or tags where a project publishes no releases |
| Toolchains | the `go` directive, `go-version`, `node-version`, `nvm install`, `pyenv install`, download URLs | endoflife.date, the Go module proxy, nodejs.org, Maven Central |
| Images and runners | `FROM`, `image:`, image references in `.env` files, `runs-on` and matrix `os:` labels, a WSL `distribution:` | GitHub's runner-images table for what a label runs, endoflife.date for an OS release, the registry for tags |
| System packages | `dnf install` in a Containerfile | Fedora's mdapi for the version each name resolves to, and Bodhi for security updates |
| Native and binaries | an `ARG *_VERSION` whose download URL names its upstream, a Renovate annotation, a declared pin | GitHub releases, or the datasource the pin names; Firecracker's own support tables |
| Vendored code | an AboutCode `.ABOUT` file beside the copy | the registry its `package_url` names, or the upstream its `download_url` names |

Release lines for anything else come from endoflife.date's index of package identifiers, which knows
React and ESLint by their npm names. Vulnerabilities come from api.osv.dev for Go modules, the Go standard library, every package an npm
lockfile installs, and PyPI. Manifests under a directory named `testdata`, `cassettes`, `examples`,
`vendor` or `node_modules` are skipped, since they describe something other than the project.

Install scripts come from the `hasInstallScript` flag npm writes into a lockfile. The table below names
every source and what each gives.

### Running the collector

The collector is `python3 -m collector`, run from this repository's root. It is standard-library Python,
and it needs `git`. Reachability also needs Go, which builds govulncheck from this repository's
`go.mod`. A token in `GH_TOKEN` or `GITHUB_TOKEN` is used for GitHub when present, and nothing needs one.

| Option | Default | What it does |
|---|---|---|
| `--projects` | `projects.json` | the projects to read |
| `--config` | `deps-config.json` | pins, snapshots and policies |
| `--output` | `deps.json` | where the dependencies file goes; `-` for standard output |
| `--actions` | | also write the actions file |
| `--feed` | | also write the Atom feed |
| `--sbom` | | also write the CycloneDX SBOM |
| `--previous` | | an earlier dependencies file, as a path or URL, for history and known facts |
| `--only` | every project | comma-separated project names |
| `--keep` | a temporary directory | clone here and leave the clones |
| `--jobs` | 12 | records resolved at once |
| `--no-reachability` | | skip govulncheck |
| `--owner` | `$GITHUB_REPOSITORY_OWNER`, else `org` | the GitHub owner whose repositories are read |
| `--site` | the config's `site`, when the owner is `org` | where this site is published, for status files and links |

The site's build runs it with `--actions`, `--feed` and `--sbom`. It passes `--site` as the address this
repository's Pages settings give, and `--previous` as the dependencies file published there.

**A fork reads its own projects.** `org` names the namespace of the Go modules, npm packages and images
that count as siblings, and a fork does not rename those. `--owner` names whose repositories are cloned,
and `--site` where the status files and the previous file are read. So a fork's build reads the fork's
repositories and its own history, and still knows `github.com/codesweep-ai/ledger` for a sibling. With
no site, no status file is read and the links the collector writes are relative.

The collector clones nothing for an owner that cannot be a GitHub name, such as a path, or for a
`$GITHUB_REPOSITORY` that is not `owner/name`. It exits 2 and names the setting, because the clone would
otherwise fail as `Authentication failed`, which points at credentials instead of at the owner.

Every request identifies the collector in its `User-Agent`, and gives up after 30 seconds. A
request that fails is retried up to four times with a growing pause, and a 429 waits as long as its
`Retry-After` asks. A few hosts that throttle bursts get a cap on requests in flight at once.

### Information sources

Every source the collector reads is public and needs no key. The build's own token goes to GitHub's hosts
and no other, where it lifts limits shared by every runner. `data_sources` in the file carries this list
in short, and the page shows it under Sources.

**The projects**

| Source | Read from | What the collector takes | Access | Data terms | Notes |
|---|---|---|---|---|---|
| [GitHub repositories](https://github.com/codesweep-ai) | `github.com/<owner>/<project>` over git | Manifests, lockfiles, workflows, Containerfiles, env files, deployment YAML, `.ABOUT` files and pinned files; commit history for internal pins; the full source govulncheck reads; tags, over `git ls-remote`, for an action with no releases | the build's token, sent only to github.com | each repository's own | Clones are blobless and sparse, so only manifest files are downloaded until govulncheck needs the rest. |
| [Project status files](https://codesweep.ai/) | each project's status file, resolved against `--site` | The one-line description a project card shows | none | each project's own | The same files the CI page reads. |
| [The previous deps.json](https://codesweep.ai/dashboards/deps.json) | `deps.json` under `--site`, as `--previous` | `history`, `seen`, and each exact version's licenses, provenance and source repository, and each Fedora build's source package and license | none | this site's own | A missing file starts history again, and every fact is asked for afresh. |

**Versions and releases**

| Source | Read from | What the collector takes | Access | Data terms | Notes |
|---|---|---|---|---|---|
| [Go module proxy](https://proxy.golang.org) | `proxy.golang.org`, and `sum.golang.org` through `go` | The newest version and release dates of direct Go modules, the next major, and Go release dates from the `golang.org/toolchain` module; the modules govulncheck builds | none | none stated | Indirect modules are not looked up. |
| [npm registry](https://registry.npmjs.org) | `registry.npmjs.org/<name>/latest` | The newest version and repository of each direct npm package | none | none stated | Transitive packages are not looked up. A 429 is retried after its `Retry-After`. |
| [PyPI](https://pypi.org) | `pypi.org/pypi/<name>/json` | The newest version of each Python package | none | none stated |  |
| [Maven Central](https://repo1.maven.org) | `repo1.maven.org` `maven-metadata.xml` | Maven releases, for a Maven toolchain pin | none | none stated |  |
| [Node.js release index](https://nodejs.org/dist/index.json) | `nodejs.org/dist/index.json` | Node.js releases and their dates | none | none stated |  |
| [GitHub releases](https://docs.github.com/en/rest/releases) | `api.github.com/repos/<repo>/releases`, or `github.com/<repo>/releases.atom` | Releases, their dates and pre-release flags for GitHub Actions and binaries downloaded from GitHub | the build's token; the Atom feed without one | each repository's own | The feed lists only recent releases, so a run without a token can see fewer. |
| [Container registries](https://github.com/opencontainers/distribution-spec) | `/v2/<repo>/tags/list` on Docker Hub, `ghcr.io`, `registry.fedoraproject.org` and `gcr.io` | The tags of each container image | anonymous registry tokens | none stated |  |

**Support windows**

| Source | Read from | What the collector takes | Access | Data terms | Notes |
|---|---|---|---|---|---|
| [endoflife.date](https://endoflife.date) | `endoflife.date/api/v1/products/<product>` and `/api/v1/identifiers/purl` | Each release line's release, support and end-of-life dates and newest release; which package identifiers belong to which product | none | MIT | Its v1 API. A 429 is retried after its `Retry-After`. |
| [GitHub runner images](https://github.com/actions/runner-images) | `actions/runner-images` README, as raw text | The image, OS version and architecture each hosted runner label runs, and which labels are deprecated or in preview | the build's token | MIT | A table in a README, not an API: a change of shape leaves runners unresolved rather than wrong. |
| [Firecracker policies](https://github.com/firecracker-microvm/firecracker/blob/main/docs/RELEASE_POLICY.md) | `docs/RELEASE_POLICY.md` and `docs/kernel-policy.md` in Firecracker's repository, as raw text | Firecracker's supported release lines and their end dates, and the guest kernel lines each supports | the build's token | Apache-2.0 | Tables in documents, read the same way as the runner images. |

**Security**

| Source | Read from | What the collector takes | Access | Data terms | Notes |
|---|---|---|---|---|---|
| [OSV](https://osv.dev) | `api.osv.dev/v1/querybatch` and `/v1/vulns/<id>` | Which advisories affect each Go module, npm package and PyPI version and the Go standard library, with aliases, severity, fixed releases and dates; the same for each proposed fix | none | each source database's, such as CC-BY-4.0 for GitHub's |  |
| [Go vulnerability database, through govulncheck](https://pkg.go.dev/vuln/) | `vuln.go.dev`, read by govulncheck | Whether a project's code calls, imports or only requires what a Go advisory names, and which modules its build includes | none | CC-BY-4.0 | govulncheck is built from the version this repository's `go.mod` pins. |
| [CISA KEV catalog](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) | CISA's `known_exploited_vulnerabilities.json`, or its `cisagov/kev-data` copy on GitHub | Which CVEs are exploited in the wild, and the day each was listed | none; the build's token for the GitHub copy | CC0-1.0 | The GitHub copy is CISA's own and syncs within minutes, so both give the same answer. |
| [FIRST EPSS](https://www.first.org/epss/) | `api.first.org/data/v1/epss?cve=…` | Each CVE's probability of exploitation within 30 days, and its percentile | none | free, with attribution requested | CVEs go in batches whose query stays under 2,000 characters. |
| [Fedora Bodhi](https://bodhi.fedoraproject.org) | `bodhi.fedoraproject.org/updates/` | Security updates pushed to a Fedora release since an image was built, with their builds and severity; the newest kernel build of a release | none | none stated |  |

**Packages and licenses**

| Source | Read from | What the collector takes | Access | Data terms | Notes |
|---|---|---|---|---|---|
| [deps.dev](https://deps.dev) | `api.deps.dev/v3` version, dependencies and project endpoints | Each version's licenses, publish date, deprecation, provenance and source repository; each repository's license, stars and OpenSSF Scorecard checks; what installing an npm version brings in | none | CC-BY-4.0 | One stable v3 call per version the previous file does not already describe. Deprecation is asked for every direct record. The dependency graph is asked only of a version an upgrade moves to, when a vulnerable lockfile package hangs from it. |
| [ClearlyDefined](https://clearlydefined.io) | `api.clearlydefined.io/definitions`, in batches, without file lists | The licenses scans found in a shipped package's files, and its license score | none | CC0-1.0 | Evidence only: a failure leaves no verdict changed. |
| [ScanCode LicenseDB](https://scancode-licensedb.aboutcode.org) | `scancode-licensedb.aboutcode.org/index.json` | The category of each license, shown beside a license the policy does not name | none | CC-BY-4.0 | A hint only: no verdict depends on it. |
| [Fedora mdapi](https://mdapi.fedoraproject.org) | `mdapi.fedoraproject.org/<branch>/pkg/<name>` | The version a package name resolves to in a Fedora release, its architecture, and the packages built from the same source | none | none stated |  |
| [Fedora Koji](https://koji.fedoraproject.org) | `koji.fedoraproject.org/kojihub`, XML-RPC `getRPM` and `getBuild` | The source package each binary package was built from | none | none stated | A call times out after 30 seconds and is tried twice. When Koji cannot answer, name rules pick the source package. |
| [Fedora dist-git](https://src.fedoraproject.org) | `src.fedoraproject.org/rpms/<source>/raw/f<release>/f/<source>.spec` | The `License:` tag of each source package's spec, an SPDX expression | none | each package's own | An HTML page in place of the spec, such as a bot filter's challenge, is a failure. |

- **A host that fails five requests in a row is not asked again in that run.** Each request has already
  been retried. Its records say what failed, so a build ends inside its time limit instead of waiting on
  a host that is down.
- **Facts about an exact version are reused, and nothing else is.** A version's license, a build's source
  package and a spec's license do not change once published. A newest version, an advisory or a support
  date can, and is asked for on every run.

### Shape

```jsonc
{
  "schema": 1,
  "generated": "2026-09-13T05:17:00Z",
  "org": "codesweep-ai",              // the namespace whose packages count as siblings
  "owner": "codesweep-ai",            // whose repositories were read; a fork's own owner in a fork
  "levels": ["idle", "good", "info", "warning", "serious", "critical"],
  "projects": [ /* see below */ ],
  "lifecycle": [ /* every release cycle in use, once: the record's lifecycle fields, plus
                    "projects": the projects using it, and "dependencies": the records' names */ ],
  "actions": [ /* every action across the org, as "Actions" below describes */ ],
  "history": [ /* one row a day: date, dependencies, attention, vulnerabilities, eol, major, libyears */ ],
  "data_sources": [ { "id": "osv", "group": "security", "name": "OSV", "url": "https://osv.dev",
                      "gives": "…", "license": "…", "hosts": ["api.osv.dev"] } ],  // as the table below
  "seen": { "lint|go|golang.org/x/mod|vulnerable": "2026-09-13T02:44:09Z",  // first sightings: of a gap,
            "action|eol|linux|6.19": "2026-09-13T05:17:00Z" },            // and of an action the feed announces
  "sources": [ { "host": "proxy.golang.org", "requests": 23, "failures": 0, "errors": [] } ],
  "authenticated": true              // whether GitHub was read with the build's token
}
```

Each entry in `projects` has this shape:

```jsonc
{
  "name": "sandbox",
  "repo": { "full_name": "codesweep-ai/sandbox", "url": "…", "branch": "main",
            "sha": "fe9796d…", "committed": "2026-09-11T22:06:08Z", "description": "…" },
  "error": "…",                      // present only when the repository could not be read
  "manifests": ["go.mod", "image/Containerfile.base"],
  "dependencies": [ /* records, see below */ ],
  "summary": {
    "dependencies": 151,             // records that are not indirect
    "indirect": 214,                 // indirect and transitive records
    "attention": 22,                 // records that need attention
    "by_status": { "minor": 6 },
    "by_ecosystem": { "go": { "total": 10, "levels": { "info": 4, "good": 6 } } },
    "vulnerabilities": 2,            // distinct advisories
    "libyears": 8.5,
    "state": "critical",             // the highest level that needs attention
    "licenses": { "allowed": 147, "not-shipped": 320 },
    "signals": 6,                    // declared records carrying a supply-chain signal
    "sla": { "breached": 0, "due-soon": 0, "within": 0 },
    "tiers": { "fix": 3, "plan": 3, "routine": 5 }   // the project's own actions, by tier
  },
  "actions": [ /* this project's actions on their own, titled for it */ ],
  "reachability": "govulncheck",     // present when govulncheck read the project
  "unmatched": [ { "path": "…", "match": "…", "name": "…", "reason": "pattern did not match" } ],
  "snapshot": { "built": "…", "from": "image/tiers.env", "packages": "image/Containerfile.base",
                "release": "44", "updates": 23, "matched": 2 },
  "notes": [ "…" ]                   // optional: a lookup that failed for the whole project
}
```

Each record in `dependencies` has this shape:

```jsonc
{
  "ecosystem": "native",             // go, npm, pypi, actions, runtime, image, package or native
  "name": "github.com/firecracker-microvm/firecracker",
  "label": "Firecracker",            // optional display name
  "version": "1.16.0",               // as pinned, or null when nothing is pinned
  "constraint": "^18.3.1",           // optional: the range as written, when it differs
  "scope": "build",                  // direct, dev, tool, build, toolchain, ci, deploy, engines,
                                     // optional, vendored, indirect or transitive
  "internal": false,                 // pins a repository of the org
  "dev": true,                       // npm: installed only for development, by the lockfile's reckoning
  "floating": false,                 // names a line rather than a release: `v7`, `24`, a dnf package
  "sources": [ { "path": "internal/fcdisk/build.go", "line": 73 } ],   // every place it is declared
  // A source folded in from a Containerfile ARG names it: { "path": "…", "line": 3, "arg": "GO_VERSION" }
  // A Go module file naming the module's commands as tools lists them: "tools": ["…/cmd/golangci-lint"]
  "datasource": "github",            // where its upstream is read: github, npm, goproxy, pypi, golang, node,
                                     // python, temurin, maven, oci, runner, rpm or fedora-kernel
  "package": "firecracker-microvm/firecracker",   // its name at that datasource, when it differs
  "tag_prefix": "v",                 // GitHub: what a release tag puts before the version
  "declared": true,                  // named by a pin in deps-config.json or an .ABOUT file
  "arg": "FC_VERSION",               // a Containerfile ARG that holds the version
  "variable": "AGENTS_IMAGE",        // an .env variable that holds an image reference
  "subpath": "save",                 // an action inside a repository: actions/cache/save
  "pinned_sha": false,               // an action pinned by a full commit
  "self_hosted": true, "os": "macos",   // a self-hosted runner, and the OS its labels name
  "manager": "dnf",                  // a system package: dnf, microdnf or yum
  "release": "44",                   // a system package or kernel: the Fedora release it comes from
  "snapshot": "2026-09-07T21:57:45Z",   // a system package: when its image was built
  "lockfile": "apps/viewer/package-lock.json",   // npm: the lockfile that resolved it
  "via": ["vitest"],                 // a vulnerable transitive package: the declared dependencies that install it
  "cleared_by": [ { "name": "vitest", "version": "4.1.11" } ],   // …and the moves that drop every affected copy
  "upstream_repo": "github.com/vitest-dev/vitest",   // the source repository, for grouping and Scorecard
  "upstream": {
    "latest": "1.17.0",
    "latest_date": "2026-09-10T10:42:35Z",
    "version_date": "2026-05-12T09:01:12Z",
    "line_latest": "1.16.2",         // the newest release on the pin's major.minor
    "effective": "4.3.0",            // what a floating pin resolves to
    "next_major": "…",               // Go: the next major version's module path
    "url": "…"
  },
  "behind": "minor",                 // major, minor or patch; absent when not behind
  "libyears": 0.27,
  "lifecycle": { "product": "linux", "cycle": "6.19", "release": "2026-02-08", "support": null,
                 "eol_is_floor": false,    // true when the end is only "supported at least until"
                 "eol": "2026-04-22", "lts": false, "latest": "6.19.14", "phase": "eol", "url": "…" },
  "lag": { "commits": 8, "commits_touching": 1, "paths": ["action"], "days": 0.5,
           "head": "d687ad5b27750000…",   // the whole commit the sibling's default branch is at
           "built": "a48d212425fe0000…",  // the sibling's last passing build: the first its status file names
           "pinned": "4c204c69b8b2",
           "version": "0.3.1-dev.20260922202805.27eb21f",   // npm: the version that commit's build published
           "image_only": true,            // npm: npmjs.com lists no such version yet, and the build's image carries it
           "builds": 3,                   // an image: newer tier builds, in place of commits
           "held": "ledger lists no build",   // why nothing moves the pin, when its status is `held`
           "off_branch": true },          // the pinned commit is not on the default branch
  "provider": "dashboards",          // internal: the project pinned
  "runner": { "image": "macOS 26 Arm64", "os": "macos", "version": "26", "arch": "arm64",
              "deprecated": false, "preview": false },   // a GitHub-hosted runner label, resolved
  "compat": {                        // a declared compatibility check
    "with": "Firecracker", "line": "6.19", "ok": false, "url": "…",
    "validated": ["5.10", "6.1", "6.18"],                 // the guest lines its policy lists
    "guaranteed": { "6.18": "2028-06-01" },              // each line's minimum end of support
    "requires": { "6.18": "1.16.1" },                    // each line's first Firecracker release
    "target": { "line": "6.18", "lts": true, "eol": "2028-12-31", "latest": "6.18.51",
                "guaranteed": "2028-06-01", "min_firecracker": "1.16.1" },   // the line to move to
    "newer": { "ended": ["6.19", "7.0", "7.1"], "not_validated": ["7.2"] },  // why not a newer line
    "distribution": { "name": "Fedora 44", "latest": "7.2.4-200.fc44", "line": "7.2", "has_target": false },
    "firecracker": { "name": "github.com/firecracker-microvm/firecracker", "pinned": "1.16.0",
                     "needs": "1.16.1", "ok": false }  // the project's own Firecracker pin, against the target
  },
  "source_package": "openssl",       // a Fedora package: the source package it builds from
  "note": "…",                       // something true that is not a verdict
  "purl": "pkg:npm/react@18.3.1",    // the package URL of the pinned release, where a purl type fits
  "tier": "routine",                 // needing attention: the tier its own action sits in
  "how": "npm install react@18.3.2", // needing attention: the command or edit that makes its change
  "after": [ { "run": "make viewer-build build", "cwd": "." } ],   // deps-config.json's steps after a move
  "fix_advisories": ["GO-2026-6180"], // advisories on the first fixed release, which `fix` steps past
  "in_build": true,                  // Go: whether the project's packages build this module
  "reachability": "not-in-build",    // Go: the closest the code comes to any advisory on it
  "licenses": ["MIT"],               // SPDX expressions, as its registry or spec states them
  "license": { "expression": "MIT", "verdict": "allowed",
               "found": ["MPL-2.0"], "found_verdict": "review",  // flagged or unnamed licenses a scan found in its files
               "unlisted": { "MPL-2.0": "Copyleft Limited" },   // licenses the policy does not name, with a hint
               "found_url": "https://clearlydefined.io/definitions/…", "score": 46,
               "opened": "…", "sla": { /* as below */ } },  // graded against the policy
  "signals": [ { "kind": "abandoned", "text": "no release since 2019-06-19 and …" } ],
  "repo_signals": { "scorecard": { "score": 6.2, "date": "2026-08-24", "checks": { "Maintained": 2 } },
                    "stars": 44592 },
  "provenance": true,                // published with a build attestation
  "install_script": true,            // npm runs a script when it installs this package
  "installer": "scripts/with-npmrevs.sh",   // npm, internal: the script the project's installs run through
  "opened": "2026-08-13T21:43:54Z",  // when a security or end-of-life gap opened
  "sla": { "since": "…", "due": "…", "days": 7, "state": "breached" },  // only with a policy
  "accepted": { "reason": "not_used", "note": "…", "until": "2026-12-01T00:00:00Z",
                "finding": "GO-2026-6179", "lapsed": false },
  "vulnerabilities": [ { "id": "GHSA-…", "aliases": ["CVE-…"], "severity": "high",
                         "summary": "…", "fixed": "6.4.3", "published": "…", "url": "…",
                         "reachable": "called",       // Go: called, imported, required or not-in-build
                         "exploited": "2026-09-01",   // the day CISA's KEV catalog listed it
                         "epss": { "probability": 0.42, "percentile": 0.97 } } ],
  "fix": "6.4.3",                    // the release that clears every advisory
  "fix_partial": true,               // no known release clears every advisory
  "deprecated": "…",
  "error": "HTTP 503 (proxy.golang.org)",
  "status": "minor",
  "level": "info"
}
```

### Scopes

A record's scope says what the dependency is for, and so whether its code reaches what the project ships:

| Scope | Declared as | Ships |
|---|---|---|
| `direct` | a `require` in `go.mod`, `dependencies` in `package.json` | yes |
| `indirect` | an `// indirect` require in `go.mod` | yes, unless govulncheck finds it outside the build |
| `transitive` | a package only a lockfile installs | yes, unless the lockfile marks it dev |
| `dev` | `devDependencies` | no |
| `optional` | `optionalDependencies` | no |
| `engines` | `engines.node` in `package.json` | no |
| `tool` | a module a `tool` directive names | no |
| `toolchain` | a `toolchain` directive | no |
| `build` | the `go` directive, a Containerfile, a download URL, a declared pin | yes |
| `ci` | a workflow or an action's `action.yml` | no |

An install command in a `run:` step or a `RUN` line, such as `go install x@v`, `npm install -g x@v` or
`pip install`, takes the scope of the file it is in: `ci` or `build`.
| `deploy` | a deployment manifest or a compose file | yes |
| `vendored` | an `.ABOUT` file beside a copy | yes |

"Ships" is the question a license verdict asks. It never changes a status, and a vulnerable record needs
attention whatever its scope.

### Statuses and levels

Every record carries one status and one level. The status says what is true, and the level says how
urgent it is. The collector decides both, so a page renders them rather than re-deriving them. The first
row that applies wins:

| Status | When | Level |
|---|---|---|
| `vulnerable` | an advisory affects the pinned version | `critical` for a high or critical advisory in something that ships, or for any advisory exploited in the wild; `serious` otherwise |
| `eol` | its release cycle is past its end of life | `critical`, `serious` for a dev, CI or optional scope, or `warning` for an `engines` floor |
| `eol-soon` | its release cycle ends within 90 days | `serious`, or `info` for an `engines` floor |
| `held` | an internal pin whose project lists no build, so a repin has nothing to move it to | `idle` |
| `behind` | an internal pin trails the last passing build of the project it pins | `info` up to 14 days of work, `warning` to 60, `serious` past that |
| `major` | a newer major version exists | `warning`, or `serious` once that release is a year old |
| `deprecated` | its publisher deprecated the pinned version | `warning` |
| `minor`, `patch` | a newer minor or patch release exists | `info`, or `warning` once that release is 90 days old |
| `unknown` | a lookup failed | `idle` |
| `current` | the newest release is what runs | `good` |
| `floating` | nothing pins a release, and no row above applies | `idle` |
| `untracked` | an indirect or transitive record no advisory affects | `idle` |

The two release-age windows are Chromium's policy for third-party code. An advisory counts as shipping
unless its scope is `dev`, `ci` or `optional`. A record needs attention at `info` or above. An indirect or
transitive record needs attention only when it is vulnerable.

One more rule changes a level after the table has set it. **A vulnerable Go module the code never calls
drops to `warning`.** govulncheck read the project and found no call to what any advisory names, so the
version sits in the module graph while the hole sits outside every path the code takes. An advisory
exploited in the wild drops only to `serious`, since a scanner can miss a call an attacker finds.

"Exploited in the wild" means CISA's KEV catalog lists a CVE the advisory aliases. The catalog lists what
attackers have used against real systems, whatever an advisory's score says.

### Evidence beside the verdict

A status says how current a dependency is. The fields below say what else is true of it, and none of
them changes the status.

**Reachability.** `reachable` on a Go advisory says how close the project's code comes to it:

| Value | Meaning |
|---|---|
| `called` | a function the advisory names is reachable from the project's packages |
| `imported` | a package the advisory names is imported, and nothing calls the vulnerable code |
| `required` | the module is required, and no package the advisory names is imported |
| `not-in-build` | the module is not in what the project's packages build at all |

govulncheck can miss a call made through reflection or unsafe code, so a `not-in-build` advisory stays
on the record and in the queue, one level lower.

**Exploitation.** `exploited` on an advisory is the day KEV listed one of its CVEs. `epss` is EPSS's
probability that the CVE is exploited within 30 days, and the share of scored CVEs it ranks above. Neither
is set for an advisory with no CVE alias. The page orders work by exploitation, then by EPSS, within a
level.

**Fixed releases.** `fix` is the release to move to. It starts as the highest first-fixed release among
the record's advisories. That release is looked up in turn, and when an advisory affects it too, `fix`
steps to the release that clears that one, up to three times. `fix_advisories` lists what was stepped
past. When no release is known to clear them, `fix_partial` is true.

**License verdicts.** `license.verdict` grades a record's license expression against the policy in
`deps-config.json`. A license is graded by an exact entry first, then by a wildcard entry. Nothing fetched
changes a verdict, so the same policy and the same declared license always grade the same way:

| Verdict | Meaning |
|---|---|
| `allowed` | the policy allows every license the expression requires |
| `review` | the policy asks for a human to read at least one of them |
| `denied` | the policy denies a license the expression requires |
| `unknown` | no registry or spec states a license, or the policy names none of the licenses stated |
| `not-shipped` | the dependency never reaches what the project ships, so its license binds nobody downstream |
| `aggregate` | a separate program an image redistributes, graded only against `deny_aggregate` |

An expression is graded the way SPDX reads it: `A AND B` takes the worse of the two, and `A OR B` the
better. `A WITH exception` is graded as that pair when the policy names it, and by `A` otherwise. A dependency is shipped unless its scope is `dev`, `ci`,
`tool`, `toolchain`, `engines` or `optional`. A Go module is also not shipped when govulncheck found it
outside the project's build, or when only a second module file such as `go.golangci.mod` requires it. The
org's own packages carry no verdict.

`license.unlisted` maps each license the policy does not name to its LicenseDB category, as a hint for
which list it belongs in. The category is empty when LicenseDB does not know the license.

**Licenses found in files.** A shipped package's declared license can hide code under another: a bundled
font, a vendored file. `license.found` lists licenses ClearlyDefined's scans found in the package's files,
from each expression that grades worse than the declared one as a whole. Each is a license the policy
flags, or one it does not name. `found_verdict` is the worst of them, and `score` is ClearlyDefined's 0 to 100 measure of how clearly the package states its licensing.
Direct npm, Go and PyPI packages that ship are looked up. Only SPDX ids count, because some of ScanCode's
own license keys are loose matches, such as a copyright line read as a proprietary license. A found
license is evidence on the record, and never an action of its own.

**Supply chain signals.** `signals` lists what a reviewer would want to know before trusting a
dependency:

| Kind | When |
|---|---|
| `deprecated` | the registry marks the version deprecated |
| `abandoned` | no release in two years, and OpenSSF Scorecard finds no repository activity in 90 days |
| `stale` | no release in a year |
| `quiet` | OpenSSF Scorecard finds no repository activity in 90 days |
| `scorecard` | OpenSSF Scorecard fails `Dangerous-Workflow`, `Binary-Artifacts` or `Code-Review` outright |
| `install-script` | npm runs a script when it installs the package |
| `unpinned-action` | a third-party action is pinned by a tag its owner can move |

**When a gap opened.** `opened` dates a gap from its public start. For a vulnerable record that is the
earliest advisory's date, and for an end of life the date support ended. For a version behind it is the
day the newest release came out. Anything else, a license finding or an end of life still ahead, opens on
the first run that saw it, carried forward in `seen`. The page shows the gap's age and orders work by it.

**Fix-by dates.** `sla` exists only when `deps-config.json` sets a number of days for the record's level.
It says when the fix is due and whether that date is `within`, `due-soon` or `breached`. `due-soon` is
the last quarter of the window, and at least the last three days.

**Acceptances.** `accepted` records a decision from `deps-config.json` to leave a finding be. Its reason is
one of Dependabot's dismissal reasons: `fix_started`, `inaccurate`, `no_bandwidth`, `not_used` or
`tolerable_risk`. An accepted record needs no attention until its `until` date. After that date
`lapsed` is true and it needs attention again.

### Rules

- **`schema` is the compatibility gate**, as it is for a status file. `scripts/check-deps.py` enforces the
  shape.
- **Times are UTC, `YYYY-MM-DDTHH:MM:SSZ`.** Release-cycle dates are whole days, `YYYY-MM-DD`.
- **A lookup that fails marks the record and never guesses.** The record's `error` says what failed and
  its status is `unknown`. `sources` counts the failure against the host.
- **Every indirect record is kept, and needs attention only when it is vulnerable.** An indirect Go module
  and a transitive npm package belong to the tools that maintain them. Their advisories are looked up, and
  their newest release is not, so each is `untracked` unless an advisory applies. `summary.indirect`
  counts them.
- **A fact about an exact version is asked for once.** A version's license, provenance and source
  repository, and a Fedora build's source package and license, do not change. The collector reuses them
  from the previous file for an indirect record or a Fedora build it has already described, and asks about
  the rest. Whether a version is deprecated can change, so a direct record is always asked.
- **The file is compact, and leaves out what a record does not have.** A null, an empty list and an empty
  object are omitted, except `version`, `eol` and `latest`, whose absence would read as "not looked at".
- **A libyear is the time from the pinned release to the newest one**, in years. It is counted only for
  a record that is behind with both release dates known. For an internal pin it runs from the pinned
  commit to the commit it trails.
- **An internal pin trails the sibling's last passing build.** That is the first commit the sibling's
  status file names in `built`, and the one a repin moves a pin to. `lag.commits`, `lag.days` and the compare
  link run from the pin to it. A pin at that build, or past it, is `current` though the head has moved on.
  Where the status file names no build, or names one the clone does not hold on the branch, the pin is
  `held`. A repin has nothing to move it to, since the head may not have built. `lag.held` says why, the
  record links no compare, and no action moves it.
- **Versions compare as numbers.** A pin with fewer components floats inside what it names, so `v7`
  trails `v8` but not `v7.3`. A 0.x minor bump counts as a minor one.
- **A newer cycle of Node.js, Java or Ubuntu counts once it is long-term support.** Until then the newest
  release on the current long-term line is the target.
- **An action pinned by commit is behind only when its own directory moved.** `lag.commits_touching`
  counts the commits under `lag.paths`.
- **A runner label resolves to the image GitHub names for it.** A `-latest` label follows GitHub, so it is
  never behind. A pinned label trails when a newer release of its OS exists. A self-hosted runner declares
  no OS version, so it is named and marked `unknown` rather than guessed.
- **A support window known only as a floor is shown as one.** Firecracker guarantees a release line until
  a date and may support it longer; `eol_is_floor` marks that date, and the phase is read from it.
- **A system package floats with its release.** It is vulnerable when Fedora pushed a security update for
  its source package after the image carrying it was built. A snapshot in the configuration says when
  that was. The match is by source package name, so a package installed only as another's dependency is
  not seen.
- **`history` gains one row a day.** The build copies the rows forward from the published file and keeps
  the newest 400. A deployment that lost its file starts again from one row.
- **A record carries a purl where a purl type fits.** Go modules are `pkg:golang`, npm packages
  `pkg:npm`, Python packages `pkg:pypi`, and actions and GitHub binaries `pkg:github`, with an action's
  subpath after `#`. Images are `pkg:docker`, with `repository_url` for a registry other than Docker Hub.
  Fedora packages are `pkg:rpm/fedora` at the version they resolve to, with `distro`. Toolchains, runners
  and the kernel have none.
- **The SBOM carries the same inventory for other tools.** `deps.cdx.json`, published beside the file, is a
  CycloneDX document with a component per project and its dependencies inside. Each advisory carries a
  VEX analysis where the collector has one:

  | The collector knows | VEX state | Justification or response |
  |---|---|---|
  | accepted as `not_used` | `not_affected` | `code_not_reachable` |
  | accepted as `inaccurate` | `false_positive` | |
  | accepted as `tolerable_risk` | `exploitable` | `will_not_fix` |
  | accepted as `fix_started` | `exploitable` | `update` |
  | accepted as `no_bandwidth` | `in_triage` | |
  | govulncheck: `not-in-build` | `not_affected` | `code_not_present` |
  | govulncheck: `required` or `imported` | `not_affected` | `code_not_reachable` |
  | govulncheck: `called` | `exploitable` | |

  An acceptance wins over govulncheck. Records judged differently get separate entries under the same
  advisory id, since one analysis covers every component its entry affects.
- **The feed announces what needs fixing now.** `deps-feed.xml`, published beside the file, is an Atom feed
  with one entry per action in the `fix` tier, and per action for a license the policy denies. An entry's
  id is the action's key, so a reader announces a change once, however many projects it spans. Its date
  is the run that first called for it, carried forward in `seen`, so a gap open for months that a run
  has just found still reads as new. Its summary holds what the action buys, its evidence, how to make
  it and the projects it covers, and its link opens the action on the page. The feed keeps the newest
  100. Any feed reader can follow it, which makes it an alert that needs no account and no key.

### Actions

An action is one change to make: moving one dependency, or several that move together, in one project or
in several. The collector turns records into actions, as it turns facts into statuses, so the page and
an agent follow the same work. `deps.json` carries them twice: `actions` across every project, and
`projects[].actions` for each project on its own.

```jsonc
{
  "id": "action-eol-linux-6-19",     // the page's anchor for the card
  "key": "eol|linux|6.19",           // what groups items onto the action
  "kind": "eol",                     // one of the kinds below
  "tier": "fix",                     // fix, plan or routine: when to act
  "level": "critical",               // the highest level among its items
  "type": "eol",                     // security, eol, license, supply, sync or update: what kind of work
  "status": "eol",                   // the status of its most urgent item
  "title": "Move off Linux kernel 6.19 in sandbox",
  "result": "Linux kernel 6.19 ended 2026-04-22",
  "why": "Firecracker supports guest kernels 6.18, not 6.19",
  "released": { "version": "19.3.0", "date": "…" },   // for a version gap: when the target came out
  "how": "set the version to a Fedora kernel build on the 6.18 line   at internal/fcdisk/build.go:28",
  "evidence": ["…"],
  "projects": ["sandbox"],
  "opened": "…", "ends": "…", "sla": { /* the earliest */ },
  "exploited": false, "epss": 0.0091,
  "items": [ { "project": "sandbox", "record": 3, "tier": "fix", "reason": null, "to": "6.18 line" } ],
  "requires": ["action-eol-firecracker-1-16"],   // actions to make first
  "options": [ { "recommended": true, "text": "6.18 LTS microVM kernel: …", "url": "…" },
               { "text": "Fedora 44 kernel 7.2.5-200.fc44: patched, not validated by Firecracker" } ],   // a choice for a person
  "steps": [ /* every step, as the actions file lists them, when there are requires or options */ ]
}
```

`record` is the index of the record in that project's `dependencies`. `reason` is `license`, `unlisted` or
`abandoned` when the item is there for that rather than for its status, and `to` is the version it moves
to.

| Kind | One action per | Its items |
|---|---|---|
| `lock` | lockfile | vulnerable packages the lockfile installs, fixed by refreshing it |
| `rebuild` | Containerfile | Fedora packages with a security update newer than the image |
| `sync` | internal pin | the same sibling pinned across projects |
| `eol` | release line | everything on a release line past, or near, its end of life |
| `actions` | org | every GitHub Action a major behind |
| `devtools` | package.json | majors in one project's dev tooling |
| `routine` | ecosystem | minor and patch releases |
| `license` | package and verdict | a license the policy denies or asks to review |
| `unlisted` | package | a license the policy does not name |
| `replace` | package | an abandoned package |
| `dep` | upstream repository | packages one upstream releases together, such as react, react-dom and their types |

- **Tiers say when, and nothing else.** An item at `critical` or `serious` is `fix`, at `warning` it is
  `plan`, and at `info` it is `routine`. A denied license and an abandoned package are `plan`. A sibling
  pin is `routine` until it is `serious`. A passed fix-by date moves an item up one tier. An action sits in
  the most urgent tier among its items.
- **Actions are ordered within a tier** by passed fix-by dates, then level, exploitation, EPSS, gap age,
  and the number of projects.
- **An accepted record is no work.** It stays on the record and leaves every action.
- **A lockfile package another move clears joins that move's action.** `cleared_by` on its record names
  the declared dependencies that install it, when each moves and deps.dev's graph of the version it moves
  to asks for no affected copy. A range the pinned copy satisfies does not clear it, because npm keeps a
  copy that satisfies the range. When one upgrade action makes every move named, the package leaves the
  lockfile refresh for that action, with its advisories and its `done_when`. A refresh left with nothing
  is not listed.

## The actions file

`deps-actions.json`, published beside the dependencies file, is the same actions arranged for an agent
to follow in a clone of each project. It needs no key and no parsing of the page:

```
https://codesweep.ai/dashboards/deps-actions.json
```

```jsonc
{
  "schema": 1,
  "generated": "2026-09-12T20:56:03Z",
  "org": "codesweep-ai",
  "owner": "codesweep-ai",           // whose repositories to clone and edit
  "about": "…",
  "workflow": ["…"],                 // how to take the actions: order, commits, checks, declining
  "tiers": { "fix": "…", "plan": "…", "routine": "…" },
  "links": { "page": "…", "data": "…", "sbom": "…", "feed": "…", "spec": "…" },
  "data_sources": [ { "name": "OSV", "url": "…", "gives": "…", "license": "…" } ],
  "projects": [ {
    "name": "tracer",
    "repo": { "url": "…", "clone": "https://github.com/codesweep-ai/tracer.git", "branch": "main", "sha": "…" },
    "counts": { "fix": 4, "plan": 5, "routine": 5 },
    "actions": [ {
      "id": "tracer:dep:github-com-vitest-dev-vitest",
      "tier": "fix", "level": "serious", "type": "security",
      "title": "Upgrade vitest to 4.1.11 in tracer",
      "result": "Fixes 2 advisories", "why": "…", "evidence": ["…"],
      "opened": "…", "ends": "…", "due": "…", "exploited": true, "epss": 0.42,
      "requires": ["sandbox:eol:firecracker-1-16"],   // actions to make first, listed before this one
      "options": [ { "recommended": true, "text": "…", "url": "…" }, { "text": "…" } ],
      "steps": [
        { "run": "npm install -D vitest@4.1.11", "cwd": "apps/viewer" },
        { "edit": "internal/fcdisk/build.go", "line": 73, "text": "set the version to 1.17.0", "from": "1.16.0", "to": "1.17.0" },
        { "do": "Rebuild the image that image/Containerfile.base describes, then point the pinned tag at the new build." }
      ],
      "changes": [ {
        "ecosystem": "npm", "name": "vitest", "label": "…", "purl": "pkg:npm/vitest@2.1.9", "scope": "dev", "dev": true,
        "status": "vulnerable", "level": "serious", "from": "2.1.9", "to": "4.1.11",
        "reason": "license",           // present when the change is there for a license or an abandoned package
        "files": ["apps/viewer/package.json:45"],
        "advisories": [ { "id": "GHSA-…", "aliases": ["CVE-…"], "severity": "critical", "summary": "…",
                          "fixed": "3.2.6", "url": "…", "reachable": "called", "exploited": "…", "epss": { } } ],
        "fix_advisories": ["GHSA-…"],
        "license": { /* the record's, for a license change */ },
        "lifecycle": { "product": "…", "cycle": "…", "eol": "…", "phase": "…", "url": "…" },
        "compat": { /* the record's */ }, "lag": { /* the record's */ },
        "cleared_by": ["vitest"],      // a lockfile package this action's own moves clear, with no step of its own
        "signals": [ /* the record's, for an abandoned package */ ],
        "done_when": "apps/viewer/package-lock.json carries no copy of vitest that GHSA-… affects"
      } ],
      "page": "https://codesweep.ai/dashboards/deps?project=tracer#action-…",
      "decline": { "file": "deps-config.json", "repo": "codesweep-ai/dashboards",  // the repository that built this file
                   "accepted": { "project": "tracer", "name": "vitest", "finding": "GHSA-…", "reason": "tolerable_risk",
                                 "note": "…", "until": "YYYY-MM-DD", "version": "2.1.9" },
                   "reasons": ["fix_started", "inaccurate", "no_bandwidth", "not_used", "tolerable_risk"] }
    } ]
  } ]
}
```

- **A step is one of three things.** `run` is a command to run in `cwd`, relative to the repository root.
  `edit` is a change at a file and line, described by `text`, with `from` and `to` where the collector
  knows both. `do` is a task no command makes, such as rebuilding an image.
- **A step is listed at every place the pin is written.** A command is listed once for every directory
  whose manifest declares the dependency. A Go pin gets one for every module file: `go mod edit` or
  `go get`, with `-modfile` naming a file other than `go.mod`. A place no command maintains, such as a
  Containerfile `ARG` or a constant a configured pin reads, gets an edit. The configuration's `after`
  steps come last.
- **`go get` moves a tool by its command,** `-tool …/cmd/golangci-lint@v2.13.2`, which loads what the
  command imports. The module path would add a second tool line. Only `go.mod` is tidied: a module file
  beside it holds a tool's requirements, and tidy would add the directory's packages to it.
- **A sibling pin moves to one commit, and no other pin moves with it.** That commit is `lag.built`,
  and a `held` pin, which has none, gets no step. A Go `sync` runs `go get <module>@<commit>` in each module file that pins it, with the tool's command as above. An
  npm `sync` installs `lag.version`, the version that commit's build published. A dist-tag names
  whichever build was tagged last, which can be older than the pin. The status file names the version,
  or npmjs.com lists it, or the build's `npm/<name>` image carries it, in that order. The Pages run that
  npm's own run starts can read npmjs.com before the version is listed. A build npm never published is
  listed nowhere. So the image can be the only place that holds it, which is `lag.image_only`. A
  project that carries `scripts/with-npmrevs.sh` names it as its `installer`, and the install runs
  through it, since cs-npmrevs serves the image's version as well. A project without it installs from
  npmjs.com, so an image-only build gets a step that says to wait for npmjs.com instead.
- **`requires` orders the work.** An action comes after every action it requires, in the file as in time.
- **`options` is a choice for a person.** The collector recommends one and picks none. An agent proposes
  the recommended option, and makes none of them unasked.
- **An action's `id` is stable** for as long as the same change is called for: the project, the kind, and
  what groups its items. A commit or pull request can name it, and the next file shows whether it is gone.
- **`done_when` says what the collector will see** once a change is made. An action is done when every
  change meets its condition, and the next build of the file drops it.
- **A vulnerable change is done when no affected copy is left, at whatever version.** Its condition names
  the lockfile, the module files or the pin, and the advisories, and never a version. The registry moves
  between this file and the step, so a refresh can rightly land above `to`, and a newer release can carry
  an advisory of its own. A step's exit status proves nothing either: `npm audit fix` exits 0 whether or
  not it changed anything.
- **`decline` is the entry that leaves a finding be.** It goes in `accepted` in this repository's
  `deps-config.json`, with a real reason, note and expiry in place of the placeholders.
- **The file is rewritten on every build**, on each push and once a day. An agent reads it fresh before it
  starts, and compares `repo.sha` with its clone: a clone behind that commit may already hold a fix.

## The dependencies configuration

`deps-config.json` in this repository holds what no manifest says:

```jsonc
{
  "schema": 1,
  "comment": "…",                             // for a reader of the file; nothing reads it
  "org": "codesweep-ai",                      // the namespace whose modules, packages and images are siblings
  "site": "https://codesweep.ai/dashboards/",  // where org publishes this site; a fork passes --site instead
  "include": ["dashboards"],                  // repositories read besides the projects in projects.json
  "pins": [ {
    "project": "sandbox",
    "path": "internal/fcdisk/build.go",
    "match": "DefaultFCVersion = \"v?([^\"]+)\"",
    "name": "github.com/firecracker-microvm/firecracker",
    "label": "Firecracker",
    "datasource": "github",
    "package": "firecracker-microvm/firecracker",
    "tag_prefix": "v",                        // optional: what a release tag puts before the version
    "ecosystem": "native",                    // optional, native by default
    "scope": "build",                         // optional, build by default
    "internal": false,                        // optional: a pin on the org's own package
    "compat": "firecracker-guest",            // optional: a compatibility check to run
    "cycle": "1.16",                          // optional: the release line, when the version does not say
    "note": "…"                               // optional: shown on the record
  } ],
  "snapshots": [ {
    "project": "sandbox",
    "packages": "image/Containerfile.base",
    "built": { "path": "image/tiers.env", "match": "^AGENTS_REF=\\S+:v0\\.0\\.0-(\\d{14})-" }
  } ],
  "after": [ {
    "project": "ledger",
    "name": "@codesweep-ai/ui",
    "steps": [ { "run": "make viewer-repin", "cwd": "." } ]
  } ]
}
```

- **A pin is a version no extractor finds.** `match` is a regular expression whose first group is the
  version. `datasource` is `github`, `npm`, `goproxy`, `pypi`, `fedora-kernel`, `golang`, `node`,
  `python`, `temurin` or `maven`. `internal` marks a pin on the org's own package. `compat` names a
  compatibility check. An omitted `ecosystem` means `native`, and an omitted `scope` means `build`.
- **`firecracker-guest` checks a guest kernel against Firecracker's kernel policy.** A kernel passes when
  its line is one the policy's guest table lists. A listed line's date is a minimum end of support, so the
  line stays validated past it. When the kernel fails, its `target` is the listed release guaranteed
  longest that upstream still maintains: in practice, the newest long-term kernel. `newer` says
  what became of every line released after it.
- **The move names what it depends on.** The target line's first Firecracker release is compared with the
  project's own Firecracker pin, and a pin too old makes upgrading it the first step. That upgrade's action
  is named in the kernel's `requires`.
- **A move says where its build comes from.** A Fedora kernel pin is compared with the only line its
  release maintains, the line of its newest stable kernel. When that is the target line, the step is an
  edit to that build. When it is not, the action carries `options` for a person to choose from.
- **A pin that stops matching is reported.** It lands in the project's `unmatched` list and in a notice
  on the page, instead of dropping out unseen.
- **A project can name a pin's upstream where the pin is.** The collector reads Renovate's annotation on
  the line above an `ARG` or `ENV` in a Containerfile:

  ```dockerfile
  # renovate: datasource=github-releases depName=firecracker-microvm/firecracker
  ARG FC_VERSION=1.16.0
  ```

- **A project can describe vendored code where it sits.** The collector reads every AboutCode `.ABOUT`
  file in a repository, `vendor/` included, as a record with scope `vendored`. A `package_url` of type
  `npm`, `github`, `pypi` or `golang` names the upstream and the version. Otherwise `version` is required,
  and a `download_url` on GitHub releases names the upstream. `spdx_license_expression`, or else
  `license_expression`, gives its license. One with no upstream it recognises is kept, with an `error`.

  ```yaml
  about_resource: axe.min.js
  name: axe-core
  version: 4.10.2
  package_url: pkg:npm/axe-core@4.10.2
  spdx_license_expression: MPL-2.0
  ```

- **A snapshot says when a Containerfile's packages were resolved.** `packages` names the Containerfile.
  `built.match` finds a 14-digit build stamp in `built.path`, usually the tag of the image that
  Containerfile produced.
- **`after` is what a move needs beyond its manifest.** Its steps follow the record's own in every action
  that moves the dependency named, in that project: a bundle that embeds it rebuilt, a page that
  reports its version re-rendered. Each step has the shape the actions file gives one.

The configuration also carries three policies:

```jsonc
{
  "licenses": {
    "allow": ["MIT", "Apache-2.0", "BSD-3-Clause"],   // SPDX identifiers, with * as a wildcard
    "review": ["MPL-*", "LGPL-*"],
    "deny": ["GPL-*", "AGPL-*", "SSPL-*"],
    "deny_aggregate": ["LicenseRef-Callaway-*"],     // denied even for a program an image carries
    "aggregate": ["package", "image", "native", "runtime"]
  },
  "sla": { "critical": 7, "serious": 30 },          // optional: fix-by days per level
  "accepted": [ {
    "name": "golang.org/x/mod",
    "project": "lint",                               // optional, * for every project
    "version": "v0.39.0",                            // optional
    "finding": "GO-2026-6179",                       // optional: an advisory id, a status, or "license"
    "reason": "not_used",
    "note": "only the ledger tool requires it",
    "until": "2026-12-01"
  } ]
}
```

- **A license nothing in the policy names is `unknown`.** Exact entries are checked before wildcards, and
  within each, deny is checked before review and review before allow. An unknown license in something
  that ships becomes a routine action to name it in the policy.
- **An absent `sla` means no fix-by dates.** The page then orders work by how long each gap has been open.
- **An acceptance needs a reason and should carry an expiry.** One without `until` never lapses, which is
  a decision nobody revisits.

## Mechanisms and prior art

The dependencies page borrows most of what it does from existing projects and products. Each mechanism
below has a table of how others do the same job, and its last row says how this dashboard does it. The
comparisons are about approach, and each approach buys something different.

These are the projects and products it is compared with:

- **Renovate** is Mend's update bot, and **Dependabot** is GitHub's. Both run per repository and open pull
  requests.
- **Dependency-Track** is OWASP's platform for tracking risk across the SBOMs, the software bills of
  materials, that builds produce.
- **Snyk**, **Sonatype Lifecycle**, **Endor Labs**, **FOSSA** and **Socket** are commercial scanners with
  hosted dashboards.
- **OSV** is Google's open vulnerability database, and **osv-scanner** is its scanner. **deps.dev** is
  Google's Open Source Insights service.
- **OpenSSF Scorecard** grades a repository's security practices. **govulncheck** is the Go team's
  vulnerability scanner.
- **Grype**, **Trivy** and **Docker Scout** scan container images. **Chainguard** publishes minimal images.
- **endoflife.date** publishes support dates, and **xeol** scans for end-of-life software with its data.
- **Anitya**, at release-monitoring.org, and **Repology** track upstream releases for Linux distributions.
- **zeitgeist** is the Kubernetes project's checker for versions pinned in arbitrary files.
- **Chromium's third-party policy** sets how far bundled code may lag. The **Go repository's moddeps test**
  fails when its vendored modules fall behind.
- **libyear** is a measure of dependency drift, adopted by CHAOSS, the Linux Foundation's project health
  metrics group.
- **AboutCode** is a family of open source compliance tools. **ScanCode** detects licenses and packages
  in code, **ScanCode.io** runs scanning pipelines, **VulnerableCode** aggregates advisories, and
  **DejaCode** applies usage policies. AboutCode also maintains the purl, the package URL that names a
  package across ecosystems.

### Finding dependencies

How a tool finds what a project depends on.

| Who | How |
|---|---|
| Renovate | A manager per manifest format reads the repository it runs in, and regex managers and comment annotations cover the rest. |
| Dependabot | GitHub's dependency graph reads the manifests it supports, and a pin outside them is not tracked. |
| zeitgeist | A `dependencies.yaml` lists each version with the files and patterns where it appears, then checks each upstream. |
| Dependency-Track | It ingests the CycloneDX SBOM a build produces, so it sees what the SBOM generator saw. |
| Grype, Trivy | They scan a directory or an image, which finds installed packages that no manifest names. |
| ScanCode.io | Its pipelines scan a codebase or a container image, and write a CycloneDX SBOM of the packages they find. |
| AboutCode `.ABOUT` files | A small text file beside vendored code states its name, version, purl and license. |
| **This dashboard** | It clones each default branch without file contents and reads only the files a manifest can live in, so nothing is built, pulled or added to a project. A version kept elsewhere is declared in `deps-config.json`, a Renovate annotation or an `.ABOUT` file. A package an image installs only as another's dependency is missed. |

### Freshness

How a tool says a dependency is behind.

| Who | How |
|---|---|
| Renovate, Dependabot | Each update becomes a pull request with release notes, so freshness is a queue of pull requests per repository. |
| libyear, CHAOSS | Libyears sum drift into one number per project. |
| Chromium | Its policy states how long bundled third-party code may trail upstream. |
| Anitya, Repology | They map upstream releases onto distribution packages. |
| **This dashboard** | Versions compare as numbers, and libyears measure drift. Chromium's release-age windows raise a gap's level as the newer release ages. It opens no pull request: each action names the command or edit, once for every project it spans. |

### Release lines and end of life

Where support dates come from.

| Who | How |
|---|---|
| endoflife.date | It publishes support dates per release cycle, and an index from package identifiers to products. |
| xeol | It matches an SBOM or an image against endoflife.date's data. |
| Docker Scout | It recommends a newer base image tag for an image it scans. |
| Chainguard | Its images are rebuilt as upstream releases land, so freshness becomes the image publisher's job. |
| **This dashboard** | It reads endoflife.date by product or package identifier, and the publisher's own policy where that has none: Firecracker, GitHub's runner images, nodejs.org and go.dev. A support end known only as a floor is shown as one. A guest kernel is checked against the lines Firecracker validates, and pointed at the newest long-term one. |

### Vulnerabilities

Where advisories come from, and how a fix is chosen.

| Who | How |
|---|---|
| OSV, osv-scanner | OSV aggregates advisory sources, and the scanner reads lockfiles, SBOMs and images. |
| Dependabot | Alerts come from the GitHub Advisory Database, repository by repository. |
| Dependency-Track | It matches SBOM components against several vulnerability feeds. |
| Grype, Trivy, Docker Scout | They match the distribution packages inside an image against each distribution's security data. |
| Snyk | It matches against its own curated database. |
| VulnerableCode | It aggregates advisories by purl, and names the next version no advisory affects. |
| **This dashboard** | Advisories come from OSV for Go, npm and PyPI. A Fedora package is vulnerable when Bodhi pushed a security update after its image was built, which needs no image pull. The proposed fix is looked up in OSV too, as VulnerableCode does, so it has no advisory of its own. A package installed only as another's dependency is missed. |

### Reachability

Whether a project's code calls what an advisory names.

| Who | How |
|---|---|
| govulncheck | It analyses calls down to the vulnerable function, for Go only. |
| osv-scanner | Its call analysis for Go uses govulncheck's analysis. |
| Endor Labs, Snyk | They trace calls to vulnerable functions in several languages, as commercial services. |
| **This dashboard** | It runs govulncheck over each Go project. An advisory nothing calls drops to `warning`, or to `serious` when it is exploited in the wild, and stays on the record. npm advisories have no reachability, so each counts as called. |

### License policy

How licenses are found and graded.

| Who | How |
|---|---|
| Dependency-Track | A policy engine groups licenses and raises a violation per component. |
| Snyk, Sonatype Lifecycle, FOSSA | License policies carry severities and waivers across an organisation. |
| deps.dev | It serves the licenses a registry declares, as SPDX expressions. |
| ScanCode, ClearlyDefined | ScanCode finds license text in a package's files. ClearlyDefined serves those results with a score for how clearly a package is licensed. |
| ScanCode LicenseDB | It sorts licenses into categories such as permissive, copyleft and proprietary, as static JSON. |
| DejaCode, ScanCode.io | Usage policies attach to licenses and packages, and flag what a product must not ship. |
| **This dashboard** | Declared licenses come from deps.dev, and from spec files for Fedora packages. Each SPDX expression is graded against `deps-config.json` alone, and only what a project ships counts. A license the policy does not name stays `unknown`, with its LicenseDB category as a hint. It scans no file itself: ClearlyDefined's scans show what shipped packages' files carry. |

### Supply chain signals

How a tool flags risk beyond a dependency's version.

| Who | How |
|---|---|
| OpenSSF Scorecard | Automated checks grade a repository's maintenance and security practices. |
| deps.dev | It serves Scorecard results, deprecation and provenance attestations per package version. |
| Socket | Static analysis of package code flags install scripts, network access, obfuscation and typosquats. Its API needs an account. |
| zizmor | It audits GitHub Actions workflows, unpinned third-party actions included. |
| **This dashboard** | Only what public sources already say: deprecation, lockfile install scripts, release age, OpenSSF Scorecard results, provenance and third-party actions pinned by tag. Package code is not analysed, which is the part of Socket's signal that needs its service. |

### Internal lag

How far a pin on a sibling project trails it.

| Who | How |
|---|---|
| Renovate | Digest updates move commit pins and Go pseudo-versions forward, one pull request at a time. |
| Go's moddeps test | The test fails when a vendored module trails its newest version. |
| **This dashboard** | It counts the commits the sibling's default branch has made since the pin, up to the last one the sibling built and passed: under an action's own directory for an action, and newer tier builds for an image. A project's card shows its pins on siblings and who pins it. |

### Grouping and ordering work

How findings become work, and in what order.

| Who | How |
|---|---|
| Renovate | Group presets move packages that release together, such as a monorepo's, in one pull request. |
| Dependabot | Grouped updates batch by pattern or update type, and alerts sort by severity. |
| Snyk | A priority score weighs severity, exploit maturity and reachability. |
| Endor Labs | A funnel narrows findings to those that are reachable and fixable. |
| VulnerableCode | A risk score multiplies weighted severity by exploitability. Exploitability rises for a CVE in CISA's catalog of known exploited vulnerabilities, or with a high FIRST EPSS score. |
| **This dashboard** | Dependencies from one upstream repository share a card, and one change across projects is one card. Tiers say when to act. Within a tier, work is ordered by level, then exploitation, EPSS and gap age. A listing in CISA's KEV catalog makes an advisory `critical`, and EPSS orders work without changing a level. |

### Gap age and fix-by dates

How long a gap has been open, and when it is due.

| Who | How |
|---|---|
| Dependency-Track | A finding records when it was first attributed to a component. |
| Snyk, Sonatype Lifecycle | Reports measure open issues against remediation targets. |
| GitHub security campaigns | A campaign gives a set of alerts a due date. |
| **This dashboard** | Each gap is dated from its public start: the advisory, the end of life or the release. Fix-by dates exist only when `deps-config.json` sets a policy. |

### Accepted exceptions

How a finding is left be on purpose.

| Who | How |
|---|---|
| Dependabot | A dismissed alert records one of five reasons. |
| osv-scanner | Its configuration ignores an advisory with a reason and an `ignoreUntil` date. |
| Trivy | `.trivyignore` accepts an expiry date per entry. |
| Snyk | A `.snyk` policy file ignores an issue until a date. |
| Dependency-Track | Analysis states and VEX justifications record why a finding does not apply. VEX, the Vulnerability Exploitability eXchange, is a standard way to say so. |
| DejaCode | A vulnerability analysis per product records its state and justification in VEX terms. |
| **This dashboard** | An entry in `deps-config.json` names the finding, one of Dependabot's reasons and a date it lapses. The SBOM carries it as a VEX analysis. |

### Alerting

How people hear about new work.

| Who | How |
|---|---|
| Dependabot | Alerts arrive as GitHub notifications. |
| Dependency-Track | Notifications go out by webhook, email or chat integration. |
| Anitya | New upstream releases are published as messages on Fedora's message bus. |
| **This dashboard** | `deps-feed.xml` is an Atom feed with one entry per action to fix now. It needs no secret in the build and no account for the reader, where email and chat would need both. |

### Sharing findings with other tools

How findings reach other tools.

| Who | How |
|---|---|
| Dependency-Track | It imports CycloneDX SBOMs and VEX, and exports both. |
| ScanCode.io | Its `load_sbom` pipeline imports CycloneDX and SPDX documents. |
| osv-scanner | It scans an SBOM's purls against OSV. |
| GitHub | The dependency graph exports a repository's SBOM as SPDX. |
| **This dashboard** | `deps.cdx.json` is the inventory as a CycloneDX SBOM, with a purl on each record and a VEX analysis on each advisory where one is known. `deps-actions.json` gives agents the page's actions. |

### Presentation

How the work is shown.

| Who | How |
|---|---|
| Renovate | A Dependency Dashboard issue per repository lists pending updates as checkboxes. |
| Dependency-Track | Portfolio views chart risk across projects over time. |
| Snyk, Socket | Hosted dashboards list findings by organisation and project. |
| **This dashboard** | A static page reads one same-origin file. It opens on the next action, puts urgency in three tiers, and keeps reference detail closed until it is opened. |

## What a page may assume

- **Every status file is same-origin.** Each project's Pages site is a path on the
  host that serves the page, so a page fetches them with no CORS, no token and no
  API quota. That host is `codesweep.ai` for codesweep-ai, and `<owner>.github.io`
  for a fork, whose page therefore reads the fork's own projects. The page keeps
  the codesweep-ai name either way: only where it reads from follows the fork.
- **A page must not call the GitHub API.** Anonymous browser calls are capped at
  60 an hour per IP. Conditional requests do not help, because GitHub exempts a
  `304` from the limit only *"if the request was made while correctly authorized
  with an `Authorization` header"*. Authorising would mean putting a token in a
  public page, which is publishing it. Read the static files instead.
- **A project may be missing or stale, and that is data.** A file may 404 because
  the project has not published yet, and a project may not have built for days.
  Both are facts worth showing, and neither may break the page or be counted as
  a failure.
- **A project is dated by its latest `ci` run.** A project may rebuild its Pages
  site on a schedule, which rewrites `generated` with nothing built, so a page
  reads the date off the workflow named `ci` instead. A file that stops being
  published freezes that date too. A page falls back to `generated` only when
  there is no `ci` run to read.
- **The dependencies file is same-origin as well**, because it is published with
  the page that reads it.
- **The dependencies file may be old, and that is data too.** The build writes
  it daily, so a file older than 36 hours means the build stopped, and a page
  says so. Its schedule refreshes facts, because every run reads the repositories
  and the registries again.
