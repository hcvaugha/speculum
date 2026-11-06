# Handoff: where Speculum stands (November 6, 2026)

Read this first in a new session. It records what is built, how it runs, the rules the project keeps, and what comes next.

## What Speculum is

A public, nonpartisan record of Congress at speculum.vote: how every member votes, where their campaign money comes from, and who lobbies them, built only from public filings and updated nightly. The record began on Day Zero, November 3, 2026. Public launch is planned for November 11–12, 2026 (flip `data/site.json` `"public"` to `"launch"`).

## Rules the project keeps

- Show the pattern, never a verdict. Fixed rules, the same for every member and party; every figure links to its source; the Method page publishes every rule. Findings are fixed measures chosen in advance, never selected for what they show.
- Real data only. When a figure can't be checked, say so rather than estimate.
- Never ask for, paste or handle API keys or passwords in chat. Keys live only in GitHub secrets, which the owner sets.
- Never force-push.
- Commit to `main`; the deploy runs on push. (The old session also mirrored `main` to a `tide` branch.)
- Subtle lore (Malleus Cordis / Vesperhold) stays subtle. The one open reference is The Glass (`glass.html`, linked from the auditor's seal and the footer): the Catedral's mirror framing four panes of plain facts from the record, and a closed door teasing Malleus Cordis, with a note that the game casts no real person as anything and nothing in the record is drawn from it.

## How it runs

- Nightly: `.github/workflows/nightly.yml` runs `python -m speculum snapshot`, commits `data/snapshots/<date>/` (only the files that changed; see Snapshots below), archives raw API responses to a GitHub release (`raw-<date>`), builds and deploys. A Cloudflare Worker in `scheduler/` starts it at 10:20, 13:50 and 17:20 UTC; the gate lets only the first full run of the day through.
- Deploy: `scripts/deploy.sh` publishes two Cloudflare Pages projects. `speculum-public` is speculum.vote, still the holding page with the early-access sign-up. `speculum-preview` is the full site at early.speculum.vote, behind Cloudflare Access (an email allow-list, policy "Approved readers"); its pages.dev addresses redirect there.
- Source check: `check.yml` uses the Anthropic API to verify every claim in The Dependents against its sources. Readers' disputes go to the KV store `speculum-disputes`.
- Early access: sign-ups are stored in KV `speculum-signups`, never in the repository. The owner's list is at speculum.vote/signups. Approve and Remove edit the Access policy through `functions/api/approve.js`; Invite opens a draft in the Speculum Gmail account, which sends as hello@speculum.vote.
- Secrets (GitHub): CONGRESS_API_KEY, FEC_API_KEY, LDA_API_KEY, CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, ANTHROPIC_API_KEY, SPECULUM_DISPATCH_TOKEN (fine-grained token, owner speculum-vote, Actions read/write), SPECULUM_ADMIN_PASSWORD (signups page), CLOUDFLARE_ACCESS_TOKEN (Access policies only).
- Email: hello@ and corrections@ forward through Cloudflare Email Routing to the Speculum Gmail account, which sends as hello@speculum.vote. Instagram: @speculum.vote.

## Snapshots

- A data file is stored only on a night its content changed. Each night's `meta.json` has a `files` index: for every file, the night whose copy stood that night, its SHA-256 and size. Reads follow the index, so a night that stored nothing for a source (unchanged, or the fetch failed) shows the copy that stood.
- After a full run on the newest night, copies no longer standing leave the working tree (`prune` in `speculum/snapshot.py`). They stay in git history, and the raw filings behind them stay in that night's raw release. `meta.json` and `tide.json` stay on every night; Day Zero keeps every file as the baseline.
- `python -m speculum restore <date>` rebuilds any night's files from git history and checks each against the index (a shallow clone needs `git fetch --unshallow` first).
- `lobbying.json` and `spend.json` are written as compact JSON. GitHub refuses files over 100 MB, and the run prints a warning when any file passes 80 MB. Gzip is written with a fixed header, so unchanged data gives unchanged bytes. Big files stay uncompressed on purpose: git stores a plain night as a small delta, while a gzipped one would be stored whole.
- The checkout holds about 180 MB of snapshots (Day Zero plus one standing copy of each file) and grows by a few KB a night, plus the size of whatever changed.
- A back-dated rerun (`--date` earlier than the newest night) never prunes. It keeps that night's saved edition of The Tide when the night before is in git history only.
- A night whose edition of The Tide failed to build keeps its own files, and the night before's, until it has one. The site build then makes the edition from them. A rerun whose copy turns out the same as the night before's drops its copy and updates the index.

## The site's sections

Home (`index.html`), The Tide (nightly editions with a dated timeline), Findings (fixed measures), The Chamber (`chamber.html`, every member), The Money (campaign sources, lobbying contributions and spending by issue area, shared funders), Bills (who lobbied each bill, by sector, from LD-2 descriptions; sector rules in `speculum/lobbysectors.py`), Interests, The Dependents (decisions traced to outcomes, by theme), Day Zero, Method, About, Corrections, Join the readers (`join.html`).

The Dependents has five themes, each a rule in `RULES` (`speculum/chains.py`):
- Foreign arms sales (`chain.html`): Senate votes on Arms Export Control Act disapprovals; money from SIPRI Top 100 companies (`data/sectors.json`).
- Regulations overturned (`chain_rule.html`, added November 6): every roll call in either chamber on a Congressional Review Act disapproval; one chain per resolution across both chambers. Money around each chamber's last vote comes from organizations whose LD-2 reports name the resolution (LD-203, through the vote date). The drafter has its own brief (`SYSTEM_RULE` in `speculum/interpret.py`): the rule, what it required, and the agency's own benefit and cost estimates side by side. It drafts resolutions that became law first, oldest first. 48 resolutions; at 3 drafts a night (`--limit 3` in `nightly.yml`), research takes about 16 nights, and each chain goes public once its record passes the source check. The Dependents' measures are fetched first in the nightly bill-details step, so every resolution's final status (became law or not) is known.
- Budget reconciliation, Ending national emergencies and War powers (added November 6): same page (`chain_rule.html`) and money method as regulations. Each rule has a `page` block that sets its heading, subject, record fields and closing note. Each has its own drafter brief (`BRIEFS` in `speculum/interpret.py`). Selected by statutory title wording: "to provide for reconciliation pursuant to" (2 bills: H.R. 1, S. 2); "terminating the national emergency declared" (7, all Senate so far); "removal of United States Armed Forces from hostilities" (18, Senate so far). House votes on these appear as House bill titles arrive.
- 75 chains await research across the four newer themes. At 3 drafts a night that is about 25 nights; raising `--limit` on the chains step in `nightly.yml` speeds it up at more API cost.

## Next, in order

1. Launch preparation: give the early-access sign-up a home on the public site (the holding page goes away at launch); run a full phone and desktop check of the public build; flip `public` to `launch` on November 11–12.
2. Donations: undecided. Options are a Stripe payment link now with HCB fiscal sponsorship later, or HCB only. Planned funding rules: refuse money from parties, campaigns, PACs, lobbying registrants and their clients; cap each donor at about $1,000 a year; publish donor names above a small amount and monthly costs on a Funding page.
3. Requested by early readers: CSV downloads on every table and a Data page; a weekly trend report from The Tide (PDF and CSV); later, an in-browser report writer.
4. Households and capital: official series (Federal Reserve wealth shares, BEA/BLS labor share and corporate profits, real wages, housing cost burden, health coverage, life expectancy) side by side, with markers for laws the lobbying sectors worked on. Use neutral naming.
5. Later: Remove should also revoke the reader's Access session; a sector view of lobbyists who held government posts; a vetted member network (invitation tree, influence over process only, never over findings).

## Known limits and notes

- Bill titles come from Congress.gov, up to 600 a night (`speculum/sources/billinfo.py`).
- One lobbying filer is set aside, with its filings, in `data/lobbying_set_aside.json`.
- Lobbying spending matches in-house and firm reports by organization name, so totals run about 3% above independent counts (stated on the Method page).
- GitHub's own cron is unreliable; the Cloudflare scheduler is the main trigger.
- To check: LD-203 years follow the current Congress (`_years` in `speculum/sources/lda.py`). From January 1, 2027 the window is 2027 only, so Year-End 2026 contribution reports (July–December 2026, filed by January 30, 2027) would not be fetched. Decide whether the window should keep the previous Congress's final year until those reports are in.
- Bills list and lobbied-bill ranking break ties by bill number, so the order a file lists bills in never changes a page.
- Adding a Dependents theme: a rule in `RULES` (title test from the statute, subject parser, area, `page` block, `why` with verified sources) and a drafter brief in `BRIEFS`.
