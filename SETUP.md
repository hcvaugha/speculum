# Setting up Speculum

About 20 minutes, once. After this, everything runs by itself every night.

## 1. API keys (free)

| Key | Where | Needed? |
| --- | --- | --- |
| Congress.gov | https://api.congress.gov/sign-up/ | Yes |
| FEC | https://api.open.fec.gov/developers/ (sign up for an api.data.gov key) | Yes |
| Lobbying (LDA) | https://lda.gov/api/register/ | Optional; without it lobbying data downloads more slowly |

Keys arrive by email. Do not paste them into chat or commit them to the code.

## 2. Cloudflare

You bought speculum.vote on Cloudflare, so you already have an account.

1. **Account ID.** In the Cloudflare dashboard, open speculum.vote. The Account ID is in the right-hand column of the Overview page. Copy it.
2. **API token.** Click your profile icon → My Profile → API Tokens → Create Token → Create Custom Token.
   - Name: `speculum-deploy`
   - Permissions: Account → Cloudflare Pages → Edit, Account → Workers KV Storage → Edit (KV holds readers' disputes) and Account → Workers Scripts → Edit (runs the scheduler that starts the nightly on time); an existing token can be edited to add them
   - Create it and copy the token (it is shown once).

## 3. GitHub secrets

In github.com/hcvaugha/speculum: Settings → Secrets and variables → Actions → New repository secret. Add each of these:

| Name | Value |
| --- | --- |
| `CONGRESS_API_KEY` | your Congress.gov key |
| `FEC_API_KEY` | your FEC key |
| `LDA_API_KEY` | your LDA key (optional) |
| `CLOUDFLARE_API_TOKEN` | the token from step 2 |
| `CLOUDFLARE_ACCOUNT_ID` | the Account ID from step 2 |
| `ANTHROPIC_API_KEY` | a key from console.anthropic.com → API Keys. It runs the nightly source check: without it nothing new in The Dependents is checked, so nothing new is published. Expect a few dollars a month. |
| `SPECULUM_DISPATCH_TOKEN` | a GitHub fine-grained token with access to this repository only and the permission Actions → Read and write. The Cloudflare scheduler uses it to start the nightly, because GitHub's own schedule often skips runs. When it expires, make a new one, replace this secret, and rerun the Scheduler workflow. |

Then Settings → Actions → General → Workflow permissions → choose **Read and write permissions** → Save. The nightly job needs this to commit each snapshot and attach the raw archive.

## 4. First run

Actions tab → **Nightly snapshot** → Run workflow → Run.

The first run downloads the whole session's roll calls and every member's campaign totals, so it takes a few hours. Later nights fetch only what is new.

To stamp the first snapshot as Day Zero even if it runs later, type `2026-11-03` in the date box. The votes and filings it collects are the same public record either way.

## 5. Point the domain at the site

After the first run finishes, Cloudflare has two Pages projects.

1. Workers & Pages → **speculum-public** → Custom domains → Set up a custom domain → `speculum.vote`.
2. Workers & Pages → **speculum-preview** → Custom domains → `preview.speculum.vote`.

## 6. Lock the preview

The preview shows real figures before the method review, so only invited people should see it.

1. Cloudflare dashboard → Zero Trust (free plan is fine) → Access → Applications → Add an application → Self-hosted.
2. Application domains: add both `preview.speculum.vote` and `speculum-preview.pages.dev`.
3. Policy: Action **Allow**, Include → Emails → your email (and reviewers' emails later).
4. Login method: One-time PIN. Visitors get a code by email.

speculum.vote (the holding page) stays public.

## Running it on a Mac instead

```
pip3 install -r requirements.txt
export CONGRESS_API_KEY=... FEC_API_KEY=...
python3 -m speculum snapshot
python3 -m speculum build
open dist/preview/index.html
```
