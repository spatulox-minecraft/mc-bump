"""The toolchain moves only when the target Minecraft version needs it."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from lib.common import Failure
from lib.gradle import gradle_version_key
from lib.loaders.base import BuildEnv
from lib.loaders.fabric import LOOM_METADATA, LOOM_MODULE, TOOLCHAIN_TABLE, FabricLoader
from lib.toolchain import (
    Requirements,
    Toolchain,
    buildtool_line,
    dump_table,
    gradle_from_wrapper,
    in_line,
    introducing_commits,
    load_table,
    loom_from_example,
    minecraft_from_example,
    plan_toolchain,
    required_for,
    satisfies,
    version_numbers,
)
from lib.versions import parse_version

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

    def test_a_known_release_wins_over_a_newer_unreadable_one(self):
        """fabric-loom-1.17.21.module missing: its Java and Gradle are unknown."""
        requirements = {k: v for k, v in REQUIREMENTS.items() if k != "1.17.21"}
        plan = plan_toolchain(
            minecraft="26.2", table=TABLE, current_buildtool="1.15.4", current_gradle="9.3.0",
            java=25, candidates=lambda: STABLE, requirements=requirements.get, label="fabric-loom",
        )
        self.assertEqual(plan.buildtool, "1.17.18")

    def test_an_unreadable_release_is_taken_when_no_known_one_runs(self):
        requirements = {
            "1.17.18": Requirements(gradle="9.5.0", java=25),
        }
        plan = plan_toolchain(
            minecraft="26.2", table=TABLE, current_buildtool="1.15.4", current_gradle="9.3.0",
            java=21, candidates=lambda: STABLE, requirements=requirements.get, label="fabric-loom",
        )
        self.assertEqual(plan.buildtool, "1.17.21")

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


def loom_module(gradle, java):
    return json.dumps({
        "variants": [
            {"name": "apiElements", "attributes": {"org.gradle.jvm.version": 8}},
            {"name": "runtimeElements", "attributes": {
                "org.gradle.plugin.api-version": gradle, "org.gradle.jvm.version": java,
            }},
        ]
    })


class FabricLoomTest(unittest.TestCase):
    """What FabricLoader reads from Fabric's Maven, without the network."""

    def setUp(self):
        self.documents = {
            LOOM_METADATA: "<metadata><versioning><versions>"
            + "".join(f"<version>{v}</version>" for v in [*STABLE, "1.18.0-alpha.4"])
            + "</versions></versioning></metadata>",
            **{
                LOOM_MODULE.format(v=version): loom_module(req.gradle, req.java)
                for version, req in REQUIREMENTS.items()
            },
        }
        self.fetched: list[str] = []
        patcher = mock.patch("lib.loaders.fabric.http_get", side_effect=self.http_get)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.loader = FabricLoader()

    def http_get(self, url, allow_status=()):
        self.fetched.append(url)
        return self.documents.get(url, "<html>404 Not Found</html>")

    def test_the_runtime_variant_says_what_loom_needs(self):
        self.assertEqual(
            self.loader._loom_requirements("1.18.1"), Requirements(gradle="9.7.0", java=25)
        )

    def test_a_missing_or_odd_module_is_unknown(self):
        self.assertIsNone(self.loader._loom_requirements("1.16.0"))
        self.documents[LOOM_MODULE.format(v="1.16.1")] = json.dumps({"variants": []})
        self.assertIsNone(self.loader._loom_requirements("1.16.1"))
        self.documents[LOOM_MODULE.format(v="1.16.2")] = loom_module("9.4.0", "21")
        self.assertEqual(self.loader._loom_requirements("1.16.2"), Requirements(gradle="9.4.0"))

    def test_each_module_is_read_once_per_run(self):
        """The Loom picked is asked for its Java, then for its Gradle."""
        plan = self.loader._toolchain(
            "26.2", BuildEnv(buildtool="1.15.4", gradle="9.3.0", java=25), None
        )
        self.assertEqual((plan.buildtool, plan.gradle), ("1.17.21", "9.5.1"))
        self.loader._toolchain("26.2", BuildEnv(buildtool="1.15.4", gradle="9.3.0", java=25), None)
        modules = [url for url in self.fetched if url.endswith(".module")]
        self.assertEqual(modules, [LOOM_MODULE.format(v="1.17.21")])


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

    def test_an_indented_property_is_read(self):
        """The 2021 example mod indented its properties with a tab."""
        properties = "\tminecraft_version=1.17\r\n\tloom_version=0.8-SNAPSHOT\r\n"
        self.assertEqual(minecraft_from_example(properties), "1.17")
        self.assertEqual(loom_from_example(properties, ""), "0.8")

    def test_an_empty_property_does_not_read_the_next_line(self):
        self.assertIsNone(minecraft_from_example("minecraft_version=\nloader_version=0.18.6\n"))


DEPLOYMENT = "Deployment from commit FabricMC/fabricmc.net@5059c39"


class IntroducingCommitsTest(unittest.TestCase):
    def test_the_first_commit_setting_a_version_wins(self):
        history = [("a", "1.21.1", "1.21.1"), ("b", "Update Loom", "1.21.1")]
        self.assertEqual(introducing_commits(history), {"1.21.1": "a"})

    def test_a_hand_made_commit_wins_over_an_older_generated_one(self):
        history = [("gen", DEPLOYMENT, "1.21.1"), ("old", "1.21.1", "1.21.1")]
        self.assertEqual(introducing_commits(history), {"1.21.1": "old"})

    def test_the_generator_backfilling_an_old_version_is_left_out(self):
        """1.16.5 regenerated in 2026 does not need Loom 1.15."""
        history = [("old", "1.21.1", "1.21.1"), ("gen", DEPLOYMENT, "1.16.5")]
        self.assertEqual(introducing_commits(history), {"1.21.1": "old"})

    def test_a_version_only_the_generator_published_is_kept_when_newer(self):
        """Since 26.1 Fabric publishes new versions through the generator only."""
        history = [("old", "26.1", "26.1"), ("gen", DEPLOYMENT, "26.2")]
        self.assertEqual(introducing_commits(history), {"26.1": "old", "26.2": "gen"})

    def test_snapshots_and_unreadable_commits_are_ignored(self):
        history = [("a", "19w03c", "19w03c"), ("b", "no properties", None)]
        self.assertEqual(introducing_commits(history), {})


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

    def test_a_newer_minecraft_never_needs_an_older_toolchain(self):
        """What required_for() assumes. Run before the weekly refresh moves a tag,
        so a table read from regenerated branches (1.16.5 needing Loom 1.15 while
        1.17 needs 0.8) is never released."""
        table = load_table(TOOLCHAIN_TABLE)
        ordered = sorted(table, key=parse_version)
        for older, newer in zip(ordered, ordered[1:]):
            with self.subTest(older=older, newer=newer):
                self.assertLessEqual(
                    version_numbers(table[older].buildtool), version_numbers(table[newer].buildtool)
                )
                self.assertLessEqual(
                    gradle_version_key(table[older].gradle), gradle_version_key(table[newer].gradle)
                )


if __name__ == "__main__":
    unittest.main()
