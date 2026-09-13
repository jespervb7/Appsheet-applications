"""Reusable Google Sheets client for AppSheet automation scripts.

Authenticates with a service account and provides generic read/append/
upsert primitives. Individual scripts under ``src/<app-name>/`` import
``GoogleSheetsClient``, pass in their own spreadsheet ID and worksheet
name(s), and layer their own business logic (e.g. deciding which rows are
"missing") on top.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

DEFAULT_CREDENTIALS_ENV_VAR = "GOOGLE_SERVICE_ACCOUNT_JSON"

_VALUE_INPUT_OPTION = "USER_ENTERED"


class GoogleSheetsError(RuntimeError):
    """Raised for client-level failures: bad/missing credentials, a missing
    id column, an ambiguous upsert target, etc."""


@dataclass
class UpsertResult:
    """Summary of an ``upsert_records`` call."""

    updated: int
    appended: int
    updated_ids: list[Any] = field(default_factory=list)
    appended_ids: list[Any] = field(default_factory=list)


class GoogleSheetsClient:
    """Thin wrapper around gspread, parameterized by spreadsheet/worksheet."""

    def __init__(
        self,
        spreadsheet_id: str,
        credentials: str | dict | None = None,
        credentials_env_var: str = DEFAULT_CREDENTIALS_ENV_VAR,
    ) -> None:
        creds_info = self._resolve_credentials(credentials, credentials_env_var)
        creds = Credentials.from_service_account_info(creds_info, scopes=_SCOPES)
        gclient = gspread.authorize(creds)
        self._spreadsheet = gclient.open_by_key(spreadsheet_id)
        self._worksheets: dict[str, gspread.Worksheet] = {}

    @staticmethod
    def _resolve_credentials(credentials: str | dict | None, credentials_env_var: str) -> dict:
        if isinstance(credentials, dict):
            return credentials
        if isinstance(credentials, str):
            return json.loads(credentials)

        raw = os.environ.get(credentials_env_var)
        if not raw:
            raise GoogleSheetsError(
                "No credentials provided and "
                f"{credentials_env_var!r} is not set in the environment."
            )
        return json.loads(raw)

    def worksheet(self, worksheet_name: str) -> gspread.Worksheet:
        """Return the raw gspread worksheet, cached per name.

        Escape hatch for anything not covered by the methods below.
        """
        if worksheet_name not in self._worksheets:
            self._worksheets[worksheet_name] = self._spreadsheet.worksheet(worksheet_name)
        return self._worksheets[worksheet_name]

    def get_header(self, worksheet_name: str) -> list[str]:
        return self.worksheet(worksheet_name).row_values(1)

    def get_records(self, worksheet_name: str) -> list[dict[str, Any]]:
        return self.worksheet(worksheet_name).get_all_records()

    def get_dataframe(self, worksheet_name: str) -> pd.DataFrame:
        """Read the worksheet as a pandas DataFrame, columns from the header row."""
        return pd.DataFrame(self.get_records(worksheet_name))

    def append_rows(
        self,
        worksheet_name: str,
        rows: Iterable[dict[str, Any]] | Iterable[list[Any]],
    ) -> int:
        """Append rows to the end of the sheet.

        Dict rows are projected onto the sheet's current header order
        (missing keys become "", extra keys are dropped). List rows are
        passed through positionally as-is.
        """
        rows = list(rows)
        if not rows:
            return 0

        if isinstance(rows[0], dict):
            header = self.get_header(worksheet_name)
            if not header:
                raise GoogleSheetsError(
                    f"Cannot append dict rows to worksheet {worksheet_name!r}: "
                    "it has no header row."
                )
            values = [[row.get(col, "") for col in header] for row in rows]
        else:
            values = [list(row) for row in rows]

        self.worksheet(worksheet_name).append_rows(values, value_input_option=_VALUE_INPUT_OPTION)
        return len(values)

    def upsert_records(
        self,
        worksheet_name: str,
        records: Iterable[dict[str, Any]],
        id_field: str,
    ) -> UpsertResult:
        """Update existing rows matching ``id_field``, append the rest.

        Raises ``GoogleSheetsError`` if ``id_field`` isn't a column, if the
        sheet has more than one existing row with the same id (ambiguous
        update target), or if an input record has no value for ``id_field``.
        If the *input* contains duplicate ids, the later record wins for
        that row's update.
        """
        records = list(records)
        header = self.get_header(worksheet_name)
        if id_field not in header:
            raise GoogleSheetsError(
                f"id_field {id_field!r} is not a column in worksheet {worksheet_name!r} "
                f"(columns: {header})."
            )

        existing = self.get_records(worksheet_name)
        row_by_id: dict[Any, int] = {}
        for offset, existing_row in enumerate(existing):
            row_id = existing_row.get(id_field)
            row_number = offset + 2  # +1 for header row, +1 for 1-based indexing
            if row_id in row_by_id:
                raise GoogleSheetsError(
                    f"Duplicate id {row_id!r} in column {id_field!r} of worksheet "
                    f"{worksheet_name!r} (rows {row_by_id[row_id]} and {row_number}); "
                    "refusing to guess which row to update."
                )
            row_by_id[row_id] = row_number

        updates: list[dict[str, Any]] = []
        to_append: list[dict[str, Any]] = []
        result = UpsertResult(updated=0, appended=0)

        for record in records:
            if id_field not in record or record[id_field] in (None, ""):
                raise GoogleSheetsError(
                    f"Record is missing a value for id_field {id_field!r}: {record!r}"
                )
            record_id = record[id_field]
            row_number = row_by_id.get(record_id)
            if row_number is None:
                to_append.append(record)
                continue

            values = [record.get(col, "") for col in header]
            last_col = re.sub(r"\d+$", "", gspread.utils.rowcol_to_a1(1, len(header)))
            updates.append(
                {
                    "range": f"A{row_number}:{last_col}{row_number}",
                    "values": [values],
                }
            )
            result.updated_ids.append(record_id)

        if updates:
            self.worksheet(worksheet_name).batch_update(
                updates, value_input_option=_VALUE_INPUT_OPTION
            )
            result.updated = len(updates)

        if to_append:
            result.appended = self.append_rows(worksheet_name, to_append)
            result.appended_ids = [record[id_field] for record in to_append]

        return result
