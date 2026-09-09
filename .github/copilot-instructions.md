# Copilot instructions

## What this repo is

Personal repository for AppSheet application development: documentation
about existing AppSheet apps, Python automation that reads/writes their
underlying Google Sheets, and GitHub Actions that run that automation.

## Layout

- `docs/<app-name>/` — one folder per AppSheet app: what it does, data
  model, automations.
- `src/<app-name>/` — Python code automating that app (Google Sheets
  reads/writes, etc.).
- `tests/` — pytest tests, mirroring the `src/` layout.
- `.github/workflows/` — GitHub Actions that trigger the `src/`
  automation (schedule, push, manual dispatch).

Keep an app's docs, code, and workflow together conceptually — when
automation behavior changes for an app, update `docs/<app-name>/` too.

## Conventions

- Format/lint Python with `ruff` and `black` (see `pyproject.toml` and
  `.pre-commit-config.yaml`); keep line length at 100.
- Pin dependencies in `pyproject.toml`.

## Credentials

Automation here authenticates to Google Sheets (and possibly the
AppSheet API). Service account JSON keys, OAuth tokens, and API keys
must never be committed — `.gitignore` already excludes common
credential filenames/patterns. In workflows, read secrets from GitHub
Actions repository secrets, not from files in the repo.
