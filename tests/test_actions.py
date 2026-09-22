"""The steps the actions file prints, checked against fixture projects.

Each directory under tests/testdata holds a project's manifests as a clone
does. The collector reads it the way it reads a clone, the facts a lookup would
add are laid over the records by hand, and each test asserts what an agent
would run.
"""

import os
import unittest

from collector import actions, extract, gitrepo

TESTDATA = os.path.join(os.path.dirname(__file__), "testdata")
QUIET = {"status": "current", "level": "good"}


def fixture(name, facts):
    """The project in testdata/<name>. `facts` maps a record's name to what resolving it would find."""
    repo = gitrepo.Repo(os.path.join(TESTDATA, name), f"https://github.com/codesweep-ai/{name}")
    deps = extract.repository(repo, "codesweep-ai")["dependencies"]
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


if __name__ == "__main__":
    unittest.main()
