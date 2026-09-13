# example_app (template, not a real app)

`src/example_app/` is a non-functional template — there is no real
spreadsheet or workflow trigger wired up. It exists to show the two script
patterns described in [`docs/common/README.md`](../common/README.md),
built on the shared `GoogleSheetsClient`, as a copy-paste starting point
for a real `src/<app-name>/` automation.

## Patterns

- **`sync_missing_rows.py`** — reads existing sheet data, compares it
  against `get_source_data()` by `ID_FIELD`, and appends only the rows not
  already present. The comparison itself (`compute_missing_rows`) is a
  pure function with no gspread dependency, so it's unit-tested directly
  (see `tests/example_app/test_sync_missing_rows.py`) without mocking the
  Sheets API.
- **`sync_auto_append.py`** — skips the comparison step entirely: fetches
  new data via `get_new_data()` and appends it unconditionally on every
  run. Use this shape for append-only logs where duplicate-avoidance isn't
  needed or is handled upstream.

Neither script currently pulls from a real data source —
`get_source_data()` / `get_new_data()` return an empty list and are
clearly marked for replacement.

## Running a script locally

Scripts import from the shared `src.common` package, so run
`uv sync --extra dev` once per clone (see `CONTRIBUTING.md`) so `src`
resolves as an installed package. After that you can run a script either
way:

```bash
uv run python -m src.example_app.sync_missing_rows
# or, e.g. via your editor's Run/Debug button (with .venv selected as the
# interpreter):
python src/example_app/sync_missing_rows.py
```

Without the editable install, running the file directly fails with
`ModuleNotFoundError: No module named 'src'`.

### Setting local env vars via `.env`

Both scripts read `EXAMPLE_APP_SPREADSHEET_ID` and (via `GoogleSheetsClient`)
`GOOGLE_SERVICE_ACCOUNT_JSON` from the environment. A gitignored `.env` file
at the repo root (already created, with dummy placeholder values) is loaded
automatically at script startup if `python-dotenv` is installed:

```bash
uv sync --extra dev
```

Edit `.env` and replace the dummy values with a real spreadsheet ID and
service-account JSON to actually run against a sheet (setup steps in
[`docs/common/README.md`](../common/README.md)). Without `python-dotenv`
installed, the scripts fall back to whatever's already in your shell
environment (e.g. real env vars in CI) — `.env` is never required, only a
local convenience.

## Forking this into a real app

1. Copy the script(s) you need into a new `src/<app-name>/` directory,
   and copy this doc into `docs/<app-name>/`.
2. Replace `get_source_data()` / `get_new_data()` with your real source.
3. Set `WORKSHEET_NAME` and `ID_FIELD` (if used) to match your sheet.
4. Add a service-account credentials secret and a spreadsheet ID variable
   for the new app (see [`docs/common/README.md`](../common/README.md)).
5. Copy `.github/workflows/example_app_sync.yml` to a workflow named for
   your app, update the `python -m src.<app-name>.<script>` command and
   env var names, and enable a `schedule` trigger once it's been tested
   via manual `workflow_dispatch` runs.

## Configuration used by this template

| Env var | Where it comes from | Purpose |
| --- | --- | --- |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | GitHub Actions secret | Service-account credentials (see `docs/common/README.md`) |
| `EXAMPLE_APP_SPREADSHEET_ID` | GitHub Actions repository variable | Target spreadsheet ID (not sensitive) |
