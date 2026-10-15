#!/usr/bin/env python3
"""Compatibility launcher for the Ubersight dashboard CLI."""

from pathlib import Path
import os
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

src_path = str(SRC_DIR)
if src_path in sys.path:
    sys.path.remove(src_path)
sys.path.insert(0, src_path)
# `--supervise` starts the dashboard as a `python -m ubersight` child, which
# must import this checkout too rather than fail or find another copy.
inherited = os.environ.get("PYTHONPATH", "").split(os.pathsep)
os.environ["PYTHONPATH"] = os.pathsep.join(
    [src_path, *(part for part in inherited if part and part != src_path)]
)

from ubersight.dashboard import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
