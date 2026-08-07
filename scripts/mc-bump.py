#!/usr/bin/env python3
"""Resolve the loader versions matching a Minecraft version and update the mod.

Reads .github/mc-bump.yml from the mod repository, writes gradle.properties and
the loader metadata. Standard library only, except PyYAML.

Examples
--------
    # latest Mojang release
    python3 mc-bump.py

    # specific version
    python3 mc-bump.py 26.2

    # see what would change without writing anything
    python3 mc-bump.py 26.2 --dry-run

    # machine readable output (used by the GitHub workflow)
    python3 mc-bump.py --json

    # update, then build and boot a server for EVERY claimed version, escalating
    # the frozen dependencies if needed, and only claim compatibility if it works
    python3 mc-bump.py --run-tests

    # AFTER a successful matrix: mark the version as compatible
    python3 mc-bump.py --mark-supported

    # after a FAILED matrix: restore the previous compatibility claims
    python3 mc-bump.py --revert-compat

    # escalation steps, driven by test-with-escalation.py
    python3 mc-bump.py --bump-api
    python3 mc-bump.py --bump-loader

Exit codes
----------
    0  success (updated, or already up to date)
    1  error (network, missing file, version not found, failed tests...)
    2  the loader does not support this Minecraft version yet
    3  --bump-*: already on the newest version, this escalation step is a no-op
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import config as config_module  # noqa: E402
from lib import update as update_module  # noqa: E402
from lib.common import Failure  # noqa: E402
from lib.gradle import read_property  # noqa: E402
from lib.matrix import run_with_escalation  # noqa: E402
from lib.versions import latest_minecraft_release, java_version_for, series_of  # noqa: E402

ESCALATION_LADDER = "scripts/test-with-escalation.py"


def emit_github_output(result: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in result.items():
            if isinstance(value, list):
                value = ",".join(value)
            if isinstance(value, bool):
                value = str(value).lower()
            handle.write(f"{key}={'' if value is None else value}\n")


def run_tests(project, log) -> str | None:
    """Run the same ladder as CI. Returns the failed step, or None.

    Calls run_with_escalation() directly rather than shelling out to the entry
    point script, so local and CI run strictly the same code: a full matrix over
    every claimed version, then an API bump and another full matrix, then a loader
    bump and a third one. When the ladder lived in bash this had to be a
    subprocess, and its exit code was all we could learn from it.
    """
    log("\n==> version matrix with escalation")
    result, _ = run_with_escalation(project)
    if result.ok:
        return None
    broken = ", ".join(f"{o.minecraft} ({o.outcome})" for o in result.failed)
    return f"version matrix, escalation exhausted: {broken}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Update the Minecraft and loader versions of a mod.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "minecraft_version",
        nargs="?",
        help="target version (e.g. 26.2). Defaults to the latest Mojang release.",
    )
    parser.add_argument("--root", help="mod repository (default: walk up from the cwd)")
    parser.add_argument(
        "--dry-run", action="store_true", help="show the changes without writing"
    )
    parser.add_argument(
        "--json", action="store_true", help="JSON output on stdout, nothing else"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="continue even if the repo is already on that version",
    )
    parser.add_argument(
        "--buildtool",
        "--loom",
        dest="buildtool",
        metavar="VERSION",
        help="pin the build plugin instead of resolving the latest stable one "
        "(e.g. an older fabric-loom for an old Minecraft version)",
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="after the update, run the version matrix with its escalation ladder, "
        "and mark the version as supported only if it passes. On failure the "
        "compatibility claims are reverted and the exit code is 1.",
    )
    parser.add_argument(
        "--mark-supported",
        action="store_true",
        help="add the current minecraft_version to supported_minecraft_versions. "
        "Use ONLY AFTER a successful matrix: this list is what is announced as "
        "compatible on the stores.",
    )
    parser.add_argument(
        "--list-test-versions",
        action="store_true",
        help="print, one per line, every Minecraft version the mod claims and that "
        "must therefore be booted. Used by test-matrix.py.",
    )
    parser.add_argument(
        "--revert-compat",
        action="store_true",
        help="restore supported_minecraft_versions and the metadata range to the "
        "values recorded before the last update, keeping the version bumps.",
    )
    parser.add_argument(
        "--bump-api",
        action="store_true",
        help="escalation step: move the frozen API version to the newest release "
        "for the current minecraft_version. Exit code 3 when there is nothing "
        "newer. The metadata is left to --mark-supported.",
    )
    parser.add_argument(
        "--bump-loader",
        action="store_true",
        help="escalation step: same as --bump-api, for the loader version.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    modes = [
        args.mark_supported,
        args.revert_compat,
        args.run_tests,
        args.bump_api,
        args.bump_loader,
    ]
    if sum(bool(mode) for mode in modes) > 1:
        parser.error(
            "--mark-supported, --revert-compat, --run-tests, --bump-api and "
            "--bump-loader are mutually exclusive"
        )
    if args.run_tests and (args.dry_run or args.json):
        parser.error("--run-tests cannot be combined with --dry-run or --json")

    quiet = args.json

    def log(message: str = "") -> None:
        if not quiet:
            print(message)

    project = config_module.load(Path(args.root) if args.root else None)
    project.paths.require()
    loader = project.loader
    keys = loader.gradle_keys

    # -- read-only modes ---------------------------------------------------
    if args.list_test_versions:
        for version in update_module.list_test_versions(project):
            print(version)
        return 0

    if args.revert_compat:
        result = update_module.revert_compat(project, args.dry_run)
        log(f"Compatibility claims restored for Minecraft {result['minecraft_version']}.")
        log(
            f"  supported_minecraft_versions = "
            f"{','.join(result['supported_minecraft_versions']) or '(empty)'}"
        )
        log(f"  metadata range = {result['minecraft_range']}")
        log("  The version bumps themselves are kept.")
        if args.dry_run:
            log("\n--dry-run: no file modified.")
        if args.json:
            print(json.dumps(result, indent=2))
        emit_github_output(result)
        return 0

    if args.bump_api or args.bump_loader:
        role = "api" if args.bump_api else "loader"
        result = update_module.bump_dependency(project, role, args.dry_run, log)
        if args.dry_run:
            log("\n--dry-run: no file modified.")
        if args.json:
            print(json.dumps(result, indent=2))
        emit_github_output(result)
        return (
            update_module.EXIT_ALREADY_LATEST
            if result["status"] == "already-latest"
            else 0
        )

    if args.mark_supported:
        version, supported, new_range, floors = update_module.mark_supported(
            project, args.dry_run, log
        )
        log(f"Minecraft {version} marked as compatible.")
        log(f"  supported_minecraft_versions = {','.join(supported)}")
        log(f"  metadata range = {new_range}")
        for key, floor in floors.items():
            log(f"  depends.{key} = {floor}  (escalated)")
        if args.dry_run:
            log("\n--dry-run: no file modified.")
        result = {
            "status": "marked-supported",
            "minecraft_version": version,
            "supported_minecraft_versions": supported,
            "minecraft_range": new_range,
            "mod_version": read_property(
                project.paths.gradle_properties.read_text(encoding="utf-8"), "mod_version"
            ),
            "dependency_floors": ",".join(
                f"{key}{floor}" for key, floor in floors.items()
            ),
        }
        if args.json:
            print(json.dumps(result, indent=2))
        emit_github_output(result)
        return 0

    # -- the update itself -------------------------------------------------
    current, _ = update_module.current_versions(project)
    target = args.minecraft_version or latest_minecraft_release()
    log(f"Loader          : {loader.name}")
    log(f"Current version : {current}")
    log(f"Target version  : {target}")

    properties = project.paths.gradle_properties.read_text(encoding="utf-8")
    frozen_loader = read_property(properties, keys["loader"])
    frozen_api = read_property(properties, keys["api"])

    # loader/api report what the build will ACTUALLY use, which after this run is
    # still the frozen pair. What upstream offers goes in available_*, so the PR
    # can say "frozen on X, latest available Y" without the two being confused.
    result = {
        "status": "",
        "loader": loader.name,
        "minecraft_version": target,
        "previous_version": current,
        "series": series_of(target),
        "loader_version": frozen_loader,
        "api_version": frozen_api,
        "available_loader_version": None,
        "available_api_version": None,
        "buildtool_version": None,
        "java_version": None,
        "mod_version": None,
        "minecraft_range": None,
        "changed": False,
    }

    def stop(status: str, message: str, code: int) -> int:
        log(f"\n{message}")
        result["status"] = status
        if args.json:
            print(json.dumps(result, indent=2))
        emit_github_output(result)
        return code

    if target == current and not args.force:
        return stop("up-to-date", "Already up to date. (--force to reapply)", 0)

    resolved = loader.resolve(target, pin_buildtool=args.buildtool)
    result["available_loader_version"] = resolved.loader
    result["available_api_version"] = resolved.api
    if not resolved.usable:
        missing = "loader" if not resolved.loader else "API"
        return stop(
            "unsupported",
            f"No stable {loader.name} {missing} published for Minecraft {target} yet.",
            2,
        )

    java = java_version_for(target)

    def frozen(current_value: str | None, available: str) -> str:
        if current_value == available:
            return f"{current_value} (frozen, already the latest)"
        return f"{current_value} (frozen, latest available: {available})"

    log(f"\n  {keys['loader']} = {frozen(frozen_loader, resolved.loader)}")
    log(f"  {keys['api']} = {frozen(frozen_api, resolved.api)}")
    log(f"  {keys['buildtool']} = {resolved.buildtool}{' (pinned)' if args.buildtool else ''}")
    if java is None:
        log("  java_version = (absent from the Mojang manifest, left as is)")
    else:
        log(f"  java_version = {java}")

    # Snapshot the compatibility claims before touching anything, so a failed
    # matrix can restore them with --revert-compat.
    if not args.dry_run:
        update_module.save_update_state(
            project,
            target,
            read_property(properties, "supported_minecraft_versions") or "",
        )

    applied = update_module.update_gradle_properties(
        project, target, resolved.buildtool, java, args.dry_run, log
    )
    java_changed = (
        loader.write_java_version(project.paths, java, args.dry_run)
        if java is not None
        else False
    )

    # The range has to be widened NOW, before the tests: otherwise the loader
    # refuses to load the mod on the new version and no version could ever be
    # validated. --revert-compat undoes it if the tests fail.
    new_range = update_module.compat_range(project, target, applied.supported)
    range_changed = loader.write_depends(
        project.paths, loader.depends_keys()["minecraft"], new_range, args.dry_run
    )

    log(f"  mod_version = {applied.mod_version}")
    log(f"  supported_minecraft_versions = {','.join(applied.supported) or '(empty)'}")
    log(f"  metadata range = {new_range}")

    result.update(
        {
            "status": "updated",
            "buildtool_version": resolved.buildtool,
            "java_version": java,
            "mod_version": applied.mod_version,
            "supported_minecraft_versions": applied.supported,
            "minecraft_range": new_range,
            "changed": applied.changed or range_changed or java_changed,
        }
    )

    if target not in applied.supported:
        log(
            f"\n  /!\\ Minecraft {target} is NOT marked as compatible yet.\n"
            f"      Nothing proves the mod works at this point. Run the matrix,\n"
            f"      which escalates the frozen dependencies if it has to:\n"
            f"        python3 {ESCALATION_LADDER}\n"
            f"      then, if it passes:\n"
            f"        python3 mc-bump.py --mark-supported\n"
            f"      If it fails:\n"
            f"        python3 mc-bump.py --revert-compat"
        )

    if args.dry_run:
        log("\n--dry-run: no file modified.")
    elif result["changed"]:
        log("\nFiles updated. Remember to resync Gradle in your IDE.")
    else:
        log("\nNothing to change.")

    if args.run_tests:
        failed = run_tests(project, log)
        if failed:
            # Read back what the escalation left in gradle.properties, since those
            # bumps are kept.
            properties = project.paths.gradle_properties.read_text(encoding="utf-8")
            reverted = update_module.revert_compat(project, dry_run=False)
            result["status"] = "tests-failed"
            result["failed_step"] = failed
            result["loader_version"] = read_property(properties, keys["loader"])
            result["api_version"] = read_property(properties, keys["api"])
            result["supported_minecraft_versions"] = reverted[
                "supported_minecraft_versions"
            ]
            result["minecraft_range"] = reverted["minecraft_range"]
            log(
                f"\n=== FAILED: {failed} on Minecraft {target} ===\n"
                f"    Compatibility claims restored, version bumps kept.\n"
                f"    metadata range = {reverted['minecraft_range']}"
            )
            emit_github_output(result)
            return 1

        version, supported, new_range, floors = update_module.mark_supported(
            project, dry_run=False, log=log
        )
        # An escalation rewrote gradle.properties behind our back, so the result
        # has to be re-read rather than kept from before the tests.
        properties = project.paths.gradle_properties.read_text(encoding="utf-8")
        result["status"] = "validated"
        result["loader_version"] = read_property(properties, keys["loader"])
        result["api_version"] = read_property(properties, keys["api"])
        result["mod_version"] = read_property(properties, "mod_version")
        result["supported_minecraft_versions"] = supported
        result["minecraft_range"] = new_range
        log(
            f"\n=== OK: Minecraft {version} builds and the server starts ===\n"
            f"    supported_minecraft_versions = {','.join(supported)}\n"
            f"    metadata range = {new_range}"
        )
        for key, floor in floors.items():
            log(f"    depends.{key} = {floor}  (escalated)")

    if args.json:
        print(json.dumps(result, indent=2))
    emit_github_output(result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
