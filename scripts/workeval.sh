#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
out="${1:-docs/benchmarks}"
runs="${RUNS:-5}"
env="${WORKBENCH_ENV:-$HOME/.cache/gpython-workbench-env}"
mkdir -p "$out"

if [[ ! -x "$env/bin/python" ]]; then
    uv venv -q --python 3.13 "$env"
fi
uv pip install -q --python "$env/bin/python" ipykernel numpy jupyterlab marimo psutil tornado jupyter_client

"$env/bin/python" scripts/benchmeta.py > "$out/workmeta.json"
"$env/bin/python" scripts/workbench.py --runs "$runs" --json "$out/workbench.json"
uv run --with matplotlib python3 scripts/workchart.py "$out/workbench.json" "$out/workload_scaling"
echo "wrote workmeta.json, workbench.json and workload_scaling.png/.pdf in $out"
