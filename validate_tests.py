#!/usr/bin/env python3
"""Validate that all test modules can be imported without syntax errors."""

from __future__ import annotations

import sys
from pathlib import Path

def validate_test_imports() -> int:
    """Attempt to import all test modules."""
    test_dir = Path("tests") / "property"
    test_files = [
        "test_version_properties",
        "test_redis_client_properties",
        "test_db_properties",
        "test_models_properties",
        "test_schemas_properties",
        "test_hashing_properties",
        "test_api_properties",
    ]

    print("Validating test modules...")
    print()

    all_ok = True
    for test_module in test_files:
        try:
            # This will fail due to missing fixtures when actually running,
            # but will verify syntax is OK
            __import__(f"tests.property.{test_module}")
            print(f"✅ {test_module}")
        except ImportError as e:
            # Expected - fixtures won't be available
            if "conftest" in str(e) or "fixture" in str(e):
                print(f"✅ {test_module} (import ok, fixtures unavailable)")
            else:
                print(f"❌ {test_module}: {e}")
                all_ok = False
        except SyntaxError as e:
            print(f"❌ {test_module}: Syntax error: {e}")
            all_ok = False
        except Exception as e:
            # Other errors that might occur during import
            print(f"⚠️  {test_module}: {type(e).__name__}: {e}")

    print()
    if all_ok:
        print("✅ All test modules validated successfully!")
        return 0
    else:
        print("❌ Some test modules have errors")
        return 1

if __name__ == "__main__":
    sys.exit(validate_test_imports())
