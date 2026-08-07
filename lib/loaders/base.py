"""The mod loader seam.

Everything that knows what "Fabric" is lives behind this interface, so adding
NeoForge means writing one more implementation next to fabric.py and nothing
else. What is NOT here is deliberate: resolving Minecraft versions, computing
compatibility bounds and naming a jar are the same problem on every loader, and
they live in versions.py.

Three kinds of loader specific knowledge:

    resolution   which loader/API/build plugin versions match a Minecraft version,
                 and whether the loader supports it at all
    metadata     the file that declares the mod and its dependency ranges, and the
                 syntax of those ranges
    runtime      the gradle task that boots a server, and what a loaded mod looks
                 like in the log
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..gradle import ModPaths


@dataclass(frozen=True)
class Resolved:
    """What upstream offers for one Minecraft version.

    `loader` and `api` are AVAILABILITY probes: an update freezes them and writes
    neither, so that a red matrix has one suspect (Minecraft) instead of three.
    Only `buildtool` is written, because it is the gradle plugin rather than
    something that ships in the jar.
    """

    loader: str | None = None
    api: str | None = None
    buildtool: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return bool(self.loader and self.api)


@dataclass(frozen=True)
class Rung:
    """One step of the escalation ladder, least invasive first."""

    gradle_key: str
    flag: str
    label: str


class Loader(ABC):
    #: value of `loader:` in .github/mc-bump.yml
    name: str

    #: gradle.properties keys this loader owns, by role
    #: {"loader": ..., "api": ..., "buildtool": ...}
    gradle_keys: dict[str, str]

    # -- resolution --------------------------------------------------------
    @abstractmethod
    def resolve(self, minecraft_version: str, pin_buildtool: str | None = None) -> Resolved:
        """Everything upstream publishes for this Minecraft version.

        Never raises for "not published yet": that is `Resolved.usable == False`,
        which the caller reports as a normal, retry-next-week outcome.
        """

    @abstractmethod
    def resolve_one(self, role: str, minecraft_version: str) -> str | None:
        """A single role, for the escalation ladder. role in gradle_keys."""

    @abstractmethod
    def escalation_rungs(self) -> list[Rung]:
        """Frozen dependencies, ordered from the least to the most invasive."""

    # -- metadata ----------------------------------------------------------
    @abstractmethod
    def render_range(self, low: str, high: str) -> str:
        """The dependency range syntax. Equal bounds mean "pin exactly"."""

    @abstractmethod
    def read_depends(self, paths: ModPaths, key: str) -> str | None:
        """One dependency constraint from the mod metadata."""

    @abstractmethod
    def write_depends(self, paths: ModPaths, key: str, value: str, dry_run: bool) -> bool:
        """Rewrite one dependency constraint. True when the file changed."""

    @abstractmethod
    def depends_keys(self) -> dict[str, str]:
        """Map a gradle role to the metadata dependency key it constrains.

        {"loader": "fabricloader", "api": "fabric-api", "minecraft": "minecraft",
         "java": "java"}
        """

    @abstractmethod
    def write_java_version(self, paths: ModPaths, java: int, dry_run: bool) -> bool:
        """Propagate the Java level to every file that repeats it."""

    # -- runtime -----------------------------------------------------------
    def server_task(self) -> str:
        """Gradle task booting a dedicated server with the mod."""
        return "runServer"

    def client_gametest_task(self) -> str:
        return "runClientGameTest"

    @abstractmethod
    def mod_loaded_pattern(self, mod_id: str) -> str:
        """Regex proving the mod was actually LOADED, not merely on the classpath.

        Grepping the bare mod id is not enough: it appears in every classpath dump,
        so the check passes on a mod the loader rejected.
        """

    def fatal_patterns(self) -> list[str]:
        """Log signatures that mean the run is a failure even if the server booted.

        Minecraft logs plenty of harmless WARNs, so this is a deliberate list of
        real failure signatures rather than a "grep for ERROR". The mod's own
        config adds to it through tests.server.fatal-extra; it never replaces it.
        """
        return ["Failed to load mod", "Incompatible mod set"]

    @abstractmethod
    def store_loader_name(self) -> str:
        """Loader name as Modrinth and CurseForge spell it."""
