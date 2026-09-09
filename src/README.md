# Automation code

Python scripts that automate AppSheet-related tasks — primarily reading
from and writing to Google Sheets.

Suggested layout per app/automation:

```
src/
  <app-name>/
    ...           # scripts specific to this app
```

Dependencies go in [`../pyproject.toml`](../pyproject.toml) as they're
introduced. See [`../CONTRIBUTING.md`](../CONTRIBUTING.md) for how to
set up a virtual environment.

Credentials (service account JSON, OAuth tokens) must never be checked
in — see [`../SECURITY.md`](../SECURITY.md) and [`../.gitignore`](../.gitignore).
