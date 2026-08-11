#!/usr/bin/env python3
"""run.py - the entry point PyInstaller builds from.

A frozen entry script runs as a top-level module with no package context, so the
relative imports in rulebuddy/__main__.py cannot work there. This module keeps
the packaged build on absolute imports; `python -m rulebuddy` still goes through
__main__.py directly.
"""

import os
import sys

if getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.join(sys._MEIPASS, "src"))
else:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from rulebuddy.__main__ import complain, main   # noqa: E402

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as err:
        complain(f"Rule Buddy could not start.\n\n{type(err).__name__}: {err}")
