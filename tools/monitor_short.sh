#!/usr/bin/env bash
LOG_FILE="agent_monitor.log"
SS_DIR="agent_screenshots"
mkdir -p "$SS_DIR"
echo "$(date -Iseconds) monitor_short_start" | tee -a "$LOG_FILE"
last_task=""
last_ss=0
for ((i=1; i<=60; i++)); do
  sleep 5
  state=$(playwright-cli --raw run-code --filename=capture_state.js 2>/dev/null | tail -n 1 | sed 's/^"//;s/"$//')
  ts=$(date -Iseconds)
  task_changed="false"; [[ "$state" != "$last_task" ]] && task_changed="true"
  now_ts=$(date +%s)
  time_for_ss=false; (( now_ts - last_ss > 30 )) && time_for_ss=true
  ss_path=""
  if [[ "$task_changed" == "true" ]] || [[ "$state" == *"DONE:true"* ]] || [[ "$time_for_ss" == "true" ]]; then
    ss_path="$SS_DIR/short_step_$(printf '%03d' $i).png"
    playwright-cli screenshot --filename="$ss_path" >/dev/null 2>&1 || ss_path=""
    last_ss=$now_ts
  fi
  last_task="$state"
  echo "$ts short_poll iter=$i ss=$ss_path state=$state" | tee -a "$LOG_FILE"
  if [[ "$state" == *"DONE:true"* ]] || { [[ "$state" == *"RUNNING:false"* ]] && [[ "$state" == *"PAUSED:false"* ]] && [[ "$state" == *"所有任务已完成"* ]]; }; then
    echo "$ts short_completed state=$state" | tee -a "$LOG_FILE"
    break
  fi
  if (( i > 8 )) && [[ "$state" == *"RUNNING:false"* ]] && [[ "$state" == *"PAUSED:false"* ]] && [[ "$state" != *"ERROR:"*"连接已断开"* ]]; then
    task_value=$(echo "$state" | cut -d'|' -f1 | sed 's/TASK://')
    if [[ -z "$task_value" ]]; then break; fi
  fi
done
echo "$(date -Iseconds) monitor_short_end" | tee -a "$LOG_FILE"
