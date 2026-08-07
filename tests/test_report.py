"""The failure report. What the user actually reads when something breaks."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lib import report
from lib.common import Failure


def write_report(directory: Path, name: str, *, title: str, kind: str, log: str = "",
                 failed=True, note=""):
    folder = directory / f"failure-report-{name}"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(
        json.dumps({"title": title, "kind": kind, "failed": failed, "note": note}),
        encoding="utf-8",
    )
    (folder / "log.txt").write_text(log, encoding="utf-8")


class CollectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_passing_tests_are_kept_as_table_rows(self):
        """They say what DID work, which is half of what makes a failure readable."""
        write_report(self.dir, "unit", title="Unit tests", kind="unit", log="boom")
        write_report(
            self.dir, "gametest", title="Gametest", kind="gametest", log="ok", failed=False
        )
        blocks = report.collect(self.dir)
        self.assertEqual([b.title for b in blocks], ["Unit tests", "Gametest"])
        self.assertEqual([b.title for b in report.failed_only(blocks)], ["Unit tests"])

    def test_failures_sort_before_passes(self):
        write_report(self.dir, "a", title="Build ok", kind="build", log="", failed=False)
        write_report(self.dir, "b", title="Gametest ko", kind="gametest", log="x")
        self.assertEqual(
            [b.title for b in report.collect(self.dir)], ["Gametest ko", "Build ok"]
        )

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


class TestReportTest(unittest.TestCase):
    """The table + collapsible logs pasted into BOTH the pull request and the issue."""

    BLOCKS = [
        report.Block(title="Headless server — 26.1.1", log="boom", kind="server"),
        report.Block(
            title="Client gametest", log="assertion failed", kind="gametest",
            note="non blocking",
        ),
        report.Block(title="Unit tests", kind="unit", failed=False),
    ]

    def test_every_test_gets_a_row_passed_or_failed(self):
        rendered = report.test_report(self.BLOCKS)
        self.assertIn("| Headless server — 26.1.1 | :x: |", rendered)
        self.assertIn("| Unit tests | :white_check_mark: |", rendered)

    def test_the_note_qualifies_the_icon(self):
        self.assertIn("| Client gametest | :x: — non blocking |", report.test_report(self.BLOCKS))

    def test_only_failures_get_a_collapsible_log(self):
        rendered = report.test_report(self.BLOCKS)
        self.assertEqual(rendered.count("<details>"), 2)
        self.assertIn("<summary>Headless server — 26.1.1</summary>", rendered)
        self.assertIn("<summary>Client gametest</summary>", rendered)
        self.assertNotIn("<summary>Unit tests</summary>", rendered)

    def test_the_table_comes_before_the_logs(self):
        rendered = report.test_report(self.BLOCKS)
        self.assertLess(rendered.index("| test | result |"), rendered.index("<details>"))

    def test_nothing_to_report_renders_nothing(self):
        self.assertEqual(report.test_report([]), "")


class MatrixStatusTest(unittest.TestCase):
    """The sequential matrix reports through its status file, not through artifacts."""

    def test_each_outcome_becomes_a_block(self):
        blocks = report.blocks_from_matrix_status("26.1 ok\n26.1.1 server\n26.1.2 build\n")
        by_title = {b.title: b for b in blocks}
        self.assertFalse(by_title["Build + server — Minecraft 26.1"].failed)
        self.assertTrue(by_title["Headless server — Minecraft 26.1.1"].failed)
        self.assertTrue(by_title["Build — Minecraft 26.1.2"].failed)

    def test_it_renders_the_same_way_as_the_job_matrix(self):
        rendered = report.test_report(
            report.blocks_from_matrix_status("26.1 ok\n26.1.1 server\n")
        )
        self.assertIn("| Build + server — Minecraft 26.1 | :white_check_mark: — builds and boots |", rendered)
        self.assertIn("did not start", rendered)

    def test_blank_lines_are_ignored(self):
        self.assertEqual(report.blocks_from_matrix_status("\n\n"), [])

    def test_an_empty_status_file_falls_back_to_the_run_log(self):
        """Empty means the matrix died before its first version, not that nothing ran."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "test-matrix.log").write_text("gradlew: not found", encoding="utf-8")
            blocks = report.blocks_from_matrix_status("", root)
            self.assertEqual(len(blocks), 1)
            self.assertTrue(blocks[0].failed)
            self.assertIn("not found", blocks[0].log)


class EscalationTest(unittest.TestCase):
    def test_no_escalation_renders_nothing(self):
        self.assertEqual(report.escalation_section(""), "")

    def test_a_bump_is_rendered_as_a_row(self):
        section = report.escalation_section("fabric_api_version 0.155.2 0.156.0\n")
        self.assertIn("`fabric_api_version`", section)
        self.assertIn("`0.155.2` -> `0.156.0`", section)


if __name__ == "__main__":
    unittest.main()
