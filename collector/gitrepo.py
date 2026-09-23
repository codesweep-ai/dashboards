"""Read a project's default branch over plain git, with no token and no API quota.

The clone is blobless and sparse. Every commit is there, which is what counting
how far an internal pin trails needs. Only the files a manifest can live in are
checked out, so the clone fetches those blobs and not a repository's fixtures.
"""

import base64
import os
import subprocess
from datetime import datetime, timezone

# Where a dependency can be declared. Non-cone patterns, gitignore syntax.
SPARSE = [
    "go.mod",
    "go.*.mod",
    "package.json",
    "package-lock.json",
    "/.github/workflows/",
    "action.yml",
    "action.yaml",
    "Containerfile*",
    "Dockerfile*",
    "*.env",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "compose.yml",
    "compose.yaml",
    "/deploy/",
    "/k8s/",
    "/manifests/",
    "/charts/",
    "*.ABOUT",
]


class GitError(Exception):
    pass


_TOKEN = None


def use_token(token):
    """Send the build's own token to github.com: runners share an IP, and anonymous limits with it."""
    global _TOKEN
    _TOKEN = token


def git_env():
    """The environment every git call runs in: no prompt, and the token only for github.com.

    The header goes in through git's config environment, so it never appears
    on a command line another process could read.
    """
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true")
    if _TOKEN:
        basic = base64.b64encode(f"x-access-token:{_TOKEN}".encode()).decode()
        env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                   GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}")
    return env


def _git(args, cwd=None, check=True):
    env = git_env()
    proc = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True)
    if check and proc.returncode != 0:
        lines = (proc.stderr or proc.stdout).strip().splitlines()
        raise GitError(f"git {args[0]}: {lines[-1] if lines else 'failed'}")
    return proc.stdout


class Repo:
    def __init__(self, path, url):
        self.path = path
        self.url = url
        self.branch = None
        self.sha = None
        self.committed = None

    def rev(self, ref):
        """The full SHA `ref` names, or None when it is not in this history."""
        out = _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=self.path, check=False)
        return out.strip() or None

    def commit_time(self, ref):
        out = _git(["show", "-s", "--format=%cI", ref], cwd=self.path, check=False).strip()
        if not out:
            return None
        return datetime.fromisoformat(out).astimezone(timezone.utc)

    def behind(self, ref, paths=(), upto="HEAD"):
        """How many commits `upto` has beyond `ref`: the default branch's head, unless
        another commit is named.

        Returns None when `ref` is not in the history at all. With `paths`, only
        commits touching those paths count, which is what an action pinned by
        commit cares about.
        """
        full = self.rev(ref)
        if not full:
            return None
        args = ["rev-list", "--count", f"{full}..{upto}"]
        if paths:
            args += ["--", *paths]
        return int(_git(args, cwd=self.path).strip() or 0)

    def on_branch(self, ref):
        full = self.rev(ref)
        if not full:
            return False
        proc = subprocess.run(["git", "merge-base", "--is-ancestor", full, "HEAD"],
                              cwd=self.path, capture_output=True)
        return proc.returncode == 0

    def files(self):
        """Every checked-out path, relative to the repository root."""
        out = []
        for root, dirs, names in os.walk(self.path):
            dirs[:] = [d for d in dirs if d != ".git"]
            for n in names:
                out.append(os.path.relpath(os.path.join(root, n), self.path))
        return sorted(out)

    def read(self, rel):
        try:
            with open(os.path.join(self.path, rel), encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None


def full_checkout(repo):
    """Check out every file at HEAD, for a scanner that builds the project."""
    _git(["sparse-checkout", "disable"], cwd=repo.path)


def clone(url, dest, extra_paths=()):
    """Clone `url` into `dest` and check out the manifest files at HEAD."""
    _git(["clone", "--quiet", "--filter=blob:none", "--no-checkout", url, dest])
    patterns = SPARSE + ["/" + p.lstrip("/") for p in extra_paths]
    _git(["sparse-checkout", "set", "--no-cone", *patterns], cwd=dest)
    _git(["checkout", "--quiet"], cwd=dest)
    repo = Repo(dest, url)
    repo.branch = _git(["symbolic-ref", "--short", "HEAD"], cwd=dest, check=False).strip() or None
    repo.sha = _git(["rev-parse", "HEAD"], cwd=dest).strip()
    repo.committed = repo.commit_time("HEAD")
    return repo
