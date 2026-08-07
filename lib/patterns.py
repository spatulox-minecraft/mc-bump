"""Glob patterns, and reading a source file without its comments.

**Glob by default, and it is the stdlib's glob.** What a mod owner writes in
`.github/mc-bump.yml` is a phrase they expect in a log, not a regular expression.
`Registered * markers` says what it means; `Registered [0-9]+ markers` requires
knowing that `+` is a quantifier and that `(` would have to be escaped. Regex
stays reachable through the `regex:` key for what a glob genuinely cannot say.

Nothing here implements glob. `fnmatch.translate()` does the whole translation —
the same one `fnmatch`, `pathlib.Path.glob` and the shell agree on.

Two things are built on top of it:

*Unanchored.* `translate()` anchors, and a log line carries a timestamp and a
logger prefix, so an anchored `Registered * markers` would never match. The
pattern is wrapped in `*` before translation, so the unanchoring is the glob's own
rather than surgery on translate's output.

*Line by line.* `translate()` emits `(?s:...)`, so `*` crosses newlines and
`Registered * markers` would happily match a "Registered foo" line followed by a
"bar markers" one. Matching is therefore per line, the way grep does it.

`<count>` is the one addition to glob syntax, because `expect-count` has to pull a
number out of the line and glob has no capture groups. It is passed through
`translate()` as an unprintable sentinel — which the translation preserves
verbatim, being no metacharacter — and swapped for a group afterwards.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from .common import Failure

COUNT_TOKEN = "<count>"
COUNT_GROUP = "mcbump_count"

#: Not a glob metacharacter and not escaped by re.escape, so fnmatch.translate
#: carries it through untouched — and it cannot occur in a config file.
_SENTINEL = "\x00"

COMMENT_STYLES = ("c", "hash", "none")


def glob_to_regex(pattern: str) -> str:
    """Translate a glob into an unanchored, line oriented regex.

        *          anything, including nothing
        ?          exactly one character
        [abc]      one of these        [!abc]   none of these
        <count>    a number, captured — expect-count only

    Everything else is literal, which is the whole point: `= marker(` and
    `Done (1.2s)!` mean themselves.
    """
    if _SENTINEL in pattern:
        raise Failure("a pattern cannot contain a NUL character")
    marked = pattern.replace(COUNT_TOKEN, _SENTINEL)
    translated = fnmatch.translate(f"*{marked}*")
    return translated.replace(_SENTINEL, f"(?P<{COUNT_GROUP}>[0-9]+)")


@dataclass(frozen=True)
class Matcher:
    """A compiled pattern that remembers how it was written, for error messages."""

    regex: re.Pattern
    source: str
    syntax: str  # "glob" or "regex"
    captures: bool

    def __str__(self) -> str:
        return self.source

    def _match(self, line: str):
        # fullmatch for a glob (translate() anchors, and the wrapping `*` is what
        # makes it unanchored), search for a regex (the user anchors it or not).
        return self.regex.fullmatch(line) if self.syntax == "glob" else self.regex.search(line)

    def search(self, text: str):
        """First line of `text` the pattern matches, or None."""
        for line in text.splitlines():
            found = self._match(line)
            if found:
                return found
        return None

    def count_lines(self, text: str) -> int:
        """Lines `text` the pattern matches, the way `grep -c` counts them."""
        return sum(1 for line in text.splitlines() if self._match(line))

    def captured(self, match) -> str | None:
        if not self.captures or match is None:
            return None
        if COUNT_GROUP in (self.regex.groupindex or {}):
            return match.group(COUNT_GROUP)
        return match.group(1)


def compile_pattern(
    glob: str | None, regex: str | None, *, where: str, need_capture: bool = False
) -> Matcher:
    """Build a Matcher from whichever of the two keys the config used."""
    if glob and regex:
        raise Failure(f"{where}: use either a glob 'pattern' or a 'regex', not both")
    if not glob and not regex:
        raise Failure(f"{where}: one of 'pattern' (glob) or 'regex' is required")

    if glob:
        source, syntax = glob, "glob"
        captures = COUNT_TOKEN in glob
        if need_capture and not captures:
            raise Failure(
                f"{where}: the glob '{glob}' has no {COUNT_TOKEN}, so there is no "
                f"number to compare. Write it where the number appears, e.g. "
                f"'Registered {COUNT_TOKEN} markers'."
            )
        if not need_capture and captures:
            raise Failure(
                f"{where}: {COUNT_TOKEN} only means something under 'expect-count'"
            )
        expression = glob_to_regex(glob)
    else:
        source, syntax, expression, captures = regex, "regex", regex, False

    try:
        compiled = re.compile(expression)
    except re.error as exc:
        raise Failure(f"{where}: invalid {syntax} '{source}': {exc}") from exc

    if syntax == "regex":
        captures = compiled.groups > 0
        if need_capture and not captures:
            raise Failure(
                f"{where}: the regex '{source}' has no capture group, so there is "
                f"no number to compare"
            )

    return Matcher(regex=compiled, source=source, syntax=syntax, captures=captures)


# --------------------------------------------------------------------------
# Comments
# --------------------------------------------------------------------------
def strip_comments(text: str, style: str = "c") -> str:
    """Blank out comments, keeping every newline so line counting still works.

    Hand written because the standard library has no Java tokenizer — `tokenize`
    only speaks Python. Kept to the one hard part, which is being STRING AWARE:
    truncating at the first `//` would cut `String url = "http://example.com";`
    in half and silently drop the rest of the line.

    Java text blocks are treated as one ordinary string, so their content is never
    stripped — erring towards counting too much rather than losing a real
    registration.
    """
    if style == "none":
        return text
    if style not in COMMENT_STYLES:
        raise Failure(
            f"comment-style: '{style}' is not one of {', '.join(COMMENT_STYLES)}"
        )

    out: list[str] = []
    index = 0
    length = len(text)
    quote: str | None = None

    while index < length:
        char = text[index]

        if quote is not None:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue

        if char in "\"'":
            quote = char
            out.append(char)
            index += 1
            continue

        if style == "c" and text.startswith("//", index):
            while index < length and text[index] != "\n":
                index += 1
            continue

        if style == "c" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            # the newlines survive, so a block comment does not merge the line
            # before it with the line after it
            out.append("\n" * text.count("\n", index, end))
            index = end
            continue

        if style == "hash" and char == "#":
            while index < length and text[index] != "\n":
                index += 1
            continue

        out.append(char)
        index += 1

    return "".join(out)
