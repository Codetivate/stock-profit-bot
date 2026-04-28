"""
make_chart_html.py — HTML/CSS chart renderer via Playwright.

Drop-in replacement for ``make_chart.py``'s ``make_chart()`` function.
Designed for pixel-perfect "modern dark" visuals that matplotlib can't
match natively: real Inter typography, true box-shadow glows, CSS
gradients, smooth rounded corners.

Pipeline per render:
    HTML string → Chromium headless → screenshot(.chart element) → PNG

Thread safety: each call spins up a fresh browser (~300-500 ms overhead)
because Playwright's sync API is not thread-safe for shared browsers.
Bot workers can call concurrently without collision.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from playwright.sync_api import sync_playwright

# Re-export QuarterlyData from the matplotlib module so existing call
# sites (command_handler, broadcast, server) don't need to change imports.
from make_chart import QuarterlyData  # noqa: F401

LOGO_PATH = Path(__file__).parent / "assets" / "logo.png"


# ═══ Themes ═══
# Each theme is a flat dict of colour tokens. Rendering pulls from these
# tokens so swapping themes is a single parameter change. Table-row tones
# are calibrated so header, latest, and ordinary rows read as three
# distinct bands without relying on neon glow for separation.
@dataclass(frozen=True)
class Theme:
    name: str

    # Surfaces
    canvas: str         # page background
    row_bg: str         # ordinary row
    row_alt: str        # (unused today, reserved for zebra striping)
    row_latest: str     # latest-year highlight row
    thead_bg: str       # table header band
    card_border: str    # neutral divider / thin borders

    # Text
    text: str           # primary
    text_muted: str     # secondary labels
    text_faint: str     # footnote / source

    # Accents — sign-aware
    pos: str            # hero profit, value text
    pos_soft: str       # yoy / qoq positive
    pos_bg: str         # comparison card bg when up
    pos_border: str     # comparison card border when up

    neg: str            # hero loss
    neg_soft: str       # yoy / qoq negative
    neg_bg: str         # comparison card bg when down
    neg_border: str     # comparison card border when down

    # Latest-row border stripe colour (usually same as pos)
    latest_stripe: str

    # LATEST pill text colour (on accent fill)
    pill_on_accent: str = "#000000"

    # Whether this is a light-mode theme (drives logo colour inversion)
    is_light: bool = False

    # Solid pill on profit hero (mint-fill brand badge). False keeps the
    # outlined pi-neon look.
    solid_pill_on_profit: bool = False

    # Pi brand gold/orange — used for the PI INSIGHTS chip and other
    # subtle frame-style accents seen across Pi's marketing kits.
    accent_gold: str = "#F58F2A"


THEMES: Dict[str, Theme] = {
    # Pi Financial — official brand palette, light & minimal modern.
    # Built from the 7 Pi swatches: mint #21CE99, dark teal #167579,
    # orange #F58F2A, white, navy #002E60, purple #473F72, black.
    # White canvas with navy primary text, dark teal for the profit
    # value (mint #21CE99 alone is too light on white — it pairs best
    # as an accent surface), and red for losses per Pi's convention.
    # Orange #F58F2A and purple #473F72 sit on the supporting roles
    # (FY column tint, comparison-card border accents) so all seven
    # palette colors appear without crowding the eye.
    # Pi Financial — light "Pi Research" tone. Pure white canvas,
    # charcoal-navy text, navy header band, mint + cream-gold accents.
    # Mirrors pi.financial/research — clean, minimalist, professional.
    "pi": Theme(
        name="Pi Financial",
        canvas="#F5F7FA",             # soft gray app background
        row_bg="#FFFFFF",
        row_alt="#FFFFFF",            # no zebra — flat white rows
        row_latest="#E6F4F1",         # light mint background for latest row
        thead_bg="#002E60",           # dark navy header band
        card_border="#E5E7EB",        # neutral border gray
        text="#0F172A",               # primary text
        text_muted="#6B7280",         # secondary text
        text_faint="#6B7280",         # footnote tone
        pos="#21CE99",                # mint green — positive
        pos_soft="#21CE99",
        pos_bg="#E6F4F1",             # light mint surface
        pos_border="#21CE99",
        neg="#EF4444",                # red — negative
        neg_soft="#EF4444",
        neg_bg="#FEE2E2",             # light red surface
        neg_border="#EF4444",
        latest_stripe="#21CE99",      # mint left-border on latest row
        pill_on_accent="#FFFFFF",     # white text on mint LATEST pill
        is_light=True,
        solid_pill_on_profit=False,
        accent_gold="#F58F2A",        # Pi brand orange (palette swatch 3)
    ),
    # Pi Financial — dark "VI Portfolio" variant. Deep forest-teal with
    # warm cream/gold text. Available via theme='pi-dark'.
    "pi-dark": Theme(
        name="Pi Financial (Dark)",
        canvas="#0F2A26",
        row_bg="#163632",
        row_alt="#13302C",
        row_latest="#1C4540",
        thead_bg="#0A201D",
        card_border="#284F47",
        text="#EDE2C6",
        text_muted="#B8AE93",
        text_faint="#7A7160",
        pos="#3FE0AE",
        pos_soft="#3FE0AE",
        pos_bg="#103A2E",
        pos_border="#21CE99",
        neg="#E88A8A",
        neg_soft="#E88A8A",
        neg_bg="#3A1F1E",
        neg_border="#E07B7B",
        latest_stripe="#21CE99",
        pill_on_accent="#0F2A26",
        is_light=False,
        solid_pill_on_profit=True,
        accent_gold="#D4BD8B",
    ),
    # Keep the old neon Pi palette around in case someone wants the
    # punchier original look for a single chart.
    "pi-neon": Theme(
        name="Pi Neon",
        canvas="#000000",
        row_bg="#0E0E0E",
        row_alt="#0B0B0B",
        row_latest="#0A1F14",
        thead_bg="#141414",
        card_border="#1E1E1E",
        text="#FFFFFF",
        text_muted="#A1A1AA",
        text_faint="#6B7280",
        pos="#00E676",
        pos_soft="#00D97E",
        pos_bg="#051A0F",
        pos_border="#00A352",
        neg="#FF3366",
        neg_soft="#FF5577",
        neg_bg="#1F050C",
        neg_border="#C3214F",
        latest_stripe="#00E676",
    ),
}

DEFAULT_THEME = "pi"


# ═══ Helpers ═══
def _pct_change(now: Optional[float], prior: Optional[float]) -> Optional[float]:
    """Return pct change using abs(prior) so loss→profit flips register
    as positive, mirroring the matplotlib renderer's behaviour."""
    if now is None or prior is None or prior == 0:
        return None
    return (now - prior) / abs(prior) * 100


def _fmt(v: Optional[float]) -> str:
    return f"{v:,.2f}" if v is not None else "—"


def _get_prev_quarter(history: Dict[int, QuarterlyData], y: int, q: str) -> Optional[float]:
    order = ["Q1", "Q2", "Q3", "Q4"]
    idx = order.index(q)
    if idx == 0:
        prev = history.get(y - 1)
        return prev.q4 if prev else None
    return history[y].get(order[idx - 1])


def _logo_data_url() -> Optional[str]:
    if LOGO_PATH.exists():
        try:
            return "data:image/png;base64," + base64.b64encode(LOGO_PATH.read_bytes()).decode()
        except OSError:
            return None
    return None


# ═══ HTML builders ═══
def _delta_line_html(label: str, pct: Optional[float], theme: Theme,
                     always_show: bool = False) -> str:
    """Render a delta line as: [muted LABEL] [colored signed-pct].

    Format matches the reference mockup: ``YOY +20.0%`` / ``QOQ -10.8%``.
    No arrow icon — the explicit +/- sign carries the direction, and the
    colour (green/red) makes it pop.

    With ``always_show=True``, a missing percentage renders the label
    next to a muted ``—`` placeholder instead of collapsing to nothing.
    Used for QoQ so every cell shows the same row of metadata, even on
    the oldest year where prior-period data isn't available.
    """
    if pct is None and not always_show:
        return ""
    if pct is None:
        return (f'<div class="tc-sub">'
                f'<span class="tc-sub-lbl">{label.upper()}</span>'
                f'<span class="tc-sub-val" style="color:{theme.text_faint};">—</span>'
                f'</div>')
    color = theme.pos if pct >= 0 else theme.neg
    return (f'<div class="tc-sub">'
            f'<span class="tc-sub-lbl">{label.upper()}</span>'
            f'<span class="tc-sub-val" style="color:{color};">{pct:+.1f}%</span>'
            f'</div>')


def _cell_html(val: Optional[float], yoy: Optional[float], qoq: Optional[float],
               is_latest: bool, theme: Theme) -> str:
    if val is None:
        return '<div class="tc tc-empty">—</div>'
    val_cls = "tc-val tc-val-latest" if is_latest else "tc-val"
    color = theme.pos if val >= 0 else theme.neg
    parts = [
        f'<div class="{val_cls}" style="color:{color};">{_fmt(val)}</div>',
        # YoY collapses on the oldest year (no prior year exists).
        _delta_line_html("YoY", yoy, theme),
        # QoQ always renders so every value cell has a footer line.
        _delta_line_html("QoQ", qoq, theme, always_show=True),
    ]
    return f'<div class="tc">{"".join(parts)}</div>'


def _fy_cell_html(total: Optional[float], yoy: Optional[float],
                  is_latest: bool, theme: Theme) -> str:
    if total is None:
        return '<div class="tc tc-fy tc-empty">—</div>'
    # Unified palette — FY totals use the same green/red as quarterly
    # values; emphasis comes from font size and weight, not a hue shift.
    color = theme.pos if total >= 0 else theme.neg
    val_cls = "tc-val-fy tc-val-fy-latest" if is_latest else "tc-val-fy"
    parts = [f'<div class="{val_cls}" style="color:{color};">{_fmt(total)}</div>']
    yoy_line = _delta_line_html("YoY", yoy, theme)
    if yoy_line:
        parts.append(yoy_line)
    return f'<div class="tc tc-fy">{"".join(parts)}</div>'


def _build_html(
    symbol: str,
    company: str,
    history: Dict[int, QuarterlyData],
    latest_year: int,
    latest_quarter: str,
    report_date: str,
    period_label: str,
    theme: Theme,
) -> str:
    # Hero
    latest = history[latest_year].get(latest_quarter)
    if latest is None:
        raise ValueError(f"No data for {latest_year} {latest_quarter}")
    hero_is_profit = latest >= 0
    hero_color = theme.pos if hero_is_profit else theme.neg
    pill_label = f"{latest_quarter}/{str(latest_year)[-2:]}"

    # Hero card sign-aware visual identity. Profit keeps the navy /
    # teal / mint gradient and the number switches white → brand mint
    # so the value itself reads as "growth". Loss flips the entire
    # card to a deep burgundy gradient — a bright red number on a navy
    # card looked "lit up" and broke the theme, so the negative tone
    # is now conveyed by the surface and the number stays white for
    # legibility on top of it. Pill follows the same logic: mint
    # badge for profit, soft rose-on-rose-pink badge for loss
    # (mirrors the loss style of the comparison-card icons).
    if hero_is_profit:
        hero_card_bg = (
            "radial-gradient(circle at 22% 28%, "
            "rgba(33, 206, 153, 0.32) 0%, rgba(33, 206, 153, 0) 55%), "
            "linear-gradient(135deg, #167579 0%, #002E60 65%, #000000 130%)"
        )
        hero_pill_bg     = theme.pos
        hero_pill_text   = "#FFFFFF"
        hero_pill_shadow = "rgba(33, 206, 153, 0.38)"
        hero_num_color   = theme.pos
    else:
        hero_card_bg = (
            "radial-gradient(circle at 22% 28%, "
            "rgba(239, 68, 68, 0.24) 0%, rgba(239, 68, 68, 0) 55%), "
            "linear-gradient(135deg, #7B1D1D 0%, #2C0A0E 65%, #000000 130%)"
        )
        hero_pill_bg     = "#FECACA"   # rose-200, soft loss badge
        hero_pill_text   = "#991B1B"   # rose-800, high contrast on rose-200
        hero_pill_shadow = "rgba(239, 68, 68, 0.32)"
        hero_num_color   = theme.neg   # same red as table — matches theme

    # Comparison cards
    prev_q_val = _get_prev_quarter(history, latest_year, latest_quarter)
    prev_y_row = history.get(latest_year - 1)
    prev_y_val = prev_y_row.get(latest_quarter) if prev_y_row else None
    qoq = _pct_change(latest, prev_q_val)
    yoy = _pct_change(latest, prev_y_val)

    q_order = ["Q1", "Q2", "Q3", "Q4"]
    q_idx = q_order.index(latest_quarter)
    prev_q_label = (
        f"{q_order[q_idx - 1]}/{latest_year}" if q_idx > 0
        else f"Q4/{latest_year - 1}"
    )
    prev_y_label = f"{latest_quarter}/{latest_year - 1}"

    def card_html(title: str, pct: Optional[float], prior_label: str, prior_val: Optional[float]) -> str:
        if pct is None:
            return f"""
            <div class="card">
              <div class="card-title">{title}</div>
              <div class="card-row">
                <div class="card-icon" style="background:{theme.row_alt};color:{theme.text_muted};">↑</div>
                <div class="card-pct" style="color:{theme.text_muted};">—</div>
              </div>
              <div class="card-prior">n/a</div>
            </div>
            """
        up = pct >= 0
        color   = theme.pos_soft if up else theme.neg_soft
        icon_bg = theme.pos_bg if up else theme.neg_bg
        arrow   = "↑" if up else "↓"
        sign    = "+" if up else ""
        return f"""
        <div class="card">
          <div class="card-title">{title}</div>
          <div class="card-row">
            <div class="card-icon" style="background:{icon_bg};color:{color};">{arrow}</div>
            <div class="card-pct" style="color:{color};">{sign}{pct:.1f}%</div>
          </div>
          <div class="card-prior">{prior_label}: {_fmt(prior_val)} MB</div>
        </div>
        """

    # Table rows (cap at 5 years so the layout stays consistent)
    years = sorted(history.keys(), reverse=True)[:5]
    rows_html = []
    for y in years:
        row = history[y]
        is_latest = y == latest_year
        row_cls = "row row-latest" if is_latest else "row"

        year_cell = (
            f'<div class="tc tc-year">'
            f'<div class="fy">{y}</div>'
            + ('<div class="latest-tag">LATEST</div>' if is_latest else '')
            + '</div>'
        )

        cells = [year_cell]
        for q in q_order:
            val = row.get(q)
            prior_y = history.get(y - 1)
            prior_y_val = prior_y.get(q) if prior_y else None
            cell_yoy = _pct_change(val, prior_y_val)
            prev_q = _get_prev_quarter(history, y, q)
            cell_qoq = _pct_change(val, prev_q)
            cells.append(_cell_html(val, cell_yoy, cell_qoq, is_latest, theme))

        total = row.sum()
        prior_total_row = history.get(y - 1)
        prior_total = prior_total_row.sum() if prior_total_row else None
        fy_yoy = _pct_change(total, prior_total)
        cells.append(_fy_cell_html(total, fy_yoy, is_latest, theme))

        rows_html.append(f'<div class="{row_cls}">{"".join(cells)}</div>')

    # Footer — logo.png already contains the "pi | A8/4" mark, so no
    # text tag is needed. The plain-text fallback reproduces the mark
    # when the asset is missing.
    logo_url = _logo_data_url()
    if logo_url:
        logo_html = f'<img class="logo-img" src="{logo_url}" alt="pi A8/4"/>'
    else:
        logo_html = (
            '<span class="logo-text">pi</span>'
            '<span class="logo-sep">|</span>'
            '<span class="logo-tag">A8/4</span>'
        )
    source_url = f"https://www.set.or.th/th/market/product/stock/quote/{symbol}/news"

    # Paper theme needs white text on the LATEST pill; every other
    # theme uses black-on-accent for legibility.
    pill_text = theme.pill_on_accent

    # Assemble
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    background: {theme.canvas};
    font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
    color: {theme.text};
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }}

  .chart {{
    width: 1600px;
    padding: 44px 52px 36px;
    background: {(
        f"radial-gradient(ellipse at 78% 0%, #1F4A42 0%, {theme.canvas} 60%), {theme.canvas}"
        if not theme.is_light else theme.canvas
    )};
    position: relative;
  }}
  /* Brand accent stripe — mint→teal→navy gradient at top edge */
  .chart::before {{
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0; height: 5px;
    background: linear-gradient(90deg, #21CE99 0%, #167579 50%, #002E60 100%);
  }}

  /* ─── Header ─── */
  .hdr {{
    display: flex; justify-content: space-between; align-items: flex-start;
    margin-bottom: 20px;
  }}
  .sym {{ font-size: 70px; font-weight: 800; letter-spacing: -1.2px; line-height: 1.05; color: #002E60; }}
  .co  {{ font-size: 19px; color: {theme.text_muted}; margin-top: 8px; font-weight: 400; }}
  .hdr-r {{ text-align: right; }}
  .period {{ font-size: 26px; font-weight: 600; letter-spacing: 0.3px; color: #002E60; }}
  .date   {{ font-size: 17px; color: {theme.text_muted}; margin-top: 6px; font-weight: 400; }}

  /* ─── Hero row: gradient hero + 2 comparison cards side-by-side ─── */
  .hero-row {{
    display: grid;
    grid-template-columns: 1.45fr 1fr 1fr;
    gap: 22px;
    margin: 22px 0 36px;
  }}
  /* Hero card visuals — background, pill colours, and number colour
     are all driven by inline styles built sign-aware in Python, so
     this rule only owns the layout/typography. */
  .hero-card {{
    border-radius: 22px;
    padding: 40px 44px;
    position: relative;
    min-height: 380px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    overflow: hidden;
  }}
  .hero-pill {{
    position: absolute;
    top: 28px; left: 28px;
    padding: 14px 38px;
    border-radius: 999px;
    font-size: 30px;
    font-weight: 800;
    letter-spacing: 0.8px;
    z-index: 2;
  }}
  .hero-num {{
    font-size: 128px;
    font-weight: 800;
    line-height: 1;
    letter-spacing: -3.5px;
    z-index: 1;
  }}
  .hero-cap {{
    color: rgba(255, 255, 255, 0.88);
    font-size: 22px;
    font-weight: 500;
    margin-top: 14px;
    letter-spacing: 0.3px;
    z-index: 1;
  }}

  .card {{
    border: 1px solid {theme.card_border};
    background: {theme.row_bg};
    border-radius: 22px;
    padding: 36px 38px 32px;
    text-align: center;
    display: flex;
    flex-direction: column;
    justify-content: center;
  }}
  .card-title {{
    font-size: 18px; font-weight: 700; letter-spacing: 1.4px;
    color: {theme.text_muted}; margin-bottom: 28px;
    text-transform: uppercase;
  }}
  .card-row {{
    display: flex; align-items: center; justify-content: center;
    gap: 22px; margin-bottom: 24px;
  }}
  .card-icon {{
    width: 64px; height: 64px;
    border-radius: 50%;
    background: #E6F4F1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 32px;
    font-weight: 900;
    line-height: 1;
  }}
  .card-pct {{
    font-size: 64px; font-weight: 800;
    letter-spacing: -0.8px;
    line-height: 1;
  }}
  .card-prior {{
    font-size: 18px; color: {theme.text_muted}; font-weight: 500;
    border-top: 1px dashed {theme.card_border}; padding-top: 22px;
    margin-top: 14px;
  }}

  /* ─── Section heading ─── */
  .section-hdr {{ margin: 12px 0 22px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
  .section-icon {{
    width: 44px; height: 44px;
    border-radius: 9px;
    background: #E6F4F1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }}
  .section-icon svg {{ display: block; }}
  .section-title {{ font-size: 28px; font-weight: 700; letter-spacing: 0.6px; color: #002E60; text-transform: uppercase; }}
  .section-sub   {{ font-size: 21px; color: {theme.text_muted}; font-style: italic; font-weight: 500; }}

  /* ─── Table ─── */
  /* No row-tint or FY column tint on the light theme. Latest row is
     signalled by the mint left-stripe + LATEST badge; the FY column
     reads as distinct via larger / bolder totals alone. Dark themes
     keep their faint translucent mint so rows remain separable. */
  {(
    ""
    if theme.is_light else
    f"""  .row > .tc.tc-fy {{ background: rgba(33, 206, 153, 0.05); }}
  .row.row-latest > .tc.tc-fy {{ background: rgba(33, 206, 153, 0.13); }}"""
  )}
  .tbl {{ border-radius: 22px; overflow: hidden; border: 1px solid {theme.card_border}; background: {theme.row_bg}; }}
  .thead {{
    display: grid;
    grid-template-columns: 1fr 1fr 1fr 1fr 1fr 1.2fr;
    background: {theme.thead_bg};
  }}
  .th {{
    font-size: 22px; font-weight: 700; text-align: center;
    color: #FFFFFF;
    letter-spacing: 1.6px; text-transform: uppercase;
    padding: 26px 0;
  }}
  .tbody {{ display: flex; flex-direction: column; }}
  .row {{
    display: grid;
    grid-template-columns: 1fr 1fr 1fr 1fr 1fr 1.2fr;
    background: {theme.row_bg};
    min-height: 190px;
    border-left: 8px solid transparent;
    border-top: 1px solid {theme.card_border};
  }}
  .row:first-child {{ border-top: none; }}
  .row.row-latest {{
    border-left-color: {theme.latest_stripe};
  }}

  /* Cells fill the entire row height so column tints (FY) cover the
     full vertical band, not just the value text area. */
  .tc {{
    display: flex; flex-direction: column; justify-content: center;
    align-items: center; text-align: center;
    padding: 28px 10px; gap: 7px;
  }}
  .tc-empty {{ color: {theme.text_faint}; font-size: 26px; }}
  .tc-val {{ font-size: 30px; font-weight: 700; letter-spacing: -0.3px; }}
  .tc-val-latest {{ font-size: 32px; font-weight: 700; }}
  .tc-val-fy {{ font-size: 44px; font-weight: 800; letter-spacing: -0.4px; }}
  .tc-val-fy-latest {{ font-size: 48px; font-weight: 800; }}
  /* Subtitle row: muted LABEL on the left, signed coloured percentage
     on the right. Two-column grid keeps every cell aligned vertically
     so a column of YOY / QOQ values reads as a clean ladder. */
  .tc-sub {{ font-size: 18px; font-weight: 600; letter-spacing: 0.3px;
            display: grid; grid-template-columns: 56px 96px;
            align-items: baseline; column-gap: 10px;
            justify-content: center; margin-top: 3px; }}
  .tc-sub-lbl {{ color: {theme.text_muted}; font-weight: 600;
                 font-size: 0.88em; letter-spacing: 0.6px;
                 text-align: right; }}
  .tc-sub-val {{ font-weight: 700; text-align: left; }}

  .tc-year {{ gap: 14px; }}
  .fy {{ font-size: 40px; font-weight: 700; color: #002E60; }}
  .latest-tag {{
    background: {theme.latest_stripe}; color: {pill_text};
    font-size: 16px; font-weight: 700;
    padding: 6px 22px; border-radius: 999px;
    letter-spacing: 0.8px;
  }}

  /* ─── Footer ─── */
  .footer {{
    display: grid;
    grid-template-columns: auto 1fr auto;
    align-items: center;
    gap: 24px;
    margin-top: 22px; padding-top: 14px;
  }}
  .brand {{ display: flex; align-items: center; gap: 16px; }}
  .logo-img  {{ height: 170px; width: auto; opacity: 0.95; filter: {('invert(1)' if theme.is_light else 'none')}; }}
  .logo-text {{ font-family: Georgia, serif; font-style: italic; font-size: 68px; font-weight: 300; color: {theme.text}; }}
  .logo-sep  {{ font-size: 48px; color: {theme.text_muted}; font-weight: 200; }}
  .logo-tag  {{ font-size: 28px; color: {theme.text}; font-weight: 500; letter-spacing: 0.5px; }}

  .footer-meta {{
    display: flex; flex-direction: column; gap: 4px;
    align-items: center; text-align: center;
  }}
  .src-line  {{ font-size: 20px; color: {theme.text_faint}; font-style: italic; font-weight: 500; }}
  .src-note  {{ font-size: 18px; color: {theme.text_faint}; font-style: italic; }}

  .footer-qr {{
    width: 170px; height: 170px;
    border: 3px dashed {theme.card_border};
    border-radius: 18px;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    color: {theme.text_muted};
    font-size: 16px;
    text-align: center;
    line-height: 1.3;
  }}
  .footer-qr .qr-icon {{ font-size: 40px; margin-bottom: 6px; opacity: 0.6; }}
</style>
</head>
<body>
  <div class="chart">
    <div class="hdr">
      <div>
        <div class="sym">{symbol}</div>
        <div class="co">{company}</div>
      </div>
      <div class="hdr-r">
        <div class="period">{period_label}</div>
        <div class="date">{report_date}</div>
      </div>
    </div>

    <div class="hero-row">
      <div class="hero-card" style="background:{hero_card_bg};">
        <div class="hero-pill" style="background:{hero_pill_bg};color:{hero_pill_text};box-shadow:0 10px 28px {hero_pill_shadow};">{pill_label}</div>
        <div class="hero-num" style="color:{hero_num_color};">{_fmt(latest)}</div>
        <div class="hero-cap">million baht</div>
      </div>
      {card_html("VS LAST QUARTER", qoq, prev_q_label, prev_q_val)}
      {card_html("VS SAME QUARTER LAST YEAR", yoy, prev_y_label, prev_y_val)}
    </div>

    <div class="section-hdr">
      <span class="section-icon">
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect x="3" y="13" width="4" height="8" rx="1" fill="{theme.pos}"/>
          <rect x="10" y="8" width="4" height="13" rx="1" fill="{theme.pos_border}"/>
          <rect x="17" y="4" width="4" height="17" rx="1" fill="{theme.pos}"/>
        </svg>
      </span>
      <span class="section-title">Quarterly Net Profit</span>
      <span class="section-sub">(million baht)</span>
    </div>

    <div class="tbl">
      <div class="thead">
        <div class="th">Year</div>
        <div class="th">Q1</div>
        <div class="th">Q2</div>
        <div class="th">Q3</div>
        <div class="th">Q4</div>
        <div class="th">Full Year</div>
      </div>
      <div class="tbody">
        {''.join(rows_html)}
      </div>
    </div>

    <div class="footer">
      <div class="brand">{logo_html}</div>
      <div class="footer-meta">
        <div class="src-line">Source:&nbsp;&nbsp;{source_url}</div>
        <div class="src-note">AI can make mistakes. Please double-check responses.</div>
      </div>
      <div class="footer-qr">
        <div class="qr-icon">▦</div>
        <div>QR Code</div>
      </div>
    </div>
  </div>
</body>
</html>"""


# ═══ Public API ═══
def make_chart(
    symbol: str,
    company_name: str,
    history: Dict[int, QuarterlyData],
    latest_year: int,
    latest_quarter: str,
    report_date: str,
    period_label: str,
    theme: str = DEFAULT_THEME,
) -> bytes:
    """Render the quarterly net-profit chart to PNG bytes.

    Signature matches ``make_chart.make_chart`` so it's a drop-in swap.
    ``theme`` picks a palette from ``THEMES`` — default preserves the
    original midnight-neon look.
    """
    theme_obj = THEMES.get(theme, THEMES[DEFAULT_THEME])
    html = _build_html(
        symbol=symbol,
        company=company_name,
        history=history,
        latest_year=latest_year,
        latest_quarter=latest_quarter,
        report_date=report_date,
        period_label=period_label,
        theme=theme_obj,
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-web-security"])
        page = browser.new_page(
            viewport={"width": 1700, "height": 2400},
            device_scale_factor=2,  # retina-crisp output
        )
        page.set_content(html, wait_until="networkidle", timeout=15000)
        # Wait for the Google Fonts to actually load so the screenshot
        # renders with Inter, not the fallback.
        try:
            page.wait_for_function("document.fonts && document.fonts.ready",
                                    timeout=5000)
        except Exception:
            pass  # if fonts time out, we still get a readable fallback
        element = page.query_selector(".chart")
        png = element.screenshot(type="png", omit_background=False)
        browser.close()
    return png


if __name__ == "__main__":
    # Smoke test with CPALL data
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
        period_label="Q4/2568",
    )
    out = Path("test_chart_cpall_html.png")
    out.write_bytes(png)
    print(f"Rendered: {len(png):,} bytes -> {out}")
