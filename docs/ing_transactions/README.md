# ing_transactions

Parses ING bank CSV exports into a single deduplicated, enriched dataset —
built as a first step toward feeding this data into an AppSheet-backed
Google Sheet for personal transaction tracking/tagging (not wired up yet;
see "Not yet implemented" below).

## What it does

ING's exports pack several structured sub-fields into a single
`Mededelingen` ("notifications") text column, formatted differently
depending on transaction type. Repeated exports (taken at different times)
also have overlapping date ranges, so the same transaction can appear more
than once, sometimes with small formatting differences between exports.

`src/ing_transactions/extract_mededelingen.py` handles all of this in one
pipeline:

1. **Load** every `.csv` in `data/` (ING's own `;`-separated format).
2. **Parse `Datum`** from ING's `YYYYMMDD` int into a real date, and
   **validate** the rest of the schema matches what the rest of the
   pipeline expects (catches ING silently changing its export format).
3. **Parse `Mededelingen`** into `med_*` columns (card/transaction
   reference, IBAN, sender name, etc.) plus boolean flags (`Apple Pay`,
   `Doorlopende incasso`, `Afronding`).
4. **Deduplicate** rows that represent the same real transaction re-exported
   across overlapping files — matching on date, amount, direction, and
   either the parsed transaction reference or (as a fallback) the
   payee/sender detail, never the raw account-name column alone, since ING
   reformats and genericizes it in ways that would otherwise either miss
   real duplicates or merge distinct transactions. See the module
   docstring and `deduplicate_records` for the full reasoning; this went
   through several rounds of fixes after individually verifying flagged
   duplicate groups against the real data.
5. **Add analysis columns** (`add_analysis_columns`): numeric `amount`/
   `signed_amount`/`balance_after`, calendar breakdowns (`year`, `month`,
   `year_month`, `quarter`, `weekday_name`, `is_weekend`), a coarse
   `transaction_type` classification (see `classify_transaction`), and a
   couple of best-known-value columns (`counterparty_detail`,
   `iban_counterparty`) pulled out of the `med_*` fields.

Each surviving row gets a `row_hash` — a stable id derived from its
identity columns, intended as the `id_field` for a future
`GoogleSheetsClient.upsert_records` call (see `docs/common/README.md`) so
re-running this against a fresh export only adds genuinely new
transactions instead of re-appending everything.

## Running it

```bash
uv run python -m src.ing_transactions.extract_mededelingen [data_dir] [output_path]
```

Defaults: `data_dir` = `data/`, `output_path` = `extracted_transactions.xlsx`.

## Data model

Output is one row per transaction, with columns in three groups:

- **Raw ING columns**: `Datum`, `Naam / Omschrijving`, `Rekening`,
  `Tegenrekening`, `Code`, `Af Bij`, `Bedrag (EUR)`, `Mutatiesoort`,
  `Mededelingen`, `Saldo na mutatie`, `Tag`, plus `source_file` (which
  export the surviving row came from) and `row_hash`.
- **`med_*` columns**: the sub-fields parsed out of `Mededelingen` — see
  `LABELS` and `FLAGS` in `extract_mededelingen.py` for the full list.
- **Analysis columns**: `amount`, `signed_amount`, `balance_after`, `year`,
  `month`, `year_month`, `quarter`, `weekday_name`, `is_weekend`,
  `transaction_type`, `is_refund`, `is_foreign_currency`,
  `counterparty_detail`, `iban_counterparty`.

## Not yet implemented

Writing this into a Google Sheet (for AppSheet to read as application data,
with room for manually-added tags per transaction) is the planned next
step, following the `sync_missing_rows.py` / `upsert_records` pattern
described in `docs/example_app/README.md` and `docs/common/README.md` —
keyed on `row_hash` so reruns only add new transactions. Not built yet:
needs a real spreadsheet + service-account credentials configured first.
