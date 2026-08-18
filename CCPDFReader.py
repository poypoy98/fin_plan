#!/usr/bin/env python3
"""
Extract transaction tables from all PDFs across multiple source folders
into a single pipe-delimited output file.

Requirements:
pip install pdfplumber

Output columns:
Source PDF|Date|Financial Year|Month|Transaction details|Amount (A$)
"""

from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence, Set

import pdfplumber

# Global configuration dictionary
CONFIG = {
    "DEFAULT_SOURCE_DIRS": [
        r"C:\Users\azira\OneDrive\Documents\Finance\00 FIRE\CreditCard Statements\xx5485",
        r"C:\Users\azira\OneDrive\Documents\Finance\00 FIRE\CreditCard Statements\xx1197",
        r"C:\Users\azira\OneDrive\Documents\Finance\00 FIRE\CreditCard Statements\xx6329",
    ],
    "DEFAULT_OUTPUT_FILE": "credit_cards_trxns.txt",
    "CSV_COLUMNS": [
        "Source PDF",
        "Date",
        "Financial Year",
        "Month",
        "Transaction details",
        "Amount (A$)",
    ],
    "MONTHS": "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec",
    "MONTH_MAP": {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    },
    "EXPLICIT_TABLE_SETTINGS": {
        "vertical_strategy": "explicit",
        "explicit_vertical_lines": [50, 105, 510, 570],
        "horizontal_strategy": "text",
        "text_x_tolerance": 2,
        "text_y_tolerance": 3,
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "intersection_tolerance": 5,
        "min_words_horizontal": 1,
    },
    "TEXT_TABLE_SETTINGS": {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "text_x_tolerance": 2,
        "text_y_tolerance": 3,
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "intersection_tolerance": 5,
        "min_words_vertical": 3,
        "min_words_horizontal": 1,
    },
    "EXCLUSION_PHRASES": (
        "transactions date transaction details",
        "date transaction details amount",
        "interest charged on purchases",
        "interest charged on cash advances",
        "purchase rate",
        "cash advance rate",
        "regular payments",
        "things you should know",
        "company name last amount",
        "awards points summary",
        "opening points balance",
        "points earned",
        "total points balance",
        "mastercard is the registered trademark",
        "payment summary",
    ),
}

# Pre-compiled regular expressions for performance
AMOUNT_RE = re.compile(
    r"\$?-?\d{1,3}(?:,\d{3})*(?:\.|\s)\d{2}-?"
    r"|\$?-?\d+(?:\.|\s)\d{2}-?"
)

DATE_MATCH_RE = re.compile(
    rf"^(?P<date>\d{{1,2}}\s+(?:{CONFIG['MONTHS']}))\b(?P<rest>.*)$",
    re.IGNORECASE,
)

STATEMENT_PERIOD_RE = re.compile(
    r"Statement\s*Period\s*"
    r"(?P<start_day>\d{1,2})\s*"
    r"(?P<start_mon>[A-Za-z]{3})\s*"
    r"(?P<start_year>\d{4})\s*-\s*"
    r"(?P<end_day>\d{1,2})\s*"
    r"(?P<end_mon>[A-Za-z]{3})\s*"
    r"(?P<end_year>\d{4})",
    re.IGNORECASE,
)


def normalise_space(value: object) -> str:
    """Replace newlines/tabs with spaces and collapse multi-space sequences."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\n", " ").replace("\t", " ")).strip()


def normalise_amount(amount: str) -> str:
    """Normalise extracted amount text to signed decimal format."""
    amount = amount.replace("$", "").replace(",", "").strip()
    is_negative = amount.startswith("-") or amount.endswith("-")
    amount = amount.strip("-").strip()

    # Repair whitespace decimal points (e.g., "40 00" -> "40.00")
    amount = re.sub(r"^(\d+)\s+(\d{2})$", r"\1.\2", amount).replace(" ", "")

    return f"-{amount}" if is_negative else amount


def extract_statement_period(pdf: pdfplumber.PDF) -> tuple[Optional[datetime], Optional[datetime]]:
    """Extract statement start and end dates from page 1."""
    if not pdf.pages:
        return None, None

    page1_text = pdf.pages[0].extract_text() or ""
    match = STATEMENT_PERIOD_RE.search(page1_text)

    if not match:
        return None, None

    try:
        start_date = datetime.strptime(
            f"{match.group('start_day')} {match.group('start_mon')} {match.group('start_year')}".title(),
            "%d %b %Y",
        )
        end_date = datetime.strptime(
            f"{match.group('end_day')} {match.group('end_mon')} {match.group('end_year')}".title(),
            "%d %b %Y",
        )
        return start_date, end_date
    except ValueError:
        return None, None


def parse_statement_date(
    date_text: str,
    statement_start: Optional[datetime],
    statement_end: Optional[datetime],
) -> datetime:
    """Parse string date (e.g. '09 Apr') directly into a datetime object using statement context."""
    parts = date_text.split()
    day = int(parts[0])
    month = CONFIG["MONTH_MAP"].get(parts[1].title(), 1)

    if not statement_start or not statement_end:
        year = 2000
    elif statement_start.year != statement_end.year:
        year = statement_start.year if month >= statement_start.month else statement_end.year
    else:
        year = statement_start.year

    return datetime(year, month, day)


def calculate_financial_year(dt: datetime) -> str:
    """Calculate the Australian financial year ending year from a datetime object."""
    return str(dt.year + 1 if dt.month >= 7 else dt.year)


def extract_month_number(dt: datetime) -> str:
    """Extract 2-digit month string from a datetime object."""
    return f"{dt.month:02d}"


def looks_like_non_transaction(text: str) -> bool:
    """Check if row text matches standard statement headers or summary rows."""
    lower = text.lower()
    return any(phrase in lower for phrase in CONFIG["EXCLUSION_PHRASES"])


def smart_join_detail_parts(parts: Sequence[str]) -> str:
    """Join split merchant description fragments."""
    cleaned = [normalise_space(part) for part in parts if normalise_space(part)]
    if not cleaned:
        return ""

    detail = cleaned[0]
    for piece in cleaned[1:]:
        last_word = detail.split()[-1] if detail.split() else ""
        if len(last_word) <= 3 and last_word[-1:].isalpha() and piece[:1].islower():
            detail += piece
        else:
            detail += " " + piece

    return normalise_space(detail)


def parse_transaction_cells(
    row: Sequence[object],
    statement_start: Optional[datetime],
    statement_end: Optional[datetime],
) -> Optional[dict[str, str]]:
    """Parse raw table cells into a structured transaction dictionary."""
    cells = [normalise_space(cell) for cell in row if normalise_space(cell)]
    if not cells or looks_like_non_transaction(" ".join(cells)):
        return None

    amount_index = None
    amount_text = None
    residual_override = None

    # Identify right-most amount
    for index in range(len(cells) - 1, -1, -1):
        match = AMOUNT_RE.search(cells[index])
        if match:
            amount_index = index
            amount_text = match.group(0)
            break

        # Handle split amounts across adjacent cells
        if index > 0 and re.fullmatch(r"\d{2}-?", cells[index]):
            previous_cell = cells[index - 1]
            previous_match = re.search(
                r"(?P<prefix>.*?)(?P<dollars>\d{1,3}(?:,\d{3})*|\d+)$",
                previous_cell,
            )
            if previous_match:
                amount_index = index - 1
                amount_text = f"{previous_match.group('dollars')} {cells[index]}"
                residual_override = normalise_space(previous_match.group("prefix"))
                break

    if amount_index is None or amount_text is None:
        return None

    left_cells = cells[:amount_index]
    if residual_override is not None:
        residual = residual_override
    else:
        amount_pos = cells[amount_index].rfind(amount_text)
        residual = normalise_space(cells[amount_index][:amount_pos])

    if residual:
        left_cells.append(residual)

    if not left_cells:
        return None

    text = normalise_space(" ".join(left_cells))
    date_match = DATE_MATCH_RE.match(text)
    if not date_match:
        return None

    dt = parse_statement_date(date_match.group("date"), statement_start, statement_end)

    first_cell = DATE_MATCH_RE.match(left_cells[0])
    if first_cell:
        details = smart_join_detail_parts([first_cell.group("rest")] + left_cells[1:])
    else:
        details = normalise_space(date_match.group("rest"))

    return {
        "Date": dt.strftime("%d-%m-%Y"),
        "Financial Year": calculate_financial_year(dt),
        "Month": extract_month_number(dt),
        "Transaction details": normalise_space(details),
        "Amount (A$)": normalise_amount(amount_text),
    }


def table_contains_transaction(table: Sequence[Sequence[object]]) -> bool:
    """Determine if an extracted table contains valid transactions."""
    dummy_dt = datetime(2000, 1, 1)
    return any(parse_transaction_cells(row, dummy_dt, dummy_dt) for row in table)


def get_tables(page: pdfplumber.page.Page) -> list:
    """Extract candidate tables using explicit lines first, falling back to text strategy."""
    cropped = page.crop((45, 90, page.width - 20, page.height - 65))

    explicit_tables = cropped.extract_tables(CONFIG["EXPLICIT_TABLE_SETTINGS"]) or []
    if any(table_contains_transaction(t) for t in explicit_tables):
        return explicit_tables

    return cropped.extract_tables(CONFIG["TEXT_TABLE_SETTINGS"]) or []


def extract_transactions(pdf_path: Path) -> list[dict[str, str]]:
    """Process a single PDF file and extract all contained transactions."""
    transactions = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            statement_start, statement_end = extract_statement_period(pdf)
            for page in pdf.pages:
                for table in get_tables(page):
                    for row in table:
                        txn = parse_transaction_cells(row, statement_start, statement_end)
                        if txn:
                            txn["Source PDF"] = pdf_path.name
                            transactions.append(txn)
    except Exception as exc:
        print(f"Error processing {pdf_path.name}: {exc}")

    return transactions


def iter_pdf_files(source_dirs: Iterable[Path]) -> Iterable[Path]:
    """Yield unique PDF files across specified directories."""
    seen_files: Set[Path] = set()

    for source_dir in source_dirs:
        if not source_dir.exists():
            print(f"Warning: Directory not found, skipping: {source_dir}")
            continue

        for pdf_file in sorted(source_dir.rglob("*.pdf"), key=lambda p: str(p).lower()):
            resolved_path = pdf_file.resolve()
            if resolved_path not in seen_files:
                seen_files.add(resolved_path)
                yield pdf_file


def write_output(rows: list[dict[str, str]], output_file: str | Path) -> None:
    """Write extracted dictionary rows to a pipe-delimited output file."""
    with open(output_file, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CONFIG["CSV_COLUMNS"],
            delimiter="|",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract PDF statement transactions into a single PSV file."
    )
    parser.add_argument(
        "--source",
        nargs="+",
        action="extend",
        default=None,
        help="One or more source directory paths.",
    )
    parser.add_argument(
        "--output",
        default=CONFIG["DEFAULT_OUTPUT_FILE"],
        help="Target output PSV file path.",
    )

    args = parser.parse_args()
    source_paths = args.source if args.source else CONFIG["DEFAULT_SOURCE_DIRS"]
    source_dirs = [Path(p) for p in source_paths]

    all_rows = []
    processed_count = 0

    for pdf_file in iter_pdf_files(source_dirs):
        rows = extract_transactions(pdf_file)
        print(f"{pdf_file.name}: {len(rows)} transactions")
        all_rows.extend(rows)
        processed_count += 1

    write_output(all_rows, args.output)

    print(
        f"\nProcessed {processed_count} PDF file(s) across {len(source_dirs)} directory location(s).\n"
        f"Total transactions extracted: {len(all_rows)}\n"
        f"Output written to: {args.output}"
    )


if __name__ == "__main__":
    main()
