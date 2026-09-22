# dashboards

> **Health dashboards for the codesweep-ai open-source projects on GitHub.**

[![CI](https://github.com/codesweep-ai/dashboards/actions/workflows/ci.yml/badge.svg)](https://github.com/codesweep-ai/dashboards/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Pages](https://img.shields.io/badge/pages-codesweep.ai%2Fdashboards-lightgrey)

This repository holds the web pages that show how the codesweep-ai projects on GitHub are doing.
The set will grow.

- **Continuous integration**, at
  **[https://codesweep.ai/dashboards/ci](https://codesweep.ai/dashboards/ci)**.
- **Dependencies**, at
  **[https://codesweep.ai/dashboards/deps](https://codesweep.ai/dashboards/deps)**.

## The CI dashboard page

Nothing collects the data centrally. **Each project publishes its own status as part of its Pages
deployment**, and this page assembles them in the browser.

```
codesweep.ai/lint/ci-status.json      published by lint's pages workflow
codesweep.ai/ledger/ci-status.json    …
codesweep.ai/dashboards/
    projects.json                     where to find them
    ci.html + ci.js                   fetches the above and renders
```

### Adding a project

1. Give the project a Pages workflow that runs the action in `action/` and deploys the result.
   `examples/lint-pages.yml` is the worked example.
2. Set that repository's **Settings › Pages › Source** to **GitHub Actions**.
3. Add it to `projects.json` here.

The collection logic lives in `action/`, so it is written once rather than copied into each
project. A project pins it by commit, `codesweep-ai/dashboards/action@<sha>`, rather than by
branch: `@main` would mean that project's build runs whatever this repository holds today.
[CONTRIBUTING.md](CONTRIBUTING.md) says what changing it then costs.

### In a fork

The page works unchanged in a fork, and shows the fork's own projects. Its paths are relative, so it
reads the status files on the host that serves it. A fork owned by `alice` serves the page at
`alice.github.io/dashboards/ci`, which reads `alice.github.io/lint/ci-status.json` and the rest. The
page still carries the codesweep-ai name, since only the data follows the fork.

A project appears once its fork publishes a status:

1. Create a fork of the project under the same owner.
2. Enable Actions on the fork's **Actions** tab, since GitHub leaves them off in a fork.
3. Set the fork's Pages source to **GitHub Actions**, as in step 2 above.

The action reads the workflow history of the repository that calls it. A fork's pin to
`codesweep-ai/dashboards/action` therefore reports the fork's own builds. A project nobody has forked
is shown as "no status".

`make status` takes the owner from the origin remote, so a fork's preview shows the fork's runs. Set
`OWNER` to preview another owner's projects. Where origin is not on GitHub, such as a sandbox's local
path, every target that reads GitHub stops at once and asks for `OWNER=` and `REPOSITORY=`. `make owner`
prints the owner the targets would read.

[SPEC.md](SPEC.md) is the contract: what a status file must contain, and what a page may assume
about one. A project that satisfies it needs to know nothing else about these pages.

### Missing and stale projects

Because each project publishes independently, the page is explicit about what it received:

- **No file yet.** It is shown as a dashed "no status" card and named in a notice. It does not break
  the page or count against the green tally.
- **No ci run in 72 hours.** The card footer is flagged and the project is named in a notice.
  Green from three weeks ago is not the same claim as green now, and the page says which it is.
  The age comes from the project's latest `ci` run, not from when its file was written. A project
  that rebuilds its site on a schedule is therefore still flagged once it stops building.

## The dependencies page

The page says where each project's dependencies stand and what to do about them. It covers Go
modules, npm packages, GitHub Actions, toolchains, container images, Fedora packages and native pins
such as the Firecracker release and the guest kernel. CI runners count too, macOS and self-hosted ones
included.

It opens on **Projects**. The **Next action** card names the most urgent change across the org, with the
command that makes it, and a short list follows it. A card per project links to its own page. A
project's page starts with what to do in that project, in three tiers of urgency: fix now, plan and
routine. Below that, closed until opened, sit its advisories, licenses, supply chain signals, pins on
siblings, release lines and every dependency it has. Each row opens onto the detail behind it.

Four more views show the whole org another way:

- **Upgrades** turns every verdict into an action, in the same three tiers. One change across several
  projects is one card, such as a React major or a lockfile to refresh. A tag says what kind of work it
  is, and each card says which command or edit makes the change. The exceptions accepted in
  `deps-config.json` are listed below the tiers, with when each lapses.
- **Internal** gives each project a card of its pins on siblings, and of who pins it in turn.
- **Lifecycle** puts every runtime, OS release, runner image, kernel line and supported library on one
  calendar, and names what publishes no support window.
- **Inventory** lists every tracked dependency, with a filter. Indirect dependencies are included on
  request. Their advisories are checked, and their newest release is not looked up, so they read as not
  compared.

The headline counts the projects with something to fix now. A [libyear](https://libyear.com/) is the
time between a pinned release and the newest one, and the tiles add them up across the org.

A verdict comes with the evidence a reviewer would ask for:

- **Exploitation.** An advisory CISA lists as exploited in the wild is fixed now, wherever it runs. The
  rest are ordered by EPSS, a public estimate of how likely an exploit is.
- **Reachability.** govulncheck, the Go team's scanner, says whether a project's code calls what a Go
  advisory names. An advisory nothing calls moves from fix now to plan.
- **Clean fixes.** The release a card suggests is looked up too, so it has no advisory of its own.
- **Licenses.** Each license is graded against the policy in `deps-config.json` and nothing else, and a
  license only matters where the dependency ships. Fedora packages are graded from their spec files. A
  license the policy does not name becomes a routine action to add it. A stricter license found in a
  package's own files is shown beside the one it declares.
- **Supply chain signals.** Deprecated and abandoned packages, install scripts, failing OpenSSF Scorecard
  checks and actions pinned by a movable tag are all flagged.
- **How long a gap has been open**, dated from the advisory, the end of life or the release. Fix-by dates
  appear only when `deps-config.json` sets a policy for them.
- **Accepted exceptions**, recorded in `deps-config.json` with a reason and a date they lapse.

`deps-feed.xml`, beside the page, is an Atom feed of what needs fixing now. Following it in a feed reader
is the alert, and needs no account. `deps.cdx.json` is the same inventory as a CycloneDX SBOM, with a VEX
analysis on each advisory, for tools such as Dependency-Track.

### For agents

`deps-actions.json`, beside the page, is the actions file: every change the page recommends, per project,
written for an agent to carry out in a clone. Each action lists the commands to run and the edits to make,
at their file and line. It says what each dependency moves from and to, and when the change counts as
done. It also carries the `accepted` entry to propose when a change should not be made. An agent needs one GET:

```sh
curl -s https://codesweep.ai/dashboards/deps-actions.json
```

[SPEC.md](SPEC.md#the-actions-file) describes every field.

### Where the data comes from

The page reads `deps.json`, the dependencies file, which is published beside it. This site's build
writes it by running the collector, which clones each project's default branch over plain git and asks
public sources about every dependency. It needs no API key. **Sources**, at the foot of the page, lists
every source and what each gives, and [SPEC.md](SPEC.md#information-sources) describes each in full.

In a fork, the build clones the fork owner's repositories and reads the history its own site published,
so the page shows the fork's projects. The codesweep-ai modules, packages and images still count as
siblings, because a fork keeps their names. The page keeps the codesweep-ai name too.

The collector runs every day as well as on every push to this repository. Dependencies move without
anyone committing: a registry publishes a release, a runtime reaches its end of life, an advisory lands.
The page flags a file older than 36 hours.

### When a dependency is missing

The collector finds dependencies in manifests, workflows and Containerfiles on its own. A version kept
anywhere else, such as in a Go constant, needs a pin in `deps-config.json`: the file, a pattern that
matches the version, and where newer releases are published. A pin that stops matching is flagged on the
page. [SPEC.md](SPEC.md#the-dependencies-configuration) lists the fields, and the other ways to declare a
version.

## Local preview

`_preview/` mirrors the `codesweep.ai` layout, so relative paths and same-origin behaviour are
exercised exactly as in production:

```sh
export GH_TOKEN=$(gh auth token)
make deps status dependencies preview
# then open http://localhost:8732/dashboards/ci.html
# or        http://localhost:8732/dashboards/deps.html
```

The preview server runs no Jekyll and serves no extensionless URLs, so follow `ci.html` rather than
`ci`. `make dependencies` writes `deps.json`, `deps-actions.json`, `deps-feed.xml` and `deps.cdx.json`
into the tree. It needs no token, and needs Go for govulncheck. It takes a few minutes.
[CONTRIBUTING.md](CONTRIBUTING.md) has the rest.

`?theme=light|dark|system` overrides the theme for one load without saving it.

## Docs

- [SPEC.md](SPEC.md) · the contract: what a project must publish, what the dependencies file and the
  actions file hold, where their information comes from, how it is compared with prior art, and what a
  page may assume
- [CONTRIBUTING.md](CONTRIBUTING.md) · working on the pages: previewing a change, the design rules,
  commit shape and writing

## Contributing

Bug reports and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) has the rules, and
applies to coding agents as much as to people. It also covers how to report a security issue
privately.

**Testing.** `make ci` must pass before you open a PR. It runs every gate CI runs. No gate looks at
a page, so preview the result as well.

## License

[Apache-2.0](LICENSE). See [LICENSE](LICENSE) and [NOTICE](NOTICE).
