# Getting Started

Three things: what your repository must already have, the config file, and one
workflow per pipeline.

## 1. What your mod repository needs

mc-bump edits **your** repository, so it has to recognise what it reads.

### `gradle.properties`

These keys are read, and some are rewritten by an update.

| Key | Read | Written by an update | Meaning |
|---|---|---|---|
| `minecraft_version` | ✅ | ✅ | the version currently targeted |
| `supported_minecraft_versions` | ✅ | ✅ | comma separated, what the stores announce. **Only written after a green matrix.** |
| `mod_version` | ✅ | ✅ | follows `version.format`, e.g. `26.1.x-1.1.0` |
| `java_version` | ✅ | ✅ | derived from the Minecraft version, via the Mojang manifest |
| `loader_version` | ✅ | frozen¹ | `fabricloader` |
| `fabric_api_version` | ✅ | frozen¹ | Fabric API |
| `loom_version` | ✅ | ✅ | the build plugin follows, it does not ship in the jar |
| `archives_base_name` | | | used by your `build.gradle` for the jar name |

¹ Frozen means an update never touches them. Only the
[escalation ladder](Versions-and-compatibility#the-escalation-ladder) moves them,
one rung at a time, after a red matrix.

A minimal file:

```properties
org.gradle.jvmargs=-Xmx1G

minecraft_version=26.2
loader_version=0.19.3
loom_version=1.17.18
java_version=25

mod_version=26.2-1.0.0
maven_group=com.example.mymod
archives_base_name=my-mod

fabric_api_version=0.156.0+26.2

supported_minecraft_versions=26.2
```

### `build.gradle`

The matrix builds every claimed version in the same checkout, so the Minecraft
version has to be overridable from the command line:

```bash
./gradlew build -Pminecraft_version=26.1.1
```

Reading it through `project.minecraft_version` is enough, since Gradle lets `-P`
override a `gradle.properties` value on its own:

```groovy
plugins {
    id "net.fabricmc.fabric-loom" version "${loom_version}"
}

base { archivesName = project.archives_base_name }

dependencies {
    minecraft "com.mojang:minecraft:${project.minecraft_version}"
    // ...
}
```

### Gradle tasks

| Task | Used by | Configurable |
|---|---|---|
| `build` | every pipeline | no |
| `test` | the unit-test job | `tests.unit.task` |
| `runServer` | the headless server test | no |
| `runClientGameTest` | the gametest job | no |
| `modrinth` | the release job | only when `modrinth` is in `release.stores` |
| `publishCurseForge` | the release job | only when `curseforge` is in `release.stores` |

The two publish tasks come from your own build script (`minotaur`,
`cf-gradle-plugin`, and so on). mc-bump only calls them.

### `fabric.mod.json`

At the path given by `mod.metadata`. mc-bump rewrites `depends.minecraft` (the
compatibility range), `depends.java`, and, when the ladder moved them,
`depends.fabricloader` and `depends.fabric-api`.

If you use mixins, point `mod.mixins` at the mixin config so its
`compatibilityLevel` follows the Java version.

## 2. `.github/mc-bump.yml`

The minimum. Everything else has a default, see [Configuration](Configuration).

```yaml
loader: fabric

mod:
  id: my-mod
  metadata: src/main/resources/fabric.mod.json
```

Validation is strict and **rejects unknown keys by name**, nested ones included.
Check it before pushing:

```bash
PYTHONPATH=../mc-bump python3 -m lib.config --json
```

## 3. The workflows

One file per pipeline, in your mod's `.github/workflows/`. Each one is a thin
`uses:`, since every decision lives in the config.

### CI

```yaml
# .github/workflows/ci.yml
name: CI
on:
  push: { branches: [master] }
  pull_request:
  workflow_dispatch:
permissions:
  contents: read
  issues: write        # the failure report opens an issue
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
jobs:
  ci:
    uses: spatulox-minecraft/mc-bump/.github/workflows/ci.yml@v1
    secrets: inherit
```

### Auto-update

```yaml
# .github/workflows/auto-update.yml
name: Auto-update
on:
  schedule: [{ cron: "0 4 * * 1" }]   # Monday, 04:00 UTC
  workflow_dispatch:
    inputs:
      minecraft-version:
        description: Force a specific Minecraft version. Empty = latest release.
        type: string
      force:
        description: Continue even if the repo is already on that version.
        type: boolean
        default: false
permissions:
  contents: write
  pull-requests: write
  issues: write
jobs:
  update:
    uses: spatulox-minecraft/mc-bump/.github/workflows/auto-update.yml@v1
    with:
      minecraft-version: ${{ inputs['minecraft-version'] }}
      force: ${{ inputs.force == true }}
    secrets: inherit
```

Two details worth copying exactly. A context property whose name contains a
hyphen cannot be reached with a dot, hence the index syntax. And
`inputs.force == true` rather than `inputs.force` is what keeps the scheduled run
working: on a `schedule` trigger `inputs` is empty, and an empty string is not a
boolean.

### Release

```yaml
# .github/workflows/release.yml
name: Release
on:
  push: { branches: [master] }
  workflow_dispatch:
    inputs:
      dry-run:
        description: Do everything except the uploads and the tag.
        type: boolean
        default: false
permissions:
  contents: write
  issues: write
jobs:
  release:
    uses: spatulox-minecraft/mc-bump/.github/workflows/release.yml@v1
    with:
      dry-run: ${{ inputs.dry-run == true }}
    secrets: inherit
```

The release is triggered by `mod_version` changing, not by "the claimed versions
grew". The tag carries `mod_version`, so it is the only field that guarantees a
unique name, and it covers a manual `1.1.0` to `1.2.0` with no Minecraft change.
Running it on an already released version is a no-op, by design.

## 4. Repository settings

| Setting | Why |
|---|---|
| **Settings → Actions → Allow GitHub Actions to create and approve pull requests** | the auto-update opens the PR with `GITHUB_TOKEN` |
| Secret `MODRINTH_TOKEN` | only if `modrinth` is in `release.stores` |
| Secret `CURSEFORGE_TOKEN` | only if `curseforge` is in `release.stores` |

The release pipeline checks both secrets **before** the matrix: a missing token
is a two second failure instead of a ninety minute one.

`secrets: inherit` is what passes them through. Without it, the reusable workflow
sees nothing.

## 5. Check it before trusting it

From your mod's repository, with mc-bump cloned next to it:

```bash
MCB=../mc-bump

python3 $MCB/scripts/mc-bump.py --list-test-versions   # what the matrix will boot
python3 $MCB/scripts/headless-server-test.py           # one server, right now
```

If the second one is green, your CI will be too. See [CLI](CLI) for the rest.

## Pinning a version

`@v1` is a moving tag: it follows the v1 line. Pin a commit SHA instead if you
want the pipeline to change only when you say so.

> **Note.** A pull request opened by the auto-update with the default
> `GITHUB_TOKEN` does **not** trigger `pull_request` workflows, so your `ci.yml`
> will not run on it. That is harmless: `auto-update` already built and booted
> every claimed version on that branch.
