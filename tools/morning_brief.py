#!/usr/bin/env python3
"""Alias wrapper for morning-brief.py to support pythonic underscore naming."""
import sys
import subprocess
from pathlib import Path

PKA_ROOT = Path(__file__).resolve().parent.parent
TARGET = PKA_ROOT / "tools" / "morning-brief.py"

if __name__ == "__main__":
    venv_py = PKA_ROOT / "discord-bridge" / "venv" / "bin" / "python3"
    python_exe = str(venv_py) if venv_py.exists() else sys.executable
    res = subprocess.run([python_exe, str(TARGET)] + sys.argv[1:], cwd=str(PKA_ROOT))
    sys.exit(res.returncode)
