from src.example_app.sync_missing_rows import compute_missing_rows


def test_compute_missing_rows_returns_only_unseen_ids():
    source = [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]
    existing = [{"id": "1", "name": "a"}]

    missing = compute_missing_rows(source, existing, id_field="id")

    assert missing == [{"id": "2", "name": "b"}]


def test_compute_missing_rows_empty_existing_returns_all_source_rows():
    source = [{"id": "1"}, {"id": "2"}]

    assert compute_missing_rows(source, [], id_field="id") == source


def test_compute_missing_rows_no_source_returns_empty():
    assert compute_missing_rows([], [{"id": "1"}], id_field="id") == []


def test_compute_missing_rows_all_present_returns_empty():
    source = [{"id": "1"}, {"id": "2"}]
    existing = [{"id": "1"}, {"id": "2"}, {"id": "3"}]

    assert compute_missing_rows(source, existing, id_field="id") == []
