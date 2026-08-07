#!/usr/bin/env python3
"""Unit tests for scripts/update-mc-version.py.

    python3 -m unittest discover -s scripts

Standard library only, and no network: everything tested here is either a pure
function or a file rewrite over a temporary workspace. The resolvers that call
Mojang, Fabric and Modrinth are deliberately NOT tested — they describe the shape
of upstream APIs, which no local assertion can pin down.

What is tested is the decision layer, and it is worth testing for one reason: a
mistake there does not crash. It produces a green matrix, a jar that a player's
loader refuses, or a store page announcing a version nobody ever booted. Those
functions also run once a week at best, at the end of a forty minute pipeline,
and only for the single scenario reality happens to present that day; the series
reset path is exercised the day Mojang ships a new series, not before.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "update-mc-version.py"
_spec = importlib.util.spec_from_file_location("update_mc_version", MODULE_PATH)
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)

SILENT = lambda *_args, **_kwargs: None  # noqa: E731 - the module's log callback


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------
class SeriesTest(unittest.TestCase):
    def test_series_is_the_first_two_components(self):
        self.assertEqual(mc.series_of("26.1.2"), "26.1")
        self.assertEqual(mc.series_of("26.2"), "26.2")
        self.assertEqual(mc.series_of("1.21.10"), "1.21")

    def test_parse_version_orders_numerically_not_lexically(self):
        self.assertEqual(mc.parse_version("26.1.2"), (26, 1, 2))
        # the whole point: "1.21.10" must sort above "1.21.9"
        self.assertGreater(mc.parse_version("1.21.10"), mc.parse_version("1.21.9"))

    def test_parse_version_rejects_anything_not_purely_numeric(self):
        for version in ("24w14a", "26.2-rc1", "1.21-pre1", ""):
            self.assertIsNone(mc.parse_version(version), version)


class McLabelTest(unittest.TestCase):
    """The label is the published version number, burnt on first upload."""

    def test_a_single_version_is_named_exactly(self):
        self.assertEqual(mc.mc_label(["26.2"]), "26.2")

    def test_several_sub_versions_collapse_to_the_series(self):
        self.assertEqual(mc.mc_label(["26.1", "26.1.1"]), "26.1.x")
        self.assertEqual(mc.mc_label(["26.1", "26.1.1", "26.1.2"]), "26.1.x")
        self.assertEqual(mc.mc_label(["1.21", "1.21.1", "1.21.10"]), "1.21.x")

    def test_input_order_does_not_matter(self):
        self.assertEqual(mc.mc_label(["26.1.2", "26.1"]), "26.1.x")

    def test_the_wildcard_never_climbs_to_the_series_level(self):
        """"26.x" would promise 26.3, which nothing ever built or booted."""
        for versions in (["26.1", "26.1.1"], ["26.1", "26.2"], ["26.1", "26.1.1", "26.2"]):
            label = mc.mc_label(versions)
            self.assertNotEqual(label, "26.x", versions)
            self.assertTrue(label.startswith("26.1"), f"{versions} -> {label}")

    def test_no_version_yields_no_label(self):
        self.assertEqual(mc.mc_label([]), "")
        self.assertEqual(mc.mc_label([""]), "")


class VersionsToTestTest(unittest.TestCase):
    """What the matrix boots. Returning too little announces an untested version."""

    def test_target_is_included_even_when_not_supported_yet(self):
        self.assertEqual(mc.versions_to_test("26.1.2", ["26.1"]), ["26.1", "26.1.2"])

    def test_oldest_first_and_deduplicated(self):
        self.assertEqual(
            mc.versions_to_test("26.1.2", ["26.1.2", "26.1", "26.1.1", "26.1"]),
            ["26.1", "26.1.1", "26.1.2"],
        )

    def test_other_series_are_dropped(self):
        self.assertEqual(mc.versions_to_test("26.2", ["26.1", "26.1.1"]), ["26.2"])

    def test_non_numeric_entries_are_dropped(self):
        self.assertEqual(mc.versions_to_test("26.1.1", ["26.1", "24w14a"]), ["26.1", "26.1.1"])


class CompatRangeTest(unittest.TestCase):
    """depends.minecraft: what Fabric Loader accepts or refuses at runtime."""

    def test_the_documented_table(self):
        self.assertEqual(mc.compat_range("26.1", []), "=26.1")
        self.assertEqual(mc.compat_range("26.1.1", ["26.1"]), ">=26.1 <=26.1.1")
        self.assertEqual(mc.compat_range("26.1.2", ["26.1", "26.1.1"]), ">=26.1 <=26.1.2")
        # series reset: supported has been emptied by the update
        self.assertEqual(mc.compat_range("26.2", []), "=26.2")

    def test_other_series_do_not_widen_the_range(self):
        self.assertEqual(mc.compat_range("26.2", ["26.1", "26.1.1"]), "=26.2")

    def test_a_non_numeric_target_is_pinned_exactly(self):
        self.assertEqual(mc.compat_range("24w14a", []), "=24w14a")

    def test_the_upper_bound_is_the_highest_not_the_last_listed(self):
        self.assertEqual(
            mc.compat_range("1.21.9", ["1.21.10", "1.21"]), ">=1.21 <=1.21.10"
        )


class CoherenceTest(unittest.TestCase):
    """The three derivations must agree; they are read by three different files.

    supported_minecraft_versions, depends.minecraft and mod_version are written
    to gradle.properties, fabric.mod.json and the jar name respectively. Nothing
    at runtime compares them, so a divergence is silent until a player hits it.
    """

    SCENARIOS = (
        ("26.1", []),
        ("26.1.1", ["26.1"]),
        ("26.1.2", ["26.1", "26.1.1"]),
        ("26.2", []),
        ("26.2.1", ["26.2"]),
    )

    def test_range_upper_bound_is_the_last_version_actually_booted(self):
        for target, supported in self.SCENARIOS:
            with self.subTest(target=target):
                booted = mc.versions_to_test(target, supported)
                compat = mc.compat_range(target, supported)
                self.assertTrue(compat.endswith(booted[-1]), f"{compat} vs {booted}")

    def test_label_and_range_describe_the_same_series(self):
        for target, supported in self.SCENARIOS:
            with self.subTest(target=target):
                booted = mc.versions_to_test(target, supported)
                label = mc.mc_label(booted)
                self.assertTrue(
                    label.startswith(mc.series_of(target)),
                    f"{label} is not in series {mc.series_of(target)}",
                )

    def test_the_jar_name_says_x_exactly_when_several_versions_are_claimed(self):
        for target, supported in self.SCENARIOS:
            with self.subTest(target=target):
                booted = mc.versions_to_test(target, supported)
                self.assertEqual(
                    mc.mc_label(booted).endswith(".x"),
                    len(booted) > 1,
                    f"{target}/{supported} -> {booted}",
                )


# ---------------------------------------------------------------------------
# File rewrites, over a temporary workspace
# ---------------------------------------------------------------------------
PROPERTIES = """\
minecraft_version=26.1.1
loader_version=0.19.3
loom_version=1.17.18
java_version=25
mod_version=26.1.x-1.1.0
fabric_api_version=0.155.2+26.1.1
supported_minecraft_versions=26.1,26.1.1
"""

MOD_JSON = {
    "schemaVersion": 1,
    "id": "extended-time-potion",
    "depends": {
        "fabricloader": ">=0.18.4",
        "minecraft": ">=26.1 <=26.1.1",
        "java": ">=25",
        "fabric-api": "*",
    },
}

MIXINS_JSON = {"required": True, "compatibilityLevel": "JAVA_25"}


class WorkspaceTestCase(unittest.TestCase):
    """Redirects the module's file constants onto a throwaway directory."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.properties = root / "gradle.properties"
        self.mod_json = root / "fabric.mod.json"
        self.mixins = root / "mixins.json"
        self.state = root / ".mc-update-state.json"

        self.properties.write_text(PROPERTIES, encoding="utf-8")
        self.mod_json.write_text(json.dumps(MOD_JSON, indent="\t"), encoding="utf-8")
        self.mixins.write_text(json.dumps(MIXINS_JSON, indent="\t"), encoding="utf-8")

        self._saved = (
            mc.GRADLE_PROPERTIES,
            mc.FABRIC_MOD_JSON,
            mc.MIXINS_JSON,
            mc.UPDATE_STATE,
        )
        mc.GRADLE_PROPERTIES = self.properties
        mc.FABRIC_MOD_JSON = self.mod_json
        mc.MIXINS_JSON = self.mixins
        mc.UPDATE_STATE = self.state

    def tearDown(self):
        (
            mc.GRADLE_PROPERTIES,
            mc.FABRIC_MOD_JSON,
            mc.MIXINS_JSON,
            mc.UPDATE_STATE,
        ) = self._saved
        self._tmp.cleanup()

    # -- helpers
    def prop(self, key):
        return mc.read_property(self.properties.read_text(encoding="utf-8"), key)

    def depends(self, key):
        return json.loads(self.mod_json.read_text(encoding="utf-8"))["depends"][key]


class UpdateModVersionTest(WorkspaceTestCase):
    def test_the_mod_part_is_preserved(self):
        text = mc.update_mod_version(PROPERTIES, ["26.2"], SILENT)
        self.assertEqual(mc.read_property(text, "mod_version"), "26.2-1.1.0")

    def test_a_mod_version_without_separator_is_left_alone(self):
        text = mc.update_mod_version("mod_version=1.1.0\n", ["26.2"], SILENT)
        self.assertEqual(mc.read_property(text, "mod_version"), "1.1.0")

    def test_an_empty_version_set_changes_nothing(self):
        text = mc.update_mod_version(PROPERTIES, [], SILENT)
        self.assertEqual(mc.read_property(text, "mod_version"), "26.1.x-1.1.0")


class UpdateGradlePropertiesTest(WorkspaceTestCase):
    def test_extending_a_series_labels_the_jar_with_x(self):
        changed, supported, mod_version = mc.update_gradle_properties(
            "26.1.2", "1.17.18", 25, dry_run=False, log=SILENT
        )
        self.assertTrue(changed)
        self.assertEqual(supported, ["26.1", "26.1.1"])  # untouched: nothing proven yet
        self.assertEqual(mod_version, "26.1.x-1.1.0")

    def test_a_series_change_resets_supported_and_renames_the_jar(self):
        """A new series starts from nothing: empty list, exact name, pinned range.

        Note on what does NOT need guarding here: the label is computed after the
        reset, but it would come out identical computed before, because
        versions_to_test() filters on the target's series anyway (see
        VersionsToTestTest.test_other_series_are_dropped). That filtering is the
        real invariant; the call ordering only makes it obvious.
        """
        _, supported, mod_version = mc.update_gradle_properties(
            "26.2", "1.17.18", 25, dry_run=False, log=SILENT
        )
        self.assertEqual(supported, [])
        self.assertEqual(mod_version, "26.2-1.1.0")
        self.assertEqual(self.prop("supported_minecraft_versions"), "")

    def test_the_frozen_dependencies_are_not_touched(self):
        mc.update_gradle_properties("26.2", "1.17.18", 25, dry_run=False, log=SILENT)
        self.assertEqual(self.prop("loader_version"), "0.19.3")
        self.assertEqual(self.prop("fabric_api_version"), "0.155.2+26.1.1")

    def test_dry_run_writes_nothing(self):
        before = self.properties.read_text(encoding="utf-8")
        mc.update_gradle_properties("26.2", "1.17.18", 25, dry_run=True, log=SILENT)
        self.assertEqual(self.properties.read_text(encoding="utf-8"), before)


class MarkSupportedTest(WorkspaceTestCase):
    def test_it_extends_the_list_and_recomputes_everything_from_it(self):
        self.properties.write_text(
            PROPERTIES.replace("minecraft_version=26.1.1", "minecraft_version=26.1.2"),
            encoding="utf-8",
        )
        version, supported, new_range, floors = mc.mark_supported(False, SILENT)

        self.assertEqual(version, "26.1.2")
        self.assertEqual(supported, ["26.1", "26.1.1", "26.1.2"])
        self.assertEqual(new_range, ">=26.1 <=26.1.2")
        self.assertEqual(self.depends("minecraft"), ">=26.1 <=26.1.2")
        self.assertEqual(self.prop("mod_version"), "26.1.x-1.1.0")
        self.assertEqual(floors, {})

    def test_a_single_version_series_is_named_without_x(self):
        self.properties.write_text(
            PROPERTIES.replace(
                "supported_minecraft_versions=26.1,26.1.1",
                "supported_minecraft_versions=",
            ).replace("minecraft_version=26.1.1", "minecraft_version=26.2"),
            encoding="utf-8",
        )
        _, supported, new_range, _ = mc.mark_supported(False, SILENT)
        self.assertEqual(supported, ["26.2"])
        self.assertEqual(new_range, "=26.2")
        self.assertEqual(self.prop("mod_version"), "26.2-1.1.0")

    def test_a_floor_is_written_only_for_what_the_escalation_moved(self):
        mc.save_update_state("26.1.1", "26.1,26.1.1")
        # the ladder bumps Fabric API, the loader stays where it was
        self.properties.write_text(
            self.properties.read_text(encoding="utf-8").replace(
                "fabric_api_version=0.155.2+26.1.1", "fabric_api_version=0.156.0+26.2"
            ),
            encoding="utf-8",
        )
        _, _, _, floors = mc.mark_supported(False, SILENT)

        self.assertEqual(floors, {"fabric-api": ">=0.156.0+26.2"})
        self.assertEqual(self.depends("fabric-api"), ">=0.156.0+26.2")
        self.assertEqual(self.depends("fabricloader"), ">=0.18.4")  # untouched

    def test_without_an_escalation_fabric_api_keeps_its_wildcard(self):
        mc.save_update_state("26.1.1", "26.1,26.1.1")
        mc.mark_supported(False, SILENT)
        self.assertEqual(self.depends("fabric-api"), "*")

    def test_proving_the_version_clears_the_snapshot(self):
        mc.save_update_state("26.1.1", "26.1,26.1.1")
        mc.mark_supported(False, SILENT)
        self.assertFalse(self.state.exists())


class RevertCompatTest(WorkspaceTestCase):
    def _updated_then_escalated(self):
        """The state after an optimistic update plus a Fabric API escalation."""
        mc.save_update_state("26.1.2", "26.1,26.1.1")
        text = self.properties.read_text(encoding="utf-8")
        text = mc.set_property(text, "minecraft_version", "26.1.2")
        text = mc.set_property(text, "fabric_api_version", "0.156.0+26.2")
        text = mc.set_property(text, "supported_minecraft_versions", "26.1,26.1.1,26.1.2")
        text = mc.set_property(text, "mod_version", "26.1.x-1.1.0")
        self.properties.write_text(text, encoding="utf-8")
        mc.write_depends("minecraft", ">=26.1 <=26.1.2", False)
        mc.write_depends("fabric-api", ">=0.156.0+26.2", False)

    def test_claims_come_back_and_bumps_stay(self):
        self._updated_then_escalated()
        result = mc.revert_compat(dry_run=False)

        # restored
        self.assertEqual(self.prop("supported_minecraft_versions"), "26.1,26.1.1")
        self.assertEqual(self.depends("minecraft"), ">=26.1 <=26.1.1")
        self.assertEqual(self.depends("fabric-api"), "*")
        self.assertEqual(result["minecraft_range"], ">=26.1 <=26.1.1")

        # kept: this is the dependency diff a human picks up from
        self.assertEqual(self.prop("minecraft_version"), "26.1.2")
        self.assertEqual(self.prop("fabric_api_version"), "0.156.0+26.2")

    def test_the_snapshot_is_consumed(self):
        self._updated_then_escalated()
        mc.revert_compat(dry_run=False)
        self.assertFalse(self.state.exists())

    def test_it_refuses_a_snapshot_written_for_another_version(self):
        mc.save_update_state("26.2", "26.1,26.1.1")  # gradle.properties says 26.1.1
        with self.assertRaises(mc.Failure):
            mc.revert_compat(dry_run=False)

    def test_it_refuses_when_there_is_nothing_to_revert(self):
        with self.assertRaises(mc.Failure):
            mc.revert_compat(dry_run=False)


class SaveUpdateStateTest(WorkspaceTestCase):
    def test_a_snapshot_for_the_same_target_is_kept(self):
        """--force must not overwrite the pre-bump values with bumped ones."""
        mc.save_update_state("26.1.2", "26.1,26.1.1")
        self.properties.write_text(
            mc.set_property(
                self.properties.read_text(encoding="utf-8"),
                "supported_minecraft_versions",
                "26.1,26.1.1,26.1.2",
            ),
            encoding="utf-8",
        )
        mc.save_update_state("26.1.2", "26.1,26.1.1,26.1.2")

        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["supported_minecraft_versions"], "26.1,26.1.1")

    def test_a_snapshot_for_another_target_is_replaced(self):
        mc.save_update_state("26.1.2", "26.1,26.1.1")
        mc.save_update_state("26.2", "26.1,26.1.1,26.1.2")
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["target"], "26.2")


if __name__ == "__main__":
    unittest.main()
