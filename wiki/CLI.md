# CLI

Everything the CI runs, runs from your mod's repository with **no GitHub
involved**. Same code, same decisions, so a green run locally means a green run
in CI.

```bash
git clone https://github.com/spatulox-minecraft/mc-bump.git ../mc-bump
python3 -m pip install pyyaml
```

Requires `python3` and `pyyaml`, and nothing else. No `bash`, no `jq`, no
`shellcheck`.

```bash
MCB=../mc-bump

python3 $MCB/scripts/mc-bump.py --list-test-versions   # what the matrix will boot
python3 $MCB/scripts/mc-bump.py 26.2 --dry-run         # what an update would change

python3 $MCB/scripts/headless-server-test.py           # one server
python3 $MCB/scripts/test-matrix.py                    # every claimed version
python3 $MCB/scripts/test-with-escalation.py           # ... plus the ladder

PYTHONPATH=$MCB python3 -m lib.config --json           # the resolved config
```

## Common to all four scripts

- They run **from your mod's repository**, not from mc-bump's. The root is found
  by walking up from `$MOD_ROOT` or the cwd looking for `.github/mc-bump.yml`,
  and `--root` overrides it.
- An error prints `error: ...` rather than a traceback.
- `Ctrl-C` exits `130`.

---

## `mc-bump.py`, resolve an update and apply it

Resolves what upstream publishes for a target Minecraft version, the latest
Mojang release by default, writes `gradle.properties` and `fabric.mod.json`, and
with `--run-tests` proves it.

```bash
python3 $MCB/scripts/mc-bump.py [VERSION] [flags]
```

### Modes

Mutually exclusive. Without one, it updates and stops.

| Mode | What it does |
|---|---|
| *(default)* | Update to the target version. Loader and API stay frozen, the compatibility range is widened optimistically, and `.mc-update-state.json` snapshots what to restore. |
| `--run-tests` | The above, then the full matrix with its escalation ladder, then mark the version supported on green, or revert the claims on red. |
| `--mark-supported` | Add the current version to `supported_minecraft_versions`, recompute the range and the jar name. **Only after a green matrix**, since this list is what the stores announce. |
| `--revert-compat` | Restore the claims from the snapshot, keeping the version bumps. |
| `--bump-api` | One rung of the ladder: move the frozen API to the newest release for the current `minecraft_version`. |
| `--bump-loader` | Same, for the loader. |

### Flags

| Flag | |
|---|---|
| `VERSION` | Target Minecraft version. Defaults to the latest Mojang release. |
| `--dry-run` | Show the changes, write nothing. Refused with `--run-tests`. |
| `--json` | JSON on stdout, nothing else. Refused with `--run-tests`. |
| `--force` | Reapply the version already in the repo. |
| `--buildtool VERSION` or `--loom VERSION` | Pin the build plugin instead of resolving the latest stable one. An old Minecraft version may need an older fabric-loom. |
| `--root PATH` | Your mod's repository. |

### Exit codes

| | |
|---|---|
| `0` | success, or already up to date |
| `1` | error, or failed tests |
| `2` | the loader does not support that Minecraft version yet |
| `3` | `--bump-*` had nothing newer to move to |

`2` is a **normal** outcome, not a failure: Fabric had not published a loader for
that Minecraft release yet. The weekly workflow says so in the job summary and
retries next week. `3` likewise means that rung cannot change anything, not that
something broke.

### Examples

```bash
# what would change, without touching anything
python3 $MCB/scripts/mc-bump.py --dry-run

# a specific version, machine readable
python3 $MCB/scripts/mc-bump.py 26.2 --json

# an old Minecraft version that needs an older loom
python3 $MCB/scripts/mc-bump.py 1.20.1 --loom 1.4.10

# the whole auto-update pipeline, locally
python3 $MCB/scripts/mc-bump.py --run-tests
```

---

## `headless-server-test.py`, one server

Boots a dedicated server with your mod on the version currently in
`gradle.properties`, and checks the log. This is the fastest way to iterate on
your [`expect` patterns](Proving-the-mod-works).

```bash
python3 $MCB/scripts/headless-server-test.py
```

Flags: `--log`, `--minecraft`, `--run-dir`, `--level-name`, `--root`.

The world is **wiped between runs**, because a save written by a newer Minecraft
refuses to load on an older one, which the matrix would hit immediately.

### Environment

| Variable | Meaning | Default |
|---|---|---|
| `RUN_DIR` | run directory | `run` |
| `LOG` | log file produced | `server-test.log` |
| `BOOT_TIMEOUT` | seconds before giving up on startup | `tests.server.boot-timeout` |
| `STOP_TIMEOUT` | seconds before force-killing the JVM | `tests.server.stop-timeout` |
| `EXPECTED_MC` | version that must actually boot | `gradle.properties` |
| `GRADLE_ARGS` | extra gradle arguments, split with `shlex` | none |
| `LEVEL_NAME` | world name, wiped before each run | `ci-smoke-test` |
| `MOD_ROOT` | your mod's repository | walked up from the cwd |

---

## `test-matrix.py`, every claimed version

Builds and boots a server for each version `--list-test-versions` prints. Exits
`1` if any of them fails, and records the outcome as `<version> <ok|build|server>`,
one per line.

```bash
python3 $MCB/scripts/test-matrix.py
python3 $MCB/scripts/test-matrix.py --minecraft 26.1        # restrict the run
```

Flags: `--minecraft V...`, `--status-file`, `--root`.
Environment: `MC_VERSIONS` (space separated), `STATUS_FILE` (default
`test-matrix-status.txt`), plus everything `headless-server-test.py` accepts.

The status file is written **after every version**, so a run killed halfway still
reports what it got through.

There is one retry, and only for a specific case: a build-setup deadlock that
never reaches `Starting minecraft server version`. A mod that is genuinely broken
*does* get that far, which is how the two are told apart, so a real failure is
never retried.

In CI this sequential loop is usually replaced by a GitHub job matrix running the
same versions in parallel. It stays the local entry point.

---

## `test-with-escalation.py`, the matrix plus the ladder

`test-matrix.py`, and on failure bump the frozen dependencies one at a time, API
first and then loader, replaying the **whole** matrix after each bump. Exits `1`
when it is still red after every rung.

```bash
python3 $MCB/scripts/test-with-escalation.py
```

Flags: `--status-file`, `--escalation-file`, `--root`.
Environment: `ESCALATION_FILE` (default `test-escalation.txt`), plus everything
the two scripts above accept.

The bumps are **kept** on failure, since they are the dependency diff you pick up
from. Your `fabric.mod.json` is not touched: a bump is a hypothesis, and the
floor is only written by `mc-bump.py --mark-supported` once the matrix has proven
it.

---

## `python3 -m lib.config`, the resolved config

```bash
PYTHONPATH=$MCB python3 -m lib.config --json            # everything, resolved
PYTHONPATH=$MCB python3 -m lib.config --github-output   # what a workflow branches on
PYTHONPATH=$MCB python3 -m lib.config --tag 26.2-1.1.0  # the release tag
```

Run it after editing `.github/mc-bump.yml`. Validation
[refuses by name](Configuration#validation-is-strict-on-purpose), so this turns a
typo into a message instead of a green pipeline testing the wrong thing.

---

## Files these scripts leave in your repository

Worth adding to your `.gitignore`.

| File | Written by | Read by |
|---|---|---|
| `test-matrix-status.txt` | `test-matrix.py` | the failure report, for the test table |
| `test-escalation.txt` | `test-with-escalation.py` | the failure report, for the escalation table |
| `build-<version>.log`, `server-test-<version>.log` | the matrix | the collapsible logs |
| `server-test.log` | `headless-server-test.py` | you |
| `.mc-update-state.json` | `mc-bump.py` | `--revert-compat`, `--mark-supported` |
