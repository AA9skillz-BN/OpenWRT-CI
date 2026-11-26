#!/usr/bin/env python3
"""Fail Linux CI when the build regression suite leaves required tests skipped."""
from pathlib import Path
import unittest

suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent))
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful() or result.skipped or result.testsRun == 0:
    raise SystemExit("Build tests failed or checks were skipped")
