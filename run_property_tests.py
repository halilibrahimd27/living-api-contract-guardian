#!/usr/bin/env python3
"""Run property-based tests programmatically and report results."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    """Run property tests using pytest programmatically."""
    import pytest

    # Set up paths
    root = Path(__file__).parent
    test_dir = root / "tests" / "property"

    # Test files to run
    test_files = [
        test_dir / "test_version_properties.py",
        test_dir / "test_redis_client_properties.py",
        test_dir / "test_db_properties.py",
        test_dir / "test_models_properties.py",
    ]

    # Check that all test files exist
    missing_files = [f for f in test_files if not f.exists()]
    if missing_files:
        print("Error: Missing test files:")
        for f in missing_files:
            print(f"  - {f}")
        return 1

    print("Running Guardian property tests...")
    print(f"Test directory: {test_dir}")
    print()

    # Run pytest
    args = [
        str(test_dir),
        "-v",
        "--tb=short",
        "--hypothesis-seed=0",  # Use deterministic seed for reproducibility
    ]

    result = pytest.main(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
