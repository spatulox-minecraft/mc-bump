#!/usr/bin/env python3
"""
Resout les versions Fabric correspondant a une version de Minecraft et met a
jour gradle.properties (et fabric.mod.json si le major change).

Uniquement la bibliotheque standard : aucun pip install necessaire.

Exemples
--------
    # derniere release Mojang
    python3 scripts/update-mc-version.py

    # version precise
    python3 scripts/update-mc-version.py 26.2

    # voir ce qui changerait sans rien ecrire
    python3 scripts/update-mc-version.py 26.2 --dry-run

    # sortie machine (utilisee par le workflow GitHub)
    python3 scripts/update-mc-version.py --json

Codes de sortie
---------------
    0  succes (mise a jour effectuee, ou deja a jour)
    1  erreur (reseau, fichier introuvable, version introuvable...)
    2  Fabric ne supporte pas encore cette version de Minecraft
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
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


class Failure(Exception):
    """Erreur attendue, affichee proprement sans traceback."""


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def get_json(url: str, params: dict[str, str] | None = None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Fabric meta repond 400 (et non 404) pour une version inconnue, avec
        # un corps JSON valide : on le renvoie plutot que de lever.
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            raise Failure(f"HTTP {exc.code} sur {url}") from exc
    except urllib.error.URLError as exc:
        raise Failure(f"impossible de joindre {url} : {exc.reason}") from exc


# --------------------------------------------------------------------------
# Resolution des versions
# --------------------------------------------------------------------------
def latest_minecraft_release() -> str:
    data = get_json(MOJANG_MANIFEST)
    version = (data or {}).get("latest", {}).get("release")
    if not version:
        raise Failure("impossible de lire .latest.release du manifeste Mojang")
    return version


def fabric_supports(minecraft_version: str) -> bool:
    data = get_json(f"{FABRIC_META}/loader/{urllib.parse.quote(minecraft_version)}")
    return isinstance(data, list) and len(data) > 0


def latest_stable_loader() -> str:
    data = get_json(f"{FABRIC_META}/loader")
    for entry in data or []:
        if entry.get("stable"):
            return entry["version"]
    raise Failure("aucune version stable du Fabric Loader trouvee")


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
    # Modrinth renvoie generalement du plus recent au plus ancien, mais on ne
    # s'appuie pas dessus.
    newest = max(data, key=lambda v: v.get("date_published", ""))
    return newest.get("version_number")


# --------------------------------------------------------------------------
# Edition des fichiers
# --------------------------------------------------------------------------
def read_property(text: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def set_property(text: str, key: str, value: str) -> str:
    pattern = rf"^{re.escape(key)}=.*$"
    if not re.search(pattern, text, re.MULTILINE):
        raise Failure(f"cle '{key}' introuvable dans {GRADLE_PROPERTIES.name}")
    # lambda pour ne pas interpreter \1, \g<...> etc. dans la valeur
    return re.sub(pattern, lambda _: f"{key}={value}", text, count=1, flags=re.MULTILINE)


def major_of(version: str) -> str:
    return version.split(".", 1)[0]


def write_preserving_final_newline(path: Path, original: str, new: str) -> None:
    """Ecrit `new` en conservant la presence (ou l'absence) de newline final."""
    if original.endswith("\n") and not new.endswith("\n"):
        new += "\n"
    elif not original.endswith("\n"):
        new = new.rstrip("\n")
    path.write_text(new, encoding="utf-8")


def update_gradle_properties(
    minecraft_version: str,
    loader_version: str,
    fabric_api_version: str,
    major_changed: bool,
    dry_run: bool,
) -> tuple[bool, list[str]]:
    original = GRADLE_PROPERTIES.read_text(encoding="utf-8")
    text = original

    text = set_property(text, "minecraft_version", minecraft_version)
    text = set_property(text, "loader_version", loader_version)
    text = set_property(text, "fabric_api_version", fabric_api_version)

    supported_raw = read_property(text, "supported_minecraft_versions") or ""
    supported = [v.strip() for v in supported_raw.split(",") if v.strip()]
    if major_changed:
        # nouveau major : les anciennes versions ne sont plus couvertes par la
        # borne depends.minecraft de fabric.mod.json
        supported = [minecraft_version]
    elif minecraft_version not in supported:
        supported.append(minecraft_version)
    text = set_property(text, "supported_minecraft_versions", ",".join(supported))

    changed = text != original
    if changed and not dry_run:
        write_preserving_final_newline(GRADLE_PROPERTIES, original, text)
    return changed, supported


def update_fabric_mod_json(minecraft_version: str, dry_run: bool) -> str | None:
    """Reecrit depends.minecraft. Retourne la nouvelle borne, ou None."""
    original = FABRIC_MOD_JSON.read_text(encoding="utf-8")
    data = json.loads(original)

    parts = minecraft_version.split(".")
    floor = minecraft_version if len(parts) >= 3 else f"{minecraft_version}.0"
    ceiling = int(major_of(minecraft_version)) + 1
    new_range = f">={floor} <{ceiling}"

    if data.get("depends", {}).get("minecraft") == new_range:
        return None

    data.setdefault("depends", {})["minecraft"] = new_range
    if not dry_run:
        rendered = json.dumps(data, indent="\t", ensure_ascii=False)
        write_preserving_final_newline(FABRIC_MOD_JSON, original, rendered)
    return new_range


# --------------------------------------------------------------------------
# Sortie
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
        description="Met a jour les versions Minecraft/Fabric dans gradle.properties.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "minecraft_version",
        nargs="?",
        help="version cible (ex: 26.2). Par defaut : derniere release Mojang.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="affiche les changements sans ecrire"
    )
    parser.add_argument(
        "--json", action="store_true", help="sortie JSON sur stdout, rien d'autre"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="continuer meme si la version est deja celle du repo",
    )
    args = parser.parse_args()

    quiet = args.json

    def log(message: str = "") -> None:
        if not quiet:
            print(message)

    for path in (GRADLE_PROPERTIES, FABRIC_MOD_JSON):
        if not path.exists():
            raise Failure(f"{path} introuvable — lance le script depuis le depot")

    current = read_property(
        GRADLE_PROPERTIES.read_text(encoding="utf-8"), "minecraft_version"
    )
    if not current:
        raise Failure("minecraft_version absent de gradle.properties")

    target = args.minecraft_version or latest_minecraft_release()
    log(f"Version actuelle : {current}")
    log(f"Version cible    : {target}")

    result = {
        "status": "",
        "minecraft_version": target,
        "previous_version": current,
        "loader_version": None,
        "fabric_api_version": None,
        "changed": False,
    }

    if target == current and not args.force:
        log("\nDeja a jour. (--force pour reappliquer)")
        result["status"] = "up-to-date"
        if args.json:
            print(json.dumps(result, indent=2))
        emit_github_output(result)
        return 0

    if not fabric_supports(target):
        log(f"\nFabric ne supporte pas encore Minecraft {target}.")
        result["status"] = "unsupported"
        if args.json:
            print(json.dumps(result, indent=2))
        emit_github_output(result)
        return 2

    loader = latest_stable_loader()
    fabric_api = latest_fabric_api(target)
    if not fabric_api:
        log(f"\nPas encore de Fabric API publiee pour Minecraft {target}.")
        result["status"] = "unsupported"
        result["loader_version"] = loader
        if args.json:
            print(json.dumps(result, indent=2))
        emit_github_output(result)
        return 2

    log(f"\n  loader_version     = {loader}")
    log(f"  fabric_api_version = {fabric_api}")

    major_changed = major_of(target) != major_of(current)
    changed, supported = update_gradle_properties(
        target, loader, fabric_api, major_changed, args.dry_run
    )

    new_range = None
    if major_changed:
        new_range = update_fabric_mod_json(target, args.dry_run)

    log(f"  supported_minecraft_versions = {','.join(supported)}")
    if new_range:
        log(f"  fabric.mod.json depends.minecraft = {new_range}")
    elif major_changed:
        log("  fabric.mod.json : deja bon")

    result.update(
        {
            "status": "updated",
            "loader_version": loader,
            "fabric_api_version": fabric_api,
            "supported_minecraft_versions": supported,
            "minecraft_range": new_range,
            "changed": changed or bool(new_range),
        }
    )

    if args.dry_run:
        log("\n--dry-run : aucun fichier modifie.")
    elif result["changed"]:
        log("\nFichiers mis a jour. Pense a resynchroniser Gradle dans IntelliJ")
        log("(Ctrl+Shift+O), puis : ./gradlew build")
    else:
        log("\nRien a changer.")

    if args.json:
        print(json.dumps(result, indent=2))
    emit_github_output(result)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as error:
        print(f"erreur : {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
