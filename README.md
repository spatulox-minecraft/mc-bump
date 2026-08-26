# mc-bump

> This is a school project where we needed to entirely code the project with AI
> while deciding the architecture, functionality and more.
> At one point of the project, it might be humanly rework if I have time,
> patience and determination

Reusable CI for Minecraft mods: follow a new Minecraft release, prove the mod
still works on **every version it claims**, and publish it, driven by one config
file in the mod's repository.

Built for Fabric.

**[Read the wiki](https://github.com/spatulox-minecraft/mc-bump/wiki)** for
everything below in detail.

## What it does

| Pipeline | What it is for |
|---|---|
| `ci` | On every push and pull request: unit tests, then build and boot a server for each claimed Minecraft version, plus the client gametest. |
| `auto-update` | Weekly: detect a new Minecraft release, resolve the dependencies, run the matrix, escalate the frozen dependencies if it fails, open a pull request. |
| `release` | On merge: prove the matrix again, publish to Modrinth and CurseForge in separate, individually replayable jobs, then tag. |

Two ideas hold the whole thing together.

**One jar covers one series.** `26.1.2` and `26.1` are the same series, and a jar
that claims the series must be booted on every version of it, not just the
newest. That is what the version matrix does, and it is why the compatibility
range is only widened *after* the matrix proves it.

**An update moves one variable.** Minecraft. The loader and its API stay frozen,
and only move through an escalation ladder as a reaction to a red matrix, one at
a time, each followed by a full matrix re-run. A red matrix therefore has one
suspect instead of three.

## Using it

Add `.github/mc-bump.yml` to your mod:

```yaml
loader: fabric

mod:
  id: my-mod
  metadata: src/main/resources/fabric.mod.json
```

Then one workflow per pipeline:

```yaml
# .github/workflows/ci.yml
name: CI
on:
  push: { branches: [master] }
  pull_request:
permissions:
  contents: read
  issues: write
jobs:
  ci:
    uses: spatulox-minecraft/mc-bump/.github/workflows/ci.yml@v1
    secrets: inherit
```

Full setup, including what your `gradle.properties` and `build.gradle` must
already have:
**[Getting Started](https://github.com/spatulox-minecraft/mc-bump/wiki/Getting-Started)**.

## Documentation

| | |
|---|---|
| [Getting Started](https://github.com/spatulox-minecraft/mc-bump/wiki/Getting-Started) | What your mod repository needs, and the three workflow files to add. |
| [Configuration](https://github.com/spatulox-minecraft/mc-bump/wiki/Configuration) | Every key of `.github/mc-bump.yml`, with its default. |
| [Proving the mod works](https://github.com/spatulox-minecraft/mc-bump/wiki/Proving-the-mod-works) | `expect` and `expect-count`, turning "it booted" into "it worked". |
| [Versions and compatibility](https://github.com/spatulox-minecraft/mc-bump/wiki/Versions-and-compatibility) | What mc-bump writes into your version numbers, and why. |
| [Pipelines](https://github.com/spatulox-minecraft/mc-bump/wiki/Pipelines) | What each workflow does, job by job. |
| [CLI](https://github.com/spatulox-minecraft/mc-bump/wiki/CLI) | Running everything locally, with no GitHub involved. |
| [Composite Action](https://github.com/spatulox-minecraft/mc-bump/wiki/Composite-Action) | The escape hatch for a pipeline of your own. |
| [Troubleshooting](https://github.com/spatulox-minecraft/mc-bump/wiki/Troubleshooting) | Error messages, exit codes, and what they mean. |

## Running it locally

Everything the CI runs, runs from the mod's repository with no GitHub involved:

```bash
MCB=../mc-bump

python3 $MCB/scripts/mc-bump.py --list-test-versions   # what the matrix will boot
python3 $MCB/scripts/headless-server-test.py           # one server
python3 $MCB/scripts/test-matrix.py                    # every claimed version
```

Requires `python3` and `pyyaml`, and nothing else. See
**[CLI](https://github.com/spatulox-minecraft/mc-bump/wiki/CLI)** for the flags,
the exit codes and the environment variables.

## Developing mc-bump

```bash
python3 -m unittest discover -s tests -t .
```

Developer documentation lives in [`doc/`](doc/):
[contributing](doc/contributing.md) for the test policy and the self-test,
[internals](doc/internals.md) for how the pieces fit,
[adding a loader](doc/adding-a-loader.md) for NeoForge and friends. Module by
module notes sit next to the code, in [`lib/`](lib/README.md),
[`scripts/`](scripts/README.md) and [`tests/`](tests/README.md).
