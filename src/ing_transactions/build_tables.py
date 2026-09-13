"""Split the enriched `ing_transactions` output into a normalized (3NF)
table structure - one worksheet per table - meant to back a real
multi-table AppSheet application rather than one wide analysis sheet.

Three tables form a raw -> processed -> business lineage chain:

- `OriginalCsvData` - every raw CSV row exactly as loaded, before any
  parsing, deduplication, or enrichment (see `build_original_csv_data_table`).
- `ModifiedCsvData` - one row per surviving (post-dedup, enriched)
  transaction, referencing the `OriginalCsvData` row it came from (see
  `build_modified_csv_data_table`).
- `Transactions` - a thin, app-facing table referencing `ModifiedCsvData`,
  carrying only the fields a user manages directly (`reviewed`, `notes`).

Every table has two identity columns:

- `row_hash` - a plain SHA-256-based hash of the row's natural key (a
  company name, an IBAN, a full raw CSV row, ...), computed by `hash_row`.
  This is the intended matching key for a future `upsert_records` call -
  the same real-world entity always hashes to the same value.
- `id` - a UUID5 deterministically derived *from* `row_hash` (see
  `_stable_id`), used as the actual primary/foreign key value elsewhere in
  the sheet (an AppSheet `Ref` column stores this, not the raw hash).

Both are deterministic, not random: this pipeline is a stateless script
that rebuilds its output from the source CSVs on every run, so a random id
would change every time and orphan any manual data (a `category` filled in
on a company, a `reviewed` flag) already entered in AppSheet against the
previous run's ids.

Boolean flags that could be derived from other columns (`is_refund`,
`is_foreign_currency`, ...) are materialized directly on `ModifiedCsvData`
here rather than left as AppSheet virtual columns, by request - simpler to
work with in the sheet itself, at the cost of needing to stay in sync with
the columns they're derived from if that logic ever changes.
"""

from __future__ import annotations

import re
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from src.ing_transactions.extract_mededelingen import (
    EXPECTED_DTYPES,
    ROW_HASH_COLUMN,
    SOURCE_FILE_COLUMN,
    add_analysis_columns,
    deduplicate_records,
    extract_iban_country,
    extract_mededelingen,
    hash_row,
    load_csv_files,
    parse_datum,
    validate_dtypes,
)

# The raw CSV column holding the account the export is *for* - every value
# in this dataset is the same IBAN, but the table is built generically in
# case a future export ever covers more than one own account.
OWN_ACCOUNT_COLUMN = "Rekening"

# The raw ING columns that make up one `OriginalCsvData` row - every column
# `validate_dtypes` checks, i.e. everything ING itself put in the CSV,
# excluding `SOURCE_FILE_COLUMN` (added by `load_csv_files`, not from ING).
RAW_CSV_COLUMNS = list(EXPECTED_DTYPES)

# Arbitrary but fixed - only needs to never change once real ids have been
# generated from it, so every table's ids stay stable across reruns.
_UUID_NAMESPACE = uuid.UUID("355a5767-635f-41bd-bf45-f2269b8e0ef2")


def _stable_hash(entity_type: str, natural_key: str) -> str:
    """Deterministic `row_hash` for one (`entity_type`, `natural_key`) pair.

    `entity_type` namespaces the key so two different tables' natural keys
    that happen to be the same string (a currency code and a payment
    processor name, say) never collide on the same hash.
    """
    return hash_row(pd.Series({"entity_type": entity_type, "natural_key": natural_key}))


def _stable_id(row_hash: str) -> str:
    """Deterministic UUID derived from a `row_hash` - a table's own primary
    key, and the value other tables' foreign keys reference it by.
    """
    return str(uuid.uuid5(_UUID_NAMESPACE, row_hash))


def _lookup_hash_and_id(entity_type: str, natural_key: str) -> tuple[str, str]:
    """`(row_hash, id)` for one lookup-table row - see `_stable_hash`/`_stable_id`."""
    row_hash = _stable_hash(entity_type, natural_key)
    return row_hash, _stable_id(row_hash)


def _lookup_id_column(entity_type: str, values: pd.Series) -> pd.Series:
    """Foreign-key column of ids, computed the same way the referenced
    lookup table derives its own ids from the same natural keys - no join
    needed, since the id for a given natural key is always the same
    wherever it's computed.
    """
    return values.apply(lambda v: _stable_id(_stable_hash(entity_type, v)) if pd.notna(v) else None)


def _raw_csv_row_hash(df: pd.DataFrame) -> pd.Series:
    """`row_hash` for each row's `RAW_CSV_COLUMNS`, tagged so it can't
    collide with a lookup table's hash even if the content coincidentally
    matched - shared by `build_original_csv_data_table` (hashing raw rows
    directly) and `build_modified_csv_data_table` (hashing the same columns
    carried through unchanged on the enriched dataframe), so the same
    physical row always gets the same hash in both places.
    """
    hash_basis = df[RAW_CSV_COLUMNS].copy()
    hash_basis.insert(0, "entity_type", "original_csv_row")
    return hash_basis.apply(hash_row, axis=1)


def build_original_csv_data_table(raw_df: pd.DataFrame) -> pd.DataFrame:
    """One row per distinct *raw* CSV row, exactly as loaded - before any
    parsing, deduplication, or enrichment.

    Two rows with byte-identical content across the two overlapping exports
    collapse to a single record here (there's no reason to store the same
    real data point twice just because it appears in both files); keeps
    whichever file it was read from last, consistent with `deduplicate_records`.
    """
    row_hash = _raw_csv_row_hash(raw_df)
    unique = raw_df.assign(**{ROW_HASH_COLUMN: row_hash}).drop_duplicates(
        subset=ROW_HASH_COLUMN, keep="last"
    )

    result = unique[RAW_CSV_COLUMNS + [SOURCE_FILE_COLUMN]].reset_index(drop=True)
    result.insert(0, ROW_HASH_COLUMN, unique[ROW_HASH_COLUMN].values)
    result.insert(0, "id", result[ROW_HASH_COLUMN].apply(_stable_id))
    return result


def build_modified_csv_data_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per surviving (post-dedup, enriched) transaction - the
    processed content, referencing the `OriginalCsvData` row it came from
    and the lookup tables below by id.
    """
    return pd.DataFrame(
        {
            "id": df[ROW_HASH_COLUMN].apply(_stable_id),
            ROW_HASH_COLUMN: df[ROW_HASH_COLUMN],
            "original_csv_row_hash": _raw_csv_row_hash(df),
            "transaction_date": df["Datum"],
            "amount": df["amount"],
            "direction": df["Af Bij"].map({"Af": "out", "Bij": "in"}),
            "balance_after": df["balance_after"],
            "company_id": _lookup_id_column("company", df["company"]),
            "counterparty_account_id": _lookup_id_column("account", df["iban_counterparty"]),
            "transaction_type_id": _lookup_id_column("transaction_type", df["transaction_type"]),
            "payment_processor_id": _lookup_id_column("payment_processor", df["payment_processor"]),
            "source_file_id": _lookup_id_column("source_file", df[SOURCE_FILE_COLUMN]),
            "mutatiesoort": df["Mutatiesoort"],
            "description_raw": df["Mededelingen"],
            "counterparty_detail": df["counterparty_detail"],
            "is_refund": df["is_refund"],
            "is_foreign_currency": df["is_foreign_currency"],
            "is_subscription": df["is_subscription"],
            "is_roundup_savings": df["is_roundup_savings"],
            "has_unknown_counterparty": df["has_unknown_counterparty"],
        }
    )


# Dutch personal-title prefixes - a confident signal `name` is a person,
# not a business.
_PERSON_TITLE_RE = re.compile(r"^(Hr|Dhr|Mw|Mevr|Fam)\.?\s", re.IGNORECASE)
# "S. Dijkstra", "G.G.A. van Es", "s.koot" (1-4 dotted initials followed by
# a capitalized surname).
_PERSON_INITIALS_RE = re.compile(r"^([A-Za-z]{1,4}\.){1,4}\s?[A-Z][a-z]+")
# "D Mozes", "J Tiggeloven" (a single bare initial, no dot, plus a surname).
_PERSON_SINGLE_INITIAL_RE = re.compile(r"^[A-Z]\.?\s[A-Z][a-z]+$")
# "e tijdeman", "t.prins" - lower-case initial(+dot) plus a surname. Loose
# enough to also match a run-on lowercase business name split awkwardly
# (see `_COMPANY_DOMAIN_RE`/`_NON_PERSON_PREFIXES` for the exclusions that
# claws back the false positives this causes, e.g. "bol.com", "fa hoogervorst").
_PERSON_LOWERCASE_INITIAL_RE = re.compile(r"^[a-z]{1,4}[.\s]+[a-z]+$")
# Dutch surname "tussenvoegsel" (prefix particles) specific enough to be a
# reliable person signal on their own. Deliberately excludes "de"/"der"/
# "den"/"eo" - those are also ordinary Dutch words ("de" = "the") that show
# up plenty in company and place names ("WERELDKEUKEN DE BRAAK", "Kano
# verenigin Den Haag"), so they caused real false positives here.
_DUTCH_NAME_PARTICLES = {"van", "vd"}
# Excludes web-style domains ("bol.com", "jakdojade.pl") from ever being
# classified as a person, even though some coincidentally fit the
# initials-plus-surname shape.
_COMPANY_DOMAIN_RE = re.compile(r"\.(com|nl|pl|org|co\.uk)\b", re.IGNORECASE)
# "fa " = Dutch abbreviation for "firma" (a sole-proprietorship business
# name, e.g. "fa hoogervorst") - shaped like a personal name but isn't one.
_NON_PERSON_PREFIXES = ("fa ",)
# An explicit legal-entity suffix is a definitive company signal, checked
# ahead of everything else - e.g. "Van Dijk Educatie BV" would otherwise
# match the "van" particle rule despite plainly being a business.
_COMPANY_ENTITY_SUFFIX_RE = re.compile(
    r"\b(B\.?V\.?|N\.?V\.?|V\.?O\.?F\.?|GmbH|Ltd\.?|LLC|S\.A\.|"
    r"St(ichting)?\.?|Vereniging|Verenigin\w*)\b",
    re.IGNORECASE,
)


# Explicit overrides for `Companies.entity_type`, keyed by exact
# `company_name`, checked *before* `classify_entity_type`'s heuristic guess.
# This is the maintained source of truth: since the whole `Companies` table
# is rebuilt from scratch on every pipeline run, a correction made by
# editing the exported sheet directly would just get silently overwritten
# on the next run - add it here instead, permanently, and it always wins.
# Seeded from a full manual pass over the heuristic's first output: mostly
# businesses/institutions trading under a personal-sounding founder's name
# ("Hotel Van der Valk", "Van Gogh Museum" - structurally identical to
# "Albert Heijn", no heuristic can tell these apart from a real person's
# name) plus a couple of address/account labels that happen to contain
# "van" as part of a place name, not a surname. Add more as you spot them.
ENTITY_TYPE_OVERRIDES: dict[str, str] = {
    "Van Uffelen Mode UTRECHT": "Company",
    "Bouwbedrijf van Grunsven": "Company",
    # "t.h.o.d.n" = "tevens handelend onder de naam" (also trading under
    # the name) - a sole proprietor's business name, not a personal
    # payment to the owner.
    "E. Boz t.h.o.d.n Doner LEIDEN": "Company",
    "CCVSellier  Van der DELFT": "Company",
    "Chin. Rest. Jade UITHOORN": "Company",
    "Geldmaat GM Simon van Noorden": "Company",
    "Hotel Van der Valk BREDA": "Company",
    "ING>LAAN VAN MEERWIJK 1   005019": "Company",
    "Rest Kop vd Haven IJMUIDEN": "Company",
    "U.R.K.V. Michiel de Ruyt": "Company",
    "U.R.K.V. Michiel de Ruyter": "Company",
    "Van Bonusrenterekening 0003610006": "Company",
    "Van Gogh Museum via MultiSafepay": "Company",
    "Van Maanen Verkoop UITHOORN": "Company",
}


def classify_entity_type(name: str) -> str:
    """Best-effort guess at whether `name` refers to a person or a company.

    Not authoritative - `ENTITY_TYPE_OVERRIDES` is the actual source of
    truth for anything it lists; this is only the fallback guess for
    everything else, and it will get some wrong. A plain "Firstname
    Lastname" (e.g. "Chantal Mozes") is structurally indistinguishable from
    a business name without a real name database, so this only catches the
    patterns that are actually distinctive: Dutch personal titles,
    initials-plus-surname shapes, and names built around a surname
    particle. Everything else defaults to "Company", the overwhelming
    majority in this data.
    """
    if name in ENTITY_TYPE_OVERRIDES:
        return ENTITY_TYPE_OVERRIDES[name]

    if (
        name.lower().startswith(_NON_PERSON_PREFIXES)
        or _COMPANY_DOMAIN_RE.search(name)
        or _COMPANY_ENTITY_SUFFIX_RE.search(name)
    ):
        return "Company"

    if _PERSON_TITLE_RE.match(name):
        return "Person"

    if (
        _PERSON_INITIALS_RE.match(name)
        or _PERSON_SINGLE_INITIAL_RE.match(name)
        or _PERSON_LOWERCASE_INITIAL_RE.match(name)
    ):
        return "Person"

    words = re.findall(r"[a-z]+", name.lower())
    if len(words) <= 6 and any(w in _DUTCH_NAME_PARTICLES for w in words):
        return "Person"

    return "Company"


def build_companies_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per distinct `company`, plus blank columns for manual use.

    `entity_type` is pre-filled with `classify_entity_type`'s best-effort
    guess - review it, it will get some wrong (see that function's docstring).
    """
    names = sorted(df["company"].dropna().unique())
    hashes, ids = (
        zip(*(_lookup_hash_and_id("company", name) for name in names))
        if names
        else (
            (),
            (),
        )
    )
    return pd.DataFrame(
        {
            "id": ids,
            ROW_HASH_COLUMN: hashes,
            "company_name": names,
            "entity_type": [classify_entity_type(name) for name in names],
            "category": None,
            "notes": None,
        }
    )


def build_payment_processors_table(df: pd.DataFrame) -> pd.DataFrame:
    names = sorted(df["payment_processor"].dropna().unique())
    hashes, ids = (
        zip(*(_lookup_hash_and_id("payment_processor", name) for name in names))
        if names
        else ((), ())
    )
    return pd.DataFrame(
        {
            "id": ids,
            ROW_HASH_COLUMN: hashes,
            "processor_name": names,
            "notes": None,
        }
    )


def build_transaction_types_table(df: pd.DataFrame) -> pd.DataFrame:
    names = sorted(df["transaction_type"].dropna().unique())
    hashes, ids = (
        zip(*(_lookup_hash_and_id("transaction_type", name) for name in names))
        if names
        else ((), ())
    )
    return pd.DataFrame(
        {
            "id": ids,
            ROW_HASH_COLUMN: hashes,
            "type_name": names,
            "notes": None,
        }
    )


def build_accounts_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per distinct IBAN seen as either the own account or a counterparty."""
    own_ibans = set(df[OWN_ACCOUNT_COLUMN].dropna().unique())
    counterparty_ibans = set(df["iban_counterparty"].dropna().unique())
    all_ibans = sorted(own_ibans | counterparty_ibans)
    hashes, ids = (
        zip(*(_lookup_hash_and_id("account", iban) for iban in all_ibans))
        if all_ibans
        else ((), ())
    )

    return pd.DataFrame(
        {
            "id": ids,
            ROW_HASH_COLUMN: hashes,
            "iban": all_ibans,
            "country_code": [extract_iban_country(iban) for iban in all_ibans],
            "is_own_account": [iban in own_ibans for iban in all_ibans],
            "label": None,
            "notes": None,
        }
    )


def build_source_files_table(df: pd.DataFrame) -> pd.DataFrame:
    names = sorted(df[SOURCE_FILE_COLUMN].dropna().unique())
    hashes, ids = (
        zip(*(_lookup_hash_and_id("source_file", name) for name in names)) if names else ((), ())
    )
    return pd.DataFrame(
        {
            "id": ids,
            ROW_HASH_COLUMN: hashes,
            "filename": names,
            "notes": None,
        }
    )


def build_currencies_table(df: pd.DataFrame) -> pd.DataFrame:
    codes = sorted(df["foreign_currency"].dropna().unique())
    hashes, ids = (
        zip(*(_lookup_hash_and_id("currency", code) for code in codes)) if codes else ((), ())
    )
    return pd.DataFrame(
        {
            "id": ids,
            ROW_HASH_COLUMN: hashes,
            "currency_code": codes,
            "currency_name": None,
            "notes": None,
        }
    )


def build_transactions_table(df: pd.DataFrame) -> pd.DataFrame:
    """The thin, app-facing business table - one row per transaction,
    linking to `ModifiedCsvData` for the actual processed content and
    carrying only the fields a user manages directly in the app.

    `row_hash` is the same value as the linked `ModifiedCsvData` row's -
    both represent the identity of the same real transaction - but `id` is
    namespaced separately so the two tables never share a literal id value.
    """
    modified_csv_data_id = df[ROW_HASH_COLUMN].apply(_stable_id)
    own_hash = df[ROW_HASH_COLUMN].apply(lambda h: _stable_hash("transaction", h))
    return pd.DataFrame(
        {
            "id": own_hash.apply(_stable_id),
            ROW_HASH_COLUMN: df[ROW_HASH_COLUMN],
            "modified_csv_data_id": modified_csv_data_id,
            "reviewed": False,
            "notes": None,
        }
    )


def build_transaction_foreign_currency_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per transaction with a foreign-currency component - 1:1 with `ModifiedCsvData`."""
    foreign = df[df["foreign_amount"].notna()]
    modified_csv_data_id = foreign[ROW_HASH_COLUMN].apply(_stable_id)
    own_hash = foreign[ROW_HASH_COLUMN].apply(
        lambda h: _stable_hash("transaction_foreign_currency", h)
    )
    return pd.DataFrame(
        {
            "id": own_hash.apply(_stable_id),
            ROW_HASH_COLUMN: foreign[ROW_HASH_COLUMN],
            "modified_csv_data_id": modified_csv_data_id,
            "foreign_amount": foreign["foreign_amount"],
            "currency_id": _lookup_id_column("currency", foreign["foreign_currency"]),
            "exchange_rate": foreign["exchange_rate"],
            "markup_fee": foreign["currency_markup_fee"],
        }
    )


# Builders that only need the enriched/deduped pipeline output.
TABLE_BUILDERS: dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {
    "Companies": build_companies_table,
    "PaymentProcessors": build_payment_processors_table,
    "TransactionTypes": build_transaction_types_table,
    "Accounts": build_accounts_table,
    "SourceFiles": build_source_files_table,
    "Currencies": build_currencies_table,
    "ModifiedCsvData": build_modified_csv_data_table,
    "Transactions": build_transactions_table,
    "TransactionForeignCurrency": build_transaction_foreign_currency_table,
}


def build_all_tables(raw_df: pd.DataFrame, df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Return every normalized table.

    `raw_df` is the loaded-and-dated-but-otherwise-untouched combined CSV
    data (used only for `OriginalCsvData`); `df` is the fully enriched and
    deduplicated pipeline output (used for everything else).
    """
    tables = {"OriginalCsvData": build_original_csv_data_table(raw_df)}
    tables.update({name: builder(df) for name, builder in TABLE_BUILDERS.items()})
    return tables


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("normalized_tables.xlsx")

    combined = load_csv_files(data_dir)
    combined = parse_datum(combined)
    validate_dtypes(combined)
    enriched = extract_mededelingen(combined)
    deduped = deduplicate_records(enriched)
    result = add_analysis_columns(deduped)

    tables = build_all_tables(combined, result)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        for name, table in tables.items():
            table.to_excel(writer, sheet_name=name, index=False)

    summary = ", ".join(f"{name}={len(table)}" for name, table in tables.items())
    print(f"Wrote {len(tables)} table(s) to {output_path}: {summary}")


if __name__ == "__main__":
    main()
