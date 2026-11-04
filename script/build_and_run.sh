#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
PYTHON_BIN="$BACKEND_DIR/venv/bin/python"
ALPHA_ROOT="$BACKEND_DIR/data/alpha-v2"
FRONTEND_DIR="$ALPHA_ROOT/frontend"
RUNTIME_DIR="$ALPHA_ROOT/runtime"
LOG_DIR="$ROOT_DIR/logs/alpha-v2"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"
BACKEND_PORT="18787"
FRONTEND_PORT="14173"
BACKEND_URL="http://127.0.0.1:$BACKEND_PORT"
BACKEND_HEALTH_URL="$BACKEND_URL/api/v1/health"
FRONTEND_URL="http://127.0.0.1:$FRONTEND_PORT"

mkdir -p "$FRONTEND_DIR" "$RUNTIME_DIR" "$LOG_DIR"

export APP_ENV="alpha-local"
export APP_PORT="$BACKEND_PORT"
export SQLITE_PATH="$ALPHA_ROOT/database/speaker-v2-alpha.db"
export LOCAL_UPLOAD_DIR="$ALPHA_ROOT/tmp/uploads"
export LOCAL_AUDIO_DIR="$ALPHA_ROOT/tmp/audio"
export LOCAL_RENDER_DIR="$ALPHA_ROOT/tmp/render"
export LOCAL_MEDIA_LIBRARY_DIR="$ALPHA_ROOT/library/media"
export LOCAL_LOWER_THIRD_LIBRARY_DIR="$ALPHA_ROOT/library/lower-thirds"
export LOCAL_VOICEPRINT_LIBRARY_DIR="$ALPHA_ROOT/library/voiceprints"
export LOCAL_VOICE_CLONE_LIBRARY_DIR="$ALPHA_ROOT/library/voice-cloning"
export CLIENT_UPDATE_DIR="$ALPHA_ROOT/client-updates"
export CLIENT_UPDATE_ENABLED="false"
export PROVIDER_COST_FACTS_ENABLED="true"
export SPEAKER_V2_SHADOW_ENABLED="false"
export OSS_UPLOAD_PREFIX="alpha-v2/uploads/source"
export OSS_AUDIO_PREFIX="alpha-v2/uploads/audio"
export OSS_SUBTITLE_PREFIX="alpha-v2/outputs/subtitles"
export OSS_RENDER_PREFIX="alpha-v2/outputs/rendered"
export OSS_LOWER_THIRD_PREFIX="alpha-v2/assets/lower-thirds"
export OSS_VOICE_SAMPLE_PREFIX="alpha-v2/assets/voice-samples"

stop_pid_file() {
  local pid_file="$1"
  local expected_command="$2"
  if [[ ! -f "$pid_file" ]]; then
    return
  fi
  local pid
  pid="$(tr -dc '0-9' < "$pid_file")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    local command_line
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [[ "$command_line" != *"$expected_command"* ]]; then
      echo "忽略不属于 Alpha 的进程：$pid" >&2
      rm -f "$pid_file"
      return
    fi
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    wait "$pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
}

stop_matching_listener() {
  local port="$1"
  local expected_command="$2"
  local pid command_line
  while IFS= read -r pid; do
    pid="$(printf '%s' "$pid" | tr -dc '0-9')"
    [[ -n "$pid" ]] || continue
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    if [[ "$command_line" != *"$expected_command"* ]]; then
      echo "保留占用端口 $port 的非 Alpha 进程：$pid" >&2
      continue
    fi
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    wait "$pid" 2>/dev/null || true
  done < <(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
}

stop_alpha() {
  stop_pid_file "$FRONTEND_PID_FILE" "http.server $FRONTEND_PORT"
  stop_pid_file "$BACKEND_PID_FILE" "uvicorn main:app --host 127.0.0.1 --port $BACKEND_PORT"
  stop_matching_listener "$FRONTEND_PORT" "http.server $FRONTEND_PORT"
  stop_matching_listener "$BACKEND_PORT" "uvicorn main:app --host 127.0.0.1 --port $BACKEND_PORT"
}

assert_port_free() {
  local port="$1"
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "本机端口 $port 已被其他程序占用；为避免影响现有服务，Alpha 未启动。" >&2
    exit 1
  fi
}

build_frontend() {
  SPEAKER_BUILD_OUTPUT_DIR="$FRONTEND_DIR" \
  SPEAKER_API_BASE="$BACKEND_URL/api/v1" \
  SPEAKER_BUILD_MARKER="2026-07-18 / speaker-v2-local-alpha" \
  SPEAKER_UPDATE_CHANNEL="alpha-local" \
    node "$ROOT_DIR/scripts/build-frontend.mjs"
}

initialize_database() {
  mkdir -p "$(dirname "$SQLITE_PATH")"
  (
    cd "$BACKEND_DIR"
    "$PYTHON_BIN" -c "import main"
    "$PYTHON_BIN" -m scripts.migrate_v2_provider_cost_facts --apply
    "$PYTHON_BIN" -m scripts.migrate_v2_subtitle_pipeline_runs --apply
  ) > "$LOG_DIR/database-init.log"
}

wait_for_url() {
  local url="$1"
  for _ in {1..40}; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return
    fi
    sleep 0.25
  done
  echo "本机 Alpha 启动超时：$url" >&2
  stop_alpha
  exit 1
}

start_alpha() {
  stop_alpha
  assert_port_free "$BACKEND_PORT"
  assert_port_free "$FRONTEND_PORT"
  build_frontend
  initialize_database

  (
    cd "$BACKEND_DIR"
    nohup "$PYTHON_BIN" -m uvicorn main:app \
      --host 127.0.0.1 \
      --port "$BACKEND_PORT" \
      > "$LOG_DIR/backend.log" 2>&1 &
    echo $! > "$BACKEND_PID_FILE"
  )
  nohup "$PYTHON_BIN" -m http.server "$FRONTEND_PORT" \
    --bind 127.0.0.1 \
    --directory "$FRONTEND_DIR" \
    > "$LOG_DIR/frontend.log" 2>&1 &
  echo $! > "$FRONTEND_PID_FILE"

  wait_for_url "$BACKEND_HEALTH_URL"
  wait_for_url "$FRONTEND_URL/"
  echo "Speaker 2.0 本机 Alpha 已启动：$FRONTEND_URL/"
  echo "隔离数据库：$SQLITE_PATH"
  echo "日志目录：$LOG_DIR"
}

wait_for_alpha() {
  trap 'stop_alpha' EXIT INT TERM
  local backend_pid frontend_pid
  backend_pid="$(tr -dc '0-9' < "$BACKEND_PID_FILE")"
  frontend_pid="$(tr -dc '0-9' < "$FRONTEND_PID_FILE")"
  echo "本机 Alpha 正在运行；结束此运行任务即可安全停止。"
  while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
    sleep 1
  done
  echo "本机 Alpha 服务意外停止，请查看：$LOG_DIR" >&2
  return 1
}

case "$MODE" in
  --start|start)
    start_alpha
    ;;
  run)
    start_alpha
    /usr/bin/open "$FRONTEND_URL/"
    wait_for_alpha
    ;;
  --verify|verify)
    start_alpha
    curl -fsS "$BACKEND_HEALTH_URL"
    echo
    stop_alpha
    ;;
  --debug|debug|--logs|logs|--telemetry|telemetry)
    start_alpha
    trap 'stop_alpha' EXIT INT TERM
    tail -n 80 -f "$LOG_DIR/backend.log" "$LOG_DIR/frontend.log"
    ;;
  --stop|stop)
    stop_alpha
    echo "Speaker 2.0 本机 Alpha 已停止。"
    ;;
  *)
    echo "usage: $0 [run|--start|--verify|--debug|--logs|--telemetry|--stop]" >&2
    exit 2
    ;;
esac
