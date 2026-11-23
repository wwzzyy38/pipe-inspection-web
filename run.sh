#!/usr/bin/env bash
set -eo pipefail
cd -- "$(dirname -- "$0")"
source /opt/ros/humble/setup.bash
for ws in "$HOME/pipe_ws/install/local_setup.bash" "$PWD/model_ws/install/local_setup.bash"; do
  if [[ -f "$ws" ]]; then source "$ws"; fi
done
if [[ -r config.env ]]; then set -a; source config.env; set +a; fi
mkdir -p logs "${PIPE_WEB_RECORDINGS:-$PWD/recordings}"
exec python3 -u server.py --port "${PIPE_WEB_PORT:-18120}" --recordings "${PIPE_WEB_RECORDINGS:-$PWD/recordings}"
