# dashboards — check/preview.
# `make check` is the full local gate: the data files, the action, and the
# document linters. `make ci` adds every gate CI runs.
# Nothing here compiles. The pages are served as they are, so what would be a
# build elsewhere is `make build` assembling the local preview tree.
# The linters need Go alone: they are pinned as `tool` directives in go.mod and
# run with `go tool`, so a fresh checkout runs the gate with nothing installed
# by hand. `make repin` moves that pin.
# The pages need Node for one thing only: the design tokens come out of the
# @codesweep-ai/ui package rather than a copy in this repository.
# The dependencies collector needs Python 3 and git and nothing installed: it is
# the standard library, run as `python3 -m collector`.

CS_LINT ?= go tool cs-lint
PYTHON  ?= python3
NPM     ?= npm

# The pages and the data the preview tree is assembled from. README.md is not
# here: Jekyll renders it as the site's index in production, and the preview
# server runs no Jekyll, so there is nothing to copy.
SITE     := ci.html ci.js deps.html deps.js dashboard.css tokens.css projects.json
PREVIEW  ?= _preview
PORT     ?= 8732
STATUS_TMP := $(shell mktemp -u -t ci-status.XXXXXX.json)
DEPS_TMP   := $(shell mktemp -u -t deps.XXXXXX.json)
# The projects whose status files the preview mirrors. Read from projects.json
# so this list cannot drift from the one the page actually loads.
PROJECTS := $(shell $(PYTHON) -c "import json;print(' '.join(p['name'] for p in json.load(open('projects.json'))['projects']))" 2>/dev/null)
# This checkout's repository, and the owner whose projects `status` and the
# collector read, so a fork previews its own. Actions sets GITHUB_REPOSITORY, and
# elsewhere it comes from the origin remote. Set OWNER to read another owner's.
REPOSITORY ?= $(or $(GITHUB_REPOSITORY),$(shell git remote get-url origin 2>/dev/null | sed -E 's#^(https://github\.com/|git@github\.com:|ssh://git@github\.com/)##; s#\.git$$##'))
OWNER      ?= $(firstword $(subst /, ,$(REPOSITORY)))

.PHONY: help deps tokens build test check ci lint actionlint prose refs oss data ledger preview status dependencies repin clean

.DEFAULT_GOAL := help

## help: list available targets (this menu)
help:
	@echo "dashboards make targets:"
	@grep -E '^## [a-z][a-z0-9-]*: ' $(MAKEFILE_LIST) | sed -E 's/^## ([^:]+): (.*)/  \1|\2/' | column -t -s '|'

## deps: install what the pages are built from
deps:
	$(NPM) ci

## tokens: copy the design tokens out of @codesweep-ai/ui
##
## The package is the source. tokens.css is a build artifact, gitignored, and
## `npm install` is what updates it.
tokens: tokens.css
tokens.css: package-lock.json scripts/copy-tokens.mjs
	@test -d node_modules || $(NPM) install --silent
	@$(NPM) run --silent tokens

## build: assemble the local preview tree from the pages and the status files
build: $(PREVIEW)/dashboards
$(PREVIEW)/dashboards: $(SITE)
	@mkdir -p $(PREVIEW)/dashboards
	@cp $(SITE) $(PREVIEW)/dashboards/
	@echo "build: $(PREVIEW)/dashboards is current"

## status: write each project's ci-status.json into the preview tree
##
## Stands in for what a project's own Pages build will publish, so the page can
## be looked at before any project publishes anything. It reads the same API the
## action reads in CI, so what you see is what the real file will say.
##
## Needs a GitHub token in GH_TOKEN, which `gh auth token` prints. The published
## pages never do this: they read static files and hold no token.
status:
	@test -n "$(PROJECTS)" || { echo "status: no projects in projects.json" >&2; exit 1; }
	@test -n "$(OWNER)" || { echo "status: no owner, since origin is not on GitHub. Set OWNER." >&2; exit 1; }
	@test -n "$$GH_TOKEN$$GITHUB_TOKEN" || { \
	  echo "status: no GH_TOKEN in the environment. Run:" >&2; \
	  echo "    export GH_TOKEN=\$$(gh auth token)" >&2; exit 1; }
	@for p in $(PROJECTS); do \
	  mkdir -p $(PREVIEW)/$$p; \
	  CI_STATUS_OUTPUT=$(PREVIEW)/$$p/ci-status.json ./action/ci-status "$(OWNER)/$$p" >/dev/null \
	    || { echo "status: $$p failed" >&2; exit 1; }; \
	  echo "  $$p"; \
	done

## dependencies: write deps.json into the preview tree
##
## Runs the collector the site's build runs: it clones every project, asks
## public registries about each dependency, and runs govulncheck over the Go
## projects, which takes a few minutes. It needs Go and no token. With GH_TOKEN set it reads GitHub releases through the API, and
## without one it reads github.com's release feeds, which list fewer releases.
dependencies:
	@mkdir -p $(PREVIEW)/dashboards
	@PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m collector --owner $(OWNER) --output $(PREVIEW)/dashboards/deps.json \
	  --feed $(PREVIEW)/dashboards/deps-feed.xml --sbom $(PREVIEW)/dashboards/deps.cdx.json \
	  --actions $(PREVIEW)/dashboards/deps-actions.json

## preview: serve the preview tree at http://localhost:$(PORT)/dashboards/
##
## Says so when the tree holds no status files. Every card would read "no
## status", which is the page working correctly and looks like it is broken.
preview: build
	@n=$$(ls $(PREVIEW)/*/ci-status.json 2>/dev/null | wc -l); \
	if [ "$$n" -eq 0 ]; then \
	  echo "preview: no status files yet, so every project will read \"no status\"."; \
	  echo "preview: run 'make status' to write them from the GitHub API."; \
	else \
	  echo "preview: $$n of $(words $(PROJECTS)) projects have a status file"; \
	fi
	@test -s $(PREVIEW)/dashboards/deps.json || \
	  echo "preview: no deps.json yet, so the dependencies page has nothing to show. Run 'make dependencies'."
	@echo "preview: http://localhost:$(PORT)/dashboards/ci.html (ctrl-c to stop)"
	@cd $(PREVIEW) && $(PYTHON) -m http.server $(PORT)

## data: check projects.json is well formed and same-origin
data:
	@$(PYTHON) scripts/check-projects.py projects.json

## test: check the action and the collector still write what SPEC.md describes
##
## The action's end-to-end run needs a token, so without one it reports a skip
## rather than a pass: a run that checked nothing must never read as a run that
## checked everything. The collector's needs none, so it always runs, over this
## repository alone to keep it quick.
test:
	@$(PYTHON) -m py_compile action/ci-status && echo "test: action/ci-status compiles"
	@PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -t . -q
	@PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m collector --owner $(OWNER) --only dashboards --no-reachability --output $(DEPS_TMP) \
	  >/dev/null 2>$(DEPS_TMP).log || { cat $(DEPS_TMP).log >&2; exit 1; }
	@$(PYTHON) scripts/check-deps.py $(DEPS_TMP)
	@if [ -n "$$GH_TOKEN$$GITHUB_TOKEN" ]; then \
	  CI_STATUS_OUTPUT=$(STATUS_TMP) ./action/ci-status $(REPOSITORY) >/dev/null && \
	  $(PYTHON) scripts/check-status.py $(STATUS_TMP); \
	else \
	  echo "test: SKIP the end-to-end run, no GH_TOKEN in the environment"; \
	fi

## lint: check the workflows
lint: actionlint

## actionlint: check the workflow files themselves
actionlint:
	go tool actionlint

## prose: check how this repository's documents are written
prose:
	$(CS_LINT) prose

## refs: check that everything the documents point at is there
refs:
	$(CS_LINT) refs

## oss: the rules this repo has to satisfy as a published project
oss:
	$(CS_LINT) oss

# The three targets above are one shared tool: github.com/codesweep-ai/lint,
# pinned in go.mod and run with `go tool`, so the gates use the version this
# repo records rather than whatever a machine happens to have installed. There
# is no `surface` target, because that rule reads the binary a repository
# builds and this one builds none.
# Its knobs for this repo live in .cs-lint.yaml, and `cs-lint <linter> --explain`
# prints what each rule wants.

## ledger: validate the issue records and prove ledger.html is current
##
## cs-ledger is pinned in go.mod and run with `go tool`, so this gate is real on
## every machine rather than skipping where the binary was never installed.
## ledger.html is generated: change a record, re-render, and commit the two
## together.
ledger:
	go tool cs-ledger check ledger

## repin: move the pinned tools to their latest release
repin:
	go get -tool github.com/codesweep-ai/lint/cmd/cs-lint@latest
	go get -tool github.com/codesweep-ai/ledger/cmd/cs-ledger@latest
	go get -tool github.com/rhysd/actionlint/cmd/actionlint@latest
	go get -tool golang.org/x/vuln/cmd/govulncheck@latest
	go mod tidy

## check: the gate a contributor runs before pushing
check: data test prose refs oss

## ci: every gate the CI workflow runs, on this machine
##
## The jobs of .github/workflows/ci.yml, in the order CI runs them, so a red
## build is something you can see before you push rather than after.
##
## check is the faster subset to keep beside you while you work. The two gates
## it does not carry are the ones with something to install: actionlint, and the
## ledger check. The sibling projects draw the line in the same place.
ci: check actionlint ledger
	@printf '\nci: every gate ran. Not reproduced here: looking at the pages.\n'

## clean: remove the local preview tree, the copied tokens and the collector's local files
clean:
	rm -rf $(PREVIEW) tokens.css deps.json deps-actions.json deps-feed.xml deps.cdx.json
	@echo "clean: $(PREVIEW), tokens.css and the collector's local files removed"
