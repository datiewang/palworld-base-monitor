#!/usr/bin/env python3
"""Run palmon straight out of a checkout: ./palmon.py serve

Installing the package (pip install -e .) gives a `palmon` command that does
the same thing; this script exists so a clone works without installing
anything but palworld-save-tools.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from palmon.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
