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
from typing import Iterable, List, Optional, Sequence, Set

import pdfplumber

# Consolidated configuration dictionary
CONFIG = {
    "DEFAULT_SOURCE_DIRS": [
        r"C:\Users\azira\OneDrive\Documents\Finance\00 FIRE\CreditCard Statements\xx5485",
        r"C:\Users\azira\OneDrive\Documents\Finance\00 FIRE\CreditCard Statements\xx1197",
        r"C:\Users\azira\OneDrive\Documents\Finance\00 FIRE\CreditCard Statements\xx6329"
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
}

AMOUNT_RE = re.compile(
    r"\$?-?\d{1,3}(?:,\d{3})*\.\d{2}-?|\$?-?\d+\.\d{2}-?"
)

TRANSACTION_ROW_RE = re.compile(
    rf"^(?P<date>\d{{1,2}}\s+(?:{CONFIG['MONTHS']}))\s+"
    rf"(?P<details>.+?)\s+"
    rf"(?P<amount>{AMOUNT_RE.pattern})$",
    re.IGNORECASE,
)


def normalise_space(value: object) -> str:
    """Replace newlines and tabs with spaces, then collapse multiple spaces."""
    if value is None:
        return ""
    cleaned_str = str(value).replace("\n", " ").replace("\t", " ")
    return re.sub(r"\s+", " ", cleaned_str).strip()


def normalise_amount(amount: str) -> str:
    amount = amount.replace("$", "").replace(",", "").strip()
    if amount.endswith("-"):
        amount = "-" + amount[:-1]
    return amount


def extract_statement_period(pdf: pdfplumber.PDF) -> tuple[Optional[datetime], Optional[datetime]]:
    """Extract statement start/end dates from first page."""
    if not pdf.pages:
        return None, None

    page1_text = pdf.pages[0].extract_text() or ""
    match = re.search(
        r"Statement Period\s+"
        r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s*-\s*"
        r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})",
        page1_text,
        re.IGNORECASE,
    )

    if not match:
        return None, None

    try:
        start_date = datetime.strptime(match.group(1), "%d %b %Y")
        end_date = datetime.strptime(match.group(2), "%d %b %Y")
        return start_date, end_date
    except ValueError:
        return None, None


def convert_statement_date(
    date_text: str,
    statement_start: Optional[datetime],
    statement_end: Optional[datetime],
) -> str:
    """Convert '08 Dec' to '08-12-2020' using statement context."""
    parts = date_text.split()
    day = int(parts[0])
    month_text = parts[1].title()
    month = CONFIG["MONTH_MAP"].get(month_text, 1)

    if not statement_start or not statement_end:
        year = 2000
    elif statement_start.year != statement_end.year:
        year = statement_start.year if month >= statement_start.month else statement_end.year
    else:
        year = statement_start.year

    return datetime(year, month, day).strftime("%d-%m-%Y")


def calculate_financial_year(date_str: str) -> str:
    """
    Calculate the Australian Financial Year ending year from a 'DD-MM-YYYY' date string.
    Example: '08-08-2020' -> '2021', '10-02-2021' -> '2021'
    """
    dt = datetime.strptime(date_str, "%d-%m-%Y")
    fy_year = dt.year + 1 if dt.month >= 7 else dt.year
    return str(fy_year)


def extract_month_number(date_str: str) -> str:
    """Extract 2-digit numerical month string from a 'DD-MM-YYYY' date string."""
    dt = datetime.strptime(date_str, "%d-%m-%Y")
    return f"{dt.month:02d}"


def looks_like_non_transaction(text: str) -> bool:
    lower = text.lower()
    exclusions = [
        "transactions date transaction details",
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
    ]
    return any(x in lower for x in exclusions)


def smart_join_detail_parts(parts: Sequence[str]) -> str:
    cleaned = [normalise_space(x) for x in parts if normalise_space(x)]
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
    cells = [normalise_space(c) for c in row if normalise_space(c)]
    if not cells:
        return None

    joined = " ".join(cells)
    if looks_like_non_transaction(joined):
        return None

    amount_index = None
    amount_text = None

    for i in range(len(cells) - 1, -1, -1):
        match = AMOUNT_RE.search(cells[i])
        if match:
            amount_index = i
            amount_text = match.group(0)
            break

    if amount_index is None or amount_text is None:
        return None

    left_cells = cells[:amount_index]
    amount_pos = cells[amount_index].rfind(amount_text)
    residual = normalise_space(cells[amount_index][:amount_pos])

    if residual:
        left_cells.append(residual)

    if not left_cells:
        return None

    text = " ".join(left_cells)
    date_match = re.match(
        rf"^(?P<date>\d{{1,2}}\s+(?:{CONFIG['MONTHS']}))\b(?P<rest>.*)$",
        text,
        re.IGNORECASE,
    )

    if not date_match:
        return None

    statement_date = convert_statement_date(
        date_match.group("date"),
        statement_start,
        statement_end,
    )

    first_cell = re.match(
        rf"^(\d{{1,2}}\s+(?:{CONFIG['MONTHS']}))\b(?P<rest>.*)$",
        left_cells[0],
        re.IGNORECASE,
    )

    if first_cell:
        details = smart_join_detail_parts([first_cell.group("rest")] + left_cells[1:])
    else:
        details = normalise_space(date_match.group("rest"))

    details = normalise_space(details.replace("\t", " "))

    financial_year = calculate_financial_year(statement_date)
    month_num = extract_month_number(statement_date)

    return {
        "Date": statement_date,
        "Financial Year": financial_year,
        "Month": month_num,
        "Transaction details": details,
        "Amount (A$)": normalise_amount(amount_text),
    }


def table_contains_transaction(table: Sequence[Sequence[object]]) -> bool:
    for row in table:
        if parse_transaction_cells(row, datetime(2000, 1, 1), datetime(2000, 1, 1)):
            return True
    return False


def get_tables(page: pdfplumber.page.Page) -> list:
    width = page.width
    height = page.height

    cropped = page.crop((45, 90, width - 20, height - 65))

    explicit_tables = cropped.extract_tables(CONFIG["EXPLICIT_TABLE_SETTINGS"]) or []
    for t in explicit_tables:
        if table_contains_transaction(t):
            return explicit_tables

    return cropped.extract_tables(CONFIG["TEXT_TABLE_SETTINGS"]) or []


def extract_transactions(pdf_path: Path) -> list[dict[str, str]]:
    transactions = []

    with pdfplumber.open(pdf_path) as pdf:
        statement_start, statement_end = extract_statement_period(pdf)

        for page in pdf.pages:
            for table in get_tables(page):
                for row in table:
                    txn = parse_transaction_cells(
                        row,
                        statement_start,
                        statement_end,
                    )
                    if not txn:
                        continue

                    txn["Source PDF"] = pdf_path.name
                    transactions.append(txn)

    return transactions


def iter_pdf_files(source_dirs: Iterable[Path]) -> Iterable[Path]:
    """Find all unique PDF files across multiple source directories."""
    seen_files: Set[Path] = set()

    for source_dir in source_dirs:
        if not source_dir.exists():
            print(f"Warning: Directory not found, skipping: {source_dir}")
            continue

        for pdf_file in sorted(source_dir.rglob("*.pdf"), key=lambda x: str(x).lower()):
            resolved_path = pdf_file.resolve()
            if resolved_path not in seen_files:
                seen_files.add(resolved_path)
                yield pdf_file


def write_output(rows: list[dict[str, str]], output_file: str | Path) -> None:
    with open(output_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CONFIG["CSV_COLUMNS"],
            delimiter="|",
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract PDF statement transactions from multiple directories into a single PSV."
    )
    parser.add_argument(
        "--source",
        nargs="+",
        action="extend",
        default=None,
        help="One or more source directory paths. Overrides defaults if supplied.",
    )
    parser.add_argument(
        "--output",
        default=CONFIG["DEFAULT_OUTPUT_FILE"],
        help="Target output PSV file path.",
    )

    args = parser.parse_args()

    # Use CLI sources if provided, otherwise fall back to CONFIG defaults
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

    print()
    print(f"Processed {processed_count} PDF files across {len(source_dirs)} folder(s).")
    print(f"Total transactions extracted: {len(all_rows)}")
    print(f"Output file created: {args.output}")


if __name__ == "__main__":
    main()
