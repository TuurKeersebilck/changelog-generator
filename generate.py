#!/usr/bin/env python3
"""
Changelog generator — fetches merged PRs since last release, rewrites them
in user-friendly language via a local Ollama model, and outputs a changelog entry.

Usage:
    python generate.py --owner TuurKeersebilck --repo TimeManagement
    python generate.py --owner TuurKeersebilck --repo TimeManagement --model gemma2:9b
    python generate.py --owner TuurKeersebilck --repo TimeManagement --backfill
"""

import argparse
import os
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
    "docs": "Documentation",
    "chore": None,
    "ci": None,
    "test": None,
    "build": None,
}

PROMPT_TEMPLATE = """You are writing a changelog for end users, not developers.
Rewrite each of the following changes as a short, friendly bullet point.

Rules:
- Use plain English, no jargon (no "refactor", "PR", "merge", "commit", "chore", "deps")
- Start each bullet with a verb: "Added", "Fixed", "Improved", "You can now"
- Keep each bullet to one sentence
- Only output the bullet points, no headers, no extra text

Changes:
{changes}
"""

SECTION_ORDER = ["New Features", "Bug Fixes", "Improvements", "Documentation"]


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


def classify_pr(title: str) -> tuple[str | None, str]:
    lower = title.lower()
    for skip in SKIP_PREFIXES:
        if lower.startswith(skip):
            return None, title
    for prefix, category in TYPE_MAP.items():
        if lower.startswith(f"{prefix}:") or lower.startswith(f"{prefix}("):
            clean = title.split(":", 1)[-1].strip()
            return category, clean[0].upper() + clean[1:] if clean else clean
    return "Improvements", title


def group_prs(prs: list[dict]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for pr in prs:
        category, clean_title = classify_pr(pr["title"])
        if category is None:
            continue
        groups.setdefault(category, []).append(clean_title)
    return groups


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
    lines = [f"## [{version}] - {date}\n"]
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


def prepend_to_changelog(entries: list[str], path: str):
    existing = ""
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()

    header = "# Changelog\n\n"
    body = existing[len(header):] if existing.startswith(header) else existing

    with open(path, "w") as f:
        f.write(header + "\n".join(entries) + "\n" + body)

    print(f"Written to {path}")


def main():
    parser = argparse.ArgumentParser(description="Generate a user-friendly changelog via Ollama")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--version", help="Version label (default: auto-detected from latest release tag)")
    parser.add_argument("--output", default="CHANGELOG.md")
    parser.add_argument("--print-only", action="store_true")
    parser.add_argument("--backfill", action="store_true", help="Generate entries for all historical releases")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Warning: GITHUB_TOKEN not set — using unauthenticated API (60 req/hr limit).", file=sys.stderr)

    releases = fetch_all_releases(args.owner, args.repo, token)

    if not releases:
        print("No releases found. Create a release on GitHub first.")
        return

    if args.backfill:
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

            print(f"\n[{version}] {date}")
            prs = fetch_merged_prs(args.owner, args.repo, since_date, until_date, token)
            print(f"  {len(prs)} merged PRs")

            if not prs:
                continue

            groups = group_prs(prs)
            total = sum(len(v) for v in groups.values())
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

        print(f"Generating changelog for {version} ({date})")
        prs = fetch_merged_prs(args.owner, args.repo, since_date, None, token)
        print(f"Found {len(prs)} merged PRs since previous release.")

        if not prs:
            print("Nothing to changelog.")
            return

        groups = group_prs(prs)
        total = sum(len(v) for v in groups.values())
        print(f"{total} entries to rewrite across {len(groups)} sections.")

        entry = build_entry(groups, args.model, version, date)

        if args.print_only:
            print("\n" + entry)
        else:
            prepend_to_changelog([entry], args.output)


if __name__ == "__main__":
    main()
