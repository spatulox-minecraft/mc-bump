"""Fabric: meta API, fabric.mod.json, fabric-loom.

Every URL, file format and log line specific to Fabric is in this file. A
NeoForge implementation is this file rewritten against neoforge.mods.toml and the
NeoForged maven, with base.py unchanged.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Callable
from xml.etree import ElementTree

from ..common import Failure, get_json, http_get
from ..gradle import ModPaths, gradle_version_key, read_json, write_json
from ..versions import parse_version
from .base import BuildEnv, Loader, Resolved, Rung

FABRIC_META = "https://meta.fabricmc.net/v2/versions"
MODRINTH_FABRIC_API = "https://api.modrinth.com/v2/project/fabric-api/version"
LOOM_METADATA = "https://maven.fabricmc.net/net/fabricmc/fabric-loom/maven-metadata.xml"

# A stable Loom version is purely numeric: this rejects 1.18.0-alpha.9 and
# 1.17-SNAPSHOT without having to enumerate every pre-release marker.
STABLE_LOOM = re.compile(r"^\d+(?:\.\d+)*$")

# https://fabricmc.net/wiki/documentation:fabric_mod_json_spec
MOD_ID_RE = re.compile(r"[a-z][a-z0-9_-]{1,63}")

# The two id shapes Mojang uses for pre-releases and release candidates.
DATE_BASED_ID = re.compile(r"(\d+\.\d+(?:\.\d+)?)-(snapshot|pre|rc)-(\d+)")
LEGACY_PRE_ID = re.compile(r"(\d+\.\d+(?:\.\d+)?)-(pre|rc)(\d+)")

# Gradle Module Metadata of one Loom version: what it needs to run.
LOOM_MODULE = "https://maven.fabricmc.net/net/fabricmc/fabric-loom/{v}/fabric-loom-{v}.module"

# How far back to look for a Loom the wrapper can run. Each step is one request,
# and a wrapper that far behind is better told to upgrade than silently served a
# Loom from a year ago.
MAX_LOOM_LOOKUPS = 15


@dataclass(frozen=True)
class LoomRequirements:
    """What a Loom version declares it needs. None: not declared."""

    gradle: str | None = None
    java: int | None = None

    def gradle_too_old(self, env: BuildEnv) -> bool:
        return bool(
            self.gradle
            and env.gradle
            and gradle_version_key(self.gradle) > gradle_version_key(env.gradle)
        )

    def java_too_old(self, env: BuildEnv) -> bool:
        return bool(self.java and env.java and self.java > env.java)

    def gaps(self, env: BuildEnv) -> list[str]:
        """Every requirement `env` does not meet, as a readable clause."""
        out = []
        if self.gradle_too_old(env):
            out.append(f"Gradle >= {self.gradle} but the wrapper is on {env.gradle}")
        if self.java_too_old(env):
            out.append(f"Java >= {self.java} but java_version is {env.java}")
        return out


@dataclass(frozen=True)
class LoomChoice:
    version: str
    #: why it is not the newest stable, empty when it is
    note: str = ""


def _loom_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def pick_loom(
    stable: list[str],
    requirements: Callable[[str], LoomRequirements | None],
    env: BuildEnv,
    limit: int = MAX_LOOM_LOOKUPS,
) -> LoomChoice:
    """The newest stable Loom that runs on the mod's Gradle and Java.

    A version whose requirements cannot be read is skipped, never picked blind:
    being wrong here is a build that fails before any test, which is exactly the
    failure this exists to prevent. Without any constraint to check, the newest
    is taken with no lookup at all.
    """
    ordered = sorted(set(stable), key=_loom_key, reverse=True)
    if not ordered:
        raise Failure("no stable fabric-loom version found")
    newest = ordered[0]
    if env.gradle is None and env.java is None:
        return LoomChoice(newest)

    newest_needs = requirements(newest)
    why = (
        f"{newest} requires {' and '.join(newest_needs.gaps(env))}"
        if newest_needs is not None
        else f"the requirements of {newest} could not be read"
    )
    for version in ordered[:limit]:
        needs = newest_needs if version == newest else requirements(version)
        if needs is None or needs.gaps(env):
            continue
        if version == newest:
            return LoomChoice(version)
        return LoomChoice(version, note=f"newest that runs on this build; {why}")

    advice = []
    if newest_needs is not None and newest_needs.gradle_too_old(env):
        advice.append(f"./gradlew wrapper --gradle-version {newest_needs.gradle}")
    raise Failure(
        f"no stable fabric-loom among the {min(limit, len(ordered))} newest runs on "
        f"this build: {why}. "
        + (f"Upgrade with {' and '.join(advice)}, " if advice else "")
        + "or pin a fabric-loom with --loom."
    )


def check_pinned_loom(
    version: str,
    requirements: Callable[[str], LoomRequirements | None],
    env: BuildEnv,
) -> None:
    """A pinned Loom is a deliberate choice, but one the wrapper must still run.

    Unreadable requirements let it through: the pin is the escape hatch, and
    refusing on missing metadata would leave no way out.
    """
    needs = requirements(version)
    gaps = needs.gaps(env) if needs is not None else []
    if gaps:
        raise Failure(f"fabric-loom {version} requires {' and '.join(gaps)}")


def normalize_minecraft_version(version: str) -> str:
    """The Minecraft version as Fabric Loader compares it.

    Fabric Loader rewrites the id Mojang ships into semver before matching a
    `depends.minecraft` predicate (McVersionLookup.normalizeVersion), so a mod
    declaring "=26.2-rc-1" is refused on the very server it targets: the loader
    sees "26.2-rc.1". The same rewrite, for the shapes an update can target:

        26.2-snapshot-1   ->  26.2-alpha.1
        26.2-pre-1        ->  26.2-pre.1
        26.2-rc-1         ->  26.2-rc.1
        1.21.11-pre1      ->  1.21.11-beta.1
        1.21.11-rc1       ->  1.21.11-rc.1

    A weekly snapshot (25w45a) is mapped by Fabric onto the release it leads to,
    through a table only the loader has, so it is refused rather than guessed:
    a wrong guess is a mod the loader silently never loads. Mojang stopped
    shipping that shape with 26.1.
    """
    if parse_version(version) is not None:
        return version
    match = DATE_BASED_ID.fullmatch(version)
    if match:
        release, kind, number = match.groups()
        return f"{release}-{'alpha' if kind == 'snapshot' else kind}.{number}"
    match = LEGACY_PRE_ID.fullmatch(version)
    if match:
        release, kind, number = match.groups()
        return f"{release}-{'beta' if kind == 'pre' else kind}.{number}"
    raise Failure(
        f"Minecraft {version}: Fabric Loader maps this version id through its own "
        f"table, mc-bump cannot write a range it would accept. Only releases, "
        f"pre-releases, release candidates and X.Y-snapshot-N snapshots are supported."
    )


class FabricLoader(Loader):
    name = "fabric"
    gradle_keys = {
        "loader": "loader_version",
        "api": "fabric_api_version",
        "buildtool": "loom_version",
    }

    # -- resolution --------------------------------------------------------
    def resolve(
        self,
        minecraft_version: str,
        pin_buildtool: str | None = None,
        env: BuildEnv = BuildEnv(),
    ) -> Resolved:
        loader = self._loader_for(minecraft_version)
        if not loader:
            return Resolved()
        api = self._latest_fabric_api(minecraft_version)
        if not api:
            return Resolved(loader=loader)
        # Loom is the build plugin, not a Minecraft dependency: Fabric publishes
        # no "which loom builds which Minecraft" mapping, and loom is backward
        # compatible in practice. What it does declare is the Gradle and Java it
        # runs on, so the newest stable the mod's own build can run is taken, and
        # pin_buildtool is the escape hatch when an old Minecraft version needs an
        # older loom.
        if pin_buildtool:
            check_pinned_loom(pin_buildtool, self._loom_requirements, env)
            return Resolved(loader=loader, api=api, buildtool=pin_buildtool)
        choice = self._loom_for(env)
        return Resolved(
            loader=loader,
            api=api,
            buildtool=choice.version,
            extra={"buildtool_note": choice.note} if choice.note else {},
        )

    def resolve_one(
        self, role: str, minecraft_version: str, env: BuildEnv = BuildEnv()
    ) -> str | None:
        if role == "loader":
            return self._loader_for(minecraft_version)
        if role == "api":
            return self._latest_fabric_api(minecraft_version)
        if role == "buildtool":
            return self._loom_for(env).version
        raise Failure(f"unknown role '{role}' for the fabric loader")

    def escalation_rungs(self) -> list[Rung]:
        # Fabric API first: it is a normal library the mod calls into, while the
        # loader is the thing that runs every mod on the server.
        return [
            Rung(gradle_key="fabric_api_version", flag="--bump-api", label="fabric-api"),
            Rung(gradle_key="loader_version", flag="--bump-loader", label="fabric-loader"),
        ]

    def _loader_for(self, minecraft_version: str) -> str | None:
        """Latest STABLE Fabric Loader listed for this Minecraft version.

        Returns None when Fabric does not support the version yet, or when it only
        lists unstable loaders. Asking Fabric for the loaders OF THAT VERSION,
        rather than for the latest stable loader overall, is what guarantees the
        loader we write is actually compatible with the Minecraft version we
        target.
        """
        # Fabric meta answers 400 (not 404) for an unknown Minecraft version, with
        # a valid JSON body, so that status is expected rather than an error.
        data = get_json(
            f"{FABRIC_META}/loader/{urllib.parse.quote(minecraft_version)}",
            allow_status=(400,),
        )
        if not isinstance(data, list) or not data:
            return None
        for entry in data:
            loader = entry.get("loader") or {}
            if loader.get("stable"):
                return loader.get("version")
        return None

    def _latest_fabric_api(self, minecraft_version: str) -> str | None:
        data = get_json(
            MODRINTH_FABRIC_API,
            {
                "game_versions": json.dumps([minecraft_version]),
                "loaders": json.dumps(["fabric"]),
            },
        )
        if not isinstance(data, list) or not data:
            return None
        # Modrinth usually returns newest first, but we do not rely on it.
        newest = max(data, key=lambda v: v.get("date_published", ""))
        return newest.get("version_number")

    def _loom_for(self, env: BuildEnv) -> LoomChoice:
        return pick_loom(self._stable_looms(), self._loom_requirements, env)

    def _loom_requirements(self, version: str) -> LoomRequirements | None:
        """Read from the Gradle Module Metadata Fabric publishes with each Loom.

        Its runtime variant carries the exact attributes Gradle matches the plugin
        on: org.gradle.plugin.api-version (1.18.1 -> 9.7.0) and
        org.gradle.jvm.version (1.18.1 -> 25). A missing or unreadable file is
        None, which the caller treats as unknown rather than as "no requirement".
        """
        url = LOOM_MODULE.format(v=urllib.parse.quote(version))
        body = http_get(url, allow_status=(404,))
        try:
            document = json.loads(body)
        except json.JSONDecodeError:
            return None
        variants = document.get("variants") if isinstance(document, dict) else None
        runtime = next(
            (
                variant.get("attributes") or {}
                for variant in variants or []
                if variant.get("name") == "runtimeElements"
            ),
            None,
        )
        if runtime is None:
            return None
        java = runtime.get("org.gradle.jvm.version")
        return LoomRequirements(
            gradle=runtime.get("org.gradle.plugin.api-version"),
            java=java if isinstance(java, int) else None,
        )

    def _stable_looms(self) -> list[str]:
        """Every stable fabric-loom from the Fabric Maven metadata.

        The metadata lists versions in PUBLICATION order, not version order
        (1.17.14 comes after 1.18.0-alpha.4), so pick_loom() sorts it numerically
        rather than reading it from the end. <release> is not used either: nothing stops
        it from pointing at a pre-release, which is not a SNAPSHOT.
        """
        body = http_get(LOOM_METADATA)
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise Failure(f"invalid XML from {LOOM_METADATA}") from exc

        stable = [
            element.text
            for element in root.iter("version")
            if element.text and STABLE_LOOM.match(element.text)
        ]
        return stable

    # -- metadata ----------------------------------------------------------
    def render_range(self, low: str, high: str) -> str:
        """Fabric's own range syntax, as fabric.mod.json spells it."""
        if low == high:
            return f"={normalize_minecraft_version(low)}"
        return f">={low} <={high}"

    def depends_keys(self) -> dict[str, str]:
        return {
            "loader": "fabricloader",
            "api": "fabric-api",
            "minecraft": "minecraft",
            "java": "java",
        }

    def read_depends(self, paths: ModPaths, key: str) -> str | None:
        return read_json(paths.metadata).get("depends", {}).get(key)

    def write_depends(self, paths: ModPaths, key: str, value: str, dry_run: bool) -> bool:
        data = read_json(paths.metadata)
        if data.get("depends", {}).get(key) == value:
            return False
        data.setdefault("depends", {})[key] = value
        write_json(paths.metadata, data, dry_run)
        return True

    def write_java_version(self, paths: ModPaths, java: int, dry_run: bool) -> bool:
        """Propagate the Java level to fabric.mod.json and the mixin config.

        gradle.properties is handled by the caller, in the same pass as the other
        keys. build.gradle and the workflows read the value from there, so those
        files never need editing.
        """
        changed = self.write_depends(paths, "java", f">={java}", dry_run)

        if paths.mixins is None:
            return changed

        data = read_json(paths.mixins)
        if data.get("compatibilityLevel") != f"JAVA_{java}":
            data["compatibilityLevel"] = f"JAVA_{java}"
            write_json(paths.mixins, data, dry_run)
            changed = True
        return changed

    # -- runtime -----------------------------------------------------------
    def mod_loaded_pattern(self, mod_id: str) -> str:
        """The loader's own inventory line, not any mention of the id.

        A Python regex, matched line by line against the log. It used to be a
        POSIX ERE with `[[:space:]]`, for `grep -E`; the module `re` does not know
        that class, so the scripts moving to Python would have silently matched
        nothing.

        Fabric Loader prints the mods it LOADED as an indented tree:

            Loading 5 mods:
                - extended-time-potion 26.2-1.1.0
                - fabric-api 0.156.0+26.2

        Anchoring on that dash-space prefix is what tells a loaded mod apart from
        the same id appearing in a classpath dump or a stack trace, which is what
        a bare grep matched.

        No escaping: the Fabric spec restricts a mod id to [a-z0-9_-], none of
        which is a regex metacharacter, and MOD_ID_RE rejects anything else rather
        than building a pattern out of unvalidated input.
        """
        if not MOD_ID_RE.fullmatch(mod_id):
            raise Failure(
                f"mod.id = '{mod_id}' is not a valid Fabric mod id "
                f"(lowercase letters, digits, '-' and '_', 2 to 64 characters)"
            )
        return rf"^\s*-\s+{mod_id}\s"

    def fatal_patterns(self) -> list[str]:
        return [
            "Mixin apply failed",
            "Failed to load mod",
            "Could not execute entrypoint",
            # Fabric Loader's own wording when it refuses a mod set
            "A potential solution has been determined",
            "Incompatible mod set",
        ]

    def store_loader_name(self) -> str:
        return "fabric"
