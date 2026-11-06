// Starts the nightly snapshot on time. GitHub's own schedule is best-effort and often skips runs,
// so this Cloudflare Worker asks GitHub to start it at the same times. The nightly's gate lets only
// one full run through per day, so a start here and a scheduled one never both run.
const REPO = "speculum-vote/speculum";

async function dispatch(env) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/nightly.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "speculum-scheduler",
    },
    body: JSON.stringify({ ref: "main", inputs: { by: "scheduler" } }),
  });
  if (r.status !== 204) throw new Error(`GitHub answered ${r.status}: ${await r.text()}`);
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
};
