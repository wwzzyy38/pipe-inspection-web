#!/usr/bin/env bash
set -eo pipefail
cd -- "$(dirname -- "$0")/.."
source /opt/ros/humble/setup.bash
source model_ws/install/local_setup.bash
source runtime/model_env.sh
mkdir -p logs recordings
export ROS_LOCALHOST_ONLY=1 ROS_DOMAIN_ID=73
export PIPE_YOLO_PYTHON="$INSPECTION_ROOT/runtime/python-cuda"
export PIPE_YOLO_MODEL="$INSPECTION_ROOT/assets/weights/yolo26m.pt"
export PIPE_POINT_PYTHON="$INSPECTION_ROOT/runtime/python-cuda"
export PIPE_POINT_UPSTREAM="$INSPECTION_ROOT/model_ws/openpoints-source"
export PIPE_POINT_MODEL="$INSPECTION_ROOT/assets/weights/pointnext-b-best62.pt"
python3 runtime/verify_models.py "$@"
