"""File rewrites over a temporary mod repository.

Ported from ExtendedTimePotion's test_update_mc_version.py: the scenarios are the
same, they just run through a real Project instead of monkeypatched globals.
"""

from __future__ import annotations

import json
import unittest

from lib import update
from lib.common import Failure

from .helpers import PROPERTIES, ModRepoTestCase, SILENT


class UpdateGradlePropertiesTest(ModRepoTestCase):
    def test_extending_a_series_labels_the_jar_with_x(self):
        applied = update.update_gradle_properties(
            self.project, "26.1.2", "1.17.18", 25, dry_run=False, log=SILENT
        )
        self.assertTrue(applied.changed)
        # untouched: nothing proven yet
        self.assertEqual(applied.supported, ["26.1", "26.1.1"])
        self.assertEqual(applied.mod_version, "26.1.x-1.1.0")

    def test_a_series_change_resets_supported_and_renames_the_jar(self):
        """A new series starts from nothing: empty list, exact name, pinned range.

        Note on what does NOT need guarding here: the label is computed after the
        reset, but it would come out identical computed before, because
        versions_to_test() filters on the target's series anyway. That filtering
        is the real invariant; the call ordering only makes it obvious.
        """
        applied = update.update_gradle_properties(
            self.project, "26.2", "1.17.18", 25, dry_run=False, log=SILENT
        )
        self.assertEqual(applied.supported, [])
        self.assertEqual(applied.mod_version, "26.2-1.1.0")
        self.assertEqual(self.prop("supported_minecraft_versions"), "")

    def test_the_frozen_dependencies_are_not_touched(self):
        update.update_gradle_properties(
            self.project, "26.2", "1.17.18", 25, dry_run=False, log=SILENT
        )
        self.assertEqual(self.prop("loader_version"), "0.19.3")
        self.assertEqual(self.prop("fabric_api_version"), "0.155.2+26.1.1")

    def test_the_build_plugin_follows(self):
        update.update_gradle_properties(
            self.project, "26.2", "1.18.0", 25, dry_run=False, log=SILENT
        )
        self.assertEqual(self.prop("loom_version"), "1.18.0")

    def test_dry_run_writes_nothing(self):
        before = self.properties_file.read_text(encoding="utf-8")
        update.update_gradle_properties(
            self.project, "26.2", "1.17.18", 25, dry_run=True, log=SILENT
        )
        self.assertEqual(self.properties_file.read_text(encoding="utf-8"), before)


class CompatRangeTest(ModRepoTestCase):
    """The bounds are generic; only their rendering belongs to the loader."""

    def test_fabric_renders_the_documented_table(self):
        self.assertEqual(update.compat_range(self.project, "26.1", []), "=26.1")
        self.assertEqual(
            update.compat_range(self.project, "26.1.1", ["26.1"]), ">=26.1 <=26.1.1"
        )
        self.assertEqual(
            update.compat_range(self.project, "26.1.2", ["26.1", "26.1.1"]),
            ">=26.1 <=26.1.2",
        )
        self.assertEqual(update.compat_range(self.project, "26.2", []), "=26.2")


class MarkSupportedTest(ModRepoTestCase):
    def test_it_extends_the_list_and_recomputes_everything_from_it(self):
        self.set_prop("minecraft_version", "26.1.2")
        version, supported, new_range, floors = update.mark_supported(
            self.project, False, SILENT
        )

        self.assertEqual(version, "26.1.2")
        self.assertEqual(supported, ["26.1", "26.1.1", "26.1.2"])
        self.assertEqual(new_range, ">=26.1 <=26.1.2")
        self.assertEqual(self.depends("minecraft"), ">=26.1 <=26.1.2")
        self.assertEqual(self.prop("mod_version"), "26.1.x-1.1.0")
        self.assertEqual(floors, {})

    def test_a_single_version_series_is_named_without_x(self):
        self.set_prop("supported_minecraft_versions", "")
        self.set_prop("minecraft_version", "26.2")
        _, supported, new_range, _ = update.mark_supported(self.project, False, SILENT)
        self.assertEqual(supported, ["26.2"])
        self.assertEqual(new_range, "=26.2")
        self.assertEqual(self.prop("mod_version"), "26.2-1.1.0")

    def test_a_floor_is_written_only_for_what_the_escalation_moved(self):
        update.save_update_state(self.project, "26.1.1", "26.1,26.1.1")
        # the ladder bumps the API, the loader stays where it was
        self.set_prop("fabric_api_version", "0.156.0+26.2")
        _, _, _, floors = update.mark_supported(self.project, False, SILENT)

        self.assertEqual(floors, {"fabric-api": ">=0.156.0+26.2"})
        self.assertEqual(self.depends("fabric-api"), ">=0.156.0+26.2")
        self.assertEqual(self.depends("fabricloader"), ">=0.18.4")  # untouched

    def test_without_an_escalation_the_api_keeps_its_wildcard(self):
        update.save_update_state(self.project, "26.1.1", "26.1,26.1.1")
        update.mark_supported(self.project, False, SILENT)
        self.assertEqual(self.depends("fabric-api"), "*")

    def test_proving_the_version_clears_the_snapshot(self):
        update.save_update_state(self.project, "26.1.1", "26.1,26.1.1")
        update.mark_supported(self.project, False, SILENT)
        self.assertFalse(self.project.paths.state.exists())


class RevertCompatTest(ModRepoTestCase):
    def _updated_then_escalated(self):
        """The state after an optimistic update plus an API escalation."""
        update.save_update_state(self.project, "26.1.2", "26.1,26.1.1")
        self.set_prop("minecraft_version", "26.1.2")
        self.set_prop("fabric_api_version", "0.156.0+26.2")
        self.set_prop("supported_minecraft_versions", "26.1,26.1.1,26.1.2")
        loader = self.project.loader
        loader.write_depends(self.project.paths, "minecraft", ">=26.1 <=26.1.2", False)
        loader.write_depends(self.project.paths, "fabric-api", ">=0.156.0+26.2", False)

    def test_claims_come_back_and_bumps_stay(self):
        self._updated_then_escalated()
        result = update.revert_compat(self.project, dry_run=False)

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
        update.revert_compat(self.project, dry_run=False)
        self.assertFalse(self.project.paths.state.exists())

    def test_it_refuses_a_snapshot_written_for_another_version(self):
        # gradle.properties says 26.1.1
        update.save_update_state(self.project, "26.2", "26.1,26.1.1")
        with self.assertRaises(Failure):
            update.revert_compat(self.project, dry_run=False)

    def test_it_refuses_when_there_is_nothing_to_revert(self):
        with self.assertRaises(Failure):
            update.revert_compat(self.project, dry_run=False)


class SaveUpdateStateTest(ModRepoTestCase):
    def test_a_snapshot_for_the_same_target_is_kept(self):
        """--force must not overwrite the pre-bump values with bumped ones."""
        update.save_update_state(self.project, "26.1.2", "26.1,26.1.1")
        self.set_prop("supported_minecraft_versions", "26.1,26.1.1,26.1.2")
        update.save_update_state(self.project, "26.1.2", "26.1,26.1.1,26.1.2")

        state = json.loads(self.project.paths.state.read_text(encoding="utf-8"))
        self.assertEqual(state["supported_minecraft_versions"], "26.1,26.1.1")

    def test_a_snapshot_for_another_target_is_replaced(self):
        update.save_update_state(self.project, "26.1.2", "26.1,26.1.1")
        update.save_update_state(self.project, "26.2", "26.1,26.1.1,26.1.2")
        state = json.loads(self.project.paths.state.read_text(encoding="utf-8"))
        self.assertEqual(state["target"], "26.2")

    def test_the_snapshot_is_keyed_by_role_not_by_fabric_names(self):
        """What makes it survive a second loader implementation."""
        update.save_update_state(self.project, "26.1.2", "26.1,26.1.1")
        state = json.loads(self.project.paths.state.read_text(encoding="utf-8"))
        self.assertEqual(sorted(state["frozen"]), ["api", "loader"])
        self.assertEqual(state["frozen"]["api"], "0.155.2+26.1.1")
        self.assertEqual(state["depends"]["api"], "*")


class ListTestVersionsTest(ModRepoTestCase):
    def test_it_lists_what_the_matrix_must_boot(self):
        self.assertEqual(update.list_test_versions(self.project), ["26.1", "26.1.1"])


class ModVersionFormatTest(ModRepoTestCase):
    """A repo whose mod_version disagrees with its declared format is refused."""

    CONFIG = ModRepoTestCase.CONFIG.replace(
        'format: "{mc}-{mod}"', 'format: "{mod}+mc{mc}"'
    )
    PROPERTIES = PROPERTIES.replace("mod_version=26.1.x-1.1.0", "mod_version=1.1.0+mc26.1.1")

    def test_the_configured_format_drives_the_rewrite(self):
        applied = update.update_gradle_properties(
            self.project, "26.2", "1.17.18", 25, dry_run=False, log=SILENT
        )
        self.assertEqual(applied.mod_version, "1.1.0+mc26.2")

    def test_a_version_in_the_wrong_format_is_refused(self):
        self.set_prop("mod_version", "26.1.x-1.1.0")
        with self.assertRaises(Failure) as caught:
            update.update_gradle_properties(
                self.project, "26.2", "1.17.18", 25, dry_run=False, log=SILENT
            )
        self.assertIn("version.format", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
