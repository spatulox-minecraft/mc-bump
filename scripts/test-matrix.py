#!/usr/bin/env python3
"""Build the mod and boot a server for EVERY Minecraft version it claims.

Usable locally, from the mod repository:

    python3 /path/to/mc-bump/scripts/test-matrix.py
    MC_VERSIONS="26.1 26.1.1" python3 .../test-matrix.py    # restrict the run

Environment variables:
    MC_VERSIONS  space or newline separated list  (default: the claimed versions)
    STATUS_FILE  where the outcome is recorded    (default: test-matrix-status.txt)
    plus everything headless-server-test.py accepts (BOOT_TIMEOUT, RUN_DIR...)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import config as config_module  # noqa: E402
from lib.common import Failure  # noqa: E402
from lib.matrix import STATUS_FILE, run_matrix  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", help="mod repository (default: walk up from the cwd)")
    parser.add_argument(
        "--minecraft",
        nargs="*",
        help="versions to test (default: $MC_VERSIONS, else the claimed ones)",
    )
    parser.add_argument("--status-file", default=os.environ.get("STATUS_FILE", STATUS_FILE))
    args = parser.parse_args()

    project = config_module.load(Path(args.root) if args.root else None)
    project.paths.require()

    versions = args.minecraft
    if not versions:
        versions = os.environ.get("MC_VERSIONS", "").split() or None

    result = run_matrix(project, versions, status_file=args.status_file)
    return 0 if result.ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as error:
        print(f"\n=== FAILED: {error} ===", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
