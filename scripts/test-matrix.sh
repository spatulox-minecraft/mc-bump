#!/usr/bin/env bash
#
# Builds the mod and boots a dedicated server for EVERY Minecraft version the mod
# claims, not just the newest one. Usable locally, from the mod repository:
#
#   bash /path/to/mc-bump/scripts/test-matrix.sh
#
# Why a matrix. An update bumps the loader API and widens the compatibility range
# over the whole series (">=26.1 <=26.1.2"). Testing only 26.1.2 proves nothing
# about 26.1 and 26.1.1 running with THAT API, yet those are versions the jar
# promises to load on and that the stores list. Every claimed version is
# therefore built and booted with the resolved dependencies, which is exactly the
# combination that ships.
#
# In CI this loop is usually replaced by a GitHub job matrix, which runs the same
# versions in parallel; this script stays the local entry point and the sequential
# path the escalation ladder needs.
#
# Environment variables:
#   MC_VERSIONS  space separated list to test  (default: the claimed versions)
#   STATUS_FILE  where the outcome is recorded (default: test-matrix-status.txt)
#   plus everything headless-server-test.sh accepts (BOOT_TIMEOUT, LOG...)
#
set -uo pipefail

# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
mcbump_load_config

HERE="$MCBUMP_HOME/scripts"

if [ -z "${MC_VERSIONS:-}" ]; then
    MC_VERSIONS="$(mcbump_python "$HERE/mc-bump.py" --list-test-versions)" || {
        echo "=== FAILED: cannot list the versions to test ==="
        exit 1
    }
fi

read -r -a VERSIONS <<< "$(echo "$MC_VERSIONS" | tr '\n' ' ')"
if [ "${#VERSIONS[@]}" -eq 0 ]; then
    echo "=== FAILED: no Minecraft version to test ==="
    exit 1
fi

echo "############ Version matrix: ${VERSIONS[*]} ############"

# What was ACTUALLY tested, one "<version> <ok|build|server>" per line. The PR
# body reads this rather than recomputing the list: by then --revert-compat may
# have restored the previous, shorter list of supported versions.
STATUS_FILE="${STATUS_FILE:-test-matrix-status.txt}"
: > "$STATUS_FILE"

FAILED=()
for MC in "${VERSIONS[@]}"; do
    echo ""
    echo "======== Minecraft $MC ========"
    START=$SECONDS

    if ! ./gradlew build "-Pminecraft_version=$MC" --stacktrace --console=plain; then
        echo "=== FAILED: build on Minecraft $MC ==="
        echo "$MC build" >> "$STATUS_FILE"
        FAILED+=("$MC (build)")
        continue
    fi

    # a per-version log, so a failure in the middle of the matrix keeps the
    # evidence of the runs around it
    boot() {
        LOG="server-test-$MC.log" \
        EXPECTED_MC="$MC" \
        GRADLE_ARGS="-Pminecraft_version=$MC" \
        bash "$HERE/headless-server-test.sh"
    }

    if ! boot; then
        # The build tool resolves mods on virtual threads, and they can deadlock
        # against each other on the JVM-wide Cleaner monitor while setting
        # Minecraft up. That hang never reaches the point where Minecraft itself
        # starts, which is exactly how it is told apart from the mod being broken:
        # a mod that fails to load DOES get that far. Only the hang is worth
        # retrying.
        if grep -q 'Starting minecraft server version' "server-test-$MC.log" 2>/dev/null; then
            echo "=== FAILED: headless server on Minecraft $MC ==="
            echo "$MC server" >> "$STATUS_FILE"
            FAILED+=("$MC (server)")
            continue
        fi

        echo "==> Minecraft never started on $MC (build setup hang?), retrying once"
        if ! boot; then
            echo "=== FAILED: headless server on Minecraft $MC (twice) ==="
            echo "$MC server" >> "$STATUS_FILE"
            FAILED+=("$MC (server)")
            continue
        fi
    fi

    echo "$MC ok" >> "$STATUS_FILE"
    echo "======== Minecraft $MC OK ($((SECONDS - START))s) ========"
done

echo ""
if [ "${#FAILED[@]}" -ne 0 ]; then
    echo "=== FAILED: ${#FAILED[@]}/${#VERSIONS[@]} version(s) broken: ${FAILED[*]} ==="
    exit 1
fi

echo "=== OK: all ${#VERSIONS[@]} claimed version(s) build and boot: ${VERSIONS[*]} ==="
