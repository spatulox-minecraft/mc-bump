"""The config is the whole reusability claim, so its failure modes are tested.

A config that is wrong in a subtle way does not crash: it produces a green
pipeline testing the wrong thing. Every assertion here is about refusing early
and by name.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from lib import config as config_module
from lib.common import Failure

from .helpers import CONFIG, ModRepoTestCase, write_mod


class LoadTest(ModRepoTestCase):
    def test_defaults_are_applied_for_everything_left_out(self):
        raw = self.project.raw
        self.assertEqual(raw["version"]["tag"], "v{version}")
        self.assertEqual(raw["tests"]["server"]["boot-timeout"], 900)
        self.assertEqual(raw["tests"]["unit"]["source"], "src/test/java")
        self.assertEqual(raw["release"]["stores"], ["modrinth", "curseforge"])
        self.assertTrue(raw["workflows"]["ci"])
        self.assertFalse(raw["workflows"]["gametest"]["enabled"])

    def test_paths_are_resolved_under_the_mod_root(self):
        paths = self.project.paths
        self.assertEqual(paths.root, self.root)
        self.assertTrue(paths.metadata.is_file())
        self.assertTrue(paths.mixins.is_file())
        paths.require()

    def test_the_loader_is_instantiated_from_its_name(self):
        self.assertEqual(self.project.loader.name, "fabric")

    def test_tag_uses_the_configured_template(self):
        self.assertEqual(self.project.tag_for("26.2-1.1.0"), "v26.2-1.1.0")


class FindRootTest(unittest.TestCase):
    def test_it_walks_up_from_a_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_mod(root)
            deep = root / "src/main/java/com/spatulox"
            deep.mkdir(parents=True, exist_ok=True)
            self.assertEqual(config_module.find_root(deep), root.resolve())

    def test_it_says_so_when_there_is_no_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Failure) as caught:
                config_module.find_root(Path(tmp))
            self.assertIn(config_module.CONFIG_PATH, str(caught.exception))


class ValidationTest(unittest.TestCase):
    def _load(self, config: str):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        write_mod(root, config=config)
        return config_module.load(root)

    def _rejects(self, config: str, *expected_in_message: str):
        with self.assertRaises(Failure) as caught:
            self._load(config)
        message = str(caught.exception)
        for fragment in expected_in_message:
            self.assertIn(fragment, message)
        return message

    def test_a_missing_required_key_names_itself(self):
        self._rejects(CONFIG.replace("  id: extended-time-potion\n", ""), "mod.id")

    def test_an_unknown_key_names_itself_and_lists_the_known_ones(self):
        self._rejects(CONFIG + "\nunknown_section:\n  a: 1\n", "unknown_section")

    def test_an_unknown_nested_key_is_rejected_too(self):
        """A typo in a nested key used to be silently ignored."""
        self._rejects(
            CONFIG.replace("notify:\n  assignee:", "notify:\n  assigne:"), "notify.assigne"
        )

    def test_an_unknown_loader_lists_the_known_ones(self):
        self._rejects(CONFIG.replace("loader: fabric", "loader: forge"), "fabric")

    def test_a_string_where_a_boolean_belongs(self):
        self._rejects(
            CONFIG + "\nworkflows:\n  ci: 'yes'\n", "workflows.ci", "true or false"
        )

    def test_a_negative_timeout(self):
        self._rejects(
            CONFIG.replace("  server:\n", "  server:\n    boot-timeout: -5\n"),
            "boot-timeout",
            "positive",
        )

    def test_an_expect_record_missing_its_pattern(self):
        self._rejects(
            CONFIG.replace(
                '      - pattern: "Brewing mixes registered"\n'
                '        message: "the brewing callback never ran"\n',
                '      - message: "no pattern here"\n',
            ),
            "pattern",
            "required",
        )

    def test_an_unknown_key_inside_an_expect_record(self):
        self._rejects(
            CONFIG.replace(
                '        message: "the brewing callback never ran"',
                '        mesage: "typo"',
            ),
            "mesage",
        )

    def test_an_unknown_store(self):
        self._rejects(
            CONFIG + "\nrelease:\n  stores: [modrinth, itch]\n", "itch", "modrinth"
        )

    def test_an_invalid_mod_id_is_caught_at_load_time(self):
        """The id becomes a grep pattern; a bad one makes the server test prove
        nothing rather than fail loudly."""
        self._rejects(CONFIG.replace("id: extended-time-potion", "id: Extended Time"), "mod.id")


class ExportTest(ModRepoTestCase):
    def test_bash_really_evaluates_the_exports(self):
        """The contract is `eval`, so the test evals.

        Patterns are full of quotes, brackets and backslashes; asserting on the
        rendered string would prove nothing about what a shell does with it.
        """
        script = (
            f"{config_module.export_shell(self.project)}\n"
            'printf "%s\\n" "$MCBUMP_MOD_ID" "$MCBUMP_MOD_LOADED_PATTERN" '
            '"$MCBUMP_FATAL_PATTERNS" "$MCBUMP_TESTS_SERVER_EXPECT"\n'
        )
        out = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=True
        ).stdout.splitlines()

        self.assertEqual(out[0], "extended-time-potion")
        self.assertIn("extended-time-potion", out[1])
        self.assertIn("Mixin apply failed", out[2])
        self.assertEqual(
            out[3], "Brewing mixes registered\tthe brewing callback never ran"
        )

    def test_a_shell_loop_reads_the_records_back(self):
        """Exactly the loop headless-server-test.sh runs."""
        script = (
            f"{config_module.export_shell(self.project)}\n"
            'while IFS=$\'\\t\' read -r pattern source count message; do\n'
            '  printf "[%s][%s][%s][%s]\\n" "$pattern" "$source" "$count" "$message"\n'
            "done <<< \"$MCBUMP_TESTS_SERVER_EXPECT_COUNT\"\n"
        )
        out = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual(
            out,
            "[Registered ([0-9]+) potions]"
            "[src/main/java/com/spatulox/ExtendedTimePotion.java]"
            "[= registerPotion(]"
            "[potion count mismatch]",
        )

    def test_booleans_become_shell_words(self):
        exports = config_module.export_shell(self.project)
        self.assertIn("MCBUMP_WORKFLOWS_CI=true", exports)
        self.assertIn("MCBUMP_WORKFLOWS_GAMETEST_ENABLED=false", exports)

    def test_the_loader_contributes_what_the_shell_cannot_derive(self):
        exports = config_module.export_shell(self.project)
        self.assertIn("MCBUMP_SERVER_TASK=runServer", exports)
        self.assertIn("MCBUMP_STORE_LOADER=fabric", exports)
        self.assertIn("MCBUMP_MOD_LOADED_PATTERN=", exports)
        self.assertIn("MCBUMP_FATAL_PATTERNS=", exports)

    def test_fatal_patterns_merge_the_loader_and_the_config(self):
        self.CONFIG = CONFIG.replace(
            "    expect:", '    fatal-extra: ["Custom explosion"]\n    expect:'
        )
        self.tearDown()  # the workspace setUp built with the default config
        self.setUp()
        exports = config_module.export_shell(self.project)
        line = next(
            line for line in exports.splitlines() if line.startswith("MCBUMP_FATAL_PATTERNS=")
        )
        self.assertIn("Mixin apply failed", line)
        self.assertIn("Custom explosion", line)

    def test_records_are_tab_separated_one_per_line(self):
        value = config_module._shell_value(
            self.project.raw["tests"]["server"]["expect-count"]
        )
        fields = value.split("\n")[0].split("\t")
        self.assertEqual(len(fields), 4)
        self.assertEqual(fields[0], "Registered ([0-9]+) potions")
        self.assertEqual(fields[2], "= registerPotion(")

    def test_github_output_is_flat_key_value(self):
        lines = config_module.export_github_output(self.project).splitlines()
        pairs = dict(line.split("=", 1) for line in lines)
        self.assertEqual(pairs["mod_id"], "extended-time-potion")
        self.assertEqual(pairs["ci"], "true")
        self.assertEqual(pairs["gametest"], "false")
        self.assertEqual(pairs["stores"], "modrinth,curseforge")
        self.assertEqual(pairs["assignee"], "Spatulox")


if __name__ == "__main__":
    unittest.main()
