// @ts-check
/**
 * Offline CI tests for the Guardian Probot App.
 *
 * Instead of spinning up the full Probot HTTP server we exercise
 * `handlePullRequest` directly with a hand-rolled `ctx` that records
 * every Octokit call. The webhook payloads under `test/fixtures/` are
 * shaped exactly like the canned JSON `@octokit/fixtures` ships for
 * `pull_request.opened` / `pull_request.labeled`, so this suite is the
 * unit-test equivalent of:
 *
 *     npx probot receive -e pull_request \
 *         -p test/fixtures/pull_request.opened.json ./src/index.js
 *
 * but it asserts the resulting check run + PR comment + backend POST
 * without spawning Python, talking to GitHub, or running a network
 * loop. The `runDiff` and `postRun` collaborators are injected as
 * overrides so we never shell out to the `guardian` CLI.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  handlePullRequest,
  BYPASS_LABEL,
  CHECK_NAME,
  ALLOWED_PR_ACTIONS,
} from "../src/index.js";
import { buildAnnotations, ANNOTATION_LIMIT } from "../src/annotations.js";
import { summarizeReport } from "../src/diff.js";

const HERE = dirname(fileURLToPath(import.meta.url));

/** @returns {import("../src/diff.js").ChangeReport} */
function breakingReport() {
  return {
    contract_kind: "openapi",
    ruleset_id: "default",
    summary: { breaking: 1, behavioral: 0, additive: 0, total: 1 },
    changes: [
      {
        change_id: "abc12345",
        kind: "openapi.path.removed",
        location: "paths./users",
        verdict: "breaking",
        rule_id: "OAS-PATH-REMOVED",
        rationale: "Path /users was removed — clients calling it will 404.",
        affected_clients: ["acme/users-client", "acme/billing"],
      },
    ],
  };
}

/** @returns {import("../src/diff.js").ChangeReport} */
function additiveReport() {
  return {
    contract_kind: "openapi",
    ruleset_id: "default",
    summary: { breaking: 0, behavioral: 0, additive: 1, total: 1 },
    changes: [
      {
        change_id: "abc99999",
        kind: "openapi.path.added",
        location: "paths./pets",
        verdict: "additive",
        rule_id: "OAS-PATH-ADDED",
        rationale: "Path /pets was added.",
        affected_clients: [],
      },
    ],
  };
}

/**
 * Build a fake Probot `ctx` from a recorded webhook payload.
 *
 * @param {Record<string, any>} payload
 * @param {{
 *   getContentByRef?: Record<string, string>,
 *   getContentMissing?: boolean,
 * }} [opts]
 */
function makeCtx(payload, opts = {}) {
  /** @type {Array<{name: string, args: unknown}>} */
  const calls = [];
  const owner = payload.repository.owner.login;
  const repo = payload.repository.name;
  const refMap = opts.getContentByRef ?? {
    [payload.pull_request.base.sha]:
      '{"openapi":"3.0.0","info":{"title":"x","version":"1"},"paths":{"/users":{"get":{"responses":{"200":{"description":"ok"}}}}}}',
    [payload.pull_request.head.sha]:
      '{"openapi":"3.0.0","info":{"title":"x","version":"1"},"paths":{}}',
  };
  let nextCheckRunId = 555000;
  const ctx = {
    payload,
    repo: () => ({ owner, repo }),
    log: {
      info: (..._args) => calls.push({ name: "log.info", args: _args }),
      warn: (..._args) => calls.push({ name: "log.warn", args: _args }),
      error: (..._args) => calls.push({ name: "log.error", args: _args }),
    },
    octokit: {
      repos: {
        getContent: async (args) => {
          calls.push({ name: "repos.getContent", args });
          if (opts.getContentMissing) {
            throw new Error("404 spec not found");
          }
          const content = refMap[args.ref];
          if (content === undefined) {
            throw new Error(`no fixture for ref ${args.ref}`);
          }
          return {
            data: {
              type: "file",
              content: Buffer.from(content, "utf-8").toString("base64"),
            },
          };
        },
      },
      checks: {
        create: async (args) => {
          calls.push({ name: "checks.create", args });
          return { data: { id: nextCheckRunId++ } };
        },
        update: async (args) => {
          calls.push({ name: "checks.update", args });
          return { data: { id: args.check_run_id } };
        },
      },
      issues: {
        createComment: async (args) => {
          calls.push({ name: "issues.createComment", args });
          return { data: { id: 1 } };
        },
      },
    },
  };
  return { ctx, calls };
}

/** Find the first recorded call by name (for terse assertions). */
function callOf(calls, name) {
  const c = calls.find((c) => c.name === name);
  assert.ok(c, `expected a call to ${name}; got: ${calls.map((c) => c.name).join(", ")}`);
  return c;
}

test("ALLOWED_PR_ACTIONS contains the expected webhook subactions", () => {
  for (const action of [
    "opened",
    "reopened",
    "synchronize",
    "labeled",
    "unlabeled",
    "ready_for_review",
  ]) {
    assert.ok(ALLOWED_PR_ACTIONS.has(action), `expected ${action}`);
  }
  assert.equal(ALLOWED_PR_ACTIONS.has("closed"), false);
});

test("handlePullRequest skips events whose action is not in the allow-list", async () => {
  /** @type {string[]} */
  const calls = [];
  // Even without a runDiff stub the function should bail before reaching it.
  await handlePullRequest(
    /** @type {any} */ ({
      payload: { action: "closed", pull_request: { labels: [], base: {}, head: {} } },
      repo: () => ({ owner: "x", repo: "y" }),
      log: { info() {}, warn() {}, error() {} },
      // A bare octokit — any access would throw, so we can prove the
      // handler bailed before touching it.
      octokit: new Proxy({}, { get: () => () => calls.push("unexpected") }),
    }),
    {
      runDiff: async () => {
        calls.push("runDiff");
        throw new Error("should not run");
      },
      postRun: async () => {
        calls.push("postRun");
        return {};
      },
    },
  );
  assert.deepEqual(calls, []);
});

test("handlePullRequest on a breaking diff posts a failing check, an inline comment, and persists the run", async () => {
  const payload = JSON.parse(
    await readFile(join(HERE, "fixtures", "pull_request.opened.json"), "utf-8"),
  );
  const { ctx, calls } = makeCtx(payload);
  /** @type {Array<{baseUrl: string, body: any}>} */
  const persisted = [];
  await handlePullRequest(/** @type {any} */ (ctx), {
    specPath: "openapi.json",
    backendUrl: "http://backend.test",
    runDiff: async () => breakingReport(),
    postRun: async (baseUrl, body) => {
      persisted.push({ baseUrl, body });
      return { id: "run-1" };
    },
  });

  // Spec fetched at both refs.
  const getContentCalls = calls.filter((c) => c.name === "repos.getContent");
  assert.equal(getContentCalls.length, 2);
  for (const c of getContentCalls) {
    assert.equal(c.args.path, "openapi.json");
  }

  // Check run opened, then completed with conclusion: failure.
  const create = callOf(calls, "checks.create");
  assert.equal(create.args.name, CHECK_NAME);
  assert.equal(create.args.status, "in_progress");
  assert.equal(create.args.head_sha, payload.pull_request.head.sha);

  const update = callOf(calls, "checks.update");
  assert.equal(update.args.status, "completed");
  assert.equal(update.args.conclusion, "failure");
  assert.ok(update.args.output.title.includes("1 breaking"));

  // Annotations are anchored on the spec file at line 1, marked failure.
  const annotations = update.args.output.annotations;
  assert.ok(Array.isArray(annotations));
  assert.equal(annotations.length, 1);
  assert.equal(annotations[0].path, "openapi.json");
  assert.equal(annotations[0].start_line, 1);
  assert.equal(annotations[0].end_line, 1);
  assert.equal(annotations[0].annotation_level, "failure");
  assert.match(annotations[0].title, /OAS-PATH-REMOVED/);
  assert.match(annotations[0].message, /paths\.\/users/);

  // PR comment body includes the per-client impact summary table.
  const comment = callOf(calls, "issues.createComment");
  assert.equal(comment.args.issue_number, 42);
  assert.match(comment.args.body, /Per-client impact/);
  assert.match(comment.args.body, /acme\/users-client/);
  assert.match(comment.args.body, /acme\/billing/);
  // The "Breaking changes" subsection lists the structural location.
  assert.match(comment.args.body, /Breaking changes/);
  assert.match(comment.args.body, /paths\.\/users/);

  // Run persisted to the Guardian backend keyed by (repo, pr, head_sha).
  assert.equal(persisted.length, 1);
  assert.equal(persisted[0].baseUrl, "http://backend.test");
  assert.equal(persisted[0].body.repo, "acme/users");
  assert.equal(persisted[0].body.pr_number, 42);
  assert.equal(persisted[0].body.head_sha, payload.pull_request.head.sha);
  assert.equal(persisted[0].body.base_sha, payload.pull_request.base.sha);
  assert.equal(persisted[0].body.conclusion, "failure");
  assert.equal(persisted[0].body.bypass_label_present, false);
  assert.equal(persisted[0].body.check_run_id, create.args ? 555000 : -1);
});

test("handlePullRequest on a breaking diff with the bypass label flips the conclusion to success", async () => {
  const payload = JSON.parse(
    await readFile(join(HERE, "fixtures", "pull_request.labeled_bypass.json"), "utf-8"),
  );
  assert.ok(payload.pull_request.labels.some((l) => l.name === BYPASS_LABEL));
  const { ctx, calls } = makeCtx(payload);
  /** @type {any[]} */
  const persisted = [];
  let receivedAcceptBreaking = null;
  await handlePullRequest(/** @type {any} */ (ctx), {
    specPath: "openapi.json",
    backendUrl: "http://backend.test",
    runDiff: async (_b, _h, opts) => {
      receivedAcceptBreaking = opts?.acceptBreaking ?? null;
      return breakingReport();
    },
    postRun: async (_baseUrl, body) => {
      persisted.push(body);
      return { id: "run-2" };
    },
  });

  // The diff wrapper was told to honour the bypass label.
  assert.equal(receivedAcceptBreaking, true);

  // Check is still completed, but conclusion is success.
  const update = callOf(calls, "checks.update");
  assert.equal(update.args.status, "completed");
  assert.equal(update.args.conclusion, "success");

  // Comment surfaces that the gate was bypassed by the label.
  const comment = callOf(calls, "issues.createComment");
  assert.match(comment.args.body, new RegExp(BYPASS_LABEL));
  assert.match(comment.args.body, /bypassed/);

  // Backend run row records the bypass for audit.
  assert.equal(persisted.length, 1);
  assert.equal(persisted[0].conclusion, "success");
  assert.equal(persisted[0].bypass_label_present, true);
});

test("handlePullRequest on a purely-additive diff posts a passing check", async () => {
  const payload = JSON.parse(
    await readFile(join(HERE, "fixtures", "pull_request.opened.json"), "utf-8"),
  );
  const { ctx, calls } = makeCtx(payload);
  /** @type {any[]} */
  const persisted = [];
  await handlePullRequest(/** @type {any} */ (ctx), {
    specPath: "openapi.json",
    backendUrl: "http://backend.test",
    runDiff: async () => additiveReport(),
    postRun: async (_baseUrl, body) => {
      persisted.push(body);
      return { id: "run-3" };
    },
  });

  const update = callOf(calls, "checks.update");
  assert.equal(update.args.conclusion, "success");

  // Annotations exist but at the `notice` level.
  const annotations = update.args.output.annotations;
  assert.equal(annotations.length, 1);
  assert.equal(annotations[0].annotation_level, "notice");

  // Comment still includes the per-client impact section even when no
  // downstream clients are affected — it explicitly says so.
  const comment = callOf(calls, "issues.createComment");
  assert.match(comment.args.body, /Per-client impact/);
  assert.match(comment.args.body, /No downstream client repos are affected/);

  assert.equal(persisted[0].conclusion, "success");
});

test("handlePullRequest skips silently when the spec is absent at the PR refs", async () => {
  const payload = JSON.parse(
    await readFile(join(HERE, "fixtures", "pull_request.opened.json"), "utf-8"),
  );
  const { ctx, calls } = makeCtx(payload, { getContentMissing: true });
  let ranDiff = false;
  await handlePullRequest(/** @type {any} */ (ctx), {
    runDiff: async () => {
      ranDiff = true;
      return breakingReport();
    },
    postRun: async () => ({}),
  });
  // The diff is never run and no check/comment is posted when the spec
  // can't be materialised.
  assert.equal(ranDiff, false);
  assert.equal(calls.find((c) => c.name === "checks.create"), undefined);
  assert.equal(calls.find((c) => c.name === "issues.createComment"), undefined);
});

test("handlePullRequest reports diff crashes as a Checks API action_required", async () => {
  const payload = JSON.parse(
    await readFile(join(HERE, "fixtures", "pull_request.opened.json"), "utf-8"),
  );
  const { ctx, calls } = makeCtx(payload);
  await handlePullRequest(/** @type {any} */ (ctx), {
    runDiff: async () => {
      throw new Error("guardian diff failed (code=137): killed");
    },
    postRun: async () => ({}),
  });
  const update = callOf(calls, "checks.update");
  assert.equal(update.args.conclusion, "action_required");
  assert.match(update.args.output.summary, /guardian diff failed/);
});

test("buildAnnotations maps verdicts to Checks API annotation levels", () => {
  const report = {
    contract_kind: "openapi",
    ruleset_id: "default",
    summary: { breaking: 1, behavioral: 1, additive: 1, total: 3 },
    changes: [
      {
        change_id: "a",
        kind: "openapi.path.removed",
        location: "paths./users",
        verdict: "breaking",
        rule_id: "OAS-PATH-REMOVED",
        rationale: "removed",
        affected_clients: ["acme/c1"],
      },
      {
        change_id: "b",
        kind: "openapi.response.added",
        location: "paths./pets.get.responses.500",
        verdict: "behavioral",
        rule_id: "OAS-RESP-ADDED",
        rationale: "behavioral",
        affected_clients: [],
      },
      {
        change_id: "c",
        kind: "openapi.path.added",
        location: "paths./things",
        verdict: "additive",
        rule_id: "OAS-PATH-ADDED",
        rationale: "additive",
        affected_clients: [],
      },
    ],
  };
  const ann = buildAnnotations(/** @type {any} */ (report), "openapi.json");
  // Breaking first, then behavioral, then additive — stable ordering.
  assert.equal(ann[0].annotation_level, "failure");
  assert.equal(ann[1].annotation_level, "warning");
  assert.equal(ann[2].annotation_level, "notice");
  for (const a of ann) {
    assert.equal(a.path, "openapi.json");
    assert.equal(a.start_line, 1);
    assert.equal(a.end_line, 1);
  }
  // Affected-client count is surfaced in the message.
  assert.match(ann[0].message, /1 client repo/);
});

test("buildAnnotations caps at ANNOTATION_LIMIT (Checks API limit)", () => {
  /** @type {any[]} */
  const changes = [];
  for (let i = 0; i < ANNOTATION_LIMIT + 5; i++) {
    changes.push({
      change_id: `id${i}`,
      kind: "openapi.path.removed",
      location: `paths./p${i}`,
      verdict: "breaking",
      rule_id: "OAS-PATH-REMOVED",
      rationale: "removed",
      affected_clients: [],
    });
  }
  const report = {
    contract_kind: "openapi",
    ruleset_id: "default",
    summary: { breaking: changes.length, behavioral: 0, additive: 0, total: changes.length },
    changes,
  };
  const ann = buildAnnotations(/** @type {any} */ (report), "openapi.json");
  assert.equal(ann.length, ANNOTATION_LIMIT);
});

test("summarizeReport renders per-client impact summary in the comment body", () => {
  const body = summarizeReport(breakingReport(), { bypassLabel: null });
  assert.match(body, /Per-client impact/);
  assert.match(body, /acme\/users-client/);
  assert.match(body, /acme\/billing/);
  // Both clients appear in the breaking-changes section.
  assert.match(body, /Breaking changes/);
});
