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

    def test_none_when_nothing_passed(self):
        self.assertIsNone(ci_status.built([run("a", conclusion="failure")], everywhere))
        self.assertIsNone(ci_status.built([], everywhere))


if __name__ == "__main__":
    unittest.main()
