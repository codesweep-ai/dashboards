# dashboards

> **Health dashboards for the codesweep-ai projects: each repository publishes its own status, and
> the browser assembles them into one page.**

[![CI](https://github.com/codesweep-ai/dashboards/actions/workflows/ci.yml/badge.svg)](https://github.com/codesweep-ai/dashboards/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Pages](https://img.shields.io/badge/pages-codesweep.ai%2Fdashboards-lightgrey)

What is true across every codesweep-ai project, on one page each, at
**https://codesweep.ai/dashboards/**.

| Page | URL | State |
|---|---|---|
| Continuous integration | `/dashboards/ci` | built |
| Dependencies | `/dashboards/dependencies` | not built yet |
| Open-source readiness | `/dashboards/oss` | not built yet |
| Releases | `/dashboards/releases` | not built yet |

Pages are flat files. `ci.html` is served at `/dashboards/ci` as well as
`/dashboards/ci.html`, so links need no extension.

## The CI page

Nothing collects the data centrally. **Each project publishes its own status as part of its Pages
deployment**, and this page assembles them in the browser.

```
codesweep.ai/lint/ci-status.json      published by lint's pages workflow
codesweep.ai/ledger/ci-status.json    …
codesweep.ai/dashboards/
    projects.json                     where to find them
    ci.html + ci.js                   fetches the above and renders
```

### Why it is shaped this way

Every one of those files is served from **the same origin**, `codesweep.ai`, because each project's
Pages site is a path under the org's custom domain. That removes most of the problems this kind of
dashboard usually has:

- **No CORS.** Same-origin fetches need no headers and no proxy.
- **No API rate limit.** Calling the GitHub API from the browser is capped at 60 requests an hour
  per IP anonymously. Conditional requests do not help, because GitHub exempts a `304` only *"if
  the request was made while correctly authorized with an `Authorization` header"*. Static JSON has
  no such ceiling.
- **No token, anywhere.** A token in a public page is a leaked token, so client-side API calls could
  never be authorised. Instead each project reads only *itself*, inside its own Actions job, where
  the built-in `GITHUB_TOKEN` already has the access. It never leaves that job.
- **No commits.** The status is written during the Pages build and deployed with the site, so it
  never enters git history. This is why the projects move to workflow-built Pages: on a
  branch-served site the branch *is* the published site, so publishing a file would mean committing
  it on every build.
- **Event-driven, not polled.** The page is current as of each project's last build, because that
  build is what published the file. Nothing polls and nothing is scheduled.

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
# then open http://localhost:8732/dashboards/
```

Two differences from production, both harmless: `python3 -m http.server` does not serve
extensionless URLs (use `ci.html`), and it sends no cache headers.

`?theme=light|dark|system` overrides the theme for one load without saving it, which is also how to
screenshot a specific theme.

## Design

These pages are styled with the [`@codesweep-ai/ui`](https://github.com/codesweep-ai/ui) design
tokens. The package is a dependency in `package.json`, and `npm run tokens` copies its tokens out
through its own `@codesweep-ai/ui/tokens` export. The result, `tokens.css`, is a build artifact
rather than a file kept here. `dashboard.css` holds no colour or spacing literals of its own, and
class names are BEM, as `DESIGN_SYSTEM_SPEC.md` §7.8 prescribes for domain CSS. Dark is the default because that is what
`tokens.css` puts on `:root`, and the theme control is that package's `ThemeToggle` (`icon-cycle`
variant) rebuilt in plain HTML from its documented spec.

To take a design-system update, move the dependency: `npm install @codesweep-ai/ui@latest`.

## Files

| Path | What it is |
|---|---|
| `index.html` | The index of dashboards. |
| `ci.html`, `ci.js` | The CI page and its logic. |
| `dashboard.css` | Shared styles, in BEM, against the tokens. |
| `package.json` | The design system this repository depends on. |
| `scripts/copy-tokens.mjs` | Copies the tokens out of that package into `tokens.css`. |
| `projects.json` | Where each project publishes its status. The only file to edit when the set changes. |
| `action/` | The composite action each project runs to write its own `ci-status.json`. |
| `examples/` | A worked Pages workflow to adapt per project. |
| `SPEC.md` | The contract between a project and these pages. |
| `CONTRIBUTING.md` | Conventions, and how to preview a change. |

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
