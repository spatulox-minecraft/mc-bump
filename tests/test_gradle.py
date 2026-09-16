"""The Gradle wrapper, as far as choosing a build plugin is concerned."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lib.common import Failure
from lib.gradle import (
    ModPaths,
    gradle_version_key,
    read_wrapper_gradle_version,
    set_wrapper_gradle_version,
    with_wrapper_gradle_version,
)

WRAPPER = """\
distributionBase=GRADLE_USER_HOME
distributionPath=wrapper/dists
distributionUrl=https\\://services.gradle.org/distributions/gradle-{version}-{kind}.zip
networkTimeout=10000
"""


class WrapperVersionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.paths = ModPaths.under(Path(tmp.name), metadata="fabric.mod.json")

    def write(self, text: str) -> None:
        self.paths.gradle_wrapper.parent.mkdir(parents=True, exist_ok=True)
        self.paths.gradle_wrapper.write_text(text, encoding="utf-8")

    def test_bin_and_all_distributions(self):
        for kind in ("bin", "all"):
            with self.subTest(kind=kind):
                self.write(WRAPPER.format(version="9.5.1", kind=kind))
                self.assertEqual(read_wrapper_gradle_version(self.paths), "9.5.1")

    def test_a_release_candidate_keeps_its_suffix(self):
        self.write(WRAPPER.format(version="9.7.0-rc-1", kind="bin"))
        self.assertEqual(read_wrapper_gradle_version(self.paths), "9.7.0-rc-1")

    def test_no_wrapper_means_no_constraint(self):
        self.assertIsNone(read_wrapper_gradle_version(self.paths))

    def test_an_unreadable_url_means_no_constraint(self):
        self.write("distributionUrl=https\\://example.invalid/custom.zip\n")
        self.assertIsNone(read_wrapper_gradle_version(self.paths))


class WrapperRewriteTest(WrapperVersionTest):
    def test_only_the_version_moves(self):
        text = WRAPPER.format(version="9.3.0", kind="all")
        rewritten = with_wrapper_gradle_version(text, "9.5.1")
        self.assertIn("gradle-9.5.1-all.zip", rewritten)
        self.assertEqual(rewritten.replace("9.5.1", "9.3.0"), text)

    def test_a_pinned_checksum_is_replaced_by_the_published_one(self):
        text = WRAPPER.format(version="9.3.0", kind="bin") + "distributionSha256Sum=" + "a" * 64 + "\n"
        asked = []
        rewritten = with_wrapper_gradle_version(
            text, "9.5.1", lambda name: asked.append(name) or "b" * 64
        )
        self.assertEqual(asked, ["gradle-9.5.1-bin.zip"])
        self.assertIn("distributionSha256Sum=" + "b" * 64, rewritten)

    def test_no_checksum_means_no_lookup(self):
        with_wrapper_gradle_version(
            WRAPPER.format(version="9.3.0", kind="bin"), "9.5.1", lambda _n: self.fail("looked up")
        )

    def test_an_unreadable_url_is_refused(self):
        with self.assertRaises(Failure):
            with_wrapper_gradle_version("distributionUrl=https\\://example.invalid/x.zip\n", "9.5.1")

    def test_writing_the_version_already_there_changes_nothing(self):
        self.write(WRAPPER.format(version="9.5.1", kind="bin"))
        self.assertFalse(set_wrapper_gradle_version(self.paths, "9.5.1", dry_run=False))

    def test_dry_run_writes_nothing(self):
        text = WRAPPER.format(version="9.3.0", kind="bin")
        self.write(text)
        self.assertTrue(set_wrapper_gradle_version(self.paths, "9.5.1", dry_run=True))
        self.assertEqual(self.paths.gradle_wrapper.read_text(encoding="utf-8"), text)

    def test_the_wrapper_is_rewritten(self):
        self.write(WRAPPER.format(version="9.3.0", kind="bin"))
        self.assertTrue(set_wrapper_gradle_version(self.paths, "9.5.1", dry_run=False))
        self.assertEqual(read_wrapper_gradle_version(self.paths), "9.5.1")


class GradleVersionKeyTest(unittest.TestCase):
    def test_missing_components_are_zeros(self):
        self.assertEqual(gradle_version_key("9.7"), gradle_version_key("9.7.0"))

    def test_numeric_not_lexical(self):
        self.assertGreater(gradle_version_key("9.10.0"), gradle_version_key("9.9.0"))

    def test_a_release_candidate_sorts_just_below_its_release(self):
        self.assertLess(gradle_version_key("9.7.0-rc-1"), gradle_version_key("9.7.0"))
        self.assertGreater(gradle_version_key("9.7.0-rc-1"), gradle_version_key("9.6.9"))


if __name__ == "__main__":
    unittest.main()
