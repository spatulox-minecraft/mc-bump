# mc-bump

Reusable CI for Minecraft mods: follow a new Minecraft release, prove the mod
still works on **every version it claims**, and publish it — driven by one config
file in the mod's repository.

Built for Fabric. The loader specific parts live behind one interface
(`lib/loaders/base.py`), so NeoForge is one more file next to `fabric.py`.

## What it does

| Pipeline | What it is for |
|---|---|
| `ci` | On every push and pull request: unit tests, then build + boot a server for each claimed Minecraft version, plus the client gametest. |
| `auto-update` | Weekly: detect a new Minecraft release, resolve the dependencies, run the matrix, escalate the frozen dependencies if it fails, open a pull request. |
| `release` | On merge: prove the matrix again, publish to Modrinth and CurseForge in **separate, individually replayable jobs**, then tag. |

Two ideas hold the whole thing together:

**One jar covers one series.** `26.1.2` and `26.1` are the same series, and a jar
that claims the series must be booted on every version of it — not just the
newest. That is what the version matrix does, and it is why the compatibility
range is only widened *after* the matrix proves it.

**An update moves one variable.** Minecraft. The loader and its API stay frozen,
and only move through an escalation ladder as a reaction to a red matrix, one at
a time, each followed by a full matrix re-run. A red matrix therefore has one
suspect instead of three.

## Using it

Add `.github/mc-bump.yml` to your mod, then one workflow per pipeline:

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

For a pipeline of your own, the scripts are also exposed as a composite action:

```yaml
- uses: spatulox-minecraft/mc-bump@v1
  with:
    command: test-matrix.sh
```

## Configuration

`.github/mc-bump.yml`, in the mod's repository. Only `loader`, `mod.id` and
`mod.metadata` are required; everything below shows the defaults.

```yaml
loader: fabric                     # the only implementation, for now

mod:
  id: my-mod                       # the id the loader announces
  package: com.example             # informational
  metadata: src/main/resources/fabric.mod.json
  mixins: src/main/resources/my-mod.mixins.json    # optional

workflows:
  ci: true
  auto-update: true
  release: true
  unit-tests: true
  gametest:
    enabled: false
    blocking: false

version:
  format: "{mc}-{mod}"             # or "{mod}+mc{mc}", "{mod}-{mc}", "{mod}"
  tag: "v{version}"

tests:
  unit:
    source: src/test/java
    task: test
    require-non-empty: true        # an empty src/test is a green proving nothing

  matrix:
    enabled: true
    parallel: true                 # one GitHub job per version

  server:
    boot-timeout: 900
    stop-timeout: 60
    expect: []                     # see below
    expect-count: []
    fatal-extra: []                # added to the loader's own signatures

release:
  stores: [modrinth, curseforge]
  branch-prefix: chore/mc-
  artifact-retention-days: 30

notify:
  assignee: ""                     # who gets the failure issue
  label: ci-failure
  keep-branch: true
  on-pull-request: false
  log-tail: 100                    # log lines per <details> block
```

### Proving the mod actually works

A server that boots proves nothing about the mod: an empty registry and a
callback that never ran both produce a perfectly healthy server. Two lists turn
"it booted" into "it worked".

`expect` — a phrase that must appear in the server log:

```yaml
tests:
  server:
    expect:
      - pattern: "Brewing mixes registered"
        message: "the brewing callback never ran"
```

`expect-count` — the mod reports a number, and the expected value is **derived
from the source** rather than kept as a constant to maintain in two places:

```yaml
    expect-count:
      - pattern: "Registered ([0-9]+) potions"        # exactly one capture group
        count-source: src/main/java/com/example/MyMod.java
        count-pattern: "= registerPotion("            # a literal, not a regex
        message: "potion registry mismatch"
```

`pattern` is an extended regex fed to `sed -nE`, so it needs exactly one capture
group. `count-pattern` is a literal counted with `grep -cF`. Adding a potion
updates both sides on its own.

## Running it locally

Everything the CI runs, runs from the mod's repository with no GitHub involved:

```bash
MCB=../mc-bump

python3 $MCB/scripts/mc-bump.py --list-test-versions   # what the matrix will boot
python3 $MCB/scripts/mc-bump.py 26.2 --dry-run         # what an update would change

bash $MCB/scripts/headless-server-test.sh              # one server
bash $MCB/scripts/test-matrix.sh                       # every claimed version
bash $MCB/scripts/test-with-escalation.sh              # ... plus the ladder

MC_VERSIONS="26.1" bash $MCB/scripts/test-matrix.sh    # restrict the matrix
PYTHONPATH=$MCB python3 -m lib.config --json           # the resolved config
```

Requires `python3` and `pyyaml`.

## Adding a loader

1. Copy `lib/loaders/fabric.py` to `lib/loaders/neoforge.py` and implement
   `Loader` against the NeoForged maven and `neoforge.mods.toml`.
2. Add it to `LOADERS` in `lib/loaders/__init__.py`.
3. Set `loader: neoforge` in a mod's config.

Nothing else changes: the workflows, the shell scripts and the version arithmetic
never name a loader.

## Developing mc-bump

```bash
python3 -m unittest discover -s tests -t .
```

No network in the tests: the resolvers that call Mojang, Fabric and Modrinth are
deliberately untested — they describe the shape of upstream APIs, which no local
assertion can pin down. What is tested is the decision layer, because a mistake
there does not crash. It produces a green matrix, a jar a player's loader
refuses, or a store page announcing a version nobody ever booted.
