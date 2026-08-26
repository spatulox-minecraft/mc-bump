# Composite Action

The escape hatch. For the full pipelines use the
[reusable workflows](Pipelines). This runs **one mc-bump script** against the mod
repository checked out in the workspace, so you can build a pipeline of your own.

```yaml
- uses: spatulox-minecraft/mc-bump@v1
  with:
    command: test-matrix.py
```

## Inputs

| Input | Required | Default | |
|---|---|---|---|
| `command` | ✅ | | Script to run, relative to `mc-bump/scripts`: `headless-server-test.py`, `test-matrix.py`, `test-with-escalation.py` or `mc-bump.py`. |
| `args` | | `""` | Arguments passed to the command. Word-split, so a list of flags works. |
| `working-directory` | | `.` | The mod repository. Defaults to the workspace root. |

An unknown `command` fails with `::error::unknown mc-bump command '...'` and prints
the list of available scripts.

The action installs PyYAML itself. It does **not** set up a JDK or Gradle. That is
your workflow's job, and it is why this is an escape hatch rather than a
shortcut.

## What it does not do

The reusable workflows do a lot that this action leaves to you:

- reading the config into job outputs (`.github/actions/setup`)
- picking the Java version for the target Minecraft release
- fanning the matrix out into one job per version
- collecting the failure reports into an issue
- the `verdict` job that keeps the Actions tab honest

If you find yourself reimplementing those, use `ci.yml` instead.

## Examples

### One version, in your own job

```yaml
jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-java@v5
        with:
          distribution: temurin
          java-version: 25
      - uses: gradle/actions/setup-gradle@v5

      - run: chmod +x gradlew
      - uses: spatulox-minecraft/mc-bump@v1
        with:
          command: headless-server-test.py

      - uses: actions/upload-artifact@v7
        if: always()
        with:
          name: server-log
          path: server-test.log
```

### Restrict the matrix to one series

```yaml
- uses: spatulox-minecraft/mc-bump@v1
  with:
    command: test-matrix.py
    args: --minecraft 26.1 26.1.1
```

### A dry-run update, as a scheduled report

```yaml
- uses: spatulox-minecraft/mc-bump@v1
  with:
    command: mc-bump.py
    args: --dry-run
```

### A mod in a subdirectory

```yaml
- uses: spatulox-minecraft/mc-bump@v1
  with:
    command: test-matrix.py
    working-directory: mods/my-mod
```

Your mod's root is otherwise found by walking up from the cwd looking for
`.github/mc-bump.yml`. `MOD_ROOT` and `--root` are the other two ways to say it.

## Environment

Every environment variable the scripts read still applies. Set them with `env:` on
the step:

```yaml
- uses: spatulox-minecraft/mc-bump@v1
  env:
    BOOT_TIMEOUT: "1200"
    LOG: server-test-26.1.log
    EXPECTED_MC: "26.1"
    GRADLE_ARGS: -Pminecraft_version=26.1
  with:
    command: headless-server-test.py
```

See [CLI](CLI#environment) for the full list.

## Exit codes

They propagate, so `mc-bump.py`'s codes are what fails or passes your step:

| | |
|---|---|
| `0` | success, or already up to date |
| `1` | error, or failed tests |
| `2` | the loader does not support that Minecraft version yet |
| `3` | `--bump-*` had nothing newer to move to |

`2` and `3` are normal outcomes and will still fail a step. Handle them with
`continue-on-error` plus a check on `steps.<id>.outcome`, the way `auto-update`
does.
