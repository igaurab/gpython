"""Print, as JSON, the conditions a benchmark ran under, so two results can be compared fairly.

    uv run --with jupyterlab --with marimo python3 scripts/benchmeta.py > meta.json

Records the gpython commit (and whether the tree had changes), the date, the machine (CPU
model, cores, memory), the OS, Python, the versions of the tools being compared and the load
average when it started. scripts/eval.sh writes it next to the results.
"""

from __future__ import annotations

import datetime
import json
import os
import platform
import subprocess
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ["textual", "ipykernel", "jupyter-client", "jupyterlab", "jupyter-server", "marimo", "pyzmq"]


def git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def cpu_model():
    if platform.system() == "Darwin":
        try:
            return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def memory_gb():
    try:
        import psutil

        return round(psutil.virtual_memory().total / 2**30, 1)
    except ImportError:
        return None


def version(package):
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def main():
    print(json.dumps({
        "commit": git("rev-parse", "--short", "HEAD"),
        "tree_changed": bool(git("status", "--porcelain", "--untracked-files=no")),
        "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "cpu": cpu_model(),
        "cores": os.cpu_count(),
        "memory_gb": memory_gb(),
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "packages": {package: version(package) for package in PACKAGES},
        "load_average_1m": round(os.getloadavg()[0], 2),
    }, indent=2))


if __name__ == "__main__":
    main()
