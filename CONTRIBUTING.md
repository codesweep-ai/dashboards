# Contributing to dashboards

Bug reports and pull requests are welcome. These rules apply to humans and
coding agents alike. If you are an agent working in this repository, read this
file before you change anything and follow it.

For a security issue, use GitHub's private vulnerability reporting on this
repository's Security tab, rather than opening a public issue.

## Submitting a change

File a bug or an idea as a GitHub issue on this repository. For a fix that
stands on its own, a pull request on its own is enough. For anything that adds a
page, changes the status file's shape or moves a boundary in
[SPEC.md](SPEC.md), open an issue first, so the design gets settled before you
write it.

1. Fork the repository, and create a branch off `main`.
2. Make the change.
3. Preview it as described below, and look at the result in both themes.
4. Open a pull request describing what changed and why.

By opening a pull request you agree that your contribution ships under the
[Apache 2.0 licence](LICENSE) this project is released under.

## Before you push

Run the gate:

```sh
make check
```

That is every gate CI runs except `actionlint`, which `make ci` adds. Neither
can look at a page, so preview it as well.

## Previewing

`_preview/` mirrors the `codesweep.ai` layout, so relative paths and same-origin
behaviour are exercised exactly as in production rather than approximated:

```sh
export GH_TOKEN=$(gh auth token)
make deps status preview
```

`make deps` installs the design system, `status` fetches each project's status
file into the tree, and `preview` copies the pages in and serves them.

Then open `http://localhost:8732/dashboards/`. Two differences from production,
both harmless: `python3 -m http.server` does not serve extensionless URLs, so
follow `ci.html` rather than `ci`, and it sends no cache headers.

`?theme=light|dark|system` overrides the theme for one load without saving it,
which is also how to screenshot a specific theme.

Check the degraded paths too, because production will hit them first. Point an
entry in `projects.json` at a file that does not exist, and back-date a
`generated` timestamp by a week. A missing project must render as a
card and a notice rather than an error, and a stale one must say how old it is.

## Design rules

**No colour or spacing literals.** Every value resolves through a token defined
in `tokens.css`. If you find yourself typing a hex code or a pixel padding, the
token you want already exists. Surfaces are `--card` and `--bg`, status roles
are `--color-success`, `--color-error`, `--color-warning` and `--color-neutral`,
and spacing is the `--space-*` scale.

Two exceptions are deliberate, and each is commented where it sits. The run
strip's bar geometry encodes data rather than style. The hero figure derives
from `--font-size-stat`, because the system has no hero token.

**`tokens.css` is a build artifact, not source.** It is copied out of
[`@codesweep-ai/ui`](https://github.com/codesweep-ai/ui) by `npm run tokens`,
through that package's own `@codesweep-ai/ui/tokens` export, and it is
gitignored. Never edit it: the next `make build` overwrites you. To take a
design system update, move the dependency with
`npm install @codesweep-ai/ui@latest` and commit the lockfile.

**Class names are BEM**, as `DESIGN_SYSTEM_SPEC.md` §7.8 in that repository
prescribes for domain CSS outside the component library: `.ci-card__head`,
`.ci-wf__stat--flaky`.

**Reuse the component specs rather than inventing.** The theme control is that
package's `ThemeToggle`, rebuilt in plain HTML from its documented spec, and
the chips take their geometry from its chip component. Where a rebuild departs
from a spec, say so in a comment and say why. The theme toggle's two re-pointed
tokens are the worked example.

**Status colour never travels alone.** Every state carries an icon and a word as
well. The run strip encodes pass and fail by bar height as much as by colour, so
it survives greyscale and colour blindness.

## Pages

A new page is a flat `.html` file at the root, plus its `.js`, sharing
`dashboard.css` and `tokens.css`. GitHub Pages serves `foo.html` at `/foo` as
well. Link without the extension in prose, and with it in the index, which also
has to work under the local preview server.

Add it to the index in `index.html` and to the table in `README.md`. A page that
is not built yet belongs in the index as a placeholder that is visibly not a
link, rather than as a link that goes nowhere.

## Commits

**Keep it short.** One idea per commit, and a message a reader takes in at a glance. If a change
will not fit one idea, split it.

**Subject**, always. Under 60 characters, imperative, no trailing period, completing *"If applied,
this commit will …"*. Say what the change does in plain English. The test: would this subject make
sense to someone who has not read the diff and does not know this codebase? Use no category label:
`fix(ci):`, `bugfix:` and `[docs]` each name a class of change rather than the change itself,
which the diff already shows. The gate fails on one, so amend before you push.

**Body**, rarely. Most commits need none. Add one only when the subject leaves a question a reader
would otherwise have to open the diff to answer, and then answer that question. A sentence or two
does it. Wrap it at 72 columns.

Leave out how the work was scheduled, how you tested it, and what led you to it, and stop once the
question is answered. A second paragraph usually means the message has turned into a report of the
session. A rule's reason belongs beside the rule in [`SPEC.md`](SPEC.md), and the investigation that
found it belongs in the pull request.

```
Reject a status URL that is not same-origin
```

```
Exclude the publisher from its own status file

It is mid-run while it reads the API, so every card carried a
workflow that never finished.
```

Keep the `Co-Authored-By:` trailer when an agent wrote the change. Drop any trailer linking to the
agent's session or transcript. Such a link is private to whoever ran it and dead to everyone else,
and it cannot be fixed after publication.

## The ledger

This repository keeps a ledger of open issues under [`ledger/`](ledger/), as the
sibling projects do. Read `ledger/queue.json` and the open records before you
start: they are the handoff from whoever worked here last.

`ledger/ledger.html` is generated from the JSON and must never be edited by
hand. Run both of these before any commit that touches the directory, and let
the record and the page travel together in it:

```sh
go tool cs-ledger render ledger
make ledger
```

`ledger/AGENTS.md` routes to the guide, and `cs-ledger guide` prints the
doctrine from inside the binary.

## Changing the action

A project pins the action to a commit:

```yaml
uses: codesweep-ai/dashboards/action@<sha>
```

A branch would mean a project's build runs whatever this repository holds today,
and `cs-lint oss` fails a workflow that names one. A commit is the strongest
pin there is: it cannot move under a project that trusted it.

The cost is that a change to `action/` reaches a project only when that project
moves its pin. So a change here is two steps, and the second one is somebody
else's repository:

1. Land it here, with the gate green.
2. Update the `@<sha>` in each project's `pages.yml`, and say in that commit
   what moving it buys.

A change to the status file's shape is also a change to [SPEC.md](SPEC.md) and to
`schema` in it. Projects pinned to older commits keep writing the old shape until
they move, so the page has to read both until every project has.

## Docs

[SPEC.md](SPEC.md) is the contract with the projects that publish status files.
A change to what a status file contains, or to what a page may assume about one,
lands there in the same commit as the code. [README.md](README.md) is for
someone arriving at the repository; this file is for someone changing it.

## Writing

Six principles do most of the work. Read them before you write a document, and apply them when you
edit one:

1. **Introduce a term where you first use it**, in the same sentence, or link to the page that
   defines it. A reader should never meet a word the docs have not explained.
2. **State the point first, then qualify it.** Opening with the qualifier makes the reader decode
   the sentence backwards.
3. **Give every sentence a subject and a verb.** "Two version numbers, one verdict, one remedy"
   reads as knowing rather than clear. Say what the thing is.
4. **A how-to is steps that work.** Put the reasons somewhere else. A reader working through
   one wants commands that run.
5. **Describe what the software does, not how it came to do it.** Leave out what the project used
   to do, what was tried and dropped, and numbers from a run somebody did once.
6. **Do not explain a design by contrast with a worse one.** Say what it is and what you get,
   rather than asking the reader to picture a design nobody proposed.

The mechanical rules are enforced rather than restated here.
[`cs-lint`](https://github.com/codesweep-ai/lint) carries them, and `make check` runs it over this
repository. To read what a rule wants and the guidance behind it:

```bash
cs-lint prose --explain
```

That listing is the authority. Where this section and the linter disagree, the linter is right.
Turning a check off is a waiver: write it under `allow` in [`.cs-lint.yaml`](.cs-lint.yaml) with the
reason, which is printed with the finding.

## AI-assisted contributions

Agents are welcome here, on the same terms as anyone else. Read this file and
[SPEC.md](SPEC.md) first, preview what you changed, and look at it. Do not
describe a page as working because the code appears correct. These pages render
wrong while the markup validates, so the evidence is a screenshot, not a diff.
