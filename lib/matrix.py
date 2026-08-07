"""The version matrix, and the escalation ladder on top of it.

Why a matrix. An update bumps the loader API and widens the compatibility range
over the whole series (">=26.1 <=26.1.2"). Testing only 26.1.2 proves nothing
about 26.1 and 26.1.1 running with THAT API, yet those are versions the jar
promises to load on and that the stores list. Every claimed version is therefore
built and booted with the resolved dependencies, which is exactly the combination
that ships.

Why a ladder. A Minecraft update used to bump the loader and its API at the same
time, so a red matrix had three suspects — and those two change the behaviour of
EVERY sub-version at once. They are now frozen by the updater and only move here,
as a reaction to a failure, one at a time:

    matrix with the frozen dependencies
      KO -> bump the API      -> whole matrix again
              KO -> bump loader -> whole matrix again
                      KO -> the mod is really broken

Each rung re-runs the WHOLE matrix, not just the version that failed: a newer API
is exactly the kind of change that fixes the newest version while breaking an
older one of the same series, and the range promises them all.

In CI this sequential loop is usually replaced by a GitHub job matrix running the
same versions in parallel. It stays the local entry point, and the sequential path
the ladder needs.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .common import Failure
from .config import Project
from .gradle import read_property, set_property, write_preserving_final_newline
from .server_test import ServerTest
from .server_test import run as run_server_test
from .update import bump_dependency, list_test_versions

#: What the matrix wrote, one "<version> <outcome>" per line. The pull request
#: body reads this rather than recomputing the list: by then a revert may have
#: restored the previous, shorter list of supported versions.
STATUS_FILE = "test-matrix-status.txt"

#: What the ladder moved, one "<gradle key> <from> <to>" per line.
ESCALATION_FILE = "test-escalation.txt"

OK = "ok"
BUILD_FAILED = "build"
SERVER_FAILED = "server"


@dataclass
class VersionOutcome:
    minecraft: str
    outcome: str
    seconds: int = 0

    @property
    def ok(self) -> bool:
        return self.outcome == OK


@dataclass
class MatrixResult:
    outcomes: list[VersionOutcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.outcomes) and all(o.ok for o in self.outcomes)

    @property
    def failed(self) -> list[VersionOutcome]:
        return [o for o in self.outcomes if not o.ok]

    def as_status_file(self) -> str:
        return "".join(f"{o.minecraft} {o.outcome}\n" for o in self.outcomes)


def _gradle_build(project: Project, minecraft: str, log: Path) -> bool:
    command = [
        "./gradlew",
        "build",
        f"-Pminecraft_version={minecraft}",
        "--stacktrace",
        "--console=plain",
    ]
    with open(log, "wb") as handle:
        completed = subprocess.run(
            command, cwd=project.root, stdout=handle, stderr=subprocess.STDOUT
        )
    return completed.returncode == 0


def run_matrix(
    project: Project,
    versions: list[str] | None = None,
    *,
    status_file: str = STATUS_FILE,
) -> MatrixResult:
    import time

    versions = versions if versions is not None else list_test_versions(project)
    if not versions:
        raise Failure("no Minecraft version to test")

    print(f"############ Version matrix: {' '.join(versions)} ############")
    result = MatrixResult()
    status_path = project.root / status_file
    status_path.write_text("", encoding="utf-8")

    for minecraft in versions:
        print(f"\n======== Minecraft {minecraft} ========")
        started = time.monotonic()
        build_log = project.root / f"build-{minecraft}.log"
        server_log = f"server-test-{minecraft}.log"

        if not _gradle_build(project, minecraft, build_log):
            print(f"=== FAILED: build on Minecraft {minecraft} ===")
            print(_tail(build_log, 100))
            result.outcomes.append(VersionOutcome(minecraft, BUILD_FAILED))
            status_path.write_text(result.as_status_file(), encoding="utf-8")
            continue

        test = ServerTest(
            project=project,
            log=Path(server_log),
            expected_minecraft=minecraft,
            gradle_args=(f"-Pminecraft_version={minecraft}",),
        )

        outcome = _boot_with_one_retry(project, test, server_log, minecraft)
        result.outcomes.append(
            VersionOutcome(minecraft, outcome, int(time.monotonic() - started))
        )
        status_path.write_text(result.as_status_file(), encoding="utf-8")

        if outcome == OK:
            print(
                f"======== Minecraft {minecraft} OK "
                f"({result.outcomes[-1].seconds}s) ========"
            )

    print()
    if result.ok:
        print(
            f"=== OK: all {len(result.outcomes)} claimed version(s) build and boot: "
            f"{' '.join(o.minecraft for o in result.outcomes)} ==="
        )
    else:
        broken = ", ".join(f"{o.minecraft} ({o.outcome})" for o in result.failed)
        print(
            f"=== FAILED: {len(result.failed)}/{len(result.outcomes)} "
            f"version(s) broken: {broken} ==="
        )
    return result


def _boot_with_one_retry(
    project: Project, test: ServerTest, server_log: str, minecraft: str
) -> str:
    try:
        run_server_test(test)
        return OK
    except Failure as error:
        # The build tool resolves mods on virtual threads, and they can deadlock
        # against each other on the JVM-wide Cleaner monitor while setting
        # Minecraft up. That hang never reaches the point where Minecraft itself
        # starts, which is exactly how it is told apart from the mod being broken:
        # a mod that fails to load DOES get that far. Only the hang is worth
        # retrying.
        log_path = project.root / server_log
        text = (
            log_path.read_text(encoding="utf-8", errors="replace")
            if log_path.is_file()
            else ""
        )
        if "Starting minecraft server version" in text:
            print(f"=== FAILED: headless server on Minecraft {minecraft} ===\n{error}")
            return SERVER_FAILED

        print(
            f"==> Minecraft never started on {minecraft} (build setup hang?), "
            f"retrying once"
        )

    try:
        run_server_test(test)
        return OK
    except Failure as error:
        print(f"=== FAILED: headless server on Minecraft {minecraft} (twice) ===\n{error}")
        return SERVER_FAILED


def _tail(path: Path, lines: int) -> str:
    if not path.is_file():
        return "(no log)"
    return "\n".join(
        path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    )


# --------------------------------------------------------------------------
# The ladder
# --------------------------------------------------------------------------
@dataclass
class Escalation:
    gradle_key: str
    before: str
    after: str


def run_with_escalation(
    project: Project,
    *,
    status_file: str = STATUS_FILE,
    escalation_file: str = ESCALATION_FILE,
) -> tuple[MatrixResult, list[Escalation]]:
    """Run the matrix, bumping the frozen dependencies one at a time if it fails.

    The mod metadata is not touched here. A bump is a hypothesis; the dependency
    floor is only engraved by --mark-supported, once the matrix has proven it.

    The rungs come from the loader module, so a loader with a different set of
    frozen dependencies needs no change here.
    """
    escalations: list[Escalation] = []
    escalation_path = project.root / escalation_file
    escalation_path.write_text("", encoding="utf-8")

    def record() -> None:
        escalation_path.write_text(
            "".join(f"{e.gradle_key} {e.before} {e.after}\n" for e in escalations),
            encoding="utf-8",
        )

    print("\n#################### Matrix: frozen dependencies ####################")
    result = run_matrix(project, status_file=status_file)
    if result.ok:
        print("\n=== OK: no escalation needed ===")
        return result, escalations

    print("\n==> The matrix failed with the frozen dependencies, escalating.")

    for rung in project.loader.escalation_rungs():
        role = next(
            r for r, key in project.loader.gradle_keys.items() if key == rung.gradle_key
        )
        text = project.paths.gradle_properties.read_text(encoding="utf-8")
        before = read_property(text, rung.gradle_key) or ""

        print(f"\n==> Escalation: {rung.label} (currently {rung.gradle_key}={before})")
        bumped = bump_dependency(project, role, dry_run=False, log=print)

        if bumped["status"] == "already-latest":
            print(f"==> {rung.gradle_key} is already the newest available, skipping.")
            continue

        after = read_property(
            project.paths.gradle_properties.read_text(encoding="utf-8"), rung.gradle_key
        ) or ""
        escalations.append(Escalation(rung.gradle_key, before, after))
        record()

        print(f"\n#################### Matrix: {rung.gradle_key}={after} ####################")
        result = run_matrix(project, status_file=status_file)
        if result.ok:
            print(f"\n=== OK: fixed by {rung.gradle_key} {before} -> {after} ===")
            return result, escalations

        print(f"==> Still failing with {rung.gradle_key}={after}.")

    print("\n=== FAILED: the matrix is still red after every escalation step ===")
    if escalations:
        print("Bumps applied and kept, as a starting point for a manual fix:")
        for entry in escalations:
            print(f"  {entry.gradle_key}: {entry.before} -> {entry.after}")
    return result, escalations


def pin_property(project: Project, key: str, value: str) -> None:
    """Used by the tests to simulate what a rung of the ladder writes."""
    original = project.paths.gradle_properties.read_text(encoding="utf-8")
    write_preserving_final_newline(
        project.paths.gradle_properties, original, set_property(original, key, value)
    )
