# Migrating

Each major version of mc-bump is a **line**: a `release/vN` branch and a `vN` tag
that moves along it. An older line keeps working and still gets fixes, so moving
up is a choice, never a deadline.

This page sums up what changes from one major to the next, newest first. The
detail stays where it matters, in the `@v1` blocks folded into each page.

---

## From v1 to v2

### In short

v2 can follow and publish Minecraft **release candidates, pre-releases and
snapshots**, on request. Nothing is required: every new key defaults to the v1
behaviour, so a v1 config runs unchanged on v2 and still only sees releases.

### Steps

1. **Point at `@v2`** everywhere you point at `@v1`:

   ```yaml
   uses: spatulox-minecraft/mc-bump/.github/workflows/ci.yml@v2
   uses: spatulox-minecraft/mc-bump/.github/workflows/auto-update.yml@v2
   uses: spatulox-minecraft/mc-bump/.github/workflows/release.yml@v2
   uses: spatulox-minecraft/mc-bump@v2        # the composite action
   ```

   For the [CLI](CLI), the default branch is the v2 line: a plain `git clone`
   is enough, no `--branch v1`.

2. **Optional.** List the channels to follow and to publish, see
   [New configuration keys](#new-configuration-keys).

3. **Only if you publish non-releases.** Have the upload tasks read
   `release_type`, which v2 passes as `release`, `beta` or `alpha`, see
   [Gradle tasks](Getting-Started#gradle-tasks):

   ```groovy
   modrinth {
       versionType = project.findProperty("release_type") ?: "release"
   }
   ```

### New configuration keys

Both take a list of `release`, `rc`, `pre`, `snapshot`. An empty list or an
unknown channel is an error.

| Key | Default | Meaning |
|---|---|---|
| [`minecraft.channels`](Configuration#minecraft) | `[release]` | Which Minecraft versions the auto-update follows: the pull request and the version matrix. |
| [`release.channels`](Configuration#publishing-a-release-candidate-or-a-snapshot) | `[release]` | Which Minecraft versions may be published. |

Following a channel does not publish it. To test release candidates without
shipping them:

```yaml
minecraft:
  channels: [release, rc]
release:
  channels: [release]
```

### What behaves differently

With the default channels and no forced version, only the rows marked * change
anything you can see.

| | `@v1` | `@v2` |
|---|---|---|
| Auto-update target | latest Minecraft release | latest version in `minecraft.channels` |
| `minecraft-version` input left empty | latest release | latest version in `minecraft.channels` |
| Forcing a release candidate or a snapshot | not supported end to end | targeted whatever its channel |
| Compatibility range of a non-release | `=26.2-rc-1`, which Fabric Loader refuses | `=26.2-rc.1`, the way the loader spells it; a weekly `25w45a` is refused |
| Server test, booted version | must equal the id, so releases only | `26.2 Release Candidate 1` matches `26.2-rc-1` |
| Auto-update pull request | — | warns when the target is not a release |
| Release `check` | 3 guards | 4: adds a channel guard, a channel outside `release.channels` publishes nothing and stays green |
| `-Prelease_type` for the upload tasks | never passed | `release`, `beta` (`rc`, `pre`) or `alpha` (`snapshot`) * |
| GitHub release | always a release | pre-release outside the `release` channel |
| `mc-bump.py` with no `VERSION` | latest release | latest version in `minecraft.channels` |
| `python3 -m lib.config --channel` | — | prints the channel of a version id * |
| `mc-bump-ref` default | `v1` | `v2` * |

More in [Versions and compatibility](Versions-and-compatibility#release-candidates-and-snapshots)
and [Pipelines](Pipelines#release).

### Going back to v1

Remove the `minecraft` section and `release.channels`: v1 refuses them as
unknown keys. And `minecraft_version` must be a release, v1 cannot test or
publish anything else.

### Shared by both lines

These reach `@v1` and `@v2` alike, with nothing to migrate:

- [the toolchain](Versions-and-compatibility#the-toolchain): fabric-loom and the
  Gradle wrapper move only when Minecraft needs it, from a table refreshed weekly
  that moves both tags;
- the client gametest runs on a virtual display with software OpenGL and Vulkan,
  so the SDL3 clients of Minecraft 26.3+ find a render backend.
