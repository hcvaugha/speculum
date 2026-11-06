// Early-access sign-ups. Stored only in the SIGNUPS KV namespace, never in the public repository.
// One record per email address (a second sign-up replaces the first). The list is read with the
// password-protected /signups.html page (functions/api/signups.js).
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

async function hash(s, n = 8) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(d)].slice(0, n).map(b => b.toString(16).padStart(2, '0')).join('');
}

export async function onRequestPost({ request, env }) {
  if (!env.SIGNUPS) return json({ error: 'Sign-up is not open yet. Please try again later.' }, 503);
  let b;
  try { b = await request.json(); } catch { return json({ error: 'Bad request.' }, 400); }
  if (b.website) return json({ ok: true });                      // filled only by bots
  const email = String(b.email || '').trim().toLowerCase().slice(0, 254);
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return json({ error: 'Please enter a valid email address.' }, 400);
  const day = new Date().toISOString().slice(0, 10);
  const rk = `rate:${day}:${await hash(request.headers.get('CF-Connecting-IP') || '?')}`;
  const n = parseInt((await env.SIGNUPS.get(rk)) || '0', 10);
  if (n >= 5) return json({ error: 'Too many sign-ups from this connection today. Please try tomorrow.' }, 429);
  await env.SIGNUPS.put(rk, String(n + 1), { expirationTtl: 172800 });
  const key = `s:${await hash(email, 16)}`;
  const prev = JSON.parse((await env.SIGNUPS.get(key)) || 'null');
  await env.SIGNUPS.put(key, JSON.stringify({
    name: String(b.name || '').trim().slice(0, 120), email,
    note: String(b.note || '').trim().slice(0, 1000),
    first: prev ? prev.first : new Date().toISOString(), updated: new Date().toISOString(),
  }));
  return json({ ok: true });
}

export const onRequest = () => json({ error: 'Use POST.' }, 405);
