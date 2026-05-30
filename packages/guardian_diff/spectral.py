"""Optional integration with the Spectral OpenAPI linter.

If a Spectral CLI is vendored at ``vendor/bin/spectral`` (relative to the
repository root), :func:`run_spectral` invokes it on a spec and returns
a list of :class:`~guardian_diff.models.SpectralFinding` rows.  When the
binary isn't present (or fails, or times out), it returns an empty list
and logs a warning — the diff engine never hard-fails on a missing
linter so local development without the vendored toolchain still works.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from guardian_core.logging import get_logger

from guardian_diff.models import SpectralFinding

_log = get_logger(__name__)

# Search order for the Spectral binary.
_VENDOR_PATH = Path(__file__).resolve().parents[2] / "vendor" / "bin" / "spectral"


def _find_spectral() -> str | None:
    if _VENDOR_PATH.is_file():
        return str(_VENDOR_PATH)
    found = shutil.which("spectral")
    return found if found else None


def _coerce_finding(item: dict[str, Any]) -> SpectralFinding | None:
    code = item.get("code")
    message = item.get("message")
    severity = item.get("severity")
    path = item.get("path", [])
    if not isinstance(code, str) or not isinstance(message, str):
        return None
    if not isinstance(severity, int):
        return None
    if not isinstance(path, list):
        path = []
    return SpectralFinding(
        code=code,
        message=message,
        severity=severity,
        path=[str(p) for p in path],
    )


def run_spectral(openapi_spec: dict[str, Any], *, timeout_s: float = 10.0) -> list[SpectralFinding]:
    """Run Spectral on an OpenAPI spec dict and parse the JSON output.

    Returns ``[]`` when no Spectral binary is available, when the spec
    is empty, or when invocation fails. Never raises.
    """
    if not openapi_spec:
        return []
    binary = _find_spectral()
    if binary is None:
        _log.debug("spectral.missing", searched=str(_VENDOR_PATH))
        return []

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(openapi_spec, tmp)
        tmp_path = tmp.name

    try:
        proc = subprocess.run(  # noqa: S603 — vendored binary, controlled args
            [binary, "lint", "--format", "json", tmp_path],
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.warning("spectral.invoke_failed", error=str(exc))
        return []
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:  # pragma: no cover
            pass

    raw = proc.stdout.decode("utf-8", errors="replace").strip()
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("spectral.bad_json", stdout=raw[:200])
        return []
    if not isinstance(items, list):
        return []
    out: list[SpectralFinding] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        finding = _coerce_finding(item)
        if finding is not None:
            out.append(finding)
    return out
