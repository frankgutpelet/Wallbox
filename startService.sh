#!/bin/bash
set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1
mkdir -p "$SCRIPT_DIR/log"
PID_FILE="$SCRIPT_DIR/.wallbox.pid"

project_pids() {
    for proc_dir in /proc/[0-9]*; do
        [ -r "$proc_dir/cmdline" ] || continue
        process_cwd="$(readlink -f "$proc_dir/cwd" 2>/dev/null || true)"
        [ "$process_cwd" = "$SCRIPT_DIR" ] || continue
        process_cmd="$(tr '\0' ' ' < "$proc_dir/cmdline" 2>/dev/null || true)"
        case "$process_cmd" in
            *"main.py"*|*"uvicorn main:app"*) printf '%s\n' "${proc_dir##*/}" ;;
        esac
    done
}

existing="$(project_pids | head -n 1)"
if [ -n "$existing" ] && kill -0 "$existing" 2>/dev/null; then
    printf '%s\n' "$existing" > "$PID_FILE"
    echo "Wallbox laeuft bereits (PID $existing)."
    exit 0
fi

log_stamp="$(date +"%Y-%m-%d_%H-%M")"
log_file="$SCRIPT_DIR/log/error${log_stamp}.log"
nohup python3 -m uvicorn main:app --app-dir "$SCRIPT_DIR" --host 0.0.0.0 --port 8001 \
    >> "$log_file" 2>&1 < /dev/null &
wallbox_pid=$!
printf '%s\n' "$wallbox_pid" > "$PID_FILE"
sleep 1
if ! kill -0 "$wallbox_pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "Wallbox konnte nicht gestartet werden. Details: $log_file" >&2
    exit 1
fi
echo "Wallbox gestartet (PID $wallbox_pid). Log: $log_file"