#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env.demo}"
APP_MODULE="${APP_MODULE:-enterprise_ai_agents_demo/streamlit_app.py}"
HOST="${STREAMLIT_HOST:-0.0.0.0}"
PORT="${STREAMLIT_PORT:-8501}"
PID_FILE="${PID_FILE:-$APP_DIR/streamlit_demo.pid}"
RUN_LOG_FILE="${RUN_LOG_FILE:-$APP_DIR/streamlit_demo.out.log}"
export OCI_AGENT_MEMORY_FILE="${OCI_AGENT_MEMORY_FILE:-$APP_DIR/enterprise_ai_agents_demo/agent_memory.json}"
export OCI_AGENT_LOG_FILE="${OCI_AGENT_LOG_FILE:-$APP_DIR/enterprise_ai_agents_demo/agent_demo.log}"

usage() {
  cat <<EOF
Usage: ./run_streamlit_demo.sh [start|stop|restart|status|tail]

Environment overrides:
  ENV_FILE                 default: $ENV_FILE
  VENV_DIR                 default: $VENV_DIR
  STREAMLIT_HOST           default: $HOST
  STREAMLIT_PORT           default: $PORT
  PID_FILE                 default: $PID_FILE
  RUN_LOG_FILE             default: $RUN_LOG_FILE
  OCI_AGENT_MEMORY_FILE    default: $OCI_AGENT_MEMORY_FILE
  OCI_AGENT_LOG_FILE       default: $OCI_AGENT_LOG_FILE

The script loads .env.demo when present, activates .venv, and starts Streamlit
in the background.
EOF
}

load_env() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
}

activate_venv() {
  if [[ ! -d "$VENV_DIR" ]]; then
    echo "Virtual environment not found: $VENV_DIR" >&2
    echo "Create it with: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
  fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
}

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

start() {
  load_env
  activate_venv
  if is_running; then
    echo "Streamlit demo is already running with PID $(cat "$PID_FILE")."
    exit 0
  fi

  mkdir -p "$(dirname "$RUN_LOG_FILE")" "$(dirname "$OCI_AGENT_LOG_FILE")" "$(dirname "$OCI_AGENT_MEMORY_FILE")"
  cd "$APP_DIR"

  nohup streamlit run "$APP_MODULE" \
    --server.address "$HOST" \
    --server.port "$PORT" \
    >>"$RUN_LOG_FILE" 2>&1 &

  echo "$!" > "$PID_FILE"
  echo "Started Streamlit demo."
  echo "PID: $(cat "$PID_FILE")"
  echo "URL: http://<vm-public-ip>:$PORT"
  echo "Streamlit process log: $RUN_LOG_FILE"
  echo "Agent app log: $OCI_AGENT_LOG_FILE"
}

stop() {
  if is_running; then
    PID="$(cat "$PID_FILE")"
    kill "$PID"
    rm -f "$PID_FILE"
    echo "Stopped Streamlit demo PID $PID."
  else
    rm -f "$PID_FILE"
    echo "Streamlit demo is not running."
  fi
}

status() {
  if is_running; then
    echo "Streamlit demo is running with PID $(cat "$PID_FILE")."
    echo "Port: $PORT"
    echo "Streamlit process log: $RUN_LOG_FILE"
    echo "Agent app log: $OCI_AGENT_LOG_FILE"
  else
    echo "Streamlit demo is not running."
  fi
}

tail_logs() {
  touch "$RUN_LOG_FILE" "$OCI_AGENT_LOG_FILE"
  tail -f "$RUN_LOG_FILE" "$OCI_AGENT_LOG_FILE"
}

case "${1:-start}" in
  start)
    start
    ;;
  stop)
    stop
    ;;
  restart)
    stop
    start
    ;;
  status)
    status
    ;;
  tail)
    tail_logs
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
esac
