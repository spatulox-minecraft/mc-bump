#!/usr/bin/env bash
#
# Runs the version matrix, and escalates the frozen dependencies when it fails.
# Usable locally:
#
#   bash .github/scripts/test-with-escalation.sh
#
# Why an escalation ladder. A Minecraft update used to bump Fabric Loader and
# Fabric API at the same time, so a red matrix had three suspects and the loader
# and the API each change the behaviour of EVERY sub-version at once. Those two
# are now frozen by scripts/update-mc-version.py, and only move here, as a
# reaction to a failure, one at a time:
#
#   matrix with the frozen dependencies
#     KO -> bump fabric-api    -> whole matrix again
#             KO -> bump loader -> whole matrix again
#                     KO -> exit 1, the mod is really broken
#
# Each rung re-runs the WHOLE matrix, not just the version that failed: a newer
# Fabric API is exactly the kind of change that fixes the newest version while
# breaking an older one of the same series, and depends.minecraft promises them
# all.
#
# fabric.mod.json is not touched here. A bump is a hypothesis; the dependency
# floor is only engraved by --mark-supported, once the matrix has proven it.
#
# Environment variables:
#   ESCALATION_FILE  report of the bumps applied  (default: test-escalation.txt)
#   plus everything test-matrix.sh accepts (MC_VERSIONS, STATUS_FILE...) and
#   everything headless-server-test.sh accepts (BOOT_TIMEOUT, LOG...)
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

UPDATER="scripts/update-mc-version.py"

# What was escalated, one "<gradle key> <from> <to>" per line. The PR body reads
# this to say why the dependency diff is bigger than a Minecraft bump. Truncated
# up front, like STATUS_FILE in test-matrix.sh, so a re-run does not accumulate.
ESCALATION_FILE="${ESCALATION_FILE:-test-escalation.txt}"
: > "$ESCALATION_FILE"

# Exit code 3 of the updater: already on the newest version. Not a failure, but
# this rung cannot change anything, so re-running the matrix would spend twenty
# minutes reproducing the same red.
EXIT_ALREADY_LATEST=3

run_matrix() {
    echo ""
    echo "#################### Matrix: $1 ####################"
    bash .github/scripts/test-matrix.sh
}

prop() {
    sed -n "s/^$1=//p" gradle.properties | head -n 1 | tr -d '[:space:]'
}

if run_matrix "frozen dependencies"; then
    echo ""
    echo "=== OK: no escalation needed ==="
    exit 0
fi

echo ""
echo "==> The matrix failed with the frozen dependencies, escalating."

# Ordered from the least to the most invasive: Fabric API is a normal library the
# mod calls into, the loader is the thing that runs every mod on the server.
for STEP in "fabric_api_version --bump-fabric-api" "loader_version --bump-loader"; do
    read -r KEY FLAG <<< "$STEP"
    BEFORE="$(prop "$KEY")"

    echo ""
    echo "==> Escalation: $FLAG (currently $KEY=$BEFORE)"
    python3 "$UPDATER" "$FLAG"
    rc=$?

    if [ "$rc" -eq "$EXIT_ALREADY_LATEST" ]; then
        echo "==> $KEY is already the newest available, skipping this step."
        continue
    fi
    if [ "$rc" -ne 0 ]; then
        echo "=== FAILED: $FLAG could not resolve a version ==="
        exit 1
    fi

    AFTER="$(prop "$KEY")"
    echo "$KEY $BEFORE $AFTER" >> "$ESCALATION_FILE"

    if run_matrix "$KEY=$AFTER"; then
        echo ""
        echo "=== OK: fixed by $KEY $BEFORE -> $AFTER ==="
        exit 0
    fi

    echo "==> Still failing with $KEY=$AFTER."
done

echo ""
echo "=== FAILED: the matrix is still red after every escalation step ==="
if [ -s "$ESCALATION_FILE" ]; then
    echo "Bumps applied and kept, as a starting point for a manual fix:"
    while read -r KEY BEFORE AFTER; do
        echo "  $KEY: $BEFORE -> $AFTER"
    done < "$ESCALATION_FILE"
fi
exit 1
