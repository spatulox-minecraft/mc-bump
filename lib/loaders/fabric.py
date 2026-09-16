"""Fabric: meta API, fabric.mod.json, fabric-loom.

Every URL, file format and log line specific to Fabric is in this file. A
NeoForge implementation is this file rewritten against neoforge.mods.toml and the
NeoForged maven, with base.py unchanged.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from xml.etree import ElementTree

from ..common import Failure, get_json, http_get
from ..gradle import ModPaths, read_json, write_json
from ..versions import parse_version
from .base import Loader, Resolved, Rung

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
    def resolve(self, minecraft_version: str, pin_buildtool: str | None = None) -> Resolved:
        loader = self._loader_for(minecraft_version)
        if not loader:
            return Resolved()
        api = self._latest_fabric_api(minecraft_version)
        if not api:
            return Resolved(loader=loader)
        # Loom is the build plugin, not a Minecraft dependency: Fabric publishes
        # no "which loom builds which Minecraft" mapping, and loom is backward
        # compatible in practice. So the latest stable is taken, and pin_buildtool
        # is the escape hatch when an old Minecraft version needs an older loom.
        return Resolved(
            loader=loader,
            api=api,
            buildtool=pin_buildtool or self._latest_stable_loom(),
        )

    def resolve_one(self, role: str, minecraft_version: str) -> str | None:
        if role == "loader":
            return self._loader_for(minecraft_version)
        if role == "api":
            return self._latest_fabric_api(minecraft_version)
        if role == "buildtool":
            return self._latest_stable_loom()
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

    def _latest_stable_loom(self) -> str:
        """Latest stable fabric-loom from the Fabric Maven metadata.

        The metadata lists versions in PUBLICATION order, not version order
        (1.17.14 comes after 1.18.0-alpha.4), so the list is sorted numerically
        rather than read from the end. <release> is not used either: nothing stops
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
        if not stable:
            raise Failure("no stable fabric-loom version found")
        return max(stable, key=lambda version: tuple(int(p) for p in version.split(".")))

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
