# `lib/` — the library

Everything mc-bump knows how to do, as importable modules. The files in
`scripts/` are argument parsing around these; the GitHub workflows are argument
parsing around `scripts/`. Nothing here reads `argv` or the environment except
through a `Project`.

Standard library only, except PyYAML in `config.py`.

## The dependency order

```
common.py                        errors + HTTP
  ├── versions.py                series arithmetic, mod_version template
  ├── gradle.py                  gradle.properties + JSON metadata I/O
  ├── patterns.py                globs, comment stripping
  ├── report.py                  markdown bodies
  └── loaders/                   the Fabric-specific seam
        └── config.py            .github/mc-bump.yml -> Project
              ├── update.py      what an update writes
              ├── server_test.py boot a server, assert on the log
              └── matrix.py      every claimed version + the escalation ladder
```

Two rules keep it that way: nothing below `config.py` imports a `Project`, and
nothing outside `loaders/` names Fabric.

## Files

### `__init__.py`
Package docstring. Nothing is re-exported — every caller imports the module it
actually needs.

### `common.py` — errors and HTTP
`Failure`, the exception every entry point catches to print an error without a
traceback, and the two HTTP helpers (`http_get`, `get_json`) the resolvers use.

`allow_status` is the notable part: a 400 from Fabric meta means "that Minecraft
version does not exist yet" and must be readable, while a 500 must raise —
without that distinction the weekly update would stop working while staying
green. `user_agent()` derives its string from `$GITHUB_REPOSITORY` so one
abusive repository does not get every mc-bump user rate-limited.

### `versions.py` — series arithmetic
Loader agnostic, no I/O beyond the Mojang manifest. Holds the model the whole
repo is built on: **one jar covers one series** (`26.1.2` → series `26.1`).

- `mojang_manifest()`, `latest_minecraft_release()`, `java_version_for()` — the
  only network calls here, manifest cached per run.
- `series_of()`, `parse_version()` — numeric ordering, so `26.1.10` sorts after
  `26.1.9`; returns `None` for snapshots.
- `versions_to_test()` — every version the jar will *claim*, which is the list
  the matrix must boot, not just the target.
- `compat_bounds()` — `(low, high)` for the range. Only the *rendering* of those
  bounds is loader-specific and lives in `loaders/`.
- `mc_label()` — names a set of versions for the `{mc}` half of `mod_version`
  (`26.1.x`). Deliberately never climbs a level: `26.x` would promise a series
  nothing ever booted.
- `render_mod_version()`, `parse_mod_version()`, `update_mod_version()` — the
  `{mc}`/`{mod}` template. `parse_mod_version()` raises rather than warns: a
  version number mc-bump cannot read is one it must not rewrite.

### `gradle.py` — file I/O over the mod repository
`ModPaths` carries every path (`gradle.properties`, the loader metadata, the
mixin config, `.mc-update-state.json`) because mc-bump runs from *its own*
checkout and edits *someone else's* repository — "the repo" is never
`Path(__file__).parent`. `require()` fails by name, up front.

Plus `read_property` / `set_property` / `split_supported` for
`gradle.properties`, `read_json` / `write_json` for the metadata (tab indented,
the Fabric convention), and `write_preserving_final_newline` so a rewrite never
shows up as a whitespace diff.

### `patterns.py` — globs, and source counting
What a mod owner writes in `mc-bump.yml` is a phrase they expect in a log, not a
regex, so `pattern:` is a **glob**. The translation itself is
`fnmatch.translate()`; this module only adds the three things a log needs:

- **unanchored** (wrapping the pattern in `*` before translating, so the
  timestamp and logger prefix in front do not defeat it),
- **line by line** (`translate()` emits `(?s:…)`, so `*` would otherwise cross
  newlines and match across two unrelated lines),
- **`<count>`**, the one syntax addition, since `expect-count` has to pull a
  number out and glob has no capture groups.

`compile_pattern()` builds a `Matcher` from whichever of `pattern:`/`regex:` the
config used and rejects both-at-once. `strip_comments()` blanks out `//`,
`/* */` or `#` comments **string-aware** (a `http://` inside a literal survives)
while keeping every newline, so line counting still works — a registration named
in a Javadoc is a mention, not a registration.

### `loaders/` — the loader seam
See `loaders/base.py`. Everything that knows what "Fabric" *is* lives here.

- `base.py` — the `Loader` ABC, plus `Resolved` (what upstream offers for a
  Minecraft version) and `Rung` (one step of the escalation ladder). Three kinds
  of knowledge: resolution, metadata, runtime. What is deliberately *not* here:
  version arithmetic, which is the same problem on every loader.
- `fabric.py` — the only implementation. Fabric meta, the Modrinth fabric-api
  listing, the fabric-loom maven metadata, `fabric.mod.json` reads and writes,
  the `>=26.1 <=26.1.2` range syntax, the fatal log signatures, and
  `mod_loaded_pattern()` — which anchors on the loader's indented inventory line
  rather than grepping the bare mod id, because that id also appears in every
  classpath dump.
- `__init__.py` — the `LOADERS` registry and `get_loader()`. Adding NeoForge is
  one more file plus one line here.

### `config.py` — `.github/mc-bump.yml` → `Project`
The only thing that parses the config, so a value can never mean two different
things depending on which part of the pipeline reads it.

`SCHEMA` is the whole config as `Field` / `Record` objects; `_validate()` walks
it, applies defaults and **rejects unknown keys by name**. Strictness is the
point: a config that is subtly wrong does not crash, it produces a green
pipeline testing the wrong thing.

`find_root()` walks up from `$MOD_ROOT`/cwd looking for the config file.
`load()` returns a frozen `Project` (root, validated `raw`, `Loader`,
`ModPaths`). `export_json()` and `export_github_output()` are what the workflows
read. Runnable: `python3 -m lib.config --json`.

### `update.py` — what an update writes
The decision layer, generic over loaders. **An update moves one variable:**
Minecraft. The build plugin follows (it is a gradle plugin, not something that
ships in the jar) and Java follows (Mojang dictates it). The loader and its API
stay **frozen** and only move through the ladder in `matrix.py`.

The ordering problem this file solves: the compatibility range has to be widened
*before* the tests, or the loader refuses to load the mod on the new version and
nothing could ever be validated. So it is raised optimistically, and then:

- `save_update_state()` snapshots the previous claims into
  `.mc-update-state.json` before anything is written,
- matrix green → `mark_supported()` extends `supported_minecraft_versions` and
  recomputes the range *and* the jar name from that same list, so the three can
  never drift apart. `engrave_dependency_floors()` writes a floor only for what
  an escalation actually moved (compared against the snapshot).
- matrix red → `revert_compat()` restores both claims and **keeps the version
  bumps**, which are the dependency diff someone picks up from.

`bump_dependency()` is one rung: resolve the newest release for the Minecraft
version *currently in gradle.properties*, write it, leave the metadata alone.
`"already-latest"` is not a failure, it means the rung cannot change anything.

### `server_test.py` — boot a server, then prove the mod worked
Split in two on purpose:

- `check_log()` — **pure**, takes the log as a string. Was the mod in the
  loader's inventory? Did the version we asked for actually boot (a stale build
  cache would otherwise turn the test green on the wrong version)? Any fatal
  signature? Every `expect` phrase present? Every `expect-count` number equal to
  what `count_in_source()` counts in the source? This is the part
  `tests/test_server_test.py` covers without booting anything.
- `run()` — the process handling. Flat world, watchdog off, world wiped between
  runs (a save written by a newer Minecraft refuses to load on an older one,
  which the matrix would hit immediately). `start_new_session` + `killpg`,
  because gradle is a launcher and the JVM is the real server.

A server that boots proves nothing: an empty registry and a callback that never
ran both produce a perfectly healthy server. The two expectation lists are what
turn "it booted" into "it worked".

### `matrix.py` — every claimed version, then the ladder
`run_matrix()` builds and boots each version in `versions_to_test()`, writing
`test-matrix-status.txt` after every one, so a job killed mid-run still reports
what it got through. One retry, and only for a specific case: a build-setup
deadlock that never reaches "Starting minecraft server version" — a mod that is
genuinely broken *does* get that far, which is how the two are told apart.

`run_with_escalation()` is the ladder:

```
matrix with the frozen dependencies
  KO -> bump the API      -> whole matrix again
          KO -> bump loader -> whole matrix again
                  KO -> the mod is really broken
```

Each rung replays the **whole** matrix, because a newer API is exactly the kind
of change that fixes the newest version while breaking an older one of the same
series. The rungs come from the loader, so a loader with different frozen
dependencies needs no change here. Writes `test-escalation.txt`.

### `report.py` — the markdown people actually read
Was ~250 lines of bash inlined in a workflow, where nothing but GitHub could run
it. Now functions over plain data, checked by tests instead of by opening a pull
request.

The artifact contract: each test job uploads `failure-report-<job>/` holding
`meta.json` (`title`, `kind`, `failed`) and `log.txt`; `collect()` reads them
all. A passing job uploads `"failed": false`, which is how the report tells
"green" from "never ran". A malformed report is kept under a generic title
rather than dropped — losing the evidence of a failure is worse.

`test_report()` renders one table plus one collapsible log per failure, and is
pasted into **both** the issue and the pull request from the same data.
`blocks_from_matrix_status()` folds the sequential matrix (which runs in one job
and produces no per-version artifact) into that same table.
`failure_issue_title()` is deterministic, so a re-run comments instead of
opening a duplicate. Runnable: `python3 -m lib.report --failure-issue`.
