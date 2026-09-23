"""Which commit a status file names as built, for a sibling's `make repin` to pin.

It has to be a commit CI built and passed on the branch, and still on it. A
commit that changed nothing CI builds, a failed build and a build still going
are all passed over for the one before, so a pin never lands on a commit nobody
knows to be good.
"""

import importlib.machinery
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
    def test_newest_passing_push_build(self):
        runs = [run("c"), run("b"), run("a")]
        self.assertEqual(ci_status.built(runs, everywhere), "c" * 40)

    def test_passes_over_what_did_not_pass(self):
        runs = [run("e", conclusion=None), run("d", conclusion="failure"),
                run("c", conclusion="cancelled"), run("b"), run("a")]
        self.assertEqual(ci_status.built(runs, everywhere), "b" * 40)

    def test_only_the_gate_and_only_a_push(self):
        runs = [run("d", name="pages"), run("c", event="pull_request"),
                run("b", event="workflow_dispatch"), run("a")]
        self.assertEqual(ci_status.built(runs, everywhere), "a" * 40)

    def test_passes_over_a_commit_the_branch_lost(self):
        runs = [run("b"), run("a")]
        self.assertEqual(ci_status.built(runs, lambda sha: sha != "b" * 40), "a" * 40)

    def test_waits_for_the_images_of_a_project_that_publishes_them(self):
        # c's images are still being published, b's failed, a's are there.
        runs = [run("c", name="publish images", event="workflow_run", conclusion=None), run("c"),
                run("b", name="publish images", event="workflow_run", conclusion="failure"), run("b"),
                run("a", name="publish images", event="workflow_run"), run("a")]
        self.assertEqual(ci_status.built(runs, everywhere, images=True), "a" * 40)
        # A project that publishes none needs none.
        self.assertEqual(ci_status.built(runs, everywhere), "c" * 40)

    def test_a_skipped_image_run_publishes_nothing(self):
        runs = [run("b", name="publish images", event="workflow_run", conclusion="skipped"), run("b"),
                run("a", name="publish images", event="workflow_run", conclusion="skipped"),
                run("a", name="publish images", event="workflow_run"), run("a")]
        self.assertEqual(ci_status.built(runs, everywhere, images=True), "a" * 40)

    def test_none_when_nothing_passed(self):
        self.assertIsNone(ci_status.built([run("a", conclusion="failure")], everywhere))
        self.assertIsNone(ci_status.built([], everywhere))


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
        self.assertIsNone(ci_status.built_entry(None, lambda sha: seen))


if __name__ == "__main__":
    unittest.main()
