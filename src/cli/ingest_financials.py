"""Ingest all financial statements for one symbol.

Pipeline:
    1. search SET news for financial_statement items (5-year max from SET).
    2. For each: grab zip URL from detail page, download to data/raw/.
    3. Parse the XLSX (net profit attributable to shareholders).
    4. Compute standalone quarterly values (Q2 = H1 - Q1, etc.).
    5. Emit data/processed/{SYMBOL}/financials.json per the v1 schema,
       including a provenance `sources` manifest.
    6. Diff against the previous file and print unchanged/changed/new.

Usage:
    python -m src.cli.ingest_financials CPALL
"""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from parsers.parse_set_zip import parse_zip  # existing XLSX parser
from src.ingest.browser import SetSession
from src.ingest.set_api import extract_zip_urls, search_news
from src.ingest.zip_downloader import (
    IngestedFiling,
    download_filing,
    parse_headline,
    safe_symbol_dir,
)


PROCESSED_ROOT = Path("data/processed")
REFERENCE_COMPANIES = Path("reference/companies.json")


def _load_company_meta(symbol: str) -> dict:
    if not REFERENCE_COMPANIES.exists():
        return {}
    data = json.loads(REFERENCE_COMPANIES.read_text(encoding="utf-8"))
    return (data.get("companies") or {}).get(symbol, {})


def _is_financial_statement(headline: str) -> bool:
    """True only for the actual financial-statement filings — not cover
    letters, management discussions, or pre-audit drafts that get refiled
    post-audit."""
    h = headline or ""
    if "งบการเงิน" not in h:
        return False
    # Cover letters ("delivery letters") accompany the financial zip but
    # don't carry the XLSX themselves.
    if "จดหมายนำส่ง" in h or "นำส่งงบ" in h:
        return False
    return True


def _is_amendment(headline: str) -> bool:
    """Skip pure-text clarifications (no zip / no new numbers) — but
    KEEP corrections marked ``(แก้ไข)``: those re-publish the financial
    statement with revised figures, and SET's company-highlight API
    uses the corrected values. GPSC 2568 FY shipped both the original
    and an ``(แก้ไข)`` version; ignoring the amendment left us with
    pre-correction numbers that disagree with SET by ~1.5B baht.

    ``คำชี้แจง`` / ``ชี้แจงเพิ่มเติม`` are commentary letters that
    explain something about a prior filing — they don't carry a zip
    we can parse, so we still skip them."""
    h = headline or ""
    amendment_markers = (
        "คำชี้แจง", "ชี้แจงเพิ่มเติม",
    )
    return any(k in h for k in amendment_markers)


def compute_standalone_quarters(
    sources: List[dict],
) -> Dict[int, Dict[str, Optional[float]]]:
    """Turn filing rows into year → Q1..Q4 + FullYear.

    Each filing row carries two profit figures we can use:
      • ``shareholder_profit``      — the 3-month standalone number
        (first numeric column of the PL sheet). For a Q1 report this
        is Q1, for a 9M report it's Q3, for an FY report it's the
        full year since the annual sheet has only one column.
      • ``shareholder_profit_cum``  — the cumulative number from the
        longer PL sheet when the filing ships two sheets (H1 / 9M
        filings), or the same as standalone when it's a single-sheet
        Q1 / FY.

    Derivation strategy — prefer direct values, fall back to
    differences:
      Q1  ← Q1 filing standalone (= Q1 cumulative for Q1)
      Q2  ← H1 standalone, or (H1 cum − Q1), or (9M cum − Q1 − Q3)
      Q3  ← 9M standalone
      Q4  ← (FY − 9M cum) or (FY − Q1 − Q2 − Q3)
      FY  ← annual filing's standalone number

    That last Q2 path is what lets Thai commercial banks (KBANK, BBL,
    KTB, TTB, BAY, KKP, CIMBT, LHFG) show Q2 and Q4 even though they
    skip the H1 filing at SET entirely.
    """
    by_year: Dict[int, Dict[str, Optional[float]]] = {}
    cum_by_year: Dict[int, Dict[str, Optional[float]]] = {}
    fy_total_by_year: Dict[int, Optional[float]] = {}
    # Index by both standalone and cumulative — keep the row even when
    # only one is present so we can fall back to derivations like
    # Q3 = 9M_cum − Q1 − Q2 when the 3-month standalone failed to parse.
    #
    # For interim filings (Q1/H1/9M), only treat ``shareholder_profit``
    # as a real "standalone" when it actually came from a 3-month sheet.
    # Some filers ship 9M filings with only the cumulative sheet — in
    # that case the parser ends up with sp == sp_cum and primary_months
    # equal to 6 or 9, so the value is the cum, not Q2/Q3 standalone.
    # Treating it as standalone would double-count and inflate Q4 by
    # the prior cumulative quarters.
    for r in sources:
        sp = r.get("shareholder_profit")
        sp_cum = r.get("shareholder_profit_cum")
        if sp is None and sp_cum is None:
            continue
        period = r["period"]
        primary = r.get("primary_months")
        y = r["thai_year"]
        if (period in ("Q1", "H1", "9M") and primary is not None
                and primary > 3):
            sp = None
        # For FY filings, prefer the 12-month cumulative number as the
        # full-year total. Some filers (BCP) ship FY zips whose
        # 3-month "Q4 standalone" sheet contains stale data from an
        # earlier filing — using sp as FY would put a stale or
        # quarterly number into the FY slot and break the entire
        # year. The 12-month sheet (sp_cum) is the authoritative
        # full-year figure; Q4 is derived downstream from FY − 9M cum.
        if period == "FY":
            if primary is not None and primary == 3 and sp_cum is not None:
                fy_total_by_year[y] = sp_cum
            else:
                fy_total_by_year[y] = sp if sp is not None else sp_cum
        by_year.setdefault(y, {})[period] = sp
        cum_by_year.setdefault(y, {})[period] = sp_cum

    out: Dict[int, Dict[str, Optional[float]]] = {}
    for y, periods in by_year.items():
        cums = cum_by_year.get(y, {})

        q1 = periods.get("Q1")
        q3 = periods.get("9M")     # 3-month figure (= Q3 standalone)
        # FY total comes from fy_total_by_year, which already handles
        # both the single-sheet (sp == FY) and the BCP-style
        # 3+12 layout (sp_cum == FY). Falling back to periods.get("FY")
        # would put Q4 standalone in the FY slot for BCP-style filings.
        fy = fy_total_by_year.get(y)

        h1_cum = cums.get("H1")    # 6-month cumulative from H1 filing
        nine_cum = cums.get("9M")  # 9-month cumulative from 9M filing

        # Q2 — try three paths in order of reliability.
        q2 = periods.get("H1")     # H1 standalone = Q2 (when we fetched H1)
        if q2 is None and h1_cum is not None and q1 is not None:
            q2 = h1_cum - q1
        if q2 is None and nine_cum is not None and q1 is not None and q3 is not None:
            q2 = nine_cum - q1 - q3

        # Q3 — fall back to 9M cumulative minus (Q1 + Q2) when the 3-month
        # standalone is missing (e.g. parser couldn't read the 3-month sheet
        # but found the cumulative). This is the same logic Q2 uses, just
        # solved for Q3 instead.
        if q3 is None and nine_cum is not None and q1 is not None and q2 is not None:
            q3 = nine_cum - q1 - q2

        # Q4 — prefer the four-quarter subtraction (FY − Q1 − Q2 − Q3)
        # when all three standalones are available. The 9M cumulative
        # path is technically equivalent but has been seen to fail when
        # filers ship 9M zips containing stale prior-year cumulative
        # values (CPALL 2567's 9M cum reads as 2566's 9M total),
        # which silently inflates Q4 by the YoY delta. Standalone
        # quarters come from three independent quarterly filings, so
        # one stale filing can't corrupt the others. Fall back to the
        # 9M cum subtraction only when at least one standalone is
        # missing — that's typically the case for newer issuers whose
        # quarterly history hasn't been fully ingested yet.
        q4 = None
        if all(v is not None for v in (fy, q1, q2, q3)):
            q4 = fy - q1 - q2 - q3
        elif fy is not None and nine_cum is not None:
            q4 = fy - nine_cum

        out[y] = {"Q1": q1, "Q2": q2, "Q3": q3, "Q4": q4, "FullYear": fy}

    # Per-symbol manual overrides: when SET's company-highlight API uses
    # a value from a different filing than our pipeline picks (almost
    # always a TFRS-related restatement reflected in the next year's FY
    # prior-period comparative), an explicit override entry in
    # parsers/manual_overrides.json points the pipeline at the cell SET
    # actually reads. The value must come from a SET-filed XLSX —
    # the json doc requires a `source_note` pointing at sheet + row.
    # Re-derive Q4 from the new FY total when all three other quarters
    # are present so the chart's Q1+Q2+Q3+Q4 = FullYear invariant holds.
    if sources:
        symbol = sources[0].get("symbol")
    else:
        symbol = None
    if symbol:
        from pathlib import Path as _P
        ov_path = _P("parsers/manual_overrides.json")
        if ov_path.exists():
            try:
                ov = json.loads(ov_path.read_text(encoding="utf-8")).get(symbol, {})
            except (json.JSONDecodeError, OSError):
                ov = {}
            for y_str, data in ov.items():
                try:
                    y_int = int(y_str)
                except (TypeError, ValueError):
                    continue
                if y_int not in out:
                    continue
                fy_override = data.get("FullYear")
                if fy_override is None:
                    continue
                out[y_int]["FullYear"] = fy_override
                q1, q2, q3 = (out[y_int].get(k) for k in ("Q1", "Q2", "Q3"))
                if all(v is not None for v in (q1, q2, q3)):
                    out[y_int]["Q4"] = fy_override - q1 - q2 - q3
    return out


def ingest_symbol(
    symbol: str,
    *,
    years_back: int = 6,
    today: Optional[date] = None,
    session: Optional[SetSession] = None,
) -> dict:
    """Ingest financials for one symbol. If `session` is provided, reuse it
    (needed when called from a caller that already holds a Playwright session,
    since the sync API forbids nesting)."""
    today = today or date.today()
    from_date = today - timedelta(days=365 * years_back)

    print(f"\n=== Ingesting {symbol}  ·  {from_date} → {today} ===\n")

    staged: List[IngestedFiling] = []
    parse_rows: List[dict] = []

    session_ctx = (
        nullcontext(session) if session is not None
        else SetSession(warm_symbol=symbol)
    )
    with session_ctx as session:
        print("[1/4] Fetching news feed…")
        news = search_news(session, symbol, from_date, today, today=today)
        fin_items = [
            n for n in news
            if _is_financial_statement(n.headline) and not _is_amendment(n.headline)
            and parse_headline(n.headline) is not None
        ]
        # Dedupe by (thai_year, period), keeping the latest news_datetime.
        # Pre-audit drafts and post-audit finals share the same period;
        # the post-audit version is filed later and is the canonical
        # numbers — let it win. The pre-audit's small "summary" XLSX
        # also has a different layout that mis-reads through the parser.
        #
        # Many issuers (SCGP, KBANK, AOT, …) file BOTH "งบการเงินรวม"
        # (consolidated) and "งบการเงินเฉพาะกิจการ" (separate /
        # parent-only) at the same time. SET company-highlight reports
        # the CONSOLIDATED figure, so we must prefer the conso filing
        # when both are available — separate financials only show the
        # parent's profit (no subsidiaries) and would silently
        # under-report by tens of percent for a holding company.
        def _filing_priority(headline: str) -> tuple[int, ...]:
            # Higher tuple wins. (1) prefer consolidated. (2) prefer
            # post-audit final. (3) tie-break by datetime via the
            # outer loop's monotonically-newer comparison.
            is_conso = "งบการเงินรวม" in headline or "ระหว่างกาลรวม" in headline
            is_separate = "เฉพาะกิจการ" in headline
            audited = "ตรวจสอบแล้ว" in headline or "สอบทานแล้ว" in headline
            return (1 if is_conso and not is_separate else 0, 1 if audited else 0)

        latest_by_period: Dict[tuple, NewsItem] = {}
        for n in fin_items:
            key = parse_headline(n.headline)
            cur = latest_by_period.get(key)
            if cur is None:
                latest_by_period[key] = n
                continue
            cur_pri = _filing_priority(cur.headline)
            new_pri = _filing_priority(n.headline)
            # Strict priority order: conso > separate, audited > pre-audit,
            # then datetime as the tie-breaker.
            if (new_pri, n.datetime) > (cur_pri, cur.datetime):
                latest_by_period[key] = n
        fin_items = sorted(latest_by_period.values(),
                           key=lambda n: n.datetime, reverse=True)
        print(f"      {len(news)} total news, {len(fin_items)} financial statements")

        print("\n[2/4] Downloading zips + extracting XLSX…")
        for i, item in enumerate(fin_items, 1):
            try:
                urls = extract_zip_urls(session, item.url)
                if not urls:
                    print(f"      [{i:2d}/{len(fin_items)}] ✗ no zip: {item.headline[:60]}")
                    continue
                filing = download_filing(
                    symbol=symbol,
                    zip_url=urls[0],
                    news_id=item.news_id,
                    headline=item.headline,
                    news_datetime=item.datetime,
                )
                staged.append(filing)
                size_kb = filing.zip_path.stat().st_size // 1024
                print(f"      [{i:2d}/{len(fin_items)}] ✓ "
                      f"{filing.key.thai_year} {filing.key.period}  "
                      f"({size_kb}KB) {filing.headline[:50]}")
            except Exception as e:
                print(f"      [{i:2d}/{len(fin_items)}] ERROR: {e}")

    print(f"\n[3/4] Parsing {len(staged)} XLSX files…")
    for f in staged:
        try:
            fd = parse_zip(str(f.zip_path), symbol=symbol)
            # Keep the row when either the 3-month standalone OR the
            # cumulative number is populated. Cumulative-only rows are
            # still useful: a 9M filing whose 3-month sheet was malformed
            # can still let us derive Q4 from (FY − 9M cum), and Q3 from
            # (9M cum − Q1 − Q2).
            if not fd or (fd.shareholder_profit is None and fd.shareholder_profit_cum is None):
                print(f"      ✗ parse miss: {f.key.thai_year} {f.key.period}")
                continue
            # Prefer XLSX-detected end year over filename-derived thai_year
            # for non-Dec fiscal filers (AEONTS et al.). Same +/-1 sanity
            # bound as reparse_financials uses.
            row_year = f.key.thai_year
            if (
                fd.year
                and 2540 <= fd.year <= 2600
                and abs(fd.year - f.key.thai_year) <= 1
            ):
                row_year = fd.year
            parse_rows.append({
                "symbol": symbol,
                "thai_year": row_year,
                "period": f.key.period,
                "shareholder_profit": fd.shareholder_profit,
                "shareholder_profit_prior": fd.shareholder_profit_prior,
                "shareholder_profit_cum": fd.shareholder_profit_cum,
                "shareholder_profit_cum_prior": fd.shareholder_profit_cum_prior,
                "cum_months": fd.cum_months,
                "primary_months": fd.primary_months,
                "revenue": fd.revenue,
                "net_profit": fd.net_profit,
                "eps": fd.eps,
                "filing_date": f.filing_date,
                "news_id": f.news_id,
                "raw_path": str(f.zip_path.relative_to(Path("data/raw"))).replace("\\", "/"),
                "sha256": f.sha256,
                "ingested_at": json.loads(f.metadata_path.read_text(encoding="utf-8"))["ingested_at"],
            })
            sp_str = (
                f"{fd.shareholder_profit:,.2f} MB"
                if fd.shareholder_profit is not None
                else f"cum={fd.shareholder_profit_cum:,.2f} MB"
            )
            print(f"      ✓ {f.key.thai_year} {f.key.period}  {sp_str}")
        except Exception as e:
            print(f"      ✗ parse error {f.key.thai_year} {f.key.period}: {e}")

    # Dedupe by (thai_year, period), keeping the row with the latest
    # filing_date. This ensures a post-audit filing supersedes the
    # pre-audit draft once both have been fetched for the same period.
    latest_per_period: Dict[tuple, dict] = {}
    for row in parse_rows:
        key = (row["thai_year"], row["period"])
        cur = latest_per_period.get(key)
        if cur is None or (row.get("filing_date") or "") > (cur.get("filing_date") or ""):
            latest_per_period[key] = row
    parse_rows = list(latest_per_period.values())

    print(f"\n[4/4] Computing standalone quarterly + emitting financials.json…")
    quarterly = compute_standalone_quarters(parse_rows)

    # Load any existing file for comparison
    proc_dir = PROCESSED_ROOT / safe_symbol_dir(symbol)
    proc_dir.mkdir(parents=True, exist_ok=True)
    out_path = proc_dir / "financials.json"
    previous = {}
    if out_path.exists():
        previous = json.loads(out_path.read_text(encoding="utf-8"))
    previous_quarterly = {
        int(y): q for y, q in (previous.get("quarterly_history") or {}).items()
    }

    meta = _load_company_meta(symbol)
    payload = {
        "schema_version": 1,
        "symbol": symbol,
        "company_code": meta.get("company_code"),
        "company_name_en": meta.get("name_en"),
        "company_name_th": meta.get("name_th"),
        "updated_at": max((r["filing_date"] for r in parse_rows), default=None),
        "quarterly_history": {
            str(y): quarterly[y] for y in sorted(quarterly.keys())
        },
        "sources": [
            {
                "year": r["thai_year"],
                "period": r["period"],
                "filing_date": r["filing_date"],
                "news_id": r["news_id"],
                "raw_path": r["raw_path"],
                "sha256": r["sha256"],
                "ingested_at": r["ingested_at"],
                "shareholder_profit": r["shareholder_profit"],
            }
            for r in sorted(parse_rows, key=lambda x: (x["thai_year"], x["period"]))
        ],
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"      Wrote {out_path}")

    # Diff
    print("\n=== Diff vs previous financials.json ===")
    all_years = sorted(set(quarterly.keys()) | set(previous_quarterly.keys()))
    unchanged = changed = new = 0
    for y in all_years:
        new_qs = quarterly.get(y, {})
        old_qs = previous_quarterly.get(y, {})
        for q in ("Q1", "Q2", "Q3", "Q4"):
            nv = new_qs.get(q)
            ov = old_qs.get(q)
            if ov is None and nv is None:
                continue
            if ov is None:
                new += 1
                print(f"  +  {y} {q}: {nv}")
            elif nv is None:
                changed += 1
                print(f"  ?  {y} {q}: was {ov}, now MISSING")
            elif abs(nv - ov) < 0.01:
                unchanged += 1
            else:
                changed += 1
                delta = nv - ov
                print(f"  ≠  {y} {q}: {ov:,.2f} → {nv:,.2f}  (Δ {delta:+,.2f})")
    print(f"\n  Summary: {unchanged} unchanged, {changed} changed, {new} new")

    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--years", type=int, default=6)
    ap.add_argument("--today", help="Override 'today' (YYYY-MM-DD) for reproducibility")
    args = ap.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    ingest_symbol(args.symbol.upper(), years_back=args.years, today=today)


if __name__ == "__main__":
    main()
