# Adding a loader

Fabric is the only implementation today, but everything loader-specific lives
behind one interface. Adding NeoForge is one new file plus one line.

```
1. Copy lib/loaders/fabric.py to lib/loaders/neoforge.py and implement Loader
   against the NeoForged maven and neoforge.mods.toml.
2. Add it to LOADERS in lib/loaders/__init__.py.
3. Set `loader: neoforge` in a mod's config.
```

Nothing else changes: the workflows, the scripts and the version arithmetic never
name a loader.

## The seam

Two rules keep it that way:

- **nothing outside `loaders/` names Fabric**;
- **version arithmetic is not the loader's business.** Series, bounds, ordering
  and the `mod_version` template are the same problem on every loader and live in
  `lib/versions.py`. The loader only *renders* the bounds it is given.

That second rule is the one that decides what belongs in your file. `>=26.1
<=26.1.2` is Fabric's spelling; `[26.1,26.1.3)` would be a maven range. The
`(26.1, 26.1.2)` pair behind both is shared.

## The interface

`lib/loaders/base.py`. Three kinds of knowledge, plus two class attributes.

### Class attributes

```python
class NeoForgeLoader(Loader):
    name = "neoforge"
    gradle_keys = {
        "loader": "neoforge_version",
        "api": "...",
        "buildtool": "...",
    }
```

`gradle_keys` maps the three **roles** mc-bump reasons about to the property
names in `gradle.properties`. Roles, not names — the snapshot in
`.mc-update-state.json` is keyed by role, so a loader that spells them
differently needs no change anywhere else.

### Resolution — what upstream offers

| Method | |
|---|---|
| `resolve(minecraft_version, pin_buildtool=None) -> Resolved` | The loader, its API and the build plugin for that Minecraft version. Return an empty `Resolved` when the version is not supported yet. |
| `resolve_one(role, minecraft_version) -> str \| None` | One role, for an escalation rung. |
| `escalation_rungs() -> list[Rung]` | The ladder, in order. |

`Resolved` carries `loader`, `api`, `buildtool` and a free-form `extra`;
`usable` says whether it is complete enough to write.

A `Rung` is `(gradle_key, flag, label)` — the property to move, the `mc-bump.py`
flag that moves it, and the name a report shows.

Order matters: the API before the loader, because the API is a normal library the
mod calls into while the loader runs every mod on the server. A loader with a
different dependency shape declares a different ladder, and `matrix.py` needs no
change.

**Return `None`, do not raise, when upstream simply has nothing yet.** That is
how exit code `2` ("not supported yet", a normal weekly outcome) stays distinct
from a real error.

### Metadata — reading and writing the mod's files

| Method | |
|---|---|
| `render_range(low, high) -> str` | The compatibility range, in your syntax. |
| `depends_keys() -> dict[str, str]` | Role → the key name inside the metadata file. |
| `read_depends(paths, key) -> str \| None` | |
| `write_depends(paths, key, value, dry_run) -> bool` | Return whether it changed. |
| `write_java_version(paths, java, dry_run) -> bool` | Propagate the Java level to the metadata and, if there is one, the mixin config. |

`paths` is a `ModPaths` — every path explicit, because mc-bump edits someone
else's repository and nothing can be derived from `__file__`.

`gradle.properties` is **not** your problem: the caller writes it in the same
pass as the other keys.

### Runtime — what the log has to say

| Method | |
|---|---|
| `mod_loaded_pattern(mod_id) -> str` | A Python regex, matched line by line, proving the loader actually loaded the mod. |
| `fatal_patterns() -> list[str]` | Failure signatures, defaulted in the base class. |
| `server_task() -> str` | Default `runServer`. |
| `client_gametest_task() -> str` | Default `runClientGameTest`. |
| `store_loader_name() -> str` | What Modrinth and CurseForge call this loader. |

`mod_loaded_pattern` is the one to get right. Anchor on your loader's **own
inventory line**, not on any mention of the mod id — an id also appears in
classpath dumps, stack traces and Gradle warnings, and a bare grep passes on a
mod the loader rejected.

Fabric prints:

```
Loading 5 mods:
    - my-mod 26.2-1.1.0
    - fabric-api 0.156.0+26.2
```

so the pattern is `^\s*-\s+my-mod\s` — the dash-space prefix is what separates a
loaded mod from a mentioned one, and the trailing `\s` is what keeps `my-mod`
from matching `my-mod-extras`.

Validate the id before building a pattern out of it, rather than escaping
unvalidated input. Fabric restricts a mod id to `[a-z0-9_-]`, none of which is a
regex metacharacter, and `MOD_ID_RE` rejects anything else — which is also why
`mod.id` is checked at config load time.

Return a **Python** regex. `[[:space:]]` is a POSIX class `re` does not know; a
pattern using it silently matches nothing.

## Register it

```python
# lib/loaders/__init__.py
LOADERS = {
    "fabric": FabricLoader,
    "neoforge": NeoForgeLoader,
}
```

`config.py` reads `LOADERS` for the `loader:` choices, so the new value is
accepted and an unknown one is refused by name, automatically.

## Test it

`tests/helpers.py` builds a throwaway mod repository — config,
`gradle.properties`, the metadata, the mixin config — and loads it through
`config.load()`. Point a copy at your metadata format and the existing decision
tests apply to your loader too.

What is worth testing:

- `render_range` for equal bounds (pin exactly) and a real range;
- `mod_loaded_pattern` against a **real log**, including a line where the id
  appears outside the inventory;
- `escalation_rungs()` ordering.

What is not: `resolve()`. The resolvers describe the shape of upstream APIs,
which no local assertion can pin down — see [Contributing](Contributing#no-network-in-the-tests).
