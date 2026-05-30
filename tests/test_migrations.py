"""Verify Alembic migrations create the expected schema."""

from __future__ import annotations

from sqlalchemy import Inspector

CORE_TABLES = {
    "services",
    "contracts",
    "contract_versions",
    "clients",
    "endpoints",
    "usages",
    "deprecations",
}


def test_migrations_create_core_tables(inspector: Inspector) -> None:
    tables = set(inspector.get_table_names())
    assert CORE_TABLES.issubset(tables), f"missing tables: {CORE_TABLES - tables}"


def test_services_columns(inspector: Inspector) -> None:
    cols = {c["name"] for c in inspector.get_columns("services")}
    assert {"id", "name", "owner", "created_at"}.issubset(cols)


def test_contracts_columns(inspector: Inspector) -> None:
    cols = {c["name"] for c in inspector.get_columns("contracts")}
    assert {"id", "service_id", "name", "kind", "created_at"}.issubset(cols)


def test_contract_versions_columns(inspector: Inspector) -> None:
    cols = {c["name"] for c in inspector.get_columns("contract_versions")}
    assert {
        "id",
        "contract_id",
        "service_id",
        "version_hash",
        "raw_blob",
        "canonical_blob",
        "spec_metadata",
        "created_at",
    }.issubset(cols)


def test_endpoints_columns(inspector: Inspector) -> None:
    cols = {c["name"] for c in inspector.get_columns("endpoints")}
    assert {
        "id",
        "contract_version_id",
        "service_id",
        "method",
        "path",
        "operation_id",
        "fingerprint",
        "spec_excerpt",
        "created_at",
    }.issubset(cols)


def test_usages_columns(inspector: Inspector) -> None:
    cols = {c["name"] for c in inspector.get_columns("usages")}
    assert {
        "id",
        "endpoint_id",
        "client_id",
        "window_start",
        "window_end",
        "request_count",
        "source",
        "created_at",
    }.issubset(cols)


def test_deprecations_columns(inspector: Inspector) -> None:
    cols = {c["name"] for c in inspector.get_columns("deprecations")}
    assert {
        "id",
        "contract_version_id",
        "endpoint_id",
        "status",
        "reason",
        "sunset_at",
        "notes",
        "created_at",
    }.issubset(cols)


def test_ci_runs_table(inspector: Inspector) -> None:
    tables = set(inspector.get_table_names())
    assert "ci_runs" in tables
    cols = {c["name"] for c in inspector.get_columns("ci_runs")}
    assert {
        "id",
        "repo",
        "pr_number",
        "head_sha",
        "base_sha",
        "conclusion",
        "report_json",
        "bypass_label_present",
        "check_run_id",
        "created_at",
    }.issubset(cols)
    uniques = {u["name"] for u in inspector.get_unique_constraints("ci_runs")}
    assert "uq_ci_runs_repo_pr_sha" in uniques


def test_guides_tables(inspector: Inspector) -> None:
    tables = set(inspector.get_table_names())
    assert {"contract_diffs", "guides"}.issubset(tables)
    cd_cols = {c["name"] for c in inspector.get_columns("contract_diffs")}
    assert {"id", "contract_kind", "report_json", "created_at"}.issubset(cd_cols)
    g_cols = {c["name"] for c in inspector.get_columns("guides")}
    assert {
        "id",
        "diff_id",
        "client_id",
        "prompt_version",
        "model",
        "prompt_hash",
        "markdown",
        "retries",
        "created_at",
    }.issubset(g_cols)
    g_uniques = {u["name"] for u in inspector.get_unique_constraints("guides")}
    assert "uq_guides_prompt_hash" in g_uniques


def test_unique_constraints(inspector: Inspector) -> None:
    uniques = {u["name"] for u in inspector.get_unique_constraints("contract_versions")}
    assert "uq_versions_service_hash" in uniques
    uniques_c = {u["name"] for u in inspector.get_unique_constraints("contracts")}
    assert "uq_contracts_service_name" in uniques_c
    uniques_e = {u["name"] for u in inspector.get_unique_constraints("endpoints")}
    assert "uq_endpoints_version_method_path" in uniques_e
    uniques_u = {u["name"] for u in inspector.get_unique_constraints("usages")}
    assert "uq_usages_endpoint_client_window" in uniques_u
    uniques_d = {u["name"] for u in inspector.get_unique_constraints("deprecations")}
    assert "uq_deprecations_version_endpoint" in uniques_d
