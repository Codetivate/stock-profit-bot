"""smart_refresh.py — auto-detect filer-overwritten zips and refresh them.

The SCC investigation taught us that SET filers can re-upload a
corrected zip at the SAME URL after the initial download (SCC posted
the separate-only XLSX under the "งบการเงินรวม" news headline first,
then quietly replaced it with the consolidated XLSX). Our ingest
pipeline dedupes by ``news_id`` and never refetches, so the local
cache holds the old (wrong) bytes forever.

This script walks every cached zip, HEAD-or-GET-fetches the source URL
(only the first ~64KB is enough to compute sha256 of small zips), and
flags / refreshes anything whose live sha256 differs from the recorded
one. It does NOT fully re-parse — pair with reparse_financials after
to rebuild processed/financials.json.

Usage:
    python scripts/smart_refresh.py                          # dry-run, no writes
    python scripts/smart_refresh.py --apply                  # download + overwrite mismatches
    python scripts/smart_refresh.py --symbol SCC             # check one symbol
    python scripts/smart_refresh.py --watchlist set50 --apply  # SET50 only
    python scripts/smart_refresh.py --apply --reparse        # also reparse symbols that changed

Implementation notes:
  • Uses streaming SHA-256 — pulls the URL once and hashes while
    streaming. No memory blow-up on large zips.
  • Skips files already verified within --recheck-hours (default 24h)
    so re-running daily is cheap.
  • On mismatch, writes a new ``source.zip``, re-extracts the .xlsx /
    .xls, and updates metadata.json's ``sha256`` + ``size_bytes`` +
    a new ``refreshed_at`` timestamp pointing at the change event.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import requests


RAW_ROOT = Path("data/raw")
REFERENCE = Path("reference")


def _stream_sha256(url: str, *, timeout: int = 60) -> tuple[str, bytes]:
    """Stream-download the URL and return (sha256_hex, full_bytes).
    Streaming keeps peak memory at the chunk size for large zips while
    still letting callers persist the bytes once divergence is confirmed.
    """
    h = hashlib.sha256()
    chunks: list[bytes] = []
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            h.update(chunk)
            chunks.append(chunk)
    return h.hexdigest(), b"".join(chunks)


def _iter_filings(symbols: Optional[Iterable[str]] = None):
    """Yield (symbol, year, period, raw_dir) for every cached filing.

    Walks ``data/raw/{SYMBOL}/financials/{YEAR}/{PERIOD}/`` and only
    emits dirs that look like a real filing (have metadata.json + a
    source.zip).
    """
    syms = (
        sorted(symbols)
        if symbols is not None
        else sorted(p.name for p in RAW_ROOT.iterdir() if p.is_dir())
    )
    for sym in syms:
        fin_dir = RAW_ROOT / sym / "financials"
        if not fin_dir.exists():
            continue
        for year_dir in sorted(fin_dir.iterdir()):
            if not year_dir.is_dir():
                continue
            try:
                year = int(year_dir.name)
            except ValueError:
                continue
            for period_dir in sorted(year_dir.iterdir()):
                if not period_dir.is_dir():
                    continue
                if (period_dir / "metadata.json").exists() and (
                    period_dir / "source.zip"
                ).exists():
                    yield sym, year, period_dir.name, period_dir


def _load_watchlist(name: str) -> list[str]:
    path = REFERENCE / f"{name}.json"
    if not path.exists():
        sys.exit(f"watchlist not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("symbols") or [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", action="append",
                    help="Limit to specific symbols; repeat for many.")
    ap.add_argument("--watchlist",
                    help="Limit to a reference/<name>.json watchlist (e.g. set50).")
    ap.add_argument("--apply", action="store_true",
                    help="Actually download and overwrite when sha256 mismatches. "
                         "Without this flag the script reports findings only.")
    ap.add_argument("--recheck-hours", type=int, default=24,
                    help="Skip filings whose metadata was last refreshed within "
                         "this many hours (default 24).")
    ap.add_argument("--reparse", action="store_true",
                    help="After applying refreshes, run reparse_financials for "
                         "every symbol that had at least one zip replaced. "
                         "Requires --apply.")
    args = ap.parse_args()

    if args.symbol:
        symbols = [s.upper() for s in args.symbol]
    elif args.watchlist:
        symbols = [s.upper() for s in _load_watchlist(args.watchlist)]
    else:
        symbols = None  # all

    now = datetime.now(timezone.utc)
    refreshed_symbols: set[str] = set()
    n_checked = 0
    n_skipped = 0
    n_changed = 0
    n_errors = 0

    print(f"smart_refresh — {'APPLY' if args.apply else 'DRY-RUN'} mode  ·  "
          f"recheck_hours={args.recheck_hours}")
    print()

    for sym, year, period, raw_dir in _iter_filings(symbols):
        meta_path = raw_dir / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        url = meta.get("source_url")
        recorded_sha = meta.get("sha256", "")
        if not url or not recorded_sha:
            continue

        # Honor recheck-hours so re-running stays cheap.
        last_refresh = meta.get("refreshed_at") or meta.get("ingested_at")
        if last_refresh:
            try:
                last_dt = datetime.fromisoformat(last_refresh.replace("Z", "+00:00"))
                hours_since = (now - last_dt).total_seconds() / 3600
                if hours_since < args.recheck_hours:
                    n_skipped += 1
                    continue
            except ValueError:
                pass

        n_checked += 1
        try:
            fresh_sha, fresh_bytes = _stream_sha256(url)
        except requests.RequestException as e:
            print(f"  ✗ {sym} {year} {period}: fetch failed — {e}")
            n_errors += 1
            continue

        if fresh_sha == recorded_sha:
            # Update refreshed_at so we don't re-check this one for a while.
            if args.apply:
                meta["refreshed_at"] = now.isoformat()
                meta_path.write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            continue

        n_changed += 1
        print(f"  ⚠ {sym} {year} {period}: filer overwrote zip "
              f"({recorded_sha[:8]}… → {fresh_sha[:8]}…)")

        if not args.apply:
            continue

        # Overwrite cached zip + extract XLSX
        zip_path = raw_dir / "source.zip"
        zip_path.write_bytes(fresh_bytes)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                for name in zf.namelist():
                    upper = name.upper()
                    if upper.endswith(".XLSX"):
                        (raw_dir / "source.xlsx").write_bytes(zf.read(name))
                        break
                    if upper.endswith(".XLS"):
                        (raw_dir / "source.xls").write_bytes(zf.read(name))
                        break
        except zipfile.BadZipFile as e:
            print(f"    ✗ extract failed: {e}")
            n_errors += 1
            continue

        meta["sha256"] = fresh_sha
        meta["size_bytes"] = len(fresh_bytes)
        meta["refreshed_at"] = now.isoformat()
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        refreshed_symbols.add(sym)
        print(f"    ✓ replaced cache and metadata")

    print()
    print(f"Summary: checked={n_checked}  changed={n_changed}  "
          f"skipped (recent)={n_skipped}  errors={n_errors}")
    if refreshed_symbols:
        print(f"Symbols that need reparse: {sorted(refreshed_symbols)}")

    if args.apply and args.reparse and refreshed_symbols:
        print()
        print("Running reparse_financials for refreshed symbols…")
        import subprocess
        subprocess.call(
            [sys.executable, "-m", "src.cli.reparse_financials",
             *sorted(refreshed_symbols)]
        )

    return 0 if n_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
