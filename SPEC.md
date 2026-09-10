# Specification

What a project must publish for a dashboard to read it, and what a dashboard may
assume in return. This is the only contract between the two. A project that
satisfies it needs to know nothing about the pages, and a page needs to know
nothing about how a project builds.

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
  "schema": 1,                       // integer, bumped on a breaking change
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
  "url": "https://github.com/…/actions/runs/123"
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

## What a page may assume

- **Every status file is same-origin.** Each project's Pages site is a path under
  `codesweep.ai`, so a page fetches them with no CORS, no token and no API quota.
- **A page must not call the GitHub API.** Anonymous browser calls are capped at
  60 an hour per IP. Conditional requests do not help, because GitHub exempts a
  `304` from the limit only *"if the request was made while correctly authorized
  with an `Authorization` header"*. Authorising would mean putting a token in a
  public page, which is publishing it. Read the static files instead.
- **A project may be missing or stale, and that is data.** A file may 404 because
  the project has not published yet; it may be old because the project has not
  built. Both are facts worth showing, and neither may break the page or be
  counted as a failure. Nothing refreshes a status on a schedule, so its age is
  honest: a cron would update the timestamp without updating the facts.
