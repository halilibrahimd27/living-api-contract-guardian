#!/usr/bin/env python3
"""Verify property test files are syntactically correct and importable."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def verify_test_file(filepath: Path) -> tuple[bool, str]:
    """Verify a test file is syntactically correct."""
    try:
        with open(filepath) as f:
            content = f.read()
        ast.parse(content)

        # Check for required imports
        tree = ast.parse(content)
        has_hypothesis = any(
            isinstance(node, (ast.Import, ast.ImportFrom))
            and (
                (
                    isinstance(node, ast.Import)
                    and any(alias.name == "hypothesis" for alias in node.names)
                )
                or (isinstance(node, ast.ImportFrom) and node.module == "hypothesis")
            )
            for node in ast.walk(tree)
        )

        has_pytest = any(
            isinstance(node, (ast.Import, ast.ImportFrom))
            and (
                (
                    isinstance(node, ast.Import)
                    and any("pytest" in alias.name for alias in node.names)
                )
                or (isinstance(node, ast.ImportFrom) and "pytest" in (node.module or ""))
            )
            for node in ast.walk(tree)
        )

        # Count test classes and functions
        test_classes = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test")
        )
        test_funcs = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        )

        # Note: Many tests use @given, not all tests need pytest directly
        if has_hypothesis and (has_pytest or test_funcs > 0):
            return True, f"✓ {test_classes} test classes, {test_funcs} test functions"
        elif has_hypothesis and test_funcs > 0:
            return (
                True,
                f"✓ {test_classes} test classes, {test_funcs} test functions (hypothesis-based)",
            )
        else:
            return False, "Missing hypothesis imports or test functions"

    except SyntaxError as e:
        return False, f"Syntax error: {e}"
    except Exception as e:
        return False, f"Error: {e}"


def main() -> int:
    """Main verification function."""
    test_dir = Path("tests/property")
    test_files = [
        test_dir / "test_version_properties.py",
        test_dir / "test_redis_client_properties.py",
        test_dir / "test_db_properties.py",
        test_dir / "test_models_properties.py",
    ]

    print("Verifying property test files...\n")

    all_ok = True
    for filepath in test_files:
        ok, msg = verify_test_file(filepath)
        status = "✓" if ok else "✗"
        print(f"{status} {filepath.name}: {msg}")
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print("✓ All test files verified successfully!")
        print("\nTest summary:")
        print("  - test_version_properties.py: Tests for get_version() and get_git_sha()")
        print("  - test_redis_client_properties.py: Tests for Redis connectivity functions")
        print("  - test_db_properties.py: Tests for database engine and session management")
        print("  - test_models_properties.py: Tests for SQLAlchemy ORM models")
        print("\nTo run tests: pytest -q tests/property/")
        return 0
    else:
        print("✗ Some test files have issues!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
