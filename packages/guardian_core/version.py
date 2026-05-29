"""Version and build-info accessors.

The Guardian distribution version is kept in sync with ``pyproject.toml``.
``git_sha`` is best-effort: it is read from the ``GUARDIAN_GIT_SHA``
environment variable (set by the Dockerfile / CI) and falls back to
``"unknown"`` if not present.
"""

from __future__ import annotations

import os

__version__: str = "0.1.0"


def get_version() -> str:
    """Return the Guardian distribution version."""
    return __version__


def get_git_sha() -> str:
    """Return the configured git SHA, or ``"unknown"`` if not provided."""
    return os.environ.get("GUARDIAN_GIT_SHA", "unknown")
