# Pipelines

Three reusable workflows. Each is called from a one-job file in your mod's
repository (see [Getting Started](Getting-Started#3-the-workflows)) and reads
every decision from `.github/mc-bump.yml`.

All three start the same way: a `config` job reads the config and
`gradle.properties` and exposes them as job outputs. It runs first and costs no
JDK, because everything downstream branches on it and reading a config must not
cost a toolchain.

| | [`ci`](#ci) | [`auto-update`](#auto-update) | [`release`](#release) |
|---|---|---|---|
| Trigger | push, pull request | schedule, manual | push to the default branch |
| Matrix | parallel, one job per version | sequential, with the ladder | parallel, one job per version |
| Writes to your repo | no | branch and PR | tags |
| Needs secrets | no | no | store tokens |

---

## `ci`

`spatulox-minecraft/mc-bump/.github/workflows/ci.yml@v1`

Three independent kinds of test, three jobs, so a red one names itself.

```
config ──┬── unit-tests      pure JVM logic, seconds
         ├── matrix          build and boot a server, ONE JOB PER CLAIMED VERSION
         ├── gametest        a real client under xvfb, usually non blocking
         └── report-failure / verdict
```

### Inputs

| Input | Default | |
|---|---|---|
| `mc-bump-ref` | `v1` | Which mc-bump to run. Only worth changing to test mc-bump itself from a branch. |

### `unit-tests`

Runs `tests.unit.task`, `test` by default. When `tests.unit.require-non-empty` is
on, it first refuses an empty `tests.unit.source`: `gradlew test` with no test
source set passes with zero tests, which is a green proving nothing.

The JUnit HTML report and the raw results are uploaded as `unit-test-reports`.

### `matrix`

**The point of this workflow.** One job per version in
`supported_minecraft_versions` plus the current one, all in parallel:

```
./gradlew build -Pminecraft_version=<version>
python3 headless-server-test.py     # boot, then check the log
```

`fail-fast: false`, because a version failing must not hide the state of the
others. Knowing whether 26.1 broke *too* is what tells a dependency bump apart
from a code bug.

Your compatibility range promises a whole Minecraft series, so testing only the
newest version proves nothing about the others, which are exactly the versions
the stores announce.

Each job uploads `server-logs-<version>`: the build log, the server log, and
`run/logs/`.

### `gametest`

A real Minecraft client under `xvfb`. Off by default
(`workflows.gametest.enabled`), and usually declared non-blocking.

Non-blocking means the job ends green and does **not** open a failure issue. It
still reports into the same table, because an unrun test rots silently.

### `report-failure`

Collects every failure report of the run and opens **one** issue with a table
plus one collapsible log per failure. The title is deterministic, so a re-run
comments on the existing issue instead of opening a duplicate.

Skipped on pull requests unless `notify.on-pull-request` is true, since the PR
already shows its own red checks.

### `verdict`

The Actions tab must not say the opposite of the truth. `verdict` fails when a
required job did not pass, and treats `skipped` as legitimate, since that means
you disabled that pipeline.

A non-blocking gametest failure appears here as a warning, not a failure.

### Permissions

```yaml
permissions:
  contents: read
  issues: write        # the failure report
```

---

## `auto-update`

`spatulox-minecraft/mc-bump/.github/workflows/auto-update.yml@v1`

Detects a new Minecraft release, resolves the dependencies, proves your mod on
**every version the new range claims**, and opens a pull request.

One job, sequential by necessity, because the
[escalation ladder](Versions-and-compatibility#the-escalation-ladder) replays the
whole matrix after each dependency bump.

### Inputs

| Input | Type | Default | |
|---|---|---|---|
| `mc-bump-ref` | string | `v1` | |
| `minecraft-version` | string | `""` | Force a version. Empty means the latest Mojang release. |
| `force` | boolean | `false` | Continue even if the repo is already on that version. |

### The sequence

```
1. resolve       latest Mojang release, then the loader, API and build plugin for it
2. commit        the bump and the optimistically widened compatibility range
3. unit tests    seconds, and a broken unit test explains a broken matrix
4. matrix        every claimed version, WITH the escalation ladder
5. gametest      if enabled, non blocking
6a. green -> extend supported_minecraft_versions, recompute the range and the
             jar name from that same list
6b. red   -> restore the claims, KEEP the version bumps
7. push          force-push the branch, open or update the pull request
8. red only -> open a failure issue, then fail the run
```

Why the range is widened before the tests, and why the bumps survive a failure:
[Versions and compatibility](Versions-and-compatibility#why-the-range-is-widened-before-the-tests).

### What the pull request looks like

- **green**: a normal pull request. The commit message records the escalation, if
  there was one.
- **red**: a **draft** pull request, plus a failure issue linking to it. The
  branch says your mod does *not* support the new version, and the bumps are
  there as a starting point.

The branch is `release.branch-prefix` plus the Minecraft version, force-pushed,
and never deleted.

### Permissions

```yaml
permissions:
  contents: write
  pull-requests: write
  issues: write        # the failure issue, and creating its label
```

Plus **Settings → Actions → Allow GitHub Actions to create and approve pull
requests**.

### Concurrency

`group: mc-bump-update`, `cancel-in-progress: false`. Two updates must not race
over the same branch, and cancelling one mid-matrix leaves a half-written repo.

---

## `release`

`spatulox-minecraft/mc-bump/.github/workflows/release.yml@v1`

Publishes to each configured store, then tags.

```
config ── check ── matrix ──┬── publish-modrinth ──┬── tag ── publish-github
                            └── publish-curseforge ┘
```

### Inputs and secrets

| Input | Type | Default | |
|---|---|---|---|
| `mc-bump-ref` | string | `v1` | |
| `dry-run` | boolean | `false` | Everything except the uploads and the tag. |

| Secret | Required |
|---|---|
| `MODRINTH_TOKEN` | when `modrinth` is in `release.stores` |
| `CURSEFORGE_TOKEN` | when `curseforge` is in `release.stores` |
| none | `github` publishes with `github.token`, which `contents: write` already covers |

`secrets: inherit` in your caller is what passes them through.

### `check`, refuses early and writes nothing

Three guards, all cheap, before the expensive matrix starts:

1. **already released?** The release tag exists, so there is nothing to do. The
   whole condition is idempotent, which is what makes re-running always safe.
2. **is the version actually proven?** `minecraft_version` must appear in
   `supported_minecraft_versions`. Merging a *draft* pull request, one where the
   matrix failed, leaves the version bumped while the revert restored the shorter
   supported list. Publishing then would ship a jar built against the new version
   while announcing the old one.
3. **are the tokens there?** A missing or expired token is a two second check
   here, or a ninety minute one after the matrix.

### `matrix`

The same per-version build and boot as CI, run again. Not redundant: it proves
every version listed in `supported_minecraft_versions`, which is exactly what the
stores are about to announce.

### One job per store

Not cosmetic. With a single publish task, Modrinth succeeding and CurseForge
failing left the run with no tag, and every re-run hit *"version already exists"*
on Modrinth. The pipeline could never finish again without a human.

Each store job first checks a **marker tag**, `published/<store>/<tag>`:

- present, so skip, that store is already done;
- absent, so upload, then push the marker.

The marker is pushed **after** the upload, for the same reason the release tag
is: it means *"this is out there"*.

### `tag`

Only once every enabled store is done. A skipped store counts as done, it simply
had nothing left to do. The result is an annotated tag carrying the Minecraft
version, the supported list, the range and the Java version.

The tag means *"this version is RELEASED"*, which is why it is pushed **after** a
successful upload. That ordering is what makes a failure recoverable: no tag is
left behind, so re-running simply tries again.

### `publish-github`, after the tag

A GitHub release hangs off a tag, so this one cannot run in the store row: it
comes **after** `tag`. That would normally make it unreplayable, since `check`
refuses everything once the release tag is out — so it is the one job that does
not gate on that verdict. It runs whenever the guards passed, a re-run of an
already released version included, and asks `gh release view` whether there is
anything left to do.

`gh release view` replaces the marker tag here. The markers exist because
CurseForge's listing API needs a key the upload token is not; the GitHub API is
free, authenticated by `github.token`, and authoritative.

The practical consequence: delete the release, re-run the workflow, and it comes
back — jars, compatibility table and generated notes included. No tag is ever
created ahead of a successful upload, so the invariant above still holds.

### Concurrency

`group: mc-bump-release`, `cancel-in-progress: false`. An upload must never be
cut in flight.

---

## The failure report

Shared by all three pipelines, and the same data renders into both the issue and
the pull request body.

The issue holds one table of every test that ran, plus one collapsible block per
failure with the last `notify.log-tail` lines. Failures are listed first, then by
severity: a build failure explains a server failure, so it comes first.

A passing job reports too, which is how the table tells "green" apart from "never
ran".
