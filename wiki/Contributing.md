# Contributing

```bash
git clone https://github.com/spatulox-minecraft/mc-bump.git
cd mc-bump
python3 -m pip install pyyaml
python3 -m unittest discover -s tests -t .
```

`-t .` so the tests import `lib.*` the same way the scripts do.

Standard library only, except PyYAML in `config.py`. No `bash`, no `jq`, no
`shellcheck` — everything is Python, including the scripts that used to be shell.

## Layout

| | |
|---|---|
| `lib/` | Everything mc-bump knows how to do, as importable modules. |
| `scripts/` | Four executables: argument parsing, environment variables and exit codes around `lib/`. |
| `tests/` | `unittest`, no network. |
| `.github/workflows/` | The three reusable pipelines, plus `self-test.yml`. |
| `.github/actions/` | `setup`, `report`, `report-issue` — composite actions shared by the pipelines. |
| `testmod/fabric/` | A real Fabric mod, built and booted by the self-test. |

Each directory has its own `README.md` going module by module. The
[How it works](How-it-works#the-library-layout) page has the dependency graph and
the two rules that keep it acyclic.

## The one architectural rule

**Every decision lives in `lib/`.**

`scripts/` parses arguments; the workflows parse `scripts/`. That is what makes
the CI and a local run go through strictly the same code — and it is why
`check_log()` can be a pure function over a string while the CI still uses it to
boot a real server.

Concretely: if you find yourself writing an `if` in a workflow's `run:` block,
it probably belongs in `lib/`. The failure report used to be ~250 lines of bash
inlined in a workflow, where nothing but GitHub could run it.

## No network in the tests

The resolvers that call Mojang, Fabric and Modrinth are **deliberately untested**.
They describe the shape of upstream APIs, which no local assertion can pin down —
a mock only asserts that today's guess still matches yesterday's guess.

What is tested is the **decision layer**, for one reason: a mistake there does not
crash. It produces a green matrix, a jar a player's loader refuses, or a store
page announcing a version nobody ever booted. Those functions also run once a
week at best, at the end of a forty minute pipeline, and only for the single
scenario reality happens to present that day.

| File | What it pins down |
|---|---|
| `test_versions.py` | Series arithmetic, numeric ordering, `mod_version` templates. Asserts the exact table in `versions.py`'s docstring, and that the three claims agree with each other. |
| `test_config.py` | Refusing early and **by name** — every assertion is about the error naming the offending key. |
| `test_patterns.py` | The three things layered on `fnmatch.translate()`, the `regex:` hatch, and string-aware comment stripping. |
| `test_server_test.py` | What the log has to say, without booting anything. |
| `test_update.py` | The file rewrites, over a real temporary repository. |
| `test_report.py` | What a human reads when something breaks. |

### The fixture

`tests/helpers.py` builds a real mod repository in a `TemporaryDirectory` —
config, `gradle.properties`, `fabric.mod.json`, the mixin config — and loads it
through `config.load()`.

An actual directory rather than monkeypatched module globals: it is shorter, and
stricter, because every test then goes through `find_root()`, schema validation
and `ModPaths`. A config change that breaks the contract fails **here** instead of
on the first CI run of somebody else's repository.

The fixture sits on `extended-time-potion`, Minecraft `26.1.1`,
`supported=26.1,26.1.1`, `mod_version=26.1.x-1.1.0` — a **mid-series** state,
which is where the interesting cases live.

## The self-test

mc-bump *is* the CI of other repositories, so nothing here may rot unnoticed.
`.github/workflows/self-test.yml` runs on every push and pull request:

| Job | |
|---|---|
| **Unit tests** | The suite, plus a check that every `yaml` config example in `README.md` still validates against the live schema. |
| **Entry points** | Every script still answers `--help` — what a syntax error or a bad import breaks first, and the one thing the unit tests never exercise because they import `lib/` directly. |
| **actionlint** | The workflows themselves. |
| **Fixture mod** | `testmod/fabric` is really built, really booted, and really passed through `check_log()` — and then deliberately broken, to prove `check_log()` can say *no*. |

That last job is the important one. A log checker that has only ever been shown
passing logs has not been tested; the fixture proves the failure path by breaking
the mod on purpose and asserting the issue body carries **the reason**, not just a
red mark.

Adding a `check_log()` assertion means adding a case there too.

## Writing a change

- **Match the surrounding prose.** Every module carries a docstring saying *why*,
  not *what* — the constraint that shaped it, the bug that motivated it. The
  `README.md` files go module by module in the same voice. A change that adds
  behaviour without saying what it protects against is half a change.
- **Add the test to the layer that decides.** A pure function over a string beats
  an integration test that boots a server, every time.
- **Do not name a loader outside `loaders/`.** See
  [Adding a loader](Adding-a-loader).
- **Do not put a decision in a workflow.**

## Releasing

The pipelines are consumed as `@v1`, a moving tag on the v1 line. A change that
breaks a mod's existing `.github/mc-bump.yml` — a removed key, a changed default,
a stricter schema — is a v2, not a v1 move. Mods pin `@v1` precisely so their CI
does not change under them on a Monday morning.
