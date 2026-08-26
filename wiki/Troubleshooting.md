# Troubleshooting

Every error mc-bump raises names what it did not like. This page groups them by
where they come from.

Reproduce almost all of them locally, without waiting for a pipeline:

```bash
MCB=../mc-bump
PYTHONPATH=$MCB python3 -m lib.config --json      # the config
python3 $MCB/scripts/headless-server-test.py      # the server test
python3 $MCB/scripts/mc-bump.py --dry-run         # the update
```

---

## Config

### `no .github/mc-bump.yml found in … or any parent directory`

The script walks up from `$MOD_ROOT` or the cwd. You are outside the mod
repository, or the file is not at `.github/mc-bump.yml`. Use `--root`, or set
`MOD_ROOT`.

Note the direction: mc-bump runs from its **own** checkout and edits **your**
repository, so "the repo" is never derived from where the script lives.

### `unknown key 'x'. Known keys here: …`

Exactly what it says, including for a nested key. This is deliberate — a typo in
`tests.server.expct` used to be silently ignored, which produces a green pipeline
testing the wrong thing.

### `'mod.metadata' is required in .github/mc-bump.yml`

Only three keys are required: `loader`, `mod.id`, `mod.metadata`.

### `tests.server.boot-timeout: expected a positive number, got -1`

Also `expected true or false`, `expected a string`, `expected a list`. The schema
checks types before anything runs.

### `notify.label: expected a single line`

Not a style rule. Every string here can end up in `$GITHUB_OUTPUT`, where a
newline opens a second `key=value` line — and GitHub keeps the **last**
occurrence of a key. A block scalar could therefore rewrite `ci` or `release` and
branch the pipeline on a forged value. Write it inline.

### `mod.id = 'My Mod' is not a valid Fabric mod id`

Lowercase letters, digits, `-` and `_`, 2 to 64 characters. This is checked at
**load** time, because an id the loader cannot turn into a log pattern makes the
server test unable to prove anything.

### `release.stores: unknown store(s) …`

Known stores: `modrinth`, `curseforge`.

---

## Patterns

### `tests.server.expect[0]: use either a glob 'pattern' or a 'regex', not both`

One or the other. Same for `count-pattern` / `count-regex`.

### `the glob '…' has no <count>, so there is no number to read`

An `expect-count` entry has to say *where* the number is:
`"Registered <count> potions"`.

### `<count> only means something under 'expect-count'`

You used it in a plain `expect`. Drop it, or move the entry.

### `count-source '…' does not exist`

Relative to the mod root. A typo, or a file that moved.

### A pattern that should match, but does not

Three things to check, in order:

1. **it is a glob, not a regex.** `Registered [0-9]+ potions` is a glob asking
   for one character from `0-9`, followed by a literal `]`, a literal `+`… Use
   `Registered * potions`, or switch to `regex:`.
2. **matching is line by line.** A `*` cannot cross a newline, so a pattern
   spanning two log lines never matches.
3. **matching is unanchored**, so you do *not* need to account for the timestamp
   and logger prefix — but you also cannot rely on `^`. In a `regex:`, `^` and
   `$` anchor to the **line**.

Iterate against a log you already have with
`python3 $MCB/scripts/headless-server-test.py`.

---

## The server test

### `my-mod does not appear in the loader's list of loaded mods`

The server booted, but the loader did not load your mod. Look for it in
`server-test.log` — usually a mismatch between `mod.id` and the id in
`fabric.mod.json`, or the mod failed to build into the run classpath.

Note this checks the loader's **indented inventory line**, not any mention of the
id. A mod id appears in classpath dumps and Gradle warnings, so a mod that
appears "everywhere" in the log can still legitimately fail here.

### `the server booted Minecraft 26.1 while 26.2 was expected`

A stale build cache, a concurrent edit of `gradle.properties`, or a
`-Pminecraft_version` your `build.gradle` ignores. Check that your build script
reads `project.minecraft_version` rather than hardcoding a version.

Without this guard the whole matrix would happily boot the same version three
times and report green.

### `cannot read the booted version from the log`

The server never reached its startup line — it died earlier. The failure is
above in the log; the last 200 lines are printed with the error.

### `fatal error detected in the log: …`

A line matched the loader's failure signatures (for Fabric: `Mixin apply failed`,
`Failed to load mod`, `Could not execute entrypoint`, `A potential solution has
been determined`, `Incompatible mod set`) or one of your `fatal-extra` globs. Up
to ten matching lines are shown.

### `<message>: '<pattern>' never appeared in the log`

An `expect` phrase is missing — the callback never ran, or the phrase changed.
The `message` you wrote is the first half; that is what it is for.

### `<message>: 3 instead of 5`

An `expect-count` mismatch, naming **both** numbers. Either the source gained
registrations the runtime did not, or the runtime is reporting stale data.

Watch for the comment case: registrations named in a Javadoc do **not** count,
which is usually what a "the source says more than the log" mismatch turns out to
be.

### `<message>: the mod never reported '…'`

The count pattern matched nothing in the **log**. The mod did not print its
count line at all.

### The server hangs, then times out

Raise `tests.server.boot-timeout` (default 900s). A cold Gradle cache on a fresh
runner genuinely takes a while.

The matrix retries **once**, and only for a build-setup deadlock that never
reaches `Starting minecraft server version`. A mod that is genuinely broken does
get that far, which is how the two are told apart — so a real failure is never
retried.

---

## Updates

### exit code `2` — `<loader> does not support Minecraft <version> yet`

**Not a failure.** Fabric has not published a stable loader for that Minecraft
release yet. The weekly workflow says so in the job summary and retries next
week.

### exit code `3` — `--bump-*` had nothing newer

Also normal: that rung of the ladder cannot change anything, because the
dependency is already on the newest release for the current Minecraft version.

### `mod_version = '1.1.0' does not match version.format = '{mc}-{mod}'`

Fix one of the two. mc-bump refuses to rewrite a version number it cannot read —
the previous behaviour (warn, then leave the file alone) produced a jar whose
name silently disagreed with what was tested.

### `version.format = '…': {mod} is required`

`{mod}` is the part a release actually chooses. `{mc}` is optional.

### `minecraft_version missing from gradle.properties`

Also `key 'mod_version' not found`. See the
[required keys](Getting-Started#gradleproperties).

### `no update state to revert (.mc-update-state.json not found)`

`--revert-compat` needs the snapshot an update writes. Nothing to revert.

### `.mc-update-state.json is for 26.2 but gradle.properties is on 26.1; refusing to revert`

A stale snapshot from another run. Delete it, or re-run the update.

---

## Network

### `HTTP 500 from …` / `cannot reach …`

Upstream is down. Re-run.

A **400** from Fabric meta is *not* an error — it is how Fabric says "I do not
know that Minecraft version", and it becomes exit code `2`.

### `no stable fabric-loom version found`

The Fabric maven metadata listed nothing purely numeric. Pin one yourself with
`--loom VERSION`, or `--buildtool VERSION`.

---

## GitHub Actions

### The auto-update opens no pull request

Enable **Settings → Actions → Allow GitHub Actions to create and approve pull
requests**.

### The mod's `ci.yml` does not run on the update pull request

Expected. A pull request opened with the default `GITHUB_TOKEN` does not trigger
`pull_request` workflows. Harmless: `auto-update` already built and booted every
claimed version on that branch.

### `release.stores lists modrinth,curseforge but these secrets are empty: MODRINTH_TOKEN`

The store tokens are checked **before** the matrix, on purpose. Add the secret,
or drop the store from `release.stores`.

Check `secrets: inherit` is in your caller workflow — without it the reusable
workflow sees nothing.

### `minecraft_version=26.2 is not in supported_minecraft_versions=26.1,26.1.1: compatibility was never proven, refusing to publish`

You merged a **draft** pull request — one where the matrix failed. The revert
restored the old claims while the version bump stayed. Fix the mod, let the
matrix go green, then release.

### The release job says "already released, nothing to do"

The tag exists. Bump `mod_version` for a new release; the tag carries it, which
is what makes it unique.

### A re-run says "`published/modrinth/v1.2.0` already exists, skipping"

Working as intended. Each store carries a marker tag, so re-running after a
partial failure uploads only what is missing instead of hitting *"version already
exists"* forever.

### `no Minecraft version to test`

`--list-test-versions` printed nothing: `minecraft_version` and
`supported_minecraft_versions` are both empty or unparseable.

### The Actions tab is green but something failed

It should not be — that is what the `verdict` job is for. The one legitimate case
is a **non-blocking gametest**: it ends green by design, and reports as a
`::warning::` plus a row in the failure table. Set
`workflows.gametest.blocking: true` if you want it to veto.

---

## Still stuck

Run the failing step locally — it is the same code:

```bash
python3 $MCB/scripts/test-matrix.py --minecraft <the version that failed>
```

and read `server-test-<version>.log`. If the answer is not there, open an issue
with that log and your `.github/mc-bump.yml`.
