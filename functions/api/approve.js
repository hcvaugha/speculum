// Approve or remove an early-access reader: adds or removes their email in the Cloudflare Access policy
// "Approved readers" (which guards early.speculum.vote) and records it on their sign-up. Owner only: needs the
// ADMIN_KEY password, plus CF_ACCESS_TOKEN (a token limited to Access: Apps and Policies, Edit) and CF_ACCOUNT_ID.
const POLICY_NAME = 'Approved readers';
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', 'cache-control': 'no-store' } });

async function digest(s) {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)));
}
async function hex(s, n) { return [...(await digest(s))].slice(0, n).map(b => b.toString(16).padStart(2, '0')).join(''); }

async function cf(env, path, init = {}) {
  // Secrets pasted into GitHub often carry a trailing newline, which fetch rejects in a header: trim them.
  const token = String(env.CF_ACCESS_TOKEN || '').trim(), account = String(env.CF_ACCOUNT_ID || '').trim();
  const r = await fetch(`https://api.cloudflare.com/client/v4/accounts/${account}${path}`, {
    ...init, headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || j.success === false) throw new Error((j.errors && j.errors[0] && j.errors[0].message) || `Cloudflare answered ${r.status}`);
  return j.result;
}

// The policy may be a reusable one (account level) or attached to the app; look in both places.
async function findPolicy(env) {
  const reusable = (await cf(env, '/access/policies')) || [];
  const p = reusable.find(x => x.name === POLICY_NAME);
  if (p) return { path: `/access/policies/${p.id}`, policy: p };
  for (const app of (await cf(env, '/access/apps')) || []) {
    for (const ap of app.policies || []) {
      if (ap.name === POLICY_NAME) return { path: `/access/apps/${app.id}/policies/${ap.id}`, policy: await cf(env, `/access/apps/${app.id}/policies/${ap.id}`) };
    }
  }
  throw new Error(`No Access policy named "${POLICY_NAME}" was found.`);
}

export async function onRequestPost(ctx) {
  try {
    return await handle(ctx);
  } catch (e) {                                   // never answer with Cloudflare's HTML error page
    return json({ error: `Server error: ${e && e.message ? e.message : e}` }, 422);
  }
}

async function handle({ request, env }) {
  if (!env.SIGNUPS || !env.ADMIN_KEY) return json({ error: 'The list is not set up yet.' }, 503);
  if (!env.CF_ACCESS_TOKEN || !env.CF_ACCOUNT_ID) return json({ error: 'Approvals need the CLOUDFLARE_ACCESS_TOKEN secret (see SETUP.md).' }, 503);
  const day = new Date().toISOString().slice(0, 10);
  const rk = `adminfail:${day}:${await hex(request.headers.get('CF-Connecting-IP') || '?', 8)}`;
  const fails = parseInt((await env.SIGNUPS.get(rk)) || '0', 10);
  if (fails >= 10) return json({ error: 'Too many wrong passwords today.' }, 429);
  let b;
  try { b = await request.json(); } catch { return json({ error: 'Bad request.' }, 400); }
  const a = await digest(String(b.key || '')), k = await digest(String(env.ADMIN_KEY).trim());
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ k[i];
  if (diff) {
    await env.SIGNUPS.put(rk, String(fails + 1), { expirationTtl: 172800 });
    return json({ error: 'Wrong password.' }, 403);
  }
  const email = String(b.email || '').trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return json({ error: 'Not an email address.' }, 400);
  const approve = b.action !== 'remove';
  try {
    const { path, policy } = await findPolicy(env);
    let include = (policy.include || []).filter(r => !(r.email && (r.email.email || '').toLowerCase() === email));
    if (approve) include = include.concat([{ email: { email } }]);
    const body = { name: policy.name, decision: policy.decision, include, exclude: policy.exclude || [], require: policy.require || [] };
    for (const f of ['session_duration', 'approval_required', 'approval_groups', 'isolation_required', 'purpose_justification_required',
                     'purpose_justification_prompt', 'mfa_config', 'precedence']) {
      if (policy[f] !== undefined && policy[f] !== null) body[f] = policy[f];
    }
    await cf(env, path, { method: 'PUT', body: JSON.stringify(body) });
  } catch (e) {
    return json({ error: `Cloudflare: ${e.message}` }, 422);   // not 502: Cloudflare replaces 502 with its own page
  }
  const key = `s:${await hex(email, 16)}`;
  const rec = JSON.parse((await env.SIGNUPS.get(key)) || 'null') || { email, name: '', note: '', first: new Date().toISOString() };
  rec.approved = approve ? new Date().toISOString() : null;
  await env.SIGNUPS.put(key, JSON.stringify(rec));
  return json({ ok: true, approved: approve });
}

export const onRequest = () => json({ error: 'Use POST.' }, 405);
