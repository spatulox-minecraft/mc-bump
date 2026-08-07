"""The failure report. What the user actually reads when something breaks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lib import report
from lib.common import Failure


def write_report(directory: Path, name: str, *, title: str, kind: str, log: str, failed=True):
    folder = directory / f"failure-report-{name}"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(
        json.dumps({"title": title, "kind": kind, "failed": failed}), encoding="utf-8"
    )
    (folder / "log.txt").write_text(log, encoding="utf-8")


class CollectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_only_failed_jobs_are_reported(self):
        write_report(self.dir, "unit", title="Unit tests", kind="unit", log="boom")
        write_report(
            self.dir, "gametest", title="Gametest", kind="gametest", log="ok", failed=False
        )
        blocks = report.collect(self.dir)
        self.assertEqual([b.title for b in blocks], ["Unit tests"])

    def test_blocks_come_out_most_severe_first(self):
        write_report(self.dir, "gametest", title="Gametest", kind="gametest", log="x")
        write_report(self.dir, "unit", title="Unit tests", kind="unit", log="x")
        write_report(self.dir, "server", title="Server 26.2", kind="server", log="x")
        write_report(self.dir, "build", title="Build 26.1", kind="build", log="x")

        self.assertEqual(
            [b.kind for b in report.collect(self.dir)],
            ["build", "server", "unit", "gametest"],
        )

    def test_a_malformed_report_is_kept_under_a_fallback_title(self):
        """Losing the evidence of a failure is worse than an ugly title."""
        folder = self.dir / "failure-report-mystery"
        folder.mkdir()
        (folder / "meta.json").write_text("{not json", encoding="utf-8")
        (folder / "log.txt").write_text("something happened", encoding="utf-8")

        blocks = report.collect(self.dir)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].title, "failure-report-mystery")
        self.assertIn("something happened", blocks[0].log)

    def test_a_missing_directory_yields_nothing(self):
        self.assertEqual(report.collect(self.dir / "nope"), [])


class DetailsTest(unittest.TestCase):
    def test_the_summary_is_the_title_of_the_failed_test(self):
        block = report.Block(title="Serveur headless — Minecraft 26.1.1", log="x", kind="server")
        rendered = report.details(block, log_tail=10)
        self.assertTrue(
            rendered.startswith(
                "<details><summary>Serveur headless — Minecraft 26.1.1</summary>"
            )
        )
        self.assertTrue(rendered.endswith("</details>"))

    def test_the_log_is_tailed(self):
        block = report.Block(title="t", log="\n".join(str(i) for i in range(100)))
        rendered = report.details(block, log_tail=3)
        self.assertIn("97\n98\n99", rendered)
        self.assertNotIn("\n42\n", rendered)

    def test_a_log_containing_a_code_fence_does_not_break_out(self):
        """A mod printing markdown would otherwise spill into the issue body."""
        block = report.Block(title="t", log="before\n```\nafter")
        rendered = report.details(block, log_tail=10)
        self.assertIn("````", rendered)
        # the inner fence is still inside the outer one
        self.assertLess(rendered.index("````"), rendered.index("```\n"))

    def test_an_empty_log_says_so(self):
        self.assertIn("(empty log)", report.details(report.Block("t", ""), 10))


class IssueBodyTest(unittest.TestCase):
    BLOCKS = [
        report.Block(title="Build — Minecraft 26.2", log="compile error", kind="build"),
        report.Block(title="Gametest client", log="assertion failed", kind="gametest"),
    ]

    def body(self, **kwargs):
        return report.failure_issue_body(
            self.BLOCKS,
            workflow="CI",
            ref="master",
            run_url="https://example.invalid/run/1",
            **kwargs,
        )

    def test_one_details_block_per_failed_job(self):
        body = self.body()
        self.assertEqual(body.count("<details>"), 2)
        self.assertEqual(body.count("</details>"), 2)
        self.assertIn("<summary>Build — Minecraft 26.2</summary>", body)
        self.assertIn("<summary>Gametest client</summary>", body)

    def test_it_says_the_branch_is_kept(self):
        body = self.body(branch="chore/mc-26.2")
        self.assertIn("chore/mc-26.2", body)
        self.assertIn("kept", body)

    def test_it_links_the_run(self):
        self.assertIn("https://example.invalid/run/1", self.body())

    def test_reporting_nothing_is_an_error(self):
        """An empty issue is worse than no issue: it reads as a false alarm."""
        with self.assertRaises(Failure):
            report.failure_issue_body(
                [], workflow="CI", ref="master", run_url="https://example.invalid"
            )

    def test_the_title_is_deterministic_so_re_runs_comment(self):
        self.assertEqual(
            report.failure_issue_title("CI", "master"),
            report.failure_issue_title("CI", "master"),
        )
        self.assertNotEqual(
            report.failure_issue_title("CI", "master"),
            report.failure_issue_title("Release", "master"),
        )


class MatrixTableTest(unittest.TestCase):
    def test_each_outcome_gets_its_own_wording(self):
        table = report.matrix_table("26.1 ok\n26.1.1 server\n26.1.2 build\n")
        self.assertIn("| `26.1` | :white_check_mark: builds and boots |", table)
        self.assertIn("did not start", table)
        self.assertIn("build failed", table)

    def test_blank_lines_are_ignored(self):
        self.assertEqual(len(report.matrix_table("\n\n").splitlines()), 2)  # header only


class EscalationTest(unittest.TestCase):
    def test_no_escalation_renders_nothing(self):
        self.assertEqual(report.escalation_section(""), "")

    def test_a_bump_is_rendered_as_a_row(self):
        section = report.escalation_section("fabric_api_version 0.155.2 0.156.0\n")
        self.assertIn("`fabric_api_version`", section)
        self.assertIn("`0.155.2` -> `0.156.0`", section)


if __name__ == "__main__":
    unittest.main()
