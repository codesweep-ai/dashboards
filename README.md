# dashboards

> **Health dashboards for the codesweep-ai open-source projects on GitHub.**

[![CI](https://github.com/codesweep-ai/dashboards/actions/workflows/ci.yml/badge.svg)](https://github.com/codesweep-ai/dashboards/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Pages](https://img.shields.io/badge/pages-codesweep.ai%2Fdashboards-lightgrey)

This repository holds the web pages that show how the codesweep-ai projects on GitHub are doing.
The set will grow.

The first is **Continuous integration**, at
**[https://codesweep.ai/dashboards/ci](https://codesweep.ai/dashboards/ci)**.

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

[SPEC.md](SPEC.md) is the contract: what a status file must contain, and what a page may assume
about one. A project that satisfies it needs to know nothing else about these pages.

### Missing and stale projects

Because each project publishes independently, the page is explicit about what it received:

- **No file yet.** It is shown as a dashed "no status" card and named in a notice. It does not break
  the page or count against the green tally.
- **A file older than 72 hours.** The card footer is flagged and the project is named in a notice.
  Green from three weeks ago is not the same claim as green now, and the page says which it is.
  Nothing refreshes a status on a schedule, deliberately: a cron would update the timestamp without
  updating the facts, hiding a project that has quietly stopped building.

## Local preview

`_preview/` mirrors the `codesweep.ai` layout, so relative paths and same-origin behaviour are
exercised exactly as in production:

```sh
export GH_TOKEN=$(gh auth token)
make deps status preview
# then open http://localhost:8732/dashboards/ci.html
```

The preview server runs no Jekyll and serves no extensionless URLs, so follow `ci.html` rather than
`ci`. [CONTRIBUTING.md](CONTRIBUTING.md) has the rest.

`?theme=light|dark|system` overrides the theme for one load without saving it.

## Docs

- [SPEC.md](SPEC.md) · the contract: what a project must publish, and what a page may assume
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
