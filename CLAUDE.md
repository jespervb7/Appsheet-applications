# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Personal repository for AppSheet application development: documentation
about existing AppSheet apps, Python automation that reads/writes their
underlying Google Sheets, and GitHub Actions that run that automation.
There is no build/test tooling yet — this is an early-stage repo, so
don't assume a package manager, test runner, or CI setup exists until
you see one added.

## Layout

- `docs/<app-name>/` — one folder per AppSheet app: what it does, data
  model, automations.
- `src/<app-name>/` — Python code automating that app (Google Sheets
  reads/writes, etc.). Add dependencies to `pyproject.toml` as they're
  introduced.
- `tests/` — pytest tests, mirroring the `src/` layout.
- `.github/workflows/` — GitHub Actions that trigger the `src/`
  automation (schedule, push, manual dispatch).

Keep an app's docs, code, and workflow together conceptually — when you
change automation behavior for an app, update `docs/<app-name>/` too.

## Tooling

Python is formatted/linted with `ruff` and `black` (config in
`pyproject.toml`, enforced via `.pre-commit-config.yaml`). Run
`pre-commit install` once per clone to enable the git hook.

## Credentials

Automation here authenticates to Google Sheets (and possibly the
AppSheet API). Service account JSON keys, OAuth tokens, and API keys
must never be committed — `.gitignore` already excludes common
credential filenames/patterns. In workflows, read secrets from GitHub
Actions repository secrets, not from files in the repo.
