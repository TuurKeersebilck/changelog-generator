#!/usr/bin/env python3
"""
Changelog generator — fetches merged PRs since last release, rewrites them
in user-friendly language via a local Ollama model, and outputs a changelog entry.

Usage:
    python generate.py                                         # auto-detects repo from current directory
    python generate.py --owner TuurKeersebilck --repo TimeManagement
    python generate.py --backfill
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import requests

GITHUB_API = "https://api.github.com"
OLLAMA_API = "http://localhost:11434/api/generate"

SKIP_PREFIXES = ("chore(deps)", "chore: bump", "dependabot")

TYPE_MAP = {
    "feat": "New Features",
    "fix": "Bug Fixes",
    "perf": "Improvements",
    "refactor": "Improvements",
    "docs": None,
    "chore": None,
    "ci": None,
    "test": None,
    "build": None,
}

PROMPT_TEMPLATE = """You are writing a changelog for end users, not developers.
Rewrite the following changes as short, friendly bullet points.

Rules:
- Use plain English — no jargon, no technical terms like "refactor", "PR", "commit", "flag", "chore", "deps", "repo", "fallback"
- Focus on the user benefit, not the implementation (e.g. "You can now run the script without setting up a token" not "Made token optional")
- Start each bullet with a verb: "Added", "Fixed", "Improved", "You can now"
- If multiple changes are about the same feature, combine them into one bullet
- Keep each bullet to one sentence
- Only output the bullet points, nothing else

Changes:
{changes}
"""

SECTION_ORDER = ["New Features", "Bug Fixes", "Improvements", "Documentation"]


def detect_owner_repo() -> tuple[str, str]:
    try:
        url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except subprocess.CalledProcessError:
        print("Error: not a git repository and --owner/--repo not specified.", file=sys.stderr)
        sys.exit(1)

    # SSH: git@github.com:owner/repo.git
    # HTTPS: https://github.com/owner/repo.git
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not match:
        print(f"Error: could not parse GitHub owner/repo from remote URL: {url}", file=sys.stderr)
        sys.exit(1)

    return match.group(1), match.group(2)


def github_headers(token: str | None) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_all_releases(owner: str, repo: str, token: str | None) -> list[dict]:
    releases = []
    page = 1
    while True:
        r = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/releases",
            headers=github_headers(token),
            params={"per_page": 100, "page": page},
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        releases.extend(batch)
        page += 1
    return sorted(releases, key=lambda x: x["published_at"])


def fetch_merged_prs(owner: str, repo: str, since: datetime, until: datetime | None, token: str | None) -> list[dict]:
    prs = []
    page = 1
    while True:
        r = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/pulls",
            headers=github_headers(token),
            params={"state": "closed", "base": "main", "sort": "updated", "direction": "desc", "per_page": 100, "page": page},
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for pr in batch:
            if not pr.get("merged_at"):
                continue
            merged_at = datetime.fromisoformat(pr["merged_at"].replace("Z", "+00:00"))
            if merged_at <= since:
                return prs
            if until is None or merged_at <= until:
                prs.append(pr)
        page += 1
    return prs


def fetch_commits(owner: str, repo: str, since: datetime, until: datetime | None, token: str | None) -> list[str]:
    titles = []
    page = 1
    skip_patterns = ("merge pull request", "merge branch", "dependabot")
    while True:
        params = {"per_page": 100, "page": page}
        if since.year > 2000:
            params["since"] = since.isoformat()
        if until:
            params["until"] = until.isoformat()
        r = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/commits",
            headers=github_headers(token),
            params=params,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        for commit in batch:
            subject = commit["commit"]["message"].splitlines()[0].strip()
            if any(subject.lower().startswith(p) for p in skip_patterns):
                continue
            titles.append(subject)
        page += 1
    return titles


def classify_title(title: str) -> tuple[str | None, str]:
    lower = title.lower()
    for skip in SKIP_PREFIXES:
        if lower.startswith(skip):
            return None, title
    for prefix, category in TYPE_MAP.items():
        if lower.startswith(f"{prefix}:") or lower.startswith(f"{prefix}("):
            clean = title.split(":", 1)[-1].strip()
            return category, clean[0].upper() + clean[1:] if clean else clean
    return "Improvements", title


def group_items(titles: list[str]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for title in titles:
        category, clean_title = classify_title(title)
        if category is None:
            continue
        groups.setdefault(category, []).append(clean_title)
    return groups


def fetch_and_group(owner: str, repo: str, since: datetime, until: datetime | None, token: str | None) -> tuple[dict[str, list[str]], str]:
    """Returns (groups, source) where source is 'PRs' or 'commits'."""
    prs = fetch_merged_prs(owner, repo, since, until, token)
    if prs:
        return group_items([pr["title"] for pr in prs]), "PRs"

    print("No merged PRs found — falling back to commits...")
    commits = fetch_commits(owner, repo, since, until, token)
    return group_items(commits), "commits"


def rewrite_with_ollama(titles: list[str], model: str) -> list[str]:
    prompt = PROMPT_TEMPLATE.format(changes="\n".join(f"- {t}" for t in titles))
    try:
        r = requests.post(OLLAMA_API, json={"model": model, "prompt": prompt, "stream": False}, timeout=120)
        r.raise_for_status()
        response_text = r.json().get("response", "").strip()
        lines = [l.strip().lstrip("-• ").strip() for l in response_text.splitlines() if l.strip()]
        return [f"- {l}" for l in lines if l]
    except requests.exceptions.ConnectionError:
        print("Warning: Ollama not reachable at localhost:11434 — using raw titles.", file=sys.stderr)
        return [f"- {t}" for t in titles]


def build_entry(groups: dict[str, list[str]], model: str, version: str, date: str) -> str:
    header = f"## [{version}]" if version == date else f"## [{version}] - {date}"
    lines = [header + "\n"]
    for section in SECTION_ORDER:
        titles = groups.get(section)
        if not titles:
            continue
        print(f"  Rewriting {len(titles)} entries for '{section}'...")
        bullets = rewrite_with_ollama(titles, model)
        lines.append(f"### {section}\n")
        lines.extend(bullets)
        lines.append("")
    return "\n".join(lines)


def parse_changelog(path: str) -> tuple[set[str], datetime | None]:
    """Returns (existing version labels, date of most recent entry)."""
    versions: set[str] = set()
    last_date: datetime | None = None
    if not os.path.exists(path):
        return versions, None
    with open(path) as f:
        for line in f:
            # ## [v1.0.0] - 2026-05-14
            match = re.search(r'^## \[(.*?)\] - (\d{4}-\d{2}-\d{2})', line)
            if match:
                versions.add(match.group(1))
                if last_date is None:
                    last_date = datetime.strptime(match.group(2), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                continue
            # ## [2026-05-14]
            match = re.search(r'^## \[(\d{4}-\d{2}-\d{2})\]', line)
            if match:
                versions.add(match.group(1))
                if last_date is None:
                    last_date = datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return versions, last_date


def prepend_to_changelog(entries: list[str], path: str):
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()

    header = "# Changelog\n\n"
    body = existing[len(header):] if existing.startswith(header) else existing

    # Replace any existing sections whose version label matches an incoming entry
    for entry in entries:
        match = re.search(r'^## \[(.*?)\]', entry)
        if not match:
            continue
        version = re.escape(match.group(1))
        # Remove the existing section for this version (from its header to the next ## or end)
        body = re.sub(
            rf'## \[{version}\].*?(?=^## |\Z)',
            '',
            body,
            flags=re.DOTALL | re.MULTILINE,
        ).lstrip("\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(entries) + "\n" + body)

    print(f"Written to {path}")


def main():
    parser = argparse.ArgumentParser(description="Generate a user-friendly changelog via Ollama")
    parser.add_argument("--owner", help="GitHub username or org (default: auto-detected from git remote)")
    parser.add_argument("--repo", help="Repository name (default: auto-detected from git remote)")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--version", help="Version label (default: auto-detected from latest release tag)")
    parser.add_argument("--output", default="CHANGELOG.md")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--backfill", action="store_true", help="Generate entries for all historical releases")
    parser.add_argument("--since-date", help="Fetch PRs merged after this date (YYYY-MM-DD), ignores releases")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Warning: GITHUB_TOKEN not set — using unauthenticated API (60 req/hr limit).", file=sys.stderr)

    if not args.owner or not args.repo:
        detected_owner, detected_repo = detect_owner_repo()
        owner = args.owner or detected_owner
        repo = args.repo or detected_repo
        print(f"Auto-detected repo: {owner}/{repo}")
    else:
        owner, repo = args.owner, args.repo

    # --since-date bypasses releases entirely
    if args.since_date:
        try:
            since_date = datetime.strptime(args.since_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print("Error: --since-date must be in YYYY-MM-DD format.", file=sys.stderr)
            sys.exit(1)
        date = datetime.now().strftime("%Y-%m-%d")
        version = args.version or date
        groups, source = fetch_and_group(owner, repo, since_date, None, token)
        total = sum(len(v) for v in groups.values())
        if not total:
            print("Nothing to changelog.")
            return
        print(f"{total} entries from {source} to rewrite across {len(groups)} sections.")
        entry = build_entry(groups, args.model, version, date)
        if args.print_only:
            print("\n" + entry)
        else:
            prepend_to_changelog([entry], args.output)
        return

    releases = fetch_all_releases(owner, repo, token)

    if not releases:
        _, last_date = parse_changelog(args.output)
        since = last_date or datetime(2000, 1, 1, tzinfo=timezone.utc)
        if last_date:
            print(f"No releases found — fetching changes since last entry ({last_date.strftime('%Y-%m-%d')})...")
        else:
            print("No releases found — generating changelog for all commits...")
        date = datetime.now().strftime("%Y-%m-%d")
        version = args.version or date
        groups, source = fetch_and_group(owner, repo, since, None, token)
        total = sum(len(v) for v in groups.values())
        if not total:
            print("Nothing new to changelog.")
            return
        print(f"{total} entries from {source} to rewrite across {len(groups)} sections.")
        entry = build_entry(groups, args.model, version, date)
        if args.print_only:
            print("\n" + entry)
        else:
            prepend_to_changelog([entry], args.output)
        return

    if args.backfill:
        existing_versions, _ = parse_changelog(args.output)
        print(f"Backfilling {len(releases)} releases...")
        entries = []
        for i, release in enumerate(releases):
            version = release["tag_name"]
            date = release["published_at"][:10]
            since_date = (
                datetime.fromisoformat(releases[i - 1]["published_at"].replace("Z", "+00:00"))
                if i > 0
                else datetime(2000, 1, 1, tzinfo=timezone.utc)
            )
            until_date = datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))

            if version in existing_versions:
                print(f"\n[{version}] already in changelog — skipping")
                continue
            print(f"\n[{version}] {date}")
            groups, source = fetch_and_group(owner, repo, since_date, until_date, token)
            total = sum(len(v) for v in groups.values())
            print(f"  {total} entries from {source}")
            if total == 0:
                continue

            entry = build_entry(groups, args.model, version, date)
            entries.append(entry)

        if not entries:
            print("Nothing to backfill.")
            return

        # Newest first
        entries.reverse()
        if args.print_only:
            print("\n# Changelog\n")
            print("\n".join(entries))
        else:
            prepend_to_changelog(entries, args.output)

    else:
        latest = releases[-1]
        version = args.version or latest["tag_name"]
        date = latest["published_at"][:10]
        since_date = (
            datetime.fromisoformat(releases[-2]["published_at"].replace("Z", "+00:00"))
            if len(releases) > 1
            else datetime(2000, 1, 1, tzinfo=timezone.utc)
        )

        print(f"Generating changelog for {owner}/{repo} — {version} ({date})")
        groups, source = fetch_and_group(owner, repo, since_date, None, token)
        total = sum(len(v) for v in groups.values())
        if not total:
            print("Nothing to changelog.")
            return
        print(f"{total} entries from {source} to rewrite across {len(groups)} sections.")

        entry = build_entry(groups, args.model, version, date)

        if args.print_only:
            print("\n" + entry)
        else:
            prepend_to_changelog([entry], args.output)


if __name__ == "__main__":
    main()
