"""
make_chart.py — Generate v5-style profit analysis chart.

Takes historical quarterly net profit data and produces a
world-class comparison chart matching the Mockup v5 design.
"""
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

# Force non-interactive backend BEFORE pyplot import — the bot server
# renders charts from worker threads; the default Tk-based backend
# fails in that scenario with "main thread is not in main loop".
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.patheffects as patheffects  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

# Modern sans-serif font stack — matplotlib falls through until one is
# installed, so we can ask for Inter/SF/Helvetica first and still land
# on the Windows-standard Segoe UI or the bundled DejaVu Sans.
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = [
    "Inter", "SF Pro Display", "SF Pro Text",
    "Helvetica Neue", "Helvetica",
    "Segoe UI", "Arial",
    "DejaVu Sans",
]

# Logo asset — drop `logo.png` into ./assets to replace the text fallback.
LOGO_PATH = Path(__file__).parent / "assets" / "logo.png"


# ═══ Pure Black + Neon palette (pi.financial inspired) ═══
# Backgrounds — near-blacks layered so the OLED feel is preserved
# while still giving rows/latest-highlight a hint of separation.
BG_PRIMARY     = "#000000"   # pure OLED black canvas
BG_SECONDARY   = "#080808"   # table rows — just above pure black
BG_HIGHLIGHT   = "#0A1A12"   # latest row — faint neon-green tint
BORDER_DIV     = "#1F1F1F"   # subtle dividers, table band
CARD_SURFACE   = "#050505"   # panels / containers

# Text
TEXT_PRIMARY   = "#F5F5F5"   # near-white, easy against pure black
TEXT_SECONDARY = "#9CA3AF"   # labels, subtitles
TEXT_MUTED     = "#585858"   # footnotes, source

# Neon green (Pi brand translated to OLED)
PROFIT_GREEN   = "#00E676"   # vivid neon green — hero profit numbers
GROWTH_GREEN   = "#00D97E"   # calmer variant for YoY / QoQ positive
GREEN_GLOW     = "#00FFAA"   # brightest — optional glow / accent

# Neon red — hot pink-red so negatives pop without clashing with green.
LOSS_RED       = "#FF2D55"   # hot pink-red — hero loss numbers
DECLINE_RED    = "#FF3366"   # YoY / QoQ negative

# Accents
ACCENT_BLUE    = "#00C3FF"   # cyan — LATEST tag / highlight
ACCENT_CYAN    = "#00FFFF"
ACCENT_PURPLE  = "#B026FF"

# Card inner fills for QoQ / YoY cards — deeper neon tints so the
# panels read as glowing rectangles on the black canvas.
GREEN_CARD_BG     = "#051A0F"
GREEN_CARD_BORDER = "#00A352"
RED_CARD_BG       = "#1F050C"
RED_CARD_BORDER   = "#C3214F"

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
    # Annual total reported directly by the FY filing, kept so we can
    # display a Full-Year figure even for issuers that skip H1/Q2
    # (most commercial banks) where back-computing Q4 isn't possible.
    full_year: Optional[float] = None

    def sum(self) -> Optional[float]:
        """Annual total. Prefer the FY filing's own number when present;
        otherwise fall back to the sum of standalone quarters (requires
        all four to be known)."""
        if self.full_year is not None:
            return self.full_year
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

    # Calculate growth — use abs(prior) so a swing from loss -> profit shows
    # as a positive %, not a misleading negative.
    qoq = ((latest_profit - prev_q_profit) / abs(prev_q_profit) * 100) \
        if prev_q_profit else None
    yoy = ((latest_profit - prev_y_profit) / abs(prev_y_profit) * 100) \
        if prev_y_profit else None

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

    # ═══ Hero section: green-bordered "Q4/68" pill + big number ═══
    # Pill shows quarter + Thai year short form (2568 -> 68).
    # Layout: pill sits high under the header, hero number sits low enough
    # that its top clears the pill's bottom edge with a small breathing gap.
    hero_color = PROFIT_GREEN if latest_profit >= 0 else LOSS_RED
    pill_label = f"{latest_quarter}/{str(latest_year)[-2:]}"
    pill_w, pill_h = 0.14, 0.035
    pill_x = 0.5 - pill_w / 2
    pill_y = 0.917
    ax_pill = fig.add_axes([pill_x, pill_y, pill_w, pill_h])
    ax_pill.set_xlim(0, 1); ax_pill.set_ylim(0, 1); ax_pill.axis("off")
    ax_pill.add_patch(FancyBboxPatch(
        (0.06, 0.08), 0.88, 0.84,
        boxstyle="round,pad=0.02",
        linewidth=1.6,
        facecolor=BG_PRIMARY,
        edgecolor=hero_color,
    ))
    ax_pill.text(0.5, 0.5, pill_label,
                 fontsize=16, color=hero_color, fontweight="800",
                 ha="center", va="center")

    # Hero number tinted by sign — neon green for profit, hot pink for
    # loss. A soft wide stroke in the same hue produces the subtle glow
    # that gives the "neon on black" look its signature lift.
    hero_glow = [
        patheffects.withStroke(linewidth=8, foreground=hero_color, alpha=0.22),
        patheffects.withStroke(linewidth=3, foreground=hero_color, alpha=0.45),
        patheffects.Normal(),
    ]
    fig.text(0.5, 0.860, f"{latest_profit:,.2f}",
             fontsize=72, fontweight="800", color=hero_color,
             ha="center", va="center",
             path_effects=hero_glow)
    fig.text(0.5, 0.800, "million baht",
             fontsize=10, color=TEXT_SECONDARY,
             ha="center", va="center", fontweight="500")

    # ═══ Two comparison cards ═══
    card_y = 0.685
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
        ax_c1.text(0.5, 0.50, f"{c1_arrow} {qoq:+.1f}%",
                   fontsize=32, color=c1_color, fontweight="800", ha="center")
        # Dotted hairline separator matching the reference mockup.
        ax_c1.plot([0.12, 0.88], [0.28, 0.28],
                   color=c1_border, linewidth=0.7, linestyle=(0, (1, 2.5)))
        ax_c1.text(0.5, 0.13, f"{prev_q_label}: {prev_q_profit:,.2f} MB",
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
        ax_c2.text(0.5, 0.50, f"{c2_arrow} {yoy:+.1f}%",
                   fontsize=32, color=c2_color, fontweight="800", ha="center")
        ax_c2.plot([0.12, 0.88], [0.28, 0.28],
                   color=c2_border, linewidth=0.7, linestyle=(0, (1, 2.5)))
        ax_c2.text(0.5, 0.13, f"{prev_y_label}: {prev_y_profit:,.2f} MB",
                   fontsize=9, color=TEXT_SECONDARY, ha="center", fontweight="500")

    # Years sorted for later use
    years_sorted = sorted(history.keys())

    # ═══ Quarterly breakdown table ═══
    fig.text(0.06, 0.663, "QUARTERLY NET PROFIT",
             fontsize=15, color=TEXT_PRIMARY, fontweight="800")
    fig.text(0.38, 0.663, "(million baht)",
             fontsize=10, color=TEXT_SECONDARY, fontweight="500",
             style="italic")

    ax_tbl = fig.add_axes([0.03, 0.080, 0.94, 0.575])
    ax_tbl.set_xlim(0, 10)
    ax_tbl.set_ylim(0, 10)
    ax_tbl.axis("off")
    ax_tbl.set_facecolor(BG_PRIMARY)

    # Cap the table at 5 years so every chart renders with the same
    # row size and cadence as the reference CPALL mockup, regardless of
    # how much history we happen to have for a given symbol.
    table_years = sorted(history.keys(), reverse=True)
    if len(table_years) > 5:
        table_years = table_years[:5]

    # Table layout — columns: Year | Q1 | Q2 | Q3 | Q4 | Full Year.
    # Values stay centred in their column so the stacked "value / yoy /
    # qoq" micro-layout reads as a single block instead of a ragged edge.
    col_positions = [0.7, 2.4, 4.1, 5.8, 7.5, 9.1]
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

        # Left accent stripe for latest year — neon green, matches the
        # profit colour so the eye reads the row as "this quarter is live".
        if is_latest_row:
            ax_tbl.add_patch(Rectangle((0.1, row_top - row_h + row_gap),
                                         0.04, row_h - row_gap,
                                         facecolor=PROFIT_GREEN, edgecolor="none"))

        # Year column — primary text on row bg.
        year_y_center = row_top - row_h/2 + row_gap/2
        if is_latest_row:
            ax_tbl.text(col_positions[0], year_y_center + 0.35 * y_scale,
                        f"FY{y}",
                        fontsize=14, color=TEXT_PRIMARY, fontweight="800",
                        ha="center", va="center")
            # LATEST tag: neon green fill, black text for high contrast.
            ax_tbl.text(col_positions[0], year_y_center - 0.30 * y_scale,
                        "LATEST",
                        fontsize=7.5, color=BG_PRIMARY,
                        fontweight="800", ha="center", va="center",
                        bbox=dict(boxstyle="round,pad=0.3",
                                  facecolor=PROFIT_GREEN, edgecolor="none"))
        else:
            ax_tbl.text(col_positions[0], year_y_center,
                        f"FY{y}",
                        fontsize=12, color=TEXT_PRIMARY, fontweight="700",
                        ha="center", va="center")

        # Q1-Q4 cells: each has 3 lines (value / YoY / QoQ), centred.
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
            # Divide by abs(prior) so a flip from loss -> profit shows as a
            # positive %, and loss narrowing vs a negative prior shows as +.
            prior_y_data = history.get(y - 1)
            prior_y_val = prior_y_data.get(q) if prior_y_data else None
            if prior_y_val is not None and prior_y_val != 0:
                yoy = (val - prior_y_val) / abs(prior_y_val) * 100
                yoy_c = GROWTH_GREEN if yoy >= 0 else DECLINE_RED
                ax_tbl.text(x, year_y_center - 0.05 * y_scale,
                            f"yoy  {yoy:+.1f}%",
                            fontsize=9.5, color=yoy_c,
                            fontweight="700",
                            ha="center", va="center")

            # ─── Line 3: QoQ — same styling as YoY, slightly smaller ───
            prev_q_val = get_prev_quarter(y, q)
            if prev_q_val is not None and prev_q_val != 0:
                qoq = (val - prev_q_val) / abs(prev_q_val) * 100
                qoq_c = GROWTH_GREEN if qoq >= 0 else DECLINE_RED
                ax_tbl.text(x, year_y_center - 0.55 * y_scale,
                            f"qoq  {qoq:+.1f}%",
                            fontsize=8.5, color=qoq_c,
                            fontweight="600",
                            ha="center", va="center")

        # Full Year total (right column) — centred like numeric cells.
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

            # Full-Year YoY — growth green / decline red, abs(prior) base
            prior_total = history.get(y - 1)
            prior_total = prior_total.sum() if prior_total else None
            if prior_total is not None and prior_total != 0:
                yoy_t = (total - prior_total) / abs(prior_total) * 100
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

    # ═══════════════════════════════════════════════════
    # Footer — brand mark on the left, source + AI disclaimer centred
    # under the table so the chart has a clean symmetric bottom edge.
    # ═══════════════════════════════════════════════════

    # ─── Bottom-left branding ───
    # PNG at assets/logo.png wins; text mark is the graceful fallback.
    logo_rendered = False
    if LOGO_PATH.exists():
        try:
            logo_img = plt.imread(str(LOGO_PATH))
            ax_logo = fig.add_axes([0.040, 0.008, 0.200, 0.058])
            ax_logo.imshow(logo_img, interpolation="bilinear")
            ax_logo.axis("off")
            logo_rendered = True
        except Exception:
            logo_rendered = False
    if not logo_rendered:
        fig.text(0.052, 0.037, "pi",
                 fontsize=32, color=TEXT_PRIMARY,
                 fontweight="300", ha="left", va="center",
                 fontname="DejaVu Serif", fontstyle="italic")
        fig.text(0.110, 0.037, "|",
                 fontsize=24, color=TEXT_SECONDARY,
                 fontweight="200", ha="left", va="center")
        fig.text(0.130, 0.037, "A8/4",
                 fontsize=14, color=TEXT_PRIMARY,
                 fontweight="500", ha="left", va="center")

    # ─── Bottom-centre: source + AI disclaimer ───
    # Centred under the table, matching the reference mockup. Source is
    # tinted with the growth-green tone to signal it's a live link.
    source_url = f"https://www.set.or.th/th/market/product/stock/quote/{symbol}/news"
    fig.text(0.55, 0.046,
             f"Source:    {source_url}",
             fontsize=9, color=GROWTH_GREEN,
             ha="center", va="center", style="italic", fontweight="500")

    fig.text(0.55, 0.022,
             "AI can make mistakes. Please double-check responses.",
             fontsize=8, color=TEXT_MUTED,
             ha="center", va="center", style="italic", fontweight="400")

    # Save to bytes — keep dark canvas across the saved PNG
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130,
                facecolor=BG_PRIMARY, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return buf.getvalue()


if __name__ == "__main__":
    # Test with CPALL data — 5 years to mirror the reference mockup.
    history = {
        2568: QuarterlyData(2568, 7585.24, 6768.45, 6596.53, 7255.88),
        2567: QuarterlyData(2567, 6319.40, 6239.48, 5607.86, 7179.10),
        2566: QuarterlyData(2566, 4122.78, 4438.41, 4424.29, 5496.66),
        2565: QuarterlyData(2565, 3453.03, 3004.02, 3676.93, 3137.73),
        2564: QuarterlyData(2564, 2599.05, 2189.70, 1493.01, 6703.72),
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
