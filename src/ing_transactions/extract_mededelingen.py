"""Parse the free-text ``Mededelingen`` column from ING transaction exports.

ING's CSV exports pack several structured sub-fields into a single
``Mededelingen`` ("notifications") text column, formatted differently
depending on the transaction type (card payment, internal transfer, direct
debit, Tikkie request, ...). This module loads every ``.csv`` export found
in a data folder (exports downloaded at different times tend to have
overlapping date ranges, so the same transaction can show up more than
once), drops the resulting duplicate rows, and pulls the recognizable
``Label: value`` sub-fields out of ``Mededelingen`` into their own columns,
plus a couple of boolean flags for the unlabeled markers ING adds inline
(e.g. "Apple Pay").
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

# Column added by `load_csv_files` to record provenance; excluded when
# hashing rows for dedup so the same transaction still matches across files.
SOURCE_FILE_COLUMN = "source_file"

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


def hash_row(row: pd.Series) -> str:
    """Hash a row's values across all of its (source) columns.

    Sorting by column name first makes the hash independent of column
    order, so the same transaction hashes the same way even if one export
    lists columns differently than another.
    """
    normalized = "|".join("" if pd.isna(value) else str(value) for value in row.sort_index())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def deduplicate_records(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows that are exact duplicates across every source column.

    Overlapping export date ranges mean the same transaction can appear in
    more than one file; this treats two rows as the same record only when
    every column matches, not just when they line up positionally.
    `SOURCE_FILE_COLUMN` is ignored when comparing rows (otherwise the same
    transaction read from two different files would never be seen as a
    duplicate), and pandas' `duplicated()` keeps the first occurrence by
    default, so the row - and its `SOURCE_FILE_COLUMN` value - from
    whichever file was read first is the one that's kept.
    """
    hashable = df.drop(columns=SOURCE_FILE_COLUMN, errors="ignore")
    row_hashes = hashable.apply(hash_row, axis=1)
    return df.loc[~row_hashes.duplicated()].reset_index(drop=True)


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("extracted_transactions.xlsx")

    combined = load_csv_files(data_dir)
    deduped = deduplicate_records(combined)
    result = extract_mededelingen(deduped)

    result.to_excel(output_path, index=False, engine="openpyxl")
    print(
        f"Loaded {len(combined)} row(s) from {data_dir}, "
        f"removed {len(combined) - len(deduped)} duplicate(s), "
        f"wrote {len(result)} row(s) to {output_path}"
    )


if __name__ == "__main__":
    main()
