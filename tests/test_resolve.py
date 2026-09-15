import unittest
from datetime import datetime, timezone

from collector import resolve
from collector.http import FetchError

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)


class FakeSources:
    """Answers the resolver's questions from fixed data, and counts them."""

    def __init__(self, **answers):
        self.answers = answers

    def __getattr__(self, name):
        def call(*args):
            value = self.answers.get(name)
            if isinstance(value, Exception):
                raise value
            return value(*args) if callable(value) else value
        return call


class FakeRepo:
    def __init__(self, behind, touching=None, committed=NOW, pinned_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                 url="https://github.com/codesweep-ai/ledger"):
        self._behind, self._touching = behind, touching
        self.committed, self._pinned_at = committed, pinned_at
        self.sha, self.branch, self.url = "d687ad5b27750000", "main", url

    def behind(self, ref, paths=()):
        return self._touching if paths else self._behind

    def commit_time(self, ref):
        return self._pinned_at

    def on_branch(self, ref):
        return True


def resolver(sources=None, repos=None):
    return resolve.Resolver(sources or FakeSources(), "codesweep-ai", repos or {}, NOW)


def dep(**kw):
    d = {"ecosystem": "go", "name": "x", "version": "v1.0.0", "scope": "direct", "internal": False,
         "sources": [{"path": "go.mod", "line": 1}]}
    d.update(kw)
    return d


class Classify(unittest.TestCase):
    """The rules SPEC.md states, one at a time, in the order it states them."""

    def verdict(self, **kw):
        d = resolver().classify(dep(**kw))
        return d["status"], d["level"]

    def test_a_high_advisory_that_ships_is_critical(self):
        self.assertEqual(self.verdict(vulnerabilities=[{"id": "GHSA-1", "severity": "high"}]), ("vulnerable", "critical"))

    def test_the_same_advisory_in_a_dev_dependency_is_serious(self):
        self.assertEqual(self.verdict(scope="dev", vulnerabilities=[{"id": "GHSA-1", "severity": "critical"}]),
                         ("vulnerable", "serious"))

    def test_end_of_life(self):
        self.assertEqual(self.verdict(lifecycle={"phase": "eol"}), ("eol", "critical"))
        self.assertEqual(self.verdict(scope="dev", lifecycle={"phase": "eol"}), ("eol", "serious"))
        self.assertEqual(self.verdict(lifecycle={"phase": "eol-soon"}), ("eol-soon", "serious"))

    def test_an_engines_floor_on_an_ended_release_is_a_warning(self):
        self.assertEqual(self.verdict(scope="engines", lifecycle={"phase": "eol"}), ("eol", "warning"))

    def test_internal_lag_by_age(self):
        self.assertEqual(self.verdict(lag={"commits": 3, "days": 2}), ("behind", "info"))
        self.assertEqual(self.verdict(lag={"commits": 3, "days": 20}), ("behind", "warning"))
        self.assertEqual(self.verdict(lag={"commits": 3, "days": 90}), ("behind", "serious"))

    def test_an_action_pin_is_behind_only_when_its_directory_moved(self):
        self.assertEqual(self.verdict(lag={"commits": 8, "commits_touching": 0, "days": 1}), ("current", "good"))
        self.assertEqual(self.verdict(lag={"commits": 8, "commits_touching": 1, "days": 1}), ("behind", "info"))

    def test_release_age_escalates_a_bump(self):
        fresh = {"latest": "v2.0.0", "latest_date": "2026-09-01T00:00:00Z"}
        stale = {"latest": "v2.0.0", "latest_date": "2025-06-01T00:00:00Z"}
        self.assertEqual(self.verdict(behind="major", upstream=fresh), ("major", "warning"))
        self.assertEqual(self.verdict(behind="major", upstream=stale), ("major", "serious"))
        self.assertEqual(self.verdict(behind="patch", upstream=fresh), ("patch", "info"))
        self.assertEqual(self.verdict(behind="patch", upstream=stale), ("patch", "warning"))

    def test_an_indirect_record_needs_attention_only_when_vulnerable(self):
        self.assertEqual(self.verdict(scope="indirect", behind="major", upstream={"latest": "v2.0.0"}), ("untracked", "idle"))
        self.assertEqual(self.verdict(scope="transitive", vulnerabilities=[{"id": "x", "severity": "high"}])[0], "vulnerable")

    def test_current_floating_and_unknown(self):
        self.assertEqual(self.verdict(upstream={"latest": "v1.0.0"}), ("current", "good"))
        self.assertEqual(self.verdict(version=None, floating=True), ("floating", "idle"))
        self.assertEqual(self.verdict(error="HTTP 503 (proxy.golang.org)"), ("unknown", "idle"))

    def test_the_fix_is_the_release_that_clears_every_advisory(self):
        d = resolver().classify(dep(vulnerabilities=[{"id": "a", "severity": "low", "fixed": "1.2.0"},
                                                     {"id": "b", "severity": "low", "fixed": "1.10.0"}]))
        self.assertEqual(d["fix"], "1.10.0")


class Resolve(unittest.TestCase):
    def test_go_module(self):
        src = FakeSources(go_latest={"version": "v1.2.0", "time": "2026-06-01T00:00:00Z"},
                          go_time="2025-06-01T00:00:00Z", go_next_major=None)
        d = resolver(src).resolve(dep(package="github.com/a/b", datasource="goproxy"))
        self.assertEqual((d["behind"], d["upstream"]["latest"], d["libyears"]), ("minor", "v1.2.0", 1.0))
        self.assertEqual(d["upstream_repo"], "github.com/a/b")

    def test_a_registry_failure_marks_the_record_rather_than_guessing(self):
        src = FakeSources(go_latest=FetchError("https://proxy.golang.org/x/@latest", "HTTP 503", 503))
        d = resolver(src).resolve(dep(package="x", datasource="goproxy"))
        self.assertIn("HTTP 503", d["error"])
        self.assertNotIn("behind", d)

    def test_a_floating_action_tag_resolves_to_its_newest_release(self):
        releases = [{"tag": t, "published": p, "prerelease": False, "url": ""} for t, p in
                    [("v7.0.1", "2026-07-20T00:00:00Z"), ("v4.3.0", "2025-10-01T00:00:00Z"), ("v4.2.0", "2025-01-01T00:00:00Z")]]
        src = FakeSources(github_releases=releases, github_tags={})
        d = resolver(src).resolve(dep(ecosystem="actions", name="actions/cache", version="v4", scope="ci",
                                      datasource="github", package="actions/cache", floating=True))
        self.assertEqual(d["upstream"]["effective"], "4.3.0")
        self.assertEqual(d["behind"], "major")

    def test_an_internal_pseudo_version_counts_commits(self):
        repos = {"ledger": FakeRepo(behind=8)}
        d = resolver(repos=repos).resolve(dep(name="github.com/codesweep-ai/ledger", package="github.com/codesweep-ai/ledger",
                                              version="v0.0.0-20260901000000-bda511aea589", internal=True,
                                              scope="tool", datasource="goproxy"))
        self.assertEqual((d["provider"], d["lag"]["commits"], d["lag"]["pinned"]), ("ledger", 8, "bda511aea589"))
        self.assertGreater(d["lag"]["days"], 10)

    def test_a_fork_compares_a_sibling_pin_in_its_own_clone(self):
        # A fork keeps the org's module path, so the pin is still internal. Its lag
        # and its compare link come from the clone the run read, which is the fork's.
        repos = {"ledger": FakeRepo(behind=2, url="https://github.com/alice/ledger")}
        d = resolver(repos=repos).resolve(dep(name="github.com/codesweep-ai/ledger", package="github.com/codesweep-ai/ledger",
                                              version="v0.0.0-20260901000000-bda511aea589", internal=True,
                                              scope="tool", datasource="goproxy"))
        self.assertEqual(d["upstream"]["url"], "https://github.com/alice/ledger/compare/bda511aea589...main")

    def test_an_internal_image_links_to_the_namespace_it_is_named_in(self):
        tags = ["v0.0.0-20260910000000-aaaaaaaaaaaa", "v0.0.0-20260901000000-bbbbbbbbbbbb"]
        r = resolve.Resolver(FakeSources(oci_tags=tags), "alice", {}, NOW)
        d = r.resolve(dep(ecosystem="image", name="ghcr.io/codesweep-ai/sandbox-agents", package="ghcr.io/codesweep-ai/sandbox-agents",
                          version="v0.0.0-20260901000000-bbbbbbbbbbbb", datasource="oci", scope="build", internal=True))
        self.assertEqual(d["upstream"]["url"], "https://github.com/codesweep-ai/sandbox/pkgs/container/sandbox-agents")
        self.assertEqual(d["lag"]["builds"], 1)

    def test_an_internal_action_counts_commits_in_its_directory(self):
        repos = {"dashboards": FakeRepo(behind=8, touching=1)}
        d = resolver(repos=repos).resolve(dep(ecosystem="actions", name="codesweep-ai/dashboards/action",
                                              version="4c204c69b8b2b79a101c65520b86944de06942a0", subpath="action",
                                              pinned_sha=True, internal=True, datasource="github",
                                              package="codesweep-ai/dashboards", scope="ci"))
        self.assertEqual((d["lag"]["commits"], d["lag"]["commits_touching"]), (8, 1))

    def test_an_os_image_takes_its_release_cycle(self):
        cycles = [{"cycle": "13", "releaseDate": "2025-08-09", "eol": "2030-06-30", "support": "2028-08-09"},
                  {"cycle": "12", "releaseDate": "2023-06-10", "eol": "2028-06-30", "support": "2026-07-11"}]
        d = resolver(FakeSources(cycles=cycles)).resolve(dep(ecosystem="image", name="gcr.io/distroless/static-debian12",
                                                             version="nonroot", datasource="oci", scope="deploy",
                                                             package="gcr.io/distroless/static-debian12"))
        self.assertEqual((d["lifecycle"]["cycle"], d["lifecycle"]["phase"], d["behind"]), ("12", "maintenance", "major"))


RUNNERS = {
    "macos-latest": {"image": "macOS 26 Arm64", "os": "macos", "version": "26", "arch": "arm64", "deprecated": False, "preview": False},
    "macos-14": {"image": "macOS 14 Arm64", "os": "macos", "version": "14", "arch": "arm64", "deprecated": True, "preview": False},
}
MACOS = [{"cycle": "26", "releaseDate": "2025-09-15", "eol": False}, {"cycle": "14", "releaseDate": "2023-09-26", "eol": False}]


class Runners(unittest.TestCase):
    def runner(self, label, floating):
        src = FakeSources(runner_images=RUNNERS, cycles=MACOS)
        return resolver(src).classify(resolver(src).resolve(dep(ecosystem="image", name="runner/macos", version=label,
                                                                scope="ci", datasource="runner", os="macos", floating=floating)))

    def test_a_latest_label_resolves_to_the_image_github_names(self):
        d = self.runner("macos-latest", True)
        self.assertEqual((d["label"], d["lifecycle"]["cycle"], d["status"]), ("macOS 26 Arm64 (macos-latest)", "26", "current"))

    def test_a_deprecated_image(self):
        d = self.runner("macos-14", False)
        self.assertEqual(d["status"], "major")
        self.assertIn("deprecated", d["deprecated"])

    def test_a_self_hosted_runner_is_named_not_guessed(self):
        d = resolver().resolve(dep(ecosystem="image", name="runner/self-hosted", version="macOS ARM64", scope="ci",
                                   datasource="runner", self_hosted=True))
        self.assertIn("self-hosted", d["error"])


POLICY = {"releases": {"1.16": {"release": "2026-06-03", "latest": "1.16.2", "min_support": "2026-12-03", "eol": None}},
          "guest_kernels": {"6.1": {"min_firecracker": "1.9.0", "min_support": "2026-09-02"},
                            "6.18": {"min_firecracker": "1.16.1", "min_support": "2028-06-01"}},
          "url": "u", "kernel_url": "k"}


class Firecracker(unittest.TestCase):
    def test_a_support_floor_close_to_today_is_eol_soon(self):
        releases = [{"tag": "v1.17.0", "published": "2026-09-10T00:00:00Z", "prerelease": False, "url": ""},
                    {"tag": "v1.16.0", "published": "2026-06-03T00:00:00Z", "prerelease": False, "url": ""}]
        src = FakeSources(github_releases=releases, github_tags={}, firecracker_policy=POLICY)
        d = resolver(src).resolve(dep(ecosystem="native", name="github.com/firecracker-microvm/firecracker", version="1.16.0",
                                      datasource="github", package="firecracker-microvm/firecracker", scope="build"))
        self.assertEqual((d["lifecycle"]["phase"], d["lifecycle"]["eol"], d["lifecycle"]["eol_is_floor"]), ("eol-soon", "2026-12-03", True))

    LINUX = [{"cycle": "7.2", "releaseDate": "2026-08-16", "eol": False, "latest": "7.2.5"},
             {"cycle": "7.1", "releaseDate": "2026-06-14", "eol": "2026-09-02"},
             {"cycle": "6.19", "releaseDate": "2026-02-08", "eol": "2026-04-22"},
             {"cycle": "6.18", "releaseDate": "2025-11-30", "eol": "2028-12-31", "lts": True, "latest": "6.18.51"},
             {"cycle": "6.1", "releaseDate": "2022-12-11", "eol": "2027-12-31", "lts": True, "latest": "6.1.187"}]

    def kernel(self, fedora_latest="7.2.4-200.fc44"):
        src = FakeSources(cycles=self.LINUX, bodhi_latest=(fedora_latest, "2026-09-10 00:58:54"), firecracker_policy=POLICY)
        return resolver(src).resolve(dep(ecosystem="native", name="kernel", label="Linux kernel (Fedora guest)",
                                         version="6.19.10-300.fc44", datasource="fedora-kernel", scope="build",
                                         compat="firecracker-guest"))

    def test_the_guest_kernel_moves_to_the_long_term_line_firecracker_validates(self):
        c = self.kernel()["compat"]
        self.assertEqual((c["ok"], c["validated"], c["target"]["line"], c["target"]["lts"], c["target"]["min_firecracker"]),
                         (False, ["6.1", "6.18"], "6.18", True, "1.16.1"))
        # A date in Firecracker's table is a floor: 6.1 is past it and still validated.
        self.assertIn("6.1", c["validated"])
        self.assertEqual(c["newer"], {"ended": ["6.19", "7.1"], "not_validated": ["7.2"]})
        self.assertEqual(c["distribution"], {"name": "Fedora 44", "latest": "7.2.4-200.fc44", "line": "7.2", "has_target": False})

    def test_the_move_names_its_firecracker_upgrade_and_the_choices_fedora_leaves(self):
        from collector import actions
        kernel = self.kernel()
        fc = dep(ecosystem="native", name="github.com/firecracker-microvm/firecracker", package="firecracker-microvm/firecracker",
                 label="Firecracker", version="1.16.0", status="eol-soon", level="serious", sources=[{"path": "build.go", "line": 73}],
                 upstream={"latest": "1.17.0"}, lifecycle={"product": "firecracker", "cycle": "1.16", "eol": "2026-12-03", "eol_is_floor": True})
        kernel["sources"] = [{"path": "build.go", "line": 28}]
        project = {"name": "sandbox", "repo": {"url": "u"}, "dependencies": [resolver().classify(kernel), fc]}
        resolver().link_compat([project])
        self.assertEqual(kernel["compat"]["firecracker"], {"name": fc["name"], "pinned": "1.16.0", "needs": "1.16.1", "ok": False})
        cards = {a["kind"] + ":" + a["title"]: a for a in actions.build([project])}
        move = next(a for k, a in cards.items() if "kernel" in a["title"])
        upgrade = next(a for k, a in cards.items() if "Firecracker" in a["title"])
        self.assertEqual(move["why"], "Firecracker validates 6.1, 6.18 · 6.18 is LTS to 2028-12-31 · Fedora 44 ships only 7.2")
        self.assertEqual(move["requires"], [upgrade["key"]])
        self.assertEqual(move["how"], "Upgrade Firecracker to 1.16.1+ (pinned 1.16.0)")
        self.assertTrue(move["options"][0]["recommended"])
        self.assertIn("microvm-kernel-6.18", move["options"][0]["text"])
        self.assertEqual(upgrade["evidence"][-1], "the 6.18 guest kernel needs 1.16.1+")
        project["_actions"] = actions.build([project])
        doc = actions.agent_document({"generated": "2026-09-13T00:00:00Z", "org": "o", "projects": [project]}, "https://x/d/", "spec")
        ids = [a["id"] for a in doc["projects"][0]["actions"]]
        kernel_act = next(a for a in doc["projects"][0]["actions"] if "kernel" in a["title"])
        self.assertLess(ids.index(kernel_act["requires"][0]), ids.index(kernel_act["id"]))

    def test_a_fedora_that_maintains_the_line_gets_an_edit_and_no_options(self):
        from collector import actions
        kernel = self.kernel(fedora_latest="6.18.51-200.fc44")
        self.assertTrue(kernel["compat"]["distribution"]["has_target"])
        steps = actions.steps_for(kernel)
        self.assertEqual((steps[-1]["text"], steps[-1]["to"]), ("set the version to 6.18.51-200.fc44", "6.18.51-200.fc44"))
        self.assertIsNone(actions.compat_options(kernel["compat"]))


class Enrichment(unittest.TestCase):
    def test_a_package_endoflife_date_knows_by_purl_takes_its_release_line(self):
        src = FakeSources(eol_products_by_purl={"pkg:npm/react": "react"},
                          cycles=[{"cycle": "19", "releaseDate": "2024-12-05", "eol": False},
                                  {"cycle": "18", "releaseDate": "2022-03-29", "eol": False, "support": "2024-12-05"}])
        project = {"dependencies": [dep(ecosystem="npm", name="react", version="18.3.1", datasource="npm", package="react")]}
        resolver(src).enrich_lifecycles([project])
        lc = project["dependencies"][0]["lifecycle"]
        self.assertEqual((lc["product"], lc["cycle"], lc["phase"]), ("react", "18", "maintenance"))

    def test_a_fedora_package_resolves_and_names_its_source_and_license(self):
        src = FakeSources(fedora_package={"version": "3.5.8-1.fc44", "repo": "updates", "co_packages": ["openssl", "openssl-libs"],
                                          "nvra": "openssl-devel-3.5.8-1.fc44.x86_64"},
                          koji_source="openssl", fedora_license=lambda release, cands: (cands[0], "Apache-2.0"))
        r = resolver(src)
        d = r.resolve(dep(ecosystem="package", name="openssl-devel", version=None, datasource="rpm",
                          release="44", floating=True, scope="build"))
        self.assertEqual(d["upstream"]["effective"], "3.5.8-1.fc44")
        r.enrich_fedora_licenses([{"dependencies": [d]}])
        self.assertEqual((d["source_package"], d["licenses"]), ("openssl", ["Apache-2.0"]))

    def test_a_conditional_license_macro_is_left_out(self):
        from collector.sources import Sources

        class Http:
            def text(self, url, accept_missing=False):
                return "Name: cmake\nLicense:        BSD-3-Clause AND Zlib%{?with_bundled_cppdap: AND Apache-2.0}\n"
        self.assertEqual(Sources(Http()).fedora_license("44", ["cmake"]), ("cmake", "BSD-3-Clause AND Zlib"))


class Advisories(unittest.TestCase):
    def test_a_go_id_and_the_ghsa_it_aliases_are_one_issue(self):
        recs = [
            {"id": "GO-2026-1", "aliases": ["GHSA-aaaa", "CVE-2026-1"], "summary": "hole",
             "affected": [{"package": {"name": "golang.org/x/mod", "ecosystem": "Go"},
                           "ranges": [{"events": [{"introduced": "0"}, {"fixed": "0.40.0"}]}]}]},
            {"id": "GHSA-aaaa", "aliases": ["GO-2026-1"], "database_specific": {"severity": "HIGH"}},
        ]
        found = resolve._merge_advisories(recs, ("Go", "golang.org/x/mod", "v0.39.0"))
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["id"], found[0]["severity"], found[0]["fixed"]), ("GHSA-aaaa", "high", "0.40.0"))

    def test_cvss_base_score(self):
        self.assertEqual(resolve.cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"), 9.8)
        self.assertEqual(resolve.cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N"), 6.1)
        self.assertIsNone(resolve.cvss3_score("not a vector"))


class Snapshot(unittest.TestCase):
    def test_security_updates_since_the_image_was_built_reach_the_packages_it_installs(self):
        class Repo:
            def read(self, path):
                return "AGENTS_REF=ghcr.io/codesweep-ai/sandbox-agents:v0.0.0-20260907215745-bea6812a16c7\n"
        pkgs = [{"ecosystem": "package", "name": n, "release": "44", "scope": "build",
                 "sources": [{"path": "image/Containerfile.base", "line": 5}]}
                for n in ("curl", "openssl-devel", "git-delta")]
        updates = [
            {"id": "FEDORA-1", "severity": "high", "pushed": "2026-09-11 01:00:19", "builds": ["curl-8.18.0-10.fc44"], "url": "u", "summary": ""},
            {"id": "FEDORA-2", "severity": "medium", "pushed": "2026-09-09 01:00:19", "builds": ["openssl-3.5.8-1.fc44"], "url": "u", "summary": ""},
            {"id": "FEDORA-3", "severity": "low", "pushed": "2026-09-09 01:00:19", "builds": ["git-2.51.0-1.fc44"], "url": "u", "summary": ""},
        ]
        project = {"dependencies": pkgs}
        r = resolver(FakeSources(bodhi_security_since=updates))
        r.snapshot(project, Repo(), {"packages": "image/Containerfile.base",
                                     "built": {"path": "image/tiers.env", "match": r"^AGENTS_REF=\S+:v0\.0\.0-(\d{14})-"}})
        names = {d["name"]: [v["id"] for v in d.get("vulnerabilities", [])] for d in pkgs}
        self.assertEqual(names["curl"], ["FEDORA-1"])
        self.assertEqual(names["openssl-devel"], ["FEDORA-2"])
        self.assertEqual(names["git-delta"], [], "git-delta is its own source package, not a git subpackage")
        self.assertEqual((project["snapshot"]["updates"], project["snapshot"]["matched"]), (3, 2))


if __name__ == "__main__":
    unittest.main()


class Policy(unittest.TestCase):
    POLICY = {"allow": ["MIT", "Apache-2.0", "BSD-*"], "review": ["MPL-*"], "deny": ["GPL-*"],
              "aggregate": ["package", "image", "native", "runtime"]}

    def verdict(self, **kw):
        from collector import policy
        return policy.license_verdict(dep(**kw), self.POLICY)

    def test_an_expression_takes_its_best_choice_and_its_worst_requirement(self):
        from collector import policy
        classify = lambda i: {"MIT": "allowed", "GPL-2.0-only": "denied", "MPL-1.1": "review"}.get(i, "unknown")
        self.assertEqual(policy.evaluate("MIT OR GPL-2.0-only", classify), "allowed")
        self.assertEqual(policy.evaluate("MIT AND GPL-2.0-only", classify), "denied")
        self.assertEqual(policy.evaluate("(MPL-1.1 OR GPL-2.0-only) AND MIT", classify), "review")

    def test_an_exception_the_policy_names_grades_the_pair(self):
        rules = dict(self.POLICY, review=["MPL-*", "GPL-2.0-only WITH Classpath-exception-2.0"])
        from collector import policy
        graded = lambda expr: policy.license_verdict(dep(licenses=[expr]), rules)["verdict"]
        self.assertEqual(graded("GPL-2.0-only WITH Classpath-exception-2.0"), "review")
        self.assertEqual(graded("GPL-2.0-only WITH GCC-exception-2.0"), "denied")
        self.assertEqual(graded("Apache-2.0 WITH LLVM-exception"), "allowed")

    def test_what_ships_is_graded_and_what_does_not_is_not(self):
        self.assertEqual(self.verdict(licenses=["GPL-3.0"])["verdict"], "denied")
        self.assertEqual(self.verdict(licenses=["GPL-3.0"], scope="tool")["verdict"], "not-shipped")
        self.assertEqual(self.verdict(licenses=["GPL-3.0"], in_build=False)["verdict"], "not-shipped")
        self.assertEqual(self.verdict(licenses=["GPL-3.0"], sources=[{"path": "go.golangci.mod"}])["verdict"], "not-shipped")

    def test_a_package_an_image_redistributes_is_aggregate(self):
        self.assertEqual(self.verdict(ecosystem="package", licenses=["GPL-2.0-only"], scope="build")["verdict"], "aggregate")

    def test_the_orgs_own_packages_are_not_graded(self):
        self.assertIsNone(self.verdict(ecosystem="npm", internal=True, licenses=[]))

    def test_supply_chain_signals(self):
        from collector import policy
        old = {"latest_date": "2023-01-01T00:00:00Z"}
        abandoned = policy.signals(dep(ecosystem="npm", upstream=old), {"scorecard": {"checks": {"Maintained": 0}}}, NOW)
        self.assertEqual([s["kind"] for s in abandoned], ["abandoned"])
        pinned = policy.signals(dep(ecosystem="actions", name="sigstore/cosign-installer", version="v3"), None, NOW)
        self.assertEqual([s["kind"] for s in pinned], ["unpinned-action"])
        self.assertEqual(policy.signals(dep(ecosystem="actions", name="actions/checkout", version="v7"), None, NOW), [])

    def test_a_gap_opens_on_its_public_date(self):
        from collector import policy
        d = dep(status="vulnerable", level="critical", vulnerabilities=[{"id": "a", "published": "2026-09-01T00:00:00Z"}])
        start = policy.since(d, {}, "k", NOW)
        self.assertEqual(start.date().isoformat(), "2026-09-01")
        self.assertIsNone(policy.sla(d, start, {}, NOW), "no fix-by date without a policy")
        self.assertEqual(policy.sla(d, start, {"critical": 7}, NOW)["state"], "breached")

    def test_an_acceptance_hides_a_finding_until_it_lapses(self):
        from collector import policy
        rules = [{"name": "x", "reason": "not_used", "until": "2026-12-01"}]
        self.assertFalse(policy.accepted("lint", dep(), rules, NOW)["lapsed"])
        self.assertTrue(policy.accepted("lint", dep(), [dict(rules[0], until="2026-01-01")], NOW)["lapsed"])
        self.assertIsNone(policy.accepted("lint", dep(name="y"), rules, NOW))


class Reachability(unittest.TestCase):
    def test_an_advisory_is_matched_through_its_aliases(self):
        from collector import reach
        project = {"dependencies": [dep(name="golang.org/x/mod", vulnerabilities=[{"id": "GHSA-a", "aliases": ["CVE-1"]}]),
                                    dep(name="github.com/spf13/cobra")]}
        reach.annotate(project, {"GO-2026-1": "called"}, {"GO-2026-1": {"GHSA-a"}}, {"golang.org/x/mod"})
        d = project["dependencies"][0]
        self.assertEqual((d["reachability"], d["in_build"]), ("called", True))
        self.assertFalse(project["dependencies"][1]["in_build"])

    def test_a_vulnerable_module_the_code_never_calls_is_planned_not_fixed_now(self):
        d = resolver().classify(dep(vulnerabilities=[{"id": "GO-1", "severity": "high", "reachable": "not-in-build"}],
                                    reachability="not-in-build"))
        self.assertEqual((d["status"], d["level"]), ("vulnerable", "warning"))


class Exploits(unittest.TestCase):
    def test_an_exploited_advisory_is_critical_wherever_it_runs(self):
        v = {"id": "GHSA-1", "aliases": ["CVE-2026-1"], "severity": "moderate"}
        p = {"dependencies": [dep(scope="dev", vulnerabilities=[dict(v)])]}
        r = resolver(FakeSources(kev={"CVE-2026-1": "2026-09-01"},
                                 epss={"CVE-2026-1": (0.4213, 0.9731)}))
        r.known_exploited([p])
        r.exploit_prediction([p])
        d = r.classify(p["dependencies"][0])
        self.assertEqual(d["vulnerabilities"][0]["exploited"], "2026-09-01")
        self.assertEqual(d["vulnerabilities"][0]["epss"], {"probability": 0.4213, "percentile": 0.9731})
        self.assertEqual(d["level"], "critical")

    def test_an_exploited_advisory_no_code_calls_still_needs_fixing(self):
        d = resolver().classify(dep(vulnerabilities=[{"id": "GO-1", "severity": "low", "exploited": "2026-09-01",
                                                      "reachable": "not-in-build"}], reachability="not-in-build"))
        self.assertEqual(d["level"], "serious")

    def test_the_fix_steps_past_an_advisory_of_its_own(self):
        later = {"id": "GO-2", "affected": [{"package": {"name": "golang.org/x/mod", "ecosystem": "Go"},
                                             "ranges": [{"events": [{"introduced": "0"}, {"fixed": "0.41.0"}]}]}]}
        answers = {("Go", "golang.org/x/mod", "0.40.0"): ["GO-2"], ("Go", "golang.org/x/mod", "0.41.0"): []}
        src = FakeSources(osv_batch=lambda qs: [answers[q] for q in qs], osv_vuln=lambda vid: later)
        d = dep(name="golang.org/x/mod", version="v0.39.0", vulnerabilities=[{"id": "GO-1", "severity": "low", "fixed": "0.40.0"}])
        r = resolver(src)
        r.check_fixes([{"dependencies": [d]}])
        r.classify(d)
        self.assertEqual((d["fix"], d["fix_advisories"]), ("0.41.0", ["GO-2"]))

    def test_a_fix_with_an_advisory_nothing_fixes_is_marked_partial(self):
        unfixed = {"id": "GO-2", "affected": [{"package": {"name": "x", "ecosystem": "Go"}, "ranges": [{"events": [{"introduced": "0"}]}]}]}
        src = FakeSources(osv_batch=lambda qs: [["GO-2"] for _ in qs], osv_vuln=lambda vid: unfixed)
        d = dep(vulnerabilities=[{"id": "GO-1", "severity": "low", "fixed": "1.1.0"}])
        r = resolver(src)
        r.check_fixes([{"dependencies": [d]}])
        r.classify(d)
        self.assertTrue(d["fix_partial"])


class Licenses(unittest.TestCase):
    RULES = {"allow": ["MIT", "GPL-2.0-with-classpath-exception"], "review": ["LicenseRef-*", "MPL-*"], "deny": ["GPL-*"]}
    CATEGORIES = {"MIT": "Permissive", "Zlib": "Permissive", "MPL-2.0": "Copyleft Limited", "OSL-3.0": "Copyleft"}

    def graded(self, expr, categories=None, **kw):
        from collector import policy
        return policy.license_verdict(dep(licenses=[expr], **kw), self.RULES,
                                      self.CATEGORIES if categories is None else categories)

    def test_an_exact_entry_wins_over_a_wildcard(self):
        self.assertEqual(self.graded("GPL-2.0-with-classpath-exception")["verdict"], "allowed")
        self.assertEqual(self.graded("GPL-2.0-only")["verdict"], "denied")
        self.assertEqual(self.graded("LicenseRef-Other")["verdict"], "review")

    def test_a_category_is_a_hint_and_never_a_grade(self):
        self.assertEqual(self.graded("Zlib"), {"expression": "Zlib", "verdict": "unknown", "unlisted": {"Zlib": "Permissive"}})
        self.assertEqual(self.graded("Zlib", categories={})["verdict"], "unknown")
        self.assertEqual(self.graded("MIT AND Zlib", categories={})["unlisted"], {"Zlib": ""})

    def test_a_stricter_license_found_in_the_files_is_named(self):
        lic = self.graded("MIT", licenses_found=["MIT", "(MIT OR GPL-2.0-only) AND MIT", "MIT AND MPL-2.0", "OSL-3.0", "NOASSERTION"],
                          license_score=46)
        self.assertEqual((lic["verdict"], lic["found"], lic["found_verdict"], lic["unlisted"], lic["score"]),
                         ("allowed", ["MPL-2.0", "OSL-3.0"], "review", {"OSL-3.0": "Copyleft"}, 46))
        self.assertNotIn("found", self.graded("MIT", scope="dev", licenses_found=["GPL-2.0-only"]))
        self.assertNotIn("found", self.graded("MIT", licenses_found=["MIT AND LicenseRef-scancode-proprietary-license"]))

    def test_clearlydefined_coordinates(self):
        self.assertEqual(resolve._clearlydefined_coordinate(dep(name="golang.org/x/net", version="v0.30.0")),
                         "go/golang/golang.org%2fx/net/v0.30.0")
        self.assertEqual(resolve._clearlydefined_coordinate(dep(ecosystem="npm", datasource="npm", name="@codemirror/state",
                                                                package="@codemirror/state", version="6.4.1")),
                         "npm/npmjs/@codemirror/state/6.4.1")


class Sbom(unittest.TestCase):
    def test_purls(self):
        from collector import sbom
        self.assertEqual(sbom.purl(dep(name="github.com/spf13/cobra", version="v1.8.1")), "pkg:golang/github.com/spf13/cobra@v1.8.1")
        self.assertEqual(sbom.purl(dep(ecosystem="npm", datasource="npm", name="@types/react", package="@types/react",
                                       version="18.3.1")), "pkg:npm/%40types/react@18.3.1")
        self.assertEqual(sbom.purl(dep(ecosystem="actions", name="actions/cache/save", version="v4")),
                         "pkg:github/actions/cache@v4#save")
        self.assertEqual(sbom.purl(dep(ecosystem="image", datasource="oci", name="registry.fedoraproject.org/fedora",
                                       version="44")), "pkg:docker/fedora@44?repository_url=registry.fedoraproject.org")
        rpm = sbom.purl(dep(ecosystem="package", datasource="rpm", name="curl", version=None, release="44",
                            upstream={"effective": "8.18.0-10.fc44"}))
        self.assertEqual(rpm.split("@"), ["pkg:rpm/fedora/curl", "8.18.0-10.fc44?distro=fedora-44"])
        self.assertIsNone(sbom.purl(dep(ecosystem="runtime", name="go", version="1.27")))

    def test_vex_says_what_the_collector_knows(self):
        from collector import sbom
        v = {"id": "GO-1", "aliases": [], "severity": "moderate", "url": "https://osv.dev/vulnerability/GO-1"}
        data = {"org": "o", "generated": "2026-09-12T00:00:00Z", "projects": [
            {"name": "a", "repo": {}, "dependencies": [dep(vulnerabilities=[dict(v, reachable="not-in-build")])]},
            {"name": "b", "repo": {}, "dependencies": [dep(vulnerabilities=[dict(v)],
                                                           accepted={"reason": "tolerable_risk", "note": "n"})]},
            {"name": "c", "repo": {}, "dependencies": [dep(vulnerabilities=[dict(v, reachable="called")])]}]}
        states = sorted((x["analysis"]["state"], x["analysis"].get("justification"), tuple(x["analysis"].get("response", [])))
                        for x in sbom.cyclonedx(data)["vulnerabilities"])
        self.assertEqual(states, [("exploitable", None, ()), ("exploitable", None, ("will_not_fix",)),
                                  ("not_affected", "code_not_present", ())])


class KnownFacts(unittest.TestCase):
    def test_an_indirect_version_seen_before_is_not_asked_again(self):
        previous = {"schema": 1, "projects": [{"dependencies": [
            dep(ecosystem="npm", datasource="npm", name="left-pad", package="left-pad", version="1.3.0", scope="transitive",
                licenses=["WTFPL"]),
            dep(ecosystem="package", datasource="rpm", name="curl", release="44", upstream={"effective": "8.18.0-10.fc44"},
                source_package="curl", licenses=["curl"])]}]}
        packages, fedora = resolve.known_facts(previous)
        asked = []
        r = resolver(FakeSources(depsdev_versions=lambda keys: asked.extend(keys) or {}))
        seen = dep(ecosystem="npm", datasource="npm", name="left-pad", package="left-pad", version="1.3.0", scope="transitive")
        direct = dep(ecosystem="npm", datasource="npm", name="left-pad", package="left-pad", version="1.3.0", scope="direct")
        r.enrich_packages([{"dependencies": [seen, direct]}], packages)
        self.assertEqual((seen["licenses"], asked), (["WTFPL"], [("NPM", "left-pad", "1.3.0")]))
        rpm = dep(ecosystem="package", datasource="rpm", name="curl", release="44", upstream={"effective": "8.18.0-10.fc44"},
                  source_candidates=["curl"], nvra="curl-8.18.0-10.fc44.x86_64")
        resolver(FakeSources(koji_source=AssertionError("asked"), fedora_license=AssertionError("asked"))).enrich_fedora_licenses(
            [{"dependencies": [rpm]}], fedora)
        self.assertEqual((rpm["source_package"], rpm["licenses"], "nvra" in rpm), ("curl", ["curl"], False))


class Sources(unittest.TestCase):
    def test_endoflife_date_v1_reads_as_cycles(self):
        from collector.sources import Sources as Real

        class Http:
            def json(self, url, **kw):
                assert "/api/v1/products/nodejs" in url
                return {"result": {"releases": [{"name": "26", "releaseDate": "2026-05-05", "isLts": False, "ltsFrom": "2026-10-28",
                                                 "isEoas": False, "eoasFrom": "2027-10-27", "isEol": True, "eolFrom": None,
                                                 "latest": {"name": "26.8.2", "date": "2026-09-09"}}]}}
        c = Real(Http()).cycles("nodejs")[0]
        self.assertEqual((c["cycle"], c["lts"], c["support"], c["eol"], c["latest"], c["latestReleaseDate"]),
                         ("26", "2026-10-28", "2027-10-27", True, "26.8.2", "2026-09-09"))

    def test_the_token_goes_to_github_in_the_environment_only(self):
        from collector import gitrepo
        gitrepo.use_token("t0k")
        try:
            env = gitrepo.git_env()
            self.assertEqual(env["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraheader")
            self.assertTrue(env["GIT_CONFIG_VALUE_0"].startswith("AUTHORIZATION: basic "))
        finally:
            gitrepo.use_token(None)
        self.assertNotIn("GIT_CONFIG_KEY_0", gitrepo.git_env())


class Actions(unittest.TestCase):
    def project(self, *deps):
        return {"name": "p", "repo": {"url": "https://github.com/o/p", "sha": "abc", "branch": "main"}, "dependencies": list(deps)}

    def test_vulnerable_lockfile_packages_share_one_refresh(self):
        from collector import actions
        a = dep(ecosystem="npm", name="vite", scope="transitive", status="vulnerable", level="serious", dev=True,
                sources=[{"path": "apps/viewer/package-lock.json", "line": None}], vulnerabilities=[{"id": "GHSA-1", "severity": "moderate"}])
        b = dict(a, name="esbuild", vulnerabilities=[{"id": "GHSA-2", "severity": "low"}])
        (card,) = actions.build([self.project(a, b)])
        self.assertEqual((card["tier"], card["title"], card["how"]), ("fix", "Refresh the lockfile in p/apps/viewer", "cd apps/viewer && npm audit fix"))

    def test_an_edit_is_listed_at_every_place_the_pin_is_written(self):
        from collector import actions
        d = dep(ecosystem="actions", name="actions/checkout", version="v4", scope="ci", status="major", level="warning",
                upstream={"latest": "6.0.0"}, sources=[{"path": ".github/workflows/ci.yml", "line": 12}, {"path": ".github/workflows/pages.yml", "line": 30}])
        p = self.project(d)
        p["_actions"] = actions.build([p])
        doc = actions.agent_document({"generated": "2026-09-12T00:00:00Z", "org": "o", "projects": [p]}, "https://x/d/", "spec")
        (act,) = doc["projects"][0]["actions"]
        self.assertEqual([(s["edit"], s["line"], s["text"]) for s in act["steps"]],
                         [(".github/workflows/ci.yml", 12, "uses: actions/checkout@v6"), (".github/workflows/pages.yml", 30, "uses: actions/checkout@v6")])
        self.assertEqual(act["id"], "p:actions:actions")
        self.assertEqual(act["decline"]["accepted"]["finding"], "major")

    def test_a_fork_proposes_a_decline_in_its_own_copy_of_this_repository(self):
        from collector import actions
        d = dep(ecosystem="actions", name="actions/checkout", version="v4", scope="ci", status="major", level="warning",
                upstream={"latest": "6.0.0"}, sources=[{"path": ".github/workflows/ci.yml", "line": 12}])
        p = self.project(d)
        p["_actions"] = actions.build([p])
        doc = actions.agent_document({"generated": "2026-09-12T00:00:00Z", "org": "o", "owner": "alice", "projects": [p]},
                                     "https://alice.github.io/dashboards/", "spec")
        (act,) = doc["projects"][0]["actions"]
        self.assertEqual((doc["org"], doc["owner"], act["decline"]["repo"]), ("o", "alice", "alice/dashboards"))
        self.assertIn("deps-config.json in alice/dashboards", doc["workflow"][-1])
        self.assertTrue(act["page"].startswith("https://alice.github.io/dashboards/deps?project=p#"))

    def test_an_accepted_record_is_no_work(self):
        from collector import actions
        d = dep(status="vulnerable", level="critical", vulnerabilities=[{"id": "GO-1", "severity": "high"}], accepted={"reason": "not_used"})
        self.assertEqual(actions.build([self.project(d)]), [])

    def test_a_license_the_policy_does_not_name_asks_to_be_named(self):
        from collector import actions
        d = dep(status="current", level="good", license={"expression": "Zlib", "verdict": "unknown", "unlisted": {"Zlib": "Permissive"}})
        (card,) = actions.build([self.project(d)])
        self.assertEqual((card["tier"], card["title"], card["why"]), ("routine", "Add Zlib to the license policy", "ScanCode LicenseDB calls Zlib Permissive"))


class Http(unittest.TestCase):
    def test_a_host_that_keeps_failing_is_not_asked_again(self):
        from collector import http
        client = http.Client()
        calls = []

        def down(req, timeout):
            calls.append(req.full_url)
            raise OSError("connection refused")
        client._open = down
        retries, http.RETRIES = http.RETRIES, 0
        try:
            for i in range(http.GIVE_UP_AFTER + 3):
                with self.assertRaises(FetchError):
                    client.get(f"https://down.example/{i}")
        finally:
            http.RETRIES = retries
        self.assertEqual(len(calls), http.GIVE_UP_AFTER)


class Catalog(unittest.TestCase):
    def test_spec_describes_every_source_the_page_lists(self):
        import os
        import re as _re
        from collector.catalog import CATALOG, GROUPS
        with open(os.path.join(os.path.dirname(__file__), "..", "SPEC.md"), encoding="utf-8") as fh:
            spec = fh.read()
        section = spec[spec.index("### Information sources"):]
        section = section[:section.index("\n### ", 5)]
        named = set(_re.findall(r"^\| \[([^\]]+)\]\(", section, _re.MULTILINE))
        self.assertEqual(named, {c["name"] for c in CATALOG})
        self.assertTrue(all(c["group"] in GROUPS and c["gives"] and c["url"].startswith("https://") for c in CATALOG))


    def test_a_fork_reads_its_own_repositories_and_site(self):
        from collector.catalog import entries
        byid = {c["id"]: c for c in entries("alice", "https://alice.github.io/dashboards/",
                                            "https://alice.github.io/dashboards/deps.json")}
        self.assertEqual(byid["github-git"]["url"], "https://github.com/alice")
        self.assertEqual((byid["status-files"]["url"], byid["status-files"]["hosts"]), ("https://alice.github.io/", ["alice.github.io"]))
        self.assertEqual(byid["previous"]["url"], "https://alice.github.io/dashboards/deps.json")

    def test_a_run_with_no_site_names_no_status_file_or_previous_file(self):
        from collector.catalog import entries
        ids = {c["id"] for c in entries("alice")}
        self.assertIn("github-git", ids)
        self.assertFalse(ids & {"status-files", "previous"})


class Feed(unittest.TestCase):
    def test_the_feed_announces_each_action_to_fix_once_dated_when_first_seen(self):
        import xml.etree.ElementTree as ET
        from collector.__main__ import atom_feed
        act = lambda key, tier, kind="dep": {"key": key, "id": "action-" + key, "kind": kind, "tier": tier, "type": "security",
                                             "title": "Upgrade " + key, "result": "Fixes 1 advisory", "why": "", "evidence": [],
                                             "how": "npm install x@2", "projects": ["a", "b"]}
        data = {"org": "o", "generated": "2026-09-13T05:17:00Z",
                "seen": {"action|dep|x": "2026-09-01T05:17:00Z"},
                "actions": [act("dep|x", "fix"), act("dep|y", "plan"), act("license|denied|z", "plan", "license")]}
        root = ET.fromstring(atom_feed(data, "https://example.org/d/"))
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entries = root.findall("a:entry", ns)
        self.assertEqual([e.find("a:id", ns).text for e in entries], ["urn:o:deps:action:license|denied|z", "urn:o:deps:action:dep|x"])
        self.assertEqual(entries[1].find("a:updated", ns).text, "2026-09-01T05:17:00Z")
        self.assertEqual(entries[1].find("a:link", ns).get("href"), "https://example.org/d/deps?view=upgrades#action-dep|x")
        self.assertIn("How: npm install x@2", entries[1].find("a:summary", ns).text)
        self.assertEqual(root.find("a:author/a:name", ns).text, "o dashboards")

    def test_a_fork_keeps_the_org_name_and_ids_its_entries_by_its_own_owner(self):
        import xml.etree.ElementTree as ET
        from collector.__main__ import atom_feed
        data = {"org": "o", "owner": "alice", "generated": "2026-09-13T05:17:00Z", "seen": {},
                "actions": [{"key": "dep|x", "id": "action-dep|x", "kind": "dep", "tier": "fix", "type": "security",
                             "title": "Upgrade x", "result": "", "why": "", "evidence": [], "projects": ["a"]}]}
        root = ET.fromstring(atom_feed(data, "https://alice.github.io/dashboards/"))
        ns = {"a": "http://www.w3.org/2005/Atom"}
        self.assertEqual(root.find("a:entry/a:id", ns).text, "urn:alice:deps:action:dep|x")
        self.assertEqual(root.find("a:author/a:name", ns).text, "o dashboards")
        self.assertEqual(root.find("a:link[@rel='self']", ns).get("href"), "https://alice.github.io/dashboards/deps-feed.xml")
