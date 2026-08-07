#!/usr/bin/env python3
"""Run the version matrix, escalating the frozen dependencies when it fails.

Usable locally, from the mod repository:

    python3 /path/to/mc-bump/scripts/test-with-escalation.py

Environment variables:
    ESCALATION_FILE  report of the bumps applied  (default: test-escalation.txt)
    plus everything test-matrix.py accepts (MC_VERSIONS, STATUS_FILE...) and
    everything headless-server-test.py accepts (BOOT_TIMEOUT, RUN_DIR...)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import config as config_module  # noqa: E402
from lib.common import Failure  # noqa: E402
from lib.matrix import ESCALATION_FILE, STATUS_FILE, run_with_escalation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", help="mod repository (default: walk up from the cwd)")
    parser.add_argument("--status-file", default=os.environ.get("STATUS_FILE", STATUS_FILE))
    parser.add_argument(
        "--escalation-file", default=os.environ.get("ESCALATION_FILE", ESCALATION_FILE)
    )
    args = parser.parse_args()

    project = config_module.load(Path(args.root) if args.root else None)
    project.paths.require()

    result, _ = run_with_escalation(
        project, status_file=args.status_file, escalation_file=args.escalation_file
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as error:
        print(f"\n=== FAILED: {error} ===", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
