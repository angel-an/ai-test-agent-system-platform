#!/usr/bin/env bash
# 监控 Agent 执行状态（不 set -e，避免单个命令失败导致退出）

LOG_FILE="agent_monitor.log"
SS_DIR="agent_screenshots"
mkdir -p "$SS_DIR"

echo "$(date -Iseconds) monitor_start" | tee -a "$LOG_FILE"

last_task=""
last_ss=0
iteration=0
max_iterations=150

for ((i=1; i<=max_iterations; i++)); do
  iteration=$i
  sleep 5

  # 捕获状态
  state=$(playwright-cli --raw run-code --filename=capture_state.js 2>/dev/null | tail -n 1 | sed 's/^"//;s/"$//')
  ts=$(date -Iseconds)

  # 决定是否截图
  task_changed="false"
  if [[ "$state" != "$last_task" ]]; then
    task_changed="true"
  fi
  now_ts=$(date +%s)
  time_for_ss=false
  if (( now_ts - last_ss > 30 )); then
    time_for_ss=true
  fi

  ss_path=""
  if [[ "$task_changed" == "true" ]] || [[ "$state" == *"ERROR:"*"连接已断开"* ]] || [[ "$state" == *"PAUSED:true"* ]] || [[ "$state" == *"DONE:true"* ]] || [[ "$time_for_ss" == "true" ]]; then
    ss_path="$SS_DIR/step_$(printf '%03d' $iteration).png"
    playwright-cli screenshot --filename="$ss_path" >/dev/null 2>&1 || ss_path=""
    last_ss=$now_ts
  fi
  last_task="$state"

  echo "$ts poll iter=$iteration ss=$ss_path state=$state" | tee -a "$LOG_FILE"

  # 自动批准
  if [[ "$state" == *"APPROVE:true"* ]]; then
    approved=$(playwright-cli --raw run-code --filename=approve_all.js 2>/dev/null | tail -n 1 | sed 's/^"//;s/"$//')
    echo "$ts approval_clicked approved=$approved" | tee -a "$LOG_FILE"
    sleep 2
    continue
  fi

  # 流错误时点击刷新
  if [[ "$state" == *"ERROR:"*"连接已断开"* ]] && [[ "$state" == *"RELOAD:true"* ]]; then
    reloaded=$(playwright-cli --raw run-code --filename=reload_page.js 2>/dev/null | tail -n 1 | sed 's/^"//;s/"$//')
    echo "$ts reload_clicked reloaded=$reloaded" | tee -a "$LOG_FILE"
    sleep 3
    continue
  fi

  # 完成判断
  if [[ "$state" == *"DONE:true"* ]] || { [[ "$state" == *"RUNNING:false"* ]] && [[ "$state" == *"PAUSED:false"* ]] && [[ "$state" == *"所有任务已完成"* ]]; }; then
    echo "$ts completed state=$state" | tee -a "$LOG_FILE"
    break
  fi

  # 空闲无任务（可能是已完成或会话丢失）
  if (( i > 12 )) && [[ "$state" == *"RUNNING:false"* ]] && [[ "$state" == *"PAUSED:false"* ]] && [[ "$state" != *"ERROR:"*"连接已断开"* ]]; then
    task_value=$(echo "$state" | cut -d'|' -f1 | sed 's/TASK://')
    if [[ -z "$task_value" ]]; then
      echo "$ts completed idle_no_tasks state=$state" | tee -a "$LOG_FILE"
      break
    fi
  fi
done

final=$(playwright-cli --raw run-code --filename=capture_state.js 2>/dev/null | tail -n 1 | sed 's/^"//;s/"$//')
echo "$(date -Iseconds) monitor_end iter=$iteration state=$final" | tee -a "$LOG_FILE"
playwright-cli screenshot --filename="$SS_DIR/final.png" >/dev/null 2>&1 || true
