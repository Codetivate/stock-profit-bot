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
import xlrd  # type: ignore  # legacy .xls reader


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
    """Match 'กำไรสำหรับปี/ไตรมาส/งวด' or the bare 'กำไรสุทธิ' label used
    by banks and financial institutions."""
    if not text:
        return False
    text = str(text).strip()
    patterns = [
        r"^กำไรสำหรับ(ปี|งวด|ไตรมาส)",
        r"^กำไร \(ขาดทุน\) สำหรับ(ปี|งวด|ไตรมาส)",
        r"^กำไรสุทธิสำหรับ",
        r"^กำไรสุทธิ$",            # banks: bare "กำไรสุทธิ"
        r"^กำไร \(ขาดทุน\) สุทธิ$",
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


def _detect_unit_divisor(rows_top: list) -> float:
    """Infer how to convert XLSX cell values to millions of baht.

    SET financial XLSX files include a unit marker near the top of every
    sheet (e.g. ``(บาท)`` for annual reports, ``(พันบาท)`` for quarterly,
    occasionally ``(ล้านบาท)`` for summarised sheets).

    Returns the divisor to apply to raw numeric cells so the result is in
    millions of baht. Defaults to 1,000,000 (treat as baht) if no marker
    is found — the original behaviour for XLSX where the unit was implicit.
    """
    for row in rows_top:
        for cell in row:
            if not cell or not isinstance(cell, str):
                continue
            s = cell.strip()
            # Order matters: check more-specific markers first.
            if "ล้านบาท" in s:
                return 1.0
            if "พันบาท" in s:
                return 1_000.0
            if "บาท" in s:
                return 1_000_000.0
    return 1_000_000.0


class _XlsAdapter:
    """Minimal subset of openpyxl's API backed by xlrd, so the rest of the
    parser can treat .xls and .xlsx identically."""

    class Sheet:
        def __init__(self, sheet):
            self._s = sheet
            self.max_row = sheet.nrows
            self.max_column = sheet.ncols

        def iter_rows(self, min_row=1, max_row=None, values_only=True):
            end = self._s.nrows if max_row is None else min(max_row, self._s.nrows)
            for r in range(min_row - 1, end):
                yield tuple(self._s.row_values(r))

    def __init__(self, xls_path: str):
        self._book = xlrd.open_workbook(xls_path)
        self.sheetnames = self._book.sheet_names()

    def __getitem__(self, name: str) -> "Sheet":
        return self.Sheet(self._book.sheet_by_name(name))


def _open_workbook(path: str):
    """Open .xls or .xlsx and return an object with sheetnames + __getitem__."""
    lower = path.lower()
    if lower.endswith(".xlsx"):
        return openpyxl.load_workbook(path, data_only=True)
    if lower.endswith(".xls"):
        return _XlsAdapter(path)
    raise ValueError(f"Unsupported workbook format: {path}")


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

        # Find a financial-statement workbook (.xlsx preferred, fall back to .xls)
        xlsx_path = xls_path = None
        for root, _, files in os.walk(tmp):
            for f in files:
                upper = f.upper()
                if upper.endswith(".XLSX") and not xlsx_path:
                    xlsx_path = os.path.join(root, f)
                elif upper.endswith(".XLS") and not xls_path:
                    xls_path = os.path.join(root, f)

        book_path = xlsx_path or xls_path
        if not book_path:
            print(f"[parse_zip] No XLS/XLSX found in {filename}")
            return None

        # Open workbook (unified adapter handles both formats)
        try:
            wb = _open_workbook(book_path)
        except Exception as e:
            print(f"[parse_zip] Failed to open workbook: {e}")
            return None

        pl_sheet = _find_pl_sheet(wb)
        if not pl_sheet:
            print(f"[parse_zip] No PL sheet in {filename}")
            return None

        ws = wb[pl_sheet]

        # Detect the unit divisor from the top of the sheet
        top_rows = list(ws.iter_rows(min_row=1, max_row=15, values_only=True))
        unit_divisor = _detect_unit_divisor(top_rows)

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
                    result.revenue = nums[0] / unit_divisor
                    result.revenue_prior = nums[1] / unit_divisor
                elif _is_netprofit_row(label) and result.net_profit is None:
                    result.net_profit = nums[0] / unit_divisor
                    result.net_profit_prior = nums[1] / unit_divisor
                elif _is_shareholder_profit_row(label) and result.shareholder_profit is None:
                    result.shareholder_profit = nums[0] / unit_divisor
                    result.shareholder_profit_prior = nums[1] / unit_divisor
                elif is_eps and result.eps is None:
                    # EPS is always in baht per share regardless of sheet unit
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
