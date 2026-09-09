# Security Policy

This repository contains automation that connects to Google Sheets and
other services on behalf of AppSheet applications. Treat any credentials
used by that automation (service account JSON keys, OAuth client
secrets, API keys, tokens) as sensitive.

## Handling credentials

- Never commit credentials, API keys, or tokens to this repository.
  Files matching common credential patterns are excluded via
  [`.gitignore`](.gitignore) — do not remove those entries.
- Store secrets used by GitHub Actions in the repository's
  [Actions secrets](../../settings/secrets/actions), not in workflow
  files or code.
- If a credential is ever committed by mistake, treat it as compromised:
  revoke/rotate it at the provider (Google Cloud, AppSheet, etc.)
  immediately, in addition to removing it from git history.

## Reporting a vulnerability

This is a personal repository. If you find a security issue (e.g. an
exposed secret, an insecure automation, a dependency vulnerability),
please open a private report via
[GitHub Security Advisories](../../security/advisories/new) for this
repository, or contact the owner directly, rather than opening a public
issue.
