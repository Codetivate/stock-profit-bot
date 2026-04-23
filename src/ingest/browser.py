"""Playwright browser manager.

One shared browser context per scan so we pay the Incapsula-bootstrap cost
(~5s to acquire cookies) exactly once, then reuse it for API calls and
detail-page visits.

Usage:
    with SetSession() as session:
        data = session.request_json(url)
        html = session.fetch_page(url)
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Optional

from playwright.sync_api import APIResponse, Browser, BrowserContext, sync_playwright


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)


class SetSession:
    """Session bound to SET's domain. Warms Incapsula cookies on enter."""

    def __init__(self, warm_symbol: str = "CPALL", headless: bool = True):
        self._warm_symbol = warm_symbol
        self._headless = headless
        self._pw = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None

    def __enter__(self) -> "SetSession":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        self._context = self._browser.new_context(
            user_agent=USER_AGENT,
            locale="th-TH",
            viewport={"width": 1440, "height": 900},
        )
        self._warm_up()
        return self

    def __exit__(self, *exc):
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def _warm_up(self):
        """Visit a SET page so Incapsula issues us a cookie we can reuse."""
        page = self._context.new_page()
        try:
            page.goto(
                f"https://www.set.or.th/th/market/product/stock/quote/{self._warm_symbol}/news",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            page.wait_for_timeout(4000)
        finally:
            page.close()

    def request_json(
        self,
        url: str,
        referer: Optional[str] = None,
        headers: Optional[dict] = None,
    ) -> Any:
        """GET a JSON endpoint using the warmed-up browser context.

        `headers` merges on top of the defaults, so callers can add
        endpoint-specific auth (e.g. x-channel: WEB_SET for the CMS
        news API) without having to reconstruct the request.
        """
        merged = {"Accept": "application/json"}
        if referer:
            merged["Referer"] = referer
        if headers:
            merged.update(headers)
        resp: APIResponse = self._context.request.get(url, headers=merged)
        if resp.status != 200:
            raise RuntimeError(
                f"API {url} returned {resp.status}: {resp.text()[:200]}"
            )
        return resp.json()

    def fetch_page_html(self, url: str, settle_ms: int = 4000) -> str:
        """Fetch a fully-rendered HTML page (for scraping zip links etc)."""
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(settle_ms)
            return page.content()
        finally:
            page.close()
