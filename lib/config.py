"""Read, validate and export .github/mc-bump.yml.

One file describes a mod to mc-bump, and this module is the only thing that
parses it. It is read by the python CLI AND by the shell scripts (through
`--sh`), so a value can never mean two different things depending on which side
of the pipeline reads it.

Validation is strict and names the offending key: a config that is wrong in a
subtle way produces a green pipeline testing the wrong thing, which is the exact
failure mode this whole repo exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - environment problem, not logic
    print(
        "error: PyYAML is required to read .github/mc-bump.yml "
        "(pip install pyyaml)",
        file=sys.stderr,
    )
    raise SystemExit(1)

from .common import Failure
from .gradle import ModPaths
from .loaders import LOADERS, Loader, get_loader

CONFIG_PATH = ".github/mc-bump.yml"


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Field:
    """One leaf of the schema."""

    type: type
    default: object = None
    required: bool = False
    choices: tuple = ()
    #: for lists of records: the keys each record must / may have
    record: tuple[tuple[str, bool], ...] = ()

    def coerce(self, path: str, value):
        if self.type is bool:
            if not isinstance(value, bool):
                raise Failure(f"{path}: expected true or false, got {value!r}")
            return value
        if self.type is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise Failure(f"{path}: expected a number, got {value!r}")
            if value <= 0:
                raise Failure(f"{path}: expected a positive number, got {value}")
            return value
        if self.type is str:
            if not isinstance(value, str):
                raise Failure(f"{path}: expected a string, got {value!r}")
            value = value.strip()
            if not value:
                # A field whose default is "" accepts being spelled out as empty:
                # writing `assignee: ""` to mean "nobody" must not be an error.
                if self.required or self.default != "":
                    raise Failure(f"{path}: expected a non-empty string")
                return ""
            if self.choices and value not in self.choices:
                raise Failure(
                    f"{path}: '{value}' is not one of {', '.join(self.choices)}"
                )
            return value
        if self.type is list:
            if not isinstance(value, list):
                raise Failure(f"{path}: expected a list, got {value!r}")
            return [self._record(f"{path}[{i}]", item) for i, item in enumerate(value)]
        raise Failure(f"{path}: unsupported schema type {self.type}")  # pragma: no cover

    def _record(self, path: str, item):
        if not self.record:
            if not isinstance(item, str) or not item.strip():
                raise Failure(f"{path}: expected a non-empty string, got {item!r}")
            return item.strip()
        if not isinstance(item, dict):
            raise Failure(f"{path}: expected a mapping, got {item!r}")
        known = {key for key, _ in self.record}
        for key in item:
            if key not in known:
                raise Failure(
                    f"{path}: unknown key '{key}'. Known keys: {', '.join(sorted(known))}"
                )
        out = {}
        for key, required in self.record:
            value = item.get(key)
            if value is None:
                if required:
                    raise Failure(f"{path}: '{key}' is required")
                out[key] = ""
                continue
            if not isinstance(value, str) or not value.strip():
                raise Failure(f"{path}.{key}: expected a non-empty string, got {value!r}")
            out[key] = value.strip()
        return out


SCHEMA: dict = {
    "loader": Field(str, required=True, choices=tuple(sorted(LOADERS))),
    "mod": {
        "id": Field(str, required=True),
        "package": Field(str, default=""),
        "metadata": Field(str, required=True),
        "mixins": Field(str, default=""),
    },
    "workflows": {
        "ci": Field(bool, default=True),
        "auto-update": Field(bool, default=True),
        "release": Field(bool, default=True),
        "unit-tests": Field(bool, default=True),
        "gametest": {
            "enabled": Field(bool, default=False),
            "blocking": Field(bool, default=False),
        },
    },
    "version": {
        "format": Field(str, default="{mc}-{mod}"),
        "tag": Field(str, default="v{version}"),
    },
    "tests": {
        "unit": {
            "source": Field(str, default="src/test/java"),
            "task": Field(str, default="test"),
            "require-non-empty": Field(bool, default=True),
        },
        "matrix": {
            "enabled": Field(bool, default=True),
            "parallel": Field(bool, default=True),
        },
        "server": {
            "boot-timeout": Field(int, default=900),
            "stop-timeout": Field(int, default=60),
            "expect": Field(
                list,
                default=[],
                record=(("pattern", True), ("message", False)),
            ),
            "expect-count": Field(
                list,
                default=[],
                record=(
                    ("pattern", True),
                    ("count-source", True),
                    ("count-pattern", True),
                    ("message", False),
                ),
            ),
            "fatal-extra": Field(list, default=[]),
        },
    },
    "release": {
        "stores": Field(
            list, default=["modrinth", "curseforge"]
        ),
        "branch-prefix": Field(str, default="chore/mc-"),
        "artifact-retention-days": Field(int, default=30),
    },
    "notify": {
        "assignee": Field(str, default=""),
        "label": Field(str, default="ci-failure"),
        "keep-branch": Field(bool, default=True),
        "on-pull-request": Field(bool, default=False),
        "log-tail": Field(int, default=100),
    },
}

KNOWN_STORES = ("modrinth", "curseforge")


def _validate(node: dict, schema: dict, prefix: str = "") -> dict:
    """Walk the schema, apply defaults, reject anything unexpected."""
    if not isinstance(node, dict):
        raise Failure(f"{prefix or 'the config'}: expected a mapping, got {node!r}")

    for key in node:
        if key not in schema:
            raise Failure(
                f"unknown key '{prefix}{key}'. Known keys here: "
                f"{', '.join(sorted(schema))}"
            )

    out: dict = {}
    for key, spec in schema.items():
        path = f"{prefix}{key}"
        value = node.get(key)
        if isinstance(spec, dict):
            out[key] = _validate(value or {}, spec, prefix=f"{path}.")
            continue
        if value is None:
            if spec.required:
                raise Failure(f"'{path}' is required in {CONFIG_PATH}")
            # copy: a mutable default must not be shared between two loads
            out[key] = list(spec.default) if isinstance(spec.default, list) else spec.default
            continue
        out[key] = spec.coerce(path, value)
    return out


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def find_root(start: Path | None = None) -> Path:
    """Walk up from `start` (or $MOD_ROOT, or the cwd) to the mod repository.

    mc-bump lives in its own checkout and edits someone else's repository, so the
    root can never be derived from __file__.
    """
    if start is None:
        start = Path(os.environ.get("MOD_ROOT") or Path.cwd())
    start = Path(start).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / CONFIG_PATH).is_file():
            return candidate
    raise Failure(
        f"no {CONFIG_PATH} found in {start} or any parent directory. "
        f"mc-bump must run from inside the mod repository."
    )


@dataclass(frozen=True)
class Project:
    """A mod repository, as mc-bump sees it."""

    root: Path
    raw: dict
    loader: Loader
    paths: ModPaths = field(init=False)

    def __post_init__(self):
        object.__setattr__(
            self,
            "paths",
            ModPaths.under(
                self.root,
                metadata=self.raw["mod"]["metadata"],
                mixins=self.raw["mod"]["mixins"] or None,
            ),
        )

    # -- shortcuts used all over the CLI
    @property
    def mod_id(self) -> str:
        return self.raw["mod"]["id"]

    @property
    def version_format(self) -> str:
        return self.raw["version"]["format"]

    @property
    def tag_format(self) -> str:
        return self.raw["version"]["tag"]

    def tag_for(self, mod_version: str) -> str:
        return self.tag_format.format(version=mod_version)


def load(root: Path | None = None) -> Project:
    root = find_root() if root is None else Path(root).resolve()
    path = root / CONFIG_PATH
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise Failure(f"{path} is not valid YAML: {exc}") from exc
    except OSError as exc:
        raise Failure(f"cannot read {path}: {exc}") from exc

    raw = _validate(document or {}, SCHEMA)

    unknown = [s for s in raw["release"]["stores"] if s not in KNOWN_STORES]
    if unknown:
        raise Failure(
            f"release.stores: unknown store(s) {', '.join(unknown)}. "
            f"Known stores: {', '.join(KNOWN_STORES)}."
        )

    project = Project(root=root, raw=raw, loader=get_loader(raw["loader"]))
    # Fail here rather than in the middle of a rewrite: an id the loader cannot
    # turn into a log pattern makes the server test unable to prove anything.
    project.loader.mod_loaded_pattern(project.mod_id)
    return project


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
def _flatten(node: dict, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in node.items():
        name = f"{prefix}{key}".replace("-", "_").replace(".", "_").upper()
        if isinstance(value, dict):
            out.update(_flatten(value, prefix=f"{prefix}{key}."))
        else:
            out[name] = value
    return out


def _shell_value(value) -> str:
    """Render one config value for `eval`.

    Records become TAB separated fields, one per line, so a shell loop reads them
    with `while IFS=$'\\t' read -r ...`. Patterns are line oriented by nature
    (they are fed to grep), so newline is a safe record separator and TAB is a
    safe field separator.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, list):
        rows = []
        for item in value:
            if isinstance(item, dict):
                rows.append("\t".join(item.values()))
            else:
                rows.append(str(item))
        return "\n".join(rows)
    return str(value)


def export_shell(project: Project) -> str:
    """`eval "$(python3 -m lib.config --sh)"` in the shell scripts."""
    values = _flatten(project.raw)
    values["MOD_ROOT"] = str(project.root)
    values["SERVER_TASK"] = project.loader.server_task()
    values["CLIENT_GAMETEST_TASK"] = project.loader.client_gametest_task()
    values["MOD_LOADED_PATTERN"] = project.loader.mod_loaded_pattern(project.mod_id)
    values["STORE_LOADER"] = project.loader.store_loader_name()
    # The loader's own signatures plus whatever the mod adds, already joined into
    # the alternation `grep -E` expects: assembling it here keeps the shell from
    # having to know that these two lists are the same kind of thing.
    values["FATAL_PATTERNS"] = "|".join(
        [*project.loader.fatal_patterns(), *project.raw["tests"]["server"]["fatal-extra"]]
    )

    lines = []
    for key in sorted(values):
        lines.append(f"MCBUMP_{key}={shlex.quote(_shell_value(values[key]))}")
    return "\n".join(lines)


def export_json(project: Project) -> str:
    document = dict(project.raw)
    document["root"] = str(project.root)
    document["server_task"] = project.loader.server_task()
    document["client_gametest_task"] = project.loader.client_gametest_task()
    document["mod_loaded_pattern"] = project.loader.mod_loaded_pattern(project.mod_id)
    document["store_loader"] = project.loader.store_loader_name()
    return json.dumps(document, indent=2, sort_keys=True)


def export_github_output(project: Project) -> str:
    """Only what a workflow branches on, flat and lowercase."""
    raw = project.raw
    pairs = {
        "loader": raw["loader"],
        "mod_id": raw["mod"]["id"],
        "ci": raw["workflows"]["ci"],
        "auto_update": raw["workflows"]["auto-update"],
        "release": raw["workflows"]["release"],
        "unit_tests": raw["workflows"]["unit-tests"],
        "gametest": raw["workflows"]["gametest"]["enabled"],
        "gametest_blocking": raw["workflows"]["gametest"]["blocking"],
        "unit_source": raw["tests"]["unit"]["source"],
        "unit_task": raw["tests"]["unit"]["task"],
        "unit_require_non_empty": raw["tests"]["unit"]["require-non-empty"],
        "matrix": raw["tests"]["matrix"]["enabled"],
        "matrix_parallel": raw["tests"]["matrix"]["parallel"],
        "stores": ",".join(raw["release"]["stores"]),
        "branch_prefix": raw["release"]["branch-prefix"],
        "retention_days": raw["release"]["artifact-retention-days"],
        "assignee": raw["notify"]["assignee"],
        "label": raw["notify"]["label"],
        "on_pull_request": raw["notify"]["on-pull-request"],
        "log_tail": raw["notify"]["log-tail"],
        "client_gametest_task": project.loader.client_gametest_task(),
    }
    return "\n".join(
        f"{key}={'true' if value is True else 'false' if value is False else value}"
        for key, value in pairs.items()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mc-bump config",
        description=f"Read and validate {CONFIG_PATH}.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sh", action="store_true", help="shell exports, for eval")
    group.add_argument("--json", action="store_true", help="the whole resolved config")
    group.add_argument(
        "--github-output",
        action="store_true",
        help="key=value lines for $GITHUB_OUTPUT",
    )
    parser.add_argument("--root", help="mod repository (default: walk up from the cwd)")
    args = parser.parse_args(argv)

    project = load(Path(args.root) if args.root else None)

    if args.json:
        print(export_json(project))
    elif args.github_output:
        print(export_github_output(project))
    else:
        print(export_shell(project))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Failure as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
