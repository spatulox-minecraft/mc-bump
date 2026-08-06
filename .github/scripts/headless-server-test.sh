#!/usr/bin/env bash
#
# Launches a headless dedicated Minecraft server with the mod and checks that it
# reaches "Done" without crashing. Usable locally:
#
#   bash .github/scripts/headless-server-test.sh
#
# Environment variables:
#   RUN_DIR          loom run directory                    (default: run)
#   LOG              log file produced                     (default: server-test.log)
#   BOOT_TIMEOUT     seconds before giving up on startup   (default: 900)
#   STOP_TIMEOUT     seconds before force killing the JVM  (default: 60)
#   EXPECTED_POTIONS potion count expected in the log      (default: counted in the source)
#   EXPECTED_MC      Minecraft version that must boot      (default: from gradle.properties)
#   GRADLE_ARGS      extra gradle arguments, e.g.          (default: none)
#                    "-Pminecraft_version=26.1"
#   LEVEL_NAME       world name, wiped before each run     (default: ci-smoke-test)
#
set -euo pipefail

RUN_DIR="${RUN_DIR:-run}"
LOG="${LOG:-server-test.log}"
BOOT_TIMEOUT="${BOOT_TIMEOUT:-900}"
STOP_TIMEOUT="${STOP_TIMEOUT:-60}"
GRADLE_ARGS="${GRADLE_ARGS:-}"
LEVEL_NAME="${LEVEL_NAME:-ci-smoke-test}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Expected potion count, derived from the source so it stays correct when potions
# are added (rather than a constant to maintain in two places).
#
# "|| true" is required, not defensive: grep -c exits 1 when it counts zero, and
# under set -e that kills the script right here, on an assignment, with no
# message whatsoever. Counting zero is a real answer — the registrations left the
# source, or the pattern stopped matching them — and it deserves to be said out
# loud rather than to look like an infrastructure glitch.
MOD_SOURCE="src/main/java/com/spatulox/ExtendedTimePotion.java"
if [ -z "${EXPECTED_POTIONS:-}" ]; then
    EXPECTED_POTIONS="$(grep -c '= registerPotion(' "$MOD_SOURCE" || true)"
fi
if ! [ "${EXPECTED_POTIONS:-0}" -gt 0 ] 2>/dev/null; then
    echo "=== FAILED: expected potion count is '${EXPECTED_POTIONS:-}' ==="
    echo "No '= registerPotion(' line found in $MOD_SOURCE."
    echo "Either the potions were removed, or the registration was rewritten and"
    echo "this script no longer knows how to count it. Set EXPECTED_POTIONS to"
    echo "override."
    exit 1
fi

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

echo "==> Starting the server (timeout ${BOOT_TIMEOUT}s)...${GRADLE_ARGS:+ [$GRADLE_ARGS]}"
# shellcheck disable=SC2086 # GRADLE_ARGS is a deliberate list of gradle flags
./gradlew runServer $GRADLE_ARGS --no-daemon --console=plain --stacktrace \
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

# --- log checks -----------------------------------------------------------
if ! grep -q 'extended-time-potion' "$LOG"; then
    fail "the extended-time-potion mod does not appear in the loading log"
fi

# The version actually booted must match the one asked for. Without this guard a
# stale loom cache, a concurrent edit of the file or an ignored -Pminecraft_version
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

# Targeted grep: Minecraft logs plenty of harmless WARNs, we only look for real
# failure signatures.
FATAL_PATTERNS='Mixin apply failed|Failed to load mod|Could not execute entrypoint|A potential solution has been determined|Incompatible mod set'
if grep -qE "$FATAL_PATTERNS" "$LOG"; then
    echo "--- fatal lines detected ---"
    grep -nE "$FATAL_PATTERNS" "$LOG" || true
    fail "fatal error detected in the log"
fi

# --- did the mod actually do its job? -------------------------------------
# "the server starts" does not prove the mod works: an empty registry or a brewing
# callback that never ran would both go unnoticed. Those two markers are emitted
# by ExtendedTimePotion.onInitialize().
if ! grep -q 'Brewing mixes registered' "$LOG"; then
    fail "the Fabric API brewing callback never ran (FabricPotionBrewingBuilder broken?)"
fi

POTIONS="$(sed -n 's/.*Registered \([0-9]\{1,\}\) potions.*/\1/p' "$LOG" | head -n 1)"
if [ -z "$POTIONS" ]; then
    fail "the mod did not report how many potions it registered"
fi
echo "==> Potions registered: $POTIONS (expected: $EXPECTED_POTIONS)"
if [ "$POTIONS" -ne "$EXPECTED_POTIONS" ]; then
    fail "$POTIONS potions registered instead of $EXPECTED_POTIONS"
fi

# --- clean shutdown -------------------------------------------------------
echo "==> Sending the stop command..."
echo "stop" >&3 || true

waited=0
while [ "$waited" -lt "$STOP_TIMEOUT" ] && kill -0 "$GRADLE_PID" 2>/dev/null; do
    sleep 2
    waited=$((waited + 2))
done

if kill -0 "$GRADLE_PID" 2>/dev/null; then
    # loom does not always forward System.in to the server. The server started
    # without crashing, which is the point of this test: kill it and move on.
    echo "==> The server did not answer \"stop\" within ${STOP_TIMEOUT}s, forcing shutdown."
    kill "$GRADLE_PID" 2>/dev/null || true
    wait "$GRADLE_PID" 2>/dev/null || true
else
    wait "$GRADLE_PID" 2>/dev/null || true
    echo "==> Server stopped cleanly."
fi

GRADLE_PID=""
echo ""
echo "=== OK: the server started with the mod and no fatal error ==="
grep -E 'Done \([0-9.]+s\)' "$LOG" | tail -n 1
