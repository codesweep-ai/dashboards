"""scripts/record-build.sh, run against throwaway repositories.

Every project carries the same file, and SPEC.md describes the store it writes,
so this is where the script is held to that description.
"""

import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SCRIPT = os.path.join(ROOT, "scripts", "record-build.sh")

# The user's own git configuration stays out: a signing key or a hook there
# would change what these repositories are.
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, check=True,
                          capture_output=True, text=True).stdout.strip()


def read(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def tgz(path, name, version):
    """An npm tarball holding only its package.json, as `npm pack` lays one out."""
    data = json.dumps({"name": name, "version": version}).encode()
    with tarfile.open(path, "w:gz") as t:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))


@unittest.skipUnless(shutil.which("git"), "needs git")
class RecordBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = os.path.join(self.tmp, "store")
        self.env = dict(GIT_ENV, CS_BUILDS_DIR=self.store)

    def repo(self, files, owner_dir="workspace", name="demo", origin="https://github.com/acme/demo"):
        """A repository at <tmp>/<owner_dir>/<name> holding files, with the script, committed once."""
        path = os.path.join(self.tmp, owner_dir, name)
        os.makedirs(os.path.join(path, "scripts"))
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(path, rel)), exist_ok=True)
            with open(os.path.join(path, rel), "w") as f:
                f.write(text)
            if rel.endswith(".sh"):
                os.chmod(os.path.join(path, rel), 0o755)
        git(path, "init", "-q", "-b", "main")
        if origin:
            git(path, "remote", "add", "origin", origin)
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "first")
        # Carried, not committed, as a project's own copy would be: the tree stays clean.
        shutil.copy(SCRIPT, os.path.join(path, "scripts", "record-build.sh"))
        with open(os.path.join(path, ".git", "info", "exclude"), "a") as f:
            f.write("scripts/record-build.sh\n")
        return path

    def run_script(self, path, *args, env=None):
        r = subprocess.run([os.path.join(path, "scripts", "record-build.sh"), *args], cwd=path,
                           env=env or self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def gate(self, path, between=None, env=None):
        """start, then whatever happens while the gate runs, then finish."""
        self.run_script(path, "start", env=env)
        if between:
            between()
        return self.run_script(path, "finish", env=env)

    def entries(self, owner, name="demo"):
        d = os.path.join(self.store, owner, "status", name)
        return {f: json.loads(read(os.path.join(d, f))) for f in sorted(os.listdir(d))} if os.path.isdir(d) else {}

    GO_MOD = {"go.mod": "module github.com/acme/demo\n\ngo 1.21\n", "demo.go": "package demo\n"}

    @unittest.skipUnless(shutil.which("go"), "needs go")
    def test_a_clean_pass_records_the_module_and_an_entry(self):
        path = self.repo(self.GO_MOD)
        sha = git(path, "rev-parse", "HEAD")
        out = self.gate(path)
        self.assertIn(f"recorded demo {sha[:7]} as a local build", out)
        (entry,) = self.entries("acme").values()
        version = entry["versions"]["go"]
        self.assertRegex(version, r"^v0\.0\.0-\d{14}-" + sha[:12] + "$")
        self.assertEqual((entry["name"], entry["commit"], entry["gate"], entry["local"]), ("demo", sha, "make ci", True))
        self.assertEqual(entry["versions"]["npm"], {})
        self.assertNotIn("awaits", entry)
        self.assertRegex(entry["committed"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        # The commit time in UTC is the stamp the pseudo-version carries.
        self.assertEqual(re.sub(r"\D", "", entry["committed"]), version.split("-")[1])
        at = os.path.join(self.store, "acme", "goproxy", "github.com", "acme", "demo", "@v", version)
        for ext in (".info", ".mod", ".zip"):
            self.assertTrue(os.path.isfile(at + ext), ext)
        self.assertIn('"Time"', read(at + ".info"))

        # And Go resolves it from there, with the checksum database told to skip it.
        cache = os.path.join(self.tmp, "gomodcache")
        r = subprocess.run(["go", "mod", "download", "-json", f"github.com/acme/demo@{version}"], cwd=self.tmp,
                           env=dict(os.environ, GOWORK="off", GOFLAGS="-modcacherw", GOMODCACHE=cache,
                                    GOTOOLCHAIN="local", GOPROXY="file://" + os.path.join(self.store, "acme", "goproxy"),
                                    GONOSUMDB="github.com/acme/demo"),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["Version"], version)

    @unittest.skipUnless(shutil.which("go"), "needs go")
    def test_the_owner_is_origins_github_owner_else_the_directory_the_repository_sits_in(self):
        for origin, owner in (("https://github.com/acme/demo", "acme"),
                              ("git@github.com:acme/demo.git", "acme"),
                              ("git@github.com-fork:forker/demo.git", "forker"),
                              ("ssh://git@github.com/acme/demo.git", "acme"),
                              ("/run/cs-sandbox-repo-1", "members"),
                              (None, "members")):
            with self.subTest(origin=origin):
                self.store = os.path.join(self.tmp, f"store-{owner}-{len(os.listdir(self.tmp))}")
                self.env["CS_BUILDS_DIR"] = self.store
                path = self.repo(self.GO_MOD, owner_dir=f"members-{len(os.listdir(self.tmp))}/members", origin=origin)
                self.gate(path)
                self.assertEqual(os.listdir(self.store), [owner])

    @unittest.skipUnless(shutil.which("go"), "needs go")
    def test_a_project_that_builds_images_lists_them_as_awaited(self):
        path = self.repo(self.GO_MOD)
        out = self.gate(path, env=dict(self.env, CS_BUILD_IMAGES="demo demo-slim"))
        self.assertIn("siblings take it once its images are built: demo demo-slim", out)
        (entry,) = self.entries("acme").values()
        self.assertEqual(entry["awaits"], ["demo", "demo-slim"])
        self.assertEqual(entry["versions"]["images"], {})

    def test_an_image_name_that_is_no_repository_records_nothing(self):
        path = self.repo(self.GO_MOD)
        out = self.gate(path, env=dict(self.env, CS_BUILD_IMAGES="demo ../x"))
        self.assertIn("not recorded: CS_BUILD_IMAGES names '../x'", out)
        self.assertFalse(os.path.exists(self.store))

    @unittest.skipUnless(shutil.which("go"), "needs go")
    def test_cs_build_store_names_the_store_outright(self):
        named = os.path.join(self.tmp, "member-home", "cs-builds")
        env = dict(self.env, CS_BUILD_STORE=named)
        path = self.repo(self.GO_MOD, origin="/run/cs-sandbox-repo-1")
        sha = git(path, "rev-parse", "HEAD")
        self.gate(path, env=env)
        self.assertTrue(os.path.isfile(os.path.join(named, "status", "demo", sha + ".json")))
        self.assertEqual(self.run_script(path, "store", env=env).strip(), named)
        self.assertFalse(os.path.exists(self.store))

    def store_repo(self):
        """The owner's store as a git repository, as a campaign member's clone is."""
        store = os.path.join(self.store, "acme")
        os.makedirs(os.path.join(store, "status"))
        with open(os.path.join(store, "README"), "w") as f:
            f.write("store\n")
        git(store, "init", "-q", "-b", "main")
        git(store, "add", "-A")
        git(store, "commit", "-q", "-m", "seed")
        return store

    @unittest.skipUnless(shutil.which("go"), "needs go")
    def test_a_store_that_is_a_repository_keeps_each_build_as_a_commit(self):
        store = self.store_repo()
        path = self.repo(self.GO_MOD)
        sha = git(path, "rev-parse", "HEAD")
        self.assertIn("committed it in", self.gate(path))
        self.assertEqual(git(store, "log", "-1", "--format=%s"), f"Record demo {sha[:7]}")
        self.assertEqual(git(store, "status", "--porcelain"), "")
        self.assertIn(f"status/demo/{sha}.json", git(store, "ls-files"))

    def delivered(self, store, name):
        """A build the orchestrator pushed to refs/campaign/orchestrator, on top of what it pushed before."""
        base = subprocess.run(["git", "rev-parse", "-q", "--verify", "refs/campaign/orchestrator"], cwd=store,
                              env=GIT_ENV, capture_output=True, text=True).stdout.strip() or "HEAD"
        git(store, "checkout", "-q", "-b", "elsewhere", base)
        os.makedirs(os.path.join(store, "status", name), exist_ok=True)
        with open(os.path.join(store, "status", name, "c.json"), "w") as f:
            f.write("{}\n")
        git(store, "add", "-A")
        git(store, "commit", "-q", "-m", f"Record {name}")
        git(store, "update-ref", "refs/campaign/orchestrator", "HEAD")
        git(store, "checkout", "-q", "main")
        git(store, "branch", "-q", "-D", "elsewhere")

    def test_the_store_takes_in_what_the_orchestrator_delivered(self):
        store = self.store_repo()
        path = self.repo(self.GO_MOD)
        # Fast forward, where the member has recorded nothing since.
        self.delivered(store, "lint")
        self.run_script(path, "store")
        self.assertTrue(os.path.isfile(os.path.join(store, "status", "lint", "c.json")))
        self.assertEqual(git(store, "log", "-1", "--format=%s"), "Record lint")
        # A merge, where it has builds of its own the orchestrator has not seen.
        with open(os.path.join(store, "status", "own.json"), "w") as f:
            f.write("{}\n")
        git(store, "add", "-A")
        git(store, "commit", "-q", "-m", "Record own")
        self.delivered(store, "ledger")
        self.run_script(path, "store")
        for f in ("lint/c.json", "ledger/c.json", "own.json"):
            self.assertTrue(os.path.isfile(os.path.join(store, "status", f)), f)
        self.assertEqual(len(git(store, "log", "-1", "--format=%P").split()), 2)
        # And a store already holding it is left as it is.
        head = git(store, "rev-parse", "HEAD")
        self.run_script(path, "store")
        self.assertEqual(git(store, "rev-parse", "HEAD"), head)

    @unittest.skipUnless(shutil.which("go"), "needs go")
    def test_a_second_run_on_the_same_commit_does_no_work(self):
        path = self.repo(self.GO_MOD)
        self.gate(path)
        before = self.entries("acme")
        self.assertIn("is already recorded", self.gate(path))
        self.assertEqual(self.entries("acme"), before)

    def test_nothing_is_recorded_from_a_tree_with_changes(self):
        path = self.repo(self.GO_MOD)
        stray = os.path.join(path, "stray.txt")
        open(stray, "w").close()
        self.run_script(path, "start")
        os.remove(stray)
        self.assertIn("not recorded: the tree had changes when the gate started", self.run_script(path, "finish"))
        self.assertFalse(os.path.exists(self.store))

    def test_nothing_is_recorded_when_the_tree_changes_during_the_gate(self):
        path = self.repo(self.GO_MOD)
        out = self.gate(path, between=lambda: open(os.path.join(path, "stray.txt"), "w").close())
        self.assertIn("not recorded: the tree changed while the gate ran", out)
        self.assertFalse(os.path.exists(self.store))

    def test_nothing_is_recorded_when_head_moves_during_the_gate(self):
        path = self.repo(self.GO_MOD)
        out = self.gate(path, between=lambda: git(path, "commit", "-q", "--allow-empty", "-m", "moved"))
        self.assertIn("not recorded: HEAD moved while the gate ran", out)
        self.assertFalse(os.path.exists(self.store))

    def test_a_finish_with_no_start_records_nothing(self):
        path = self.repo(self.GO_MOD)
        self.assertIn("not recorded: the gate did not run record-build.sh start", self.run_script(path, "finish"))

    # A stand-in for ui's pack step: it writes the tarballs the real one would.
    NPM_PACK = """import { copyFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
const here = new URL("..", import.meta.url).pathname;
for (const f of readdirSync(join(here, "packed"))) copyFileSync(join(here, "packed", f), join(process.env.CS_NPMREVS_DATA, f));
"""

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_a_project_with_no_go_mod_records_the_npm_package_named_after_it(self):
        version = "0.3.1-dev.20260925060245.f172c4b"
        path = self.repo({"package.json": json.dumps({"name": "@acme/demo"}),
                          "scripts/npmrevs-registry.mjs": self.NPM_PACK, "packed/.keep": ""})
        tgz(os.path.join(path, "packed", f"acme-demo-{version}.tgz"), "@acme/demo", version)
        tgz(os.path.join(path, "packed", f"acme-demo-linux-x64-{version}.tgz"), "@acme/demo-linux-x64", version)
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "packages")
        self.gate(path)
        (entry,) = self.entries("acme").values()
        self.assertEqual(entry["versions"], {"go": None, "images": {}, "npm": {"@acme/demo": version}})
        self.assertEqual(sorted(os.listdir(os.path.join(self.store, "acme", "npm"))),
                         [f"acme-demo-{version}.tgz", f"acme-demo-linux-x64-{version}.tgz"])

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_a_file_already_in_the_store_is_never_rewritten(self):
        version = "0.3.1-dev.20260925060245.f172c4b"
        path = self.repo({"package.json": json.dumps({"name": "@acme/demo"}),
                          "scripts/npmrevs-registry.mjs": self.NPM_PACK, "packed/.keep": ""})
        tgz(os.path.join(path, "packed", f"acme-demo-{version}.tgz"), "@acme/demo", version)
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "packages")
        there = os.path.join(self.store, "acme", "npm", f"acme-demo-{version}.tgz")
        os.makedirs(os.path.dirname(there))
        with open(there, "wb") as f:
            f.write(b"the first build's bytes")
        self.gate(path)
        self.assertEqual(read(there, "rb"), b"the first build's bytes")
        self.assertEqual(len(self.entries("acme")), 1)

    @unittest.skipUnless(shutil.which("go") and shutil.which("node"), "needs go and node")
    def test_a_go_project_with_npm_packages_records_both(self):
        # lint, ledger and npmrevs: npm/local-registry.sh packs, and needs goreleaser, node and npm.
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        for tool in ("goreleaser", "npm"):
            p = os.path.join(bin_dir, tool)
            with open(p, "w") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        pack = '#!/bin/sh\ncp "$(dirname "$0")"/../packed/*.tgz "$CS_NPMREVS_DATA"/\n'
        path = self.repo(dict(self.GO_MOD, **{"npm/local-registry.sh": pack, "packed/.keep": ""}))
        sha12 = git(path, "rev-parse", "HEAD")[:12]
        # The wrapper's version is the module's pseudo-version, less its v: build.mjs reads it out of the binary.
        stamp = subprocess.run(["git", "log", "-1", "--format=%cd", "--date=format-local:%Y%m%d%H%M%S"], cwd=path,
                               env=dict(GIT_ENV, TZ="UTC"), capture_output=True, text=True).stdout.strip()
        version = f"0.0.0-{stamp}-{sha12}"
        tgz(os.path.join(path, "packed", f"acme-demo-{version}.tgz"), "@acme/demo", version)
        # Packed files sit outside the commit, as a real pack's output does, so the tree stays clean.
        with open(os.path.join(path, ".git", "info", "exclude"), "a") as f:
            f.write("packed/*.tgz\n")
        env = dict(self.env, PATH=bin_dir + os.pathsep + os.environ["PATH"])
        self.gate(path, env=env)
        (entry,) = self.entries("acme").values()
        self.assertEqual(entry["versions"]["go"], "v" + version)
        self.assertEqual(entry["versions"]["npm"], {"@acme/demo": version})


if __name__ == "__main__":
    unittest.main()
