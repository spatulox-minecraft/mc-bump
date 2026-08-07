"""Glob translation and comment stripping.

The glob half is thin on purpose: `fnmatch.translate()` does the translation, so
what is tested here is the three things layered on top of it — the unanchoring,
the line-by-line matching, and the `<count>` capture — rather than glob semantics,
which are the standard library's problem.
"""

from __future__ import annotations

import unittest

from lib.common import Failure
from lib.patterns import Matcher, compile_pattern, strip_comments

LOG_LINE = "[13:48:19] [main/INFO] (extended-time-potion) Registered 50 potions"


def glob(pattern: str, **kwargs) -> Matcher:
    return compile_pattern(pattern, None, where="test", **kwargs)


def regex(pattern: str, **kwargs) -> Matcher:
    return compile_pattern(None, pattern, where="test", **kwargs)


class GlobTest(unittest.TestCase):
    def test_a_bare_phrase_matches_anywhere_in_the_line(self):
        """Anchored, a log line's timestamp and logger prefix would defeat it."""
        self.assertTrue(glob("Brewing mixes registered").search("[13:48] (mod) Brewing mixes registered"))

    def test_star_and_question_mark(self):
        self.assertTrue(glob("Registered * potions").search(LOG_LINE))
        self.assertTrue(glob("Registered ?? potions").search(LOG_LINE))
        self.assertFalse(glob("Registered ??? potions").search(LOG_LINE))

    def test_a_character_class(self):
        self.assertTrue(glob("Registered [0-9][0-9] potions").search(LOG_LINE))
        self.assertFalse(glob("Registered [a-z]* potions").search(LOG_LINE))

    def test_regex_metacharacters_are_literal(self):
        """The whole point of glob: `(` and `+` mean themselves."""
        self.assertTrue(glob("*= registerPotion(*").search("  X = registerPotion(\"a\","))
        self.assertTrue(glob("*Done (1.2s)!*").search("[main] Done (1.2s)! For help"))
        self.assertFalse(glob("*a+*").search("aaa"))
        self.assertTrue(glob("*a+*").search("a+b"))

    def test_a_glob_never_crosses_a_newline(self):
        """fnmatch.translate emits (?s:...), so this needs matching line by line."""
        self.assertIsNone(glob("Registered * potions").search("Registered foo\nbar potions"))

    def test_count_captures_the_number(self):
        matcher = glob("Registered <count> potions", need_capture=True)
        found = matcher.search(LOG_LINE)
        self.assertEqual(matcher.captured(found), "50")

    def test_count_only_matches_digits(self):
        matcher = glob("Registered <count> potions", need_capture=True)
        self.assertIsNone(matcher.search("Registered fifty potions"))

    def test_a_count_pattern_without_the_token_is_refused(self):
        with self.assertRaises(Failure) as caught:
            glob("Registered * potions", need_capture=True)
        self.assertIn("<count>", str(caught.exception))

    def test_the_token_outside_expect_count_is_refused(self):
        with self.assertRaises(Failure):
            glob("Registered <count> potions")

    def test_counting_lines_the_way_grep_does(self):
        source = "a = marker(1)\n// b = marker(2)\nc = marker(3)\n"
        self.assertEqual(glob("*= marker(*").count_lines(source), 3)


class RegexEscapeHatchTest(unittest.TestCase):
    def test_a_regex_still_works(self):
        self.assertTrue(regex(r"Registered \d+ potions").search(LOG_LINE))

    def test_its_capture_group_is_used(self):
        matcher = regex(r"Registered ([0-9]+) potions", need_capture=True)
        self.assertEqual(matcher.captured(matcher.search(LOG_LINE)), "50")

    def test_a_regex_without_a_group_is_refused_where_a_number_is_needed(self):
        with self.assertRaises(Failure) as caught:
            regex("Registered [0-9]+ potions", need_capture=True)
        self.assertIn("capture group", str(caught.exception))

    def test_both_keys_at_once_is_refused(self):
        with self.assertRaises(Failure) as caught:
            compile_pattern("a", "b", where="test")
        self.assertIn("not both", str(caught.exception))

    def test_neither_key_is_refused(self):
        with self.assertRaises(Failure):
            compile_pattern(None, None, where="test")

    def test_an_invalid_regex_names_itself(self):
        with self.assertRaises(Failure) as caught:
            regex("Registered ([0-9]+ potions")
        self.assertIn("invalid regex", str(caught.exception))


class StripCommentsTest(unittest.TestCase):
    def test_a_line_comment_goes(self):
        self.assertEqual(strip_comments("code(); // gone\nmore();"), "code(); \nmore();")

    def test_a_block_comment_goes_but_its_newlines_stay(self):
        """Otherwise the line before it would merge with the line after."""
        stripped = strip_comments("a();\n/* one\n   two */\nb();")
        self.assertNotIn("one", stripped)
        self.assertEqual(stripped.count("\n"), 3)

    def test_a_javadoc_mention_no_longer_counts(self):
        """The bug mc-bump's own fixture hit: naming the pattern in a docstring."""
        source = (
            "/**\n"
            " * Adding a `= marker(` line moves the expectation.\n"
            " */\n"
            "static final String A = marker(\"a\");\n"
            "static final String B = marker(\"b\");\n"
        )
        matcher = glob("*= marker(*")
        self.assertEqual(matcher.count_lines(source), 3)
        self.assertEqual(matcher.count_lines(strip_comments(source)), 2)

    def test_a_url_in_a_string_is_not_a_comment(self):
        """The classic trap: truncating at // would eat the rest of the line."""
        source = 'String url = "http://example.com"; // real comment'
        self.assertEqual(
            strip_comments(source), 'String url = "http://example.com"; '
        )

    def test_a_comment_marker_inside_a_string_survives(self):
        self.assertEqual(strip_comments('x = "/* not a comment */";'),
                         'x = "/* not a comment */";')

    def test_an_escaped_quote_does_not_end_the_string(self):
        source = 'x = "he said \\"hi\\" // still a string"; // gone'
        self.assertEqual(strip_comments(source),
                         'x = "he said \\"hi\\" // still a string"; ')

    def test_a_char_literal_holding_a_quote(self):
        self.assertEqual(strip_comments("c = '\\''; // gone"), "c = '\\''; ")

    def test_an_unterminated_block_comment_eats_the_rest(self):
        self.assertEqual(strip_comments("a();\n/* forever").strip(), "a();")

    def test_the_hash_style(self):
        self.assertEqual(strip_comments("value = 1  # gone", style="hash"), "value = 1  ")

    def test_none_leaves_everything(self):
        source = "a(); // kept"
        self.assertEqual(strip_comments(source, style="none"), source)

    def test_an_unknown_style_names_the_valid_ones(self):
        with self.assertRaises(Failure) as caught:
            strip_comments("x", style="lisp")
        self.assertIn("hash", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
