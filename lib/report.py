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
    """One test, passed or failed, as it appears in the report."""

    title: str
    log: str = ""
    kind: str = "other"
    failed: bool = True
    #: qualifier shown next to the icon, e.g. "non blocking"
    note: str = ""

    @property
    def rank(self) -> int:
        # Failures first, then by severity: a reader scanning the table should
        # meet what broke before what worked.
        return (0 if self.failed else 1, SEVERITY.get(self.kind, len(SEVERITY)))


def collect(directory: Path) -> list[Block]:
    """Read every report written under `directory`, failures first.

    Passing tests are kept: they are the rows of the summary table that say what
    DID work, which is half of what makes a failure readable. Only the failures
    get a collapsible log.

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
                failed=bool(meta.get("failed", True)),
                note=meta.get("note") or "",
            )
        )

    return sorted(blocks, key=lambda block: (block.rank, block.title))


def failed_only(blocks: list[Block]) -> list[Block]:
    return [block for block in blocks if block.failed]


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


def test_report(blocks: list[Block], log_tail: int = 100, heading: str = "## Test results") -> str:
    """The one thing pasted into BOTH the pull request and the issue.

    A table saying at a glance what passed and what did not, then one collapsible
    block per failure holding its log. Rendered from the same data in both places,
    so a reader never has to correlate two differently shaped reports — which is
    what the previous split did: a per-version table in the pull request, and
    logs with no table in the issue.
    """
    if not blocks:
        return ""

    rows = ["| test | result |", "|---|---|"]
    for block in blocks:
        icon = ICON_KO if block.failed else ICON_OK
        note = f" — {block.note}" if block.note else ""
        rows.append(f"| {block.title} | {icon}{note} |")

    parts = [heading, "\n".join(rows)]
    parts.extend(details(block, log_tail) for block in failed_only(blocks))
    return "\n\n".join(parts)


def blocks_from_matrix_status(
    status_text: str,
    root: Path | None = None,
    fallback_log: str = "test-matrix.log",
    fallback_title: str = "Version matrix",
) -> list[Block]:
    """Turn the matrix status file into report blocks, logs included.

    The sequential matrix (the escalation ladder) runs inside ONE job, so it
    produces no per-version artifact the way the GitHub job matrix does. Its
    status file plus the per-version logs it leaves behind carry the same
    information, and this is what makes the two paths render identically.

    An EMPTY status file is not "nothing happened": the matrix truncates it on
    start, so an empty one means it died before finishing its first version. The
    fallback turns that into one block holding the run log, rather than a report
    that silently shows no test at all.
    """
    blocks: list[Block] = []
    for line in status_text.splitlines():
        parts = line.split()
        if not parts:
            continue
        version = parts[0]
        outcome = parts[1] if len(parts) > 1 else "build"

        if outcome == "ok":
            blocks.append(
                Block(
                    title=f"Build + server — Minecraft {version}",
                    kind="server",
                    failed=False,
                    note="builds and boots",
                )
            )
            continue

        if outcome == "server":
            title = f"Headless server — Minecraft {version}"
            kind = "server"
            note = "builds, but the server did not start"
            log_name = f"server-test-{version}.log"
        else:
            title = f"Build — Minecraft {version}"
            kind = "build"
            note = "build failed"
            log_name = f"build-{version}.log"

        log = ""
        if root is not None:
            candidate = root / log_name
            if candidate.is_file():
                log = candidate.read_text(encoding="utf-8", errors="replace")
        blocks.append(Block(title=title, log=log or "(no log)", kind=kind, note=note))

    if not blocks and root is not None:
        candidate = root / fallback_log
        if candidate.is_file():
            blocks.append(
                Block(
                    title=fallback_title,
                    log=candidate.read_text(encoding="utf-8", errors="replace"),
                    kind="build",
                    note="never reached a single version",
                )
            )

    return sorted(blocks, key=lambda block: (block.rank, block.title))


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
    failures = failed_only(blocks)
    if not failures:
        raise Failure("no failed job to report")

    parts = [intro.strip()] if intro.strip() else []
    names = ", ".join(f"**{block.title}**" for block in failures)
    parts.append(f"{len(failures)} test(s) failed on `{ref}`: {names}")

    if branch:
        parts.append(
            f"The branch `{branch}` is **kept**, so the failure can be reproduced "
            f"and fixed from where it happened."
        )

    if extra.strip():
        parts.append(extra.strip())

    # The same table + collapsible logs the pull request shows, from the same data.
    parts.append(test_report(blocks, log_tail))
    parts.append(f"Full logs in the [run artifacts]({run_url}).")
    return "\n\n".join(parts) + "\n"


# --------------------------------------------------------------------------
# The update pull request
# --------------------------------------------------------------------------
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
    blocks: list,
    escalation: str,
    run_url: str,
    workflow_file: str,
    log_tail: int = 100,
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

    # Every version covered by the compatibility range is built and booted with
    # the resolved dependencies above, not just the newest one — and the unit
    # tests and the gametest sit in the same table, so the pull request shows
    # everything that ran rather than the matrix alone.
    sections.append(
        test_report(blocks, log_tail, heading="## Test results")
        + f"\n\nThe full logs are available in the artifacts of the [run]({run_url})."
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
    parser.add_argument(
        "--pr-body",
        metavar="JSON",
        help="body of the update pull request, from a JSON file holding the "
        "keyword arguments of pr_body(). A file rather than a pile of flags: the "
        "workflow already has every value in hand and jq can assemble them "
        "without any shell quoting.",
    )
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument(
        "--matrix-status",
        help="status file of the SEQUENTIAL matrix, folded into the same table as "
        "the other tests. The GitHub job matrix reports through --reports-dir "
        "instead, one artifact per version.",
    )
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

    def all_blocks() -> list[Block]:
        """Every test that ran, whichever shape the pipeline reported it in."""
        blocks = collect(Path(args.reports_dir))
        if args.matrix_status and Path(args.matrix_status).is_file():
            blocks += blocks_from_matrix_status(
                Path(args.matrix_status).read_text(encoding="utf-8"), Path.cwd()
            )
        return sorted(blocks, key=lambda block: (block.rank, block.title))

    if args.pr_body:
        try:
            data = json.loads(Path(args.pr_body).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise Failure(f"cannot read {args.pr_body}: {exc}") from exc
        try:
            rendered = pr_body(blocks=all_blocks(), log_tail=args.log_tail, **data)
        except TypeError as exc:
            raise Failure(f"{args.pr_body} does not describe a pull request: {exc}") from exc
    elif args.failure_title:
        rendered = failure_issue_title(args.workflow, args.ref)
    elif args.failure_issue:
        rendered = failure_issue_body(
            all_blocks(),
            workflow=args.workflow,
            ref=args.ref,
            run_url=args.run_url,
            log_tail=args.log_tail,
            branch=args.branch or None,
            intro=args.intro,
            extra=args.extra,
        )
    else:
        parser.error("choose --failure-issue, --failure-title or --pr-body")

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
