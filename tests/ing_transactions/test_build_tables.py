import pandas as pd

from src.ing_transactions.build_tables import (
    RAW_CSV_COLUMNS,
    _stable_hash,
    _stable_id,
    build_accounts_table,
    build_companies_table,
    build_modified_csv_data_table,
    build_original_csv_data_table,
    build_transaction_foreign_currency_table,
    build_transactions_table,
    classify_entity_type,
)


def _raw_csv_row(**overrides):
    row = {
        "Datum": pd.Timestamp("2024-04-13"),
        "Naam / Omschrijving": "Mw W Huizinga",
        "Rekening": "NL52INGB0003610006",
        "Tegenrekening": "NL10INGB0688427367",
        "Code": "OV",
        "Af Bij": "Af",
        "Bedrag (EUR)": "13,49",
        "Mutatiesoort": "Overschrijving",
        "Mededelingen": "Naam: Mw W Huizinga Omschrijving: Sokken Valutadatum: 13-04-2024",
        "Saldo na mutatie": "4582,46",
        "Tag": None,
        "source_file": "export.csv",
    }
    row.update(overrides)
    return row


def _transactions_df(**overrides):
    row = {
        "row_hash": "abc123",
        "amount": 13.49,
        "company": "Albert Heijn",
        "iban_counterparty": "NL13ABNA0506417344",
        "transaction_type": "Card payment",
        "payment_processor": "BCK",
        "counterparty_detail": None,
        "is_refund": False,
        "is_foreign_currency": False,
        "is_subscription": False,
        "is_roundup_savings": False,
        "has_unknown_counterparty": False,
        "foreign_amount": None,
        "foreign_currency": None,
        "exchange_rate": None,
        "currency_markup_fee": None,
        **_raw_csv_row(),
        "balance_after": 4582.46,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_stable_hash_is_deterministic_across_calls():
    assert _stable_hash("company", "Albert Heijn") == _stable_hash("company", "Albert Heijn")


def test_stable_hash_differs_by_entity_type_for_the_same_key():
    # A currency code and a payment processor name that happen to match as
    # plain strings must not collide on the same hash.
    assert _stable_hash("currency", "NL") != _stable_hash("payment_processor", "NL")


def test_stable_id_is_deterministic_from_a_hash():
    row_hash = _stable_hash("company", "Albert Heijn")
    assert _stable_id(row_hash) == _stable_id(row_hash)


def test_build_companies_table_lists_distinct_companies_with_blank_columns():
    df = pd.DataFrame({"company": ["Albert Heijn", "McDonald's", "Albert Heijn"]})

    result = build_companies_table(df)

    assert sorted(result["company_name"]) == ["Albert Heijn", "McDonald's"]
    assert result["category"].isna().all()
    assert result["notes"].isna().all()
    expected = {n: _stable_hash("company", n) for n in result["company_name"]}
    assert all(
        result.loc[i, "row_hash"] == expected[result.loc[i, "company_name"]] for i in result.index
    )
    assert result["id"].tolist() == [_stable_id(h) for h in result["row_hash"]]
    assert result["id"].is_unique


def test_build_accounts_table_flags_own_account():
    df = _transactions_df()

    result = build_accounts_table(df)

    own = result[result["iban"] == "NL52INGB0003610006"]
    counterparty = result[result["iban"] == "NL13ABNA0506417344"]
    assert own.iloc[0]["is_own_account"]
    assert not counterparty.iloc[0]["is_own_account"]
    assert counterparty.iloc[0]["country_code"] == "NL"
    assert own.iloc[0]["id"] == _stable_id(_stable_hash("account", "NL52INGB0003610006"))


def test_build_original_csv_data_table_collapses_byte_identical_rows_across_files():
    df = pd.DataFrame(
        [
            _raw_csv_row(source_file="old_export.csv"),
            _raw_csv_row(source_file="new_export.csv"),
        ]
    )

    result = build_original_csv_data_table(df)

    assert len(result) == 1
    assert result.loc[0, "source_file"] == "new_export.csv"
    assert result["id"].is_unique


def test_build_original_csv_data_table_keeps_genuinely_different_rows():
    df = pd.DataFrame(
        [
            _raw_csv_row(source_file="export.csv"),
            _raw_csv_row(source_file="export.csv", **{"Bedrag (EUR)": "99,00"}),
        ]
    )

    result = build_original_csv_data_table(df)

    assert len(result) == 2


def test_build_modified_csv_data_table_links_to_matching_original_csv_row_hash():
    raw_df = pd.DataFrame([_raw_csv_row()])
    df = _transactions_df()

    original = build_original_csv_data_table(raw_df)
    modified = build_modified_csv_data_table(df)

    assert modified.loc[0, "original_csv_row_hash"] == original.loc[0, "row_hash"]


def test_build_modified_csv_data_table_maps_direction_and_materializes_bit_columns():
    df = _transactions_df(**{"Af Bij": "Bij", "is_subscription": True})

    result = build_modified_csv_data_table(df)

    assert result.loc[0, "direction"] == "in"
    assert result.loc[0, "is_subscription"] == True


def test_build_modified_csv_data_table_foreign_keys_match_lookup_table_ids():
    df = _transactions_df()

    result = build_modified_csv_data_table(df)

    assert result.loc[0, "id"] == _stable_id("abc123")
    assert result.loc[0, "company_id"] == _stable_id(_stable_hash("company", "Albert Heijn"))
    assert result.loc[0, "counterparty_account_id"] == _stable_id(
        _stable_hash("account", "NL13ABNA0506417344")
    )


def test_build_modified_csv_data_table_leaves_nullable_foreign_keys_none():
    df = _transactions_df(payment_processor=None)

    result = build_modified_csv_data_table(df)

    assert pd.isna(result.loc[0, "payment_processor_id"])


def test_build_transactions_table_links_to_modified_csv_data_and_carries_workflow_fields():
    df = _transactions_df()

    modified = build_modified_csv_data_table(df)
    transactions = build_transactions_table(df)

    assert transactions.loc[0, "modified_csv_data_id"] == modified.loc[0, "id"]
    assert transactions.loc[0, "row_hash"] == modified.loc[0, "row_hash"]
    assert transactions.loc[0, "id"] != modified.loc[0, "id"]
    assert transactions.loc[0, "reviewed"] == False
    assert pd.isna(transactions.loc[0, "notes"])


def test_build_transaction_foreign_currency_table_only_includes_foreign_rows():
    df = pd.concat(
        [
            _transactions_df(
                row_hash="fx1",
                foreign_amount=20.0,
                foreign_currency="PLN",
                exchange_rate=4.2,
                currency_markup_fee=0.5,
            ),
            _transactions_df(row_hash="domestic1"),
        ],
        ignore_index=True,
    )

    result = build_transaction_foreign_currency_table(df)

    assert len(result) == 1
    assert result.loc[0, "modified_csv_data_id"] == _stable_id("fx1")
    assert result.loc[0, "currency_id"] == _stable_id(_stable_hash("currency", "PLN"))


def test_raw_csv_columns_matches_expected_dtypes_keys():
    from src.ing_transactions.extract_mededelingen import EXPECTED_DTYPES

    assert RAW_CSV_COLUMNS == list(EXPECTED_DTYPES)


def test_classify_entity_type_recognizes_dutch_titles():
    assert classify_entity_type("Hr AM van Beemdelust") == "Person"
    assert classify_entity_type("Mw W Huizinga") == "Person"


def test_classify_entity_type_recognizes_initials_plus_surname():
    assert classify_entity_type("S. Dijkstra") == "Person"
    assert classify_entity_type("F. Melchers") == "Person"
    assert classify_entity_type("D Mozes") == "Person"


def test_classify_entity_type_recognizes_lowercase_initial_names():
    assert classify_entity_type("s.koot") == "Person"
    assert classify_entity_type("e tijdeman") == "Person"


def test_classify_entity_type_recognizes_dutch_name_particles():
    assert classify_entity_type("arjan van beemdelust") == "Person"
    assert classify_entity_type("ms vd werf") == "Person"
    assert classify_entity_type("G.G.A. van Es") == "Person"


def test_classify_entity_type_does_not_flag_firma_as_person():
    # "fa " = Dutch abbreviation for "firma" - a business, despite fitting
    # the initials-plus-surname shape.
    assert classify_entity_type("fa hoogervorst") == "Company"


def test_classify_entity_type_does_not_flag_web_domains_as_person():
    assert classify_entity_type("bol.com") == "Company"
    assert classify_entity_type("jakdojade.pl Poznan") == "Company"


def test_classify_entity_type_defaults_unclear_names_to_company():
    assert classify_entity_type("Albert Heijn") == "Company"
    assert classify_entity_type("watersportverbond") == "Company"
    assert classify_entity_type("dppdynamic") == "Company"


def test_build_companies_table_includes_entity_type_guess():
    df = pd.DataFrame({"company": ["Albert Heijn", "Hr AM van Beemdelust"]})

    result = build_companies_table(df)

    by_name = result.set_index("company_name")["entity_type"]
    assert by_name["Albert Heijn"] == "Company"
    assert by_name["Hr AM van Beemdelust"] == "Person"


def test_classify_entity_type_override_wins_over_heuristic():
    from src.ing_transactions.build_tables import ENTITY_TYPE_OVERRIDES

    name = "Van Uffelen Mode UTRECHT"
    assert name in ENTITY_TYPE_OVERRIDES
    assert classify_entity_type(name) == ENTITY_TYPE_OVERRIDES[name] == "Company"
