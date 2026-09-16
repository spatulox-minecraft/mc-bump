"""Fabric specifics that decide whether the loader accepts the mod at all."""

from __future__ import annotations

import unittest

from lib.common import Failure
from lib.loaders.fabric import FabricLoader, normalize_minecraft_version


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


if __name__ == "__main__":
    unittest.main()
