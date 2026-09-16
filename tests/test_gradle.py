"""The Gradle wrapper, as far as choosing a build plugin is concerned."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lib.gradle import ModPaths, gradle_version_key, read_wrapper_gradle_version

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
