#!/usr/bin/env python3
"""Alias wrapper for fetch-transcript.py to support pythonic underscore naming.

Runs the hyphenated script with the same interpreter that ran this one, so
bin/pka (or any interpreter you choose) decides the environment.
"""
import subprocess
import sys
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
TARGET = PKA_ROOT / "tools" / "fetch-transcript.py"

if __name__ == "__main__":
    res = subprocess.run([sys.executable, str(TARGET)] + sys.argv[1:], cwd=str(PKA_ROOT))
    sys.exit(res.returncode)
