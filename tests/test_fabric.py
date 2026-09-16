"""Fabric specifics that decide whether the loader accepts the mod at all."""

from __future__ import annotations

import unittest

from lib.common import Failure
from lib.loaders.base import BuildEnv
from lib.loaders.fabric import (
    FabricLoader,
    LoomRequirements,
    check_pinned_loom,
    normalize_minecraft_version,
    pick_loom,
)


class NormalizeTest(unittest.TestCase):
    """Fabric Loader compares its own rewrite of the id, not the id itself."""

    def test_releases_are_left_alone(self):
        self.assertEqual(normalize_minecraft_version("26.2"), "26.2")
        self.assertEqual(normalize_minecraft_version("1.21.11"), "1.21.11")

    def test_date_based_ids(self):
        self.assertEqual(normalize_minecraft_version("26.2-snapshot-3"), "26.2-alpha.3")
        self.assertEqual(normalize_minecraft_version("26.2-pre-2"), "26.2-pre.2")
        self.assertEqual(normalize_minecraft_version("26.2-rc-1"), "26.2-rc.1")
        self.assertEqual(normalize_minecraft_version("26.2.1-rc-1"), "26.2.1-rc.1")

    def test_legacy_ids(self):
        self.assertEqual(normalize_minecraft_version("1.21.11-pre1"), "1.21.11-beta.1")
        self.assertEqual(normalize_minecraft_version("1.21.11-rc2"), "1.21.11-rc.2")

    def test_a_weekly_snapshot_is_refused_rather_than_guessed(self):
        with self.assertRaises(Failure) as caught:
            normalize_minecraft_version("25w45a")
        self.assertIn("25w45a", str(caught.exception))


class RenderRangeTest(unittest.TestCase):
    def test_a_pinned_candidate_uses_the_normalized_form(self):
        """"=26.2-rc-1" is refused by the loader on the 26.2-rc-1 server."""
        self.assertEqual(FabricLoader().render_range("26.2-rc-1", "26.2-rc-1"), "=26.2-rc.1")

    def test_releases_render_as_before(self):
        loader = FabricLoader()
        self.assertEqual(loader.render_range("26.2", "26.2"), "=26.2")
        self.assertEqual(loader.render_range("26.1", "26.1.2"), ">=26.1 <=26.1.2")


class PickLoomTest(unittest.TestCase):
    """The newest Loom the mod's own build can run, read from Loom's metadata."""

    # What fabric-loom-<v>.module declares, as of 1.18.1.
    REQUIREMENTS = {
        "1.18.1": LoomRequirements(gradle="9.7.0", java=25),
        "1.18.0": LoomRequirements(gradle="9.7.0", java=25),
        "1.17.18": LoomRequirements(gradle="9.5.0", java=21),
        "1.17.9": LoomRequirements(gradle="9.2.0", java=21),
    }
    STABLE = ["1.17.9", "1.18.1", "1.17.18", "1.18.0"]  # publication order

    def setUp(self):
        self.asked: list[str] = []

    def requirements(self, version):
        self.asked.append(version)
        return self.REQUIREMENTS.get(version)

    def pick(self, env, stable=None, **kwargs):
        return pick_loom(stable or self.STABLE, self.requirements, env, **kwargs)

    def test_a_recent_enough_wrapper_gets_the_newest(self):
        choice = self.pick(BuildEnv(gradle="9.7.0", java=25))
        self.assertEqual(choice.version, "1.18.1")
        self.assertEqual(choice.note, "")

    def test_an_older_wrapper_gets_the_newest_it_can_run(self):
        """The self-test fixture: wrapper 9.5.1, and 1.18.1 needing 9.7.0."""
        choice = self.pick(BuildEnv(gradle="9.5.1", java=25))
        self.assertEqual(choice.version, "1.17.18")
        self.assertIn("1.18.1 requires Gradle >= 9.7.0", choice.note)
        self.assertIn("9.5.1", choice.note)

    def test_a_short_gradle_version_compares_numerically(self):
        self.assertEqual(self.pick(BuildEnv(gradle="9.7", java=25)).version, "1.18.1")
        self.assertEqual(self.pick(BuildEnv(gradle="9.10", java=25)).version, "1.18.1")

    def test_a_gradle_release_candidate_is_not_the_release(self):
        self.assertEqual(self.pick(BuildEnv(gradle="9.7.0-rc-1", java=25)).version, "1.17.18")

    def test_java_is_a_constraint_too(self):
        choice = self.pick(BuildEnv(gradle="9.7.0", java=21))
        self.assertEqual(choice.version, "1.17.18")
        self.assertIn("Java >= 25", choice.note)

    def test_unreadable_metadata_is_skipped_never_picked_blind(self):
        choice = self.pick(BuildEnv(gradle="9.7.0", java=25), stable=["1.19.0", *self.STABLE])
        self.assertEqual(choice.version, "1.18.1")
        self.assertIn("could not be read", choice.note)

    def test_no_constraint_takes_the_newest_without_a_lookup(self):
        self.assertEqual(self.pick(BuildEnv()).version, "1.18.1")
        self.assertEqual(self.asked, [])

    def test_nothing_compatible_names_the_gap_and_the_fix(self):
        with self.assertRaises(Failure) as caught:
            self.pick(BuildEnv(gradle="8.14", java=25))
        message = str(caught.exception)
        self.assertIn("1.18.1 requires Gradle >= 9.7.0", message)
        self.assertIn("./gradlew wrapper --gradle-version 9.7.0", message)
        self.assertIn("--loom", message)

    def test_the_search_is_bounded(self):
        stable = [f"1.{minor}.0" for minor in range(40)]
        with self.assertRaises(Failure):
            pick_loom(
                stable,
                lambda version: self.asked.append(version) or LoomRequirements(gradle="99.0"),
                BuildEnv(gradle="9.5.1"),
                limit=5,
            )
        self.assertEqual(len(self.asked), 5)

    def test_no_stable_loom_at_all(self):
        with self.assertRaises(Failure):
            pick_loom([], self.requirements, BuildEnv(gradle="9.7.0"))


class PinnedLoomTest(unittest.TestCase):
    def test_a_pin_the_wrapper_cannot_run_is_refused_up_front(self):
        with self.assertRaises(Failure) as caught:
            check_pinned_loom(
                "1.18.1", lambda _v: LoomRequirements(gradle="9.7.0"), BuildEnv(gradle="9.5.1")
            )
        self.assertIn("Gradle >= 9.7.0", str(caught.exception))

    def test_a_pin_with_unreadable_metadata_goes_through(self):
        """The pin is the escape hatch; missing metadata must not close it."""
        check_pinned_loom("1.4.10", lambda _v: None, BuildEnv(gradle="8.0"))


if __name__ == "__main__":
    unittest.main()
