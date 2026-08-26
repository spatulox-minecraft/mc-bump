# mc-bump

Reusable CI for Minecraft mods: follow a new Minecraft release, prove the mod
still works on **every version it claims**, and publish it, driven by one config
file in your mod's repository.

Built for Fabric.

```yaml
# .github/workflows/ci.yml, in your mod
jobs:
  ci:
    uses: spatulox-minecraft/mc-bump/.github/workflows/ci.yml@v1
    secrets: inherit
```

## Start here

| | |
|---|---|
| **[Getting Started](Getting-Started)** | What your mod repository needs, and the three workflow files to add. |
| **[Configuration](Configuration)** | Every key of `.github/mc-bump.yml`, with its default. |
| **[Proving the mod works](Proving-the-mod-works)** | `expect` and `expect-count`, turning "it booted" into "it worked". |
| **[Versions and compatibility](Versions-and-compatibility)** | What mc-bump writes into your version numbers, and why. |
| **[Pipelines](Pipelines)** | What `ci`, `auto-update` and `release` actually do, job by job. |
| **[CLI](CLI)** | Running everything locally, with no GitHub involved. |
| **[Composite Action](Composite-Action)** | The escape hatch for a pipeline of your own. |
| **[Troubleshooting](Troubleshooting)** | Error messages, exit codes, and what they mean. |

## The two ideas

Everything else follows from these.

### One jar covers one series

`26.1.2` and `26.1` are the same series, and a jar that claims the series must be
booted on **every version of it**, not just the newest. That is what the version
matrix does, and it is why your compatibility range is only widened *after* the
matrix proves it.

The alternative, testing the newest and announcing the series, publishes claims
about versions nobody ever ran.

### An update moves one variable

Minecraft. The loader and its API stay **frozen**, and only move through an
[escalation ladder](Versions-and-compatibility#the-escalation-ladder) as a
reaction to a red matrix, one at a time, each followed by a full matrix re-run.

A red matrix therefore has one suspect instead of three.

## The three pipelines

| Pipeline | What it is for |
|---|---|
| [`ci`](Pipelines#ci) | On every push and pull request: unit tests, then build and boot a server for each claimed Minecraft version, plus the client gametest. |
| [`auto-update`](Pipelines#auto-update) | Weekly: detect a new Minecraft release, resolve the dependencies, run the matrix, escalate the frozen dependencies if it fails, open a pull request. |
| [`release`](Pipelines#release) | On merge: prove the matrix again, publish to Modrinth and CurseForge in **separate, individually replayable jobs**, then tag. |

---

> This is a school project where we needed to entirely code the project with AI
> while deciding the architecture, functionality and more. At one point of the
> project, it might be humanly reworked if I have time, patience and
> determination.
