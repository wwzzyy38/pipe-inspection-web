#!/usr/bin/env bash
set -eo pipefail
cd -- "$(dirname -- "$0")/.."
source runtime/model_env.sh
export TORCH_CUDA_ARCH_LIST=8.7
export MAX_JOBS=2
hdr="$INSPECTION_ROOT/runtime/vendor/cuda-headers"
for lib in libcusparse-dev-12-6 libcublas-dev-12-6 libcusolver-dev-12-6; do
  include="$hdr/$lib/usr/local/cuda-12.6/targets/aarch64-linux/include"
  test -d "$include"
  export CPATH="$include${CPATH:+:$CPATH}"
done
python - <<'PY'
import torch
assert torch.cuda.is_available()
print(torch.__version__,torch.version.cuda,torch.cuda.get_device_name(0),flush=True)
PY
python -m pip install --use-pep517 --no-build-isolation --no-deps ./model_ws/openpoints-source/openpoints/cpp/pointnet2_batch
python - <<'PY'
import torch,pointnet2_batch_cuda
print(pointnet2_batch_cuda.__file__)
PY
