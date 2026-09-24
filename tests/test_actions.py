"""The steps the actions file prints, checked against fixture projects.

Each directory under tests/testdata holds a project's manifests as a clone
does. The collector reads it the way it reads a clone, the facts a lookup would
add are laid over the records by hand, and each test asserts what an agent
would run.
"""

import json
import os
import unittest

from collector import actions, extract, gitrepo, resolve

TESTDATA = os.path.join(os.path.dirname(__file__), "testdata")
QUIET = {"status": "current", "level": "good"}
with open(os.path.join(os.path.dirname(__file__), "..", "deps-config.json")) as fh:
    CONFIG = json.load(fh)


def fixture(name, facts):
    """The project in testdata/<name>, with this repository's configuration for the project of that name.

    `facts` maps a record's name to what resolving it would find.
    """
    repo = gitrepo.Repo(os.path.join(TESTDATA, name), f"https://github.com/codesweep-ai/{name}")
    found = extract.repository(repo, "codesweep-ai", [p for p in CONFIG["pins"] if p["project"] == name],
                               [a for a in CONFIG.get("after", []) if a["project"] == name])
    deps = found["dependencies"] + extract.lockfile_records(found["dependencies"], found["installed"], "codesweep-ai")
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

    def test_a_held_sibling_pin_is_no_action(self):
        # Its sibling lists no build, so there is nothing to move it to, the head
        # least of all.
        head = "a48d212425fe0a9d5822b3dcfe670b61dfa41045"
        p = fixture("go-project", {"github.com/codesweep-ai/ledger": {
            "status": "held", "level": "idle", "provider": "ledger",
            "lag": {"commits": 0, "head": head, "pinned": "bbe29a48e449", "held": "ledger lists no build"}}})
        acts = document(p)
        self.assertFalse([c for a in acts.values() for c in a["changes"] if c["name"] == "github.com/codesweep-ai/ledger"])

    def test_a_sibling_pin_moves_to_its_last_passing_build_rather_than_its_head(self):
        head, built = "a48d212425fe0a9d5822b3dcfe670b61dfa41045", "c0ffee00c0ffee00c0ffee00c0ffee00c0ffee00"
        p = fixture("go-project", {"github.com/codesweep-ai/ledger": {
            "status": "behind", "level": "info", "provider": "ledger",
            "lag": {"commits": 5, "head": head, "built": built, "pinned": "bbe29a48e449"}}})
        act = document(p)["go-project:sync:ledger-go-github-com-codesweep-ai-ledger"]
        self.assertEqual(act["steps"], [{"run": f"go get -tool github.com/codesweep-ai/ledger/cmd/cs-ledger@{built} && go mod tidy",
                                         "cwd": "."}])

    def test_a_vulnerable_module_is_done_when_no_module_file_requires_an_affected_version(self):
        p = fixture("go-project", {"golang.org/x/tools": {
            "status": "vulnerable", "level": "serious", "fix": "v0.50.0", "upstream": {"latest": "v0.50.0"},
            "vulnerabilities": [{"id": "GO-2026-0001", "severity": "high", "fixed": "0.50.0"}]}})
        (change,) = [c for a in document(p).values() for c in a["changes"] if c["name"] == "golang.org/x/tools"]
        self.assertEqual(change["done_when"], "go.golangci.mod and go.mod require no version of golang.org/x/tools that GO-2026-0001 affects")


class NpmProject(unittest.TestCase):
    def test_a_sibling_pin_installs_the_build_of_its_last_built_commit_not_a_tag(self):
        p = fixture("npm-project", {
            "@codesweep-ai/ui": {"status": "behind", "level": "info", "provider": "ui", "lag": {
                "commits": 30, "head": "27eb21f6ee839c56e0f157dc7d1c6bf955004f3c",
                "built": "27eb21f6ee839c56e0f157dc7d1c6bf955004f3c", "version": "0.3.1-dev.20260922202805.27eb21f"}},
            "@codesweep-ai/ledger": {"status": "behind", "level": "info", "provider": "ledger", "lag": {
                "commits": 4, "head": "a48d212425fe0a9d5822b3dcfe670b61dfa41045",
                "built": "a48d212425fe0a9d5822b3dcfe670b61dfa41045"}},
        })
        acts = document(p)
        ui = acts["npm-project:sync:ui-npm-codesweep-ai-ui"]
        self.assertEqual(ui["steps"], [{"run": "npm install --save-exact @codesweep-ai/ui@0.3.1-dev.20260922202805.27eb21f", "cwd": "."}])
        self.assertEqual(ui["changes"][0]["to"], "0.3.1-dev.20260922202805.27eb21f")
        # The registry holds no version of that build yet: the step says what to wait
        # for rather than naming a tag.
        (step,) = acts["npm-project:sync:ledger-npm-codesweep-ai-ledger"]["steps"]
        self.assertIn("a48d212425fe", step["do"])

    def test_a_fix_is_done_when_the_lockfile_carries_no_affected_copy_whatever_its_version(self):
        p = fixture("npm-project", {
            "fast-uri": {"status": "vulnerable", "level": "serious", "fix": "3.1.6",
                         "vulnerabilities": [{"id": "GHSA-aaaa-bbbb-cccc", "severity": "high", "fixed": "3.1.6"}]},
            "vitest": {"status": "vulnerable", "level": "serious", "fix": "4.1.11", "upstream": {"latest": "4.1.11"},
                       "vulnerabilities": [{"id": "GHSA-dddd-eeee-ffff", "severity": "critical", "fixed": "2.1.10"},
                                           {"id": "GHSA-gggg-hhhh-iiii", "severity": "moderate", "fixed": "4.1.11"}]},
        })
        acts = document(p)
        (lock,) = acts["npm-project:lock:npm-project-package-lock-json"]["changes"]
        self.assertEqual(lock["done_when"], "package-lock.json carries no copy of fast-uri that GHSA-aaaa-bbbb-cccc affects")
        (vitest,) = acts["npm-project:dep:npm-vitest"]["changes"]
        self.assertEqual(vitest["done_when"],
                         "package-lock.json carries no copy of vitest that GHSA-dddd-eeee-ffff or GHSA-gggg-hhhh-iiii affects")
        self.assertEqual(acts["npm-project:lock:npm-project-package-lock-json"]["tier"], "fix")

    VITEST = {
        "vitest": {"status": "vulnerable", "level": "serious", "fix": "4.1.11", "upstream": {"latest": "4.1.11"},
                   "vulnerabilities": [{"id": "GHSA-dddd-eeee-ffff", "severity": "critical", "fixed": "4.1.11"}]},
        "@vitest/mocker": {"status": "vulnerable", "level": "serious", "fix": "4.1.11",
                           "vulnerabilities": [{"id": "GHSA-dddd-eeee-ffff", "severity": "critical", "fixed": "4.1.11"}]},
        "vite": {"status": "vulnerable", "level": "serious", "fix": "6.4.3",
                 "vulnerabilities": [{"id": "GHSA-jjjj-kkkk-llll", "severity": "high", "fixed": "6.4.3"}]},
        "esbuild": {"status": "vulnerable", "level": "warning", "fix": "0.25.0",
                    "vulnerabilities": [{"id": "GHSA-mmmm-nnnn-oooo", "severity": "moderate", "fixed": "0.25.0"}]},
    }
    # What deps.dev resolves for vitest 4.1.11: its mocker in lockstep, a vite 5.4.21 cannot satisfy, and no esbuild.
    GRAPH = {"vitest": {"4.1.11": []}, "@vitest/mocker": {"4.1.11": ["4.1.11"]}, "vite": {"8.3.0": ["^6.0.0 || ^7.0.0 || ^8.0.0"]}}

    def cleared(self, facts, graph):
        p = fixture("npm-project", facts)

        class Graphs:
            def npm_graph(self, name, version):
                return graph if (name, version) == ("vitest", "4.1.11") else None
        resolve.Resolver(Graphs(), "codesweep-ai", {}, None).cleared_by_moves([p])
        return document(p)

    def test_a_refresh_the_upgrade_clears_in_full_is_not_listed(self):
        acts = self.cleared(self.VITEST, self.GRAPH)
        self.assertEqual(list(acts), ["npm-project:dep:npm-vitest"])
        act = acts["npm-project:dep:npm-vitest"]
        self.assertEqual((act["title"], act["steps"]), ("Upgrade vitest to 4.1.11 in npm-project",
                                                        [{"run": "npm install -D vitest@4.1.11", "cwd": "."}]))
        self.assertEqual([(c["name"], c.get("cleared_by")) for c in act["changes"]],
                         [("vitest", None), ("@vitest/mocker", ["vitest"]), ("vite", ["vitest"]), ("esbuild", ["vitest"])])
        self.assertEqual(act["result"], "Fixes 3 advisories")

    def test_a_copy_something_else_installs_keeps_its_refresh(self):
        facts = dict(self.VITEST, **{"fast-uri": {"status": "vulnerable", "level": "serious", "fix": "3.1.6",
                                                  "vulnerabilities": [{"id": "GHSA-aaaa-bbbb-cccc", "severity": "high", "fixed": "3.1.6"}]}})
        lock = self.cleared(facts, self.GRAPH)["npm-project:lock:npm-project-package-lock-json"]
        self.assertEqual([c["name"] for c in lock["changes"]], ["fast-uri"])

    def test_a_copy_npm_could_keep_is_not_cleared(self):
        # vite 5.4.21 satisfies ^5.0.0, so npm may keep it however new a vite deps.dev picks.
        graph = dict(self.GRAPH, vite={"6.4.3": ["^5.0.0 || ^6.0.0"]})
        lock = self.cleared(self.VITEST, graph)["npm-project:lock:npm-project-package-lock-json"]
        self.assertEqual([c["name"] for c in lock["changes"]], ["vite"])


class Ledger(unittest.TestCase):
    def test_the_ui_pin_edits_the_go_constant_and_rebuilds_and_re_renders_after(self):
        built = "0.3.1-dev.20260922202805.27eb21f"
        p = fixture("ledger", {"@codesweep-ai/ui": {"status": "behind", "level": "info", "provider": "ui", "lag": {
            "commits": 30, "head": "27eb21f6ee839c56e0f157dc7d1c6bf955004f3c",
            "built": "27eb21f6ee839c56e0f157dc7d1c6bf955004f3c", "version": built}}})
        act = document(p)["ledger:sync:ui-npm-codesweep-ai-ui"]
        self.assertEqual(act["steps"][:2], [
            {"run": f"npm install --save-exact @codesweep-ai/ui@{built}", "cwd": "viewer"},
            {"edit": "internal/ledger/render.go", "line": 6, "text": f"set the version to {built}",
             "from": "0.3.1-dev.20260909170256.1638d27", "to": built},
        ])
        self.assertEqual(act["steps"][2:], next(a["steps"] for a in CONFIG["after"] if a["project"] == "ledger"))
        self.assertNotIn("internal/ledger", [s.get("cwd") for s in act["steps"]])


# tracer and campaign commit the viewer their ui build produces, and the binary
# embeds it, so a move that only installs leaves the committed viewer stale.
UI_BUILD = "0.3.1-dev.20260922233547.67ef1cf"
UI_BEHIND = {"@codesweep-ai/ui": {"status": "behind", "level": "info", "provider": "ui", "lag": {
    "commits": 4, "head": "67ef1cf11c130f524597e0b99beabf739899ab16",
    "built": "67ef1cf11c130f524597e0b99beabf739899ab16", "version": UI_BUILD}}}


class Tracer(unittest.TestCase):
    def test_a_ui_move_rebuilds_the_committed_viewer_after_the_install(self):
        act = document(fixture("tracer", UI_BEHIND))["tracer:sync:ui-npm-codesweep-ai-ui"]
        self.assertEqual(act["steps"], [
            {"run": f"npm install --save-exact @codesweep-ai/ui@{UI_BUILD}", "cwd": "apps/viewer"},
            {"run": "make viewer-build", "cwd": "."},
        ])


class Campaign(unittest.TestCase):
    def test_a_ui_move_rebuilds_the_committed_page_after_the_install(self):
        act = document(fixture("campaign", UI_BEHIND))["campaign:sync:ui-npm-codesweep-ai-ui"]
        self.assertEqual(act["steps"], [
            {"run": f"npm install --save-exact @codesweep-ai/ui@{UI_BUILD}", "cwd": "dispatch-viewer/app"},
            {"run": "make viewer-build", "cwd": "."},
        ])


if __name__ == "__main__":
    unittest.main()
