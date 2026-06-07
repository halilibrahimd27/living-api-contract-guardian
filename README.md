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

> Status: active build. The data model, service registry, contract
> ingestion (hashing + canonicalisation), health surface, CLI,
> migrations, the **static client AST miner** (Python + JS/TS + gRPC),
> the **traffic-replay contract augmentor** (HAR + gRPC log → merged
> "de-facto contract"), the **evolution rule engine** (additive /
> behavioral / breaking classification for OpenAPI + protobuf), the
> **GitHub App + reusable Action** that gate PRs on breaking diffs,
> the **LLM-drafted per-client migration guides** (cached,
> tree-sitter-validated, served at `GET /guides/{diff_id}/{client_id}`),
> and the **deprecation campaign orchestrator** (Redis-backed RQ scheduler,
> EWMA decay curves, automated reminder PRs, `GET /campaigns/{id}`)
> are in place.

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
- **Evolution rule engine** — `POST /diff` (and `guardian diff` on
  the CLI) walks two contract versions, classifies each atomic delta
  as `additive` / `behavioral` / `breaking` against a YAML ruleset,
  joins the changes against the mined client catalogue, and returns a
  structured `ChangeReport` with a per-change `affected_clients` list.
- **CI gate for PRs** — a composite GitHub Action plus a Probot App
  run the diff on every pull request, fail the check when breaking
  changes are introduced (unless the `guardian:accept-breaking` label
  is set), and post a check-run + comment with a per-client impact
  table and inline annotations on the spec file.
- **Per-client migration guides** — `GET /guides/{diff_id}/{client_id}`
  returns an LLM-drafted Markdown migration guide tailored to one
  downstream client's mined call sites. Guides are deterministically
  cached by `hash(diff_id, client_id, prompt_version, model)`, and
  every fenced code block is tree-sitter-validated before the guide is
  persisted (the LLM is retried with a stricter prompt on parse error).

## Architecture

```
apps/
  api/        FastAPI app: /healthz, /services, /diff, /ingest, /ci, /guides
    routes/
      services.py  register service + upload contract versions
      diff.py      POST /diff → classified ChangeReport
      ingest.py    POST /ingest/traffic + GET /ingest/defacto/{id}
      ci.py        POST /ci/runs + GET /ci/runs/{owner}/{repo}/{pr}
      guides.py    GET /guides/{diff_id}/{client_id}
  cli/        `guardian` Typer CLI: version / health / migrate / mine / diff
  github_app/ Probot 13.x App (Node 20): PR checks + comments
packages/
  guardian_core/
    db.py       SQLAlchemy engine + session (DATABASE_URL-driven)
    models.py   all ORM models (Service → Guide)
    schemas.py  Pydantic v2 HTTP-boundary schemas
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
    ci_format.py   GitHub Markdown + annotations formatter
  guardian_guides/ LLM-powered per-client migration guide generator
    models.py      GuideRequest / GuideContext / GuideResult pydantic v2
    llm.py         LiteLLM provider abstraction + MockLLMProvider
    service.py     GuideService: cache → prompt → LLM → validate
    syntax.py      tree-sitter snippet validation
    prompts/       Jinja2 prompt templates
alembic/        schema migrations (6 versions)
fixtures/       sample client repos + labels.yaml (recall corpus)
tests/          pytest unit suite + Hypothesis property tests
```

Data model (one line each):

| Model               | Purpose                                                              |
|---------------------|----------------------------------------------------------------------|
| `Service`           | A named, owned API producer.                                         |
| `Contract`          | A named spec under a service (`openapi` / `proto`).                  |
| `ContractVersion`   | An immutable, content-hashed snapshot (raw + canonical blob).        |
| `Endpoint`          | One operation extracted from a version, with a fingerprint.          |
| `Client`            | A named API consumer.                                                |
| `Usage`             | A client's calls to an endpoint over a time window.                  |
| `Deprecation`       | A planned/active deprecation of an endpoint.                         |
| `InferredEndpoint`  | A call site mined from a client repo at a given commit SHA.          |
| `IngestBatch`       | One traffic-ingest invocation; content-hashed for dedup.             |
| `ObservedEndpoint`  | An endpoint inferred from traffic and merged across batches.         |
| `FieldUsage`        | Per-field telemetry (count + last_seen_at) under an observed endpoint.|
| `DefactoContract`   | Materialized static spec ⊕ observed traffic contract.                |
| `ContractDiff`      | A persisted `ChangeReport` produced by `POST /diff`.                 |
| `Guide`             | A cached LLM-drafted per-client migration guide.                     |
| `CiRun`             | A persisted GitHub PR check run produced by the App.                 |
| `Campaign`          | A deprecation campaign tracking one endpoint/field through decay.    |
| `CampaignMetric`    | One EWMA sample point for a campaign's decay curve.                  |
| `ReminderPR`        | A reminder pull-request opened on a client repo for a campaign.      |

`ChangeReport` (returned by `POST /diff`, which also persists a
`ContractDiff` row and stamps the new `diff_id` onto the response so
callers can hand it off to `GET /guides/{diff_id}/{client_id}`) is a
pydantic-v2 payload: `{contract_kind, changes[ChangeRecord], summary,
spectral_findings, ruleset_id, diff_id?}`, where each
`ChangeRecord = {change_id, kind, location, verdict, rule_id, rationale,
affected_clients[], before, after, detail}`. The `diff_id` field is
unset on in-process invocations (`guardian_diff.diff_contracts`) that
do not pass a session.

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
guardian diff \                        # diff two specs (used by GitHub Action)
  --base before.json --head after.json \
  --format github                      #   github | text | json
  # exits with code 2 on breaking changes unless --accept-breaking is set
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

| Method | Path                            | Description                                              |
|--------|---------------------------------|----------------------------------------------------------|
| GET    | `/healthz`                      | Deep health: version, git SHA, DB, Redis                 |
| POST   | `/services`                     | Register a service                                       |
| GET    | `/services/{id}`                | Fetch a service                                          |
| POST   | `/services/{id}/contracts`      | Upload a contract version (hashed + deduped)             |
| POST   | `/ingest/traffic`               | Ingest HAR + gRPC log → de-facto contract id             |
| GET    | `/ingest/defacto/{id}`          | Fetch a materialized de-facto contract                   |
| POST   | `/diff`                         | Diff two contract versions → classified ChangeReport     |
| POST   | `/ci/runs`                      | Upsert a persisted GitHub PR check run (used by the App) |
| GET    | `/ci/runs/{owner}/{repo}/{pr}`  | Most recent persisted CI run for a PR                    |
| GET    | `/guides/{diff_id}/{client_id}` | LLM-drafted migration guide (cached, `text/markdown`)    |
| POST   | `/campaigns`                    | Create a deprecation campaign (draft state)              |
| GET    | `/campaigns/{id}`               | Decay curve, remaining clients, reminder PRs             |
| PATCH  | `/campaigns/{id}`               | Update mutable campaign fields                           |
| POST   | `/campaigns/{id}/transition`    | Fire a state-machine trigger manually                    |
| POST   | `/campaigns/{id}/evaluate`      | Sample usage + drive state transitions (inline)          |

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

### GitHub CI integration

Guardian ships with a **reusable composite GitHub Action** plus a
**Probot 13.x App** that together gate PRs on breaking API changes.

The composite action (`./action.yml` at the repo root) materialises the
spec at both refs via `git show <sha>:<spec_path>`, then shells out to
`guardian diff`. It fails the workflow when breaking changes are
detected unless `accept_breaking: true` is set (typically by checking
for the `guardian:accept-breaking` PR label):

```yaml
# .github/workflows/api-diff.yml
on: pull_request
jobs:
  diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: your-org/living-api-contract-guardian@v1
        with:
          base_sha: ${{ github.event.pull_request.base.sha }}
          head_sha: ${{ github.event.pull_request.head.sha }}
          spec_path: openapi.json
          accept_breaking: ${{ contains(github.event.pull_request.labels.*.name, 'guardian:accept-breaking') }}
```

The CLI has the same surface for local use:

```bash
guardian diff \
  --base fixtures/diff/openapi.base.json \
  --head fixtures/diff/openapi.head_breaking.json \
  --format github \
  --summary-out $GITHUB_STEP_SUMMARY
echo "exit=$?"  # → 2 when breaking changes are detected
```

The Probot App (under `apps/github_app/`) subscribes to PR webhooks,
posts a Checks API check run + a PR comment with the same Markdown
body (including the per-client impact table), and upserts the result
to `POST /ci/runs` so the run is keyed by `(repo, pr_number,
head_sha)` in `ci_runs`. The check run carries one **Checks API
annotation per change** (anchored on the spec file, `failure` /
`warning` / `notice` matched to verdict) so each delta surfaces inline
on the PR's *Files Changed* view, with the structural pointer plus
`rule_id` + `rationale`. Webhook payloads are HMAC-verified by
`@octokit/webhooks` before any handler runs. The App ships an
offline test suite (`node --test apps/github_app/test/*.test.js`)
that replays canned `pull_request.opened` / `pull_request.labeled`
fixtures through `handlePullRequest` with a stubbed octokit + injected
`runDiff`/`postRun`, asserting the resulting check run, annotations,
comment body, and backend POST — no Python, no GitHub, no network.
See [`apps/github_app/README.md`](apps/github_app/README.md) for the
installation and full replay workflow.

### Per-client migration guides (`GET /guides/{diff_id}/{client_id}`)

Once `POST /diff` has persisted a `ContractDiff`, the guides endpoint
turns that diff into a Markdown migration guide tailored to one
downstream client's mined call sites:

```bash
curl -sS http://127.0.0.1:8000/guides/$DIFF_ID/acme%2Fusers-client
# → text/markdown body; response headers carry:
#     X-Guide-Cache: hit|miss
#     X-Guide-Model: gpt-4o-mini
#     X-Guide-Prompt-Version: v1
#     X-Guide-Retries: 0
```

How it works:

* The LLM call is abstracted behind a `LLMProvider` protocol. Production
  uses `LiteLLMProvider` (which routes through `litellm.completion` with
  `temperature=0` / `seed=0`); tests inject a `MockLLMProvider` keyed by
  the SHA-256 of the rendered prompt.
* Guides are cached by `hash(diff_id, client_id, prompt_version, model)`
  in the `guides` table — a second call with the same key never invokes
  the LLM. Bump `PROMPT_VERSION` in `guardian_guides.service` to
  invalidate the cache after a template change.
* Every fenced code block in the generated Markdown is parsed with
  `tree-sitter` (Python / JS / TS / TSX). On parse error the service
  retries with a stricter prompt up to `RETRY_LIMIT` times before
  surfacing `502 Bad Gateway`.
* The prompt template (`packages/guardian_guides/prompts/`) is grounded
  by three explicit sections: the `ChangeReport` entries affecting the
  client, up to N mined call sites with surrounding source lines, and a
  language-specific style hint.

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
