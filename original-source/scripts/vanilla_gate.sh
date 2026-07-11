#!/bin/bash
#
# Phase 0.6 — vanilla policy gate
#
# Plan v3 §0.6: 在 1 台 4a 上跑 1 task 验证 vanilla 行为符合精确定义：
#   (1) screenrecord 显示动画 scale 非零 (按钮 ripple / 窗口过渡可见)
#   (2) dumpsys accessibility: 我们的 event-based-wait listener 未注册,
#       但 AndroidWorld / DroidBotApp 必需 a11y service 仍在线
#   (3) tracked_sleep('ui_wait', 2.0) 路径 active
#   (4) 至少 1 smoke action 跑通 (不要求完整完成)
#
# 输出打包后由人工检查 + 推送; 用户回复 OK 才放行 B1 vanilla 全量.
#
# Usage:
#   bash vanilla_gate.sh [ADB_SERIAL] [TASK_NAME]
#   ADB_SERIAL: 默认 10.33.2.155:5555  (px2)
#   TASK_NAME : 默认 aw_simple_calendar_add_one_event (有滑入动画)

set -euo pipefail

ADB_SERIAL="${1:-10.33.2.155:5555}"
TASK_NAME="${2:-aw_SimpleCalendarAddOneEvent}"
PYTHON_BIN="${PYTHON_BIN:-/home/chris/miniconda3/envs/android_automatic_314/bin/python}"
VANILLA_GATE_TIMEOUT="${VANILLA_GATE_TIMEOUT:-180}"
TS=$(date +%Y%m%d_%H%M%S)
OUT_DIR="/android/aosp_host_working_dir/vanilla_gate_${TS}"
mkdir -p "$OUT_DIR"

echo "=== vanilla_gate.sh ==="
echo "  serial:  $ADB_SERIAL"
echo "  task:    $TASK_NAME"
echo "  out_dir: $OUT_DIR"
echo

adb connect "$ADB_SERIAL" 2>&1 | tail -1
sleep 1

# ------------------------------------------------------------
# Check (1): 动画 scale 应该非零
# ------------------------------------------------------------
echo "--- check (1) animation scales ---"
echo "[before]" >> "$OUT_DIR/animation_scales.txt"
for k in window_animation_scale transition_animation_scale animator_duration_scale; do
  v=$(adb -s "$ADB_SERIAL" shell "settings get global $k" 2>/dev/null | tr -d '\r')
  echo "  $k = $v"
  echo "$k=$v" >> "$OUT_DIR/animation_scales.txt"
done

# 强制设为 1.0 防止之前 --is_agent 跑过留下 0.0
adb -s "$ADB_SERIAL" shell '
  settings put global window_animation_scale 1.0
  settings put global transition_animation_scale 1.0
  settings put global animator_duration_scale 1.0
' 2>&1 | tail -3
sleep 1
echo "[after_force_1_0]" >> "$OUT_DIR/animation_scales.txt"
for k in window_animation_scale transition_animation_scale animator_duration_scale; do
  v=$(adb -s "$ADB_SERIAL" shell "settings get global $k" 2>/dev/null | tr -d '\r')
  echo "  $k = $v"
  echo "$k=$v" >> "$OUT_DIR/animation_scales.txt"
done

# ------------------------------------------------------------
# Check (2): dumpsys accessibility
# ------------------------------------------------------------
echo
echo "--- check (2) accessibility services ---"
adb -s "$ADB_SERIAL" shell dumpsys accessibility 2>&1 \
  > "$OUT_DIR/dumpsys_accessibility.txt"
grep -E "Service|enabled|installed" "$OUT_DIR/dumpsys_accessibility.txt" \
  | head -30 > "$OUT_DIR/a11y_services_summary.txt"
echo "  full dump in $OUT_DIR/dumpsys_accessibility.txt"
echo "  service summary in $OUT_DIR/a11y_services_summary.txt"

# ------------------------------------------------------------
# Check (3) + (4): screenrecord while running 1 vanilla smoke action
# ------------------------------------------------------------
echo
echo "--- check (3)+(4): start screenrecord + run vanilla smoke ---"

# 启 screenrecord (后台)
adb -s "$ADB_SERIAL" shell "screenrecord --time-limit 30 /sdcard/vanilla_${TS}.mp4" &
SR_PID=$!
sleep 1

# 跑 1 task 用 vanilla policy 精确开关组合
cd /android/androidtools/AutoDroid

# vanilla = 关 event-based wait + 关 --is_agent + 关 --report
# 用一个超短的 budget 限定为 smoke 不是完整跑
ENABLE_EVENT_BASED_WAIT=0 \
ENABLE_IS_AGENT_FLAG_FOR_INPUT=0 \
ENABLE_INJECTION_REPORT=0 \
BACKGROUND_INPUT_REPORT_RETRY=0 \
AUTODROID_KEEP_ANIMATIONS=1 \
timeout "$VANILLA_GATE_TIMEOUT" "$PYTHON_BIN" -m benchmark_runner \
  --jobs "$TASK_NAME" \
  --devices "$ADB_SERIAL" \
  --copies 1 \
  --extra_args "POLICY=vanilla FIXED_SLEEP_S=2.0 MAX_STEPS=3" \
  --base_dir "$OUT_DIR/benchmark" \
  2>&1 | tee "$OUT_DIR/smoke_run.log" || true

# 等 screenrecord 自然结束
wait "$SR_PID" 2>/dev/null || true

adb -s "$ADB_SERIAL" pull "/sdcard/vanilla_${TS}.mp4" "$OUT_DIR/vanilla.mp4" 2>&1 | tail -3
adb -s "$ADB_SERIAL" shell "rm /sdcard/vanilla_${TS}.mp4" 2>&1 | tail -1

# ------------------------------------------------------------
# 提取证据 — fixed sleep path active 的证据
# ------------------------------------------------------------
echo
echo "--- check (3) fixed sleep path evidence ---"
if grep -E "tracked_sleep.*ui_wait|ENABLE_EVENT_BASED_WAIT=0|AUTODROID_KEEP_ANIMATIONS=1|using fallback wait|Executing event" \
   "$OUT_DIR/smoke_run.log" > "$OUT_DIR/fixed_sleep_evidence.txt"; then
  echo "  evidence found ($(wc -l < $OUT_DIR/fixed_sleep_evidence.txt) lines)"
else
  echo "  WARNING: no 'fallback wait' / 'tracked_sleep' lines in log"
fi

# 提取证据 — smoke action 至少跑通 1 步
echo
echo "--- check (4) at least 1 smoke action completed ---"
if grep -E "^\[.*step.*1.*\]|Executing event|action.*executed|input tap|input.*--is_agent" \
   "$OUT_DIR/smoke_run.log" > "$OUT_DIR/smoke_action_evidence.txt"; then
  echo "  evidence found ($(wc -l < $OUT_DIR/smoke_action_evidence.txt) lines)"
else
  echo "  WARNING: no smoke action evidence"
fi

# ------------------------------------------------------------
# 打包 + 准备推送
# ------------------------------------------------------------
echo
echo "--- bundling for user review ---"
cd "$OUT_DIR"
ls -la
tar czf "/tmp/vanilla_gate_${TS}.tgz" \
  animation_scales.txt \
  a11y_services_summary.txt \
  fixed_sleep_evidence.txt \
  smoke_action_evidence.txt \
  smoke_run.log
echo
echo "Bundle: /tmp/vanilla_gate_${TS}.tgz  (text only, $(du -h /tmp/vanilla_gate_${TS}.tgz | cut -f1))"
echo "Video:  $OUT_DIR/vanilla.mp4"
echo
echo "Next: send PushNotification with bundle path + video path"
echo "      and wait for user OK before launching B1 vanilla full batch."
