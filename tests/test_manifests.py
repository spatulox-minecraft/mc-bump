"""The action manifests and workflows are templates, not documents.

A GitHub runner evaluates every string in a manifest before running anything —
an input's `description:` included. Writing an example expression there is not
documentation, it is code: `${{ steps.<id>.outcome }}` in a description made the
whole file unloadable with "Unrecognized named-value: 'steps'", and no step of
the job ever ran.

actionlint does not catch this, and neither did six months of green runs: every
caller of the broken composite lived in a reusable workflow that only a mod's
repository ever executes. Hence a test.
"""

from __future__ import annotations

import pathlib
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFESTS = sorted((ROOT / ".github" / "actions").glob("*/action.yml"))
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def descriptions(node, path: str = ""):
    """Every `description:` string in a parsed manifest, with its location."""
    if isinstance(node, dict):
        for key, value in node.items():
            where = f"{path}.{key}" if path else str(key)
            if key == "description" and isinstance(value, str):
                yield where, value
            else:
                yield from descriptions(value, where)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from descriptions(item, f"{path}[{index}]")


class DescriptionTest(unittest.TestCase):
    def test_the_manifests_are_where_we_think_they_are(self):
        # A glob that silently matches nothing would make every other test here
        # pass by testing no file at all.
        self.assertTrue(MANIFESTS, "no action manifest found")
        self.assertTrue(WORKFLOWS, "no workflow found")

    def test_no_description_carries_an_expression(self):
        for path in MANIFESTS + WORKFLOWS:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            for where, text in descriptions(document, ""):
                with self.subTest(file=path.name, key=where):
                    self.assertNotIn(
                        "${{",
                        text,
                        f"{path.relative_to(ROOT)}: {where} spells an expression. "
                        f"The runner evaluates it and refuses to load the file. "
                        f"Write the example without the braces.",
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
