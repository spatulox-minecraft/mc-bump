"""Pure functions over versions: series, labels, bounds, mod_version template.

No network: the resolvers that call Mojang, Fabric and Modrinth are deliberately
NOT tested — they describe the shape of upstream APIs, which no local assertion
can pin down.

What is tested is the decision layer, and it is worth testing for one reason: a
mistake there does not crash. It produces a green matrix, a jar that a player's
loader refuses, or a store page announcing a version nobody ever booted. Those
functions also run once a week at best, at the end of a forty minute pipeline,
and only for the single scenario reality happens to present that day; the series
reset path is exercised the day Mojang ships a new series, not before.
"""

from __future__ import annotations

import unittest

from lib.common import Failure
from lib.versions import (
    compat_bounds,
    mc_label,
    parse_mod_version,
    parse_version,
    render_mod_version,
    series_of,
    update_mod_version,
    versions_to_test,
)


class SeriesTest(unittest.TestCase):
    def test_series_is_the_first_two_components(self):
        self.assertEqual(series_of("26.1.2"), "26.1")
        self.assertEqual(series_of("26.2"), "26.2")
        self.assertEqual(series_of("1.21.10"), "1.21")

    def test_parse_version_orders_numerically_not_lexically(self):
        self.assertEqual(parse_version("26.1.2"), (26, 1, 2))
        # the whole point: "1.21.10" must sort above "1.21.9"
        self.assertGreater(parse_version("1.21.10"), parse_version("1.21.9"))

    def test_parse_version_rejects_anything_not_purely_numeric(self):
        for version in ("24w14a", "26.2-rc1", "1.21-pre1", ""):
            self.assertIsNone(parse_version(version), version)


class McLabelTest(unittest.TestCase):
    """The label is the published version number, burnt on first upload."""

    def test_a_single_version_is_named_exactly(self):
        self.assertEqual(mc_label(["26.2"]), "26.2")

    def test_several_sub_versions_collapse_to_the_series(self):
        self.assertEqual(mc_label(["26.1", "26.1.1"]), "26.1.x")
        self.assertEqual(mc_label(["26.1", "26.1.1", "26.1.2"]), "26.1.x")
        self.assertEqual(mc_label(["1.21", "1.21.1", "1.21.10"]), "1.21.x")

    def test_input_order_does_not_matter(self):
        self.assertEqual(mc_label(["26.1.2", "26.1"]), "26.1.x")

    def test_the_wildcard_never_climbs_to_the_series_level(self):
        """"26.x" would promise 26.3, which nothing ever built or booted."""
        for versions in (["26.1", "26.1.1"], ["26.1", "26.2"], ["26.1", "26.1.1", "26.2"]):
            label = mc_label(versions)
            self.assertNotEqual(label, "26.x", versions)
            self.assertTrue(label.startswith("26.1"), f"{versions} -> {label}")

    def test_no_version_yields_no_label(self):
        self.assertEqual(mc_label([]), "")
        self.assertEqual(mc_label([""]), "")


class VersionsToTestTest(unittest.TestCase):
    """What the matrix boots. Returning too little announces an untested version."""

    def test_target_is_included_even_when_not_supported_yet(self):
        self.assertEqual(versions_to_test("26.1.2", ["26.1"]), ["26.1", "26.1.2"])

    def test_oldest_first_and_deduplicated(self):
        self.assertEqual(
            versions_to_test("26.1.2", ["26.1.2", "26.1", "26.1.1", "26.1"]),
            ["26.1", "26.1.1", "26.1.2"],
        )

    def test_other_series_are_dropped(self):
        self.assertEqual(versions_to_test("26.2", ["26.1", "26.1.1"]), ["26.2"])

    def test_non_numeric_entries_are_dropped(self):
        self.assertEqual(versions_to_test("26.1.1", ["26.1", "24w14a"]), ["26.1", "26.1.1"])


class CompatBoundsTest(unittest.TestCase):
    """What the loader accepts or refuses at runtime, before rendering."""

    def test_the_documented_table(self):
        self.assertEqual(compat_bounds("26.1", []), ("26.1", "26.1"))
        self.assertEqual(compat_bounds("26.1.1", ["26.1"]), ("26.1", "26.1.1"))
        self.assertEqual(compat_bounds("26.1.2", ["26.1", "26.1.1"]), ("26.1", "26.1.2"))
        # series reset: supported has been emptied by the update
        self.assertEqual(compat_bounds("26.2", []), ("26.2", "26.2"))

    def test_other_series_do_not_widen_the_bounds(self):
        self.assertEqual(compat_bounds("26.2", ["26.1", "26.1.1"]), ("26.2", "26.2"))

    def test_a_non_numeric_target_is_pinned_exactly(self):
        self.assertEqual(compat_bounds("24w14a", []), ("24w14a", "24w14a"))

    def test_the_upper_bound_is_the_highest_not_the_last_listed(self):
        self.assertEqual(compat_bounds("1.21.9", ["1.21.10", "1.21"]), ("1.21", "1.21.10"))


class CoherenceTest(unittest.TestCase):
    """The three derivations must agree; they are read by three different files.

    supported_minecraft_versions, the metadata range and mod_version are written
    to gradle.properties, the loader metadata and the jar name respectively.
    Nothing at runtime compares them, so a divergence is silent until a player
    hits it.
    """

    SCENARIOS = (
        ("26.1", []),
        ("26.1.1", ["26.1"]),
        ("26.1.2", ["26.1", "26.1.1"]),
        ("26.2", []),
        ("26.2.1", ["26.2"]),
    )

    def test_upper_bound_is_the_last_version_actually_booted(self):
        for target, supported in self.SCENARIOS:
            with self.subTest(target=target):
                booted = versions_to_test(target, supported)
                _, high = compat_bounds(target, supported)
                self.assertEqual(high, booted[-1])

    def test_label_and_bounds_describe_the_same_series(self):
        for target, supported in self.SCENARIOS:
            with self.subTest(target=target):
                label = mc_label(versions_to_test(target, supported))
                self.assertTrue(
                    label.startswith(series_of(target)),
                    f"{label} is not in series {series_of(target)}",
                )

    def test_the_jar_name_says_x_exactly_when_several_versions_are_claimed(self):
        for target, supported in self.SCENARIOS:
            with self.subTest(target=target):
                booted = versions_to_test(target, supported)
                self.assertEqual(
                    mc_label(booted).endswith(".x"),
                    len(booted) > 1,
                    f"{target}/{supported} -> {booted}",
                )


class ModVersionTemplateTest(unittest.TestCase):
    """The format used to be hardcoded as "<mc>-<mod>", split on the first dash."""

    def test_the_default_format_round_trips(self):
        self.assertEqual(
            parse_mod_version("{mc}-{mod}", "26.1.x-1.1.0"),
            {"mc": "26.1.x", "mod": "1.1.0"},
        )
        self.assertEqual(render_mod_version("{mc}-{mod}", "26.2", "1.1.0"), "26.2-1.1.0")

    def test_only_the_last_placeholder_is_greedy(self):
        """A mod version may itself contain a dash; the mc label may not."""
        self.assertEqual(
            parse_mod_version("{mc}-{mod}", "26.1.x-1.1.0-beta.2"),
            {"mc": "26.1.x", "mod": "1.1.0-beta.2"},
        )

    def test_the_modrinth_style_format(self):
        self.assertEqual(
            parse_mod_version("{mod}+mc{mc}", "1.1.0+mc26.2"),
            {"mod": "1.1.0", "mc": "26.2"},
        )
        self.assertEqual(
            render_mod_version("{mod}+mc{mc}", "26.2", "1.1.0"), "1.1.0+mc26.2"
        )

    def test_a_format_without_mc_leaves_the_version_alone(self):
        self.assertEqual(update_mod_version("{mod}", "1.1.0", ["26.2"]), "1.1.0")

    def test_a_mismatching_version_is_an_error_not_a_warning(self):
        """The old behaviour warned and moved on, shipping a jar named after the
        wrong Minecraft version. Refusing is the point of the template."""
        with self.assertRaises(Failure) as caught:
            update_mod_version("{mc}-{mod}", "1.1.0", ["26.2"])
        self.assertIn("does not match", str(caught.exception))

    def test_a_format_without_mod_is_rejected(self):
        with self.assertRaises(Failure):
            render_mod_version("{mc}", "26.2", "1.1.0")

    def test_an_unknown_placeholder_is_rejected(self):
        with self.assertRaises(Failure):
            render_mod_version("{mc}-{mod}-{loader}", "26.2", "1.1.0")

    def test_an_empty_version_set_changes_nothing(self):
        self.assertEqual(update_mod_version("{mc}-{mod}", "26.1.x-1.1.0", []), "26.1.x-1.1.0")

    def test_the_mod_part_is_preserved_when_minecraft_moves(self):
        self.assertEqual(
            update_mod_version("{mc}-{mod}", "26.1.x-1.1.0", ["26.2"]), "26.2-1.1.0"
        )


if __name__ == "__main__":
    unittest.main()
