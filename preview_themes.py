"""Render one preview PNG per theme so we can eyeball the palettes."""
from pathlib import Path

from make_chart_html import QuarterlyData, THEMES, make_chart


OUT_DIR = Path(__file__).parent / "theme_previews"
OUT_DIR.mkdir(exist_ok=True)

history = {
    2568: QuarterlyData(2568, 7585.24, 6768.45, 6596.53, 7255.88),
    2567: QuarterlyData(2567, 6319.40, 6239.48, 5607.86, 7179.10),
    2566: QuarterlyData(2566, 4122.78, 4438.41, 4424.29, 5496.66),
    2565: QuarterlyData(2565, 3453.03, 3004.02, 3676.93, 3137.73),
    2564: QuarterlyData(2564, 2599.05, 2189.70, 1493.01, 6703.72),
}

for key, theme in THEMES.items():
    png = make_chart(
        symbol="CPALL",
        company_name="CP All Public Company Limited",
        history=history,
        latest_year=2568,
        latest_quarter="Q4",
        report_date="25 Feb 2026",
        period_label="FY 2568  ·  Q4",
        theme=key,
    )
    out = OUT_DIR / f"theme_{key}.png"
    out.write_bytes(png)
    print(f"[{key:>9}] {theme.name:<20} {len(png):>8,} bytes -> {out}")
