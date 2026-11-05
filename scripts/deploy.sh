#!/usr/bin/env bash
# Deploy both Pages projects. Readers' disputes need a KV namespace bound to the sites; if the Cloudflare
# token cannot create or read one, the sites still deploy and the dispute form says disputes are not open.
set -u
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ] || [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  echo "::warning::Cloudflare secrets are not set yet (CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID). Skipping deploy; see SETUP.md step 3."
  exit 0
fi
API="https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/storage/kv/namespaces"
AUTH="Authorization: Bearer $CLOUDFLARE_API_TOKEN"
NS=$(curl -s -H "$AUTH" "$API?per_page=100" | python3 -c 'import json,sys
d=json.load(sys.stdin); print(next((n["id"] for n in d.get("result") or [] if n.get("title")=="speculum-disputes"), ""))' 2>/dev/null)
if [ -z "$NS" ]; then
  NS=$(curl -s -X POST -H "$AUTH" -H "content-type: application/json" -d '{"title":"speculum-disputes"}' "$API" \
       | python3 -c 'import json,sys; print((json.load(sys.stdin).get("result") or {}).get("id",""))' 2>/dev/null)
fi
if [ -n "$NS" ]; then
  printf 'pages_build_output_dir = "dist/public"\ncompatibility_date = "2024-09-01"\n\n[[kv_namespaces]]\nbinding = "DISPUTES"\nid = "%s"\n' "$NS" > wrangler.toml
  echo "disputes store: $NS"
else
  echo "::warning::Could not create or find the disputes store (the Cloudflare token needs Workers KV Storage: Edit). The dispute form will say disputes are not open yet."
fi
npx --yes wrangler@3 pages project create speculum-public --production-branch main >/dev/null 2>&1 || true
npx --yes wrangler@3 pages project create speculum-preview --production-branch main >/dev/null 2>&1 || true
status=0
for pair in "dist/public speculum-public" "dist/preview speculum-preview"; do
  set -- $pair
  if ! npx --yes wrangler@3 pages deploy "$1" --project-name "$2" --branch main --commit-dirty=true; then
    if [ -f wrangler.toml ]; then
      echo "::warning::Deploy of $2 with the disputes binding failed; deploying without it."
      mv wrangler.toml wrangler.toml.off
      npx --yes wrangler@3 pages deploy "$1" --project-name "$2" --branch main --commit-dirty=true || status=1
      mv wrangler.toml.off wrangler.toml
    else
      status=1
    fi
  fi
done
rm -f wrangler.toml
exit $status
