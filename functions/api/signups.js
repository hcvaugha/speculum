// The early-access list, for the site's owner only. Needs the ADMIN_KEY secret on the Pages project
// (set by scripts/deploy.sh from the SPECULUM_ADMIN_PASSWORD GitHub secret). Wrong passwords are slowed
// and limited per connection.
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', 'cache-control': 'no-store' } });

async function digest(s) {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)));
}

export async function onRequestPost({ request, env }) {
  if (!env.SIGNUPS || !env.ADMIN_KEY) return json({ error: 'The list is not set up yet (see SETUP.md).' }, 503);
  const ip = request.headers.get('CF-Connecting-IP') || '?';
  const day = new Date().toISOString().slice(0, 10);
  const rk = `adminfail:${day}:${[...(await digest(ip))].slice(0, 8).map(b => b.toString(16).padStart(2, '0')).join('')}`;
  const fails = parseInt((await env.SIGNUPS.get(rk)) || '0', 10);
  if (fails >= 10) return json({ error: 'Too many wrong passwords today.' }, 429);
  let b;
  try { b = await request.json(); } catch { return json({ error: 'Bad request.' }, 400); }
  const a = await digest(String(b.key || '')), k = await digest(String(env.ADMIN_KEY).trim());
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ k[i];        // constant-time comparison
  if (diff) {
    await env.SIGNUPS.put(rk, String(fails + 1), { expirationTtl: 172800 });
    await new Promise(r => setTimeout(r, 800));
    return json({ error: 'Wrong password.' }, 403);
  }
  const rows = [];
  let cursor;
  do {
    const page = await env.SIGNUPS.list({ prefix: 's:', cursor });
    for (const k of page.keys) {
      const v = await env.SIGNUPS.get(k.name);
      if (v) rows.push(JSON.parse(v));
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  rows.sort((x, y) => (x.first < y.first ? -1 : 1));
  return json({ rows });
}

export const onRequest = () => json({ error: 'Use POST.' }, 405);
