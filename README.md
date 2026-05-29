# Living API Contract Guardian

A self-hostable registry that keeps a **living, versioned history of every
API contract in your system** — OpenAPI and protobuf specs — so you can
answer the questions that break microservice fleets in production:

- *What changed between version N and N+1 of this service's contract?*
- *Which endpoints exist right now, and which clients still call the ones
  we want to deprecate?*
- *Has this exact spec been uploaded before?* (content-addressed dedup)

It is the missing "source of truth" layer between your services and your
clients: upload a contract on every deploy, and the Guardian fingerprints
it, extracts its endpoints, and tracks who uses what — so a deprecation
is a data question, not an archaeology project.

> Status: early scaffold (milestone 1 of a multi-milestone build). The
> data model, service registry, contract ingestion (hashing +
> canonicalisation), health surface, CLI, and migrations are in place;
> endpoint-diffing and usage-analytics endpoints land in later milestones.

## Why this is useful

Most teams discover a breaking API change when a downstream client falls
over. Schema-registry tooling exists for *events* (Kafka/Avro) but the
HTTP/gRPC contract space is underserved. The Guardian gives you:

- **Content-addressed versions** — every uploaded spec is hashed over its
  *canonical* form, so semantically-identical re-uploads dedup and real
  changes get a new immutable version.
- **Endpoint extraction** — each version's operations are stored with a
  stable fingerprint, the unit a future milestone diffs to flag
  added/removed/changed endpoints.
- **Usage tracking** — record which client hit which endpoint in which
  time window, so "safe to deprecate?" becomes a query.

## Architecture

```
apps/
  api/        FastAPI app: /healthz, /services, contract upload
  cli/        `guardian` Typer CLI: version / health / migrate
packages/
  guardian_core/
    db.py       SQLAlchemy engine + session (DATABASE_URL-driven)
    models.py   Service, Contract, ContractVersion, Endpoint,
                Client, Usage, Deprecation
    hashing.py  canonicalisation + content hashing of specs
    ...
alembic/        schema migrations
tests/          pytest unit suite + Hypothesis property tests
```

Data model (one line each):

| Model            | Purpose                                                       |
|------------------|---------------------------------------------------------------|
| `Service`        | A named, owned API producer.                                  |
| `Contract`       | A named spec under a service (`openapi` / `proto`).           |
| `ContractVersion`| An immutable, content-hashed snapshot (raw + canonical blob). |
| `Endpoint`       | One operation extracted from a version, with a fingerprint.   |
| `Client`         | A named API consumer.                                         |
| `Usage`          | A client's calls to an endpoint over a time window.           |
| `Deprecation`    | A planned/active deprecation of an endpoint.                  |

## Stack

Python 3.11 · FastAPI · SQLAlchemy 2 · Alembic · Pydantic v2 · Typer ·
Redis (health probe) · SQLite by default, Postgres in production.

## Quickstart

```bash
# 1. Install (with dev extras for tests/lint)
pip install -e ".[dev]"

# 2. Point at a database (defaults to a local SQLite file if unset)
export DATABASE_URL="sqlite:///guardian.db"        # or postgresql://...

# 3. Apply migrations
guardian migrate

# 4. Run the API
uvicorn apps.api.main:app --reload
#   → http://127.0.0.1:8000/docs  (interactive OpenAPI UI)
```

### CLI

```bash
guardian version     # package version + git SHA
guardian health      # probe DB + Redis connectivity
guardian migrate     # alembic upgrade head
```

### HTTP API (current surface)

| Method | Path                         | Description                          |
|--------|------------------------------|--------------------------------------|
| GET    | `/healthz`                   | Deep health: version, git SHA, DB, Redis |
| POST   | `/services`                  | Register a service                   |
| GET    | `/services/{id}`             | Fetch a service                      |
| POST   | `/services/{id}/contracts`   | Upload a contract version (hashed + deduped) |

## Docker

```bash
docker compose up --build
```

Brings up Postgres + the API (`/healthz` is the container healthcheck).

## Development

```bash
ruff check .                 # lint
mypy --strict apps packages  # types
pytest -q                    # unit + property tests
```

The full gate (`ruff` + `black --check` + `mypy --strict` + `pytest`)
also runs in GitHub Actions on every push — see `.github/workflows/ci.yml`.

## License

See repository.
