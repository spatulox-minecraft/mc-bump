"""gradle.properties and JSON metadata I/O, over one mod repository.

Every path is carried by ModPaths rather than by module level constants: mc-bump
runs from its own checkout and edits SOMEONE ELSE'S repository, so "the repo" is
never the directory this file lives in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .common import Failure


@dataclass(frozen=True)
class ModPaths:
    """Where the files mc-bump edits live inside the mod repository."""

    root: Path
    gradle_properties: Path
    metadata: Path
    mixins: Path | None
    state: Path
    gradle_wrapper: Path

    @classmethod
    def under(
        cls, root: Path, metadata: str, mixins: str | None = None
    ) -> "ModPaths":
        root = Path(root)
        return cls(
            root=root,
            gradle_properties=root / "gradle.properties",
            metadata=root / metadata,
            mixins=(root / mixins) if mixins else None,
            state=root / ".mc-update-state.json",
            gradle_wrapper=root / "gradle/wrapper/gradle-wrapper.properties",
        )

    def require(self) -> None:
        """Fail early and by name, rather than on a confusing read further down."""
        required = [self.gradle_properties, self.metadata]
        if self.mixins is not None:
            required.append(self.mixins)
        for path in required:
            if not path.exists():
                raise Failure(
                    f"{path} not found — run mc-bump from the mod repository, "
                    f"or fix the path in .github/mc-bump.yml"
                )


# --------------------------------------------------------------------------
# gradle.properties
# --------------------------------------------------------------------------
def read_property(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def set_property(text: str, key: str, value: str) -> str:
    pattern = rf"^{re.escape(key)}=.*$"
    if not re.search(pattern, text, re.MULTILINE):
        raise Failure(f"key '{key}' not found in gradle.properties")
    # lambda so that \1, \g<...> etc. in the value are not interpreted
    return re.sub(pattern, lambda _: f"{key}={value}", text, count=1, flags=re.MULTILINE)


def split_supported(raw: str | None) -> list[str]:
    return [v.strip() for v in (raw or "").split(",") if v.strip()]


def write_preserving_final_newline(path: Path, original: str, new: str) -> None:
    """Write `new`, keeping the presence (or absence) of a final newline."""
    if original.endswith("\n") and not new.endswith("\n"):
        new += "\n"
    elif not original.endswith("\n"):
        new = new.rstrip("\n")
    path.write_text(new, encoding="utf-8")


# --------------------------------------------------------------------------
# The Gradle wrapper
# --------------------------------------------------------------------------
WRAPPER_DISTRIBUTION = re.compile(
    r"^distributionUrl=.*gradle-([0-9][0-9A-Za-z.\-]*?)-(?:bin|all)\.zip\s*$", re.MULTILINE
)


def read_wrapper_gradle_version(paths: ModPaths) -> str | None:
    """The Gradle version the mod builds with, from its wrapper.

    None without a wrapper, or with a distributionUrl this cannot read: the
    caller then applies no Gradle constraint rather than guessing one.
    """
    try:
        text = paths.gradle_wrapper.read_text(encoding="utf-8")
    except OSError:
        return None
    match = WRAPPER_DISTRIBUTION.search(text)
    return match.group(1) if match else None


def gradle_version_key(version: str) -> tuple:
    """Order Gradle versions: "9.7" equals "9.7.0", "9.10.0" follows "9.9.0".

    A pre-release ("9.7.0-rc-1") sorts just below its release, so it does not
    satisfy a plugin that requires 9.7.0.
    """
    base, _, suffix = version.partition("-")
    numbers = []
    for chunk in base.split("."):
        if not chunk.isdigit():
            break
        numbers.append(int(chunk))
    while len(numbers) < 3:
        numbers.append(0)
    return (*numbers, 0 if suffix else 1)


# --------------------------------------------------------------------------
# JSON metadata (fabric.mod.json, the mixin config)
# --------------------------------------------------------------------------
def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Failure(f"{path} is not valid JSON: {exc}") from exc


def write_json(path: Path, data: dict, dry_run: bool) -> None:
    """Rewrite a JSON file with tab indentation, the Fabric convention."""
    if dry_run:
        return
    original = path.read_text(encoding="utf-8")
    write_preserving_final_newline(
        path, original, json.dumps(data, indent="\t", ensure_ascii=False)
    )
