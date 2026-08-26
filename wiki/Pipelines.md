# Pipelines

Three reusable workflows. Each is called from a one-job file in the mod's
repository ([Getting Started](Getting-Started#3-the-workflows)) and reads every
decision from `.github/mc-bump.yml`.

All three start the same way: a `config` step reads the config and
`gradle.properties` and exposes them as job outputs. It runs first and costs no
JDK — everything downstream branches on it, and reading a config must not cost a
toolchain.

| | [`ci`](#ci) | [`auto-update`](#auto-update) | [`release`](#release) |
|---|---|---|---|
| Trigger | push, pull request | schedule, manual | push to the default branch |
| Matrix | parallel, one job per version | sequential, with the ladder | parallel, one job per version |
| Writes to the repo | no | branch + PR | tags |
| Needs secrets | no | no | store tokens |

---

## `ci`

`spatulox-minecraft/mc-bump/.github/workflows/ci.yml@v1`

Three independent kinds of test, three jobs, so a red one names itself.

```
config ──┬── unit-tests      pure JVM logic, seconds
         ├── matrix          build + boot a server, ONE JOB PER CLAIMED VERSION
         ├── gametest        a real client under xvfb, usually non blocking
         └── report-failure / verdict
```

### Inputs

| Input | Default | |
|---|---|---|
| `mc-bump-ref` | `v1` | Which mc-bump to run. Only worth changing to test mc-bump itself from a branch. |

Why an input rather than just `@branch` on the `uses:` — a nested `uses:` cannot
take an expression, so the ref is passed to `actions/checkout` instead.

### `unit-tests`

Runs `tests.unit.task` (default `test`). When `tests.unit.require-non-empty` is
on, it first refuses an empty `tests.unit.source`: `gradlew test` with no test
source set passes with zero tests, which is a green proving nothing.

The JUnit HTML report and the raw results are uploaded as `unit-test-reports`.

### `matrix`

**The point of this workflow.** One job per version in
`supported_minecraft_versions` + the current one, all in parallel:

```
./gradlew build -Pminecraft_version=<version>
python3 headless-server-test.py     # boot, then check_log()
```

`fail-fast: false` — a version failing must not hide the state of the others.
Knowing whether 26.1 broke *too* is what tells a dependency bump apart from a
code bug.

The compatibility range promises a whole Minecraft series, so testing only the
newest version proves nothing about the others — which are exactly the versions
the stores announce.

Each job uploads `server-logs-<version>`: the build log, the server log, and
`run/logs/`.

### `gametest`

A real Minecraft client under `xvfb`. Off by default
(`workflows.gametest.enabled`), and usually declared non-blocking.

Non-blocking means the job ends green and does **not** open a failure issue. It
still reports into the same table — an unrun test rots silently.

### `report-failure`

Collects every `failure-report-*` artifact of the run and opens **one** issue
with a table plus one collapsible log per failure. The title is deterministic, so
a re-run comments on the existing issue instead of opening a duplicate.

Skipped on pull requests unless `notify.on-pull-request` is true — the PR already
shows its own red checks.

### `verdict`

The Actions tab must not say the opposite of the truth. `verdict` fails when a
required job did not pass, and treats `skipped` as legitimate — the mod disabled
that pipeline.

A non-blocking gametest failure appears here as a `::warning::`, not a failure.

### Permissions

```yaml
permissions:
  contents: read
  issues: write        # the failure report
```

---

## `auto-update`

`spatulox-minecraft/mc-bump/.github/workflows/auto-update.yml@v1`

Detects a new Minecraft release, resolves the dependencies, proves the mod on
**every version the new range claims**, and opens a pull request.

One job, sequential by necessity: the escalation ladder replays the whole matrix
after each dependency bump.

### Inputs

| Input | Type | Default | |
|---|---|---|---|
| `mc-bump-ref` | string | `v1` | |
| `minecraft-version` | string | `""` | Force a version. Empty = latest Mojang release. |
| `force` | boolean | `false` | Continue even if the repo is already on that version. |

### The sequence

```
1. resolve       latest Mojang release, then the loader + API + build plugin for it
2. commit        the bump and the OPTIMISTICALLY widened compatibility range
3. unit tests    seconds, and a broken unit test explains a broken matrix
4. matrix        every claimed version, WITH the escalation ladder
5. gametest      if enabled, non blocking
6a. green -> mark-supported   extend supported_minecraft_versions, recompute the
                              range and the jar name from that same list
6b. red   -> revert-compat    restore the claims, KEEP the version bumps
7. push          force-push the branch, open or update the pull request
8. red only -> open a failure issue, then fail the run
```

### Why the range is widened before the tests

It has to be. The loader refuses to load the mod on a Minecraft version outside
its declared range, so with the old range the new version's server would never
start and nothing could ever be validated.

So the bump is committed optimistically, and `.mc-update-state.json` snapshots
what to restore. On red, only the **claims** are reverted; the version bumps —
including whatever the ladder tried — are kept, because they are the dependency
diff a human picks up from.

### What the pull request looks like

- **green** → a normal pull request. The commit message records the escalation,
  if there was one.
- **red** → a **draft** pull request, plus a failure issue linking to it. The
  branch says the mod does *not* support the new version; the bumps are there as
  a starting point.

The branch is `release.branch-prefix` + the Minecraft version, force-pushed, and
never deleted.

### Permissions

```yaml
permissions:
  contents: write
  pull-requests: write
  issues: write        # the failure issue AND creating its label
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
config ── check ── matrix ──┬── publish-modrinth ──┬── tag
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

`secrets: inherit` in your caller is what passes them through.

### `check` — refuses early, writes nothing

Three guards, all cheap, before the expensive matrix starts:

1. **already released?** — the release tag exists → nothing to do. The whole
   condition is idempotent, so re-running is always safe.
2. **is the version actually proven?** — `minecraft_version` must appear in
   `supported_minecraft_versions`. Merging a *draft* pull request (the matrix
   failed) leaves the version bumped while the revert restored the shorter
   supported list; publishing then would ship a jar built against the new version
   while announcing the old one.
3. **are the tokens there?** — a missing or expired token is a two second check
   here, or a ninety minute one after the matrix.

### `matrix`

The same per-version build + boot as CI, run again. Not redundant: it proves
every version listed in `supported_minecraft_versions`, which is exactly what the
stores are about to announce.

### One job per store

Not cosmetic. With a single publish task, Modrinth succeeding and CurseForge
failing left the run with no tag, and every re-run hit *"version already exists"*
on Modrinth — the pipeline could never finish again without a human.

Each store job first checks a **marker tag**, `published/<store>/<tag>`:

- present → skip, that store is already done;
- absent → upload, then push the marker.

A marker tag rather than an API call, because the same mechanism has to work for
both stores and CurseForge's listing API needs a separate "core" key the upload
token is not. `git ls-remote` costs no secret at all.

The marker is pushed **after** the upload, for the same reason the release tag
is: it means *"this is out there"*.

### `tag`

Only once every enabled store is done — `skipped` counts as done, that store
simply had nothing left to do. An annotated tag carrying the Minecraft version,
the supported list, the range and the Java version.

The tag means *"this version is RELEASED"*, which is why it is pushed **after** a
successful upload. That ordering is what makes a failure recoverable: no tag is
left behind, so re-running simply tries again.

### Concurrency

`group: mc-bump-release`, `cancel-in-progress: false`. An upload must never be
cut in flight.

---

## The failure report

Shared by all three pipelines, and the same data renders into both the issue and
the pull request body.

Each test job uploads `failure-report-<job>/` holding `meta.json` (`title`,
`kind`, `failed`) and `log.txt`. A **passing** job uploads `"failed": false` —
that is how the report tells "green" from "never ran".

The issue holds one table of every test that ran, plus one collapsible block per
failure with the last `notify.log-tail` lines. Failures are listed first, then by
severity: a build failure explains a server failure, so it comes first.

A malformed report is kept under a generic title rather than dropped. Losing the
evidence of a failure is worse than showing it badly.
