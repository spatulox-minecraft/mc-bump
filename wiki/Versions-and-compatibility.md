# Versions and compatibility

What mc-bump writes into your version numbers, when it writes it, and why. This
is the part worth reading before your first auto-update lands, because it is what
decides what your mod *claims*.

## Series

A **series** is the first two components of a Minecraft version: `26.1.2` becomes
`26.1`. One jar covers one series.

Two claims describe compatibility, and they must agree:

| Claim | Where | Meaning |
|---|---|---|
| `supported_minecraft_versions` | `gradle.properties` | what is **announced** on Modrinth and CurseForge |
| `depends.minecraft` | `fabric.mod.json` | what the **loader** accepts or refuses at runtime |

Both derive from the series, and the upper bound is the highest version actually
proven to work:

| `minecraft_version` | `supported_minecraft_versions` | range | |
|---|---|---|---|
| `26.1` | `26.1` | `=26.1` | exact |
| `26.1.1` | `26.1,26.1.1` | `>=26.1 <=26.1.1` | |
| `26.1.2` | `26.1,26.1.1,26.1.2` | `>=26.1 <=26.1.2` | |
| `26.2` | `26.2` (series reset) | `=26.2` | exact |

A move to a new series **resets** the supported list. The old jar keeps covering
the old series; the new one starts from nothing and earns its versions back one
green matrix at a time.

Versions are ordered **numerically**, not lexically, so `26.1.10` sorts after
`26.1.9`.

## Release candidates and snapshots

Only followed when [`minecraft.channels`](Configuration#minecraft) asks for them.
A non-release is **its own series** and is pinned exactly, never folded into a
range:

| `minecraft_version` | `supported_minecraft_versions` | range | jar |
|---|---|---|---|
| `26.2-snapshot-3` | `26.2-snapshot-3` | `=26.2-alpha.3` | `26.2-snapshot-3-1.1.0` |
| `26.2-rc-1` | `26.2-rc-1` (reset) | `=26.2-rc.1` | `26.2-rc-1-1.1.0` |
| `26.2` | `26.2` (reset) | `=26.2` | `26.2-1.1.0` |

The matrix boots that one version. A jar proven on a candidate promises nothing
about the release, so the release starts its supported list from nothing, like
any new series.

The range is written the way **Fabric Loader** reads it, not the way Mojang spells
the id: the loader rewrites `26.2-rc-1` into `26.2-rc.1` before comparing, and
would refuse a mod declaring `=26.2-rc-1` on the very server it targets.

| Mojang id | Fabric |
|---|---|
| `26.2-snapshot-N` | `26.2-alpha.N` |
| `26.2-pre-N` | `26.2-pre.N` |
| `26.2-rc-N` | `26.2-rc.N` |
| `1.21.11-preN` | `1.21.11-beta.N` |
| `1.21.11-rcN` | `1.21.11-rc.N` |

A weekly snapshot id (`25w45a`) is **refused**: Fabric maps it onto the release it
leads to through a table only the loader has, and a wrong guess is a mod the
loader silently never loads. Mojang stopped shipping that shape with 26.1.

<details>
<summary>@v1</summary>

Only Minecraft releases are followed. Forcing a release candidate through the
`minecraft-version` input does not work end to end:

- the matrix has nothing to boot, the non-numeric target is filtered out;
- the range is written as Mojang spells the id (`=26.2-rc-1`), which Fabric Loader
  refuses;
- with `{mc}-{mod}`, `mod_version` is misread (`26.2` and `rc-1-1.1.0`);
- the server test expects `26.2-rc-1` where Minecraft logs
  `26.2 Release Candidate 1`.

</details>

## The jar name says what was tested

`mod_version` carries the same information in its `{mc}` half:

```
[26.2]                      ->  26.2
[26.1, 26.1.1]              ->  26.1.x
[26.1, 26.1.1, 26.1.2]      ->  26.1.x
```

The wildcard **never climbs a level**. `26.1.x` yes, `26.x` never. `26.x` would
promise 26.3 to anyone reading the file name, and nothing has ever built, booted
or published that version.

On a green matrix, the supported list, the range and the jar name are all
recomputed from the same source in one pass, so the three can never drift apart.
Drift between them is the failure mc-bump exists to prevent: a store page
announcing a version the loader will refuse, or a jar whose name says more than
was tested.

## Why the range is widened before the tests

It has to be. The loader refuses to load your mod on a Minecraft version outside
its declared range, so with the old range the new version's server would never
start, and nothing could ever be validated. The range is what gates the very test
that would justify widening it.

So the sequence is optimistic, with a snapshot:

```
1. snapshot the previous claims into .mc-update-state.json
2. widen the range, optimistically, before anything has been proven
3. run the matrix
4a. green  ->  extend the claims, recompute everything from them
4b. red    ->  restore the claims, KEEP the version bumps
```

Step 4b is the one to understand. Your claims come back because the mod does not
work and must not say it does. The version bumps stay because they are the
dependency diff you pick up from; throwing them away would mean redoing the
resolution by hand.

This is why a failed auto-update leaves you a **draft** pull request rather than
nothing: the branch holds the resolved dependencies, and says honestly that the
new Minecraft version is not supported.

## The escalation ladder

An update moves one variable: Minecraft. Java follows, because Mojang dictates
it. The loader and its API stay **frozen**.

The build plugin and the Gradle wrapper only move **when the target Minecraft
version needs it**, see [the toolchain](#the-toolchain) below. On an update where
your mod already builds with enough, neither is touched.

The loader and its API only move as a *reaction* to a red matrix:

```
matrix with the frozen dependencies
  KO -> bump the API        -> whole matrix again
          KO -> bump the loader  -> whole matrix again
                  KO -> the mod is really broken
```

Two things make this worth the wall time.

**One suspect at a time.** If Minecraft, the loader and the API all moved
together, a red matrix has three candidate causes and no way to separate them. A
frozen baseline means the first red is *about Minecraft*, and each rung after it
is about exactly one dependency.

**Each rung replays the whole matrix.** A newer API is exactly the kind of change
that fixes the newest version while breaking an older one of the same series.
Testing only the version that failed would ship that regression.

The API moves before the loader, because the API is a normal library your mod
calls into while the loader is the thing that runs every mod on the server.

What the ladder did ends up in the pull request body and in the failure issue,
one line per dependency, so you can see whether the update needed help.

### The floors

A dependency that an escalation actually moved gets a **floor** written into your
metadata, for example `depends.fabric-api: ">=0.157.0"`, because that version is
now genuinely required.

Only what moved. An API left at `*` keeps its `*`. And only after a green matrix,
since a bump is a hypothesis until the matrix proves it.

## The toolchain

Minecraft needs no Gradle. The build plugin does: fabric-loom 1.18.1 refuses to
load on a Gradle older than 9.7.0, and a build that fails there fails before any
test. Fabric built its own example mod with one pair per Minecraft version, and
mc-bump ships those pairs as a table,
[`lib/loaders/fabric_toolchain.json`](https://github.com/spatulox-minecraft/mc-bump/blob/main/lib/loaders/fabric_toolchain.json),
from `1.14.4` to the latest release. An excerpt:

| Minecraft | fabric-loom | Gradle |
|---|---|---|
| `1.16.5` | `0.5` | `6.8` |
| `1.20.1` | `1.2` | `8.1.1` |
| `1.21.1` | `1.7` | `8.10` |
| `1.21.11` | `1.14` | `9.2.1` |
| `26.1` | `1.15` | `9.3.0` |
| `26.2` | `1.17` | `9.5.1` |

A newer Minecraft never needs an older pair than the one before it, and the
tests check it on every refresh.

An update compares your mod to the entry for the target, and moves only what is
**below** it:

| your mod | target `26.2` needs | written |
|---|---|---|
| Loom `1.17.18`, Gradle `9.5.1` | `1.17` / `9.5.1` | nothing |
| Loom `1.18.1`, Gradle `9.7.0` | `1.17` / `9.5.1` | nothing, never moved down |
| Loom `1.15.4`, Gradle `9.3.0` | `1.17` / `9.5.1` | Loom `1.17.21`, wrapper `9.5.1` |

- The Loom taken is the newest release **of the required line**, not the newest
  overall, and one your `java_version` can run. Its Java and Gradle come from the
  module metadata Fabric publishes with it. A release without readable metadata
  is only taken when no release of the line with known requirements fits.
- The wrapper goes to the highest of the table's Gradle and the one that Loom
  declares in its module metadata. Only `gradle-wrapper.properties` changes: the
  `distributionUrl`, and `distributionSha256Sum` when you pin one. That checksum
  is fetched from Gradle before any file is written, so a failure there leaves
  your mod untouched.
- A release candidate or a snapshot is governed by the release it leads to, and a
  version the table does not list by the closest release below it.
- A version older than the table (before `1.14.4`) moves nothing.

The pull request carries a build plugin row and a Gradle wrapper row, each saying
whether it moved and why.

### Where the table comes from

The table is rebuilt every week from
[fabric-example-mod](https://github.com/FabricMC/fabric-example-mod), which keeps
one branch per Minecraft version. Each version is read at the commit that
**introduced** it: later commits backport newer tooling to every branch at once
and say nothing about what a version needs.

That commit is searched on every branch, not only the version's own. In 2026
Fabric's template generator recreated the old branches with that year's tooling
(commits named `Deployment from commit FabricMC/fabricmc.net@…`). Read from its
own branch alone, `1.16.5` would need Loom `1.15` and Gradle `9.3.0`, and an
update to it would fail on Java 8. So:

- a hand-made commit wins over a generated one: `1.21.1` is read from its 2024
  commit, Loom `1.7` and Gradle `8.10`;
- a version only the generator ever set counts when it is **newer** than every
  hand-made one, which is how Fabric publishes since `26.1` (`26.2`, `26.3`);
- below that it is a backfill and is left out of the table, so the closest
  release below governs it (`1.21.9` follows `1.21.8`).

The refresh runs the tests before it commits, and moves `@v1` and `@v2` only
when they were already on their branch head.

`--loom VERSION` pins the build plugin instead. The wrapper still follows what
that Loom declares.

## When nothing happens

Two outcomes look like failures and are not:

- **the loader does not support the new Minecraft release yet.** Fabric has not
  published a stable loader for it. The weekly run says so in the job summary and
  retries next week.
- **a rung had nothing newer to move to.** That dependency is already on the
  newest release for the current Minecraft version, so the ladder simply moves
  on.

Both are reported plainly rather than as errors. See
[Troubleshooting](Troubleshooting#updates) for the exit codes behind them.
