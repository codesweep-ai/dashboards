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
  "window": 20,                      // how many runs per workflow are summarised
  "repo": {
    "name": "lint",
    "full_name": "codesweep-ai/lint",
    "description": "…",              // may be empty
    "url": "https://github.com/codesweep-ai/lint",
    "branch": "main",                // the branch the runs are from
    "pushed_at": "2026-09-09T22:00:00Z"
  },
  "workflows": [ /* see below */ ]
}
```

Each entry in `workflows` has this shape:

```jsonc
{
  "name": "ci",
  "path": ".github/workflows/ci.yml", // "" when the workflow is gone but its runs remain
  "declared": true,                   // present in .github/workflows today
  "latest": { /* run, or null when it has never run */ },
  "runs": [ /* newest first, at most `window` of them */ ],
  "counted": 20,                      // runs that reached a verdict
  "pass_rate": 85,                    // percent of `counted`, or null when counted is 0
  "failures": 3,
  "median_duration": 218,             // seconds, or null
  "green_streak": 12,                 // consecutive passes at the tip
  "last_failure": { /* run, or null */ }
}
```

Each run:

```jsonc
{
  "state": "success",                 // conclusion, or status when still running
  "started": "2026-09-09T21:00:00Z",
  "duration": 218,                    // seconds, or null
  "title": "Bump dependencies",
  "sha": "70fa286",                   // 7 characters
  "event": "push",
  "attempt": 1,
  "actor": "octocat",
  "url": "https://github.com/…/actions/runs/123"
}
```

### Rules

- **`schema` is the compatibility gate.** Add optional fields freely; a page must
  ignore what it does not recognise. Removing a field, or changing what one
  means, is a bump.
- **Times are UTC, `YYYY-MM-DDTHH:MM:SSZ`.** They carry no offset and no
  fractional seconds.
- **Durations are whole seconds**, or `null` when they cannot be computed.
- **`null` means "not applicable", never zero.** A workflow that has never run has
  `latest: null`, not a synthetic run; a rate over no runs is `null`, not `0`.
- **Only the default branch is summarised.** Pull-request runs are out of scope.
- **The Pages build GitHub generates is never reported.** It is not CI, and not
  the repository's to report.
- **The run writing the file is never reported.** It is in flight while it reads
  the API, and would otherwise put a workflow that never finishes on the card.
  Its workflow's finished runs are still reported: whatever publishes the status
  can fail, and that failure is worth seeing.
- **Cancelled and skipped runs are not verdicts.** They are excluded from
  `counted`, so they neither pass nor fail. They still appear in `runs`.

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

`status` is resolved relative to `projects.json`. It must stay same-origin, for
the reason below.

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
