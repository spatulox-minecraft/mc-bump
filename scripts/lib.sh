#!/usr/bin/env bash
#
# Sourced by every mc-bump shell script. Locates mc-bump and the mod repository,
# loads .github/mc-bump.yml, and moves to the mod root.
#
# After sourcing, every config value is available as MCBUMP_<UPPER_SNAKE>, e.g.
# MCBUMP_MOD_ID, MCBUMP_TESTS_SERVER_BOOT_TIMEOUT, MCBUMP_SERVER_TASK. Records
# (the expect lists) are TAB separated fields, one per line.
#
# Environment variables:
#   MOD_ROOT   the mod repository (default: walked up from the cwd)
#

# shellcheck disable=SC2034 # consumed by the scripts that source this file
MCBUMP_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mcbump_python() {
    PYTHONPATH="$MCBUMP_HOME${PYTHONPATH:+:$PYTHONPATH}" python3 "$@"
}

mcbump_load_config() {
    local exports
    if ! exports="$(mcbump_python -m lib.config --sh)"; then
        echo "=== FAILED: cannot read .github/mc-bump.yml ==="
        return 1
    fi
    eval "$exports"
    cd "$MCBUMP_MOD_ROOT" || return 1
}

# Print a line per record of a TAB separated config list. Empty input yields
# nothing, which is what an empty `expect:` list should do.
mcbump_records() {
    local value="$1"
    [ -n "$value" ] || return 0
    printf '%s\n' "$value"
}
