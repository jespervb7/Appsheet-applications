"""Parse the free-text ``Mededelingen`` column from ING transaction exports.

ING's CSV exports pack several structured sub-fields into a single
``Mededelingen`` ("notifications") text column, formatted differently
depending on the transaction type (card payment, internal transfer, direct
debit, Tikkie request, ...). This module loads every ``.csv`` export found
in a data folder (exports downloaded at different times tend to have
overlapping date ranges, so the same transaction can show up more than
once, occasionally with small differences - see `IDENTITY_COLUMNS`), pulls
the recognizable ``Label: value`` sub-fields out of ``Mededelingen`` into
their own columns (plus a couple of boolean flags for the unlabeled markers
ING adds inline, e.g. "Apple Pay"), and then drops the resulting duplicate
rows - using the parsed transaction reference code where one is available,
since it's more reliable for matching than the free-text fields (see
`_build_naam_key`).
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pandas as pd

LABELS = [
    "Kaartnr",
    "Pasvolgnr",
    "Datum",
    "Tijd",
    "Transactie",
    "Term",
    "RRN",
    "Valuta",
    "Koers",
    "Opslag",
    "Kosten",
    "Naam",
    "Omschrijving",
    "IBAN",
    "Kenmerk",
    "Machtiging ID",
    "Incassant ID",
    "Overige partij",
    "Valutadatum",
]
# Unlike LABELS, these never have a trailing "Label: value" — they're bare
# markers ING drops inline, so they're modeled as booleans instead of text.
FLAGS = ["Apple Pay", "Doorlopende incasso", "Afronding"]

# Column added by `load_csv_files` to record provenance.
SOURCE_FILE_COLUMN = "source_file"
# Column added by `deduplicate_records` holding each row's own `hash_row` value.
ROW_HASH_COLUMN = "row_hash"

NAAM_COLUMN = "Naam / Omschrijving"
# `med_*` columns produced by `extract_mededelingen`, used as a more
# reliable stand-in for `NAAM_COLUMN` when dedup runs after extraction -
# see `_build_naam_key`.
TRANSACTIE_COLUMN = "med_transactie"
PASVOLGNR_COLUMN = "med_pasvolgnr"
OMSCHRIJVING_COLUMN = "med_omschrijving"
MED_NAAM_COLUMN = "med_naam"
VRIJE_TEKST_COLUMN = "med_vrije_tekst"
VALUTA_COLUMN = "med_valuta"
KOERS_COLUMN = "med_koers"
OPSLAG_COLUMN = "med_opslag"
KOSTEN_COLUMN = "med_kosten"
MED_IBAN_COLUMN = "med_iban"
DOORLOPENDE_INCASSO_COLUMN = "med_doorlopende_incasso"
AFRONDING_COLUMN = "med_afronding"

# The exact `NAAM_COLUMN` value ING uses when it has no counterparty name
# for a transaction at all (rather than a generic or truncated one).
UNKNOWN_COUNTERPARTY_NAAM = "NOTPROVIDED"

# Columns added by `add_analysis_columns`.
AMOUNT_COLUMN = "amount"
SIGNED_AMOUNT_COLUMN = "signed_amount"
BALANCE_AFTER_COLUMN = "balance_after"
TRANSACTION_TYPE_COLUMN = "transaction_type"

# The exact `NAAM_COLUMN` value ING uses for a round-trip transfer to/from
# this account's own savings sub-account - not a real external payee, so
# `classify_transaction` special-cases it ahead of `Mutatiesoort`.
INTERNAL_TRANSFER_NAAM = "Oranje Spaarrekening"

# `Mutatiesoort` values that map onto a single `TRANSACTION_TYPE_COLUMN`
# category with no further disambiguation needed - see `classify_transaction`
# for the handful of `Mutatiesoort` values that need extra logic instead.
_MUTATIESOORT_TRANSACTION_TYPES = {
    "Incasso": "Direct debit",
    "Betaalautomaat": "Card payment",
    "Geldautomaat": "ATM withdrawal",
    "Storting": "Cash deposit",
    "Diversen": "Bank fee/interest",
    "Verzamelbetaling": "Collective payment",
    "iDEAL": "iDEAL payment",
    "iDEAL | Wero": "iDEAL payment",
}

# A card terminal/payment-processor code ING prepends to the merchant name
# for some transactions, e.g. "BCK*Albert Heijn UITHOORN NLD" or
# "SumUp *fa hoogervorst NLD" - never part of the merchant's own name, so
# stripped by `_clean_naam` before falling back to it in `resolve_company`.
_POS_PREFIX_RE = re.compile(r"^[^*\s]{1,20}\s*\*\s*")
# Trailing country code ING appends to a card payment abroad. A curated
# whitelist rather than "any trailing uppercase letters", since that also
# matches legal-entity suffixes ("BV", "NV", "LLC", "AB") and names that
# just happen to end in capitals ("KLM", "ING") which aren't country codes.
_COUNTRY_CODE_SUFFIX_RE = re.compile(
    r"\s+(?:NLD|GBR|DEU|BEL|POL|AUT|IRL|HUN|USA|FRA|CHE|CUW|TWN|DNK)$"
)

# (pattern, canonical company name) pairs used by `resolve_company` to group
# `NAAM_COLUMN` variants of the same real-world payee - matched in order,
# first match wins, against the *raw* name (so this still works even when
# `_clean_naam` can't fully strip the noise, e.g. "CCVVOF MCDONALDS AMST
# AMSTELVEEN" has no "*" separator for `_POS_PREFIX_RE` to find, but still
# contains "mcdonald"). Seeded from merchants that actually recur under
# multiple raw spellings in this dataset - add more here as new ones come
# up rather than trying to solve this generically for every merchant.
COMPANY_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"albert[\s-]*heijn", re.IGNORECASE), "Albert Heijn"),
    (re.compile(r"\bah\s*to\s*go\b", re.IGNORECASE), "Albert Heijn"),
    # Confirmed genuine AH store-format names. Deliberately not a bare
    # "\bah\b" match - that would also catch e.g. "AH Jan Linders" (Jan
    # Linders is a separate, unrelated Dutch supermarket chain).
    (re.compile(r"\bah\s*campus\b", re.IGNORECASE), "Albert Heijn"),
    (re.compile(r"\bah\s*station\b", re.IGNORECASE), "Albert Heijn"),
    (re.compile(r"mc\s*donald", re.IGNORECASE), "McDonald's"),
    (re.compile(r"\bkfc\b", re.IGNORECASE), "KFC"),
    (re.compile(r"paypal", re.IGNORECASE), "PayPal"),
    (re.compile(r"netflix", re.IGNORECASE), "Netflix"),
    (re.compile(r"\bkpn\b", re.IGNORECASE), "KPN"),
    (re.compile(r"abn[\s-]*amro", re.IGNORECASE), "ABN AMRO"),
    (re.compile(r"belastingdienst", re.IGNORECASE), "Belastingdienst"),
    (re.compile(r"\banwb\b", re.IGNORECASE), "ANWB"),
    (re.compile(r"zilveren kruis", re.IGNORECASE), "Zilveren Kruis"),
    (re.compile(r"ing hypotheken", re.IGNORECASE), "ING Hypotheken"),
    (re.compile(r"internationale hochschule", re.IGNORECASE), "IU Internationale Hochschule"),
    (re.compile(r"\bachmea\b", re.IGNORECASE), "Achmea"),
    (re.compile(r"thuisbezorgd", re.IGNORECASE), "Thuisbezorgd.nl"),
    (re.compile(r"\bdeen\b", re.IGNORECASE), "DEEN"),
    (re.compile(r"takeaway\.com", re.IGNORECASE), "Takeaway.com"),
    (re.compile(r"amstelland\s*bibliotheken", re.IGNORECASE), "Stichting Amstelland Bibliotheken"),
    (re.compile(r"nationale-?nederlanden", re.IGNORECASE), "Nationale-Nederlanden"),
    (re.compile(r"\bsodexo", re.IGNORECASE), "Sodexo"),
    (re.compile(r"\btikkie\b", re.IGNORECASE), "Tikkie"),
]

# Payment-terminal/gateway prefix token (lowercased) -> display name, for
# the handful `extract_payment_processor` can confidently identify. ING
# doesn't document what the others (e.g. "NYX", "PLV", "SEP") stand for, so
# those are surfaced as their raw token rather than guessed at.
_KNOWN_PROCESSOR_NAMES = {
    "ccv": "CCV",
    "bck": "BCK",
    "sumup": "SumUp",
    "mol": "Mollie",
    "zettle": "Zettle",
    "sq": "Square",
    "ubr": "Uber",
    "uber": "Uber",
}

# Columns that identify "the same transaction" for dedup purposes. Kept
# narrower than "every column" because ING sometimes re-exports the same
# transaction with small text differences elsewhere (e.g. `Mededelingen`
# gaining an "Apple Pay" marker it didn't have in an older export) that
# would otherwise stop it from being recognized as a duplicate. `Af Bij`
# (money in vs out) is included because a card payment and its refund can
# share the same `med_transactie` reference code and amount, and would
# otherwise collide despite being two distinct, opposite-direction entries.
IDENTITY_COLUMNS = ["Datum", NAAM_COLUMN, "Bedrag (EUR)", "Af Bij"]

# Expected dtype for each column of a freshly loaded ING export. Checked by
# `validate_dtypes` to catch schema drift early - e.g. a normally-text
# column like `Tegenrekening` coming back as `float64` because every value
# in one file happened to be empty - before it causes confusing failures or
# silently wrong data further down the pipeline.
EXPECTED_DTYPES: dict[str, str] = {
    # `object` here means a column of real `datetime.date` values, not
    # arbitrary objects - pandas has no dedicated date-only dtype, so a
    # `.dt.date`-truncated column (see `parse_datum`) always reports as
    # `object`, the same as a column of strings would.
    "Datum": "object",
    "Naam / Omschrijving": "object",
    "Rekening": "object",
    "Tegenrekening": "object",
    "Code": "object",
    "Af Bij": "object",
    "Bedrag (EUR)": "object",
    "Mutatiesoort": "object",
    "Mededelingen": "object",
    "Saldo na mutatie": "object",
    "Tag": "object",
}

_LABEL_ALTERNATION = "|".join(re.escape(label) for label in LABELS)
# Non-greedy value capture bounded by a lookahead for the next known label
# (or end of string) - this is what lets one pass find all "Label: value"
# pairs regardless of which labels are actually present in a given row.
_FIELD_RE = re.compile(
    rf"(?P<label>{_LABEL_ALTERNATION}):\s*(?P<value>.*?)(?=(?:{_LABEL_ALTERNATION}):|$)"
)
# Used only to find where the first label starts, so text before it (e.g. a
# transfer's counterparty description) can be captured separately.
_FIRST_LABEL_RE = re.compile(rf"(?:{_LABEL_ALTERNATION}):")


def _column_name(label: str) -> str:
    return label.lower().replace(" ", "_")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_mededeling(text: str | float | None) -> dict[str, str | bool | None]:
    """Break one `Mededelingen` value into its labeled sub-fields and flags.

    Returns a dict with one key per entry in `LABELS` (snake_cased, value or
    None if absent), one boolean key per entry in `FLAGS`, and a
    `vrije_tekst` key holding any free text before the first recognized
    label (e.g. the counterparty description on an internal transfer).
    """
    result: dict[str, str | bool | None] = {_column_name(label): None for label in LABELS}
    result.update({_column_name(flag): False for flag in FLAGS})
    result["vrije_tekst"] = None

    if not isinstance(text, str) or not text.strip():
        # NaN (missing Mededelingen) comes through as float, not str.
        return result

    remaining = text
    # Strip flags out first so they don't get swallowed into the value of
    # whichever label happens to precede them (e.g. "Term: X Apple Pay
    # Valutadatum: Y" would otherwise leave "X Apple Pay" as the Term value).
    for flag in FLAGS:
        if flag in remaining:
            result[_column_name(flag)] = True
            remaining = remaining.replace(flag, " ")

    # Anything before the first "Label:" is free-form text ING doesn't
    # structure itself, e.g. "Van Oranje spaarrekening F56227366" on an
    # internal transfer, or the date-range prefix on a bank-fee line.
    first_label_match = _FIRST_LABEL_RE.search(remaining)
    leading_text = remaining[: first_label_match.start()] if first_label_match else remaining
    leading_text = _clean(leading_text)
    if leading_text:
        result["vrije_tekst"] = leading_text

    for match in _FIELD_RE.finditer(remaining):
        value = _clean(match.group("value"))
        if value:
            result[_column_name(match.group("label"))] = value

    return result


def extract_mededelingen(df: pd.DataFrame, column: str = "Mededelingen") -> pd.DataFrame:
    """Return `df` with `column` parsed into new `med_*` columns appended."""
    parsed = pd.DataFrame(df[column].apply(parse_mededeling).tolist(), index=df.index)
    return pd.concat([df, parsed.add_prefix("med_")], axis=1)


def load_csv_files(data_dir: Path) -> pd.DataFrame:
    """Read and concatenate every `.csv` file found directly under `data_dir`.

    ING exports use `;` as the field separator, not `,`. Each row is tagged
    with a `SOURCE_FILE_COLUMN` holding the name of the file it came from.
    """
    paths = sorted(data_dir.glob("*.csv"))
    if not paths:
        raise FileNotFoundError(f"No .csv files found in {data_dir}")

    frames = []
    for path in paths:
        frame = pd.read_csv(path, sep=";")
        frame.insert(0, SOURCE_FILE_COLUMN, path.name)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def parse_datum(df: pd.DataFrame, column: str = "Datum") -> pd.DataFrame:
    """Return `df` with `column` converted from ING's `YYYYMMDD` int to a real date.

    Truncated to a plain `datetime.date` (via `.dt.date`) rather than left
    as `datetime64[ns]`, since ING's export has no time-of-day component -
    keeping a phantom midnight timestamp around just invites a spreadsheet
    or downstream tool to render it as a datetime instead of a date.
    """
    df = df.copy()
    df[column] = pd.to_datetime(df[column], format="%Y%m%d").dt.date
    return df


def validate_dtypes(df: pd.DataFrame, expected: dict[str, str] = EXPECTED_DTYPES) -> None:
    """Raise if any `expected` column is missing from `df` or has the wrong dtype."""
    problems = []
    for column, expected_dtype in expected.items():
        if column not in df.columns:
            problems.append(f"{column!r} is missing")
            continue
        actual_dtype = str(df[column].dtype)
        if actual_dtype != expected_dtype:
            problems.append(f"{column!r} is {actual_dtype!r}, expected {expected_dtype!r}")
    if problems:
        raise TypeError("Unexpected column dtype(s): " + "; ".join(problems))


def hash_row(row: pd.Series) -> str:
    """Hash a row's values across all of its (source) columns.

    Sorting by column name first makes the hash independent of column
    order, so the same transaction hashes the same way even if one export
    lists columns differently than another.
    """
    normalized = "|".join("" if pd.isna(value) else str(value) for value in row.sort_index())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _truncate_to_shortest(values: pd.Series) -> pd.Series:
    """Truncate every value in `values` to the length of its shortest member."""
    return values.str.slice(0, values.str.len().min())


def _build_naam_key(df: pd.DataFrame, group_columns: list[str]) -> pd.Series:
    """Return a per-row merchant key to use in place of `NAAM_COLUMN` for hashing.

    `NAAM_COLUMN` alone is unreliable for two different reasons, handled in
    priority order:

    1. ING can reformat it inconsistently between exports of the same card
       payment - not just truncating it, but truncating *and* appending a
       country code (e.g. "McDonalds Erritsoe Fredericia" vs "McDonalds
       Erritsoe Frederici DNK", where the name itself also lost its last
       letter). That defeats simple prefix-truncation, so rows with a
       parsed `TRANSACTIE_COLUMN` use that reference code instead - paired
       with `PASVOLGNR_COLUMN` in case the same code is ever reused on a
       different card - since it identifies the transaction directly
       regardless of how the name was mangled.
    2. For incoming transfers (Tikkie, invoices, ...) ING often uses the
       same generic `NAAM_COLUMN` for every sender - e.g. every incoming
       Tikkie payment is named "AAB INZ TIKKIE" regardless of who sent it -
       so two unrelated payments of the same amount on the same day would
       otherwise collide. `OMSCHRIJVING_COLUMN` (paired with
       `MED_NAAM_COLUMN`, the sender name parsed out of `Mededelingen`
       itself, distinct from the generic `NAAM_COLUMN`) carries the actual
       payment reference or sender detail and is used here instead.

    Rows with neither (plain internal transfers, bank-fee summaries, ...)
    fall back to `NAAM_COLUMN` truncated to the shortest value seen *within
    its own group of rows that already match on `group_columns`* (rather
    than the shortest value in the whole column - 3 characters in this
    dataset), which still collapses simple truncation-only mismatches like
    "CCV*MCDONALD'S BETAALA" vs "CCV*MCDONALD'S BETAALA NLD" without merging
    unrelated transactions that just happen to share date and amount.
    """
    key = df[NAAM_COLUMN].copy()
    unresolved = pd.Series(True, index=df.index)

    if TRANSACTIE_COLUMN in df.columns and PASVOLGNR_COLUMN in df.columns:
        has_transactie = unresolved & df[TRANSACTIE_COLUMN].notna()
        reference = df[PASVOLGNR_COLUMN].fillna("") + "|" + df[TRANSACTIE_COLUMN]
        key = key.mask(has_transactie, reference)
        unresolved &= ~has_transactie

    if OMSCHRIJVING_COLUMN in df.columns and MED_NAAM_COLUMN in df.columns:
        has_omschrijving = unresolved & df[OMSCHRIJVING_COLUMN].notna()
        reference = df[MED_NAAM_COLUMN].fillna("") + "|" + df[OMSCHRIJVING_COLUMN]
        key = key.mask(has_omschrijving, reference)
        unresolved &= ~has_omschrijving

    if unresolved.any():
        fallback = df.loc[unresolved, group_columns].copy()
        fallback["_key"] = key[unresolved]
        key.loc[unresolved] = fallback.groupby(group_columns)["_key"].transform(
            _truncate_to_shortest
        )

    return key


def _build_hash_basis(
    df: pd.DataFrame, identity_columns: list[str] = IDENTITY_COLUMNS
) -> pd.DataFrame:
    """Return `df[identity_columns]` with `NAAM_COLUMN` normalized for hashing.

    See `_build_naam_key` for how the normalization works.
    """
    basis = df[identity_columns].copy()
    if NAAM_COLUMN in identity_columns:
        group_columns = [c for c in identity_columns if c != NAAM_COLUMN]
        basis[NAAM_COLUMN] = _build_naam_key(df, group_columns)
    return basis


def deduplicate_records(
    df: pd.DataFrame, identity_columns: list[str] = IDENTITY_COLUMNS
) -> pd.DataFrame:
    """Drop rows that are duplicates by `identity_columns` - but only across files.

    Overlapping export date ranges mean the same transaction can appear in
    more than one file, sometimes with small differences elsewhere (see
    `IDENTITY_COLUMNS` and `_build_hash_basis`), so only these columns
    decide whether two rows are "the same transaction" - not every column.

    Neither of the two real ING exports this was built against ever
    contains a fully identical row duplicated within itself, so *every* row
    a given file contributes to one identity-hash group is a genuinely
    distinct transaction - e.g. two different people paying the same
    amount via Tikkie on the same day, or two separate payments to the same
    payee for the same amount on the same day, which `_build_naam_key`
    can't always tell apart. For each hash group, this keeps *all* of the
    rows from whichever file contributed to that group last (see
    `load_csv_files` for file order) and drops every row from any other
    file in that same group - trusting that file's rows are already
    correctly deduplicated internally, and treating the others as that
    file's re-export of the same set of transactions. A group contained in
    only one file is therefore always kept in full. The hash itself is
    attached to the result as `ROW_HASH_COLUMN`, giving each surviving row
    a stable identifier derived from its identity columns.

    This assumes a hash group's row count is consistent across the files
    that contain it (true for every case seen in practice) - if one file
    genuinely had fewer matching rows than another, this would still drop
    all of that file's rows in favor of the last file's, rather than trying
    to pair them up individually.
    """
    hash_basis = _build_hash_basis(df, identity_columns)
    row_hashes = hash_basis.apply(hash_row, axis=1)

    if SOURCE_FILE_COLUMN in df.columns:
        preferred_file = df.groupby(row_hashes)[SOURCE_FILE_COLUMN].transform("last")
        keep_mask = df[SOURCE_FILE_COLUMN] == preferred_file
    else:
        keep_mask = ~row_hashes.duplicated(keep="last")

    deduped = df.loc[keep_mask].copy()
    deduped.insert(0, ROW_HASH_COLUMN, row_hashes.loc[keep_mask])
    return deduped.reset_index(drop=True)


def _parse_euro_amount(series: pd.Series) -> pd.Series:
    """Parse a European-formatted EUR amount column (comma decimal, e.g. "9,40") into float."""
    return series.str.replace(",", ".", regex=False).astype(float)


# `VALUTA_COLUMN` values look like "20,00 PLN" (amount + ISO currency code).
_AMOUNT_CURRENCY_RE = re.compile(r"^([\d.,]+)\s+([A-Z]{3})$")


def _split_amount_currency(value: str | float | None) -> tuple[float | None, str | None]:
    """Split a `VALUTA_COLUMN`-shaped "<amount> <CUR>" value into (amount, currency)."""
    if not isinstance(value, str):
        return None, None
    match = _AMOUNT_CURRENCY_RE.match(value.strip())
    if not match:
        return None, None
    amount_str, currency = match.groups()
    return float(amount_str.replace(",", ".")), currency


def _parse_fee_amount(value: str | float | None) -> float | None:
    """Parse a `OPSLAG_COLUMN`/`KOSTEN_COLUMN`-shaped "<amount> EUR" value into float."""
    amount, _currency = _split_amount_currency(value)
    return amount


# A real IBAN starts with a 2-letter country code and 2 check digits. Some
# values in `iban_counterparty` (e.g. a credit card's domestic account
# number) aren't IBANs at all and would otherwise produce a nonsense
# "country" - this shape check filters those out.
_IBAN_SHAPE_RE = re.compile(r"^[A-Z]{2}\d{2}")


def extract_iban_country(iban: str | float | None) -> str | None:
    """Return the 2-letter country code from `iban` if it's IBAN-shaped, else None."""
    if not isinstance(iban, str) or not _IBAN_SHAPE_RE.match(iban):
        return None
    return iban[:2]


def classify_transaction(row: pd.Series) -> str:
    """Return a coarse category for one row, mainly derived from `Mutatiesoort`.

    ING's own `Mutatiesoort` column already distinguishes most transaction
    shapes, but a few of its values are too broad for analysis: both a
    round-trip transfer to this account's own savings sub-account and a
    genuine external bank transfer show up as "Overschrijving" or "Online
    bankieren", and a card refund looks identical to its original charge
    (same `Mutatiesoort`, same amount) except for the "Retourpintransactie"
    marker in `Mededelingen`. Those cases are special-cased ahead of the
    `_MUTATIESOORT_TRANSACTION_TYPES` lookup.
    """
    mededelingen = row.get("Mededelingen")
    if isinstance(mededelingen, str) and "Retourpintransactie" in mededelingen:
        return "Refund"

    if row.get(NAAM_COLUMN) == INTERNAL_TRANSFER_NAAM:
        return "Internal transfer"

    mutatiesoort = row.get("Mutatiesoort")
    if mutatiesoort in ("Overschrijving", "Online bankieren"):
        if "TIKKIE" in str(row.get(NAAM_COLUMN) or "").upper():
            return "Tikkie / payment request"
        return "Bank transfer"

    return _MUTATIESOORT_TRANSACTION_TYPES.get(mutatiesoort, "Other")


def _clean_naam(naam: str) -> str:
    """Strip a POS/processor prefix and trailing country code from `naam`."""
    cleaned = _POS_PREFIX_RE.sub("", naam)
    cleaned = _COUNTRY_CODE_SUFFIX_RE.sub("", cleaned)
    return cleaned.strip()


def extract_payment_processor(naam: str | float | None) -> str | None:
    """Return the payment-terminal/gateway prefix `_clean_naam` strips, if any.

    E.g. "BCK*Albert Heijn UITHOORN NLD" -> "BCK", "SumUp *fa hoogervorst
    NLD" -> "SumUp". A signal for payment *channel* (in-person terminal vs.
    online gateway) rather than who was paid - that's what `company` is
    for. Only maps to a recognizable brand name via
    `_KNOWN_PROCESSOR_NAMES` (e.g. "Mol" -> "Mollie"); anything else comes
    back as the raw prefix token itself.
    """
    if not isinstance(naam, str):
        return None
    match = _POS_PREFIX_RE.match(naam)
    if not match:
        return None
    # `_POS_PREFIX_RE`'s trailing `\s*` can consume whitespace *after* the
    # "*" (e.g. "UBR* " -> "UBR* "), so the leading/trailing strip has to
    # run before stripping the asterisk - otherwise it's left stuck in the
    # middle of the string instead of at the end.
    token = match.group().strip().rstrip("*").strip()
    return _KNOWN_PROCESSOR_NAMES.get(token.lower(), token)


def resolve_company(naam: str | float | None) -> str | None:
    """Group `NAAM_COLUMN` variants of the same merchant under one company name.

    ING varies a merchant's name across (and even within) exports - by
    branch/location ("Albert Heijn 1653 LUCHTH SCHIPH" vs "ALBERT HEIJN
    1011 AMSTELVEEN NLD"), by payment-processor prefix ("BCK*Albert Heijn
    UITHOORN NLD"), and by punctuation ("PayPal Europe S.a.r.l. et Cie
    S.C.A" vs "PayPal (Europe) S.a r.l. et Cie, S.C.A."). `COMPANY_ALIASES`
    matches known merchants regardless of that noise; anything not in the
    list falls back to `_clean_naam`'s best-effort cleanup rather than a
    guess, since grouping arbitrary long-tail merchants isn't solvable
    without a full lookup service.
    """
    if not isinstance(naam, str) or not naam.strip():
        return None

    for pattern, company in COMPANY_ALIASES:
        if pattern.search(naam):
            return company

    return _clean_naam(naam)


def add_analysis_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return `df` with derived columns useful for analysis/tagging appended.

    Built with feeding this into an AppSheet-backed spreadsheet in mind:
    numeric amounts to sum/filter on directly instead of parsing
    `"Bedrag (EUR)"` client-side, a coarse `TRANSACTION_TYPE_COLUMN` (see
    `classify_transaction`) to tag/filter by, a `company` column grouping
    `NAAM_COLUMN`'s many per-branch/per-export spellings of the same
    merchant (see `resolve_company`), a `payment_processor` column exposing
    the POS/gateway prefix that grouping strips out (see
    `extract_payment_processor`), numeric foreign-currency details
    (`foreign_amount`/`foreign_currency`/`exchange_rate`/
    `currency_markup_fee`) parsed out of otherwise-opaque `VALUTA_COLUMN`/
    `KOERS_COLUMN`/`OPSLAG_COLUMN`/`KOSTEN_COLUMN` strings, a
    `counterparty_country` derived from `iban_counterparty`, a couple of
    booleans (`is_roundup_savings`, `has_unknown_counterparty`) for cases
    `TRANSACTION_TYPE_COLUMN` doesn't distinguish on its own, and a couple
    of other best-known-value columns otherwise buried across several
    `med_*` fields. Calendar breakdowns (year, month, ...) are deliberately
    not included here - they're meant to come from a date dimension once
    this feeds into AppSheet, not be baked into the raw data.
    """
    df = df.copy()

    df[AMOUNT_COLUMN] = _parse_euro_amount(df["Bedrag (EUR)"])
    df[SIGNED_AMOUNT_COLUMN] = df[AMOUNT_COLUMN].where(df["Af Bij"] == "Bij", -df[AMOUNT_COLUMN])
    df[BALANCE_AFTER_COLUMN] = _parse_euro_amount(df["Saldo na mutatie"])

    df[TRANSACTION_TYPE_COLUMN] = df.apply(classify_transaction, axis=1)
    df["is_refund"] = df[TRANSACTION_TYPE_COLUMN] == "Refund"
    df["is_foreign_currency"] = df[VALUTA_COLUMN].notna()
    # ING marks a direct debit as authorized under a standing ("continuous")
    # mandate - i.e. a subscription/recurring agreement - independently of
    # whether the amount stays the same between charges (it often doesn't:
    # Netflix's price alone changed 4 times across this flag staying True).
    # That makes it a far more reliable subscription signal than pattern-
    # matching on repeated amounts, which price changes would break.
    df["is_subscription"] = df[DOORLOPENDE_INCASSO_COLUMN]
    # ING's own automatic savings round-up transfer, distinct from a manual
    # internal transfer (both otherwise look like `TRANSACTION_TYPE_COLUMN`
    # "Internal transfer").
    df["is_roundup_savings"] = df[AFRONDING_COLUMN]
    # No counterparty at all, not merely a generic or truncated one - flags
    # which rows most need a human to fill in the gap.
    df["has_unknown_counterparty"] = df[NAAM_COLUMN] == UNKNOWN_COUNTERPARTY_NAAM

    df["counterparty_detail"] = df[OMSCHRIJVING_COLUMN].fillna(df[VRIJE_TEKST_COLUMN])
    df["iban_counterparty"] = df[MED_IBAN_COLUMN].fillna(df["Tegenrekening"])
    # Catches cross-border spending `is_foreign_currency` misses entirely -
    # e.g. a EUR-denominated SEPA transfer to Germany.
    df["counterparty_country"] = df["iban_counterparty"].apply(extract_iban_country)
    df["company"] = df[NAAM_COLUMN].apply(resolve_company)
    df["payment_processor"] = df[NAAM_COLUMN].apply(extract_payment_processor)

    foreign_amount, foreign_currency = zip(*df[VALUTA_COLUMN].apply(_split_amount_currency))
    df["foreign_amount"] = foreign_amount
    df["foreign_currency"] = foreign_currency
    df["exchange_rate"] = _parse_euro_amount(df[KOERS_COLUMN])
    # ING has used two labels for the foreign-transaction markup fee across
    # export eras - "Opslag" in newer ones, "Kosten" in older ones - never
    # both on the same row, so this coalesces them into one column.
    df["currency_markup_fee"] = (
        df[OPSLAG_COLUMN]
        .apply(_parse_fee_amount)
        .fillna(df[KOSTEN_COLUMN].apply(_parse_fee_amount))
    )

    return df


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("extracted_transactions.xlsx")

    combined = load_csv_files(data_dir)
    combined = parse_datum(combined)
    validate_dtypes(combined)
    # extract_mededelingen runs before dedup so `TRANSACTIE_COLUMN` is
    # available to `_build_naam_key` for identifying duplicates.
    enriched = extract_mededelingen(combined)
    deduped = deduplicate_records(enriched)
    result = add_analysis_columns(deduped)

    result.to_excel(output_path, index=False, engine="openpyxl")
    print(
        f"Loaded {len(combined)} row(s) from {data_dir}, "
        f"removed {len(combined) - len(result)} duplicate(s), "
        f"wrote {len(result)} row(s) to {output_path}"
    )


if __name__ == "__main__":
    main()
