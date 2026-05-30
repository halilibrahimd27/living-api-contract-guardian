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

> Status: early build. The data model, service registry, contract
> ingestion (hashing + canonicalisation), health surface, CLI,
> migrations, the **static client AST miner** (Python + JS/TS + gRPC),
> the **traffic-replay contract augmentor** (HAR + gRPC log → merged
> "de-facto contract"), and the **evolution rule engine** (additive /
> behavioral / breaking classification for OpenAPI + protobuf) are in
> place; usage-analytics endpoints land in later milestones.

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
- **Static client mining** — point `guardian mine` at a client
  repo and the analyzer extracts the API calls it makes (HTTP method,
  OpenAPI-style path template, query/body field names, gRPC stub
  invocations) by walking the AST. No runtime instrumentation, no
  network traffic — just `tree-sitter` over the source.
- **Traffic-replay augmentation** — `POST /ingest/traffic` accepts a
  HAR (HTTP Archive) and/or a JSON-lines gRPC call log, infers JSON
  Schemas for each observed request/response (genson + enum + oneOf
  post-processing), matches URLs to known OpenAPI templates (with a
  numeric/UUID heuristic fallback), and stores per-field counts +
  `last_seen_at` for usage-decay tracking. The result is a materialized
  **de-facto contract** — the union of the static spec and what
  production actually does.

## Architecture

```
apps/
  api/        FastAPI app: /healthz, /services, contract upload
  cli/        `guardian` Typer CLI: version / health / migrate
packages/
  guardian_core/
    db.py       SQLAlchemy engine + session (DATABASE_URL-driven)
    models.py   Service, Contract, ContractVersion, Endpoint,
                Client, Usage, Deprecation, InferredEndpoint
    hashing.py  canonicalisation + content hashing of specs
    mining/     tree-sitter based static client miner
      python_visitor.py  requests / httpx / gRPC stub call sites
      js_visitor.py      fetch / axios call sites (JS + TS)
      path_normalize.py  URL -> OpenAPI {param} templates
      repo_scanner.py    walk a repo, persist findings
    traffic/    HAR + gRPC log ingestion → de-facto contract
      har_parser.py        haralyzer + ijson HAR streamer
      grpc_parser.py       JSONL gRPC call log parser
      schema_inference.py  genson + enum + anyOf post-processing
      url_match.py         OpenAPI route-tree match w/ heuristic fallback
      ingestor.py          orchestrator + idempotent ON CONFLICT upserts
      defacto.py           static spec ⊕ observed → merged contract
      _merge.py            shared JSON-schema union helper
  guardian_diff/   contract evolution rule engine
    models.py      RawChange / ChangeRecord / ChangeReport pydantic v2
    openapi.py     OpenAPI 3.x raw-change walker
    proto.py       protobuf FileDescriptorSet walker
    ruleset.py     YAML rule loader + per-rule classify()
    rules/default.yml  shipped additive / behavioral / breaking ruleset
    clients.py     join changes against InferredEndpoint catalogue
    spectral.py    optional Spectral CLI integration (vendored)
    engine.py      diff_contracts() — walk + classify + summarize
    ...
alembic/        schema migrations
fixtures/       sample client repos + labels.yaml (recall corpus)
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
| `InferredEndpoint` | A call site mined from a client repo at a given commit SHA. |

`ChangeReport` (returned by `POST /diff`, not persisted) is a transient
pydantic-v2 payload: `{contract_kind, changes[ChangeRecord], summary,
spectral_findings, ruleset_id}`, where each
`ChangeRecord = {change_id, kind, location, verdict, rule_id, rationale,
affected_clients[], before, after, detail}`.

## Stack

Python 3.11 · FastAPI · SQLAlchemy 2 · Alembic · Pydantic v2 · Typer ·
Redis (health probe) · SQLite by default, Postgres in production ·
`tree-sitter-languages` 1.10 (precompiled grammars) for client mining.

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
guardian version                       # package version + git SHA
guardian health                        # probe DB + Redis connectivity
guardian migrate                       # alembic upgrade head
guardian mine ./path/to/client-repo \  # mine a client repo for API calls
  --name acme/users-client \           #   logical repo identifier
  --sha 0123abcd                       #   commit SHA (auto-detected if omitted)
```

### Static client miner

`guardian mine <repo>` walks a checkout, runs the per-language
tree-sitter visitor, and persists one `InferredEndpoint` row per
discovered call site. Out of the box the miner recognises:

| Language    | Libraries / patterns                                  |
|-------------|-------------------------------------------------------|
| Python      | `requests`, `httpx` (sync + `AsyncClient`), `*_pb2_grpc` stubs |
| JavaScript  | `fetch`, `axios` (default + renamed import + `require`) |
| TypeScript  | `fetch`, `axios` (same shapes as JS, `.tsx` supported) |

Path templates are normalized OpenAPI-style: f-string / template-literal
placeholders become `{name}`, static numeric/UUID segments collapse to
`{id}`, and scheme + host are stripped. Field names (query, JSON body,
gRPC request kwargs) are captured. Re-running the miner against the
same `(repo, commit_sha)` is a no-op thanks to a per-row content hash.

A small fixture corpus under `fixtures/clients/` plus
`fixtures/clients/labels.yaml` gives the recall metric exercised by
`tests/test_mining_recall.py` (≥ 90% across all libraries).

### HTTP API (current surface)

| Method | Path                         | Description                          |
|--------|------------------------------|--------------------------------------|
| GET    | `/healthz`                   | Deep health: version, git SHA, DB, Redis |
| POST   | `/services`                  | Register a service                   |
| GET    | `/services/{id}`             | Fetch a service                      |
| POST   | `/services/{id}/contracts`   | Upload a contract version (hashed + deduped) |
| POST   | `/ingest/traffic`            | Ingest HAR + gRPC log → de-facto contract id |
| GET    | `/ingest/defacto/{id}`       | Fetch a materialized de-facto contract |
| POST   | `/diff`                      | Diff two contract versions → classified ChangeReport |

The traffic endpoint accepts a multipart form with `service_id`
(required), an optional `client_id`, and at least one of `har` (an HTTP
Archive file) or `grpc_log` (a JSON-lines gRPC call log). It returns
the new `defacto_contract` row's id plus a per-batch summary; re-posting
the exact same payload is idempotent (the response carries
`is_duplicate_batch: true` and field-usage counts do not double).

```bash
curl -sS -X POST http://127.0.0.1:8000/ingest/traffic \
  -F "service_id=$SVC" \
  -F "client_id=billing-worker" \
  -F "har=@fixtures/traffic/sample.har;type=application/json" \
  -F "grpc_log=@fixtures/traffic/sample.grpc.jsonl;type=application/jsonl"
```

(Client-mined endpoints are not yet exposed over HTTP; consume them via
the database, or follow up with `guardian mine` in CI.)

### Evolution rule engine (`POST /diff`)

Submit two contract versions and the Guardian returns a structured
`ChangeReport` — one `ChangeRecord` per atomic delta, each tagged with a
`verdict` in `{additive, behavioral, breaking}`, the matching `rule_id`,
a human-readable `rationale`, and the list of client repos
(`affected_clients`) whose mined call sites land on the changed
location.

* **OpenAPI** changes are walked structurally (paths / operations /
  parameters / request body / responses / enums / `components.schemas`).
  When a Spectral CLI is vendored under `vendor/bin/spectral` and the
  request opts in with `run_spectral: true`, lint findings are attached
  to the report as `spectral_findings`.
* **Protobuf** changes are computed from two
  `FileDescriptorSet` blobs (produced by `protoc --descriptor_set_out`).
  Field number / type / label transitions and RPC signature changes are
  canonical wire-format breaks.
* The default ruleset ships at `packages/guardian_diff/rules/default.yml`.
  Callers may override any rule by id by passing custom YAML in
  `rules_yaml`; overrides merge last-write-wins by `id`, with optional
  glob scoping by location.

```bash
curl -sS -X POST http://127.0.0.1:8000/diff \
  -H 'content-type: application/json' \
  -d '{
        "kind": "openapi",
        "before_spec": {"openapi":"3.0.0","info":{"title":"x","version":"1"},
                        "paths":{"/users":{"get":{"responses":{"200":{"description":"ok"}}}}}},
        "after_spec":  {"openapi":"3.0.0","info":{"title":"x","version":"1"},
                        "paths":{}}
      }'
# → {"contract_kind":"openapi","changes":[{"change_id":"...",
#     "kind":"openapi.path.removed","verdict":"breaking",
#     "rule_id":"OAS-PATH-REMOVED", ...}], ...}
```

## Docker

```bash
docker compose up --build
```

Brings up Postgres + the API (`/healthz` is the container healthcheck).

## Development

```bash
make verify                  # the full CI gate: lint + format + types + tests
# or, individually:
make lint                    # ruff check .
make format-check            # black --check .
make types                   # mypy --strict packages apps
make test                    # pytest -q  (unit + Hypothesis property tests)
```

`make verify` is what GitHub Actions runs on every push — see
`.github/workflows/ci.yml`. The Reviewer uses the same gate.

## License

See repository.
