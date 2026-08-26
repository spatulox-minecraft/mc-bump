"""$GITHUB_OUTPUT is a wire format, and a newline is how you forge it.

GitHub keeps the LAST occurrence of a repeated key, so a value that spans two
lines does not corrupt itself — it silently rewrites another output, and the
pipeline branches on the forged one while staying green.
"""

from __future__ import annotations

import unittest
from unittest import mock

from lib import github
from lib.common import Failure


def parse(text: str) -> dict[str, str]:
    """The reader GitHub Actions implements, in the smallest honest form."""
    pairs: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        key, separator, rest = line.partition("<<")
        if separator:
            closing = lines.index(rest, index + 1)
            pairs[key] = "\n".join(lines[index + 1 : closing])
            index = closing + 1
            continue
        key, _, value = line.partition("=")
        pairs[key] = value
        index += 1
    return pairs


class RenderTest(unittest.TestCase):
    def test_booleans_are_lowercase_words(self):
        self.assertEqual(github.render(True), "true")
        self.assertEqual(github.render(False), "false")

    def test_an_int_is_not_mistaken_for_a_boolean(self):
        """`1 == True` in Python; a log-tail of 1 must not print as 'true'."""
        self.assertEqual(github.render(1), "1")
        self.assertEqual(github.render(0), "0")

    def test_none_is_an_empty_value_not_the_word_none(self):
        self.assertEqual(github.render(None), "")

    def test_a_list_is_comma_separated(self):
        self.assertEqual(github.render(["modrinth", "curseforge"]), "modrinth,curseforge")


class OutputTest(unittest.TestCase):
    def test_single_line_values_keep_the_plain_spelling(self):
        """Readable in the runner log, and the format the workflows document."""
        text = github.output({"ci": True, "log_tail": 60, "stores": ["a", "b"]})
        self.assertEqual(text, "ci=true\nlog_tail=60\nstores=a,b")

    def test_a_multiline_value_cannot_forge_another_output(self):
        text = github.output({"label": "boom\nci=true", "ci": False})
        pairs = parse(text)
        self.assertEqual(pairs["label"], "boom\nci=true")
        self.assertEqual(pairs["ci"], "false")
        self.assertEqual(len(pairs), 2)

    def test_the_heredoc_delimiter_is_not_guessable(self):
        """A fixed delimiter would just move the injection one level down."""
        first = github.output({"a": "x\ny"})
        second = github.output({"a": "x\ny"})
        self.assertNotEqual(first, second)

    def test_a_value_containing_the_delimiter_is_refused(self):
        """Unreachable with 128 real bits, so the collision is forced here."""
        with mock.patch.object(github.secrets, "token_hex", return_value="dead"):
            with self.assertRaises(Failure) as caught:
                github.output({"a": "line\nghadelim_dead"})
        self.assertIn("a:", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
