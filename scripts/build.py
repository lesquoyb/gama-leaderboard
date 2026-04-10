#!/usr/bin/env python3
"""
Build a GitHub leaderboard JSON file from public GitHub API data.

Metrics per user (aggregated AND bucketed by month in `timeline`):
  - commits
  - java_lines, gaml_lines, wiki_lines
  - issues_opened, issues_closed
  - prs_opened, prs_merged
  - global_score (normalized mean of all metrics, computed on totals)

Reads config.json at repo root, writes site/data.json.
Auth: set GITHUB_TOKEN env var (uses unauthenticated API otherwise).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
OUTPUT_PATH = ROOT / "site" / "data.json"

API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
UA = "gama-leaderboard-builder/1.0"

METRICS = [
    "commits",
    "java_lines",
    "gaml_lines",
    "wiki_lines",
    "issues_opened",
    "issues_closed",
    "prs_opened",
    "prs_merged",
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def gh_request(url: str, params: dict | None = None) -> tuple[Any, dict[str, str]]:
    if params:
        url = f"{url}?{urlencode(params)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    for attempt in range(5):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else None, dict(resp.headers)
        except HTTPError as e:
            msg = ""
            try:
                msg = e.read().decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                pass
            if e.code == 403 and "rate limit" in msg.lower():
                reset = int(e.headers.get("X-RateLimit-Reset", "0") or "0")
                wait = max(5, reset - int(time.time()) + 2)
                print(f"[rate-limit] sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code in (502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            if e.code == 404:
                return None, {}
            raise
        except URLError:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"gave up on {url}")


def gh_paginate(url: str, params: dict | None = None, max_pages: int = 200) -> Iterable[dict]:
    params = dict(params or {})
    params.setdefault("per_page", 100)
    page = 1
    while page <= max_pages:
        params["page"] = page
        data, _ = gh_request(url, params)
        if not data:
            return
        if isinstance(data, dict):
            yield data
            return
        yield from data
        if len(data) < params["per_page"]:
            return
        page += 1


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_repos(cfg: dict) -> list[str]:
    repos: list[str] = list(cfg.get("repos") or [])
    org = cfg.get("org")
    if org:
        print(f"[info] listing repos for org {org}", file=sys.stderr)
        for r in gh_paginate(f"{API}/orgs/{org}/repos", {"type": "public"}):
            if r.get("fork"):
                continue
            repos.append(r["full_name"])
    exclude = set(cfg.get("exclude_repos") or [])
    seen: set[str] = set()
    out: list[str] = []
    for r in repos:
        if r in exclude or r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def since_for(repo: str, cfg: dict) -> str | None:
    overrides = cfg.get("repo_overrides") or {}
    if repo in overrides and overrides[repo].get("since"):
        return overrides[repo]["since"]
    return cfg.get("since")


def resolve_since(repo: str, raw: str | None) -> str | None:
    """Resolve an ISO date string OR a 40-char commit SHA to an ISO timestamp."""
    if not raw:
        return None
    if len(raw) == 40 and all(c in "0123456789abcdef" for c in raw.lower()):
        data, _ = gh_request(f"{API}/repos/{repo}/commits/{raw}")
        if data:
            return data["commit"]["author"]["date"]
        return None
    return raw


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def is_bot(login: str) -> bool:
    if not login:
        return True
    return login.endswith("[bot]") or login in {"web-flow", "github-actions"}


def classify(filename: str, cfg: dict, is_wiki_repo: bool) -> str | None:
    m = cfg.get("metrics", {})
    if is_wiki_repo:
        return "wiki_lines"
    for ext in m.get("java_extensions", [".java"]):
        if filename.endswith(ext):
            return "java_lines"
    for ext in m.get("gaml_extensions", [".gaml"]):
        if filename.endswith(ext):
            return "gaml_lines"
    return None


def new_user() -> dict:
    return {
        "avatar_url": "",
        "html_url": "",
        "timeline": {},  # "YYYY-MM" -> {metric: int}
    }


def bump(user: dict, month: str, metric: str, amount: int = 1) -> None:
    bucket = user["timeline"].setdefault(month, {m: 0 for m in METRICS})
    bucket[metric] += amount


def month_of(iso: str) -> str:
    # "2024-03-15T12:00:00Z" -> "2024-03"
    return iso[:7]


def iso_geq(a: str | None, b: str | None) -> bool:
    if not b:
        return True
    return (a or "") >= b


# ---------------------------------------------------------------------------
# Per-repo processing
# ---------------------------------------------------------------------------

def process_commits(repo: str, cfg: dict, users: dict[str, dict], since: str | None) -> None:
    owner, name = repo.split("/", 1)
    is_wiki = name.endswith(cfg.get("metrics", {}).get("wiki_repos_suffix", ".wiki"))
    params: dict = {}
    if since:
        params["since"] = since
    print(f"[info] {repo}: commits since {params.get('since', 'ALL')}", file=sys.stderr)
    max_commits = cfg.get("max_commits_per_repo", 2000)
    count = 0
    for commit in gh_paginate(f"{API}/repos/{repo}/commits", params):
        if count >= max_commits:
            print(f"[warn] {repo}: hit max_commits cap ({max_commits})", file=sys.stderr)
            break
        count += 1
        author = commit.get("author") or {}
        login = author.get("login") or (commit.get("commit", {}).get("author", {}).get("name", ""))
        if not login or (cfg.get("exclude_bots", True) and is_bot(login)):
            continue
        u = users.setdefault(login, new_user())
        if author.get("avatar_url"):
            u["avatar_url"] = author["avatar_url"]
            u["html_url"] = author.get("html_url", "")

        date = commit.get("commit", {}).get("author", {}).get("date", "")
        month = month_of(date) if date else "0000-00"
        bump(u, month, "commits", 1)

        sha = commit["sha"]
        detail, _ = gh_request(f"{API}/repos/{repo}/commits/{sha}")
        if not detail:
            continue
        for f in detail.get("files", []) or []:
            bucket = classify(f.get("filename", ""), cfg, is_wiki)
            if not bucket:
                continue
            changes = int(f.get("additions", 0)) + int(f.get("deletions", 0))
            bump(u, month, bucket, changes)


def process_issues(repo: str, cfg: dict, users: dict[str, dict], since: str | None) -> None:
    # /issues returns both issues AND PRs; we split them here.
    params = {"state": "all", "filter": "all"}
    if since:
        params["since"] = since
    print(f"[info] {repo}: issues", file=sys.stderr)
    for issue in gh_paginate(f"{API}/repos/{repo}/issues", params):
        if "pull_request" in issue:
            continue  # PRs handled in process_prs
        created = issue.get("created_at") or ""
        if since and created < since:
            continue
        user = (issue.get("user") or {}).get("login")
        if not user or (cfg.get("exclude_bots", True) and is_bot(user)):
            continue
        u = users.setdefault(user, new_user())
        if not u["avatar_url"]:
            u["avatar_url"] = (issue.get("user") or {}).get("avatar_url", "")
            u["html_url"] = (issue.get("user") or {}).get("html_url", "")
        bump(u, month_of(created), "issues_opened", 1)
        if issue.get("state") == "closed" and issue.get("closed_at"):
            bump(u, month_of(issue["closed_at"]), "issues_closed", 1)


def process_prs(repo: str, cfg: dict, users: dict[str, dict], since: str | None) -> None:
    # /pulls doesn't support ?since so we iterate sorted desc and stop early.
    print(f"[info] {repo}: pulls", file=sys.stderr)
    params = {"state": "all", "sort": "created", "direction": "desc"}
    for pr in gh_paginate(f"{API}/repos/{repo}/pulls", params):
        created = pr.get("created_at") or ""
        if since and created < since:
            break  # list is sorted desc — safe to stop
        user = (pr.get("user") or {}).get("login")
        if not user or (cfg.get("exclude_bots", True) and is_bot(user)):
            continue
        u = users.setdefault(user, new_user())
        if not u["avatar_url"]:
            u["avatar_url"] = (pr.get("user") or {}).get("avatar_url", "")
            u["html_url"] = (pr.get("user") or {}).get("html_url", "")
        bump(u, month_of(created), "prs_opened", 1)
        merged_at = pr.get("merged_at")
        if merged_at:
            bump(u, month_of(merged_at), "prs_merged", 1)


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------

def totals_from_timeline(timeline: dict) -> dict:
    out = {m: 0 for m in METRICS}
    for month_data in timeline.values():
        for m in METRICS:
            out[m] += month_data.get(m, 0)
    return out


def compute_global(users_list: list[dict]) -> None:
    maxes = {m: max((u[m] for u in users_list), default=0) for m in METRICS}
    for u in users_list:
        parts = [(u[m] / maxes[m]) if maxes[m] > 0 else 0.0 for m in METRICS]
        u["global_score"] = round(sum(parts) / len(parts), 4)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    cfg = load_config()
    if not TOKEN:
        print("[warn] no GITHUB_TOKEN set — using unauthenticated API (60 req/h)", file=sys.stderr)

    repos = resolve_repos(cfg)
    print(f"[info] {len(repos)} repo(s) to analyze", file=sys.stderr)

    users: dict[str, dict] = {}
    for repo in repos:
        try:
            raw_since = since_for(repo, cfg)
            since = resolve_since(repo, raw_since)
            process_commits(repo, cfg, users, since)
            process_issues(repo, cfg, users, since)
            process_prs(repo, cfg, users, since)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {repo}: {e}", file=sys.stderr)

    # Build flat user records with totals + keep timeline for UI filtering.
    users_list = []
    all_months: set[str] = set()
    for login, data in users.items():
        totals = totals_from_timeline(data["timeline"])
        all_months.update(data["timeline"].keys())
        users_list.append({
            "login": login,
            "avatar_url": data["avatar_url"],
            "html_url": data["html_url"],
            **totals,
            "timeline": data["timeline"],
        })
    compute_global(users_list)
    users_list.sort(key=lambda u: u["global_score"], reverse=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "org": cfg.get("org"),
            "repos": repos,
            "since": cfg.get("since"),
        },
        "metrics": METRICS,
        "months": sorted(m for m in all_months if m and m != "0000-00"),
        "users": users_list,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"[ok] wrote {OUTPUT_PATH} ({len(users_list)} users, {len(payload['months'])} months)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
