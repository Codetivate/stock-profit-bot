"""
make_chart.py — Generate v5-style profit analysis chart.

Takes historical quarterly net profit data and produces a
world-class comparison chart matching the Mockup v5 design.
"""
import io
from dataclasses import dataclass
from typing import Dict, Optional

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch, Rectangle


# ═══ Full dark-mode palette ═══
# Everything sits on DARK_BG; containers (cards, table rows, summary
# panel) layer slightly lighter navys on top for depth.
DARK_BG       = "#050E1D"     # page background — near-black navy
NAVY          = "#0A2540"     # accent navy (table header, misc.)
MID_BLUE      = "#4A85D1"     # links, LATEST badge, icon
TEXT_DARK     = "#F1F5F9"     # main white-ish text
TEXT_MUTED    = "#8A9CB0"     # subdued labels / captions
GREEN         = "#22D081"     # bright emerald for positive
GREEN_BG      = "#0F2A1E"     # dark green card inner
GREEN_BORDER  = "#2D7A52"     # green card border
RED           = "#FF5656"     # bright red for negative
RED_BG        = "#2A0F11"     # dark red card inner
RED_BORDER    = "#7A2D34"     # red card border

# Table rows — three shades of navy so rows are distinguishable but
# still feel part of the same dark panel.
ROW_LATEST_DARK = "#162A44"
ROW_DARK        = "#0F1E34"
ROW_DARK_ALT    = "#132640"
DIVIDER_ON_DARK = "#2A3B55"
TEXT_ON_DARK    = "#F8FAFC"
TEXT_ON_DARK_MUTED = "#8A9CB0"

# Summary panel (narrative at the bottom)
SUMMARY_BG      = "#0E2038"
SUMMARY_BORDER  = "#1E3555"

# Legacy aliases kept so pre-existing code using them compiles.
LIGHT_BLUE_BG = ROW_LATEST_DARK
SURFACE       = ROW_DARK
GRID          = DIVIDER_ON_DARK
LINE_GRID     = DIVIDER_ON_DARK


@dataclass
class QuarterlyData:
    """Historical quarterly net profit for a single year."""
    year: int  # Thai year, e.g. 2568
    q1: Optional[float] = None
    q2: Optional[float] = None
    q3: Optional[float] = None
    q4: Optional[float] = None

    def sum(self) -> Optional[float]:
        if all(q is not None for q in [self.q1, self.q2, self.q3, self.q4]):
            return self.q1 + self.q2 + self.q3 + self.q4
        return None

    def get(self, q: str) -> Optional[float]:
        return getattr(self, q.lower(), None)


def make_chart(
    symbol: str,
    company_name: str,
    history: Dict[int, QuarterlyData],
    latest_year: int,
    latest_quarter: str,  # "Q1"/"Q2"/"Q3"/"Q4"
    report_date: str,     # "25 Feb 2026"
    period_label: str,    # "FY 2568 · Q4"
) -> bytes:
    """Generate a Mockup v5-style chart.

    Returns PNG bytes.
    """
    # Extract values
    latest_profit = history[latest_year].get(latest_quarter)
    if latest_profit is None:
        raise ValueError(f"No data for {latest_year} {latest_quarter}")

    # Previous quarter (same year)
    q_order = ["Q1", "Q2", "Q3", "Q4"]
    q_idx = q_order.index(latest_quarter)
    if q_idx > 0:
        prev_q_profit = history[latest_year].get(q_order[q_idx - 1])
        prev_q_label = f"{q_order[q_idx - 1]}/{latest_year}"
    else:
        prev_q_profit = history.get(latest_year - 1)
        prev_q_profit = prev_q_profit.q4 if prev_q_profit else None
        prev_q_label = f"Q4/{latest_year - 1}"

    # Same quarter, prior year (YoY)
    prev_y_profit = history.get(latest_year - 1)
    prev_y_profit = prev_y_profit.get(latest_quarter) if prev_y_profit else None
    prev_y_label = f"{latest_quarter}/{latest_year - 1}"

    # Calculate growth
    qoq = ((latest_profit - prev_q_profit) / prev_q_profit * 100) if prev_q_profit else None
    yoy = ((latest_profit - prev_y_profit) / prev_y_profit * 100) if prev_y_profit else None

    # ═══════════════════════════════════════════════════
    # Create figure — full dark canvas
    # ═══════════════════════════════════════════════════
    fig = plt.figure(figsize=(9, 13), facecolor=DARK_BG)

    # Header
    fig.text(0.06, 0.980, symbol, fontsize=22, fontweight="800", color=TEXT_DARK)
    fig.text(0.06, 0.963, company_name, fontsize=9, color=TEXT_MUTED, fontweight="400")
    fig.text(0.94, 0.980, period_label, fontsize=11, color=TEXT_DARK,
             fontweight="600", ha="right")
    fig.text(0.94, 0.964, report_date, fontsize=8.5, color=TEXT_MUTED,
             ha="right", fontweight="400")

    # (separator removed — the dark canvas already provides visual break)

    # ═══ Hero section label ═══
    fig.text(0.5, 0.935, f"THIS QUARTER'S NET PROFIT",
             fontsize=9.5, color=TEXT_DARK, ha="center",
             fontweight="800")

    # Hero number stays white — the direction is already shown by the
    # coloured QoQ / YoY cards below. Overloading the hero number with
    # a tint muddles the "how much did they earn this quarter" read.
    fig.text(0.5, 0.875, f"{latest_profit:,.2f}",
             fontsize=60, fontweight="800", color=TEXT_DARK, ha="center")
    fig.text(0.5, 0.855, "million baht",
             fontsize=10, color=TEXT_MUTED, ha="center",
             fontweight="500")

    # ═══ Two comparison cards ═══
    card_y = 0.72
    card_h = 0.10
    card_w = 0.40
    gap = 0.04
    left_x = (1 - 2 * card_w - gap) / 2

    # Card 1: vs last quarter
    ax_c1 = fig.add_axes([left_x, card_y, card_w, card_h])
    ax_c1.set_xlim(0, 1); ax_c1.set_ylim(0, 1); ax_c1.axis("off")

    if qoq is not None:
        c1_color  = GREEN if qoq >= 0 else RED
        c1_bg     = GREEN_BG if qoq >= 0 else RED_BG
        c1_border = GREEN_BORDER if qoq >= 0 else RED_BORDER
        c1_arrow  = "▲" if qoq >= 0 else "▼"

        ax_c1.add_patch(FancyBboxPatch((0.02, 0.02), 0.96, 0.96,
                                        boxstyle="round,pad=0.02",
                                        linewidth=1.4,
                                        facecolor=c1_bg,
                                        edgecolor=c1_border))
        ax_c1.text(0.5, 0.85, "VS LAST QUARTER",
                   fontsize=10, color=c1_color, fontweight="800", ha="center")
        ax_c1.text(0.5, 0.46, f"{c1_arrow} {qoq:+.1f}%",
                   fontsize=32, color=c1_color, fontweight="800", ha="center")
        ax_c1.text(0.5, 0.12, f"{prev_q_label}: {prev_q_profit:,.2f} MB",
                   fontsize=9, color=TEXT_MUTED, ha="center", fontweight="500")

    # Card 2: vs same quarter last year
    ax_c2 = fig.add_axes([left_x + card_w + gap, card_y, card_w, card_h])
    ax_c2.set_xlim(0, 1); ax_c2.set_ylim(0, 1); ax_c2.axis("off")

    if yoy is not None:
        c2_color  = GREEN if yoy >= 0 else RED
        c2_bg     = GREEN_BG if yoy >= 0 else RED_BG
        c2_border = GREEN_BORDER if yoy >= 0 else RED_BORDER
        c2_arrow  = "▲" if yoy >= 0 else "▼"

        ax_c2.add_patch(FancyBboxPatch((0.02, 0.02), 0.96, 0.96,
                                        boxstyle="round,pad=0.02",
                                        linewidth=1.4,
                                        facecolor=c2_bg,
                                        edgecolor=c2_border))
        ax_c2.text(0.5, 0.85, "VS SAME QUARTER LAST YEAR",
                   fontsize=10, color=c2_color, fontweight="800", ha="center")
        ax_c2.text(0.5, 0.46, f"{c2_arrow} {yoy:+.1f}%",
                   fontsize=32, color=c2_color, fontweight="800", ha="center")
        ax_c2.text(0.5, 0.12, f"{prev_y_label}: {prev_y_profit:,.2f} MB",
                   fontsize=9, color=TEXT_MUTED, ha="center", fontweight="500")

    # Years sorted for later use
    years_sorted = sorted(history.keys())

    # ═══ Quarterly breakdown table ═══
    fig.text(0.06, 0.695, "QUARTERLY NET PROFIT",
             fontsize=15, color=TEXT_DARK, fontweight="800")
    fig.text(0.38, 0.695, "(million baht)",
             fontsize=10, color=TEXT_MUTED, fontweight="500",
             style="italic")

    ax_tbl = fig.add_axes([0.03, 0.225, 0.94, 0.45])
    ax_tbl.set_xlim(0, 10)
    ax_tbl.set_ylim(0, 10)
    ax_tbl.axis("off")
    ax_tbl.set_facecolor(DARK_BG)

    # Get years sorted newest first for table
    table_years = sorted(history.keys(), reverse=True)
    # Show max 4 years
    if len(table_years) > 4:
        table_years = table_years[:4]

    # Table layout
    # Columns: Year | Q1 | Q2 | Q3 | Q4 | Sum
    col_positions = [0.7, 2.4, 4.1, 5.8, 7.5, 9.1]  # x centers
    col_headers = ["Year", "Q1", "Q2", "Q3", "Q4", "Full Year"]
    n_rows = len(table_years)

    # ─── Header row (navy background band) ───
    header_y = 9.0
    header_h = 0.9
    ax_tbl.add_patch(Rectangle((0.1, header_y - 0.1), 9.8, header_h,
                                facecolor=NAVY, edgecolor="none"))

    for i, (x, h) in enumerate(zip(col_positions, col_headers)):
        ax_tbl.text(x, header_y + 0.35, h,
                    fontsize=11, color="white", fontweight="800",
                    ha="center", va="center")

    # ─── Data rows ───
    # Each cell shows:
    #   Value (big)
    #   YoY% (green/red, vs same Q last year)
    #   QoQ% (gray/subtle, vs prev Q same year)
    row_h = 2.0
    row_gap = 0.1
    y_start = header_y - 0.4

    # Helper to get previous quarter (same year or rolling back)
    def get_prev_quarter(y: int, q: str):
        """Return value of quarter preceding (y, q) in timeline."""
        q_order_local = ["Q1", "Q2", "Q3", "Q4"]
        idx = q_order_local.index(q)
        if idx == 0:
            # Prev is Q4 of previous year
            prev_y_data = history.get(y - 1)
            return prev_y_data.get("Q4") if prev_y_data else None
        else:
            return history[y].get(q_order_local[idx - 1])

    for row_idx, y in enumerate(table_years):
        row_top = y_start - row_idx * row_h
        is_latest_row = (y == latest_year)

        # Dark-tone row background for better visual separation
        if is_latest_row:
            row_bg = ROW_LATEST_DARK
        elif row_idx % 2 == 0:
            row_bg = ROW_DARK
        else:
            row_bg = ROW_DARK_ALT

        ax_tbl.add_patch(Rectangle((0.1, row_top - row_h + row_gap),
                                     9.8, row_h - row_gap,
                                     facecolor=row_bg, edgecolor="none"))

        # Left accent stripe for latest year (bright for emphasis)
        if is_latest_row:
            ax_tbl.add_patch(Rectangle((0.1, row_top - row_h + row_gap),
                                         0.04, row_h - row_gap,
                                         facecolor=MID_BLUE, edgecolor="none"))

        # Year column — always light text on dark rows
        year_y_center = row_top - row_h/2 + row_gap/2
        if is_latest_row:
            ax_tbl.text(col_positions[0], year_y_center + 0.35,
                        f"FY{y}",
                        fontsize=14, color=TEXT_ON_DARK, fontweight="800",
                        ha="center", va="center")
            ax_tbl.text(col_positions[0], year_y_center - 0.30,
                        "LATEST",
                        fontsize=7.5, color=NAVY,
                        fontweight="800", ha="center", va="center",
                        bbox=dict(boxstyle="round,pad=0.3",
                                  facecolor=MID_BLUE, edgecolor="none"))
        else:
            ax_tbl.text(col_positions[0], year_y_center,
                        f"FY{y}",
                        fontsize=12, color=TEXT_ON_DARK, fontweight="700",
                        ha="center", va="center")

        # Q1-Q4 cells: each has 3 lines (value / YoY / QoQ)
        q_order = ["Q1", "Q2", "Q3", "Q4"]
        for q_idx, q in enumerate(q_order):
            val = history[y].get(q)
            x = col_positions[q_idx + 1]

            if val is None:
                ax_tbl.text(x, year_y_center, "—",
                            fontsize=11, color=TEXT_ON_DARK_MUTED,
                            ha="center", va="center")
                continue

            # ─── Line 1: Profit value (MAIN — light text on dark row) ───
            ax_tbl.text(x, year_y_center + 0.45,
                        f"{val:,.2f}",
                        fontsize=14 if is_latest_row else 12.5,
                        color=TEXT_ON_DARK,
                        fontweight="800" if is_latest_row else "700",
                        ha="center", va="center")

            # ─── Line 2: YoY — coloured text, no pill (cleaner on dark) ───
            prior_y_data = history.get(y - 1)
            prior_y_val = prior_y_data.get(q) if prior_y_data else None
            if prior_y_val is not None and prior_y_val > 0:
                yoy = (val - prior_y_val) / prior_y_val * 100
                yoy_c = GREEN if yoy >= 0 else RED
                ax_tbl.text(x, year_y_center - 0.05,
                            f"yoy  {yoy:+.1f}%",
                            fontsize=9.5, color=yoy_c,
                            fontweight="700",
                            ha="center", va="center")

            # ─── Line 3: QoQ — same styling as YoY, slightly smaller ───
            prev_q_val = get_prev_quarter(y, q)
            if prev_q_val is not None and prev_q_val > 0:
                qoq = (val - prev_q_val) / prev_q_val * 100
                qoq_c = GREEN if qoq >= 0 else RED
                ax_tbl.text(x, year_y_center - 0.55,
                            f"qoq  {qoq:+.1f}%",
                            fontsize=8.5, color=qoq_c,
                            fontweight="600",
                            ha="center", va="center")

        # Full Year total (right column) — bigger value + YoY pill
        total = history[y].sum()
        x = col_positions[5]
        if total is not None:
            # Value (larger to emphasize yearly total)
            ax_tbl.text(x, year_y_center + 0.35,
                        f"{total:,.2f}",
                        fontsize=15 if is_latest_row else 13,
                        color=TEXT_ON_DARK,
                        fontweight="800" if is_latest_row else "700",
                        ha="center", va="center")

            # Full-Year YoY — matching text style (no pill)
            prior_total = history.get(y - 1)
            prior_total = prior_total.sum() if prior_total else None
            if prior_total is not None and prior_total > 0:
                yoy_t = (total - prior_total) / prior_total * 100
                yoy_c = GREEN if yoy_t >= 0 else RED
                ax_tbl.text(x, year_y_center - 0.25,
                            f"yoy  {yoy_t:+.1f}%",
                            fontsize=10.5, color=yoy_c,
                            fontweight="700",
                            ha="center", va="center")

    # Column divider lines — subtle lighter tint over dark rows
    for x_divider in [1.45, 3.25, 4.95, 6.65, 8.30]:
        ax_tbl.plot([x_divider, x_divider],
                    [header_y - 0.1, y_start - n_rows * row_h + row_gap],
                    color=DIVIDER_ON_DARK, linewidth=0.4, alpha=0.6, zorder=0)

    # Legend under the table
    fig.text(0.5, 0.205,
             "yoy = vs same quarter last year    ·    qoq = vs previous quarter",
             fontsize=9, color=TEXT_MUTED, ha="center", fontweight="500")

    # Summary narrative card — CAGR across the years ingested
    table_years_sorted = sorted(table_years)
    annual_totals = [history[y].sum() for y in table_years_sorted
                     if history[y].sum() is not None]
    if len(annual_totals) >= 2:
        first_val = annual_totals[0]
        last_val = annual_totals[-1]
        years_span = len(annual_totals) - 1
        if years_span > 0 and first_val > 0:
            cagr = ((last_val / first_val) ** (1 / years_span) - 1) * 100
            cagr_color = GREEN if cagr >= 0 else RED

            # Card container
            ax_sum = fig.add_axes([0.05, 0.075, 0.90, 0.115])
            ax_sum.set_xlim(0, 1); ax_sum.set_ylim(0, 1); ax_sum.axis("off")
            ax_sum.add_patch(FancyBboxPatch(
                (0.0, 0.0), 1.0, 1.0,
                boxstyle="round,pad=0.02",
                linewidth=1.2,
                facecolor=SUMMARY_BG,
                edgecolor=SUMMARY_BORDER,
            ))
            # Blue circle icon on the left — drawn bar-chart mark
            # (three ascending rectangles) instead of an emoji so it
            # renders on fonts without glyph support.
            ax_sum.add_patch(plt.Circle((0.075, 0.5), 0.085,
                                         facecolor=MID_BLUE,
                                         edgecolor="none"))
            for i, h_bar in enumerate([0.04, 0.07, 0.10]):
                ax_sum.add_patch(Rectangle(
                    (0.050 + i*0.018, 0.45), 0.013, h_bar,
                    facecolor="white", edgecolor="none"))
            # Heading + narrative
            ax_sum.text(0.20, 0.73, "SUMMARY",
                        fontsize=12, color=MID_BLUE, fontweight="800",
                        ha="left", va="center")
            first_text = (f"Net profit grew from {first_val:,.2f} MB to "
                          f"{last_val:,.2f} MB over {years_span} years,")
            second_text_prefix = "an average increase of  "
            second_text_cagr = f"{cagr:+.1f}%"
            second_text_suffix = "  per year."
            ax_sum.text(0.20, 0.48, first_text,
                        fontsize=10, color=TEXT_DARK, fontweight="500",
                        ha="left", va="center")
            ax_sum.text(0.20, 0.25, second_text_prefix,
                        fontsize=10, color=TEXT_DARK, fontweight="500",
                        ha="left", va="center")
            # Place CAGR coloured chunk after the prefix
            ax_sum.text(0.445, 0.25, second_text_cagr,
                        fontsize=11, color=cagr_color, fontweight="800",
                        ha="left", va="center")
            ax_sum.text(0.525, 0.25, second_text_suffix,
                        fontsize=10, color=TEXT_DARK, fontweight="500",
                        ha="left", va="center")

    # Source line + AI disclaimer at the very bottom
    fig.text(0.5, 0.040,
             f"Source:  https://www.set.or.th/th/market/product/stock/quote/{symbol}/news",
             fontsize=8, color=MID_BLUE, ha="center", style="italic")
    fig.text(0.5, 0.020,
             "AI can make mistakes. Please double-check responses.",
             fontsize=8, color=TEXT_MUTED, ha="center", style="italic",
             fontweight="500")

    # Save to bytes — keep dark canvas across the saved PNG
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130,
                facecolor=DARK_BG, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return buf.getvalue()


if __name__ == "__main__":
    # Test with CPALL data
    history = {
        2568: QuarterlyData(2568, 7585.24, 6768.46, 6596.53, 7255.88),
        2567: QuarterlyData(2567, 6319.40, 6239.48, 5607.86, 7179.10),
        2566: QuarterlyData(2566, 4122.78, 4438.41, 4424.29, 5496.66),
        2565: QuarterlyData(2565, 3453.03, 3004.02, 3676.93, 3137.73),
    }

    png = make_chart(
        symbol="CPALL",
        company_name="CP All Public Company Limited",
        history=history,
        latest_year=2568,
        latest_quarter="Q4",
        report_date="25 Feb 2026",
        period_label="FY 2568  ·  Q4",
    )

    with open("test_chart_cpall.png", "wb") as f:
        f.write(png)
    print(f"Chart saved: {len(png):,} bytes -> test_chart_cpall.png")
