#!/usr/bin/env python3
"""
Changelog generator — fetches merged PRs since last release, rewrites them
in user-friendly language via a local Ollama model, and outputs a changelog entry.

Usage:
    python generate.py --owner TuurKeersebilck --repo TimeManagement
    python generate.py --owner TuurKeersebilck --repo TimeManagement --model gemma2:9b --since v0.1.0
"""

import argparse
import json
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
    "chore": None,  # skip
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


def github_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def get_last_release_date(owner: str, repo: str, since_tag: str | None, token: str) -> datetime:
    if since_tag:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/releases/tags/{since_tag}"
        r = requests.get(url, headers=github_headers(token))
        r.raise_for_status()
        return datetime.fromisoformat(r.json()["published_at"].replace("Z", "+00:00"))

    url = f"{GITHUB_API}/repos/{owner}/{repo}/releases/latest"
    r = requests.get(url, headers=github_headers(token))
    if r.status_code == 404:
        print("No releases found — fetching all merged PRs.")
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
    r.raise_for_status()
    release = r.json()
    print(f"Last release: {release['tag_name']} ({release['published_at']})")
    return datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))


def fetch_merged_prs(owner: str, repo: str, since: datetime, token: str) -> list[dict]:
    prs = []
    page = 1
    while True:
        url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls"
        r = requests.get(url, headers=github_headers(token), params={
            "state": "closed",
            "base": "main",
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
            "page": page,
        })
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
            prs.append(pr)
        page += 1
    return prs


def classify_pr(title: str) -> tuple[str | None, str]:
    """Returns (category, clean_title). category is None if the PR should be skipped."""
    lower = title.lower()
    for skip in SKIP_PREFIXES:
        if lower.startswith(skip):
            return None, title

    for prefix, category in TYPE_MAP.items():
        if lower.startswith(f"{prefix}:") or lower.startswith(f"{prefix}("):
            clean = title.split(":", 1)[-1].strip()
            clean = clean[0].upper() + clean[1:] if clean else clean
            return category, clean

    # No known prefix — include as improvement
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
    payload = {"model": model, "prompt": prompt, "stream": False}

    try:
        r = requests.post(OLLAMA_API, json=payload, timeout=120)
        r.raise_for_status()
        response_text = r.json().get("response", "").strip()
        lines = [l.strip().lstrip("-• ").strip() for l in response_text.splitlines() if l.strip()]
        return [f"- {l}" for l in lines if l]
    except requests.exceptions.ConnectionError:
        print("Warning: Ollama not reachable at localhost:11434 — using raw titles.", file=sys.stderr)
        return [f"- {t}" for t in titles]


def build_changelog(groups: dict[str, list[str]], model: str, version: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"## [{version}] - {today}\n"]

    section_order = ["New Features", "Bug Fixes", "Improvements", "Documentation"]
    for section in section_order:
        titles = groups.get(section)
        if not titles:
            continue
        print(f"Rewriting {len(titles)} entries for '{section}'...")
        bullets = rewrite_with_ollama(titles, model)
        lines.append(f"### {section}\n")
        lines.extend(bullets)
        lines.append("")

    return "\n".join(lines)


def prepend_to_changelog(entry: str, path: str):
    existing = ""
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()

    header = "# Changelog\n\n"
    if existing.startswith(header):
        content = existing[len(header):]
    else:
        content = existing

    with open(path, "w") as f:
        f.write(header + entry + "\n" + content)

    print(f"Written to {path}")


def main():
    parser = argparse.ArgumentParser(description="Generate a user-friendly changelog via Ollama")
    parser.add_argument("--owner", required=True, help="GitHub repo owner")
    parser.add_argument("--repo", required=True, help="GitHub repo name")
    parser.add_argument("--model", default="qwen2.5:7b", help="Ollama model to use")
    parser.add_argument("--since", help="Tag to use as baseline (default: latest release)")
    parser.add_argument("--version", default="Unreleased", help="Version label for this entry")
    parser.add_argument("--output", default="CHANGELOG.md", help="Output file path")
    parser.add_argument("--print-only", action="store_true", help="Print to stdout instead of writing")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Error: GITHUB_TOKEN environment variable not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching PRs for {args.owner}/{args.repo}...")
    since = get_last_release_date(args.owner, args.repo, args.since, token)
    prs = fetch_merged_prs(args.owner, args.repo, since, token)
    print(f"Found {len(prs)} merged PRs since last release.")

    if not prs:
        print("Nothing to changelog.")
        return

    groups = group_prs(prs)
    total = sum(len(v) for v in groups.values())
    print(f"{total} entries to rewrite across {len(groups)} sections.")

    entry = build_changelog(groups, args.model, args.version)

    if args.print_only:
        print("\n" + entry)
    else:
        prepend_to_changelog(entry, args.output)


if __name__ == "__main__":
    main()
