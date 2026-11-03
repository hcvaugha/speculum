# Speculum

A mirror held to Congress. Recording began November 3, 2026.

Speculum shows, for every member of the House and Senate, who funds them, who lobbies them, and how they vote. It uses only public filings, applies the same yardstick to every member and party, and links every figure to its source. It describes; it does not accuse.

## How it works

Every night a GitHub Actions job:

1. Pulls the current roster and ID crosswalk ([congress-legislators](https://github.com/unitedstates/congress-legislators)), House roll calls ([Congress.gov API](https://api.congress.gov/)), Senate roll calls (senate.gov XML), campaign totals ([OpenFEC](https://api.open.fec.gov/developers/)) and lobbyist contribution reports ([lda.gov](https://lda.gov/api/v1/)).
2. Saves every raw file untouched, hashes it with SHA-256, and attaches the night's archive to a GitHub release.
3. Writes a normalized, dated snapshot to `data/snapshots/<date>/` and commits it. Git history is the audit trail.
4. Computes each member's Ledger and rebuilds two static sites on Cloudflare Pages:
   - `speculum.vote`: the public holding page.
   - `preview.speculum.vote`: the private prototype, behind a login until the method review is done.

## Layout

```
speculum/
  archive.py       raw archive with SHA-256 manifest
  http.py          paced, retrying fetcher that archives before parsing
  snapshot.py      dated snapshot store
  ledger.py        the readings (funding mix, lobbyist money, party unity, attendance)
  sources/         one module per public source
  site/            templates, styles and the static site builder
data/snapshots/    one folder per night
tests/             tests against recorded real responses
```

## Commands

```
python -m speculum snapshot [--date YYYY-MM-DD] [--only votes finance lobbying]
python -m speculum build --out dist
pytest
```

Setup steps are in [SETUP.md](SETUP.md).
