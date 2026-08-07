"""Applying an update: the decision layer, generic over loaders.

An update moves ONE variable: Minecraft. The build plugin follows, because it is
a gradle plugin rather than a runtime dependency, and the Java level follows
because Mojang dictates it. The loader and its API stay FROZEN on the values
already in gradle.properties; they only move through the escalation ladder, one
at a time, as a reaction to a failing matrix. That is what makes a red matrix
name its culprit instead of listing three suspects.

The compatibility range has to be widened BEFORE the tests run, otherwise the
loader refuses to load the mod on the new version's server and no version could
ever be validated. So an update raises it optimistically, and then:

    whole matrix OK  -> mark_supported() keeps it and extends the list
    any version KO   -> revert_compat() restores BOTH claims, keeping the bumps

The values needed by revert_compat() are snapshotted in .mc-update-state.json
(gitignored) before anything is written.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .common import Failure
from .config import Project
from .gradle import (
    read_property,
    set_property,
    split_supported,
    write_preserving_final_newline,
)
from .versions import (
    compat_bounds,
    series_of,
    update_mod_version,
    versions_to_test,
)

EXIT_ALREADY_LATEST = 3


def _properties(project: Project) -> str:
    return project.paths.gradle_properties.read_text(encoding="utf-8")


def _write_properties(project: Project, original: str, text: str, dry_run: bool) -> bool:
    if text == original:
        return False
    if not dry_run:
        write_preserving_final_newline(project.paths.gradle_properties, original, text)
    return True


def current_versions(project: Project) -> tuple[str, list[str]]:
    """(minecraft_version, supported_minecraft_versions) as the repo has them."""
    text = _properties(project)
    current = read_property(text, "minecraft_version")
    if not current:
        raise Failure("minecraft_version missing from gradle.properties")
    return current, split_supported(read_property(text, "supported_minecraft_versions"))


def list_test_versions(project: Project) -> list[str]:
    current, supported = current_versions(project)
    return versions_to_test(current, supported)


def compat_range(project: Project, target: str, supported: list[str]) -> str:
    low, high = compat_bounds(target, supported)
    return project.loader.render_range(low, high)


# --------------------------------------------------------------------------
# Update state, used by revert_compat()
# --------------------------------------------------------------------------
def save_update_state(project: Project, target: str, supported_raw: str) -> None:
    """Snapshot the compatibility claims as they were BEFORE the update.

    The frozen dependency versions are snapshotted too, not to restore them, but
    so mark_supported() can tell what an escalation moved: comparing
    gradle.properties against this is what decides whether a dependency floor is
    written into the mod metadata.

    An existing snapshot for the same target is kept: it holds the genuine
    pre-bump values, which a re-run (--force) would otherwise overwrite with the
    already bumped ones. A snapshot for a different target is stale and replaced.
    """
    state_file = project.paths.state
    if state_file.exists():
        try:
            previous = json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = {}
        if previous.get("target") == target:
            return

    loader = project.loader
    text = _properties(project)
    depends_keys = loader.depends_keys()

    state_file.write_text(
        json.dumps(
            {
                "target": target,
                "supported_minecraft_versions": supported_raw,
                "depends_minecraft": loader.read_depends(
                    project.paths, depends_keys["minecraft"]
                ),
                "frozen": {
                    role: read_property(text, loader.gradle_keys[role])
                    for role in ("loader", "api")
                },
                "depends": {
                    role: loader.read_depends(project.paths, depends_keys[role])
                    for role in ("loader", "api")
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def load_update_state(project: Project) -> dict:
    """The snapshot, or {} when there is none / it is unreadable."""
    if not project.paths.state.exists():
        return {}
    try:
        return json.loads(project.paths.state.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def clear_update_state(project: Project) -> None:
    project.paths.state.unlink(missing_ok=True)


def revert_compat(project: Project, dry_run: bool) -> dict:
    """Restore the compatibility claims recorded before the last update.

    Only the CLAIMS are restored: supported_minecraft_versions, the metadata
    Minecraft range and the two dependency floors an escalation may have written.
    The version bumps are kept: they are the dependency diff someone picks up
    from.
    """
    state_file = project.paths.state
    if not state_file.exists():
        raise Failure(f"no update state to revert ({state_file.name} not found)")
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Failure(f"{state_file.name} is not valid JSON") from exc

    original = _properties(project)
    current = read_property(original, "minecraft_version")
    if state.get("target") != current:
        raise Failure(
            f"{state_file.name} was written for Minecraft {state.get('target')} "
            f"but gradle.properties is on {current}; refusing to revert"
        )

    loader = project.loader
    depends_keys = loader.depends_keys()
    supported = state.get("supported_minecraft_versions") or ""
    depends = state.get("depends_minecraft")

    text = set_property(original, "supported_minecraft_versions", supported)
    _write_properties(project, original, text, dry_run)
    if depends is not None:
        loader.write_depends(project.paths, depends_keys["minecraft"], depends, dry_run)

    # The floors are only written by mark_supported(), so a failed run should not
    # have any to restore. Doing it anyway makes the revert idempotent, and covers
    # a re-run that succeeded once and now fails.
    for role, value in (state.get("depends") or {}).items():
        if value is not None and role in depends_keys:
            loader.write_depends(project.paths, depends_keys[role], value, dry_run)

    if not dry_run:
        clear_update_state(project)

    return {
        "status": "reverted",
        "minecraft_version": current,
        "supported_minecraft_versions": split_supported(supported),
        "minecraft_range": depends,
    }


# --------------------------------------------------------------------------
# Writing the update
# --------------------------------------------------------------------------
@dataclass
class UpdateResult:
    changed: bool
    supported: list[str]
    mod_version: str | None


def update_gradle_properties(
    project: Project,
    minecraft_version: str,
    buildtool_version: str,
    java_version: int | None,
    dry_run: bool,
    log,
) -> UpdateResult:
    """Apply the update. The loader and API versions are NOT touched."""
    loader = project.loader
    original = _properties(project)
    current = read_property(original, "minecraft_version") or ""
    text = original

    text = set_property(text, "minecraft_version", minecraft_version)
    text = set_property(text, loader.gradle_keys["buildtool"], buildtool_version)
    if java_version is not None:
        text = set_property(text, "java_version", str(java_version))

    # supported_minecraft_versions is what we ANNOUNCE on the stores, so nothing
    # here may add to it: at this point nothing proves the mod still works. Only
    # mark_supported(), called after a successful matrix, is allowed to extend it.
    # Changing series does clear it though, since a jar covers one series and the
    # previous one is no longer the target.
    supported = split_supported(read_property(text, "supported_minecraft_versions"))
    if series_of(minecraft_version) != series_of(current):
        log(
            f"  Series change {series_of(current)} -> {series_of(minecraft_version)}: "
            f"supported_minecraft_versions reset."
        )
        supported = []
        text = set_property(text, "supported_minecraft_versions", "")

    # After the possible reset, so the label names the versions THIS jar will
    # claim: the ones the matrix is about to run, not the previous series.
    text = _rewrite_mod_version(
        project, text, versions_to_test(minecraft_version, supported)
    )

    changed = _write_properties(project, original, text, dry_run)
    return UpdateResult(changed, supported, read_property(text, "mod_version"))


def _rewrite_mod_version(project: Project, text: str, covered: list[str]) -> str:
    current = read_property(text, "mod_version")
    if current is None:
        raise Failure("key 'mod_version' not found in gradle.properties")
    new = update_mod_version(project.version_format, current, covered)
    return text if new == current else set_property(text, "mod_version", new)


def engrave_dependency_floors(project: Project, dry_run: bool) -> dict[str, str]:
    """Write dependency floors for what an escalation had to move.

    Only for what MOVED, hence the comparison against the pre-update snapshot: a
    nominal update escalates nothing, and a "*" constraint must keep its "*"
    rather than acquire a floor nobody asked for.

    The version is written raw, suffix included (">=0.156.1+26.2"). Fabric follows
    semver, where build metadata is ignored when comparing, so the suffix is
    cosmetic at runtime — but it makes the file say exactly which artifact was
    tested, which is what someone reading it wants to know.

    Without a snapshot (a manual, standalone mark_supported) nothing is observable
    as having escalated, so nothing is written.
    """
    state = load_update_state(project)
    if not state:
        return {}

    loader = project.loader
    depends_keys = loader.depends_keys()
    text = _properties(project)
    frozen = state.get("frozen") or {}

    written: dict[str, str] = {}
    for role in ("api", "loader"):
        version = read_property(text, loader.gradle_keys[role])
        if not version or version == frozen.get(role):
            continue
        floor = f">={version}"
        loader.write_depends(project.paths, depends_keys[role], floor, dry_run)
        written[depends_keys[role]] = floor
    return written


def mark_supported(
    project: Project, dry_run: bool, log=lambda _message="": None
) -> tuple[str, list[str], str, dict[str, str]]:
    """Add the current minecraft_version to supported_minecraft_versions.

    Only to be called after a successful matrix: this list is what is announced as
    compatible on the stores. The metadata range and the {mc} part of mod_version
    are recomputed from that same list, so the announced versions, the range the
    loader enforces and the jar file name can never drift apart.

    This is also where an escalated dependency becomes a requirement of the mod:
    proof that it was needed is exactly what running the matrix produced.
    """
    original = _properties(project)
    current = read_property(original, "minecraft_version")
    if not current:
        raise Failure("minecraft_version missing from gradle.properties")

    supported = split_supported(read_property(original, "supported_minecraft_versions"))
    if current not in supported:
        supported.append(current)

    text = set_property(original, "supported_minecraft_versions", ",".join(supported))
    # The proven list is the final word on the jar name: a matrix that only
    # validated 26.1 must not ship a jar called 26.1.x.
    text = _rewrite_mod_version(project, text, supported)
    _write_properties(project, original, text, dry_run)

    new_range = compat_range(project, current, supported)
    project.loader.write_depends(
        project.paths, project.loader.depends_keys()["minecraft"], new_range, dry_run
    )
    floors = engrave_dependency_floors(project, dry_run)

    # Compatibility is proven, there is nothing left to revert.
    if not dry_run:
        clear_update_state(project)
    return current, supported, new_range, floors


# --------------------------------------------------------------------------
# Escalation
# --------------------------------------------------------------------------
def bump_dependency(project: Project, role: str, dry_run: bool, log) -> dict:
    """Move ONE frozen dependency to the newest release for the current Minecraft.

    Called only after a red matrix. The target is read from gradle.properties
    rather than taken as an argument: the escalation reacts to whatever the update
    left behind, and a caller passing a different version would be resolving for a
    Minecraft the matrix never ran.

    The mod metadata is deliberately untouched. A bump is a hypothesis; the floor
    is only engraved by mark_supported(), once the matrix has proven it.

    status is "already-latest" when there is nothing newer. That is not a failure,
    it just means this rung of the ladder cannot change the outcome and re-running
    the matrix would burn CI time to reproduce the same red.
    """
    loader = project.loader
    gradle_key = loader.gradle_keys[role]
    label = next(
        (rung.label for rung in loader.escalation_rungs() if rung.gradle_key == gradle_key),
        role,
    )

    original = _properties(project)
    minecraft_version = read_property(original, "minecraft_version")
    if not minecraft_version:
        raise Failure("minecraft_version missing from gradle.properties")

    previous = read_property(original, gradle_key)
    resolved = loader.resolve_one(role, minecraft_version)
    if not resolved:
        raise Failure(f"no {label} published for Minecraft {minecraft_version}")

    result = {
        "status": "already-latest",
        "minecraft_version": minecraft_version,
        "gradle_key": gradle_key,
        f"previous_{gradle_key}": previous,
        gradle_key: resolved,
    }

    if resolved == previous:
        log(f"{label} is already on {resolved} for Minecraft {minecraft_version}.")
        return result

    _write_properties(
        project, original, set_property(original, gradle_key, resolved), dry_run
    )

    result["status"] = f"bumped-{label}"
    log(f"{label}: {previous} -> {resolved} (Minecraft {minecraft_version})")
    log("  the mod metadata is untouched: the floor is written by --mark-supported.")
    return result
