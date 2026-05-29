"""Verify Alembic migrations create the expected schema."""

from __future__ import annotations

from sqlalchemy import Inspector


def test_migrations_create_core_tables(inspector: Inspector) -> None:
    tables = set(inspector.get_table_names())
    assert {"services", "contracts", "contract_versions", "clients"}.issubset(tables)


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


def test_unique_constraints(inspector: Inspector) -> None:
    uniques = {u["name"] for u in inspector.get_unique_constraints("contract_versions")}
    assert "uq_versions_service_hash" in uniques
    uniques_c = {u["name"] for u in inspector.get_unique_constraints("contracts")}
    assert "uq_contracts_service_name" in uniques_c
