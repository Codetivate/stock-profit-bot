"""
parse_set_zip.py — SET financial statement parser

Extracts net profit data from SET news zip files.
Format: XLSX with sheets like 'PL 10-11', 'BS 7-9', etc.

Usage:
    from parse_set_zip import parse_zip
    data = parse_zip("0737FIN250220261406350902T.zip")
"""
import os
import re
import zipfile
import tempfile
from typing import Optional, Dict, Any
from datetime import datetime
from dataclasses import dataclass, asdict

import openpyxl


@dataclass
class FinancialData:
    """Extracted financial data from one report."""
    symbol: str
    filename: str
    period_label: str  # e.g. "งบประจำปี 2568"
    period_type: str   # "annual" | "quarterly" | "half"
    year: int          # Thai year, e.g. 2568
    quarter: Optional[int]  # 1, 2, 3, 4 or None for annual

    # Income statement numbers (in millions of baht)
    revenue: Optional[float] = None          # รวมรายได้
    revenue_prior: Optional[float] = None    # previous period
    net_profit: Optional[float] = None       # กำไรสำหรับปี/ไตรมาส
    net_profit_prior: Optional[float] = None
    shareholder_profit: Optional[float] = None  # ส่วนที่เป็นของผู้ถือหุ้น
    shareholder_profit_prior: Optional[float] = None
    eps: Optional[float] = None              # กำไรต่อหุ้น
    eps_prior: Optional[float] = None


def _is_revenue_row(text: str) -> bool:
    """Match 'รวมรายได้' (total revenue)."""
    if not text:
        return False
    text = str(text).strip()
    return text == "รวมรายได้"


def _is_netprofit_row(text: str) -> bool:
    """Match 'กำไรสำหรับปี/ไตรมาส/งวด' (net profit for period)."""
    if not text:
        return False
    text = str(text).strip()
    patterns = [
        r"^กำไรสำหรับ(ปี|งวด|ไตรมาส)",
        r"^กำไร \(ขาดทุน\) สำหรับ(ปี|งวด|ไตรมาส)",
        r"^กำไรสุทธิสำหรับ",
    ]
    return any(re.match(p, text) for p in patterns)


def _is_shareholder_profit_row(text: str) -> bool:
    """Match 'ส่วนที่เป็นของผู้ถือหุ้นของบริษัท'."""
    if not text:
        return False
    text = str(text).strip()
    # Must contain both keywords
    return "ผู้ถือหุ้น" in text and "บริษัท" in text and "ส่วน" in text


def _is_eps_row(text: str) -> bool:
    """Match 'กำไรต่อหุ้น' (EPS)."""
    if not text:
        return False
    text = str(text).strip()
    return text.startswith("กำไรต่อหุ้น") or "กำไรต่อหุ้นขั้นพื้นฐาน" in text


def _find_pl_sheet(workbook) -> Optional[str]:
    """Find the Profit & Loss sheet in the workbook.
    SET uses 'PL' prefix (e.g. 'PL 10-11').
    """
    for name in workbook.sheetnames:
        upper = name.upper()
        if "PL" in upper or "กำไรขาดทุน" in name:
            return name
    return None


def _extract_numeric(row: tuple, is_eps: bool = False) -> list:
    """Extract first 2 non-zero numeric values from row (current, prior).
    Skips small integers (1-99) which are footnote references in SET XBRL.

    For non-EPS: skips whole-number values < 100 (likely notes)
    For EPS: skips whole integers (which are notes), accepts decimals
    """
    nums = []
    for cell in row[1:]:
        if not isinstance(cell, (int, float)) or cell == 0:
            continue
        val = float(cell)

        # EPS-specific logic: EPS is always a decimal (like 3.10, 2.77)
        # Notes are whole integers. Skip any integer value.
        if is_eps:
            # If cell is integer type OR value has no decimal, it's a note
            if isinstance(cell, int):
                continue
            if val == int(val):
                continue
        else:
            # Skip footnote references (small whole integers)
            if isinstance(cell, int) and 1 <= abs(val) < 100:
                continue
            if val == int(val) and abs(val) < 100:
                continue

        nums.append(val)
        if len(nums) >= 2:
            break
    return nums


def _parse_filename(filename: str) -> Dict[str, Any]:
    """Parse SET zip filename.
    Format: {code}FIN{DD}{MM}{YYYY}{HHMMSS}{seq}T.zip
    Example: 0737FIN250220261406350902T.zip
    """
    m = re.match(r"(\d{4})(FIN|ANN|NWS|F56)(\d{2})(\d{2})(\d{4})(\d{6})(\d{4})T\.zip",
                 filename, re.IGNORECASE)
    if m:
        code, rtype, dd, mm, yyyy, _, _ = m.groups()
        return {
            "company_code": code,
            "report_type": rtype,
            "report_date": f"{yyyy}-{mm}-{dd}",
        }
    return {}


def parse_zip(zip_path: str, symbol: str = "UNKNOWN") -> Optional[FinancialData]:
    """Parse a SET news zip file and extract key financial data.

    Args:
        zip_path: Path to the zip file
        symbol: Stock symbol (e.g. 'CPALL')

    Returns:
        FinancialData object, or None if parsing failed.
    """
    if not os.path.exists(zip_path):
        raise FileNotFoundError(f"Zip not found: {zip_path}")

    filename = os.path.basename(zip_path)
    meta = _parse_filename(filename)

    with tempfile.TemporaryDirectory() as tmp:
        # Extract zip
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)

        # Find XLSX file
        xlsx_path = None
        for root, _, files in os.walk(tmp):
            for f in files:
                if f.upper().endswith(".XLSX"):
                    xlsx_path = os.path.join(root, f)
                    break
            if xlsx_path:
                break

        if not xlsx_path:
            print(f"[parse_zip] No XLSX found in {filename}")
            return None

        # Open workbook
        try:
            wb = openpyxl.load_workbook(xlsx_path, data_only=True)
        except Exception as e:
            print(f"[parse_zip] Failed to open XLSX: {e}")
            return None

        pl_sheet = _find_pl_sheet(wb)
        if not pl_sheet:
            print(f"[parse_zip] No PL sheet in {filename}")
            return None

        ws = wb[pl_sheet]

        # Detect period type from first few rows
        period_label = ""
        period_type = "unknown"
        year = 0
        quarter = None

        for row in ws.iter_rows(min_row=1, max_row=15, values_only=True):
            for cell in row:
                if cell and isinstance(cell, str):
                    text = str(cell).strip()
                    # Period type detection
                    if "สำหรับปี" in text or "สำหรับรอบปี" in text:
                        period_type = "annual"
                    elif "สำหรับงวดหกเดือน" in text or "6 เดือน" in text or "หกเดือน" in text:
                        period_type = "half"
                    elif "สำหรับไตรมาส" in text or "3 เดือน" in text or "สำหรับงวดสามเดือน" in text:
                        period_type = "quarterly"

                    # Year from Thai date text
                    year_match = re.search(r"25(\d{2})", text)
                    if year_match and year == 0:
                        year = int(f"25{year_match.group(1)}")

        # Also look at header row cells for year values (column headers often have years)
        if year == 0:
            for row in ws.iter_rows(min_row=1, max_row=10, values_only=True):
                for cell in row:
                    if cell and isinstance(cell, (int, float)):
                        val = int(cell)
                        # Thai fiscal year 2560-2580 range
                        if 2560 <= val <= 2580:
                            year = val
                            break
                if year > 0:
                    break

        # Fallback to filename date (Gregorian year - 543 = Thai year)
        if year == 0 and "report_date" in meta:
            try:
                gregorian = int(meta["report_date"][:4])
                year = gregorian - 543 - 1  # Report filed in following year
            except ValueError:
                pass

        # Extract financial data
        result = FinancialData(
            symbol=symbol,
            filename=filename,
            period_label=period_label or f"FY{year}" if period_type == "annual" else "",
            period_type=period_type,
            year=year,
            quarter=quarter,
        )

        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, values_only=True):
            if not row:
                continue
            label = str(row[0]).strip() if row[0] else ""
            if not label:
                continue

            is_eps = _is_eps_row(label)
            nums = _extract_numeric(row, is_eps=is_eps)

            if len(nums) >= 2:
                if _is_revenue_row(label) and result.revenue is None:
                    result.revenue = nums[0] / 1_000_000  # to millions
                    result.revenue_prior = nums[1] / 1_000_000
                elif _is_netprofit_row(label) and result.net_profit is None:
                    result.net_profit = nums[0] / 1_000_000
                    result.net_profit_prior = nums[1] / 1_000_000
                elif _is_shareholder_profit_row(label) and result.shareholder_profit is None:
                    result.shareholder_profit = nums[0] / 1_000_000
                    result.shareholder_profit_prior = nums[1] / 1_000_000
                elif is_eps and result.eps is None:
                    # EPS is in baht already, not millions
                    result.eps = nums[0]
                    result.eps_prior = nums[1]

        # Build period label
        if not result.period_label:
            if period_type == "annual":
                result.period_label = f"งบประจำปี {year}"
            elif period_type == "half":
                result.period_label = f"งบครึ่งปี {year}"
            elif period_type == "quarterly":
                result.period_label = f"งบไตรมาส {year}"
            else:
                result.period_label = f"งบการเงิน {year}"

        return result


def to_dict(data: FinancialData) -> dict:
    """Convert FinancialData to dict for JSON storage."""
    return asdict(data)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python parse_set_zip.py <zip_path> [symbol]")
        sys.exit(1)

    zip_path = sys.argv[1]
    symbol = sys.argv[2] if len(sys.argv) > 2 else "UNKNOWN"

    data = parse_zip(zip_path, symbol)
    if data:
        print("=" * 60)
        print(f"EXTRACTED: {data.symbol}  ·  {data.period_label}")
        print("=" * 60)
        for k, v in asdict(data).items():
            if v is not None:
                if isinstance(v, float):
                    print(f"  {k:30} : {v:>12,.2f}")
                else:
                    print(f"  {k:30} : {v}")
    else:
        print("Failed to parse")
