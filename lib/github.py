"""Render values for $GITHUB_OUTPUT.

One writer for every emitter, because `key=value` is not a format: a value
carrying a newline silently becomes a SECOND output, and GitHub keeps the last
occurrence of a key. A mod's own `notify.label` could therefore rewrite `ci`,
`release` or `stores` — a green pipeline branching on a forged value, which is
the failure mode this repo exists to prevent.

Single-line values keep the plain `key=value` spelling so the runner log stays
readable; anything else uses the heredoc form GitHub documents, with a random
delimiter so the value cannot close its own block.
"""

from __future__ import annotations

import secrets

from .common import Failure


def render(value: object) -> str:
    """The one place a Python value becomes a GitHub output string."""
    # `is True` rather than truthiness: an int must not be spelled "true".
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(render(item) for item in value)
    return str(value)


def output(pairs: dict[str, object]) -> str:
    """key=value lines, heredoc-quoted where a value spans several lines."""
    lines = []
    for key, value in pairs.items():
        text = render(value)
        if "\n" not in text and "\r" not in text:
            lines.append(f"{key}={text}")
            continue
        delimiter = f"ghadelim_{secrets.token_hex(16)}"
        if delimiter in text:  # pragma: no cover - 128 bits of luck
            raise Failure(f"{key}: cannot quote a value containing {delimiter}")
        lines.append(f"{key}<<{delimiter}\n{text}\n{delimiter}")
    return "\n".join(lines)
