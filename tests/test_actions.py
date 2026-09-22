"""The steps the actions file prints, checked against fixture projects.

Each directory under tests/testdata holds a project's manifests as a clone
does. The collector reads it the way it reads a clone, the facts a lookup would
add are laid over the records by hand, and each test asserts what an agent
would run.
"""

import json
import os
import unittest

from collector import actions, extract, gitrepo

TESTDATA = os.path.join(os.path.dirname(__file__), "testdata")
QUIET = {"status": "current", "level": "good"}
with open(os.path.join(os.path.dirname(__file__), "..", "deps-config.json")) as fh:
    CONFIG = json.load(fh)


def fixture(name, facts):
    """The project in testdata/<name>, with this repository's configuration for the project of that name.

    `facts` maps a record's name to what resolving it would find.
    """
    repo = gitrepo.Repo(os.path.join(TESTDATA, name), f"https://github.com/codesweep-ai/{name}")
    deps = extract.repository(repo, "codesweep-ai", [p for p in CONFIG["pins"] if p["project"] == name],
                              [a for a in CONFIG.get("after", []) if a["project"] == name])["dependencies"]
    for d in deps:
        d.update(facts.get(d["name"], QUIET))
    return {"name": name, "repo": {"url": repo.url, "sha": "abc", "branch": "main"}, "dependencies": deps}


def document(project):
    """The project's actions as the actions file lists them, by id."""
    project["_actions"] = actions.build([project])
    doc = actions.agent_document({"generated": "2026-09-22T00:00:00Z", "org": "codesweep-ai", "projects": [project]},
                                 "https://x/d/", "spec")
    return {a["id"]: a for a in doc["projects"][0]["actions"]}


class GoProject(unittest.TestCase):
    def test_a_toolchain_move_edits_every_module_file_and_the_containerfile(self):
        p = fixture("go-project", {"go": {"status": "patch", "level": "info", "upstream": {"latest": "1.27.1"}}})
        act = document(p)["go-project:routine:runtime"]
        self.assertEqual(act["steps"], [
            {"run": "go mod edit -modfile=go.golangci.mod -go=1.27.1", "cwd": "."},
            {"run": "go mod edit -go=1.27.1", "cwd": "."},
            {"edit": "image/Containerfile.base", "line": 3, "text": "set GO_VERSION to 1.27.1, with any checksum beside it",
             "from": "1.27.0", "to": "1.27.1"},
        ])
        self.assertEqual(act["changes"][0]["files"], ["go.golangci.mod:4", "go.mod:3", "image/Containerfile.base:3"])

    def test_a_tool_moves_by_its_command_and_its_module_file_is_never_tidied(self):
        p = fixture("go-project", {
            "github.com/golangci/golangci-lint/v2": {"status": "patch", "level": "info", "upstream": {"latest": "v2.13.2"}},
            "golang.org/x/tools": {"status": "minor", "level": "info", "upstream": {"latest": "v0.50.0"}},
        })
        act = document(p)["go-project:routine:go"]
        self.assertEqual(act["steps"], [
            {"run": "go get -modfile=go.golangci.mod -tool github.com/golangci/golangci-lint/v2/cmd/golangci-lint@v2.13.2", "cwd": "."},
            {"run": "go get -modfile=go.golangci.mod golang.org/x/tools@v0.50.0", "cwd": "."},
            {"run": "go get -tool golang.org/x/tools/cmd/deadcode@v0.50.0 && go mod tidy", "cwd": "."},
            {"edit": "image/Containerfile.base", "line": 8, "text": "set the version to v0.50.0", "from": "v0.49.0", "to": "v0.50.0"},
        ])

    def test_a_sibling_pin_moves_to_its_head_commit_and_nothing_else(self):
        head = "a48d212425fe0a9d5822b3dcfe670b61dfa41045"
        p = fixture("go-project", {"github.com/codesweep-ai/ledger": {
            "status": "behind", "level": "info", "provider": "ledger",
            "lag": {"commits": 12, "head": head, "pinned": "bbe29a48e449"}}})
        act = document(p)["go-project:sync:ledger-go-github-com-codesweep-ai-ledger"]
        self.assertEqual(act["steps"], [{"run": f"go get -tool github.com/codesweep-ai/ledger/cmd/cs-ledger@{head} && go mod tidy",
                                         "cwd": "."}])


class NpmProject(unittest.TestCase):
    def test_a_sibling_pin_installs_the_build_of_its_head_commit_not_a_tag(self):
        p = fixture("npm-project", {
            "@codesweep-ai/ui": {"status": "behind", "level": "info", "provider": "ui", "lag": {
                "commits": 30, "head": "27eb21f6ee839c56e0f157dc7d1c6bf955004f3c", "version": "0.3.1-dev.20260922202805.27eb21f"}},
            "@codesweep-ai/ledger": {"status": "behind", "level": "info", "provider": "ledger", "lag": {
                "commits": 4, "head": "a48d212425fe0a9d5822b3dcfe670b61dfa41045"}},
        })
        acts = document(p)
        ui = acts["npm-project:sync:ui-npm-codesweep-ai-ui"]
        self.assertEqual(ui["steps"], [{"run": "npm install --save-exact @codesweep-ai/ui@0.3.1-dev.20260922202805.27eb21f", "cwd": "."}])
        self.assertEqual(ui["changes"][0]["to"], "0.3.1-dev.20260922202805.27eb21f")
        # No build of the head yet: the step says what to wait for rather than naming a tag.
        (step,) = acts["npm-project:sync:ledger-npm-codesweep-ai-ledger"]["steps"]
        self.assertIn("a48d212425fe", step["do"])


class Ledger(unittest.TestCase):
    def test_the_ui_pin_edits_the_go_constant_and_rebuilds_and_re_renders_after(self):
        built = "0.3.1-dev.20260922202805.27eb21f"
        p = fixture("ledger", {"@codesweep-ai/ui": {"status": "behind", "level": "info", "provider": "ui", "lag": {
            "commits": 30, "head": "27eb21f6ee839c56e0f157dc7d1c6bf955004f3c", "version": built}}})
        act = document(p)["ledger:sync:ui-npm-codesweep-ai-ui"]
        self.assertEqual(act["steps"][:2], [
            {"run": f"npm install --save-exact @codesweep-ai/ui@{built}", "cwd": "viewer"},
            {"edit": "internal/ledger/render.go", "line": 6, "text": f"set the version to {built}",
             "from": "0.3.1-dev.20260909170256.1638d27", "to": built},
        ])
        self.assertEqual(act["steps"][2:], CONFIG["after"][0]["steps"])
        self.assertNotIn("internal/ledger", [s.get("cwd") for s in act["steps"]])


if __name__ == "__main__":
    unittest.main()
