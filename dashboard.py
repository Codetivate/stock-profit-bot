"""
dashboard.py — Streamlit quarterly net-profit dashboard.

Run:
    streamlit run dashboard.py

Strict dark theme matching the design mockup. Colors follow the spec:
- Profit value is coloured by sign (green if >= 0, red if < 0).
- YoY / QoQ are coloured by their own sign, independent of the value.
- Layout mirrors the CPALL reference: header row, centred hero pill +
  number, two comparison cards, quarterly table, pi logo + source footer.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import streamlit as st

# ═══ Palette (strict — do not swap with close-but-different hex) ═══
BG_PRIMARY   = "#0B1220"
BG_SECONDARY = "#111827"
BG_HIGHLIGHT = "#1F2A44"
BORDER       = "#1F2937"
CARD         = "#0F172A"

TEXT_PRIMARY   = "#E5E7EB"
TEXT_SECONDARY = "#9CA3AF"
TEXT_MUTED     = "#6B7280"

PROFIT_POS = "#22C55E"
PROFIT_NEG = "#EF4444"
TREND_POS  = "#16A34A"
TREND_NEG  = "#DC2626"

ACCENT_BLUE = "#3B82F6"

# Green / red card fills (dark tints of the primary hues)
GREEN_CARD_BG     = "#0A2318"
GREEN_CARD_BORDER = "#166534"
RED_CARD_BG       = "#2A0A0D"
RED_CARD_BORDER   = "#7F1D1D"

LOGO_PATH = Path(__file__).parent / "assets" / "logo.png"


# ═══ Data model ═══
@dataclass
class QuarterlyData:
    year: int
    q1: Optional[float] = None
    q2: Optional[float] = None
    q3: Optional[float] = None
    q4: Optional[float] = None

    def get(self, q: str) -> Optional[float]:
        return getattr(self, q.lower(), None)

    def sum(self) -> Optional[float]:
        if all(v is not None for v in (self.q1, self.q2, self.q3, self.q4)):
            return self.q1 + self.q2 + self.q3 + self.q4
        return None


# Default sample (matches the mockup — CPALL Q4/68)
DEFAULT_HISTORY: Dict[int, QuarterlyData] = {
    2568: QuarterlyData(2568, 7585.24, 6768.45, 6596.53, 7255.88),
    2567: QuarterlyData(2567, 6319.40, 6239.48, 5607.86, 7179.10),
    2566: QuarterlyData(2566, 4122.78, 4438.41, 4424.29, 5496.66),
    2565: QuarterlyData(2565, 3453.03, 3004.02, 3676.93, 3137.73),
    2564: QuarterlyData(2564, 2599.05, 2189.70, 1493.01, 6703.72),
}
DEFAULT_SYMBOL    = "CPALL"
DEFAULT_COMPANY   = "CP All Public Company Limited"
DEFAULT_YEAR      = 2568
DEFAULT_QUARTER   = "Q4"
DEFAULT_REPORT_DT = "25 Feb 2026"


# ═══ Helpers ═══
def color_for_value(v: float) -> str:
    """Profit colour — green for >= 0, red otherwise."""
    return PROFIT_POS if v >= 0 else PROFIT_NEG


def color_for_trend(v: float) -> str:
    """Trend colour — growth green vs decline red."""
    return TREND_POS if v >= 0 else TREND_NEG


def pct_change(now: float, prior: Optional[float]) -> Optional[float]:
    """Percentage change using abs(prior) so flips from loss → profit
    register as a positive number, not a misleading negative."""
    if prior is None or prior == 0:
        return None
    return (now - prior) / abs(prior) * 100


def get_prev_quarter(history: Dict[int, QuarterlyData], y: int, q: str) -> Optional[float]:
    order = ["Q1", "Q2", "Q3", "Q4"]
    idx = order.index(q)
    if idx == 0:
        prev = history.get(y - 1)
        return prev.q4 if prev else None
    return history[y].get(order[idx - 1])


def load_logo_b64() -> Optional[str]:
    if LOGO_PATH.exists():
        try:
            return base64.b64encode(LOGO_PATH.read_bytes()).decode()
        except OSError:
            return None
    return None


def fmt(v: Optional[float]) -> str:
    return f"{v:,.2f}" if v is not None else "—"


# ═══ HTML builders ═══
def build_header(symbol: str, company: str, period: str, date: str) -> str:
    return f"""
    <div class="hdr">
      <div class="hdr-l">
        <div class="sym">{symbol}</div>
        <div class="co">{company}</div>
      </div>
      <div class="hdr-r">
        <div class="period">{period}</div>
        <div class="date">{date}</div>
      </div>
    </div>
    """


def build_hero(latest: float, quarter: str, thai_year: int) -> str:
    color = color_for_value(latest)
    pill_label = f"{quarter}/{str(thai_year)[-2:]}"
    return f"""
    <div class="hero">
      <div class="pill" style="border-color:{color};color:{color};">{pill_label}</div>
      <div class="big" style="color:{color};">{fmt(latest)}</div>
      <div class="cap">million baht</div>
    </div>
    """


def build_compare_card(
    title: str, pct: Optional[float], prior_label: str, prior_value: Optional[float]
) -> str:
    if pct is None:
        return f"""
        <div class="card" style="border-color:{BORDER};background:{CARD};">
          <div class="card-title" style="color:{TEXT_SECONDARY};">{title}</div>
          <div class="card-pct" style="color:{TEXT_MUTED};">—</div>
          <div class="card-prior">n/a</div>
        </div>
        """
    up = pct >= 0
    color  = TREND_POS if up else TREND_NEG
    bg     = GREEN_CARD_BG if up else RED_CARD_BG
    border = GREEN_CARD_BORDER if up else RED_CARD_BORDER
    arrow  = "▲" if up else "▼"
    return f"""
    <div class="card" style="border-color:{border};background:{bg};">
      <div class="card-title" style="color:{color};">{title}</div>
      <div class="card-pct" style="color:{color};">{arrow} {pct:+.1f}%</div>
      <div class="card-prior">{prior_label}: {fmt(prior_value)} MB</div>
    </div>
    """


def build_table_cell(
    val: Optional[float],
    yoy: Optional[float],
    qoq: Optional[float],
) -> str:
    if val is None:
        return f'<div class="tc tc-empty">—</div>'
    parts = [f'<div class="tc-val" style="color:{color_for_value(val)};">{fmt(val)}</div>']
    if yoy is not None:
        parts.append(
            f'<div class="tc-sub" style="color:{color_for_trend(yoy)};">'
            f'yoy&nbsp;&nbsp;{yoy:+.1f}%</div>'
        )
    if qoq is not None:
        parts.append(
            f'<div class="tc-sub tc-sub-sm" style="color:{color_for_trend(qoq)};">'
            f'qoq&nbsp;&nbsp;{qoq:+.1f}%</div>'
        )
    return f'<div class="tc">{"".join(parts)}</div>'


def build_fy_cell(total: Optional[float], yoy: Optional[float]) -> str:
    if total is None:
        return f'<div class="tc tc-empty">—</div>'
    parts = [
        f'<div class="tc-val tc-val-fy" style="color:{color_for_value(total)};">{fmt(total)}</div>'
    ]
    if yoy is not None:
        parts.append(
            f'<div class="tc-sub" style="color:{color_for_trend(yoy)};">'
            f'yoy&nbsp;&nbsp;{yoy:+.1f}%</div>'
        )
    return f'<div class="tc">{"".join(parts)}</div>'


def build_table(
    history: Dict[int, QuarterlyData],
    latest_year: int,
) -> str:
    years = sorted(history.keys(), reverse=True)[:6]

    head_cols = ["Year", "Q1", "Q2", "Q3", "Q4", "Full Year"]
    head = "".join(f'<div class="th">{c}</div>' for c in head_cols)

    rows_html = []
    for y in years:
        row = history[y]
        is_latest = y == latest_year
        row_cls = "row row-latest" if is_latest else "row"

        # Year column
        year_label = (
            f'<div class="tc tc-year">'
            f'<div class="fy">FY{y}</div>'
            + ('<div class="latest-tag">LATEST</div>' if is_latest else '')
            + '</div>'
        )

        # Q1-Q4 cells
        cells = [year_label]
        for q in ("Q1", "Q2", "Q3", "Q4"):
            val = row.get(q)
            prior_y = history.get(y - 1)
            prior_y_val = prior_y.get(q) if prior_y else None
            yoy = pct_change(val, prior_y_val) if val is not None else None
            prev_q = get_prev_quarter(history, y, q)
            qoq = pct_change(val, prev_q) if val is not None else None
            cells.append(build_table_cell(val, yoy, qoq))

        # Full year
        total = row.sum()
        prior_total = history.get(y - 1)
        prior_total_val = prior_total.sum() if prior_total else None
        fy_yoy = pct_change(total, prior_total_val) if total is not None else None
        cells.append(build_fy_cell(total, fy_yoy))

        rows_html.append(f'<div class="{row_cls}">{"".join(cells)}</div>')

    return f"""
    <div class="tbl">
      <div class="thead">{head}</div>
      <div class="tbody">{''.join(rows_html)}</div>
    </div>
    """


def build_footer(symbol: str, logo_b64: Optional[str]) -> str:
    if logo_b64:
        logo_html = (
            f'<img class="logo-img" alt="pi" '
            f'src="data:image/png;base64,{logo_b64}"/>'
            f'<span class="logo-tag">A8/4</span>'
        )
    else:
        logo_html = (
            '<span class="logo-text">pi</span>'
            '<span class="logo-sep">|</span>'
            '<span class="logo-tag">A8/4</span>'
        )
    src_url = f"https://www.set.or.th/th/market/product/stock/quote/{symbol}/news"
    return f"""
    <div class="footer">
      <div class="brand">{logo_html}</div>
      <div class="src">
        <div class="src-link">Source:&nbsp;&nbsp;{src_url}</div>
        <div class="src-note">AI can make mistakes. Please double-check responses.</div>
      </div>
    </div>
    """


# ═══ Page ═══
def render_dashboard(
    symbol: str,
    company: str,
    history: Dict[int, QuarterlyData],
    latest_year: int,
    latest_quarter: str,
    report_date: str,
) -> None:
    # Latest profit + comparison bases
    latest = history[latest_year].get(latest_quarter)
    prev_q = get_prev_quarter(history, latest_year, latest_quarter)
    prev_y_row = history.get(latest_year - 1)
    prev_y = prev_y_row.get(latest_quarter) if prev_y_row else None

    qoq_pct = pct_change(latest, prev_q) if latest is not None else None
    yoy_pct = pct_change(latest, prev_y) if latest is not None else None

    # Labels for the comparison cards' "prior" line
    order = ["Q1", "Q2", "Q3", "Q4"]
    q_idx = order.index(latest_quarter)
    prev_q_label = (
        f"{order[q_idx - 1]}/{latest_year}" if q_idx > 0
        else f"Q4/{latest_year - 1}"
    )
    prev_y_label = f"{latest_quarter}/{latest_year - 1}"

    period = f"FY {latest_year}  ·  {latest_quarter}"
    logo_b64 = load_logo_b64()

    html = f"""
    <div class="dash">
      {build_header(symbol, company, period, report_date)}
      {build_hero(latest, latest_quarter, latest_year)}
      <div class="cards">
        {build_compare_card("VS LAST QUARTER", qoq_pct, prev_q_label, prev_q)}
        {build_compare_card("VS SAME QUARTER LAST YEAR", yoy_pct, prev_y_label, prev_y)}
      </div>
      <div class="section-hdr">
        <span class="section-title">QUARTERLY NET PROFIT</span>
        <span class="section-sub">(million baht)</span>
      </div>
      {build_table(history, latest_year)}
      {build_footer(symbol, logo_b64)}
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


# ═══ CSS ═══
CSS = f"""
<style>
.stApp {{
    background-color: {BG_PRIMARY};
}}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 2rem; max-width: 920px; }}

.dash {{
    color: {TEXT_PRIMARY};
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    padding: 8px 4px;
}}

/* ─── Header ─── */
.hdr {{ display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:24px; }}
.sym {{ font-size:28px; font-weight:800; color:{TEXT_PRIMARY}; letter-spacing:0.5px; }}
.co  {{ font-size:12px; color:{TEXT_SECONDARY}; margin-top:2px; font-weight:400; }}
.hdr-r {{ text-align:right; }}
.period {{ font-size:14px; font-weight:600; color:{TEXT_PRIMARY}; }}
.date   {{ font-size:11px; color:{TEXT_SECONDARY}; margin-top:2px; }}

/* ─── Hero ─── */
.hero {{ text-align:center; margin:24px 0; }}
.pill {{
    display:inline-block; padding:6px 18px;
    border:1.5px solid; border-radius:6px;
    font-size:16px; font-weight:800; letter-spacing:0.5px;
    background:{BG_PRIMARY}; margin-bottom:8px;
}}
.big {{
    font-size:78px; font-weight:800; line-height:1.05;
    margin:4px 0; letter-spacing:-1px;
}}
.cap {{ font-size:13px; color:{TEXT_SECONDARY}; margin-top:-4px; font-weight:500; }}

/* ─── Comparison cards ─── */
.cards {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin:24px 0; }}
.card {{
    border:1.5px solid; border-radius:10px;
    padding:18px 20px; text-align:center;
}}
.card-title {{ font-size:12px; font-weight:800; letter-spacing:0.5px; margin-bottom:6px; }}
.card-pct   {{ font-size:34px; font-weight:800; margin:4px 0 10px; }}
.card-prior {{ font-size:12px; color:{TEXT_SECONDARY}; font-weight:500; border-top:1px dotted {BORDER}; padding-top:10px; }}

/* ─── Section header ─── */
.section-hdr {{ margin:28px 0 10px; }}
.section-title {{ font-size:17px; font-weight:800; color:{TEXT_PRIMARY}; letter-spacing:0.3px; }}
.section-sub   {{ font-size:12px; color:{TEXT_SECONDARY}; font-style:italic; margin-left:8px; }}

/* ─── Table ─── */
.tbl   {{ border-radius:8px; overflow:hidden; }}
.thead {{
    display:grid;
    grid-template-columns:1fr 1fr 1fr 1fr 1fr 1.1fr;
    background:{BG_HIGHLIGHT}; padding:12px 0;
}}
.th    {{ text-align:center; font-size:13px; font-weight:800; color:{TEXT_PRIMARY}; }}
.tbody {{ display:flex; flex-direction:column; gap:4px; margin-top:4px; }}

.row {{
    display:grid;
    grid-template-columns:1fr 1fr 1fr 1fr 1fr 1.1fr;
    background:{BG_SECONDARY};
    padding:16px 0; min-height:88px; align-items:center;
    border-left:3px solid transparent;
}}
.row-latest {{
    background:{BG_HIGHLIGHT};
    border-left-color:{ACCENT_BLUE};
}}

.tc {{ display:flex; flex-direction:column; justify-content:center; align-items:center; text-align:center; padding:0 8px; gap:3px; }}
.tc-empty {{ color:{TEXT_MUTED}; font-size:14px; }}
.tc-val {{ font-size:14px; font-weight:700; }}
.tc-val-fy {{ font-size:15px; font-weight:800; }}
.tc-sub {{ font-size:10.5px; font-weight:700; letter-spacing:0.2px; }}
.tc-sub-sm {{ font-size:10px; font-weight:600; }}

.tc-year {{ gap:6px; }}
.fy {{ font-size:14px; font-weight:800; color:{TEXT_PRIMARY}; }}
.latest-tag {{
    display:inline-block; background:{ACCENT_BLUE}; color:#fff;
    font-size:9px; font-weight:800; padding:3px 10px;
    border-radius:5px; letter-spacing:0.5px;
}}

/* ─── Footer ─── */
.footer {{
    display:flex; justify-content:space-between; align-items:center;
    margin-top:24px; padding-top:14px; border-top:1px solid {BORDER};
}}
.brand {{ display:flex; align-items:center; gap:8px; color:{TEXT_PRIMARY}; }}
.logo-img  {{ height:22px; width:auto; opacity:0.85; }}
.logo-text {{ font-family:"Georgia",serif; font-style:italic; font-size:20px; font-weight:300; }}
.logo-sep  {{ color:{TEXT_SECONDARY}; font-size:16px; font-weight:200; }}
.logo-tag  {{ font-size:12px; font-weight:400; letter-spacing:0.5px; color:{TEXT_PRIMARY}; }}

.src {{ text-align:right; }}
.src-link {{ font-size:10px; color:{ACCENT_BLUE}; font-style:italic; }}
.src-note {{ font-size:10px; color:{TEXT_MUTED}; font-style:italic; margin-top:2px; }}
</style>
"""


def main() -> None:
    st.set_page_config(
        page_title="Quarterly Net Profit",
        page_icon="📊",
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    st.markdown(CSS, unsafe_allow_html=True)
    render_dashboard(
        symbol=DEFAULT_SYMBOL,
        company=DEFAULT_COMPANY,
        history=DEFAULT_HISTORY,
        latest_year=DEFAULT_YEAR,
        latest_quarter=DEFAULT_QUARTER,
        report_date=DEFAULT_REPORT_DT,
    )


if __name__ == "__main__":
    main()
