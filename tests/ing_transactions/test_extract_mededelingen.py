import pandas as pd
import pytest

from src.example_app.extract_mededelingen import (
    deduplicate_records,
    extract_mededelingen,
    hash_row,
    load_csv_files,
    parse_mededeling,
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


def test_deduplicate_records_drops_exact_duplicate_rows():
    df = pd.DataFrame(
        {
            "Datum": ["01-01-2026", "01-01-2026", "02-01-2026"],
            "Bedrag": ["10,00", "10,00", "20,00"],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 2
    assert result["Datum"].tolist() == ["01-01-2026", "02-01-2026"]


def test_deduplicate_records_keeps_rows_that_differ_in_any_column():
    df = pd.DataFrame(
        {
            "Datum": ["01-01-2026", "01-01-2026"],
            "Bedrag": ["10,00", "20,00"],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 2


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


def test_deduplicate_records_ignores_source_file_when_comparing_rows():
    df = pd.DataFrame(
        {
            "source_file": ["a.csv", "b.csv"],
            "Datum": ["01-01-2026", "01-01-2026"],
            "Bedrag": ["10,00", "10,00"],
        }
    )

    result = deduplicate_records(df)

    assert len(result) == 1
    assert result.loc[0, "source_file"] == "a.csv"
