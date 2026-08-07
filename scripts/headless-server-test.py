#!/usr/bin/env python3
"""Boot a headless dedicated server with the mod and check it actually worked.

Usable locally, from the mod repository:

    python3 /path/to/mc-bump/scripts/headless-server-test.py

Environment variables are still honoured, so the CI steps and the habits built
around the shell version keep working:

    RUN_DIR       run directory                        (default: run)
    LOG           log file produced                    (default: server-test.log)
    BOOT_TIMEOUT  seconds before giving up on startup  (config)
    STOP_TIMEOUT  seconds before force killing the JVM (config)
    EXPECTED_MC   Minecraft version that must boot     (gradle.properties)
    GRADLE_ARGS   extra gradle arguments               (default: none)
    LEVEL_NAME    world name, wiped before each run    (default: ci-smoke-test)
    MOD_ROOT      the mod repository                   (walked up from the cwd)
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import config as config_module  # noqa: E402
from lib.common import Failure  # noqa: E402
from lib.server_test import ServerTest, run  # noqa: E402


def env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise Failure(f"{name}={value!r} is not a number") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", help="mod repository (default: walk up from the cwd)")
    parser.add_argument("--log", default=os.environ.get("LOG", "server-test.log"))
    parser.add_argument("--minecraft", default=os.environ.get("EXPECTED_MC"))
    parser.add_argument("--run-dir", default=os.environ.get("RUN_DIR", "run"))
    parser.add_argument(
        "--level-name", default=os.environ.get("LEVEL_NAME", "ci-smoke-test")
    )
    args = parser.parse_args()

    project = config_module.load(Path(args.root) if args.root else None)
    project.paths.require()

    run(
        ServerTest(
            project=project,
            log=Path(args.log),
            expected_minecraft=args.minecraft or None,
            # a deliberate list of gradle flags, so splitting is the point
            gradle_args=tuple(shlex.split(os.environ.get("GRADLE_ARGS", ""))),
            run_dir=args.run_dir,
            level_name=args.level_name,
            boot_timeout=env_int("BOOT_TIMEOUT"),
            stop_timeout=env_int("STOP_TIMEOUT"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as error:
        print(f"\n=== FAILED: {error} ===", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
