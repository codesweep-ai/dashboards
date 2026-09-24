"""Which commits a status file names as built, newest first, for a sibling to pin.

Each has to be a commit CI built and passed on the branch, and still on it. A
commit that changed nothing CI builds, a failed build and a build still going
are all passed over, so a pin never lands on a commit nobody knows to be good.
"""

import importlib.machinery
import io
import json
import subprocess
import tempfile
import importlib.util
import os
import unittest

ACTION = os.path.join(os.path.dirname(__file__), "..", "action", "ci-status")
_loader = importlib.machinery.SourceFileLoader("ci_status", ACTION)
_spec = importlib.util.spec_from_loader("ci_status", _loader)
ci_status = importlib.util.module_from_spec(_spec)
_loader.exec_module(ci_status)


def run(sha, name="ci", event="push", conclusion="success"):
    return {"name": name, "event": event, "conclusion": conclusion, "head_sha": sha * 40}


def everywhere(_sha):
    return True


class BuiltTest(unittest.TestCase):
    def test_newest_passing_push_builds_first(self):
        runs = [run("c"), run("b"), run("a")]
        self.assertEqual(ci_status.built(runs, everywhere), ["c" * 40, "b" * 40, "a" * 40])

    def test_passes_over_what_did_not_pass(self):
        runs = [run("e", conclusion=None), run("d", conclusion="failure"),
                run("c", conclusion="cancelled"), run("b"), run("a")]
        self.assertEqual(ci_status.built(runs, everywhere), ["b" * 40, "a" * 40])

    def test_only_the_gate_and_only_a_push(self):
        runs = [run("d", name="pages"), run("c", event="pull_request"),
                run("b", event="workflow_dispatch"), run("a")]
        self.assertEqual(ci_status.built(runs, everywhere), ["a" * 40])

    def test_passes_over_a_commit_the_branch_lost(self):
        runs = [run("b"), run("a")]
        self.assertEqual(ci_status.built(runs, lambda sha: sha != "b" * 40), ["a" * 40])

    def test_names_a_commit_built_twice_once(self):
        runs = [run("b"), run("a"), run("a")]
        self.assertEqual(ci_status.built(runs, everywhere), ["b" * 40, "a" * 40])

    def test_keeps_the_newest_few(self):
        runs = [run(c) for c in "fedcba"]
        self.assertEqual(ci_status.built(runs, everywhere, keep=2), ["f" * 40, "e" * 40])

    def test_waits_for_the_images_of_a_project_that_publishes_them(self):
        # c's images are still being published, or failed: the registry does not
        # hold its version. b's and a's are there.
        runs = [run("c"), run("b"), run("a")]
        imaged = {"b" * 40, "a" * 40}.__contains__
        self.assertEqual(ci_status.built(runs, everywhere, imaged=imaged), ["b" * 40, "a" * 40])
        # A project that publishes none needs none.
        self.assertEqual(ci_status.built(runs, everywhere), ["c" * 40, "b" * 40, "a" * 40])

    def test_images_count_for_the_commit_they_name_not_the_run(self):
        # GitHub lists the image run that c's ci started under d, main's head by
        # then, and d's own image run failed. Only the versions the registry
        # holds say which one was published.
        runs = [run("d"), run("c")]
        imaged = {"c" * 40}.__contains__
        self.assertEqual(ci_status.built(runs, everywhere, imaged=imaged), ["c" * 40])

    def test_none_when_nothing_passed(self):
        self.assertEqual(ci_status.built([run("a", conclusion="failure")], everywhere), [])
        self.assertEqual(ci_status.built([], everywhere), [])



class BuildTest(unittest.TestCase):
    """build() against a fake API: which calls it makes, and what `built` says."""
    A, B = "a" * 40, "b" * 40

    def fake(self, tags, compare=None):
        calls = []

        def api(path, token):
            calls.append(path)
            if path == "repos/o/p":
                return {"default_branch": "main"}
            if path == "repos/o/p/actions/workflows":
                return {"workflows": [{"id": 1, "name": "ci", "state": "active", "path": ".github/workflows/ci.yml"},
                                      {"id": 2, "name": "publish images", "state": "active",
                                       "path": ".github/workflows/npm-images.yml"}]}
            if path.startswith("repos/o/p/actions/workflows/ci.yml/runs?"):
                return {"workflow_runs": [run("b"), run("a")]}
            if path.startswith("repos/o/p/actions/runs?"):
                return {"workflow_runs": []}
            if path.startswith("repos/o/p/commits?sha=main"):
                return [{"sha": self.A}]
            if path.startswith("repos/o/p/compare/"):
                if compare:
                    raise compare
                return {"status": "ahead"}
            if path.startswith("repos/o/p/contents/go.mod"):
                raise ci_status.urllib.error.HTTPError(path, 404, "Not Found", {}, io.BytesIO())
            raise AssertionError(path)

        def fetch(url, headers=None):
            if "/token?" in url:
                return {"token": "t"}
            if url.endswith("/npm/p/tags/list?n=1000"):
                return {"tags": tags}
            if "/tags/list" in url:
                return {"tags": []}
            return {"versions": {}}
        return api, fetch, calls

    def build(self, api, fetch):
        saved = ci_status.api, ci_status.fetch
        ci_status.api, ci_status.fetch = api, fetch
        try:
            return ci_status.build("o", "p", "token", 20)
        finally:
            ci_status.api, ci_status.fetch = saved

    def both(self):
        return ["0.0.0-20260901000000-" + self.A[:12], "0.0.0-20260902000000-" + self.B[:12]]

    def test_a_recent_commit_is_on_the_branch_without_a_compare(self):
        api, fetch, calls = self.fake(self.both())
        data = self.build(api, fetch)
        # b is not among the branch's recent commits, so it alone is compared.
        self.assertEqual([c for c in calls if "/compare/" in c], [f"repos/o/p/compare/{self.B}...main"])
        self.assertEqual([b["commit"] for b in data["built"]], [self.B, self.A])

    def test_a_build_whose_images_the_registry_lacks_is_passed_over(self):
        # b's images failed, are still going out, or were pruned.
        api, fetch, _ = self.fake(["0.0.0-20260901000000-" + self.A[:12]])
        self.assertEqual([b["commit"] for b in self.build(api, fetch)["built"]], [self.A])

    def test_a_registry_that_does_not_answer_names_no_build(self):
        api, fetch, _ = self.fake([])
        self.assertEqual(self.build(api, fetch)["built"], [])

    def test_a_failed_lookup_stops_the_file_rather_than_dropping_a_build(self):
        err = ci_status.urllib.error.HTTPError("x", 502, "Bad Gateway", {}, io.BytesIO())
        api, fetch, _ = self.fake(self.both(), compare=err)
        with self.assertRaises(ci_status.urllib.error.HTTPError):
            self.build(api, fetch)

    def test_no_call_reads_a_commit_status(self):
        api, fetch, calls = self.fake(self.both())
        self.build(api, fetch)
        self.assertFalse([c for c in calls if c.endswith("/status")])

    def test_its_own_job_makes_one_call_the_run_history(self):
        with tempfile.TemporaryDirectory() as ws:
            def git(*args):
                return subprocess.run(["git", "-C", ws, *args], check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@example.com")
            git("config", "user.name", "t")
            os.makedirs(os.path.join(ws, ".github", "workflows"))
            for f, text in (("ci.yml", "name: ci\non: push\n"),
                            ("npm-images.yml", "# publish\nname: \"publish images\"\non: workflow_run\n"),
                            ("nameless.yml", "on: push\n")):
                with open(os.path.join(ws, ".github", "workflows", f), "w") as fh:
                    fh.write(text)
            with open(os.path.join(ws, "go.mod"), "w") as fh:
                fh.write("module example.com/p\n\ngo 1.26\n")
            git("add", "-A")
            git("commit", "-q", "-m", "a")
            a = git("rev-parse", "HEAD")
            git("commit", "-q", "--allow-empty", "-m", "b")
            b = git("rev-parse", "HEAD")
            off = "c" * 40   # built once, then force-pushed away
            event = os.path.join(ws, "event.json")
            with open(event, "w") as fh:
                json.dump({"repository": {"default_branch": "main", "description": "d", "html_url": "u",
                                          "pushed_at": 1790000000}}, fh)
            calls = []

            def api(path, token):
                calls.append(path)
                if path.startswith("repos/o/p/actions/runs?"):
                    return {"workflow_runs": [dict(run("c"), head_sha=off), dict(run("b"), head_sha=b),
                                              dict(run("a"), head_sha=a), dict(run("x", name="pages"), head_sha=b)]}
                raise AssertionError(path)

            def fetch(url, headers=None):
                if "/token?" in url:
                    return {"token": "t"}
                if url.endswith("/npm/p/tags/list?n=1000"):
                    return {"tags": ["0.0.0-1-" + sha[:12] for sha in (a, b, off)]}
                if "/tags/list" in url:
                    return {"tags": []}
                return {"versions": {}}
            env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "o/p", "GITHUB_WORKSPACE": ws, "GITHUB_EVENT_PATH": event}
            saved = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            saved_go = ci_status.go_version
            ci_status.go_version = lambda module, sha: f"{module}@{sha[:3]}"
            try:
                data = self.build(api, fetch)
            finally:
                ci_status.go_version = saved_go
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        self.assertEqual(calls, ["repos/o/p/actions/runs?branch=main&per_page=100"])
        self.assertEqual([x["commit"] for x in data["built"]], [b, a])
        self.assertEqual(data["built"][0]["versions"]["go"], f"example.com/p@{b[:3]}")
        self.assertEqual((data["repo"]["description"], data["repo"]["pushed_at"]), ("d", "2026-09-21T14:13:20Z"))
        self.assertEqual(sorted(w["name"] for w in data["workflows"]),
                         [".github/workflows/nameless.yml", "ci", "pages", "publish images"])

    def test_the_gate_s_own_runs_are_asked_for_only_when_the_history_has_none(self):
        api, fetch, calls = self.fake(self.both())
        self.build(api, fetch)
        self.assertEqual(len([c for c in calls if "/actions/workflows/ci.yml/runs?" in c]), 1)

    def test_the_scan_stops_after_its_limit(self):
        runs = [run(c) for c in "abcdef"]
        seen = []
        ci_status.built(runs, lambda sha: True, imaged=lambda sha: seen.append(sha) or False, scan=3)
        self.assertEqual(len(seen), 3)

class PublishedTest(unittest.TestCase):
    SHA = "1f8f19dd0496a01ec23a66e0ab6a9b7db3a8b8fa"

    def test_a_go_pseudo_version_names_its_commit(self):
        versions = ["0.0.0-20260923002829-11e8ee09f28d", "0.0.0-20260923200616-1f8f19dd0496"]
        self.assertEqual(ci_status.published(versions, self.SHA), "0.0.0-20260923200616-1f8f19dd0496")
        self.assertEqual(ci_status.published(["v" + versions[1]], self.SHA), "v" + versions[1])

    def test_an_npm_dev_version_names_its_commit(self):
        versions = ["0.3.1-dev.20260922230502.318a857", "0.3.1-dev.20260923200616.1f8f19d"]
        self.assertEqual(ci_status.published(versions, self.SHA), "0.3.1-dev.20260923200616.1f8f19d")

    def test_an_architecture_tag_is_no_version(self):
        tags = ["v0.0.0-20260923200616-1f8f19dd0496-amd64", "v0.0.0-20260923200616-1f8f19dd0496-arm64"]
        self.assertIsNone(ci_status.published(tags, self.SHA))

    def test_none_where_nothing_names_it(self):
        self.assertIsNone(ci_status.published(["0.0.0-20260923002829-11e8ee09f28d", "latest"], self.SHA))
        self.assertIsNone(ci_status.published([], self.SHA))

    def test_built_carries_its_versions(self):
        seen = {"go": "v0.0.0-20260923200616-1f8f19dd0496", "images": {}, "npm": {}}
        self.assertEqual(ci_status.built_entry(self.SHA, lambda sha: seen),
                         {"commit": self.SHA, "versions": seen})


if __name__ == "__main__":
    unittest.main()
