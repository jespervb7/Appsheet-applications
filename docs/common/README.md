# Shared Google Sheets client

`src/common/google_sheets.py` provides `GoogleSheetsClient`, a small
reusable wrapper around [gspread](https://docs.gspread.org/) for scripts
that read/write AppSheet-backed Google Sheets. It authenticates with a
service account and exposes generic read/append/upsert primitives — the
"what data is missing" or "what changed" logic stays in each script.

## Setup: service account

1. In Google Cloud Console, create (or reuse) a service account and enable
   the Google Sheets API for the project.
2. Create a JSON key for the service account and download it.
3. Open the target spreadsheet in Google Sheets and share it with the
   service account's `client_email` (found in the JSON key), with Editor
   access. No Drive API scope is needed — the client opens spreadsheets by
   ID (`open_by_key`), not by searching Drive.
4. Store the **entire contents** of the JSON key file as a GitHub Actions
   repository secret named `GOOGLE_SERVICE_ACCOUNT_JSON` (or a name of
   your choosing — see below). Never commit the key file; `.gitignore`
   already excludes common credential filenames as a backstop.

## Usage

```python
from src.common.google_sheets import GoogleSheetsClient

client = GoogleSheetsClient(spreadsheet_id="1AbC...xyz")

# Read
header = client.get_header("Orders")
records = client.get_records("Orders")  # list[dict], keyed by header
df = client.get_dataframe("Orders")  # same data as a pandas DataFrame

# Append
client.append_rows("Orders", [{"id": "123", "status": "new"}])

# Update-or-insert by id
result = client.upsert_records(
    "Orders",
    [{"id": "123", "status": "shipped"}],
    id_field="id",
)
print(result.updated, result.appended)
```

Both the spreadsheet ID and worksheet (tab) names are always passed in by
the caller — nothing is hardcoded in the shared client, so one instance
can be reused across multiple tabs in the same spreadsheet.

## Credentials

By default, `GoogleSheetsClient()` reads the service-account JSON from the
`GOOGLE_SERVICE_ACCOUNT_JSON` environment variable. This matches how the
value arrives in a GitHub Actions workflow (from a repo secret). You can
override this:

```python
# Explicit JSON string or dict (e.g. for local testing)
GoogleSheetsClient(spreadsheet_id="...", credentials=my_dict_or_json_string)

# A different env var name, e.g. if multiple apps use different service accounts
GoogleSheetsClient(spreadsheet_id="...", credentials_env_var="ORDERS_APP_CREDENTIALS")
```

Credentials are resolved once, at construction time, and the client opens
the spreadsheet immediately — so a bad ID or missing permissions fail fast
with a clear `GoogleSheetsError`, rather than on first use deep in a script.

## API summary

| Method | Purpose |
| --- | --- |
| `worksheet(name)` | Raw `gspread.Worksheet`, escape hatch for anything not covered below |
| `get_header(name)` | Header row (`list[str]`) |
| `get_records(name)` | All rows as `list[dict]`, keyed by header |
| `get_dataframe(name)` | All rows as a `pandas.DataFrame`, columns from the header |
| `append_rows(name, rows)` | Append dict or list rows to the end |
| `upsert_records(name, records, id_field)` | Update matching rows by id, append the rest |

`append_rows` and `upsert_records` accept dict rows keyed by column name —
missing keys are written as `""`, extra keys are dropped, and both project
onto the sheet's *current* header order, so adding an unrelated column to
the sheet doesn't break either call.

See `src/example_app/` for two full script templates (append-missing-only
and always-append) built on top of this client.
