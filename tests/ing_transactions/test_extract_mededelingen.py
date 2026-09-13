from datetime import date

import pandas as pd
import pytest

from src.ing_transactions.extract_mededelingen import (
    add_analysis_columns,
    classify_transaction,
    deduplicate_records,
    extract_iban_country,
    extract_mededelingen,
    extract_payment_processor,
    hash_row,
    load_csv_files,
    parse_datum,
    parse_mededeling,
    resolve_company,
    validate_dtypes,
)


def test_parse_mededeling_card_payment():
    text = (
        "Kaartnr: 5238 53** **** 4035 Datum: 11-09-2026 Tijd: 17:11 "
        "Transactie: I71060 Term: SC001706 Apple Pay Valutadatum: 12-09-2026"
    )

    result = parse_mededeling(text)

    assert result["kaartnr"] == "5238 53** **** 4035"
    assert result["datum"] == "11-09-2026"
    assert result["tijd"] == "17:11"
    assert result["transactie"] == "I71060"
    assert result["term"] == "SC001706"
    assert result["valutadatum"] == "12-09-2026"
    assert result["apple_pay"] is True


def test_parse_mededeling_internal_transfer_captures_leading_text():
    text = "Naar Oranje spaarrekening F56227366 Afronding Valutadatum: 12-09-2026"

    result = parse_mededeling(text)

    assert result["vrije_tekst"] == "Naar Oranje spaarrekening F56227366"
    assert result["afronding"] is True
    assert result["valutadatum"] == "12-09-2026"
    assert result["kaartnr"] is None


def test_parse_mededeling_direct_debit():
    text = (
        "Naam: PayPal Europe S.a.r.l. et Cie S.C.A Omschrijving: 1052869901265/PAYPAL "
        "IBAN: LU89751000135104200E Kenmerk: 1052869901265 Machtiging ID: 4VB2224TDPBY4 "
        "Incassant ID: LU96ZZZ0000000000000000058 Doorlopende incasso Valutadatum: 08-09-2026"
    )

    result = parse_mededeling(text)

    assert result["naam"] == "PayPal Europe S.a.r.l. et Cie S.C.A"
    assert result["omschrijving"] == "1052869901265/PAYPAL"
    assert result["iban"] == "LU89751000135104200E"
    assert result["machtiging_id"] == "4VB2224TDPBY4"
    assert result["incassant_id"] == "LU96ZZZ0000000000000000058"
    assert result["doorlopende_incasso"] is True


def test_parse_mededeling_handles_missing_value():
    result = parse_mededeling(float("nan"))

    assert result["valutadatum"] is None
    assert result["vrije_tekst"] is None
    assert result["apple_pay"] is False


def test_parse_mededeling_foreign_card_payment_with_kosten_label():
    # Regression: "Kosten" wasn't a recognized label, so it used to get
    # swallowed into the end of the "Koers" value instead of its own field.
    text = (
        "Pasvolgnr: 004 24-07-2015 19:27 Transactie: 34P7Y0 Term: HE17684 "
        "Valuta: 40,00 GBP Koers: 0,6959756 Kosten: 2,25 EUR Valutadatum: 27-07-2015"
    )

    result = parse_mededeling(text)

    assert result["koers"] == "0,6959756"
    assert result["kosten"] == "2,25 EUR"


def test_extract_mededelingen_appends_prefixed_columns():
    df = pd.DataFrame({"Mededelingen": ["Term: BS102642 Valutadatum: 09-09-2026"]})

    result = extract_mededelingen(df)

    assert result.loc[0, "med_term"] == "BS102642"
    assert result.loc[0, "med_valutadatum"] == "09-09-2026"
    assert "Mededelingen" in result.columns


def test_hash_row_ignores_column_order():
    row_a = pd.Series({"Datum": "01-01-2026", "Bedrag": "10,00"})
    row_b = pd.Series({"Bedrag": "10,00", "Datum": "01-01-2026"})

    assert hash_row(row_a) == hash_row(row_b)


def test_hash_row_differs_for_different_values():
    row_a = pd.Series({"Datum": "01-01-2026", "Bedrag": "10,00"})
    row_b = pd.Series({"Datum": "02-01-2026", "Bedrag": "10,00"})

    assert hash_row(row_a) != hash_row(row_b)


def test_hash_row_treats_missing_values_consistently():
    row_a = pd.Series({"Datum": "01-01-2026", "Tegenrekening": float("nan")})
    row_b = pd.Series({"Datum": "01-01-2026", "Tegenrekening": None})

    assert hash_row(row_a) == hash_row(row_b)


def test_deduplicate_records_drops_rows_matching_on_identity_columns():
    df = pd.DataFrame(
        {
            "Datum": ["01-01-2026", "01-01-2026", "02-01-2026"],
            "Bedrag": ["10,00", "10,00", "20,00"],
        }
    )

    result = deduplicate_records(df, identity_columns=["Datum", "Bedrag"])

    assert len(result) == 2
    assert result["Datum"].tolist() == ["01-01-2026", "02-01-2026"]


def test_deduplicate_records_keeps_rows_that_differ_in_an_identity_column():
    df = pd.DataFrame(
        {
            "Datum": ["01-01-2026", "01-01-2026"],
            "Bedrag": ["10,00", "20,00"],
        }
    )

    result = deduplicate_records(df, identity_columns=["Datum", "Bedrag"])

    assert len(result) == 2


def test_deduplicate_records_ignores_non_identity_columns():
    df = pd.DataFrame(
        {
            "Datum": ["01-01-2026", "01-01-2026"],
            "Bedrag": ["10,00", "10,00"],
            "Mededelingen": ["short text", "longer text with extra detail"],
        }
    )

    result = deduplicate_records(df, identity_columns=["Datum", "Bedrag"])

    assert len(result) == 1


def test_deduplicate_records_prefers_the_last_row_on_collision():
    df = pd.DataFrame(
        {
            "source_file": ["old_export.csv", "new_export.csv"],
            "Datum": ["21-10-2022", "21-10-2022"],
            "Naam / Omschrijving": ["Thuisbezorgdnl", "Thuisbezorgdnl"],
            "Bedrag (EUR)": ["31,40", "31,40"],
            "Af Bij": ["Af", "Af"],
            "Mededelingen": [
                "Term: --- Valutadatum: 21-10-2022",
                "Term: --- Apple Pay Valutadatum: 21-10-2022",
            ],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 1
    assert result.loc[0, "source_file"] == "new_export.csv"
    assert result.loc[0, "Mededelingen"] == "Term: --- Apple Pay Valutadatum: 21-10-2022"


def test_deduplicate_records_merges_truncated_naam_variant():
    df = pd.DataFrame(
        {
            "source_file": ["old_export.csv", "new_export.csv"],
            "Datum": ["04-10-2016", "04-10-2016"],
            "Naam / Omschrijving": ["CCV*MCDONALD'S BETAALA", "CCV*MCDONALD'S BETAALA NLD"],
            "Bedrag (EUR)": ["9,40", "9,40"],
            "Af Bij": ["Af", "Af"],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 1
    assert result.loc[0, "source_file"] == "new_export.csv"
    assert result.loc[0, "Naam / Omschrijving"] == "CCV*MCDONALD'S BETAALA NLD"


def test_deduplicate_records_does_not_merge_unrelated_short_naam_values():
    df = pd.DataFrame(
        {
            "Datum": ["01-01-2026", "01-01-2026"],
            "Naam / Omschrijving": ["Albert Heijn", "Albina Traiteur"],
            "Bedrag (EUR)": ["9,40", "9,40"],
            "Af Bij": ["Af", "Af"],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 2


def test_deduplicate_records_merges_on_transactie_when_naam_is_mangled():
    # ING truncated the name *and* appended a country code between exports,
    # so the two names no longer share a common prefix - only the parsed
    # `med_transactie`/`med_pasvolgnr` reference code identifies these as
    # the same transaction.
    df = pd.DataFrame(
        {
            "source_file": ["old_export.csv", "new_export.csv"],
            "Datum": ["09-01-2017", "09-01-2017"],
            "Naam / Omschrijving": [
                "McDonalds Erritsoe Fredericia",
                "McDonalds Erritsoe Frederici DNK",
            ],
            "Bedrag (EUR)": ["28,26", "28,26"],
            "Af Bij": ["Af", "Af"],
            "med_pasvolgnr": ["004", "004"],
            "med_transactie": ["A4P8S8", "A4P8S8"],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 1
    assert result.loc[0, "source_file"] == "new_export.csv"


def test_deduplicate_records_keeps_rows_with_different_transactie():
    df = pd.DataFrame(
        {
            "Datum": ["09-01-2017", "09-01-2017"],
            "Naam / Omschrijving": [
                "McDonalds Erritsoe Fredericia",
                "McDonalds Erritsoe Fredericia",
            ],
            "Bedrag (EUR)": ["28,26", "28,26"],
            "Af Bij": ["Af", "Af"],
            "med_pasvolgnr": ["004", "004"],
            "med_transactie": ["A4P8S8", "Z9Q1X2"],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 2


def test_deduplicate_records_falls_back_to_naam_when_transactie_is_missing():
    df = pd.DataFrame(
        {
            "source_file": ["old_export.csv", "new_export.csv"],
            "Datum": ["21-10-2022", "21-10-2022"],
            "Naam / Omschrijving": ["Oranje Spaarrekening", "Oranje Spaarrekening"],
            "Bedrag (EUR)": ["2000,00", "2000,00"],
            "Af Bij": ["Bij", "Bij"],
            "med_pasvolgnr": [None, None],
            "med_transactie": [None, None],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 1
    assert result.loc[0, "source_file"] == "new_export.csv"


def test_deduplicate_records_keeps_distinct_incoming_tikkie_payments():
    # ING labels every incoming Tikkie payment with the same generic
    # `Naam / Omschrijving` ("AAB INZ TIKKIE"), so two different senders
    # paying the same amount on the same day would otherwise collide.
    # `med_omschrijving` carries the actual Tikkie ID/sender and must be
    # used to tell them apart.
    df = pd.DataFrame(
        {
            "Datum": ["26-06-2023", "26-06-2023"],
            "Naam / Omschrijving": ["AAB INZ TIKKIE", "AAB INZ TIKKIE"],
            "Bedrag (EUR)": ["15,40", "15,40"],
            "Af Bij": ["Bij", "Bij"],
            "med_transactie": [None, None],
            "med_pasvolgnr": [None, None],
            "med_naam": ["AAB INZ TIKKIE", "AAB INZ TIKKIE"],
            "med_omschrijving": [
                "Tikkie ID 000670738011, Escape room zaterdag , Van J.M. van den Berg",
                "Tikkie ID 000670738544, Escape room zaterdag , Van M.J.M. Lotgerink",
            ],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 2


def test_deduplicate_records_merges_genuinely_repeated_incoming_transfer():
    df = pd.DataFrame(
        {
            "source_file": ["old_export.csv", "new_export.csv"],
            "Datum": ["16-09-2022", "16-09-2022"],
            "Naam / Omschrijving": ["Tikkie", "Tikkie"],
            "Bedrag (EUR)": ["10,00", "10,00"],
            "Af Bij": ["Bij", "Bij"],
            "med_transactie": [None, None],
            "med_pasvolgnr": [None, None],
            "med_naam": ["Tikkie", "Tikkie"],
            "med_omschrijving": [
                "000519867699 0030011760180112 Dijkstra NL65ABNA0418281335 Tikkie",
                "000519867699 0030011760180112 Dijkstra NL65ABNA0418281335 Tikkie",
            ],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 1
    assert result.loc[0, "source_file"] == "new_export.csv"


def test_deduplicate_records_keeps_a_refund_separate_from_its_original_charge():
    # ING can reuse the same `med_transactie` reference code for a card
    # payment's refund/reversal ("Retourpintransactie"), recorded as a
    # separate ledger line in the opposite `Af Bij` direction - it must not
    # be collapsed into the original charge just because the reference
    # code and amount match.
    df = pd.DataFrame(
        {
            "Datum": ["07-07-2026", "07-07-2026"],
            "Naam / Omschrijving": ["Thuisbezorgd.nl ThuisB Utrecht"] * 2,
            "Bedrag (EUR)": ["8,38", "8,38"],
            "Af Bij": ["Af", "Bij"],
            "med_pasvolgnr": [None, None],
            "med_transactie": ["I03757", "I03757"],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 2


def test_deduplicate_records_keeps_same_file_rows_with_identical_identity():
    # Two genuinely separate real payments (confirmed via the running
    # balance in the real data) that happen to share every identity column,
    # both from the same file. Neither raw ING export ever contains a truly
    # duplicated row within itself, so same-file collisions like this must
    # never be merged - there's no reference code to tell them apart, but
    # merging would silently drop a real transaction.
    df = pd.DataFrame(
        {
            "source_file": ["export.csv", "export.csv"],
            "Datum": ["13-04-2024", "13-04-2024"],
            "Naam / Omschrijving": ["Mw W Huizinga", "Mw W Huizinga"],
            "Bedrag (EUR)": ["13,49", "13,49"],
            "Af Bij": ["Af", "Af"],
            "med_pasvolgnr": [None, None],
            "med_transactie": [None, None],
            "med_naam": ["Mw W Huizinga", "Mw W Huizinga"],
            "med_omschrijving": ["Sokken", "Sokken"],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 2


def test_deduplicate_records_still_merges_across_files_with_identical_identity():
    df = pd.DataFrame(
        {
            "source_file": ["old_export.csv", "new_export.csv"],
            "Datum": ["13-04-2024", "13-04-2024"],
            "Naam / Omschrijving": ["Mw W Huizinga", "Mw W Huizinga"],
            "Bedrag (EUR)": ["13,49", "13,49"],
            "Af Bij": ["Af", "Af"],
            "med_pasvolgnr": [None, None],
            "med_transactie": [None, None],
            "med_naam": ["Mw W Huizinga", "Mw W Huizinga"],
            "med_omschrijving": ["Sokken", "Sokken"],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 1
    assert result.loc[0, "source_file"] == "new_export.csv"


def test_deduplicate_records_keeps_all_rows_from_last_file_when_group_repeats_per_file():
    # Two genuinely separate payments (to F. Melchers for "Ramfeest", same
    # amount, same day) each independently exported once per file, so the
    # group has 2 rows in the old file and 2 in the new one - all 4
    # distinct. Only the old file's pair should be dropped as the new
    # file's re-export of the same set; both of the new file's rows must
    # survive, not just the last one overall.
    df = pd.DataFrame(
        {
            "source_file": [
                "old_export.csv",
                "old_export.csv",
                "new_export.csv",
                "new_export.csv",
            ],
            "Datum": ["17-01-2019"] * 4,
            "Naam / Omschrijving": ["F. Melchers"] * 4,
            "Bedrag (EUR)": ["29,77"] * 4,
            "Af Bij": ["Af"] * 4,
            "med_pasvolgnr": [None] * 4,
            "med_transactie": [None] * 4,
            "med_naam": ["F. Melchers"] * 4,
            "med_omschrijving": ["Ramfeest"] * 4,
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 2
    assert (result["source_file"] == "new_export.csv").all()


def test_load_csv_files_concatenates_all_csv_in_dir(tmp_path):
    pd.DataFrame({"Datum": ["01-01-2026"], "Bedrag": ["10,00"]}).to_csv(
        tmp_path / "a.csv", sep=";", index=False
    )
    pd.DataFrame({"Datum": ["02-01-2026"], "Bedrag": ["20,00"]}).to_csv(
        tmp_path / "b.csv", sep=";", index=False
    )

    result = load_csv_files(tmp_path)

    assert len(result) == 2
    assert sorted(result["Datum"].tolist()) == ["01-01-2026", "02-01-2026"]


def test_load_csv_files_tags_each_row_with_its_source_file(tmp_path):
    pd.DataFrame({"Datum": ["01-01-2026"]}).to_csv(tmp_path / "a.csv", sep=";", index=False)
    pd.DataFrame({"Datum": ["02-01-2026"]}).to_csv(tmp_path / "b.csv", sep=";", index=False)

    result = load_csv_files(tmp_path)

    by_date = result.set_index("Datum")["source_file"]
    assert by_date["01-01-2026"] == "a.csv"
    assert by_date["02-01-2026"] == "b.csv"


def test_load_csv_files_raises_when_dir_has_no_csv(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_csv_files(tmp_path)


def test_deduplicate_records_adds_row_hash_column():
    df = pd.DataFrame({"Datum": ["01-01-2026", "02-01-2026"], "Bedrag": ["10,00", "20,00"]})

    result = deduplicate_records(df, identity_columns=["Datum", "Bedrag"])

    assert result.loc[0, "row_hash"] == hash_row(df.loc[0, ["Datum", "Bedrag"]])
    assert result.loc[1, "row_hash"] == hash_row(df.loc[1, ["Datum", "Bedrag"]])
    assert result.loc[0, "row_hash"] != result.loc[1, "row_hash"]


def test_parse_datum_converts_yyyymmdd_int_to_date():
    df = pd.DataFrame({"Datum": [20221222, 20221221]})

    result = parse_datum(df)

    assert str(result["Datum"].dtype) == "object"
    assert result.loc[0, "Datum"] == date(2022, 12, 22)
    assert result.loc[1, "Datum"] == date(2022, 12, 21)
    assert isinstance(result.loc[0, "Datum"], date)


def test_parse_datum_does_not_mutate_input():
    df = pd.DataFrame({"Datum": [20221222]})

    parse_datum(df)

    assert df.loc[0, "Datum"] == 20221222


def test_validate_dtypes_passes_for_matching_schema():
    df = pd.DataFrame({"Datum": [20260101], "Mededelingen": ["x"]})

    validate_dtypes(df, expected={"Datum": "int64", "Mededelingen": "object"})


def test_validate_dtypes_raises_for_wrong_dtype():
    df = pd.DataFrame({"Datum": ["20260101"]})

    with pytest.raises(TypeError, match="Datum"):
        validate_dtypes(df, expected={"Datum": "int64"})


def test_validate_dtypes_raises_for_missing_column():
    df = pd.DataFrame({"Datum": [20260101]})

    with pytest.raises(TypeError, match="Mededelingen"):
        validate_dtypes(df, expected={"Datum": "int64", "Mededelingen": "object"})


def test_classify_transaction_refund_takes_priority():
    row = pd.Series(
        {
            "Mededelingen": "Term: --- Retourpintransactie Valutadatum: 08-07-2026",
            "Naam / Omschrijving": "Thuisbezorgd.nl ThuisB Utrecht",
            "Mutatiesoort": "Betaalautomaat",
        }
    )

    assert classify_transaction(row) == "Refund"


def test_classify_transaction_internal_transfer():
    row = pd.Series(
        {
            "Mededelingen": "Van Oranje spaarrekening F56227366 Valutadatum: 12-09-2026",
            "Naam / Omschrijving": "Oranje Spaarrekening",
            "Mutatiesoort": "Online bankieren",
        }
    )

    assert classify_transaction(row) == "Internal transfer"


def test_classify_transaction_tikkie_within_bank_transfer_mutatiesoort():
    row = pd.Series(
        {
            "Mededelingen": "Naam: AAB INZ TIKKIE Omschrijving: Tikkie ID 000670738011 Valutadatum: 26-06-2023",
            "Naam / Omschrijving": "AAB INZ TIKKIE",
            "Mutatiesoort": "Overschrijving",
        }
    )

    assert classify_transaction(row) == "Tikkie / payment request"


def test_classify_transaction_plain_bank_transfer():
    row = pd.Series(
        {
            "Mededelingen": "Naam: F. Melchers Omschrijving: Ramfeest Valutadatum: 17-01-2019",
            "Naam / Omschrijving": "F. Melchers",
            "Mutatiesoort": "Overschrijving",
        }
    )

    assert classify_transaction(row) == "Bank transfer"


def test_classify_transaction_uses_mutatiesoort_lookup():
    row = pd.Series(
        {
            "Mededelingen": "Kaartnr: 5238 53** **** 4035 Valutadatum: 09-09-2026",
            "Naam / Omschrijving": "Albert Heijn",
            "Mutatiesoort": "Betaalautomaat",
        }
    )

    assert classify_transaction(row) == "Card payment"


def test_classify_transaction_falls_back_to_other_for_unknown_mutatiesoort():
    row = pd.Series(
        {
            "Mededelingen": float("nan"),
            "Naam / Omschrijving": "Something Unusual",
            "Mutatiesoort": "Some New Category",
        }
    )

    assert classify_transaction(row) == "Other"


def _analysis_input_row(**overrides):
    row = {
        "Datum": pd.Timestamp("2024-04-13"),
        "Naam / Omschrijving": "Mw W Huizinga",
        "Bedrag (EUR)": "13,49",
        "Af Bij": "Af",
        "Saldo na mutatie": "4582,46",
        "Mutatiesoort": "Overschrijving",
        "Mededelingen": "Naam: Mw W Huizinga Omschrijving: Sokken Valutadatum: 13-04-2024",
        "Tegenrekening": "NL10INGB0688427367",
        "med_valuta": None,
        "med_koers": None,
        "med_opslag": None,
        "med_kosten": None,
        "med_omschrijving": "Sokken",
        "med_vrije_tekst": None,
        "med_iban": None,
        "med_doorlopende_incasso": False,
        "med_afronding": False,
    }
    row.update(overrides)
    return row


def test_add_analysis_columns_amount_and_signed_amount():
    df = pd.DataFrame(
        [
            _analysis_input_row(),
            _analysis_input_row(**{"Bedrag (EUR)": "2000,00", "Af Bij": "Bij"}),
        ]
    )

    result = add_analysis_columns(df)

    assert result.loc[0, "amount"] == 13.49
    assert result.loc[0, "signed_amount"] == -13.49
    assert result.loc[1, "amount"] == 2000.00
    assert result.loc[1, "signed_amount"] == 2000.00


def test_add_analysis_columns_balance_after():
    df = pd.DataFrame([_analysis_input_row()])

    result = add_analysis_columns(df)

    assert result.loc[0, "balance_after"] == 4582.46


def test_add_analysis_columns_is_subscription_tracks_doorlopende_incasso():
    df = pd.DataFrame(
        [
            _analysis_input_row(med_doorlopende_incasso=True, Mutatiesoort="Incasso"),
            _analysis_input_row(med_doorlopende_incasso=False, Mutatiesoort="iDEAL"),
        ]
    )

    result = add_analysis_columns(df)

    assert result.loc[0, "is_subscription"] == True
    assert result.loc[1, "is_subscription"] == False


def test_add_analysis_columns_is_roundup_savings():
    df = pd.DataFrame(
        [
            _analysis_input_row(med_afronding=True),
            _analysis_input_row(med_afronding=False),
        ]
    )

    result = add_analysis_columns(df)

    assert result.loc[0, "is_roundup_savings"] == True
    assert result.loc[1, "is_roundup_savings"] == False


def test_add_analysis_columns_has_unknown_counterparty():
    df = pd.DataFrame(
        [
            _analysis_input_row(**{"Naam / Omschrijving": "NOTPROVIDED"}),
            _analysis_input_row(**{"Naam / Omschrijving": "Mw W Huizinga"}),
        ]
    )

    result = add_analysis_columns(df)

    assert result.loc[0, "has_unknown_counterparty"] == True
    assert result.loc[1, "has_unknown_counterparty"] == False


def test_extract_iban_country_returns_country_for_iban_shaped_value():
    assert extract_iban_country("NL13ABNA0506417344") == "NL"
    assert extract_iban_country("DE19210500001001415390") == "DE"


def test_extract_iban_country_returns_none_for_non_iban_value():
    # e.g. a credit card's domestic account number, not an IBAN at all.
    assert extract_iban_country("210032472388") is None
    assert extract_iban_country(None) is None


def test_add_analysis_columns_counterparty_country_ignores_non_iban_values():
    df = pd.DataFrame(
        [
            _analysis_input_row(med_iban="NL13ABNA0506417344", Tegenrekening=None),
            _analysis_input_row(med_iban=None, Tegenrekening="210032472388"),
        ]
    )

    result = add_analysis_columns(df)

    assert result.loc[0, "counterparty_country"] == "NL"
    assert pd.isna(result.loc[1, "counterparty_country"])


def test_add_analysis_columns_foreign_currency_details():
    df = pd.DataFrame(
        [
            _analysis_input_row(med_valuta="20,00 PLN", med_koers="4,2045545", med_opslag=None),
            _analysis_input_row(med_valuta=None, med_koers=None, med_opslag=None),
        ]
    )

    result = add_analysis_columns(df)

    assert result.loc[0, "foreign_amount"] == 20.00
    assert result.loc[0, "foreign_currency"] == "PLN"
    assert result.loc[0, "exchange_rate"] == 4.2045545
    assert pd.isna(result.loc[1, "foreign_amount"])
    assert pd.isna(result.loc[1, "foreign_currency"])


def test_add_analysis_columns_currency_markup_fee_prefers_opslag_then_kosten():
    df = pd.DataFrame(
        [
            _analysis_input_row(med_opslag="0,06 EUR", med_kosten="2,25 EUR"),
            _analysis_input_row(med_opslag=None, med_kosten="2,25 EUR"),
            _analysis_input_row(med_opslag=None, med_kosten=None),
        ]
    )

    result = add_analysis_columns(df)

    assert result.loc[0, "currency_markup_fee"] == 0.06
    assert result.loc[1, "currency_markup_fee"] == 2.25
    assert pd.isna(result.loc[2, "currency_markup_fee"])


def test_add_analysis_columns_counterparty_detail_prefers_omschrijving():
    df = pd.DataFrame(
        [
            _analysis_input_row(med_omschrijving="Sokken", med_vrije_tekst=None),
            _analysis_input_row(med_omschrijving=None, med_vrije_tekst="Van Oranje spaarrekening"),
        ]
    )

    result = add_analysis_columns(df)

    assert result.loc[0, "counterparty_detail"] == "Sokken"
    assert result.loc[1, "counterparty_detail"] == "Van Oranje spaarrekening"


def test_add_analysis_columns_iban_counterparty_prefers_med_iban():
    df = pd.DataFrame(
        [
            _analysis_input_row(med_iban="NL13ABNA0506417344", Tegenrekening="NL10INGB0688427367"),
            _analysis_input_row(med_iban=None, Tegenrekening="NL10INGB0688427367"),
        ]
    )

    result = add_analysis_columns(df)

    assert result.loc[0, "iban_counterparty"] == "NL13ABNA0506417344"
    assert result.loc[1, "iban_counterparty"] == "NL10INGB0688427367"


def test_add_analysis_columns_is_foreign_currency_and_is_refund():
    df = pd.DataFrame(
        [
            _analysis_input_row(med_valuta="20,00 PLN"),
            _analysis_input_row(
                med_valuta=None,
                Mededelingen="Term: --- Retourpintransactie Valutadatum: 08-07-2026",
                Mutatiesoort="Betaalautomaat",
            ),
        ]
    )

    result = add_analysis_columns(df)

    assert result.loc[0, "is_foreign_currency"] == True
    assert result.loc[0, "is_refund"] == False
    assert result.loc[1, "is_foreign_currency"] == False
    assert result.loc[1, "is_refund"] == True


def test_resolve_company_groups_branch_and_prefix_variants():
    variants = [
        "ALBERT HEIJN 1011 AMSTELVEEN NLD",
        "BCK*Albert Heijn UITHOORN NLD",
        "Albert Heijn 1653 LUCHTH SCHIPH",
        "AH togo Adm Bijlm 5833 AMSTERDAM",
        "AlbertHeijn vd Berg UITHOORN NLD",  # no space between "Albert" and "Heijn"
    ]

    assert {resolve_company(v) for v in variants} == {"Albert Heijn"}


def test_resolve_company_groups_mcdonalds_spelling_variants():
    variants = [
        "CCVVOF MCDONALDS AMST AMSTELVEEN",
        "MC DONALDS MERKSEM MERKSEM BEL",  # space between "Mc" and "Donalds"
        "McDonald's Venlo C VENLO NLD",
    ]

    assert {resolve_company(v) for v in variants} == {"McDonald's"}


def test_resolve_company_groups_abn_amro_hyphen_variant():
    assert resolve_company("ABN-AMRO UITHOORN NLD") == "ABN AMRO"
    assert resolve_company("ABN AMRO Bank NV") == "ABN AMRO"


def test_resolve_company_groups_bibliotheken_no_space_variant():
    assert (
        resolve_company("Stichting AmstellandBibliotheken") == "Stichting Amstelland Bibliotheken"
    )
    assert (
        resolve_company("Stichting Amstelland Bibliotheken") == "Stichting Amstelland Bibliotheken"
    )


def test_resolve_company_matches_regardless_of_punctuation():
    variants = [
        "PayPal Europe S.a.r.l. et Cie S.C.A",
        "PayPal (Europe) S.a r.l. et Cie, S.C.A.",
        "PayPal (Europe) S.a.r.l. et Cie., S.C.A.",
    ]

    assert {resolve_company(v) for v in variants} == {"PayPal"}


def test_resolve_company_falls_back_to_cleaned_name_for_unknown_merchant():
    assert resolve_company("SumUp *fa hoogervorst NLD") == "fa hoogervorst"


def test_resolve_company_handles_missing_value():
    assert resolve_company(float("nan")) is None
    assert resolve_company(None) is None


def test_add_analysis_columns_adds_company_column():
    df = pd.DataFrame(
        [_analysis_input_row(**{"Naam / Omschrijving": "BCK*Albert Heijn UITHOORN NLD"})]
    )

    result = add_analysis_columns(df)

    assert result.loc[0, "company"] == "Albert Heijn"


def test_extract_payment_processor_maps_known_prefix_to_brand_name():
    assert extract_payment_processor("BCK*Albert Heijn UITHOORN NLD") == "BCK"
    assert extract_payment_processor("CCV*THE PHONE HOUSE UI UITHOORN") == "CCV"
    assert extract_payment_processor("SumUp *fa hoogervorst NLD") == "SumUp"
    assert extract_payment_processor("Mol*Watersportverbond NLD") == "Mollie"
    assert extract_payment_processor("Zettle*HWC De Helmvaa Helmond") == "Zettle"


def test_extract_payment_processor_returns_raw_token_for_unknown_prefix():
    assert extract_payment_processor("NYX*VendingWork Groningen NLD") == "NYX"


def test_extract_payment_processor_strips_asterisk_when_prefix_has_extra_spacing():
    # Regression: the prefix regex's trailing `\s*` can swallow whitespace
    # *after* the "*" too (e.g. "UBR* PENDING..."), which used to leave the
    # asterisk stuck in the middle of the extracted token ("UBR*") instead
    # of being stripped.
    assert extract_payment_processor("UBR* PENDING.UBER.COM AMSTERDAM") == "Uber"
    assert extract_payment_processor("UBER   * EATS PENDING LONDON GBR") == "Uber"


def test_extract_payment_processor_returns_none_when_no_prefix():
    assert extract_payment_processor("Albert Heijn 1011 AMSTELVEEN NLD") is None
    assert extract_payment_processor(float("nan")) is None


def test_add_analysis_columns_adds_payment_processor_column():
    df = pd.DataFrame(
        [_analysis_input_row(**{"Naam / Omschrijving": "BCK*Albert Heijn UITHOORN NLD"})]
    )

    result = add_analysis_columns(df)

    assert result.loc[0, "payment_processor"] == "BCK"
