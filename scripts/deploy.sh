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
ensure_ns() {   # find or create a KV namespace by title; prints its id, or nothing
  local id
  id=$(curl -s -H "$AUTH" "$API?per_page=100" | python3 -c 'import json,sys
d=json.load(sys.stdin); print(next((n["id"] for n in d.get("result") or [] if n.get("title")==sys.argv[1]), ""))' "$1" 2>/dev/null)
  if [ -z "$id" ]; then
    id=$(curl -s -X POST -H "$AUTH" -H "content-type: application/json" -d "{\"title\":\"$1\"}" "$API" \
         | python3 -c 'import json,sys; print((json.load(sys.stdin).get("result") or {}).get("id",""))' 2>/dev/null)
  fi
  echo "$id"
}
NS=$(ensure_ns speculum-disputes)          # readers' disputes; read by the nightly run
SU=$(ensure_ns speculum-signups)           # early-access sign-ups; private, never read into the repository
if [ -n "$NS" ]; then
  echo "disputes store: $NS; sign-up store: ${SU:-none}"
else
  echo "::warning::Could not create or find the disputes store (the Cloudflare token needs Workers KV Storage: Edit). The dispute form will say disputes are not open yet."
fi
npx --yes wrangler@3 pages project create speculum-public --production-branch main >/dev/null 2>&1 || true
npx --yes wrangler@3 pages project create speculum-preview --production-branch main >/dev/null 2>&1 || true
# Set before deploying: Pages applies a secret only to deployments made after it.
# The password for the early-access list page (signups.html), from the SPECULUM_ADMIN_PASSWORD GitHub secret.
if [ -n "${SPECULUM_ADMIN_PASSWORD:-}" ]; then
  printf '%s' "$SPECULUM_ADMIN_PASSWORD" | npx --yes wrangler@3 pages secret put ADMIN_KEY --project-name speculum-public >/dev/null \
    && echo "early-access list password set" || echo "::warning::Could not set the early-access list password."
fi
status=0
for pair in "dist/public speculum-public" "dist/preview speculum-preview"; do
  set -- $pair
  # Pages reads bindings from wrangler.toml, which must name the project; one file per site.
  rm -f wrangler.toml
  [ -n "$NS" ] && printf 'name = "%s"\npages_build_output_dir = "%s"\ncompatibility_date = "2024-09-01"\n\n[[kv_namespaces]]\nbinding = "DISPUTES"\nid = "%s"\n' "$2" "$1" "$NS" > wrangler.toml
  [ -n "$NS" ] && [ -n "$SU" ] && printf '\n[[kv_namespaces]]\nbinding = "SIGNUPS"\nid = "%s"\n' "$SU" >> wrangler.toml
  # The preview alone gets a redirect from its *.pages.dev addresses to early.speculum.vote, which Access protects.
  rm -f functions/_middleware.js
  [ "$2" = "speculum-preview" ] && cp scripts/preview/_middleware.js functions/_middleware.js
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
rm -f wrangler.toml functions/_middleware.js
exit $status
