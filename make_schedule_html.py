"""make_schedule_html.py — render a per-symbol filing-schedule grid to PNG.

Companion image to the existing quarterly-profit chart. Shows when each
quarter's financial statement was actually published to SET, going as
far back as we have raw metadata (typically 2564 onward).

Layout: years down, periods across (Q1, H1, 9M, FY). Each cell shows
the Thai weekday + day + month abbreviation + time-of-day. Latest cell
is highlighted with the Pi mint stripe to match the chart.

Usage as CLI (saves to data/derived/_preview/SCHEDULE_<SYM>.png):

    python make_schedule_html.py SCB
"""
from __future__ import annotations

import base64
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from playwright.sync_api import sync_playwright

from src.ingest.zip_downloader import safe_symbol_dir


LOGO_PATH = Path(__file__).parent / "assets" / "logo.png"
QR_PATH = Path(__file__).parent / "assets" / "qr.png"


def _logo_data_url() -> Optional[str]:
    """Inline the Pi A8/4 logo as a base64 data: URL so the headless
    browser doesn't have to fetch a local file across the file:// origin."""
    if LOGO_PATH.exists():
        try:
            return (
                "data:image/png;base64,"
                + base64.b64encode(LOGO_PATH.read_bytes()).decode()
            )
        except OSError:
            return None
    return None


def _qr_data_url() -> Optional[str]:
    """Inline the Join-Group QR (assets/qr.png) for the footer."""
    if QR_PATH.exists():
        try:
            return (
                "data:image/png;base64,"
                + base64.b64encode(QR_PATH.read_bytes()).decode()
            )
        except OSError:
            return None
    return None


_THAI_WEEKDAYS = ["จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา."]
_THAI_MONTHS = [
    "", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.",
]
_PERIODS = ["Q1", "H1", "9M", "FY"]
_PERIOD_LABELS = {"Q1": "Q1", "H1": "Q2 (H1)", "9M": "Q3 (9M)", "FY": "FY"}


def _format_cell(iso_dt: str) -> str:
    """Format a filing's full ISO datetime into a compact two-line cell.

    First line: Thai weekday abbrev + day/month (e.g. "อ. 13/5/67").
    Second line: 24-hour time (e.g. "18:47").
    """
    try:
        d = datetime.strptime(iso_dt[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return iso_dt
    yy = (d.year + 543) % 100
    line1 = (
        f"{_THAI_WEEKDAYS[d.weekday()]} "
        f"{d.day} {_THAI_MONTHS[d.month]} {yy:02d}"
    )
    line2 = f"{d.hour:02d}:{d.minute:02d} น."
    return f'<div class="cell-date">{line1}</div><div class="cell-time">{line2}</div>'


def load_schedule(symbol: str) -> Dict[Tuple[int, str], str]:
    """Walk data/raw/{SYMBOL}/financials/*/*/metadata.json and return
    {(thai_year, period): filing_datetime_iso}."""
    root = Path("data/raw") / safe_symbol_dir(symbol) / "financials"
    if not root.exists():
        return {}
    out: Dict[Tuple[int, str], str] = {}
    for year_dir in sorted(root.iterdir()):
        if not year_dir.is_dir():
            continue
        try:
            year = int(year_dir.name)
        except ValueError:
            continue
        for period_dir in sorted(year_dir.iterdir()):
            if not period_dir.is_dir():
                continue
            meta = period_dir / "metadata.json"
            if not meta.exists():
                continue
            d = json.loads(meta.read_text(encoding="utf-8"))
            iso = d.get("filing_datetime") or d.get("filing_date") or ""
            if iso:
                out[(year, period_dir.name)] = iso
    return out


def _build_html(symbol: str, schedule: Dict[Tuple[int, str], str]) -> str:
    if not schedule:
        years: List[int] = []
    else:
        years = sorted({y for (y, _) in schedule.keys()}, reverse=True)
    # Pick the most recent (year, period) by filing_datetime to highlight.
    latest_key = (
        max(schedule.items(), key=lambda kv: kv[1])[0]
        if schedule else None
    )

    rows_html: List[str] = []
    for y in years:
        cells = []
        for p in _PERIODS:
            iso = schedule.get((y, p))
            is_latest = latest_key == (y, p)
            cls = "tc tc-filed" + (" tc-latest" if is_latest else "")
            if iso:
                cells.append(f'<div class="{cls}">{_format_cell(iso)}</div>')
            else:
                cells.append('<div class="tc tc-empty">—</div>')
        rows_html.append(
            f'<div class="row">'
            f'<div class="tc tc-year">{y}</div>'
            f'{"".join(cells)}'
            f"</div>"
        )

    head_cells = "".join(
        f'<div class="tc tc-head">{_PERIOD_LABELS[p]}</div>' for p in _PERIODS
    )

    logo_url = _logo_data_url()
    logo_html = (
        f'<img src="{logo_url}" alt="pi A8/4"/>' if logo_url
        else '<span class="logo-fallback">pi A8/4</span>'
    )

    qr_url = _qr_data_url()
    if qr_url:
        qr_html = (
            f'<div class="ftr-qr">'
            f'<img class="qr-img" src="{qr_url}" alt="Get Updated QR"/>'
            f'<div class="qr-label">Get Updated</div>'
            f'</div>'
        )
    else:
        qr_html = (
            '<div class="ftr-qr placeholder">'
            '<div class="qr-icon">▦</div><div>QR Code</div>'
            '</div>'
        )

    return f"""<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>{symbol} · ตารางประกาศงบ</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Sarabun:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #F5F7FA;
    --card: #FFFFFF;
    --border: #E5E7EB;
    --text: #0F172A;
    --muted: #6B7280;
    --navy: #002E60;
    --mint: #21CE99;
    --mint-bg: #E6F4F1;
    --teal: #167579;
  }}
  body {{
    margin: 0; padding: 48px;
    background: var(--bg); color: var(--text);
    font-family: 'Inter', 'Sarabun', system-ui, sans-serif;
  }}
  .chart {{
    width: 1620px; max-width: 100%;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 28px;
    padding: 36px 44px 28px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  }}
  .hdr {{
    background: var(--navy);
    color: #fff;
    padding: 28px 36px;
    border-radius: 18px;
    margin-bottom: 18px;
    display: flex; justify-content: space-between; align-items: baseline;
  }}
  .hdr-title {{ font-size: 36px; font-weight: 700; letter-spacing: 0.5px; }}
  .hdr-sub   {{ font-size: 18px; opacity: 0.78; }}
  .tbl  {{ padding: 0; }}
  .row, .head {{ display: grid; grid-template-columns: 160px repeat(4, 1fr); gap: 12px; }}
  .head {{ margin-bottom: 8px; }}
  .row  {{ margin-top: 12px; align-items: stretch; }}
  .tc {{
    border-radius: 14px;
    padding: 22px 14px;
    text-align: center;
    font-size: 18px;
    line-height: 1.4;
  }}
  .tc-head {{
    background: transparent;
    color: var(--muted);
    font-weight: 600;
    font-size: 16px;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    padding-bottom: 6px;
  }}
  .tc-year {{
    background: #F1F5F9;
    color: var(--navy);
    font-weight: 700;
    font-size: 28px;
    display: flex; align-items: center; justify-content: center;
  }}
  .tc-filed {{
    background: #FFFFFF;
    border: 1px solid var(--border);
  }}
  .tc-empty {{
    background: transparent;
    border: 1px dashed var(--border);
    color: var(--muted);
    display: flex; align-items: center; justify-content: center;
    font-size: 28px;
  }}
  .tc-latest {{
    background: var(--mint-bg);
    border: 1px solid var(--mint);
    box-shadow: 0 0 0 2px rgba(33, 206, 153, 0.18);
  }}
  .cell-date {{ font-weight: 700; color: var(--navy); font-size: 26px; letter-spacing: 0.2px; }}
  .cell-time {{ color: var(--muted); font-size: 19px; margin-top: 7px; font-variant-numeric: tabular-nums; font-weight: 500; }}

  /* ─── Footer (matches make_chart_html.py) ─── */
  .ftr {{
    display: grid;
    grid-template-columns: auto 1fr auto;
    align-items: center;
    gap: 24px;
    margin-top: 22px; padding-top: 14px;
  }}
  .ftr-logo {{ display: flex; align-items: center; gap: 16px; }}
  .ftr-logo img {{ height: 170px; width: auto; opacity: 0.95; filter: invert(1); }}
  .ftr-logo .logo-fallback {{
    font-family: Georgia, serif; font-style: italic;
    font-size: 68px; font-weight: 300; color: var(--text);
  }}
  .ftr-meta {{
    display: flex; flex-direction: column; gap: 4px;
    align-items: center; text-align: center;
  }}
  .ftr-meta .src      {{ font-size: 20px; color: var(--muted); font-style: italic; font-weight: 500; }}
  .ftr-meta .disclaim {{ font-size: 18px; color: var(--muted); font-style: italic; }}
  .ftr-qr {{
    width: 170px; height: 170px;
    border-radius: 14px;
    background: #FFFFFF;
    border: 1px solid var(--border);
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    gap: 4px;
    padding: 6px;
    box-sizing: border-box;
  }}
  .ftr-qr.placeholder {{
    border: 3px dashed var(--border);
    color: var(--muted);
    font-size: 16px;
    text-align: center;
    line-height: 1.3;
  }}
  .ftr-qr .qr-img   {{ width: 122px; height: 122px; image-rendering: pixelated; }}
  .ftr-qr .qr-label {{ font-size: 22px; font-weight: 800; color: var(--navy); letter-spacing: 0.5px; }}
  .ftr-qr .qr-icon  {{ font-size: 40px; margin-bottom: 6px; opacity: 0.6; }}
</style>
</head>
<body>
<div class="chart">
  <div class="hdr">
    <div class="hdr-title">{symbol} · วันที่ประกาศงบ</div>
  </div>
  <div class="tbl">
    <div class="head">
      <div class="tc tc-head">ปี (พ.ศ.)</div>
      {head_cells}
    </div>
    {"".join(rows_html) if rows_html else '<div style="padding:40px;text-align:center;color:var(--muted)">ไม่มีข้อมูลในระบบ</div>'}
  </div>
  <div class="ftr">
    <div class="ftr-logo">{logo_html}</div>
    <div class="ftr-meta">
      <div class="src">Source:&nbsp;&nbsp;https://www.set.or.th/th/market/product/stock/quote/{symbol}/news</div>
      <div class="disclaim">AI can make mistakes. Please double-check responses.</div>
    </div>
    {qr_html}
  </div>
</div>
</body>
</html>"""


def make_schedule(symbol: str) -> bytes:
    """Render the schedule grid for *symbol* to PNG bytes."""
    schedule = load_schedule(symbol)
    html = _build_html(symbol, schedule)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-web-security"])
        page = browser.new_page(
            viewport={"width": 1800, "height": 1600},
            device_scale_factor=2,
        )
        page.set_content(html, wait_until="networkidle", timeout=15000)
        try:
            page.wait_for_function(
                "document.fonts && document.fonts.ready", timeout=5000
            )
        except Exception:
            pass
        element = page.query_selector(".chart")
        png = element.screenshot(type="png", omit_background=False)
        browser.close()
    return png


if __name__ == "__main__":
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "TOA"
    out_dir = Path("data/derived/_preview")
    out_dir.mkdir(parents=True, exist_ok=True)
    png = make_schedule(sym)
    out = out_dir / f"SCHEDULE_{sym}.png"
    out.write_bytes(png)
    html_out = out_dir / f"SCHEDULE_{sym}.html"
    html_out.write_text(
        _build_html(sym, load_schedule(sym)), encoding="utf-8"
    )
    print(f"wrote {out} ({len(png)} bytes)")
    print(f"wrote {html_out} (preview in browser)")
