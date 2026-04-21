"""
download_set_history.py — Bulk download historical SET financial zips

Usage:
    python download_set_history.py CPALL
    python download_set_history.py CPALL --years 4

Downloads all FIN (งบการเงิน) zip files from SET news page.
Saves to ./downloads/{SYMBOL}/

Requires: requests, beautifulsoup4
    pip install requests beautifulsoup4
"""
import os
import re
import sys
import time
import argparse
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup


SET_NEWS_URL = "https://www.set.or.th/th/market/product/stock/quote/{symbol}/news"
SET_DETAIL_URL = "https://www.set.or.th/th/market/news-and-alert/newsdetails?id={id}&symbol={symbol}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7",
}


def fetch(url: str, retries: int = 3) -> str:
    """Fetch URL with retries."""
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as e:
            if i == retries - 1:
                raise
            print(f"  retry {i+1}/{retries}: {e}")
            time.sleep(2 * (i + 1))


def fetch_news_list(symbol: str) -> list:
    """Fetch the news list page and return list of (news_id, date, title)."""
    url = SET_NEWS_URL.format(symbol=symbol)
    print(f"Fetching news list: {url}")
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    news_items = []

    # The SET news page uses React/Next.js — news is usually embedded in
    # a JSON block or rendered via tables. We look for links to newsdetails.
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "newsdetails" in href:
            # Extract id from href
            qs = parse_qs(urlparse(href).query)
            news_id = qs.get("id", [None])[0]
            if news_id:
                title = link.get_text(strip=True)
                news_items.append({
                    "id": news_id,
                    "title": title,
                    "url": href if href.startswith("http")
                           else f"https://www.set.or.th{href}",
                })

    return news_items


def fetch_detail_page(news_id: str, symbol: str) -> tuple:
    """Fetch the news detail page and return (title, zip_url, date)."""
    url = SET_DETAIL_URL.format(id=news_id, symbol=symbol)
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    zip_url = ""
    date = ""

    # Title usually in h2 or similar
    for h in soup.find_all(["h1", "h2", "h3"]):
        text = h.get_text(strip=True)
        if "งบ" in text or "FIN" in text.upper():
            title = text
            break

    # Zip link
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if ".zip" in href.lower() and "weblink.set.or.th" in href:
            zip_url = href
            break

    # Date: look for date in body text
    m = re.search(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})",
                  html)
    if m:
        date = m.group(0)

    return title, zip_url, date


def is_financial_report(title: str) -> bool:
    """Check if title is a financial statement (งบการเงิน)."""
    if not title:
        return False
    # Must contain 'งบการเงิน'
    if "งบการเงิน" not in title:
        return False
    # Exclude clarifications, corrections
    if "คำชี้แจง" in title or "แก้ไข" in title:
        return False
    return True


def download_zip(url: str, out_path: str) -> bool:
    """Download a zip file."""
    try:
        print(f"  ↓ {os.path.basename(out_path)}")
        r = requests.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:
        print(f"  ✗ Failed: {e}")
        return False


def main():
    ap = argparse.ArgumentParser(description="Download SET financial statement zips")
    ap.add_argument("symbol", help="Stock symbol, e.g. CPALL")
    ap.add_argument("--years", type=int, default=4, help="How many years back (default: 4)")
    ap.add_argument("--out", default="./downloads", help="Output directory")
    args = ap.parse_args()

    symbol = args.symbol.upper()
    out_dir = Path(args.out) / symbol
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Downloading {symbol} historical financial statements")
    print(f"Target: last {args.years} years ({args.years * 4 + 1} reports)")
    print(f"Output: {out_dir}")
    print(f"{'='*60}\n")

    # Step 1: Get news list
    items = fetch_news_list(symbol)
    print(f"Found {len(items)} news items on page")

    # Step 2: Filter to financial reports only
    fin_items = [n for n in items if is_financial_report(n.get("title", ""))]
    print(f"Filtered to {len(fin_items)} financial statement reports")

    if not fin_items:
        print("\n⚠️  No financial statements found.")
        print("   The news page may render data via JavaScript (React/Next.js).")
        print("   If this happens, manual download is needed.")
        print(f"\n   Open manually: https://www.set.or.th/th/market/product/stock/quote/{symbol}/news")
        return

    # Step 3: For each, get detail page and download zip
    success = 0
    for i, item in enumerate(fin_items[:args.years * 4 + 4], 1):
        print(f"\n[{i}] {item['title'][:60]}")
        title, zip_url, date = fetch_detail_page(item["id"], symbol)
        if not zip_url:
            print(f"  ⚠️  No zip link found")
            continue

        # Build filename from URL
        zip_name = os.path.basename(urlparse(zip_url).path)
        out_path = out_dir / zip_name

        if out_path.exists():
            print(f"  ✓ Already downloaded: {zip_name}")
            success += 1
            continue

        if download_zip(zip_url, str(out_path)):
            success += 1
        time.sleep(1.5)  # polite delay

    print(f"\n{'='*60}")
    print(f"✓ Downloaded {success} / {len(fin_items)} reports")
    print(f"Saved to: {out_dir}")
    print(f"{'='*60}")
    print(f"\nNext step:")
    print(f"  python parse_all.py {symbol}")


if __name__ == "__main__":
    main()
