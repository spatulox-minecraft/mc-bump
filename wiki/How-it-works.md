# How it works

The mechanisms behind the [two ideas](Home#the-two-ideas), and the ordering
problems that shaped them.

## Series arithmetic

A **series** is the first two components of a Minecraft version: `26.1.2` →
`26.1`. One jar covers one series.

Two claims describe compatibility, and they must agree:

| Claim | Meaning |
|---|---|
| `supported_minecraft_versions` | what is **announced** on Modrinth and CurseForge |
| the loader metadata range | what the **loader** accepts or refuses at runtime |

Both derive from the series, and the upper bound is the highest version actually
proven to work:

| `minecraft_version` | `supported_minecraft_versions` | bounds | |
|---|---|---|---|
| `26.1` | `26.1` | `(26.1, 26.1)` | exact |
| `26.1.1` | `26.1,26.1.1` | `(26.1, 26.1.1)` | |
| `26.1.2` | `26.1,26.1.1,26.1.2` | `(26.1, 26.1.2)` | |
| `26.2` | `26.2` *(series reset)* | `(26.2, 26.2)` | exact |

Only the **rendering** of those bounds is loader-specific — Fabric writes
`>=26.1 <=26.1.2`, a maven range would write `[26.1,26.1.3)` — so it lives in the
loader module and the arithmetic lives in `lib/versions.py`.

Versions are ordered **numerically**, not lexically: `26.1.10` sorts after
`26.1.9`. A non-numeric version (a snapshot) returns `None` and is pinned exactly
rather than folded into a range.

### The jar name says what was tested

`mod_version` carries the same information in its `{mc}` half, through
`mc_label()`:

```
[26.2]                      -> "26.2"
[26.1, 26.1.1]              -> "26.1.x"
[26.1, 26.1.1, 26.1.2]      -> "26.1.x"
```

The wildcard **never climbs a level**. `26.x` would promise 26.3 to anyone
reading the file name, and nothing has ever built, booted or published that
version.

On green, `mark_supported()` recomputes the supported list, the range **and** the
jar name from that same list, in one pass — so the three can never drift apart.
Drift between them is the failure this repository exists to prevent.

---

## The ordering problem

The compatibility range has to be widened **before** the tests. Otherwise the
loader refuses to load the mod on the new Minecraft version's server, and nothing
could ever be validated — the range is what gates the very test that would
justify widening it.

So the sequence is optimistic, with a snapshot:

```
1. save_update_state()   snapshot the previous claims into .mc-update-state.json
2. widen the range       optimistically, before anything has been proven
3. run the matrix
4a. green -> mark_supported()   extend the claims, recompute everything from them
4b. red   -> revert_compat()    restore the claims, KEEP the version bumps
```

Step 4b is the subtle one. The claims come back because the mod does not work and
must not say it does. The version bumps stay because they are the dependency diff
a human picks up from — throwing them away would mean redoing the resolution by
hand.

A snapshot written for a different Minecraft version is **refused** rather than
applied.

---

## The escalation ladder

An update moves one variable: Minecraft. The build plugin follows (it is a Gradle
plugin, not something that ships in the jar) and Java follows (Mojang dictates
it). The loader and its API stay **frozen**.

They only move as a *reaction* to a red matrix:

```
matrix with the frozen dependencies
  KO -> bump the API      -> whole matrix again
          KO -> bump the loader -> whole matrix again
                  KO -> the mod is really broken
```

Two things make this worth the wall time.

**One suspect at a time.** If Minecraft, the loader and the API all moved
together, a red matrix has three candidate causes and no way to separate them. A
frozen baseline means the first red is *about Minecraft*, and each rung after it
is about exactly one dependency.

**Each rung replays the whole matrix.** A newer API is exactly the kind of change
that fixes the newest version while breaking an older one of the same series.
Testing only the version that failed would ship that regression.

API before loader, because the API is a normal library the mod calls into while
the loader is the thing that runs every mod on the server.

The rungs come from the loader, so a loader with different frozen dependencies
needs no change in `matrix.py`. The result is written to `test-escalation.txt`,
one `<dependency> <from> <to>` per line, and folded into the pull request body
and the failure issue.

### The floors

A dependency that an escalation actually moved gets a **floor** engraved in the
mod metadata — `depends.fabric-api: ">=0.157.0"` — because that version is now
genuinely required. Compared against the snapshot, so an API left at `*` keeps
its `*`.

Only after a green matrix. A bump is a hypothesis until the matrix proves it.

---

## What a server test actually asserts

`check_log()` is a **pure function over the log text**, which is what makes the
whole thing unit-testable without booting a single server.

1. **the mod is in the loader's inventory.** Fabric prints the mods it loaded as
   an indented tree:

   ```
   Loading 5 mods:
       - my-mod 26.2-1.1.0
       - fabric-api 0.156.0+26.2
   ```

   Anchoring on that dash-space prefix is what tells a loaded mod apart from the
   same id in a classpath dump or a stack trace — which is what a bare `grep`
   matched. A mod whose id is a *prefix* of yours does not pass either.

2. **the version we asked for is the version that booted.** A stale build cache
   would otherwise turn the test green on the wrong version.

3. **no fatal signature.** The loader's own list plus `tests.server.fatal-extra`.
   An ordinary `WARN` does not count.

4. **every [`expect`](Proving-the-mod-works#expect) phrase**, and every
   [`expect-count`](Proving-the-mod-works#expect-count) number matching what the
   source actually contains.

The process handling lives in `run()`, separately: flat world, watchdog off,
world wiped between runs, `start_new_session` + `killpg` — because Gradle is a
launcher and the JVM is the real server, so killing the Gradle process leaves the
server running.

---

## The failure report

Was ~250 lines of bash inlined in a workflow, where nothing but GitHub could run
it. Now functions over plain data, checked by tests instead of by opening a pull
request.

**The artifact contract.** Each test job uploads `failure-report-<job>/` holding
`meta.json` (`title`, `kind`, `failed`) and `log.txt`. A passing job uploads
`"failed": false` — that is how the report tells *green* from *never ran*, which
a missing artifact cannot express.

The same rendering is pasted into **both** the issue and the pull request, from
the same data. The sequential matrix — which runs in one job and produces no
per-version artifact — is folded into that same table from
`test-matrix-status.txt`.

Failures come first, then by severity: a build failure explains a server failure.
A malformed report is kept under a generic title rather than dropped; losing the
evidence of a failure is worse than showing it badly.

Log blocks use a **four-backtick** fence, because a Minecraft log can contain a
triple-backtick line and would otherwise spill out of the block.

`failure_issue_title()` is deterministic, so a re-run comments on the existing
issue instead of opening a duplicate.

---

## The library layout

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

Two rules keep it that way:

- **nothing below `config.py` imports a `Project`** — the low-level modules stay
  pure functions over strings and paths;
- **nothing outside `loaders/` names Fabric**.

`scripts/` is argument parsing around `lib/`; the workflows are argument parsing
around `scripts/`. Nothing in `lib/` reads `argv` or the environment except
through a `Project`.

### Why `ModPaths` exists

mc-bump runs from **its own** checkout and edits **someone else's** repository.
"The repo" is therefore never `Path(__file__).parent`. `ModPaths` carries every
path explicitly and `require()` fails by name, up front, rather than halfway
through a rewrite.

### Why a 400 is not an error

Fabric meta answers **400**, not 404, for a Minecraft version it does not know —
with a valid JSON body. That means *"not published yet"* and must be readable;
a 500 must raise. Without that distinction the weekly update would stop working
while staying green.

`user_agent()` derives its string from `$GITHUB_REPOSITORY`, so one abusive
repository does not get every mc-bump user rate-limited.
