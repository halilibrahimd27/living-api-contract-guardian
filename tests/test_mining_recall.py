"""Recall test: miner must hit ≥90% of labeled endpoints across fixtures."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from guardian_core.mining import mine_repo

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "clients"
LABELS_FILE = FIXTURE_ROOT / "labels.yaml"


def _load_labels() -> dict[str, list[tuple[str, str]]]:
    raw: dict[str, Any] = yaml.safe_load(LABELS_FILE.read_text(encoding="utf-8"))
    out: dict[str, list[tuple[str, str]]] = {}
    for subdir, items in raw.items():
        out[subdir] = [(entry["method"].upper(), entry["path"]) for entry in items]
    return out


@pytest.fixture(scope="module")
def labels() -> dict[str, list[tuple[str, str]]]:
    return _load_labels()


def _mined_pairs(subdir: str) -> set[tuple[str, str]]:
    sites = mine_repo(FIXTURE_ROOT / subdir)
    return {(s.method.upper(), s.path_template) for s in sites}


def test_per_fixture_perfect_recall(labels: dict[str, list[tuple[str, str]]]) -> None:
    """Every labeled endpoint per fixture must be discovered."""
    for subdir, expected in labels.items():
        found = _mined_pairs(subdir)
        missing = [pair for pair in expected if pair not in found]
        assert not missing, f"{subdir}: miner missed {missing} (found={sorted(found)})"


def test_aggregate_recall_at_least_90_percent(
    labels: dict[str, list[tuple[str, str]]],
) -> None:
    total = 0
    hits = 0
    for subdir, expected in labels.items():
        found = _mined_pairs(subdir)
        total += len(expected)
        hits += sum(1 for pair in expected if pair in found)
    recall = hits / total if total else 0.0
    assert recall >= 0.90, f"recall={recall:.2%} ({hits}/{total})"


def test_fixture_corpus_covers_all_supported_libraries(
    labels: dict[str, list[tuple[str, str]]],
) -> None:
    libs: set[str] = set()
    for subdir in labels:
        for site in mine_repo(FIXTURE_ROOT / subdir):
            libs.add(site.client_library)
    assert {"requests", "httpx", "fetch", "axios", "grpc"} <= libs
