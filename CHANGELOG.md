# Changelog

## [2026-05-14]

### New Features

- Added automatic detection of repository owner and name from the git remote
- You can now use a date to label repositories without releases
- Improved fallback to commits when no pull requests are found
- Added support for backfill mode to fill in missing data
- Made using a token optional, with auto-detection of versions

### Bug Fixes

- Added logic to replace existing changelog entry
- Fixed issue that caused duplicates on repeated runs

### Improvements

- Added changelog generator using Ollama
- Improved skipping of documentation commits
- Fixed prompt quality for better results

