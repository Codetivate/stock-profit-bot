"""Download a SET financial-statement zip and stage it under data/raw/.

The weblink.set.or.th subdomain is NOT behind Incapsula, so plain requests
works. We only use the Playwright session for pages that need it
(api.set.or.th and /newsdetails/).

Output layout (see ARCHITECTURE.md §3):
    data/raw/{SYMBOL}/financials/{THAI_YEAR}/{PERIOD}/
        source.zip
        source.xlsx         (extracted for convenience)
        metadata.json
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests


RAW_ROOT = Path("data/raw")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
}


# Period identifiers match the SET reporting structure and our schema.
Period = str  # "Q1" | "H1" | "9M" | "FY"

HEADLINE_PATTERNS = [
    (re.compile(r"ประจำปี\s*(\d{4})"),        "FY"),
    (re.compile(r"ไตรมาสที่\s*1/(\d{4})"),    "Q1"),
    (re.compile(r"ไตรมาสที่\s*2/(\d{4})"),    "H1"),
    (re.compile(r"ไตรมาสที่\s*3/(\d{4})"),    "9M"),
]


@dataclass
class FilingKey:
    """Uniquely identifies a filing in our raw/processed tree."""
    symbol: str
    thai_year: int
    period: Period   # Q1 | H1 | 9M | FY


@dataclass
class IngestedFiling:
    """Result of downloading + staging one filing."""
    key: FilingKey
    zip_path: Path
    xlsx_path: Path
    metadata_path: Path
    sha256: str
    source_url: str
    news_id: str
    filing_date: str   # ISO date from news datetime
    headline: str


def parse_headline(headline: str) -> Optional[tuple[int, Period]]:
    """Map a SET financial-statement headline to (thai_year, period).

    Returns None for headlines we don't recognise (the caller should skip
    them rather than guessing).
    """
    for pat, period in HEADLINE_PATTERNS:
        m = pat.search(headline)
        if m:
            return int(m.group(1)), period
    return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_xlsx(zip_path: Path, out_dir: Path) -> Optional[Path]:
    """Extract the financial-statements workbook from the zip into out_dir.

    SET filings use .xlsx for modern reports and .xls for older (pre-2023)
    reports. We prefer .xlsx when both are present and preserve the
    original extension so downstream parsers can branch on it.
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        xlsx = [n for n in names if n.lower().endswith(".xlsx")]
        xls = [n for n in names if n.lower().endswith(".xls")]
        chosen = xlsx[0] if xlsx else (xls[0] if xls else None)
        if not chosen:
            return None
        ext = ".xlsx" if chosen.lower().endswith(".xlsx") else ".xls"
        target = out_dir / f"source{ext}"
        with zf.open(chosen) as src, target.open("wb") as dst:
            dst.write(src.read())
        return target


def download_filing(
    *,
    symbol: str,
    zip_url: str,
    news_id: str,
    headline: str,
    news_datetime: str,
    raw_root: Path = RAW_ROOT,
) -> IngestedFiling:
    """Download a zip and stage it with metadata. Idempotent — re-runs skip
    the HTTP fetch if the zip is already present with the same sha256."""
    parsed = parse_headline(headline)
    if not parsed:
        raise ValueError(f"Unrecognised financial headline: {headline!r}")
    year, period = parsed

    out_dir = raw_root / symbol / "financials" / str(year) / period
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / "source.zip"

    if not zip_path.exists() or zip_path.stat().st_size == 0:
        r = requests.get(zip_url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        zip_path.write_bytes(r.content)

    xlsx_path = _extract_xlsx(zip_path, out_dir)
    if not xlsx_path:
        raise RuntimeError(f"No XLSX inside {zip_path}")

    sha = _sha256(zip_path)
    metadata_path = out_dir / "metadata.json"
    metadata = {
        "symbol": symbol,
        "thai_year": year,
        "period": period,
        "source_url": zip_url,
        "original_filename": zip_url.rsplit("/", 1)[-1],
        "news_id": news_id,
        "headline": headline,
        "filing_datetime": news_datetime,
        "filing_date": news_datetime[:10] if news_datetime else None,
        "sha256": sha,
        "size_bytes": zip_path.stat().st_size,
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return IngestedFiling(
        key=FilingKey(symbol=symbol, thai_year=year, period=period),
        zip_path=zip_path,
        xlsx_path=xlsx_path,
        metadata_path=metadata_path,
        sha256=sha,
        source_url=zip_url,
        news_id=news_id,
        filing_date=metadata["filing_date"],
        headline=headline,
    )
