#!/usr/bin/env python3
"""Quick check that all property test modules can be imported and discovered."""

from __future__ import annotations

import sys
from pathlib import Path


def check_test_imports() -> bool:
    """Try to import all test modules to check for syntax errors."""
    test_dir = Path("tests") / "property"
    test_files = sorted(test_dir.glob("test_*.py"))

    if not test_files:
        print("ERROR: No test files found!")
        return False

    print("Checking test module imports...")
    print()

    all_ok = True
    for test_file in test_files:
        try:
            # Try to import the module
            module_name = f"tests.property.{test_file.stem}"
            __import__(module_name)
            print(f"✓ {test_file.name:40} OK")
        except Exception as e:
            print(f"✗ {test_file.name:40} FAILED: {e}")
            all_ok = False

    print()
    if all_ok:
        print("✓ All test modules can be imported successfully!")
        return True
    else:
        print("✗ Some test modules failed to import!")
        return False


def main() -> int:
    """Main entry point."""
    try:
        success = check_test_imports()
        return 0 if success else 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
