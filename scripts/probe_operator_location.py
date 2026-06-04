#!/usr/bin/env python3
"""Compatibility launcher for Ubersight location-provider diagnostics."""

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

src_path = str(SRC_DIR)
if src_path in sys.path:
    sys.path.remove(src_path)
sys.path.insert(0, src_path)

from ubersight.probe_operator_location import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
