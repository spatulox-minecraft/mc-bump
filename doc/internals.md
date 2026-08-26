# Internals

How mc-bump is put together, for people changing it. If you are *using* mc-bump,
the [wiki](https://github.com/spatulox-minecraft/mc-bump/wiki) is the place to
look instead.

Each directory also carries its own README going module by module:
[`lib/`](../lib/README.md), [`scripts/`](../scripts/README.md),
[`tests/`](../tests/README.md).

## The dependency order

```
common.py                        errors + HTTP
  |-- versions.py                series arithmetic, mod_version template
  |-- gradle.py                  gradle.properties + JSON metadata I/O
  |-- patterns.py                globs, comment stripping
  |-- report.py                  markdown bodies
  `-- loaders/                   the Fabric-specific seam
        `-- config.py            .github/mc-bump.yml -> Project
              |-- update.py      what an update writes
              |-- server_test.py boot a server, assert on the log
              `-- matrix.py      every claimed version + the escalation ladder
```

Two rules keep it acyclic:

- **nothing below `config.py` imports a `Project`.** The low-level modules stay
  pure functions over strings and paths.
- **nothing outside `loaders/` names Fabric.**

`scripts/` is argument parsing around `lib/`, and the workflows are argument
parsing around `scripts/`. Nothing in `lib/` reads `argv` or the environment
except through a `Project`.

## The one architectural rule

**Every decision lives in `lib/`.**

That is what makes the CI and a local run go through strictly the same code, and
it is why `check_log()` can be a pure function over a string while the CI still
uses it to boot a real server.

Concretely: if you find yourself writing an `if` in a workflow's `run:` block, it
probably belongs in `lib/`. The failure report used to be around 250 lines of
bash inlined in a workflow, where nothing but GitHub could run it.

## Why `ModPaths` exists

mc-bump runs from **its own** checkout and edits **someone else's** repository.
"The repo" is therefore never `Path(__file__).parent`. `ModPaths` carries every
path explicitly (`gradle.properties`, the loader metadata, the mixin config,
`.mc-update-state.json`) and `require()` fails by name, up front, rather than
halfway through a rewrite.

## Why a 400 is not an error

Fabric meta answers **400**, not 404, for a Minecraft version it does not know,
with a valid JSON body. That means "not published yet" and must be readable,
while a 500 must raise. Without that distinction the weekly update would stop
working while staying green.

`user_agent()` derives its string from `$GITHUB_REPOSITORY`, so one abusive
repository does not get every mc-bump user rate-limited.

## `check_log()` is pure

`server_test.py` is split in two on purpose.

`check_log()` takes the log as a **string** and asserts: was the mod in the
loader's inventory, did the version we asked for actually boot, is there any
fatal signature, is every `expect` phrase present, does every `expect-count`
number equal what `count_in_source()` counts. That is the part
`tests/test_server_test.py` covers without booting anything.

`run()` is the process handling: flat world, watchdog off, world wiped between
runs, `start_new_session` plus `killpg` because Gradle is a launcher and the JVM
is the real server.

The split is the whole reason the shell to Python rewrite was worth doing. None
of those assertions was testable while they were interleaved with process
handling.

## The report artifact contract

Each test job uploads `failure-report-<job>/` holding `meta.json` (`title`,
`kind`, `failed`) and `log.txt`; `collect()` reads them all.

A passing job uploads `"failed": false`, which is how the report tells "green"
from "never ran". A missing artifact cannot express that difference.

`test_report()` renders one table plus one collapsible log per failure, and is
pasted into **both** the issue and the pull request from the same data.
`blocks_from_matrix_status()` folds the sequential matrix, which runs in one job
and produces no per-version artifact, into that same table.

A malformed report is kept under a generic title rather than dropped, because
losing the evidence of a failure is worse than showing it badly.

Log blocks use a **four-backtick** fence, since a Minecraft log can contain a
triple-backtick line and would otherwise spill out of the block.

`failure_issue_title()` is deterministic, so a re-run comments instead of opening
a duplicate.

## The ordering problem in `update.py`

The compatibility range has to be widened **before** the tests, otherwise the
loader refuses to load the mod on the new version and nothing could ever be
validated. So it is raised optimistically, and then:

- `save_update_state()` snapshots the previous claims into
  `.mc-update-state.json` before anything is written;
- matrix green, so `mark_supported()` extends `supported_minecraft_versions` and
  recomputes the range *and* the jar name from that same list, which is what
  keeps the three from drifting apart. `engrave_dependency_floors()` writes a
  floor only for what an escalation actually moved, compared against the
  snapshot.
- matrix red, so `revert_compat()` restores both claims and **keeps the version
  bumps**, which are the dependency diff someone picks up from.

The snapshot is keyed by **role**, not by Fabric's property names, so a loader
that spells them differently needs no change here.

## The escalation ladder in `matrix.py`

`run_matrix()` builds and boots each version in `versions_to_test()`, writing
`test-matrix-status.txt` after every one, so a job killed mid-run still reports
what it got through.

One retry, and only for a specific case: a build-setup deadlock that never
reaches "Starting minecraft server version". A mod that is genuinely broken
*does* get that far, which is how the two are told apart.

`run_with_escalation()` replays the **whole** matrix after each rung, because a
newer API is exactly the kind of change that fixes the newest version while
breaking an older one of the same series. The rungs come from the loader, so a
loader with different frozen dependencies needs no change here.
