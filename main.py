#!/usr/bin/env python3
"""Fortnite PC Tweaker — entry point.

Run with no arguments for the full interactive tweak run:

    python main.py

Other modes:

    python main.py --dry-run     # preview every change, write nothing
    python main.py --revert      # undo the last run
    python main.py --session     # live booster while Fortnite is open
    python main.py --benchmark   # measure frame stats (needs PresentMon)
"""

from __future__ import annotations

import sys

from tweaker.cli import main

if __name__ == "__main__":
    sys.exit(main())
