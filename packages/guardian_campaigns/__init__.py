"""Guardian deprecation campaign orchestrator.

Tracks deprecated fields/endpoints through a usage-decay lifecycle,
computes EWMA decay curves from M3 telemetry, and schedules automated
reminder PRs to client repos via the GitHub App.
"""

from __future__ import annotations
