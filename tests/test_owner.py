"""Whose repositories the Makefile and the collector read, and when there is nobody to name.

The Makefile derives the owner from the origin remote. A remote that is not on
GitHub, such as a sandbox's local path, must stop the run and name the
overrides, rather than yield an owner whose clone fails as a credential error.
"""

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from collector import __main__ as collector, gitrepo

ROOT = os.path.join(os.path.dirname(__file__), "..")
MAKEFILE = os.path.abspath(os.path.join(ROOT, "Makefile"))
# Variables the caller's environment would otherwise feed the Makefile: Actions
# sets GITHUB_REPOSITORY, and an enclosing make run passes its own flags down.
SCRUB = ("GITHUB_REPOSITORY", "GITHUB_REPOSITORY_OWNER", "OWNER", "REPOSITORY", "MAKEFLAGS", "MAKELEVEL", "MFLAGS")


@unittest.skipUnless(shutil.which("make") and shutil.which("git"), "needs make and git")
class MakefileOwnerTest(unittest.TestCase):
    def make(self, origin, *args, env=None):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            subprocess.run(["git", "-C", tmp, "remote", "add", "origin", origin], check=True)
            clean = {k: v for k, v in os.environ.items() if k not in SCRUB}
            clean.update(env or {})
            return subprocess.run(["make", "-s", "-f", MAKEFILE, "-C", tmp, *args],
                                  capture_output=True, text=True, env=clean)

    def derived(self, origin, env=None):
        show = 'show: ; @echo "$(OWNER) $(REPOSITORY)"'
        return self.make(origin, "--eval", show, "show", env=env).stdout.strip()

    def test_every_github_form_of_origin_names_the_repository(self):
        # An ssh alias such as github.com-fork is how a workspace of forks picks
        # its key, and it reaches origin verbatim.
        host, alias = "github.com", "github.com-fork"
        for origin in (f"git@{host}:codesweep-ai/dashboards.git",
                       f"git@{alias}:codesweep-ai/dashboards.git",
                       f"https://{host}/codesweep-ai/dashboards",
                       f"https://{host}/codesweep-ai/dashboards.git",
                       f"https://{host}/codesweep-ai/dashboards/",
                       f"ssh://git@{host}/codesweep-ai/dashboards.git",
                       f"ssh://git@{alias}/codesweep-ai/dashboards.git"):
            with self.subTest(origin=origin):
                self.assertEqual(self.derived(origin), "codesweep-ai codesweep-ai/dashboards")

    def test_actions_names_the_repository_whatever_origin_says(self):
        got = self.derived("/run/cs-sandbox-repo-2", env={"GITHUB_REPOSITORY": "someone/dashboards"})
        self.assertEqual(got, "someone someone/dashboards")

    def test_a_local_origin_stops_the_run_and_names_the_overrides(self):
        run = self.make("/run/cs-sandbox-repo-2", "owner")
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("/run/cs-sandbox-repo-2", run.stderr)
        self.assertIn("OWNER=", run.stderr)
        self.assertIn("REPOSITORY=", run.stderr)

    def test_naming_both_overrides_a_local_origin(self):
        run = self.make("/run/cs-sandbox-repo-2", "owner", "OWNER=someone", "REPOSITORY=someone/dashboards")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("someone", run.stdout)

    def test_a_github_origin_passes(self):
        run = self.make("https://github.com/codesweep-ai/dashboards.git", "owner")
        self.assertEqual(run.returncode, 0, run.stderr)


class CollectorOwnerTest(unittest.TestCase):
    def main(self, *argv, env=None):
        clean = {k: v for k, v in os.environ.items() if k not in SCRUB}
        clean.update(env or {})
        err = io.StringIO()
        with mock.patch.dict(os.environ, clean, clear=True), \
                mock.patch.object(gitrepo, "clone", side_effect=AssertionError("cloned")), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop:
            collector.main(["--projects", os.path.join(ROOT, "projects.json"),
                            "--config", os.path.join(ROOT, "deps-config.json"), "--output", "-", *argv])
        return stop.exception.code, err.getvalue()

    def test_an_owner_read_from_a_path_is_refused_before_any_clone(self):
        code, err = self.main("--owner", "/run/cs-sandbox-repo-2")
        self.assertEqual(code, 2)
        self.assertIn("'/run/cs-sandbox-repo-2' is not a GitHub owner", err)
        self.assertIn("--owner", err)

    def test_a_repository_that_is_not_owner_and_name_is_refused(self):
        code, err = self.main("--owner", "someone", env={"GITHUB_REPOSITORY": "/run/cs-sandbox-repo-2"})
        self.assertEqual(code, 2)
        self.assertIn("GITHUB_REPOSITORY", err)


if __name__ == "__main__":
    unittest.main()
