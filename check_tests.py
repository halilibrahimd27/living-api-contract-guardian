#!/usr/bin/env python3
"""Check if all property tests pass."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    """Run all property tests."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/property/", "-q", "--tb=short"],
        cwd="/workspace/workspaces/living-api-contract-guardian",
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
