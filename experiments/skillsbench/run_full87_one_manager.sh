#!/usr/bin/env bash
# ONE/WSL wrapper for keeping the frozen full87 batch alive across SSH sessions.

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
DEFAULT_RUNS_ROOT="${SCRIPT_DIR}/runs/model-c0-c1-full87/paper-cli-full87-20260710"
RUNS_ROOT="${1:-${DEFAULT_RUNS_ROOT}}"
MANIFEST="${2:-${RUNS_ROOT}/control/experiment-manifest.json}"
STATE="${3:-${RUNS_ROOT}/control/state.json}"
CONTROL_DIR="${RUNS_ROOT}/control"
LOG="${CONTROL_DIR}/manager.log"
PID_FILE="${CONTROL_DIR}/manager.pid"
STATUS_FILE="${CONTROL_DIR}/manager-status.txt"

mkdir -p "${CONTROL_DIR}"
exec 9>"${CONTROL_DIR}/manager.lock"
if ! flock -n 9; then
    printf 'A full87 manager already holds %s\n' "${CONTROL_DIR}/manager.lock" >&2
    exit 73
fi

cleanup() {
    local exit_code=$?
    trap - EXIT INT TERM
    printf 'stopped_at_utc=%s\nexit_code=%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${exit_code}" > "${STATUS_FILE}"
    rm -f "${PID_FILE}"
    exit "${exit_code}"
}
trap cleanup EXIT INT TERM

printf '%s\n' "$$" > "${PID_FILE}"
printf 'started_at_utc=%s\npid=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$" > "${STATUS_FILE}"

cd "${REPO_ROOT}"
{
    printf 'START %s pid=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$"
    python3 "${SCRIPT_DIR}/run_full87_c0_c1_batch.py" \
        --manifest "${MANIFEST}" \
        --runs-root "${RUNS_ROOT}" \
        --state "${STATE}"
    exit_code=$?
    printf 'STOP %s exit_code=%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${exit_code}"
    exit "${exit_code}"
} >> "${LOG}" 2>&1
