"""Minecraft versions, series and the mod_version template. Loader agnostic.

A "series" is the first two components of a Minecraft version (26.1.2 -> 26.1).
One jar covers one series. Two claims describe compatibility and they must agree:

    supported_minecraft_versions   what is ANNOUNCED on Modrinth / CurseForge
    the loader metadata range      what the LOADER accepts or refuses at runtime

Both derive from the series, and the upper bound is the highest version actually
proven to work:

    minecraft_version   supported_minecraft_versions   bounds
    26.1                26.1                           (26.1, 26.1)   -> exact
    26.1.1              26.1,26.1.1                    (26.1, 26.1.1)
    26.1.2              26.1,26.1.1,26.1.2             (26.1, 26.1.2)
    26.2                26.2            (series reset) (26.2, 26.2)   -> exact

Only the RENDERING of those bounds is loader specific (Fabric writes
">=26.1 <=26.1.2", a maven range would write "[26.1,26.1.3)"), so it lives in the
loader module and the arithmetic lives here.
"""

from __future__ import annotations

import re

from .common import Failure, get_json

MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

_manifest_cache: dict | None = None


# --------------------------------------------------------------------------
# Mojang
# --------------------------------------------------------------------------
def mojang_manifest() -> dict:
    """The Mojang version manifest, fetched at most once per run."""
    global _manifest_cache
    if _manifest_cache is None:
        _manifest_cache = get_json(MOJANG_MANIFEST) or {}
    return _manifest_cache


#: What a mod can choose to follow, from the most to the least stable.
CHANNELS = ("release", "rc", "pre", "snapshot")

_RC = re.compile(r".+-rc-?\d+")
_PRE = re.compile(r".+-pre-?\d+")


def channel_of(version: str) -> str:
    """The channel of a Minecraft version id, read from the id alone.

    Mojang's manifest types pre-releases, release candidates and snapshots all as
    "snapshot", so the type cannot tell them apart; the id can, in both eras:

        26.2, 1.21.11                  release
        26.2-rc-1, 1.21.11-rc1         rc
        26.2-pre-1, 1.21.11-pre1       pre
        26.2-snapshot-1, 25w45a        snapshot

    Reading the id rather than the manifest also keeps it offline, which is what
    the release pipeline needs to decide on a version already in gradle.properties.
    """
    if parse_version(version) is not None:
        return "release"
    if _RC.fullmatch(version):
        return "rc"
    if _PRE.fullmatch(version):
        return "pre"
    return "snapshot"


_LABEL_WORDS = (("releasecandidate", "rc"), ("prerelease", "pre"))


def _canonical(version: str) -> str:
    text = re.sub(r"[\s_-]+", "", version.lower())
    for word, short in _LABEL_WORDS:
        text = text.replace(word, short)
    return text


def same_minecraft_version(announced: str, version_id: str) -> bool:
    """Does the name a server announces designate this version id?

    Minecraft logs the NAME of a version, which for a release is its id but not
    for the rest: 26.3-rc-3 boots as "26.3 Release Candidate 3". Both sides are
    reduced to the same spelling, dots kept, so 26.1 and 26.1.1 still differ.
    """
    return announced == version_id or _canonical(announced) == _canonical(version_id)


def latest_minecraft_version(channels: list[str] | tuple[str, ...]) -> str:
    """The newest Minecraft version whose channel is one of `channels`.

    Newest by releaseTime, not by manifest order: the order is newest first in
    practice, but nothing documents it. old_alpha and old_beta never qualify.
    """
    candidates = [
        entry
        for entry in mojang_manifest().get("versions", [])
        if entry.get("type") in ("release", "snapshot")
        and entry.get("id")
        and channel_of(entry["id"]) in channels
    ]
    if not candidates:
        raise Failure(
            f"no Minecraft version in the Mojang manifest for channel(s) "
            f"{', '.join(channels)}"
        )
    return max(candidates, key=lambda entry: entry.get("releaseTime", ""))["id"]


def latest_minecraft_release() -> str:
    return latest_minecraft_version(["release"])


def java_version_for(minecraft_version: str) -> int | None:
    """Java major version Mojang ships this Minecraft version with.

    Published as javaVersion.majorVersion in each version's own manifest, for
    every era (1.16.5 -> 8, 1.21.11 -> 21, 26.x -> 25). Returns None when the
    field is absent, so a missing optional field never blocks an update.
    """
    entry = next(
        (
            version
            for version in mojang_manifest().get("versions", [])
            if version.get("id") == minecraft_version
        ),
        None,
    )
    if not entry or not entry.get("url"):
        return None
    return (get_json(entry["url"]) or {}).get("javaVersion", {}).get("majorVersion")


# --------------------------------------------------------------------------
# Series and compatibility bounds
# --------------------------------------------------------------------------
def series_of(version: str) -> str:
    """First two components of a version: "26.1.2" -> "26.1", "26.2" -> "26.2"."""
    return ".".join(version.split(".")[:2])


def parse_version(version: str) -> tuple[int, ...] | None:
    """(26, 1, 2) for "26.1.2". None when not purely numeric (snapshots...)."""
    parts = []
    for chunk in version.split("."):
        if not chunk.isdigit():
            return None
        parts.append(int(chunk))
    return tuple(parts) if parts else None


def versions_to_test(target: str, supported: list[str]) -> list[str]:
    """Every Minecraft version the mod will CLAIM, oldest first.

    The compatibility range covers the whole series up to the target, so testing
    only the target proves nothing about the older versions of that series running
    with the freshly bumped dependencies. This is the list that must boot, not
    just its last element.

    A non numeric target (a release candidate, a snapshot) is pinned exactly by
    compat_bounds(), so it is the one version to boot.
    """
    if parse_version(target) is None:
        return [target]
    series = series_of(target)
    unique = {
        version
        for version in [*supported, target]
        if series_of(version) == series and parse_version(version) is not None
    }
    return sorted(unique, key=parse_version)


def compat_bounds(target: str, supported: list[str]) -> tuple[str, str]:
    """(lowest, highest) Minecraft version the jar claims, for the target series.

    The lower bound is the series itself, the upper bound is the highest version
    of that series among the proven ones plus the target. Equal bounds mean "pin
    exactly": a series with no sub-version yet, or a non numeric target.
    """
    series = series_of(target)
    candidates = [
        version
        for version in [*supported, target]
        if series_of(version) == series and parse_version(version) is not None
    ]
    if not candidates:
        # Non numeric target (snapshot): nothing to order, pin it.
        return target, target
    return series, max(candidates, key=parse_version)


def mc_label(versions: list[str]) -> str:
    """Name a SET of covered Minecraft versions, for the {mc} part of mod_version.

    A single version is named exactly; several sub-versions of one series collapse
    to "<series>.x":

        [26.2]                      -> "26.2"
        [26.1, 26.1.1]              -> "26.1.x"
        [26.1, 26.1.1, 26.1.2]      -> "26.1.x"

    The wildcard never climbs a level. "26.x" would promise 26.3 to anyone reading
    the file name, and nothing has ever built, booted or published that version.
    A jar covers one series by construction (a series change resets
    supported_minecraft_versions), so the multi-series case cannot be produced
    here; should it ever be, the lowest series wins, which under-promises rather
    than over-promises.
    """
    unique = sorted(
        {version for version in versions if version},
        key=lambda version: parse_version(version) or (),
    )
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    return f"{series_of(unique[0])}.x"


# --------------------------------------------------------------------------
# mod_version template
# --------------------------------------------------------------------------
PLACEHOLDER = re.compile(r"\{(mc|mod)\}")

#: What mc_label() produces: a release or "<series>.x", optionally a pre-release
#: or candidate suffix, or a snapshot id of either era.
MC_LABEL = (
    r"(?:\d+\.\d+(?:\.(?:\d+|x))?(?:-(?:snapshot|pre|rc)-?\d+)?|\d+w\d+[a-z])"
)


def _template_parts(template: str) -> list[str]:
    """The placeholder names in the order they appear. Validates the template."""
    names = PLACEHOLDER.findall(template)
    leftovers = re.sub(PLACEHOLDER, "", template)
    if "{" in leftovers or "}" in leftovers:
        raise Failure(
            f"version.format = '{template}': the only placeholders are "
            f"{{mc}} and {{mod}}"
        )
    if "mod" not in names:
        raise Failure(
            f"version.format = '{template}': {{mod}} is required, it is the part "
            f"a release actually chooses"
        )
    if len(names) != len(set(names)):
        raise Failure(f"version.format = '{template}': a placeholder is repeated")
    return names


def render_mod_version(template: str, mc: str, mod: str) -> str:
    """Build a mod_version from its two halves."""
    _template_parts(template)
    return template.format(mc=mc, mod=mod)


def parse_mod_version(template: str, value: str) -> dict[str, str]:
    """Split an existing mod_version according to the template.

    Raises rather than shrugging: a mod_version that does not match the declared
    format means the automation is about to rewrite something it does not
    understand, and the previous behaviour — warn, then leave the file alone —
    produced a jar whose name silently disagreed with what was tested.
    """
    names = _template_parts(template)

    def pattern(mc_shape: str | None) -> str:
        out = ""
        for index, chunk in enumerate(PLACEHOLDER.split(template)):
            if chunk in ("mc", "mod") and index % 2 == 1:
                if chunk == "mc" and mc_shape:
                    out += rf"(?P<mc>{mc_shape})"
                    continue
                # Only the LAST placeholder is greedy, so "{mc}-{mod}" splits
                # "26.1.x-1.1.0-beta" into 26.1.x and 1.1.0-beta rather than the
                # other way round.
                quantifier = "+" if chunk == names[-1] else "+?"
                out += rf"(?P<{chunk}>\S{quantifier})"
            else:
                out += re.escape(chunk)
        return out

    # The shape of a label first: a release candidate carries dashes of its own,
    # and the lazy split alone read "26.2-rc-1-1.1.0" as 26.2 and "rc-1-1.1.0".
    # The lazy split stays as the fallback for a label of any other shape.
    match = re.fullmatch(pattern(MC_LABEL), value) or re.fullmatch(pattern(None), value)
    if not match:
        raise Failure(
            f"mod_version = '{value}' does not match version.format = '{template}'. "
            f"Fix one of the two: mc-bump refuses to rewrite a version number it "
            f"cannot read."
        )
    return match.groupdict()


def update_mod_version(template: str, current: str, covered: list[str]) -> str:
    """Rewrite the {mc} half of a mod_version, keeping the {mod} half.

    `covered` is the set of versions the jar claims, so the file name says exactly
    what was tested — see mc_label(). A template without {mc} (a mod that does not
    put Minecraft in its version number) is returned untouched.
    """
    if "mc" not in _template_parts(template):
        return current
    label = mc_label(covered)
    if not label:
        return current
    return render_mod_version(template, mc=label, mod=parse_mod_version(template, current)["mod"])
