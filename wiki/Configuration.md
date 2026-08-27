# Configuration

One file, `.github/mc-bump.yml`, in your mod's repository. It is the only thing
that parses the config, so a value can never mean two different things depending
on which part of the pipeline reads it.

Only `loader`, `mod.id` and `mod.metadata` are required. Everything below shows
its default.

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
    expect: []                     # see Proving the mod works
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

## Validation is strict on purpose

A config that is subtly wrong does not crash. It produces a **green pipeline
testing the wrong thing**, which is the exact failure mc-bump exists to prevent.
So:

- an unknown key is an error naming the key, **including a nested one**. A typo
  in `tests.server.expct` used to be silently ignored.
- a string where a boolean belongs is an error;
- a negative or zero timeout is an error;
- an unknown store is an error;
- a `mod.id` the loader cannot turn into a log pattern is an error **at load
  time**, not in the middle of a rewrite;
- an embedded newline in any string is an error, see
  [below](#why-every-string-must-be-a-single-line).

Check yours without pushing:

```bash
PYTHONPATH=../mc-bump python3 -m lib.config --json
```

---

## `loader`

**Required.** `fabric` is the only implementation today.

## `mod`

| Key | Default | Meaning |
|---|---|---|
| `id` | **required** | The id the loader announces in its inventory line. Validated against the Fabric spec: lowercase letters, digits, `-` and `_`, 2 to 64 characters. |
| `package` | `""` | Informational. Nothing reads it. |
| `metadata` | **required** | Path to `fabric.mod.json`. Rewritten by an update. |
| `mixins` | `""` | Path to the mixin config. When set, its `compatibilityLevel` follows the Java version. |

`mod.id` is what the server test looks for, but not naïvely. Fabric Loader prints
the mods it loaded as an indented tree, and the test anchors on that dash-space
prefix rather than on the bare id, which also appears in every classpath dump and
stack trace.

## `workflows`

Switches. A pipeline whose switch is `false` reads the config, says so in the job
summary, and stops. The workflow file can stay in place.

| Key | Default | Meaning |
|---|---|---|
| `ci` | `true` | The whole CI pipeline. |
| `auto-update` | `true` | The weekly Minecraft update. |
| `release` | `true` | Publishing and tagging. |
| `unit-tests` | `true` | The JUnit job inside CI. |
| `gametest.enabled` | `false` | A real Minecraft client under `xvfb`. Slow, flaky. |
| `gametest.blocking` | `false` | Does a failing gametest fail the run. |

A non-blocking gametest still **reports**: it lands in the pull request table and
in the job summary. That is the point, since an unrun test rots silently. It just
does not veto, and it does not open a failure issue on its own. Crying wolf for a
test you declared non-blocking would train everyone to ignore the issues.

## `version`

| Key | Default | Meaning |
|---|---|---|
| `format` | `"{mc}-{mod}"` | The `mod_version` template. |
| `tag` | `"v{version}"` | The release tag, from the resulting `mod_version`. |

`{mod}` is **required**, it is the part a release actually chooses. `{mc}` is
optional: a mod that does not put Minecraft in its version number simply leaves
it out, and `mod_version` is then never rewritten by an update.

Common shapes:

| `format` | `mod_version` for 26.1 + 26.1.1, mod 1.1.0 |
|---|---|
| `{mc}-{mod}` | `26.1.x-1.1.0` |
| `{mod}+mc{mc}` | `1.1.0+mc26.1.x` |
| `{mod}-{mc}` | `1.1.0-26.1.x` |
| `{mod}` | `1.1.0` |

More on what goes in the `{mc}` half:
[Versions and compatibility](Versions-and-compatibility#the-jar-name-says-what-was-tested).

A `mod_version` that does not match `format` is an **error**, not a warning. The
previous behaviour, warn and then leave the file alone, produced a jar whose name
silently disagreed with what was tested.

## `tests.unit`

| Key | Default | Meaning |
|---|---|---|
| `source` | `src/test/java` | Where the tests live. |
| `task` | `test` | The Gradle task. |
| `require-non-empty` | `true` | Fail when `source` holds no `.java` file. |

`gradlew test` on a project with no test source set passes with zero tests, which
is a green proving nothing. Refusing is the whole reason `require-non-empty`
exists. If your mod genuinely has no unit tests, set
`workflows.unit-tests: false`. That is an honest "we do not run any", where an
empty green is a lie.

## `tests.matrix`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Build and boot every claimed version. |
| `parallel` | `true` | One GitHub job per version instead of a sequential loop. |

`parallel` is a wall-time knob only: three claimed versions cost one build
instead of three in series, and a version failing never hides the state of the
others (`fail-fast: false`). Knowing whether 26.1 broke *too* is what tells a
dependency bump apart from a code bug.

The `auto-update` matrix stays **sequential regardless**, because the escalation
ladder replays the whole matrix after each bump.

## `tests.server`

| Key | Default | Meaning |
|---|---|---|
| `boot-timeout` | `900` | Seconds before giving up on startup. |
| `stop-timeout` | `60` | Seconds before force-killing the JVM. |
| `expect` | `[]` | Phrases that must appear in the log. |
| `expect-count` | `[]` | Numbers your mod reports, checked against your source. |
| `fatal-extra` | `[]` | Extra failure signatures, added to the loader's own. |

`expect` and `expect-count` have a page of their own:
**[Proving the mod works](Proving-the-mod-works)**.

The loader already contributes its own fatal signatures. For Fabric:
`Mixin apply failed`, `Failed to load mod`, `Could not execute entrypoint`,
`A potential solution has been determined`, `Incompatible mod set`.
`fatal-extra` adds to that list, it does not replace it. The entries are globs,
like everything else matched against the log.

## `release`

| Key | Default | Meaning |
|---|---|---|
| `stores` | `[modrinth, curseforge]` | Where to publish. Known: `modrinth`, `curseforge`, `github`. |
| `branch-prefix` | `chore/mc-` | The auto-update branch is `<prefix><minecraft version>`. |
| `artifact-retention-days` | `30` | How long the logs and reports are kept. |

A store listed here makes its token **required**: the release pipeline refuses up
front when `MODRINTH_TOKEN` or `CURSEFORGE_TOKEN` is empty. Publishing to one
store only is `stores: [modrinth]`.

`github` is the exception: it needs no token and no Gradle task. It creates a
**GitHub release** on the release tag, with the jars from `build/libs` attached
— the mod jar and, when the build script asks for one
(`java { withSourcesJar() }`), the sources jar. The body is the same
compatibility table the tag annotation carries, followed by the notes GitHub
generates from the commits. It is opt-in: add it, the default list does not have
it.

```yaml
release:
  stores: [modrinth, curseforge, github]
```

## `notify`

| Key | Default | Meaning |
|---|---|---|
| `assignee` | `""` | GitHub login that gets the failure issue and the update PR. Empty means nobody. |
| `label` | `ci-failure` | Label put on the failure issue. Created if missing. |
| `keep-branch` | `true` | Keep the auto-update branch after a failure. |
| `on-pull-request` | `false` | Open a failure issue for a pull request run too. |
| `log-tail` | `100` | Log lines per collapsible block in the issue and the PR. |

`on-pull-request` defaults to `false` because the pull request already shows its
own red checks, and opening an issue as well is noise. Turn it on for a
repository where pull requests come from forks and the author never looks at the
Actions tab.

The failure issue title is **deterministic**, so a re-run comments on the
existing issue instead of opening a duplicate.

---

## Why every string must be a single line

Every string in this config can end up in `$GITHUB_OUTPUT`, where a newline opens
a second `key=value` line. GitHub keeps the last occurrence of a key, so a
`notify.label` spelled as a block scalar could rewrite `ci` or `release` and
branch the pipeline on a forged value.

The emitter quotes such values; the schema refuses them at the source, where the
error can still name the key.
