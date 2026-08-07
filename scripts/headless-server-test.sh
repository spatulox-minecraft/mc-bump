#!/usr/bin/env bash
#
# Launches a headless dedicated Minecraft server with the mod and checks that it
# reaches "Done" without crashing, then that the mod actually did its job.
# Usable locally, from the mod repository:
#
#   bash /path/to/mc-bump/scripts/headless-server-test.sh
#
# What is asserted, and why "the server starts" is not enough: an empty registry
# or a callback that never ran would both boot a perfectly healthy server. The
# tests.server.expect / expect-count lists of .github/mc-bump.yml are what turns
# "it booted" into "it worked".
#
# Environment variables (all optional, config provides the defaults):
#   RUN_DIR          run directory                          (default: run)
#   LOG              log file produced                      (default: server-test.log)
#   BOOT_TIMEOUT     seconds before giving up on startup    (config)
#   STOP_TIMEOUT     seconds before force killing the JVM   (config)
#   EXPECTED_MC      Minecraft version that must boot       (gradle.properties)
#   GRADLE_ARGS      extra gradle arguments                 (default: none)
#   LEVEL_NAME       world name, wiped before each run      (default: ci-smoke-test)
#   MOD_ROOT         the mod repository                     (walked up from the cwd)
#
set -euo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
mcbump_load_config

RUN_DIR="${RUN_DIR:-run}"
LOG="${LOG:-server-test.log}"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-$MCBUMP_TESTS_SERVER_BOOT_TIMEOUT}"
STOP_TIMEOUT="${STOP_TIMEOUT:-$MCBUMP_TESTS_SERVER_STOP_TIMEOUT}"
GRADLE_ARGS="${GRADLE_ARGS:-}"
LEVEL_NAME="${LEVEL_NAME:-ci-smoke-test}"

FIFO_DIR="$(mktemp -d)"
FIFO="$FIFO_DIR/server-stdin"
GRADLE_PID=""

cleanup() {
    if [ -n "$GRADLE_PID" ] && kill -0 "$GRADLE_PID" 2>/dev/null; then
        kill "$GRADLE_PID" 2>/dev/null || true
    fi
    exec 3>&- 2>/dev/null || true
    rm -rf "$FIFO_DIR"
}
trap cleanup EXIT

fail() {
    echo ""
    echo "=== FAILED: $* ==="
    echo "--- last 200 lines of $LOG ---"
    tail -n 200 "$LOG" 2>/dev/null || echo "(no log)"
    exit 1
}

# --- run directory setup --------------------------------------------------
mkdir -p "$RUN_DIR"
printf 'eula=true\n' > "$RUN_DIR/eula.txt"

# The world is wiped rather than reused: a save written by a newer Minecraft
# refuses to load on an older one, which would break the version matrix the
# moment it tests an older version after a newer one.
rm -rf "${RUN_DIR:?}/$LEVEL_NAME"

# flat world + watchdog disabled: fast startup, no false positive on a slow CI
# runner.
cat > "$RUN_DIR/server.properties" <<PROPS
online-mode=false
level-type=minecraft\:flat
level-name=$LEVEL_NAME
max-tick-time=-1
view-distance=4
simulation-distance=4
sync-chunk-writes=false
spawn-protection=0
PROPS

rm -f "$LOG"
mkfifo "$FIFO"
# Opened read-write (3<>) and not write-only (3>): opening a FIFO write-only
# BLOCKS until a reader shows up, and the reader (gradle) is only started
# afterwards. The <> mode never blocks, and it keeps the write end open so the
# server does not see an immediate EOF on its stdin.
exec 3<> "$FIFO"

echo "==> Starting the server via ${MCBUMP_SERVER_TASK} (timeout ${BOOT_TIMEOUT}s)...${GRADLE_ARGS:+ [$GRADLE_ARGS]}"
# shellcheck disable=SC2086 # GRADLE_ARGS is a deliberate list of gradle flags
./gradlew "$MCBUMP_SERVER_TASK" $GRADLE_ARGS --no-daemon --console=plain --stacktrace \
    < "$FIFO" > "$LOG" 2>&1 &
GRADLE_PID=$!

# --- wait for startup -----------------------------------------------------
started=0
elapsed=0
while [ "$elapsed" -lt "$BOOT_TIMEOUT" ]; do
    if grep -qE 'Done \([0-9.]+s\)' "$LOG" 2>/dev/null; then
        started=1
        break
    fi
    if ! kill -0 "$GRADLE_PID" 2>/dev/null; then
        # the process died before printing "Done"
        break
    fi
    sleep 2
    elapsed=$((elapsed + 2))
done

if [ "$started" -ne 1 ]; then
    if kill -0 "$GRADLE_PID" 2>/dev/null; then
        fail "the server did not reach \"Done\" within ${BOOT_TIMEOUT}s"
    else
        fail "the server stopped before it finished starting"
    fi
fi

echo "==> Server started."

# --- was the mod actually loaded? -----------------------------------------
# The loader's own inventory line, not any mention of the id: a mod id appears in
# every classpath dump, so grepping it bare passes on a mod the loader rejected.
if ! grep -qE "$MCBUMP_MOD_LOADED_PATTERN" "$LOG"; then
    fail "$MCBUMP_MOD_ID does not appear in the loader's list of loaded mods"
fi

# The version actually booted must match the one asked for. Without this guard a
# stale build cache, a concurrent edit of the file or an ignored -Pminecraft_version
# would turn the test green on the wrong Minecraft version.
EXPECTED_MC="${EXPECTED_MC:-$(sed -n 's/^minecraft_version=//p' gradle.properties | head -n 1 | tr -d '[:space:]')}"
BOOTED_MC="$(sed -n 's/.*Starting minecraft server version \(.*\)$/\1/p' "$LOG" | head -n 1 | tr -d '[:space:]')"
echo "==> Expected version: ${EXPECTED_MC:-?} | booted version: ${BOOTED_MC:-?}"
if [ -z "$BOOTED_MC" ]; then
    fail "cannot read the booted version from the log"
fi
if [ "$BOOTED_MC" != "$EXPECTED_MC" ]; then
    fail "the server booted Minecraft $BOOTED_MC while gradle.properties asks for $EXPECTED_MC"
fi

# --- fatal signatures -----------------------------------------------------
# Targeted: Minecraft logs plenty of harmless WARNs. The list is the loader's own
# signatures plus tests.server.fatal-extra.
if grep -qE "$MCBUMP_FATAL_PATTERNS" "$LOG"; then
    echo "--- fatal lines detected ---"
    grep -nE "$MCBUMP_FATAL_PATTERNS" "$LOG" || true
    fail "fatal error detected in the log"
fi

# --- did the mod do its job? ----------------------------------------------
# tests.server.expect: a phrase the mod is expected to print. One per line,
# "<pattern>\t<message>".
while IFS=$'\t' read -r pattern message; do
    [ -n "$pattern" ] || continue
    if ! grep -qE "$pattern" "$LOG"; then
        fail "${message:-expected pattern not found}: /$pattern/ never appeared in the log"
    fi
    echo "==> Found: /$pattern/"
done < <(mcbump_records "${MCBUMP_TESTS_SERVER_EXPECT:-}")

# tests.server.expect-count: the mod reports a number, and that number is derived
# from the source rather than kept as a constant to maintain in two places.
# "<pattern>\t<count source>\t<count pattern>\t<message>".
while IFS=$'\t' read -r pattern count_source count_pattern message; do
    [ -n "$pattern" ] || continue

    if [ ! -f "$count_source" ]; then
        fail "${message:-count check}: count-source '$count_source' does not exist"
    fi

    # "|| true" is required, not defensive: grep -c exits 1 when it counts zero,
    # and under set -e that kills the script right here, on an assignment, with no
    # message whatsoever. Counting zero is a real answer — the registrations left
    # the source, or the pattern stopped matching them — and it deserves to be
    # said out loud rather than to look like an infrastructure glitch.
    expected="$(grep -cF -- "$count_pattern" "$count_source" || true)"
    if ! [ "${expected:-0}" -gt 0 ] 2>/dev/null; then
        fail "${message:-count check}: '$count_pattern' never appears in $count_source, so there is nothing to expect. Either the feature was removed, or the source was rewritten and mc-bump.yml no longer knows how to count it."
    fi

    # sed rather than grep -o: the config pattern carries ONE capture group, and
    # only sed can give back the group instead of the whole match.
    actual="$(sed -nE "s/.*${pattern}.*/\1/p" "$LOG" | head -n 1)"
    if [ -z "$actual" ]; then
        fail "${message:-count check}: the mod never reported /$pattern/"
    fi

    echo "==> Counted: $actual (expected: $expected) for /$pattern/"
    if [ "$actual" != "$expected" ]; then
        fail "${message:-count mismatch}: $actual instead of $expected"
    fi
done < <(mcbump_records "${MCBUMP_TESTS_SERVER_EXPECT_COUNT:-}")

# --- clean shutdown -------------------------------------------------------
echo "==> Sending the stop command..."
echo "stop" >&3 || true

waited=0
while [ "$waited" -lt "$STOP_TIMEOUT" ] && kill -0 "$GRADLE_PID" 2>/dev/null; do
    sleep 2
    waited=$((waited + 2))
done

if kill -0 "$GRADLE_PID" 2>/dev/null; then
    # The build tool does not always forward System.in to the server. The server
    # started without crashing, which is the point of this test: kill it and move
    # on.
    echo "==> The server did not answer \"stop\" within ${STOP_TIMEOUT}s, forcing shutdown."
    kill "$GRADLE_PID" 2>/dev/null || true
    wait "$GRADLE_PID" 2>/dev/null || true
else
    wait "$GRADLE_PID" 2>/dev/null || true
    echo "==> Server stopped cleanly."
fi

GRADLE_PID=""
echo ""
echo "=== OK: the server started with $MCBUMP_MOD_ID and no fatal error ==="
grep -E 'Done \([0-9.]+s\)' "$LOG" | tail -n 1
