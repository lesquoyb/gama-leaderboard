#!/usr/bin/env python3
"""
Build a GitHub leaderboard JSON file.

Data sources
------------
- GitHub REST API: commits, issues, pull requests for every configured repo.
- Local `git clone` of every wiki listed in config.wiki_repos (URL format:
  https://github.com/<owner>/<repo>.wiki.git). Wikis are not exposed by the
  REST API so we must parse git log --numstat locally.

Per-user metrics (also bucketed by week and by repo)
----------------------------------------------------
  commits, java_lines, gaml_lines, wiki_lines,
  issues_opened, issues_closed, prs_opened, prs_merged,
  global_score (normalized mean, computed on totals).

Bug-fixes compared to the previous monthly version
--------------------------------------------------
* Merge commits are skipped (`parents` length > 1). Counting them inflated
  java_lines and gaml_lines massively because merge commits expose the full
  symmetric diff of the two branches.
* Author resolution no longer silently falls back to the git *name* when the
  GitHub login is missing — that used to split one user across two keys
  ("John Doe" and "johndoe"). We now resolve via config.author_map by email
  then by name, and only then fall back to email as a last-resort id.
* line counts = additions + deletions on the target extension, exactly what
  the UI labels as "lines touched".
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
OUTPUT_PATH = ROOT / "docs" / "data.json"

API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
UA = "gama-leaderboard-builder/2.0"

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
# Config helpers
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
    if not raw:
        return None
    if len(raw) == 40 and all(c in "0123456789abcdef" for c in raw.lower()):
        data, _ = gh_request(f"{API}/repos/{repo}/commits/{raw}")
        if data:
            return data["commit"]["author"]["date"]
        return None
    return raw


# Cache for repo metadata (/repos/{repo}).
_REPO_META_CACHE: dict[str, dict] = {}


def repo_meta(repo: str) -> dict:
    if repo in _REPO_META_CACHE:
        return _REPO_META_CACHE[repo]
    data, _ = gh_request(f"{API}/repos/{repo}")
    _REPO_META_CACHE[repo] = data or {}
    return _REPO_META_CACHE[repo]


def effective_since(repo: str, raw_since: str | None) -> str | None:
    """Cap `raw_since` at the repo's own `created_at`.

    BUG FIX #4: `gama.experimental` and similar repos are created by pushing
    pre-existing history from another repo (so they are NOT marked `fork` on
    the API, but /commits returns years of imported commits). Without this
    cap, every historical author is credited on the new repo.
    """
    meta = repo_meta(repo)
    created = meta.get("created_at")
    if not created:
        return raw_since
    if not raw_since or created > raw_since:
        return created
    return raw_since


# ---------------------------------------------------------------------------
# Author / bucket helpers
# ---------------------------------------------------------------------------

def is_bot(login: str) -> bool:
    if not login:
        return True
    low = login.lower()
    return (
        login.endswith("[bot]")
        or login in {"web-flow", "github-actions"}
        or "bot" in low and low.endswith("bot")
    )


def resolve_author(
    login: str | None,
    email: str | None,
    name: str | None,
    author_map: dict,
) -> str | None:
    """Produce a stable canonical id for a contributor.

    Precedence:
      1. GitHub login (if the commit is linked to a GitHub account)
      2. author_map[email] / author_map[name] (manual override)
      3. email
      4. name (last resort)
    """
    if login:
        return login
    if email and email in author_map:
        return author_map[email]
    if name and name in author_map:
        return author_map[name]
    if email:
        return email
    if name:
        return name
    return None


def classify(filename: str, cfg: dict) -> str | None:
    m = cfg.get("metrics", {})
    for ext in m.get("java_extensions", [".java"]):
        if filename.endswith(ext):
            return "java_lines"
    for ext in m.get("gaml_extensions", [".gaml"]):
        if filename.endswith(ext):
            return "gaml_lines"
    return None


def day_of(iso: str) -> str:
    """Return the calendar day (YYYY-MM-DD) of the given ISO datetime."""
    if not iso:
        return "0000-00-00"
    s = iso.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except ValueError:
        return iso[:10] if len(iso) >= 10 else "0000-00-00"


# Cache for repository languages (GitHub /languages endpoint).
_LANG_CACHE: dict[str, set[str]] = {}


def repo_languages(repo: str) -> set[str]:
    """Return the set of languages GitHub detects in the repo. Cached."""
    if repo in _LANG_CACHE:
        return _LANG_CACHE[repo]
    data, _ = gh_request(f"{API}/repos/{repo}/languages")
    langs = set((data or {}).keys())
    _LANG_CACHE[repo] = langs
    return langs


def new_user() -> dict:
    return {
        "avatar_url": "",
        "html_url": "",
        "timeline": {},   # day (YYYY-MM-DD) -> { metric: int }
        "per_repo": {},   # repo (owner/name) -> { metric: int }
    }


def bump(user: dict, day: str, repo: str, metric: str, amount: int = 1) -> None:
    if amount == 0:
        return
    tl = user["timeline"].setdefault(day, {m: 0 for m in METRICS})
    tl[metric] += amount
    pr = user["per_repo"].setdefault(repo, {m: 0 for m in METRICS})
    pr[metric] += amount


# ---------------------------------------------------------------------------
# Per-repo processing (commits / issues / PRs via REST API)
# ---------------------------------------------------------------------------

def process_commits(
    repo: str, cfg: dict, users: dict[str, dict], since: str | None
) -> dict:
    author_map = cfg.get("author_map") or {}
    params: dict = {}
    if since:
        params["since"] = since

    # BUG FIX #3: safeguard against java_lines leaking into repos that contain
    # no Java code at all. We ask GitHub /repos/{repo}/languages once and only
    # accept a java bucket if Java is actually detected in the repo.
    langs = repo_languages(repo)
    has_java = "Java" in langs
    repo_created = repo_meta(repo).get("created_at") or ""
    print(
        f"[info] {repo}: commits since {params.get('since', 'ALL')}"
        f" (languages: {', '.join(sorted(langs)) or 'none'},"
        f" created {repo_created or '?'})",
        file=sys.stderr,
    )
    max_commits = cfg.get("max_commits_per_repo", 2000)

    stats = {
        "fetched": 0,
        "merges_skipped": 0,
        "no_author": 0,
        "java": 0,
        "gaml": 0,
        "java_rejected": 0,
        "pre_repo_skipped": 0,
    }

    for commit in gh_paginate(f"{API}/repos/{repo}/commits", params):
        if stats["fetched"] >= max_commits:
            print(f"[warn] {repo}: hit max_commits cap ({max_commits})", file=sys.stderr)
            break
        stats["fetched"] += 1

        # BUG FIX #1: skip merge commits — their diff is the symmetric branch
        # diff and would be double-counted against the author.
        if len(commit.get("parents") or []) > 1:
            stats["merges_skipped"] += 1
            continue

        # BUG FIX #4: drop commits authored before the repo itself existed.
        # Such commits were imported from another repository's history (typical
        # when a repo is split off, e.g. gama.experimental) and must not be
        # credited to this repo.
        git_author = (commit.get("commit") or {}).get("author") or {}
        commit_date = git_author.get("date", "") or ""
        if repo_created and commit_date and commit_date < repo_created:
            stats["pre_repo_skipped"] += 1
            continue

        api_author = commit.get("author") or {}
        login = api_author.get("login")
        email = git_author.get("email") or ""
        name = git_author.get("name") or ""

        canonical = resolve_author(login, email, name, author_map)
        if not canonical:
            stats["no_author"] += 1
            continue
        if cfg.get("exclude_bots", True) and is_bot(canonical):
            continue

        u = users.setdefault(canonical, new_user())
        if api_author.get("avatar_url") and not u["avatar_url"]:
            u["avatar_url"] = api_author["avatar_url"]
            u["html_url"] = api_author.get("html_url", "")

        day = day_of(commit_date)
        bump(u, day, repo, "commits", 1)

        sha = commit["sha"]
        detail, _ = gh_request(f"{API}/repos/{repo}/commits/{sha}")
        if not detail:
            continue
        for f in detail.get("files", []) or []:
            filename = f.get("filename", "")
            bucket = classify(filename, cfg)
            if not bucket:
                continue
            if bucket == "java_lines" and not has_java:
                # Should not happen: the file ends in .java but /languages
                # does not list Java for this repo. Log once for diagnosis.
                if stats["java_rejected"] < 3:
                    print(
                        f"[warn] {repo}: rejecting java classification for {filename}"
                        f" (repo languages: {sorted(langs)})",
                        file=sys.stderr,
                    )
                stats["java_rejected"] += 1
                continue
            changes = int(f.get("additions", 0)) + int(f.get("deletions", 0))
            bump(u, day, repo, bucket, changes)
            if bucket == "java_lines":
                stats["java"] += changes
            elif bucket == "gaml_lines":
                stats["gaml"] += changes

    print(
        f"[stats] {repo}: {stats['fetched']} commits "
        f"({stats['merges_skipped']} merges skipped, "
        f"{stats['pre_repo_skipped']} pre-creation skipped, "
        f"{stats['no_author']} unattributed, "
        f"{stats['java_rejected']} java-rejected), "
        f"java={stats['java']} gaml={stats['gaml']}",
        file=sys.stderr,
    )
    return stats


def process_issues(
    repo: str, cfg: dict, users: dict[str, dict], since: str | None
) -> None:
    author_map = cfg.get("author_map") or {}
    params = {"state": "all", "filter": "all"}
    if since:
        params["since"] = since
    print(f"[info] {repo}: issues", file=sys.stderr)
    for issue in gh_paginate(f"{API}/repos/{repo}/issues", params):
        if "pull_request" in issue:
            continue
        created = issue.get("created_at") or ""
        if since and created < since:
            continue
        api_user = issue.get("user") or {}
        canonical = resolve_author(api_user.get("login"), None, None, author_map)
        if not canonical or (cfg.get("exclude_bots", True) and is_bot(canonical)):
            continue
        u = users.setdefault(canonical, new_user())
        if not u["avatar_url"] and api_user.get("avatar_url"):
            u["avatar_url"] = api_user["avatar_url"]
            u["html_url"] = api_user.get("html_url", "")
        bump(u, day_of(created), repo, "issues_opened", 1)
        if issue.get("state") == "closed" and issue.get("closed_at"):
            bump(u, day_of(issue["closed_at"]), repo, "issues_closed", 1)


def process_prs(
    repo: str, cfg: dict, users: dict[str, dict], since: str | None
) -> None:
    author_map = cfg.get("author_map") or {}
    params = {"state": "all", "sort": "created", "direction": "desc"}
    print(f"[info] {repo}: pulls", file=sys.stderr)
    for pr in gh_paginate(f"{API}/repos/{repo}/pulls", params):
        created = pr.get("created_at") or ""
        if since and created < since:
            break
        api_user = pr.get("user") or {}
        canonical = resolve_author(api_user.get("login"), None, None, author_map)
        if not canonical or (cfg.get("exclude_bots", True) and is_bot(canonical)):
            continue
        u = users.setdefault(canonical, new_user())
        if not u["avatar_url"] and api_user.get("avatar_url"):
            u["avatar_url"] = api_user["avatar_url"]
            u["html_url"] = api_user.get("html_url", "")
        bump(u, day_of(created), repo, "prs_opened", 1)
        merged_at = pr.get("merged_at")
        if merged_at:
            bump(u, day_of(merged_at), repo, "prs_merged", 1)


# ---------------------------------------------------------------------------
# Wiki processing (git clone + git log)
# ---------------------------------------------------------------------------

def process_wiki_clone(
    base_repo: str, cfg: dict, users: dict[str, dict], since: str | None
) -> None:
    wiki_url = f"https://github.com/{base_repo}.wiki.git"
    wiki_label = f"{base_repo}.wiki"
    author_map = cfg.get("author_map") or {}
    print(f"[info] cloning wiki {wiki_url}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                ["git", "clone", "--quiet", "--filter=blob:none", wiki_url, tmp],
                check=True, capture_output=True, text=True, timeout=600,
            )
        except subprocess.CalledProcessError as e:
            err = (e.stderr or "").strip().splitlines()[-1:]
            print(f"[warn] wiki clone failed for {base_repo}: {err}", file=sys.stderr)
            return
        except subprocess.TimeoutExpired:
            print(f"[warn] wiki clone timed out for {base_repo}", file=sys.stderr)
            return

        log_args = [
            "git", "-C", tmp, "log",
            "--no-merges",
            "--numstat",
            "--date=iso-strict",
            "--pretty=format:__C__%H|%an|%ae|%aI",
        ]
        if since:
            log_args.extend(["--since", since])
        try:
            result = subprocess.run(
                log_args, capture_output=True, text=True, check=True, timeout=300
            )
        except subprocess.CalledProcessError as e:
            print(f"[warn] wiki git log failed for {base_repo}: {e.stderr}", file=sys.stderr)
            return

        current_user = None
        current_day = None
        commits = 0
        lines = 0
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            if line.startswith("__C__"):
                rest = line[len("__C__"):]
                parts = rest.split("|", 3)
                if len(parts) < 4:
                    current_user = None
                    continue
                _sha, name, email, date = parts
                canonical = resolve_author(None, email, name, author_map)
                if not canonical or (cfg.get("exclude_bots", True) and is_bot(canonical)):
                    current_user = None
                    continue
                current_user = users.setdefault(canonical, new_user())
                current_day = day_of(date)
                bump(current_user, current_day, wiki_label, "commits", 1)
                commits += 1
            else:
                if not current_user:
                    continue
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                adds, dels, _path = parts[0], parts[1], parts[2]
                if adds == "-" or dels == "-":
                    continue  # binary file
                try:
                    total = int(adds) + int(dels)
                except ValueError:
                    continue
                bump(current_user, current_day, wiki_label, "wiki_lines", total)
                lines += total
        print(f"[stats] {wiki_label}: {commits} commits, {lines} wiki lines", file=sys.stderr)


# ---------------------------------------------------------------------------
# Finalization
# ---------------------------------------------------------------------------

def totals_from_timeline(timeline: dict) -> dict:
    out = {m: 0 for m in METRICS}
    for day_data in timeline.values():
        for m in METRICS:
            out[m] += day_data.get(m, 0)
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
    wiki_repos = cfg.get("wiki_repos") or []
    print(f"[info] {len(repos)} api repo(s), {len(wiki_repos)} wiki(s)", file=sys.stderr)

    users: dict[str, dict] = {}
    for repo in repos:
        try:
            raw_since = since_for(repo, cfg)
            since = effective_since(repo, resolve_since(repo, raw_since))
            process_commits(repo, cfg, users, since)
            process_issues(repo, cfg, users, since)
            process_prs(repo, cfg, users, since)
        except Exception as e:  # noqa: BLE001
            print(f"[error] {repo}: {e}", file=sys.stderr)

    for base_repo in wiki_repos:
        try:
            since = resolve_since(base_repo, since_for(base_repo, cfg))
            # Wikis live in a separate `.wiki` repo, but we reuse the base
            # repo's creation date as a lower bound.
            since = effective_since(base_repo, since)
            process_wiki_clone(base_repo, cfg, users, since)
        except Exception as e:  # noqa: BLE001
            print(f"[error] wiki {base_repo}: {e}", file=sys.stderr)

    # Build user records with totals.
    users_list: list[dict] = []
    all_days: set[str] = set()
    all_repos: set[str] = set()
    for login, data in users.items():
        totals = totals_from_timeline(data["timeline"])
        all_days.update(data["timeline"].keys())
        all_repos.update(data["per_repo"].keys())
        users_list.append({
            "login": login,
            "avatar_url": data["avatar_url"],
            "html_url": data["html_url"],
            **totals,
            "timeline": data["timeline"],
            "per_repo": data["per_repo"],
        })
    compute_global(users_list)
    users_list.sort(key=lambda u: u["global_score"], reverse=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "org": cfg.get("org"),
            "repos": repos,
            "wiki_repos": wiki_repos,
            "since": cfg.get("since"),
        },
        "metrics": METRICS,
        "days": sorted(d for d in all_days if d and d != "0000-00-00"),
        "repos": sorted(all_repos),
        "users": users_list,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(
        f"[ok] wrote {OUTPUT_PATH} ({len(users_list)} users, "
        f"{len(payload['days'])} days, {len(payload['repos'])} repos)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
