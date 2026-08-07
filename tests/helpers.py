"""A throwaway mod repository, so tests exercise the real loading path.

The previous suite monkeypatched module level path constants. Building an actual
directory instead is both shorter and stricter: it goes through find_root(),
schema validation and ModPaths, so a config change that breaks the contract fails
in the tests rather than on the first CI run of some other repository.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lib import config as config_module

CONFIG = """\
loader: fabric

mod:
  id: extended-time-potion
  package: com.spatulox
  metadata: src/main/resources/fabric.mod.json
  mixins: src/main/resources/mixins.json

version:
  format: "{mc}-{mod}"

tests:
  server:
    expect:
      - pattern: "Brewing mixes registered"
        message: "the brewing callback never ran"
    expect-count:
      - pattern: "Registered ([0-9]+) potions"
        count-source: src/main/java/com/spatulox/ExtendedTimePotion.java
        count-pattern: "= registerPotion("
        message: "potion count mismatch"

notify:
  assignee: Spatulox
"""

PROPERTIES = """\
minecraft_version=26.1.1
loader_version=0.19.3
loom_version=1.17.18
java_version=25
mod_version=26.1.x-1.1.0
fabric_api_version=0.155.2+26.1.1
supported_minecraft_versions=26.1,26.1.1
"""

MOD_JSON = {
    "schemaVersion": 1,
    "id": "extended-time-potion",
    "depends": {
        "fabricloader": ">=0.18.4",
        "minecraft": ">=26.1 <=26.1.1",
        "java": ">=25",
        "fabric-api": "*",
    },
}

MIXINS_JSON = {"required": True, "compatibilityLevel": "JAVA_25"}

SILENT = lambda *_args, **_kwargs: None  # noqa: E731 - the log callback


def write_mod(root: Path, *, config: str = CONFIG, properties: str = PROPERTIES) -> None:
    (root / ".github").mkdir(parents=True, exist_ok=True)
    (root / ".github/mc-bump.yml").write_text(config, encoding="utf-8")
    (root / "gradle.properties").write_text(properties, encoding="utf-8")

    resources = root / "src/main/resources"
    resources.mkdir(parents=True, exist_ok=True)
    (resources / "fabric.mod.json").write_text(
        json.dumps(MOD_JSON, indent="\t"), encoding="utf-8"
    )
    (resources / "mixins.json").write_text(
        json.dumps(MIXINS_JSON, indent="\t"), encoding="utf-8"
    )


class ModRepoTestCase(unittest.TestCase):
    """A loaded Project over a temporary mod repository."""

    CONFIG = CONFIG
    PROPERTIES = PROPERTIES

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write_mod(self.root, config=self.CONFIG, properties=self.PROPERTIES)
        self.project = config_module.load(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def reload(self):
        self.project = config_module.load(self.root)
        return self.project

    # -- helpers
    @property
    def properties_file(self) -> Path:
        return self.project.paths.gradle_properties

    def prop(self, key: str):
        from lib.gradle import read_property

        return read_property(self.properties_file.read_text(encoding="utf-8"), key)

    def set_prop(self, key: str, value: str) -> None:
        from lib.gradle import set_property

        self.properties_file.write_text(
            set_property(self.properties_file.read_text(encoding="utf-8"), key, value),
            encoding="utf-8",
        )

    def depends(self, key: str):
        return json.loads(self.project.paths.metadata.read_text(encoding="utf-8"))[
            "depends"
        ][key]
