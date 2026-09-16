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
from unittest import mock

from lib import versions
from lib.common import Failure
from lib.versions import (
    channel_of,
    compat_bounds,
    latest_minecraft_version,
    same_minecraft_version,
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


class ChannelTest(unittest.TestCase):
    """Mojang types pre-releases, candidates and snapshots all as "snapshot"."""

    def test_both_id_eras_are_classified(self):
        cases = {
            "26.2": "release",
            "1.21.11": "release",
            "26.2-rc-1": "rc",
            "1.21.11-rc1": "rc",
            "26.2-pre-2": "pre",
            "1.21.11-pre3": "pre",
            "26.2-snapshot-1": "snapshot",
            "25w45a": "snapshot",
        }
        for version, channel in cases.items():
            self.assertEqual(channel_of(version), channel, version)


class BootedVersionTest(unittest.TestCase):
    """The server logs a version NAME, the matrix expects an id."""

    def test_a_candidate_boots_under_its_display_name(self):
        self.assertTrue(same_minecraft_version("26.3 Release Candidate 3", "26.3-rc-3"))
        self.assertTrue(same_minecraft_version("1.21.11 Release Candidate 1", "1.21.11-rc1"))
        self.assertTrue(same_minecraft_version("26.3 Pre-Release 1", "26.3-pre-1"))
        self.assertTrue(same_minecraft_version("26.3 Snapshot 10", "26.3-snapshot-10"))

    def test_a_different_version_is_still_caught(self):
        self.assertFalse(same_minecraft_version("26.1.1", "26.1"))
        self.assertFalse(same_minecraft_version("26.3 Release Candidate 2", "26.3-rc-3"))
        self.assertFalse(same_minecraft_version("26.3", "26.3-rc-3"))


class LatestVersionTest(unittest.TestCase):
    """What the auto-update targets, for a given minecraft.channels."""

    MANIFEST = {
        "latest": {"release": "26.1.2", "snapshot": "26.2-rc-1"},
        "versions": [
            {"id": "26.2-rc-1", "type": "snapshot", "releaseTime": "2026-09-10T10:00:00+00:00"},
            {"id": "26.2-pre-1", "type": "snapshot", "releaseTime": "2026-09-03T10:00:00+00:00"},
            {"id": "26.2-snapshot-4", "type": "snapshot", "releaseTime": "2026-08-20T10:00:00+00:00"},
            {"id": "26.1.2", "type": "release", "releaseTime": "2026-08-01T10:00:00+00:00"},
            {"id": "b1.7.3", "type": "old_beta", "releaseTime": "2026-09-30T10:00:00+00:00"},
        ],
    }

    def setUp(self):
        patcher = mock.patch.object(versions, "_manifest_cache", self.MANIFEST)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_releases_only_by_default(self):
        self.assertEqual(latest_minecraft_version(["release"]), "26.1.2")

    def test_the_newest_of_the_listed_channels_wins(self):
        self.assertEqual(latest_minecraft_version(["release", "rc"]), "26.2-rc-1")
        self.assertEqual(latest_minecraft_version(["release", "pre"]), "26.2-pre-1")

    def test_only_the_listed_channels_count(self):
        """No release in the list means no release, even a newer one."""
        self.assertEqual(latest_minecraft_version(["snapshot"]), "26.2-snapshot-4")

    def test_order_comes_from_the_release_time_not_the_manifest(self):
        shuffled = dict(self.MANIFEST, versions=list(reversed(self.MANIFEST["versions"])))
        with mock.patch.object(versions, "_manifest_cache", shuffled):
            self.assertEqual(latest_minecraft_version(["release", "rc", "pre"]), "26.2-rc-1")

    def test_old_alpha_and_beta_never_qualify(self):
        self.assertNotEqual(latest_minecraft_version(list(versions.CHANNELS)), "b1.7.3")

    def test_nothing_matching_is_an_error(self):
        empty = {"versions": [v for v in self.MANIFEST["versions"] if v["type"] == "release"]}
        with mock.patch.object(versions, "_manifest_cache", empty):
            with self.assertRaises(Failure):
                latest_minecraft_version(["rc"])


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

    def test_a_non_numeric_target_is_the_one_version_to_boot(self):
        """It used to be filtered out with the rest, leaving a matrix of nothing."""
        self.assertEqual(versions_to_test("26.2-rc-1", []), ["26.2-rc-1"])
        self.assertEqual(versions_to_test("26.2-snapshot-3", ["26.2-snapshot-2"]), ["26.2-snapshot-3"])

    def test_a_candidate_is_its_own_series(self):
        """So moving to the release resets what the candidate's jar claimed."""
        self.assertNotEqual(series_of("26.2-rc-1"), series_of("26.2"))


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

    def test_a_candidate_label_keeps_its_dashes(self):
        """The lazy split read "26.2-rc-1-1.1.0" as 26.2 and "rc-1-1.1.0"."""
        self.assertEqual(
            parse_mod_version("{mc}-{mod}", "26.2-rc-1-1.1.0"),
            {"mc": "26.2-rc-1", "mod": "1.1.0"},
        )
        self.assertEqual(
            parse_mod_version("{mc}-{mod}", "26.2-snapshot-3-1.1.0-beta.2"),
            {"mc": "26.2-snapshot-3", "mod": "1.1.0-beta.2"},
        )
        self.assertEqual(
            parse_mod_version("{mod}-{mc}", "1.1.0-beta-26.2-rc-1"),
            {"mod": "1.1.0-beta", "mc": "26.2-rc-1"},
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
