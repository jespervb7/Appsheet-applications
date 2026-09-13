"""Template: append only rows missing from the sheet.

This script pattern reads existing data from a sheet, compares it against
some external source by an id column, and appends only the rows that
aren't already present. Fork this into a real `src/<app-name>/` script by
replacing `get_source_data()` with a real data source and adjusting the
worksheet/spreadsheet configuration below.

Contrast with `sync_auto_append.py`, which always appends without
comparing against existing data.
"""

from __future__ import annotations

import os
from typing import Any

from src.common.google_sheets import GoogleSheetsClient

WORKSHEET_NAME = "Sheet1"
ID_FIELD = "id"
SPREADSHEET_ID_ENV_VAR = "EXAMPLE_APP_SPREADSHEET_ID"


def get_source_data() -> list[dict[str, Any]]:
    """Replace this with your real data source (an API call, a CSV, etc.)."""
    return []


def compute_missing_rows(
    source: list[dict[str, Any]],
    existing: list[dict[str, Any]],
    id_field: str,
) -> list[dict[str, Any]]:
    """Return the rows in `source` whose id isn't already in `existing`.

    Pure function, no gspread dependency — easy to unit test in isolation.
    """
    existing_ids = {row.get(id_field) for row in existing}
    return [row for row in source if row.get(id_field) not in existing_ids]


def main() -> None:
    spreadsheet_id = os.environ[SPREADSHEET_ID_ENV_VAR]
    client = GoogleSheetsClient(spreadsheet_id=spreadsheet_id)

    source = get_source_data()
    existing = client.get_records(WORKSHEET_NAME)
    missing = compute_missing_rows(source, existing, ID_FIELD)

    appended = client.append_rows(WORKSHEET_NAME, missing)
    print(f"Appended {appended} missing row(s) to {WORKSHEET_NAME!r}.")


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass  # python-dotenv not installed; fall back to real env vars (e.g. in CI)

    main()
