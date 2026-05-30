// @ts-check
/**
 * Thin client for the Guardian FastAPI backend.
 *
 * Uses Node 20's built-in global ``fetch`` so the App image and the
 * test harness have zero runtime dependencies for this call.
 */

/**
 * Persist a CI run via `POST /ci/runs` on the Guardian backend.
 *
 * @param {string} baseUrl
 * @param {{
 *   repo: string;
 *   pr_number: number;
 *   head_sha: string;
 *   base_sha: string;
 *   conclusion: string;
 *   report_json: unknown;
 *   bypass_label_present: boolean;
 *   check_run_id?: number;
 * }} payload
 * @returns {Promise<Record<string, unknown>>}
 */
export async function postRun(baseUrl, payload) {
  const res = await fetch(`${baseUrl.replace(/\/$/, "")}/ci/runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`POST /ci/runs failed: ${res.status} ${text}`);
  }
  return /** @type {Record<string, unknown>} */ (await res.json());
}
