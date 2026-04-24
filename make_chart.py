"""
make_chart.py — Generate v5-style profit analysis chart.

Takes historical quarterly net profit data and produces a
world-class comparison chart matching the Mockup v5 design.
"""
import io
from dataclasses import dataclass
from typing import Dict, Optional

# Force non-interactive backend BEFORE pyplot import — the bot server
# renders charts from worker threads; the default Tk-based backend
# fails in that scenario with "main thread is not in main loop".
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402


# ═══ Official palette ═══
# Backgrounds
BG_PRIMARY     = "#0B1220"   # main canvas
BG_SECONDARY   = "#111827"   # table rows
BG_HIGHLIGHT   = "#1F2A44"   # latest row / table header band
BORDER_DIV     = "#1F2937"   # dividers, borders
CARD_SURFACE   = "#0F172A"   # panels / containers

# Text
TEXT_PRIMARY   = "#E5E7EB"   # main numbers, titles
TEXT_SECONDARY = "#9CA3AF"   # labels, subtitles
TEXT_MUTED     = "#6B7280"   # footnotes, source

# Profit / growth
PROFIT_GREEN   = "#22C55E"   # net profit numbers (>= 0)
GROWTH_GREEN   = "#16A34A"   # YoY / QoQ positive
GREEN_GLOW     = "#4ADE80"   # optional accent

# Loss / decline
LOSS_RED       = "#EF4444"   # net profit numbers (< 0)
DECLINE_RED    = "#DC2626"   # YoY / QoQ negative

# Accents
ACCENT_BLUE    = "#3B82F6"   # LATEST tag
ACCENT_CYAN    = "#06B6D4"
ACCENT_PURPLE  = "#8B5CF6"

# Card inner fills for QoQ / YoY cards — dark tints derived from the
# growth/decline hues so they read as green/red panels without being
# loud on a dark canvas.
GREEN_CARD_BG     = "#0A2318"
GREEN_CARD_BORDER = "#166534"
RED_CARD_BG       = "#2A0A0D"
RED_CARD_BORDER   = "#7F1D1D"

# Legacy aliases kept so any remaining references still resolve.
DARK_BG            = BG_PRIMARY
NAVY               = BG_HIGHLIGHT
MID_BLUE           = ACCENT_BLUE
TEXT_DARK          = TEXT_PRIMARY
TEXT_ON_DARK       = TEXT_PRIMARY
TEXT_ON_DARK_MUTED = TEXT_SECONDARY
ROW_LATEST_DARK    = BG_HIGHLIGHT
ROW_DARK           = BG_SECONDARY
ROW_DARK_ALT       = BG_SECONDARY
DIVIDER_ON_DARK    = BORDER_DIV
GREEN              = PROFIT_GREEN
RED                = LOSS_RED
GREEN_BG           = GREEN_CARD_BG
GREEN_BORDER       = GREEN_CARD_BORDER
RED_BG             = RED_CARD_BG
RED_BORDER         = RED_CARD_BORDER


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
    fig = plt.figure(figsize=(9, 13), facecolor=BG_PRIMARY)

    # Header
    fig.text(0.06, 0.980, symbol, fontsize=22, fontweight="800", color=TEXT_PRIMARY)
    fig.text(0.06, 0.963, company_name, fontsize=9, color=TEXT_SECONDARY, fontweight="400")
    fig.text(0.94, 0.980, period_label, fontsize=11, color=TEXT_PRIMARY,
             fontweight="600", ha="right")
    fig.text(0.94, 0.964, report_date, fontsize=8.5, color=TEXT_SECONDARY,
             ha="right", fontweight="400")

    # (separator removed — the dark canvas already provides visual break)

    # ═══ Hero section: green-bordered "Q4/68" pill ═══
    # Pill shows quarter + Thai year short form (2568 -> 68).
    hero_color = PROFIT_GREEN if latest_profit >= 0 else LOSS_RED
    pill_label = f"{latest_quarter}/{str(latest_year)[-2:]}"
    pill_w, pill_h = 0.11, 0.028
    pill_x = 0.5 - pill_w / 2
    pill_y = 0.920
    ax_pill = fig.add_axes([pill_x, pill_y, pill_w, pill_h])
    ax_pill.set_xlim(0, 1); ax_pill.set_ylim(0, 1); ax_pill.axis("off")
    ax_pill.add_patch(FancyBboxPatch(
        (0.06, 0.08), 0.88, 0.84,
        boxstyle="round,pad=0.02",
        linewidth=1.4,
        facecolor=BG_PRIMARY,
        edgecolor=hero_color,
    ))
    ax_pill.text(0.5, 0.5, pill_label,
                 fontsize=12, color=hero_color, fontweight="700",
                 ha="center", va="center")

    # Hero number tinted by sign — green for profit, red for loss.
    fig.text(0.5, 0.875, f"{latest_profit:,.2f}",
             fontsize=60, fontweight="800", color=hero_color, ha="center")
    fig.text(0.5, 0.855, "million baht",
             fontsize=10, color=TEXT_SECONDARY, ha="center",
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
        c1_color  = GROWTH_GREEN if qoq >= 0 else DECLINE_RED
        c1_bg     = GREEN_CARD_BG if qoq >= 0 else RED_CARD_BG
        c1_border = GREEN_CARD_BORDER if qoq >= 0 else RED_CARD_BORDER
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
                   fontsize=9, color=TEXT_SECONDARY, ha="center", fontweight="500")

    # Card 2: vs same quarter last year
    ax_c2 = fig.add_axes([left_x + card_w + gap, card_y, card_w, card_h])
    ax_c2.set_xlim(0, 1); ax_c2.set_ylim(0, 1); ax_c2.axis("off")

    if yoy is not None:
        c2_color  = GROWTH_GREEN if yoy >= 0 else DECLINE_RED
        c2_bg     = GREEN_CARD_BG if yoy >= 0 else RED_CARD_BG
        c2_border = GREEN_CARD_BORDER if yoy >= 0 else RED_CARD_BORDER
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
                   fontsize=9, color=TEXT_SECONDARY, ha="center", fontweight="500")

    # Years sorted for later use
    years_sorted = sorted(history.keys())

    # ═══ Quarterly breakdown table ═══
    fig.text(0.06, 0.695, "QUARTERLY NET PROFIT",
             fontsize=15, color=TEXT_PRIMARY, fontweight="800")
    fig.text(0.38, 0.695, "(million baht)",
             fontsize=10, color=TEXT_SECONDARY, fontweight="500",
             style="italic")

    ax_tbl = fig.add_axes([0.03, 0.080, 0.94, 0.61])
    ax_tbl.set_xlim(0, 10)
    ax_tbl.set_ylim(0, 10)
    ax_tbl.axis("off")
    ax_tbl.set_facecolor(BG_PRIMARY)

    # Get years sorted newest first for table. Show up to 6 years —
    # the practical cap dictated by SET's 5-year news-search window
    # plus the current partial year.
    table_years = sorted(history.keys(), reverse=True)
    if len(table_years) > 6:
        table_years = table_years[:6]

    # Table layout
    # Columns: Year | Q1 | Q2 | Q3 | Q4 | Sum
    col_positions = [0.7, 2.4, 4.1, 5.8, 7.5, 9.1]  # x centers
    col_headers = ["Year", "Q1", "Q2", "Q3", "Q4", "Full Year"]
    n_rows = len(table_years)

    # ─── Header row (highlight background band) ───
    header_y = 9.0
    header_h = 0.9
    ax_tbl.add_patch(Rectangle((0.1, header_y - 0.1), 9.8, header_h,
                                facecolor=BG_HIGHLIGHT, edgecolor="none"))

    for i, (x, h) in enumerate(zip(col_positions, col_headers)):
        ax_tbl.text(x, header_y + 0.35, h,
                    fontsize=11, color="white", fontweight="800",
                    ha="center", va="center")

    # ─── Data rows ───
    # Each cell shows:
    #   Value (big)
    #   YoY% (green/red)
    #   QoQ% (green/red)
    # row_h shrinks when there are 5+ years so everything fits under the
    # header; text offsets scale with y_scale so spacing stays balanced.
    row_h = 2.0 if n_rows <= 4 else (8.2 / n_rows)
    y_scale = row_h / 2.0
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

        # Row backgrounds: latest = highlight, others = secondary.
        row_bg = BG_HIGHLIGHT if is_latest_row else BG_SECONDARY

        ax_tbl.add_patch(Rectangle((0.1, row_top - row_h + row_gap),
                                     9.8, row_h - row_gap,
                                     facecolor=row_bg, edgecolor="none"))

        # Left accent stripe for latest year — Accent Blue.
        if is_latest_row:
            ax_tbl.add_patch(Rectangle((0.1, row_top - row_h + row_gap),
                                         0.04, row_h - row_gap,
                                         facecolor=ACCENT_BLUE, edgecolor="none"))

        # Year column — primary text on row bg.
        year_y_center = row_top - row_h/2 + row_gap/2
        if is_latest_row:
            ax_tbl.text(col_positions[0], year_y_center + 0.35 * y_scale,
                        f"FY{y}",
                        fontsize=14, color=TEXT_PRIMARY, fontweight="800",
                        ha="center", va="center")
            ax_tbl.text(col_positions[0], year_y_center - 0.30 * y_scale,
                        "LATEST",
                        fontsize=7.5, color="#FFFFFF",
                        fontweight="800", ha="center", va="center",
                        bbox=dict(boxstyle="round,pad=0.3",
                                  facecolor=ACCENT_BLUE, edgecolor="none"))
        else:
            ax_tbl.text(col_positions[0], year_y_center,
                        f"FY{y}",
                        fontsize=12, color=TEXT_PRIMARY, fontweight="700",
                        ha="center", va="center")

        # Q1-Q4 cells: each has 3 lines (value / YoY / QoQ)
        q_order = ["Q1", "Q2", "Q3", "Q4"]
        for q_idx, q in enumerate(q_order):
            val = history[y].get(q)
            x = col_positions[q_idx + 1]

            if val is None:
                ax_tbl.text(x, year_y_center, "—",
                            fontsize=11, color=TEXT_MUTED,
                            ha="center", va="center")
                continue

            # ─── Line 1: Profit value — green if profit, red if loss ───
            val_color = PROFIT_GREEN if val >= 0 else LOSS_RED
            ax_tbl.text(x, year_y_center + 0.45 * y_scale,
                        f"{val:,.2f}",
                        fontsize=14 if is_latest_row else 12.5,
                        color=val_color,
                        fontweight="800" if is_latest_row else "700",
                        ha="center", va="center")

            # ─── Line 2: YoY — growth green / decline red ───
            prior_y_data = history.get(y - 1)
            prior_y_val = prior_y_data.get(q) if prior_y_data else None
            if prior_y_val is not None and prior_y_val > 0:
                yoy = (val - prior_y_val) / prior_y_val * 100
                yoy_c = GROWTH_GREEN if yoy >= 0 else DECLINE_RED
                ax_tbl.text(x, year_y_center - 0.05 * y_scale,
                            f"yoy  {yoy:+.1f}%",
                            fontsize=9.5, color=yoy_c,
                            fontweight="700",
                            ha="center", va="center")

            # ─── Line 3: QoQ — same styling as YoY, slightly smaller ───
            prev_q_val = get_prev_quarter(y, q)
            if prev_q_val is not None and prev_q_val > 0:
                qoq = (val - prev_q_val) / prev_q_val * 100
                qoq_c = GROWTH_GREEN if qoq >= 0 else DECLINE_RED
                ax_tbl.text(x, year_y_center - 0.55 * y_scale,
                            f"qoq  {qoq:+.1f}%",
                            fontsize=8.5, color=qoq_c,
                            fontweight="600",
                            ha="center", va="center")

        # Full Year total (right column) — bigger value + YoY pill
        total = history[y].sum()
        x = col_positions[5]
        if total is not None:
            # Value (larger to emphasize yearly total) — green if profit, red if loss
            total_color = PROFIT_GREEN if total >= 0 else LOSS_RED
            ax_tbl.text(x, year_y_center + 0.35 * y_scale,
                        f"{total:,.2f}",
                        fontsize=15 if is_latest_row else 13,
                        color=total_color,
                        fontweight="800" if is_latest_row else "700",
                        ha="center", va="center")

            # Full-Year YoY — growth green / decline red
            prior_total = history.get(y - 1)
            prior_total = prior_total.sum() if prior_total else None
            if prior_total is not None and prior_total > 0:
                yoy_t = (total - prior_total) / prior_total * 100
                yoy_c = GROWTH_GREEN if yoy_t >= 0 else DECLINE_RED
                ax_tbl.text(x, year_y_center - 0.25 * y_scale,
                            f"yoy  {yoy_t:+.1f}%",
                            fontsize=10.5, color=yoy_c,
                            fontweight="700",
                            ha="center", va="center")

    # Column divider lines — subtle border-div tone over dark rows
    for x_divider in [1.45, 3.25, 4.95, 6.65, 8.30]:
        ax_tbl.plot([x_divider, x_divider],
                    [header_y - 0.1, y_start - n_rows * row_h + row_gap],
                    color=BORDER_DIV, linewidth=0.4, alpha=0.9, zorder=0)

    # ─── Bottom-left branding mark: pi | A8/4 ───
    fig.text(0.055, 0.032, "pi",
             fontsize=18, color=TEXT_PRIMARY,
             fontweight="300", ha="left", va="center",
             fontname="DejaVu Serif", fontstyle="italic")
    fig.text(0.092, 0.032, "|",
             fontsize=16, color=TEXT_SECONDARY,
             fontweight="200", ha="left", va="center")
    fig.text(0.108, 0.032, "A8/4",
             fontsize=11, color=TEXT_PRIMARY,
             fontweight="400", ha="left", va="center")

    # Source line + AI disclaimer at the bottom (right side)
    fig.text(0.94, 0.038,
             f"Source:  https://www.set.or.th/th/market/product/stock/quote/{symbol}/news",
             fontsize=8, color=ACCENT_BLUE, ha="right", style="italic")
    fig.text(0.94, 0.022,
             "AI can make mistakes. Please double-check responses.",
             fontsize=8, color=TEXT_MUTED, ha="right", style="italic",
             fontweight="500")

    # Save to bytes — keep dark canvas across the saved PNG
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130,
                facecolor=BG_PRIMARY, bbox_inches="tight", pad_inches=0.25)
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
