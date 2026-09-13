from unittest.mock import MagicMock

import pytest

from src.common.google_sheets import (
    DEFAULT_CREDENTIALS_ENV_VAR,
    GoogleSheetsClient,
    GoogleSheetsError,
)

WORKSHEET = "Sheet1"


@pytest.fixture
def make_client(monkeypatch):
    """Build a GoogleSheetsClient with gspread/auth mocked out.

    Returns (client, mock_worksheet) so tests can set up return values on
    the worksheet mock and assert on how it was called.
    """

    def _make(credentials=None, credentials_env_var=DEFAULT_CREDENTIALS_ENV_VAR):
        mock_ws = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_spreadsheet.worksheet.return_value = mock_ws
        mock_gclient = MagicMock()
        mock_gclient.open_by_key.return_value = mock_spreadsheet

        monkeypatch.setattr(
            "src.common.google_sheets.gspread.authorize", lambda creds: mock_gclient
        )
        monkeypatch.setattr(
            "src.common.google_sheets.Credentials.from_service_account_info",
            lambda info, scopes: MagicMock(),
        )

        client = GoogleSheetsClient(
            "fake-spreadsheet-id",
            credentials=credentials,
            credentials_env_var=credentials_env_var,
        )
        return client, mock_ws

    return _make


# --- credential resolution -------------------------------------------------


def test_credentials_from_explicit_dict(make_client):
    client, _ = make_client(credentials={"type": "service_account"})
    assert client is not None


def test_credentials_from_explicit_json_string(make_client):
    client, _ = make_client(credentials='{"type": "service_account"}')
    assert client is not None


def test_credentials_from_env_var(make_client, monkeypatch):
    monkeypatch.setenv(DEFAULT_CREDENTIALS_ENV_VAR, '{"type": "service_account"}')
    client, _ = make_client(credentials=None)
    assert client is not None


def test_credentials_missing_raises(make_client, monkeypatch):
    monkeypatch.delenv(DEFAULT_CREDENTIALS_ENV_VAR, raising=False)
    with pytest.raises(GoogleSheetsError):
        make_client(credentials=None)


# --- get_header / get_records -----------------------------------------------


def test_get_header(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.row_values.return_value = ["id", "name"]

    assert client.get_header(WORKSHEET) == ["id", "name"]
    mock_ws.row_values.assert_called_once_with(1)


def test_get_records(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.get_all_records.return_value = [{"id": "1", "name": "a"}]

    assert client.get_records(WORKSHEET) == [{"id": "1", "name": "a"}]


def test_get_dataframe(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.get_all_records.return_value = [
        {"id": "1", "name": "a"},
        {"id": "2", "name": "b"},
    ]

    df = client.get_dataframe(WORKSHEET)

    assert list(df.columns) == ["id", "name"]
    assert df.to_dict("records") == [
        {"id": "1", "name": "a"},
        {"id": "2", "name": "b"},
    ]


def test_get_dataframe_empty_sheet(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.get_all_records.return_value = []

    df = client.get_dataframe(WORKSHEET)

    assert df.empty


# --- append_rows -------------------------------------------------------------


def test_append_rows_empty_input_is_noop(make_client):
    client, mock_ws = make_client(credentials={})

    assert client.append_rows(WORKSHEET, []) == 0
    mock_ws.append_rows.assert_not_called()


def test_append_rows_with_dict_rows_projects_onto_header(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.row_values.return_value = ["id", "name", "email"]

    count = client.append_rows(WORKSHEET, [{"id": "1", "name": "Ada"}])

    assert count == 1
    mock_ws.append_rows.assert_called_once_with(
        [["1", "Ada", ""]], value_input_option="USER_ENTERED"
    )


def test_append_rows_with_dict_rows_drops_extra_keys(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.row_values.return_value = ["id", "name"]

    client.append_rows(WORKSHEET, [{"id": "1", "name": "Ada", "unexpected": "x"}])

    mock_ws.append_rows.assert_called_once_with([["1", "Ada"]], value_input_option="USER_ENTERED")


def test_append_rows_with_list_rows_passes_through(make_client):
    client, mock_ws = make_client(credentials={})

    count = client.append_rows(WORKSHEET, [["1", "Ada"]])

    assert count == 1
    mock_ws.append_rows.assert_called_once_with([["1", "Ada"]], value_input_option="USER_ENTERED")
    mock_ws.row_values.assert_not_called()


def test_append_rows_dict_rows_against_headerless_sheet_raises(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.row_values.return_value = []

    with pytest.raises(GoogleSheetsError):
        client.append_rows(WORKSHEET, [{"id": "1"}])


# --- upsert_records ------------------------------------------------------


def test_upsert_records_splits_update_and_append(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.row_values.return_value = ["id", "name"]
    mock_ws.get_all_records.return_value = [
        {"id": "1", "name": "Old Ada"},
        {"id": "2", "name": "Bob"},
    ]

    result = client.upsert_records(
        WORKSHEET,
        [{"id": "1", "name": "New Ada"}, {"id": "3", "name": "Cleo"}],
        id_field="id",
    )

    assert result.updated == 1
    assert result.appended == 1
    assert result.updated_ids == ["1"]
    assert result.appended_ids == ["3"]

    mock_ws.batch_update.assert_called_once_with(
        [{"range": "A2:B2", "values": [["1", "New Ada"]]}],
        value_input_option="USER_ENTERED",
    )
    mock_ws.append_rows.assert_called_once_with([["3", "Cleo"]], value_input_option="USER_ENTERED")


def test_upsert_records_no_matches_only_appends(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.row_values.return_value = ["id", "name"]
    mock_ws.get_all_records.return_value = []

    result = client.upsert_records(WORKSHEET, [{"id": "1", "name": "Ada"}], id_field="id")

    assert result.updated == 0
    assert result.appended == 1
    mock_ws.batch_update.assert_not_called()


def test_upsert_records_missing_id_field_column_raises(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.row_values.return_value = ["name"]

    with pytest.raises(GoogleSheetsError):
        client.upsert_records(WORKSHEET, [{"id": "1", "name": "Ada"}], id_field="id")


def test_upsert_records_duplicate_id_in_sheet_raises(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.row_values.return_value = ["id", "name"]
    mock_ws.get_all_records.return_value = [
        {"id": "1", "name": "Ada"},
        {"id": "1", "name": "Duplicate"},
    ]

    with pytest.raises(GoogleSheetsError):
        client.upsert_records(WORKSHEET, [{"id": "1", "name": "New"}], id_field="id")


def test_upsert_records_input_record_missing_id_value_raises(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.row_values.return_value = ["id", "name"]
    mock_ws.get_all_records.return_value = []

    with pytest.raises(GoogleSheetsError):
        client.upsert_records(WORKSHEET, [{"name": "Ada"}], id_field="id")


def test_upsert_records_input_duplicate_id_later_record_wins(make_client):
    client, mock_ws = make_client(credentials={})
    mock_ws.row_values.return_value = ["id", "name"]
    mock_ws.get_all_records.return_value = [{"id": "1", "name": "Old"}]

    client.upsert_records(
        WORKSHEET,
        [{"id": "1", "name": "First"}, {"id": "1", "name": "Second"}],
        id_field="id",
    )

    mock_ws.batch_update.assert_called_once_with(
        [
            {"range": "A2:B2", "values": [["1", "First"]]},
            {"range": "A2:B2", "values": [["1", "Second"]]},
        ],
        value_input_option="USER_ENTERED",
    )
