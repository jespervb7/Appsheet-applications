"""Writeback safety tests for GoogleSheetsClient.

Unlike test_google_sheets.py (which mocks gspread and asserts on *how* the
client called the API), these tests back the worksheet with a real
in-memory row list (FakeWorksheet) and assert on actual sheet *state*
after a write. This catches bugs that call-assertions alone would miss —
e.g. a change that accidentally clears the sheet, drops unrelated rows, or
overwrites more than the intended row.

FakeWorksheet also has no clear()/delete_rows()/delete_row()/resize()/
update() support — each raises AssertionError — so any code path that
tries a destructive or whole-sheet-overwrite operation fails loudly.
"""

import re
from unittest.mock import MagicMock

import pytest

from src.common.google_sheets import GoogleSheetsClient, GoogleSheetsError

WORKSHEET = "Sheet1"


class FakeWorksheet:
    """Minimal in-memory stand-in for gspread.Worksheet."""

    def __init__(self, header, rows):
        self._header = list(header)
        self._data_rows = [list(row) for row in rows]

    # -- read side, mirroring the gspread calls GoogleSheetsClient makes --

    def row_values(self, row_number):
        if row_number == 1:
            return list(self._header)
        return list(self._data_rows[row_number - 2])

    def get_all_records(self):
        return [dict(zip(self._header, row)) for row in self._data_rows]

    # -- write side --

    def append_rows(self, values, value_input_option=None):
        self._data_rows.extend(list(row) for row in values)

    def batch_update(self, data, value_input_option=None):
        for entry in data:
            match = re.match(r"A(\d+):", entry["range"])
            row_number = int(match.group(1))
            self._data_rows[row_number - 2] = list(entry["values"][0])

    # -- destructive / whole-sheet operations the client must never use --

    def clear(self, *args, **kwargs):
        raise AssertionError("append_rows/upsert_records must never clear the worksheet")

    def delete_rows(self, *args, **kwargs):
        raise AssertionError("append_rows/upsert_records must never delete rows")

    def delete_row(self, *args, **kwargs):
        raise AssertionError("append_rows/upsert_records must never delete a row")

    def resize(self, *args, **kwargs):
        raise AssertionError("append_rows/upsert_records must never resize the worksheet")

    def update(self, *args, **kwargs):
        raise AssertionError(
            "append_rows/upsert_records must never do a full-range update/overwrite"
        )

    @property
    def all_rows(self):
        """Header + data rows, as they currently stand."""
        return [list(self._header)] + [list(row) for row in self._data_rows]


@pytest.fixture
def make_client_with_fake_sheet(monkeypatch):
    def _make(header, rows):
        fake_ws = FakeWorksheet(header, rows)
        mock_spreadsheet = MagicMock()
        mock_spreadsheet.worksheet.return_value = fake_ws
        mock_gclient = MagicMock()
        mock_gclient.open_by_key.return_value = mock_spreadsheet

        monkeypatch.setattr(
            "src.common.google_sheets.gspread.authorize", lambda creds: mock_gclient
        )
        monkeypatch.setattr(
            "src.common.google_sheets.Credentials.from_service_account_info",
            lambda info, scopes: MagicMock(),
        )

        client = GoogleSheetsClient("fake-spreadsheet-id", credentials={})
        return client, fake_ws

    return _make


# --- append_rows: only ever grows the sheet ---------------------------------


def test_append_rows_leaves_existing_rows_untouched(make_client_with_fake_sheet):
    header = ["id", "name"]
    client, fake_ws = make_client_with_fake_sheet(header, [["1", "Ada"], ["2", "Bob"]])

    client.append_rows(WORKSHEET, [{"id": "3", "name": "Cleo"}])

    assert fake_ws.all_rows == [
        ["id", "name"],
        ["1", "Ada"],
        ["2", "Bob"],
        ["3", "Cleo"],
    ]


def test_append_rows_only_grows_row_count(make_client_with_fake_sheet):
    header = ["id", "name"]
    client, fake_ws = make_client_with_fake_sheet(header, [["1", "Ada"]])
    rows_before = len(fake_ws.all_rows)

    client.append_rows(WORKSHEET, [{"id": "2", "name": "Bob"}])
    client.append_rows(WORKSHEET, [{"id": "3", "name": "Cleo"}])

    assert len(fake_ws.all_rows) == rows_before + 2


def test_append_rows_empty_input_leaves_sheet_unchanged(make_client_with_fake_sheet):
    header = ["id", "name"]
    existing = [["1", "Ada"], ["2", "Bob"]]
    client, fake_ws = make_client_with_fake_sheet(header, existing)

    client.append_rows(WORKSHEET, [])

    assert fake_ws.all_rows == [["id", "name"], ["1", "Ada"], ["2", "Bob"]]


def test_append_rows_never_calls_destructive_methods(make_client_with_fake_sheet):
    client, _fake_ws = make_client_with_fake_sheet(["id"], [["1"], ["2"]])
    # FakeWorksheet raises AssertionError if clear/delete_rows/resize/update
    # are called; simply not raising here proves none of them were invoked.
    client.append_rows(WORKSHEET, [{"id": "3"}])


# --- upsert_records: updates matched rows in place, appends the rest -------


def test_upsert_records_updates_matched_row_leaves_others_untouched(make_client_with_fake_sheet):
    header = ["id", "name"]
    existing = [["1", "Ada"], ["2", "Bob"], ["3", "Cleo"]]
    client, fake_ws = make_client_with_fake_sheet(header, existing)

    client.upsert_records(WORKSHEET, [{"id": "2", "name": "Bobby"}], id_field="id")

    assert fake_ws.all_rows == [
        ["id", "name"],
        ["1", "Ada"],
        ["2", "Bobby"],
        ["3", "Cleo"],
    ]


def test_upsert_records_appends_new_rows_without_losing_existing_ones(make_client_with_fake_sheet):
    header = ["id", "name"]
    existing = [["1", "Ada"], ["2", "Bob"]]
    client, fake_ws = make_client_with_fake_sheet(header, existing)

    client.upsert_records(
        WORKSHEET,
        [{"id": "2", "name": "Bobby"}, {"id": "3", "name": "Cleo"}],
        id_field="id",
    )

    assert fake_ws.all_rows == [
        ["id", "name"],
        ["1", "Ada"],
        ["2", "Bobby"],
        ["3", "Cleo"],
    ]


def test_upsert_records_row_count_never_decreases(make_client_with_fake_sheet):
    header = ["id", "name"]
    existing = [["1", "Ada"], ["2", "Bob"], ["3", "Cleo"]]
    client, fake_ws = make_client_with_fake_sheet(header, existing)
    rows_before = len(fake_ws.all_rows)

    client.upsert_records(
        WORKSHEET,
        [{"id": "1", "name": "Updated"}, {"id": "4", "name": "New"}],
        id_field="id",
    )

    # One pure update (no growth) + one genuinely new row (+1) — never a drop.
    assert len(fake_ws.all_rows) == rows_before + 1


def test_upsert_records_only_updates_are_a_pure_no_growth_operation(make_client_with_fake_sheet):
    header = ["id", "name"]
    existing = [["1", "Ada"], ["2", "Bob"]]
    client, fake_ws = make_client_with_fake_sheet(header, existing)
    rows_before = len(fake_ws.all_rows)

    client.upsert_records(
        WORKSHEET,
        [{"id": "1", "name": "New Ada"}, {"id": "2", "name": "New Bob"}],
        id_field="id",
    )

    assert len(fake_ws.all_rows) == rows_before
    assert fake_ws.all_rows == [["id", "name"], ["1", "New Ada"], ["2", "New Bob"]]


def test_upsert_records_duplicate_id_in_sheet_raises_before_any_write(make_client_with_fake_sheet):
    header = ["id", "name"]
    existing = [["1", "Ada"], ["1", "Duplicate"]]
    client, fake_ws = make_client_with_fake_sheet(header, existing)

    with pytest.raises(GoogleSheetsError):
        client.upsert_records(WORKSHEET, [{"id": "1", "name": "New"}], id_field="id")

    # Refuses to guess which row to update — sheet must be left untouched.
    assert fake_ws.all_rows == [["id", "name"], ["1", "Ada"], ["1", "Duplicate"]]


def test_upsert_records_never_calls_destructive_methods(make_client_with_fake_sheet):
    client, _fake_ws = make_client_with_fake_sheet(["id"], [["1"], ["2"]])
    client.upsert_records(WORKSHEET, [{"id": "1"}, {"id": "3"}], id_field="id")
