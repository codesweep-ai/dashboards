"""scripts/repin-go.sh, run against throwaway modules, stores and status sites.

A throwaway tool module is built at two commits. Each build is recorded with
scripts/record-build.sh into one of two stores: one stands in for what the
module proxy serves, the other is the local build store. A status file on a
file:// site names the build CI made. Nothing reaches the network: the
checksum database is off, and the proxy is the stand-in one.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SCRIPTS = os.path.join(ROOT, "scripts")
TOOL = "github.com/codesweep-ai/demotool"

GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")


def git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=cwd, env=env or GIT_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def carry_scripts(repo, *names):
    """The scripts, beside the repository's files but outside its commits."""
    for n in names:
        shutil.copy(os.path.join(SCRIPTS, n), os.path.join(repo, "scripts", n))
    with open(os.path.join(repo, ".git", "info", "exclude"), "a") as f:
        f.write("".join(f"scripts/{n}\n" for n in names))


@unittest.skipUnless(shutil.which("git") and shutil.which("go") and shutil.which("curl"), "needs git, go and curl")
class RepinGo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.go = dict(GIT_ENV, GOWORK="off", GOFLAGS="-modcacherw", GOTOOLCHAIN="local", GOSUMDB="off",
                       GOMODCACHE=os.path.join(self.tmp, "modcache"))
        # The tool, at two commits a second apart in commit time.
        self.tool = os.path.join(self.tmp, "src", "demotool")
        write(os.path.join(self.tool, "go.mod"), f"module {TOOL}\n\ngo 1.21\n")
        write(os.path.join(self.tool, "cmd", "demotool", "main.go"), "package main\n\nfunc main() {}\n")
        os.makedirs(os.path.join(self.tool, "scripts"))
        git(self.tool, "init", "-q", "-b", "main")
        git(self.tool, "remote", "add", "origin", f"https://{TOOL}")
        git(self.tool, "add", "-A")
        self.old = self.commit("old", "2026-09-24T10:00:00Z")
        self.new = self.commit("new", "2026-09-25T10:00:00Z")
        carry_scripts(self.tool, "record-build.sh")

    def commit(self, message, when):
        env = dict(GIT_ENV, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        git(self.tool, "commit", "-q", "--allow-empty", "-m", message, env=env)
        return git(self.tool, "rev-parse", "HEAD")

    def record(self, sha, store, images=None):
        """The build of `sha`, recorded into `store`; its Go version."""
        git(self.tool, "checkout", "-q", sha)
        env = dict(self.go, CS_BUILDS_DIR=store)
        if images:
            env["CS_BUILD_IMAGES"] = images
        for step in ("start", "finish"):
            subprocess.run([os.path.join(self.tool, "scripts", "record-build.sh"), step], cwd=self.tool, env=env,
                           check=True, capture_output=True, text=True)
        with open(os.path.join(store, "codesweep-ai", "status", "demotool", sha + ".json")) as f:
            return json.load(f)["versions"]["go"]

    def image(self, store, sha, name):
        """The file an image build of `sha` leaves in the store."""
        write(os.path.join(store, "codesweep-ai", "images", "demotool", sha, name + ".json"),
              json.dumps({"schema": 1, "name": "demotool", "commit": sha, "image": name}))

    def site(self, sha=None, version=None):
        """A status site whose file lists `sha` as CI's last build, or none."""
        site = os.path.join(self.tmp, "site")
        built = [{"commit": sha, "versions": {"go": version, "images": {}, "npm": {}}}] if sha else []
        write(os.path.join(site, "demotool", "ci-status.json"), json.dumps({"schema": 2, "built": built}, indent=1))
        return "file://" + site

    def consumer(self, public, pinned):
        """A repository pinning the tool at `pinned`, from the stand-in proxy."""
        repo = os.path.join(self.tmp, "ws", "consumer")
        os.makedirs(os.path.join(repo, "scripts"))
        write(os.path.join(repo, "go.mod"), "module github.com/codesweep-ai/consumer\n\ngo 1.21\n")
        env = dict(self.go, GOPROXY="file://" + os.path.join(public, "codesweep-ai", "goproxy"))
        subprocess.run(["go", "get", "-tool", f"{TOOL}/cmd/demotool@{pinned}"], cwd=repo, env=env, check=True,
                       capture_output=True, text=True)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "remote", "add", "origin", "https://github.com/codesweep-ai/consumer")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "pin")
        carry_scripts(repo, "record-build.sh", "repin-go.sh")
        return repo

    def repin(self, repo, public, store, site, local=None):
        env = dict(self.go, CS_BUILDS_DIR=store, CS_STATUS_SITE=site,
                   GOPROXY="file://" + os.path.join(public, "codesweep-ai", "goproxy"))
        if local is not None:
            env["LOCAL"] = local
        r = subprocess.run([os.path.join(repo, "scripts", "repin-go.sh")], cwd=repo, env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def pinned(self, repo):
        """The version go.mod requires the tool at, in a block or on a line of its own."""
        with open(os.path.join(repo, "go.mod")) as f:
            for line in f:
                words = line.split()
                if words[:1] == ["require"]:
                    words = words[1:]
                if words[:1] == [TOOL]:
                    return words[1]
        self.fail("go.mod requires no " + TOOL)

    def stores(self):
        return os.path.join(self.tmp, "public"), os.path.join(self.tmp, "store")

    def test_a_local_build_newer_than_the_ci_build_is_pinned_and_named(self):
        public, store = self.stores()
        old = self.record(self.old, public)
        new = self.record(self.new, store)
        repo = self.consumer(public, old)
        out = self.repin(repo, public, store, self.site(self.old, old))
        self.assertIn(f"demotool: {self.new[:7]}, a local build recorded", out)
        self.assertEqual(self.pinned(repo), new)

    def test_local_0_takes_the_ci_build_only(self):
        public, store = self.stores()
        old = self.record(self.old, public)
        self.record(self.new, store)
        repo = self.consumer(public, old)
        out = self.repin(repo, public, store, self.site(self.old, old), local="0")
        self.assertIn(f"demotool: {self.old[:7]}, the last commit its CI built", out)
        self.assertEqual(self.pinned(repo), old)

    def test_a_ci_build_newer_than_the_newest_local_one_wins(self):
        public, store = self.stores()
        old = self.record(self.old, public)
        new = self.record(self.new, public)
        self.record(self.old, store)
        repo = self.consumer(public, old)
        out = self.repin(repo, public, store, self.site(self.new, new))
        self.assertIn(f"demotool: {self.new[:7]}, the last commit its CI built", out)
        self.assertEqual(self.pinned(repo), new)

    def test_a_tie_goes_to_the_ci_build(self):
        public, store = self.stores()
        old = self.record(self.old, public)
        new = self.record(self.new, public)
        self.record(self.new, store)
        repo = self.consumer(public, old)
        out = self.repin(repo, public, store, self.site(self.new, new))
        self.assertIn("the last commit its CI built", out)
        self.assertNotIn("a local build", out)

    def test_a_local_build_waits_for_the_images_its_entry_awaits(self):
        public, store = self.stores()
        old = self.record(self.old, public)
        new = self.record(self.new, store, images="demotool demotool-slim")
        repo = self.consumer(public, old)
        site = self.site(self.old, old)
        out = self.repin(repo, public, store, site)
        self.assertIn(f"demotool: {self.old[:7]}, the last commit its CI built", out)
        self.assertIn(f"demotool: {self.new[:7]}, a newer local build, waits for its images: demotool, demotool-slim", out)
        self.assertEqual(self.pinned(repo), old)

        self.image(store, self.new, "demotool")
        self.assertIn("waits for its images: demotool-slim", self.repin(repo, public, store, site))
        self.assertEqual(self.pinned(repo), old)

        self.image(store, self.new, "demotool-slim")
        out = self.repin(repo, public, store, site)
        self.assertIn(f"demotool: {self.new[:7]}, a local build recorded", out)
        self.assertNotIn("waits", out)
        self.assertEqual(self.pinned(repo), new)

    def test_with_neither_build_the_pin_is_held(self):
        public, store = self.stores()
        old = self.record(self.old, public)
        repo = self.consumer(public, old)
        out = self.repin(repo, public, store, self.site())
        self.assertIn("demotool: held, as neither its status file nor the build store lists a build", out)
        self.assertEqual(self.pinned(repo), old)


if __name__ == "__main__":
    unittest.main()
