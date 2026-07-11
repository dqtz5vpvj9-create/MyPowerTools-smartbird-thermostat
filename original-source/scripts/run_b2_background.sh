#!/bin/bash
#
# Phase 0.3 — B2 background scenario launcher
#
# Wraps the existing run_background_androidworld.py harness (which already
# supports --devices, --jobs, --foreground-apk, --foreground-interval) to
# launch two concurrent sub-batches on pro0 CVDs:
#   - sub-group 1 (3 CVDs): wait-and-verify-bg (existing mainline)
#   - sub-group 2 (3 CVDs): Sys-bg with BACKGROUND_INPUT_REPORT_RETRY=1
#
# Both groups share the same JankBench foreground worker on display 0.
# Each task wraps EnergyProfiler and writes retry diagnostics
# (5 fields from injection_report.RetryDiagnostics) per task.
#
# Usage:
#   bash run_b2_background.sh [STABLE_DB_DIR] [JOBS_FILE]
#     STABLE_DB_DIR   default: stable 3-run postconditions DB directory
#     JOBS_FILE       default: AndroidWorld 116-task default
#
# Plan v3 §B2 / §0.3 strict gate rules:
#   - BACKGROUND_INPUT_REPORT_RETRY=1 ONLY in Sys-bg sub-group
#   - W&V-bg sub-group keeps mainline behavior
#   - B1 / B3 must NEVER call this script

set -euo pipefail

STABLE_DB="${1:-/android/androidtools/AutoDroid/data/postconditions_db_ai2local_20260428_190403}"
JOBS_FILE="${2:-}"
TS=$(date +%Y%m%d_%H%M%S)
B2_ATTEMPTS="${B2_ATTEMPTS:-3}"

AUTODROID_ROOT="${AUTODROID_ROOT:-/android/androidtools/AutoDroid}"
PYTHON_BIN="${PYTHON_BIN:-/home/chris/miniconda3/envs/android_automatic_314/bin/python}"
HARNESS="${AUTODROID_ROOT}/benchmarks/background_agent_test/run_background_androidworld.py"
RESULTS_ROOT="${RESULTS_ROOT:-/android/aosp_host_working_dir}"

# 6 pro0 CVDs split 3+3.
WV_DEVICES=("pro0:6520" "pro0:6521" "pro0:6522")
SYS_DEVICES=("pro0:6523" "pro0:6524" "pro0:6525")

echo "=== B2 background launcher ==="
echo "  TS:                        $TS"
echo "  ACTION_DB (stable 3-run):  $STABLE_DB"
echo "  attempts per task:          $B2_ATTEMPTS"
echo "  Jobs file:                 ${JOBS_FILE:-<AndroidWorld default 116>}"
echo "  W&V-bg sub-group (3 CVD):  ${WV_DEVICES[*]}  (NO BACKGROUND_INPUT_REPORT_RETRY)"
echo "  Sys-bg sub-group (3 CVD):  ${SYS_DEVICES[*]}  (BACKGROUND_INPUT_REPORT_RETRY=1)"
echo

if [[ ! -d "$STABLE_DB/post_conditions" ]]; then
  echo "ERROR: stable DB post_conditions dir not found at $STABLE_DB/post_conditions" >&2
  echo "       supply path as arg 1, e.g.:" >&2
  echo "       bash run_b2_background.sh /path/to/stable_postconditions_db" >&2
  exit 1
fi

if [[ ! -x "$HARNESS" ]] && [[ ! -f "$HARNESS" ]]; then
  echo "ERROR: harness not found at $HARNESS" >&2
  exit 1
fi

JOBS_ARG=()
if [[ -n "$JOBS_FILE" ]]; then
  if [[ ! -f "$JOBS_FILE" ]]; then
    echo "ERROR: jobs file not found: $JOBS_FILE" >&2
    exit 1
  fi
  JOBS_ARG=(--jobs-file "$JOBS_FILE")
fi

# ---- launch sub-group 1 in tmux: W&V-bg ----
WV_SESSION="b2_wv_${TS}"
WV_LOG="${RESULTS_ROOT}/b2_${TS}_wv_log.txt"

# Critical: W&V-bg must NOT have BACKGROUND_INPUT_REPORT_RETRY set.
WV_CMD=(
  env -u BACKGROUND_INPUT_REPORT_RETRY
  POLICY=wait_and_verify
  ENABLE_EVENT_BASED_WAIT=0
  ENABLE_IS_AGENT_FLAG_FOR_INPUT=1
  ENABLE_INJECTION_REPORT=1
  "$PYTHON_BIN" "$HARNESS"
    --devices "${WV_DEVICES[@]}"
    --run-name "b2_wv_${TS}"
    --results-root "$RESULTS_ROOT"
    --attempts "$B2_ATTEMPTS"
    "${JOBS_ARG[@]}"
)

# ---- launch sub-group 2 in tmux: Sys-bg ----
SYS_SESSION="b2_sys_${TS}"
SYS_LOG="${RESULTS_ROOT}/b2_${TS}_sys_log.txt"

SYS_CMD=(
  POLICY=sys
  POSTCONDITIONS_SOURCE_DIR="$STABLE_DB"
  ENABLE_EVENT_BASED_WAIT=1
  ENABLE_IS_AGENT_FLAG_FOR_INPUT=1
  ENABLE_INJECTION_REPORT=1
  BACKGROUND_INPUT_REPORT_RETRY=1
  "$PYTHON_BIN" "$HARNESS"
    --devices "${SYS_DEVICES[@]}"
    --run-name "b2_sys_${TS}"
    --results-root "$RESULTS_ROOT"
    --attempts "$B2_ATTEMPTS"
    "${JOBS_ARG[@]}"
)

echo "--- sub-group 1: W&V-bg ---"
echo "  tmux session: $WV_SESSION"
echo "  log:          $WV_LOG"
echo "  command:"
printf '    %q ' "${WV_CMD[@]}"; echo
echo

echo "--- sub-group 2: Sys-bg ---"
echo "  tmux session: $SYS_SESSION"
echo "  log:          $SYS_LOG"
echo "  command:"
printf '    %q ' "${SYS_CMD[@]}"; echo
echo

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "DRY_RUN=1, exiting without executing."
  exit 0
fi

cd "$AUTODROID_ROOT"

# Launch both sub-groups in parallel under tmux.
tmux new-session -d -s "$WV_SESSION" "${WV_CMD[*]} 2>&1 | tee $WV_LOG; echo B2_WV_EXIT=\$? >> $WV_LOG"
tmux new-session -d -s "$SYS_SESSION" "${SYS_CMD[*]} 2>&1 | tee $SYS_LOG; echo B2_SYS_EXIT=\$? >> $SYS_LOG"

echo "Both sub-groups launched."
echo "  monitor W&V:  tmux attach -t $WV_SESSION    or    tail -f $WV_LOG"
echo "  monitor Sys:  tmux attach -t $SYS_SESSION   or    tail -f $SYS_LOG"
echo "  cleanup:      tmux kill-session -t $WV_SESSION; tmux kill-session -t $SYS_SESSION"
