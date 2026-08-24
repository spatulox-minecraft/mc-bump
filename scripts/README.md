# `scripts/` — the entry points

Four executables. Each one is argument parsing, environment variables and exit
codes around `lib/` — every decision lives in the library, so the CI and a local
run go through strictly the same code.

All four:

- run **from the mod repository**, not from here. The mod root is found by
  walking up from `$MOD_ROOT` or the cwd looking for `.github/mc-bump.yml`;
  `--root` overrides it.
- prepend the repo root to `sys.path` so `lib` imports without installation.
- catch `Failure` to print `error: …` instead of a traceback, and
  `KeyboardInterrupt` to exit `130`.
- need `python3` and `pyyaml`, nothing else.

```bash
MCB=../mc-bump
python3 $MCB/scripts/mc-bump.py --list-test-versions
```

They are also exposed as a composite action (`action.yml` at the repo root):

```yaml
- uses: spatulox-minecraft/mc-bump@v1
  with: { command: test-matrix.py }
```

## Files

### `mc-bump.py` — resolve an update and apply it
The main entry point. Resolves what upstream publishes for a target Minecraft
version (default: the latest Mojang release), writes `gradle.properties` and the
loader metadata, and — with `--run-tests` — proves it.

Wraps `lib.update` and `lib.matrix`. Every mode writes `$GITHUB_OUTPUT` when it
is set, and `--json` prints the same dict on stdout.

| Mode | What it does |
|---|---|
| *(default)* | Update to the target version. Loader and API stay frozen; the compatibility range is widened optimistically, and `.mc-update-state.json` snapshots what to restore. |
| `--run-tests` | The above, then the full matrix with its escalation ladder, then `--mark-supported` on green / `--revert-compat` on red. |
| `--mark-supported` | Add the current version to `supported_minecraft_versions`, recompute the range and the jar name. **Only after a green matrix** — this list is what the stores announce. |
| `--revert-compat` | Restore the claims from the snapshot, keeping the version bumps. |
| `--bump-api` / `--bump-loader` | One rung of the ladder, driven by `test-with-escalation.py`. |
| `--list-test-versions` | Print the versions the matrix must boot, one per line. |

Other flags: `--dry-run` (show, write nothing), `--force` (reapply the version
already in the repo), `--buildtool`/`--loom VERSION` (pin the build plugin
instead of resolving the latest stable). The five action modes are mutually
exclusive, and `--run-tests` refuses `--dry-run`/`--json`.

Exit codes: `0` success or already up to date · `1` error or failed tests · `2`
the loader does not support that Minecraft version yet · `3` `--bump-*` had
nothing newer to move to.

`run_tests()` calls `run_with_escalation()` **in process** rather than shelling
out to `test-with-escalation.py`: when the ladder was bash, an exit code was all
a caller could learn from it.

### `headless-server-test.py` — one server
Boots a dedicated server with the mod on the version currently in
`gradle.properties`, and runs `lib.server_test.check_log()` against the log.
Thin: build a `ServerTest` from the flags, call `run()`.

Flags: `--log` `--minecraft` `--run-dir` `--level-name` `--root`.

The environment variables the shell version took are still honoured, so the CI
steps and any habit built around them keep working:

| Variable | Meaning | Default |
|---|---|---|
| `RUN_DIR` | run directory | `run` |
| `LOG` | log file produced | `server-test.log` |
| `BOOT_TIMEOUT` | seconds before giving up on startup | `tests.server.boot-timeout` |
| `STOP_TIMEOUT` | seconds before force-killing the JVM | `tests.server.stop-timeout` |
| `EXPECTED_MC` | version that must actually boot | `gradle.properties` |
| `GRADLE_ARGS` | extra gradle arguments (split with `shlex`) | none |
| `LEVEL_NAME` | world name, wiped before each run | `ci-smoke-test` |
| `MOD_ROOT` | the mod repository | walked up from the cwd |

### `test-matrix.py` — every claimed version
Builds and boots a server for each version in `--list-test-versions`. Exits `1`
if any of them fails, and records the outcome one `<version> <ok\|build\|server>`
per line.

Flags: `--minecraft V…` (restrict the run), `--status-file`, `--root`.
Environment: `MC_VERSIONS` (space separated), `STATUS_FILE` (default
`test-matrix-status.txt`), plus everything `headless-server-test.py` accepts.

In CI this sequential loop is usually replaced by a GitHub job matrix running
the same versions in parallel; it stays the local entry point, and the
sequential path the ladder needs.

### `test-with-escalation.py` — the matrix plus the ladder
`test-matrix.py`, and on failure bump the frozen dependencies one at a time —
API first, then loader — replaying the **whole** matrix after each bump. Exits
`1` when it is still red after every rung.

Flags: `--status-file`, `--escalation-file`, `--root`. Environment:
`ESCALATION_FILE` (default `test-escalation.txt`), plus everything
`test-matrix.py` and `headless-server-test.py` accept.

The bumps are **kept** on failure: they are the dependency diff someone picks up
from. The mod metadata is not touched — a bump is a hypothesis, and the floor is
only engraved by `mc-bump.py --mark-supported` once the matrix has proven it.

## Files they leave in the mod repository

| File | Written by | Read by |
|---|---|---|
| `test-matrix-status.txt` | `test-matrix.py` | the report, for the test table |
| `test-escalation.txt` | `test-with-escalation.py` | the report, for the escalation table |
| `build-<version>.log`, `server-test-<version>.log` | the matrix | the collapsible logs |
| `.mc-update-state.json` | `mc-bump.py` | `--revert-compat`, `--mark-supported` |
