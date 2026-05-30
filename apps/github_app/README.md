# Guardian — GitHub App (Probot)

A small Probot 13.x app that runs on every PR webhook in repos where it
is installed, computes a Guardian diff for the changed API spec, and
posts:

- A **Check Run** named "Guardian / API Contract Diff" via the Checks
  API, with `conclusion = failure` when breaking changes are present and
  the `guardian:accept-breaking` label is **not** set on the PR.
- A **PR comment** with the same Markdown body — including a
  per-client impact table.

The diff itself is computed by the Python `guardian` CLI (the App shells
out to it), keeping a single rule engine across the Action and the App.

Run results are forwarded to the Guardian backend at
`POST {GUARDIAN_API_URL}/ci/runs` so they can be reproduced from
history.

## Requirements

- Node 20+
- The Python `guardian` CLI installed on the same image
  (`pip install living-api-contract-guardian`)
- A GitHub App registered against the workspace's manifest (`app.yml`)
  with these env vars:
  - `APP_ID`, `PRIVATE_KEY`, `WEBHOOK_SECRET` — Probot standards
  - `GUARDIAN_API_URL` — base URL of the FastAPI backend
  - `GUARDIAN_SPEC_PATH` — repo-relative path to the OpenAPI spec
  - `GUARDIAN_BIN` — `guardian` (or an absolute path to the CLI)

## Local development

```bash
cd apps/github_app
npm install
APP_ID=... PRIVATE_KEY=... WEBHOOK_SECRET=... npm start
```

## Offline test (no real GitHub)

`@octokit/fixtures` ships canned webhook payloads. Replay one through
`probot receive`:

```bash
# Plays a pull_request.opened payload through the App handlers; the
# Probot logger prints which Checks / Issues APIs would have been hit.
GUARDIAN_BIN="$(which guardian)" \
GUARDIAN_API_URL=http://localhost:8000 \
GUARDIAN_SPEC_PATH=openapi.json \
npx probot receive \
  -e pull_request \
  -p test/fixtures/pull_request.opened.json \
  ./src/index.js
```

## Webhook signature verification

Probot's built-in `@octokit/webhooks` middleware enforces HMAC-SHA256
verification of every payload against `WEBHOOK_SECRET`; payloads with an
invalid signature are rejected before any handler in `src/index.js`
runs.

## Production deployment

The App is intentionally **separate from the FastAPI service** because
the Probot ecosystem is Node-native. The recommended layout is one
container for FastAPI (the Python backend) and a second container for
the Probot app, both behind the same reverse proxy.
