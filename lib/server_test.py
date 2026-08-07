"""Boot a headless dedicated server with the mod, and check it actually worked.

Split in two on purpose:

    check_log()     pure, takes the log as a string — every assertion the mod
                    cares about, unit tested without booting anything
    run()           the process handling around it

"The server started" proves nothing about the mod: an empty registry and a
callback that never ran both produce a perfectly healthy server. The
tests.server.expect / expect-count lists are what turn "it booted" into "it
worked".
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .common import Failure
from .config import Project
from .gradle import read_property

BOOT_DONE = re.compile(r"Done \([0-9.]+s\)")
BOOTED_VERSION = re.compile(r"Starting minecraft server version (.+)$", re.MULTILINE)

# flat world + watchdog disabled: fast startup, no false positive on a slow CI
# runner. The world name is wiped before each run.
SERVER_PROPERTIES = """\
online-mode=false
level-type=minecraft\\:flat
level-name={level_name}
max-tick-time=-1
view-distance=4
simulation-distance=4
sync-chunk-writes=false
spawn-protection=0
"""


@dataclass(frozen=True)
class ServerTest:
    project: Project
    log: Path
    expected_minecraft: str | None = None
    gradle_args: tuple[str, ...] = ()
    run_dir: str = "run"
    level_name: str = "ci-smoke-test"
    boot_timeout: int | None = None
    stop_timeout: int | None = None

    @property
    def server(self) -> dict:
        return self.project.raw["tests"]["server"]

    def resolved_boot_timeout(self) -> int:
        return self.boot_timeout or self.server["boot-timeout"]

    def resolved_stop_timeout(self) -> int:
        return self.stop_timeout or self.server["stop-timeout"]


# --------------------------------------------------------------------------
# The assertions — pure, and where the value of this file is
# --------------------------------------------------------------------------
def count_in_source(root: Path, source: str, literal: str) -> int:
    """Lines of `source` containing `literal`, the way `grep -cF` counts them.

    Counting from the source rather than storing a constant is what makes the
    expectation follow the code: adding a potion updates it on its own.
    """
    path = root / source
    if not path.is_file():
        raise Failure(f"count-source '{source}' does not exist")
    text = path.read_text(encoding="utf-8", errors="replace")
    return sum(1 for line in text.splitlines() if literal in line)


def check_log(test: ServerTest, log_text: str, log=print) -> None:
    """Everything the log must and must not say. Raises Failure on the first miss.

    `log` is injectable so the tests can stay silent: these assertions are the
    part worth unit testing, and they should not print a hundred lines doing it.
    """
    project = test.project
    loader = project.loader
    server = test.server

    # -- was the mod actually loaded?
    # The loader's own inventory line, not any mention of the id: a mod id appears
    # in classpath dumps and gradle warnings, so a bare search passes on a mod the
    # loader rejected.
    if not re.search(loader.mod_loaded_pattern(project.mod_id), log_text, re.MULTILINE):
        raise Failure(
            f"{project.mod_id} does not appear in the loader's list of loaded mods"
        )

    # -- did the version we asked for actually boot?
    # Without this guard a stale build cache, a concurrent edit of the file or an
    # ignored -Pminecraft_version would turn the test green on the wrong version.
    expected = test.expected_minecraft or read_property(
        project.paths.gradle_properties.read_text(encoding="utf-8"), "minecraft_version"
    )
    booted_match = BOOTED_VERSION.search(log_text)
    if not booted_match:
        raise Failure("cannot read the booted version from the log")
    booted = booted_match.group(1).strip()
    if booted != expected:
        raise Failure(
            f"the server booted Minecraft {booted} while {expected} was expected"
        )

    # -- fatal signatures
    # Targeted: Minecraft logs plenty of harmless WARNs. The list is the loader's
    # own signatures plus tests.server.fatal-extra.
    patterns = [*loader.fatal_patterns(), *server["fatal-extra"]]
    hits = [
        line
        for line in log_text.splitlines()
        if any(re.search(pattern, line) for pattern in patterns)
    ]
    if hits:
        detail = "\n".join(f"  {line}" for line in hits[:10])
        raise Failure(f"fatal error detected in the log:\n{detail}")

    # -- did the mod do its job?
    for rule in server["expect"]:
        if not re.search(rule["pattern"], log_text, re.MULTILINE):
            message = rule["message"] or "expected pattern not found"
            raise Failure(f"{message}: /{rule['pattern']}/ never appeared in the log")
        log(f"==> Found: /{rule['pattern']}/")

    for rule in server["expect-count"]:
        message = rule["message"] or "count check"
        expected_count = count_in_source(
            project.root, rule["count-source"], rule["count-pattern"]
        )
        if expected_count == 0:
            raise Failure(
                f"{message}: '{rule['count-pattern']}' never appears in "
                f"{rule['count-source']}, so there is nothing to expect. Either the "
                f"feature was removed, or the source was rewritten and mc-bump.yml "
                f"no longer knows how to count it."
            )

        found = re.search(rule["pattern"], log_text, re.MULTILINE)
        if not found:
            raise Failure(f"{message}: the mod never reported /{rule['pattern']}/")
        if not found.groups():
            raise Failure(
                f"{message}: the pattern /{rule['pattern']}/ has no capture group, "
                f"so there is no number to compare"
            )

        actual = found.group(1)
        log(
            f"==> Counted: {actual} (expected: {expected_count}) "
            f"for /{rule['pattern']}/"
        )
        if actual != str(expected_count):
            raise Failure(f"{message}: {actual} instead of {expected_count}")


# --------------------------------------------------------------------------
# The process handling
# --------------------------------------------------------------------------
def prepare_run_dir(test: ServerTest) -> Path:
    run_dir = test.project.root / test.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")

    # The world is wiped rather than reused: a save written by a newer Minecraft
    # refuses to load on an older one, which would break the version matrix the
    # moment it tests an older version after a newer one.
    world = run_dir / test.level_name
    if world.is_dir():
        import shutil

        shutil.rmtree(world)

    (run_dir / "server.properties").write_text(
        SERVER_PROPERTIES.format(level_name=test.level_name), encoding="utf-8"
    )
    return run_dir


def _terminate(process: subprocess.Popen) -> None:
    """Kill the whole process group: gradle is a launcher, the JVM is the server.

    The shell version only ever signalled gradle, which left the server alive on
    a timeout until the runner reclaimed it.
    """
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
        process.wait(timeout=15)


def run(test: ServerTest) -> None:
    """Boot, assert, stop. Raises Failure with the log tail on any problem."""
    project = test.project
    log_path = project.root / test.log
    prepare_run_dir(test)
    log_path.unlink(missing_ok=True)

    command = [
        "./gradlew",
        project.loader.server_task(),
        *test.gradle_args,
        "--no-daemon",
        "--console=plain",
        "--stacktrace",
    ]
    boot_timeout = test.resolved_boot_timeout()
    print(f"==> Starting the server (timeout {boot_timeout}s): {' '.join(command)}")

    def fail(reason: str) -> Failure:
        tail = ""
        if log_path.is_file():
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(lines[-200:])
        return Failure(f"{reason}\n--- last 200 lines of {test.log} ---\n{tail}")

    # A pipe rather than the shell's named FIFO: stdin is simply held open by the
    # parent, so there is nothing to create, nothing to clean up, and none of the
    # "opening a FIFO write-only blocks until a reader shows up" dance.
    # start_new_session so the whole tree can be signalled at once.
    with open(log_path, "wb") as log_file:
        process = subprocess.Popen(
            command,
            cwd=project.root,
            stdin=subprocess.PIPE,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        try:
            started = False
            deadline = time.monotonic() + boot_timeout
            while time.monotonic() < deadline:
                if log_path.is_file() and BOOT_DONE.search(
                    log_path.read_text(encoding="utf-8", errors="replace")
                ):
                    started = True
                    break
                if process.poll() is not None:
                    break  # died before printing "Done"
                time.sleep(2)

            if not started:
                if process.poll() is None:
                    raise fail(f'the server did not reach "Done" within {boot_timeout}s')
                raise fail("the server stopped before it finished starting")

            print("==> Server started.")
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            try:
                check_log(test, log_text)
            except Failure as error:
                raise fail(str(error)) from None

            # -- clean shutdown
            print("==> Sending the stop command...")
            try:
                process.stdin.write(b"stop\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

            try:
                process.wait(timeout=test.resolved_stop_timeout())
                print("==> Server stopped cleanly.")
            except subprocess.TimeoutExpired:
                # The build tool does not always forward stdin to the server. The
                # server started without crashing, which is the point of this
                # test: kill it and move on.
                print(
                    f'==> The server did not answer "stop" within '
                    f"{test.resolved_stop_timeout()}s, forcing shutdown."
                )
        finally:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            _terminate(process)

    print(f"\n=== OK: the server started with {project.mod_id} and no fatal error ===")
    done = BOOT_DONE.search(log_path.read_text(encoding="utf-8", errors="replace"))
    if done:
        print(done.group(0))
