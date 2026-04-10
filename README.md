# GitHub Leaderboard

Static site that ranks GitHub contributors across one or more repositories (or a whole organization) from a given start date. Data is fetched from the public GitHub REST API by a Python script, wiki contributions are collected by cloning wiki repositories locally, and the result is published as a static site on GitHub Pages.

## Metrics

| Metric | Description |
|---|---|
| `commits` | Number of non-merge commits authored |
| `java_lines` | Lines changed (additions + deletions) on `.java` files |
| `gaml_lines` | Lines changed on `.gaml` / `.experiment` files |
| `wiki_lines` | Lines changed on wikis (see below) |
| `issues_opened` | Issues opened by the user |
| `issues_closed` | Issues opened by the user that are now closed |
| `prs_opened` | Pull requests opened by the user |
| `prs_merged` | Pull requests authored by the user that were merged |
| `global_score` | Normalized mean of all metrics (0–1), recomputed client-side whenever the range / repo filter changes |

Every metric is bucketed two ways in the output `data.json`:

- **`timeline`**: `{ "YYYY-MM-DD" (Monday of week) → { metric → value } }` — drives the date range filter and the evolution chart.
- **`per_repo`**: `{ "owner/repo" → { metric → value } }` — drives the per-repo breakdown in the profile view and the repo filter dropdown.

### Wiki ingestion

The GitHub REST API does **not** expose wiki repositories. To count wiki contributions, the builder runs a `git clone https://github.com/<owner>/<repo>.wiki.git` and parses `git log --numstat`. Add the base repo names (no `.wiki` suffix) to `config.wiki_repos`:

```json
{ "wiki_repos": ["gama-platform/gama"] }
```

Because wiki commits carry only git author name/email (no GitHub login), use `author_map` to merge them with the corresponding GitHub account — see below.

### Correctness notes (bug-fixes vs. v1)

- **Merge commits are skipped** (`parents.length > 1`). Previously, merge commits were counted and their symmetric branch diff inflated `java_lines` / `gaml_lines` by a huge factor.
- **Author resolution never silently falls back to the git name**. Before, a commit with no linked GitHub account was attributed to the raw git name, splitting a single contributor into two identities (`"John Doe"` and `"johndoe"`). The new logic uses: `GitHub login > author_map[email] > author_map[name] > email > name`.
- Line counts are `additions + deletions` on the target extension (i.e. "lines touched"), exactly what the UI displays.

## Project layout

```
gama-leaderboard/
├── config.json              # repos / org / start date / wiki repos / author map
├── scripts/build.py         # fetches GitHub API + clones wikis → docs/data.json
├── docs/                    # served by GitHub Pages
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   └── data.json            # generated and committed back by the workflow
└── .github/workflows/build.yml
```

## Configuration (`config.json`)

| Field | Description |
|---|---|
| `org` | Organization whose public repositories should be analyzed. |
| `repos` | Explicit list of `owner/repo` (merged with the org list). |
| `since` | Default ISO date applied to every repository. |
| `repo_overrides` | Per-repo override. The `.since` field can be an ISO date OR a 40-char commit SHA (resolved to that commit's timestamp). |
| `wiki_repos` | Base repo names whose wiki should be cloned and parsed. |
| `author_map` | `{"email or name": "canonical-github-login"}` — merges non-linked commits into a GitHub account. |
| `exclude_repos` | Repos to skip. |
| `exclude_bots` | Skip `[bot]` accounts (default `true`). |
| `max_commits_per_repo` | Hard cap to avoid blowing the API budget (default 2000). |

Example:

```json
{
  "org": "gama-platform",
  "since": "2024-01-01T00:00:00Z",
  "wiki_repos": ["gama-platform/gama"],
  "author_map": {
    "alice@example.com": "alice-gh",
    "Bob Builder": "bobgh"
  },
  "repo_overrides": {
    "gama-platform/gama": { "since": "abcdef0123456789abcdef0123456789abcdef01" }
  }
}
```

## Local usage

```bash
export GITHUB_TOKEN=ghp_xxx   # strongly recommended (5000 req/h vs 60)
python scripts/build.py       # runs git clone for wikis — needs git on PATH
# then open docs/index.html in a browser
```

## Publishing on GitHub Pages

> The site must be served from `docs/`, **not** from the repository root, otherwise GitHub Pages will render `README.md` instead of the leaderboard.

1. Push the project to a GitHub repository.
2. Go to **Settings → Pages**.
3. Pick **one** of the following sources:
   - **GitHub Actions** (recommended): the workflow deploys `docs/` on every run.
   - **Deploy from a branch**: select branch `main`, folder `/docs`. The workflow commits the refreshed `data.json` back to `main`, so Pages picks it up automatically.
4. The workflow `.github/workflows/build.yml` runs:
   - on every push to `main` (ignoring changes limited to `docs/data.json`, to avoid loops),
   - every day at 06:00 UTC,
   - on demand (`workflow_dispatch`).
5. The `GITHUB_TOKEN` provided by Actions is used automatically. No secret to configure.
6. Paste the Pages URL (`https://<user>.github.io/<repo>/`) into **Settings → About → Website** so the leaderboard is linked from the repo home.

### Troubleshooting

- **Site shows the README**: Pages source is set to `main / (root)`. Change it to `GitHub Actions` or `main / docs`.
- **`data.json` looks stale**: hard-refresh (Ctrl+F5). `fetch` already uses `cache: "no-store"` but the HTML/JS may be cached. If the Actions run failed, see the Actions tab — the most common cause is a Pages source mismatch.
- **Contributor split in two** (e.g. `Alice` and `alice`): add an `author_map` entry in `config.json` mapping the email or name to the canonical GitHub login.

## UI features

- **Date range**: preset buttons (last week / 4 weeks / 3 months / 6 months / year / all) or explicit week picker (snaps to Monday).
- **Repo filter**: restrict every metric to a single repo — uses the `per_repo` all-time totals.
- **Metric chips**: 8 metrics + global score.
- **Podium**: clickable top-3 cards.
- **Evolution chart**: cumulative top 5 for the current metric, on the selected range.
- **Profile modal** (click any row): full metric grid (filtered + all-time), per-repo breakdown table, per-user evolution chart.

## Caveats

- Per-extension line stats still require `1 API request per commit`, capped by `max_commits_per_repo`.
- `per_repo` totals are all-time (not week-bucketed), so selecting a repo currently overrides the date range.
- `issues_closed` counts issues opened by the user that are currently `closed`, not issues closed *by* that user.
- `global_score` is the mean of `metric / max(metric)` across the currently visible users.
