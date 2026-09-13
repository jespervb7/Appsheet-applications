"""Template: always append new data, no comparison against existing rows.

Unlike `sync_missing_rows.py`, this pattern doesn't read or diff existing
sheet data first — it simply fetches new data and appends it every run.
Suitable for sheets that act as an append-only log (e.g. event/audit
trails), where duplicate-avoidance either doesn't matter or is handled
upstream. Fork this into a real `src/<app-name>/` script by replacing
`get_new_data()` with a real data source.
"""

from __future__ import annotations

import pandas as pd
import os
from typing import Any

from src.common.google_sheets import GoogleSheetsClient

WORKSHEET_NAME = "Sheet1"
SPREADSHEET_ID_ENV_VAR = "EXAMPLE_APP_SPREADSHEET_ID"


def get_new_data() -> list[dict[str, Any]]:
    """Replace this with your real data source (an API call, a CSV, etc.)."""
    return [{"ID2": 2, "ID3": "test"}]


def main() -> None:
    spreadsheet_id = os.environ[SPREADSHEET_ID_ENV_VAR]
    client = GoogleSheetsClient(spreadsheet_id=spreadsheet_id)

    new_rows = get_new_data()
    appended = client.append_rows(WORKSHEET_NAME, new_rows)
    print(f"Appended {appended} row(s) to {WORKSHEET_NAME!r}.")

if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass  # python-dotenv not installed; fall back to real env vars (e.g. in CI)

    main()
