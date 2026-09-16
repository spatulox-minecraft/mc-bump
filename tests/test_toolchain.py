"""The toolchain moves only when the target Minecraft version needs it."""

from __future__ import annotations

import json
import unittest

from lib.common import Failure
from lib.loaders.fabric import TOOLCHAIN_TABLE
from lib.toolchain import (
    Requirements,
    Toolchain,
    buildtool_line,
    dump_table,
    gradle_from_wrapper,
    in_line,
    load_table,
    loom_from_example,
    plan_toolchain,
    required_for,
    satisfies,
)

TABLE = {
    "1.21": Toolchain("1.6", "8.7"),
    "26.1": Toolchain("1.15", "9.3.0"),
    "26.2": Toolchain("1.17", "9.5.1"),
}

# What fabric-loom-<v>.module declares.
REQUIREMENTS = {
    "1.15.4": Requirements(gradle="9.3.0", java=21),
    "1.17.18": Requirements(gradle="9.5.0", java=21),
    "1.17.21": Requirements(gradle="9.5.0", java=21),
    "1.18.1": Requirements(gradle="9.7.0", java=25),
}
STABLE = ["1.15.4", "1.17.18", "1.18.1", "1.17.21"]


class RequiredForTest(unittest.TestCase):
    def test_an_exact_entry(self):
        self.assertEqual(required_for(TABLE, "26.2"), ("26.2", TABLE["26.2"]))

    def test_a_candidate_is_governed_by_its_release(self):
        self.assertEqual(required_for(TABLE, "26.2-rc-1")[0], "26.2")
        self.assertEqual(required_for(TABLE, "26.2-snapshot-3")[0], "26.2")

    def test_a_version_not_listed_yet_takes_the_closest_below(self):
        """26.3 builds with at least what 26.2 needed, until the refresh lists it."""
        self.assertEqual(required_for(TABLE, "26.3")[0], "26.2")
        self.assertEqual(required_for(TABLE, "26.3-rc-3")[0], "26.2")
        self.assertEqual(required_for(TABLE, "26.1.2")[0], "26.1")

    def test_a_weekly_snapshot_takes_the_newest(self):
        self.assertEqual(required_for(TABLE, "25w45a")[0], "26.2")

    def test_older_than_the_table_or_an_empty_table(self):
        self.assertIsNone(required_for(TABLE, "1.20.1"))
        self.assertIsNone(required_for({}, "26.2"))


class LineTest(unittest.TestCase):
    def test_a_release_of_the_line_or_newer_satisfies_it(self):
        self.assertTrue(satisfies("1.17.18", "1.17"))
        self.assertTrue(satisfies("1.18.1", "1.17"))
        self.assertTrue(satisfies("1.17-SNAPSHOT", "1.17"))
        self.assertFalse(satisfies("1.15.4", "1.17"))

    def test_an_exact_requirement(self):
        self.assertTrue(satisfies("0.12.12", "0.12.12"))
        self.assertFalse(satisfies("0.12.5", "0.12.12"))

    def test_in_line_stays_in_the_line(self):
        self.assertTrue(in_line("1.17.21", "1.17"))
        self.assertFalse(in_line("1.18.1", "1.17"))
        self.assertFalse(in_line("0.12.5", "0.12.12"))


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.looked_up: list[str] = []

    def requirements(self, version):
        self.looked_up.append(version)
        return REQUIREMENTS.get(version)

    def plan(self, minecraft, loom, gradle, java=25, pinned=None, stable=STABLE):
        return plan_toolchain(
            minecraft=minecraft,
            table=TABLE,
            current_buildtool=loom,
            current_gradle=gradle,
            java=java,
            candidates=lambda: stable,
            requirements=self.requirements,
            label="fabric-loom",
            pinned=pinned,
        )

    def test_nothing_moves_when_the_mod_meets_the_target(self):
        """The testmod: Loom 1.17.18 and Gradle 9.5.1 on 26.2."""
        plan = self.plan("26.2", "1.17.18", "9.5.1")
        self.assertEqual((plan.buildtool, plan.gradle), ("1.17.18", "9.5.1"))
        self.assertIn("unchanged", plan.buildtool_note)
        self.assertIn("unchanged", plan.gradle_note)
        self.assertEqual(self.looked_up, [], "nothing to move, nothing to look up")

    def test_a_newer_toolchain_is_never_moved_down(self):
        plan = self.plan("26.2", "1.18.1", "9.7.0")
        self.assertEqual((plan.buildtool, plan.gradle), ("1.18.1", "9.7.0"))

    def test_loom_and_gradle_move_together_when_the_target_needs_it(self):
        plan = self.plan("26.2", "1.15.4", "9.3.0")
        self.assertEqual(plan.buildtool, "1.17.21", "the newest of the line, not 1.18")
        self.assertEqual(plan.gradle, "9.5.1")
        self.assertIn("26.2 needs 1.17", plan.buildtool_note)
        self.assertEqual(plan.gradle_note, "9.3.0 -> 9.5.1, Minecraft 26.2 needs Gradle 9.5.1")

    def test_only_the_plugin_moves_when_the_wrapper_is_already_enough(self):
        plan = self.plan("26.2", "1.15.4", "9.6.0")
        self.assertEqual((plan.buildtool, plan.gradle), ("1.17.21", "9.6.0"))

    def test_the_wrapper_follows_what_the_plugin_declares_above_the_table(self):
        requirements = dict(REQUIREMENTS, **{"1.17.21": Requirements(gradle="9.6.0", java=21)})
        plan = plan_toolchain(
            minecraft="26.2", table=TABLE, current_buildtool="1.15.4", current_gradle="9.3.0",
            java=25, candidates=lambda: STABLE, requirements=requirements.get, label="fabric-loom",
        )
        self.assertEqual(plan.gradle, "9.6.0")
        self.assertIn("fabric-loom 1.17.21 needs Gradle 9.6.0", plan.gradle_note)

    def test_a_release_the_java_cannot_run_is_skipped_within_the_line(self):
        requirements = dict(REQUIREMENTS, **{"1.17.21": Requirements(gradle="9.5.0", java=25)})
        plan = plan_toolchain(
            minecraft="26.2", table=TABLE, current_buildtool="1.15.4", current_gradle="9.3.0",
            java=21, candidates=lambda: STABLE, requirements=requirements.get, label="fabric-loom",
        )
        self.assertEqual(plan.buildtool, "1.17.18")

    def test_no_release_of_the_line_is_an_error(self):
        with self.assertRaises(Failure) as caught:
            self.plan("26.2", "1.15.4", "9.3.0", stable=["1.15.4", "1.18.1"])
        self.assertIn("1.17", str(caught.exception))

    def test_a_version_older_than_the_table_moves_nothing(self):
        plan = self.plan("1.20.1", "1.3.9", "8.1.1")
        self.assertEqual((plan.buildtool, plan.gradle), ("1.3.9", "8.1.1"))
        self.assertIn("no toolchain known", plan.buildtool_note)

    def test_no_wrapper_is_reported_not_created(self):
        plan = self.plan("26.2", "1.15.4", None)
        self.assertIsNone(plan.gradle)
        self.assertIn("no Gradle wrapper", plan.gradle_note)

    def test_a_pin_is_kept_and_the_wrapper_follows_it(self):
        plan = self.plan("26.2", "1.17.18", "9.5.1", pinned="1.18.1")
        self.assertEqual((plan.buildtool, plan.gradle), ("1.18.1", "9.7.0"))
        self.assertEqual(plan.buildtool_note, "pinned")

    def test_a_pin_the_java_cannot_run_is_refused(self):
        with self.assertRaises(Failure) as caught:
            self.plan("26.2", "1.17.18", "9.5.1", java=21, pinned="1.18.1")
        self.assertIn("Java >= 25", str(caught.exception))


class ExampleModParsingTest(unittest.TestCase):
    def test_loom_from_gradle_properties(self):
        self.assertEqual(loom_from_example("loom_version=1.17-SNAPSHOT\n", ""), "1.17")

    def test_loom_from_an_older_build_script(self):
        groovy = "plugins {\n\tid 'fabric-loom' version '1.2-SNAPSHOT'\n}"
        kotlin = 'plugins {\n    id("net.fabricmc.fabric-loom") version "0.12.12"\n}'
        self.assertEqual(loom_from_example("", groovy), "1.2")
        self.assertEqual(loom_from_example("", kotlin), "0.12.12")

    def test_a_property_placeholder_is_not_a_version(self):
        self.assertIsNone(loom_from_example("", 'id "fabric-loom" version "${loom_version}"'))

    def test_buildtool_line(self):
        self.assertEqual(buildtool_line("1.17-SNAPSHOT"), "1.17")
        self.assertEqual(buildtool_line("1.17.21"), "1.17.21")

    def test_gradle_from_wrapper(self):
        self.assertEqual(
            gradle_from_wrapper("distributionUrl=https\\://services.gradle.org/distributions/gradle-9.5.1-bin.zip\n"),
            "9.5.1",
        )


class ShippedTableTest(unittest.TestCase):
    """The file every update reads. A broken one would stop all toolchain moves."""

    def test_it_loads_and_round_trips(self):
        table = load_table(TOOLCHAIN_TABLE)
        self.assertIn("26.2", table)
        source = json.loads(TOOLCHAIN_TABLE.read_text(encoding="utf-8"))["source"]
        self.assertEqual(dump_table(table, source), TOOLCHAIN_TABLE.read_text(encoding="utf-8"))

    def test_every_entry_is_readable(self):
        for minecraft, toolchain in load_table(TOOLCHAIN_TABLE).items():
            with self.subTest(minecraft=minecraft):
                self.assertTrue(toolchain.buildtool[0].isdigit())
                self.assertTrue(toolchain.gradle[0].isdigit())


if __name__ == "__main__":
    unittest.main()
