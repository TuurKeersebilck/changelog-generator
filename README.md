# changelog-generator

Generates user-friendly changelogs from your GitHub PRs using a local [Ollama](https://ollama.com) model — no tokens spent, no data sent externally.

Instead of parsing raw commit messages, it reads your merged PRs (which are already written in plain language), strips the technical noise, and rewrites them into clean end-user-facing bullets.

> Built with [Claude](https://claude.ai) (Anthropic).

---

## How it works

1. Fetches merged PRs since your last GitHub release
2. Skips dependency bumps and internal chores automatically
3. Groups entries by type: New Features, Bug Fixes, Improvements
4. Sends each group to a local Ollama model to rewrite in plain English
5. Prepends the result to `CHANGELOG.md`

---

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally
- A GitHub token (only needed for private repos — public repos work without one)

---

## Setup

```bash
git clone https://github.com/TuurKeersebilck/changelog-generator
cd changelog-generator
python -m venv .venv

# Mac/Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

Pull a model if you haven't already:

```bash
ollama pull qwen2.5:7b   # recommended for 8GB VRAM
ollama pull gemma2:9b    # slightly better prose quality
```

---

## Usage

```bash
# Preview without writing anything
python generate.py --owner yourname --repo yourrepo --print-only

# Write to CHANGELOG.md in current directory
python generate.py --owner yourname --repo yourrepo --version 1.2.0

# Backfill all historical releases at once
python generate.py --owner yourname --repo yourrepo --backfill
```

### All options

| Flag | Default | Description |
|---|---|---|
| `--owner` | required | GitHub username or org |
| `--repo` | required | Repository name |
| `--model` | `qwen2.5:7b` | Ollama model to use |
| `--version` | auto from release tag | Version label for the entry |
| `--output` | `CHANGELOG.md` | Output file path |
| `--print-only` | false | Print to stdout instead of writing |
| `--backfill` | false | Generate entries for all past releases |

### GitHub token (optional)

Only required for private repos. For public repos the script runs unauthenticated (GitHub allows 60 requests/hour without a token, which is more than enough for a manual run).

```bash
export GITHUB_TOKEN=your_token
```

---

## PR title conventions

The script maps [conventional commit](https://www.conventionalcommits.org) prefixes to changelog sections:

| Prefix | Section |
|---|---|
| `feat:` | New Features |
| `fix:` | Bug Fixes |
| `perf:`, `refactor:` | Improvements |
| `docs:` | Documentation |
| `chore:`, `ci:`, `test:`, `build:` | Skipped |
| `chore(deps):`, dependabot | Skipped |

PRs without a known prefix are included under Improvements.

---

## Recommended models (VRAM guide)

| GPU VRAM | Model | Notes |
|---|---|---|
| 8 GB | `qwen2.5:7b` or `gemma2:9b` | Both produce clean output |
| 12 GB | `qwen2.5:14b` | Noticeable quality jump |
| 24 GB | `qwen2.5:32b` | Near GPT-4 quality for writing |

If Ollama isn't running when you execute the script, it falls back to the raw PR titles without rewriting.
