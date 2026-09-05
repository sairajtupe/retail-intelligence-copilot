#!/usr/bin/env python3
"""Generate the full Indian retail dataset (20 stores) and retail.db.

Thin wrapper around src.generate_dataset.build_dataset(). Run from the
repository root:  python scripts/generate_data.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.generate_dataset import build_dataset  # noqa: E402


if __name__ == "__main__":
    stats = build_dataset()
    print(stats)