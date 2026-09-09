# Contributing

This is a personal repository for documenting and automating my AppSheet
applications. It isn't set up to accept outside contributions, but the
notes below capture how work in this repo should be done.

## Repository layout

- `docs/` — documentation about each AppSheet application (data model,
  automations, decisions).
- `src/` — Python code for automation (e.g. reading/writing Google
  Sheets).
- `tests/` — pytest tests, mirroring the `src/` layout.
- `.github/workflows/` — GitHub Actions that run the automation on a
  schedule or in response to events.

## Getting started (Python environment)

Requires Python 3.11+.

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

**macOS/Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Deactivate with `deactivate` in either shell.

Then install the git hooks once per clone:

```bash
pip install pre-commit
pre-commit install
```

## Making changes

- Keep documentation for an application next to the code that automates
  it, and update both together when behavior changes.
- Never commit credentials or secrets — see [SECURITY.md](SECURITY.md)
  and [`.gitignore`](.gitignore).
- Prefer small, focused commits with a clear message describing *why*
  the change was made.

## Python code

- Add new automation scripts under `src/`, with matching tests under
  `tests/`.
- Pin dependencies in `pyproject.toml` as they're introduced.
- Code is formatted/linted with `ruff` and `black` (run automatically
  via `pre-commit` once installed).
- If a script is run by a GitHub Actions workflow, document the trigger
  (schedule, manual, event) in the script's docstring or in `docs/`.
