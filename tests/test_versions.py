import unittest
from datetime import datetime, timezone

from collector import versions


class Distance(unittest.TestCase):
    def test_classes(self):
        cases = [
            ("v1.10.2", "v1.10.2", None),
            ("1.27.0", "1.27.1", "patch"),
            ("v1.16.0", "v1.17.0", "minor"),
            ("18.3.1", "19.3.0", "major"),
            ("v0.39.0", "v0.41.0", "minor"),
            ("25.0.4.1", "25.0.4.2", "patch"),
            ("4.0.0-rc.3", "4.0.0", "patch"),
            ("2.0.0", "1.9.9", None),
        ]
        for pinned, latest, want in cases:
            with self.subTest(pinned=pinned, latest=latest):
                self.assertEqual(versions.distance(pinned, latest), want)

    def test_a_short_pin_floats_inside_what_it_names(self):
        self.assertEqual(versions.distance("v4", "v7.0.1"), "major")
        self.assertIsNone(versions.distance("v7", "v7.3.0"))
        self.assertIsNone(versions.distance("1.27", "1.27.1"))

    def test_an_unreadable_version_is_never_behind(self):
        self.assertIsNone(versions.distance("nonroot", "13"))
        self.assertIsNone(versions.distance(None, "1.0.0"))


class Pseudo(unittest.TestCase):
    def test_go_pseudo_version(self):
        stamp, sha = versions.pseudo("v0.0.0-20260910225058-47654833d481")
        self.assertEqual(stamp, datetime(2026, 9, 10, 22, 50, 58, tzinfo=timezone.utc))
        self.assertEqual(sha, "47654833d481")

    def test_the_orgs_npm_builds(self):
        self.assertEqual(versions.pseudo("0.3.1-dev.20260909170256.1638d27")[1], "1638d27")
        self.assertEqual(versions.pseudo("0.0.0-20260910164917-9881277aba3b")[1], "9881277aba3b")

    def test_a_release_is_not_a_pseudo_version(self):
        self.assertIsNone(versions.pseudo("v1.2.3"))

    def test_pseudo_versions_order_by_time(self):
        older = versions.parse("v0.0.0-20260909183215-bda511aea589")
        newer = versions.parse("v0.0.0-20260910225058-47654833d481")
        self.assertLess(older, newer)


class Newest(unittest.TestCase):
    def test_prereleases_are_skipped(self):
        self.assertEqual(versions.newest(["v1.17.0", "v1.16.2", "v1.18.0-rc.1"]), "v1.17.0")

    def test_newest_on_a_line(self):
        self.assertEqual(versions.newest_in_line(["1.17.0", "1.16.2", "1.16.1"], "1.16.0"), "1.16.2")

    def test_prerelease_words_in_tags(self):
        self.assertTrue(versions.looks_prerelease("rust-v0.153.0-alpha.2"))
        self.assertFalse(versions.looks_prerelease("jdk-25.0.4.1+1"))

    def test_fedora_version_release(self):
        v, release = versions.nvr("6.19.10-300.fc44")
        self.assertEqual(v.nums, (6, 19, 10))
        self.assertEqual(release, "300.fc44")



class NpmRanges(unittest.TestCase):
    def test_the_forms_package_json_and_a_registry_write(self):
        for version, spec, want in [
                ("5.4.21", "^6.0.0 || ^7.0.0 || ^8.0.0", False), ("7.1.0", "^6.0.0 || ^7.0.0 || ^8.0.0", True),
                ("0.21.5", "^0.21.3", True), ("0.22.0", "^0.21.3", False), ("0.0.4", "^0.0.3", False),
                ("1.2.9", "~1.2.3", True), ("1.3.0", "~1.2.3", False), ("2.0.0", "1.x", False), ("1.5.0", ">= 1.2 <2", True),
                ("2.3.9", "1.2.3 - 2.3", True), ("2.4.0", "1.2.3 - 2.3", False), ("2.1.9", "4.1.11", False), ("3.0.0", "*", True)]:
            self.assertEqual(versions.satisfies(version, spec), want, f"{version} against {spec}")

    def test_a_prerelease_matches_only_a_range_that_names_one(self):
        self.assertFalse(versions.satisfies("2.0.0-rc.1", "^1.0.0"))
        self.assertFalse(versions.satisfies("1.0.0-rc.1", "^1.0.0"))
        self.assertTrue(versions.satisfies("1.0.0-rc.2", "^1.0.0-rc.1"))

    def test_a_range_it_cannot_read_is_unknown_not_false(self):
        self.assertIsNone(versions.satisfies("1.0.0", "latest"))
        self.assertIsNone(versions.satisfies("1.0.0", "npm:other@^1"))


if __name__ == "__main__":
    unittest.main()
