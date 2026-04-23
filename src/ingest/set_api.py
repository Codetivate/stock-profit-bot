"""Typed wrappers over SET's internal JSON APIs.

Two public entry points:
    - search_news(session, symbol, from_date, to_date) → list of news items
    - get_corporate_actions(session, symbol)           → list of XD/XM/XB

SET's /api/set/news/search caps date range at 5 years. search_news transparently
chunks longer ranges into overlapping 5-year windows and dedupes by id.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional

from .browser import SetSession


MAX_RANGE_DAYS = 365 * 5 - 5   # SET rejects any fromDate older than this
                                # (measured from "today", not from toDate).


@dataclass
class NewsItem:
    """One entry from /api/set/news/search."""
    news_id: str
    datetime: str            # ISO 8601 with +07:00
    date: str                # YYYY-MM-DD extracted from datetime
    symbol: str
    source: str
    url: str
    headline: str
    product: str             # "S" = stock
    lang: str

    @classmethod
    def from_api(cls, row: dict) -> "NewsItem":
        dt = row.get("datetime") or ""
        d = dt[:10] if len(dt) >= 10 else ""
        return cls(
            news_id=str(row.get("id", "")),
            datetime=dt,
            date=d,
            symbol=row.get("symbol", ""),
            source=row.get("source", ""),
            url=row.get("url", ""),
            headline=row.get("headline", ""),
            product=row.get("product", ""),
            lang=row.get("lang", "th"),
        )


@dataclass
class CorporateAction:
    """One entry from /api/set/stock/{SYMBOL}/corporate-action.

    Fields are kept close to SET's wire format; consumers project to our
    own announcement schema when persisting.
    """
    symbol: str
    ca_type: str             # XD | XM | XB | XR | XN | XW | ...
    xdate: Optional[str]
    record_date: Optional[str]
    meeting_date: Optional[str]
    payment_date: Optional[str]
    dividend: Optional[float]
    dividend_type: Optional[str]
    source_of_dividend: Optional[str]
    agenda: Optional[str]
    meeting_type: Optional[str]
    remark: Optional[str]
    raw: dict                # full SET payload for forward-compat

    @classmethod
    def from_api(cls, row: dict) -> "CorporateAction":
        return cls(
            symbol=row.get("symbol", ""),
            ca_type=row.get("caType") or row.get("type") or "",
            xdate=_date_only(row.get("xdate")),
            record_date=_date_only(row.get("recordDate")),
            meeting_date=_date_only(row.get("meetingDate")),
            payment_date=_date_only(row.get("paymentDate")),
            dividend=_as_float(row.get("dividend")),
            dividend_type=row.get("dividendType"),
            source_of_dividend=row.get("sourceOfDividend"),
            agenda=row.get("agenda"),
            meeting_type=row.get("meetingType"),
            remark=row.get("remark"),
            raw=row,
        )


def _date_only(s):
    if not s or not isinstance(s, str):
        return None
    return s[:10] if len(s) >= 10 else None


def _as_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def search_news(
    session: SetSession,
    symbol: str,
    from_date: date,
    to_date: date,
    today: Optional[date] = None,
) -> List[NewsItem]:
    """Fetch news for a symbol. SET caps the oldest allowed fromDate at ~5
    years before *today* (not before toDate), so ranges older than that
    are silently clamped.

    Returns items sorted most-recent-first.
    """
    if to_date < from_date:
        return []

    today = today or date.today()
    oldest_allowed = today - timedelta(days=MAX_RANGE_DAYS)
    if from_date < oldest_allowed:
        from_date = oldest_allowed

    rows = _search_news_chunk(session, symbol, from_date, to_date)
    # Deduplicate defensively; the API occasionally returns duplicates
    # when rows share a timestamp.
    seen: dict[str, NewsItem] = {}
    for item in rows:
        seen.setdefault(item.news_id, item)
    return sorted(seen.values(), key=lambda x: x.datetime, reverse=True)


def _search_news_chunk(
    session: SetSession,
    symbol: str,
    from_date: date,
    to_date: date,
) -> List[NewsItem]:
    url = (
        "https://www.set.or.th/api/set/news/search"
        f"?symbol={symbol}"
        f"&fromDate={_fmt(from_date)}"
        f"&toDate={_fmt(to_date)}"
        "&keyword=&lang=th"
    )
    payload = session.request_json(
        url,
        referer=f"https://www.set.or.th/th/market/product/stock/quote/{symbol}/news",
    )
    rows = payload.get("newsInfoList", []) if isinstance(payload, dict) else []
    return [NewsItem.from_api(r) for r in rows]


NEWS_TAPE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "x-channel": "WEB_SET",
    "x-client-uuid": "stock-profit-bot",
    "referer": "https://www.set.or.th/th/market/news-and-alert/news",
}


def fetch_news_tape(
    session: SetSession,
    from_date: date,
    to_date: date,
    *,
    per_page: int = 500,
    security_type: str = "S",
) -> List[NewsItem]:
    """Fetch every company's news across the whole market in one call.

    Uses SET's /api/cms/v1/news/set endpoint — the backend that powers
    https://www.set.or.th/th/market/news-and-alert/news. Requires the
    x-channel header ("WEB_SET"); without it the endpoint returns 401.

    With per_page=500 and a 2–3 day lookback we get ~500 items per call
    which covers normal market-wide news flow comfortably. If the tape
    ever saturates (unlikely without earnings-season batching), switch
    to the paginate* cursor returned by the API.
    """
    url = (
        "https://www.set.or.th/api/cms/v1/news/set"
        f"?sourceId=company&securityTypeIds={security_type}"
        f"&fromDate={_fmt(from_date)}&toDate={_fmt(to_date)}"
        f"&perPage={per_page}&orderBy=date&lang=th"
    )
    payload = session.request_json(
        url,
        referer=NEWS_TAPE_HEADERS["referer"],
        headers=NEWS_TAPE_HEADERS,
    )
    if not isinstance(payload, dict):
        return []

    pag = payload.get("paginateNews") or {}
    rows = pag.get("newsInfoList") if isinstance(pag, dict) else None
    if not rows:
        return []
    return [NewsItem.from_api(r) for r in rows]


def get_corporate_actions(session: SetSession, symbol: str) -> List[CorporateAction]:
    """Fetch XD/XM/XB rows for a symbol."""
    url = f"https://www.set.or.th/api/set/stock/{symbol}/corporate-action?lang=th"
    payload = session.request_json(
        url,
        referer=f"https://www.set.or.th/th/market/product/stock/quote/{symbol}/rights-benefits",
    )
    rows = payload if isinstance(payload, list) else []
    return [CorporateAction.from_api(r) for r in rows]


ZIP_URL_RE = re.compile(r"https://weblink\.set\.or\.th/[^\"' <>]+\.zip",
                        re.IGNORECASE)


def extract_zip_urls(session: SetSession, news_detail_url: str) -> List[str]:
    """Open a newsdetails page and return every attached weblink zip URL."""
    html = session.fetch_page_html(news_detail_url, settle_ms=3000)
    return sorted(set(ZIP_URL_RE.findall(html)))
