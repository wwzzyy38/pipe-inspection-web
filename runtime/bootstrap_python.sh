#!/usr/bin/env bash
set -eo pipefail
cd -- "$(dirname -- "$0")/.."
base="$PWD/runtime"
export PIP_DISABLE_PIP_VERSION_CHECK=1
if [ ! -x "$base/venv/bin/python" ]; then python3 -m venv --system-site-packages --without-pip "$base/venv"; fi
py="$base/venv/bin/python"
idx=https://pypi.jetson-ai-lab.io/jp6/cu126/+f
"$py" -m pip install --no-deps --cache-dir "$base/downloads" \
 "$idx/46b/b8b13f844b211/torch-2.11.0-cp310-cp310-linux_aarch64.whl#sha256=46bb8b13f844b2111f6f41abc703d01075a7185e0c587cb002e957ede2b8c8a3" \
 "$idx/d11/6f08d3d62417d/torchvision-0.26.0-cp310-cp310-linux_aarch64.whl#sha256=d116f08d3d62417dc36cbf41923e491b8b49b77399575f0878ae391e6e77a159"
"$py" -m pip install --index-url https://pypi.org/simple --cache-dir "$base/downloads" \
 'numpy==1.26.4' 'scipy==1.15.3' easydict PyYAML matplotlib pillow psutil polars requests setuptools wheel ninja \
 filelock 'typing_extensions>=4.14.0' 'sympy>=1.13.3' networkx jinja2 fsspec packaging tqdm termcolor multimethod h5py
"$py" -m pip install --no-deps --index-url https://pypi.org/simple --cache-dir "$base/downloads" 'ultralytics==8.4.130' ultralytics-thop
printf '\nPython wheels installed\n'
