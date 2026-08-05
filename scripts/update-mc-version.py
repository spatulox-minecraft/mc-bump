#!/usr/bin/env python3
"""
Resolve the Fabric versions matching a Minecraft version and update
gradle.properties and src/main/resources/fabric.mod.json.

Standard library only: no pip install required.

Examples
--------
    # latest Mojang release
    python3 scripts/update-mc-version.py

    # specific version
    python3 scripts/update-mc-version.py 26.2

    # see what would change without writing anything
    python3 scripts/update-mc-version.py 26.2 --dry-run

    # machine readable output (used by the GitHub workflow)
    python3 scripts/update-mc-version.py --json

    # update, then build and smoke test, and only claim compatibility if both pass
    python3 scripts/update-mc-version.py --run-tests

    # AFTER a successful build + server test: mark the version as compatible
    python3 scripts/update-mc-version.py --mark-supported

    # after a FAILED build or server test: restore the previous compatibility claims
    python3 scripts/update-mc-version.py --revert-compat

Compatibility model
-------------------
A "series" is the first two components of a Minecraft version (26.1.2 -> 26.1).
One jar covers one series. Two keys describe compatibility and they must agree:

    supported_minecraft_versions   what is ANNOUNCED on Modrinth / CurseForge
                                   (read by build.gradle, sent by Minotaur)
    depends.minecraft              what Fabric Loader LOADS or REFUSES at runtime

Both derive from the series, and the upper bound is the highest version actually
proven to work:

    minecraft_version   supported_minecraft_versions   depends.minecraft
    26.1                26.1                           "=26.1"
    26.1.1              26.1,26.1.1                    ">=26.1 <=26.1.1"
    26.1.2              26.1,26.1.1,26.1.2             ">=26.1 <=26.1.2"
    26.2                26.2            (series reset) "=26.2"

depends.minecraft has to be widened BEFORE the tests run, otherwise Fabric Loader
refuses to load the mod on the new version's server and no version could ever be
validated. So an update raises it optimistically, and then:

    build + server OK  -> --mark-supported keeps it and extends the list
    build or server KO -> --revert-compat restores BOTH keys to their previous
                          values, while keeping the version bumps themselves

The values needed by --revert-compat are snapshotted in .mc-update-state.json
(gitignored) before anything is written.

Exit codes
----------
    0  success (updated, or already up to date)
    1  error (network, missing file, version not found, failed tests...)
    2  Fabric does not support this Minecraft version yet
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "spatulox-minecraft/ExtendedTimePotion (version updater)"
TIMEOUT = 30

MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC_META = "https://meta.fabricmc.net/v2/versions"
MODRINTH_FABRIC_API = "https://api.modrinth.com/v2/project/fabric-api/version"

REPO_ROOT = Path(__file__).resolve().parent.parent
GRADLE_PROPERTIES = REPO_ROOT / "gradle.properties"
FABRIC_MOD_JSON = REPO_ROOT / "src/main/resources/fabric.mod.json"
UPDATE_STATE = REPO_ROOT / ".mc-update-state.json"

HEADLESS_TEST = ".github/scripts/headless-server-test.sh"


class Failure(Exception):
    """Expected error, printed cleanly without a traceback."""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def get_json(
    url: str,
    params: dict[str, str] | None = None,
    allow_status: tuple[int, ...] = (),
):
    """GET a JSON document.

    Any HTTP error raises, EXCEPT the statuses listed in allow_status, whose
    body is parsed and returned. Without that distinction a 5xx from an upstream
    API would be indistinguishable from a legitimate "not published yet" answer,
    and the workflow would silently stop updating while staying green.
    """
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code not in allow_status:
            raise Failure(f"HTTP {exc.code} from {url}") from exc
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise Failure(f"HTTP {exc.code} from {url}, body is not JSON") from exc
    except urllib.error.URLError as exc:
        raise Failure(f"cannot reach {url}: {exc.reason}") from exc


# --------------------------------------------------------------------------
# Version resolution
# --------------------------------------------------------------------------
def latest_minecraft_release() -> str:
    data = get_json(MOJANG_MANIFEST)
    version = (data or {}).get("latest", {}).get("release")
    if not version:
        raise Failure("cannot read .latest.release from the Mojang manifest")
    return version


def loader_for(minecraft_version: str) -> str | None:
    """Latest STABLE Fabric Loader listed for this Minecraft version.

    Returns None when Fabric does not support the version yet, or when it only
    lists unstable loaders. Asking Fabric for the loaders OF THAT VERSION, rather
    than for the latest stable loader overall, is what guarantees the loader we
    write is actually compatible with the Minecraft version we target.
    """
    # Fabric meta answers 400 (not 404) for an unknown Minecraft version, with a
    # valid JSON body, so that status is expected rather than an error.
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


def latest_fabric_api(minecraft_version: str) -> str | None:
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


# --------------------------------------------------------------------------
# Series and compatibility range
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


def compat_range(target: str, supported: list[str]) -> str:
    """depends.minecraft covering the target series up to its highest known version.

    The lower bound is the series itself, the upper bound is the highest version
    of that series among the proven ones plus the target. A series that has no
    sub-version yet is pinned exactly.
    """
    series = series_of(target)
    candidates = [
        version
        for version in [*supported, target]
        if series_of(version) == series and parse_version(version) is not None
    ]
    if not candidates:
        # Non numeric target (snapshot): nothing to order, pin it.
        return f"={target}"
    highest = max(candidates, key=parse_version)
    if highest == series:
        return f"={series}"
    return f">={series} <={highest}"


# --------------------------------------------------------------------------
# File editing
# --------------------------------------------------------------------------
def read_property(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def set_property(text: str, key: str, value: str) -> str:
    pattern = rf"^{re.escape(key)}=.*$"
    if not re.search(pattern, text, re.MULTILINE):
        raise Failure(f"key '{key}' not found in {GRADLE_PROPERTIES.name}")
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


def read_depends_minecraft() -> str | None:
    data = json.loads(FABRIC_MOD_JSON.read_text(encoding="utf-8"))
    return data.get("depends", {}).get("minecraft")


def write_depends_minecraft(value: str, dry_run: bool) -> bool:
    """Rewrite depends.minecraft. Returns True when the file changed."""
    original = FABRIC_MOD_JSON.read_text(encoding="utf-8")
    data = json.loads(original)
    if data.get("depends", {}).get("minecraft") == value:
        return False
    data.setdefault("depends", {})["minecraft"] = value
    if not dry_run:
        rendered = json.dumps(data, indent="\t", ensure_ascii=False)
        write_preserving_final_newline(FABRIC_MOD_JSON, original, rendered)
    return True


def update_mod_version(text: str, target: str, log) -> str:
    """Bump the Minecraft part of mod_version, shaped as "<mc series>-<mod version>".

    Only the left-hand side is derived from Minecraft; the right-hand side stays
    a manual release decision.
    """
    current = read_property(text, "mod_version")
    if current is None:
        raise Failure(f"key 'mod_version' not found in {GRADLE_PROPERTIES.name}")
    if "-" not in current:
        log(
            f"  /!\\ mod_version='{current}' has no '-' separator, "
            f"left untouched (expected '<mc series>-<mod version>')"
        )
        return text
    _, mod_part = current.split("-", 1)
    return set_property(text, "mod_version", f"{series_of(target)}-{mod_part}")


# --------------------------------------------------------------------------
# Update state, used by --revert-compat
# --------------------------------------------------------------------------
def save_update_state(target: str, supported_raw: str, depends: str | None) -> None:
    """Snapshot the compatibility claims as they were BEFORE the update.

    An existing snapshot for the same target is kept: it holds the genuine
    pre-bump values, which a re-run (--force) would otherwise overwrite with the
    already bumped ones. A snapshot for a different target is stale and replaced.
    """
    if UPDATE_STATE.exists():
        try:
            previous = json.loads(UPDATE_STATE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = {}
        if previous.get("target") == target:
            return
    UPDATE_STATE.write_text(
        json.dumps(
            {
                "target": target,
                "supported_minecraft_versions": supported_raw,
                "depends_minecraft": depends,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def clear_update_state() -> None:
    UPDATE_STATE.unlink(missing_ok=True)


def revert_compat(dry_run: bool) -> dict:
    """Restore the compatibility claims recorded before the last update.

    Only supported_minecraft_versions and depends.minecraft are restored. The
    version bumps (minecraft_version, loader_version, fabric_api_version,
    mod_version) are kept: they are the dependency diff someone picks up from.
    """
    if not UPDATE_STATE.exists():
        raise Failure(f"no update state to revert ({UPDATE_STATE.name} not found)")
    try:
        state = json.loads(UPDATE_STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Failure(f"{UPDATE_STATE.name} is not valid JSON") from exc

    original = GRADLE_PROPERTIES.read_text(encoding="utf-8")
    current = read_property(original, "minecraft_version")
    if state.get("target") != current:
        raise Failure(
            f"{UPDATE_STATE.name} was written for Minecraft {state.get('target')} "
            f"but gradle.properties is on {current}; refusing to revert"
        )

    supported = state.get("supported_minecraft_versions") or ""
    depends = state.get("depends_minecraft")

    text = set_property(original, "supported_minecraft_versions", supported)
    if text != original and not dry_run:
        write_preserving_final_newline(GRADLE_PROPERTIES, original, text)
    if depends is not None:
        write_depends_minecraft(depends, dry_run)
    if not dry_run:
        clear_update_state()

    return {
        "status": "reverted",
        "minecraft_version": current,
        "supported_minecraft_versions": split_supported(supported),
        "minecraft_range": depends,
    }


# --------------------------------------------------------------------------
# Writing the update
# --------------------------------------------------------------------------
def update_gradle_properties(
    minecraft_version: str,
    loader_version: str,
    fabric_api_version: str,
    dry_run: bool,
    log,
) -> tuple[bool, list[str], str | None]:
    original = GRADLE_PROPERTIES.read_text(encoding="utf-8")
    current = read_property(original, "minecraft_version") or ""
    text = original

    text = set_property(text, "minecraft_version", minecraft_version)
    text = set_property(text, "loader_version", loader_version)
    text = set_property(text, "fabric_api_version", fabric_api_version)
    text = update_mod_version(text, minecraft_version, log)

    # supported_minecraft_versions is what we ANNOUNCE on Modrinth / CurseForge,
    # so nothing here may add to it: at this point nothing proves the mod still
    # works. Only --mark-supported, called after a successful build and server
    # test, is allowed to extend it. Changing series does clear it though, since
    # a jar covers one series and the previous one is no longer the target.
    supported = split_supported(read_property(text, "supported_minecraft_versions"))
    if series_of(minecraft_version) != series_of(current):
        log(
            f"  Series change {series_of(current)} -> {series_of(minecraft_version)}: "
            f"supported_minecraft_versions reset."
        )
        supported = []
        text = set_property(text, "supported_minecraft_versions", "")

    changed = text != original
    if changed and not dry_run:
        write_preserving_final_newline(GRADLE_PROPERTIES, original, text)
    return changed, supported, read_property(text, "mod_version")


def mark_supported(dry_run: bool) -> tuple[str, list[str], str]:
    """Add the current minecraft_version to supported_minecraft_versions.

    Only to be called after a successful build and server test: this list is what
    is announced as compatible on Modrinth and CurseForge. depends.minecraft is
    recomputed from the same list so the two can never drift apart.
    """
    original = GRADLE_PROPERTIES.read_text(encoding="utf-8")
    current = read_property(original, "minecraft_version")
    if not current:
        raise Failure("minecraft_version missing from gradle.properties")

    supported = split_supported(read_property(original, "supported_minecraft_versions"))
    if current not in supported:
        supported.append(current)

    text = set_property(original, "supported_minecraft_versions", ",".join(supported))
    if text != original and not dry_run:
        write_preserving_final_newline(GRADLE_PROPERTIES, original, text)

    new_range = compat_range(current, supported)
    write_depends_minecraft(new_range, dry_run)

    # Compatibility is proven, there is nothing left to revert.
    if not dry_run:
        clear_update_state()
    return current, supported, new_range


# --------------------------------------------------------------------------
# Local build + smoke test
# --------------------------------------------------------------------------
def run_tests(log) -> str | None:
    """Run the same build and smoke test as CI. Returns the failed step, or None."""
    gradlew = "gradlew.bat" if os.name == "nt" else "./gradlew"
    steps = [
        ("build", [gradlew, "build", "--stacktrace"]),
        ("headless server test", ["bash", HEADLESS_TEST]),
    ]
    for name, command in steps:
        log(f"\n==> {name}: {' '.join(command)}")
        try:
            completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
        except OSError as exc:
            raise Failure(f"cannot run '{command[0]}': {exc}") from exc
        if completed.returncode != 0:
            return name
    return None


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def emit_github_output(result: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in result.items():
            if isinstance(value, list):
                value = ",".join(value)
            if isinstance(value, bool):
                value = str(value).lower()
            handle.write(f"{key}={'' if value is None else value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update the Minecraft/Fabric versions in gradle.properties "
        "and the compatibility range in fabric.mod.json.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "minecraft_version",
        nargs="?",
        help="target version (e.g. 26.2). Defaults to the latest Mojang release.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="show the changes without writing"
    )
    parser.add_argument(
        "--json", action="store_true", help="JSON output on stdout, nothing else"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="continue even if the repo is already on that version",
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="after the update, run './gradlew build' then the headless server "
        "test, and mark the version as supported only if both pass. On failure "
        "the compatibility claims are reverted and the exit code is 1.",
    )
    parser.add_argument(
        "--mark-supported",
        action="store_true",
        help="add the current minecraft_version to supported_minecraft_versions. "
        "Use ONLY AFTER a successful build and server test: this list is what is "
        "announced as compatible on Modrinth/CurseForge.",
    )
    parser.add_argument(
        "--revert-compat",
        action="store_true",
        help="restore supported_minecraft_versions and depends.minecraft to the "
        "values recorded before the last update, keeping the version bumps. Use "
        "after a failed build or server test.",
    )
    args = parser.parse_args()

    modes = [args.mark_supported, args.revert_compat, args.run_tests]
    if sum(bool(mode) for mode in modes) > 1:
        parser.error(
            "--mark-supported, --revert-compat and --run-tests are mutually exclusive"
        )
    if args.run_tests and (args.dry_run or args.json):
        parser.error("--run-tests cannot be combined with --dry-run or --json")

    quiet = args.json

    def log(message: str = "") -> None:
        if not quiet:
            print(message)

    for path in (GRADLE_PROPERTIES, FABRIC_MOD_JSON):
        if not path.exists():
            raise Failure(f"{path} not found — run the script from the repository")

    if args.revert_compat:
        result = revert_compat(args.dry_run)
        log(f"Compatibility claims restored for Minecraft {result['minecraft_version']}.")
        log(
            f"  supported_minecraft_versions = "
            f"{','.join(result['supported_minecraft_versions']) or '(empty)'}"
        )
        log(f"  fabric.mod.json depends.minecraft = {result['minecraft_range']}")
        log("  The version bumps themselves are kept.")
        if args.dry_run:
            log("\n--dry-run: no file modified.")
        if args.json:
            print(json.dumps(result, indent=2))
        emit_github_output(result)
        return 0

    if args.mark_supported:
        version, supported, new_range = mark_supported(args.dry_run)
        log(f"Minecraft {version} marked as compatible.")
        log(f"  supported_minecraft_versions = {','.join(supported)}")
        log(f"  fabric.mod.json depends.minecraft = {new_range}")
        if args.dry_run:
            log("\n--dry-run: no file modified.")
        result = {
            "status": "marked-supported",
            "minecraft_version": version,
            "supported_minecraft_versions": supported,
            "minecraft_range": new_range,
        }
        if args.json:
            print(json.dumps(result, indent=2))
        emit_github_output(result)
        return 0

    current = read_property(
        GRADLE_PROPERTIES.read_text(encoding="utf-8"), "minecraft_version"
    )
    if not current:
        raise Failure("minecraft_version missing from gradle.properties")

    target = args.minecraft_version or latest_minecraft_release()
    log(f"Current version : {current}")
    log(f"Target version  : {target}")

    result = {
        "status": "",
        "minecraft_version": target,
        "previous_version": current,
        "series": series_of(target),
        "loader_version": None,
        "fabric_api_version": None,
        "mod_version": None,
        "minecraft_range": None,
        "changed": False,
    }

    def stop(status: str, message: str, code: int) -> int:
        log(f"\n{message}")
        result["status"] = status
        if args.json:
            print(json.dumps(result, indent=2))
        emit_github_output(result)
        return code

    if target == current and not args.force:
        return stop("up-to-date", "Already up to date. (--force to reapply)", 0)

    loader = loader_for(target)
    if not loader:
        return stop(
            "unsupported",
            f"No stable Fabric Loader listed for Minecraft {target} yet.",
            2,
        )

    fabric_api = latest_fabric_api(target)
    if not fabric_api:
        result["loader_version"] = loader
        return stop(
            "unsupported", f"No Fabric API published for Minecraft {target} yet.", 2
        )

    log(f"\n  loader_version     = {loader}")
    log(f"  fabric_api_version = {fabric_api}")

    # Snapshot the compatibility claims before touching anything, so a failed
    # build or server test can restore them with --revert-compat.
    if not args.dry_run:
        save_update_state(
            target,
            read_property(
                GRADLE_PROPERTIES.read_text(encoding="utf-8"),
                "supported_minecraft_versions",
            )
            or "",
            read_depends_minecraft(),
        )

    changed, supported, mod_version = update_gradle_properties(
        target, loader, fabric_api, args.dry_run, log
    )

    # depends.minecraft has to be widened NOW, before the tests: otherwise Fabric
    # Loader refuses to load the mod on the new version and no version could ever
    # be validated. --revert-compat undoes it if the tests fail.
    new_range = compat_range(target, supported)
    range_changed = write_depends_minecraft(new_range, args.dry_run)

    log(f"  mod_version        = {mod_version}")
    log(f"  supported_minecraft_versions = {','.join(supported) or '(empty)'}")
    log(f"  fabric.mod.json depends.minecraft = {new_range}")

    result.update(
        {
            "status": "updated",
            "loader_version": loader,
            "fabric_api_version": fabric_api,
            "mod_version": mod_version,
            "supported_minecraft_versions": supported,
            "minecraft_range": new_range,
            "changed": changed or range_changed,
        }
    )

    if target not in supported:
        log(
            f"\n  /!\\ Minecraft {target} is NOT marked as compatible yet.\n"
            f"      Nothing proves the mod works at this point. Run the build and\n"
            f"      the server test, then, if they pass:\n"
            f"        ./gradlew build && bash {HEADLESS_TEST}\n"
            f"        python3 scripts/update-mc-version.py --mark-supported\n"
            f"      If they fail:\n"
            f"        python3 scripts/update-mc-version.py --revert-compat"
        )

    if args.dry_run:
        log("\n--dry-run: no file modified.")
    elif result["changed"]:
        log("\nFiles updated. Remember to resync Gradle in IntelliJ (Ctrl+Shift+O).")
    else:
        log("\nNothing to change.")

    if args.run_tests:
        failed = run_tests(log)
        if failed:
            reverted = revert_compat(dry_run=False)
            result["status"] = "tests-failed"
            result["failed_step"] = failed
            result["supported_minecraft_versions"] = reverted[
                "supported_minecraft_versions"
            ]
            result["minecraft_range"] = reverted["minecraft_range"]
            log(
                f"\n=== FAILED: {failed} on Minecraft {target} ===\n"
                f"    Compatibility claims restored, version bumps kept.\n"
                f"    depends.minecraft = {reverted['minecraft_range']}"
            )
            emit_github_output(result)
            return 1
        version, supported, new_range = mark_supported(dry_run=False)
        result["status"] = "validated"
        result["supported_minecraft_versions"] = supported
        result["minecraft_range"] = new_range
        log(
            f"\n=== OK: Minecraft {version} builds and the server starts ===\n"
            f"    supported_minecraft_versions = {','.join(supported)}\n"
            f"    depends.minecraft = {new_range}"
        )

    if args.json:
        print(json.dumps(result, indent=2))
    emit_github_output(result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
