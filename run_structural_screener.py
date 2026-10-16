#!/usr/bin/env python3
"""Stable workflow entrypoint for the unified SMC scanner.

All structure, scoring, and diagnostics are implemented in the scanner.
Do not mutate source code using brittle string replacements.
"""
from pathlib import Path
import runpy

SOURCE = Path(__file__).with_name("bybit_htf_ltf_screener.py")

if __name__ == "__main__":
    runpy.run_path(str(SOURCE), run_name="__main__")
