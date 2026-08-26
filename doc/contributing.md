# Contributing

```bash
git clone https://github.com/spatulox-minecraft/mc-bump.git
cd mc-bump
python3 -m pip install pyyaml
python3 -m unittest discover -s tests -t .
```

`-t .` so the tests import `lib.*` the same way the scripts do.

Standard library only, except PyYAML in `config.py`. No `bash`, no `jq`, no
`shellcheck`. Everything is Python, including the scripts that used to be shell.

## Layout

| | |
|---|---|
| `lib/` | Everything mc-bump knows how to do, as importable modules. |
| `scripts/` | Four executables: argument parsing, environment variables and exit codes around `lib/`. |
| `tests/` | `unittest`, no network. |
| `.github/workflows/` | The three reusable pipelines, plus `self-test.yml`. |
| `.github/actions/` | `setup`, `report`, `report-issue`, composite actions shared by the pipelines. |
| `testmod/fabric/` | A real Fabric mod, built and booted by the self-test. |
| `doc/` | These pages. |

Each source directory has its own `README.md` going module by module.
[internals.md](internals.md) has the dependency graph and the rules that keep it
acyclic.

## No network in the tests

The resolvers that call Mojang, Fabric and Modrinth are **deliberately untested**.
They describe the shape of upstream APIs, which no local assertion can pin down,
and a mock only asserts that today's guess still matches yesterday's guess.

What is tested is the **decision layer**, for one reason: a mistake there does
not crash. It produces a green matrix, a jar a player's loader refuses, or a
store page announcing a version nobody ever booted. Those functions also run once
a week at best, at the end of a forty minute pipeline, and only for the single
scenario reality happens to present that day.

| File | What it pins down |
|---|---|
| `test_versions.py` | Series arithmetic, numeric ordering, `mod_version` templates. Asserts the exact table in `versions.py`'s docstring, and that the three claims agree with each other. |
| `test_config.py` | Refusing early and **by name**. Every assertion is about the error naming the offending key. |
| `test_patterns.py` | The three things layered on `fnmatch.translate()`, the `regex:` hatch, and string-aware comment stripping. |
| `test_server_test.py` | What the log has to say, without booting anything. |
| `test_update.py` | The file rewrites, over a real temporary repository. |
| `test_report.py` | What a human reads when something breaks. |

### The fixture

`tests/helpers.py` builds a real mod repository in a `TemporaryDirectory`
(config, `gradle.properties`, `fabric.mod.json`, the mixin config) and loads it
through `config.load()`.

An actual directory rather than monkeypatched module globals: it is shorter, and
stricter, because every test then goes through `find_root()`, schema validation
and `ModPaths`. A config change that breaks the contract fails **here** instead
of on the first CI run of somebody else's repository.

The fixture sits on `extended-time-potion`, Minecraft `26.1.1`,
`supported=26.1,26.1.1`, `mod_version=26.1.x-1.1.0`, a **mid-series** state,
which is where the interesting cases live.

## The self-test

mc-bump *is* the CI of other repositories, so nothing here may rot unnoticed.
`.github/workflows/self-test.yml` runs on every push and pull request:

| Job | |
|---|---|
| **Unit tests** | The suite, plus a check that every `yaml` config example in `README.md` still validates against the live schema. |
| **Entry points** | Every script still answers `--help`, which is what a syntax error or a bad import breaks first, and the one thing the unit tests never exercise because they import `lib/` directly. |
| **actionlint** | The workflows themselves. |
| **Fixture mod** | `testmod/fabric` is really built, really booted, and really passed through `check_log()`, then deliberately broken to prove `check_log()` can say *no*. |

That last job is the important one. A log checker that has only ever been shown
passing logs has not been tested. The fixture proves the failure path by breaking
the mod on purpose and asserting the issue body carries **the reason**, not just
a red mark.

Adding a `check_log()` assertion means adding a case there too.

## Writing a change

- **Match the surrounding prose.** Every module carries a docstring saying *why*,
  not *what*: the constraint that shaped it, the bug that motivated it. A change
  that adds behaviour without saying what it protects against is half a change.
- **Add the test to the layer that decides.** A pure function over a string beats
  an integration test that boots a server, every time.
- **Do not name a loader outside `loaders/`.** See
  [adding-a-loader.md](adding-a-loader.md).
- **Do not put a decision in a workflow.**

## Documentation

Two audiences, two places, and they must not swap.

- **The [wiki](https://github.com/spatulox-minecraft/mc-bump/wiki)** is for mod
  authors *using* mc-bump: the config reference, the pipelines, the CLI,
  troubleshooting. Nothing about the internals, and nothing about extending
  mc-bump.
- **`doc/` and the per-directory READMEs** are for people changing mc-bump.

A behaviour change that a mod author can observe means a wiki edit too. The wiki
is a separate git repository, cloned from
`git@github.com:spatulox-minecraft/mc-bump.wiki.git`.

## Releasing

The pipelines are consumed as `@v1`, a moving tag on the v1 line. A change that
breaks a mod's existing `.github/mc-bump.yml`, whether a removed key, a changed
default or a stricter schema, is a v2 and not a v1 move. Mods pin `@v1` precisely
so their CI does not change under them on a Monday morning.
