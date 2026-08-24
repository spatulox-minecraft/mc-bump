# `tests/` — the unit tests

```bash
python3 -m unittest discover -s tests -t .
```

`unittest`, standard library only, and **no network**. The resolvers that call
Mojang, Fabric and Modrinth are deliberately untested: they describe the shape of
upstream APIs, which no local assertion can pin down.

What is tested is the **decision layer**, for one reason — a mistake there does
not crash. It produces a green matrix, a jar a player's loader refuses, or a
store page announcing a version nobody ever booted. Those functions also run once
a week at best, at the end of a forty minute pipeline, and only for the single
scenario reality happens to present that day.

## Files

### `__init__.py`
Empty. Makes `tests` a package so `from .helpers import …` works under
`discover -t .`.

### `helpers.py` — a throwaway mod repository
`ModRepoTestCase` builds a real mod repo in a `TemporaryDirectory` — config,
`gradle.properties`, `fabric.mod.json`, the mixin config — and loads it through
`config.load()`.

An actual directory rather than monkeypatched module globals: it is shorter, and
it is stricter, because every test then goes through `find_root()`, schema
validation and `ModPaths`. A config change that breaks the contract fails here
instead of on the first CI run of some other repository.

Fixture: `extended-time-potion` on Minecraft `26.1.1`, `supported=26.1,26.1.1`,
`mod_version=26.1.x-1.1.0` — a mid-series state, which is where the interesting
cases live. Helpers: `reload()`, `prop()`, `set_prop()`, `depends()`, and
`SILENT` for the log callback.

### `test_versions.py` — the arithmetic
Pure functions, no fixtures needed for most of it: `series_of`, `parse_version`
(numeric, not lexical ordering), `mc_label`, `versions_to_test`, `compat_bounds`,
and the `mod_version` template.

`CompatBoundsTest.test_the_documented_table` asserts exactly the table in
`lib/versions.py`'s docstring, and `CoherenceTest` checks the three claims agree
with each other — the announced versions, the range the loader enforces, and the
jar name — since drift between them is the failure this repo exists to prevent.

### `test_config.py` — refusing early and by name
`LoadTest` (defaults, path resolution), `FindRootTest` (walking up, and failing
when there is nothing to find), `ValidationTest` (a missing required key, an unknown key — nested ones included,
since a typo there used to be silently ignored — a string where a boolean
belongs, a negative timeout, an unknown store, and a mod id that would make the
server test prove nothing rather than fail loudly),
`ExportTest` (the JSON and `$GITHUB_OUTPUT` shapes the workflows read).

Every assertion is about the error message naming the offending key, because a
config that is wrong in a subtle way does not crash — it produces a green
pipeline testing the wrong thing.

### `test_patterns.py` — globs and comments
`GlobTest` covers the three things layered on `fnmatch.translate()`, not glob
semantics themselves (those are the standard library's problem): the unanchoring,
the line-by-line matching, and the `<count>` capture. Plus the two refusals —
`<count>` outside `expect-count`, and an `expect-count` glob without it.

`RegexEscapeHatchTest` covers the `regex:` key. `StripCommentsTest` is the
string-awareness: a `http://` in a literal is not a comment, an escaped quote
does not end the string, a block comment keeps its newlines, and a registration
named in a Javadoc no longer counts — which mc-bump's own fixture hit.

### `test_server_test.py` — what the log has to say
The reason the shell → Python rewrite was worth doing. None of this was testable
while the assertions were interleaved with process handling; they are now a pure
function over the log text.

`GOOD_LOG` is shaped after a real Fabric log, **including the gradle warning
line** that used to satisfy the old `grep 'extended-time-potion'` — so
`ModLoadedTest` proves the inventory line is what counts, and that a mod whose id
is a prefix of ours does not pass.

Then: `BootedVersionTest` (a stale cache booting the wrong version),
`FatalPatternTest` (a loader signature fails even though the server booted; an
ordinary WARN does not), `ExpectTest`, and `ExpectCountTest` — the count comes
from the source, a mismatch names both numbers, and a `count-pattern` that no
longer matches anything says so instead of passing on zero.

### `test_update.py` — the file rewrites
Over a real temporary repository. Ported from ExtendedTimePotion's
`test_update_mc_version.py`; same scenarios, now through a real `Project`.

- `UpdateGradlePropertiesTest` — extending a series vs. changing series (which
  resets `supported_minecraft_versions` and renames the jar), the frozen
  dependencies staying untouched, the build plugin following, `--dry-run`.
- `MarkSupportedTest` — the list, the range and the jar name all recomputed from
  the same source; a floor written **only** for what an escalation moved, so an
  API left at `*` keeps its `*`.
- `RevertCompatTest` — claims come back, bumps stay; a snapshot written for
  another version is refused rather than applied.
- `SaveUpdateStateTest` — a re-run keeps the genuine pre-bump snapshot; the
  snapshot is keyed by role, not by Fabric's names.
- `ModVersionFormatTest` — a version that does not match `version.format` is an
  error, not a warning.

### `test_report.py` — what the user reads when something breaks
No fixture repo; reports are built on disk from `write_report()`.

`CollectTest` (failures first, then by severity; a malformed report kept under a
generic title rather than dropped), `DetailsTest` (the four-backtick fence, since
a Minecraft log can contain a triple-backtick line and would otherwise spill out
of the block), `IssueBodyTest`, `TestReportTest` (the one table pasted into both
places), `MatrixStatusTest` (the sequential matrix folded into that same table,
and an empty status file rendering the run log rather than nothing at all), and
`EscalationTest`.
