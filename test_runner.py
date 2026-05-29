#!/usr/bin/env python3
"""Simple test runner to verify property tests."""

import subprocess
import sys

def main() -> int:
    """Run property tests and report results."""
    print("Running Guardian property tests...\n")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/property/", "-v", "--tb=short", "-q"],
        cwd="/workspace/workspaces/living-api-contract-guardian",
    )

    return result.returncode

if __name__ == "__main__":
    sys.exit(main())
