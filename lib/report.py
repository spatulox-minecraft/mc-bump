"""Markdown bodies: the failure issue, the update pull request.

These used to be ~250 lines of bash inlined in a workflow, where nothing could
run them but GitHub. Here they are functions over plain data, so the shape of a
report is checked by tests rather than by opening a pull request.

Report artifact contract
------------------------
Every test job, in `if: always()`, uploads an artifact named
`failure-report-<job>` containing:

    meta.json   {"title": "...", "kind": "unit|build|server|gametest", "failed": true}
    log.txt     the job's log

The reporting job downloads them all into one directory and calls
`--failure-issue`. A job that passed uploads `"failed": false`, which is how the
report knows the difference between "green" and "never ran".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .common import Failure

#: Most severe first. A build failure explains a server failure, which explains a
#: gametest failure, so the reader should meet them in that order.
SEVERITY = {"build": 0, "server": 1, "unit": 2, "gametest": 3}

ICON_OK = ":white_check_mark:"
ICON_KO = ":x:"


@dataclass(frozen=True)
class Block:
    """One failed job, as it appears in the issue."""

    title: str
    log: str
    kind: str = "other"

    @property
    def rank(self) -> int:
        return SEVERITY.get(self.kind, len(SEVERITY))


def collect(directory: Path) -> list[Block]:
    """Read every failure-report-* artifact under `directory`, most severe first.

    A malformed report is kept rather than dropped: losing the evidence of a
    failure is worse than rendering it under a generic title.
    """
    blocks: list[Block] = []
    if not directory.is_dir():
        return blocks

    for report in sorted(directory.iterdir()):
        if not report.is_dir():
            continue
        meta_file = report / "meta.json"
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        if not meta.get("failed", True):
            continue

        log_file = report / "log.txt"
        try:
            log = log_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log = "(no log captured)"

        blocks.append(
            Block(
                title=meta.get("title") or report.name,
                log=log,
                kind=meta.get("kind") or "other",
            )
        )

    return sorted(blocks, key=lambda block: (block.rank, block.title))


def tail(text: str, lines: int) -> str:
    kept = text.rstrip("\n").split("\n")[-lines:]
    return "\n".join(kept) if any(line.strip() for line in kept) else "(empty log)"


def details(block: Block, log_tail: int) -> str:
    """One collapsible block. The summary is the title of the failed test."""
    # The fence is four backticks: a Minecraft log can contain a triple-backtick
    # line (a mod printing markdown, a stack trace with a formatted message) and
    # that would close the block early, spilling the rest into the page.
    return (
        f"<details><summary>{block.title}</summary>\n"
        f"\n"
        f"````\n"
        f"{tail(block.log, log_tail)}\n"
        f"````\n"
        f"\n"
        f"</details>"
    )


def failure_issue_title(workflow: str, ref: str) -> str:
    """Deterministic, so a re-run comments instead of opening a duplicate.

    Filtered by exact title rather than by `gh issue list --search`: the brackets
    are tokenised oddly by the GitHub search syntax and the match becomes fuzzy.
    """
    return f"[auto] {workflow} — tests failing on {ref}"


def failure_issue_body(
    blocks: list[Block],
    *,
    workflow: str,
    ref: str,
    run_url: str,
    log_tail: int = 100,
    branch: str | None = None,
    intro: str = "",
    extra: str = "",
) -> str:
    if not blocks:
        raise Failure("no failed job to report")

    parts = [intro.strip()] if intro.strip() else []
    names = ", ".join(f"**{block.title}**" for block in blocks)
    parts.append(f"{len(blocks)} job(s) failed on `{ref}`: {names}")

    if branch:
        parts.append(
            f"The branch `{branch}` is **kept**, so the failure can be reproduced "
            f"and fixed from where it happened."
        )

    if extra.strip():
        parts.append(extra.strip())

    parts.append(f"Full logs in the [run artifacts]({run_url}).")
    parts.extend(details(block, log_tail) for block in blocks)
    return "\n\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# The update pull request
# --------------------------------------------------------------------------
def matrix_table(status_lines: str) -> str:
    """One row per version the matrix actually ran, from its status file.

    Read from what the matrix WROTE rather than recomputed: by reporting time
    --revert-compat may already have shortened the list of supported versions.
    """
    rows = ["| Minecraft | result |", "|---|---|"]
    for line in status_lines.splitlines():
        parts = line.split()
        if not parts:
            continue
        version, outcome = parts[0], (parts[1] if len(parts) > 1 else "build")
        if outcome == "ok":
            verdict = f"{ICON_OK} builds and boots"
        elif outcome == "server":
            verdict = f"{ICON_KO} builds, but the server did not start"
        else:
            verdict = f"{ICON_KO} build failed"
        rows.append(f"| `{version}` | {verdict} |")
    return "\n".join(rows)


def escalation_table(escalation_lines: str) -> str:
    """"<gradle key> <from> <to>" per line, as the ladder writes it."""
    rows = []
    for line in escalation_lines.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            rows.append(f"| `{parts[0]}` | `{parts[1]}` -> `{parts[2]}` |")
    if not rows:
        return ""
    return "\n".join(["| key | from -> to |", "|---|---|", *rows])


def escalation_section(escalation_lines: str) -> str:
    table = escalation_table(escalation_lines)
    if not table:
        return ""
    return (
        "## Escalation\n\n"
        "The matrix failed with the frozen dependencies, so they were bumped one "
        "at a time, replaying the **whole** matrix after each bump.\n\n"
        f"{table}\n\n"
        "A bump that fixed the matrix is recorded in the mod metadata as a "
        "dependency floor, so a user on an older version is told to update "
        "instead of crashing.\n"
    )


def pr_body(
    *,
    mod_id: str,
    minecraft: str,
    previous: str,
    loader_name: str,
    loader_version: str,
    api_name: str,
    api_version: str,
    available_loader: str,
    available_api: str,
    buildtool_name: str,
    buildtool_version: str,
    java: str,
    mod_version: str,
    compat_range: str,
    tests_passed: bool,
    matrix_status: str,
    escalation: str,
    run_url: str,
    workflow_file: str,
) -> str:
    def frozen_note(in_use: str, available: str) -> str:
        # "frozen" is the normal case and worth saying out loud, so nobody reads
        # an unchanged dependency as the automation having forgotten it.
        if in_use == available:
            return "frozen (already the latest)"
        return f"frozen (latest available: `{available}`)"

    if tests_passed:
        verdict = (
            f"{ICON_OK} **The mod builds and the server starts on every version "
            f"Minecraft `{minecraft}` brings the range to.**"
        )
        compat = f"`{compat_range}` — kept, the matrix proved it."
    else:
        verdict = (
            f"{ICON_KO} **The mod does not work as is on Minecraft `{minecraft}`** "
            f"— draft PR, the dependency diff is already done to start from."
        )
        compat = f"reverted to its previous value — the matrix did not prove `{compat_range}`."

    sections = [
        f"Automatic update of **{mod_id}** to **Minecraft `{minecraft}`** "
        f"(previous: `{previous}`).",
        verdict,
        "## Resolved versions\n\n"
        "| | version | |\n"
        "|---|---|---|\n"
        f"| Minecraft | `{minecraft}` | the only thing an update moves |\n"
        f"| {loader_name} | `{loader_version}` | {frozen_note(loader_version, available_loader)} |\n"
        f"| {api_name} | `{api_version}` | {frozen_note(api_version, available_api)} |\n"
        f"| {buildtool_name} | `{buildtool_version}` | build plugin, follows the latest stable |\n"
        f"| Java | `{java}` | from the Mojang manifest |\n"
        f"| `mod_version` | `{mod_version}` | |\n"
        f"| compatibility range | {compat} | |",
    ]

    escalated = escalation_section(escalation)
    if escalated:
        sections.append(escalated)

    sections.append(
        "## Results, one row per claimed version\n\n"
        "Every version covered by the compatibility range is built and booted "
        "with the resolved dependencies above, not just the newest one.\n\n"
        f"{matrix_table(matrix_status)}\n\n"
        f"The full logs of each version are available in the artifacts of the "
        f"[run]({run_url})."
    )

    sections.append(
        "## To do by hand before merging\n\n"
        f"- [ ] Bump the mod part of `mod_version` if this is a release\n"
        f"- [ ] Add a `## <version>` entry at the top of `CHANGELOG.md` — that is "
        f"what gets published on the stores\n"
        f"- [ ] Test the mod in game"
    )

    sections.append(
        "> `supported_minecraft_versions` and the metadata range are both derived "
        f"from the Minecraft series and are only extended once the build and the "
        f"headless server test pass. {loader_name} and {api_name} are **frozen** "
        f"by an update and only move through the escalation ladder, so a red "
        f"matrix has one suspect instead of three."
    )

    sections.append(f"---\nGenerated by `{workflow_file}` (mc-bump)")
    return "\n\n".join(sections) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mc-bump report", description="Render a report body as markdown."
    )
    parser.add_argument(
        "--failure-issue", action="store_true", help="body of the failure issue"
    )
    parser.add_argument("--failure-title", action="store_true", help="its title")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--workflow", default=os.environ.get("GITHUB_WORKFLOW", "CI"))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF_NAME", "?"))
    parser.add_argument("--run-url", default="")
    parser.add_argument("--branch", default="")
    parser.add_argument("--log-tail", type=int, default=100)
    parser.add_argument("--intro", default="")
    parser.add_argument("--extra", default="")
    parser.add_argument(
        "--out", help="write to this file instead of stdout (avoids quoting problems)"
    )
    args = parser.parse_args(argv)

    if args.failure_title:
        rendered = failure_issue_title(args.workflow, args.ref)
    elif args.failure_issue:
        blocks = collect(Path(args.reports_dir))
        rendered = failure_issue_body(
            blocks,
            workflow=args.workflow,
            ref=args.ref,
            run_url=args.run_url,
            log_tail=args.log_tail,
            branch=args.branch or None,
            intro=args.intro,
            extra=args.extra,
        )
    else:
        parser.error("choose --failure-issue or --failure-title")

    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Failure as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
