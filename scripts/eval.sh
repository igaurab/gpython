#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
out="${1:-docs/benchmarks}"
runs="${RUNS:-3}"
mkdir -p "$out"

uv run --with jupyterlab --with marimo python3 scripts/benchmeta.py > "$out/meta.json"
uv run --with jupyterlab --with marimo python3 scripts/warmbench.py --runs "$runs" --json "$out/warmbench.json"
uv run --with jupyter-server python3 scripts/kernelbench.py --json > "$out/kernelbench.json"
uv run --with matplotlib python3 scripts/benchchart.py "$out/warmbench.json" "$out/warmup_scaling"
echo "wrote meta.json, warmbench.json, kernelbench.json and warmup_scaling.png/.pdf in $out"
