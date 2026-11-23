#!/usr/bin/env bash
# Jetson03: start the read-only sensor/preview stack, not the flight stack.
set -eo pipefail

app_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ros_setup=${INSPECTION_ROS_SETUP:-/opt/ros/humble/setup.bash}
ws_home=${INSPECTION_WORKSPACE_HOME:-$HOME}
siyi_setup="$ws_home/siyi_ws/install/local_setup.bash"
thermal_setup="$ws_home/thermal_imager_ws/install/local_setup.bash"
livox_setup="$ws_home/livox_ws/install/local_setup.bash"
fastlio_setup="$ws_home/fast_lio_ws/install/local_setup.bash"
pipe_setup="$ws_home/pipe_ws/install/local_setup.bash"
session=pipe_preview
window=preview
check_only=false
detach=false

usage() {
    printf '%s\n' 'Usage: bash start_preview_stack.sh [--check | --detach]' \
        '  --check   Check prerequisites only; do not start any nodes.' \
        '  --detach  Start or show status without attaching the terminal.'
}

fail() { printf 'Preview stack: %s\n' "$*" >&2; exit 1; }

while (($#)); do
    case "$1" in
        --check) check_only=true ;;
        --detach) detach=true ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; fail "unknown option: $1" ;;
    esac
    shift
done
if $check_only && $detach; then fail '--check and --detach are mutually exclusive'; fi

command -v tmux >/dev/null || fail 'tmux is missing'

open_session() {
    tmux list-panes -t "=$session:$window" -F '#{pane_title} running=#{?pane_dead,no,yes}'
    if $detach; then
        printf 'Attach with: tmux attach-session -t %s\n' "$session"
    elif [[ -n ${TMUX-} ]]; then
        tmux switch-client -t "=$session:$window"
    elif [[ -t 0 && -t 1 ]]; then
        exec tmux attach-session -t "=$session:$window"
    else
        printf 'Attach with: tmux attach-session -t %s\n' "$session"
    fi
}

if ! $check_only && tmux has-session -t "=$session" 2>/dev/null; then
    open_session
    exit 0
fi

for command in ip ss timeout grep; do
    command -v "$command" >/dev/null || fail "$command is missing"
done
for file in "$ros_setup" "$siyi_setup" "$thermal_setup" "$livox_setup" "$fastlio_setup" "$pipe_setup" "$app_dir/run.sh"; do
    [[ -r "$file" ]] || fail "missing or unreadable: $file"
done

# Check in a subshell so these overlays do not alter the caller or pane environments.
(
    source "$ros_setup"
    source "$siyi_setup"
    source "$thermal_setup"
    source "$livox_setup"
    source "$fastlio_setup"
    source "$pipe_setup"
    for package in siyi_a2_mini thermal_imager livox_ros_driver2 fast_lio bringup; do
        timeout 8 ros2 pkg prefix "$package" >/dev/null || fail "ROS package unavailable: $package"
    done
    share=$(timeout 8 ros2 pkg prefix --share bringup)
    [[ -r "$share/launch/location.launch" && -r "$share/config/fastlio_mid360_pipe.yaml" ]] || fail 'missing bringup launch or FAST-LIO configuration'
)

require_route() {
    local route
    route=$(ip -4 route get "$1") || fail "no route to $1"
    [[ " $route " == *" dev $2 "* ]] || fail "$1 must route over $2 (got: $route)"
}

require_route 192.168.144.25 enx00e04cabc4ad
require_route 192.168.2.254 enx00e04c680205
require_route 192.168.1.116 enP8p1s0

port=$(
    cd "$app_dir"
    if [[ -r config.env ]]; then source config.env; fi
    printf '%s\n' "${PIPE_WEB_PORT:-18120}"
)
[[ "$port" =~ ^[1-9][0-9]{0,4}$ && "$port" -le 65535 ]] || fail 'invalid web port'

if $check_only; then
    printf 'Prerequisites OK. Web port: %s. No processes started.\n' "$port"
    exit 0
fi

listeners=$(ss -H -ltn "sport = :$port")
[[ -z "$listeners" ]] || fail "web port $port is already in use; stop the old preview explicitly first"
if tmux list-sessions -F '#{session_name}' 2>/dev/null | grep -Eq '^(mission_follow_path_real|pipe_hover|pipe_hover_real)(_|$)'; then
    fail 'a flight/positioning task session already exists; leave it untouched'
fi
nodes=$(source "$ros_setup" && timeout 8 ros2 node list --no-daemon --spin-time 2) || fail 'cannot inspect existing ROS nodes'
if grep -Eq '^(/(pipe_inspection_web|laser_mapping|livox_lidar_publisher|livox_scan_batcher|mid360_to_px4)(/|$)|/(siyi|thermal|pipe_yolo|pipe_pointcloud)/)' <<< "$nodes"; then
    fail 'a web, sensor, or recognition node is already running; stop the old preview explicitly first'
fi

# Create all four panes first, then launch the commands. Failures clean up only this new session.
web=$(tmux new-session -d -P -F '#{pane_id}' -s "$session" -n "$window" -x 160 -y 50 /bin/bash --noprofile --norc)
created=$(tmux display-message -p -t "$web" '#{session_id}')
trap 'tmux kill-session -t "$created" 2>/dev/null || true' EXIT
tmux set-option -w -t "$web" remain-on-exit on
tmux set-option -w -t "$web" pane-border-status top
tmux set-option -w -t "$web" pane-border-format '#{pane_title}'
thermal=$(tmux split-window -h -d -P -F '#{pane_id}' -t "$web" /bin/bash --noprofile --norc)
siyi=$(tmux split-window -v -d -P -F '#{pane_id}' -t "$web" /bin/bash --noprofile --norc)
lidar=$(tmux split-window -v -d -P -F '#{pane_id}' -t "$thermal" /bin/bash --noprofile --norc)

tmux respawn-pane -k -t "$web" /bin/bash -c 'exec /bin/bash "$1"' web "$app_dir/run.sh"
tmux respawn-pane -k -t "$siyi" /bin/bash -c '
    set -eo pipefail; source "$1"; source "$2"
    exec ros2 run siyi_a2_mini image_node --ros-args -r __ns:=/siyi -p rtsp_url:=rtsp://192.168.144.25:8554/main.264 -p publish_fps:=15.0
' siyi "$ros_setup" "$siyi_setup"
tmux respawn-pane -k -t "$thermal" /bin/bash -c '
    set -eo pipefail; source "$1"; source "$2"
    exec ros2 launch thermal_imager thermal_imager.launch.py
' thermal "$ros_setup" "$thermal_setup"
tmux respawn-pane -k -t "$lidar" /bin/bash -c '
    set -eo pipefail; source "$1"; source "$2"; source "$3"; source "$4"
    exec ros2 launch bringup location.launch enable_mavros:=false enable_bridge:=false pipe_lidar_hz:=20
' lidar "$ros_setup" "$livox_setup" "$fastlio_setup" "$pipe_setup"

tmux select-pane -t "$web" -T 'Web'
tmux select-pane -t "$thermal" -T 'Thermal'
tmux select-pane -t "$siyi" -T 'SIYI'
tmux select-pane -t "$lidar" -T 'Livox + FAST-LIO'
tmux select-pane -t "$web"
trap - EXIT
printf 'Preview processes launched. Web port: %s. Check the panes for readiness.\n' "$port"
open_session
