#!/usr/bin/env python3
"""Run property-based tests and report results."""

import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/property/", "-v", "--tb=short"],
    cwd="/workspace/workspaces/living-api-contract-guardian",
)
sys.exit(result.returncode)
