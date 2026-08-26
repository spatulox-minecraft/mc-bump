# Proving the mod works

A server that boots proves nothing about your mod. An empty registry and a
callback that never ran both produce a perfectly healthy server: no exception, no
warning, exit code 0.

Two lists in `tests.server` turn *"it booted"* into *"it worked"*.

| | What it asserts |
|---|---|
| [`expect`](#expect) | A phrase appears in the server log. |
| [`expect-count`](#expect-count) | A number your mod reports equals what is actually in your source. |

## What is already checked, without any config

Before your own expectations, every server test asserts:

1. **the mod is in the loader's inventory**, anchored on Fabric's indented
   `    - my-mod 26.2-1.1.0` line, not on any mention of the id, which also
   appears in classpath dumps and stack traces;
2. **the version we asked for is the version that booted**, since a stale build
   cache would otherwise turn the test green on the wrong version;
3. **no fatal signature**, from the loader's own list plus `fatal-extra`;
4. the server reached a running state and stopped cleanly.

`expect` and `expect-count` are what you add on top, about *your* mod.

---

## Patterns are globs

Not regexes. What you write is a phrase you expect in a log.

`Registered * potions` says what it means. `Registered [0-9]+ potions` requires
knowing that `+` is a quantifier and that `(` would have to be escaped.

The translation is `fnmatch.translate()`, the same glob `fnmatch`,
`pathlib.Path.glob` and your shell agree on:

```
*          anything, including nothing
?          exactly one character
[abc]      one of these        [!abc]   none of these
<count>    a number, captured (expect-count only)
```

`(`, `+`, `.` and `\` are **literal**.

### Unanchored, and line by line

Two things are layered on top of plain glob, and both matter:

**Unanchored.** A log line carries a timestamp and a logger prefix, so an
anchored pattern would never match. You write what you expect to see, not
`*[*]: Registered * potions`.

**Line by line.** Plain `translate()` lets `*` cross newlines, so
`Registered * potions` would happily match a `Registered foo` line followed by an
unrelated `bar potions` one, twenty lines later. Matching is per line, the way
`grep` does it.

### The `regex:` escape hatch

Where a glob genuinely cannot express it, `regex:` replaces `pattern:` and
`count-regex:` replaces `count-pattern:`. One or the other. Writing both is an
error naming the two keys.

```yaml
- regex: "Registered ([0-9]+) (?:potions|brews)"   # exactly one capture group
  count-source: src/main/java/com/example/MyMod.java
  count-regex: "=\\s*registerPotion\\("
```

---

## `expect`

A phrase that must appear in the server log.

```yaml
tests:
  server:
    expect:
      - pattern: "Brewing mixes registered"
        message: "the brewing callback never ran"
```

| Key | |
|---|---|
| `pattern` **or** `regex` | required, one of the two |
| `message` | optional, what the failure says |

`message` is what someone reads at 2am in a failure issue. `"the brewing callback
never ran"` beats `"pattern not found"`.

The natural source of these phrases is a log line your mod already prints at the
end of its initialisation. If it prints nothing, add one: a mod that says nothing
on startup cannot be proven to have started.

---

## `expect-count`

Your mod reports a number, and the expected value is **derived from your source**
rather than kept as a constant to maintain in two places.

```yaml
tests:
  server:
    expect-count:
      - pattern: "Registered <count> potions"     # <count> is where the number is
        count-source: src/main/java/com/example/MyMod.java
        count-pattern: "*= registerPotion(*"      # counted in the source
        message: "potion registry mismatch"
```

| Key | |
|---|---|
| `pattern` **or** `regex` | required. Must contain `<count>`, or one capture group for `regex`. |
| `count-source` | **required.** The file to count in. |
| `count-pattern` **or** `count-regex` | required, one of the two |
| `message` | optional |
| `comment-style` | optional: `c` (default), `hash`, `none` |

Adding a potion updates both sides on its own: your source now has one more
`registerPotion(` line, and your mod prints one more at runtime. Nothing to
maintain, and the assertion cannot rot into `>= 1`.

A mismatch names **both** numbers. And a `count-pattern` that no longer matches
anything says so, instead of passing on zero against zero.

### Comments are stripped before counting

`//` and `/* */`, **string aware**, so a `http://` inside a string literal is not
a comment and an escaped quote does not end the string. Block comments keep their
newlines, so line counting still works.

This is not pedantry. A pattern named in a Javadoc is a *mention*, not a
registration:

```java
/** Call {@code registerPotion(...)} to add one. */   // must NOT count
public static void registerAll() {
    LONG_NIGHT = registerPotion("long_night");        // counts
}
```

mc-bump's own fixture hit exactly that.

Use `comment-style: hash` for `#` languages, or `none` to count everything
including comments.

---

## `fatal-extra`

Globs added to the loader's own failure signatures. A line matching any of them
fails the test even if the server otherwise booted fine.

```yaml
tests:
  server:
    fatal-extra:
      - "*Failed to register * for my-mod*"
      - "*NoSuchMethodError*"
```

For Fabric the built-in list is already:

```
Mixin apply failed
Failed to load mod
Could not execute entrypoint
A potential solution has been determined
Incompatible mod set
```

`fatal-extra` adds to it, it never replaces it.

---

## A worked example

The fixture mod mc-bump tests itself with, cut down:

```yaml
loader: fabric

mod:
  id: extended-time-potion
  metadata: src/main/resources/fabric.mod.json

tests:
  server:
    expect:
      - pattern: "Extended Time Potion initialised"
        message: "the mod entrypoint never ran"
      - pattern: "Brewing mixes registered"
        message: "the brewing callback never ran"

    expect-count:
      - pattern: "Registered <count> potions"
        count-source: src/main/java/com/example/potion/Potions.java
        count-pattern: "*= registerPotion(*"
        message: "potion registry mismatch, a potion was added without being registered"
```

Three assertions, and between them they catch an entrypoint that did not run, a
callback that was never wired up, and a potion added to the class but never
registered. None of those three would redden a server that merely booted.

---

## Testing your expectations locally

You do not need GitHub, and you do not need to wait for a matrix:

```bash
python3 ../mc-bump/scripts/headless-server-test.py
```

It boots one server on the version currently in `gradle.properties`, writes
`server-test.log`, and runs exactly the same checks the CI runs. Iterate on your
patterns against a log you already have, then push once they hold.

See [CLI](CLI) for the flags and the environment variables.
