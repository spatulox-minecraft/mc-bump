#!/usr/bin/env python3
"""Regenerate the Minecraft -> build plugin / Gradle table from Fabric's example mod.

fabric-example-mod keeps one branch per Minecraft version. The commit where a
branch first sets `minecraft_version` to its own name is the toolchain Fabric
built that version with: its Loom line and its Gradle wrapper. Later commits
backport newer tooling to every branch at once, so they say nothing about what
a version NEEDS.

Run by .github/workflows/internal-toolchain.yml every week, and by hand:

    python3 scripts/refresh-toolchain.py            # rewrite the table
    python3 scripts/refresh-toolchain.py --check    # exit 1 if it is out of date

Needs git and the network. Nothing in a mod's pipeline runs this: the table is
shipped with mc-bump and read locally.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.common import Failure  # noqa: E402
from lib.loaders.fabric import TOOLCHAIN_TABLE  # noqa: E402
from lib.toolchain import (  # noqa: E402
    Toolchain,
    dump_table,
    gradle_from_wrapper,
    loom_from_example,
)
from lib.versions import parse_version  # noqa: E402

EXAMPLE_MOD = "https://github.com/FabricMC/fabric-example-mod"


def git(repo: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if check and completed.returncode != 0:
        raise Failure(f"git {' '.join(args)}: {completed.stderr.strip()}")
    return completed.stdout if completed.returncode == 0 else ""


def show(repo: Path, commit: str, path: str) -> str:
    return git(repo, "show", f"{commit}:{path}", check=False)


def toolchain_of(repo: Path, branch: str) -> Toolchain | None:
    """The toolchain of `branch` at the commit that set it to its own version."""
    wanted = f"minecraft_version={branch}"
    history = git(repo, "log", "--reverse", "--format=%H", branch, "--", "gradle.properties")
    for commit in history.split():
        properties = show(repo, commit, "gradle.properties")
        if wanted not in (line.strip() for line in properties.splitlines()):
            continue
        build_script = show(repo, commit, "build.gradle") or show(
            repo, commit, "build.gradle.kts"
        )
        loom = loom_from_example(properties, build_script)
        gradle = gradle_from_wrapper(
            show(repo, commit, "gradle/wrapper/gradle-wrapper.properties")
        )
        if loom and gradle:
            return Toolchain(loom, gradle)
        print(f"  {branch}: unreadable at {commit[:9]} (loom={loom}, gradle={gradle})")
        return None
    print(f"  {branch}: no commit sets minecraft_version={branch}")
    return None


def build_table(source: str) -> dict[str, Toolchain]:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "example.git"
        completed = subprocess.run(
            ["git", "clone", "--quiet", "--bare", source, str(repo)],
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise Failure(f"cannot clone {source}: {completed.stderr.strip()}")

        branches = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
        table = {}
        for branch in branches.split():
            # Only branches named after a release: a candidate or a snapshot is
            # governed by the release it leads to (see lib/toolchain.py).
            if parse_version(branch) is None:
                continue
            toolchain = toolchain_of(repo, branch)
            if toolchain:
                table[branch] = toolchain
    if not table:
        raise Failure(f"no toolchain read from {source}, refusing to write an empty table")
    return table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", default=EXAMPLE_MOD, help="example mod repository")
    parser.add_argument("--output", type=Path, default=TOOLCHAIN_TABLE)
    parser.add_argument(
        "--check",
        action="store_true",
        help="write nothing, exit 1 when the table is out of date",
    )
    args = parser.parse_args()

    rendered = dump_table(build_table(args.source), args.source)
    current = args.output.read_text(encoding="utf-8") if args.output.is_file() else ""

    if rendered == current:
        print(f"{args.output} is up to date.")
        return 0
    if args.check:
        print(f"{args.output} is out of date.")
        return 1
    args.output.write_text(rendered, encoding="utf-8")
    print(f"{args.output} rewritten.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
