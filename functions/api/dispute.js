// Readers' disputes. Stores each one in the DISPUTES KV namespace; the nightly run reads them, withdraws
// the disputed item and has the source checker re-read it beside the reader's source (speculum/verify.py).
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

async function hash(s) {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s));
  return [...new Uint8Array(d)].slice(0, 8).map(b => b.toString(16).padStart(2, '0')).join('');
}

export async function onRequestPost({ request, env }) {
  if (!env.DISPUTES) return json({ error: 'Disputes are not open yet. Please send the source to the corrections address.' }, 503);
  let b;
  try { b = await request.json(); } catch { return json({ error: 'Bad request.' }, 400); }
  if (b.website) return json({ ok: true });                      // filled only by bots
  const fp = String(b.fp || '');
  if (!/^[0-9a-f]{16}$/.test(fp)) return json({ error: 'Choose an item with its Dispute link first.' }, 400);
  let url;
  try { url = new URL(String(b.source || '')); } catch { return json({ error: 'Give a link to the source.' }, 400); }
  if (!/^https?:$/.test(url.protocol)) return json({ error: 'Give a web link (http or https).' }, 400);
  const day = new Date().toISOString().slice(0, 10);
  const rk = `rate:${day}:${await hash(request.headers.get('CF-Connecting-IP') || '?')}`;
  const n = parseInt((await env.DISPUTES.get(rk)) || '0', 10);
  if (n >= 10) return json({ error: 'Too many disputes from this connection today. Please try tomorrow.' }, 429);
  await env.DISPUTES.put(rk, String(n + 1), { expirationTtl: 172800 });
  const id = crypto.randomUUID();
  await env.DISPUTES.put(`d:${id}`, JSON.stringify({
    id, fp, chain: String(b.chain || '').slice(0, 80), source: url.href.slice(0, 2000),
    note: String(b.note || '').slice(0, 1000), received: new Date().toISOString(),
  }));
  return json({ ok: true });
}

export const onRequest = () => json({ error: 'Use POST.' }, 405);
