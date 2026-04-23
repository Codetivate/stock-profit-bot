"""Fetch the full stock list + index constituents from SET and write
reference/set_all.json, reference/mai.json, reference/set50.json,
reference/set100.json with current membership.
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


REF = Path("reference")
REF.mkdir(exist_ok=True)


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="th-TH")

        # Warm Incapsula
        pg = ctx.new_page()
        pg.goto("https://www.set.or.th/th/market/get-quote/composite/stocks-in-the-set50",
                wait_until="domcontentloaded", timeout=60_000)
        pg.wait_for_timeout(3000)
        pg.close()

        common_headers = {
            "accept": "application/json, text/plain, */*",
            "x-channel": "WEB_SET",
            "x-client-uuid": "stock-profit-bot",
            "referer": "https://www.set.or.th/th/market/get-quote/composite/stocks-in-the-set50",
        }

        # 1) All stocks (SET + mai)
        print("Fetching /api/set/stock/list …")
        r = ctx.request.get("https://www.set.or.th/api/set/stock/list",
                             headers=common_headers)
        print(f"  status {r.status}")
        if r.status == 200:
            data = r.json()
            symbols = data.get("securitySymbols", [])
            # Keep only common-stock rows (typeSequence == 1, securityType == "S")
            stocks = [s for s in symbols
                      if s.get("securityType") == "S"
                      and s.get("typeSequence") == 1]

            set_stocks = sorted(
                {s["symbol"] for s in stocks if s.get("market") == "SET"}
            )
            mai_stocks = sorted(
                {s["symbol"] for s in stocks if s.get("market") == "mai"}
            )

            print(f"  SET : {len(set_stocks)} common stocks")
            print(f"  mai : {len(mai_stocks)} common stocks")

            (REF / "set_all.json").write_text(
                json.dumps({
                    "_comment": f"All SET market common stocks, fetched "
                                f"{data.get('asOfDate','?')}",
                    "symbols": set_stocks,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (REF / "mai.json").write_text(
                json.dumps({
                    "_comment": f"All mai market common stocks, fetched "
                                f"{data.get('asOfDate','?')}",
                    "symbols": mai_stocks,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # Save full metadata for company-info lookups
            (REF / "stock_list_raw.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # 2) Index constituents — /composition returns the current member list
        #    under composition.stockInfos[].symbol
        for index_name in ("SET50", "SET100", "SETHD", "SETCLMV", "SETWB", "sSET"):
            print(f"\nFetching {index_name} composition …")
            url = f"https://www.set.or.th/api/set/index/{index_name}/composition"
            r = ctx.request.get(url, headers=common_headers)
            if r.status != 200:
                print(f"  ✗ status {r.status}")
                continue
            try:
                data = r.json()
                stock_infos = (data.get("composition") or {}).get("stockInfos") or []
                syms = sorted({
                    si["symbol"] for si in stock_infos if si.get("symbol")
                })
                print(f"  ✓ {len(syms)} symbols")
                out = REF / f"{index_name.lower()}.json"
                out.write_text(
                    json.dumps({
                        "_comment": f"{index_name} index constituents. "
                                    f"SET rebalances semi-annually; refresh "
                                    f"by re-running scripts/fetch_index_lists.py.",
                        "last_updated": (data.get("composition") or {})
                            .get("indexInfos", [{}])[0].get("asOfDate"),
                        "symbols": syms,
                    }, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"  → wrote {out}")
            except Exception as e:
                print(f"  ✗ parse error: {e}")

        browser.close()


if __name__ == "__main__":
    run()
