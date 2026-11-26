#!/bin/bash
set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
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

pids="$(project_pids | sort -u)"
if [ -f "$PID_FILE" ]; then
    saved_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$saved_pid" ] && kill -0 "$saved_pid" 2>/dev/null; then
        saved_cwd="$(readlink -f "/proc/$saved_pid/cwd" 2>/dev/null || true)"
        saved_cmd="$(tr '\0' ' ' < "/proc/$saved_pid/cmdline" 2>/dev/null || true)"
        case "$saved_cmd" in
            *"main.py"*|*"uvicorn main:app"*)
                if [ "$saved_cwd" = "$SCRIPT_DIR" ]; then
                    pids="$(printf '%s\n%s\n' "$pids" "$saved_pid" | sed '/^$/d' | sort -u)"
                fi
                ;;
        esac
    fi
fi

if [ -z "$pids" ]; then
    rm -f "$PID_FILE"
    echo "Kein laufender Wallbox-Prozess gefunden."
    exit 0
fi

for wallbox_pid in $pids; do
    if kill -0 "$wallbox_pid" 2>/dev/null; then
        kill -TERM "$wallbox_pid" 2>/dev/null || true
    fi
done

for _ in $(seq 1 10); do
    still_running=""
    for wallbox_pid in $pids; do
        if kill -0 "$wallbox_pid" 2>/dev/null; then
            still_running="$still_running $wallbox_pid"
        fi
    done
    [ -z "$still_running" ] && break
    sleep 1
done

still_running=""
for wallbox_pid in $pids; do
    if kill -0 "$wallbox_pid" 2>/dev/null; then
        still_running="$still_running $wallbox_pid"
    fi
done
if [ -z "$still_running" ]; then
    rm -f "$PID_FILE"
    echo "Wallbox gestoppt."
else
    echo "Stoppsignal gesendet; Prozess beendet sich noch:$still_running"
    exit 1
fi