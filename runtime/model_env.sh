# Run after loading the ROS environment, from any directory.
INSPECTION_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export CUDA_HOME=/usr/local/cuda
export PATH="$INSPECTION_ROOT/runtime/venv/bin:$CUDA_HOME/bin:$PATH"
cuda_vendor="$INSPECTION_ROOT/runtime/vendor/cuda-extra/usr/local/cuda-12.6"
venv_nvidia="$INSPECTION_ROOT/runtime/venv/lib/python3.10/site-packages/nvidia"
export LD_LIBRARY_PATH="$INSPECTION_ROOT/runtime/vendor/cudnn9/usr/lib/aarch64-linux-gnu:$cuda_vendor/targets/aarch64-linux/lib:$cuda_vendor/extras/CUPTI/lib64:$venv_nvidia/cudss/lib:$venv_nvidia/cu12/lib:$CUDA_HOME/targets/aarch64-linux/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export YOLO_CONFIG_DIR="$INSPECTION_ROOT/runtime/ultralytics"
export MPLCONFIGDIR="$INSPECTION_ROOT/runtime/matplotlib"
export OMP_NUM_THREADS=2
