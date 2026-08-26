"""What the server log has to say for a run to count as proof.

None of this was testable while it lived in bash: the assertions were interleaved
with process handling, so checking them meant booting a real Minecraft server.
They are now a pure function over the log text, and this file is the reason the
rewrite was worth doing.

The fixture is shaped after a real Fabric log — including the gradle warning line
that used to satisfy the old `grep 'extended-time-potion'`.
"""

from __future__ import annotations

import os
import shlex
import signal
import time
import unittest
from pathlib import Path

from lib.common import Failure
from lib.patterns import compile_pattern
from lib.server_test import (
    ServerTest,
    _living,
    _process_tree,
    _signal,
    _terminate,
    check_log,
    count_in_source,
)

from .helpers import ModRepoTestCase

GOOD_LOG = """\
> Configure project :
curseforge_id='extended-time-potion' is not a numeric ID: the task is disabled.

> Task :runServer
[13:48:10] [main/INFO] (FabricLoader) Loading 3 mods:
\t- extended-time-potion 26.2-1.1.0
\t- fabric-api 0.156.0+26.2
\t- java 25
[13:48:12] [main/INFO] (Minecraft) Starting minecraft server version 26.1.1
[13:48:19] [main/INFO] (extended-time-potion) Registered 50 potions
[13:48:22] [main/INFO] (extended-time-potion) Brewing mixes registered
[13:48:25] [main/INFO] (Minecraft) Done (14.512s)! For help, type "help"
"""


class CheckLogTestCase(ModRepoTestCase):
    def setUp(self):
        super().setUp()
        # 50 registrations, matching the log fixture
        source = self.root / "src/main/java/com/spatulox/ExtendedTimePotion.java"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            "\n".join(
                f"    public static Holder<Potion> P{i} = registerPotion(\"p{i}\", null);"
                for i in range(50)
            ),
            encoding="utf-8",
        )

    def test(self, log: str = GOOD_LOG, minecraft: str = "26.1.1"):
        check_log(
            ServerTest(
                project=self.project,
                log=Path("server-test.log"),
                expected_minecraft=minecraft,
            ),
            log,
            log=lambda *_: None,
        )

    def rejects(self, log: str, *fragments: str, minecraft: str = "26.1.1"):
        with self.assertRaises(Failure) as caught:
            self.test(log, minecraft)
        message = str(caught.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        return message


class HappyPathTest(CheckLogTestCase):
    def test_a_good_log_passes(self):
        self.test()

    def test_the_count_comes_from_the_source_not_a_constant(self):
        self.assertEqual(
            count_in_source(
                self.root,
                "src/main/java/com/spatulox/ExtendedTimePotion.java",
                compile_pattern("*= registerPotion(*", None, where="test"),
            ),
            50,
        )


class ModLoadedTest(CheckLogTestCase):
    def test_the_gradle_warning_alone_is_not_proof(self):
        """The regression this check exists for.

        `grep 'extended-time-potion'` matched the curseforge_id warning, so the
        test passed on a run where the loader never loaded the mod.
        """
        without_inventory = GOOD_LOG.replace("\t- extended-time-potion 26.2-1.1.0\n", "")
        self.assertIn("extended-time-potion", without_inventory)  # still mentioned
        self.rejects(without_inventory, "does not appear in the loader's list")

    def test_the_inventory_line_is_what_counts(self):
        only_inventory = (
            "[main/INFO] (FabricLoader) Loading 1 mods:\n"
            "\t- extended-time-potion 26.2-1.1.0\n"
            "[main/INFO] Starting minecraft server version 26.1.1\n"
            "[main/INFO] Registered 50 potions\n"
            "[main/INFO] Brewing mixes registered\n"
            "Done (1.0s)!\n"
        )
        self.test(only_inventory)

    def test_a_mod_whose_id_is_a_prefix_of_ours_does_not_count(self):
        impostor = GOOD_LOG.replace(
            "\t- extended-time-potion 26.2-1.1.0", "\t- extended-time-potion-test 1.0.0"
        )
        self.rejects(impostor, "does not appear")


class BootedVersionTest(CheckLogTestCase):
    def test_booting_another_version_is_refused(self):
        """A stale cache or an ignored -Pminecraft_version would go unnoticed."""
        self.rejects(GOOD_LOG, "booted Minecraft 26.1.1", minecraft="26.2")

    def test_a_log_without_a_version_line_is_refused(self):
        self.rejects(
            GOOD_LOG.replace("Starting minecraft server version 26.1.1", "starting up"),
            "cannot read the booted version",
        )


class FatalPatternTest(CheckLogTestCase):
    def test_a_loader_signature_fails_even_though_the_server_booted(self):
        self.rejects(
            GOOD_LOG + "[main/ERROR] Mixin apply failed for extended-time-potion\n",
            "fatal error detected",
            "Mixin apply failed",
        )

    def test_an_ordinary_warning_does_not(self):
        self.test(GOOD_LOG + "[main/WARN] Unable to find a suitable font\n")

    def test_the_config_can_add_its_own(self):
        self.CONFIG = self.CONFIG.replace(
            "    expect:", '    fatal-extra: ["Potion registry is empty"]\n    expect:'
        )
        self.tearDown()
        self.setUp()
        self.rejects(GOOD_LOG + "[main/ERROR] Potion registry is empty\n", "fatal error")


class ExpectTest(CheckLogTestCase):
    def test_a_missing_phrase_reports_the_configured_message(self):
        self.rejects(
            GOOD_LOG.replace("Brewing mixes registered", "something else entirely"),
            "the brewing callback never ran",
            "Brewing mixes registered",
        )


class ExpectCountTest(CheckLogTestCase):
    def test_a_mismatch_names_both_numbers(self):
        self.rejects(
            GOOD_LOG.replace("Registered 50 potions", "Registered 49 potions"),
            "49 instead of 50",
        )

    def test_the_mod_never_reporting_the_number(self):
        self.rejects(
            GOOD_LOG.replace("Registered 50 potions", "started"),
            "never reported",
        )

    def test_a_source_that_no_longer_matches_says_why(self):
        """Counting zero is a real answer, and a confusing one to hit blind."""
        (self.root / "src/main/java/com/spatulox/ExtendedTimePotion.java").write_text(
            "// the registrations were rewritten\n", encoding="utf-8"
        )
        self.rejects(GOOD_LOG, "never appears in", "no longer knows how to count it")

    def test_a_missing_source_file_says_which(self):
        (self.root / "src/main/java/com/spatulox/ExtendedTimePotion.java").unlink()
        self.rejects(GOOD_LOG, "does not exist")

    def test_a_glob_without_the_count_token_is_refused(self):
        """A config error that would otherwise crash deep inside the comparison."""
        self.CONFIG = self.CONFIG.replace(
            'pattern: "Registered <count> potions"', 'pattern: "Registered * potions"'
        )
        self.tearDown()
        self.setUp()
        self.rejects(GOOD_LOG, "<count>")

    def test_a_regex_is_still_accepted(self):
        """The escape hatch, for what a glob cannot say."""
        self.CONFIG = self.CONFIG.replace(
            'pattern: "Registered <count> potions"',
            'regex: "Registered ([0-9]+) potions"',
        )
        self.tearDown()
        self.setUp()
        self.test()

    def test_a_registration_named_in_a_comment_is_not_counted(self):
        """The bug this fixture hit on itself."""
        source = self.root / "src/main/java/com/spatulox/ExtendedTimePotion.java"
        source.write_text(
            "// see the `= registerPotion(` calls below\n"
            + source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.test()  # still 50, not 51


# --------------------------------------------------------------------------
# The teardown, which is not pure and still has to be tested
# --------------------------------------------------------------------------
DETACHED = (
    "import os, sys, time; os.setsid(); "
    "open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(300)"
)


class TerminateTest(unittest.TestCase):
    """A detached grandchild is the shape that matters.

    This is what gradle does to its build JVM, and why signalling our own process
    group left the Minecraft server running: setsid() moves it out of our session
    and out of our process group, but NOT out of the parent chain.
    """

    def test_a_detached_grandchild_is_killed_too(self):
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "pid"
            process = subprocess.Popen(
                [
                    "bash",
                    "-c",
                    f'python3 -c {shlex.quote(DETACHED)} {shlex.quote(str(marker))} '
                    f"& sleep 300",
                ],
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and not marker.is_file():
                    time.sleep(0.1)
                self.assertTrue(marker.is_file(), "the grandchild never started")
                grandchild = int(marker.read_text().strip())

                # It really is unreachable the old way: a different session and a
                # different group. Without this the test could pass by accident.
                self.assertNotEqual(os.getsid(grandchild), os.getsid(process.pid))
                self.assertNotEqual(os.getpgid(grandchild), os.getpgid(process.pid))

                _terminate(process)

                self.assertEqual(
                    _living([grandchild]),
                    [],
                    "the detached grandchild outlived _terminate",
                )
            finally:
                _signal([process.pid], signal.SIGKILL)
                process.wait(timeout=15)
                if marker.is_file():
                    _signal([int(marker.read_text().strip() or 0)], signal.SIGKILL)

    def test_the_tree_holds_the_child_before_anything_is_killed(self):
        import subprocess

        process = subprocess.Popen(["bash", "-c", "sleep 300"], start_new_session=True)
        try:
            tree = _process_tree(process.pid)
            self.assertIn(process.pid, tree)
            self.assertEqual(tree[0], process.pid, "the parent must come first")
        finally:
            _terminate(process)


if __name__ == "__main__":
    unittest.main()
