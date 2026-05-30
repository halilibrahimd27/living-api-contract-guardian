// @ts-check
/**
 * Guardian — Probot 13.x entrypoint.
 *
 * Subscribes to PR open / synchronize / reopen / label events on repos
 * where the App is installed, runs `guardian diff` on the changed spec,
 * publishes a Check Run with annotations + a PR comment, and forwards
 * the resulting ChangeReport to the Guardian backend so it can be
 * reproduced from history.
 *
 * Webhook signature verification is handled by Probot's built-in
 * `@octokit/webhooks` integration (it reads `WEBHOOK_SECRET` and
 * rejects unsigned payloads before any handler runs), so we never see
 * a payload that didn't pass HMAC validation.
 */

import { runGuardianDiff, summarizeReport } from "./diff.js";
import { postRun } from "./backend.js";

const BYPASS_LABEL = "guardian:accept-breaking";
const DEFAULT_SPEC_PATH = process.env.GUARDIAN_SPEC_PATH || "openapi.json";
const BACKEND_URL = process.env.GUARDIAN_API_URL || "http://localhost:8000";

/**
 * Pick the spec file path for a given PR. For now this comes from the
 * `GUARDIAN_SPEC_PATH` env var or `.guardian.yml` repo config (TBD);
 * future milestones will read it from the repo at the head SHA.
 *
 * @param {import("probot").Context<"pull_request">} ctx
 * @returns {Promise<string>}
 */
async function resolveSpecPath(ctx) {
  // We don't yet read repo-level config; the env var wins.
  return DEFAULT_SPEC_PATH;
}

/**
 * @param {import("probot").Context<"pull_request">} ctx
 * @param {string} specPath
 * @returns {Promise<{ baseText: string; headText: string } | null>}
 */
async function fetchSpecs(ctx, specPath) {
  const { owner, repo } = ctx.repo();
  const pr = ctx.payload.pull_request;
  const base = pr.base.sha;
  const head = pr.head.sha;
  /** @type {(ref: string) => Promise<string>} */
  const fetchAt = async (ref) => {
    const res = await ctx.octokit.repos.getContent({
      owner,
      repo,
      path: specPath,
      ref,
    });
    if (Array.isArray(res.data) || res.data.type !== "file") {
      throw new Error(`spec path ${specPath} is not a file at ${ref}`);
    }
    const b64 = /** @type {{ content: string }} */ (res.data).content;
    return Buffer.from(b64, "base64").toString("utf-8");
  };
  try {
    const baseText = await fetchAt(base);
    const headText = await fetchAt(head);
    return { baseText, headText };
  } catch (err) {
    ctx.log.warn({ err }, "guardian.fetch_spec_failed");
    return null;
  }
}

/**
 * @param {import("probot").Context<"pull_request">} ctx
 */
async function handlePullRequest(ctx) {
  const action = ctx.payload.action;
  const allowed = new Set([
    "opened",
    "reopened",
    "synchronize",
    "labeled",
    "unlabeled",
    "ready_for_review",
  ]);
  if (!allowed.has(action)) return;

  const pr = ctx.payload.pull_request;
  const { owner, repo } = ctx.repo();
  const headSha = pr.head.sha;
  const baseSha = pr.base.sha;
  const bypass = (pr.labels || []).some((l) => l.name === BYPASS_LABEL);

  const specPath = await resolveSpecPath(ctx);
  const specs = await fetchSpecs(ctx, specPath);
  if (specs === null) {
    ctx.log.info("guardian.skip.no_spec");
    return;
  }

  // Create a check run in progress, then run the diff, then complete it.
  const created = await ctx.octokit.checks.create({
    owner,
    repo,
    name: "Guardian / API Contract Diff",
    head_sha: headSha,
    status: "in_progress",
  });
  const checkRunId = created.data.id;

  let report;
  try {
    report = await runGuardianDiff(specs.baseText, specs.headText, {
      acceptBreaking: bypass,
    });
  } catch (err) {
    ctx.log.error({ err }, "guardian.diff_failed");
    await ctx.octokit.checks.update({
      owner,
      repo,
      check_run_id: checkRunId,
      status: "completed",
      conclusion: "action_required",
      output: {
        title: "Guardian diff crashed",
        summary: `Guardian failed to compute a diff: ${err instanceof Error ? err.message : String(err)}`,
      },
    });
    return;
  }

  const summary = summarizeReport(report, { bypassLabel: bypass ? BYPASS_LABEL : null });
  const conclusion =
    report.summary.breaking > 0 && !bypass ? "failure" : "success";

  await ctx.octokit.checks.update({
    owner,
    repo,
    check_run_id: checkRunId,
    status: "completed",
    conclusion,
    output: {
      title: `Guardian — ${report.summary.breaking} breaking / ${report.summary.behavioral} behavioral / ${report.summary.additive} additive`,
      summary,
    },
  });

  // PR comment with the same Markdown body so reviewers see it inline.
  await ctx.octokit.issues.createComment({
    owner,
    repo,
    issue_number: pr.number,
    body: summary,
  });

  // Persist the run via the Guardian backend.
  try {
    await postRun(BACKEND_URL, {
      repo: `${owner}/${repo}`,
      pr_number: pr.number,
      head_sha: headSha,
      base_sha: baseSha,
      conclusion,
      report_json: report,
      bypass_label_present: bypass,
      check_run_id: checkRunId,
    });
  } catch (err) {
    ctx.log.warn({ err }, "guardian.backend_post_failed");
  }
}

/**
 * Probot application factory.
 *
 * @param {import("probot").Probot} app
 */
export default function guardianApp(app) {
  app.on(
    [
      "pull_request.opened",
      "pull_request.reopened",
      "pull_request.synchronize",
      "pull_request.labeled",
      "pull_request.unlabeled",
      "pull_request.ready_for_review",
    ],
    handlePullRequest,
  );
}
